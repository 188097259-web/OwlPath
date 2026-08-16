import asyncio
import hashlib
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from .baseline import predict_baseline
from .db import Database, canonical_json_dumps, json_dumps, json_loads, redact_secrets, sha256_json
from .errors import ProviderInvocationError
from .models import (
    AggregatedResult,
    CoinfectionPair,
    DataBoundary,
    DevelopmentAgentObservationSummary,
    DevelopmentAgentRole,
    DevelopmentConcretePathogen,
    DevelopmentContractIssue,
    DevelopmentCriticIssue,
    DevelopmentCriticRequest,
    DevelopmentCriticResult,
    DevelopmentDraftPathogen,
    DevelopmentDemoProjection,
    DevelopmentEvidenceLink,
    DevelopmentEvidenceSource,
    DevelopmentPathogenProposal,
    DevelopmentResultV3,
    DevelopmentReviewSummary,
    DevelopmentRevisionContext,
    DevelopmentSourceFragment,
    DevelopmentSpecialistRequest,
    DevelopmentSpecialistResult,
    DevelopmentSpecialistRole,
    DevelopmentSynthesisDraft,
    DevelopmentSynthesisRequest,
    DevelopmentTaxonomicRank,
    DevelopmentTaxonomyResolutionStatus,
    DevelopmentTop5Validation,
    GovernanceConfig,
    LocalizedText,
    ModelContribution,
    ModelPrediction,
    NextTestSuggestion,
    PathogenCandidate,
    RankLevel,
    SafetyAction,
    validate_development_top5,
    new_id,
    utc_now,
)
from .medical_retrieval import (
    FederatedMedicalEvidenceRetriever,
    MedicalEvidenceRetriever,
    TaxonomyResolver,
    build_candidate_retrieval_queries,
    build_federated_query_plan,
    build_retrieval_queries,
    map_candidate_specific_citations,
    retrieve_candidate_evidence,
)
from .providers import (
    DEFAULT_DEVELOPMENT_PROVIDER_CONCURRENCY_LIMIT,
    ProviderClient,
    provider_request_url,
)
from .security import SecretStore, SecretStoreError


BASELINE_ID = "baseline"
BASELINE_NAME = "Transparent rule baseline (unvalidated)"
DEVELOPMENT_DEMO_BYPASSED_CONTROLS = [
    "governance_run_enabled",
    "clinical_review",
    "external_transfer_consent",
    "live_decision_time_window",
    "input_safety_gate",
    "applicability_invocation_gate",
]
EXECUTION_GRAPH_VERSION = "owlpath.execution-graph.v1"
TRACE_VERSION = "owlpath.trace.v1"
TRACE_ARTIFACT_SCHEMA_VERSION = "owlpath.trace-artifact.v1"
DEVELOPMENT_EXECUTION_GRAPH_VERSION = "owlpath.execution-graph.v4"
DEVELOPMENT_TRACE_VERSION = "owlpath.trace.v2"
DEVELOPMENT_RESULT_SCHEMA_VERSION = "owlpath.result.v3"
DEVELOPMENT_MAX_PROVIDER_REQUESTS = 18
# Specialists run first.  Keep six real-request slots for synthesis, critic,
# and a possible single revision (including bounded failover) so a burst of
# specialist transport retries cannot starve the decision stages.
DEVELOPMENT_SPECIALIST_PROVIDER_REQUEST_CEILING = 12
DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS = 6
DEVELOPMENT_HARD_TIMEOUT_SECONDS = 420.0
# The critic is advisory once the deterministic Top-5 contract has passed.
# Keep its wall-clock budget materially below the transport-level Provider
# timeout so a slow review cannot consume the run-wide seven-minute limit.
DEVELOPMENT_CRITIC_ROLE_TIMEOUT_SECONDS = 45.0
# A revision may replace a draft only after passing the same deterministic
# contract.  Bound the whole role (including failover), leaving the committed
# valid draft available if the revision times out or otherwise fails.
DEVELOPMENT_REVISION_ROLE_TIMEOUT_SECONDS = 75.0
# Preserve time for candidate evidence bookkeeping, deterministic result
# compilation, hashing, and persistence after optional LLM work.
DEVELOPMENT_FINALIZATION_RESERVE_SECONDS = 15.0
DEVELOPMENT_EVIDENCE_ENRICHMENT_TIMEOUT_SECONDS = 12.0


def _development_stage_timeout(
    preferred_seconds: float,
    hard_deadline_monotonic: Optional[float],
) -> float:
    """Return a stage budget that cannot consume the finalization reserve."""

    preferred = max(0.0, float(preferred_seconds))
    if hard_deadline_monotonic is None:
        return preferred
    available = (
        float(hard_deadline_monotonic)
        - time.monotonic()
        - DEVELOPMENT_FINALIZATION_RESERVE_SECONDS
    )
    return max(0.0, min(preferred, available))


class DevelopmentProviderCallBudget:
    """Run-scoped hard cap for real external model requests.

    The selected specialist Agents start concurrently, so the counter must be
    claimed atomically. Logical Agent nodes may each permit one failover, but a
    failover never grants permission to exceed the run-wide network budget.
    """

    def __init__(self, maximum: int = DEVELOPMENT_MAX_PROVIDER_REQUESTS) -> None:
        self.maximum = max(1, int(maximum))
        self.used = 0
        self._occupied_request_numbers: Set[int] = set()
        self._lock = asyncio.Lock()

    async def claim(self, *, maximum_used: Optional[int] = None) -> Optional[int]:
        async with self._lock:
            effective_maximum = self.maximum
            if maximum_used is not None:
                effective_maximum = max(1, min(int(maximum_used), self.maximum))
            if self.used >= effective_maximum:
                return None
            request_number = next(
                number
                for number in range(1, self.maximum + 1)
                if number not in self._occupied_request_numbers
            )
            self._occupied_request_numbers.add(request_number)
            self.used = len(self._occupied_request_numbers)
            return request_number

    async def refund_before_http(self, request_number: int) -> bool:
        """Return a reservation only when egress provably never started."""

        async with self._lock:
            if request_number not in self._occupied_request_numbers:
                return False
            self._occupied_request_numbers.remove(request_number)
            self.used = len(self._occupied_request_numbers)
            return True


def _development_same_provider_retry_delay(
    run_id: str,
    node_key: str,
    error_code: str,
) -> float:
    """Return bounded deterministic jitter without global random state."""

    digest = hashlib.sha256((run_id + "\0" + node_key).encode("utf-8")).digest()
    if error_code.startswith("provider_http_"):
        # A provider that explicitly returned 429/5xx gets a longer pause than
        # a transient socket failure.  It remains far below the three-second
        # ceiling and the logical-role/run-wide deadlines stay authoritative.
        return 0.5 + (digest[0] / 255.0) * 0.5
    return 0.04 + (digest[0] / 255.0) * 0.04


def _development_same_provider_retryable_code(error_code: str) -> bool:
    if error_code in {"provider_timeout", "provider_network_error"}:
        return True
    matched = re.fullmatch(r"provider_http_(\d{3})", error_code)
    if matched is None:
        return False
    status_code = int(matched.group(1))
    return status_code == 429 or 500 <= status_code <= 599


def _development_attempt_warning(attempts: Sequence[Dict[str, Any]]) -> List[str]:
    completed_or_failed = [
        item for item in attempts if item.get("status") in {"failed", "completed"}
    ]
    if len(completed_or_failed) <= 1:
        return []
    provider_ids = {str(item.get("provider_id")) for item in completed_or_failed}
    if len(provider_ids) > 1:
        return ["provider_failover_used"]
    if any(
        str(item.get("error_code") or "").startswith("provider_http_")
        for item in completed_or_failed
    ):
        return ["provider_http_retry_used"]
    return ["provider_transport_retry_used"]


DEVELOPMENT_CORE_SPECIALIST_ROLES: Sequence[Tuple[str, str, str]] = (
    ("infectious_diseases", "感染科核心 Agent", "Infectious Diseases Core Agent"),
    ("critical_care_emergency", "急诊与重症核心 Agent", "Emergency and Critical Care Core Agent"),
    ("clinical_epidemiology", "临床流行病学核心 Agent", "Clinical Epidemiology Core Agent"),
    ("laboratory_medicine", "检验医学核心 Agent", "Laboratory Medicine Core Agent"),
    (
        "clinical_microbiology_culture",
        "细菌培养与临床微生物核心 Agent",
        "Culture and Clinical Microbiology Core Agent",
    ),
)

DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES: Sequence[Tuple[str, str, str]] = (
    ("radiology", "影像诊断专科 Agent", "Radiology Specialist Agent"),
    ("pulmonology", "呼吸专科 Agent", "Pulmonology Specialist Agent"),
    ("gastroenterology", "消化专科 Agent", "Gastroenterology Specialist Agent"),
    ("hepatobiliary_pancreatic", "肝胆胰专科 Agent", "Hepatobiliary and Pancreatic Specialist Agent"),
    ("urology", "泌尿外科专科 Agent", "Urology Specialist Agent"),
    ("nephrology", "肾脏专科 Agent", "Nephrology Specialist Agent"),
    ("neurology_neuroinfection", "神经与神经感染专科 Agent", "Neurology and Neuroinfection Specialist Agent"),
    ("cardiology_endocarditis", "心血管与心内膜炎专科 Agent", "Cardiology and Endocarditis Specialist Agent"),
    ("hematology_immunology", "血液与免疫专科 Agent", "Hematology and Immunology Specialist Agent"),
    ("transplant_infectious_diseases", "移植感染专科 Agent", "Transplant Infectious Diseases Specialist Agent"),
    ("surgery_source_control", "外科感染源控制专科 Agent", "Surgical Source-Control Specialist Agent"),
    ("orthopedics_bone_joint", "骨与关节感染专科 Agent", "Orthopedics and Bone-Joint Infection Specialist Agent"),
    ("dermatology_soft_tissue", "皮肤与软组织感染专科 Agent", "Dermatology and Soft-Tissue Infection Specialist Agent"),
    ("obstetrics_gynecology", "妇产科感染专科 Agent", "Obstetrics and Gynecology Specialist Agent"),
    ("pediatrics_neonatology", "儿科与新生儿感染专科 Agent", "Pediatrics and Neonatology Specialist Agent"),
    ("tropical_medicine_parasitology", "热带医学与寄生虫专科 Agent", "Tropical Medicine and Parasitology Specialist Agent"),
    ("medical_mycology", "医学真菌学专科 Agent", "Medical Mycology Specialist Agent"),
    ("clinical_virology_molecular", "临床病毒与分子诊断专科 Agent", "Clinical Virology and Molecular Diagnostics Specialist Agent"),
    ("antimicrobial_stewardship", "抗微生物药物管理专科 Agent", "Antimicrobial Stewardship Specialist Agent"),
    ("healthcare_device_infection", "医疗相关与器械感染专科 Agent", "Healthcare and Device Infection Specialist Agent"),
)

# Agent names are not evidence domains.  In particular, the dynamic challenge
# roles deliberately overlap a core role and must not create a second vote for
# the same case fact.  This frozen mapping is therefore used by both the
# evidence board and the deterministic Agent-pool fallback.  Deprecated v1
# wire roles remain mapped so old persisted outputs can still be audited with
# the same rule.
DEVELOPMENT_ROLE_EVIDENCE_DOMAINS: Dict[DevelopmentSpecialistRole, str] = {
    # v3 core consultation team.
    DevelopmentSpecialistRole.INFECTIOUS_DISEASES: "infection_syndrome",
    DevelopmentSpecialistRole.CRITICAL_CARE_EMERGENCY: "acute_severity",
    DevelopmentSpecialistRole.CLINICAL_EPIDEMIOLOGY: "exposure_epidemiology",
    DevelopmentSpecialistRole.LABORATORY_MEDICINE: "laboratory_phenotype",
    DevelopmentSpecialistRole.CLINICAL_MICROBIOLOGY_CULTURE: "microbiology_diagnostics",
    # v3 dynamic specialties.  Related departments deliberately share frozen
    # evidence domains so repeating the same fact cannot manufacture votes.
    DevelopmentSpecialistRole.RADIOLOGY: "imaging_anatomy",
    DevelopmentSpecialistRole.PULMONOLOGY: "respiratory_system",
    DevelopmentSpecialistRole.GASTROENTEROLOGY: "gastrointestinal_system",
    DevelopmentSpecialistRole.HEPATOBILIARY_PANCREATIC: "hepatobiliary_pancreatic_system",
    DevelopmentSpecialistRole.UROLOGY: "urinary_tract",
    DevelopmentSpecialistRole.NEPHROLOGY: "renal_system",
    DevelopmentSpecialistRole.NEUROLOGY_NEUROINFECTION: "neurologic_system",
    DevelopmentSpecialistRole.CARDIOLOGY_ENDOCARDITIS: "cardiovascular_endovascular_system",
    DevelopmentSpecialistRole.HEMATOLOGY_IMMUNOLOGY: "host_immunity",
    DevelopmentSpecialistRole.TRANSPLANT_INFECTIOUS_DISEASES: "host_immunity",
    DevelopmentSpecialistRole.SURGERY_SOURCE_CONTROL: "surgical_source_control",
    DevelopmentSpecialistRole.ORTHOPEDICS_BONE_JOINT: "bone_joint",
    DevelopmentSpecialistRole.DERMATOLOGY_SOFT_TISSUE: "skin_soft_tissue",
    DevelopmentSpecialistRole.OBSTETRICS_GYNECOLOGY: "reproductive_perinatal",
    DevelopmentSpecialistRole.PEDIATRICS_NEONATOLOGY: "age_specific_host",
    DevelopmentSpecialistRole.TROPICAL_MEDICINE_PARASITOLOGY: "exposure_epidemiology",
    DevelopmentSpecialistRole.MEDICAL_MYCOLOGY: "microbiology_diagnostics",
    DevelopmentSpecialistRole.CLINICAL_VIROLOGY_MOLECULAR: "microbiology_diagnostics",
    DevelopmentSpecialistRole.ANTIMICROBIAL_STEWARDSHIP: "antimicrobial_exposure_resistance",
    DevelopmentSpecialistRole.HEALTHCARE_DEVICE_INFECTION: "healthcare_device_acquisition",
    # Legacy v2 roles.
    DevelopmentSpecialistRole.TIMELINE_COURSE: "clinical_course",
    DevelopmentSpecialistRole.HOST_SUSCEPTIBILITY: "host_susceptibility",
    DevelopmentSpecialistRole.SYNDROME_LOCALIZATION: "syndrome_localization",
    DevelopmentSpecialistRole.EXPOSURE_ONE_HEALTH: "exposure_epidemiology",
    DevelopmentSpecialistRole.LAB_PATHOPHYSIOLOGY: "laboratory_pathophysiology",
    DevelopmentSpecialistRole.ORGAN_SEVERITY: "organ_injury_severity",
    DevelopmentSpecialistRole.IMAGING_DISSEMINATION: "imaging_dissemination",
    DevelopmentSpecialistRole.MICROBIOLOGY_TREATMENT: "microbiology_treatment",
    # Challenge roles reuse the core domain whose claims they stress-test.
    DevelopmentSpecialistRole.NEUROINFECTION: "neurologic_system",
    DevelopmentSpecialistRole.IMMUNOCOMPROMISED_OPPORTUNISTIC: "host_immunity",
    DevelopmentSpecialistRole.TRAVEL_ZOONOTIC: "exposure_epidemiology",
    DevelopmentSpecialistRole.HEALTHCARE_DEVICE_AMR: "healthcare_device_acquisition",
    # Deprecated v1 compatibility roles.
    DevelopmentSpecialistRole.TIMELINE_HOST: "clinical_course",
    DevelopmentSpecialistRole.SYNDROME_SITE: "syndrome_localization",
    DevelopmentSpecialistRole.EXPOSURE_EPIDEMIOLOGY: "exposure_epidemiology",
    DevelopmentSpecialistRole.LABORATORY_ORGAN_INJURY: "laboratory_pathophysiology",
    DevelopmentSpecialistRole.IMAGING_MICROBIOLOGY_TREATMENT: "imaging_dissemination",
}

# Public compatibility name used by deterministic provenance/fallback code.
DEVELOPMENT_SPECIALIST_ROLES: Sequence[Tuple[str, str, str]] = (
    *DEVELOPMENT_CORE_SPECIALIST_ROLES,
    *DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES,
)


def _development_provenance_role_order() -> List[DevelopmentSpecialistRole]:
    """Return active v3 roles first while preserving old run provenance."""

    preferred = [
        DevelopmentSpecialistRole(role)
        for role, _zh, _en in DEVELOPMENT_SPECIALIST_ROLES
    ]
    return [*preferred, *[role for role in DevelopmentSpecialistRole if role not in preferred]]


def _development_role_evidence_domain(role: DevelopmentSpecialistRole) -> str:
    """Return a stable evidence-domain label for a versioned Agent role."""

    return DEVELOPMENT_ROLE_EVIDENCE_DOMAINS.get(role, "legacy_role:%s" % role.value)


def _independent_domain_fragment_support(
    domain_fragments: Dict[str, Set[str]],
) -> Set[str]:
    """Count independent domain support without reusing one case fragment.

    This is a small deterministic maximum bipartite matching.  A domain can
    contribute at most once and a source fragment can contribute at most once.
    Consequently two Agents -- or even two differently named domains -- that
    cite only the same fact cannot manufacture two independent evidence units.
    """

    fragment_owner: Dict[str, str] = {}

    def assign(domain: str, visited: Set[str]) -> bool:
        for fragment_id in sorted(domain_fragments.get(domain) or set()):
            if fragment_id in visited:
                continue
            visited.add(fragment_id)
            owner = fragment_owner.get(fragment_id)
            if owner is None or assign(owner, visited):
                fragment_owner[fragment_id] = domain
                return True
        return False

    for domain in sorted(domain_fragments):
        assign(domain, set())
    return set(fragment_owner.values())


def _candidate_independent_support_metrics(
    entries: Sequence[Tuple[DevelopmentSpecialistRole, DevelopmentPathogenProposal]],
) -> Dict[str, Any]:
    """Aggregate one candidate by frozen domains and unique source facts.

    Scores are sampled once per frozen domain.  When a core and specialty
    Agent repeat the same claim, the frozen v3 role order chooses one domain
    representative, so adding the duplicate cannot move the mean.
    All reporting roles are still retained separately for provenance.
    """

    role_priority = {
        role: index for index, role in enumerate(_development_provenance_role_order())
    }
    domain_fragments: Dict[str, Set[str]] = defaultdict(set)
    domain_representatives: Dict[str, Tuple[int, float]] = {}
    all_fragments: Set[str] = set()
    for role, proposal in entries:
        fragment_ids = tuple(sorted(set(proposal.source_fragment_ids)))
        if not fragment_ids:
            continue
        domain = _development_role_evidence_domain(role)
        domain_fragments[domain].update(fragment_ids)
        all_fragments.update(fragment_ids)
        representative = (role_priority.get(role, 999), float(proposal.model_score))
        current = domain_representatives.get(domain)
        if current is None or representative[0] < current[0]:
            domain_representatives[domain] = representative

    matched_domains = _independent_domain_fragment_support(domain_fragments)
    domain_scores = [
        domain_representatives[domain][1]
        for domain in sorted(matched_domains)
        if domain in domain_representatives
    ]
    return {
        "independent_evidence_domain_count": len(matched_domains),
        "unique_evidence_domain_count": len(domain_fragments),
        "unique_source_fragment_count": len(all_fragments),
        "independent_score_claim_count": len(domain_scores),
        "mean_model_score": (
            sum(domain_scores) / len(domain_scores) if domain_scores else 0.0
        ),
    }


_DYNAMIC_SPECIALIST_CUES: Dict[str, Sequence[str]] = {
    "radiology": (
        "ct", "mri", "胸片", "x线", "影像", "超声", "实变", "浸润", "炎性灶",
        "低强化", "脓肿", "积液", "x-ray", "radiograph", "ultrasound", "consolidation",
        "infiltrate", "abscess", "effusion",
    ),
    "pulmonology": (
        "咳嗽", "咳痰", "呼吸困难", "气促", "低氧", "血氧", "肺炎", "肺部",
        "胸腔积液", "气管镜", "呼吸机", "cough", "sputum", "dyspnea", "hypox",
        "pneumonia", "pulmonary", "pleural", "bronchoscopy", "ventilat",
    ),
    "gastroenterology": (
        "腹痛", "腹泻", "呕吐", "胃肠", "小肠", "结肠", "肠梗阻", "便血",
        "abdominal pain", "diarrhea", "vomit", "gastrointestinal", "bowel", "enterocolitis",
    ),
    "hepatobiliary_pancreatic": (
        "肝脏", "肝脓肿", "肝功能", "胆道", "胆管", "胆囊", "胰腺", "胆红素",
        "alt", "ast", "hepat", "liver abscess", "biliary", "cholang", "pancrea",
    ),
    "urology": (
        "尿频", "尿急", "尿痛", "排尿困难", "肾盂肾炎", "尿路感染", "尿培养",
        "输尿管", "膀胱", "dysuria", "urinary tract", "pyeloneph", "urine culture",
    ),
    "nephrology": (
        "急性肾损伤", "肾功能", "肌酐", "尿蛋白", "尿潜血", "血尿", "透析", "cr", "bun",
        "creatinine", "acute kidney", "renal failure", "proteinuria", "hematuria", "dialysis",
    ),
    "neurology_neuroinfection": (
        "脑脊液", "意识不清", "意识障碍", "头痛", "颈抵抗", "抽搐", "脑炎", "脑膜炎",
        "头颅ct", "cerebrospinal", "csf", "encephal", "mening", "seizure", "altered mental",
    ),
    "cardiology_endocarditis": (
        "心内膜炎", "心脏杂音", "超声心动图", "赘生物", "起搏器", "心脏植入",
        "肌钙蛋白", "心律失常", "endocarditis", "murmur", "echocardi", "vegetation",
        "cardiac device", "troponin", "arrhythmia",
    ),
    "hematology_immunology": (
        "中性粒细胞缺乏", "粒细胞缺乏", "白血病", "淋巴瘤", "血小板减少",
        "免疫缺陷", "艾滋", "hiv", "neutrop", "leukemia", "lymphoma", "thrombocytopen",
        "immunodefic",
    ),
    "transplant_infectious_diseases": (
        "移植", "抗排异", "免疫抑制剂", "他克莫司", "环孢素", "transplant",
        "anti-rejection", "tacrolimus", "cyclosporine",
    ),
    "surgery_source_control": (
        "术后", "手术后", "切口", "引流", "穿孔", "腹膜炎", "脓肿", "积液",
        "postoperative", "surgical site", "incision", "drain", "perforation", "peritonitis", "abscess",
    ),
    "orthopedics_bone_joint": (
        "骨髓炎", "化脓性关节炎", "关节肿痛", "人工关节", "骨科植入物",
        "osteomyelitis", "septic arthritis", "joint swelling", "prosthetic joint", "orthopedic implant",
    ),
    "dermatology_soft_tissue": (
        "皮疹", "皮损", "伤口", "蜂窝织炎", "坏死性筋膜炎", "软组织", "红肿",
        "rash", "skin lesion", "wound", "cellulitis", "necrotizing fasciitis", "soft tissue",
    ),
    "obstetrics_gynecology": (
        "妊娠", "孕妇", "产后", "产褥", "羊水", "宫内", "盆腔炎", "阴道分泌物",
        "pregnan", "postpartum", "puerper", "amniotic", "intrauterine", "pelvic inflammatory",
    ),
    "pediatrics_neonatology": (
        "新生儿", "婴儿", "患儿", "儿童", "早产儿", "脐带", "neonate", "newborn", "infant",
        "pediatric", "child", "premature",
    ),
    "tropical_medicine_parasitology": (
        "境外", "旅行", "热带", "疫区", "蚊", "蜱", "寄生虫", "抓鱼", "淡水", "海水",
        "travel", "tropical", "endemic", "mosquito", "tick", "parasite", "fish exposure", "freshwater",
    ),
    "medical_mycology": (
        "真菌", "霉菌", "酵母菌", "隐球菌", "曲霉", "念珠菌", "抗真菌", "真菌培养",
        "fungal", "mold", "yeast", "cryptococc", "aspergill", "candida", "antifungal",
    ),
    "clinical_virology_molecular": (
        "病毒", "核酸", "pcr", "naat", "抗原", "新型冠状病毒", "流感病毒",
        "疱疹病毒", "viral", "nucleic acid", "antigen", "sars-cov", "influenza", "herpes",
    ),
    "antimicrobial_stewardship": (
        "抗生素", "抗菌药", "广谱抗生素", "美罗培南", "万古霉素", "利奈唑胺",
        "耐药", "药敏", "antibiotic", "antimicrobial", "meropenem", "vancomycin", "resistan",
        "susceptibility",
    ),
    "healthcare_device_infection": (
        "近期住院", "导管相关", "长期导管", "长期置管", "中心静脉导管", "植入物",
        "人工关节", "起搏器", "呼吸机相关", "recent hospital", "catheter-associated",
        "long-term catheter", "central venous catheter", "implant", "prosthetic", "ventilator-associated",
        "healthcare-associated",
    ),
}


def _development_router_cue_is_negated(text: str, cue_start: int) -> bool:
    """Ignore locally negated history without interpreting the whole note.

    This is deliberately a narrow routing aid, not a clinical negation model.
    It prevents phrases such as ``无近期住院`` or ``no recent travel`` from
    recruiting a challenge Agent, while leaving contradictory positive facts
    elsewhere (for example an earlier fish/water exposure) available to route.
    """

    prefix = text[max(0, cue_start - 32):cue_start]
    prefix = re.split(r"[。！？；，;,.!?\n]", prefix)[-1]
    if re.search(r"(?:无|未见|未曾|未有|没有|否认)[^。！？；，;,.!?\n]{0,16}$", prefix):
        return True
    return bool(re.search(
        r"\b(?:no|not|without|denies?|negative\s+for)\b(?:\s+[a-z0-9_-]+){0,4}\s*$",
        prefix,
        flags=re.IGNORECASE,
    ))


def select_dynamic_development_roles(
    source_text: str,
    *,
    maximum: int = DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS,
) -> List[str]:
    """Select a bounded specialty panel with a frozen, auditable rule.

    This router does not diagnose.  It only notices broad cues that justify an
    additional independent perspective.  The source compiler has already
    preserved fragment boundaries; this cue scan never creates clinical facts
    and locally negated cues do not recruit a role. Ties follow the versioned
    role order so manifest construction and runtime selection are deterministic.
    """

    normalized = str(source_text or "").casefold()
    scored: List[Tuple[int, int, str]] = []
    role_order = {
        role: index for index, (role, _zh, _en) in enumerate(DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES)
    }
    for role, cues in _DYNAMIC_SPECIALIST_CUES.items():
        matched_cues: Set[str] = set()
        for cue in cues:
            normalized_cue = cue.casefold()
            cue_pattern = re.escape(normalized_cue)
            if normalized_cue in {
                "ct", "mri", "csf", "cr", "bun", "alt", "ast", "hiv", "pcr", "naat",
            }:
                cue_pattern = r"(?<![a-z0-9])%s(?![a-z0-9])" % cue_pattern
            for match in re.finditer(cue_pattern, normalized):
                if not _development_router_cue_is_negated(normalized, match.start()):
                    matched_cues.add(normalized_cue)
                    break
        score = len(matched_cues)
        if score:
            scored.append((-score, role_order.get(role, 999), role))
    scored.sort()
    return [role for _score, _order, role in scored[: max(0, int(maximum))]]


def build_development_evidence_board(
    specialist_results: Sequence[DevelopmentSpecialistResult],
    *,
    valid_fragment_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Deduplicate cross-Agent facts before synthesis and fallback ranking.

    Repeating the same fragment/candidate from several roles is useful for
    audit, but it is not independent evidence.  The board therefore preserves
    every proposing role while counting frozen evidence domains and unique
    source facts separately.  When the run's frozen source-fragment manifest
    is supplied, retrieval concepts and candidate evidence are allowed through
    only when every cited fragment belongs to that manifest.
    """

    observations: Dict[str, Dict[str, Any]] = {}
    candidates: Dict[str, Dict[str, Any]] = {}
    concepts: Dict[str, Dict[str, Any]] = {}
    retrieval_concept_audit = {
        "input_count": 0,
        "accepted_unique_count": 0,
        "discarded_count": 0,
        "discarded_by_reason": {
            "negated": 0,
            "missing_source_fragment": 0,
            "unknown_source_fragment_reference": 0,
        },
    }
    candidate_hypothesis_audit = {
        "input_count": 0,
        "accepted_unique_count": 0,
        "discarded_count": 0,
        "discarded_by_reason": {
            "missing_source_fragment": 0,
            "unknown_source_fragment_reference": 0,
        },
    }
    for result in specialist_results:
        role = result.role.value
        for observation in result.observations:
            statement = observation.statement_i18n.en or observation.statement_i18n.zh_cn or ""
            normalized_statement = " ".join(
                re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", statement.casefold()).split()
            )
            fragment_ids = sorted(set(observation.source_fragment_ids))
            identity = sha256_json({
                "kind": observation.kind.value,
                "statement": normalized_statement,
                "fragment_ids": fragment_ids,
            })
            row = observations.setdefault(identity, {
                "kind": observation.kind.value,
                "statement_i18n": observation.statement_i18n.model_dump(mode="json"),
                "source_fragment_ids": fragment_ids,
                "importance": observation.importance,
                "reported_by_roles": [],
            })
            if role not in row["reported_by_roles"]:
                row["reported_by_roles"].append(role)
        for proposal in result.candidate_pool:
            candidate_hypothesis_audit["input_count"] += 1
            proposal_fragment_ids = sorted(set(proposal.source_fragment_ids))
            candidate_discard_reason: Optional[str] = None
            if not proposal_fragment_ids:
                candidate_discard_reason = "missing_source_fragment"
            elif (
                valid_fragment_ids is not None
                and bool(set(proposal_fragment_ids).difference(valid_fragment_ids))
            ):
                candidate_discard_reason = "unknown_source_fragment_reference"
            if candidate_discard_reason is not None:
                candidate_hypothesis_audit["discarded_count"] += 1
                candidate_hypothesis_audit["discarded_by_reason"][
                    candidate_discard_reason
                ] += 1
                continue
            normalized_name = " ".join(proposal.canonical_latin_name.casefold().split())
            row = candidates.setdefault(normalized_name, {
                "canonical_latin_name": proposal.canonical_latin_name,
                "taxonomic_rank": proposal.taxonomic_rank.value,
                "category": proposal.category.value,
                "proposing_roles": [],
                "source_fragment_ids": [],
                "model_scores": [],
                "support_entries": [],
                "rationales_i18n": [],
            })
            if role not in row["proposing_roles"]:
                row["proposing_roles"].append(role)
            row["source_fragment_ids"] = sorted(set(
                [*row["source_fragment_ids"], *proposal_fragment_ids]
            ))
            row["model_scores"].append(proposal.model_score)
            row["support_entries"].append((result.role, proposal))
            rationale = proposal.rationale_i18n.model_dump(mode="json")
            if rationale not in row["rationales_i18n"]:
                row["rationales_i18n"].append(rationale)
        for concept in getattr(result, "retrieval_concepts", []) or []:
            rendered = concept.model_dump(mode="json")
            retrieval_concept_audit["input_count"] += 1
            concept_fragment_ids = sorted(set(
                rendered.get("source_fragment_ids") or []
            ))
            discard_reason: Optional[str] = None
            if bool(rendered.get("negated")):
                discard_reason = "negated"
            elif not concept_fragment_ids:
                discard_reason = "missing_source_fragment"
            elif (
                valid_fragment_ids is not None
                and bool(set(concept_fragment_ids).difference(valid_fragment_ids))
            ):
                discard_reason = "unknown_source_fragment_reference"
            if discard_reason is not None:
                retrieval_concept_audit["discarded_count"] += 1
                retrieval_concept_audit["discarded_by_reason"][discard_reason] += 1
                continue
            key = "%s\0%s\0%s" % (
                rendered.get("kind"),
                str(rendered.get("term_en") or "").casefold(),
                bool(rendered.get("negated")),
            )
            row = concepts.setdefault(key, {
                **rendered,
                "source_fragment_ids": concept_fragment_ids,
                "reported_by_roles": [],
            })
            if role not in row["reported_by_roles"]:
                row["reported_by_roles"].append(role)

    rendered_candidates: List[Dict[str, Any]] = []
    for row in candidates.values():
        row.pop("model_scores")
        entries = row.pop("support_entries")
        row.update(_candidate_independent_support_metrics(entries))
        rendered_candidates.append(row)
    rendered_candidates.sort(
        key=lambda item: (
            -int(item["independent_evidence_domain_count"]),
            -float(item["mean_model_score"]),
            str(item["canonical_latin_name"]).casefold(),
        )
    )
    retrieval_concept_audit["accepted_unique_count"] = len(concepts)
    candidate_hypothesis_audit["accepted_unique_count"] = len(rendered_candidates)
    return {
        "schema_version": "owlpath.evidence-board.v2",
        "specialist_result_count": len(specialist_results),
        "unique_observations": list(observations.values()),
        "retrieval_concepts": list(concepts.values()),
        "retrieval_concept_audit": retrieval_concept_audit,
        "candidate_hypotheses": rendered_candidates,
        "candidate_hypothesis_audit": candidate_hypothesis_audit,
        "counting_rule": (
            "maximum_unique_frozen_domain_to_source_fragment_matching;"
            "duplicate_agent_claims_do_not_add_votes_or_score_samples"
        ),
    }


def build_development_execution_manifest(
    provider_ids: Sequence[str],
    *,
    source_text: str = "",
    specialist_config_version: str = "owlpath.development-agents.v3",
) -> Dict[str, Any]:
    """Freeze an adaptive, heterogeneous development graph for one run."""

    ordered = list(dict.fromkeys(str(item) for item in provider_ids if str(item)))
    selected_dynamic = select_dynamic_development_roles(source_text)
    selected_roles = [
        *[item[0] for item in DEVELOPMENT_CORE_SPECIALIST_ROLES],
        *selected_dynamic,
    ]
    role_metadata = {
        role: (zh, en)
        for role, zh, en in DEVELOPMENT_SPECIALIST_ROLES
    }
    nodes: List[Dict[str, Any]] = [
        {"key": "snapshot", "kind": "deterministic_processor", "role": "snapshot_compiler", "version": "owlpath.snapshot.v2", "fan_out": False},
        {"key": "preflight", "kind": "policy_guard", "role": "technical_integrity_preflight", "version": "owlpath.preflight.v2", "fan_out": False},
        {"key": "applicability", "kind": "observation_guard", "role": "development_scope_observer", "version": "owlpath.scope-observer.v1", "fan_out": False},
        {"key": "input_quality", "kind": "observation_guard", "role": "development_quality_observer", "version": "owlpath.quality-observer.v1", "fan_out": False},
        {"key": "source_compiler", "kind": "deterministic_processor", "role": "source_fragment_compiler", "version": "owlpath.source-fragments.v1", "fan_out": True},
        {"key": "complexity_router", "kind": "deterministic_router", "role": "adaptive_specialist_router", "version": "owlpath.specialist-router.v2", "fan_out": True, "selected_dynamic_roles": selected_dynamic},
    ]
    edges: List[Dict[str, str]] = [
        {"from": "snapshot", "to": "preflight", "relation": "data"},
        {"from": "preflight", "to": "applicability", "relation": "observation"},
        {"from": "preflight", "to": "input_quality", "relation": "observation"},
        {"from": "preflight", "to": "source_compiler", "relation": "control"},
        {"from": "source_compiler", "to": "complexity_router", "relation": "routing_input"},
    ]
    specialist_keys: List[str] = []
    all_declared_roles = [item[0] for item in DEVELOPMENT_SPECIALIST_ROLES]
    selected_index = {role: index for index, role in enumerate(selected_roles)}
    for role in all_declared_roles:
        display_zh, display_en = role_metadata[role]
        key = "specialist:%s" % role
        selected = role in selected_roles
        if selected:
            specialist_keys.append(key)
        nodes.append({
            "key": key,
            "kind": "llm_agent",
            "role": role,
            "version": "owlpath.development-specialist.v3",
            "fan_out": True,
            "provider_id": (
                ordered[selected_index[role] % len(ordered)]
                if selected and ordered
                else None
            ),
            "dynamic": role in {item[0] for item in DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES},
            "selected": selected,
            "selection_state": "selected" if selected else "not_applicable",
            "evidence_domain": _development_role_evidence_domain(
                DevelopmentSpecialistRole(role)
            ),
            "display_name": {"zh_cn": display_zh, "en": display_en},
        })
        edges.append({
            "from": "complexity_router",
            "to": key,
            "relation": "selected_role" if selected else "not_selected",
        })
    nodes.extend([
        {"key": "evidence_board", "kind": "deterministic_processor", "role": "cross_domain_evidence_board", "version": "owlpath.evidence-board.v2", "fan_out": False},
        {"key": "retrieval_planner", "kind": "deterministic_processor", "role": "deidentified_medical_query_planner", "version": "owlpath.retrieval-plan.v2", "fan_out": True},
        {"key": "literature_retrieval", "kind": "tool_agent", "role": "literature_and_similar_case_retrieval", "version": "owlpath.medical-retrieval.v2", "fan_out": True, "tools": ["Europe PMC REST", "NCBI PubMed E-utilities"]},
        {"key": "public_health_retrieval", "kind": "tool_agent", "role": "guideline_and_public_health_retrieval", "version": "owlpath.medical-retrieval.v2", "fan_out": True, "tools": ["WHO Disease Outbreak News", "versioned authoritative source registry"]},
        {"key": "evidence_verifier", "kind": "deterministic_validator", "role": "retrieval_deduplication_and_evidence_verifier", "version": "owlpath.evidence-verifier.v1", "fan_out": False},
        {"key": "synthesis", "kind": "llm_agent", "role": "pathogen_chief_synthesis", "version": "owlpath.development-synthesis.v2", "fan_out": False, "provider_id": ordered[0] if ordered else None},
        {"key": "contract_validator", "kind": "deterministic_validator", "role": "taxonomy_and_top5_contract_validator", "version": "owlpath.development-contract.v1", "fan_out": False},
        {"key": "critic", "kind": "llm_agent", "role": "independent_medical_critic", "version": "owlpath.development-critic.v2", "fan_out": False, "provider_id": ordered[1] if len(ordered) > 1 else (ordered[0] if ordered else None), "independent_context": True},
        {"key": "revision", "kind": "llm_agent", "role": "single_pass_synthesis_revision", "version": "owlpath.development-synthesis.v2", "fan_out": False, "provider_id": ordered[0] if ordered else None, "max_attempts": 1},
        {"key": "candidate_evidence_enrichment", "kind": "tool_agent", "role": "candidate_specific_literature_enrichment", "version": "owlpath.candidate-evidence.v1", "fan_out": True, "tools": ["Europe PMC REST", "NCBI PubMed E-utilities"]},
        {"key": "result_compiler", "kind": "deterministic_processor", "role": "development_result_compiler", "version": DEVELOPMENT_RESULT_SCHEMA_VERSION, "fan_out": False},
        {"key": "persistence", "kind": "infrastructure", "role": "result_persistence", "version": DEVELOPMENT_TRACE_VERSION, "fan_out": False},
    ])
    for key in specialist_keys:
        edges.append({"from": key, "to": "evidence_board", "relation": "structured_opinion"})
    edges.extend([
        {"from": "evidence_board", "to": "retrieval_planner", "relation": "deidentified_concepts"},
        {"from": "retrieval_planner", "to": "literature_retrieval", "relation": "literature_queries"},
        {"from": "retrieval_planner", "to": "public_health_retrieval", "relation": "public_health_queries"},
        {"from": "literature_retrieval", "to": "evidence_verifier", "relation": "candidate_evidence"},
        {"from": "public_health_retrieval", "to": "evidence_verifier", "relation": "context_evidence"},
        {"from": "evidence_board", "to": "synthesis", "relation": "deduplicated_case_evidence"},
        {"from": "evidence_verifier", "to": "synthesis", "relation": "verified_evidence"},
        {"from": "synthesis", "to": "contract_validator", "relation": "data"},
        {"from": "contract_validator", "to": "critic", "relation": "data"},
        {"from": "evidence_verifier", "to": "critic", "relation": "evidence"},
        {"from": "critic", "to": "revision", "relation": "control"},
        {"from": "revision", "to": "candidate_evidence_enrichment", "relation": "data_or_skip"},
        {"from": "contract_validator", "to": "candidate_evidence_enrichment", "relation": "validated_data"},
        {"from": "candidate_evidence_enrichment", "to": "result_compiler", "relation": "evidence_enriched_data"},
        {"from": "critic", "to": "result_compiler", "relation": "review"},
        {"from": "result_compiler", "to": "persistence", "relation": "data"},
    ])
    return {
        "execution_graph_version": DEVELOPMENT_EXECUTION_GRAPH_VERSION,
        "trace_version": DEVELOPMENT_TRACE_VERSION,
        "run_mode": "development_demo",
        # Freeze the request label for replay/audit, while separately naming
        # the concrete prompt/contract implementation that actually ran.
        "specialist_config_version": str(
            specialist_config_version or "owlpath.development-agents.v3"
        ),
        "specialist_runtime_implementation_version": "owlpath.development-agents.v3",
        "selected_core_roles": [item[0] for item in DEVELOPMENT_CORE_SPECIALIST_ROLES],
        "selected_dynamic_roles": selected_dynamic,
        "nodes": nodes,
        "edges": edges,
        "limits": {
            "normal_llm_calls": len(selected_roles) + 2,
            "maximum_llm_calls_with_revision": len(selected_roles) + 3,
            "maximum_provider_network_requests_per_run": DEVELOPMENT_MAX_PROVIDER_REQUESTS,
            "specialist_provider_request_ceiling": DEVELOPMENT_SPECIALIST_PROVIDER_REQUEST_CEILING,
            "maximum_dynamic_specialists": DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS,
            "maximum_selected_specialists": (
                len(DEVELOPMENT_CORE_SPECIALIST_ROLES)
                + DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS
            ),
            "provider_failover_attempts_per_agent": 1,
            "provider_failover_subject_to_global_request_budget": True,
            "same_provider_retry_attempts_per_agent": 1,
            "maximum_concurrent_requests_per_provider": DEFAULT_DEVELOPMENT_PROVIDER_CONCURRENCY_LIMIT,
            "dns_preflight_before_provider_request_budget": True,
            "hard_timeout_seconds": int(DEVELOPMENT_HARD_TIMEOUT_SECONDS),
            "critic_role_timeout_seconds": int(DEVELOPMENT_CRITIC_ROLE_TIMEOUT_SECONDS),
            "revision_role_timeout_seconds": int(DEVELOPMENT_REVISION_ROLE_TIMEOUT_SECONDS),
            "finalization_reserve_seconds": int(DEVELOPMENT_FINALIZATION_RESERVE_SECONDS),
        },
        "notes": {
            "clinical_release_controls_enforced": False,
            "scope_quality_ood_and_calibration_are_observations": True,
            "technical_integrity_and_network_security_enforced": True,
            "raw_case_text_never_sent_to_literature_search": True,
            "public_health_no_hit_never_treated_as_absence": True,
            "agent_count_not_used_as_independent_evidence_count": True,
            "hidden_chain_of_thought_not_persisted": True,
        },
        "not_applicable_nodes": [
            "specialist:%s" % role
            for role, _zh, _en in DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES
            if role not in selected_dynamic
        ],
    }


def build_execution_manifest(
    provider_ids: Sequence[str],
    include_baseline: bool,
    development_demo: bool,
    development_source_text: str = "",
    development_specialist_config_version: str = "owlpath.development-agents.v3",
) -> Dict[str, Any]:
    """Build the frozen, declarative graph for one run.

    The manifest describes what is expected.  ``run_execution_nodes`` records
    what actually happened, including skipped and failed nodes.
    """
    if development_demo:
        return build_development_execution_manifest(
            provider_ids,
            source_text=development_source_text,
            specialist_config_version=development_specialist_config_version,
        )

    nodes: List[Dict[str, Any]] = [
        {"key": "snapshot", "kind": "deterministic_processor", "role": "snapshot_compiler", "version": "owlpath.snapshot.v1", "fan_out": False},
        {"key": "preflight", "kind": "policy_guard", "role": "integrity_preflight", "version": "owlpath.preflight.v1", "fan_out": False},
        {"key": "applicability", "kind": "policy_guard", "role": "applicability_guard", "version": "owlpath.scope.v1", "fan_out": False},
        {"key": "input_quality", "kind": "policy_guard", "role": "input_quality_guard", "version": "owlpath.input-quality.v1", "fan_out": False},
    ]
    edges: List[Dict[str, str]] = [
        {"from": "snapshot", "to": "preflight", "relation": "data"},
        {"from": "preflight", "to": "applicability", "relation": "control"},
        {"from": "preflight", "to": "input_quality", "relation": "control"},
    ]
    worker_keys: List[str] = []
    if include_baseline:
        nodes.extend([
            {"key": "baseline", "kind": "rule_model", "role": "engineering_baseline", "version": "owlpath-baseline-v1", "fan_out": False},
            {"key": "sanitizer:baseline", "kind": "sanitizer", "role": "normalized_output_sanitizer", "version": "owlpath.sanitizer.v1", "fan_out": False},
        ])
        edges.extend([
            {"from": "applicability", "to": "baseline", "relation": "control"},
            {"from": "input_quality", "to": "baseline", "relation": "control"},
            {"from": "baseline", "to": "sanitizer:baseline", "relation": "data"},
        ])
        worker_keys.append("sanitizer:baseline")
    for provider_id in provider_ids:
        provider_key = "provider:%s" % provider_id
        sanitizer_key = "sanitizer:%s" % provider_id
        nodes.extend([
            {"key": provider_key, "kind": "llm_agent", "role": "pathogen_hypothesis_agent", "version": "provider_model_frozen_in_run", "fan_out": True, "provider_id": provider_id},
            {"key": sanitizer_key, "kind": "sanitizer", "role": "normalized_output_sanitizer", "version": "owlpath.sanitizer.v1", "fan_out": True, "provider_id": provider_id},
        ])
        edges.extend([
            {"from": "applicability", "to": provider_key, "relation": "control"},
            {"from": "input_quality", "to": provider_key, "relation": "control"},
            {"from": provider_key, "to": sanitizer_key, "relation": "data"},
        ])
        worker_keys.append(sanitizer_key)
    nodes.extend([
        {"key": "aggregator", "kind": "aggregator", "role": "ensemble_aggregator", "version": "owlpath.aggregate.v1", "fan_out": False},
        {"key": "safety", "kind": "safety_adjudicator", "role": "release_safety_adjudicator", "version": "owlpath.safety.v1", "fan_out": False},
    ])
    nodes.extend([
        {"key": "bilingual_renderer", "kind": "renderer", "role": "bilingual_result_compiler", "version": "owlpath.result.v2", "fan_out": False},
        {"key": "persistence", "kind": "infrastructure", "role": "result_persistence", "version": TRACE_VERSION, "fan_out": False},
    ])
    for worker_key in worker_keys:
        edges.append({"from": worker_key, "to": "aggregator", "relation": "data"})
    if not worker_keys:
        edges.extend([
            {"from": "applicability", "to": "aggregator", "relation": "control"},
            {"from": "input_quality", "to": "aggregator", "relation": "control"},
        ])
    edges.append({"from": "aggregator", "to": "safety", "relation": "data"})
    edges.append({"from": "safety", "to": "bilingual_renderer", "relation": "data"})
    edges.append({"from": "bilingual_renderer", "to": "persistence", "relation": "data"})
    return {
        "execution_graph_version": EXECUTION_GRAPH_VERSION,
        "trace_version": TRACE_VERSION,
        "run_mode": "clinical_or_retrospective",
        "nodes": nodes,
        "edges": edges,
        "notes": {
            "aggregator_includes_safety_computation": False,
            "safety_node_runs_release_adjudication": True,
            "provider_artifacts_are_trace_safe_normalized_data_only": True,
            "synthetic_demo_source_may_use_demo_safe_visibility": False,
        },
        "not_applicable_nodes": ["demo_projection"],
    }


_TRACE_OMITTED_KEYS = {
    "api_key", "encrypted_api_key", "authorization", "extra_headers",
    "extra_headers_json", "raw_response", "raw_response_json", "prompt",
    "synthetic_source_text", "input_snapshot_json",
}


def trace_safe_payload(value: Any) -> Any:
    """Return data that is safe for the public trace endpoints.

    Trace writers also deliberately pass only normalized model data.  This
    recursive guard is a second boundary that strips common raw/credential
    containers and delegates header/key redaction to the database helper.
    """
    if isinstance(value, dict):
        safe: Dict[str, Any] = {}
        for key, item in value.items():
            rendered = str(key)
            if rendered.strip().lower() in _TRACE_OMITTED_KEYS:
                continue
            safe[rendered] = trace_safe_payload(item)
        return redact_secrets(safe)
    if isinstance(value, list):
        return [trace_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [trace_safe_payload(item) for item in value]
    return value


def trace_safe_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    safe = trace_safe_payload(snapshot)
    # The hash proves which immutable snapshot was used, while raw pasted
    # synthetic prose is intentionally absent from the public trace.
    safe["input_snapshot_sha256"] = sha256_json(snapshot)
    safe["raw_source_omitted"] = "synthetic_source_text" in snapshot
    return safe


_DEVELOPMENT_FRAGMENT_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|\n+")
_DEVELOPMENT_SECTION = re.compile(r"^\s*([^:：]{1,28})[:：]\s*(.*)$", flags=re.DOTALL)


def compile_development_source_fragments(text: str) -> List[DevelopmentSourceFragment]:
    """Split the primary source into stable, directly citable sentence blocks."""

    source_text = str(text or "")
    fragment_parts: List[Tuple[Optional[str], str]] = []
    active_section: Optional[str] = None
    for raw_piece in _DEVELOPMENT_FRAGMENT_SPLIT.split(source_text):
        piece = raw_piece.strip()
        if not piece:
            continue
        match = _DEVELOPMENT_SECTION.match(piece)
        if match:
            active_section = match.group(1).strip()[:120]
        # The model contract caps a fragment at 5,000 characters.  Chunking is
        # deterministic and does not modify the actual text sent as the primary
        # source.
        chunks = [piece[index:index + 4800] for index in range(0, len(piece), 4800)]
        for chunk in chunks:
            fragment_parts.append((active_section, chunk))
    if not fragment_parts:
        raise ValueError("development source contains no citable text")

    # Very sentence-dense notes used to be silently truncated at fragment 500.
    # The complete source is already capped at 30,000 characters by the API, so
    # an exact fixed-width representation always fits comfortably below the
    # 500-fragment model contract. Preserve every character (apart from outer
    # whitespace) and make the overflow behavior explicit and deterministic.
    if len(fragment_parts) > 500:
        compact_source = source_text.strip()
        fragment_parts = [
            ("overflow_merged", compact_source[index:index + 4800])
            for index in range(0, len(compact_source), 4800)
        ]

    fragments: List[DevelopmentSourceFragment] = []
    for section, chunk in fragment_parts:
        order = len(fragments) + 1
        digest = hashlib.sha256(("%s\n%s" % (order, chunk)).encode("utf-8")).hexdigest()[:10]
        fragments.append(DevelopmentSourceFragment(
            source_fragment_id="src_%04d_%s" % (order, digest),
            order=order,
            section=section,
            text=chunk,
        ))
    return fragments


def _retrieval_evidence_sources(payload: Dict[str, Any]) -> List[DevelopmentEvidenceSource]:
    sources: List[DevelopmentEvidenceSource] = []
    seen: Set[str] = set()
    for item in payload.get("citations") or []:
        if not isinstance(item, dict):
            continue
        relevance = item.get("relevance_validation")
        relevance_status = (
            str(relevance.get("status") or "")
            if isinstance(relevance, dict)
            else ""
        )
        # Search-engine rank alone is not evidence. Keep the record in the
        # trace for audit, but never feed an item with no deterministic title
        # overlap into synthesis as if it supported the case.
        if relevance_status == "unverified_search_rank":
            continue
        source_name = str(item.get("source") or "").strip().lower()
        declared_kind = str(item.get("source_kind") or "").strip().lower()
        source_kind = (
            "who"
            if "who" in source_name or declared_kind == "public_health_outbreak_notice"
            else "europe_pmc"
            if "europe" in source_name
            else "pubmed"
            if "pubmed" in source_name
            else "journal"
        )
        source_id = str(item.get("citation_id") or item.get("source_id") or "").strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        title = str(item.get("title") or "Untitled evidence record").strip()[:500]
        citation_bits = [
            str(item.get("journal") or "").strip(),
            str(item.get("year") or "").strip(),
            ("doi:%s" % item.get("doi")) if item.get("doi") else "",
        ]
        citation = ". ".join(bit for bit in citation_bits if bit) or None
        sources.append(DevelopmentEvidenceSource(
            evidence_source_id=source_id[:160],
            title=title,
            url=str(item.get("url") or "https://pubmed.ncbi.nlm.nih.gov/")[:1200],
            source_kind=source_kind,
            citation=citation,
            relevance_i18n=LocalizedText(
                zh_cn=(
                    "公共卫生通报仅用于补充时空背景；未检出通报不代表不存在。"
                    if source_kind == "who"
                    else "题名包含检索概念，作为相关性较强但仍需人工核对的文献线索。"
                    if relevance_status == "title_exact_concept_match"
                    else "题名仅与检索概念存在词语重叠，属于相关性较弱的文献线索。"
                    if relevance_status == "title_token_overlap"
                    else "用于核对病原体、暴露或综合征之间的已发表医学依据。"
                ),
                en=(
                    "Public-health notice used only as contextual evidence; no hit never proves absence."
                    if source_kind == "who"
                    else "The title contains an exact retrieval concept; this is a stronger lead but still requires human verification."
                    if relevance_status == "title_exact_concept_match"
                    else "The title has token overlap only and is a weaker literature lead."
                    if relevance_status == "title_token_overlap"
                    else "Published medical evidence used to check the pathogen, exposure, or syndrome relationship."
                ),
                status="complete",
            ),
        ))
    return sources


def _attach_candidate_specific_evidence(
    draft: DevelopmentSynthesisDraft,
    citation_ids_by_candidate: Dict[str, List[str]],
) -> DevelopmentSynthesisDraft:
    """Keep only title-verified literature bindings and cover each candidate.

    Case-fragment evidence is untouched.  Literature identifiers proposed by
    an LLM are removed unless deterministic title matching independently tied
    the source to that exact canonical pathogen name.
    """

    enriched: List[DevelopmentDraftPathogen] = []
    for candidate in draft.concrete_pathogens:
        allowed = list(dict.fromkeys(
            str(item)[:160]
            for item in citation_ids_by_candidate.get(candidate.canonical_latin_name, [])
            if str(item).strip()
        ))
        allowed_set = set(allowed)

        def cleanse(links: Sequence[DevelopmentEvidenceLink]) -> List[DevelopmentEvidenceLink]:
            cleaned: List[DevelopmentEvidenceLink] = []
            for link in links:
                verified_ids = [
                    item for item in link.evidence_source_ids if item in allowed_set
                ]
                if not link.source_fragment_ids and not verified_ids:
                    continue
                cleaned.append(link.model_copy(update={
                    "evidence_source_ids": verified_ids,
                }))
            return cleaned

        supporting = cleanse(candidate.supporting_evidence)
        opposing = cleanse(candidate.opposing_evidence)
        already_bound = {
            source_id for link in [*supporting, *opposing]
            for source_id in link.evidence_source_ids
        }
        missing = [source_id for source_id in allowed if source_id not in already_bound]
        if missing:
            zh_name = candidate.name_i18n.zh_cn or candidate.canonical_latin_name
            literature_link = DevelopmentEvidenceLink(
                statement_i18n=LocalizedText(
                    zh_cn="候选特异文献题名明确涉及 %s（%s）。" % (
                        zh_name, candidate.canonical_latin_name,
                    ),
                    en="Candidate-specific publication titles explicitly name %s."
                    % candidate.canonical_latin_name,
                    status="complete",
                ),
                evidence_source_ids=missing[:3],
            )
            # The provider contract normally emits at most three supporting
            # links.  Preserve the model limit even for malformed drafts.
            supporting = [*supporting[:19], literature_link]
        enriched.append(candidate.model_copy(update={
            "supporting_evidence": supporting,
            "opposing_evidence": opposing,
        }))
    return draft.model_copy(update={"concrete_pathogens": enriched})


def _normalized_development_pathogen_name(value: str) -> str:
    """Normalize only presentation differences in a canonical Latin name."""

    return " ".join(value.strip().casefold().replace("_", " ").split())


def reconcile_development_pathogen_provenance(
    draft: DevelopmentSynthesisDraft,
    specialist_results: Sequence[DevelopmentSpecialistResult],
) -> Tuple[DevelopmentSynthesisDraft, List[str], Dict[str, Any]]:
    """Replace LLM provenance with the frozen specialist candidate manifest.

    ``proposed_by_agent_roles`` is not a medical judgement: it is an
    auditable statement about which specialist outputs actually contained the
    same canonical pathogen name.  The synthesis model can neither add a role
    which did not propose that name nor omit a real proposer.  Matching ignores
    only case, surrounding/repeated whitespace, and underscores.  It does not
    infer synonyms or related taxa.
    """

    frozen_roles = _development_provenance_role_order()
    names_by_role: Dict[DevelopmentSpecialistRole, Set[str]] = {
        role: set() for role in frozen_roles
    }
    for result in specialist_results:
        if result.role not in names_by_role:
            continue
        names_by_role[result.role].update(
            normalized
            for proposal in result.candidate_pool
            if (normalized := _normalized_development_pathogen_name(
                proposal.canonical_latin_name
            ))
        )

    reconciled: List[DevelopmentDraftPathogen] = []
    warning_codes: List[str] = []
    candidate_audit: List[Dict[str, Any]] = []
    for candidate in draft.concrete_pathogens:
        normalized_name = _normalized_development_pathogen_name(
            candidate.canonical_latin_name
        )
        actual_roles = [
            role for role in frozen_roles
            if normalized_name in names_by_role[role]
        ]
        reported_roles = list(candidate.proposed_by_agent_roles)
        if not actual_roles:
            warning_code = (
                "candidate_provenance_missing_from_specialist_pool:rank_%d"
                % candidate.rank
            )
            warning_codes.append(warning_code)
            action = "cleared_unverified_roles"
        elif reported_roles != actual_roles:
            warning_code = "candidate_provenance_reconciled:rank_%d" % candidate.rank
            warning_codes.append(warning_code)
            action = "replaced_with_frozen_specialist_manifest"
        else:
            warning_code = None
            action = "unchanged"
        reconciled.append(candidate.model_copy(update={
            "proposed_by_agent_roles": actual_roles,
        }))
        candidate_audit.append({
            "rank": candidate.rank,
            "canonical_latin_name": candidate.canonical_latin_name,
            "normalized_canonical_latin_name": normalized_name,
            "llm_reported_roles": [role.value for role in reported_roles],
            "verified_roles": [role.value for role in actual_roles],
            "action": action,
            "warning_code": warning_code,
        })

    audit = {
        "schema_version": "owlpath.provenance-reconciliation.v1",
        "matching_rule": "canonical_latin_name_casefold_whitespace_underscore",
        "frozen_specialist_role_order": [role.value for role in frozen_roles],
        "candidates": candidate_audit,
    }
    return (
        draft.model_copy(update={"concrete_pathogens": reconciled}),
        list(dict.fromkeys(warning_codes)),
        audit,
    )


async def resolve_development_draft_taxonomy(
    draft: DevelopmentSynthesisDraft,
    resolver: TaxonomyResolver,
) -> DevelopmentSynthesisDraft:
    resolutions = await resolver.resolve([
        candidate.canonical_latin_name for candidate in draft.concrete_pathogens
    ])
    resolved_candidates: List[DevelopmentDraftPathogen] = []
    for candidate in draft.concrete_pathogens:
        normalized = " ".join(candidate.canonical_latin_name.strip().casefold().replace("_", " ").split())
        resolution = resolutions.get(normalized) or {}
        payload = candidate.model_dump(mode="json")
        payload["ncbi_taxonomy_id"] = resolution.get("ncbi_taxonomy_id")
        payload["taxonomy_resolution_status"] = resolution.get(
            "taxonomy_resolution_status", "unresolved"
        )
        payload["taxonomy_resolution_reason_code"] = resolution.get(
            "taxonomy_resolution_reason_code", "resolver_reason_missing"
        )
        payload["ncbi_taxonomy_rank"] = resolution.get("ncbi_taxonomy_rank")
        registered_name = resolution.get("canonical_latin_name")
        if registered_name:
            payload["canonical_latin_name"] = registered_name
        registered_i18n = resolution.get("name_i18n")
        if isinstance(registered_i18n, dict) and registered_i18n.get("zh_cn"):
            # Trusted terminology may complete a missing translation, but it
            # never replaces an already supplied bilingual medical label.
            existing = payload.get("name_i18n") or {}
            if not existing.get("zh_cn") or not existing.get("en"):
                payload["name_i18n"] = registered_i18n
        resolved_candidates.append(DevelopmentDraftPathogen.model_validate(payload))
    return draft.model_copy(update={"concrete_pathogens": resolved_candidates})


# Critic issue codes are intentionally free text at the provider boundary.  A
# small, explicit allow-list is used here so that only claims which can be
# proven or disproven from the frozen source manifest / Top-5 contract are
# reconciled deterministically.  Everything else remains a medical judgement
# from the independent critic and is retained unchanged.
_OBJECTIVE_CRITIC_ISSUE_CODES: Dict[str, Set[str]] = {
    "source_fragment": {
        "missing_source_fragment",
        "unknown_source_fragment",
        "nonexistent_source_fragment",
        "invalid_source_fragment",
        "source_fragment_missing",
        "source_fragment_id_invalid",
        "source_fragment_ids_invalid",
        "invalid_source_fragment_id",
        "invalid_source_fragment_ids",
    },
    "top5_count": {
        "top5_count",
        "incorrect_top5_count",
        "invalid_top5_count",
        "wrong_top5_count",
        "too_few_pathogens",
        "too_many_pathogens",
    },
    # Some critics describe the same objective defect as an insufficient
    # number of *concrete* candidates.  That claim can be caused either by a
    # short list or by one of the returned entries being generic/non-concrete,
    # so it has a slightly wider deterministic signal set than top5_count.
    "concrete_top5_count": {
        "insufficient_concrete_pathogens",
        "too_few_concrete_pathogens",
        "not_enough_concrete_pathogens",
        "fewer_than_five_concrete_pathogens",
    },
    "duplicate_pathogen": {
        "duplicate_pathogen",
        "duplicate_pathogens",
        "duplicate_candidate",
        "duplicate_candidates",
        "duplicate_top5_entry",
    },
    "rank_sequence": {
        "rank_sequence",
        "invalid_rank_sequence",
        "duplicate_rank",
        "missing_rank",
        "rank_order",
    },
    "score_order": {
        "score_order",
        "inconsistent_score_order",
        "score_rank_mismatch",
        "scores_not_descending",
    },
    "ranking": {
        "inconsistent_ranking",
        "invalid_ranking",
        "ranking_inconsistency",
    },
    "concrete_pathogen": {
        "generic_pathogen_name",
        "unspecified_pathogen_name",
        "non_concrete_taxonomic_rank",
        "category_in_top5",
        "broad_category_in_top5",
        "non_concrete_pathogen",
        "genus_in_top5",
        "genus_level_in_top5",
    },
    "supporting_evidence": {
        "missing_supporting_evidence",
        "missing_case_evidence",
        "missing_citation_for_candidate",
        "missing_candidate_citation",
        "candidate_missing_citation",
        "missing_citation_for_pathogen",
    },
    "agent_provenance": {
        "missing_agent_provenance",
    },
    "taxonomy_resolution": {
        "taxonomy_unresolved",
        "missing_taxonomy_id",
        "unresolved_taxonomy",
        "taxonomy_resolution_inconsistent",
        "taxonomy_resolution_inconsistency",
        "taxonomy_inconsistent",
    },
    # Diversity between pathogen categories is useful medical feedback, but it
    # is not an invariant of the development Top-5 contract.  A critic must not
    # force a revision merely because two concrete, contract-valid pathogens
    # belong to the same broad category.
    "category_diversity": {
        "category_overlap_in_top5",
        "category_overlap",
        "insufficient_category_diversity",
        "too_many_same_category",
    },
}

# Candidate-specific literature is deliberately attached *after* the critic
# has reviewed the clinical synthesis.  These issue codes therefore describe
# work owned by ``candidate_evidence_enrichment`` rather than a defect that the
# synthesis LLM can repair.  Keep this allow-list narrow: missing patient-case
# evidence/source fragments remain deterministic contract failures, while
# subjective medical criticism remains authoritative.
_DEFERRED_EXTERNAL_EVIDENCE_CRITIC_ISSUE_CODES: Set[str] = {
    "missing_evidence_citation",
    "missing_evidence_citations",
    "missing_external_evidence_citation",
    "missing_literature_citation",
    "missing_bibliographic_citation",
    "missing_evidence_source_id",
    "missing_evidence_source_ids",
    "missing_evidence_source_ids_in_supporting_evidence",
    "missing_external_evidence_source",
    "missing_external_evidence_source_ids",
}

_OBJECTIVE_CRITIC_VALIDATION_SIGNALS: Dict[str, Set[str]] = {
    "source_fragment": {"unknown_source_fragment", "missing_case_evidence"},
    "top5_count": {"top5_count"},
    "concrete_top5_count": {
        "top5_count",
        "duplicate_pathogen",
        "generic_pathogen_name",
        "unspecified_pathogen_name",
        "non_concrete_taxonomic_rank",
    },
    "duplicate_pathogen": {"duplicate_pathogen"},
    "rank_sequence": {"rank_sequence"},
    "score_order": {"score_order"},
    "ranking": {"rank_sequence", "score_order"},
    "concrete_pathogen": {
        "generic_pathogen_name",
        "unspecified_pathogen_name",
        "non_concrete_taxonomic_rank",
    },
    "supporting_evidence": {"missing_supporting_evidence", "missing_case_evidence"},
    "agent_provenance": {"missing_agent_provenance"},
    "taxonomy_resolution": {"taxonomy_unresolved"},
    "category_diversity": set(),
}


def _normalized_critic_issue_code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def _objective_critic_group(
    normalized_code: str,
    exact_code_to_group: Dict[str, str],
) -> Optional[str]:
    """Classify free-form critic codes only when the claim is deterministic.

    Providers frequently add suffixes such as ``_rank4`` or invent close
    variants such as ``score_order_issue``.  Exact-code matching alone let
    objectively false claims trigger a costly revision.  These deliberately
    narrow patterns cover only invariants already owned by the deterministic
    Top-5 validator; medical judgement remains untouched.
    """

    exact = exact_code_to_group.get(normalized_code)
    if exact is not None:
        return exact
    if (
        (
            "source_fragment_id" in normalized_code
            or "source_fragment_identifier" in normalized_code
        )
        and any(
            token in normalized_code
            for token in ("invalid", "unknown", "nonexistent", "not_found")
        )
    ):
        # Whether a cited source identifier exists is owned by the frozen
        # fragment manifest, not by the LLM critic.  Keep this deliberately
        # narrower than generic "missing citation" feedback, which may be a
        # subjective medical-evidence omission and must remain authoritative.
        return "source_fragment"
    if (
        re.search(r"(?:^|_)genus(?:_level)?(?:_|$)", normalized_code)
        and any(
            token in normalized_code
            for token in ("top5", "top_5", "candidate", "pathogen", "taxonomic_rank")
        )
    ):
        # A genus-level Top-5 entry is an objective contract claim.  Retain it
        # only when validate_development_top5 independently finds a
        # non-concrete taxonomic rank for the same candidate rank(s).
        return "concrete_pathogen"
    if "taxonomy" in normalized_code and any(
        token in normalized_code
        for token in (
            "unresolved", "missing", "invalid", "not_resolved",
            "inconsistent", "inconsistency", "mismatch", "conflict",
        )
    ):
        return "taxonomy_resolution"
    if "score" in normalized_code and any(
        token in normalized_code
        for token in ("order", "ordering", "rank", "mismatch", "inconsistent")
    ):
        return "score_order"
    if (
        "concrete_pathogen" in normalized_code
        and any(
            token in normalized_code
            for token in (
                "insufficient", "too_few", "not_enough", "fewer_than_five",
                "less_than_five",
            )
        )
    ):
        return "concrete_top5_count"
    if (
        "missing" in normalized_code
        and "citation" in normalized_code
        and any(token in normalized_code for token in ("candidate", "pathogen"))
        and not any(
            token in normalized_code
            for token in (
                "external", "literature", "bibliographic", "evidence_source",
            )
        )
    ):
        # The critic runs before candidate-specific literature enrichment and
        # is instructed to verify patient source_fragment citations.  A
        # candidate-scoped citation claim without an explicit external scope is
        # therefore checked against the deterministic case-evidence contract.
        return "supporting_evidence"
    if re.search(r"(?:missing|absent|incorrect|invalid)_rank_?5\b", normalized_code):
        return "top5_count"
    if "category" in normalized_code and any(
        token in normalized_code
        for token in ("top5", "top_5", "restriction", "occupies", "overlap", "diversity")
    ):
        # The contract forbids a broad category *label* as a candidate, but it
        # does not require category diversity or forbid concrete viruses.
        # Any genuinely broad candidate is independently caught by validation.
        return "category_diversity"
    if normalized_code in {
        "unlinked_evidence_usage",
        "evidence_not_linked",
        "case_evidence_unlinked",
    }:
        return "supporting_evidence"
    return None


def _is_deferred_external_evidence_critic_issue(normalized_code: str) -> bool:
    """Return true only for post-critic bibliographic enrichment claims.

    ``evidence_source_id`` identifies an external retrieval record, unlike a
    patient ``source_fragment_id``.  Free-form citation wording is deferred
    only when it also explicitly says external, literature, or bibliographic;
    a code such as ``missing_exposure_citation`` may describe omitted patient
    evidence and must remain with the medical critic.  Broad phrases such as
    ``missing_supporting_evidence`` are intentionally excluded because they
    may mean missing patient evidence and are already enforced by the
    deterministic Top-5 validator.
    """

    if normalized_code in _DEFERRED_EXTERNAL_EVIDENCE_CRITIC_ISSUE_CODES:
        return True
    if not any(token in normalized_code for token in ("missing", "absent", "unlinked")):
        return False
    if "evidence_source_id" in normalized_code:
        return True
    has_external_scope = any(
        token in normalized_code
        for token in ("external", "literature", "bibliographic")
    )
    has_citation_signal = any(
        token in normalized_code for token in ("citation", "reference")
    )
    return has_external_scope and has_citation_signal


def reconcile_development_critic_result(
    critic: DevelopmentCriticResult,
    *,
    draft: DevelopmentSynthesisDraft,
    validation: DevelopmentTop5Validation,
    valid_fragment_ids: Set[str],
) -> Tuple[DevelopmentCriticResult, Dict[str, Any]]:
    """Verify only critic claims which are objectively contract-testable.

    The LLM critic remains authoritative for subjective medical review.  It is
    not authoritative about facts already frozen in the source-fragment
    manifest or invariants already computed by ``validate_development_top5``.
    False objective claims are removed from the effective review, but the raw
    issue and its deterministic disposition are returned for trace auditing.
    """

    code_to_group = {
        code: group
        for group, codes in _OBJECTIVE_CRITIC_ISSUE_CODES.items()
        for code in codes
    }
    validation_issues = list(validation.issues)
    candidate_by_rank = {item.rank: item for item in draft.concrete_pathogens}
    retained: List[DevelopmentCriticIssue] = []
    dismissed: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    evaluations: List[Dict[str, Any]] = []

    for issue in critic.issues:
        normalized_code = _normalized_critic_issue_code(issue.code)
        if _is_deferred_external_evidence_critic_issue(normalized_code):
            evaluation = {
                "issue": issue.model_dump(mode="json"),
                "disposition": "deferred_to_candidate_evidence_enrichment",
                "verification_kind": "post_critic_external_evidence_enrichment",
                "reason_code": "external_citation_coverage_checked_after_taxonomy_validated_top5",
                "owner_node_key": "candidate_evidence_enrichment",
            }
            evaluations.append(evaluation)
            deferred.append(evaluation)
            continue
        objective_group = _objective_critic_group(normalized_code, code_to_group)
        if objective_group is None:
            retained.append(issue)
            evaluations.append({
                "issue": issue.model_dump(mode="json"),
                "disposition": "retained",
                "verification_kind": "subjective_medical_review",
                "reason_code": "not_objectively_contract_testable",
            })
            continue

        relevant_ranks = set(issue.candidate_ranks)
        expected_signals = _OBJECTIVE_CRITIC_VALIDATION_SIGNALS[objective_group]
        matching_contract_issues = [
            item for item in validation_issues
            if item.code in expected_signals
            and (
                not relevant_ranks
                or item.candidate_rank is None
                or item.candidate_rank in relevant_ranks
            )
        ]

        reason_code = "matching_deterministic_contract_issue"
        objective_evidence: Dict[str, Any] = {
            "matching_contract_issue_codes": [item.code for item in matching_contract_issues],
        }
        substantiated = bool(matching_contract_issues)

        if objective_group == "source_fragment":
            target_candidates = [
                candidate_by_rank[rank]
                for rank in sorted(relevant_ranks)
                if rank in candidate_by_rank
            ] if relevant_ranks else list(draft.concrete_pathogens)
            target_references = {
                fragment_id
                for candidate in target_candidates
                for evidence in candidate.supporting_evidence + candidate.opposing_evidence
                for fragment_id in evidence.source_fragment_ids
            }
            unknown_target_references = sorted(target_references.difference(valid_fragment_ids))
            missing_case_evidence_ranks = sorted(
                candidate.rank
                for candidate in target_candidates
                if not {
                    fragment_id
                    for evidence in candidate.supporting_evidence
                    for fragment_id in evidence.source_fragment_ids
                }
            )
            issue_manifest_presence = {
                fragment_id: fragment_id in valid_fragment_ids
                for fragment_id in issue.source_fragment_ids
            }
            objective_evidence.update({
                "issue_fragment_manifest_presence": issue_manifest_presence,
                "unknown_target_candidate_references": unknown_target_references,
                "candidate_ranks_without_case_evidence": missing_case_evidence_ranks,
            })
            substantiated = bool(unknown_target_references or missing_case_evidence_ranks)
            reason_code = (
                "target_candidate_has_missing_or_unknown_source_fragment"
                if substantiated
                else "target_candidate_sources_exist_in_frozen_manifest"
            )
        elif not substantiated:
            reason_code = "deterministic_contract_does_not_support_claim"

        evaluation = {
            "issue": issue.model_dump(mode="json"),
            "disposition": "retained" if substantiated else "dismissed_invalid",
            "verification_kind": "deterministic_contract_check",
            "objective_group": objective_group,
            "reason_code": reason_code,
            "objective_evidence": objective_evidence,
        }
        evaluations.append(evaluation)
        if substantiated:
            retained.append(issue)
        else:
            dismissed.append(evaluation)

    removed_issue_count = len(dismissed) + len(deferred)
    if removed_issue_count and not retained:
        effective = DevelopmentCriticResult(
            accepted=True,
            revision_required=False,
            review_summary_i18n=LocalizedText(
                zh_cn="审稿 Agent 提出的问题已由确定性合同验证或交给候选特异文献补强节点处理；无需因此修订病原体综合结果。",
                en="The critic's issues were either resolved by deterministic contract validation or deferred to candidate-specific literature enrichment; they do not require revising the pathogen synthesis.",
                status="complete",
            ),
            issues=[],
            required_changes_i18n=[],
        )
    elif removed_issue_count:
        # The critic schema does not link each free-text summary/change to a
        # specific issue.  After a partial dismissal/defer, forwarding either
        # field could silently reintroduce a disproven instruction into the
        # revision prompt.  Retained structured issues remain authoritative;
        # rebuild only a neutral process summary and clear the unlinked changes.
        effective = critic.model_copy(update={
            "review_summary_i18n": LocalizedText(
                zh_cn=(
                    "审稿 Agent 的 %d 项结构化问题仍保留并供修订使用；"
                    "%d 项已由确定性合同驳回，%d 项已交由后续文献补强节点处理。"
                    % (len(retained), len(dismissed), len(deferred))
                ),
                en=(
                    "%d structured critic issue(s) remain for revision; "
                    "%d issue(s) were dismissed by deterministic contract checks and "
                    "%d were deferred to the later literature-enrichment node."
                    % (len(retained), len(dismissed), len(deferred))
                ),
                status="complete",
            ),
            "issues": retained,
            "required_changes_i18n": [],
        })
    else:
        effective = critic

    audit = {
        "schema_version": "owlpath.critic-reconciliation.v1",
        "raw_decision": {
            "accepted": critic.accepted,
            "revision_required": critic.revision_required,
            "issue_codes": [item.code for item in critic.issues],
            "review_summary_i18n": critic.review_summary_i18n.model_dump(mode="json"),
            "required_changes_i18n": [
                item.model_dump(mode="json") for item in critic.required_changes_i18n
            ],
        },
        "effective_decision": {
            "accepted": effective.accepted,
            "revision_required": effective.revision_required,
            "issue_codes": [item.code for item in effective.issues],
            "review_summary_i18n": effective.review_summary_i18n.model_dump(mode="json"),
            "required_changes_i18n": [
                item.model_dump(mode="json") for item in effective.required_changes_i18n
            ],
        },
        "frozen_source_fragment_count": len(valid_fragment_ids),
        "deterministic_validation_issue_codes": [item.code for item in validation.issues],
        "evaluations": evaluations,
        "dismissed_invalid_issues": dismissed,
        "deferred_issues": deferred,
    }
    return effective, audit


_FALLBACK_ALLOWED_DECLARED_RANKS = {
    DevelopmentTaxonomicRank.SPECIES,
    DevelopmentTaxonomicRank.SPECIES_COMPLEX,
    DevelopmentTaxonomicRank.VIRUS_TYPE,
}
_FALLBACK_GENERIC_GROUP_NAMES = {
    "bacteria",
    "bacterium",
    "bacterial pathogen",
    "virus",
    "viruses",
    "viral pathogen",
    "fungus",
    "fungi",
    "fungal pathogen",
    "parasite",
    "parasites",
    "pathogen",
    "pathogens",
    "unknown",
    "unknown pathogen",
    "unspecified pathogen",
    "other pathogen",
    "anaerobic bacteria",
    "anaerobes",
    "aerobic bacteria",
    "atypical bacteria",
    "multiple organisms",
    "multiple pathogens",
    "mixed organisms",
    "mixed pathogens",
    "mixed flora",
    "normal flora",
    "oral flora",
    "skin flora",
    "polymicrobial infection",
    "polymicrobial flora",
    "细菌",
    "病毒",
    "真菌",
    "寄生虫",
    "病原体",
    "未知病原体",
    "其他病原体",
}
_FALLBACK_GENERIC_GROUP_PATTERN = re.compile(
    r"(?:"
    r"^(?:multiple|mixed|various|several)\s+(?:organisms?|pathogens?|bacteria|viruses|fungi)$|"
    r"^(?:anaerobic|aerobic|atypical|enteric|oral|skin|normal|gram[ -]positive|gram[ -]negative)\s+"
    r"(?:bacteria|organisms?|pathogens?|flora|anaerobes?)$|"
    r"\b(?:spp|sp)\.?$"
    r")",
    re.IGNORECASE,
)


def _normalize_fallback_pathogen_name(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def _is_obvious_generic_fallback_name(value: str) -> bool:
    normalized = _normalize_fallback_pathogen_name(value)
    return (
        not normalized
        or normalized in _FALLBACK_GENERIC_GROUP_NAMES
        or _FALLBACK_GENERIC_GROUP_PATTERN.search(normalized) is not None
    )


def _fallback_candidate_identity_sha256(value: str) -> str:
    """Return a trace-safe stable identity without persisting model text."""

    return hashlib.sha256(
        _normalize_fallback_pathogen_name(value).encode("utf-8")
    ).hexdigest()


def build_agent_pool_fallback(
    specialist_results: Sequence[DevelopmentSpecialistResult],
    base_draft: DevelopmentSynthesisDraft,
    *,
    max_candidates: int = 5,
    valid_fragment_ids: Optional[Set[str]] = None,
) -> DevelopmentSynthesisDraft:
    """Rank a bounded specialist pool before deterministic taxonomy filtering.

    The default remains five for callers that only need the historical pure
    ranker.  Publication paths deliberately request ten, resolve that expanded
    pool, and then backfill the first five concrete taxonomy matches.
    """

    pools: Dict[str, List[Tuple[DevelopmentSpecialistRole, DevelopmentPathogenProposal]]] = defaultdict(list)
    bounded_max_candidates = max(1, min(int(max_candidates), 10))
    for result in specialist_results:
        for proposal in result.candidate_pool:
            if (
                proposal.taxonomic_rank not in _FALLBACK_ALLOWED_DECLARED_RANKS
                or not proposal.source_fragment_ids
                or (
                    valid_fragment_ids is not None
                    and bool(set(proposal.source_fragment_ids).difference(valid_fragment_ids))
                )
                or _is_obvious_generic_fallback_name(proposal.canonical_latin_name)
            ):
                continue
            key = _normalize_fallback_pathogen_name(proposal.canonical_latin_name)
            if key:
                pools[key].append((result.role, proposal))

    ranked: List[Tuple[int, float, int, str, List[Tuple[DevelopmentSpecialistRole, DevelopmentPathogenProposal]]]] = []
    for key, entries in pools.items():
        support = _candidate_independent_support_metrics(entries)
        ranked.append((
            int(support["independent_evidence_domain_count"]),
            float(support["mean_model_score"]),
            int(support["unique_source_fragment_count"]),
            key,
            entries,
        ))
    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))

    candidates: List[DevelopmentDraftPathogen] = []
    previous_fallback_score: Optional[float] = None
    for rank, (domain_count, average, evidence_count, _, entries) in enumerate(
        ranked[:bounded_max_candidates], start=1
    ):
        entries = sorted(entries, key=lambda item: item[1].model_score, reverse=True)
        lead = entries[0][1]
        entry_roles = {role for role, _ in entries}
        roles = [
            role for role in _development_provenance_role_order()
            if role in entry_roles
        ]
        fragment_ids = list(dict.fromkeys(
            fragment for _, proposal in entries for fragment in proposal.source_fragment_ids
        ))
        # The fallback is ranked lexicographically by independent frozen
        # evidence-domain support, then domain-deduplicated mean specialist
        # score, then unique source-fragment completeness. Publishing the
        # raw mean used to make later ranks appear numerically higher, so the
        # otherwise valid fallback failed its own score-order contract. Encode
        # the same deterministic priority in a bounded, explicitly uncalibrated
        # fallback rank score and cap it monotonically in final rank order.
        fallback_score = round(
            min(1.0, (0.15 * domain_count) + (0.01 * average) + (0.000001 * min(evidence_count, 999))),
            6,
        )
        if previous_fallback_score is not None:
            fallback_score = min(fallback_score, previous_fallback_score)
        previous_fallback_score = fallback_score
        ranking_explanation = LocalizedText(
            zh_cn=(
                "Agent 池确定性回退排序：%d 个独立冻结证据域支持，"
                "域去重平均分 %.3f，引用 %d 个唯一病例片段；重复 Agent 主张不加票，"
                "发布分数是未校准的回退排名分。"
                % (domain_count, average, evidence_count)
            ),
            en=(
                "Deterministic Agent-pool fallback: supported by %d independent frozen "
                "evidence domains, domain-deduplicated mean score %.3f, with %d unique "
                "case fragments; duplicate Agent claims add no vote, and the published "
                "score is an uncalibrated fallback ranking score."
                % (domain_count, average, evidence_count)
            ),
            status="complete",
        )
        opposing = []
        if lead.counterevidence_i18n is not None:
            opposing.append(DevelopmentEvidenceLink(
                statement_i18n=lead.counterevidence_i18n,
                source_fragment_ids=fragment_ids,
            ))
        candidates.append(DevelopmentDraftPathogen(
            rank=rank,
            canonical_latin_name=lead.canonical_latin_name,
            name_i18n=lead.name_i18n,
            taxonomic_rank=lead.taxonomic_rank,
            category=lead.category,
            model_score=fallback_score,
            supporting_evidence=[DevelopmentEvidenceLink(
                statement_i18n=lead.rationale_i18n,
                source_fragment_ids=fragment_ids,
            )],
            opposing_evidence=opposing,
            why_ranked_i18n=ranking_explanation,
            main_uncertainty_i18n=(
                lead.counterevidence_i18n
                or LocalizedText(
                    zh_cn="候选来自多专科 Agent 候选池，未经临床校准。",
                    en="Candidate came from the specialist Agent pool and is not clinically calibrated.",
                    status="complete",
                )
            ),
            proposed_by_agent_roles=roles,
        ))
    return base_draft.model_copy(update={
        "concrete_pathogens": candidates,
        "warnings": list(dict.fromkeys([
            *base_draft.warnings,
            "agent_pool_fallback_score_is_deterministic_rank_score",
        ])),
    })


async def build_resolved_agent_pool_fallback(
    specialist_results: Sequence[DevelopmentSpecialistResult],
    base_draft: DevelopmentSynthesisDraft,
    resolver: TaxonomyResolver,
    *,
    valid_fragment_ids: Set[str],
) -> Tuple[DevelopmentSynthesisDraft, Dict[str, Any]]:
    """Oversample, resolve, filter, and backfill a publishable Agent-pool Top-5.

    Model-proposed generic groups and non-concrete declared ranks are never
    renamed into species.  Up to ten otherwise eligible distinct candidates
    are resolved as a batch.  Only verified positive taxonomy matches survive;
    lower-ranked matches transparently backfill invalid higher-ranked entries.
    """

    input_exclusions: List[Dict[str, Any]] = []
    input_proposal_count = 0
    for result in specialist_results:
        for proposal in result.candidate_pool:
            input_proposal_count += 1
            reason_codes: List[str] = []
            if proposal.taxonomic_rank not in _FALLBACK_ALLOWED_DECLARED_RANKS:
                reason_codes.append("declared_taxonomic_rank_not_concrete")
            if not proposal.source_fragment_ids:
                reason_codes.append("missing_case_source_fragment")
            if set(proposal.source_fragment_ids).difference(valid_fragment_ids):
                reason_codes.append("unknown_source_fragment_reference")
            if _is_obvious_generic_fallback_name(proposal.canonical_latin_name):
                reason_codes.append("obvious_generic_group_label")
            if reason_codes:
                input_exclusions.append({
                    "agent_role": result.role.value,
                    "candidate_identity_sha256": _fallback_candidate_identity_sha256(
                        proposal.canonical_latin_name
                    ),
                    "reason_codes": reason_codes,
                })

    expanded = build_agent_pool_fallback(
        specialist_results,
        base_draft,
        max_candidates=10,
        valid_fragment_ids=valid_fragment_ids,
    )
    pre_resolution_by_pool_rank = {
        candidate.rank: candidate for candidate in expanded.concrete_pathogens
    }
    resolved = await resolve_development_draft_taxonomy(expanded, resolver)

    candidate_audit: List[Dict[str, Any]] = []
    eligible: List[DevelopmentDraftPathogen] = []
    eligible_taxonomy_ids: Set[int] = set()
    eligible_canonical_names: Set[str] = set()
    for candidate in sorted(resolved.concrete_pathogens, key=lambda item: item.rank):
        reason_codes: List[str] = []
        if candidate.taxonomic_rank not in _FALLBACK_ALLOWED_DECLARED_RANKS:
            reason_codes.append("declared_taxonomic_rank_not_concrete")
        if _is_obvious_generic_fallback_name(candidate.canonical_latin_name):
            reason_codes.append("obvious_generic_group_label")
        if (
            candidate.taxonomy_resolution_status
            not in {
                DevelopmentTaxonomyResolutionStatus.RESOLVED,
                DevelopmentTaxonomyResolutionStatus.CACHE_RESOLVED,
            }
        ):
            reason_codes.append("taxonomy_not_resolved")
        if candidate.ncbi_taxonomy_id is None or candidate.ncbi_taxonomy_id <= 0:
            reason_codes.append("taxonomy_id_missing_or_invalid")
        if (
            candidate.ncbi_taxonomy_rank
            and not TaxonomyResolver._is_concrete_rank(
                candidate.ncbi_taxonomy_rank,
                candidate.canonical_latin_name,
                product_rank=candidate.taxonomic_rank.value,
                verification_reason_code=candidate.taxonomy_resolution_reason_code,
            )
        ):
            reason_codes.append("ncbi_taxonomy_rank_not_concrete")

        normalized_resolved_name = _normalize_fallback_pathogen_name(
            candidate.canonical_latin_name
        )
        if not reason_codes and (
            candidate.ncbi_taxonomy_id in eligible_taxonomy_ids
            or normalized_resolved_name in eligible_canonical_names
        ):
            # Different specialist spellings or registry aliases can resolve to
            # one taxon.  Only the first ranked identity remains eligible; a
            # lower distinct taxon can then transparently backfill the Top-5.
            reason_codes.append("duplicate_after_taxonomy_resolution")

        pre_resolution_candidate = pre_resolution_by_pool_rank.get(candidate.rank)
        verified_source_fragment_ids = {
            fragment_id
            for evidence in candidate.supporting_evidence + candidate.opposing_evidence
            for fragment_id in evidence.source_fragment_ids
        }

        audit_item: Dict[str, Any] = {
            "pool_rank": candidate.rank,
            "candidate_identity_sha256": _fallback_candidate_identity_sha256(
                candidate.canonical_latin_name
            ),
            "pre_resolution_candidate_identity_sha256": (
                _fallback_candidate_identity_sha256(
                    pre_resolution_candidate.canonical_latin_name
                )
                if pre_resolution_candidate is not None
                else None
            ),
            "declared_taxonomic_rank": candidate.taxonomic_rank.value,
            "taxonomy_resolution_status": candidate.taxonomy_resolution_status.value,
            "taxonomy_resolution_reason_code": candidate.taxonomy_resolution_reason_code,
            "ncbi_taxonomy_id": candidate.ncbi_taxonomy_id,
            "ncbi_taxonomy_rank": candidate.ncbi_taxonomy_rank,
            "verified_agent_roles": [
                role.value for role in candidate.proposed_by_agent_roles
            ],
            "verified_source_fragment_count": len(verified_source_fragment_ids),
            "source_fragment_manifest_membership_verified": (
                bool(verified_source_fragment_ids)
                and verified_source_fragment_ids.issubset(valid_fragment_ids)
            ),
            "reason_codes": reason_codes,
        }
        if reason_codes:
            audit_item["disposition"] = "excluded"
        else:
            audit_item["disposition"] = "eligible"
            eligible.append(candidate)
            if candidate.ncbi_taxonomy_id is not None:
                eligible_taxonomy_ids.add(candidate.ncbi_taxonomy_id)
            eligible_canonical_names.add(normalized_resolved_name)
        candidate_audit.append(audit_item)

    selected = eligible[:5]
    previous_score: Optional[float] = None
    final_candidates: List[DevelopmentDraftPathogen] = []
    selected_pool_ranks = {candidate.rank for candidate in selected}
    for final_rank, candidate in enumerate(selected, start=1):
        fallback_score = candidate.model_score
        if previous_score is not None:
            fallback_score = min(fallback_score, previous_score)
        previous_score = fallback_score
        final_candidates.append(candidate.model_copy(update={
            "rank": final_rank,
            "model_score": fallback_score,
        }))
        for audit_item in candidate_audit:
            if audit_item["pool_rank"] == candidate.rank:
                audit_item["disposition"] = "selected"
                audit_item["final_rank"] = final_rank
                break
    for audit_item in candidate_audit:
        if (
            audit_item["disposition"] == "eligible"
            and audit_item["pool_rank"] not in selected_pool_ranks
        ):
            audit_item["disposition"] = "valid_below_top5_cutoff"
            audit_item["reason_codes"] = ["valid_below_top5_cutoff"]

    excluded_before_selected = any(
        item["disposition"] == "excluded"
        and item["pool_rank"] <= max(selected_pool_ranks, default=0)
        for item in candidate_audit
    )
    warning_codes = [
        *base_draft.warnings,
        *resolved.warnings,
        "agent_pool_fallback_candidate_oversampling",
    ]
    if input_exclusions or any(
        item["disposition"] == "excluded" for item in candidate_audit
    ):
        warning_codes.append("agent_pool_fallback_candidates_excluded")
    if len(final_candidates) == 5 and (input_exclusions or excluded_before_selected):
        warning_codes.append("agent_pool_fallback_taxonomy_backfill_applied")
    if len(final_candidates) < 5:
        warning_codes.append(
            "agent_pool_fallback_insufficient_resolved_concrete_candidates"
        )

    audit = {
        "schema_version": "owlpath.agent-pool-fallback-audit.v1",
        "max_ranked_pool_candidates": 10,
        "input_proposal_count": input_proposal_count,
        "input_exclusions": input_exclusions,
        "expanded_candidate_count": len(expanded.concrete_pathogens),
        "selected_candidate_count": len(final_candidates),
        "excluded_candidate_count": len(input_exclusions) + sum(
            item["disposition"] == "excluded" for item in candidate_audit
        ),
        "provenance_attestation": {
            "schema_version": "owlpath.agent-pool-provenance-attestation.v1",
            "basis": "completed_frozen_specialist_candidate_pool",
            "role_claims_recomputed_server_side": True,
            "synthesis_reported_roles_used": False,
            "source_fragment_manifest_membership_enforced": True,
            "taxonomy_alias_canonicalization_preserves_pre_resolution_roles": True,
        },
        "candidates": candidate_audit,
    }
    return resolved.model_copy(update={
        "concrete_pathogens": final_candidates,
        "warnings": list(dict.fromkeys(warning_codes)),
    }), audit


def _agent_pool_fallback_provenance_audit(
    fallback_audit: Dict[str, Any],
) -> Dict[str, Any]:
    """Project the fallback's server-owned provenance into a trace-safe audit.

    Agent roles were recomputed while reading the frozen specialist result
    objects, before taxonomy aliases could change a display name.  Publication
    must therefore not ask the synthesis-name reconciler to verify those roles
    a second time by exact post-resolution spelling.
    """

    return {
        **dict(fallback_audit.get("provenance_attestation") or {}),
        "selected_candidates": [
            {
                "pool_rank": item.get("pool_rank"),
                "final_rank": item.get("final_rank"),
                "candidate_identity_sha256": item.get("candidate_identity_sha256"),
                "pre_resolution_candidate_identity_sha256": item.get(
                    "pre_resolution_candidate_identity_sha256"
                ),
                "verified_agent_roles": list(item.get("verified_agent_roles") or []),
                "verified_source_fragment_count": item.get(
                    "verified_source_fragment_count", 0
                ),
                "source_fragment_manifest_membership_verified": item.get(
                    "source_fragment_manifest_membership_verified", False
                ),
            }
            for item in fallback_audit.get("candidates") or []
            if item.get("disposition") == "selected"
        ],
    }


def load_clinical_terms() -> Dict[str, Any]:
    """Load the versioned bilingual terminology registry from project config."""
    path = Path(__file__).resolve().parents[2] / "config" / "clinical_terms.zh-en.v1.json"
    try:
        payload = json_loads(path.read_text(encoding="utf-8"), {})
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def render_bilingual_result(
    result: AggregatedResult,
    terms: Dict[str, Any],
) -> AggregatedResult:
    """Apply registered bilingual display terms without another model call."""
    payload = result.model_dump(mode="json")
    pathogens = terms.get("pathogens") if isinstance(terms.get("pathogens"), list) else []
    by_canonical_id: Dict[str, Dict[str, Any]] = {}
    by_alias: Dict[str, Dict[str, Any]] = {}
    for term in pathogens:
        if not isinstance(term, dict):
            continue
        canonical_id = str(term.get("canonical_id") or "").strip()
        if canonical_id:
            by_canonical_id[canonical_id] = term
        for alias in term.get("aliases") or []:
            by_alias[str(alias).strip().lower()] = term
        if term.get("en"):
            by_alias[str(term["en"]).strip().lower()] = term
    categories = terms.get("categories") if isinstance(terms.get("categories"), dict) else {}

    def enrich_candidate(candidate: Dict[str, Any]) -> None:
        canonical_id = str(candidate.get("canonical_id") or "").strip()
        name = str(candidate.get("name") or "").strip().lower()
        term = by_canonical_id.get(canonical_id) or by_alias.get(name)
        if term is None and candidate.get("rank_level") in {"category", "unknown"}:
            term = categories.get(str(candidate.get("category") or "unknown").lower())
        if isinstance(term, dict) and term.get("zh_cn") and term.get("en"):
            candidate["display_name_i18n"] = {
                "zh_cn": term["zh_cn"], "en": term["en"], "status": "complete",
            }
        else:
            existing = candidate.get("display_name_i18n")
            registered_en = existing.get("en") if isinstance(existing, dict) else None
            candidate["display_name_i18n"] = {
                "en": registered_en or candidate.get("name") or candidate.get("canonical_id"),
                "status": "partial",
            }

    for candidate in payload.get("candidates") or []:
        if isinstance(candidate, dict):
            enrich_candidate(candidate)
    demo_projection = payload.get("demo_projection")
    if isinstance(demo_projection, dict):
        for candidate in demo_projection.get("candidates") or []:
            if isinstance(candidate, dict):
                enrich_candidate(candidate)

    next_test_terms = terms.get("next_tests") if isinstance(terms.get("next_tests"), dict) else {}
    for suggestion in payload.get("next_tests") or []:
        if not isinstance(suggestion, dict):
            continue
        term = next_test_terms.get(str(suggestion.get("test_code") or "").strip().lower())
        if isinstance(term, dict) and term.get("zh_cn") and term.get("en"):
            suggestion["test_name_i18n"] = {
                "zh_cn": term["zh_cn"], "en": term["en"], "status": "complete",
            }

    safety_terms = terms.get("safety_states") if isinstance(terms.get("safety_states"), dict) else {}
    safety_term = safety_terms.get(result.safety_action.value)
    if isinstance(safety_term, dict) and safety_term.get("zh_cn") and safety_term.get("en"):
        payload["safety_conclusion_i18n"] = {
            "zh_cn": safety_term["zh_cn"], "en": safety_term["en"], "status": "complete",
        }
    return AggregatedResult.model_validate(payload)


class ExecutionTraceRecorder:
    """Persist real node lifecycle and trace-safe artifacts for a run."""

    def __init__(self, db: Database, emit: Any) -> None:
        self.db = db
        self.emit = emit
        self._started_perf: Dict[str, float] = {}

    def add_artifact(
        self,
        run_id: str,
        node_id: str,
        direction: str,
        artifact_type: str,
        content: Any,
        schema_version: str = TRACE_ARTIFACT_SCHEMA_VERSION,
        visibility: str = "trace_safe",
    ) -> Tuple[str, str]:
        if visibility == "demo_safe":
            run = self.db.fetchone(
                """SELECT runs.run_mode, cases.data_origin FROM runs
                   JOIN cases ON cases.id = runs.case_id WHERE runs.id = ?""",
                (run_id,),
            )
            if not run or run.get("run_mode") != "development_demo" or run.get("data_origin") != "synthetic":
                raise ValueError("demo_safe artifacts require a synthetic development-demo run")
            safe_content = redact_secrets(content)
        elif visibility == "trace_safe":
            safe_content = trace_safe_payload(content)
        else:
            raise ValueError("unsupported trace artifact visibility")
        artifact_id = new_id("art")
        digest = sha256_json(safe_content)
        self.db.execute(
            """INSERT INTO run_node_artifacts
               (id, run_id, node_run_id, direction, artifact_type, schema_version,
                content_json, content_sha256, visibility, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, run_id, node_id, direction, artifact_type, schema_version,
             json_dumps(safe_content), digest, visibility, utc_now().isoformat()),
        )
        return artifact_id, digest

    def start(
        self,
        run_id: str,
        node_key: str,
        node_kind: str,
        display_name_zh: str,
        display_name_en: str,
        *,
        input_artifact: Optional[Tuple[str, Any]] = None,
        parent_node_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        provider_model: Optional[str] = None,
        role: Optional[str] = None,
        version: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        node_id = new_id("node")
        now = utc_now().isoformat()
        display = {"zh_cn": display_name_zh, "en": display_name_en, "status": "complete"}
        safe_metadata = trace_safe_payload(metadata or {})
        base_key = node_key.split(":", 1)[0]
        default_roles = {
            "snapshot": "snapshot_compiler",
            "preflight": "integrity_preflight",
            "applicability": "applicability_guard",
            "input_quality": "input_quality_guard",
            "provider": "pathogen_hypothesis_agent",
            "baseline": "engineering_baseline",
            "sanitizer": "normalized_output_sanitizer",
            "aggregator": "ensemble_aggregator",
            "safety": "release_safety_adjudicator",
            "demo_projection": "development_demo_projection",
            "bilingual_renderer": "bilingual_result_compiler",
            "persistence": "result_persistence",
        }
        default_versions = {
            "snapshot": "owlpath.snapshot.v1",
            "preflight": "owlpath.preflight.v1",
            "applicability": "owlpath.scope.v1",
            "input_quality": "owlpath.input-quality.v1",
            "baseline": "owlpath-baseline-v1",
            "sanitizer": "owlpath.sanitizer.v1",
            "aggregator": "owlpath.aggregate.v1",
            "safety": "owlpath.safety.v1",
            "demo_projection": "owlpath.demo-projection.v1",
            "bilingual_renderer": "owlpath.result.v2",
            "persistence": TRACE_VERSION,
        }
        safe_metadata.setdefault("role", role or default_roles.get(base_key, base_key))
        safe_metadata.setdefault(
            "version",
            version or (provider_model if base_key == "provider" else default_versions.get(base_key, "owlpath.runtime.v1")),
        )
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sequence = int(conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM run_execution_nodes WHERE run_id = ?",
                (run_id,),
            ).fetchone()["n"])
            conn.execute(
                """INSERT INTO run_execution_nodes
                   (id, run_id, node_key, node_kind, display_name_json, parent_node_id,
                    provider_id, provider_model, status, sequence, attempt, metadata_json, started_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, 1, ?, ?)""",
                (node_id, run_id, node_key, node_kind, json_dumps(display), parent_node_id,
                 provider_id, provider_model, sequence, json_dumps(safe_metadata), now),
            )
            conn.commit()
        self._started_perf[node_id] = time.perf_counter()
        if input_artifact is not None:
            artifact_id, _ = self.add_artifact(
                run_id, node_id, "input", input_artifact[0], input_artifact[1]
            )
            self.db.execute(
                "UPDATE run_execution_nodes SET input_artifact_id = ? WHERE id = ?",
                (artifact_id, node_id),
            )
        self.emit(run_id, "node_started", {
            "node_id": node_id, "node_key": node_key, "node_kind": node_kind,
            "status": "running", "sequence": sequence, "provider_id": provider_id,
        })
        return node_id

    def complete(
        self,
        run_id: str,
        node_id: str,
        *,
        output_artifact: Optional[Tuple[str, Any]] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
        outcome: str = "passed",
    ) -> Optional[str]:
        artifact_id: Optional[str] = None
        digest: Optional[str] = None
        if output_artifact is not None:
            artifact_id, digest = self.add_artifact(
                run_id, node_id, "output", output_artifact[0], output_artifact[1]
            )
        latency = int((time.perf_counter() - self._started_perf.pop(node_id, time.perf_counter())) * 1000)
        completed = utc_now().isoformat()
        row = self.db.fetchone("SELECT metadata_json, node_key, node_kind FROM run_execution_nodes WHERE id = ?", (node_id,))
        metadata = json_loads(row["metadata_json"], {}) if row else {}
        metadata.update(trace_safe_payload(metadata_update or {}))
        self.db.execute(
            """UPDATE run_execution_nodes SET status = 'completed', outcome = ?, output_artifact_id = ?,
               metadata_json = ?, completed_at = ?, latency_ms = ? WHERE id = ?""",
            (outcome, artifact_id, json_dumps(metadata), completed, latency, node_id),
        )
        self.emit(run_id, "node_completed", {
            "node_id": node_id,
            "node_key": row["node_key"] if row else None,
            "node_kind": row["node_kind"] if row else None,
            "status": "completed", "outcome": outcome, "latency_ms": latency, "artifact_sha256": digest,
        })
        return digest

    def fail(self, run_id: str, node_id: str, error: Dict[str, Any], outcome: str = "blocked") -> None:
        safe_error = trace_safe_payload(error)
        latency = int((time.perf_counter() - self._started_perf.pop(node_id, time.perf_counter())) * 1000)
        completed = utc_now().isoformat()
        row = self.db.fetchone("SELECT node_key, node_kind FROM run_execution_nodes WHERE id = ?", (node_id,))
        self.db.execute(
            """UPDATE run_execution_nodes SET status = 'failed', outcome = ?, error_json = ?,
               completed_at = ?, latency_ms = ? WHERE id = ?""",
            (outcome, json_dumps(safe_error), completed, latency, node_id),
        )
        self.emit(run_id, "node_failed", {
            "node_id": node_id,
            "node_key": row["node_key"] if row else None,
            "node_kind": row["node_kind"] if row else None,
            "status": "failed", "outcome": outcome, "latency_ms": latency, "error": safe_error,
        })

    def skip(
        self,
        run_id: str,
        node_key: str,
        node_kind: str,
        display_name_zh: str,
        display_name_en: str,
        reason: str,
        *,
        parent_node_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        provider_model: Optional[str] = None,
        role: Optional[str] = None,
        version: Optional[str] = None,
        outcome: str = "not_applicable",
    ) -> str:
        node_id = self.start(
            run_id, node_key, node_kind, display_name_zh, display_name_en,
            parent_node_id=parent_node_id,
            provider_id=provider_id, provider_model=provider_model,
            role=role, version=version,
            metadata={"skip_reason": reason},
        )
        completed = utc_now().isoformat()
        latency = int((time.perf_counter() - self._started_perf.pop(node_id, time.perf_counter())) * 1000)
        self.db.execute(
            "UPDATE run_execution_nodes SET status = 'skipped', outcome = ?, completed_at = ?, latency_ms = ? WHERE id = ?",
            (outcome, completed, latency, node_id),
        )
        self.emit(run_id, "node_skipped", {
            "node_id": node_id, "node_key": node_key, "node_kind": node_kind,
            "status": "skipped", "outcome": outcome, "reason": reason, "provider_id": provider_id,
        })
        return node_id


# Only atomic, controlled facts may cross the model boundary. Raw pasted
# provenance and every free-text narrative remain in the local event ledger.
# This structural boundary is intentionally stricter than DLP regexes: an
# unrecognized symptom is omitted rather than risking PHI or future-label
# leakage to a cloud model.
CONTROLLED_FACT_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("fever", (r"发热", r"高热", r"fever")),
    ("cough", (r"咳嗽", r"cough")),
    ("sputum", (r"咳痰", r"sputum")),
    ("purulent_sputum", (r"脓痰", r"purulent\s+sputum")),
    ("dyspnea", (r"气促", r"呼吸困难", r"dyspn")),
    ("chest_pain", (r"胸痛", r"chest\s+pain")),
    ("chills", (r"寒战", r"chills?")),
    ("fatigue", (r"乏力", r"fatigue")),
    ("myalgia", (r"肌痛", r"myalgia")),
    ("diarrhea", (r"腹泻", r"diarrhea")),
    ("altered_mental_status", (r"意识障碍", r"神志改变", r"confusion")),
    ("sick_contact", (r"聚集性", r"密切接触", r"同住.*发热", r"sick\s+contact")),
    ("bird_exposure", (r"禽类", r"鸟类", r"活禽", r"bird\s+exposure")),
    ("animal_exposure", (r"动物接触", r"animal\s+exposure")),
    ("recent_travel", (r"旅行史", r"旅居史", r"recent\s+travel")),
    ("aspiration_risk", (r"误吸", r"吞咽障碍", r"aspiration")),
    ("copd", (r"慢性阻塞性肺", r"\bCOPD\b")),
    ("diabetes", (r"糖尿病", r"diabetes")),
    ("chronic_kidney_disease", (r"慢性肾", r"肾功能不全", r"\bCKD\b")),
    ("heart_failure", (r"心力衰竭", r"心衰", r"heart\s+failure")),
    ("malignancy", (r"恶性肿瘤", r"malignan")),
    ("prior_antimicrobial_exposure", (r"抗菌药", r"抗生素", r"头孢", r"青霉素", r"阿奇霉素", r"喹诺酮", r"碳青霉烯")),
)
IMAGING_FACT_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("consolidation", (r"实变", r"consolidation")),
    ("ground_glass_opacity", (r"磨玻璃", r"ground[- ]glass")),
    ("infiltrate", (r"浸润", r"infiltrat")),
    ("pleural_effusion", (r"胸腔积液", r"pleural\s+effusion")),
    ("cavitation", (r"空洞", r"cavitat")),
    ("multilobar", (r"多叶", r"multilobar")),
    ("bilateral", (r"双肺", r"bilateral")),
)
LAB_NAME_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("wbc", (r"白细胞", r"^wbc$")),
    ("neutrophil_percent", (r"中性粒细胞", r"^neut?%?$")),
    ("lymphocyte_percent", (r"淋巴细胞", r"^lym%?$")),
    ("hemoglobin", (r"血红蛋白", r"^hgb?$")),
    ("platelet", (r"血小板", r"^plt$")),
    ("crp", (r"c反应蛋白", r"^hs-?crp$", r"^crp$")),
    ("procalcitonin", (r"降钙素原", r"^pct$")),
    ("lactate", (r"乳酸", r"lactate")),
    ("creatinine", (r"肌酐", r"^crea?$")),
)
VITAL_NAME_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("temperature", (r"体温", r"^t$")),
    ("heart_rate", (r"心率", r"脉搏", r"^hr$")),
    ("respiratory_rate", (r"呼吸频率", r"^rr$")),
    ("blood_pressure", (r"血压", r"^bp$")),
    ("oxygen_saturation", (r"血氧", r"spo(?:2|₂)")),
)

# These are security canonicalization ranges, not diagnostic reference ranges.
# They are intentionally broad enough for severe clinical values but narrow
# enough that identifiers, pathogen names and arbitrary strings cannot be
# smuggled through fields that look numeric. Every accepted observation is
# emitted with a server-controlled canonical unit.
LAB_CANONICAL_SPECS: Dict[str, Dict[str, Any]] = {
    "wbc": {"unit": "10^9/L", "range": (0.0, 500.0), "aliases": {"10^9/l": 1.0, "x10^9/l": 1.0, "10e9/l": 1.0}},
    "neutrophil_percent": {"unit": "%", "range": (0.0, 100.0), "aliases": {"%": 1.0, "percent": 1.0}},
    "lymphocyte_percent": {"unit": "%", "range": (0.0, 100.0), "aliases": {"%": 1.0, "percent": 1.0}},
    "hemoglobin": {"unit": "g/L", "range": (0.0, 300.0), "aliases": {"g/l": 1.0, "g/dl": 10.0}},
    "platelet": {"unit": "10^9/L", "range": (0.0, 2000.0), "aliases": {"10^9/l": 1.0, "x10^9/l": 1.0, "10e9/l": 1.0}},
    "crp": {"unit": "mg/L", "range": (0.0, 1000.0), "aliases": {"mg/l": 1.0, "mg/dl": 10.0}},
    "procalcitonin": {"unit": "ng/mL", "range": (0.0, 1000.0), "aliases": {"ng/ml": 1.0, "ug/l": 1.0}},
    "lactate": {"unit": "mmol/L", "range": (0.0, 50.0), "aliases": {"mmol/l": 1.0}},
    "creatinine": {"unit": "umol/L", "range": (0.0, 4000.0), "aliases": {"umol/l": 1.0, "mg/dl": 88.4}},
}
VITAL_CANONICAL_SPECS: Dict[str, Dict[str, Any]] = {
    "temperature": {"unit": "degC", "range": (25.0, 45.0), "aliases": {"c": 1.0, "°c": 1.0, "degc": 1.0}},
    "heart_rate": {"unit": "bpm", "range": (10.0, 300.0), "aliases": {"bpm": 1.0, "/min": 1.0, "min^-1": 1.0, "次/分": 1.0}},
    "respiratory_rate": {"unit": "breaths/min", "range": (2.0, 100.0), "aliases": {"breaths/min": 1.0, "/min": 1.0, "min^-1": 1.0, "次/分": 1.0}},
    "oxygen_saturation": {"unit": "%", "range": (0.0, 100.0), "aliases": {"%": 1.0, "percent": 1.0}},
}

SAFE_NEXT_TEST_CATALOG: Dict[str, Dict[str, Any]] = {
    "respiratory-multiplex-naat": {
        "name": "呼吸道多重核酸检测", "specimen": "规范采集的呼吸道标本",
        "rationale": "在临床指征成立时，可缩小常见呼吸道病毒及部分非典型病原范围；必须结合标本质量解释。",
        "name_i18n": {"zh_cn": "呼吸道多重核酸检测", "en": "Respiratory multiplex nucleic acid amplification test", "status": "complete"},
        "rationale_i18n": {"zh_cn": "在临床指征成立时，可缩小常见呼吸道病毒及部分非典型病原范围；必须结合标本质量解释。", "en": "When clinically indicated, this can narrow common respiratory viruses and some atypical pathogens; interpret it with specimen quality.", "status": "complete"},
        "burden": "low",
    },
    "respiratory-culture": {
        "name": "合格呼吸道标本涂片、培养及药敏", "specimen": "按本地规范采集的呼吸道标本",
        "rationale": "可帮助区分部分细菌性病原并获得药敏信息，但必须评估标本质量与定植。",
        "name_i18n": {"zh_cn": "合格呼吸道标本涂片、培养及药敏", "en": "Quality-assessed respiratory smear, culture and susceptibility testing", "status": "complete"},
        "rationale_i18n": {"zh_cn": "可帮助区分部分细菌性病原并获得药敏信息，但必须评估标本质量与定植。", "en": "This may distinguish some bacterial pathogens and provide susceptibility data, but specimen quality and colonization must be assessed.", "status": "complete"},
        "burden": "moderate",
    },
    "paired-blood-cultures": {
        "name": "规范采集多套血培养", "specimen": "不同静脉穿刺点血液",
        "rationale": "在临床指征成立时可评估菌血症或真菌血症；采血量、时机和污染控制影响信息价值。",
        "name_i18n": {"zh_cn": "规范采集多套血培养", "en": "Properly collected multiple blood-culture sets", "status": "complete"},
        "rationale_i18n": {"zh_cn": "在临床指征成立时可评估菌血症或真菌血症；采血量、时机和污染控制影响信息价值。", "en": "When clinically indicated, this can assess bacteremia or fungemia; blood volume, timing, and contamination control determine information value.", "status": "complete"},
        "burden": "moderate",
    },
    "urine-culture-ast": {
        "name": "规范尿培养及药敏", "specimen": "按规范采集的尿标本",
        "rationale": "在泌尿系统感染指征成立时可缩小病原范围；需结合症状、菌落计数和采样方式。",
        "name_i18n": {"zh_cn": "规范尿培养及药敏", "en": "Properly collected urine culture and susceptibility testing", "status": "complete"},
        "rationale_i18n": {"zh_cn": "在泌尿系统感染指征成立时可缩小病原范围；需结合症状、菌落计数和采样方式。", "en": "When urinary infection is clinically suspected, this can narrow pathogens; interpret it with symptoms, colony count, and collection method.", "status": "complete"},
        "burden": "low",
    },
    "csf-standard-plus-naat": {
        "name": "脑脊液常规、生化、培养及指征明确的核酸检测", "specimen": "脑脊液",
        "rationale": "仅在临床评估适合且无相关禁忌时，由医生决定是否实施并结合全套结果解释。",
        "name_i18n": {"zh_cn": "脑脊液常规、生化、培养及指征明确的核酸检测", "en": "CSF routine studies, biochemistry, culture and indicated nucleic acid testing", "status": "complete"},
        "rationale_i18n": {"zh_cn": "仅在临床评估适合且无相关禁忌时，由医生决定是否实施并结合全套结果解释。", "en": "A clinician should decide whether to perform this only after confirming clinical suitability and no relevant contraindication, then interpret the full result set.", "status": "complete"},
        "burden": "high",
    },
    "confirm-visible-time": {
        "name": "核对关键检查的实际可见时间", "specimen": "不适用",
        "rationale": "先确认结果在本次决策时点前是否已经对医生可见，避免未来信息泄漏。",
        "name_i18n": {"zh_cn": "核对关键检查的实际可见时间", "en": "Verify when the key test actually became visible", "status": "complete"},
        "rationale_i18n": {"zh_cn": "先确认结果在本次决策时点前是否已经对医生可见，避免未来信息泄漏。", "en": "First verify that the result was visible to the clinician before this decision time to avoid future-information leakage.", "status": "complete"},
        "burden": "low",
    },
    "review-time": {
        "name": "核对关键检查的实际可见时间", "specimen": "不适用",
        "rationale": "先确认结果在本次决策时点前是否已经对医生可见，避免未来信息泄漏。",
        "name_i18n": {"zh_cn": "核对关键检查的实际可见时间", "en": "Verify when the key test actually became visible", "status": "complete"},
        "rationale_i18n": {"zh_cn": "先确认结果在本次决策时点前是否已经对医生可见，避免未来信息泄漏。", "en": "First verify that the result was visible to the clinician before this decision time to avoid future-information leakage.", "status": "complete"},
        "burden": "low",
    },
}
MODEL_BOOLEAN_QUALITY_FIELDS: Set[str] = {
    "verified", "time_uncertain", "needs_clinician_confirmation",
    "requires_clinician_review", "clinician_reviewed",
}
MODEL_TIME_CERTAINTY_FIELDS: Set[str] = {
    "sampled_time_certainty", "visible_time_certainty", "performed_time_certainty",
}
MODEL_TIME_CERTAINTY_VALUES: Set[str] = {
    "explicit", "assumed_decision_time", "uncertain_assumed_decision_time",
}


def _controlled_facts(text: str, rules: Sequence[Tuple[str, Sequence[str]]]) -> List[Dict[str, str]]:
    facts: List[Dict[str, str]] = []
    for code, patterns in rules:
        assertions: Set[str] = set()
        temporalities: Set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                # Interpret assertion context only inside the current clause.
                # The wider window covers English phrases such as
                # "the patient denies any current fever"; clause splitting
                # prevents that negation from leaking across "but".
                prefix = text[max(0, match.start() - 64):match.start()]
                clause_prefix = re.split(
                    r"[，,。；;.!?]|(?:但是|然而|不过|但)|\b(?:but|however|although|whereas)\b",
                    prefix,
                    flags=re.IGNORECASE,
                )[-1]
                uncertain = bool(re.search(
                    r"(?:(?:可能|考虑|疑似|不除外|待排|尚不能排除)[^，,。；;]{0,12}|"
                    r"(?:\bpossible(?:ly)?\b|\bsuspect(?:ed)?\b|\bconsider(?:ing|ed)?\b|"
                    r"\bcannot\s+rule\s+out\b|\bcan(?:not|'t)\s+exclude\b)[^,.;!?]{0,24})$",
                    clause_prefix,
                    flags=re.IGNORECASE,
                ))
                negated = bool(re.search(
                    r"(?:(?:无|否认|未见|不伴|没有|排除|未使用|未接受|从未)[^，,。；;]{0,12}|"
                    r"(?:\bno\b|\bden(?:y|ies|ied)\b|\bwithout\b|\bnegative\s+for\b|"
                    r"\bnot\b|\bnever\b)[^,.;!?]{0,24})$",
                    clause_prefix,
                    flags=re.IGNORECASE,
                ))
                assertions.add("unknown" if uncertain else "absent" if negated else "present")
                temporalities.add("historical" if re.search(
                    r"(?:既往|曾经|曾|此前|近期|\bhistory\s+of\b|\bprevious(?:ly)?\b|\bprior\b|\bpast\b)",
                    clause_prefix,
                    flags=re.IGNORECASE,
                ) else "current")
        if assertions:
            status = next(iter(assertions)) if len(assertions) == 1 else "unknown"
            temporality = next(iter(temporalities)) if len(temporalities) == 1 else "mixed"
            facts.append({"code": code, "status": status, "temporality": temporality})
    return facts


def _canonical_observation(name: Any, rules: Sequence[Tuple[str, Sequence[str]]]) -> Optional[str]:
    rendered = str(name or "").strip()
    for code, patterns in rules:
        if any(re.search(pattern, rendered, flags=re.IGNORECASE) for pattern in patterns):
            return code
    return None


def _normalized_unit(value: Any) -> str:
    return (
        str(value or "").strip().lower().replace(" ", "")
        .replace("×", "x").replace("μ", "u").replace("µ", "u")
        .replace("⁹", "^9").replace("℃", "°c")
    )


def _format_canonical_number(value: float) -> str:
    return ("%.4f" % value).rstrip("0").rstrip(".")


def _canonical_numeric_value(
    code: str, raw_value: Any, raw_unit: Any, specs: Dict[str, Dict[str, Any]]
) -> Optional[Tuple[str, str]]:
    rendered_value = str(raw_value or "").strip()
    # Fixed decimal notation and tight length are part of the anti-smuggling
    # boundary. Scientific notation and arbitrary suffixes are not accepted.
    if not re.fullmatch(r"-?\d{1,5}(?:\.\d{1,4})?", rendered_value):
        return None
    spec = specs.get(code)
    if not spec:
        return None
    unit = _normalized_unit(raw_unit)
    factor = spec["aliases"].get(unit)
    if factor is None:
        return None
    canonical_value = float(rendered_value) * float(factor)
    minimum, maximum = spec["range"]
    if canonical_value < minimum or canonical_value > maximum:
        return None
    return _format_canonical_number(canonical_value), str(spec["unit"])


def _canonical_vital_value(code: str, raw_value: Any, raw_unit: Any) -> Optional[Tuple[str, str]]:
    if code == "blood_pressure":
        rendered = str(raw_value or "").strip().replace(" ", "")
        if _normalized_unit(raw_unit) != "mmhg":
            return None
        match = re.fullmatch(r"(\d{2,3})/(\d{2,3})", rendered)
        if not match:
            return None
        systolic, diastolic = int(match.group(1)), int(match.group(2))
        if not (30 <= systolic <= 300 and 10 <= diastolic <= 200 and systolic > diastolic):
            return None
        return "%d/%d" % (systolic, diastolic), "mmHg"
    if code == "temperature" and _normalized_unit(raw_unit) in {"f", "°f", "degf"}:
        rendered = str(raw_value or "").strip()
        if not re.fullmatch(r"\d{2,3}(?:\.\d{1,4})?", rendered):
            return None
        fahrenheit = float(rendered)
        celsius = (fahrenheit - 32.0) * 5.0 / 9.0
        if 25.0 <= celsius <= 45.0:
            return _format_canonical_number(celsius), "degC"
        return None
    return _canonical_numeric_value(code, raw_value, raw_unit, VITAL_CANONICAL_SPECS)


def _model_event_payload(kind: str, data: Dict[str, Any], quality: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    safe_data: Dict[str, Any] = {}
    narrative = "\n".join(_text_values for _text_values in (
        str(value) for value in data.values() if isinstance(value, str)
    ))
    if kind in {"history", "symptom", "exposure", "medication", "procedure"}:
        facts = _controlled_facts(narrative, CONTROLLED_FACT_RULES)
        if facts:
            safe_data["clinical_facts"] = facts
    elif kind == "imaging_report":
        facts = _controlled_facts(narrative, IMAGING_FACT_RULES)
        if facts:
            safe_data["imaging_facts"] = facts
        modality = str(data.get("modality") or "").lower()
        if "ct" in modality:
            safe_data["modality"] = "CT"
        elif any(item in modality for item in ("x-ray", "xray", "胸片")):
            safe_data["modality"] = "X_RAY"
    elif kind == "lab":
        test_code = _canonical_observation(data.get("test_name"), LAB_NAME_RULES)
        canonical = _canonical_numeric_value(
            test_code, data.get("value"), data.get("unit"), LAB_CANONICAL_SPECS
        ) if test_code else None
        if test_code and canonical:
            value, unit = canonical
            safe_data = {"test_code": test_code, "value": value, "unit": unit}
            if data.get("abnormal") in {"high", "low", "normal", "unknown"}:
                safe_data["abnormal"] = data["abnormal"]
    elif kind == "vital":
        observation_code = _canonical_observation(data.get("observation"), VITAL_NAME_RULES)
        canonical = _canonical_vital_value(
            observation_code, data.get("value"), data.get("unit")
        ) if observation_code else None
        if observation_code and canonical:
            value, unit = canonical
            safe_data = {"observation_code": observation_code, "value": value, "unit": unit}
    safe_quality: Dict[str, Any] = {
        key: value for key, value in quality.items()
        if key in MODEL_BOOLEAN_QUALITY_FIELDS and isinstance(value, bool)
    }
    safe_quality.update({
        key: value for key, value in quality.items()
        if key in MODEL_TIME_CERTAINTY_FIELDS and value in MODEL_TIME_CERTAINTY_VALUES
    })
    safe_quality["raw_provenance_excluded"] = True
    return safe_data, safe_quality


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clinical timestamps must include an explicit timezone offset or Z")
    return value.astimezone(timezone.utc)


def provider_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["extra_headers"] = json_loads(result.pop("extra_headers_json"), {})
    result["options"] = json_loads(result.pop("options_json"), {})
    return result


def provider_identity(provider: Dict[str, Any]) -> Tuple[str, str]:
    defaults = {
        "openai_responses": "https://api.openai.com",
        "anthropic_messages": "https://api.anthropic.com",
        "gemini_generate_content": "https://generativelanguage.googleapis.com",
        "openai_compatible": "",
        "ollama": "http://127.0.0.1:11434",
    }
    raw = provider.get("base_url") or defaults.get(provider["kind"], "")
    parsed = urlsplit(raw)
    hostname = (parsed.hostname or "unspecified").lower()
    port = ":%s" % parsed.port if parsed.port else ""
    origin = "%s://%s%s" % (parsed.scheme or "unknown", hostname, port)
    # Endpoint duplication is not model independence.  The conservative v1
    # lineage key ignores origin/kind and de-duplicates the normalized model ID.
    # A future registry can replace this with vendor-issued lineage metadata.
    identity = [str(provider["model"]).strip().lower()]
    fingerprint = hashlib.sha256(canonical_json_dumps(identity).encode("utf-8")).hexdigest()
    return origin, fingerprint


def provider_transfer_target(provider: Dict[str, Any]) -> Dict[str, Any]:
    origin, _ = provider_identity(provider)
    return {
        "provider_id": provider["id"],
        "kind": provider["kind"],
        "model": provider["model"],
        "base_url_origin": origin,
        "endpoint_url": provider_request_url(provider),
        "data_boundary": provider["data_boundary"],
    }


def immutable_run_manifest_hash(
    *,
    case_id: str,
    decision_time: str,
    run_mode: str,
    retrospective_anchor_id: Optional[str],
    provider_ids: List[str],
    include_baseline: bool,
    input_snapshot_sha256: str,
    provider_configs_sha256: str,
    governance_config_sha256: str,
    clinical_review: Dict[str, Any],
    data_transfer_consent: Optional[Dict[str, Any]],
) -> str:
    return sha256_json({
        "case_id": case_id,
        "decision_time": decision_time,
        "run_mode": run_mode,
        "retrospective_anchor_id": retrospective_anchor_id,
        "provider_ids": provider_ids,
        "include_baseline": include_baseline,
        "input_snapshot_sha256": input_snapshot_sha256,
        "provider_configs_sha256": provider_configs_sha256,
        "governance_config_sha256": governance_config_sha256,
        "clinical_review": clinical_review,
        "data_transfer_consent": data_transfer_consent,
    })


def scope_violations(snapshot: Dict[str, Any], governance: GovernanceConfig) -> List[str]:
    reasons: List[str] = []
    demographics = snapshot.get("case", {}).get("demographics", {})
    context = snapshot.get("case", {}).get("context", {})
    age = demographics.get("age_years")
    exclusions = {item.strip().lower() for item in governance.excluded_populations}
    if age is None:
        reasons.append("年龄缺失，无法确认患者属于当前成人适用范围。")
    elif isinstance(age, (int, float)) and age < governance.minimum_age_years:
        reasons.append(
            "患者年龄低于当前治理契约的最低年龄 %.0f 岁。" % governance.minimum_age_years
        )
    immune_status = demographics.get("immunocompromised")
    if "immunocompromised" in exclusions:
        if immune_status is True:
            reasons.append("免疫抑制患者属于当前治理契约的排除人群。")
        elif immune_status is not False:
            reasons.append("免疫抑制状态未明确核对为否，无法确认适用范围。")
    pregnancy_status = demographics.get("pregnant")
    sex = str(demographics.get("sex") or "unknown").lower()
    if "pregnancy" in exclusions and sex != "male":
        if pregnancy_status is True:
            reasons.append("妊娠或产后特殊人群属于当前治理契约的排除人群。")
        elif pregnancy_status is not False:
            reasons.append("妊娠状态未明确核对为否，无法确认适用范围。")
    primary = str(context.get("primary_syndrome") or "").strip().lower()
    canonical = {"respiratory", "bloodstream", "urinary", "central_nervous_system", "other"}
    if not primary:
        reasons.append("主要综合征缺失，无法确认适用范围。")
    elif primary not in canonical:
        reasons.append("主要综合征代码无效，无法确认适用范围。")
    elif primary not in {item.lower() for item in governance.allowed_syndromes}:
        reasons.append("主要综合征不在当前治理契约允许范围内。")
    acquisition = str(context.get("acquisition_context") or "unknown").strip().lower()
    if "community-onset" in governance.intended_use.lower() and acquisition != "community":
        if acquisition == "unknown":
            reasons.append("起病场景未确认，无法证明属于当前社区起病适用范围。")
        else:
            reasons.append("医疗相关或医院获得场景超出当前社区起病适用范围。")
    return reasons


def snapshot_quality_violations(snapshot: Dict[str, Any]) -> List[str]:
    """Return conservative quality blockers for species-level interpretation."""
    reasons: List[str] = []
    for event in snapshot.get("events", []):
        if event.get("kind") not in {"lab", "imaging_report"}:
            continue
        quality = event.get("quality") or {}
        rendered = canonical_json_dumps(quality).lower()
        time_uncertain = bool(
            quality.get("time_uncertain")
            or quality.get("needs_clinician_confirmation")
            or "uncertain_assumed_decision_time" in rendered
            or "时间不确定" in rendered
        )
        if time_uncertain:
            reasons.append("关键检验或影像的结果可见时间未确认，不得用于物种级结论。")
        if event.get("kind") == "lab" and not str((event.get("data") or {}).get("unit") or "").strip():
            reasons.append("至少一项关键检验缺少单位，不得用于物种级结论。")
    return list(dict.fromkeys(reasons))


def _verified_evidence(items: List[str], snapshot: Dict[str, Any]) -> List[str]:
    events = {str(event.get("event_id")): event for event in snapshot.get("events", [])}
    verified: List[str] = []
    for item in items:
        referenced = [event_id for event_id in events if event_id and event_id in item]
        for event_id in referenced:
            event = events[event_id]
            fact = {
                "event_id": event_id,
                "visible_at": event.get("visible_at"),
                "kind": event.get("kind"),
                "data": event.get("data") or {},
                "quality": event.get("quality") or {},
            }
            rendered = canonical_json_dumps(fact)
            if rendered not in verified:
                verified.append(rendered)
    return verified[:20]


def sanitize_prediction_for_snapshot(prediction: ModelPrediction, snapshot: Dict[str, Any]) -> ModelPrediction:
    """Remove provider-authored fields that can bypass clinical policy."""
    candidates = [candidate.model_copy(update={
        # Provider self-claims never establish calibration.
        "calibration_status": "uncalibrated_model_score",
        "evidence_for": _verified_evidence(candidate.evidence_for, snapshot),
        "evidence_against": _verified_evidence(candidate.evidence_against, snapshot),
    }) for candidate in prediction.candidates]
    tests: List[NextTestSuggestion] = []
    for suggestion in prediction.next_tests:
        code = suggestion.test_code.strip().lower()
        catalog = SAFE_NEXT_TEST_CATALOG.get(code)
        if not catalog:
            continue
        tests.append(NextTestSuggestion(
            test_code=code,
            test_name=catalog["name"],
            test_name_i18n=catalog["name_i18n"],
            specimen=catalog["specimen"],
            rationale=catalog["rationale"],
            rationale_i18n=catalog["rationale_i18n"],
            expected_information_gain=suggestion.expected_information_gain,
            estimated_turnaround="依本地实验室与临床流程而定",
            burden=catalog["burden"],
            requires_clinician_order=True,
        ))
    return prediction.model_copy(update={"candidates": candidates, "next_tests": tests})


class RunEngine:
    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        provider_client: ProviderClient,
        medical_retriever: Optional[MedicalEvidenceRetriever] = None,
        taxonomy_resolver: Optional[TaxonomyResolver] = None,
    ) -> None:
        self.db = db
        self.secrets = secrets
        self.provider_client = provider_client
        self.medical_retriever = medical_retriever or MedicalEvidenceRetriever()
        self.taxonomy_resolver = taxonomy_resolver or TaxonomyResolver()
        self._tasks: Set[asyncio.Task[Any]] = set()
        self.trace = ExecutionTraceRecorder(db, self.emit)

    def emit(self, run_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> int:
        return self.db.execute(
            "INSERT INTO run_events(run_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?)",
            (run_id, event_type, json_dumps(payload or {}), utc_now().isoformat()),
        )

    def schedule(self, run_id: str) -> None:
        task = asyncio.create_task(self.process_run(run_id), name="owlpath-run-%s" % run_id)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        if not self._tasks:
            return
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def recover_interrupted(self) -> None:
        rows = self.db.fetchall("SELECT id FROM runs WHERE status IN ('queued', 'running')")
        now = utc_now().isoformat()
        for row in rows:
            error = {"code": "server_restarted", "message": "Run was interrupted by server restart; create a new run."}
            self.db.execute(
                "UPDATE runs SET status = 'failed', error_json = ?, completed_at = ? WHERE id = ?",
                (json_dumps(error), now, row["id"]),
            )
            self.emit(row["id"], "failed", error)
            self.db.audit("system", "run.recovered_as_failed", "run", row["id"], error)

    def snapshot_case(self, case_id: str, decision_time: datetime) -> Dict[str, Any]:
        decision_time = as_utc(decision_time)
        case = self.db.fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
        if case is None:
            raise KeyError(case_id)
        events = self.db.fetchall(
            "SELECT * FROM clinical_events WHERE case_id = ? ORDER BY sequence",
            (case_id,),
        )
        demographics = json_loads(case["demographics_json"], {})
        context = json_loads(case["context_json"], {})
        case_payload = {
            # The local alias is an audit/indexing handle and has no clinical
            # predictive value, so it never crosses the model boundary.
            "demographics": {key: demographics.get(key) for key in (
                "age_years", "sex", "pregnant", "immunocompromised", "care_setting"
            )},
            "context": {key: context.get(key) for key in (
                "primary_syndrome", "acquisition_context"
            )},
        }
        timeline = []
        excluded_manifest = []
        for event in events:
            reasons = []
            if event["visible_at"] > decision_time.isoformat():
                reasons.append("visible_after_decision_time")
            if event["status"] == "entered_in_error":
                reasons.append("entered_in_error")
            # The v1 task is prediction before pathogen-specific results.  Even
            # a microbiology event visible before t is phase-incompatible and
            # must be structurally excluded, not merely hidden by a prompt.
            if event["kind"] == "microbiology":
                reasons.append("phase_excluded_microbiology")
            if reasons:
                excluded_manifest.append({
                    "event_id": event["id"], "sequence": event["sequence"], "reasons": reasons,
                })
                continue
            data, quality = _model_event_payload(
                event["kind"], json_loads(event["data_json"], {}), json_loads(event["quality_json"], {})
            )
            if not data:
                excluded_manifest.append({
                    "event_id": event["id"], "sequence": event["sequence"],
                    "reasons": ["no_model_safe_atomic_facts"],
                })
                continue
            timeline.append({
                "event_id": event["id"],
                "sequence": event["sequence"],
                "kind": event["kind"],
                "occurred_at": event["occurred_at"],
                "collected_at": event["collected_at"],
                "issued_at": event["issued_at"],
                "visible_at": event["visible_at"],
                "source": "clinician_reviewed_structured_event",
                "status": event["status"],
                "data": data,
                "quality": quality,
            })
        return {
            "decision_time": decision_time.isoformat(),
            "time_rule": "Only reviewed structured non-microbiology events with visible_at <= decision_time are included; raw provenance is excluded.",
            "case": case_payload,
            "events": timeline,
            "excluded_event_manifest": excluded_manifest,
            "excluded_event_count": len(excluded_manifest),
        }

    async def _invoke_provider(
        self,
        run_id: str,
        provider: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Tuple[Optional[ModelPrediction], ModelContribution, float, bool]:
        output_id = new_id("out")
        now = utc_now().isoformat()
        origin, fingerprint = provider_identity(provider)
        provider_node_id = self.trace.start(
            run_id,
            "provider:%s" % provider["id"],
            "llm_agent",
            "%s 模型执行" % provider["name"],
            "%s provider invocation" % provider["name"],
            input_artifact=("provider_request_envelope", {
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "provider_kind": provider["kind"],
                "provider_model": provider["model"],
                "data_boundary": provider["data_boundary"],
                "base_url_origin": origin,
                "input_snapshot_sha256": sha256_json(snapshot),
                "request_schema": "owlpath.provider-request.v1",
            }),
            provider_id=provider["id"],
            provider_model=provider["model"],
            metadata={"real_provider_call": True},
        )
        self.db.execute(
            """INSERT INTO run_model_outputs
               (id, run_id, node_run_id, provider_id, provider_name, provider_kind, provider_model, base_url_origin,
                provider_weight, data_boundary, model_fingerprint, status, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (output_id, run_id, provider_node_id, provider["id"], provider["name"], provider["kind"], provider["model"],
             origin, provider["weight"], provider["data_boundary"], fingerprint, now),
        )
        self.emit(run_id, "model_started", {"provider_id": provider["id"], "provider_name": provider["name"]})
        started = time.perf_counter()
        provider_node_completed = False
        sanitizer_node_id: Optional[str] = None
        try:
            key = self.secrets.decrypt(provider.get("encrypted_api_key"))
            prediction, raw = await self.provider_client.invoke(provider, key, snapshot)
            provider_prediction_hash = sha256_json(prediction.model_dump(mode="json"))
            self.trace.complete(
                run_id,
                provider_node_id,
                output_artifact=("provider_response_attestation", {
                    "provider_id": provider["id"],
                    "schema_valid": True,
                    "normalized_response_sha256": provider_prediction_hash,
                    "raw_response_omitted": True,
                }),
            )
            provider_node_completed = True
            sanitizer_node_id = self.trace.start(
                run_id,
                "sanitizer:%s" % provider["id"],
                "sanitizer",
                "%s 输出脱敏与标准化" % provider["name"],
                "%s output sanitizer" % provider["name"],
                input_artifact=("provider_response_reference", {
                    "provider_id": provider["id"],
                    "normalized_response_sha256": provider_prediction_hash,
                }),
                parent_node_id=provider_node_id,
                provider_id=provider["id"],
                provider_model=provider["model"],
            )
            prediction = sanitize_prediction_for_snapshot(prediction, snapshot)
            self.trace.complete(
                run_id,
                sanitizer_node_id,
                output_artifact=("sanitized_model_prediction", prediction.model_dump(mode="json")),
            )
            latency = int((time.perf_counter() - started) * 1000)
            completed = utc_now().isoformat()
            self.db.execute(
                """UPDATE run_model_outputs SET status = 'completed', raw_response_json = ?,
                   normalized_json = ?, latency_ms = ?, completed_at = ? WHERE id = ?""",
                (json_dumps(raw), json_dumps(prediction.model_dump(mode="json")), latency, completed, output_id),
            )
            self.emit(run_id, "model_completed", {
                "provider_id": provider["id"], "provider_name": provider["name"], "latency_ms": latency,
            })
            return prediction, ModelContribution(
                provider_id=provider["id"], provider_name=provider["name"], status="completed",
                provider_kind=provider["kind"], model=provider["model"], base_url_origin=origin,
                weight=provider["weight"], data_boundary=provider["data_boundary"], model_fingerprint=fingerprint,
                latency_ms=latency,
            ), float(provider["weight"]), provider["data_boundary"] == DataBoundary.EXTERNAL.value
        except ProviderInvocationError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            error = exc.safe_payload()
        except SecretStoreError:
            latency = int((time.perf_counter() - started) * 1000)
            error = {"code": "secret_decryption_failed", "message": "Stored provider credential could not be decrypted", "retryable": False}
        except Exception:
            latency = int((time.perf_counter() - started) * 1000)
            error = {"code": "provider_internal_error", "message": "Provider adapter failed unexpectedly", "retryable": False}
        if sanitizer_node_id is not None:
            sanitizer_row = self.db.fetchone(
                "SELECT status FROM run_execution_nodes WHERE id = ?", (sanitizer_node_id,)
            )
            if sanitizer_row and sanitizer_row["status"] == "running":
                self.trace.fail(run_id, sanitizer_node_id, error)
        elif not provider_node_completed:
            self.trace.fail(run_id, provider_node_id, error)
        completed = utc_now().isoformat()
        self.db.execute(
            "UPDATE run_model_outputs SET status = 'failed', error_json = ?, latency_ms = ?, completed_at = ? WHERE id = ?",
            (json_dumps(error), latency, completed, output_id),
        )
        self.emit(run_id, "model_failed", {
            "provider_id": provider["id"], "provider_name": provider["name"], "error": error,
        })
        return None, ModelContribution(
            provider_id=provider["id"], provider_name=provider["name"], status="failed",
            provider_kind=provider["kind"], model=provider["model"], base_url_origin=origin,
            weight=provider["weight"], data_boundary=provider["data_boundary"], model_fingerprint=fingerprint,
            latency_ms=latency, error_code=error["code"],
        ), float(provider["weight"]), provider["data_boundary"] == DataBoundary.EXTERNAL.value

    async def _invoke_development_agent(
        self,
        *,
        run_id: str,
        node_key: str,
        display_name_zh: str,
        display_name_en: str,
        role: str,
        request: Any,
        preferred_provider: Dict[str, Any],
        all_providers: Sequence[Dict[str, Any]],
        invocation_kind: str,
        call_budget: DevelopmentProviderCallBudget,
        parent_node_id: Optional[str] = None,
        role_timeout_seconds: Optional[float] = None,
        provider_request_ceiling: Optional[int] = None,
    ) -> Tuple[Optional[Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Invoke one development Agent with one bounded resilience attempt.

        ``role_timeout_seconds`` caps the complete logical role, not each
        Provider attempt.  It is intentionally separate from the Provider
        transport timeout: the latter remains the network safety ceiling,
        while this budget prevents an advisory development role from
        exhausting the run-wide deadline.  With multiple Providers the second
        attempt is a failover.  With one Provider it is allowed only after a
        retryable HTTP transport timeout/network error or 429/5xx response.
        """

        alternate_providers = [
            provider for provider in all_providers if provider["id"] != preferred_provider["id"]
        ][:1]
        candidates = [preferred_provider] + (
            alternate_providers if alternate_providers else [preferred_provider]
        )
        request_payload = request.model_dump(mode="json")
        request_digest = sha256_json(request_payload)
        normalized_role_timeout = (
            None
            if role_timeout_seconds is None
            else max(0.0, float(role_timeout_seconds))
        )
        role_deadline = (
            None
            if normalized_role_timeout is None
            else time.monotonic() + normalized_role_timeout
        )
        node_id = self.trace.start(
            run_id,
            node_key,
            "llm_agent",
            display_name_zh,
            display_name_en,
            input_artifact=("development_agent_request_attestation", {
                "request_schema": request.__class__.__name__,
                "request_sha256": request_digest,
                "source_text_omitted": True,
                "source_fragment_count": len(request_payload.get("source_fragments") or []),
                "preferred_provider_id": preferred_provider["id"],
            }),
            parent_node_id=parent_node_id,
            provider_id=preferred_provider["id"],
            provider_model=preferred_provider["model"],
            role=role,
            version=(
                "owlpath.development-specialist.v3"
                if invocation_kind == "specialist"
                else "owlpath.development-synthesis.v2"
                if invocation_kind == "synthesis"
                else "owlpath.development-critic.v2"
            ),
            metadata={
                "real_provider_call": True,
                "maximum_provider_attempts": len(candidates),
                "run_provider_request_budget": call_budget.maximum,
                "role_group_provider_request_ceiling": provider_request_ceiling,
                "same_provider_retry_limit": (
                    1 if not alternate_providers else 0
                ),
                "provider_concurrency_queue_precedes_budget_claim": True,
                "dns_preflight_precedes_budget_claim": True,
                "hidden_chain_of_thought_persisted": False,
                "role_timeout_seconds": normalized_role_timeout,
            },
        )
        attempts: List[Dict[str, Any]] = []
        method_name = {
            "specialist": "invoke_development_specialist",
            "synthesis": "invoke_development_synthesis",
            "critic": "invoke_development_critic",
        }[invocation_kind]
        method = getattr(self.provider_client, method_name)

        async def await_with_role_deadline(awaitable: Any, phase: str) -> Any:
            if role_deadline is None:
                return await awaitable
            remaining = role_deadline - time.monotonic()
            if remaining <= 0:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=remaining)

        for attempt_number, provider in enumerate(candidates, start=1):
            remaining_role_seconds: Optional[float] = None
            if role_deadline is not None:
                remaining_role_seconds = role_deadline - time.monotonic()
                if remaining_role_seconds <= 0:
                    attempts.append({
                        "attempt": attempt_number,
                        "provider_id": provider["id"],
                        "provider_name": provider["name"],
                        "model": provider["model"],
                        "status": "skipped",
                        "error_code": "development_agent_role_timeout",
                        "role_timeout_seconds": normalized_role_timeout,
                    })
                    break
            origin, fingerprint = provider_identity(provider)
            attempt_started = time.perf_counter()
            request_started = attempt_started
            active_phase = "local_preparation"
            global_request_number: Optional[int] = None
            output_id: Optional[str] = None
            request_slot: Any = None
            budget_refunded_before_http = False
            try:
                key = self.secrets.decrypt(provider.get("encrypted_api_key"))
                acquire_slot = getattr(
                    self.provider_client,
                    "acquire_development_request_slot",
                    None,
                )
                if callable(acquire_slot):
                    active_phase = "provider_concurrency_queue"
                    request_slot = await await_with_role_deadline(
                        acquire_slot(provider),
                        active_phase,
                    )

                preflight = getattr(self.provider_client, "preflight_provider", None)
                if callable(preflight):
                    active_phase = "dns_preflight"
                    await await_with_role_deadline(
                        preflight(provider),
                        active_phase,
                    )

                # Only a request that has passed the non-egress safety
                # preflight may consume one of the run's real-call slots.
                global_request_number = await call_budget.claim(
                    maximum_used=provider_request_ceiling,
                )
                if global_request_number is None:
                    attempts.append({
                        "attempt": attempt_number,
                        "provider_id": provider["id"],
                        "provider_name": provider["name"],
                        "model": provider["model"],
                        "status": "skipped",
                        "error_code": "development_provider_call_budget_exhausted",
                        "global_provider_request_budget": call_budget.maximum,
                        "request_dispatched": False,
                    })
                    break

                output_id = new_id("out")
                started_at = utc_now().isoformat()
                self.db.execute(
                    """INSERT INTO run_model_outputs
                       (id, run_id, node_run_id, provider_id, provider_name, provider_kind, provider_model,
                        base_url_origin, provider_weight, data_boundary, model_fingerprint, status, created_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
                    (
                        output_id, run_id, node_id, provider["id"], provider["name"], provider["kind"],
                        provider["model"], origin, provider["weight"], provider["data_boundary"], fingerprint,
                        started_at,
                    ),
                )
                self.emit(run_id, "model_started", {
                    "provider_id": provider["id"],
                    "provider_name": provider["name"],
                    "agent_role": role,
                    "attempt": attempt_number,
                })
                request_started = time.perf_counter()
                invocation = method(provider, key, request)
                active_phase = "provider_http_request"
                output, raw_meta = await await_with_role_deadline(
                    invocation,
                    active_phase,
                )
                latency = int((time.perf_counter() - request_started) * 1000)
                completed_at = utc_now().isoformat()
                normalized = output.model_dump(mode="json")
                self.db.execute(
                    """UPDATE run_model_outputs SET status = 'completed', raw_response_json = ?,
                       normalized_json = ?, latency_ms = ?, completed_at = ? WHERE id = ?""",
                    (json_dumps(raw_meta), json_dumps(normalized), latency, completed_at, output_id),
                )
                attempt = {
                    "attempt": attempt_number,
                    "provider_id": provider["id"],
                    "provider_name": provider["name"],
                    "model": provider["model"],
                    "model_fingerprint": fingerprint,
                    "status": "completed",
                    "latency_ms": latency,
                    "global_provider_request_number": global_request_number,
                    "request_dispatched": True,
                }
                attempts.append(attempt)
                self.emit(run_id, "model_completed", {
                    "provider_id": provider["id"],
                    "provider_name": provider["name"],
                    "agent_role": role,
                    "attempt": attempt_number,
                    "latency_ms": latency,
                })
                self.trace.complete(
                    run_id,
                    node_id,
                    output_artifact=("development_agent_output", normalized),
                    metadata_update={
                        "attempts": attempts,
                        "attempt_count": len(attempts),
                        "final_provider_id": provider["id"],
                        "final_provider_model": provider["model"],
                        "output_sha256": sha256_json(normalized),
                    },
                    outcome=(
                        "warning"
                        if (
                            attempt_number > 1
                            or bool(getattr(output, "warnings", []))
                            or bool(getattr(output, "revision_required", False))
                        )
                        else "passed"
                    ),
                )
                return output, attempts, provider
            except asyncio.CancelledError:
                # ``process_run`` enforces a run-wide hard deadline by
                # cancelling this coroutine.  Cancellation is a BaseException
                # on supported Python versions, so the ordinary failure branch
                # below does not run.  Persist a small, provider-text-free
                # terminal record before re-raising; the outer deadline handler
                # remains responsible for the run result and trace-node state.
                latency = int((time.perf_counter() - attempt_started) * 1000)
                cancellation_error = {
                    "code": "development_agent_cancelled_due_run_deadline",
                    "message": "Development Agent invocation was cancelled by the run deadline",
                    "retryable": False,
                    "details": {
                        "role": role,
                        "timeout_phase": active_phase,
                        "request_dispatched": global_request_number is not None,
                    },
                }
                if output_id is not None:
                    completed_at = utc_now().isoformat()
                    try:
                        updated = self.db.execute_rowcount(
                            """UPDATE run_model_outputs
                               SET status = 'failed', error_json = ?, latency_ms = ?,
                                   completed_at = ?
                               WHERE id = ? AND status = 'running'""",
                            (
                                json_dumps(cancellation_error),
                                latency,
                                completed_at,
                                output_id,
                            ),
                        )
                        if updated:
                            self.emit(run_id, "model_failed", {
                                "provider_id": provider["id"],
                                "provider_name": provider["name"],
                                "agent_role": role,
                                "attempt": attempt_number,
                                "error": cancellation_error,
                            })
                    except Exception:
                        # Never replace task cancellation with a persistence
                        # exception.  The run-level hard-timeout cleanup below
                        # repeats the conditional update as an idempotent
                        # backstop.
                        pass
                raise
            except asyncio.TimeoutError:
                latency = int((time.perf_counter() - attempt_started) * 1000)
                error = {
                    "code": "development_agent_role_timeout",
                    "message": "Development Agent role exceeded its bounded time budget",
                    "retryable": False,
                    "details": {
                        "role": role,
                        "role_timeout_seconds": normalized_role_timeout,
                        "timeout_phase": active_phase,
                        "request_dispatched": global_request_number is not None,
                    },
                }
            except ProviderInvocationError as exc:
                latency = int((time.perf_counter() - attempt_started) * 1000)
                error = exc.safe_payload()
                safe_details = (
                    error.get("details")
                    if isinstance(error.get("details"), dict)
                    else {}
                )
                if (
                    global_request_number is not None
                    and safe_details.get("request_dispatched") is False
                ):
                    # ProviderClient sets this flag only for a validation or
                    # DNS rejection before httpx dispatch. This includes a
                    # public-to-private DNS change found by the mandatory
                    # egress revalidation, so it must not consume one of the
                    # run-wide real-request slots.
                    budget_refunded_before_http = (
                        await call_budget.refund_before_http(
                            global_request_number
                        )
                    )
                    if budget_refunded_before_http:
                        global_request_number = None
            except SecretStoreError:
                latency = int((time.perf_counter() - attempt_started) * 1000)
                error = {
                    "code": "secret_decryption_failed",
                    "message": "Stored provider credential could not be decrypted",
                    "retryable": False,
                }
            except Exception:
                latency = int((time.perf_counter() - attempt_started) * 1000)
                error = {
                    "code": "development_agent_internal_error",
                    "message": "Development Agent invocation failed unexpectedly",
                    "retryable": False,
                }
            finally:
                if request_slot is not None:
                    request_slot.release()
            completed_at = utc_now().isoformat()
            if output_id is not None:
                self.db.execute(
                    "UPDATE run_model_outputs SET status = 'failed', error_json = ?, latency_ms = ?, completed_at = ? WHERE id = ?",
                    (json_dumps(error), latency, completed_at, output_id),
                )
            attempt = {
                "attempt": attempt_number,
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "model": provider["model"],
                "model_fingerprint": fingerprint,
                "status": "failed",
                "latency_ms": latency,
                "error_code": error["code"],
                "request_dispatched": global_request_number is not None,
            }
            if budget_refunded_before_http:
                attempt["provider_budget_refunded_before_http"] = True
            if global_request_number is not None:
                attempt["global_provider_request_number"] = global_request_number
            if "details" in error:
                attempt["error_details"] = error["details"]
            attempts.append(attempt)
            self.emit(run_id, "model_failed", {
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "agent_role": role,
                "attempt": attempt_number,
                "error": error,
            })
            if error["code"] == "development_agent_role_timeout":
                # The timeout applies to the whole logical role.  Starting a
                # failover after it expires would only spend another Provider
                # request without any remaining wall-clock budget.
                break

            if attempt_number >= len(candidates):
                break
            next_provider = candidates[attempt_number]
            if next_provider["id"] == provider["id"]:
                # DNS already has its own one-retry single-flight preflight.
                # The same model may retry only a dispatched transport error
                # or an explicitly retryable 429/5xx response, never schema,
                # refusal, policy, 4xx or arbitrary errors.
                if (
                    not bool(error.get("retryable"))
                    or not _development_same_provider_retryable_code(error["code"])
                ):
                    break
                retry_delay = _development_same_provider_retry_delay(
                    run_id,
                    node_key,
                    error["code"],
                )
                if role_deadline is not None:
                    retry_delay = min(
                        retry_delay,
                        max(0.0, role_deadline - time.monotonic()),
                    )
                if retry_delay:
                    await asyncio.sleep(retry_delay)
            # A distinct configured Provider remains a true failover target.
            # Preserve the prior behavior even when the first model returned a
            # nonretryable model-specific schema/refusal/4xx result.

        last_error_code = attempts[-1].get("error_code") if attempts else None
        if last_error_code == "development_provider_call_budget_exhausted":
            final_code = "development_provider_call_budget_exhausted"
            final_message = "The run-wide Provider request budget was exhausted"
        elif last_error_code == "development_agent_role_timeout":
            final_code = "development_agent_role_timeout"
            final_message = "Development Agent role exceeded its bounded time budget"
        else:
            final_code = "development_agent_all_providers_failed"
            final_message = "No configured provider completed this development Agent role"
        failed_attempts = [
            item for item in attempts if item.get("status") == "failed"
        ]
        root_attempt = failed_attempts[-1] if failed_attempts else None
        root_error_code = (
            root_attempt.get("error_code") if root_attempt is not None else last_error_code
        )
        final_error = {
            "code": final_code,
            "message": final_message,
            "root_error_code": root_error_code,
            "attempts": attempts,
            "run_provider_request_budget": call_budget.maximum,
            "provider_requests_used": call_budget.used,
        }
        root_details = (
            root_attempt.get("error_details")
            if isinstance(root_attempt, dict)
            and isinstance(root_attempt.get("error_details"), dict)
            else {}
        )
        if root_details.get("timeout_phase"):
            final_error["timeout_phase"] = root_details["timeout_phase"]
        self.trace.fail(run_id, node_id, final_error, outcome="warning")
        return None, attempts, None

    def _development_technical_result(
        self,
        *,
        code: str,
        warnings: Sequence[str],
        observations: Sequence[DevelopmentAgentObservationSummary],
        validation: Optional[DevelopmentTop5Validation] = None,
        critic: Optional[DevelopmentCriticResult] = None,
        revision_count: int = 0,
    ) -> DevelopmentResultV3:
        if validation is None:
            validation = DevelopmentTop5Validation(valid=False, issues=[
                DevelopmentContractIssue(code=code, message="Development Agent pipeline did not produce a usable concrete Top-5"),
            ], attempt_origin="pipeline_technical_failure")
        return DevelopmentResultV3(
            status="technical_failure",
            summary_i18n=LocalizedText(
                zh_cn="本次开发推演遇到技术性故障，未生成可用的具体病原体 Top-5。",
                en="This development run encountered a technical failure and did not produce a usable concrete pathogen Top-5.",
                status="complete",
            ),
            concrete_pathogens=[],
            category_overview=[],
            unknown_score=1.0,
            coinfection_hypotheses=[],
            next_tests=[],
            evidence_sources=[],
            agent_observations=list(observations),
            warnings=list(dict.fromkeys([code, *warnings])),
            review=DevelopmentReviewSummary(
                accepted=False,
                status="technical_failure",
                revision_count=revision_count,
                deterministic_validation=validation,
                critic=critic,
            ),
        )

    def _finalize_development_result(
        self,
        *,
        run_id: str,
        row: Dict[str, Any],
        result: DevelopmentResultV3,
        parent_node_id: Optional[str],
    ) -> None:
        compiler_node_id = self.trace.start(
            run_id,
            "result_compiler",
            "deterministic_processor",
            "开发结果编译",
            "Development result compiler",
            input_artifact=("development_result_contract_input", {
                "schema_version": result.schema_version,
                "status": result.status,
                "candidate_count": len(result.concrete_pathogens),
                "clinical_safety_action_present": False,
            }),
            parent_node_id=parent_node_id,
            role="development_result_compiler",
            version=DEVELOPMENT_RESULT_SCHEMA_VERSION,
            metadata={"translation_provider_call_count": 0, "uncalibrated_scores": True},
        )
        unsigned = result.model_dump(mode="json")
        unsigned["result_sha256"] = None
        result_hash = sha256_json(unsigned)
        result = result.model_copy(update={"result_sha256": result_hash})
        self.trace.complete(
            run_id,
            compiler_node_id,
            output_artifact=("development_result_v3", {
                "schema_version": result.schema_version,
                "status": result.status,
                "summary_i18n": result.summary_i18n.model_dump(mode="json"),
                "concrete_pathogens": [candidate.model_dump(mode="json") for candidate in result.concrete_pathogens],
                "unknown_score": result.unknown_score,
                "next_tests": [item.model_dump(mode="json") for item in result.next_tests],
                "warnings": result.warnings,
                "fallback_mode": result.fallback_mode,
                "result_sha256": result_hash,
            }),
            outcome="warning" if result.status != "completed" else "passed",
        )
        persistence_node_id = self.trace.start(
            run_id,
            "persistence",
            "infrastructure",
            "结果持久化",
            "Result persistence",
            input_artifact=("result_persistence_request", {
                "run_id": run_id,
                "schema_version": result.schema_version,
                "result_sha256": result_hash,
            }),
            parent_node_id=compiler_node_id,
            role="result_persistence",
            version=DEVELOPMENT_TRACE_VERSION,
        )
        completed_at = utc_now().isoformat()
        run_status = "failed" if result.status == "technical_failure" else "completed"
        error_payload = (
            {
                "code": "development_technical_failure",
                "message": "Development Agent pipeline did not produce a contract-valid concrete Top-5",
            }
            if run_status == "failed" else None
        )
        self.db.execute(
            """UPDATE runs SET status = ?, result_json = ?, result_sha256 = ?, error_json = ?,
               schema_version = ?, engine_version = ?, completed_at = ? WHERE id = ?""",
            (
                run_status,
                json_dumps(result.model_dump(mode="json")),
                result_hash,
                json_dumps(error_payload) if error_payload else None,
                DEVELOPMENT_RESULT_SCHEMA_VERSION,
                "0.2.0-development-agents",
                completed_at,
                run_id,
            ),
        )
        self.trace.complete(
            run_id,
            persistence_node_id,
            output_artifact=("persistence_receipt", {
                "run_id": run_id,
                "status": run_status,
                "development_result_status": result.status,
                "result_sha256": result_hash,
                "completed_at": completed_at,
            }),
            outcome="warning" if run_status == "failed" else "passed",
        )
        terminal_event = "failed" if run_status == "failed" else "completed"
        self.emit(run_id, terminal_event, {
            "schema_version": result.schema_version,
            "development_result_status": result.status,
            "concrete_pathogen_count": len(result.concrete_pathogens),
        })
        self.db.audit("system", "run.%s" % terminal_event, "run", run_id, {
            "provider_ids": json_loads(row.get("provider_ids_json"), []),
            "run_mode": "development_demo",
            "schema_version": result.schema_version,
            "development_result_status": result.status,
        })

    async def _process_development_v3(
        self,
        *,
        run_id: str,
        row: Dict[str, Any],
        snapshot: Dict[str, Any],
        providers: Sequence[Dict[str, Any]],
        applicability: Sequence[str],
        quality_violations: Sequence[str],
        parent_node_id: Optional[str],
        hard_deadline_monotonic: Optional[float] = None,
    ) -> None:
        call_budget = DevelopmentProviderCallBudget()
        warnings: List[str] = [
            *["scope_observation:%s" % item for item in applicability],
            *["input_quality_observation:%s" % item for item in quality_violations],
            *["organizer_observation:%s" % item for item in ((snapshot.get("local_organizer") or {}).get("warning_codes") or [])],
        ]
        observations: List[DevelopmentAgentObservationSummary] = []
        source_text = str(snapshot.get("synthetic_source_text") or "")
        fragments = compile_development_source_fragments(source_text)
        fragment_ids = {fragment.source_fragment_id for fragment in fragments}
        source_node_id = self.trace.start(
            run_id,
            "source_compiler",
            "deterministic_processor",
            "原始病例全文与来源分段",
            "Primary source and fragment compiler",
            input_artifact=("source_compiler_input", {
                "source_text_sha256": snapshot.get("synthetic_source_text_sha256"),
                "source_text_length": len(source_text),
                "raw_text_omitted": True,
            }),
            parent_node_id=parent_node_id,
            role="source_fragment_compiler",
            version="owlpath.source-fragments.v1",
            metadata={"primary_reasoning_source": True},
        )
        self.trace.add_artifact(
            run_id,
            source_node_id,
            "output",
            "synthetic_source_fragment_map",
            {
                "synthetic_only": True,
                "source_fragments": [fragment.model_dump(mode="json") for fragment in fragments],
            },
            visibility="demo_safe",
        )
        self.trace.complete(
            run_id,
            source_node_id,
            output_artifact=("source_fragment_manifest", {
                "fragment_count": len(fragments),
                "fragments": [{
                    "source_fragment_id": fragment.source_fragment_id,
                    "order": fragment.order,
                    "section": fragment.section,
                    "text_sha256": hashlib.sha256(fragment.text.encode("utf-8")).hexdigest(),
                    "character_count": len(fragment.text),
                } for fragment in fragments],
            }),
        )

        selected_dynamic_roles = select_dynamic_development_roles(source_text)
        selected_role_values = [
            *[item[0] for item in DEVELOPMENT_CORE_SPECIALIST_ROLES],
            *selected_dynamic_roles,
        ]
        role_metadata = {
            role: (display_zh, display_en)
            for role, display_zh, display_en in DEVELOPMENT_SPECIALIST_ROLES
        }
        router_node_id = self.trace.start(
            run_id,
            "complexity_router",
            "deterministic_router",
            "病例复杂度与专科路由",
            "Case-complexity and specialist router",
            input_artifact=("specialist_router_input", {
                "source_text_sha256": snapshot.get("synthetic_source_text_sha256"),
                "raw_text_omitted": True,
                "core_role_count": len(DEVELOPMENT_CORE_SPECIALIST_ROLES),
                "maximum_dynamic_roles": DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS,
            }),
            parent_node_id=source_node_id,
            role="adaptive_specialist_router",
            version="owlpath.specialist-router.v2",
        )
        self.trace.complete(
            run_id,
            router_node_id,
            output_artifact=("specialist_route", {
                "schema_version": "owlpath.specialist-route.v2",
                "selected_core_roles": [item[0] for item in DEVELOPMENT_CORE_SPECIALIST_ROLES],
                "selected_dynamic_roles": selected_dynamic_roles,
                "selected_role_count": len(selected_role_values),
                "selection_rule": "frozen_source_fragment_cue_router_v2",
                "not_a_diagnostic_conclusion": True,
            }),
            outcome="warning" if selected_dynamic_roles else "passed",
        )
        for role, display_zh, display_en in DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES:
            if role in selected_dynamic_roles:
                continue
            self.trace.skip(
                run_id,
                "specialist:%s" % role,
                "llm_agent",
                display_zh,
                display_en,
                "not_selected_by_adaptive_router",
                parent_node_id=router_node_id,
                role=role,
                version="owlpath.development-specialist.v3",
                outcome="not_applicable",
            )

        if not providers:
            for role in selected_role_values:
                display_zh, display_en = role_metadata[role]
                self.trace.skip(
                    run_id, "specialist:%s" % role, "llm_agent", display_zh, display_en,
                    "no_provider", parent_node_id=router_node_id, role=role,
                    version="owlpath.development-specialist.v3", outcome="warning",
                )
            result = self._development_technical_result(
                code="no_development_provider",
                warnings=warnings,
                observations=observations,
            )
            self._finalize_development_result(run_id=run_id, row=row, result=result, parent_node_id=source_node_id)
            return

        supplementary = trace_safe_snapshot(snapshot)
        specialist_jobs = []
        role_enums: List[DevelopmentSpecialistRole] = []
        for index, role_value in enumerate(selected_role_values):
            display_zh, display_en = role_metadata[role_value]
            role = DevelopmentSpecialistRole(role_value)
            role_enums.append(role)
            request = DevelopmentSpecialistRequest(
                role=role,
                source_text=source_text,
                source_fragments=fragments,
                supplementary_structured_context=supplementary,
            )
            specialist_jobs.append(self._invoke_development_agent(
                run_id=run_id,
                node_key="specialist:%s" % role.value,
                display_name_zh=display_zh,
                display_name_en=display_en,
                role=role.value,
                request=request,
                preferred_provider=providers[index % len(providers)],
                all_providers=providers,
                invocation_kind="specialist",
                call_budget=call_budget,
                parent_node_id=router_node_id,
                provider_request_ceiling=DEVELOPMENT_SPECIALIST_PROVIDER_REQUEST_CEILING,
            ))
        specialist_invocations = await asyncio.gather(*specialist_jobs)
        specialist_results: List[DevelopmentSpecialistResult] = []
        for role, (output, attempts, _) in zip(role_enums, specialist_invocations):
            if isinstance(output, DevelopmentSpecialistResult) and output.role == role:
                specialist_results.append(output)
                specialist_warnings = list(output.warnings)
                warnings.extend("%s:%s" % (role.value, item) for item in specialist_warnings)
                observations.append(DevelopmentAgentObservationSummary(
                    role=DevelopmentAgentRole(role.value),
                    status="completed_with_warnings" if specialist_warnings or len(attempts) > 1 else "completed",
                    summary_i18n=output.summary_i18n,
                    warning_codes=(
                        specialist_warnings + _development_attempt_warning(attempts)
                    ),
                ))
            else:
                code = "specialist_role_mismatch" if output is not None else "specialist_technical_failure"
                warnings.append("%s:%s" % (role.value, code))
                observations.append(DevelopmentAgentObservationSummary(
                    role=DevelopmentAgentRole(role.value),
                    status="failed",
                    summary_i18n=LocalizedText(
                        zh_cn="该专科 Agent 未返回可用的结构化意见。",
                        en="This specialist Agent did not return a usable structured opinion.",
                        status="complete",
                    ),
                    warning_codes=[code],
                ))

        evidence_board = build_development_evidence_board(
            specialist_results,
            valid_fragment_ids=fragment_ids,
        )
        evidence_board_node_id = self.trace.start(
            run_id,
            "evidence_board",
            "deterministic_processor",
            "跨专科证据板与去重",
            "Cross-specialty evidence board and deduplication",
            input_artifact=("specialist_result_attestation", {
                "specialist_result_count": len(specialist_results),
                "specialist_result_sha256": [
                    sha256_json(item.model_dump(mode="json"))
                    for item in specialist_results
                ],
            }),
            parent_node_id=router_node_id,
            role="cross_domain_evidence_board",
            version="owlpath.evidence-board.v2",
            metadata={
                "raw_agent_vote_counting_disabled": True,
                "deduplication_is_deterministic": True,
            },
        )
        self.trace.complete(
            run_id,
            evidence_board_node_id,
            output_artifact=("development_evidence_board", evidence_board),
            outcome="warning" if not specialist_results else "passed",
        )

        if not specialist_results:
            for key, kind, zh, en in (
                ("retrieval_planner", "deterministic_processor", "检索规划已跳过", "Retrieval planning skipped"),
                ("literature_retrieval", "tool_agent", "文献与相似病例检索已跳过", "Literature and similar-case retrieval skipped"),
                ("public_health_retrieval", "tool_agent", "公共卫生与指南检索已跳过", "Public-health and guideline retrieval skipped"),
                ("evidence_verifier", "deterministic_validator", "外部证据核验已跳过", "External-evidence verification skipped"),
                ("synthesis", "llm_agent", "病原体总诊已跳过", "Pathogen synthesis skipped"),
                ("contract_validator", "deterministic_validator", "输出合同检查已跳过", "Output contract validation skipped"),
                ("critic", "llm_agent", "独立审稿已跳过", "Independent review skipped"),
                ("revision", "llm_agent", "单次修订已跳过", "Single revision skipped"),
                ("candidate_evidence_enrichment", "tool_agent", "候选特异文献补强已跳过", "Candidate-specific literature enrichment skipped"),
            ):
                self.trace.skip(run_id, key, kind, zh, en, "no_specialist_output", outcome="warning")
            result = self._development_technical_result(
                code="all_specialist_agents_failed",
                warnings=warnings,
                observations=observations,
            )
            self._finalize_development_result(run_id=run_id, row=row, result=result, parent_node_id=source_node_id)
            return

        specialist_payloads = [item.model_dump(mode="json") for item in specialist_results]
        # Outbound retrieval consumes only the server-verified evidence-board
        # projection.  Raw specialist concepts with unknown fragment claims,
        # missing provenance, or negation never reach a search connector.
        retrieval_planner_payloads = [{
            "retrieval_concepts": evidence_board.get("retrieval_concepts") or [],
            "candidate_hypotheses": evidence_board.get("candidate_hypotheses") or [],
        }]
        query_plan = build_federated_query_plan(
            retrieval_planner_payloads,
            valid_fragment_ids=fragment_ids,
        )
        retrieval_concept_audit = dict(
            evidence_board.get("retrieval_concept_audit") or {}
        )
        candidate_hypothesis_audit = dict(
            evidence_board.get("candidate_hypothesis_audit") or {}
        )
        planner_node_id = self.trace.start(
            run_id,
            "retrieval_planner",
            "deterministic_processor",
            "去标识化医学检索规划",
            "De-identified medical retrieval planner",
            input_artifact=("retrieval_concept_attestation", {
                "specialist_result_count": len(specialist_results),
                "retrieval_concept_count": len(evidence_board.get("retrieval_concepts") or []),
                "discarded_concept_count": int(
                    retrieval_concept_audit.get("discarded_count") or 0
                ),
                "discarded_candidate_count": int(
                    candidate_hypothesis_audit.get("discarded_count") or 0
                ),
                "discarded_by_reason": {
                    "retrieval_concepts": dict(
                        retrieval_concept_audit.get("discarded_by_reason") or {}
                    ),
                    "candidate_hypotheses": dict(
                        candidate_hypothesis_audit.get("discarded_by_reason") or {}
                    ),
                },
                "raw_case_text_sent": False,
                "search_query_text_omitted": True,
                "source_fragment_ids_omitted": True,
            }),
            parent_node_id=evidence_board_node_id,
            role="deidentified_medical_query_planner",
            version="owlpath.retrieval-plan.v2",
        )
        self.trace.complete(
            run_id,
            planner_node_id,
            output_artifact=("federated_retrieval_plan", {
                "schema_version": "owlpath.retrieval-plan.v2",
                "query_count": len(query_plan),
                "query_items": [item.public_payload() for item in query_plan],
                "query_sha256": [
                    hashlib.sha256(item.query.encode("utf-8")).hexdigest()
                    for item in query_plan
                ],
                "raw_case_text_sent": False,
                "search_query_text_omitted": True,
            }),
            outcome="warning" if not query_plan else "passed",
        )
        literature_node_id = self.trace.start(
            run_id,
            "literature_retrieval",
            "tool_agent",
            "医学文献与相似病例检索",
            "Medical literature and similar-case retrieval",
            input_artifact=("literature_query_attestation", {
                "plan_item_ids": [
                    item.plan_item_id for item in query_plan
                    if item.intent in {"literature", "similar_case"}
                ],
                "raw_case_text_sent": False,
            }),
            parent_node_id=planner_node_id,
            role="literature_and_similar_case_retrieval",
            version="owlpath.medical-retrieval.v2",
            metadata={"tools": ["Europe PMC REST", "NCBI PubMed E-utilities"]},
        )
        public_health_node_id = self.trace.start(
            run_id,
            "public_health_retrieval",
            "tool_agent",
            "公共卫生、疫情与指南来源检索",
            "Public-health, outbreak and guideline-source retrieval",
            input_artifact=("public_health_query_attestation", {
                "plan_item_ids": [
                    item.plan_item_id for item in query_plan
                    if item.intent == "public_health_guideline"
                ],
                "raw_case_text_sent": False,
                "no_hit_is_not_absence": True,
            }),
            parent_node_id=planner_node_id,
            role="guideline_and_public_health_retrieval",
            version="owlpath.medical-retrieval.v2",
            metadata={
                "tools": [
                    "WHO Disease Outbreak News",
                    "versioned authoritative source registry",
                ],
                "who_don_is_non_exhaustive": True,
            },
        )
        try:
            if isinstance(self.medical_retriever, MedicalEvidenceRetriever):
                federated = FederatedMedicalEvidenceRetriever(
                    timeout_seconds=self.medical_retriever.timeout_seconds,
                    max_results_per_query=self.medical_retriever.max_results_per_query,
                    transport=self.medical_retriever.transport,
                    literature_retriever=self.medical_retriever,
                )
                retrieval_bundle = await federated.retrieve_query_plan(query_plan)
                retrieval_payload = retrieval_bundle.public_payload()
            else:
                # Test/deployment adapters written for v1 remain usable.  They
                # cannot impersonate a public-health connector, so that source
                # is explicitly reported as not queried.
                generalized_queries = [item.query for item in query_plan]
                legacy_bundle = await self.medical_retriever.retrieve(generalized_queries)
                retrieval_payload = legacy_bundle.public_payload()
                retrieval_payload.update({
                    "schema_version": "owlpath.federated-medical-retrieval.v1",
                    "evidence_sources": list(retrieval_payload.get("citations") or []),
                    "literature": list(retrieval_payload.get("citations") or []),
                    "public_health": [],
                    "query_plan": [item.public_payload() for item in query_plan],
                    "authoritative_source_catalog": {"version": "adapter_not_available"},
                    "raw_case_text_sent": False,
                    "search_query_text_omitted": True,
                })
                retrieval_payload.setdefault("source_status", {})["who_don"] = "not_queried"
                retrieval_payload.setdefault("warnings", []).append(
                    "public_health_retrieval_adapter_not_available"
                )
                retrieval_payload["retrieval_partial"] = True
        except Exception:
            retrieval_payload = {
                "citations": [],
                "evidence_sources": [],
                "literature": [],
                "public_health": [],
                "warnings": ["federated_retrieval_unexpected_failure"],
                "source_status": {
                    "europe_pmc": "unavailable",
                    "pubmed": "unavailable",
                    "who_don": "unavailable",
                },
                "query_plan": [item.public_payload() for item in query_plan],
                "raw_case_text_sent": False,
                "search_query_text_omitted": True,
                "retrieval_partial": True,
            }
        literature_payload = {
            "citations": retrieval_payload.get("literature") or [],
            "source_status": {
                key: value for key, value in (retrieval_payload.get("source_status") or {}).items()
                if key in {"europe_pmc", "pubmed"}
            },
            "warning_codes": [
                item for item in (retrieval_payload.get("warnings") or [])
                if "europe" in item or "pubmed" in item or "literature" in item
            ],
            "raw_case_text_sent": False,
        }
        public_health_payload = {
            "citations": retrieval_payload.get("public_health") or [],
            "source_status": {
                key: value for key, value in (retrieval_payload.get("source_status") or {}).items()
                if key in {"who_don", "authoritative_source_catalog"}
            },
            "authoritative_source_catalog": retrieval_payload.get("authoritative_source_catalog"),
            "coverage_notes": retrieval_payload.get("coverage_notes") or [],
            "no_hit_is_not_absence": True,
            "raw_case_text_sent": False,
        }
        self.trace.complete(
            run_id,
            literature_node_id,
            output_artifact=("literature_and_similar_case_metadata", literature_payload),
            outcome=(
                "warning"
                if any(value != "available" for value in literature_payload["source_status"].values())
                else "passed"
            ),
        )
        self.trace.complete(
            run_id,
            public_health_node_id,
            output_artifact=("public_health_and_guideline_metadata", public_health_payload),
            outcome=(
                "warning"
                if retrieval_payload.get("retrieval_partial")
                else "passed"
            ),
        )
        retrieval_input_sha256 = sha256_json(retrieval_payload)
        evidence_sources = _retrieval_evidence_sources(retrieval_payload)
        retrieved_citations = [
            item for item in (retrieval_payload.get("citations") or [])
            if isinstance(item, dict)
        ]
        excluded_unverified_count = sum(
            1
            for item in retrieved_citations
            if isinstance(item.get("relevance_validation"), dict)
            and item["relevance_validation"].get("status")
            == "unverified_search_rank"
        )
        retrieval_payload["evidence_verification_summary"] = {
            "retrieved_metadata_count": len(retrieved_citations),
            "synthesis_eligible_count": len(evidence_sources),
            "unverified_search_rank_excluded_count": excluded_unverified_count,
            "verification_method": "deterministic_title_concept_overlap_v1",
            "search_rank_alone_never_promoted_to_evidence": True,
        }
        verifier_node_id = self.trace.start(
            run_id,
            "evidence_verifier",
            "deterministic_validator",
            "外部证据去重与相关性核验",
            "External-evidence deduplication and relevance verifier",
            input_artifact=("retrieval_bundle_attestation", {
                "retrieval_payload_sha256": retrieval_input_sha256,
                "raw_response_omitted": True,
            }),
            parent_node_id=literature_node_id,
            role="retrieval_deduplication_and_evidence_verifier",
            version="owlpath.evidence-verifier.v1",
        )
        retrieval_warnings = list(retrieval_payload.get("warnings") or [])
        warnings.extend(retrieval_warnings)
        self.trace.complete(
            run_id,
            verifier_node_id,
            output_artifact=("federated_medical_evidence_metadata", retrieval_payload),
            outcome="warning" if retrieval_payload.get("retrieval_partial") else "passed",
        )
        observations.append(DevelopmentAgentObservationSummary(
            role=DevelopmentAgentRole.EVIDENCE_RETRIEVAL,
            status="completed_with_warnings" if retrieval_payload.get("retrieval_partial") else "completed",
            summary_i18n=LocalizedText(
                zh_cn="已获取 %d 条可追溯文献元数据；检索不可用时仅记录告警。" % len(evidence_sources),
                en="Retrieved %d traceable literature metadata records; unavailable retrieval is warning-only." % len(evidence_sources),
                status="complete",
            ),
            warning_codes=retrieval_warnings,
        ))

        synthesis_request = DevelopmentSynthesisRequest(
            source_text=source_text,
            source_fragments=fragments,
            specialist_results=specialist_results,
            evidence_sources=evidence_sources,
            evidence_board=evidence_board,
        )
        synthesis, synthesis_attempts, _ = await self._invoke_development_agent(
            run_id=run_id,
            node_key="synthesis",
            display_name_zh="病原体总诊 Agent",
            display_name_en="Pathogen synthesis Agent",
            role="pathogen_synthesis",
            request=synthesis_request,
            preferred_provider=providers[0],
            all_providers=providers,
            invocation_kind="synthesis",
            call_budget=call_budget,
            parent_node_id=verifier_node_id,
        )
        fallback_mode = "none"
        fallback_pool_audit: Optional[Dict[str, Any]] = None
        synthesis_pool_recovery = not isinstance(
            synthesis, DevelopmentSynthesisDraft
        )
        if synthesis_pool_recovery:
            observations.append(DevelopmentAgentObservationSummary(
                role=DevelopmentAgentRole.PATHOGEN_SYNTHESIS,
                status="failed",
                summary_i18n=LocalizedText(
                    zh_cn="总诊 Agent 未返回可解析的病原体候选。",
                    en="The synthesis Agent did not return a parseable pathogen draft.",
                    status="complete",
                ),
                warning_codes=["synthesis_technical_failure"],
            ))
            warnings.append("synthesis_technical_failure")
            # A malformed/failed synthesis response must not erase five usable
            # specialist opinions.  Build only the fields that cannot be
            # inferred from those opinions, then let the existing deterministic
            # Agent-pool ranker populate the candidate list.  The uncertainty
            # score deliberately stays maximal because no synthesis model
            # supplied a defensible value.
            synthesis = DevelopmentSynthesisDraft(
                summary_i18n=LocalizedText(
                    zh_cn=(
                        "总诊 Agent 未返回可解析结果；以下 Top-5 由已完成的"
                        "专科 Agent 候选池按预先固定的规则透明排序。"
                    ),
                    en=(
                        "The synthesis Agent returned no parseable result; the "
                        "Top-5 below is transparently ranked from the completed "
                        "specialist Agent candidate pool using frozen rules."
                    ),
                    status="complete",
                ),
                unknown_score=1.0,
                warnings=["synthesis_technical_failure_agent_pool_recovery"],
            )
            fallback_mode = "agent_pool_fallback"
            warnings.extend([
                "agent_pool_fallback",
                *list(synthesis.warnings),
            ])
        else:
            observations.append(DevelopmentAgentObservationSummary(
                role=DevelopmentAgentRole.PATHOGEN_SYNTHESIS,
                status="completed_with_warnings" if synthesis.warnings or len(synthesis_attempts) > 1 else "completed",
                summary_i18n=synthesis.summary_i18n,
                warning_codes=(
                    list(synthesis.warnings)
                    + _development_attempt_warning(synthesis_attempts)
                ),
            ))
            warnings.extend("synthesis:%s" % item for item in synthesis.warnings)

        synthesis_node = self.db.fetchone(
            "SELECT id FROM run_execution_nodes WHERE run_id = ? AND node_key = 'synthesis' ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        )
        validator_node_id = self.trace.start(
            run_id,
            "contract_validator",
            "deterministic_validator",
            "确定性术语与 Top-5 合同检查",
            "Deterministic terminology and Top-5 contract validator",
            input_artifact=("synthesis_draft_reference", {
                "draft_sha256": sha256_json(synthesis.model_dump(mode="json")),
                "candidate_count": len(synthesis.concrete_pathogens),
                "draft_origin": (
                    "specialist_agent_pool_fallback"
                    if synthesis_pool_recovery
                    else "synthesis_agent"
                ),
                "synthesis_agent_parseable": not synthesis_pool_recovery,
            }),
            parent_node_id=synthesis_node["id"] if synthesis_node else None,
            role="taxonomy_and_top5_contract_validator",
            version="owlpath.development-contract.v1",
        )
        if synthesis_pool_recovery:
            current_draft, fallback_pool_audit = (
                await build_resolved_agent_pool_fallback(
                    specialist_results,
                    synthesis,
                    self.taxonomy_resolver,
                    valid_fragment_ids=fragment_ids,
                )
            )
            warnings.extend(current_draft.warnings)
            provenance_warnings: List[str] = []
            provenance_audit = _agent_pool_fallback_provenance_audit(
                fallback_pool_audit
            )
        else:
            current_draft = synthesis
            current_draft, provenance_warnings, provenance_audit = (
                reconcile_development_pathogen_provenance(
                    current_draft, specialist_results
                )
            )
        warnings.extend(provenance_warnings)
        if not synthesis_pool_recovery:
            current_draft = await resolve_development_draft_taxonomy(
                current_draft, self.taxonomy_resolver
            )
        validation = validate_development_top5(
            current_draft,
            valid_fragment_ids=fragment_ids,
            require_taxonomy_resolution=True,
        ).model_copy(update={
            "attempt_origin": (
                "synthesis_failure_agent_pool_fallback"
                if synthesis_pool_recovery
                else "synthesis_draft"
            ),
        })
        self.trace.complete(
            run_id,
            validator_node_id,
            output_artifact=("development_top5_validation", {
                "validation": validation.model_dump(mode="json"),
                "resolved_candidates": [{
                    "rank": item.rank,
                    "canonical_latin_name": item.canonical_latin_name,
                    "ncbi_taxonomy_id": item.ncbi_taxonomy_id,
                    "taxonomy_resolution_status": item.taxonomy_resolution_status,
                    "taxonomy_resolution_reason_code": item.taxonomy_resolution_reason_code,
                    "ncbi_taxonomy_rank": item.ncbi_taxonomy_rank,
                } for item in current_draft.concrete_pathogens],
                "provenance_reconciliation": provenance_audit,
                "agent_pool_fallback_audit": fallback_pool_audit,
            }),
            outcome="passed" if validation.valid else "warning",
        )

        if synthesis_pool_recovery:
            self.trace.add_artifact(
                run_id,
                validator_node_id,
                "output",
                "synthesis_failure_agent_pool_recovery",
                {
                    "schema_version": "owlpath.synthesis-failure-recovery.v1",
                    "recovery_attempted": True,
                    "recovery_mode": "agent_pool_fallback",
                    "specialist_result_count": len(specialist_results),
                    "ranked_candidate_count": len(current_draft.concrete_pathogens),
                    "deterministic_validation": validation.model_dump(mode="json"),
                    "provenance_reconciliation": provenance_audit,
                    "agent_pool_fallback_audit": fallback_pool_audit,
                    "synthesis_node_status_preserved": "failed",
                },
            )
            if not validation.valid:
                for key, kind, zh, en in (
                    ("critic", "llm_agent", "独立审稿已跳过", "Independent review skipped"),
                    ("revision", "llm_agent", "单次修订已跳过", "Single revision skipped"),
                    ("candidate_evidence_enrichment", "tool_agent", "候选特异文献补强已跳过", "Candidate-specific literature enrichment skipped"),
                ):
                    self.trace.skip(
                        run_id,
                        key,
                        kind,
                        zh,
                        en,
                        "synthesis_technical_failure_and_invalid_agent_pool",
                        outcome="warning",
                    )
                result = self._development_technical_result(
                    code="synthesis_technical_failure",
                    warnings=warnings,
                    observations=observations,
                    validation=validation,
                )
                self._finalize_development_result(
                    run_id=run_id,
                    row=row,
                    result=result,
                    parent_node_id=validator_node_id,
                )
                return

        critic_request = DevelopmentCriticRequest(
            source_text=source_text,
            source_fragments=fragments,
            specialist_results=specialist_results,
            evidence_sources=evidence_sources,
            evidence_board=evidence_board,
            draft=current_draft,
            deterministic_issues=validation.issues,
        )
        critic_provider = providers[1] if len(providers) > 1 else providers[0]
        critic_timeout_seconds = _development_stage_timeout(
            DEVELOPMENT_CRITIC_ROLE_TIMEOUT_SECONDS,
            hard_deadline_monotonic,
        )
        critic, critic_attempts, _ = await self._invoke_development_agent(
            run_id=run_id,
            node_key="critic",
            display_name_zh="独立审稿 Agent",
            display_name_en="Independent critic Agent",
            role="independent_critic",
            request=critic_request,
            preferred_provider=critic_provider,
            all_providers=providers,
            invocation_kind="critic",
            call_budget=call_budget,
            parent_node_id=validator_node_id,
            role_timeout_seconds=critic_timeout_seconds,
        )
        critic_result = critic if isinstance(critic, DevelopmentCriticResult) else None
        critic_node = self.db.fetchone(
            "SELECT id, metadata_json FROM run_execution_nodes WHERE run_id = ? AND node_key = 'critic' ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        )
        if critic_result is not None:
            critic_result, critic_reconciliation = reconcile_development_critic_result(
                critic_result,
                draft=current_draft,
                validation=validation,
                valid_fragment_ids=fragment_ids,
            )
            if critic_node:
                self.trace.add_artifact(
                    run_id,
                    critic_node["id"],
                    "output",
                    "critic_issue_reconciliation",
                    critic_reconciliation,
                )
                # The provider node was completed with the raw model decision.
                # Its final business outcome must reflect the reconciled review,
                # while the original structured output remains immutable in the
                # first trace artifact and run_model_outputs.
                reconciled_outcome = (
                    "warning"
                    if critic_result.issues
                    or critic_result.revision_required
                    or len(critic_attempts) > 1
                    else "passed"
                )
                critic_metadata = json_loads(critic_node.get("metadata_json"), {})
                critic_metadata.update({
                    "critic_reconciliation_schema": "owlpath.critic-reconciliation.v1",
                    "raw_issue_count": len((critic_reconciliation.get("raw_decision") or {}).get("issue_codes") or []),
                    "effective_issue_count": len(critic_result.issues),
                    "dismissed_invalid_issue_count": len(critic_reconciliation["dismissed_invalid_issues"]),
                    "deferred_issue_count": len(critic_reconciliation["deferred_issues"]),
                })
                self.db.execute(
                    "UPDATE run_execution_nodes SET outcome = ?, metadata_json = ? WHERE id = ?",
                    (reconciled_outcome, json_dumps(critic_metadata), critic_node["id"]),
                )
        if critic_result is not None:
            critic_codes = [item.code for item in critic_result.issues]
            warnings.extend("critic:%s" % item for item in critic_codes if item)
            observations.append(DevelopmentAgentObservationSummary(
                role=DevelopmentAgentRole.INDEPENDENT_CRITIC,
                status=(
                    "completed_with_warnings"
                    if critic_result.issues or len(critic_attempts) > 1 else "completed"
                ),
                summary_i18n=critic_result.review_summary_i18n,
                warning_codes=(
                    critic_codes + _development_attempt_warning(critic_attempts)
                ),
            ))
        else:
            critic_warning_codes = ["critic_technical_failure"]
            if any(
                item.get("error_code") == "development_agent_role_timeout"
                for item in critic_attempts
            ):
                critic_warning_codes.append("critic_role_timeout")
            warnings.extend(critic_warning_codes)
            observations.append(DevelopmentAgentObservationSummary(
                role=DevelopmentAgentRole.INDEPENDENT_CRITIC,
                status="failed",
                summary_i18n=LocalizedText(
                    zh_cn="审稿 Agent 未返回可用审稿意见；确定性合同检查仍然有效。",
                    en="The critic Agent failed; deterministic contract validation remains authoritative.",
                    status="complete",
                ),
                warning_codes=critic_warning_codes,
            ))

        revision_needed = (not validation.valid) or bool(critic_result and critic_result.revision_required)
        revision_count = 0
        # A revised draft is deliberately not sent through a second critic call:
        # the workflow is bounded to one revision.  Preserve that fact in the
        # result instead of presenting deterministic contract validity as a
        # completed independent review.
        revision_completed_and_adopted = False
        if revision_needed:
            # Treat revision as a transactional replacement.  A draft that has
            # already passed the deterministic contract remains the committed
            # version until a revised draft passes the same checks.  This keeps
            # a mistaken critic or a broken revision call from destroying a
            # usable result while preserving both attempts in the trace.
            prior_draft = current_draft
            prior_validation = validation
            revision_count = 1
            revision_critic = critic_result
            if revision_critic is None:
                revision_critic = DevelopmentCriticResult(
                    accepted=False,
                    revision_required=True,
                    review_summary_i18n=LocalizedText(
                        zh_cn="确定性合同检查要求修订。",
                        en="Deterministic contract validation requires revision.",
                        status="complete",
                    ),
                    issues=[DevelopmentCriticIssue(
                        code="deterministic_contract_failure",
                        severity="error",
                        message_i18n=LocalizedText(
                            zh_cn="请修复所有 Top-5 合同问题。",
                            en="Correct every deterministic Top-5 contract issue.",
                            status="complete",
                        ),
                    )],
                    required_changes_i18n=[LocalizedText(
                        zh_cn="返回恰好 5 个经证据支持的具体病原体。",
                        en="Return exactly five evidence-linked concrete pathogens.",
                        status="complete",
                    )],
                )
            revision_request = DevelopmentSynthesisRequest(
                source_text=source_text,
                source_fragments=fragments,
                specialist_results=specialist_results,
                evidence_sources=evidence_sources,
                evidence_board=evidence_board,
                revision_context=DevelopmentRevisionContext(
                    prior_draft=current_draft,
                    deterministic_issues=validation.issues,
                    critic_result=revision_critic,
                ),
            )
            revision_timeout_seconds = _development_stage_timeout(
                DEVELOPMENT_REVISION_ROLE_TIMEOUT_SECONDS,
                hard_deadline_monotonic,
            )
            revised, revision_attempts, _ = await self._invoke_development_agent(
                run_id=run_id,
                node_key="revision",
                display_name_zh="总诊单次修订",
                display_name_en="Single synthesis revision",
                role="single_pass_synthesis_revision",
                request=revision_request,
                preferred_provider=providers[0],
                all_providers=providers,
                invocation_kind="synthesis",
                call_budget=call_budget,
                parent_node_id=(
                    self.db.fetchone(
                        "SELECT id FROM run_execution_nodes WHERE run_id = ? AND node_key = 'critic' ORDER BY sequence DESC LIMIT 1",
                        (run_id,),
                    ) or {}
                ).get("id"),
                role_timeout_seconds=revision_timeout_seconds,
            )
            revision_node = self.db.fetchone(
                "SELECT id, status FROM run_execution_nodes WHERE run_id = ? AND node_key = 'revision' ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            )
            if isinstance(revised, DevelopmentSynthesisDraft):
                revised_draft, revised_provenance_warnings, revised_provenance_audit = (
                    reconcile_development_pathogen_provenance(
                        revised, specialist_results
                    )
                )
                warnings.extend(revised_provenance_warnings)
                revised_draft = await resolve_development_draft_taxonomy(
                    revised_draft, self.taxonomy_resolver
                )
                revised_validation = validate_development_top5(
                    revised_draft,
                    valid_fragment_ids=fragment_ids,
                    require_taxonomy_resolution=True,
                ).model_copy(update={"attempt_origin": "revision_draft"})
                if revision_node:
                    self.trace.add_artifact(
                        run_id,
                        revision_node["id"],
                        "output",
                        "revision_provenance_reconciliation",
                        revised_provenance_audit,
                    )
                    self.trace.add_artifact(
                        run_id,
                        revision_node["id"],
                        "output",
                        "revision_contract_validation",
                        revised_validation.model_dump(mode="json"),
                    )
                if revised_validation.valid:
                    current_draft = revised_draft
                    validation = revised_validation
                    revision_completed_and_adopted = True
                elif prior_validation.valid:
                    retention_warning = "revision_rejected_retained_prior_valid_draft"
                    if fallback_mode != "agent_pool_fallback":
                        fallback_mode = retention_warning
                    warnings.append(retention_warning)
                    current_draft = prior_draft
                    validation = prior_validation
                    if revision_node:
                        self.trace.add_artifact(
                            run_id,
                            revision_node["id"],
                            "output",
                            "revision_transaction_decision",
                            {
                                "decision": fallback_mode,
                                "reason": "revised_draft_failed_deterministic_contract",
                                "prior_draft_sha256": sha256_json(prior_draft.model_dump(mode="json")),
                                "revised_draft_sha256": sha256_json(revised_draft.model_dump(mode="json")),
                                "prior_validation": prior_validation.model_dump(mode="json"),
                                "revised_validation": revised_validation.model_dump(mode="json"),
                                "critic_issue_codes": [
                                    item.code for item in revision_critic.issues
                                ],
                            },
                        )
                        # The provider call completed, but its proposed revision
                        # did not pass the business contract.  Keep that
                        # distinction truthful in the public execution trace.
                        self.db.execute(
                            "UPDATE run_execution_nodes SET outcome = 'warning' WHERE id = ? AND status = 'completed'",
                            (revision_node["id"],),
                        )
                else:
                    current_draft = revised_draft
                    validation = revised_validation
            else:
                warnings.append("revision_technical_failure")
                if any(
                    item.get("error_code") == "development_agent_role_timeout"
                    for item in revision_attempts
                ):
                    warnings.append("revision_role_timeout")
                if prior_validation.valid:
                    retention_warning = "revision_rejected_retained_prior_valid_draft"
                    if fallback_mode != "agent_pool_fallback":
                        fallback_mode = retention_warning
                    warnings.append(retention_warning)
                    current_draft = prior_draft
                    validation = prior_validation
                    if revision_node:
                        self.trace.add_artifact(
                            run_id,
                            revision_node["id"],
                            "output",
                            "revision_transaction_decision",
                            {
                                "decision": fallback_mode,
                                "reason": "revision_agent_technical_failure",
                                "prior_draft_sha256": sha256_json(prior_draft.model_dump(mode="json")),
                                "prior_validation": prior_validation.model_dump(mode="json"),
                                "critic_issue_codes": [
                                    item.code for item in revision_critic.issues
                                ],
                            },
                        )
        else:
            self.trace.skip(
                run_id,
                "revision",
                "llm_agent",
                "总诊修订不需要",
                "Synthesis revision not required",
                "critic_and_contract_accepted",
                role="single_pass_synthesis_revision",
                version="owlpath.development-synthesis.v1",
                outcome="not_applicable",
            )

        if not validation.valid:
            fallback_draft, fallback_pool_audit = (
                await build_resolved_agent_pool_fallback(
                    specialist_results,
                    current_draft,
                    self.taxonomy_resolver,
                    valid_fragment_ids=fragment_ids,
                )
            )
            fallback_provenance_warnings: List[str] = []
            fallback_provenance_audit = _agent_pool_fallback_provenance_audit(
                fallback_pool_audit
            )
            warnings.extend(fallback_provenance_warnings)
            warnings.extend(fallback_draft.warnings)
            fallback_validation = validate_development_top5(
                fallback_draft,
                valid_fragment_ids=fragment_ids,
                require_taxonomy_resolution=True,
            ).model_copy(update={
                "attempt_origin": "post_revision_agent_pool_fallback",
            })
            self.trace.add_artifact(
                run_id,
                validator_node_id,
                "output",
                "agent_pool_fallback_provenance_reconciliation",
                fallback_provenance_audit,
            )
            self.trace.add_artifact(
                run_id,
                validator_node_id,
                "output",
                "agent_pool_fallback_validation",
                {
                    "validation": fallback_validation.model_dump(mode="json"),
                    "agent_pool_fallback_audit": fallback_pool_audit,
                },
            )
            # The fallback is the final publication attempt, whether it passes
            # or fails.  Keep the public validation aligned with this exact
            # attempt so an honest underfill is not obscured by stale issues
            # from the synthesis/revision draft that preceded it.
            validation = fallback_validation
            if fallback_validation.valid:
                current_draft = fallback_draft
                fallback_mode = "agent_pool_fallback"
                warnings.append("agent_pool_fallback")

        if not validation.valid:
            self.trace.skip(
                run_id,
                "candidate_evidence_enrichment",
                "tool_agent",
                "候选特异文献补强已跳过",
                "Candidate-specific literature enrichment skipped",
                "specific_top5_contract_failed",
                role="candidate_specific_literature_enrichment",
                version="owlpath.candidate-evidence.v1",
                outcome="warning",
            )
            result = self._development_technical_result(
                code="specific_top5_contract_failed",
                warnings=warnings,
                observations=observations,
                validation=validation,
                critic=critic_result,
                revision_count=revision_count,
            )
            self._finalize_development_result(
                run_id=run_id,
                row=row,
                result=result,
                parent_node_id=validator_node_id,
            )
            return

        # The broad pre-synthesis search helps the Agents reason, but a final
        # candidate needs a candidate-specific citation rather than a generic
        # syndrome hit.  Reuse already retrieved title-verifiable records and
        # query only uncovered, taxonomy-validated canonical names.
        final_candidate_names = [
            item.canonical_latin_name
            for item in sorted(current_draft.concrete_pathogens, key=lambda item: item.rank)
        ]
        initial_citations = [
            item for item in (retrieval_payload.get("citations") or [])
            if isinstance(item, dict)
        ]
        initial_mapping = map_candidate_specific_citations(
            final_candidate_names, initial_citations
        )
        uncovered_names = [
            name for name in final_candidate_names if not initial_mapping.get(name)
        ]
        targeted_queries = build_candidate_retrieval_queries(uncovered_names)
        enrichment_parent = (
            self.db.fetchone(
                "SELECT id FROM run_execution_nodes WHERE run_id = ? AND node_key IN ('revision', 'critic') ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ) or {}
        ).get("id") or validator_node_id
        enrichment_node_id = self.trace.start(
            run_id,
            "candidate_evidence_enrichment",
            "tool_agent",
            "候选特异文献补强",
            "Candidate-specific literature enrichment",
            input_artifact=("candidate_evidence_query_attestation", {
                "validated_candidate_count": len(final_candidate_names),
                "targeted_query_count": len(targeted_queries),
                "query_sha256": [
                    hashlib.sha256(query.encode("utf-8")).hexdigest()
                    for _, query in targeted_queries
                ],
                "query_basis": "validated_canonical_taxonomy_names_only",
                "raw_case_text_sent": False,
                "personal_identifiers_sent": False,
                "search_query_text_omitted": True,
            }),
            parent_node_id=enrichment_parent,
            role="candidate_specific_literature_enrichment",
            version="owlpath.candidate-evidence.v1",
            metadata={"tools": ["Europe PMC REST", "NCBI PubMed E-utilities"]},
        )
        targeted_citations: List[Dict[str, Any]] = []
        targeted_warnings: List[str] = []
        if uncovered_names:
            enrichment_timeout_seconds = _development_stage_timeout(
                DEVELOPMENT_EVIDENCE_ENRICHMENT_TIMEOUT_SECONDS,
                hard_deadline_monotonic,
            )
            try:
                if enrichment_timeout_seconds <= 0:
                    raise asyncio.TimeoutError
                targeted_bundle = await asyncio.wait_for(
                    retrieve_candidate_evidence(
                        self.medical_retriever, uncovered_names
                    ),
                    timeout=enrichment_timeout_seconds,
                )
                targeted_payload = targeted_bundle.public_payload()
                targeted_citations = list(targeted_bundle.citations)
                targeted_warnings = list(targeted_bundle.warnings)
            except asyncio.TimeoutError:
                targeted_payload = {
                    "citations": [],
                    "candidate_coverage": [],
                    "warnings": ["candidate_evidence_enrichment_timeout"],
                    "source_status": {
                        "europe_pmc": "timed_out", "pubmed": "timed_out",
                    },
                    "retrieval_partial": True,
                    "unrelated_citation_count": 0,
                    "raw_case_text_sent": False,
                    "search_query_text_omitted": True,
                }
                targeted_warnings = ["candidate_evidence_enrichment_timeout"]
        else:
            targeted_payload = {
                "citations": [],
                "candidate_coverage": [],
                "warnings": [],
                "source_status": {
                    "europe_pmc": "not_needed", "pubmed": "not_needed",
                },
                "retrieval_partial": False,
                "unrelated_citation_count": 0,
                "raw_case_text_sent": False,
                "search_query_text_omitted": True,
            }

        combined_citations = [*initial_citations, *targeted_citations]
        final_mapping = map_candidate_specific_citations(
            final_candidate_names, combined_citations
        )
        current_draft = _attach_candidate_specific_evidence(
            current_draft, final_mapping
        )

        # The development result contract limits the identifiers placed in an
        # individual evidence link.  Build the public evidence registry from
        # those identifiers after enrichment, so every visible publication is
        # actually referenced by a visible Top-5 candidate (and vice versa).
        published_mapping: Dict[str, List[str]] = {}
        for candidate in current_draft.concrete_pathogens:
            published_mapping[candidate.canonical_latin_name] = list(dict.fromkeys(
                source_id
                for link in [
                    *candidate.supporting_evidence,
                    *candidate.opposing_evidence,
                ]
                for source_id in link.evidence_source_ids
                if source_id
            ))
        missing_evidence_names = [
            name for name in final_candidate_names if not published_mapping.get(name)
        ]
        if missing_evidence_names:
            targeted_warnings.append("candidate_specific_evidence_coverage_partial")
        warnings.extend(targeted_warnings)
        # Publish only literature that is deterministically bound to at least
        # one final Top-5 candidate.  The earlier broad retrieval may contain
        # useful material for the specialist/synthesis agents, but unrelated
        # search hits must not appear beside the final ranked pathogens as if
        # they supported the released development result.
        published_evidence_source_ids = {
            source_id
            for source_ids in published_mapping.values()
            for source_id in source_ids
        }
        bound_citations = [
            item
            for item in combined_citations
            if str(item.get("citation_id") or item.get("source_id") or "").strip()
            in published_evidence_source_ids
        ]
        evidence_sources = _retrieval_evidence_sources({"citations": bound_citations})
        combined_evidence_source_ids = {
            str(item.get("citation_id") or item.get("source_id") or "").strip()
            for item in combined_citations
            if str(item.get("citation_id") or item.get("source_id") or "").strip()
        }
        enrichment_payload = {
            **targeted_payload,
            "candidate_coverage": [
                {
                    "canonical_latin_name": name,
                    "evidence_source_ids": published_mapping.get(name, []),
                    "covered": bool(published_mapping.get(name)),
                }
                for name in final_candidate_names
            ],
            "coverage_count": len(final_candidate_names) - len(missing_evidence_names),
            "candidate_count": len(final_candidate_names),
            "published_evidence_source_count": len(evidence_sources),
            "unbound_broad_source_count": len(
                combined_evidence_source_ids - published_evidence_source_ids
            ),
            "warnings": list(dict.fromkeys(targeted_warnings)),
        }
        self.trace.complete(
            run_id,
            enrichment_node_id,
            output_artifact=("candidate_specific_evidence_metadata", enrichment_payload),
            outcome="warning" if missing_evidence_names or targeted_payload.get("retrieval_partial") else "passed",
        )

        concrete = [
            DevelopmentConcretePathogen.model_validate(item.model_dump(mode="json"))
            for item in sorted(current_draft.concrete_pathogens, key=lambda item: item.rank)
        ]
        warnings = list(dict.fromkeys(warnings))
        if revision_completed_and_adopted:
            review_status = "revision_completed_not_re_reviewed"
        elif critic_result is None:
            review_status = "critic_unavailable"
        elif critic_result.accepted and not critic_result.revision_required:
            review_status = "critic_accepted"
        else:
            # The critic requested changes, but no revised draft that satisfies
            # the release contract was adopted.  The result may still be
            # contract-valid (for example because a prior valid draft was
            # retained), but it is not an independently approved final draft.
            review_status = "critic_changes_not_closed"

        result = DevelopmentResultV3(
            status="completed_with_warnings" if warnings else "completed",
            summary_i18n=current_draft.summary_i18n,
            concrete_pathogens=concrete,
            category_overview=current_draft.category_overview,
            unknown_score=current_draft.unknown_score,
            coinfection_hypotheses=current_draft.coinfection_hypotheses,
            next_tests=current_draft.next_tests,
            evidence_sources=evidence_sources,
            agent_observations=observations,
            warnings=warnings,
            review=DevelopmentReviewSummary(
                accepted=True,
                status=review_status,
                revision_count=revision_count,
                deterministic_validation=validation,
                critic=critic_result,
            ),
            fallback_mode=fallback_mode,
        )
        self._finalize_development_result(
            run_id=run_id,
            row=row,
            result=result,
            parent_node_id=enrichment_node_id,
        )

    def _record_baseline(self, run_id: str, snapshot: Dict[str, Any]) -> Tuple[ModelPrediction, ModelContribution, float, bool]:
        output_id = new_id("out")
        started = time.perf_counter()
        baseline_node_id = self.trace.start(
            run_id,
            "baseline",
            "rule_model",
            "透明规则基线执行",
            "Transparent rule baseline",
            input_artifact=("baseline_request_envelope", {
                "provider_id": BASELINE_ID,
                "input_snapshot_sha256": sha256_json(snapshot),
                "algorithm": "owlpath-baseline-v1",
            }),
            provider_id=BASELINE_ID,
            provider_model="owlpath-baseline-v1",
            metadata={"deterministic": True, "external_call": False},
        )
        self.emit(run_id, "model_started", {"provider_id": BASELINE_ID, "provider_name": BASELINE_NAME})
        sanitizer_node_id: Optional[str] = None
        try:
            raw_prediction = predict_baseline(snapshot)
            baseline_prediction_hash = sha256_json(raw_prediction.model_dump(mode="json"))
            self.trace.complete(
                run_id,
                baseline_node_id,
                output_artifact=("baseline_response_attestation", {
                    "schema_valid": True,
                    "normalized_response_sha256": baseline_prediction_hash,
                }),
            )
            sanitizer_node_id = self.trace.start(
                run_id,
                "sanitizer:baseline",
                "sanitizer",
                "基线输出脱敏与标准化",
                "Baseline output sanitizer",
                input_artifact=("baseline_response_reference", {
                    "normalized_response_sha256": baseline_prediction_hash,
                }),
                parent_node_id=baseline_node_id,
                provider_id=BASELINE_ID,
                provider_model="owlpath-baseline-v1",
            )
            prediction = sanitize_prediction_for_snapshot(raw_prediction, snapshot)
            self.trace.complete(
                run_id,
                sanitizer_node_id,
                output_artifact=("sanitized_model_prediction", prediction.model_dump(mode="json")),
            )
        except Exception:
            error = {
                "code": "baseline_internal_error",
                "message": "Transparent baseline failed unexpectedly",
                "retryable": False,
            }
            target_node = sanitizer_node_id or baseline_node_id
            target_row = self.db.fetchone("SELECT status FROM run_execution_nodes WHERE id = ?", (target_node,))
            if target_row and target_row["status"] == "running":
                self.trace.fail(run_id, target_node, error)
            raise
        latency = int((time.perf_counter() - started) * 1000)
        now = utc_now().isoformat()
        self.db.execute(
            """INSERT INTO run_model_outputs
               (id, run_id, node_run_id, provider_id, provider_name, provider_kind, provider_model, base_url_origin,
                provider_weight, data_boundary, model_fingerprint, status, normalized_json, latency_ms,
                created_at, completed_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)""",
            (output_id, run_id, baseline_node_id, BASELINE_ID, BASELINE_NAME, "transparent_rule", "owlpath-baseline-v1", None,
             0.5, DataBoundary.LOCAL.value, "baseline:owlpath-baseline-v1",
             json_dumps(prediction.model_dump(mode="json")), latency, now, now),
        )
        self.emit(run_id, "model_completed", {"provider_id": BASELINE_ID, "provider_name": BASELINE_NAME, "latency_ms": latency})
        return prediction, ModelContribution(
            provider_id=BASELINE_ID, provider_name=BASELINE_NAME, status="completed",
            provider_kind="transparent_rule", model="owlpath-baseline-v1", weight=0.5,
            data_boundary=DataBoundary.LOCAL, model_fingerprint="baseline:owlpath-baseline-v1", latency_ms=latency,
        ), 0.5, False

    async def process_run(self, run_id: str) -> None:
        row = self.db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None or row["status"] != "queued":
            return
        try:
            snapshot = json_loads(row["input_snapshot_json"], {})
            provider_ids = json_loads(row["provider_ids_json"], [])
            frozen_configs = json_loads(row.get("provider_configs_json"), [])
            frozen_governance = json_loads(row.get("governance_config_json"), {})
            review_record = json_loads(row.get("clinical_review_json"), {})
            transfer_record = json_loads(row.get("data_transfer_consent_json")) if row.get("data_transfer_consent_json") else None
            development_demo = (row.get("run_mode") == "development_demo")
            expected_execution_graph_version = (
                DEVELOPMENT_EXECUTION_GRAPH_VERSION if development_demo else EXECUTION_GRAPH_VERSION
            )
            expected_trace_version = DEVELOPMENT_TRACE_VERSION if development_demo else TRACE_VERSION

            execution_manifest = build_execution_manifest(
                provider_ids=provider_ids,
                include_baseline=bool(row["include_baseline"]),
                development_demo=development_demo,
                development_source_text=(
                    str(snapshot.get("synthetic_source_text") or "")
                    if development_demo
                    else ""
                ),
                development_specialist_config_version=(
                    str(
                        snapshot.get("specialist_config_version")
                        or "owlpath.development-agents.v3"
                    )
                    if development_demo
                    else "owlpath.development-agents.v3"
                ),
            )
            execution_manifest_hash = sha256_json(execution_manifest)
            if not row.get("execution_manifest_json"):
                self.db.execute(
                    """UPDATE runs SET execution_graph_version = ?, execution_manifest_json = ?,
                       execution_manifest_sha256 = ?, trace_version = ? WHERE id = ?""",
                    (expected_execution_graph_version, json_dumps(execution_manifest), execution_manifest_hash,
                     expected_trace_version, run_id),
                )
                row.update({
                    "execution_graph_version": expected_execution_graph_version,
                    "execution_manifest_json": json_dumps(execution_manifest),
                    "execution_manifest_sha256": execution_manifest_hash,
                    "trace_version": expected_trace_version,
                })

            snapshot_node_id = self.trace.start(
                run_id,
                "snapshot",
                "deterministic_processor",
                "决策时点快照加载",
                "Decision-time snapshot load",
                input_artifact=("snapshot_reference", {
                    "input_snapshot_sha256": row.get("input_snapshot_sha256"),
                    "decision_time": row["decision_time"],
                    "run_mode": row.get("run_mode") or "live",
                }),
                metadata={
                    "raw_source_exposed": development_demo,
                    "raw_source_visibility": "demo_safe" if development_demo else "omitted",
                },
            )
            if development_demo and isinstance(snapshot.get("synthetic_source_text"), str):
                self.trace.add_artifact(
                    run_id,
                    snapshot_node_id,
                    "input",
                    "synthetic_demo_source",
                    {
                        "development_demo": True,
                        "synthetic_only": True,
                        "not_for_clinical_use": True,
                        "synthetic_source_text": snapshot["synthetic_source_text"],
                        "synthetic_source_text_sha256": snapshot.get("synthetic_source_text_sha256"),
                    },
                    visibility="demo_safe",
                )
            self.trace.complete(
                run_id,
                snapshot_node_id,
                output_artifact=("trace_safe_input_snapshot", trace_safe_snapshot(snapshot)),
            )

            # Integrity and consent are re-verified before status changes,
            # provider rows are opened, or any model receives patient data.
            preflight_node_id = self.trace.start(
                run_id,
                "preflight",
                "policy_guard",
                "完整性与授权预检",
                "Integrity and authorization preflight",
                input_artifact=("preflight_hash_manifest", {
                    "input_snapshot_sha256": row.get("input_snapshot_sha256"),
                    "provider_configs_sha256": row.get("provider_configs_sha256"),
                    "governance_config_sha256": row.get("governance_config_sha256"),
                    "run_manifest_sha256": row.get("run_manifest_sha256"),
                    "execution_manifest_sha256": row.get("execution_manifest_sha256"),
                    "run_mode": row.get("run_mode") or "live",
                }),
                parent_node_id=snapshot_node_id,
            )
            snapshot_hash = sha256_json(snapshot)
            provider_configs_hash = sha256_json(frozen_configs)
            governance_config_hash = sha256_json(frozen_governance)
            preflight_errors: List[str] = []
            try:
                stored_execution_manifest = json_loads(row.get("execution_manifest_json"), {})
            except (TypeError, ValueError):
                stored_execution_manifest = {}
                preflight_errors.append("execution_manifest_invalid_json")
            if row.get("execution_graph_version") != expected_execution_graph_version:
                preflight_errors.append("execution_graph_version_mismatch")
            if row.get("trace_version") != expected_trace_version:
                preflight_errors.append("trace_version_mismatch")
            if sha256_json(stored_execution_manifest) != row.get("execution_manifest_sha256"):
                preflight_errors.append("execution_manifest_hash_mismatch")
            if stored_execution_manifest != execution_manifest:
                preflight_errors.append("execution_manifest_content_mismatch")
            if snapshot_hash != row.get("input_snapshot_sha256"):
                preflight_errors.append("input_snapshot_hash_mismatch")
            if development_demo:
                case_row = self.db.fetchone("SELECT data_origin FROM cases WHERE id = ?", (row["case_id"],))
                if not case_row or case_row.get("data_origin") != "synthetic":
                    preflight_errors.append("development_demo_case_not_synthetic")
                synthetic_text = snapshot.get("synthetic_source_text")
                if (
                    snapshot.get("development_demo") is not True
                    or snapshot.get("synthetic_only") is not True
                    or snapshot.get("not_for_clinical_use") is not True
                    or not isinstance(synthetic_text, str)
                    or hashlib.sha256((synthetic_text or "").encode("utf-8")).hexdigest()
                    != snapshot.get("synthetic_source_text_sha256")
                ):
                    preflight_errors.append("development_demo_snapshot_contract_mismatch")
            if row.get("provider_configs_sha256") and provider_configs_hash != row["provider_configs_sha256"]:
                preflight_errors.append("provider_configs_hash_mismatch")
            if row.get("governance_config_sha256") and governance_config_hash != row["governance_config_sha256"]:
                preflight_errors.append("governance_config_hash_mismatch")
            if (
                not development_demo
                and review_record.get("input_snapshot_sha256") != row.get("input_snapshot_sha256")
            ):
                preflight_errors.append("clinical_review_snapshot_mismatch")
            if frozen_configs and [item.get("id") for item in frozen_configs] != provider_ids:
                preflight_errors.append("provider_id_manifest_mismatch")
            external_targets = sorted(
                [provider_transfer_target(item) for item in frozen_configs
                 if item.get("data_boundary") == DataBoundary.EXTERNAL.value],
                key=lambda item: item["provider_id"],
            )
            if external_targets and not development_demo:
                submitted_targets = sorted(
                    (transfer_record or {}).get("provider_targets") or [],
                    key=lambda item: item.get("provider_id", ""),
                )
                if (
                    not transfer_record
                    or not transfer_record.get("accepted")
                    or transfer_record.get("input_snapshot_sha256") != row.get("input_snapshot_sha256")
                    or submitted_targets != external_targets
                ):
                    preflight_errors.append("external_transfer_consent_manifest_mismatch")
            if row.get("run_manifest_sha256"):
                actual_manifest = immutable_run_manifest_hash(
                    case_id=row["case_id"],
                    decision_time=row["decision_time"],
                    run_mode=row.get("run_mode") or "live",
                    retrospective_anchor_id=row.get("retrospective_anchor_id"),
                    provider_ids=provider_ids,
                    include_baseline=bool(row["include_baseline"]),
                    input_snapshot_sha256=row.get("input_snapshot_sha256") or "",
                    provider_configs_sha256=row.get("provider_configs_sha256") or provider_configs_hash,
                    governance_config_sha256=row.get("governance_config_sha256") or governance_config_hash,
                    clinical_review=review_record,
                    data_transfer_consent=transfer_record,
                )
                if actual_manifest != row["run_manifest_sha256"]:
                    preflight_errors.append("run_manifest_hash_mismatch")
            if preflight_errors:
                error = {
                    "code": "run_integrity_failure_before_egress",
                    "message": "Run integrity or consent preflight failed before any model call",
                    "details": sorted(set(preflight_errors)),
                }
                self.trace.fail(run_id, preflight_node_id, error)
                persistence_node_id = self.trace.start(
                    run_id,
                    "persistence",
                    "infrastructure",
                    "失败状态持久化",
                    "Failure-state persistence",
                    input_artifact=("run_failure", error),
                )
                completed = utc_now().isoformat()
                self.db.execute(
                    "UPDATE runs SET status = 'failed', error_json = ?, completed_at = ? WHERE id = ?",
                    (json_dumps(error), completed, run_id),
                )
                self.trace.complete(
                    run_id,
                    persistence_node_id,
                    output_artifact=("persistence_receipt", {
                        "run_id": run_id, "status": "failed", "completed_at": completed,
                    }),
                )
                self.emit(run_id, "failed", error)
                self.db.audit("system", "run.preflight_integrity_failed", "run", run_id, error)
                return

            self.trace.complete(
                run_id,
                preflight_node_id,
                output_artifact=("preflight_result", {
                    "passed": True,
                    "checked_controls": [
                        "input_snapshot_integrity", "provider_config_integrity",
                        "governance_config_integrity", "execution_manifest_integrity",
                        *(["development_demo_synthetic_contract"] if development_demo else [
                            "clinical_review_binding", "external_transfer_consent_binding",
                        ]),
                    ],
                    "development_demo": development_demo,
                    "bypassed_controls": DEVELOPMENT_DEMO_BYPASSED_CONTROLS if development_demo else [],
                }),
                metadata_update={"enforcement_mode": "development_demo" if development_demo else "strict"},
                outcome="demo_bypassed" if development_demo else "passed",
            )

            self.db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
            self.emit(run_id, "running", {"decision_time": row["decision_time"]})
            governance = GovernanceConfig.model_validate(frozen_governance) if frozen_governance else self.db.governance()

            applicability_node_id = self.trace.start(
                run_id,
                "applicability",
                "policy_guard",
                "适用范围判定",
                "Applicability assessment",
                input_artifact=("applicability_context", {
                    "input_snapshot_sha256": snapshot_hash,
                    "governance_version": governance.version,
                    "intended_use": governance.intended_use,
                }),
                parent_node_id=preflight_node_id,
            )
            applicability = scope_violations(snapshot, governance)
            self.trace.complete(
                run_id,
                applicability_node_id,
                output_artifact=("applicability_result", {
                    "applicable": not applicability,
                    "violations": applicability,
                    "development_demo_invocation_bypass": bool(development_demo and applicability),
                }),
                metadata_update={"enforcement_mode": "observe_only" if development_demo else "enforced"},
                outcome=(
                    "demo_bypassed" if development_demo
                    else "blocked" if applicability
                    else "passed"
                ),
            )

            quality_node_id = self.trace.start(
                run_id,
                "input_quality",
                "policy_guard",
                "输入质量评估",
                "Input-quality assessment",
                input_artifact=("quality_context", {"input_snapshot_sha256": snapshot_hash}),
                parent_node_id=preflight_node_id,
            )
            quality_violations = snapshot_quality_violations(snapshot)
            self.trace.complete(
                run_id,
                quality_node_id,
                output_artifact=("input_quality_result", {
                    "passed": not quality_violations,
                    "violations": quality_violations,
                }),
                metadata_update={"enforcement_mode": "observe_only" if development_demo else "safety_input"},
                outcome=(
                    "demo_bypassed" if development_demo
                    else "warning" if quality_violations
                    else "passed"
                ),
            )

            providers: List[Dict[str, Any]] = []
            if frozen_configs:
                for frozen in frozen_configs:
                    provider = dict(frozen)
                    provider_row = self.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider["id"],))
                    provider["encrypted_api_key"] = provider_row["encrypted_api_key"] if provider_row else None
                    providers.append(provider)
            else:
                # Compatibility path for runs created by the earliest research build.
                for provider_id in provider_ids:
                    provider_row = self.db.fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
                    if provider_row is not None and bool(provider_row["enabled"]):
                        providers.append(provider_from_row(provider_row))

            available_provider_ids = {provider["id"] for provider in providers}
            for provider_id in provider_ids:
                if provider_id in available_provider_ids:
                    continue
                self.trace.skip(
                    run_id, "provider:%s" % provider_id, "llm_agent",
                    "Provider 不可用", "Provider unavailable", "provider_not_available",
                    provider_id=provider_id, outcome="blocked",
                )
                self.trace.skip(
                    run_id, "sanitizer:%s" % provider_id, "sanitizer",
                    "Provider 输出处理已跳过", "Provider sanitizer skipped",
                    "provider_not_available", provider_id=provider_id, outcome="blocked",
                )

            if development_demo:
                development_hard_deadline = (
                    time.monotonic() + DEVELOPMENT_HARD_TIMEOUT_SECONDS
                )
                try:
                    await asyncio.wait_for(
                        self._process_development_v3(
                            run_id=run_id,
                            row=row,
                            snapshot=snapshot,
                            providers=providers,
                            applicability=applicability,
                            quality_violations=quality_violations,
                            parent_node_id=preflight_node_id,
                            hard_deadline_monotonic=development_hard_deadline,
                        ),
                        timeout=DEVELOPMENT_HARD_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    timeout_error = {
                        "code": "development_hard_timeout",
                        "message": "Development Agent run exceeded the seven-minute technical limit",
                    }
                    # Idempotent run-level backstop for cancellation points that
                    # occur after a model-output row is created but before the
                    # per-invocation cancellation handler can finish.  Do not
                    # retain exception prose or Provider payloads in this error.
                    cancelled_output_error = {
                        "code": "development_agent_cancelled_due_run_deadline",
                        "message": "Development Agent invocation was cancelled by the run deadline",
                        "retryable": False,
                        "details": {
                            "timeout_phase": "run_hard_timeout_cleanup",
                            "request_dispatched": True,
                        },
                    }
                    self.db.execute_rowcount(
                        """UPDATE run_model_outputs
                           SET status = 'failed', error_json = ?, completed_at = ?
                           WHERE run_id = ? AND status = 'running'""",
                        (
                            json_dumps(cancelled_output_error),
                            utc_now().isoformat(),
                            run_id,
                        ),
                    )
                    for running_node in self.db.fetchall(
                        "SELECT id FROM run_execution_nodes WHERE run_id = ? AND status = 'running'",
                        (run_id,),
                    ):
                        self.trace.fail(run_id, running_node["id"], timeout_error, outcome="warning")
                    timeout_result = self._development_technical_result(
                        code="development_hard_timeout",
                        warnings=[],
                        observations=[],
                    )
                    self._finalize_development_result(
                        run_id=run_id,
                        row=row,
                        result=timeout_result,
                        parent_node_id=preflight_node_id,
                    )
                return

            all_results: List[Tuple[Optional[ModelPrediction], ModelContribution, float, bool]] = []
            # Clinical and retrospective modes fail closed before provider
            # invocation when the case is outside the registered scope.  The
            # separate development-demo surface contains synthetic data only:
            # it still computes the same formal applicability result, but lets
            # the configured real providers run so developers can exercise the
            # complete orchestration path.
            if development_demo or not applicability:
                if bool(row["include_baseline"]):
                    all_results.append(self._record_baseline(run_id, snapshot))
                tasks = [self._invoke_provider(run_id, provider, snapshot) for provider in providers]
                if tasks:
                    all_results.extend(await asyncio.gather(*tasks))
            else:
                if bool(row["include_baseline"]):
                    self.trace.skip(
                        run_id, "baseline", "rule_model", "透明基线已跳过",
                        "Transparent baseline skipped", "applicability_gate",
                        provider_id=BASELINE_ID, provider_model="owlpath-baseline-v1",
                        outcome="blocked",
                    )
                    self.trace.skip(
                        run_id, "sanitizer:baseline", "sanitizer", "基线脱敏已跳过",
                        "Baseline sanitizer skipped", "applicability_gate",
                        provider_id=BASELINE_ID, provider_model="owlpath-baseline-v1",
                        outcome="blocked",
                    )
                for provider in providers:
                    self.trace.skip(
                        run_id, "provider:%s" % provider["id"], "llm_agent",
                        "%s 已跳过" % provider["name"], "%s provider skipped" % provider["name"],
                        "applicability_gate", provider_id=provider["id"], provider_model=provider["model"],
                        outcome="blocked",
                    )
                    self.trace.skip(
                        run_id, "sanitizer:%s" % provider["id"], "sanitizer",
                        "%s 脱敏已跳过" % provider["name"], "%s sanitizer skipped" % provider["name"],
                        "applicability_gate", provider_id=provider["id"], provider_model=provider["model"],
                        outcome="blocked",
                    )
            successes: List[Tuple[str, str, float, bool, ModelPrediction]] = []
            contributions: List[ModelContribution] = []
            for prediction, contribution, weight, external in all_results:
                contributions.append(contribution)
                if prediction is not None:
                    successes.append((contribution.provider_id, contribution.provider_name, weight, external, prediction))

            aggregator_input = {
                "input_snapshot_sha256": row["input_snapshot_sha256"] or snapshot_hash,
                "normalized_predictions": [
                    {
                        "provider_id": provider_id,
                        "provider_name": provider_name,
                        "weight": weight,
                        "external": external,
                        "normalized_prediction_sha256": sha256_json(prediction.model_dump(mode="json")),
                    }
                    for provider_id, provider_name, weight, external, prediction in successes
                ],
                "contributions": [item.model_dump(mode="json") for item in contributions],
                "applicability_violations": applicability,
                "input_quality_violations": quality_violations,
            }
            aggregator_node_id = self.trace.start(
                run_id,
                "aggregator",
                "aggregator",
                "多模型融合",
                "Multi-model aggregation",
                input_artifact=("aggregation_inputs", aggregator_input),
                metadata={
                    "implementation": "aggregate_raw_predictions",
                    "includes_safety_adjudication": False,
                    "algorithm_split": True,
                },
            )
            aggregation_draft = aggregate_raw_predictions(
                run_id=run_id,
                decision_time=datetime.fromisoformat(row["decision_time"]),
                successes=successes,
                contributions=contributions,
                governance=governance,
                input_snapshot_sha256=row["input_snapshot_sha256"] or sha256_json(snapshot),
                applicability_violations=applicability,
                input_quality_violations=quality_violations,
                development_demo=development_demo,
            )
            aggregate_digest = self.trace.complete(
                run_id,
                aggregator_node_id,
                output_artifact=("aggregation_draft", aggregation_draft.to_trace_payload()),
                outcome="passed" if successes else "warning",
            )

            safety_node_id = self.trace.start(
                run_id,
                "safety",
                "safety_adjudicator",
                "安全裁决",
                "Release safety adjudication",
                input_artifact=("aggregation_draft_reference", {
                    "aggregation_draft_sha256": aggregate_digest,
                    "aggregation_draft_schema_version": aggregation_draft.schema_version,
                }),
                parent_node_id=aggregator_node_id,
                metadata={
                    "algorithm_split": True,
                    "verification_only": False,
                    "implementation": "adjudicate_aggregation",
                },
            )
            result = adjudicate_aggregation(aggregation_draft)
            safety_payload = {
                "safety_action": result.safety_action.value,
                "safety_reasons": result.safety_reasons,
                "safety_conclusion_i18n": result.safety_conclusion_i18n.model_dump(mode="json"),
                "disagreement_score": result.disagreement_score,
                "candidate_count_after_safety": len(result.candidates),
            }
            self.trace.complete(
                run_id,
                safety_node_id,
                output_artifact=("safety_result", safety_payload),
                outcome=(
                    "blocked" if result.safety_action == SafetyAction.ABSTAIN
                    else "warning" if result.safety_action != SafetyAction.SPECIES_SET
                    else "passed"
                ),
            )

            if development_demo:
                demo_node_id = self.trace.start(
                    run_id,
                    "demo_projection",
                    "renderer",
                    "开发演示投影",
                    "Development-demo projection",
                    input_artifact=("safety_result_reference", {
                        "safety_action": result.safety_action.value,
                        "formal_candidate_count": len(result.candidates),
                    }),
                    parent_node_id=safety_node_id,
                )
                self.trace.complete(
                    run_id,
                    demo_node_id,
                    output_artifact=("development_demo_projection", {
                        "present": result.demo_projection is not None,
                        "projection": (
                            result.demo_projection.model_dump(mode="json")
                            if result.demo_projection is not None else None
                        ),
                    }),
                )
            else:
                self.trace.skip(
                    run_id, "demo_projection", "renderer", "开发演示投影不适用",
                    "Development-demo projection not applicable", "not_development_demo",
                )

            bilingual_node_id = self.trace.start(
                run_id,
                "bilingual_renderer",
                "renderer",
                "中英文结果契约渲染",
                "Bilingual result-contract renderer",
                input_artifact=("localized_result_source", {
                    "human_summary_i18n": result.human_summary_i18n.model_dump(mode="json"),
                    "safety_conclusion_i18n": result.safety_conclusion_i18n.model_dump(mode="json"),
                    "candidate_display_names": [
                        item.display_name_i18n.model_dump(mode="json") for item in result.candidates
                    ],
                    "next_tests": [{
                        "test_code": item.test_code,
                        "test_name_i18n": item.test_name_i18n.model_dump(mode="json"),
                        "rationale_i18n": item.rationale_i18n.model_dump(mode="json"),
                    } for item in result.next_tests],
                }),
                parent_node_id=safety_node_id,
                metadata={"translation_provider_call_count": 0},
            )
            # Re-validation is the actual renderer boundary: it converts the
            # internal object into the v2 presentation contract and rejects an
            # invalid localized object without making another model call.
            clinical_terms = load_clinical_terms()
            result = render_bilingual_result(result, clinical_terms)
            localized_items = [
                result.human_summary_i18n,
                result.safety_conclusion_i18n,
                *[item.display_name_i18n for item in result.candidates],
                *[item.test_name_i18n for item in result.next_tests],
                *[item.rationale_i18n for item in result.next_tests],
            ]
            self.trace.complete(
                run_id,
                bilingual_node_id,
                output_artifact=("bilingual_render_result", {
                    "schema_version": result.schema_version,
                    "localized_object_count": len(localized_items),
                    "complete_count": sum(item.status == "complete" for item in localized_items),
                    "partial_count": sum(item.status == "partial" for item in localized_items),
                    "single_call_contract": True,
                    "terminology_schema_version": clinical_terms.get("schema_version"),
                    "terminology_registry_loaded": bool(clinical_terms),
                }),
                outcome="warning" if any(item.status == "partial" for item in localized_items) else "passed",
            )

            unsigned = result.model_dump(mode="json")
            unsigned["result_sha256"] = None
            result_hash = sha256_json(unsigned)
            result = result.model_copy(update={"result_sha256": result_hash})
            self.emit(run_id, "safety_adjudicated", {
                "safety_action": result.safety_action.value,
                "reasons": result.safety_reasons,
                "disagreement_score": result.disagreement_score,
            })
            persistence_node_id = self.trace.start(
                run_id,
                "persistence",
                "infrastructure",
                "结果持久化",
                "Result persistence",
                input_artifact=("result_persistence_request", {
                    "run_id": run_id,
                    "schema_version": result.schema_version,
                    "result_sha256": result_hash,
                }),
                parent_node_id=bilingual_node_id,
            )
            completed = utc_now().isoformat()
            self.db.execute(
                "UPDATE runs SET status = 'completed', result_json = ?, result_sha256 = ?, completed_at = ? WHERE id = ?",
                (json_dumps(result.model_dump(mode="json")), result_hash, completed, run_id),
            )
            self.trace.complete(
                run_id,
                persistence_node_id,
                output_artifact=("persistence_receipt", {
                    "run_id": run_id, "status": "completed", "result_sha256": result_hash,
                    "completed_at": completed,
                }),
            )
            self.emit(run_id, "completed", {"safety_action": result.safety_action.value})
            self.db.audit("system", "run.completed", "run", run_id, {
                "provider_ids": provider_ids,
                "consent_at_run": bool(row["consent_at_run"]),
                "safety_action": result.safety_action.value,
                "run_mode": row.get("run_mode") or "live",
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            error = {"code": "run_internal_error", "message": "Run failed during orchestration"}
            for running_node in self.db.fetchall(
                "SELECT id FROM run_execution_nodes WHERE run_id = ? AND status = 'running'",
                (run_id,),
            ):
                self.trace.fail(run_id, running_node["id"], error)
            completed = utc_now().isoformat()
            self.db.execute(
                "UPDATE runs SET status = 'failed', error_json = ?, completed_at = ? WHERE id = ?",
                (json_dumps(error), completed, run_id),
            )
            self.emit(run_id, "failed", error)
            self.db.audit("system", "run.failed", "run", run_id, error)


def _mean(values: Sequence[Tuple[float, float]], default: float = 0.0) -> float:
    weight = sum(item[1] for item in values)
    if weight <= 0:
        return default
    return sum(item[0] * item[1] for item in values) / weight


def _category_candidates(candidates: List[PathogenCandidate], maximum: int) -> List[PathogenCandidate]:
    grouped: Dict[str, List[PathogenCandidate]] = defaultdict(list)
    for item in candidates:
        grouped[(item.category or "unknown").lower()].append(item)
    result = []
    for category, items in grouped.items():
        probability = 1.0 - math.prod(1.0 - item.probability for item in items)
        result.append(PathogenCandidate(
            canonical_id="category:%s" % category,
            name=category,
            rank_level=RankLevel.CATEGORY,
            category=category,
            probability=min(1.0, probability),
            calibration_status="uncalibrated_model_score",
            evidence_for=[],
            evidence_against=["安全裁决已将物种级结果降级为大类；不能据此选择针对性治疗。"],
        ))
    return sorted(result, key=lambda item: item.probability, reverse=True)[:maximum]


def _prediction_probability_violations(prediction: ModelPrediction) -> List[str]:
    """Validate the declared unconditional marginal-probability semantics.

    Candidate probabilities are treated as marginal probabilities of causal
    pathogens. Unknown is the residual outside the named candidate set, and
    coinfection permits at most one additional expected pathogen in v1.
    """
    reasons: List[str] = []
    unique_candidates: Dict[str, float] = {}
    for candidate in prediction.candidates:
        key = candidate.canonical_id.strip().lower()
        unique_candidates[key] = max(unique_candidates.get(key, 0.0), candidate.probability)
    mass = sum(unique_candidates.values()) + prediction.unknown_probability
    if mass > 1.0 + prediction.coinfection_probability + 0.15:
        reasons.append("候选病原边际评分、未知评分与共感染余量不相容。")
    if prediction.coinfection_probability > prediction.infection_probability + 0.05:
        reasons.append("共感染评分高于感染评分，语义不相容。")
    if prediction.syndrome_probabilities:
        syndrome_mass = sum(prediction.syndrome_probabilities.values())
        if syndrome_mass < 0.85 or syndrome_mass > 1.15:
            reasons.append("互斥综合征评分之和未接近 1。")
    return reasons


@dataclass
class AggregationDraft:
    """Policy-neutral numerical fusion output consumed by safety adjudication."""

    schema_version: str
    aggregated_at: datetime
    run_id: str
    decision_time: datetime
    input_snapshot_sha256: str
    successes: List[Tuple[str, str, float, bool, ModelPrediction]]
    contributions: List[ModelContribution]
    governance: GovernanceConfig
    applicability_violations: List[str]
    input_quality_violations: List[str]
    validated_species_calibration: bool
    development_demo: bool
    infection_score: float
    syndrome_scores: Dict[str, float]
    candidates: List[PathogenCandidate]
    coinfection_score: float
    coinfection_pairs: List[CoinfectionPair]
    unknown_score: float
    disagreement_score: float
    disagreement_notes: List[str]
    next_tests: List[NextTestSuggestion]
    limitations: List[str]

    def to_trace_payload(self) -> Dict[str, Any]:
        """Serialize only normalized, sanitizer-approved aggregation data."""
        return {
            "schema_version": self.schema_version,
            "aggregated_at": self.aggregated_at.isoformat(),
            "run_id": self.run_id,
            "decision_time": self.decision_time.isoformat(),
            "input_snapshot_sha256": self.input_snapshot_sha256,
            "governance_version": self.governance.version,
            "successful_model_count": len(self.successes),
            "successful_nonbaseline_model_count": len([
                item for item in self.successes if item[0] != BASELINE_ID
            ]),
            "infection_score": self.infection_score,
            "syndrome_scores": self.syndrome_scores,
            "candidates": [item.model_dump(mode="json") for item in self.candidates],
            "coinfection_score": self.coinfection_score,
            "coinfection_pairs": [item.model_dump(mode="json") for item in self.coinfection_pairs],
            "unknown_score": self.unknown_score,
            "disagreement_score": self.disagreement_score,
            "disagreement_notes": self.disagreement_notes,
            "next_tests": [item.model_dump(mode="json") for item in self.next_tests],
            "model_contributions": [item.model_dump(mode="json") for item in self.contributions],
            "applicability_violations": self.applicability_violations,
            "input_quality_violations": self.input_quality_violations,
            "validated_species_calibration": self.validated_species_calibration,
            "development_demo": self.development_demo,
            "limitations": self.limitations,
            "safety_action": None,
            "safety_not_yet_adjudicated": True,
        }


def aggregate_raw_predictions(
    run_id: str,
    decision_time: datetime,
    successes: List[Tuple[str, str, float, bool, ModelPrediction]],
    contributions: List[ModelContribution],
    governance: GovernanceConfig,
    input_snapshot_sha256: str,
    applicability_violations: Optional[List[str]] = None,
    input_quality_violations: Optional[List[str]] = None,
    validated_species_calibration: bool = False,
    development_demo: bool = False,
) -> AggregationDraft:
    """Fuse normalized model outputs without making a release/safety decision."""
    limitations = [
        governance.disclaimer,
        "候选分数是异构模型估计的加权融合，除非明确标注 calibrated，否则不是经前瞻验证的真实概率。",
        "输出不能替代微生物学确证、抗菌药敏结果、感染科/临床微生物专家判断或当地指南。",
        "不得据此自主启动、停用或更改抗感染治疗。",
        "当前在线引擎未接入经离线发布评审的物种级校准制品，因此运行时物种级状态保持锁定。"
        if not validated_species_calibration else "物种级校准制品已由离线发布流程验证并冻结。",
    ]
    applicability = list(applicability_violations or [])
    input_quality = list(input_quality_violations or [])
    normalized_decision_time = as_utc(decision_time)
    aggregated_at = utc_now()
    if not successes:
        disagreement_notes = [
            "真实模型调用未返回可用结构化结果；请查看各模型错误。"
            if development_demo
            else (
                "适用范围预检查已在模型调用前阻断。"
                if applicability else "所有模型均失败或未配置可用模型。"
            )
        ]
        return AggregationDraft(
            schema_version="owlpath.aggregation-draft.v1",
            aggregated_at=aggregated_at,
            run_id=run_id,
            decision_time=normalized_decision_time,
            input_snapshot_sha256=input_snapshot_sha256,
            successes=list(successes),
            contributions=list(contributions),
            governance=governance,
            applicability_violations=applicability,
            input_quality_violations=input_quality,
            validated_species_calibration=validated_species_calibration,
            development_demo=development_demo,
            infection_score=0.0,
            syndrome_scores={},
            candidates=[],
            coinfection_score=0.0,
            coinfection_pairs=[],
            unknown_score=1.0,
            disagreement_score=1.0,
            disagreement_notes=disagreement_notes,
            next_tests=[],
            limitations=limitations,
        )

    total_weight = sum(item[2] for item in successes)
    infection = _mean([(item[4].infection_probability, item[2]) for item in successes])
    unknown = _mean([(item[4].unknown_probability, item[2]) for item in successes])
    coinfection = _mean([(item[4].coinfection_probability, item[2]) for item in successes])

    syndrome_keys = {key for item in successes for key in item[4].syndrome_probabilities}
    syndrome_scores = {
        key: sum(item[4].syndrome_probabilities.get(key, 0.0) * item[2] for item in successes) / total_weight
        for key in syndrome_keys
    }

    candidate_meta: Dict[str, PathogenCandidate] = {}
    candidate_scores: Dict[str, float] = defaultdict(float)
    candidate_calibrations: Dict[str, List[str]] = defaultdict(list)
    evidence_for: Dict[str, List[str]] = defaultdict(list)
    evidence_against: Dict[str, List[str]] = defaultdict(list)
    for provider_id, provider_name, weight, _external, prediction in successes:
        seen: Dict[str, float] = {}
        for candidate in prediction.candidates:
            key = candidate.canonical_id.strip().lower()
            if key not in candidate_meta or candidate.probability > candidate_meta[key].probability:
                candidate_meta[key] = candidate
            seen[key] = max(seen.get(key, 0.0), candidate.probability)
            evidence_for[key].extend("%s: %s" % (provider_name, text) for text in candidate.evidence_for)
            evidence_against[key].extend("%s: %s" % (provider_name, text) for text in candidate.evidence_against)
            if provider_id != BASELINE_ID:
                candidate_calibrations[key].append(candidate.calibration_status)
        for key, score in seen.items():
            candidate_scores[key] += score * weight
    candidates: List[PathogenCandidate] = []
    for key, weighted_sum in candidate_scores.items():
        base = candidate_meta[key]
        nonbaseline_calibrations = candidate_calibrations.get(key, [])
        calibration_status = (
            "calibrated"
            if nonbaseline_calibrations and all(item == "calibrated" for item in nonbaseline_calibrations)
            else (base.calibration_status if len(successes) == 1 else "uncalibrated_model_score")
        )
        candidates.append(base.model_copy(update={
            "probability": min(1.0, weighted_sum / total_weight),
            "calibration_status": calibration_status,
            "evidence_for": list(dict.fromkeys(evidence_for[key]))[:20],
            "evidence_against": list(dict.fromkeys(evidence_against[key]))[:20],
        }))
    candidates.sort(key=lambda item: item.probability, reverse=True)

    top_votes: Dict[str, float] = defaultdict(float)
    for _provider_id, _provider_name, weight, _external, prediction in successes:
        if prediction.candidates:
            top = max(prediction.candidates, key=lambda item: item.probability)
            top_votes[top.canonical_id.strip().lower()] += weight
    vote_disagreement = 1.0 - (max(top_votes.values()) / total_weight if top_votes else 0.0)
    ranges: List[float] = []
    for candidate in candidates[:governance.max_candidates]:
        values = []
        key = candidate.canonical_id.strip().lower()
        for _, _, _, _, prediction in successes:
            lookup = {item.canonical_id.strip().lower(): item.probability for item in prediction.candidates}
            values.append(lookup.get(key, 0.0))
        ranges.append(max(values) - min(values))
    range_disagreement = sum(ranges) / len(ranges) if ranges else 1.0
    disagreement = min(1.0, 0.6 * vote_disagreement + 0.4 * range_disagreement)
    disagreement_notes: List[str] = []
    if vote_disagreement > 0.25:
        disagreement_notes.append("模型的第一候选不一致。")
    if range_disagreement > 0.30:
        disagreement_notes.append("模型对主要候选的分数差异较大。")
    if not disagreement_notes:
        disagreement_notes.append("未发现达到预设阈值的明显模型冲突；这不等同于预测正确。")

    pair_scores: Dict[Tuple[str, ...], List[Tuple[float, float]]] = defaultdict(list)
    pair_rationale: Dict[Tuple[str, ...], str] = {}
    for _, _, weight, _, prediction in successes:
        for pair in prediction.coinfection_pairs:
            key = tuple(sorted(pair.pathogen_ids))
            pair_scores[key].append((pair.probability, weight))
            if pair.rationale:
                pair_rationale[key] = pair.rationale
    pairs = [CoinfectionPair(
        pathogen_ids=list(key), probability=_mean(values), rationale=pair_rationale.get(key)
    ) for key, values in pair_scores.items()]
    pairs.sort(key=lambda item: item.probability, reverse=True)

    test_groups: Dict[str, List[Tuple[NextTestSuggestion, float]]] = defaultdict(list)
    for _, _, weight, _, prediction in successes:
        for test in prediction.next_tests:
            test_groups[test.test_code.strip().lower()].append((test, weight))
    tests: List[NextTestSuggestion] = []
    for grouped in test_groups.values():
        best = max(grouped, key=lambda item: item[0].expected_information_gain)[0]
        tests.append(best.model_copy(update={
            "expected_information_gain": _mean([
                (item.expected_information_gain, weight) for item, weight in grouped
            ])
        }))
    tests.sort(key=lambda item: item.expected_information_gain, reverse=True)

    return AggregationDraft(
        schema_version="owlpath.aggregation-draft.v1",
        aggregated_at=aggregated_at,
        run_id=run_id,
        decision_time=normalized_decision_time,
        input_snapshot_sha256=input_snapshot_sha256,
        successes=list(successes),
        contributions=list(contributions),
        governance=governance,
        applicability_violations=applicability,
        input_quality_violations=input_quality,
        validated_species_calibration=validated_species_calibration,
        development_demo=development_demo,
        infection_score=infection,
        syndrome_scores=syndrome_scores,
        candidates=candidates,
        coinfection_score=coinfection,
        coinfection_pairs=pairs,
        unknown_score=unknown,
        disagreement_score=disagreement,
        disagreement_notes=disagreement_notes,
        next_tests=tests,
        limitations=limitations,
    )


def adjudicate_aggregation(draft: AggregationDraft) -> AggregatedResult:
    """Apply release, degradation, abstention, and demo-projection policy."""
    governance = draft.governance
    successes = draft.successes
    contributions = draft.contributions
    applicability_violations = draft.applicability_violations
    input_quality_violations = draft.input_quality_violations
    development_demo = draft.development_demo
    validated_species_calibration = draft.validated_species_calibration
    infection = draft.infection_score
    unknown = draft.unknown_score
    coinfection = draft.coinfection_score
    candidates = list(draft.candidates)
    pairs = list(draft.coinfection_pairs)
    tests = list(draft.next_tests)
    disagreement = draft.disagreement_score
    disagreement_notes = list(draft.disagreement_notes)

    if not successes:
        reasons = list(applicability_violations)
        if development_demo:
            reasons.append("真实模型调用已发起，但没有模型返回可通过结构校验的结果；请查看模型错误并重试。")
        else:
            reasons.append(
                "适用范围闸门未通过，未调用任何模型并必须转人工。"
                if applicability_violations else "没有可用于裁决的模型输出。"
            )
        return AggregatedResult(
            governance_version=governance.version,
            generated_at=utc_now(),
            input_snapshot_sha256=draft.input_snapshot_sha256,
            run_id=draft.run_id,
            decision_time=draft.decision_time,
            infection_probability=0.0,
            syndrome_probabilities={},
            candidates=[],
            coinfection_probability=0.0,
            coinfection_pairs=[],
            unknown_probability=1.0,
            disagreement_score=1.0,
            disagreement_notes=disagreement_notes,
            safety_action=SafetyAction.ABSTAIN,
            safety_reasons=reasons,
            next_tests=[],
            model_contributions=contributions,
            limitations=draft.limitations,
            development_demo=development_demo,
            demo_projection=DevelopmentDemoProjection(
                infection_probability=0.0,
                unknown_probability=1.0,
                candidates=[],
                coinfection_pairs=[],
                bypassed_controls=DEVELOPMENT_DEMO_BYPASSED_CONTROLS,
                successful_model_count=0,
                applicability_warnings=applicability_violations,
                input_quality_warnings=input_quality_violations,
            ) if development_demo else None,
        )

    safety_reasons: List[str] = []
    nonbaseline = [item for item in successes if item[0] != BASELINE_ID]
    independent_fingerprints = {
        item.model_fingerprint for item in contributions
        if item.status == "completed" and item.provider_id != BASELINE_ID and item.model_fingerprint
    }
    model_quality_warning = any(
        item[0] != BASELINE_ID and bool(item[4].data_quality_warnings) for item in successes
    )
    shift_or_abstain = any(item[4].distribution_shift_warning or item[4].abstain for item in successes)
    probability_violations = [
        "%s：%s" % (provider_name, reason)
        for _, provider_name, _, _, prediction in successes
        for reason in _prediction_probability_violations(prediction)
    ]
    top_probability = candidates[0].probability if candidates else 0.0
    top_species = bool(candidates and candidates[0].rank_level == RankLevel.SPECIES)
    semantic_conflict = top_probability > infection + 0.15 or coinfection > infection + 0.10
    if semantic_conflict:
        disagreement_notes.append("感染评分与病原/共感染评分存在语义冲突，不能解释为经校准概率。")
    if probability_violations:
        disagreement_notes.append("至少一个模型输出违反已声明的联合评分约束，已阻止高粒度发布。")
    low_infection_without_conflict = (
        infection <= governance.non_infection_max_probability
        and disagreement <= governance.max_disagreement
        and not shift_or_abstain
        and unknown < governance.unknown_abstain_threshold
        and not semantic_conflict
    )
    if applicability_violations:
        action = SafetyAction.ABSTAIN
        safety_reasons.extend(applicability_violations)
        safety_reasons.append("适用范围闸门未通过，必须转人工，不能继续解释病原排序。")
    elif input_quality_violations:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.ABSTAIN
        safety_reasons.extend(input_quality_violations)
        safety_reasons.append("输入质量闸门未通过，禁止物种级解释。")
    elif probability_violations:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.ABSTAIN
        safety_reasons.extend(probability_violations[:10])
        safety_reasons.append("模型评分未通过联合一致性校验，不能融合为可发布候选集合。")
    elif low_infection_without_conflict:
        action = SafetyAction.NON_INFECTION
        safety_reasons.append("融合感染评分低于研究版阈值，进入非感染鉴别路径；这不是对感染的排除诊断。")
    elif unknown >= governance.unknown_abstain_threshold:
        action = SafetyAction.ABSTAIN
        safety_reasons.append("未知病原评分达到弃答阈值。")
    elif semantic_conflict:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.ABSTAIN
        safety_reasons.append("感染评分与候选病原评分互相冲突，需要补充信息或人工复核。")
    elif not candidates:
        action = SafetyAction.ABSTAIN
        safety_reasons.append("没有结构化病原候选。")
    elif shift_or_abstain or model_quality_warning:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.ABSTAIN
        safety_reasons.append("至少一个非基线模型提示数据质量问题，或模型提示分布外、资料不足/主动弃答。")
    elif disagreement > governance.max_disagreement:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.CATEGORY_ONLY
        safety_reasons.append("模型分歧超过治理阈值。")
    elif len(nonbaseline) == 0:
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("只有未经临床验证的透明规则基线可用，禁止物种级报告。")
    elif len(independent_fingerprints) < governance.min_independent_nonbaseline_models_for_species:
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("达到物种级输出所需的独立非基线模型数量不足；本地与云端模型按同一规则计数。")
    elif not validated_species_calibration:
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("在线运行未绑定经离线验证并执行的物种级校准制品，禁止物种级报告。")
    elif not governance.species_calibrator_version:
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("治理注册中心未冻结经独立验证的物种级校准器版本，禁止物种级报告。")
    elif not candidates or candidates[0].calibration_status != "calibrated":
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("首位候选未通过冻结校准器标记，禁止把模型分数解释为物种级预测集合。")
    elif top_species and top_probability >= governance.exact_species_min_probability:
        action = SafetyAction.SPECIES_SET
        safety_reasons.append("候选分数和模型数量达到研究版物种集合阈值；仍需临床与微生物学复核。")
    elif top_probability >= governance.category_min_probability:
        action = SafetyAction.CATEGORY_ONLY
        safety_reasons.append("证据仅达到病原大类报告阈值。")
    else:
        action = SafetyAction.NEXT_TEST if tests else SafetyAction.ABSTAIN
        safety_reasons.append("候选分数不足，优先补充信息。")

    reportable = candidates[:governance.max_candidates]
    demo_candidate_ids = {item.canonical_id for item in candidates[:governance.max_candidates]}
    demo_coinfection_pairs = [
        pair.model_copy(update={"rationale": None}) for pair in pairs
        if all(item in demo_candidate_ids for item in pair.pathogen_ids)
    ][:5]
    if action in {SafetyAction.CATEGORY_ONLY, SafetyAction.NEXT_TEST}:
        reportable = _category_candidates(reportable, governance.max_candidates)
        pairs = []
    elif action in {SafetyAction.ABSTAIN, SafetyAction.NON_INFECTION}:
        reportable = []
        pairs = []

    demo_projection = None
    if development_demo:
        demo_candidates = [candidate.model_copy(update={
            "calibration_status": "uncalibrated_model_score",
        }) for candidate in candidates[:governance.max_candidates]]
        demo_projection = DevelopmentDemoProjection(
            infection_probability=min(1.0, infection),
            unknown_probability=min(1.0, unknown),
            candidates=demo_candidates,
            coinfection_pairs=demo_coinfection_pairs,
            bypassed_controls=DEVELOPMENT_DEMO_BYPASSED_CONTROLS,
            successful_model_count=len(nonbaseline),
            applicability_warnings=applicability_violations,
            input_quality_warnings=input_quality_violations,
        )

    return AggregatedResult(
        governance_version=governance.version,
        generated_at=utc_now(),
        input_snapshot_sha256=draft.input_snapshot_sha256,
        run_id=draft.run_id,
        decision_time=draft.decision_time,
        infection_probability=min(1.0, infection),
        syndrome_probabilities=dict(sorted(draft.syndrome_scores.items(), key=lambda item: item[1], reverse=True)),
        candidates=reportable,
        coinfection_probability=min(1.0, coinfection),
        coinfection_pairs=pairs[:5],
        unknown_probability=min(1.0, unknown),
        disagreement_score=disagreement,
        disagreement_notes=disagreement_notes,
        safety_action=action,
        safety_reasons=safety_reasons,
        next_tests=tests[:5],
        model_contributions=contributions,
        limitations=draft.limitations,
        development_demo=development_demo,
        demo_projection=demo_projection,
    )


def aggregate_predictions(
    run_id: str,
    decision_time: datetime,
    successes: List[Tuple[str, str, float, bool, ModelPrediction]],
    contributions: List[ModelContribution],
    governance: GovernanceConfig,
    input_snapshot_sha256: str,
    applicability_violations: Optional[List[str]] = None,
    input_quality_violations: Optional[List[str]] = None,
    validated_species_calibration: bool = False,
    development_demo: bool = False,
) -> AggregatedResult:
    """Backward-compatible wrapper around the two real execution stages."""
    draft = aggregate_raw_predictions(
        run_id=run_id,
        decision_time=decision_time,
        successes=successes,
        contributions=contributions,
        governance=governance,
        input_snapshot_sha256=input_snapshot_sha256,
        applicability_violations=applicability_violations,
        input_quality_violations=input_quality_violations,
        validated_species_calibration=validated_species_calibration,
        development_demo=development_demo,
    )
    return adjudicate_aggregation(draft)
