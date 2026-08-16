import json
import hashlib
import os
import time
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import json_loads, sha256_json
from app.main import create_app
from app.errors import ProviderRefusal
from app.models import ModelPrediction


def test_health_security_and_research_headers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["research_only"] is True
    assert response.headers["X-OwlPath-Clinical-Status"] == "research-only-not-validated"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_spa_shell_revalidates_while_hashed_assets_are_immutable(tmp_path: Path) -> None:
    frontend_dist = tmp_path / "frontend" / "dist"
    assets = frontend_dist / "assets"
    assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        '<!doctype html><script type="module" src="/assets/index-testhash.js"></script>',
        encoding="utf-8",
    )
    (assets / "index-testhash.js").write_text("export {};", encoding="utf-8")
    settings = Settings(
        base_dir=tmp_path / "backend",
        database_path=tmp_path / "cache-headers.db",
    )
    with TestClient(create_app(settings)) as test_client:
        shell = test_client.get("/")
        assert shell.status_code == 200
        assert shell.headers["Cache-Control"] == "no-store, max-age=0"
        assert shell.headers["Pragma"] == "no-cache"

        asset = test_client.get("/assets/index-testhash.js")
        assert asset.status_code == 200
        assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def _create_case(client: TestClient, consent: bool = False) -> str:
    response = client.post("/api/cases", json={
        "case_alias": "CASE-DEID-001",
        "demographics": {"age_years": 68, "sex": "male", "immunocompromised": False, "care_setting": "emergency"},
        "context": {"primary_syndrome": "respiratory", "acquisition_context": "community"},
        "external_data_consent": consent,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _mark_provider_ready(client: TestClient, provider_id: str) -> None:
    """Establish a tested-provider precondition without invoking scenario-specific fakes."""
    client.app.state.db.execute(
        """UPDATE providers SET enabled = 1, last_test_ok = 1, last_tested_at = ?,
           last_test_latency_ms = 1, last_test_error_code = NULL WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), provider_id),
    )


def _wait_run(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        data = client.get("/api/runs/%s" % run_id).json()
        if data["status"] in {"completed", "failed"}:
            return data
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _review(source_text: Optional[str] = None) -> dict:
    return {
        "accepted": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "statement_version": "owlpath-clinical-review-v1",
        "parser_version": "test-parser-v1",
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else None,
    }


def _bound_review(
    client: TestClient,
    case_id: str,
    decision_time: datetime,
    source_text: Optional[str] = None,
) -> dict:
    response = client.get(
        "/api/cases/%s/snapshot-hash" % case_id,
        params={"decision_time": decision_time.isoformat()},
    )
    assert response.status_code == 200, response.text
    review = _review(source_text)
    review["input_snapshot_sha256"] = response.json()["input_snapshot_sha256"]
    return review


def test_baseline_time_gate_hashes_sse_and_evaluation(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    included = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom", "occurred_at": (now - timedelta(hours=2)).isoformat(),
        "visible_at": (now - timedelta(hours=1)).isoformat(), "source": "deidentified-note",
        "status": "final", "data": {"symptom": "发热咳嗽"}, "quality": {"verified": True},
    })
    assert included.status_code == 201
    future_marker = "FUTURE-PCR-SECRET-MARKER"
    future = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "microbiology", "occurred_at": now.isoformat(),
        "issued_at": (now + timedelta(hours=5)).isoformat(),
        "visible_at": (now + timedelta(hours=5)).isoformat(), "source": "lab",
        "status": "final", "data": {"result": future_marker}, "quality": {},
    })
    assert future.status_code == 201
    created = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": now.isoformat(), "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, now),
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    assert run["result"]["safety_action"] == "category_only"
    assert run["result"]["research_only"] is True
    assert run["input_snapshot_sha256"] == run["result"]["input_snapshot_sha256"]
    assert run["result_sha256"] == run["result"]["result_sha256"]
    unsigned = dict(run["result"])
    unsigned["result_sha256"] = None
    assert sha256_json(unsigned) == run["result_sha256"]

    row = client.app.state.db.fetchone("SELECT input_snapshot_json FROM runs WHERE id = ?", (run["id"],))
    snapshot_text = row["input_snapshot_json"]
    snapshot = json_loads(snapshot_text)
    assert future_marker not in snapshot_text
    assert snapshot["excluded_event_count"] == 1
    assert set(snapshot["excluded_event_manifest"][0]["reasons"]) == {
        "visible_after_decision_time", "phase_excluded_microbiology",
    }

    with client.stream("GET", "/api/runs/%s/events" % run["id"]) as response:
        stream_text = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: queued" in stream_text and "event: completed" in stream_text

    evaluated = client.post("/api/evaluations", json={
        "run_id": run["id"],
        "label": {"infection_status": "infectious", "causal_pathogens": [{
            "canonical_id": "taxon:1313", "name": "Streptococcus pneumoniae", "certainty": "confirmed",
        }], "adjudication_status": "panel_consensus"},
    })
    assert evaluated.status_code == 201, evaluated.text
    assert set(evaluated.json()["metrics"]) >= {"top1", "top3", "top5", "mrr", "pathogen_brier"}
    summary = client.get("/api/evaluations/summary").json()
    assert summary["n_evaluations"] == 1.0


def test_explicit_empty_provider_list_means_baseline_only(client: TestClient) -> None:
    provider = client.post("/api/providers", json={
        "name": "enabled-cloud-provider",
        "kind": "openai_responses",
        "model": "mock",
        "api_key": "TEST-ONLY-KEY",
        "enabled": False,
        "data_boundary": "external",
    })
    assert provider.status_code == 201
    _mark_provider_ready(client, provider.json()["id"])
    case_id = _create_case(client, consent=False)

    # Omitting provider_ids keeps the explicit API shorthand "all enabled".
    all_enabled = client.post("/api/runs", json={
        "case_id": case_id,
        "include_baseline": True,
    })
    assert all_enabled.status_code == 422
    assert all_enabled.json()["error"]["code"] == "external_data_consent_required"

    # The UI always sends an explicit list. [] must therefore mean no configured
    # providers, never an accidental expansion to every enabled cloud model.
    baseline_decision = datetime.now(timezone.utc)
    baseline_only = client.post("/api/runs", json={
        "case_id": case_id,
        "decision_time": baseline_decision.isoformat(),
        "provider_ids": [],
        "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, baseline_decision),
    })
    assert baseline_only.status_code == 202, baseline_only.text
    run = _wait_run(client, baseline_only.json()["id"])
    assert run["provider_ids"] == []
    outputs = client.get("/api/runs/%s/models" % run["id"]).json()
    assert [item["provider_id"] for item in outputs] == ["baseline"]


def test_external_consent_key_protection_ssrf_and_recursive_redaction(client: TestClient) -> None:
    missing_compatible_url = client.post("/api/providers", json={
        "name": "missing-url", "kind": "openai_compatible", "model": "m",
    })
    assert missing_compatible_url.status_code == 422
    assert missing_compatible_url.json()["error"]["code"] == "openai_compatible_base_url_required"

    sensitive_header = client.post("/api/providers", json={
        "name": "bad", "kind": "openai_responses", "model": "m",
        "extra_headers": {"Cookie": "SHOULD-NOT-ECHO"},
    })
    assert sensitive_header.status_code == 422
    assert "SHOULD-NOT-ECHO" not in sensitive_header.text
    sensitive_option = client.post("/api/providers", json={
        "name": "bad", "kind": "openai_responses", "model": "m",
        "options": {"nested": {"token": "SHOULD-NOT-ECHO"}},
    })
    assert sensitive_option.status_code == 422
    assert "SHOULD-NOT-ECHO" not in sensitive_option.text
    disguised_header = client.post("/api/providers", json={
        "name": "bad", "kind": "openai_compatible", "model": "m",
        "base_url": "https://provider.example/v1", "extra_headers": {"X-Custom-Token": "SHOULD-NOT-ECHO"},
    })
    assert disguised_header.status_code == 422
    assert "SHOULD-NOT-ECHO" not in disguised_header.text
    ssrf = client.post("/api/providers", json={
        "name": "ssrf", "kind": "openai_compatible", "model": "m",
        "base_url": "http://169.254.169.254/latest", "data_boundary": "external",
    })
    assert ssrf.status_code == 422
    false_local = client.post("/api/providers", json={
        "name": "false-local", "kind": "openai_compatible", "model": "m",
        "base_url": "https://public.example/v1", "data_boundary": "local",
    })
    assert false_local.status_code == 422
    compatible = client.post("/api/providers", json={
        "name": "compatible", "kind": "openai_compatible", "model": "m",
        "base_url": "https://provider.example/v1", "data_boundary": "external",
    })
    assert compatible.status_code == 201
    cleared_compatible_url = client.patch(
        f"/api/providers/{compatible.json()['id']}", json={"base_url": None},
    )
    assert cleared_compatible_url.status_code == 422
    assert cleared_compatible_url.json()["error"]["code"] == "openai_compatible_base_url_required"
    cloud_default_marked_local = client.post("/api/providers", json={
        "name": "false-local-default", "kind": "openai_responses", "model": "m",
        "data_boundary": "local",
    })
    assert cloud_default_marked_local.status_code == 422

    created = client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_responses", "model": "mock", "api_key": "TOP-SECRET-KEY",
    })
    assert created.status_code == 201
    provider = created.json()
    assert provider["has_api_key"] is True and "TOP-SECRET-KEY" not in created.text
    stored = client.app.state.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider["id"],))
    assert stored["encrypted_api_key"] != "TOP-SECRET-KEY"
    _mark_provider_ready(client, provider["id"])

    case_id = _create_case(client, consent=False)
    blocked = client.post("/api/runs", json={
        "case_id": case_id, "provider_ids": [provider["id"]], "include_baseline": False,
    })
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "external_data_consent_required"

    client.app.state.db.audit("test", "nested", "test", "1", {
        "outer": {"authorization": "Bearer BAD", "items": [{"token": "BAD2"}]},
    })
    audit = client.get("/api/audit", params={"entity_id": "1"}).json()[0]
    rendered = json.dumps(audit)
    assert "Bearer BAD" not in rendered and "BAD2" not in rendered
    assert rendered.count("[REDACTED]") == 2


def test_provider_key_update_semantics_are_explicit_and_safe(client: TestClient) -> None:
    blank = client.post("/api/providers", json={
        "name": "blank-key", "kind": "openai_responses", "model": "m",
        "api_key": "   ", "enabled": False,
    })
    assert blank.status_code == 422

    created = client.post("/api/providers", json={
        "name": "key-lifecycle", "kind": "openai_responses", "model": "m",
        "api_key": "FIRST-KEY", "enabled": False,
    })
    assert created.status_code == 201
    provider_id = created.json()["id"]
    before = client.app.state.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider_id,))

    _mark_provider_ready(client, provider_id)

    renamed = client.patch(f"/api/providers/{provider_id}", json={"name": "renamed"})
    assert renamed.status_code == 200
    after_rename = client.app.state.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider_id,))
    assert after_rename["encrypted_api_key"] == before["encrypted_api_key"]

    ambiguous = client.patch(f"/api/providers/{provider_id}", json={
        "api_key": "SECOND-KEY", "clear_api_key": True,
    })
    assert ambiguous.status_code == 422
    assert "SECOND-KEY" not in ambiguous.text

    replaced = client.patch(f"/api/providers/{provider_id}", json={"api_key": "SECOND-KEY"})
    assert replaced.status_code == 200
    assert replaced.json()["has_api_key"] is True
    assert replaced.json()["enabled"] is False
    assert "SECOND-KEY" not in replaced.text
    after_replace = client.app.state.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider_id,))
    assert after_replace["encrypted_api_key"] != before["encrypted_api_key"]

    cleared = client.patch(f"/api/providers/{provider_id}", json={"clear_api_key": True})
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert cleared.json()["enabled"] is False
    stored = client.app.state.db.fetchone("SELECT encrypted_api_key FROM providers WHERE id = ?", (provider_id,))
    assert stored["encrypted_api_key"] is None


def test_direct_identifier_field_is_rejected_without_echo(client: TestClient) -> None:
    case_id = _create_case(client)
    response = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "history", "occurred_at": datetime.now(timezone.utc).isoformat(),
        "visible_at": datetime.now(timezone.utc).isoformat(), "source": "note",
        "data": {"patient_name": "DO-NOT-ECHO"},
    })
    assert response.status_code == 422
    assert "DO-NOT-ECHO" not in response.text


def test_run_gate_blocks_raw_future_and_pathogen_leakage_server_side(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    marker = "FUTURE-RAW-MARKER"
    raw = "%s PCR报告阳性 %s" % ((now + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"), marker)
    created = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "history", "occurred_at": now.isoformat(), "visible_at": now.isoformat(),
        "source": "note", "status": "final",
        "data": {"deidentified_note": raw}, "quality": {},
    })
    assert created.status_code == 201
    blocked = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": now.isoformat(), "provider_ids": [], "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, now, raw),
    })
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "input_safety_gate_blocked"
    assert set(blocked.json()["error"]["details"]["warning_codes"]) >= {
        "future_timestamp_in_text", "possible_pathogen_label_leakage",
    }
    assert marker not in blocked.text


def test_run_gate_blocks_identifier_values_even_when_field_name_is_generic(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    created = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "history", "occurred_at": now.isoformat(), "visible_at": now.isoformat(),
        "source": "note", "status": "final", "data": {"note": "姓名：测试甲，发热咳嗽"}, "quality": {},
    })
    assert created.status_code == 201
    blocked = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": now.isoformat(), "provider_ids": [], "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, now, "姓名：测试甲，发热咳嗽"),
    })
    assert blocked.status_code == 422
    assert "possible_direct_identifier_name" in blocked.json()["error"]["details"]["warning_codes"]
    assert "测试甲" not in blocked.text


def test_out_of_scope_case_abstains_before_any_provider_call(client: TestClient) -> None:
    class BombProvider:
        calls = 0

        async def invoke(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            raise AssertionError("out-of-scope data must not reach providers")

    bomb = BombProvider()
    client.app.state.engine.provider_client = bomb
    provider = client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_compatible", "model": "mock",
        "base_url": "https://provider.example/v1", "api_key": "TEST-KEY",
        "data_boundary": "external",
    })
    assert provider.status_code == 201
    _mark_provider_ready(client, provider.json()["id"])
    case = client.post("/api/cases", json={
        "case_alias": "PED-DEID-001",
        "demographics": {"age_years": 12, "sex": "male", "immunocompromised": False, "care_setting": "emergency"},
        "context": {"primary_syndrome": "respiratory", "acquisition_context": "community"},
        "external_data_consent": True,
    })
    assert case.status_code == 201
    out_decision = datetime.now(timezone.utc)
    bound_review = _bound_review(client, case.json()["id"], out_decision)
    created = client.post("/api/runs", json={
        "case_id": case.json()["id"], "decision_time": out_decision.isoformat(),
        "provider_ids": [provider.json()["id"]], "include_baseline": True,
        "clinical_review": bound_review,
        "data_transfer_consent": {
            "accepted": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "statement_version": "owlpath-external-transfer-v1",
            "external_provider_ids": [provider.json()["id"]],
            "input_snapshot_sha256": bound_review["input_snapshot_sha256"],
            "provider_targets": [{
                "provider_id": provider.json()["id"],
                "kind": "openai_compatible",
                "model": "mock",
                "base_url_origin": "https://provider.example",
                "endpoint_url": "https://provider.example/v1/chat/completions",
                "data_boundary": "external",
            }],
        },
    })
    assert created.status_code == 202
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    assert run["result"]["safety_action"] == "abstain"
    assert any("最低年龄" in reason for reason in run["result"]["safety_reasons"])
    assert client.get("/api/runs/%s/models" % run["id"]).json() == []
    assert bomb.calls == 0


def test_raw_provenance_never_enters_model_snapshot(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    raw = "68岁男性院外起病，发热咳嗽2天；这段完整原文只用于本地来源追溯。"
    created = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "history", "occurred_at": now.isoformat(), "visible_at": now.isoformat(),
        "source": "clinician-ui", "status": "final",
        "data": {"deidentified_note": raw, "present_illness": "发热咳嗽2天"},
        "quality": {"clinician_reviewed": True, "source_text": raw},
    })
    assert created.status_code == 201
    run_response = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": now.isoformat(), "provider_ids": [],
        "include_baseline": True, "clinical_review": _bound_review(client, case_id, now, raw),
    })
    assert run_response.status_code == 202, run_response.text
    row = client.app.state.db.fetchone("SELECT input_snapshot_json FROM runs WHERE id = ?", (run_response.json()["id"],))
    assert raw not in row["input_snapshot_json"]
    snapshot = json_loads(row["input_snapshot_json"])
    assert "case_alias" not in snapshot["case"]
    assert snapshot["events"][0]["data"] == {"clinical_facts": [
        {"code": "fever", "status": "present", "temporality": "current"},
        {"code": "cough", "status": "present", "temporality": "current"},
    ]}
    assert "source_text" not in snapshot["events"][0]["quality"]


def test_event_time_order_is_rejected(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    response = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "lab",
        "occurred_at": now.isoformat(),
        "collected_at": (now + timedelta(minutes=10)).isoformat(),
        "issued_at": (now + timedelta(minutes=20)).isoformat(),
        "visible_at": (now + timedelta(minutes=15)).isoformat(),
        "source": "lab",
        "data": {"test_name": "WBC", "value": "12", "unit": "10^9/L"},
    })
    assert response.status_code == 422
    assert "visible_at cannot precede issued_at" in response.text


def test_completed_run_integrity_tamper_fails_closed(client: TestClient) -> None:
    case_id = _create_case(client)
    decision = datetime.now(timezone.utc)
    created = client.post("/api/runs", json={
        "case_id": case_id,
        "decision_time": decision.isoformat(),
        "provider_ids": [],
        "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, decision),
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    client.app.state.db.execute(
        "UPDATE runs SET result_json = ? WHERE id = ?",
        (json.dumps({"tampered": True}), run["id"]),
    )
    verified = client.get("/api/runs/%s" % run["id"]).json()
    assert verified["status"] == "failed"
    assert verified["result"] is None
    assert verified["error"]["code"] == "run_integrity_failure"
    assert client.get("/api/runs/%s/models" % run["id"]).json()[0]["normalized"] is None


def test_case_alias_rejects_patient_name_without_echo(client: TestClient) -> None:
    response = client.post("/api/cases", json={"case_alias": "测试甲"})
    assert response.status_code == 422
    assert "测试甲" not in response.text


def test_unstarted_case_can_roll_back_but_audited_case_cannot(client: TestClient) -> None:
    case_id = _create_case(client)
    now = datetime.now(timezone.utc)
    event = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom", "occurred_at": now.isoformat(), "visible_at": now.isoformat(),
        "source": "clinician-ui", "data": {"symptom": "咳嗽"}, "quality": {},
    })
    assert event.status_code == 201
    assert client.delete("/api/cases/%s" % case_id).status_code == 204
    assert client.get("/api/cases/%s" % case_id).status_code == 404

    audited = _create_case(client)
    audited_decision = datetime.now(timezone.utc)
    run_response = client.post("/api/runs", json={
        "case_id": audited, "decision_time": audited_decision.isoformat(),
        "provider_ids": [], "include_baseline": True,
        "clinical_review": _bound_review(client, audited, audited_decision),
    })
    assert run_response.status_code == 202
    refused = client.delete("/api/cases/%s" % audited)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "case_has_runs"


def test_governance_update_is_locked_and_db_files_are_private(client: TestClient, tmp_path: Any) -> None:
    current = client.get("/api/governance").json()
    locked = client.put("/api/governance", json=current)
    assert locked.status_code == 403
    db_path = client.app.state.db.path
    client.app.state.db.fetchone("SELECT 1")
    assert os.stat(db_path).st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            assert os.stat(sidecar).st_mode & 0o777 == 0o600

    admin_app = create_app(Settings(
        database_path=tmp_path / "admin.db",
        governance_admin_token="TEST-ADMIN-TOKEN",
    ))
    with TestClient(admin_app) as admin_client:
        config = admin_client.get("/api/governance").json()
        config["version"] = "test-admin-update"
        updated = admin_client.put(
            "/api/governance",
            json=config,
            headers={"X-OwlPath-Admin-Token": "TEST-ADMIN-TOKEN"},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == "test-admin-update"


def test_naive_clinical_timestamps_are_rejected(client: TestClient) -> None:
    case_id = _create_case(client)
    event = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom", "occurred_at": "2026-08-06T09:00:00",
        "visible_at": "2026-08-06T09:00:00", "source": "manual",
        "data": {"symptom": "fever"},
    })
    assert event.status_code == 422
    organized = client.post("/api/clinical-text/organize", json={
        "text": "fever", "decision_time": "2026-08-06T09:00:00", "source": "manual",
    })
    assert organized.status_code == 422
    snapshot = client.get(
        "/api/cases/%s/snapshot-hash" % case_id,
        params={"decision_time": "2026-08-06T09:00:00"},
    )
    assert snapshot.status_code == 422
    assert snapshot.json()["error"]["code"] == "timezone_required"


def test_live_time_is_current_and_retrospective_mode_is_separately_locked(client: TestClient) -> None:
    case_id = _create_case(client)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    blocked_live = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": future.isoformat(),
        "provider_ids": [], "include_baseline": True,
    })
    assert blocked_live.status_code == 422
    assert blocked_live.json()["error"]["code"] == "live_decision_time_not_current"

    blocked_replay = client.post("/api/runs", json={
        "case_id": case_id,
        "decision_time": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "run_mode": "retrospective",
        "retrospective_anchor_id": "PREREG-2026-001",
        "provider_ids": [], "include_baseline": True,
    })
    assert blocked_replay.status_code == 403
    assert blocked_replay.json()["error"]["code"] == "retrospective_run_admin_required"


def test_authorized_retrospective_replay_is_explicit_and_audited(tmp_path: Any) -> None:
    app = create_app(Settings(
        database_path=tmp_path / "retrospective.db",
        governance_admin_token="ADMIN-TOKEN",
        allow_retrospective_runs=True,
    ))
    with TestClient(app) as client:
        case_id = _create_case(client)
        decision = datetime.now(timezone.utc) - timedelta(days=30)
        review = _bound_review(client, case_id, decision)
        created = client.post("/api/runs", headers={"X-OwlPath-Admin-Token": "ADMIN-TOKEN"}, json={
            "case_id": case_id, "decision_time": decision.isoformat(),
            "run_mode": "retrospective", "retrospective_anchor_id": "PREREG-2026-001",
            "provider_ids": [], "include_baseline": True, "clinical_review": review,
        })
        assert created.status_code == 202, created.text
        assert created.json()["run_mode"] == "retrospective"
        assert created.json()["retrospective_anchor_id"] == "PREREG-2026-001"


def test_pre_egress_integrity_check_blocks_tampered_snapshot(client: TestClient) -> None:
    class BombProvider:
        calls = 0

        async def invoke(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            raise AssertionError("tampered snapshot must never leave the process")

    bomb = BombProvider()
    client.app.state.engine.provider_client = bomb
    client.app.state.engine.schedule = lambda _run_id: None
    provider = client.post("/api/providers", json={
        "name": "local-model", "kind": "openai_compatible", "model": "local-model",
        "base_url": "http://127.0.0.1:9010/v1", "data_boundary": "local",
    })
    assert provider.status_code == 201
    _mark_provider_ready(client, provider.json()["id"])
    case_id = _create_case(client)
    decision = datetime.now(timezone.utc)
    event = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom", "occurred_at": decision.isoformat(), "visible_at": decision.isoformat(),
        "source": "manual", "status": "final", "data": {"symptom": "fever"},
    })
    assert event.status_code == 201
    created = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": decision.isoformat(),
        "provider_ids": [provider.json()["id"]], "include_baseline": False,
        "clinical_review": _bound_review(client, case_id, decision),
    })
    assert created.status_code == 202, created.text
    client.app.state.db.execute(
        "UPDATE runs SET input_snapshot_json = ? WHERE id = ?",
        (json.dumps({"patient_secret": "PATIENT-SECRET"}), created.json()["id"]),
    )
    asyncio.run(client.app.state.engine.process_run(created.json()["id"]))
    row = client.app.state.db.fetchone("SELECT status, error_json FROM runs WHERE id = ?", (created.json()["id"],))
    assert row["status"] == "failed"
    assert json_loads(row["error_json"])["code"] == "run_integrity_failure_before_egress"
    assert bomb.calls == 0


def test_tampered_completed_run_cannot_be_evaluated(client: TestClient) -> None:
    case_id = _create_case(client)
    decision = datetime.now(timezone.utc)
    created = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": decision.isoformat(),
        "provider_ids": [], "include_baseline": True,
        "clinical_review": _bound_review(client, case_id, decision),
    })
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    client.app.state.db.execute(
        "UPDATE runs SET result_json = ? WHERE id = ?",
        (json.dumps({"tampered": True}), run["id"]),
    )
    evaluated = client.post("/api/evaluations", json={
        "run_id": run["id"], "label": {"infection_status": "infectious"},
    })
    assert evaluated.status_code == 409
    assert evaluated.json()["error"]["code"] == "run_not_integrity_verified"


def test_external_consent_binds_full_endpoint_not_only_origin(client: TestClient) -> None:
    provider = client.post("/api/providers", json={
        "name": "cloud", "kind": "openai_compatible", "model": "model-a",
        "base_url": "https://provider.example/v1", "api_key": "TEST-KEY",
        "data_boundary": "external",
    })
    assert provider.status_code == 201
    _mark_provider_ready(client, provider.json()["id"])
    case_id = _create_case(client, consent=True)
    decision = datetime.now(timezone.utc)
    review = _bound_review(client, case_id, decision)
    blocked = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": decision.isoformat(),
        "provider_ids": [provider.json()["id"]], "include_baseline": False,
        "clinical_review": review,
        "data_transfer_consent": {
            "accepted": True, "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "statement_version": "owlpath-external-transfer-v1",
            "external_provider_ids": [provider.json()["id"]],
            "input_snapshot_sha256": review["input_snapshot_sha256"],
            "provider_targets": [{
                "provider_id": provider.json()["id"], "kind": "openai_compatible",
                "model": "model-a", "base_url_origin": "https://provider.example",
                "endpoint_url": "https://provider.example/evil/chat/completions",
                "data_boundary": "external",
            }],
        },
    })
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "external_data_consent_required"


def test_safety_views_hide_species_and_provider_controlled_errors(client: TestClient) -> None:
    species_marker = "Streptococcus pneumoniae"
    refusal_marker = "Staphylococcus aureus is most likely"

    class AdversarialProvider:
        async def invoke(self, provider: Any, *args: Any, **kwargs: Any) -> Any:
            if provider["model"] == "refusal-model":
                raise ProviderRefusal(refusal_marker)
            return ModelPrediction.model_validate({
                "summary": "raw narrative " + species_marker,
                "infection_probability": 0.75,
                "syndrome_probabilities": {species_marker: 1.0, "respiratory": 1.0},
                "candidates": [{
                    "canonical_id": "taxon:1313", "name": species_marker,
                    "rank_level": "species", "category": "bacteria",
                    "species": species_marker, "probability": 0.62,
                    "calibration_status": "calibrated",
                }],
                "coinfection_probability": 0.05, "unknown_probability": 0.2,
            }), {"raw": species_marker}

    client.app.state.engine.provider_client = AdversarialProvider()
    providers = []
    for model in ("species-model", "refusal-model"):
        response = client.post("/api/providers", json={
            "name": model, "kind": "openai_compatible", "model": model,
            "base_url": "http://127.0.0.1:9020/v1", "data_boundary": "local",
        })
        assert response.status_code == 201
        providers.append(response.json()["id"])
        _mark_provider_ready(client, response.json()["id"])
    case_id = _create_case(client)
    decision = datetime.now(timezone.utc)
    event = client.post("/api/cases/%s/events" % case_id, json={
        "kind": "symptom", "occurred_at": decision.isoformat(), "visible_at": decision.isoformat(),
        "source": "manual", "status": "final", "data": {"symptom": "fever and cough"},
    })
    assert event.status_code == 201
    created = client.post("/api/runs", json={
        "case_id": case_id, "decision_time": decision.isoformat(),
        "provider_ids": providers, "include_baseline": False,
        "clinical_review": _bound_review(client, case_id, decision),
    })
    assert created.status_code == 202, created.text
    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    assert run["result"]["safety_action"] == "category_only"
    assert species_marker not in json.dumps(run)
    models = client.get("/api/runs/%s/models" % run["id"])
    assert species_marker not in models.text
    assert refusal_marker not in models.text
    with client.stream("GET", "/api/runs/%s/events" % run["id"]) as response:
        stream_text = "".join(response.iter_text())
    assert species_marker not in stream_text
    assert refusal_marker not in stream_text
    exported = client.get("/api/cases/%s/export" % case_id)
    assert species_marker not in exported.text
    assert refusal_marker not in exported.text


def test_development_demo_alias_runs_v3_agents_with_full_synthetic_text(
    client: TestClient,
    development_provider_factory: Any,
    offline_medical_retriever: Any,
) -> None:
    marker = "姓名：测试甲，12岁男性，发热咳嗽；PCR报告阳性（全部为虚构测试）。"
    capture = development_provider_factory()
    client.app.state.engine.provider_client = capture
    client.app.state.engine.medical_retriever = offline_medical_retriever
    provider = client.post("/api/providers", json={
        "name": "demo-cloud", "kind": "openai_compatible", "model": "demo-model",
        "base_url": "https://provider.example/v1", "api_key": "DEMO-TEST-KEY",
        "data_boundary": "external",
    })
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    _mark_provider_ready(client, provider_id)

    # The clinical governance kill switch remains meaningful for clinical runs,
    # but it is intentionally not a content gate on this isolated demo endpoint.
    governance = client.app.state.db.governance()
    client.app.state.db.update_governance(governance.model_copy(update={"run_enabled": False}))

    created = client.post("/api/development-demo/runs", json={
        "text": marker,
        "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    assert created.json()["run_mode"] == "development_demo"
    assert created.json()["include_baseline"] is False
    assert created.json()["clinical_review"] is None
    assert created.json()["data_transfer_consent"] is None

    run = _wait_run(client, created.json()["id"])
    assert run["status"] == "completed"
    assert capture.calls == 9
    assert capture.keys == ["DEMO-TEST-KEY"] * 9
    assert capture.source_texts == [marker] * 9
    assert set(capture.specialist_roles) == {
        "infectious_diseases", "critical_care_emergency", "clinical_epidemiology",
        "laboratory_medicine", "clinical_microbiology_culture", "pulmonology",
        "clinical_virology_molecular",
    }
    assert capture.synthesis_calls == 1
    assert capture.critic_calls == 1

    # The compatibility alias now executes the same development-first v3
    # contract: concrete Top-5, no clinical abstention/safety action.
    assert run["result"]["schema_version"] == "owlpath.result.v3"
    assert len(run["result"]["concrete_pathogens"]) == 5
    assert "safety_action" not in run["result"]

    models = client.get("/api/runs/%s/models" % run["id"])
    assert models.status_code == 200
    assert len(models.json()) == 9
    assert all(item["development_demo"] is True for item in models.json())

    stored = client.app.state.db.fetchone(
        "SELECT input_snapshot_json, clinical_review_json, data_transfer_consent_json FROM runs WHERE id = ?",
        (run["id"],),
    )
    assert json_loads(stored["input_snapshot_json"])["synthetic_source_text"] == marker
    assert stored["clinical_review_json"] is None
    assert stored["data_transfer_consent_json"] is None

    evaluation = client.post("/api/evaluations", json={
        "run_id": run["id"], "label": {"infection_status": "infectious"},
    })
    assert evaluation.status_code == 409
    assert evaluation.json()["error"]["code"] == "development_demo_not_evaluable"
    assert client.get("/api/evaluations/summary").json()["n_evaluations"] == 0.0


def test_development_demo_requires_existing_enabled_verified_provider(client: TestClient) -> None:
    missing = client.post("/api/development-demo/runs", json={
        "text": "synthetic fever and cough", "provider_ids": ["prv_missing"],
    })
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "providers_unavailable"

    provider = client.post("/api/providers", json={
        "name": "not-ready", "kind": "openai_compatible", "model": "demo-model",
        "base_url": "http://127.0.0.1:9011/v1", "data_boundary": "local",
    })
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    disabled = client.post("/api/development-demo/runs", json={
        "text": "synthetic fever and cough", "provider_ids": [provider_id],
    })
    assert disabled.status_code == 422
    assert disabled.json()["error"]["details"]["disabled"] == [provider_id]

    client.app.state.db.execute("UPDATE providers SET enabled = 1 WHERE id = ?", (provider_id,))
    unverified = client.post("/api/development-demo/runs", json={
        "text": "synthetic fever and cough", "provider_ids": [provider_id],
    })
    assert unverified.status_code == 422
    assert unverified.json()["error"]["code"] == "providers_unverified"


def test_development_demo_integrity_tamper_still_fails_before_provider(client: TestClient) -> None:
    class BombProvider:
        calls = 0

        async def invoke(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            raise AssertionError("tampered demo snapshots must not reach providers")

    bomb = BombProvider()
    client.app.state.engine.provider_client = bomb
    client.app.state.engine.schedule = lambda _run_id: None
    provider = client.post("/api/providers", json={
        "name": "demo-local", "kind": "openai_compatible", "model": "demo-model",
        "base_url": "http://127.0.0.1:9012/v1", "data_boundary": "local",
    })
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    _mark_provider_ready(client, provider_id)
    created = client.post("/api/development-demo/runs", json={
        "text": "synthetic fever and cough", "provider_ids": [provider_id],
    })
    assert created.status_code == 202, created.text
    client.app.state.db.execute(
        "UPDATE runs SET input_snapshot_json = ? WHERE id = ?",
        (json.dumps({"development_demo": True, "synthetic_source_text": "tampered"}), created.json()["id"]),
    )
    asyncio.run(client.app.state.engine.process_run(created.json()["id"]))
    row = client.app.state.db.fetchone(
        "SELECT status, error_json FROM runs WHERE id = ?", (created.json()["id"],),
    )
    assert row["status"] == "failed"
    assert json_loads(row["error_json"])["code"] == "run_integrity_failure_before_egress"
    assert bomb.calls == 0
