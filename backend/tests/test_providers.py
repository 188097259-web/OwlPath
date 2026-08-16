import asyncio
import gc
import json
import re
import socket
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.errors import ProviderInvocationError
from app.models import (
    DataBoundary,
    DevelopmentAgentRole,
    DevelopmentCriticRequest,
    DevelopmentSourceFragment,
    DevelopmentSpecialistRequest,
    DevelopmentSpecialistResult,
    DevelopmentSpecialistRole,
    DevelopmentSynthesisDraft,
    DevelopmentSynthesisRequest,
    ProviderKind,
    validate_development_top5,
)
from app.network_security import AsyncOutboundURLValidator, validate_outbound_url
from app.providers import ProviderClient, SYSTEM_INSTRUCTION


PREDICTION: Dict[str, Any] = {
    "summary": "Synthetic schema-valid response",
    "infection_probability": 0.7,
    "syndrome_probabilities": {"respiratory": 0.8},
    "candidates": [{
        "canonical_id": "taxon:1313", "name": "Streptococcus pneumoniae",
        "rank_level": "species", "category": "bacteria", "genus": "Streptococcus",
        "species": "Streptococcus pneumoniae", "probability": 0.62,
        "calibration_status": "uncalibrated_model_score", "evidence_for": ["synthetic"],
        "evidence_against": [],
    }],
    "coinfection_probability": 0.1,
    "coinfection_pairs": [],
    "unknown_probability": 0.2,
    "next_tests": [],
    "data_quality_warnings": [],
    "distribution_shift_warning": False,
    "abstain": False,
    "abstain_reason": None,
}


def _localized(zh_cn: str, en: str) -> Dict[str, str]:
    return {"zh_cn": zh_cn, "en": en, "status": "complete"}


def _development_specialist_payload(role: str, fragment_id: str) -> Dict[str, Any]:
    pathogens = [
        ("Streptococcus pneumoniae", "肺炎链球菌", "species", "bacteria"),
        ("Legionella pneumophila", "嗜肺军团菌", "species", "bacteria"),
        ("Mycoplasma pneumoniae", "肺炎支原体", "species", "bacteria"),
        ("Klebsiella pneumoniae", "肺炎克雷伯菌", "species", "bacteria"),
        ("Influenza A virus", "甲型流感病毒", "virus_type", "virus"),
    ]
    return {
        "schema_version": "owlpath.specialist.v1",
        "role": role,
        "summary_i18n": _localized("虚构专科总结", "Synthetic specialist summary"),
        "observations": [{
            "observation_id": "obs-%s" % role,
            "kind": "key_fact",
            "statement_i18n": _localized("虚构病例事实", "Synthetic case fact"),
            "source_fragment_ids": [fragment_id],
            "importance": "high",
        }],
        "candidate_pool": [{
            "canonical_latin_name": latin,
            "name_i18n": _localized(zh_cn, latin),
            "taxonomic_rank": taxonomic_rank,
            "category": category,
            "model_score": 0.8 - rank * 0.1,
            "rationale_i18n": _localized(
                "虚构专科候选", "Synthetic specialist candidate"
            ),
            "counterevidence_i18n": _localized(
                "尚无确证", "Confirmation is pending"
            ),
            "source_fragment_ids": [fragment_id],
        } for rank, (latin, zh_cn, taxonomic_rank, category) in enumerate(
            pathogens, start=1
        )],
        "warnings": [],
    }


def _development_synthesis_payload(fragment_id: str) -> Dict[str, Any]:
    pathogens = [
        ("Streptococcus pneumoniae", "肺炎链球菌", "species", "bacteria"),
        ("Legionella pneumophila", "嗜肺军团菌", "species", "bacteria"),
        ("Mycoplasma pneumoniae", "肺炎支原体", "species", "bacteria"),
        ("Klebsiella pneumoniae", "肺炎克雷伯菌", "species", "bacteria"),
        ("Influenza A virus", "甲型流感病毒", "virus_type", "virus"),
    ]
    candidates = []
    for rank, (latin, zh_cn, taxonomic_rank, category) in enumerate(pathogens, start=1):
        candidates.append({
            "rank": rank,
            "canonical_latin_name": latin,
            "name_i18n": _localized(zh_cn, latin),
            "taxonomic_rank": taxonomic_rank,
            "category": category,
            "ncbi_taxonomy_id": None,
            "taxonomy_resolution_status": "not_checked",
            "model_score": 0.8 - rank * 0.1,
            "supporting_evidence": [{
                "statement_i18n": _localized("虚构病例支持证据", "Synthetic supporting evidence"),
                "source_fragment_ids": [fragment_id],
                "evidence_source_ids": [],
            }],
            "opposing_evidence": [],
            "why_ranked_i18n": _localized("综合征相符", "Syndrome-compatible"),
            "main_uncertainty_i18n": _localized("尚无病原学确证", "No microbiological confirmation"),
            "proposed_by_agent_roles": ["timeline_host"],
        })
    return {
        "schema_version": "owlpath.synthesis-draft.v1",
        "summary_i18n": _localized("开发总诊", "Development synthesis"),
        "concrete_pathogens": candidates,
        "category_overview": [],
        "unknown_score": 0.1,
        "coinfection_hypotheses": [],
        "next_tests": [],
        "warnings": [],
    }


def _development_critic_payload() -> Dict[str, Any]:
    return {
        "schema_version": "owlpath.critic.v1",
        "accepted": True,
        "revision_required": False,
        "review_summary_i18n": _localized("审稿通过", "Critic accepted"),
        "issues": [],
        "required_changes_i18n": [],
    }


def _development_http_payload(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the v3 contract selected by the actual HTTP system prompt."""
    messages = request_payload.get("messages") or []
    system = str((messages[0] if messages else {}).get("content") or "")
    user = str((messages[1] if len(messages) > 1 else {}).get("content") or "")
    fragment_match = re.search(r'"source_fragment_id":"([^"]+)"', user)
    fragment_id = fragment_match.group(1) if fragment_match else "fragment_001"
    role_match = re.search(r"Assigned specialist role: ([a-z_]+)", system)
    if role_match:
        return _development_specialist_payload(role_match.group(1), fragment_id)
    if "pathogen synthesis agent" in system:
        return _development_synthesis_payload(fragment_id)
    if "independent output-contract" in system:
        return _development_critic_payload()
    return PREDICTION


def _chat_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _provider(kind: ProviderKind) -> Dict[str, Any]:
    boundary = "local" if kind == ProviderKind.OLLAMA else "external"
    base = "http://127.0.0.1:11434" if kind == ProviderKind.OLLAMA else "https://provider.example/v1"
    return {
        "id": "p1", "name": kind.value, "kind": kind.value, "model": "test-model",
        "base_url": base, "extra_headers": {}, "options": {}, "weight": 1.0,
        "data_boundary": boundary,
    }


def _envelope(kind: ProviderKind) -> Dict[str, Any]:
    text = json.dumps(PREDICTION)
    if kind == ProviderKind.OPENAI_RESPONSES:
        return {"output": [{"content": [{"type": "output_text", "text": text}]}]}
    if kind == ProviderKind.ANTHROPIC_MESSAGES:
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    if kind == ProviderKind.GEMINI_GENERATE_CONTENT:
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
    if kind == ProviderKind.OPENAI_COMPATIBLE:
        return {"choices": [{"message": {"content": text}}]}
    return {"message": {"role": "assistant", "content": text}}


def test_all_provider_adapters_are_mocked_and_schema_valid() -> None:
    for kind in ProviderKind:
        captured: Dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_envelope(kind), headers={"x-request-id": "safe-id"})

        client = ProviderClient(transport=httpx.MockTransport(handler))
        key = None if kind == ProviderKind.OLLAMA else "secret-test-key"
        prediction, raw = __import__("asyncio").run(client.invoke(
            _provider(kind), key,
            {"decision_time": "2026-01-01T00:00:00Z", "case": {}, "events": []},
        ))
        assert prediction.candidates[0].canonical_id == "taxon:1313"
        assert "secret-test-key" not in json.dumps(raw)
        if kind == ProviderKind.OPENAI_RESPONSES:
            assert captured["payload"]["text"]["format"] == {"type": "json_object"}
        if kind == ProviderKind.OPENAI_COMPATIBLE:
            assert captured["payload"]["response_format"] == {"type": "json_object"}
        if kind == ProviderKind.OLLAMA:
            assert captured["payload"]["stream"] is False
            assert isinstance(captured["payload"]["format"], dict)


def test_single_call_prompt_exposes_the_safe_next_test_catalog() -> None:
    for code in (
        "respiratory-multiplex-naat",
        "respiratory-culture",
        "paired-blood-cultures",
        "urine-culture-ast",
        "csf-standard-plus-naat",
        "confirm-visible-time",
    ):
        assert code in SYSTEM_INSTRUCTION
    assert "return 1 to 5 next_tests" in SYSTEM_INSTRUCTION
    assert "Do not defer translation to another call" in SYSTEM_INSTRUCTION


def test_openai_compatible_modes() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_envelope(ProviderKind.OPENAI_COMPATIBLE))

    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    client = ProviderClient(transport=httpx.MockTransport(handler))
    for mode in ("json_schema", "prompt_only"):
        provider["options"] = {"response_format_mode": mode}
        __import__("asyncio").run(client.invoke(provider, "key", {"case": {}, "events": []}))
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in seen[1]


def test_chat_completion_token_limit_is_reported_as_truncated_not_bad_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "length",
                "message": {"role": "assistant", "content": "{\"summary\":\"partial"},
            }],
        })

    client = ProviderClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(client.invoke(
            _provider(ProviderKind.OPENAI_COMPATIBLE),
            "valid-test-key",
            {"case": {}, "events": []},
        ))
    assert raised.value.code == "provider_output_truncated"
    assert raised.value.retryable is True


def test_chat_completion_length_accepts_only_a_complete_top_level_json_object() -> None:
    complete = _chat_envelope(PREDICTION)
    complete["choices"][0]["finish_reason"] = "length"

    def complete_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=complete)

    prediction, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(complete_handler),
    ).invoke(
        _provider(ProviderKind.OPENAI_COMPATIBLE),
        "valid-test-key",
        {"case": {}, "events": []},
    ))
    assert prediction.summary == PREDICTION["summary"]

    def nested_only_handler(_request: httpx.Request) -> httpx.Response:
        # The nested object is complete, but the required top-level response is
        # not.  Recovery must not promote the nested object or add missing text.
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"outer":[{"complete_nested":true}]'},
            }],
        })

    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(nested_only_handler),
        ).invoke(
            _provider(ProviderKind.OPENAI_COMPATIBLE),
            "valid-test-key",
            {"case": {}, "events": []},
        ))
    assert raised.value.code == "provider_output_truncated"

    def complete_but_wrong_contract_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"complete":true}'},
            }],
        })

    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(complete_but_wrong_contract_handler),
        ).invoke(
            _provider(ProviderKind.OPENAI_COMPATIBLE),
            "valid-test-key",
            {"case": {}, "events": []},
        ))
    assert raised.value.code == "provider_schema_mismatch"
    validation_errors = raised.value.safe_details["validation_errors"]
    assert validation_errors
    assert all(set(item) == {"loc", "type"} for item in validation_errors)
    assert all("msg" not in item and "input" not in item for item in validation_errors)
    extra_error = next(item for item in validation_errors if item["type"] == "extra_forbidden")
    assert extra_error["loc"] == ["<extra_field>"]


def test_v3_official_deepseek_disables_thinking_and_uses_compact_contract() -> None:
    captured: Dict[str, Any] = {}
    fragment = DevelopmentSourceFragment(
        source_fragment_id="src_0001_test",
        order=1,
        section="history",
        text="Synthetic fever after water exposure.",
    )
    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY,
        source_text=fragment.text,
        source_fragments=[fragment],
        supplementary_structured_context={},
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        captured.update(payload)
        return httpx.Response(200, json=_chat_envelope(_development_specialist_payload(
            request.role.value,
            fragment.source_fragment_id,
        )))

    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    provider.update({
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
    })
    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(provider, "valid-test-key", request))

    assert result.role == request.role
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == 7000
    system_prompt = captured["messages"][0]["content"]
    assert "Required JSON output contract" in system_prompt
    assert '"candidate_pool"' in system_prompt
    assert '"role":"exposure_epidemiology"' in system_prompt
    assert '"$defs"' not in system_prompt


def test_specialist_v2_contract_emits_grounded_retrieval_concepts_and_reads_v1() -> None:
    fragment_id = "src_0001_water"
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        section="exposure",
        text="Synthetic fever after catching fish in freshwater.",
    )
    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH,
        source_text=fragment.text,
        source_fragments=[fragment],
    )
    payload = _development_specialist_payload(request.role.value, fragment_id)
    payload.update({
        "schema_version": "owlpath.specialist.v2",
        "retrieval_concepts": [{
            "kind": "exposure",
            "term_en": "  freshwater   fish exposure  ",
            "source_fragment_ids": [fragment_id],
            "negated": False,
        }],
    })
    captured: Dict[str, Any] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(http_request.content))
        return httpx.Response(200, json=_chat_envelope(payload))

    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))

    assert result.schema_version == "owlpath.specialist.v2"
    assert result.role == DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH
    assert result.retrieval_concepts[0].term_en == "freshwater fish exposure"
    assert result.retrieval_concepts[0].source_fragment_ids == [fragment_id]
    system_prompt = captured["messages"][0]["content"]
    assert '"schema_version":"owlpath.specialist.v2"' in system_prompt
    assert '"retrieval_concepts"' in system_prompt
    assert "One Health lens" in system_prompt

    legacy = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("exposure_epidemiology", fragment_id)
    )
    assert legacy.schema_version == "owlpath.specialist.v1"
    assert legacy.role == DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY
    assert legacy.retrieval_concepts == []


def test_unknown_retrieval_concept_kind_is_dropped_without_losing_specialist_output() -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("syndrome_localization", fragment_id)
    payload.update({
        "schema_version": "owlpath.specialist.v2",
        "retrieval_concepts": [
            {
                "kind": "syndrome",
                "term_en": "septic shock",
                "source_fragment_ids": [fragment_id],
                "negated": False,
            },
            {
                # Unknown provider-controlled labels are not guessed into a
                # valid class and must never reach the search connector.
                "kind": "unsupported_search_dimension",
                "term_en": "multi-organ dysfunction",
                "source_fragment_ids": [fragment_id],
                "negated": False,
            },
        ],
    })

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic fever and shock.",
    )
    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.SYNDROME_LOCALIZATION,
        source_text=fragment.text,
        source_fragments=[fragment],
    )
    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))

    assert result.role == DevelopmentSpecialistRole.SYNDROME_LOCALIZATION
    assert [item.kind.value for item in result.retrieval_concepts] == ["syndrome"]
    assert "provider_invalid_retrieval_concept_dropped" in result.warnings


def test_v2_role_sets_and_twelve_agent_provenance_contract() -> None:
    role_values = [
        "timeline_course",
        "host_susceptibility",
        "syndrome_localization",
        "exposure_one_health",
        "lab_pathophysiology",
        "organ_severity",
        "imaging_dissemination",
        "microbiology_treatment",
        "neuroinfection",
        "immunocompromised_opportunistic",
        "travel_zoonotic",
        "healthcare_device_amr",
    ]
    assert all(DevelopmentSpecialistRole(value).value == value for value in role_values)
    assert all(DevelopmentAgentRole(value).value == value for value in role_values)

    fragment_id = "src_0001_test"
    specialists = []
    for role in role_values:
        payload = _development_specialist_payload(role, fragment_id)
        payload["schema_version"] = "owlpath.specialist.v2"
        payload["retrieval_concepts"] = []
        specialists.append(DevelopmentSpecialistResult.model_validate(payload))
    request = DevelopmentSynthesisRequest(
        source_text="Synthetic fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic fever.",
        )],
        specialist_results=specialists,
        evidence_board={"version": "owlpath.evidence-board.v1"},
    )
    assert len(request.specialist_results) == 12
    assert request.evidence_board["version"] == "owlpath.evidence-board.v1"

    draft_payload = _development_synthesis_payload(fragment_id)
    draft_payload["concrete_pathogens"][0]["proposed_by_agent_roles"] = role_values
    draft = DevelopmentSynthesisDraft.model_validate(draft_payload)
    assert len(draft.concrete_pathogens[0].proposed_by_agent_roles) == 12


def test_specialist_importance_common_aliases_are_normalized_but_unknown_labels_remain_invalid() -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("timeline_host", fragment_id)
    payload["observations"] = [
        {**payload["observations"][0], "observation_id": "medium", "importance": "medium"},
        {**payload["observations"][0], "observation_id": "very-high", "importance": "very_high"},
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic fever.",
    )
    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text=fragment.text,
        source_fragments=[fragment],
    )
    client = ProviderClient(transport=httpx.MockTransport(handler))
    result, _ = __import__("asyncio").run(client.invoke_development_specialist(
        provider, "valid-test-key", request,
    ))
    assert [item.importance for item in result.observations] == ["moderate", "critical"]

    payload["observations"][0]["importance"] = "unexpected-high"
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(client.invoke_development_specialist(
            provider, "valid-test-key", request,
        ))
    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["observations", 0, "importance"],
        "type": "literal_error",
    } in raised.value.safe_details["validation_errors"]


@pytest.mark.parametrize(
    "wire_category",
    ["protozoa", "Protozoan", "protozoal", "protozoan_parasite", "protozoal-parasite"],
)
def test_specialist_normalizes_only_safe_protozoan_category_aliases(
    wire_category: str,
) -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("exposure_epidemiology", fragment_id)
    payload["candidate_pool"][0]["canonical_latin_name"] = "Plasmodium falciparum"
    payload["candidate_pool"][0]["category"] = wire_category

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY,
        source_text="Synthetic travel-associated fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic travel-associated fever.",
        )],
    )
    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert result.candidate_pool[0].category.value == "parasite"
    assert "provider_protozoan_category_normalized_to_parasite" in result.warnings


def test_specialist_category_normalization_keeps_unknown_label_invalid() -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("timeline_host", fragment_id)
    payload["candidate_pool"][0]["category"] = "mystery_protozoan_like"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text="Synthetic fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic fever.",
        )],
    )
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(handler),
        ).invoke_development_specialist(
            _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
        ))
    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["candidate_pool", 0, "category"],
        "type": "enum",
    } in raised.value.safe_details["validation_errors"]


@pytest.mark.parametrize(
    ("wire_rank", "expected_rank"),
    [
        ("species-level", "species"),
        ("species_level", "species"),
        ("species level", "species"),
        ("Species", "species"),
        ("species-complex", "species_complex"),
        ("species complex", "species_complex"),
        ("virus type", "virus_type"),
        ("virus-subtype", "virus_type"),
        ("virus_subtype", "virus_type"),
        ("virus type/subtype", "virus_type"),
        ("viral subtype", "virus_type"),
    ],
)
def test_specialist_normalizes_only_safe_taxonomic_rank_spelling_aliases(
    wire_rank: str,
    expected_rank: str,
) -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("timeline_host", fragment_id)
    payload["candidate_pool"][0]["taxonomic_rank"] = wire_rank

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text="Synthetic fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic fever.",
        )],
    )
    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert result.candidate_pool[0].taxonomic_rank.value == expected_rank
    assert "provider_taxonomic_rank_alias_normalized" in result.warnings


@pytest.mark.parametrize("wire_rank", ["strain", "serovar", "mystery_rank"])
def test_specialist_taxonomic_rank_normalization_keeps_unknown_labels_invalid(
    wire_rank: str,
) -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("timeline_host", fragment_id)
    payload["candidate_pool"][0]["taxonomic_rank"] = wire_rank

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text="Synthetic fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic fever.",
        )],
    )
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(handler),
        ).invoke_development_specialist(
            _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
        ))
    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["candidate_pool", 0, "taxonomic_rank"],
        "type": "enum",
    } in raised.value.safe_details["validation_errors"]


def test_specialist_taxonomic_rank_normalization_does_not_invent_specificity() -> None:
    fragment_id = "src_0001_test"
    payload = _development_specialist_payload("timeline_host", fragment_id)
    payload["candidate_pool"][0]["taxonomic_rank"] = "genus"
    payload["candidate_pool"][1]["taxonomic_rank"] = "category"
    payload["candidate_pool"][2]["taxonomic_rank"] = "unknown"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text="Synthetic fever.",
        source_fragments=[DevelopmentSourceFragment(
            source_fragment_id=fragment_id,
            order=1,
            text="Synthetic fever.",
        )],
    )
    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_specialist(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert [item.taxonomic_rank.value for item in result.candidate_pool[:3]] == [
        "genus", "category", "unknown",
    ]
    assert "provider_taxonomic_rank_alias_normalized" not in result.warnings


def test_v3_deepseek_thinking_override_is_validated_and_generic_provider_is_unchanged() -> None:
    seen = []
    fragment = DevelopmentSourceFragment(
        source_fragment_id="src_0001_test",
        order=1,
        text="Synthetic fever.",
    )
    request = DevelopmentSpecialistRequest(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        source_text=fragment.text,
        source_fragments=[fragment],
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(http_request.content))
        return httpx.Response(200, json=_chat_envelope(_development_specialist_payload(
            request.role.value,
            fragment.source_fragment_id,
        )))

    deepseek = _provider(ProviderKind.OPENAI_COMPATIBLE)
    deepseek.update({
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1",
        "options": {"thinking": {"type": "enabled"}},
    })
    generic = _provider(ProviderKind.OPENAI_COMPATIBLE)
    client = ProviderClient(transport=httpx.MockTransport(handler))
    __import__("asyncio").run(client.invoke_development_specialist(
        deepseek, "valid-test-key", request,
    ))
    __import__("asyncio").run(client.invoke_development_specialist(
        generic, "valid-test-key", request,
    ))
    assert seen[0]["thinking"] == {"type": "enabled"}
    assert "thinking" not in seen[1]
    assert all(
        "Candidate taxonomic_rank must be exactly species, species_complex, or virus_type"
        in payload["messages"][0]["content"]
        for payload in seen
    )

    deepseek["options"] = {"thinking": {"type": "sometimes"}}
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(client.invoke_development_specialist(
            deepseek, "valid-test-key", request,
        ))
    assert raised.value.code == "invalid_provider_option"


def test_all_v3_deepseek_agent_paths_use_non_thinking_compact_json_contracts() -> None:
    captured = []
    fragment = DevelopmentSourceFragment(
        source_fragment_id="src_0001_test",
        order=1,
        text="Synthetic fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("timeline_host", fragment.source_fragment_id)
    )
    draft = DevelopmentSynthesisDraft.model_validate(
        _development_synthesis_payload(fragment.source_fragment_id)
    )
    synthesis_request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
        evidence_board={"facts": [{"key": "fever", "state": "positive"}]},
    )
    critic_request = DevelopmentCriticRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
        evidence_board={"facts": [{"key": "fever", "state": "positive"}]},
        draft=draft,
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        captured.append(payload)
        return httpx.Response(200, json=_chat_envelope(_development_http_payload(payload)))

    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    provider.update({
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
    })
    client = ProviderClient(transport=httpx.MockTransport(handler))
    __import__("asyncio").run(client.invoke_development_synthesis(
        provider, "valid-test-key", synthesis_request,
    ))
    __import__("asyncio").run(client.invoke_development_critic(
        provider, "valid-test-key", critic_request,
    ))

    assert len(captured) == 2
    assert all(payload["thinking"] == {"type": "disabled"} for payload in captured)
    synthesis_system = captured[0]["messages"][0]["content"]
    critic_system = captured[1]["messages"][0]["content"]
    assert '"concrete_pathogens"' in synthesis_system
    assert "exactly five times" in synthesis_system
    assert "Taxonomy attestation is server-owned" in synthesis_system
    assert '"taxonomy_resolution_reason_code":"not_checked"' in synthesis_system
    assert '"ncbi_taxonomy_rank":null' in synthesis_system
    assert (
        "Every concrete_pathogens taxonomic_rank must be exactly "
        "species, species_complex, or virus_type"
    ) in synthesis_system
    assert '"revision_required"' in critic_system
    assert '"$defs"' not in synthesis_system + critic_system
    assert all(
        '"evidence_board":{"facts":[{"key":"fever","state":"positive"}]}'
        in payload["messages"][1]["content"]
        for payload in captured
    )


def test_synthesis_drops_only_untraceable_links_and_unknown_overview_with_warnings() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    payload["concrete_pathogens"][1]["opposing_evidence"] = [{
        "statement_i18n": _localized("无可追溯来源", "No traceable source"),
        "source_fragment_ids": [],
        "evidence_source_ids": [],
    }]
    payload["category_overview"] = [{
        "category": "bacteria",
        "model_score": 0.8,
        "rationale_i18n": _localized("细菌为主", "Bacterial pattern predominates"),
    }, {
        "category": "unknown",
        "model_score": 0.2,
        "rationale_i18n": _localized("未知原因", "Unknown cause"),
    }]
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("timeline_host", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_synthesis(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert result.concrete_pathogens[1].opposing_evidence == []
    assert [row.category.value for row in result.category_overview] == ["bacteria"]
    assert "provider_untraceable_evidence_link_dropped" in result.warnings
    assert "provider_unknown_category_overview_dropped" in result.warnings


def test_synthesis_resets_all_untrusted_model_taxonomy_attestations() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    supplied_attestations = [
        {
            "ncbi_taxonomy_id": 1313,
            "taxonomy_resolution_status": "resolved",
            "taxonomy_resolution_reason_code": "model_claimed_match",
            "ncbi_taxonomy_rank": "species",
        },
        {
            "ncbi_taxonomy_id": "not-an-integer",
            "taxonomy_resolution_status": "verified",
            "taxonomy_resolution_reason_code": "Provider says resolved!",
            "ncbi_taxonomy_rank": ["species"],
        },
        {
            "ncbi_taxonomy_id": -1,
            "taxonomy_resolution_status": "NCBI_MATCH",
            "taxonomy_resolution_reason_code": None,
            "ncbi_taxonomy_rank": 123,
        },
        {
            "ncbi_taxonomy_id": {"provider": "asserted"},
            "taxonomy_resolution_status": {"state": "resolved"},
            "taxonomy_resolution_reason_code": ["claimed"],
            "ncbi_taxonomy_rank": {"rank": "species"},
        },
        {
            "ncbi_taxonomy_id": True,
            "taxonomy_resolution_status": "untrusted-provider-status",
            "taxonomy_resolution_reason_code": "not_checked",
            "ncbi_taxonomy_rank": "virus",
        },
    ]
    assert len(payload["concrete_pathogens"]) == len(supplied_attestations)
    for candidate, attestation in zip(
        payload["concrete_pathogens"], supplied_attestations
    ):
        candidate.update(attestation)

    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic travel-associated fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("exposure_epidemiology", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_synthesis(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))

    assert all(item.ncbi_taxonomy_id is None for item in result.concrete_pathogens)
    assert all(
        item.taxonomy_resolution_status.value == "not_checked"
        for item in result.concrete_pathogens
    )
    assert all(
        item.taxonomy_resolution_reason_code == "not_checked"
        for item in result.concrete_pathogens
    )
    assert all(item.ncbi_taxonomy_rank is None for item in result.concrete_pathogens)
    assert result.warnings.count(
        "provider_taxonomy_attestation_reset_for_server_resolution"
    ) == 1


def test_synthesis_taxonomy_reset_does_not_hide_unrelated_schema_errors() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    for candidate in payload["concrete_pathogens"]:
        candidate["ncbi_taxonomy_id"] = "model-invented-id"
        candidate["taxonomy_resolution_status"] = "model-verified"
    payload["concrete_pathogens"][2]["model_score"] = "not-a-score"

    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic travel-associated fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("exposure_epidemiology", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(handler),
        ).invoke_development_synthesis(
            _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
        ))

    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["concrete_pathogens", 2, "model_score"],
        "type": "float_parsing",
    } in raised.value.safe_details["validation_errors"]
    assert not any(
        item["loc"][-1:] == ["taxonomy_resolution_status"]
        for item in raised.value.safe_details["validation_errors"]
    )


def test_synthesis_normalization_does_not_weaken_required_support_or_other_enums() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    payload["concrete_pathogens"][0]["supporting_evidence"] = [{
        "statement_i18n": _localized("无可追溯来源", "No traceable source"),
        "source_fragment_ids": [],
        "evidence_source_ids": [],
    }]
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("timeline_host", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    client = ProviderClient(transport=httpx.MockTransport(handler))
    result, _ = __import__("asyncio").run(client.invoke_development_synthesis(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    validation = validate_development_top5(
        result,
        valid_fragment_ids={fragment_id},
        require_taxonomy_resolution=False,
    )
    assert validation.valid is False
    assert "missing_supporting_evidence" in {issue.code for issue in validation.issues}

    payload["category_overview"] = [{
        "category": "mystery",
        "model_score": 0.1,
        "rationale_i18n": _localized("非法类别", "Invalid category"),
    }]
    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(client.invoke_development_synthesis(
            _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
        ))
    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["category_overview", 0, "category"],
        "type": "enum",
    } in raised.value.safe_details["validation_errors"]


def test_synthesis_normalizes_protozoan_candidate_and_overview_categories() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    payload["concrete_pathogens"][0]["canonical_latin_name"] = "Plasmodium falciparum"
    payload["concrete_pathogens"][0]["category"] = "protozoa"
    payload["category_overview"] = [{
        "category": "protozoan parasite",
        "model_score": 0.7,
        "rationale_i18n": _localized("疟原虫候选", "Plasmodium candidate"),
    }]
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic travel-associated fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("exposure_epidemiology", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_synthesis(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert result.concrete_pathogens[0].category.value == "parasite"
    assert result.category_overview[0].category.value == "parasite"
    assert "provider_protozoan_category_normalized_to_parasite" in result.warnings


def test_synthesis_normalizes_safe_taxonomic_rank_aliases_for_candidates() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    aliases = [
        "species-level",
        "species complex",
        "virus-subtype",
        "species_level",
        "virus type",
    ]
    expected = ["species", "species_complex", "virus_type", "species", "virus_type"]
    for candidate, alias in zip(payload["concrete_pathogens"], aliases):
        candidate["taxonomic_rank"] = alias
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic travel-associated fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("exposure_epidemiology", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    result, _ = __import__("asyncio").run(ProviderClient(
        transport=httpx.MockTransport(handler),
    ).invoke_development_synthesis(
        _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
    ))
    assert [item.taxonomic_rank.value for item in result.concrete_pathogens] == expected
    assert "provider_taxonomic_rank_alias_normalized" in result.warnings


def test_synthesis_taxonomic_rank_aliases_leave_unrecognized_rank_strict() -> None:
    fragment_id = "src_0001_test"
    payload = _development_synthesis_payload(fragment_id)
    payload["concrete_pathogens"][0]["taxonomic_rank"] = "strain"
    fragment = DevelopmentSourceFragment(
        source_fragment_id=fragment_id,
        order=1,
        text="Synthetic fever.",
    )
    specialist = DevelopmentSpecialistResult.model_validate(
        _development_specialist_payload("timeline_host", fragment_id)
    )
    request = DevelopmentSynthesisRequest(
        source_text=fragment.text,
        source_fragments=[fragment],
        specialist_results=[specialist],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope(payload))

    with pytest.raises(ProviderInvocationError) as raised:
        __import__("asyncio").run(ProviderClient(
            transport=httpx.MockTransport(handler),
        ).invoke_development_synthesis(
            _provider(ProviderKind.OPENAI_COMPATIBLE), "valid-test-key", request,
        ))
    assert raised.value.code == "provider_schema_mismatch"
    assert {
        "loc": ["concrete_pathogens", 0, "taxonomic_rank"],
        "type": "enum",
    } in raised.value.safe_details["validation_errors"]


def test_saved_provider_live_test_uses_synthetic_data_and_cost_gate(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "provider-test" in json.dumps(body)
        assert "Synthetic provider schema test" in json.dumps(body)
        return httpx.Response(200, json=_envelope(ProviderKind.OPENAI_COMPATIBLE))

    provider_client = ProviderClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(database_path=tmp_path / "provider-test.db"), provider_client=provider_client)
    with TestClient(app) as client:
        created = client.post("/api/providers", json={
            "name": "local-test", "kind": "openai_compatible", "model": "mock",
            "base_url": "http://127.0.0.1:9999/v1", "data_boundary": "local",
        }).json()
        unverified_enable = client.patch("/api/providers/%s" % created["id"], json={"enabled": True})
        assert unverified_enable.status_code == 422
        assert unverified_enable.json()["error"]["code"] == "provider_requires_successful_test_before_enable"
        blocked = client.post("/api/providers/%s/test" % created["id"], json={})
        assert blocked.status_code == 422
        tested = client.post("/api/providers/%s/test" % created["id"], json={"confirm_possible_cost": True})
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert "api_key" not in json.dumps(tested.json())
        public = client.get("/api/providers/%s" % created["id"]).json()
        assert public["last_test_ok"] is True
        assert public["last_tested_at"]
        assert public["last_test_latency_ms"] >= 0
        assert public["last_test_error_code"] is None
        assert public["enabled"] is False

        enabled = client.patch("/api/providers/%s" % created["id"], json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        assert enabled.json()["last_test_ok"] is True

        renamed = client.patch("/api/providers/%s" % created["id"], json={"name": "local-test-renamed"})
        assert renamed.status_code == 200
        assert renamed.json()["last_test_ok"] is True
        assert renamed.json()["enabled"] is True

        changed = client.patch("/api/providers/%s" % created["id"], json={"model": "mock-v2"})
        assert changed.status_code == 200
        assert changed.json()["enabled"] is False
        assert changed.json()["last_test_ok"] is None
        assert changed.json()["last_tested_at"] is None

        retested = client.post("/api/providers/%s/test" % created["id"], json={"confirm_possible_cost": True})
        assert retested.json()["ok"] is True
        assert client.patch("/api/providers/%s" % created["id"], json={"enabled": True}).status_code == 200
        changed_options = client.patch("/api/providers/%s" % created["id"], json={
            "options": {"response_format_mode": "prompt_only"},
        })
        assert changed_options.status_code == 200
        assert changed_options.json()["enabled"] is False
        assert changed_options.json()["last_test_ok"] is None


def test_development_demo_uses_provider_http_adapter_with_exact_synthetic_text(
    tmp_path: Path,
    offline_medical_retriever: Any,
) -> None:
    marker = "EXACT-SYNTHETIC-DEMO-MARKER 虚构病例"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append({
            "headers": dict(request.headers),
            "payload": payload,
        })
        return httpx.Response(
            200,
            json=_chat_envelope(_development_http_payload(payload)),
            headers={"x-request-id": "demo-http-adapter"},
        )

    provider_client = ProviderClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(database_path=tmp_path / "demo-http.db"), provider_client=provider_client)
    with TestClient(app) as client:
        app.state.engine.medical_retriever = offline_medical_retriever
        created = client.post("/api/providers", json={
            "name": "external-demo", "kind": "openai_compatible", "model": "demo-model",
            "base_url": "https://provider.example/v1", "api_key": "HTTP-DEMO-KEY",
            "data_boundary": "external",
        })
        assert created.status_code == 201
        provider_id = created.json()["id"]
        tested = client.post(
            "/api/providers/%s/test" % provider_id,
            json={"confirm_possible_cost": True},
        )
        assert tested.status_code == 200 and tested.json()["ok"] is True
        assert client.patch("/api/providers/%s" % provider_id, json={"enabled": True}).status_code == 200

        run_response = client.post("/api/development-demo/runs", json={
            "text": marker,
            "provider_ids": [provider_id],
        })
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["id"]
        run = run_response.json()
        for _ in range(100):
            run = client.get("/api/runs/%s" % run_id).json()
            if run["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert run["status"] == "completed"
        # Readiness + 5 complementary v3 core specialists + synthesis +
        # critic. No dynamic specialty is selected for this marker fixture.
        assert len(requests) == 8
        development_requests = requests[1:]
        assert all(item["headers"]["authorization"] == "Bearer HTTP-DEMO-KEY"
                   for item in development_requests)
        rendered = [json.dumps(item["payload"], ensure_ascii=False) for item in development_requests]
        assert all(marker in item for item in rendered)
        assert all("PRIMARY SOURCE TEXT" in item for item in rendered)
        assert run["result"]["schema_version"] == "owlpath.result.v3"
        assert len(run["result"]["concrete_pathogens"]) == 5
        assert "safety_action" not in run["result"]


def test_failed_provider_test_disables_previously_enabled_provider(tmp_path: Path) -> None:
    responses = [
        httpx.Response(200, json=_envelope(ProviderKind.OPENAI_COMPATIBLE)),
        httpx.Response(503, json={"error": "synthetic failure"}),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    app = create_app(
        Settings(database_path=tmp_path / "provider-test-failure.db"),
        provider_client=ProviderClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        created = client.post("/api/providers", json={
            "name": "local-test", "kind": "openai_compatible", "model": "mock",
            "base_url": "http://127.0.0.1:9999/v1", "data_boundary": "local",
        }).json()
        first = client.post("/api/providers/%s/test" % created["id"], json={"confirm_possible_cost": True})
        assert first.json()["ok"] is True
        assert client.patch("/api/providers/%s" % created["id"], json={"enabled": True}).status_code == 200

        failed = client.post("/api/providers/%s/test" % created["id"], json={"confirm_possible_cost": True})
        assert failed.status_code == 200
        assert failed.json()["ok"] is False
        public = client.get("/api/providers/%s" % created["id"]).json()
        assert public["enabled"] is False
        assert public["last_test_ok"] is False
        assert public["last_test_error_code"] == "provider_http_503"


def test_provider_test_result_is_discarded_if_settings_change_midflight(tmp_path: Path) -> None:
    state: Dict[str, Any] = {}

    def handler(_request: httpx.Request) -> httpx.Response:
        provider_id = state["provider_id"]
        changed_at = "2026-08-08T12:00:00+00:00"
        state["app"].state.db.execute(
            """UPDATE providers SET model = ?, enabled = 0, last_test_ok = NULL,
               last_tested_at = NULL, last_test_latency_ms = NULL,
               last_test_error_code = NULL, updated_at = ?, revision = revision + 1
               WHERE id = ?""",
            ("mock-v2", changed_at, provider_id),
        )
        return httpx.Response(200, json=_envelope(ProviderKind.OPENAI_COMPATIBLE))

    provider_client = ProviderClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(database_path=tmp_path / "provider-test-race.db"), provider_client=provider_client)
    state["app"] = app
    with TestClient(app) as client:
        created = client.post("/api/providers", json={
            "name": "local-test", "kind": "openai_compatible", "model": "mock",
            "base_url": "http://127.0.0.1:9999/v1", "data_boundary": "local",
        }).json()
        state["provider_id"] = created["id"]

        tested = client.post(
            "/api/providers/%s/test" % created["id"],
            json={"confirm_possible_cost": True},
        )
        assert tested.status_code == 409
        assert tested.json()["error"]["code"] == "provider_test_superseded"

        public = client.get("/api/providers/%s" % created["id"]).json()
        assert public["model"] == "mock-v2"
        assert public["enabled"] is False
        assert public["last_test_ok"] is None
        assert public["last_tested_at"] is None


def test_inflight_success_cannot_reenable_a_provider_disabled_by_the_user(tmp_path: Path) -> None:
    state: Dict[str, Any] = {"disable_during_test": False}

    def handler(_request: httpx.Request) -> httpx.Response:
        if state["disable_during_test"]:
            state["app"].state.db.execute(
                """UPDATE providers SET enabled = 0, updated_at = ?, revision = revision + 1
                   WHERE id = ?""",
                ("2026-08-08T12:00:00+00:00", state["provider_id"]),
            )
        return httpx.Response(200, json=_envelope(ProviderKind.OPENAI_COMPATIBLE))

    provider_client = ProviderClient(transport=httpx.MockTransport(handler))
    app = create_app(Settings(database_path=tmp_path / "provider-test-disable-race.db"), provider_client=provider_client)
    state["app"] = app
    with TestClient(app) as client:
        created = client.post("/api/providers", json={
            "name": "local-test", "kind": "openai_compatible", "model": "mock",
            "base_url": "http://127.0.0.1:9999/v1", "data_boundary": "local",
        }).json()
        state["provider_id"] = created["id"]

        first = client.post(
            "/api/providers/%s/test" % created["id"],
            json={"confirm_possible_cost": True},
        )
        assert first.status_code == 200
        assert client.patch("/api/providers/%s" % created["id"], json={"enabled": True}).status_code == 200
        verified_at = client.get("/api/providers/%s" % created["id"]).json()["last_tested_at"]

        state["disable_during_test"] = True
        raced = client.post(
            "/api/providers/%s/test" % created["id"],
            json={"confirm_possible_cost": True},
        )
        assert raced.status_code == 409
        assert raced.json()["error"]["code"] == "provider_test_superseded"

        public = client.get("/api/providers/%s" % created["id"]).json()
        assert public["enabled"] is False
        assert public["last_test_ok"] is True
        assert public["last_tested_at"] == verified_at


def test_external_provider_requires_https_and_global_destination() -> None:
    with pytest.raises(ProviderInvocationError) as plaintext:
        validate_outbound_url(
            "http://provider.example/v1", DataBoundary.EXTERNAL, resolve_dns=False,
        )
    assert plaintext.value.code == "external_provider_requires_https"

    with pytest.raises(ProviderInvocationError) as cgnat:
        validate_outbound_url(
            "https://100.64.0.1/v1", DataBoundary.EXTERNAL, resolve_dns=False,
        )
    assert cgnat.value.code == "unsafe_provider_url"

    validate_outbound_url(
        "http://127.0.0.1:11434/v1", DataBoundary.LOCAL, resolve_dns=False,
    )


def test_external_dns_egress_proxy_cidrs_allow_only_resolved_fake_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly acknowledged sandbox fake-IP ranges relax DNS validation only.

    Some research sandboxes answer every external DNS query with a fake-IP
    proxy address such as 198.18.1.157. The operator can acknowledge those
    CIDRs through OWLPATH_ALLOW_EGRESS_PROXY_CIDRS; literal provider URLs
    inside the same range must remain rejected.
    """

    monkeypatch.setenv("OWLPATH_ALLOW_EGRESS_PROXY_CIDRS", "198.18.0.0/15")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.157", 443))
        ],
    )

    validate_outbound_url(
        "https://provider.example/v1", DataBoundary.EXTERNAL, resolve_dns=True,
    )
    with pytest.raises(ProviderInvocationError) as literal:
        validate_outbound_url(
            "https://198.18.1.157/v1", DataBoundary.EXTERNAL, resolve_dns=False,
        )
    assert literal.value.code == "unsafe_provider_url"


def test_provider_dns_validation_does_not_block_the_asyncio_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled resolver must not freeze API responses or unrelated tasks."""

    resolver_entered = threading.Event()
    release_resolver = threading.Event()
    resolver_stalled_the_loop = threading.Event()

    def stalled_getaddrinfo(*_args: Any, **_kwargs: Any) -> Any:
        resolver_entered.set()
        if not release_resolver.wait(timeout=2.0):
            resolver_stalled_the_loop.set()
        raise socket.gaierror(socket.EAI_AGAIN, "synthetic temporary DNS failure")

    monkeypatch.setattr(socket, "getaddrinfo", stalled_getaddrinfo)

    async def scenario() -> None:
        invocation = asyncio.create_task(ProviderClient().invoke(
            _provider(ProviderKind.OPENAI_COMPATIBLE),
            "synthetic-test-key",
            {"case": {}, "events": []},
        ))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        while not resolver_entered.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.005)

        # With a synchronous getaddrinfo call on this loop, the resolver's
        # two-second timeout fires before this coroutine can run again.
        loop_remained_responsive = (
            resolver_entered.is_set() and not resolver_stalled_the_loop.is_set()
        )
        release_resolver.set()
        with pytest.raises(ProviderInvocationError) as raised:
            await invocation
        assert raised.value.code == "provider_dns_error"
        assert loop_remained_responsive is True

    try:
        asyncio.run(scenario())
    finally:
        release_resolver.set()


def test_external_dns_validation_is_single_flight_for_concurrent_waiters_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    calls_lock = threading.Lock()

    def public_resolver(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    validator = AsyncOutboundURLValidator(
        dns_retry_backoff_seconds=0.0,
        dns_attempt_timeout_seconds=0.5,
    )

    async def scenario() -> None:
        await asyncio.gather(*[
            validator.validate(
                "https://provider.example:443/v1/chat/completions",
                DataBoundary.EXTERNAL,
            )
            for _ in range(5)
        ])
        assert calls == 1
        # Once the shared task completes it is removed. A later call must
        # resolve again even when host, port and path are unchanged.
        await validator.validate(
            "https://provider.example:443/v1/chat/completions",
            DataBoundary.EXTERNAL,
        )
        assert calls == 2
        await validator.validate(
            "https://provider.example:444/v1/chat/completions",
            DataBoundary.EXTERNAL,
        )
        assert calls == 3
        with pytest.raises(ProviderInvocationError) as credentials:
            await validator.validate(
                "https://user:password@provider.example:443/v1",
                DataBoundary.EXTERNAL,
            )
        assert credentials.value.code == "unsafe_provider_url"
        assert calls == 3

    asyncio.run(scenario())


def test_external_dns_public_to_private_rebinding_is_rechecked_and_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def rebinding_resolver(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        address = "93.184.216.34" if calls == 1 else "127.0.0.1"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_resolver)
    validator = AsyncOutboundURLValidator(
        dns_retry_backoff_seconds=0.0,
        dns_attempt_timeout_seconds=0.5,
    )

    async def scenario() -> None:
        await validator.validate(
            "https://provider.example/v1", DataBoundary.EXTERNAL
        )
        with pytest.raises(ProviderInvocationError) as rebound:
            await validator.validate(
                "https://provider.example/v1", DataBoundary.EXTERNAL
            )
        assert rebound.value.code == "unsafe_provider_url"
        assert calls == 2

    asyncio.run(scenario())


def test_outbound_validator_can_be_reused_across_distinct_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def public_resolver(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    validator = AsyncOutboundURLValidator()
    loop_references: list[weakref.ReferenceType[asyncio.AbstractEventLoop]] = []

    async def validate_once() -> None:
        loop_references.append(weakref.ref(asyncio.get_running_loop()))
        await validator.validate(
            "https://provider.example/v1", DataBoundary.EXTERNAL
        )

    asyncio.run(validate_once())
    asyncio.run(validate_once())
    # Async locks/tasks are loop-scoped; completed validation state from a
    # closed loop is neither reused nor retained by a new loop.
    assert calls == 2
    for _ in range(3):
        gc.collect()
    assert all(reference() is None for reference in loop_references)
    assert len(validator._states) == 0


def test_external_dns_transient_failure_retries_once_for_all_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def transient_then_public(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise socket.gaierror(socket.EAI_AGAIN, "synthetic temporary failure")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", transient_then_public)
    validator = AsyncOutboundURLValidator(
        dns_retry_backoff_seconds=0.0,
        dns_attempt_timeout_seconds=0.5,
    )

    async def scenario() -> None:
        await asyncio.gather(*[
            validator.validate("https://provider.example/v1", DataBoundary.EXTERNAL)
            for _ in range(5)
        ])
        assert calls == 2

    asyncio.run(scenario())


def test_external_dns_timeout_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def timeout_then_public(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("synthetic resolver timeout")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", timeout_then_public)
    validator = AsyncOutboundURLValidator(
        dns_retry_backoff_seconds=0.0,
        dns_attempt_timeout_seconds=0.5,
    )
    asyncio.run(validator.validate(
        "https://provider.example/v1", DataBoundary.EXTERNAL
    ))
    assert calls == 2


def test_external_dns_failures_are_not_cached_and_private_results_stay_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def private_resolver(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        time.sleep(0.01)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", private_resolver)
    validator = AsyncOutboundURLValidator(
        dns_retry_backoff_seconds=0.0,
        dns_attempt_timeout_seconds=0.5,
    )

    async def scenario() -> None:
        results = await asyncio.gather(*[
            validator.validate("https://provider.example/v1", DataBoundary.EXTERNAL)
            for _ in range(4)
        ], return_exceptions=True)
        assert calls == 1
        assert all(
            isinstance(item, ProviderInvocationError)
            and item.code == "unsafe_provider_url"
            for item in results
        )
        with pytest.raises(ProviderInvocationError) as second:
            await validator.validate(
                "https://provider.example/v1", DataBoundary.EXTERNAL
            )
        assert second.value.code == "unsafe_provider_url"
        assert calls == 2

    asyncio.run(scenario())


def test_development_provider_concurrency_slot_caps_same_provider_at_three() -> None:
    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    provider["id"] = "provider-concurrency-test"
    client = ProviderClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        max_concurrent_requests_per_provider=3,
    )

    async def scenario() -> None:
        active = 0
        maximum_active = 0
        three_entered = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> None:
            nonlocal active, maximum_active
            lease = await client.acquire_development_request_slot(provider)
            try:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 3:
                    three_entered.set()
                await release.wait()
            finally:
                active -= 1
                lease.release()

        tasks = [asyncio.create_task(worker()) for _ in range(5)]
        await asyncio.wait_for(three_entered.wait(), timeout=0.5)
        await asyncio.sleep(0.02)
        assert maximum_active == 3
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_cancelled_provider_slot_waiter_does_not_leak_capacity() -> None:
    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    provider["id"] = "provider-cancelled-waiter-test"
    client = ProviderClient(max_concurrent_requests_per_provider=1)

    async def scenario() -> None:
        first = await client.acquire_development_request_slot(provider)
        blocked = asyncio.create_task(
            client.acquire_development_request_slot(provider)
        )
        await asyncio.sleep(0)
        assert blocked.done() is False
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        first.release()

        replacement = await asyncio.wait_for(
            client.acquire_development_request_slot(provider),
            timeout=0.2,
        )
        replacement.release()

    asyncio.run(scenario())


def test_provider_semaphore_registry_releases_closed_contended_event_loops() -> None:
    provider = _provider(ProviderKind.OPENAI_COMPATIBLE)
    provider["id"] = "provider-loop-gc-test"
    client = ProviderClient(max_concurrent_requests_per_provider=1)
    loop_references: list[weakref.ReferenceType[asyncio.AbstractEventLoop]] = []

    async def contend_once() -> None:
        loop_references.append(weakref.ref(asyncio.get_running_loop()))
        first = await client.acquire_development_request_slot(provider)
        waiter = asyncio.create_task(
            client.acquire_development_request_slot(provider)
        )
        await asyncio.sleep(0)
        assert waiter.done() is False
        first.release()
        second = await waiter
        second.release()

    asyncio.run(contend_once())
    asyncio.run(contend_once())
    for _ in range(3):
        gc.collect()
    assert all(reference() is None for reference in loop_references)
    assert len(client._request_semaphores) == 0


def test_provider_timeout_exposes_only_safe_timeout_phase() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("UNTRUSTED RAW TRANSPORT TEXT", request=request)

    async def scenario() -> None:
        with pytest.raises(ProviderInvocationError) as raised:
            await ProviderClient(
                transport=httpx.MockTransport(handler)
            ).invoke(
                _provider(ProviderKind.OPENAI_COMPATIBLE),
                "synthetic-test-key",
                {"case": {}, "events": []},
            )
        assert raised.value.code == "provider_timeout"
        assert raised.value.safe_details == {
            "timeout_phase": "http_read",
            "request_dispatched": True,
        }
        assert "UNTRUSTED" not in json.dumps(raised.value.safe_payload())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [(400, False), (429, True), (503, True)],
)
def test_provider_http_retryability_is_limited_to_429_and_5xx(
    status_code: int,
    expected_retryable: bool,
) -> None:
    async def scenario() -> None:
        with pytest.raises(ProviderInvocationError) as raised:
            await ProviderClient(transport=httpx.MockTransport(
                lambda _request: httpx.Response(status_code, json={"error": "x"})
            )).invoke(
                _provider(ProviderKind.OPENAI_COMPATIBLE),
                "synthetic-test-key",
                {"case": {}, "events": []},
            )
        assert raised.value.code == "provider_http_%d" % status_code
        assert raised.value.retryable is expected_retryable

    asyncio.run(scenario())
