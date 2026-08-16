"""Focused offline tests for evidence-domain and retrieval provenance rules."""

from __future__ import annotations

from app.engine import build_agent_pool_fallback, build_development_evidence_board
from app.medical_retrieval import build_federated_query_plan
from app.models import (
    DevelopmentPathogenCategory,
    DevelopmentPathogenProposal,
    DevelopmentRetrievalConcept,
    DevelopmentSpecialistResult,
    DevelopmentSpecialistRole,
    DevelopmentSynthesisDraft,
    DevelopmentTaxonomicRank,
    LocalizedText,
)


def _lt(value: str) -> LocalizedText:
    return LocalizedText(zh_cn=value, en=value, status="complete")


def _proposal(name: str, score: float, *fragments: str) -> DevelopmentPathogenProposal:
    return DevelopmentPathogenProposal(
        canonical_latin_name=name,
        name_i18n=_lt(name),
        taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
        category=DevelopmentPathogenCategory.BACTERIA,
        model_score=score,
        rationale_i18n=_lt("synthetic evidence"),
        source_fragment_ids=list(fragments),
    )


def _result(
    role: DevelopmentSpecialistRole,
    *proposals: DevelopmentPathogenProposal,
    concepts: list[DevelopmentRetrievalConcept] | None = None,
) -> DevelopmentSpecialistResult:
    return DevelopmentSpecialistResult(
        role=role,
        summary_i18n=_lt("synthetic"),
        candidate_pool=list(proposals),
        retrieval_concepts=concepts or [],
    )


def test_evidence_board_does_not_count_challenge_agent_duplicate_as_new_domain() -> None:
    results = [
        _result(
            DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH,
            _proposal("Pathogen alpha", 0.80, "src_a"),
            _proposal("Pathogen beta", 0.60, "src_a"),
        ),
        _result(
            DevelopmentSpecialistRole.TRAVEL_ZOONOTIC,
            _proposal("Pathogen alpha", 0.99, "src_a"),
        ),
        _result(
            DevelopmentSpecialistRole.SYNDROME_LOCALIZATION,
            _proposal("Pathogen beta", 0.60, "src_b"),
        ),
    ]

    board = build_development_evidence_board(
        results,
        valid_fragment_ids={"src_a", "src_b"},
    )
    by_name = {
        item["canonical_latin_name"]: item
        for item in board["candidate_hypotheses"]
    }

    alpha = by_name["Pathogen alpha"]
    assert alpha["proposing_roles"] == ["exposure_one_health", "travel_zoonotic"]
    assert alpha["independent_evidence_domain_count"] == 1
    assert alpha["unique_evidence_domain_count"] == 1
    # The overlapping challenge role adds provenance, not a score sample.
    assert alpha["independent_score_claim_count"] == 1
    assert alpha["mean_model_score"] == 0.80
    assert by_name["Pathogen beta"]["independent_evidence_domain_count"] == 2


def test_agent_pool_fallback_ranks_domains_and_unique_facts_not_agent_votes() -> None:
    results = [
        _result(
            DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH,
            _proposal("Pathogen alpha", 0.95, "src_a"),
            _proposal("Pathogen beta", 0.55, "src_a"),
        ),
        _result(
            DevelopmentSpecialistRole.TRAVEL_ZOONOTIC,
            _proposal("Pathogen alpha", 0.95, "src_a"),
        ),
        _result(
            DevelopmentSpecialistRole.SYNDROME_LOCALIZATION,
            _proposal("Pathogen beta", 0.55, "src_b"),
        ),
    ]
    draft = DevelopmentSynthesisDraft(summary_i18n=_lt("fallback"), unknown_score=0.1)

    fallback = build_agent_pool_fallback(
        results,
        draft,
        valid_fragment_ids={"src_a", "src_b"},
    )

    assert fallback.concrete_pathogens[0].canonical_latin_name == "Pathogen beta"
    explanation = fallback.concrete_pathogens[0].why_ranked_i18n.en
    assert "2 independent frozen evidence domains" in explanation
    assert "duplicate Agent claims add no vote" in explanation


def test_retrieval_plan_consumes_only_manifest_grounded_non_negated_board_items() -> None:
    valid = "src_valid"
    results = [_result(
        DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH,
        _proposal("Vibrio vulnificus", 0.8, valid),
        _proposal("Invented organism", 0.9, "src_unknown"),
        concepts=[
            DevelopmentRetrievalConcept(
                kind="exposure",
                term_en="seawater fish exposure",
                source_fragment_ids=[valid],
            ),
            DevelopmentRetrievalConcept(
                kind="syndrome",
                term_en="invented syndrome",
                source_fragment_ids=["src_unknown"],
            ),
            DevelopmentRetrievalConcept(
                kind="host_factor",
                term_en="neutropenia",
                source_fragment_ids=[valid],
                negated=True,
            ),
            DevelopmentRetrievalConcept(
                kind="anatomy",
                term_en="missing provenance",
                source_fragment_ids=[],
            ),
        ],
    )]

    board = build_development_evidence_board(results, valid_fragment_ids={valid})
    audit = board["retrieval_concept_audit"]
    assert audit["input_count"] == 4
    assert audit["accepted_unique_count"] == 1
    assert audit["discarded_by_reason"] == {
        "negated": 1,
        "missing_source_fragment": 1,
        "unknown_source_fragment_reference": 1,
    }
    assert board["candidate_hypothesis_audit"]["discarded_by_reason"][
        "unknown_source_fragment_reference"
    ] == 1

    plan = build_federated_query_plan(
        [{
            "retrieval_concepts": board["retrieval_concepts"],
            "candidate_hypotheses": board["candidate_hypotheses"],
        }],
        valid_fragment_ids={valid},
    )
    outbound_queries = " ".join(item.query for item in plan).casefold()
    assert "seawater fish exposure" in outbound_queries
    assert "vibrio vulnificus" in outbound_queries
    assert "invented" not in outbound_queries
    assert "neutropenia" not in outbound_queries
    assert "missing provenance" not in outbound_queries
    public_payload = [item.public_payload() for item in plan]
    assert all("query" not in item and "source_fragment_ids" not in item for item in public_payload)
