import sys
from pathlib import Path
from typing import Any, Generator, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.medical_retrieval import RetrievalBundle
from app.models import (
    DevelopmentCriticResult,
    DevelopmentSpecialistObservation,
    DevelopmentSpecialistResult,
    DevelopmentSynthesisDraft,
)
from app.providers import ProviderClient


@pytest.fixture
def offline_medical_retriever() -> Any:
    """Keep unit/integration tests independent of public literature APIs."""

    class OfflineMedicalRetriever:
        async def retrieve(self, queries: Any) -> RetrievalBundle:
            return RetrievalBundle(
                warnings=[
                    "retrieval_europe_pmc_unavailable",
                    "retrieval_pubmed_unavailable",
                ],
                source_status={
                    "europe_pmc": "unavailable",
                    "pubmed": "unavailable",
                },
            )

    return OfflineMedicalRetriever()


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(database_path=tmp_path / "owlpath-test.db")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def make_client(tmp_path: Path, provider_client: Optional[ProviderClient] = None) -> TestClient:
    settings = Settings(database_path=tmp_path / "owlpath-custom.db")
    return TestClient(create_app(settings, provider_client=provider_client))


@pytest.fixture
def development_provider_factory() -> Any:
    """Build a schema-valid seven-call v3 provider double for API/trace tests."""

    class DevelopmentProviderDouble:
        def __init__(self, *, secret_marker: str = "", raw_marker: str = "") -> None:
            self.secret_marker = secret_marker
            self.raw_marker = raw_marker
            self.calls = 0
            self.keys: list[Optional[str]] = []
            self.source_texts: list[str] = []
            self.specialist_roles: list[str] = []
            self.synthesis_calls = 0
            self.critic_calls = 0

        @staticmethod
        def _lt(zh_cn: str, en: str) -> dict[str, str]:
            return {"zh_cn": zh_cn, "en": en, "status": "complete"}

        def _meta(self, role: str) -> dict[str, Any]:
            return {
                "request_id": "v3-%s" % role,
                "api_key": self.secret_marker,
                "raw_response": self.raw_marker,
                "nested": {"authorization": "Bearer %s" % self.secret_marker},
            }

        def _record(self, api_key: Optional[str], request: Any) -> None:
            self.calls += 1
            self.keys.append(api_key)
            self.source_texts.append(request.source_text)

        async def invoke_development_specialist(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self._record(api_key, request)
            role = request.role.value
            self.specialist_roles.append(role)
            fragment_id = request.source_fragments[0].source_fragment_id
            pathogen_specs = [
                ("Streptococcus pneumoniae", "肺炎链球菌", "species", "bacteria"),
                ("Legionella pneumophila", "嗜肺军团菌", "species", "bacteria"),
                ("Mycoplasma pneumoniae", "肺炎支原体", "species", "bacteria"),
                ("Klebsiella pneumoniae", "肺炎克雷伯菌", "species", "bacteria"),
                ("Influenza A virus", "甲型流感病毒", "virus_type", "virus"),
            ]
            result = DevelopmentSpecialistResult(
                role=request.role,
                summary_i18n=self._lt("虚构专科总结", "Synthetic specialist summary"),
                observations=[DevelopmentSpecialistObservation(
                    observation_id="obs-%s" % role,
                    kind="key_fact",
                    statement_i18n=self._lt("虚构病例事实", "Synthetic case fact"),
                    source_fragment_ids=[fragment_id],
                    importance="high",
                )],
                candidate_pool=[{
                    "canonical_latin_name": latin,
                    "name_i18n": self._lt(zh_cn, latin),
                    "taxonomic_rank": taxonomic_rank,
                    "category": category,
                    "model_score": 0.8 - rank * 0.1,
                    "rationale_i18n": self._lt(
                        "虚构专科候选", "Synthetic specialist candidate"
                    ),
                    "counterevidence_i18n": self._lt(
                        "尚无确证", "Confirmation is pending"
                    ),
                    "source_fragment_ids": [fragment_id],
                } for rank, (latin, zh_cn, taxonomic_rank, category) in enumerate(
                    pathogen_specs, start=1
                )],
            )
            return result, self._meta(role)

        async def invoke_development_synthesis(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self._record(api_key, request)
            self.synthesis_calls += 1
            fragment_id = request.source_fragments[0].source_fragment_id
            pathogen_specs = [
                ("Streptococcus pneumoniae", "肺炎链球菌", "species", "bacteria"),
                ("Legionella pneumophila", "嗜肺军团菌", "species", "bacteria"),
                ("Mycoplasma pneumoniae", "肺炎支原体", "species", "bacteria"),
                ("Klebsiella pneumoniae", "肺炎克雷伯菌", "species", "bacteria"),
                ("Influenza A virus", "甲型流感病毒", "virus_type", "virus"),
            ]
            candidates = []
            for rank, (latin, zh_cn, taxonomic_rank, category) in enumerate(pathogen_specs, start=1):
                candidates.append({
                    "rank": rank,
                    "canonical_latin_name": latin,
                    "name_i18n": self._lt(zh_cn, latin),
                    "taxonomic_rank": taxonomic_rank,
                    "category": category,
                    "ncbi_taxonomy_id": None,
                    "taxonomy_resolution_status": "not_checked",
                    "model_score": 0.8 - rank * 0.1,
                    "supporting_evidence": [{
                        "statement_i18n": self._lt("虚构支持证据", "Synthetic supporting evidence"),
                        "source_fragment_ids": [fragment_id],
                        "evidence_source_ids": [],
                    }],
                    "opposing_evidence": [],
                    "why_ranked_i18n": self._lt("综合征相符", "Syndrome-compatible"),
                    "main_uncertainty_i18n": self._lt("尚无确证", "Confirmation is pending"),
                    "proposed_by_agent_roles": ["timeline_host"],
                })
            result = DevelopmentSynthesisDraft.model_validate({
                "summary_i18n": self._lt("开发总诊", "Development synthesis"),
                "concrete_pathogens": candidates,
                "category_overview": [],
                "unknown_score": 0.1,
                "coinfection_hypotheses": [],
                "next_tests": [],
                "warnings": [],
            })
            return result, self._meta("synthesis")

        async def invoke_development_critic(
            self, provider: dict[str, Any], api_key: Optional[str], request: Any,
        ) -> Any:
            self._record(api_key, request)
            self.critic_calls += 1
            result = DevelopmentCriticResult(
                accepted=True,
                revision_required=False,
                review_summary_i18n=self._lt("审稿通过", "Critic accepted"),
            )
            return result, self._meta("critic")

    return DevelopmentProviderDouble
