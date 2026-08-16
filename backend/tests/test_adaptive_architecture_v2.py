from __future__ import annotations

from collections import defaultdict, deque

from app.engine import (
    DEVELOPMENT_CORE_SPECIALIST_ROLES,
    DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES,
    DEVELOPMENT_ROLE_EVIDENCE_DOMAINS,
    _retrieval_evidence_sources,
    build_development_execution_manifest,
    select_dynamic_development_roles,
)
from app.models import DevelopmentAgentRole, DevelopmentSpecialistRole
from app.providers import _SPECIALIST_ROLE_FOCUS


EXPECTED_CORE_ROLES = [
    "infectious_diseases",
    "critical_care_emergency",
    "clinical_epidemiology",
    "laboratory_medicine",
    "clinical_microbiology_culture",
]

EXPECTED_DYNAMIC_ROLES = [
    "radiology", "pulmonology", "gastroenterology", "hepatobiliary_pancreatic",
    "urology", "nephrology", "neurology_neuroinfection", "cardiology_endocarditis",
    "hematology_immunology", "transplant_infectious_diseases", "surgery_source_control",
    "orthopedics_bone_joint", "dermatology_soft_tissue", "obstetrics_gynecology",
    "pediatrics_neonatology", "tropical_medicine_parasitology", "medical_mycology",
    "clinical_virology_molecular", "antimicrobial_stewardship",
    "healthcare_device_infection",
]


def test_v3_roster_focus_domains_and_legacy_wire_values_are_complete() -> None:
    assert [item[0] for item in DEVELOPMENT_CORE_SPECIALIST_ROLES] == EXPECTED_CORE_ROLES
    assert [item[0] for item in DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES] == EXPECTED_DYNAMIC_ROLES

    active = [*EXPECTED_CORE_ROLES, *EXPECTED_DYNAMIC_ROLES]
    assert all(_SPECIALIST_ROLE_FOCUS[role].strip() for role in active)
    assert all(
        DevelopmentSpecialistRole(role) in DEVELOPMENT_ROLE_EVIDENCE_DOMAINS
        for role in active
    )
    assert (
        DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[
            DevelopmentSpecialistRole.CLINICAL_MICROBIOLOGY_CULTURE
        ]
        == DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[DevelopmentSpecialistRole.MEDICAL_MYCOLOGY]
        == DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[
            DevelopmentSpecialistRole.CLINICAL_VIROLOGY_MOLECULAR
        ]
    )
    assert (
        DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[DevelopmentSpecialistRole.CLINICAL_EPIDEMIOLOGY]
        == DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[
            DevelopmentSpecialistRole.TROPICAL_MEDICINE_PARASITOLOGY
        ]
    )
    assert (
        DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[DevelopmentSpecialistRole.HEMATOLOGY_IMMUNOLOGY]
        == DEVELOPMENT_ROLE_EVIDENCE_DOMAINS[
            DevelopmentSpecialistRole.TRANSPLANT_INFECTIOUS_DISEASES
        ]
    )

    legacy = [
        "timeline_course", "host_susceptibility", "syndrome_localization",
        "exposure_one_health", "lab_pathophysiology", "organ_severity",
        "imaging_dissemination", "microbiology_treatment", "neuroinfection",
        "immunocompromised_opportunistic", "travel_zoonotic", "healthcare_device_amr",
        "timeline_host", "syndrome_site", "exposure_epidemiology",
        "laboratory_organ_injury", "imaging_microbiology_treatment",
    ]
    assert all(DevelopmentSpecialistRole(role).value == role for role in legacy)
    assert all(DevelopmentAgentRole(role).value == role for role in legacy)
    assert all(_SPECIALIST_ROLE_FOCUS[role].strip() for role in legacy)
    assert not set(legacy).intersection(active)


def test_adaptive_router_is_deterministic_bounded_and_cue_specific() -> None:
    assert select_dynamic_development_roles(
        "成人社区获得性肺炎，无特殊暴露，既往体健。"
    ) == ["pulmonology"]
    assert select_dynamic_development_roles(
        "意识不清，脑脊液已送；清洗虚构淡水景观水池后接触淡水发热。"
    ) == ["neurology_neuroinfection", "tropical_medicine_parasitology"]
    assert select_dynamic_development_roles(
        "肾移植后长期免疫抑制，中心静脉导管，近期住院并使用广谱抗生素。"
    ) == [
        "antimicrobial_stewardship",
        "healthcare_device_infection",
        "transplant_infectious_diseases",
    ]

    all_cues = (
        "脑脊液脑炎；移植化疗免疫抑制；旅行清洗虚构淡水景观水池接触淡水动物蜱蚊；"
        "近期住院导管人工关节呼吸机广谱抗生素。"
    )
    first = select_dynamic_development_roles(all_cues)
    second = select_dynamic_development_roles(all_cues)
    assert first == second
    assert len(first) == 6


def test_adaptive_router_ignores_negated_history_and_post_onset_support_devices() -> None:
    source = (
        "男，51岁。2天前清洗虚构淡水景观水池后乏力，今日意识不清；脑脊液已送检。"
        "近2个月内无住院史，近1个月无广谱抗生素暴露，无旅行史，无动物接触史。"
        "本次发病后因生命体征不稳行气管插管并留置尿管。"
    )

    assert select_dynamic_development_roles(source) == [
        "neurology_neuroinfection",
        "tropical_medicine_parasitology",
    ]
    assert select_dynamic_development_roles(
        "No recent hospitalization, no broad-spectrum antibiotics, and no recent travel."
    ) == []
    assert select_dynamic_development_roles(
        "无咳嗽，无呼吸困难，无肺炎，无胸腔积液。"
    ) == []


def test_graph_v4_freezes_five_core_and_at_most_six_dynamic_roles() -> None:
    multisystem_case = (
        "脑脊液提示脑膜炎并抽搐；胸部CT肺实变、胸腔积液，咳嗽低氧；"
        "右肝肝脓肿合并胆管炎、胆红素升高；尿频尿痛、肾盂肾炎，尿培养待回；"
        "心内膜炎杂音与赘生物。"
    )
    manifest = build_development_execution_manifest(
        ["provider_high", "provider_low"],
        source_text=multisystem_case,
        specialist_config_version="owlpath.development-agents.synthetic-test-v3",
    )

    assert manifest["execution_graph_version"] == "owlpath.execution-graph.v4"
    assert manifest["specialist_config_version"] == (
        "owlpath.development-agents.synthetic-test-v3"
    )
    assert manifest["specialist_runtime_implementation_version"] == (
        "owlpath.development-agents.v3"
    )
    assert manifest["selected_core_roles"] == [
        role for role, _zh, _en in DEVELOPMENT_CORE_SPECIALIST_ROLES
    ]
    assert manifest["selected_dynamic_roles"] == [
        "radiology",
        "urology",
        "pulmonology",
        "hepatobiliary_pancreatic",
        "neurology_neuroinfection",
        "cardiology_endocarditis",
    ]
    assert manifest["limits"]["maximum_dynamic_specialists"] == 6
    assert manifest["limits"]["maximum_selected_specialists"] == 11
    assert manifest["limits"]["maximum_provider_network_requests_per_run"] == 18
    assert manifest["limits"]["specialist_provider_request_ceiling"] == 12

    node_by_key = {node["key"]: node for node in manifest["nodes"]}
    all_roles = [
        role
        for role, _zh, _en in (
            *DEVELOPMENT_CORE_SPECIALIST_ROLES,
            *DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES,
        )
    ]
    assert all("specialist:%s" % role in node_by_key for role in all_roles)
    assert sum(
        bool(node_by_key["specialist:%s" % role]["selected"])
        for role in all_roles
    ) == 11
    assert len(manifest["not_applicable_nodes"]) == 14
    assert all(
        node_by_key[key]["selection_state"] == "not_applicable"
        and node_by_key[key]["selected"] is False
        for key in manifest["not_applicable_nodes"]
    )
    assert manifest["limits"]["normal_llm_calls"] == 13
    assert manifest["limits"]["maximum_llm_calls_with_revision"] == 14

    # The declared non-feedback graph must remain a DAG. This protects the
    # single bounded revision loop from being misrepresented as open-ended.
    indegree = defaultdict(int)
    children = defaultdict(list)
    for edge in manifest["edges"]:
        children[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
        indegree.setdefault(edge["from"], 0)
    queue = deque(key for key in node_by_key if indegree[key] == 0)
    visited = []
    while queue:
        key = queue.popleft()
        visited.append(key)
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    assert set(visited) == set(node_by_key)


def test_search_rank_without_title_overlap_is_not_promoted_to_synthesis_evidence() -> None:
    payload = {
        "citations": [
            {
                "citation_id": "exact",
                "source": "Europe PMC",
                "title": "Vibrio vulnificus sepsis after marine exposure",
                "url": "https://europepmc.org/article/MED/1",
                "relevance_validation": {"status": "title_exact_concept_match"},
            },
            {
                "citation_id": "weak",
                "source": "PubMed",
                "title": "Water exposure and severe sepsis",
                "url": "https://pubmed.ncbi.nlm.nih.gov/2/",
                "relevance_validation": {"status": "title_token_overlap"},
            },
            {
                "citation_id": "rank-only",
                "source": "PubMed",
                "title": "Unrelated basic science record",
                "url": "https://pubmed.ncbi.nlm.nih.gov/3/",
                "relevance_validation": {"status": "unverified_search_rank"},
            },
        ]
    }

    sources = _retrieval_evidence_sources(payload)
    assert [item.evidence_source_id for item in sources] == ["exact", "weak"]
    assert "exact retrieval concept" in sources[0].relevance_i18n.en
    assert "token overlap only" in sources[1].relevance_i18n.en
