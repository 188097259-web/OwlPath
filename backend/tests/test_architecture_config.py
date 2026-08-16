import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def _assert_acyclic(nodes, edges):
    node_ids = {item["id"] for item in nodes}
    incoming = {item: 0 for item in node_ids}
    outgoing = {item: [] for item in node_ids}
    for edge in edges:
        if edge["kind"] == "feedback":
            continue
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]] += 1
    ready = [item for item, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    assert visited == len(node_ids), "non-feedback architecture edges must be acyclic"


def test_architecture_manifest_is_internally_consistent():
    manifest = _load("agent_architecture.v1.json")
    assert manifest["schema_version"] == "owlpath.architecture.v1"
    assert set(manifest["views"]) == {"current", "target"}
    allowed_maturity = {"implemented", "partial", "planned"}
    allowed_planes = {"governance", "online", "offline"}
    allowed_edges = {"data", "control", "external", "feedback", "planned"}

    for view in manifest["views"].values():
        nodes = view["nodes"]
        ids = [item["id"] for item in nodes]
        assert len(ids) == len(set(ids))
        assert {item["plane"] for item in nodes} <= allowed_planes
        assert {item["maturity"] for item in nodes} <= allowed_maturity
        assert all(item["name"]["zh_cn"] and item["name"]["en"] for item in nodes)
        assert all(item["description"]["zh_cn"] and item["description"]["en"] for item in nodes)
        for edge in view["edges"]:
            assert edge["source"] in ids
            assert edge["target"] in ids
            assert edge["kind"] in allowed_edges
        _assert_acyclic(nodes, view["edges"])


def test_current_architecture_does_not_mislabel_deterministic_nodes_as_agents():
    current = _load("agent_architecture.v1.json")["views"]["current"]
    kinds = {item["id"]: item["kind"] for item in current["nodes"]}
    for agent_id in (
        "infectious_diseases", "critical_care_emergency", "clinical_epidemiology",
        "laboratory_medicine", "clinical_microbiology_culture", "radiology",
        "pulmonology", "gastroenterology", "hepatobiliary_pancreatic", "urology",
        "nephrology", "neurology_neuroinfection", "cardiology_endocarditis",
        "hematology_immunology", "transplant_infectious_diseases",
        "surgery_source_control", "orthopedics_bone_joint",
        "dermatology_soft_tissue", "obstetrics_gynecology",
        "pediatrics_neonatology", "tropical_medicine_parasitology",
        "medical_mycology", "clinical_virology_molecular",
        "antimicrobial_stewardship", "healthcare_device_infection",
        "synthesis",
        "critic",
        "revision",
    ):
        assert kinds[agent_id] == "llm_agent"
    assert kinds["literature_retrieval"] == "tool_agent"
    assert kinds["public_health_retrieval"] == "tool_agent"
    assert kinds["candidate_evidence_enrichment"] == "tool_agent"
    assert kinds["source_compiler"] == "deterministic_processor"
    assert kinds["complexity_router"] == "deterministic_router"
    assert kinds["evidence_board"] == "deterministic_processor"
    assert kinds["retrieval_planner"] == "deterministic_processor"
    assert kinds["evidence_verifier"] == "deterministic_validator"
    assert kinds["contract_validator"] == "deterministic_validator"
    assert kinds["result_compiler"] == "deterministic_processor"
    assert kinds["persistence"] == "infrastructure"


def test_target_only_capabilities_are_marked_planned():
    target = _load("agent_architecture.v1.json")["views"]["target"]
    maturity = {item["id"]: item["maturity"] for item in target["nodes"]}
    for node_id in (
        "target_router",
        "target_discriminative",
        "target_world_model",
        "target_bayesian_prior",
        "target_report_agent",
        "target_human",
    ):
        assert maturity[node_id] == "planned"


def test_external_clinician_decision_is_not_claimed_as_implemented_software():
    manifest = _load("agent_architecture.v1.json")
    current = manifest["views"]["current"]
    target = manifest["views"]["target"]
    current_ids = {item["id"] for item in current["nodes"]}
    target_nodes = {item["id"]: item for item in target["nodes"]}
    clinician = target_nodes["target_human"]

    assert clinician["kind"] == "human"
    assert clinician["maturity"] == "planned"
    assert "系统之外的人工责任边界" in clinician["description"]["zh_cn"]
    assert "不实现临床复核、签署或最终决策流程" in clinician["description"]["zh_cn"]
    assert "outside the system" in clinician["description"]["en"]
    assert "does not implement clinical review, sign-off or final decision" in clinician["description"]["en"]
    assert "target_human" not in current_ids
    assert {
        "source": "target_bilingual",
        "target": "target_human",
        "kind": "planned",
    } in target["edges"]


def test_planned_target_nodes_do_not_claim_runtime_results():
    manifest = _load("agent_architecture.v1.json")
    current_ids = {item["id"] for item in manifest["views"]["current"]["nodes"]}
    runtime_result_fields = {
        "status",
        "outcome",
        "result",
        "output",
        "latency_ms",
        "attempt",
    }

    for node in manifest["views"]["target"]["nodes"]:
        if node["maturity"] != "planned":
            continue
        assert node["id"] not in current_ids
        assert runtime_result_fields.isdisjoint(node), (
            f"planned node {node['id']} must describe architecture only, not a fabricated run"
        )


def test_critical_care_core_description_names_organ_failure_without_legacy_typo():
    current = _load("agent_architecture.v1.json")["views"]["current"]
    nodes = {item["id"]: item for item in current["nodes"]}
    description = nodes["critical_care_emergency"]["description"]

    assert "器官衰竭" in description["zh_cn"]
    assert "胝肾" not in description["zh_cn"]
    assert "organ failure" in description["en"]


def test_bilingual_term_registry_has_unique_pathogens_and_safe_fallback():
    terms = _load("clinical_terms.zh-en.v1.json")
    pathogens = terms["pathogens"]
    canonical_ids = [item["canonical_id"] for item in pathogens]
    assert len(canonical_ids) == len(set(canonical_ids))
    # A reliable Chinese term is optional by contract.  When it is not in the
    # versioned terminology registry the UI keeps the Latin name and marks the
    # localization partial instead of inventing a translation.
    assert all(item["en"] for item in pathogens)
    assert all("zh_cn" in item for item in pathogens)
    assert all(item["ncbi_scientific_name"] for item in pathogens)
    assert all(
        item["taxonomic_rank"] in {"species", "species_complex", "virus_type"}
        for item in pathogens
    )
    assert all(item["ncbi_taxonomy_rank"] for item in pathogens)
    assert all(item["rank_mapping_rule"] for item in pathogens)
    assert terms["taxonomy_rank_policy"]["schema_version"] == (
        "owlpath.registry-taxonomy-rank-policy.v1"
    )
    assert "never invent" in terms["fallback_policy"].lower()
    assert {"non_infection", "species_set", "category_only", "next_test", "abstain"} == set(terms["safety_states"])
