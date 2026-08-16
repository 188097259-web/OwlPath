import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import json_loads, sha256_json
from app.models import ModelPrediction


def _wait_run(client: TestClient, run_id: str) -> dict:
    for _ in range(150):
        response = client.get("/api/runs/%s" % run_id)
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _create_baseline_run(client: TestClient) -> dict:
    case_response = client.post("/api/cases", json={
        "case_alias": "TRACE-%s" % uuid4().hex[:16].upper(),
        "demographics": {
            "age_years": 68,
            "sex": "male",
            "immunocompromised": False,
            "care_setting": "emergency",
        },
        "context": {
            "primary_syndrome": "respiratory",
            "acquisition_context": "community",
        },
        "external_data_consent": False,
    })
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]
    decision_time = datetime.now(timezone.utc)
    event_response = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom",
        "occurred_at": (decision_time - timedelta(hours=2)).isoformat(),
        "visible_at": (decision_time - timedelta(hours=1)).isoformat(),
        "source": "deidentified-test",
        "status": "final",
        "data": {"symptom": "发热、咳嗽"},
        "quality": {"verified": True},
    })
    assert event_response.status_code == 201, event_response.text
    snapshot_response = client.get(
        "/api/cases/%s/snapshot-hash" % case_id,
        params={"decision_time": decision_time.isoformat()},
    )
    assert snapshot_response.status_code == 200, snapshot_response.text
    review = {
        "accepted": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "statement_version": "owlpath-clinical-review-v1",
        "parser_version": "trace-test-parser-v1",
        "source_text_sha256": hashlib.sha256(b"trace-test-source").hexdigest(),
        "input_snapshot_sha256": snapshot_response.json()["input_snapshot_sha256"],
    }
    run_response = client.post("/api/runs", json={
        "case_id": case_id,
        "decision_time": decision_time.isoformat(),
        "provider_ids": [],
        "include_baseline": True,
        "clinical_review": review,
    })
    assert run_response.status_code == 202, run_response.text
    run = _wait_run(client, run_response.json()["id"])
    assert run["status"] == "completed", run
    return run


def _sse_event_ids(text: str) -> list[int]:
    return [
        int(line.split(":", 1)[1].strip())
        for line in text.splitlines()
        if line.startswith("id:")
    ]


def _all_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _all_mapping_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _all_mapping_keys(child)}
    return set()


def test_architecture_endpoint_serves_versioned_current_and_target_views(
    client: TestClient,
) -> None:
    response = client.get("/api/architecture")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["schema_version"] == "owlpath.architecture.v1"
    assert set(payload["views"]) == {"current", "target"}
    for view in payload["views"].values():
        assert view["title"]["zh_cn"] and view["title"]["en"]
        node_ids = {node["id"] for node in view["nodes"]}
        assert len(node_ids) == len(view["nodes"])
        assert all(node["name"]["zh_cn"] and node["name"]["en"] for node in view["nodes"])
        assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in view["edges"])

    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in rendered
    assert "raw_response" not in rendered


def test_v2_result_accepts_partial_or_legacy_bilingual_text(client: TestClient) -> None:
    run = _create_baseline_run(client)
    row = client.app.state.db.fetchone(
        "SELECT result_json FROM runs WHERE id = ?", (run["id"],),
    )
    assert row is not None
    result = json_loads(row["result_json"], {})
    result["schema_version"] = "owlpath.result.v2"
    result["human_summary_i18n"] = "仅有中文的旧版摘要"
    result["safety_conclusion_i18n"] = {"en": "English-only safety conclusion"}
    result["result_sha256"] = None
    digest = sha256_json(result)
    result["result_sha256"] = digest
    client.app.state.db.execute(
        "UPDATE runs SET schema_version = ?, result_json = ?, result_sha256 = ? WHERE id = ?",
        ("owlpath.result.v2", json.dumps(result, ensure_ascii=False), digest, run["id"]),
    )

    response = client.get("/api/runs/%s" % run["id"])
    assert response.status_code == 200, response.text
    restored = response.json()["result"]
    assert restored["schema_version"] == "owlpath.result.v2"
    assert restored["human_summary_i18n"] == {
        "zh_cn": "仅有中文的旧版摘要",
        "en": None,
        "status": "partial",
    }
    assert restored["safety_conclusion_i18n"] == {
        "zh_cn": None,
        "en": "English-only safety conclusion",
        "status": "partial",
    }


def test_new_run_trace_exposes_hashed_dag_and_integrity_checked_node_details(
    client: TestClient,
) -> None:
    run = _create_baseline_run(client)
    assert run["trace_version"] == "owlpath.trace.v1"
    assert run["execution_graph_version"] == "owlpath.execution-graph.v1"
    assert len(run["execution_manifest_sha256"]) == 64

    response = client.get("/api/runs/%s/trace" % run["id"])
    assert response.status_code == 200, response.text
    trace = response.json()
    assert trace["run_id"] == run["id"]
    assert trace["trace_version"] == run["trace_version"]
    assert trace["execution_graph_version"] == run["execution_graph_version"]
    assert trace["execution_manifest_sha256"] == run["execution_manifest_sha256"]
    assert trace["manifest_integrity_ok"] is True
    assert sha256_json(trace["manifest"]) == trace["execution_manifest_sha256"]

    manifest_node_keys = {item["key"] for item in trace["manifest"]["nodes"]}
    assert {
        "snapshot", "preflight", "applicability", "input_quality", "baseline",
        "sanitizer:baseline", "aggregator", "safety", "bilingual_renderer", "persistence",
    } <= manifest_node_keys
    incoming = {key: 0 for key in manifest_node_keys}
    outgoing = {key: [] for key in manifest_node_keys}
    for edge in trace["manifest"]["edges"]:
        assert edge["from"] in manifest_node_keys
        assert edge["to"] in manifest_node_keys
        outgoing[edge["from"]].append(edge["to"])
        incoming[edge["to"]] += 1
    queue = [key for key, degree in incoming.items() if degree == 0]
    visited = 0
    while queue:
        key = queue.pop()
        visited += 1
        for target in outgoing[key]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    assert visited == len(manifest_node_keys), "execution manifest must be a DAG"

    nodes = trace["nodes"]
    actual_node_keys = [node["node_key"] for node in nodes]
    assert manifest_node_keys <= set(actual_node_keys)
    assert len(actual_node_keys) == len(set(actual_node_keys))
    assert [node["sequence"] for node in nodes] == sorted(node["sequence"] for node in nodes)
    for node in nodes:
        assert node["status"] in {"completed", "failed", "skipped"}
        assert node["display_name_i18n"]["zh_cn"]
        assert node["display_name_i18n"]["en"]
        assert node["role"] and node["version"]

        detail_response = client.get("/api/trace/nodes/%s" % node["id"])
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["node"]["id"] == node["id"]
        assert detail["trace_privacy"] == {
            "raw_provider_response_exposed": False,
            "api_credentials_exposed": False,
            "synthetic_demo_source_expandable": False,
        }
        artifact_ids = {artifact["id"] for artifact in detail["artifacts"]}
        if node["input_artifact_id"]:
            assert node["input_artifact_id"] in artifact_ids
        if node["output_artifact_id"]:
            assert node["output_artifact_id"] in artifact_ids
        for artifact in detail["artifacts"]:
            assert artifact["visibility"] == "trace_safe"
            assert artifact["integrity_ok"] is True
            assert artifact["content"] is not None
            assert sha256_json(artifact["content"]) == artifact["content_sha256"]


def test_v3_trace_omits_credentials_and_raw_provider_responses_for_all_agent_calls(
    client: TestClient,
    development_provider_factory: Any,
    offline_medical_retriever: Any,
) -> None:
    api_key_marker = "TRACE-API-KEY-MUST-NOT-LEAK"
    raw_response_marker = "TRACE-RAW-RESPONSE-MUST-NOT-LEAK"
    capture = development_provider_factory(
        secret_marker=api_key_marker,
        raw_marker=raw_response_marker,
    )
    client.app.state.engine.provider_client = capture
    client.app.state.engine.medical_retriever = offline_medical_retriever
    provider_response = client.post("/api/providers", json={
        "name": "trace-local-provider",
        "kind": "openai_compatible",
        "model": "trace-model",
        "base_url": "http://127.0.0.1:9019/v1",
        "api_key": api_key_marker,
        "data_boundary": "local",
    })
    assert provider_response.status_code == 201, provider_response.text
    provider_id = provider_response.json()["id"]
    client.app.state.db.execute(
        """UPDATE providers SET enabled = 1, last_test_ok = 1, last_tested_at = ?,
           last_test_latency_ms = 1, last_test_error_code = NULL WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), provider_id),
    )

    create_response = client.post("/api/development-demo/runs", json={
        "text": "纯虚构：68岁患者发热咳嗽，无任何真实身份信息。",
        "provider_ids": [provider_id],
    })
    assert create_response.status_code == 202, create_response.text
    run = _wait_run(client, create_response.json()["id"])
    assert run["status"] == "completed", run
    # The v3 panel always runs five complementary core consultants; the cough
    # cue recruits pulmonology, followed by synthesis and critic.
    assert capture.calls == 8
    assert capture.keys == [api_key_marker] * 8

    trace_response = client.get("/api/runs/%s/trace" % run["id"])
    assert trace_response.status_code == 200, trace_response.text
    trace = trace_response.json()
    node_keys = {node["node_key"] for node in trace["nodes"]}
    assert {
        "specialist:infectious_diseases",
        "specialist:critical_care_emergency",
        "specialist:clinical_epidemiology",
        "specialist:laboratory_medicine",
        "specialist:clinical_microbiology_culture",
        "specialist:pulmonology",
        "synthesis",
        "critic",
        "result_compiler",
    } <= node_keys
    assert "bilingual_renderer" not in node_keys

    public_payloads: list[Any] = [trace]
    for node in trace["nodes"]:
        detail_response = client.get("/api/trace/nodes/%s" % node["id"])
        assert detail_response.status_code == 200, detail_response.text
        public_payloads.append(detail_response.json())
    keys = _all_mapping_keys(public_payloads)
    rendered = json.dumps(public_payloads, ensure_ascii=False)
    assert "api_key" not in keys
    assert "raw_response" not in keys
    assert api_key_marker not in rendered
    assert raw_response_marker not in rendered


def test_run_events_resume_from_query_or_last_event_id_header(client: TestClient) -> None:
    run = _create_baseline_run(client)
    rows = client.app.state.db.fetchall(
        "SELECT id FROM run_events WHERE run_id = ? ORDER BY id", (run["id"],),
    )
    all_ids = [int(row["id"]) for row in rows]
    assert len(all_ids) >= 5

    query_cutoff = all_ids[1]
    query_response = client.get(
        "/api/runs/%s/events" % run["id"], params={"after_id": query_cutoff},
    )
    assert query_response.status_code == 200, query_response.text
    assert _sse_event_ids(query_response.text) == [value for value in all_ids if value > query_cutoff]

    header_cutoff = all_ids[len(all_ids) // 2]
    header_response = client.get(
        "/api/runs/%s/events" % run["id"],
        headers={"Last-Event-ID": str(header_cutoff)},
    )
    assert header_response.status_code == 200, header_response.text
    assert _sse_event_ids(header_response.text) == [value for value in all_ids if value > header_cutoff]

    newest_cutoff = all_ids[-1]
    empty_response = client.get(
        "/api/runs/%s/events" % run["id"],
        params={"after_id": query_cutoff},
        headers={"Last-Event-ID": str(newest_cutoff)},
    )
    assert empty_response.status_code == 200, empty_response.text
    assert _sse_event_ids(empty_response.text) == []


def test_run_trace_version_filter_hides_legacy_rows_without_deleting_them(
    client: TestClient,
) -> None:
    current_run = _create_baseline_run(client)
    legacy_run = _create_baseline_run(client)
    client.app.state.db.execute(
        "UPDATE runs SET trace_version = NULL WHERE id = ?", (legacy_run["id"],),
    )

    filtered_response = client.get(
        "/api/runs", params={"trace_version": "owlpath.trace.v1"},
    )
    assert filtered_response.status_code == 200, filtered_response.text
    filtered_ids = {item["id"] for item in filtered_response.json()}
    assert current_run["id"] in filtered_ids
    assert legacy_run["id"] not in filtered_ids

    unfiltered_response = client.get("/api/runs")
    assert unfiltered_response.status_code == 200, unfiltered_response.text
    unfiltered_ids = {item["id"] for item in unfiltered_response.json()}
    assert {current_run["id"], legacy_run["id"]} <= unfiltered_ids
    assert client.app.state.db.fetchone(
        "SELECT id FROM runs WHERE id = ?", (legacy_run["id"],),
    ) is not None
