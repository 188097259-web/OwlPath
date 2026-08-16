import asyncio
import hashlib
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.db import sha256_json
from app.errors import ProviderInvocationError
import app.engine as engine_module
from app.engine import (
    DEVELOPMENT_EXECUTION_GRAPH_VERSION,
    DEVELOPMENT_TRACE_VERSION,
    build_agent_pool_fallback,
    build_resolved_agent_pool_fallback,
    build_development_execution_manifest,
    build_execution_manifest,
    reconcile_development_critic_result,
    reconcile_development_pathogen_provenance,
)
from app.models import (
    DevelopmentAgentObservationSummary,
    DevelopmentAgentRole,
    DevelopmentCategoryOverview,
    DevelopmentConcretePathogen,
    DevelopmentContractIssue,
    DevelopmentCriticIssue,
    DevelopmentCriticResult,
    DevelopmentDraftPathogen,
    DevelopmentEvidenceLink,
    DevelopmentPathogenCategory,
    DevelopmentPathogenProposal,
    DevelopmentResultV3,
    DevelopmentReviewSummary,
    DevelopmentSpecialistObservation,
    DevelopmentSpecialistResult,
    DevelopmentSpecialistRole,
    DevelopmentSynthesisDraft,
    DevelopmentTaxonomicRank,
    DevelopmentTaxonomyResolutionStatus,
    DevelopmentTop5Validation,
    LocalizedText,
    validate_development_top5,
)
from app.medical_retrieval import (
    EUROPE_PMC_SEARCH_URL,
    NCBI_PUBMED_SEARCH_URL,
    MedicalEvidenceRetriever,
    RetrievalBundle,
)
from app.main import create_app
from app.providers import ProviderClient


def _lt(zh_cn: str, en: str) -> LocalizedText:
    return LocalizedText(zh_cn=zh_cn, en=en, status="complete")


def _evidence(fragment_id: str = "fragment_001") -> DevelopmentEvidenceLink:
    return DevelopmentEvidenceLink(
        statement_i18n=_lt("虚构病例证据", "Synthetic case evidence"),
        source_fragment_ids=[fragment_id],
    )


def test_agent_pool_fallback_publishes_monotonic_transparent_rank_scores() -> None:
    def proposal(name: str, score: float, fragment: str) -> DevelopmentPathogenProposal:
        return DevelopmentPathogenProposal(
            canonical_latin_name=name,
            name_i18n=_lt(name, name),
            taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=score,
            rationale_i18n=_lt("纯虚构支持证据", "Synthetic supporting evidence"),
            source_fragment_ids=[fragment],
        )

    roles = list(DevelopmentSpecialistRole)
    specialist_results = [
        DevelopmentSpecialistResult(
            role=roles[0],
            summary_i18n=_lt("纯虚构", "Synthetic"),
            candidate_pool=[
                proposal("Pathogen alpha", 0.20, "fragment_001"),
                proposal("Pathogen beta", 0.95, "fragment_002"),
                proposal("Pathogen gamma", 0.70, "fragment_003"),
                proposal("Pathogen delta", 0.60, "fragment_004"),
                proposal("Pathogen epsilon", 0.50, "fragment_005"),
            ],
        ),
        DevelopmentSpecialistResult(
            role=roles[1],
            summary_i18n=_lt("纯虚构", "Synthetic"),
            candidate_pool=[proposal("Pathogen alpha", 0.20, "fragment_006")],
        ),
    ]
    base = DevelopmentSynthesisDraft(
        summary_i18n=_lt("纯虚构回退", "Synthetic fallback"),
        unknown_score=0.2,
    )

    fallback = build_agent_pool_fallback(specialist_results, base)
    scores = [item.model_score for item in fallback.concrete_pathogens]

    assert fallback.concrete_pathogens[0].canonical_latin_name == "Pathogen alpha"
    assert scores == sorted(scores, reverse=True)
    assert "2 个独立冻结证据域" in fallback.concrete_pathogens[0].why_ranked_i18n.zh_cn
    assert "重复 Agent 主张不加票" in fallback.concrete_pathogens[0].why_ranked_i18n.zh_cn
    assert "agent_pool_fallback_score_is_deterministic_rank_score" in fallback.warnings
    validation = validate_development_top5(
        fallback,
        valid_fragment_ids={"fragment_%03d" % index for index in range(1, 7)},
        require_taxonomy_resolution=False,
    )
    assert "score_order" not in {item.code for item in validation.issues}


def test_resolved_agent_pool_fallback_skips_invalid_leaders_and_backfills_top5(
) -> None:
    def proposal(
        name: str,
        score: float,
        *,
        rank: DevelopmentTaxonomicRank = DevelopmentTaxonomicRank.SPECIES,
    ) -> DevelopmentPathogenProposal:
        return DevelopmentPathogenProposal(
            canonical_latin_name=name,
            name_i18n=_lt(name, name),
            taxonomic_rank=rank,
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=score,
            rationale_i18n=_lt("纯虚构支持证据", "Synthetic supporting evidence"),
            source_fragment_ids=["fragment_001"],
        )

    concrete = [
        ("Escherichia coli", 562),
        ("Klebsiella pneumoniae", 573),
        ("Bacteroides fragilis", 817),
        ("Enterococcus faecalis", 1351),
        ("Clostridium perfringens", 1502),
    ]
    specialist_results = [DevelopmentSpecialistResult(
        role=DevelopmentSpecialistRole.SYNDROME_SITE,
        summary_i18n=_lt("纯虚构", "Synthetic"),
        candidate_pool=[
            proposal("Multiple organisms", 0.99),
            proposal("Anaerobic bacteria", 0.98),
            # The model falsely declares this genus as a species.  A verified
            # non-concrete NCBI rank must still keep it out of publication.
            proposal("Cytomegalovirus", 0.97),
            proposal("Unresolved examplevirus", 0.96),
            *[
                proposal(name, 0.90 - (index * 0.05))
                for index, (name, _taxonomy_id) in enumerate(concrete)
            ],
        ],
    )]

    class ExpandedPoolTaxonomy:
        def __init__(self) -> None:
            self.requested_names: list[str] = []

        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            self.requested_names = list(names)
            records: dict[str, dict[str, Any]] = {
                "cytomegalovirus": {
                    "ncbi_taxonomy_id": 10358,
                    "taxonomy_resolution_status": "resolved",
                    "taxonomy_resolution_reason_code": "synthetic_genus_match",
                    "canonical_latin_name": "Cytomegalovirus",
                    "name_i18n": {"en": "Cytomegalovirus", "status": "partial"},
                    "ncbi_taxonomy_rank": "genus",
                },
                "unresolved examplevirus": {
                    "ncbi_taxonomy_id": None,
                    "taxonomy_resolution_status": "unresolved",
                    "taxonomy_resolution_reason_code": "synthetic_not_found",
                    "canonical_latin_name": "Unresolved examplevirus",
                    "name_i18n": {
                        "en": "Unresolved examplevirus", "status": "partial",
                    },
                    "ncbi_taxonomy_rank": None,
                },
            }
            for name, taxonomy_id in concrete:
                records[name.casefold()] = {
                    "ncbi_taxonomy_id": taxonomy_id,
                    "taxonomy_resolution_status": "resolved",
                    "taxonomy_resolution_reason_code": "synthetic_species_match",
                    "canonical_latin_name": name,
                    "name_i18n": {"en": name, "status": "partial"},
                    "ncbi_taxonomy_rank": "species",
                }
            return records

    resolver = ExpandedPoolTaxonomy()
    base = DevelopmentSynthesisDraft(
        summary_i18n=_lt("纯虚构回退", "Synthetic fallback"),
        unknown_score=0.5,
    )
    fallback, audit = asyncio.run(build_resolved_agent_pool_fallback(
        specialist_results,
        base,
        resolver,  # type: ignore[arg-type]
        valid_fragment_ids={"fragment_001"},
    ))

    assert resolver.requested_names == [
        "Cytomegalovirus",
        "Unresolved examplevirus",
        *[item[0] for item in concrete],
    ]
    assert [item.canonical_latin_name for item in fallback.concrete_pathogens] == [
        item[0] for item in concrete
    ]
    assert [item.rank for item in fallback.concrete_pathogens] == [1, 2, 3, 4, 5]
    assert [item.model_score for item in fallback.concrete_pathogens] == sorted(
        [item.model_score for item in fallback.concrete_pathogens],
        reverse=True,
    )
    assert all(item.ncbi_taxonomy_id for item in fallback.concrete_pathogens)
    assert audit["selected_candidate_count"] == 5
    assert audit["expanded_candidate_count"] == 7
    assert {
        reason
        for item in audit["input_exclusions"]
        for reason in item["reason_codes"]
    } == {"obvious_generic_group_label"}
    excluded = [
        item for item in audit["candidates"]
        if item["disposition"] == "excluded"
    ]
    excluded_reason_codes = {
        reason for item in excluded for reason in item["reason_codes"]
    }
    assert "ncbi_taxonomy_rank_not_concrete" in excluded_reason_codes
    assert "taxonomy_not_resolved" in excluded_reason_codes
    assert "taxonomy_id_missing_or_invalid" in excluded_reason_codes
    assert all("canonical_latin_name" not in item for item in excluded)
    assert "agent_pool_fallback_taxonomy_backfill_applied" in fallback.warnings
    assert validate_development_top5(
        fallback,
        valid_fragment_ids={"fragment_001"},
        require_taxonomy_resolution=True,
    ).valid


def test_resolved_agent_pool_fallback_with_fewer_than_five_valid_stays_invalid(
) -> None:
    def proposal(name: str, score: float) -> DevelopmentPathogenProposal:
        return DevelopmentPathogenProposal(
            canonical_latin_name=name,
            name_i18n=_lt(name, name),
            taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=score,
            rationale_i18n=_lt("纯虚构支持证据", "Synthetic supporting evidence"),
            source_fragment_ids=["fragment_001"],
        )

    specialist_results = [DevelopmentSpecialistResult(
        role=DevelopmentSpecialistRole.SYNDROME_SITE,
        summary_i18n=_lt("纯虚构", "Synthetic"),
        candidate_pool=[
            proposal("Multiple organisms", 0.99),
            proposal("Cytomegalovirus", 0.95),
            proposal("Escherichia coli", 0.80),
            proposal("Klebsiella pneumoniae", 0.70),
        ],
    )]

    class SparseTaxonomy:
        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            records: dict[str, dict[str, Any]] = {}
            for name, taxonomy_id in (
                ("Escherichia coli", 562),
                ("Klebsiella pneumoniae", 573),
            ):
                records[name.casefold()] = {
                    "ncbi_taxonomy_id": taxonomy_id,
                    "taxonomy_resolution_status": "resolved",
                    "taxonomy_resolution_reason_code": "synthetic_species_match",
                    "canonical_latin_name": name,
                    "name_i18n": {"en": name, "status": "partial"},
                    "ncbi_taxonomy_rank": "species",
                }
            records["cytomegalovirus"] = {
                "ncbi_taxonomy_id": None,
                "taxonomy_resolution_status": "unresolved",
                "taxonomy_resolution_reason_code": "synthetic_genus_rejected",
                "canonical_latin_name": "Cytomegalovirus",
                "name_i18n": {"en": "Cytomegalovirus", "status": "partial"},
                "ncbi_taxonomy_rank": None,
            }
            return records

    fallback, audit = asyncio.run(build_resolved_agent_pool_fallback(
        specialist_results,
        DevelopmentSynthesisDraft(
            summary_i18n=_lt("纯虚构回退", "Synthetic fallback"),
            unknown_score=0.8,
        ),
        SparseTaxonomy(),  # type: ignore[arg-type]
        valid_fragment_ids={"fragment_001"},
    ))
    validation = validate_development_top5(
        fallback,
        valid_fragment_ids={"fragment_001"},
        require_taxonomy_resolution=True,
    )

    assert len(fallback.concrete_pathogens) == 2
    assert not validation.valid
    assert "top5_count" in {item.code for item in validation.issues}
    assert audit["selected_candidate_count"] == 2
    assert "agent_pool_fallback_insufficient_resolved_concrete_candidates" in (
        fallback.warnings
    )


def test_resolved_agent_pool_fallback_deduplicates_resolved_aliases_and_backfills(
) -> None:
    def proposal(name: str, score: float) -> DevelopmentPathogenProposal:
        return DevelopmentPathogenProposal(
            canonical_latin_name=name,
            name_i18n=_lt(name, name),
            taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=score,
            rationale_i18n=_lt("纯虚构支持证据", "Synthetic supporting evidence"),
            source_fragment_ids=["fragment_001"],
        )

    proposed_names = [
        "E. coli",
        "Escherichia coli",
        "Klebsiella pneumoniae",
        "Staphylococcus aureus",
        "Pseudomonas aeruginosa",
        "Enterococcus faecalis",
        "Streptococcus pneumoniae",
    ]
    canonical_records = {
        "e. coli": ("Escherichia coli", 562),
        "escherichia coli": ("Escherichia coli", 562),
        "klebsiella pneumoniae": ("Klebsiella pneumoniae", 573),
        "staphylococcus aureus": ("Staphylococcus aureus", 1280),
        "pseudomonas aeruginosa": ("Pseudomonas aeruginosa", 287),
        "enterococcus faecalis": ("Enterococcus faecalis", 1351),
        "streptococcus pneumoniae": ("Streptococcus pneumoniae", 1313),
    }

    class AliasTaxonomy:
        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            return {
                name.casefold(): {
                    "ncbi_taxonomy_id": canonical_records[name.casefold()][1],
                    "taxonomy_resolution_status": "resolved",
                    "taxonomy_resolution_reason_code": "synthetic_species_match",
                    "canonical_latin_name": canonical_records[name.casefold()][0],
                    "name_i18n": {
                        "en": canonical_records[name.casefold()][0], "status": "partial",
                    },
                    "ncbi_taxonomy_rank": "species",
                }
                for name in names
            }

    specialist_results = [DevelopmentSpecialistResult(
        role=DevelopmentSpecialistRole.SYNDROME_SITE,
        summary_i18n=_lt("纯虚构", "Synthetic"),
        candidate_pool=[
            proposal(name, 0.99 - (index * 0.05))
            for index, name in enumerate(proposed_names)
        ],
    )]
    fallback, audit = asyncio.run(build_resolved_agent_pool_fallback(
        specialist_results,
        DevelopmentSynthesisDraft(
            summary_i18n=_lt("纯虚构回退", "Synthetic fallback"),
            unknown_score=0.5,
        ),
        AliasTaxonomy(),  # type: ignore[arg-type]
        valid_fragment_ids={"fragment_001"},
    ))

    assert [item.canonical_latin_name for item in fallback.concrete_pathogens] == [
        "Escherichia coli",
        "Klebsiella pneumoniae",
        "Staphylococcus aureus",
        "Pseudomonas aeruginosa",
        "Enterococcus faecalis",
    ]
    assert len({item.ncbi_taxonomy_id for item in fallback.concrete_pathogens}) == 5
    assert validate_development_top5(
        fallback,
        valid_fragment_ids={"fragment_001"},
    ).valid
    duplicate_rows = [
        item for item in audit["candidates"]
        if "duplicate_after_taxonomy_resolution" in item["reason_codes"]
    ]
    assert len(duplicate_rows) == 1
    assert audit["selected_candidate_count"] == 5
    assert "agent_pool_fallback_taxonomy_backfill_applied" in fallback.warnings
    rendered_audit = json.dumps(audit, ensure_ascii=False)
    assert "E. coli" not in rendered_audit
    assert "Escherichia coli" not in rendered_audit


def test_resolved_agent_pool_fallback_excludes_unknown_fragments_and_backfills(
) -> None:
    def proposal(name: str, score: float, fragment_id: str) -> DevelopmentPathogenProposal:
        return DevelopmentPathogenProposal(
            canonical_latin_name=name,
            name_i18n=_lt(name, name),
            taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=score,
            rationale_i18n=_lt("纯虚构支持证据", "Synthetic supporting evidence"),
            source_fragment_ids=[fragment_id],
        )

    valid_records = [
        ("Escherichia coli", 562),
        ("Klebsiella pneumoniae", 573),
        ("Staphylococcus aureus", 1280),
        ("Pseudomonas aeruginosa", 287),
        ("Enterococcus faecalis", 1351),
    ]

    class ManifestAwareTaxonomy:
        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            record_by_name = {name.casefold(): taxonomy_id for name, taxonomy_id in valid_records}
            return {
                name.casefold(): {
                    "ncbi_taxonomy_id": record_by_name[name.casefold()],
                    "taxonomy_resolution_status": "resolved",
                    "taxonomy_resolution_reason_code": "synthetic_species_match",
                    "canonical_latin_name": name,
                    "name_i18n": {"en": name, "status": "partial"},
                    "ncbi_taxonomy_rank": "species",
                }
                for name in names
            }

    specialist_results = [DevelopmentSpecialistResult(
        role=DevelopmentSpecialistRole.SYNDROME_SITE,
        summary_i18n=_lt("纯虚构", "Synthetic"),
        candidate_pool=[
            proposal("Hallucinated candidate", 0.99, "fragment_not_in_manifest"),
            *[
                proposal(name, 0.90 - (index * 0.05), "fragment_001")
                for index, (name, _taxonomy_id) in enumerate(valid_records)
            ],
        ],
    )]
    fallback, audit = asyncio.run(build_resolved_agent_pool_fallback(
        specialist_results,
        DevelopmentSynthesisDraft(
            summary_i18n=_lt("纯虚构回退", "Synthetic fallback"),
            unknown_score=0.5,
        ),
        ManifestAwareTaxonomy(),  # type: ignore[arg-type]
        valid_fragment_ids={"fragment_001"},
    ))

    assert [item.canonical_latin_name for item in fallback.concrete_pathogens] == [
        name for name, _taxonomy_id in valid_records
    ]
    assert validate_development_top5(
        fallback,
        valid_fragment_ids={"fragment_001"},
    ).valid
    assert any(
        "unknown_source_fragment_reference" in item["reason_codes"]
        for item in audit["input_exclusions"]
    )
    assert audit["selected_candidate_count"] == 5
    assert all(
        item["source_fragment_manifest_membership_verified"] is True
        for item in audit["candidates"]
        if item["disposition"] == "selected"
    )
    assert "fragment_not_in_manifest" not in json.dumps(audit, ensure_ascii=False)
    assert "Hallucinated candidate" not in json.dumps(audit, ensure_ascii=False)


_PATHOGENS = [
    ("Aeromonas hydrophila", "嗜水气单胞菌", 644),
    ("Edwardsiella tarda", "迟钝爱德华氏菌", 636),
    ("Vibrio vulnificus", "创伤弧菌", 672),
    ("Streptococcus suis", "猪链球菌", 1307),
    ("Klebsiella pneumoniae", "肺炎克雷伯菌", 573),
]


_DEVELOPMENT_CASE_TEXT = """
【纯虚构开发测试：从零编写，非真实患者】51岁女性，3天前清洗虚构淡水景观水池后乏力，随后高热、意识不清和呕吐。
实验室：PLT 83×10^9/L，CRP 176 mg/L，PCT 9.2 ng/mL，ALT 468 U/L，AST 731 U/L，Cr 126 μmol/L。
脑脊液白细胞28/μL，革兰染色阴性；血培养和脑脊液培养已送但结果未回。
影像提示右下肺炎性灶、少量胸腔积液，肝内一处低密度灶，肝脓肿待排。
已经验使用广谱抗菌药头孢吡肟和利奈唑胺。
""".strip()


def _mark_provider_ready(client: TestClient, provider_id: str) -> None:
    client.app.state.db.execute(
        """UPDATE providers SET enabled = 1, last_test_ok = 1, last_tested_at = ?,
           last_test_latency_ms = 1, last_test_error_code = NULL WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), provider_id),
    )


def _create_provider(
    client: TestClient,
    *,
    api_key: str = "V3-TEST-KEY",
    name: str = "development-v3-fake",
    model: str = "development-v3-model",
    weight: float = 1.0,
) -> str:
    response = client.post("/api/providers", json={
        "name": name,
        "kind": "openai_compatible",
        "model": model,
        "base_url": "http://127.0.0.1:9024/v1",
        "api_key": api_key,
        "data_boundary": "local",
        "weight": weight,
    })
    assert response.status_code == 201, response.text
    provider_id = response.json()["id"]
    _mark_provider_ready(client, provider_id)
    return provider_id


def _wait_run(client: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(300):
        response = client.get("/api/runs/%s" % run_id)
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.02)
    raise AssertionError("development run did not finish")


def test_development_create_returns_202_while_provider_dns_is_still_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting an async run must not hold its POST response behind DNS.

    The outer ASGI wrapper deliberately pauses the response-start message
    until the scheduled run reaches DNS validation.  This makes the production
    race deterministic: the 202 must be delivered while the resolver is still
    blocked, rather than only after DNS times out.
    """

    resolver_entered = threading.Event()
    release_resolver = threading.Event()
    resolver_timed_out_before_response = threading.Event()

    def stalled_getaddrinfo(*_args: Any, **_kwargs: Any) -> Any:
        resolver_entered.set()
        if not release_resolver.wait(timeout=2.0):
            resolver_timed_out_before_response.set()
        raise socket.gaierror(socket.EAI_AGAIN, "synthetic temporary DNS failure")

    inner_app = create_app(Settings(database_path=tmp_path / "dns-start.db"))

    class WaitForRunDnsBeforeSendingResponse:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            waited = False

            async def gated_send(message: Any) -> None:
                nonlocal waited
                if (
                    not waited
                    and scope.get("type") == "http"
                    and scope.get("path") == "/api/development/runs"
                    and message.get("type") == "http.response.start"
                ):
                    waited = True
                    await asyncio.to_thread(resolver_entered.wait, 1.0)
                await send(message)

            await self.app(scope, receive, gated_send)

    with TestClient(WaitForRunDnsBeforeSendingResponse(inner_app)) as client:
        provider = client.post("/api/providers", json={
            "name": "external-dns-start-test",
            "kind": "openai_compatible",
            "model": "synthetic-model",
            "base_url": "https://provider.example/v1",
            "api_key": "synthetic-test-key",
            "data_boundary": "external",
        })
        assert provider.status_code == 201, provider.text
        provider_id = provider.json()["id"]
        inner_app.state.db.execute(
            """UPDATE providers SET enabled = 1, last_test_ok = 1,
               last_tested_at = ?, last_test_latency_ms = 1,
               last_test_error_code = NULL WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), provider_id),
        )
        monkeypatch.setattr(socket, "getaddrinfo", stalled_getaddrinfo)

        created: Any = None
        try:
            created = client.post("/api/development/runs", json={
                "text": _DEVELOPMENT_CASE_TEXT,
                "provider_ids": [provider_id],
                "specialist_config_version": "owlpath.dns-start.test-v1",
            })
            returned_before_dns_completion = (
                resolver_entered.is_set()
                and not resolver_timed_out_before_response.is_set()
            )
        finally:
            release_resolver.set()

        assert created is not None
        assert created.status_code == 202, created.text
        assert returned_before_dns_completion is True
        run = _wait_run(client, created.json()["id"])
        assert run["status"] == "failed"
        assert run["error"]["code"] == "development_technical_failure"


class _OfflineRetriever:
    async def retrieve(self, queries: Any) -> RetrievalBundle:
        return RetrievalBundle(
            warnings=["retrieval_europe_pmc_unavailable", "retrieval_pubmed_unavailable"],
            source_status={"europe_pmc": "unavailable", "pubmed": "unavailable"},
        )


class _ResolvedTaxonomy:
    async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
        by_name = {latin.casefold(): (latin, zh_cn, tax_id) for latin, zh_cn, tax_id in _PATHOGENS}
        resolved: dict[str, dict[str, Any]] = {}
        for raw_name in names:
            normalized = " ".join(str(raw_name).strip().casefold().split())
            record = by_name.get(normalized)
            if record is None:
                resolved[normalized] = {
                    "ncbi_taxonomy_id": None,
                    "taxonomy_resolution_status": "unresolved",
                    "canonical_latin_name": str(raw_name),
                    "name_i18n": {"en": str(raw_name), "status": "partial"},
                }
            else:
                latin, zh_cn, tax_id = record
                resolved[normalized] = {
                    "ncbi_taxonomy_id": tax_id,
                    "taxonomy_resolution_status": "cache_resolved",
                    "canonical_latin_name": latin,
                    "name_i18n": {"zh_cn": zh_cn, "en": latin, "status": "complete"},
                }
        return resolved


def _draft_candidate(
    rank: int,
    *,
    name: Optional[str] = None,
    taxonomic_rank: DevelopmentTaxonomicRank = DevelopmentTaxonomicRank.SPECIES,
    score: Optional[float] = None,
    fragment_id: str = "fragment_001",
) -> DevelopmentDraftPathogen:
    latin, zh_cn, tax_id = _PATHOGENS[rank - 1]
    latin = name or latin
    return DevelopmentDraftPathogen(
        rank=rank,
        canonical_latin_name=latin,
        name_i18n=_lt(zh_cn if name is None else name, latin),
        taxonomic_rank=taxonomic_rank,
        category=DevelopmentPathogenCategory.BACTERIA,
        ncbi_taxonomy_id=tax_id,
        taxonomy_resolution_status=DevelopmentTaxonomyResolutionStatus.CACHE_RESOLVED,
        model_score=score if score is not None else 0.8 - (rank * 0.1),
        supporting_evidence=[_evidence(fragment_id)],
        opposing_evidence=[],
        why_ranked_i18n=_lt("结合综合征与暴露史", "Ranked from syndrome and exposure"),
        main_uncertainty_i18n=_lt("尚无病原学结果", "Microbiology remains pending"),
        proposed_by_agent_roles=[DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY],
    )


def _valid_draft() -> DevelopmentSynthesisDraft:
    return DevelopmentSynthesisDraft(
        summary_i18n=_lt("发展模式病原体总诊", "Development pathogen synthesis"),
        concrete_pathogens=[_draft_candidate(rank) for rank in range(1, 6)],
        category_overview=[DevelopmentCategoryOverview(
            category=DevelopmentPathogenCategory.BACTERIA,
            model_score=0.82,
            rationale_i18n=_lt("细菌性感染综合征", "Bacterial infection syndrome"),
        )],
        unknown_score=0.12,
    )


class _DevelopmentProvider:
    """Deterministic provider double for the multi-Agent orchestration tests."""

    def __init__(self, *, category_first: bool = False, secret_marker: str = "") -> None:
        self.category_first = category_first
        self.secret_marker = secret_marker
        self.specialist_calls: list[str] = []
        self.specialist_provider_attempts: dict[str, list[str]] = {}
        self.synthesis_calls = 0
        self.synthesis_provider_ids: list[str] = []
        self.critic_calls = 0
        self.critic_provider_ids: list[str] = []
        self.seen_full_source_text: list[str] = []

    def _metadata(self, role: str) -> dict[str, Any]:
        return {
            "request_id": "fake-%s" % role,
            "api_key": self.secret_marker,
            "raw_response": "RAW-%s" % self.secret_marker,
        }

    async def invoke_development_specialist(
        self, provider: dict[str, Any], api_key: Optional[str], request: Any,
    ) -> tuple[DevelopmentSpecialistResult, dict[str, Any]]:
        role = request.role
        self.specialist_calls.append(role.value)
        self.specialist_provider_attempts.setdefault(role.value, []).append(provider["id"])
        self.seen_full_source_text.append(request.source_text)
        fragment_id = request.source_fragments[0].source_fragment_id
        proposals = []
        for rank, (latin, zh_cn, _tax_id) in enumerate(_PATHOGENS, start=1):
            proposals.append(DevelopmentPathogenProposal(
                canonical_latin_name=latin,
                name_i18n=_lt(zh_cn, latin),
                taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
                category=DevelopmentPathogenCategory.BACTERIA,
                model_score=0.8 - (rank * 0.1),
                rationale_i18n=_lt("专科Agent候选", "Specialist Agent candidate"),
                counterevidence_i18n=_lt("缺少确证", "Definitive confirmation is absent"),
                source_fragment_ids=[fragment_id],
            ))
        summary_by_role = {
            DevelopmentSpecialistRole.INFECTIOUS_DISEASES: "高热、意识不清与多部位感染线索需要具体病原体鉴别",
            DevelopmentSpecialistRole.CRITICAL_CARE_EMERGENCY: "肝肾、神经及凝血系统存在多器官损伤",
            DevelopmentSpecialistRole.CLINICAL_EPIDEMIOLOGY: "清洗虚构淡水景观水池后发病，需澄清水体、鱼类或伤口暴露",
            DevelopmentSpecialistRole.LABORATORY_MEDICINE: "炎症、凝血与代谢指标提示严重感染病理生理",
            DevelopmentSpecialistRole.CLINICAL_MICROBIOLOGY_CULTURE: "脑脊液异常、培养待回，已用头孢吡肟和利奈唑胺",
            DevelopmentSpecialistRole.RADIOLOGY: "肺部病灶、胸腔积液与肝脏低强化灶提示多部位",
            DevelopmentSpecialistRole.PULMONOLOGY: "肺部病灶与胸腔积液需要呼吸专科解读",
            DevelopmentSpecialistRole.HEPATOBILIARY_PANCREATIC: "肝脏低强化灶需要鉴别肝内感染灶",
            DevelopmentSpecialistRole.NEUROLOGY_NEUROINFECTION: "意识障碍与脑脊液异常需要神经感染视角",
            DevelopmentSpecialistRole.SURGERY_SOURCE_CONTROL: "肝脏病灶与可能脓肿需要解剖感染源评估",
            DevelopmentSpecialistRole.ANTIMICROBIAL_STEWARDSHIP: "头孢吡肟和利奈唑胺会影响培养产率与结果解释",
            DevelopmentSpecialistRole.TIMELINE_COURSE: "3天内急性起病并快速进展",
            DevelopmentSpecialistRole.HOST_SUSCEPTIBILITY: "51岁，既往体健，免疫状态需结合原文核对",
            DevelopmentSpecialistRole.SYNDROME_LOCALIZATION: "高热、意识不清与多部位感染线索",
            DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH: "清洗虚构淡水景观水池后发病，需澄清水体、鱼类或伤口暴露",
            DevelopmentSpecialistRole.LAB_PATHOPHYSIOLOGY: "炎症、凝血与代谢指标提示严重感染病理生理",
            DevelopmentSpecialistRole.ORGAN_SEVERITY: "肝肾、神经及凝血系统存在器官损伤",
            DevelopmentSpecialistRole.IMAGING_DISSEMINATION: "肺部病灶、胸腔积液与肝脏低强化灶提示多部位",
            DevelopmentSpecialistRole.MICROBIOLOGY_TREATMENT: "脑脊液异常、培养待回，已用头孢吡肟和利奈唑胺",
            DevelopmentSpecialistRole.NEUROINFECTION: "意识障碍与脑脊液异常需要神经感染挑战视角",
            DevelopmentSpecialistRole.IMMUNOCOMPROMISED_OPPORTUNISTIC: "机会感染风险按明确宿主证据核对",
            DevelopmentSpecialistRole.TRAVEL_ZOONOTIC: "水体、鱼类与人兽共患暴露需要独立挑战",
            DevelopmentSpecialistRole.HEALTHCARE_DEVICE_AMR: "气管插管、器械与抗菌药会影响标本和耐药解释",
            DevelopmentSpecialistRole.TIMELINE_HOST: "3天内急性起病，既往体健",
            DevelopmentSpecialistRole.SYNDROME_SITE: "高热、意识不清与多器官损伤",
            DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY: "清洗虚构淡水景观水池后发病，需澄清水体、鱼类或伤口暴露",
            DevelopmentSpecialistRole.LABORATORY_ORGAN_INJURY: "炎症指标显著升高并伴肝肾损伤",
            DevelopmentSpecialistRole.IMAGING_MICROBIOLOGY_TREATMENT: "脑脊液异常、肝内感染灶可能，培养待回，已用头孢吡肟和利奈唑胺",
        }
        summary = summary_by_role.get(
            role,
            "按新版会诊职责提供结构化专科意见",
        )
        result = DevelopmentSpecialistResult(
            role=role,
            summary_i18n=_lt(summary, "Structured specialist summary for the synthetic case"),
            observations=[DevelopmentSpecialistObservation(
                observation_id="obs-%s" % role.value,
                kind="key_fact",
                statement_i18n=_lt(summary, "Key synthetic-case observation"),
                source_fragment_ids=[fragment_id],
                importance="high",
            )],
            candidate_pool=proposals,
        )
        return result, self._metadata(role.value)

    async def invoke_development_synthesis(
        self, provider: dict[str, Any], api_key: Optional[str], request: Any,
    ) -> tuple[DevelopmentSynthesisDraft, dict[str, Any]]:
        self.synthesis_calls += 1
        self.synthesis_provider_ids.append(provider["id"])
        self.seen_full_source_text.append(request.source_text)
        fragment_id = request.source_fragments[0].source_fragment_id
        draft = _valid_draft().model_copy(update={
            "concrete_pathogens": [
                _draft_candidate(rank, fragment_id=fragment_id) for rank in range(1, 6)
            ],
        })
        if self.category_first and request.revision_context is None:
            draft = draft.model_copy(update={
                "concrete_pathogens": [
                    _draft_candidate(
                        1,
                        name="Bacteria",
                        taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
                        fragment_id=fragment_id,
                    ),
                    *draft.concrete_pathogens[1:],
                ],
            })
        return draft, self._metadata("synthesis-%d" % self.synthesis_calls)

    async def invoke_development_critic(
        self, provider: dict[str, Any], api_key: Optional[str], request: Any,
    ) -> tuple[DevelopmentCriticResult, dict[str, Any]]:
        self.critic_calls += 1
        self.critic_provider_ids.append(provider["id"])
        self.seen_full_source_text.append(request.source_text)
        rejected = bool(request.deterministic_issues)
        if rejected:
            result = DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "Top-5含大类候选，需修订。",
                    "The Top-5 contains a broad category and requires revision.",
                ),
                issues=[DevelopmentCriticIssue(
                    code="generic_pathogen_name",
                    severity="error",
                    message_i18n=_lt("大类不能占据Top-5", "A category cannot occupy Top-5"),
                    candidate_ranks=[1],
                )],
                required_changes_i18n=[_lt("改为具体病原体", "Use a concrete pathogen")],
            )
        else:
            result = DevelopmentCriticResult(
                accepted=True,
                revision_required=False,
                review_summary_i18n=_lt("通过独立审稿", "Accepted by independent critic"),
            )
        return result, self._metadata("critic")


def _concrete(candidate: DevelopmentDraftPathogen) -> DevelopmentConcretePathogen:
    return DevelopmentConcretePathogen.model_validate(candidate.model_dump(mode="json"))


def test_v3_contract_requires_exactly_five_specific_pathogens_and_has_no_safety_action() -> None:
    draft = _valid_draft()
    validation = validate_development_top5(
        draft,
        valid_fragment_ids={"fragment_001"},
    )
    assert validation.valid is True

    result = DevelopmentResultV3(
        status="completed_with_warnings",
        summary_i18n=draft.summary_i18n,
        concrete_pathogens=[_concrete(item) for item in draft.concrete_pathogens],
        category_overview=draft.category_overview,
        unknown_score=draft.unknown_score,
        agent_observations=[DevelopmentAgentObservationSummary(
            role=DevelopmentAgentRole.PATHOGEN_SYNTHESIS,
            status="completed_with_warnings",
            summary_i18n=draft.summary_i18n,
            warning_codes=["uncalibrated_model_scores"],
        )],
        warnings=["uncalibrated_model_scores"],
        review=DevelopmentReviewSummary(
            accepted=True,
            revision_count=0,
            deterministic_validation=validation,
        ),
    )
    payload = result.model_dump(mode="json")

    assert payload["schema_version"] == "owlpath.result.v3"
    assert [item["rank"] for item in payload["concrete_pathogens"]] == [1, 2, 3, 4, 5]
    assert len({item["canonical_latin_name"] for item in payload["concrete_pathogens"]}) == 5
    assert "safety_action" not in payload
    assert "abstain" not in json.dumps(payload, ensure_ascii=False).casefold()
    assert payload["category_overview"][0]["category"] == "bacteria"
    assert payload["unknown_score"] == pytest.approx(0.12)
    assert payload["review"]["status"] == "not_reviewed"

    with pytest.raises(ValidationError, match="exactly five concrete pathogens"):
        DevelopmentResultV3(
            status="completed",
            summary_i18n=draft.summary_i18n,
            concrete_pathogens=[_concrete(item) for item in draft.concrete_pathogens[:4]],
            unknown_score=0.1,
            review=DevelopmentReviewSummary(
                accepted=False,
                revision_count=1,
                deterministic_validation=DevelopmentTop5Validation(
                    valid=False,
                    issues=[DevelopmentContractIssue(
                        code="top5_count",
                        message="concrete_pathogens must contain exactly five candidates",
                    )],
                ),
            ),
        )


def test_legacy_v3_review_status_is_inferred_without_overwriting_new_status() -> None:
    """Old persisted v3 payloads gain an honest review label when read."""
    draft = _valid_draft()
    validation = validate_development_top5(draft, valid_fragment_ids={"fragment_001"})
    original = DevelopmentResultV3(
        status="completed_with_warnings",
        summary_i18n=draft.summary_i18n,
        concrete_pathogens=[_concrete(item) for item in draft.concrete_pathogens],
        category_overview=draft.category_overview,
        unknown_score=draft.unknown_score,
        review=DevelopmentReviewSummary(
            accepted=True,
            status="revision_completed_not_re_reviewed",
            revision_count=1,
            deterministic_validation=validation,
            critic=DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt("需要修订", "Revision required"),
            ),
        ),
    )
    legacy_payload = original.model_dump(mode="json")
    legacy_payload["review"].pop("status")

    restored = DevelopmentResultV3.model_validate(legacy_payload)
    assert restored.review.status == "revision_completed_not_re_reviewed"

    explicit_payload = original.model_dump(mode="json")
    explicit_payload["review"]["status"] = "critic_changes_not_closed"
    explicit = DevelopmentResultV3.model_validate(explicit_payload)
    assert explicit.review.status == "critic_changes_not_closed"

    technical_payload = original.model_dump(mode="json")
    technical_payload["status"] = "technical_failure"
    technical_payload["concrete_pathogens"] = []
    technical_payload["review"].pop("status")
    technical = DevelopmentResultV3.model_validate(technical_payload)
    assert technical.review.status == "technical_failure"


def test_development_stage_timeout_preserves_hard_deadline_finalization_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: 100.0)

    # Twenty-five seconds remain, but the last fifteen are reserved for
    # deterministic compilation/persistence, so the optional role gets ten.
    assert engine_module._development_stage_timeout(45.0, 125.0) == pytest.approx(10.0)
    # Once the reserve has been reached, no new optional Provider call starts.
    assert engine_module._development_stage_timeout(75.0, 115.0) == 0.0
    # Unit tests and direct internal calls without a run deadline retain the
    # explicit role limit.
    assert engine_module._development_stage_timeout(45.0, None) == 45.0


def test_provenance_reconciliation_leaves_unproposed_candidate_empty_and_invalid() -> None:
    proposal = DevelopmentPathogenProposal(
        canonical_latin_name="  AEROMONAS   hydrophila ",
        name_i18n=_lt("嗜水气单胞菌", "Aeromonas hydrophila"),
        taxonomic_rank=DevelopmentTaxonomicRank.SPECIES,
        category=DevelopmentPathogenCategory.BACTERIA,
        model_score=0.7,
        rationale_i18n=_lt("专科候选", "Specialist candidate"),
        source_fragment_ids=["fragment_001"],
    )
    specialist = DevelopmentSpecialistResult(
        role=DevelopmentSpecialistRole.TIMELINE_HOST,
        summary_i18n=_lt("时间线", "Timeline"),
        candidate_pool=[proposal],
    )

    reconciled, warnings, audit = reconcile_development_pathogen_provenance(
        _valid_draft(), [specialist]
    )

    assert reconciled.concrete_pathogens[0].proposed_by_agent_roles == [
        DevelopmentSpecialistRole.TIMELINE_HOST
    ]
    assert reconciled.concrete_pathogens[4].proposed_by_agent_roles == []
    assert "candidate_provenance_missing_from_specialist_pool:rank_5" in warnings
    assert audit["candidates"][0]["action"] == (
        "replaced_with_frozen_specialist_manifest"
    )
    validation = validate_development_top5(
        reconciled, valid_fragment_ids={"fragment_001"}
    )
    assert any(
        issue.code == "missing_agent_provenance" and issue.candidate_rank == 5
        for issue in validation.issues
    )


def test_category_only_draft_is_parseable_but_rejected_for_revision() -> None:
    draft = _valid_draft().model_copy(update={
        "concrete_pathogens": [
            _draft_candidate(
                1,
                name="Bacteria",
                taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
            ),
            *_valid_draft().concrete_pathogens[1:],
        ],
    })
    validation = validate_development_top5(draft, valid_fragment_ids={"fragment_001"})
    issue_codes = {item.code for item in validation.issues}

    assert validation.valid is False
    assert {"non_concrete_taxonomic_rank", "generic_pathogen_name"} <= issue_codes

    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "Top-5中的细菌是大类，必须修订为具体病原体。",
            "Bacteria is a category and must be revised to a concrete pathogen.",
        ),
        issues=[DevelopmentCriticIssue(
            code="generic_pathogen_name",
            severity="error",
            message_i18n=_lt("第1项是大类", "Rank 1 is a broad category"),
            candidate_ranks=[1],
        )],
        required_changes_i18n=[_lt("换成具体病原体", "Replace it with a concrete pathogen")],
    )
    assert critic.accepted is False
    assert critic.revision_required is True


def test_development_manifest_has_v3_core_and_dynamic_specialists_and_strict_graph_stays_v1() -> None:
    provider_ids = ["prv_primary", "prv_critic"]
    development = build_development_execution_manifest(
        provider_ids, source_text=_DEVELOPMENT_CASE_TEXT,
    )
    keys = {node["key"] for node in development["nodes"]}

    assert development["execution_graph_version"] == DEVELOPMENT_EXECUTION_GRAPH_VERSION
    assert development["trace_version"] == DEVELOPMENT_TRACE_VERSION
    assert {
        "source_compiler",
        "complexity_router",
        "specialist:infectious_diseases",
        "specialist:critical_care_emergency",
        "specialist:clinical_epidemiology",
        "specialist:laboratory_medicine",
        "specialist:clinical_microbiology_culture",
        "specialist:radiology",
        "specialist:hepatobiliary_pancreatic",
        "specialist:pulmonology",
        "specialist:neurology_neuroinfection",
        "specialist:surgery_source_control",
        "specialist:antimicrobial_stewardship",
        "evidence_board",
        "retrieval_planner",
        "literature_retrieval",
        "public_health_retrieval",
        "evidence_verifier",
        "synthesis",
        "contract_validator",
        "critic",
        "revision",
        "candidate_evidence_enrichment",
        "result_compiler",
        "persistence",
    } <= keys
    specialist_providers = {
        node["role"]: node["provider_id"]
        for node in development["nodes"]
        if node["key"].startswith("specialist:") and node.get("selected")
    }
    frozen_role_order = [
        *development["selected_core_roles"],
        *development["selected_dynamic_roles"],
    ]
    assert specialist_providers == {
        role: provider_ids[index % len(provider_ids)]
        for index, role in enumerate(frozen_role_order)
    }
    critic = next(node for node in development["nodes"] if node["key"] == "critic")
    assert critic["provider_id"] == "prv_critic"
    assert development["selected_dynamic_roles"] == [
        "radiology", "hepatobiliary_pancreatic", "pulmonology",
        "neurology_neuroinfection", "surgery_source_control",
        "antimicrobial_stewardship",
    ]
    assert development["limits"]["maximum_llm_calls_with_revision"] == 14
    assert development["limits"]["maximum_provider_network_requests_per_run"] == 18
    assert development["limits"]["specialist_provider_request_ceiling"] == 12
    assert development["limits"]["provider_failover_subject_to_global_request_budget"] is True
    assert development["limits"]["same_provider_retry_attempts_per_agent"] == 1
    assert development["limits"]["maximum_concurrent_requests_per_provider"] == 3
    assert development["limits"]["dns_preflight_before_provider_request_budget"] is True

    strict = build_execution_manifest(provider_ids, include_baseline=False, development_demo=False)
    strict_keys = {node["key"] for node in strict["nodes"]}
    assert strict["execution_graph_version"] == "owlpath.execution-graph.v1"
    assert strict["trace_version"] == "owlpath.trace.v1"
    assert "safety" in strict_keys
    assert "critic" not in strict_keys


def test_development_endpoint_completes_specific_top5_with_specialist_trace_and_offline_retrieval(
    client: TestClient,
) -> None:
    secret_marker = "V3-SECRET-MUST-NOT-LEAK"
    provider_double = _DevelopmentProvider(secret_marker=secret_marker)
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, api_key=secret_marker)

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
        "specialist_config_version": "owlpath.development-agents.test-v1",
    })
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    run = _wait_run(client, run_id)

    assert run["status"] == "completed", run
    assert run["schema_version"] == "owlpath.result.v3"
    assert run["execution_graph_version"] == "owlpath.execution-graph.v4"
    assert run["trace_version"] == "owlpath.trace.v2"
    result = run["result"]
    assert result["schema_version"] == "owlpath.result.v3"
    assert result["status"] in {"completed", "completed_with_warnings"}
    assert len(result["concrete_pathogens"]) == 5
    assert [item["rank"] for item in result["concrete_pathogens"]] == [1, 2, 3, 4, 5]
    assert len({item["canonical_latin_name"] for item in result["concrete_pathogens"]}) == 5
    assert all(item["taxonomic_rank"] in {"species", "species_complex", "virus_type"}
               for item in result["concrete_pathogens"])
    assert not {"bacteria", "virus", "fungus", "unknown"}.intersection(
        item["canonical_latin_name"].casefold() for item in result["concrete_pathogens"]
    )
    assert "safety_action" not in result
    assert "abstain" not in json.dumps(result, ensure_ascii=False).casefold()
    assert "retrieval_europe_pmc_unavailable" in result["warnings"]
    assert "retrieval_pubmed_unavailable" in result["warnings"]
    assert provider_double.synthesis_calls == 1
    assert provider_double.critic_calls == 1
    assert set(provider_double.specialist_calls) == {
        "infectious_diseases", "critical_care_emergency", "clinical_epidemiology",
        "laboratory_medicine", "clinical_microbiology_culture", "radiology",
        "hepatobiliary_pancreatic", "pulmonology", "neurology_neuroinfection",
        "surgery_source_control", "antimicrobial_stewardship",
    }
    assert len(provider_double.specialist_calls) == 11
    assert all(text == _DEVELOPMENT_CASE_TEXT for text in provider_double.seen_full_source_text)
    stored_manifest = json.loads(client.app.state.db.fetchone(
        "SELECT execution_manifest_json FROM runs WHERE id = ?", (run_id,),
    )["execution_manifest_json"])
    assert stored_manifest["specialist_config_version"] == (
        "owlpath.development-agents.test-v1"
    )
    assert stored_manifest["specialist_runtime_implementation_version"] == (
        "owlpath.development-agents.v3"
    )

    # GET-by-run-id, rather than browser session state, is the authoritative v3 read path.
    restored = client.get("/api/runs/%s" % run_id)
    assert restored.status_code == 200, restored.text
    assert restored.json()["id"] == run_id
    assert restored.json()["result"] == result

    trace_response = client.get("/api/runs/%s/trace" % run_id)
    assert trace_response.status_code == 200, trace_response.text
    trace = trace_response.json()
    nodes_by_key = {node["node_key"]: node for node in trace["nodes"]}
    assert {
        "source_compiler",
        "complexity_router", "evidence_board", "retrieval_planner",
        "literature_retrieval", "public_health_retrieval", "evidence_verifier",
        "specialist:infectious_diseases", "specialist:critical_care_emergency",
        "specialist:clinical_epidemiology", "specialist:laboratory_medicine",
        "specialist:clinical_microbiology_culture", "specialist:radiology",
        "specialist:hepatobiliary_pancreatic", "specialist:pulmonology",
        "specialist:neurology_neuroinfection", "specialist:surgery_source_control",
        "specialist:antimicrobial_stewardship",
        "synthesis",
        "contract_validator",
        "critic",
        "revision",
        "candidate_evidence_enrichment",
        "result_compiler",
        "persistence",
    } <= set(nodes_by_key)
    assert nodes_by_key["evidence_verifier"]["status"] == "completed"
    assert nodes_by_key["evidence_verifier"]["outcome"] == "warning"
    assert nodes_by_key["revision"]["status"] == "skipped"
    assert nodes_by_key["candidate_evidence_enrichment"]["status"] == "completed"
    assert nodes_by_key["candidate_evidence_enrichment"]["outcome"] == "warning"

    # The specialist output is visible, but provider secrets/raw responses are not.
    public_payloads: list[Any] = [run, trace]
    specialist_rendered = ""
    for key in (
        "specialist:clinical_epidemiology",
        "specialist:radiology",
        "specialist:clinical_microbiology_culture",
    ):
        node_id = nodes_by_key[key]["id"]
        detail = client.get("/api/runs/%s/trace/nodes/%s" % (run_id, node_id))
        assert detail.status_code == 200, detail.text
        public_payloads.append(detail.json())
        specialist_rendered += json.dumps(detail.json(), ensure_ascii=False)
    assert "清洗虚构淡水景观水池" in specialist_rendered
    assert "脑脊液" in specialist_rendered
    assert "肝脏低强化灶" in specialist_rendered
    assert "头孢吡肟" in specialist_rendered

    rendered = json.dumps(public_payloads, ensure_ascii=False)
    assert secret_marker not in rendered
    assert "RAW-%s" % secret_marker not in rendered
    assert "raw_response" not in rendered
    assert "api_key" not in rendered


def test_legacy_v3_result_without_taxonomy_reason_remains_readable(
    client: TestClient,
) -> None:
    """Adding taxonomy audit fields must not break already stored v3 runs."""

    client.app.state.engine.provider_client = _DevelopmentProvider()
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="legacy-v3-read-compatibility")
    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed", run

    legacy_result = json.loads(json.dumps(run["result"]))
    for candidate in legacy_result["concrete_pathogens"]:
        candidate.pop("taxonomy_resolution_reason_code", None)
        candidate.pop("ncbi_taxonomy_rank", None)
    legacy_result["result_sha256"] = None
    digest = sha256_json(legacy_result)
    legacy_result["result_sha256"] = digest
    client.app.state.db.execute(
        "UPDATE runs SET result_json = ?, result_sha256 = ? WHERE id = ?",
        (json.dumps(legacy_result, ensure_ascii=False), digest, run["id"]),
    )

    restored = client.get("/api/runs/%s" % run["id"])
    assert restored.status_code == 200, restored.text
    assert all(
        item["taxonomy_resolution_reason_code"] == "legacy_reason_unavailable"
        for item in restored.json()["result"]["concrete_pathogens"]
    )
    history = client.get(
        "/api/runs", params={"trace_version": "owlpath.trace.v2"},
    )
    assert history.status_code == 200, history.text
    assert run["id"] in {item["id"] for item in history.json()}


def test_development_endpoint_deterministically_reconciles_all_real_proposing_agents(
    client: TestClient,
) -> None:
    class ProvenanceMismatchProvider(_DevelopmentProvider):
        proposals_by_role = {
            DevelopmentSpecialistRole.CRITICAL_CARE_EMERGENCY: {
                "Aeromonas hydrophila", "Edwardsiella tarda",
            },
            DevelopmentSpecialistRole.INFECTIOUS_DISEASES: {
                "Edwardsiella tarda", "Vibrio vulnificus",
            },
            DevelopmentSpecialistRole.CLINICAL_EPIDEMIOLOGY: {
                "Aeromonas hydrophila", "Vibrio vulnificus", "Streptococcus suis",
            },
            DevelopmentSpecialistRole.LABORATORY_MEDICINE: {
                "Streptococcus suis", "Klebsiella pneumoniae",
            },
            DevelopmentSpecialistRole.CLINICAL_MICROBIOLOGY_CULTURE: {
                "Klebsiella pneumoniae",
            },
        }

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            result, metadata = await super().invoke_development_specialist(
                provider, api_key, request
            )
            selected = []
            for proposal in result.candidate_pool:
                if proposal.canonical_latin_name not in self.proposals_by_role.get(result.role, set()):
                    continue
                if (
                    result.role == DevelopmentSpecialistRole.CRITICAL_CARE_EMERGENCY
                    and proposal.canonical_latin_name == "Aeromonas hydrophila"
                ):
                    proposal = proposal.model_copy(update={
                        "canonical_latin_name": "  AEROMONAS   hydrophila  ",
                    })
                selected.append(proposal)
            return result.model_copy(update={"candidate_pool": selected}), metadata

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            draft, metadata = await super().invoke_development_synthesis(
                provider, api_key, request
            )
            # Deliberately fabricate one role and omit every true multi-Agent
            # proposer.  Runtime reconciliation, not this LLM field, is the
            # authority for the final provenance.
            candidates = [
                item.model_copy(update={
                        "proposed_by_agent_roles": [
                            DevelopmentSpecialistRole.INFECTIOUS_DISEASES
                    ],
                })
                for item in draft.concrete_pathogens
            ]
            return draft.model_copy(update={"concrete_pathogens": candidates}), metadata

    provider_double = ProvenanceMismatchProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="provenance-reconciliation-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    roles_by_name = {
        item["canonical_latin_name"]: item["proposed_by_agent_roles"]
        for item in result["concrete_pathogens"]
    }
    assert roles_by_name == {
        "Aeromonas hydrophila": ["critical_care_emergency", "clinical_epidemiology"],
        "Edwardsiella tarda": ["infectious_diseases", "critical_care_emergency"],
        "Vibrio vulnificus": ["infectious_diseases", "clinical_epidemiology"],
        "Streptococcus suis": [
            "clinical_epidemiology", "laboratory_medicine",
        ],
        "Klebsiella pneumoniae": [
            "laboratory_medicine", "clinical_microbiology_culture",
        ],
    }
    assert "candidate_provenance_reconciled:rank_1" in result["warnings"]
    assert not any(
        item.startswith("candidate_provenance_missing_from_specialist_pool")
        for item in result["warnings"]
    )

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    validator = next(
        item for item in trace["nodes"] if item["node_key"] == "contract_validator"
    )
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], validator["id"])
    ).json()
    validation_artifact = next(
        item["content"] for item in detail["artifacts"]
        if item["artifact_type"] == "development_top5_validation"
    )
    reconciliation = validation_artifact["provenance_reconciliation"]
    aeromonas = reconciliation["candidates"][0]
    assert aeromonas["llm_reported_roles"] == ["infectious_diseases"]
    assert aeromonas["verified_roles"] == [
        "critical_care_emergency", "clinical_epidemiology",
    ]
    assert aeromonas["action"] == "replaced_with_frozen_specialist_manifest"


def test_final_top5_gets_candidate_specific_mock_literature_without_case_text(
    client: TestClient,
) -> None:
    provider_double = _DevelopmentProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    search_queries: list[str] = []
    relevant_ids = {
        latin: str(41000000 + index)
        for index, (latin, _zh_cn, _tax_id) in enumerate(_PATHOGENS, start=1)
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(EUROPE_PMC_SEARCH_URL):
            query = request.url.params["query"]
            search_queries.append(query)
            # The initial pre-synthesis search is intentionally empty.  Only
            # one-query-per-final-candidate enrichment returns publishable
            # records.  The broad, unbound hit may inform synthesis but must
            # not leak into the final candidate evidence list.
            if not query.startswith('"'):
                return httpx.Response(200, json={"resultList": {"result": [{
                    "pmid": "39999999",
                    "title": "Broad review of severe sepsis workflows",
                    "journalTitle": "Mock General Medicine Journal",
                    "pubYear": "2026",
                }]}})
            name = next(latin for latin, _zh, _tax in _PATHOGENS if latin in query)
            assert query == '"%s" AND (human OR patient OR clinical OR infection)' % name
            pmid = relevant_ids[name]
            return httpx.Response(200, json={
                "resultList": {"result": [
                    {
                        "pmid": pmid,
                        "title": "Clinical case report of %s infection" % name,
                        "journalTitle": "Mock Infectious Diseases Journal",
                        "pubYear": "2026",
                    },
                    {
                        "pmid": "9%s" % pmid[1:],
                        "title": "Unrelated intensive care workflow study",
                        "journalTitle": "Mock ICU Journal",
                        "pubYear": "2026",
                    },
                ]},
            })
        if str(request.url).startswith(NCBI_PUBMED_SEARCH_URL):
            search_queries.append(request.url.params["term"])
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        raise AssertionError("Unexpected HTTP request: %s" % request.url)

    client.app.state.engine.medical_retriever = MedicalEvidenceRetriever(
        transport=httpx.MockTransport(handler),
        max_results_per_query=2,
        max_queries=3,
    )
    provider_id = _create_provider(client)
    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    sources_by_id = {
        item["evidence_source_id"]: item for item in result["evidence_sources"]
    }
    assert len(sources_by_id) == 5
    for candidate in result["concrete_pathogens"]:
        linked_ids = {
            source_id
            for link in candidate["supporting_evidence"]
            for source_id in link["evidence_source_ids"]
        }
        assert linked_ids, candidate
        assert all(source_id in sources_by_id for source_id in linked_ids)
        assert all(
            candidate["canonical_latin_name"].casefold()
            in sources_by_id[source_id]["title"].casefold()
            for source_id in linked_ids
        )
    linked_source_ids = {
        source_id
        for candidate in result["concrete_pathogens"]
        for link in [
            *candidate["supporting_evidence"],
            *candidate["opposing_evidence"],
        ]
        for source_id in link["evidence_source_ids"]
    }
    assert set(sources_by_id) == linked_source_ids
    rendered_result = json.dumps(result, ensure_ascii=False)
    assert "Unrelated intensive care workflow" not in rendered_result
    assert "Broad review of severe sepsis workflows" not in rendered_result
    for restricted_case_text in (
        "清洗虚构淡水景观水池", "51岁", "肝内感染灶", "头孢吡肟", "脑脊液",
    ):
        assert all(restricted_case_text not in query for query in search_queries)

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    node = next(
        item for item in trace["nodes"]
        if item["node_key"] == "candidate_evidence_enrichment"
    )
    assert node["status"] == "completed"
    assert node["outcome"] == "passed"
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], node["id"])
    )
    assert detail.status_code == 200, detail.text
    detail_rendered = json.dumps(detail.json(), ensure_ascii=False)
    assert '"coverage_count": 5' in detail_rendered
    # Federated pre-synthesis retrieval may already bind a title-verifiable
    # candidate source, so there need not be an unbound broad record.
    assert "Unrelated intensive care workflow" not in detail_rendered


def test_category_only_first_synthesis_is_criticized_and_revised_once(
    client: TestClient,
) -> None:
    provider_double = _DevelopmentProvider(category_first=True)
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client)

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert provider_double.synthesis_calls == 2
    assert provider_double.critic_calls == 1
    assert result["review"]["revision_count"] == 1
    assert result["review"]["status"] == "revision_completed_not_re_reviewed"
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert len(result["concrete_pathogens"]) == 5
    assert all(item["canonical_latin_name"] != "Bacteria" for item in result["concrete_pathogens"])

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes_by_key = {node["node_key"]: node for node in trace["nodes"]}
    assert nodes_by_key["contract_validator"]["outcome"] == "warning"
    assert nodes_by_key["critic"]["outcome"] == "warning"
    assert nodes_by_key["revision"]["status"] == "completed"
    assert nodes_by_key["revision"]["outcome"] == "passed"


def test_invalid_revision_from_hallucinated_critic_retains_prior_valid_draft(
    client: TestClient,
) -> None:
    class HallucinatedCriticInvalidRevisionProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            draft, metadata = await super().invoke_development_synthesis(provider, api_key, request)
            if request.revision_context is not None:
                fragment_id = request.source_fragments[0].source_fragment_id
                draft = draft.model_copy(update={
                    "concrete_pathogens": [
                        _draft_candidate(
                            1,
                            name="Bacteria",
                            taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
                            fragment_id=fragment_id,
                        ),
                        *draft.concrete_pathogens[1:],
                    ],
                })
            return draft, metadata

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            assert request.deterministic_issues == []
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "审稿错误地认为遗漏了清洗虚构淡水景观水池暴露。",
                    "The critic incorrectly claims that fish handling was omitted.",
                ),
                issues=[DevelopmentCriticIssue(
                    code="hallucinated_fish_exposure_omission",
                    severity="error",
                    message_i18n=_lt(
                        "清洗虚构淡水景观水池暴露未被纳入排序。",
                        "Fish handling was not incorporated into ranking.",
                    ),
                    candidate_ranks=[1],
                )],
                required_changes_i18n=[_lt(
                    "根据该错误意见重写第一名。",
                    "Rewrite rank one based on this mistaken finding.",
                )],
            ), self._metadata("critic-hallucination")

    provider_double = HallucinatedCriticInvalidRevisionProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="invalid-revision-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert result["fallback_mode"] == "revision_rejected_retained_prior_valid_draft"
    assert "revision_rejected_retained_prior_valid_draft" in result["warnings"]
    assert "agent_pool_fallback" not in result["warnings"]
    assert [item["canonical_latin_name"] for item in result["concrete_pathogens"]] == [
        item[0] for item in _PATHOGENS
    ]
    assert result["review"]["revision_count"] == 1
    assert result["review"]["status"] == "critic_changes_not_closed"
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["critic"]["revision_required"] is True
    assert result["review"]["critic"]["issues"][0]["code"] == (
        "hallucinated_fish_exposure_omission"
    )

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    revision_node = nodes["revision"]
    assert revision_node["status"] == "completed"
    assert revision_node["outcome"] == "warning"
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], revision_node["id"]),
    )
    assert detail.status_code == 200, detail.text
    artifacts = {item["artifact_type"]: item["content"] for item in detail.json()["artifacts"]}
    assert artifacts["revision_contract_validation"]["valid"] is False
    decision = artifacts["revision_transaction_decision"]
    assert decision["decision"] == "revision_rejected_retained_prior_valid_draft"
    assert decision["reason"] == "revised_draft_failed_deterministic_contract"
    assert decision["prior_validation"]["valid"] is True
    assert decision["revised_validation"]["valid"] is False
    assert decision["critic_issue_codes"] == ["hallucinated_fish_exposure_omission"]


def test_hallucinated_missing_fragment_and_ranking_issues_are_dismissed_without_revision(
    client: TestClient,
) -> None:
    class HallucinatedObjectiveCriticProvider(_DevelopmentProvider):
        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            existing_fragment_id = request.source_fragments[0].source_fragment_id
            assert not request.deterministic_issues
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "错误地声称来源片段不存在且分数排序冲突。",
                    "Incorrectly claims a missing source fragment and inconsistent score ranking.",
                ),
                issues=[
                    DevelopmentCriticIssue(
                        code="missing_source_fragment",
                        severity="error",
                        message_i18n=_lt(
                            "第一名引用的来源片段不存在。",
                            "The source fragment cited by rank one does not exist.",
                        ),
                        candidate_ranks=[1],
                        source_fragment_ids=[existing_fragment_id],
                    ),
                    DevelopmentCriticIssue(
                        code="inconsistent_ranking",
                        severity="error",
                        message_i18n=_lt(
                            "模型分数与名次不一致。",
                            "Model scores are inconsistent with rank order.",
                        ),
                        candidate_ranks=[1, 2, 3, 4, 5],
                    ),
                ],
                required_changes_i18n=[_lt("重新排序并补来源", "Re-rank and add a source")],
            ), self._metadata("critic-objective-hallucination")

    provider_double = HallucinatedObjectiveCriticProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="objective-hallucination-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert provider_double.synthesis_calls == 1
    assert result["review"]["revision_count"] == 0
    assert result["review"]["status"] == "critic_accepted"
    assert result["review"]["critic"]["accepted"] is True
    assert result["review"]["critic"]["revision_required"] is False
    assert result["review"]["critic"]["issues"] == []
    assert not any(item.startswith("critic:") for item in result["warnings"])

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["critic"]["outcome"] == "passed"
    assert nodes["revision"]["status"] == "skipped"
    critic_detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], nodes["critic"]["id"]),
    ).json()
    reconciliation = next(
        item["content"] for item in critic_detail["artifacts"]
        if item["artifact_type"] == "critic_issue_reconciliation"
    )
    assert reconciliation["effective_decision"]["revision_required"] is False
    assert [
        item["issue"]["code"] for item in reconciliation["dismissed_invalid_issues"]
    ] == ["missing_source_fragment", "inconsistent_ranking"]
    source_check = reconciliation["dismissed_invalid_issues"][0]
    existing_fragment_id = source_check["issue"]["source_fragment_ids"][0]
    assert source_check["objective_evidence"]["issue_fragment_manifest_presence"] == {
        existing_fragment_id: True,
    }


def test_taxonomy_inconsistency_claim_defers_to_true_deterministic_validation() -> None:
    issue = DevelopmentCriticIssue(
        code="taxonomy_resolution_inconsistent",
        severity="error",
        message_i18n=_lt(
            "审稿声称第一名的分类解析不一致。",
            "The critic claims rank one's taxonomy resolution is inconsistent.",
        ),
        candidate_ranks=[1],
    )
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt("需要修订分类。", "Taxonomy requires revision."),
        issues=[issue],
        required_changes_i18n=[_lt("重做分类解析", "Repeat taxonomy resolution")],
    )

    valid_draft = _valid_draft()
    valid_validation = validate_development_top5(
        valid_draft, valid_fragment_ids={"fragment_001"}
    )
    effective, audit = reconcile_development_critic_result(
        critic,
        draft=valid_draft,
        validation=valid_validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert valid_validation.valid is True
    assert effective.accepted is True
    assert effective.revision_required is False
    assert effective.issues == []
    assert audit["dismissed_invalid_issues"][0]["issue"]["code"] == (
        "taxonomy_resolution_inconsistent"
    )
    assert audit["dismissed_invalid_issues"][0]["objective_group"] == (
        "taxonomy_resolution"
    )

    unresolved_first = valid_draft.concrete_pathogens[0].model_copy(update={
        "ncbi_taxonomy_id": None,
        "taxonomy_resolution_status": DevelopmentTaxonomyResolutionStatus.UNRESOLVED,
        "taxonomy_resolution_reason_code": "not_found",
    })
    unresolved_draft = valid_draft.model_copy(update={
        "concrete_pathogens": [
            unresolved_first, *valid_draft.concrete_pathogens[1:],
        ],
    })
    unresolved_validation = validate_development_top5(
        unresolved_draft, valid_fragment_ids={"fragment_001"}
    )
    effective, audit = reconcile_development_critic_result(
        critic,
        draft=unresolved_draft,
        validation=unresolved_validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert unresolved_validation.valid is False
    assert effective.accepted is False
    assert effective.revision_required is True
    assert [item.code for item in effective.issues] == [
        "taxonomy_resolution_inconsistent"
    ]
    assert audit["dismissed_invalid_issues"] == []
    assert audit["evaluations"][0]["disposition"] == "retained"
    assert audit["evaluations"][0]["objective_evidence"][
        "matching_contract_issue_codes"
    ] == ["taxonomy_unresolved"]


@pytest.mark.parametrize(
    ("issue_code", "expected_group"),
    [
        ("source_fragment_id_invalid", "source_fragment"),
        ("invalid-source-fragment-identifiers-rank1", "source_fragment"),
        ("genus_in_top5", "concrete_pathogen"),
        ("genus-level-in-top5", "concrete_pathogen"),
    ],
)
def test_objective_critic_code_variants_are_dismissed_when_contract_is_valid(
    issue_code: str,
    expected_group: str,
) -> None:
    """Free-form critic wording cannot override the frozen manifest/validator."""

    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt("声称客观合同失败。", "Claims an objective contract failure."),
        issues=[DevelopmentCriticIssue(
            code=issue_code,
            severity="error",
            message_i18n=_lt(
                "审稿意见与确定性校验结果不一致。",
                "The critic claim conflicts with deterministic validation.",
            ),
            candidate_ranks=[1],
            source_fragment_ids=["fragment_001"],
        )],
        required_changes_i18n=[_lt("要求修订。", "Requests revision.")],
    )
    draft = _valid_draft()
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert validation.valid is True
    assert effective.accepted is True
    assert effective.revision_required is False
    assert effective.issues == []
    dismissed = audit["dismissed_invalid_issues"]
    assert [item["issue"]["code"] for item in dismissed] == [issue_code]
    assert dismissed[0]["objective_group"] == expected_group


@pytest.mark.parametrize("failure_kind", ["source_fragment", "genus"])
def test_objective_critic_variants_are_retained_when_validator_confirms(
    failure_kind: str,
) -> None:
    if failure_kind == "source_fragment":
        issue_code = "source_fragment_id_invalid"
        first = _draft_candidate(1, fragment_id="fragment_not_in_manifest")
        expected_contract_code = "unknown_source_fragment"
    else:
        issue_code = "genus-level-in-top5"
        first = _draft_candidate(
            1,
            name="Hantavirus",
            taxonomic_rank=DevelopmentTaxonomicRank.GENUS,
        )
        expected_contract_code = "non_concrete_taxonomic_rank"
    draft = _valid_draft().model_copy(update={
        "concrete_pathogens": [first, *_valid_draft().concrete_pathogens[1:]],
    })
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt("合同问题已确认。", "Contract issue confirmed."),
        issues=[DevelopmentCriticIssue(
            code=issue_code,
            severity="error",
            message_i18n=_lt("需要修订。", "Revision is required."),
            candidate_ranks=[1],
        )],
        required_changes_i18n=[_lt("修复客观合同。", "Fix the objective contract.")],
    )
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert expected_contract_code in {item.code for item in validation.issues}
    assert effective.accepted is False
    assert effective.revision_required is True
    assert [item.code for item in effective.issues] == [issue_code]
    assert effective.required_changes_i18n == critic.required_changes_i18n
    assert audit["dismissed_invalid_issues"] == []
    assert audit["evaluations"][0]["disposition"] == "retained"


def test_aspiration_critic_aliases_are_dismissed_for_valid_top5_and_case_evidence() -> None:
    """The exact false issue codes seen in the aspiration run cannot force revision."""

    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "错误地声称具体候选和病例引用不足。",
            "Incorrectly claims too few concrete candidates and case citations.",
        ),
        issues=[
            DevelopmentCriticIssue(
                code="insufficient_concrete_pathogens",
                severity="error",
                message_i18n=_lt(
                    "具体病原体不足五项。", "Fewer than five concrete pathogens."
                ),
                candidate_ranks=[1, 2, 3, 4, 5],
            ),
            DevelopmentCriticIssue(
                code="missing_citation_for_candidate",
                severity="error",
                message_i18n=_lt(
                    "第一名缺少病例引用。", "Rank one lacks a case citation."
                ),
                candidate_ranks=[1],
            ),
        ],
        required_changes_i18n=[_lt("补足候选和引用。", "Add candidates and citations.")],
    )
    draft = _valid_draft()
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert validation.valid is True
    assert effective.accepted is True
    assert effective.revision_required is False
    assert effective.issues == []
    assert [
        item["issue"]["code"] for item in audit["dismissed_invalid_issues"]
    ] == [
        "insufficient_concrete_pathogens",
        "missing_citation_for_candidate",
    ]
    assert [
        item["objective_group"] for item in audit["dismissed_invalid_issues"]
    ] == ["concrete_top5_count", "supporting_evidence"]


def test_partial_critic_reconciliation_clears_unlinked_dismissed_instructions() -> None:
    removed_instruction = "ADD A FALSE SIXTH PATHOGEN MUST NOT REACH REVISION"
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "虚假客观问题与真实主观问题混合。",
            "Mixed false objective and retained subjective issues: %s"
            % removed_instruction,
        ),
        issues=[
            DevelopmentCriticIssue(
                code="insufficient_concrete_pathogens",
                severity="error",
                message_i18n=_lt(
                    "错误声称具体病原体不足。",
                    "Incorrectly claims too few concrete pathogens.",
                ),
            ),
            DevelopmentCriticIssue(
                code="exposure_weighting_needs_review",
                severity="error",
                message_i18n=_lt(
                    "暴露证据的排序权重需要复核。",
                    "Review the weighting of exposure evidence.",
                ),
            ),
        ],
        required_changes_i18n=[
            _lt("错误要求增加候选。", removed_instruction),
            _lt("复核暴露证据。", "Review exposure evidence."),
        ],
    )
    draft = _valid_draft()
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert effective.accepted is False
    assert effective.revision_required is True
    assert [item.code for item in effective.issues] == [
        "exposure_weighting_needs_review"
    ]
    assert effective.required_changes_i18n == []
    assert removed_instruction not in json.dumps(
        effective.model_dump(mode="json"), ensure_ascii=False
    )
    assert removed_instruction in json.dumps(audit["raw_decision"], ensure_ascii=False)
    assert removed_instruction not in json.dumps(
        audit["effective_decision"], ensure_ascii=False
    )
    assert [
        item["issue"]["code"] for item in audit["dismissed_invalid_issues"]
    ] == ["insufficient_concrete_pathogens"]


def test_mixed_critic_revision_payload_contains_only_retained_issues(
    client: TestClient,
) -> None:
    removed_instruction = "ADD A FALSE SIXTH PATHOGEN MUST NOT REACH REVISION"

    class MixedCriticProvider(_DevelopmentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.revision_payloads: list[dict[str, Any]] = []

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if request.revision_context is not None:
                self.revision_payloads.append(request.model_dump(mode="json"))
            return await super().invoke_development_synthesis(
                provider, api_key, request
            )

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "审稿中混合了一项虚假客观问题。",
                    "Mixed review containing: %s" % removed_instruction,
                ),
                issues=[
                    DevelopmentCriticIssue(
                        code="insufficient_concrete_pathogens",
                        severity="error",
                        message_i18n=_lt(
                            "错误声称具体病原体不足。",
                            "Incorrectly claims too few concrete pathogens.",
                        ),
                    ),
                    DevelopmentCriticIssue(
                        code="exposure_weighting_needs_review",
                        severity="error",
                        message_i18n=_lt(
                            "需要复核暴露证据的排序权重。",
                            "Review the weighting of exposure evidence.",
                        ),
                    ),
                ],
                required_changes_i18n=[
                    _lt("错误要求增加候选。", removed_instruction),
                    _lt("复核暴露证据。", "Review exposure evidence."),
                ],
            ), self._metadata("mixed-critic")

    provider_double = MixedCriticProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="mixed-critic-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert len(provider_double.revision_payloads) == 1
    revision_payload = provider_double.revision_payloads[0]
    rendered_revision = json.dumps(revision_payload, ensure_ascii=False)
    assert removed_instruction not in rendered_revision
    revision_critic = revision_payload["revision_context"]["critic_result"]
    assert [item["code"] for item in revision_critic["issues"]] == [
        "exposure_weighting_needs_review"
    ]
    assert revision_critic["required_changes_i18n"] == []
    assert run["result"]["review"]["revision_count"] == 1
    assert [
        item["code"] for item in run["result"]["review"]["critic"]["issues"]
    ] == ["exposure_weighting_needs_review"]
    assert removed_instruction not in json.dumps(
        run["result"]["review"]["critic"], ensure_ascii=False
    )

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    critic_node = next(item for item in trace["nodes"] if item["node_key"] == "critic")
    critic_detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], critic_node["id"])
    ).json()
    reconciliation = next(
        item["content"] for item in critic_detail["artifacts"]
        if item["artifact_type"] == "critic_issue_reconciliation"
    )
    assert removed_instruction in json.dumps(
        reconciliation["raw_decision"], ensure_ascii=False
    )
    assert removed_instruction not in json.dumps(
        reconciliation["effective_decision"], ensure_ascii=False
    )


def test_aspiration_critic_aliases_are_retained_for_real_contract_failures() -> None:
    """The aliases remain actionable when the deterministic contract agrees."""

    valid = _valid_draft()
    first_without_support = valid.concrete_pathogens[0].model_copy(
        update={"supporting_evidence": []}
    )
    invalid_draft = valid.model_copy(update={
        "concrete_pathogens": [
            first_without_support,
            *valid.concrete_pathogens[1:4],
        ],
    })
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "具体候选和病例引用确有缺失。",
            "Concrete candidates and case citations are genuinely missing.",
        ),
        issues=[
            DevelopmentCriticIssue(
                code="insufficient_concrete_pathogens",
                severity="error",
                message_i18n=_lt(
                    "具体病原体不足五项。", "Fewer than five concrete pathogens."
                ),
                candidate_ranks=[1, 2, 3, 4, 5],
            ),
            DevelopmentCriticIssue(
                code="missing_citation_for_candidate",
                severity="error",
                message_i18n=_lt(
                    "第一名缺少病例引用。", "Rank one lacks a case citation."
                ),
                candidate_ranks=[1],
            ),
        ],
        required_changes_i18n=[_lt("补足候选和引用。", "Add candidates and citations.")],
    )
    validation = validate_development_top5(
        invalid_draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=invalid_draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert validation.valid is False
    assert {item.code for item in validation.issues}.issuperset({
        "top5_count", "missing_supporting_evidence", "missing_case_evidence",
    })
    assert effective.accepted is False
    assert effective.revision_required is True
    assert [item.code for item in effective.issues] == [
        "insufficient_concrete_pathogens",
        "missing_citation_for_candidate",
    ]
    assert audit["dismissed_invalid_issues"] == []
    assert [item["disposition"] for item in audit["evaluations"]] == [
        "retained", "retained",
    ]


@pytest.mark.parametrize(
    "issue_code",
    ["missing_source_fragment_citation", "missing_exposure_citation"],
)
def test_patient_evidence_citation_critic_issues_remain_authoritative(
    issue_code: str,
) -> None:
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "病例证据引用需要修订。", "Patient evidence citation requires revision."
        ),
        issues=[DevelopmentCriticIssue(
            code=issue_code,
            severity="error",
            message_i18n=_lt(
                "病例来源或暴露证据可能遗漏。",
                "A patient source or exposure may have been omitted.",
            ),
            candidate_ranks=[1],
            source_fragment_ids=["fragment_001"],
        )],
        required_changes_i18n=[_lt(
            "核对病例来源证据。", "Verify the patient source evidence."
        )],
    )
    draft = _valid_draft()
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert validation.valid is True
    assert effective.accepted is False
    assert effective.revision_required is True
    assert [item.code for item in effective.issues] == [issue_code]
    assert audit["deferred_issues"] == []
    assert audit["evaluations"][0]["disposition"] == "retained"
    assert audit["evaluations"][0]["verification_kind"] == (
        "subjective_medical_review"
    )


@pytest.mark.parametrize(
    "issue_code",
    [
        "missing_external_citation_rank_1",
        "unlinked_literature_reference_rank_2",
        "absent_bibliographic_reference",
    ],
)
def test_explicit_external_citation_variants_are_deferred_to_enrichment(
    issue_code: str,
) -> None:
    critic = DevelopmentCriticResult(
        accepted=False,
        revision_required=True,
        review_summary_i18n=_lt(
            "外部文献引用尚未补强。", "External literature citation is pending."
        ),
        issues=[DevelopmentCriticIssue(
            code=issue_code,
            severity="error",
            message_i18n=_lt(
                "缺少外部文献引用。", "An external literature citation is missing."
            ),
            candidate_ranks=[1],
        )],
        required_changes_i18n=[_lt(
            "补充外部文献。", "Add external literature evidence."
        )],
    )
    draft = _valid_draft()
    validation = validate_development_top5(
        draft, valid_fragment_ids={"fragment_001"}
    )

    effective, audit = reconcile_development_critic_result(
        critic,
        draft=draft,
        validation=validation,
        valid_fragment_ids={"fragment_001"},
    )

    assert validation.valid is True
    assert effective.accepted is True
    assert effective.revision_required is False
    assert effective.issues == []
    assert audit["dismissed_invalid_issues"] == []
    assert [
        item["issue"]["code"] for item in audit["deferred_issues"]
    ] == [issue_code]
    assert audit["evaluations"][0]["disposition"] == (
        "deferred_to_candidate_evidence_enrichment"
    )


def test_external_citation_critic_issues_are_deferred_to_enrichment_without_revision(
    client: TestClient,
) -> None:
    class PrematureCitationCriticProvider(_DevelopmentProvider):
        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            assert not request.deterministic_issues
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "错误地要求总诊 Agent 在后续文献补强前先补齐外部引文。",
                    "Incorrectly requires external citations before post-critic enrichment.",
                ),
                issues=[
                    DevelopmentCriticIssue(
                        code="missing_evidence_citation",
                        severity="error",
                        message_i18n=_lt("缺少外部引文", "External citation is missing"),
                        candidate_ranks=[1],
                    ),
                    DevelopmentCriticIssue(
                        code="missing_evidence_source_ids_in_supporting_evidence",
                        severity="error",
                        message_i18n=_lt(
                            "支持证据中缺少外部文献ID",
                            "Supporting evidence lacks external literature IDs",
                        ),
                        candidate_ranks=[1, 2, 3, 4, 5],
                    ),
                ],
                required_changes_i18n=[
                    _lt("在总诊结果中补入文献", "Add literature to the synthesis")
                ],
            ), self._metadata("critic-premature-citations")

    provider_double = PrematureCitationCriticProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="deferred-citation-critic-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert provider_double.synthesis_calls == 1
    assert result["review"]["revision_count"] == 0
    assert result["review"]["status"] == "critic_accepted"
    assert result["review"]["critic"]["accepted"] is True
    assert result["review"]["critic"]["revision_required"] is False
    assert result["review"]["critic"]["issues"] == []
    assert "candidate_specific_evidence_coverage_partial" in result["warnings"]
    assert not any(item.startswith("critic:") for item in result["warnings"])

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["critic"]["outcome"] == "passed"
    assert nodes["revision"]["status"] == "skipped"
    assert nodes["candidate_evidence_enrichment"]["outcome"] == "warning"
    critic_detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], nodes["critic"]["id"]),
    ).json()
    reconciliation = next(
        item["content"] for item in critic_detail["artifacts"]
        if item["artifact_type"] == "critic_issue_reconciliation"
    )
    assert reconciliation["raw_decision"]["issue_codes"] == [
        "missing_evidence_citation",
        "missing_evidence_source_ids_in_supporting_evidence",
    ]
    assert reconciliation["dismissed_invalid_issues"] == []
    assert [
        item["issue"]["code"] for item in reconciliation["deferred_issues"]
    ] == [
        "missing_evidence_citation",
        "missing_evidence_source_ids_in_supporting_evidence",
    ]
    assert all(
        item["disposition"] == "deferred_to_candidate_evidence_enrichment"
        for item in reconciliation["deferred_issues"]
    )
    assert nodes["critic"]["metadata"]["deferred_issue_count"] == 2


def test_hallucinated_category_overlap_is_not_a_top5_contract_failure(
    client: TestClient,
) -> None:
    class HallucinatedCategoryRuleCriticProvider(_DevelopmentProvider):
        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            assert not request.deterministic_issues
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "错误地把同一大类的两个具体病原体当成合同冲突。",
                    "Incorrectly treats two concrete pathogens in one category as a contract conflict.",
                ),
                issues=[
                    DevelopmentCriticIssue(
                        code=code,
                        severity="error",
                        message_i18n=_lt(
                            "这是一条可由确定性合同驳回的虚构问题。",
                            "This is a fabricated issue disproved by the deterministic contract.",
                        ),
                        candidate_ranks=[1, 2, 3, 4, 5],
                    )
                    for code in (
                        "category_overlap_in_top5",
                        "category_restriction_violation",
                        "category_occupies_top5",
                        "rank_score_mismatch",
                        "score_order_issue",
                        "score_ordering_inconsistent",
                        "missing_rank5",
                        "unlinked_evidence_usage",
                        "taxonomy_unresolved_rank4",
                    )
                ],
                required_changes_i18n=[_lt("强制替换第二名", "Force replacement of rank two")],
            ), self._metadata("critic-category-overlap-hallucination")

    provider_double = HallucinatedCategoryRuleCriticProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="category-overlap-hallucination-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert provider_double.synthesis_calls == 1
    assert run["result"]["review"]["revision_count"] == 0
    assert run["result"]["review"]["critic"]["accepted"] is True
    assert run["result"]["review"]["critic"]["issues"] == []

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    critic_node = next(item for item in trace["nodes"] if item["node_key"] == "critic")
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], critic_node["id"]),
    ).json()
    reconciliation = next(
        item["content"] for item in detail["artifacts"]
        if item["artifact_type"] == "critic_issue_reconciliation"
    )
    assert [
        item["issue"]["code"] for item in reconciliation["dismissed_invalid_issues"]
    ] == [
        "category_overlap_in_top5",
        "category_restriction_violation",
        "category_occupies_top5",
        "rank_score_mismatch",
        "score_order_issue",
        "score_ordering_inconsistent",
        "missing_rank5",
        "unlinked_evidence_usage",
        "taxonomy_unresolved_rank4",
    ]
    assert all(
        item["reason_code"] == "deterministic_contract_does_not_support_claim"
        for item in reconciliation["dismissed_invalid_issues"]
    )


def test_real_missing_candidate_fragment_is_retained_and_triggers_revision(
    client: TestClient,
) -> None:
    missing_fragment_id = "src_9999_definitely_missing"

    class RealMissingFragmentProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            draft, metadata = await super().invoke_development_synthesis(provider, api_key, request)
            if request.revision_context is None:
                first = draft.concrete_pathogens[0].model_copy(update={
                    "supporting_evidence": [_evidence(missing_fragment_id)],
                })
                draft = draft.model_copy(update={
                    "concrete_pathogens": [first, *draft.concrete_pathogens[1:]],
                })
            return draft, metadata

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            assert "unknown_source_fragment" in {
                item.code for item in request.deterministic_issues
            }
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "第一名确实引用了来源清单中不存在的片段。",
                    "Rank one genuinely cites a fragment absent from the manifest.",
                ),
                issues=[DevelopmentCriticIssue(
                    code="missing_source_fragment",
                    severity="error",
                    message_i18n=_lt(
                        "替换不存在的来源引用。",
                        "Replace the nonexistent source reference.",
                    ),
                    candidate_ranks=[1],
                    source_fragment_ids=[missing_fragment_id],
                )],
                required_changes_i18n=[_lt("修正第一名的来源引用", "Correct rank one's source reference")],
            ), self._metadata("critic-real-missing-fragment")

    provider_double = RealMissingFragmentProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="real-missing-fragment-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert provider_double.synthesis_calls == 2
    assert result["review"]["revision_count"] == 1
    assert result["review"]["critic"]["revision_required"] is True
    assert result["review"]["critic"]["issues"][0]["code"] == "missing_source_fragment"
    assert "critic:missing_source_fragment" in result["warnings"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["revision"]["status"] == "completed"
    critic_detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], nodes["critic"]["id"]),
    ).json()
    reconciliation = next(
        item["content"] for item in critic_detail["artifacts"]
        if item["artifact_type"] == "critic_issue_reconciliation"
    )
    assert reconciliation["dismissed_invalid_issues"] == []
    assert reconciliation["evaluations"][0]["disposition"] == "retained"
    assert reconciliation["evaluations"][0]["objective_evidence"][
        "unknown_target_candidate_references"
    ] == [missing_fragment_id]


def test_revision_technical_failure_retains_prior_valid_draft(
    client: TestClient,
) -> None:
    class CriticForcedTechnicalRevisionProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if request.revision_context is not None:
                self.synthesis_calls += 1
                self.synthesis_provider_ids.append(provider["id"])
                self.seen_full_source_text.append(request.source_text)
                raise ProviderInvocationError(
                    "synthetic_revision_failure",
                    "Synthetic revision failure",
                    retryable=False,
                )
            return await super().invoke_development_synthesis(provider, api_key, request)

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt("要求不必要的修订", "An unnecessary revision is requested"),
                issues=[DevelopmentCriticIssue(
                    code="unnecessary_revision_request",
                    severity="warning",
                    message_i18n=_lt("确定性合同已经通过", "The deterministic contract already passed"),
                )],
                required_changes_i18n=[_lt("重新生成", "Regenerate the draft")],
            ), self._metadata("critic-forced-revision")

    provider_double = CriticForcedTechnicalRevisionProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="technical-revision-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert result["fallback_mode"] == "revision_rejected_retained_prior_valid_draft"
    assert "revision_technical_failure" in result["warnings"]
    assert "revision_rejected_retained_prior_valid_draft" in result["warnings"]
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["status"] == "critic_changes_not_closed"
    assert len(result["concrete_pathogens"]) == 5

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    revision_node = next(item for item in trace["nodes"] if item["node_key"] == "revision")
    assert revision_node["status"] == "failed"
    assert revision_node["outcome"] == "warning"
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], revision_node["id"]),
    ).json()
    decision = next(
        item["content"] for item in detail["artifacts"]
        if item["artifact_type"] == "revision_transaction_decision"
    )
    assert decision["reason"] == "revision_agent_technical_failure"
    assert decision["prior_validation"]["valid"] is True
    assert decision["critic_issue_codes"] == ["unnecessary_revision_request"]


def test_invalid_initial_and_revision_drafts_still_use_agent_pool_fallback(
    client: TestClient,
) -> None:
    class AlwaysInvalidSynthesisProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            draft, metadata = await super().invoke_development_synthesis(provider, api_key, request)
            fragment_id = request.source_fragments[0].source_fragment_id
            return draft.model_copy(update={
                "concrete_pathogens": [
                    _draft_candidate(
                        1,
                        name="Bacteria",
                        taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
                        fragment_id=fragment_id,
                    ),
                    *draft.concrete_pathogens[1:],
                ],
            }), metadata

    provider_double = AlwaysInvalidSynthesisProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="agent-pool-regression-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["fallback_mode"] == "agent_pool_fallback"
    assert "agent_pool_fallback" in result["warnings"]
    assert "revision_rejected_retained_prior_valid_draft" not in result["warnings"]
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert [item["canonical_latin_name"] for item in result["concrete_pathogens"]] == [
        item[0] for item in _PATHOGENS
    ]


def test_two_providers_are_frozen_by_weight_rotated_across_specialists_and_split_for_critic(
    client: TestClient,
) -> None:
    provider_double = _DevelopmentProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    low_provider_id = _create_provider(
        client,
        name="secondary-low-weight",
        model="secondary-model",
        api_key="SECONDARY-KEY",
        weight=1.0,
    )
    high_provider_id = _create_provider(
        client,
        name="primary-high-weight",
        model="primary-model",
        api_key="PRIMARY-KEY",
        weight=9.0,
    )

    # Deliberately submit the low-weight Provider first. Runtime priority must
    # be frozen from weight, not inherited from browser array order.
    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [low_provider_id, high_provider_id],
    })
    assert created.status_code == 202, created.text
    assert created.json()["provider_ids"] == [high_provider_id, low_provider_id]
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed", run
    assert run["provider_ids"] == [high_provider_id, low_provider_id]

    stored = client.app.state.db.fetchone(
        "SELECT provider_configs_json FROM runs WHERE id = ?", (run["id"],),
    )
    frozen = json.loads(stored["provider_configs_json"])
    assert [item["id"] for item in frozen] == [high_provider_id, low_provider_id]
    assert [item["weight"] for item in frozen] == [9.0, 1.0]

    assert provider_double.specialist_provider_attempts == {
        "infectious_diseases": [high_provider_id],
        "critical_care_emergency": [low_provider_id],
        "clinical_epidemiology": [high_provider_id],
        "laboratory_medicine": [low_provider_id],
        "clinical_microbiology_culture": [high_provider_id],
        "radiology": [low_provider_id],
        "hepatobiliary_pancreatic": [high_provider_id],
        "pulmonology": [low_provider_id],
        "neurology_neuroinfection": [high_provider_id],
        "surgery_source_control": [low_provider_id],
        "antimicrobial_stewardship": [high_provider_id],
    }
    assert provider_double.synthesis_provider_ids == [high_provider_id]
    assert provider_double.critic_provider_ids == [low_provider_id]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    manifest_nodes = {item["key"]: item for item in trace["manifest"]["nodes"]}
    assert manifest_nodes["specialist:infectious_diseases"]["provider_id"] == high_provider_id
    assert manifest_nodes["specialist:critical_care_emergency"]["provider_id"] == low_provider_id
    assert manifest_nodes["synthesis"]["provider_id"] == high_provider_id
    assert manifest_nodes["critic"]["provider_id"] == low_provider_id


def test_specialist_primary_failure_uses_second_provider_and_run_still_completes(
    client: TestClient,
) -> None:
    class SpecialistFailoverProvider(_DevelopmentProvider):
        failing_provider_id: Optional[str] = None
        failed_once = False

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if (
                request.role.value == "infectious_diseases"
                and provider["id"] == self.failing_provider_id
                and not self.failed_once
            ):
                self.failed_once = True
                self.specialist_provider_attempts.setdefault(request.role.value, []).append(provider["id"])
                self.seen_full_source_text.append(request.source_text)
                raise ProviderInvocationError(
                    "synthetic_specialist_failure",
                    "Synthetic first-provider failure",
                    retryable=True,
                )
            return await super().invoke_development_specialist(provider, api_key, request)

    provider_double = SpecialistFailoverProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    secondary_id = _create_provider(
        client, name="failover-secondary", model="secondary-model", weight=1.0,
    )
    primary_id = _create_provider(
        client, name="failover-primary", model="primary-model", weight=8.0,
    )
    provider_double.failing_provider_id = primary_id

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [secondary_id, primary_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert provider_double.specialist_provider_attempts["infectious_diseases"] == [
        primary_id, secondary_id,
    ]
    timeline_observation = next(
        item for item in run["result"]["agent_observations"]
        if item["role"] == "infectious_diseases"
    )
    assert timeline_observation["status"] == "completed_with_warnings"
    assert "provider_failover_used" in timeline_observation["warning_codes"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    timeline_node = next(
        item for item in trace["nodes"]
        if item["node_key"] == "specialist:infectious_diseases"
    )
    assert timeline_node["status"] == "completed"
    assert timeline_node["outcome"] == "warning"
    assert timeline_node["metadata"]["attempt_count"] == 2
    assert [item["status"] for item in timeline_node["metadata"]["attempts"]] == [
        "failed", "completed",
    ]
    assert timeline_node["metadata"]["final_provider_id"] == secondary_id


def test_dns_preflight_failure_does_not_consume_provider_request_budget(
    client: TestClient,
) -> None:
    class DNSPreflightFailureProvider(_DevelopmentProvider):
        invoked = False

        async def preflight_provider(self, provider: dict[str, Any]) -> str:
            raise ProviderInvocationError(
                "provider_dns_error",
                "Synthetic DNS failure",
                retryable=True,
                safe_details={
                    "timeout_phase": "dns_preflight",
                    "request_dispatched": False,
                    "dns_failure_class": "temporary",
                },
            )

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.invoked = True
            raise AssertionError("HTTP-capable invocation must not start")

    provider_double = DNSPreflightFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="dns-preflight-budget-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "failed"
    assert provider_double.invoked is False

    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 0

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    specialist_nodes = [
        item for item in trace["nodes"]
        if item["node_key"].startswith("specialist:")
    ]
    failed_specialists = [item for item in specialist_nodes if item["status"] == "failed"]
    skipped_specialists = [item for item in specialist_nodes if item["status"] == "skipped"]
    assert len(failed_specialists) == 11
    assert len(skipped_specialists) == 14
    for node in failed_specialists:
        assert node["error"]["root_error_code"] == "provider_dns_error"
        assert node["error"]["timeout_phase"] == "dns_preflight"
        assert node["error"]["provider_requests_used"] == 0
        assert node["error"]["attempts"][0]["request_dispatched"] is False


def test_dns_revalidation_failure_after_claim_refunds_budget_before_http(
    client: TestClient,
) -> None:
    class DNSRevalidationFailureProvider(_DevelopmentProvider):
        async def preflight_provider(self, provider: dict[str, Any]) -> str:
            return "https://provider.example/v1/chat/completions"

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            raise ProviderInvocationError(
                "provider_dns_error",
                "Synthetic DNS revalidation failure",
                retryable=True,
                safe_details={
                    "timeout_phase": "dns_preflight",
                    "request_dispatched": False,
                    "dns_failure_class": "temporary",
                },
            )

    provider_double = DNSRevalidationFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="dns-revalidation-refund")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "failed"

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    specialist_nodes = [
        item for item in trace["nodes"]
        if item["node_key"].startswith("specialist:")
    ]
    failed_specialists = [item for item in specialist_nodes if item["status"] == "failed"]
    skipped_specialists = [item for item in specialist_nodes if item["status"] == "skipped"]
    assert len(failed_specialists) == 11
    assert len(skipped_specialists) == 14
    for node in failed_specialists:
        assert node["error"]["provider_requests_used"] == 0
        attempt = node["error"]["attempts"][0]
        assert attempt["request_dispatched"] is False
        assert attempt["provider_budget_refunded_before_http"] is True
        assert "global_provider_request_number" not in attempt


def test_public_preflight_then_private_egress_revalidation_is_blocked_and_refunded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_answers: list[str] = []
    resolver_lock = threading.Lock()
    http_started = threading.Event()

    def rebinding_resolver(
        _host: str, port: int, *_args: Any, **_kwargs: Any,
    ) -> Any:
        with resolver_lock:
            address = (
                "93.184.216.34" if not resolver_answers else "127.0.0.1"
            )
            resolver_answers.append(address)
        if address == "93.184.216.34":
            # Give all currently admitted specialist calls time to join the
            # same preflight task. Completed results themselves are not cached.
            time.sleep(0.03)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
        ]

    class HTTPMustNotStart:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            http_started.set()
            raise AssertionError(
                "HTTP transport must not start after private DNS revalidation"
            )

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_resolver)
    monkeypatch.setattr(httpx, "AsyncClient", HTTPMustNotStart)
    provider = client.post("/api/providers", json={
        "name": "dns-rebinding-provider",
        "kind": "openai_compatible",
        "model": "synthetic-model",
        "base_url": "https://provider-rebind.example/v1",
        "api_key": "REBINDING-TEST-KEY",
        "data_boundary": "external",
    })
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    _mark_provider_ready(client, provider_id)

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "failed"
    assert resolver_answers[0] == "93.184.216.34"
    assert "127.0.0.1" in resolver_answers[1:]
    assert http_started.is_set() is False
    outputs = client.app.state.db.fetchall(
        """SELECT status, error_json FROM run_model_outputs
           WHERE run_id = ? ORDER BY created_at""",
        (run["id"],),
    )
    assert outputs
    assert all(item["status"] == "failed" for item in outputs)
    assert all(
        json.loads(item["error_json"])["code"] == "unsafe_provider_url"
        and json.loads(item["error_json"])["details"]
        ["request_dispatched"] is False
        for item in outputs
    )
    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    specialist_nodes = [
        item for item in trace["nodes"]
        if item["node_key"].startswith("specialist:")
        and item["status"] == "failed"
    ]
    attempts = [
        attempt
        for item in specialist_nodes
        for attempt in item["error"]["attempts"]
        if attempt["status"] == "failed"
    ]
    assert attempts
    assert all(attempt["request_dispatched"] is False for attempt in attempts)
    assert all("global_provider_request_number" not in attempt for attempt in attempts)
    assert any(
        attempt.get("provider_budget_refunded_before_http") is True
        for attempt in attempts
    )
    rendered = json.dumps([run, trace], ensure_ascii=False)
    assert "REBINDING-TEST-KEY" not in rendered
    assert "HTTP transport must not start" not in rendered


def test_single_provider_transport_failures_use_two_specialist_retries_and_reserve_decision_calls(
    client: TestClient,
) -> None:
    class FirstTransportAttemptFailsProvider(_DevelopmentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.role_attempts: dict[str, int] = {}
            self.network_attempt_count = 0

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            role = request.role.value
            self.network_attempt_count += 1
            self.role_attempts[role] = self.role_attempts.get(role, 0) + 1
            self.specialist_provider_attempts.setdefault(role, []).append(provider["id"])
            if self.role_attempts[role] == 1:
                raise ProviderInvocationError(
                    "provider_timeout",
                    "Synthetic first HTTP read timeout",
                    retryable=True,
                    safe_details={
                        "timeout_phase": "http_read",
                        "request_dispatched": True,
                    },
                )
            # Avoid recording the successful attempt twice in the helper's
            # provider-attempt list while retaining its schema-valid result.
            previous = list(self.specialist_provider_attempts[role])
            result = await super().invoke_development_specialist(
                provider, api_key, request
            )
            self.specialist_provider_attempts[role] = previous
            return result

    provider_double = FirstTransportAttemptFailsProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="single-provider-retry-budget")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    # Eleven selected specialists consume their first attempts. The specialist
    # ceiling of twelve admits exactly one same-provider retry while keeping
    # six global requests available for synthesis/critic/revision recovery.
    assert provider_double.network_attempt_count == 12
    assert provider_double.synthesis_calls == 1
    assert provider_double.critic_calls == 1
    result = run["result"]
    assert result["fallback_mode"] == "none"
    assert len(result["concrete_pathogens"]) == 5

    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 14

    completed_specialists = [
        item for item in result["agent_observations"]
        if item["role"] in {
            "infectious_diseases", "critical_care_emergency", "clinical_epidemiology",
            "laboratory_medicine", "clinical_microbiology_culture", "radiology",
            "hepatobiliary_pancreatic", "pulmonology", "neurology_neuroinfection",
            "surgery_source_control", "antimicrobial_stewardship",
        } and item["status"] != "failed"
    ]
    assert len(completed_specialists) == 1
    assert all(
        "provider_transport_retry_used" in item["warning_codes"]
        for item in completed_specialists
    )

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    failed_specialists = [
        item for item in trace["nodes"]
        if item["node_key"].startswith("specialist:")
        and item["status"] == "failed"
    ]
    assert len(failed_specialists) == 10
    for node in failed_specialists:
        assert node["error"]["code"] == (
            "development_provider_call_budget_exhausted"
        )
        assert node["error"]["root_error_code"] == "provider_timeout"
        assert node["error"]["timeout_phase"] == "http_read"
        assert node["error"]["provider_requests_used"] == 12


@pytest.mark.parametrize("http_error_code", ["provider_http_429", "provider_http_503"])
def test_single_provider_retryable_http_response_retries_once_and_completes(
    client: TestClient,
    http_error_code: str,
) -> None:
    class OneRetryableHTTPFailureProvider(_DevelopmentProvider):
        failed_once = False
        network_attempt_count = 0

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.network_attempt_count += 1
            if (
                request.role.value == "clinical_epidemiology"
                and not self.failed_once
            ):
                self.failed_once = True
                self.specialist_provider_attempts.setdefault(
                    request.role.value, []
                ).append(provider["id"])
                raise ProviderInvocationError(
                    http_error_code,
                    "Synthetic retryable HTTP response",
                    retryable=True,
                )
            return await super().invoke_development_specialist(
                provider, api_key, request
            )

    provider_double = OneRetryableHTTPFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="single-provider-http-retry")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed", run
    assert provider_double.network_attempt_count == 12
    assert provider_double.synthesis_calls == 1
    assert provider_double.critic_calls == 1

    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 14
    exposure = next(
        item for item in run["result"]["agent_observations"]
        if item["role"] == "clinical_epidemiology"
    )
    assert "provider_http_retry_used" in exposure["warning_codes"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    exposure_node = next(
        item for item in trace["nodes"]
        if item["node_key"] == "specialist:clinical_epidemiology"
    )
    assert [
        item["provider_id"] for item in exposure_node["metadata"]["attempts"]
    ] == [provider_id, provider_id]
    assert exposure_node["metadata"]["attempts"][0]["error_code"] == (
        http_error_code
    )


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [("provider_http_400", False), ("provider_schema_mismatch", False)],
)
def test_single_provider_nonretryable_http_or_schema_error_is_not_retried(
    client: TestClient,
    error_code: str,
    retryable: bool,
) -> None:
    class NonretryableProvider(_DevelopmentProvider):
        network_attempt_count = 0

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.network_attempt_count += 1
            raise ProviderInvocationError(
                error_code,
                "Synthetic nonretryable provider result",
                retryable=retryable,
            )

    provider_double = NonretryableProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="single-provider-no-retry")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "failed"
    assert provider_double.network_attempt_count == 11
    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 11


def test_run_wide_provider_budget_allows_failover_and_revision_within_eighteen_requests(
    client: TestClient,
) -> None:
    class FailoverThenRevisionProvider(_DevelopmentProvider):
        failing_provider_id: Optional[str] = None
        failed_once = False

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if (
                request.role.value == "infectious_diseases"
                and provider["id"] == self.failing_provider_id
                and not self.failed_once
            ):
                self.failed_once = True
                self.specialist_provider_attempts.setdefault(
                    request.role.value, []
                ).append(provider["id"])
                raise ProviderInvocationError(
                    "synthetic_specialist_failure",
                    "Synthetic first-provider failure",
                    retryable=True,
                )
            return await super().invoke_development_specialist(
                provider, api_key, request
            )

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "审稿建议再修订一次。", "The critic requests one revision."
                ),
                issues=[DevelopmentCriticIssue(
                    code="medical_revision_requested",
                    severity="warning",
                    message_i18n=_lt(
                        "补充医学解释。", "Add further medical explanation."
                    ),
                )],
                required_changes_i18n=[_lt(
                    "补充医学解释。", "Add further medical explanation."
                )],
            ), self._metadata("critic-budget")

    provider_double = FailoverThenRevisionProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    secondary_id = _create_provider(
        client, name="budget-secondary", model="secondary-model", weight=1.0,
    )
    primary_id = _create_provider(
        client, name="budget-primary", model="primary-model", weight=8.0,
    )
    provider_double.failing_provider_id = primary_id

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [secondary_id, primary_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert provider_double.synthesis_calls == 2
    assert provider_double.critic_calls == 1
    assert run["result"]["fallback_mode"] == "none"
    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 15

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    revision = next(
        item for item in trace["nodes"] if item["node_key"] == "revision"
    )
    assert revision["status"] == "completed"
    assert revision["outcome"] == "passed"
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], revision["id"])
    ).json()
    rendered = json.dumps(detail, ensure_ascii=False)
    assert "development_provider_call_budget_exhausted" not in rendered
    assert model_request_count <= 18


def test_critic_technical_failure_is_warning_only_when_deterministic_top5_is_valid(
    client: TestClient,
) -> None:
    class CriticFailureProvider(_DevelopmentProvider):
        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            raise ProviderInvocationError(
                "synthetic_critic_failure",
                "Synthetic critic failure",
                retryable=False,
            )

    provider_double = CriticFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="critic-failure-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert len(result["concrete_pathogens"]) == 5
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["critic"] is None
    assert "critic_technical_failure" in result["warnings"]
    critic_observation = next(
        item for item in result["agent_observations"]
        if item["role"] == "independent_critic"
    )
    assert critic_observation["status"] == "failed"
    assert critic_observation["warning_codes"] == ["critic_technical_failure"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["critic"]["status"] == "failed"
    assert nodes["critic"]["outcome"] == "warning"
    assert nodes["revision"]["status"] == "skipped"


def test_critic_role_timeout_preserves_contract_valid_initial_draft(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingCriticProvider(_DevelopmentProvider):
        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            await asyncio.Event().wait()

    monkeypatch.setattr(
        engine_module,
        "DEVELOPMENT_CRITIC_ROLE_TIMEOUT_SECONDS",
        0.02,
    )
    provider_double = HangingCriticProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="critic-timeout-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert len(result["concrete_pathogens"]) == 5
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["critic"] is None
    assert "development_hard_timeout" not in result["warnings"]
    assert "critic_technical_failure" in result["warnings"]
    assert "critic_role_timeout" in result["warnings"]

    critic_observation = next(
        item for item in result["agent_observations"]
        if item["role"] == "independent_critic"
    )
    assert critic_observation["warning_codes"] == [
        "critic_technical_failure",
        "critic_role_timeout",
    ]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["critic"]["status"] == "failed"
    assert nodes["critic"]["outcome"] == "warning"
    assert nodes["critic"]["metadata"]["role_timeout_seconds"] == pytest.approx(0.02)
    assert nodes["revision"]["status"] == "skipped"

    model_output = client.app.state.db.fetchone(
        """SELECT status, error_json FROM run_model_outputs
           WHERE run_id = ? AND node_run_id = ?""",
        (run["id"], nodes["critic"]["id"]),
    )
    assert model_output["status"] == "failed"
    assert json.loads(model_output["error_json"])["code"] == (
        "development_agent_role_timeout"
    )


def test_revision_role_timeout_retains_prior_contract_valid_draft(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingRevisionProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if request.revision_context is not None:
                self.synthesis_calls += 1
                self.synthesis_provider_ids.append(provider["id"])
                self.seen_full_source_text.append(request.source_text)
                await asyncio.Event().wait()
            return await super().invoke_development_synthesis(
                provider, api_key, request
            )

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "建议补充医学解释。",
                    "The critic requests additional medical explanation.",
                ),
                issues=[DevelopmentCriticIssue(
                    code="medical_explanation_requested",
                    severity="warning",
                    message_i18n=_lt(
                        "补充鉴别诊断解释。",
                        "Add differential-diagnosis explanation.",
                    ),
                )],
                required_changes_i18n=[_lt(
                    "补充鉴别诊断解释。",
                    "Add differential-diagnosis explanation.",
                )],
            ), self._metadata("critic-revision-timeout")

    monkeypatch.setattr(
        engine_module,
        "DEVELOPMENT_REVISION_ROLE_TIMEOUT_SECONDS",
        0.02,
    )
    provider_double = HangingRevisionProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="revision-timeout-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert len(result["concrete_pathogens"]) == 5
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["revision_count"] == 1
    assert result["fallback_mode"] == (
        "revision_rejected_retained_prior_valid_draft"
    )
    assert "development_hard_timeout" not in result["warnings"]
    assert "revision_technical_failure" in result["warnings"]
    assert "revision_role_timeout" in result["warnings"]
    assert "revision_rejected_retained_prior_valid_draft" in result["warnings"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["revision"]["status"] == "failed"
    assert nodes["revision"]["outcome"] == "warning"
    assert nodes["revision"]["metadata"]["role_timeout_seconds"] == pytest.approx(0.02)

    model_output = client.app.state.db.fetchone(
        """SELECT status, error_json FROM run_model_outputs
           WHERE run_id = ? AND node_run_id = ?""",
        (run["id"], nodes["revision"]["id"]),
    )
    assert model_output["status"] == "failed"
    assert json.loads(model_output["error_json"])["code"] == (
        "development_agent_role_timeout"
    )


def test_run_hard_timeout_finalizes_model_outputs_and_releases_provider_slots(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingProviderWithRealSlots:
        def __init__(self) -> None:
            self.slot_client = ProviderClient(
                max_concurrent_requests_per_provider=3
            )

        async def acquire_development_request_slot(
            self, provider: dict[str, Any],
        ) -> Any:
            return await self.slot_client.acquire_development_request_slot(
                provider
            )

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            await asyncio.Event().wait()

    monkeypatch.setattr(
        engine_module,
        "DEVELOPMENT_HARD_TIMEOUT_SECONDS",
        0.05,
    )
    provider_double = HangingProviderWithRealSlots()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(
        client,
        api_key="HARD-DEADLINE-TEST-KEY",
        name="hard-deadline-provider",
    )

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "failed"
    assert run["result"]["status"] == "technical_failure"
    assert "development_hard_timeout" in run["result"]["warnings"]

    model_outputs = client.app.state.db.fetchall(
        """SELECT status, error_json, completed_at
           FROM run_model_outputs WHERE run_id = ? ORDER BY created_at""",
        (run["id"],),
    )
    # Three calls hold the configured per-Provider semaphore; the remaining
    # selected specialist coroutines are cancelled while still queued and
    # therefore never create model-output rows.
    assert len(model_outputs) == 3
    assert all(item["status"] == "failed" for item in model_outputs)
    assert all(item["completed_at"] for item in model_outputs)
    errors = [json.loads(item["error_json"]) for item in model_outputs]
    assert all(
        item["code"] == "development_agent_cancelled_due_run_deadline"
        and item["retryable"] is False
        and item["details"]["request_dispatched"] is True
        and item["details"]["timeout_phase"] in {
            "provider_http_request", "run_hard_timeout_cleanup",
        }
        for item in errors
    )
    assert client.app.state.db.fetchone(
        """SELECT COUNT(*) AS count FROM run_model_outputs
           WHERE run_id = ? AND status = 'running'""",
        (run["id"],),
    )["count"] == 0

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    specialist_nodes = [
        item for item in trace["nodes"]
        if item["node_key"].startswith("specialist:")
    ]
    assert len(specialist_nodes) == 25
    assert len([item for item in specialist_nodes if item["status"] == "failed"]) == 11
    assert len([item for item in specialist_nodes if item["status"] == "skipped"]) == 14
    public_models = client.get("/api/runs/%s/models" % run["id"]).json()
    rendered = json.dumps([run, trace, public_models], ensure_ascii=False)
    assert "HARD-DEADLINE-TEST-KEY" not in rendered
    assert "CancelledError" not in rendered

    async def reacquire_all_three_slots() -> None:
        provider = client.app.state.db.fetchone(
            "SELECT * FROM providers WHERE id = ?", (provider_id,)
        )
        leases = []
        try:
            for _ in range(3):
                leases.append(await asyncio.wait_for(
                    provider_double.acquire_development_request_slot(provider),
                    timeout=0.2,
                ))
        finally:
            for lease in leases:
                lease.release()

    # Execute on TestClient's application loop so this probes the exact
    # semaphore pool used by the cancelled run rather than a fresh loop pool.
    client.portal.call(reacquire_all_three_slots)


def test_synthesis_all_providers_failed_recovers_from_valid_specialist_pool(
    client: TestClient,
) -> None:
    class SynthesisFailureProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.synthesis_calls += 1
            self.synthesis_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            raise ProviderInvocationError(
                "synthetic_synthesis_failure",
                "Synthetic synthesis failure",
                retryable=True,
            )

    provider_double = SynthesisFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    secondary_id = _create_provider(
        client, name="synthesis-secondary", model="secondary-model", weight=1.0,
    )
    primary_id = _create_provider(
        client, name="synthesis-primary", model="primary-model", weight=8.0,
    )

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [secondary_id, primary_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert provider_double.synthesis_provider_ids == [primary_id, secondary_id]
    result = run["result"]
    assert result["schema_version"] == "owlpath.result.v3"
    assert result["status"] == "completed_with_warnings"
    assert [item["rank"] for item in result["concrete_pathogens"]] == [1, 2, 3, 4, 5]
    assert len({
        item["canonical_latin_name"] for item in result["concrete_pathogens"]
    }) == 5
    assert all(
        item["taxonomy_resolution_status"] in {"resolved", "cache_resolved"}
        for item in result["concrete_pathogens"]
    )
    assert result["fallback_mode"] == "agent_pool_fallback"
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["unknown_score"] == 1.0
    assert "synthesis_technical_failure" in result["warnings"]
    assert "agent_pool_fallback" in result["warnings"]
    rendered = json.dumps(result, ensure_ascii=False).casefold()
    assert "abstain" not in rendered
    assert "转人工" not in rendered

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["synthesis"]["status"] == "failed"
    assert nodes["synthesis"]["outcome"] == "warning"
    assert nodes["contract_validator"]["status"] == "completed"
    assert nodes["contract_validator"]["outcome"] == "passed"
    assert nodes["critic"]["status"] == "completed"
    assert nodes["revision"]["status"] == "skipped"
    assert nodes["candidate_evidence_enrichment"]["status"] == "completed"

    validator_detail = client.get(
        "/api/runs/%s/trace/nodes/%s"
        % (run["id"], nodes["contract_validator"]["id"])
    ).json()
    recovery = next(
        item["content"] for item in validator_detail["artifacts"]
        if item["artifact_type"] == "synthesis_failure_agent_pool_recovery"
    )
    assert recovery["recovery_mode"] == "agent_pool_fallback"
    assert recovery["ranked_candidate_count"] == 5
    assert recovery["deterministic_validation"]["valid"] is True
    assert recovery["synthesis_node_status_preserved"] == "failed"

    # Eleven selected specialists + two failed synthesis attempts + one critic.
    # Recovery itself is deterministic and must not create a hidden call.
    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 14
    assert len(provider_double.specialist_calls) == 11
    assert provider_double.synthesis_calls == 2
    assert provider_double.critic_calls == 1


@pytest.mark.parametrize("fallback_trigger", ["synthesis_failure", "invalid_revision"])
def test_fallback_publication_preserves_frozen_roles_across_taxonomy_aliases(
    client: TestClient,
    fallback_trigger: str,
) -> None:
    aliases = {
        "Aeromonas hydrophila": "A. hydrophila",
        "Edwardsiella tarda": "E. tarda",
        "Vibrio vulnificus": "V. vulnificus",
        "Streptococcus suis": "S. suis",
        "Klebsiella pneumoniae": "K. pneumoniae",
    }

    class AliasFallbackProvider(_DevelopmentProvider):
        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            result, metadata = await super().invoke_development_specialist(
                provider, api_key, request
            )
            return result.model_copy(update={
                "candidate_pool": [
                    proposal.model_copy(update={
                        "canonical_latin_name": aliases[proposal.canonical_latin_name],
                        "name_i18n": _lt(
                            aliases[proposal.canonical_latin_name],
                            aliases[proposal.canonical_latin_name],
                        ),
                    })
                    for proposal in result.candidate_pool
                ],
            }), metadata

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            if fallback_trigger == "synthesis_failure":
                self.synthesis_calls += 1
                self.synthesis_provider_ids.append(provider["id"])
                self.seen_full_source_text.append(request.source_text)
                raise ProviderInvocationError(
                    "synthetic_synthesis_failure",
                    "Synthetic synthesis failure",
                    retryable=True,
                )
            draft, metadata = await super().invoke_development_synthesis(
                provider, api_key, request
            )
            fragment_id = request.source_fragments[0].source_fragment_id
            return draft.model_copy(update={
                "concrete_pathogens": [
                    _draft_candidate(
                        1,
                        name="Bacteria",
                        taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
                        fragment_id=fragment_id,
                    ),
                    *draft.concrete_pathogens[1:],
                ],
            }), metadata

    class AliasCanonicalizingTaxonomy:
        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            canonical = {
                **{
                    latin.casefold(): (latin, zh_cn, tax_id)
                    for latin, zh_cn, tax_id in _PATHOGENS
                },
                **{
                    alias.casefold(): (latin, zh_cn, tax_id)
                    for latin, zh_cn, tax_id in _PATHOGENS
                    for alias in [aliases[latin]]
                },
            }
            resolved: dict[str, dict[str, Any]] = {}
            for raw_name in names:
                normalized = " ".join(str(raw_name).strip().casefold().split())
                record = canonical.get(normalized)
                if record is None:
                    resolved[normalized] = {
                        "ncbi_taxonomy_id": None,
                        "taxonomy_resolution_status": "unresolved",
                        "taxonomy_resolution_reason_code": "synthetic_not_found",
                        "canonical_latin_name": str(raw_name),
                        "name_i18n": {"en": str(raw_name), "status": "partial"},
                        "ncbi_taxonomy_rank": None,
                    }
                else:
                    latin, zh_cn, tax_id = record
                    resolved[normalized] = {
                        "ncbi_taxonomy_id": tax_id,
                        "taxonomy_resolution_status": "cache_resolved",
                        "taxonomy_resolution_reason_code": "synthetic_species_match",
                        "canonical_latin_name": latin,
                        "name_i18n": {
                            "zh_cn": zh_cn, "en": latin, "status": "complete",
                        },
                        "ncbi_taxonomy_rank": "species",
                    }
            return resolved

    provider_double = AliasFallbackProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = AliasCanonicalizingTaxonomy()
    provider_id = _create_provider(
        client, name="alias-fallback-%s" % fallback_trigger,
    )

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["fallback_mode"] == "agent_pool_fallback"
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert [item["canonical_latin_name"] for item in result["concrete_pathogens"]] == [
        item[0] for item in _PATHOGENS
    ]
    assert all(item["proposed_by_agent_roles"] for item in result["concrete_pathogens"])

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    validator_node = next(
        item for item in trace["nodes"] if item["node_key"] == "contract_validator"
    )
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], validator_node["id"])
    ).json()
    fallback_audits = [
        content["agent_pool_fallback_audit"]
        for artifact in detail["artifacts"]
        if isinstance((content := artifact.get("content")), dict)
        and isinstance(content.get("agent_pool_fallback_audit"), dict)
    ]
    assert fallback_audits
    audit = fallback_audits[-1]
    assert audit["provenance_attestation"]["role_claims_recomputed_server_side"] is True
    assert all(
        item["verified_agent_roles"]
        and item["source_fragment_manifest_membership_verified"] is True
        for item in audit["candidates"]
        if item["disposition"] == "selected"
    )
    rendered_audit = json.dumps(audit, ensure_ascii=False)
    assert all(alias not in rendered_audit for alias in aliases.values())


@pytest.mark.parametrize("invalid_pool_kind", ["fewer_than_five", "taxonomy_unresolved"])
def test_synthesis_failure_with_invalid_specialist_pool_remains_technical_failure(
    client: TestClient,
    invalid_pool_kind: str,
) -> None:
    class InvalidPoolSynthesisFailureProvider(_DevelopmentProvider):
        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            result, metadata = await super().invoke_development_specialist(
                provider, api_key, request
            )
            if invalid_pool_kind == "fewer_than_five":
                result = result.model_copy(update={
                    "candidate_pool": result.candidate_pool[:4],
                })
            return result, metadata

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.synthesis_calls += 1
            self.synthesis_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            raise ProviderInvocationError(
                "synthetic_synthesis_failure",
                "Synthetic synthesis failure",
                retryable=True,
            )

    class OneUnresolvedTaxonomy(_ResolvedTaxonomy):
        async def resolve(self, names: Any) -> dict[str, dict[str, Any]]:
            resolved = await super().resolve(names)
            if invalid_pool_kind == "taxonomy_unresolved":
                normalized = "klebsiella pneumoniae"
                resolved[normalized] = {
                    "ncbi_taxonomy_id": None,
                    "taxonomy_resolution_status": "unresolved",
                    "taxonomy_resolution_reason_code": "synthetic_not_found",
                    "canonical_latin_name": "Klebsiella pneumoniae",
                    "name_i18n": {
                        "en": "Klebsiella pneumoniae", "status": "partial",
                    },
                }
            return resolved

    provider_double = InvalidPoolSynthesisFailureProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = OneUnresolvedTaxonomy()
    provider_id = _create_provider(
        client, name="invalid-agent-pool-%s" % invalid_pool_kind,
    )

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "failed", run
    result = run["result"]
    assert result["status"] == "technical_failure"
    assert result["concrete_pathogens"] == []
    assert "synthesis_technical_failure" in result["warnings"]
    assert result["review"]["deterministic_validation"]["valid"] is False
    assert result["review"]["deterministic_validation"]["attempt_origin"] == (
        "synthesis_failure_agent_pool_fallback"
    )
    issue_codes = {
        item["code"]
        for item in result["review"]["deterministic_validation"]["issues"]
    }
    # Unresolved candidates are now removed before publication rather than
    # left in a nominal Top-5, so both invalid pools fail by honest underfill.
    assert "top5_count" in issue_codes

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["synthesis"]["status"] == "failed"
    assert nodes["synthesis"]["outcome"] == "warning"
    assert nodes["contract_validator"]["status"] == "completed"
    assert nodes["contract_validator"]["outcome"] == "warning"
    assert nodes["critic"]["status"] == "skipped"
    assert nodes["revision"]["status"] == "skipped"
    assert nodes["candidate_evidence_enrichment"]["status"] == "skipped"

    validator_detail = client.get(
        "/api/runs/%s/trace/nodes/%s"
        % (run["id"], nodes["contract_validator"]["id"])
    ).json()
    recovery = next(
        item["content"] for item in validator_detail["artifacts"]
        if item["artifact_type"] == "synthesis_failure_agent_pool_recovery"
    )
    assert recovery["agent_pool_fallback_audit"]["selected_candidate_count"] < 5
    if invalid_pool_kind == "taxonomy_unresolved":
        excluded_reasons = {
            reason
            for item in recovery["agent_pool_fallback_audit"]["candidates"]
            if item["disposition"] == "excluded"
            for reason in item["reason_codes"]
        }
        assert "taxonomy_not_resolved" in excluded_reasons

    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    assert model_request_count == 12
    assert provider_double.critic_calls == 0


def test_post_revision_underfilled_fallback_publishes_final_attempt_validation(
    client: TestClient,
) -> None:
    class InvalidDraftAndUnderfilledPoolProvider(_DevelopmentProvider):
        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            result, metadata = await super().invoke_development_specialist(
                provider, api_key, request
            )
            return result.model_copy(update={
                "candidate_pool": result.candidate_pool[:4],
            }), metadata

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            draft, metadata = await super().invoke_development_synthesis(
                provider, api_key, request
            )
            fragment_id = request.source_fragments[0].source_fragment_id
            return draft.model_copy(update={
                "concrete_pathogens": [
                    _draft_candidate(
                        1,
                        name="Bacteria",
                        taxonomic_rank=DevelopmentTaxonomicRank.CATEGORY,
                        fragment_id=fragment_id,
                    ),
                    *draft.concrete_pathogens[1:],
                ],
            }), metadata

    provider_double = InvalidDraftAndUnderfilledPoolProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="post-revision-underfilled-pool")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "failed", run
    assert provider_double.synthesis_calls == 2
    result = run["result"]
    assert result["status"] == "technical_failure"
    public_validation = result["review"]["deterministic_validation"]
    assert public_validation["valid"] is False
    assert public_validation["attempt_origin"] == (
        "post_revision_agent_pool_fallback"
    )
    public_issue_codes = {item["code"] for item in public_validation["issues"]}
    assert "top5_count" in public_issue_codes
    assert "generic_pathogen_name" not in public_issue_codes

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    validator_node = next(
        item for item in trace["nodes"] if item["node_key"] == "contract_validator"
    )
    detail = client.get(
        "/api/runs/%s/trace/nodes/%s" % (run["id"], validator_node["id"])
    ).json()
    fallback_artifact = next(
        item["content"] for item in detail["artifacts"]
        if item["artifact_type"] == "agent_pool_fallback_validation"
    )
    assert fallback_artifact["agent_pool_fallback_audit"][
        "selected_candidate_count"
    ] == 4
    assert public_validation == fallback_artifact["validation"]


def test_synthesis_pool_recovery_keeps_valid_fallback_when_revision_providers_fail(
    client: TestClient,
) -> None:
    class RecoveryCriticRequestsRevisionProvider(_DevelopmentProvider):
        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.synthesis_calls += 1
            self.synthesis_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            raise ProviderInvocationError(
                "synthetic_synthesis_failure",
                "Synthetic synthesis failure",
                retryable=True,
            )

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self.critic_calls += 1
            self.critic_provider_ids.append(provider["id"])
            self.seen_full_source_text.append(request.source_text)
            return DevelopmentCriticResult(
                accepted=False,
                revision_required=True,
                review_summary_i18n=_lt(
                    "建议改进候选排序的临床解释。",
                    "The clinical ranking explanation could be improved.",
                ),
                issues=[DevelopmentCriticIssue(
                    code="ranking_explanation_needs_clinical_refinement",
                    severity="error",
                    message_i18n=_lt(
                        "排序解释仍为机械回退表述。",
                        "The ranking explanation remains a mechanical fallback description.",
                    ),
                )],
                required_changes_i18n=[_lt(
                    "补充临床排序解释。", "Add a clinical ranking explanation.",
                )],
            ), self._metadata("critic")

    provider_double = RecoveryCriticRequestsRevisionProvider()
    client.app.state.engine.provider_client = provider_double
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    secondary_id = _create_provider(
        client, name="recovery-budget-secondary", model="secondary-model", weight=1.0,
    )
    primary_id = _create_provider(
        client, name="recovery-budget-primary", model="primary-model", weight=8.0,
    )

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [secondary_id, primary_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    result = run["result"]
    assert result["status"] == "completed_with_warnings"
    assert result["fallback_mode"] == "agent_pool_fallback"
    assert len(result["concrete_pathogens"]) == 5
    assert result["review"]["deterministic_validation"]["valid"] is True
    assert result["review"]["revision_count"] == 1
    assert "revision_technical_failure" in result["warnings"]
    assert "revision_rejected_retained_prior_valid_draft" in result["warnings"]

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    nodes = {item["node_key"]: item for item in trace["nodes"]}
    assert nodes["synthesis"]["status"] == "failed"
    assert nodes["contract_validator"]["status"] == "completed"
    assert nodes["critic"]["status"] == "completed"
    assert nodes["critic"]["outcome"] == "warning"
    assert nodes["revision"]["status"] == "failed"
    assert nodes["revision"]["outcome"] == "warning"

    model_request_count = client.app.state.db.fetchone(
        "SELECT COUNT(*) AS count FROM run_model_outputs WHERE run_id = ?",
        (run["id"],),
    )["count"]
    # Eleven specialists, two failed synthesis Provider attempts, one critic
    # and two failed revision Provider attempts consume 16 of the global 18.
    assert model_request_count == 16
    # Two initial synthesis attempts and two revision attempts use the same
    # synthesis invocation method, and all four fail as configured.
    assert provider_double.synthesis_calls == 4
    assert provider_double.critic_calls == 1


def test_v3_schema_diagnostics_persist_only_field_locations_and_error_types(
    client: TestClient,
) -> None:
    diagnostics = {
        "validation_errors": [{
            "loc": ["observations", 0, "statement_i18n", "zh_cn"],
            "type": "string_type",
        }],
    }

    class SchemaMismatchProvider(_DevelopmentProvider):
        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            raise ProviderInvocationError(
                "provider_schema_mismatch",
                "UNTRUSTED RAW MODEL PROSE MUST NOT PERSIST",
                retryable=False,
                safe_details=diagnostics,
            )

    client.app.state.engine.provider_client = SchemaMismatchProvider()
    client.app.state.engine.medical_retriever = _OfflineRetriever()
    client.app.state.engine.taxonomy_resolver = _ResolvedTaxonomy()
    provider_id = _create_provider(client, name="schema-diagnostic-provider")

    created = client.post("/api/development/runs", json={
        "text": _DEVELOPMENT_CASE_TEXT,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "failed"

    rows = client.app.state.db.fetchall(
        "SELECT error_json FROM run_model_outputs WHERE run_id = ? ORDER BY created_at",
        (run["id"],),
    )
    assert len(rows) == 11
    for row in rows:
        stored_error = json.loads(row["error_json"])
        assert stored_error["details"] == diagnostics
        assert "UNTRUSTED RAW MODEL PROSE" not in json.dumps(stored_error)
        assert set(stored_error["details"]["validation_errors"][0]) == {"loc", "type"}

    trace = client.get("/api/runs/%s/trace" % run["id"]).json()
    specialist_nodes = [
        node for node in trace["nodes"]
        if node["node_key"].startswith("specialist:")
        and node["status"] == "failed"
    ]
    assert len(specialist_nodes) == 11
    for node in specialist_nodes:
        attempt = node["error"]["attempts"][0]
        assert attempt["error_details"] == diagnostics
        assert set(attempt["error_details"]["validation_errors"][0]) == {"loc", "type"}
    assert "UNTRUSTED RAW MODEL PROSE" not in json.dumps(trace)


def test_strict_endpoint_preserves_v2_safety_contract(client: TestClient) -> None:
    case = client.post("/api/cases", json={
        "case_alias": "STRICT-V2-COMPAT",
        "demographics": {
            "age_years": 68,
            "sex": "male",
            "immunocompromised": False,
            "care_setting": "emergency",
        },
        "context": {"primary_syndrome": "respiratory", "acquisition_context": "community"},
        "external_data_consent": False,
    })
    assert case.status_code == 201, case.text
    case_id = case.json()["id"]
    decision_time = datetime.now(timezone.utc)
    event = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom",
        "occurred_at": (decision_time - timedelta(hours=2)).isoformat(),
        "visible_at": (decision_time - timedelta(hours=1)).isoformat(),
        "source": "deidentified-test",
        "status": "final",
        "data": {"symptom": "发热咳嗽"},
        "quality": {"verified": True},
    })
    assert event.status_code == 201, event.text
    snapshot_hash = client.get(
        "/api/cases/%s/snapshot-hash" % case_id,
        params={"decision_time": decision_time.isoformat()},
    )
    assert snapshot_hash.status_code == 200, snapshot_hash.text
    source_hash = hashlib.sha256(b"strict-v2-compat").hexdigest()
    created = client.post("/api/runs", json={
        "case_id": case_id,
        "decision_time": decision_time.isoformat(),
        "provider_ids": [],
        "include_baseline": True,
        "clinical_review": {
            "accepted": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "statement_version": "owlpath-clinical-review-v1",
            "parser_version": "strict-v2-test",
            "source_text_sha256": source_hash,
            "input_snapshot_sha256": snapshot_hash.json()["input_snapshot_sha256"],
        },
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])

    assert run["status"] == "completed", run
    assert run["schema_version"] == "owlpath.result.v2"
    assert run["execution_graph_version"] == "owlpath.execution-graph.v1"
    assert run["trace_version"] == "owlpath.trace.v1"
    assert run["result"]["schema_version"] == "owlpath.result.v2"
    assert run["result"]["safety_action"] in {
        "non_infection", "species_set", "category_only", "next_test", "abstain",
    }
