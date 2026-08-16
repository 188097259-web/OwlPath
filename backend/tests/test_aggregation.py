from datetime import datetime, timezone
from unittest.mock import patch

from app.engine import (
    adjudicate_aggregation,
    aggregate_predictions,
    aggregate_raw_predictions,
    load_clinical_terms,
    render_bilingual_result,
    scope_violations,
)
from app.models import (
    CoinfectionPair,
    DataBoundary,
    GovernanceConfig,
    ModelContribution,
    ModelPrediction,
    NextTestSuggestion,
    PathogenCandidate,
    RankLevel,
    SafetyAction,
)


def _prediction(
    infection: float = 0.8,
    candidate_probability: float = 0.8,
    calibration_status: str = "uncalibrated_model_score",
) -> ModelPrediction:
    return ModelPrediction(
        summary="synthetic", infection_probability=infection,
        syndrome_probabilities={"respiratory": 0.9},
        candidates=[PathogenCandidate(
            canonical_id="taxon:1313", name="Streptococcus pneumoniae", rank_level=RankLevel.SPECIES,
            category="bacteria", genus="Streptococcus", species="Streptococcus pneumoniae",
            probability=candidate_probability,
            calibration_status=calibration_status,
        )],
        coinfection_probability=0.05, unknown_probability=0.1,
    )


def _contribution(provider_id: str, fingerprint: str) -> ModelContribution:
    return ModelContribution(
        provider_id=provider_id, provider_name=provider_id, status="completed",
        provider_kind="openai_compatible", model="same-model", base_url_origin="http://model.local",
        weight=1.0, data_boundary=DataBoundary.LOCAL, model_fingerprint=fingerprint,
    )


def test_duplicate_model_configs_cannot_manufacture_species_consensus() -> None:
    prediction = _prediction(calibration_status="calibrated")
    successes = [
        ("p1", "p1", 1.0, False, prediction),
        ("p2", "p2", 1.0, False, prediction),
    ]
    governance = GovernanceConfig(
        min_independent_nonbaseline_models_for_species=2,
        species_calibrator_version="synthetic-calibrator-v1",
    )
    duplicate = aggregate_predictions(
        "run", datetime.now(timezone.utc), successes,
        [_contribution("p1", "same"), _contribution("p2", "same")], governance, "a" * 64,
        validated_species_calibration=True,
    )
    assert duplicate.safety_action == SafetyAction.CATEGORY_ONLY
    independent = aggregate_predictions(
        "run", datetime.now(timezone.utc), successes,
        [_contribution("p1", "one"), _contribution("p2", "two")], governance, "a" * 64,
        validated_species_calibration=True,
    )
    assert independent.safety_action == SafetyAction.SPECIES_SET


def test_species_output_is_locked_without_frozen_calibrator() -> None:
    prediction = _prediction(calibration_status="calibrated")
    result = aggregate_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction), ("p2", "p2", 1.0, False, prediction)],
        [_contribution("p1", "one"), _contribution("p2", "two")],
        GovernanceConfig(), "e" * 64,
    )
    assert result.safety_action == SafetyAction.CATEGORY_ONLY
    assert any("校准" in reason for reason in result.safety_reasons)


def test_low_infection_is_a_distinct_fifth_state() -> None:
    prediction = _prediction(infection=0.2, candidate_probability=0.15)
    result = aggregate_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction), ("p2", "p2", 1.0, False, prediction)],
        [_contribution("p1", "one"), _contribution("p2", "two")],
        GovernanceConfig(), "b" * 64,
    )
    assert result.safety_action == SafetyAction.NON_INFECTION
    assert result.candidates == []
    assert any("非感染" in reason for reason in result.safety_reasons)


def test_default_scope_contract_is_adult_respiratory_only() -> None:
    governance = GovernanceConfig()
    assert governance.minimum_age_years == 18
    assert governance.allowed_syndromes == ["respiratory"]
    missing_age = scope_violations(
        {"case": {"demographics": {}, "context": {"primary_syndrome": "respiratory"}}}, governance
    )
    pediatric = scope_violations(
        {"case": {"demographics": {"age_years": 12}, "context": {"primary_syndrome": "respiratory"}}}, governance
    )
    non_respiratory = scope_violations(
        {"case": {"demographics": {"age_years": 50}, "context": {"primary_syndrome": "urinary", "acquisition_context": "community"}}}, governance
    )
    unknown_acquisition = scope_violations(
        {"case": {"demographics": {"age_years": 50}, "context": {"primary_syndrome": "respiratory", "acquisition_context": "unknown"}}}, governance
    )
    assert any("年龄缺失" in reason for reason in missing_age)
    assert any("最低年龄" in reason for reason in pediatric)
    assert any("不在" in reason for reason in non_respiratory)
    assert any("起病场景未确认" in reason for reason in unknown_acquisition)


def test_quality_warning_next_test_never_leaks_species_or_coinfection() -> None:
    prediction = _prediction().model_copy(update={
        "data_quality_warnings": ["关键时间不可靠"],
        "next_tests": [NextTestSuggestion(
            test_code="review-time", test_name="核对报告可见时间",
            rationale="确认该结果在决策时点前是否可见", expected_information_gain=0.8,
        )],
        "coinfection_probability": 0.4,
        "coinfection_pairs": [CoinfectionPair(
            pathogen_ids=["taxon:1313", "taxon:1280"], probability=0.4,
        )],
    })
    result = aggregate_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction), ("p2", "p2", 1.0, False, prediction)],
        [_contribution("p1", "one"), _contribution("p2", "two")],
        GovernanceConfig(), "c" * 64,
    )
    assert result.safety_action == SafetyAction.NEXT_TEST
    assert result.candidates and all(item.rank_level == RankLevel.CATEGORY for item in result.candidates)
    assert result.coinfection_pairs == []


def test_input_time_quality_violation_prevents_species_output() -> None:
    prediction = _prediction().model_copy(update={
        "next_tests": [NextTestSuggestion(
            test_code="confirm-visible-time", test_name="确认结果可见时间",
            rationale="时间闸门需要可审计时间", expected_information_gain=0.7,
        )],
    })
    result = aggregate_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction), ("p2", "p2", 1.0, False, prediction)],
        [_contribution("p1", "one"), _contribution("p2", "two")],
        GovernanceConfig(), "d" * 64,
        input_quality_violations=["检验可见时间未确认"],
    )
    assert result.safety_action == SafetyAction.NEXT_TEST
    assert all(item.rank_level == RankLevel.CATEGORY for item in result.candidates)


def test_staged_aggregation_is_exactly_equal_to_compatibility_wrapper() -> None:
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    prediction = _prediction().model_copy(update={
        "next_tests": [NextTestSuggestion(
            test_code="confirm-visible-time",
            test_name="确认结果可见时间",
            rationale="时间闸门需要可审计时间",
            expected_information_gain=0.7,
        )],
    })
    successes = [
        ("p1", "p1", 1.0, False, prediction),
        ("p2", "p2", 1.0, False, prediction),
    ]
    contributions = [_contribution("p1", "one"), _contribution("p2", "two")]
    kwargs = {
        "run_id": "run",
        "decision_time": now,
        "successes": successes,
        "contributions": contributions,
        "governance": GovernanceConfig(),
        "input_snapshot_sha256": "f" * 64,
        "input_quality_violations": ["检验可见时间未确认"],
        "development_demo": True,
    }
    with patch("app.engine.utc_now", return_value=now):
        wrapped = aggregate_predictions(**kwargs)
        draft = aggregate_raw_predictions(**kwargs)
        staged = adjudicate_aggregation(draft)
    assert staged.model_dump(mode="json") == wrapped.model_dump(mode="json")
    assert draft.candidates[0].rank_level == RankLevel.SPECIES
    assert staged.safety_action == SafetyAction.NEXT_TEST
    assert all(item.rank_level == RankLevel.CATEGORY for item in staged.candidates)
    assert staged.demo_projection is not None
    assert staged.demo_projection.candidates[0].rank_level == RankLevel.SPECIES


def test_empty_demo_draft_is_adjudicated_without_inventing_model_output() -> None:
    draft = aggregate_raw_predictions(
        "run", datetime.now(timezone.utc), [], [], GovernanceConfig(), "0" * 64,
        applicability_violations=["不在登记适用范围"],
        development_demo=True,
    )
    trace_payload = draft.to_trace_payload()
    assert trace_payload["safety_action"] is None
    assert trace_payload["safety_not_yet_adjudicated"] is True
    assert trace_payload["successful_model_count"] == 0

    result = adjudicate_aggregation(draft)
    assert result.safety_action == SafetyAction.ABSTAIN
    assert result.unknown_probability == 1.0
    assert result.demo_projection is not None
    assert result.demo_projection.candidates == []
    assert result.demo_projection.successful_model_count == 0


def test_unknown_demo_candidate_uses_registered_bilingual_category_term() -> None:
    prediction = _prediction().model_copy(update={
        "candidates": [PathogenCandidate(
            canonical_id="unknown_etiology",
            name="Unknown etiology",
            rank_level=RankLevel.UNKNOWN,
            category="unknown",
            probability=0.4,
            calibration_status="uncalibrated_model_score",
        )],
        "unknown_probability": 0.4,
    })
    result = aggregate_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction)],
        [_contribution("p1", "one")],
        GovernanceConfig(), "9" * 64,
        development_demo=True,
    )
    rendered = render_bilingual_result(result, load_clinical_terms())
    localized = rendered.demo_projection.candidates[0].display_name_i18n
    assert localized.status == "complete"
    assert localized.zh_cn == "未知或未覆盖病原"
    assert localized.en == "Unknown or uncovered pathogen"


def test_safety_stage_alone_applies_strict_calibration_downgrade() -> None:
    prediction = _prediction(calibration_status="calibrated")
    draft = aggregate_raw_predictions(
        "run", datetime.now(timezone.utc),
        [("p1", "p1", 1.0, False, prediction), ("p2", "p2", 1.0, False, prediction)],
        [_contribution("p1", "one"), _contribution("p2", "two")],
        GovernanceConfig(), "1" * 64,
    )
    assert draft.candidates[0].rank_level == RankLevel.SPECIES
    assert draft.candidates[0].calibration_status == "calibrated"

    result = adjudicate_aggregation(draft)
    assert result.safety_action == SafetyAction.CATEGORY_ONLY
    assert all(item.rank_level == RankLevel.CATEGORY for item in result.candidates)
    assert any("校准" in reason for reason in result.safety_reasons)
