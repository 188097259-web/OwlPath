import asyncio
import csv
import hashlib
import hmac
import io
import time
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .db import Database, json_dumps, json_loads, redact_secrets, sha256_json
from .clinical_text import clinical_text_safety_warnings, organize_clinical_text
from .engine import (
    DEVELOPMENT_EXECUTION_GRAPH_VERSION,
    DEVELOPMENT_RESULT_SCHEMA_VERSION,
    DEVELOPMENT_TRACE_VERSION,
    EXECUTION_GRAPH_VERSION,
    TRACE_VERSION,
    RunEngine,
    _model_event_payload,
    as_utc,
    build_execution_manifest,
    immutable_run_manifest_hash,
    provider_from_row,
    provider_transfer_target,
    trace_safe_payload,
)
from .errors import APIError, ProviderInvocationError
from .metrics import evaluate_result, summarize_metrics
from .models import (
    AggregatedResult,
    CaseCreate,
    CaseDataOrigin,
    CaseRead,
    CaseUpdate,
    ClinicalEventCreate,
    ClinicalEventRead,
    ClinicalFactPreviewItem,
    ClinicalFactsPreviewRequest,
    ClinicalFactsPreviewResponse,
    ClinicalTextOrganizeRequest,
    ClinicalTextOrganizeResponse,
    DataBoundary,
    DevelopmentDemoRunCreate,
    DevelopmentRunCreate,
    EvaluationCreate,
    EvaluationLabel,
    EvaluationRead,
    GovernanceConfig,
    ProviderCreate,
    ProviderKind,
    ProviderPublic,
    ProviderTestRequest,
    ProviderUpdate,
    RunCreate,
    RunMode,
    RunRead,
    new_id,
    utc_now,
)
from .network_security import validate_outbound_url
from .security import SecretStore


router = APIRouter()


RUN_BLOCKING_TEXT_CODES = {
    "future_timestamp_in_text",
    "possible_pathogen_label_leakage",
}


def _text_values(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    return []


def _snapshot_blocking_text_codes(snapshot: Dict[str, Any], decision_time: datetime) -> List[str]:
    chunks = _text_values(snapshot.get("case", {}))
    for event in snapshot.get("events", []):
        chunks.extend(_text_values(event.get("data", {})))
        if event.get("kind") == "microbiology":
            chunks.append("病原学确诊")
    warnings = clinical_text_safety_warnings("\n".join(chunks), decision_time)
    return sorted({
        warning.code for warning in warnings
        if warning.code in RUN_BLOCKING_TEXT_CODES or warning.code.startswith("possible_direct_identifier")
    })


def _source_case_safety(
    db: Database, case: Dict[str, Any], decision_time: datetime
) -> tuple[List[str], List[str]]:
    """Scan locally stored source text before constructing the model snapshot.

    The scanner is defense-in-depth.  The stronger boundary is structural:
    raw provenance is never copied into the snapshot sent to a model.
    """
    rows = db.fetchall(
        """SELECT kind, data_json, quality_json FROM clinical_events
           WHERE case_id = ? AND visible_at <= ? AND status != 'entered_in_error'
           ORDER BY sequence""",
        (case["id"], decision_time.isoformat()),
    )
    source_view = {
        "case": {
            "context": json_loads(case["context_json"], {}),
            "demographics": json_loads(case["demographics_json"], {}),
        },
        "events": [{
            "kind": row["kind"],
            "data": json_loads(row["data_json"], {}),
            "quality": json_loads(row["quality_json"], {}),
        } for row in rows],
    }
    codes = _snapshot_blocking_text_codes(source_view, decision_time)
    if any(row["kind"] == "microbiology" for row in rows):
        codes.append("phase_excluded_microbiology")
    raw_hashes = []
    for row in rows:
        data = json_loads(row["data_json"], {})
        raw = data.get("deidentified_note")
        if isinstance(raw, str) and raw:
            raw_hashes.append(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return sorted(set(codes)), raw_hashes


def _db(request: Request) -> Database:
    return request.app.state.db


def _engine(request: Request) -> RunEngine:
    return request.app.state.engine


def _secrets(request: Request) -> SecretStore:
    return request.app.state.secrets


def _actor(value: Optional[str]) -> str:
    actor = (value or "clinician-ui").strip()
    return actor[:120] or "clinician-ui"


def _provider_public(row: Dict[str, Any]) -> ProviderPublic:
    provider = provider_from_row(row)
    return ProviderPublic(
        id=provider["id"],
        name=provider["name"],
        kind=provider["kind"],
        model=provider["model"],
        base_url=provider["base_url"],
        enabled=provider["enabled"],
        data_boundary=provider["data_boundary"],
        weight=provider["weight"],
        has_api_key=bool(provider.get("encrypted_api_key")),
        extra_header_names=sorted(provider["extra_headers"].keys()),
        options=provider["options"],
        last_test_ok=(bool(provider["last_test_ok"]) if provider.get("last_test_ok") is not None else None),
        last_tested_at=provider.get("last_tested_at"),
        last_test_latency_ms=provider.get("last_test_latency_ms"),
        last_test_error_code=provider.get("last_test_error_code"),
        created_at=provider["created_at"],
        updated_at=provider["updated_at"],
    )


def _case_read(row: Dict[str, Any]) -> CaseRead:
    return CaseRead(
        id=row["id"], case_alias=row["case_alias"],
        demographics=json_loads(row["demographics_json"], {}),
        context=json_loads(row["context_json"], {}),
        external_data_consent=bool(row["external_data_consent"]),
        data_origin=row.get("data_origin") or CaseDataOrigin.CLINICAL.value,
        status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _event_read(row: Dict[str, Any]) -> ClinicalEventRead:
    return ClinicalEventRead(
        id=row["id"], case_id=row["case_id"], sequence=row["sequence"], kind=row["kind"],
        occurred_at=row["occurred_at"], collected_at=row["collected_at"], issued_at=row["issued_at"],
        visible_at=row["visible_at"], source=row["source"], status=row["status"],
        data=json_loads(row["data_json"], {}), quality=json_loads(row["quality_json"], {}),
        created_at=row["created_at"],
    )


def _run_read(row: Dict[str, Any]) -> RunRead:
    status = row["status"]
    result_payload = json_loads(row["result_json"]) if row["result_json"] else None
    error_payload = json_loads(row["error_json"]) if row["error_json"] else None
    integrity_errors: List[str] = []
    if row.get("input_snapshot_json") and row.get("input_snapshot_sha256"):
        if sha256_json(json_loads(row["input_snapshot_json"], {})) != row["input_snapshot_sha256"]:
            integrity_errors.append("input_snapshot_hash_mismatch")
    provider_configs = json_loads(row.get("provider_configs_json"), [])
    governance_config = json_loads(row.get("governance_config_json"), {})
    provider_configs_hash = sha256_json(provider_configs)
    governance_config_hash = sha256_json(governance_config)
    if row.get("provider_configs_sha256") and provider_configs_hash != row["provider_configs_sha256"]:
        integrity_errors.append("provider_configs_hash_mismatch")
    if row.get("governance_config_sha256") and governance_config_hash != row["governance_config_sha256"]:
        integrity_errors.append("governance_config_hash_mismatch")
    if row.get("run_manifest_sha256"):
        actual_manifest = immutable_run_manifest_hash(
            case_id=row["case_id"],
            decision_time=row["decision_time"],
            run_mode=row.get("run_mode") or "live",
            retrospective_anchor_id=row.get("retrospective_anchor_id"),
            provider_ids=json_loads(row.get("provider_ids_json"), []),
            include_baseline=bool(row["include_baseline"]),
            input_snapshot_sha256=row.get("input_snapshot_sha256") or "",
            provider_configs_sha256=row.get("provider_configs_sha256") or provider_configs_hash,
            governance_config_sha256=row.get("governance_config_sha256") or governance_config_hash,
            clinical_review=json_loads(row.get("clinical_review_json"), {}),
            data_transfer_consent=json_loads(row.get("data_transfer_consent_json")) if row.get("data_transfer_consent_json") else None,
        )
        if actual_manifest != row["run_manifest_sha256"]:
            integrity_errors.append("run_manifest_hash_mismatch")
    if row.get("execution_manifest_json") or row.get("execution_manifest_sha256"):
        try:
            execution_manifest = json_loads(row.get("execution_manifest_json"), {})
        except (TypeError, ValueError):
            execution_manifest = {}
            integrity_errors.append("execution_manifest_invalid_json")
        if sha256_json(execution_manifest) != row.get("execution_manifest_sha256"):
            integrity_errors.append("execution_manifest_hash_mismatch")
    if result_payload is not None and row.get("result_sha256"):
        unsigned = dict(result_payload)
        unsigned["result_sha256"] = None
        if sha256_json(unsigned) != row["result_sha256"] or result_payload.get("result_sha256") != row["result_sha256"]:
            integrity_errors.append("result_hash_mismatch")
    if integrity_errors:
        status = "failed"
        result_payload = None
        error_payload = {
            "code": "run_integrity_failure",
            "message": "Stored run content failed its integrity hash check",
            "details": integrity_errors,
        }
    return RunRead(
        id=row["id"], case_id=row["case_id"], decision_time=row["decision_time"],
        requested_at=row["requested_at"],
        run_mode=row.get("run_mode") or RunMode.LIVE.value,
        retrospective_anchor_id=row.get("retrospective_anchor_id"), status=status,
        provider_ids=json_loads(row["provider_ids_json"], []), include_baseline=bool(row["include_baseline"]),
        governance_version=row["governance_version"],
        schema_version=row.get("schema_version") or "owlpath.result.v2",
        engine_version=row.get("engine_version") or "0.1.0-research",
        input_snapshot_sha256=row.get("input_snapshot_sha256"),
        execution_graph_version=row.get("execution_graph_version"),
        execution_manifest_sha256=row.get("execution_manifest_sha256"),
        trace_version=row.get("trace_version"),
        result_sha256=row.get("result_sha256"),
        result=result_payload,
        error=error_payload,
        completed_at=row["completed_at"],
        clinical_review=json_loads(row.get("clinical_review_json")) if row.get("clinical_review_json") else None,
        data_transfer_consent=json_loads(row.get("data_transfer_consent_json")) if row.get("data_transfer_consent_json") else None,
    )


def _evaluation_read(row: Dict[str, Any]) -> EvaluationRead:
    return EvaluationRead(
        id=row["id"], run_id=row["run_id"], case_id=row["case_id"],
        label=json_loads(row["label_json"], {}), metrics=json_loads(row["metrics_json"], {}),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _audit_public(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "details_json"}
    result["details"] = json_loads(row.get("details_json"), {})
    return result


def _architecture_config_path(request: Request) -> Path:
    settings_base = Path(request.app.state.settings.base_dir)
    candidates = [
        settings_base / "config" / "agent_architecture.v1.json",
        settings_base.parent / "config" / "agent_architecture.v1.json",
        Path(__file__).resolve().parents[2] / "config" / "agent_architecture.v1.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise APIError(
        503,
        "architecture_config_unavailable",
        "The versioned architecture configuration is unavailable",
    )


def _trace_node_public(row: Dict[str, Any]) -> Dict[str, Any]:
    metadata = trace_safe_payload(json_loads(row.get("metadata_json"), {}))
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "node_key": row["node_key"],
        "node_kind": row["node_kind"],
        "display_name_i18n": trace_safe_payload(json_loads(row.get("display_name_json"), {})),
        "parent_node_id": row.get("parent_node_id"),
        "provider_id": row.get("provider_id"),
        "provider_model": row.get("provider_model"),
        "status": row["status"],
        "outcome": row.get("outcome"),
        "role": metadata.get("role"),
        "version": metadata.get("version"),
        "sequence": row["sequence"],
        "attempt": row["attempt"],
        "input_artifact_id": row.get("input_artifact_id"),
        "output_artifact_id": row.get("output_artifact_id"),
        "error": trace_safe_payload(json_loads(row.get("error_json"), {})) if row.get("error_json") else None,
        "metadata": metadata,
        "started_at": row["started_at"],
        "completed_at": row.get("completed_at"),
        "latency_ms": row.get("latency_ms"),
    }


def _trace_artifact_public(row: Dict[str, Any], allow_demo_safe: bool = False) -> Dict[str, Any]:
    try:
        parsed = json_loads(row.get("content_json"), {})
        if row.get("visibility") == "demo_safe" and allow_demo_safe:
            content = redact_secrets(parsed)
        elif row.get("visibility") == "demo_safe":
            content = None
        else:
            content = trace_safe_payload(parsed)
    except (TypeError, ValueError):
        content = None
    integrity_ok = content is not None and sha256_json(content) == row.get("content_sha256")
    return {
        "id": row["id"],
        "node_run_id": row["node_run_id"],
        "direction": row["direction"],
        "artifact_type": row["artifact_type"],
        "schema_version": row["schema_version"],
        "content_sha256": row["content_sha256"],
        "content": content if integrity_ok else None,
        "integrity_ok": integrity_ok,
        "visibility": row["visibility"],
        "created_at": row["created_at"],
    }


def _trace_node_detail_payload(db: Database, node: Dict[str, Any]) -> Dict[str, Any]:
    run_contract = db.fetchone(
        """SELECT runs.run_mode, cases.data_origin FROM runs
           JOIN cases ON cases.id = runs.case_id WHERE runs.id = ?""",
        (node["run_id"],),
    )
    allow_demo_safe = bool(
        run_contract
        and run_contract.get("run_mode") == RunMode.DEVELOPMENT_DEMO.value
        and run_contract.get("data_origin") == CaseDataOrigin.SYNTHETIC.value
    )
    artifacts = db.fetchall(
        """SELECT id, node_run_id, direction, artifact_type, schema_version,
                  content_json, content_sha256, visibility, created_at
           FROM run_node_artifacts WHERE node_run_id = ? ORDER BY created_at, id""",
        (node["id"],),
    )
    return {
        "node": _trace_node_public(node),
        "artifacts": [
            _trace_artifact_public(artifact, allow_demo_safe=allow_demo_safe)
            for artifact in artifacts
        ],
        "trace_privacy": {
            "raw_provider_response_exposed": False,
            "api_credentials_exposed": False,
            "synthetic_demo_source_expandable": allow_demo_safe,
        },
    }


def _safe_model_prediction_for_clinical_view(
    normalized_json: Optional[str], result_json: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Apply the independent adjudicator's resolution to model comparisons."""
    if not normalized_json or not result_json:
        return None
    normalized = json_loads(normalized_json, {})
    result = json_loads(result_json, {})
    action = result.get("safety_action")
    if action in {"abstain", "non_infection"}:
        return None
    if action == "species_set":
        # The current online engine cannot reach this state without a validated
        # calibrator hook, but keep the policy branch explicit.
        safe = dict(normalized)
        safe["summary"] = "模型叙述已隐藏；仅显示通过独立安全裁决的结构化候选。"
        return safe
    if action not in {"category_only", "next_test"}:
        return None
    allowed_categories = {"bacteria", "virus", "fungus", "parasite", "other", "unknown"}
    grouped: Dict[str, List[float]] = {}
    for candidate in normalized.get("candidates") or []:
        category = str(candidate.get("category") or "unknown").lower()
        if category not in allowed_categories:
            category = "unknown"
        grouped.setdefault(category, []).append(float(candidate.get("probability") or 0.0))
    candidates = [{
        "canonical_id": "category:%s" % category,
        "name": category,
        "rank_level": "category",
        "category": category,
        "genus": None,
        "species": None,
        "probability": min(1.0, 1.0 - math.prod(1.0 - max(0.0, min(1.0, value)) for value in values)),
        "calibration_status": "uncalibrated_model_score",
        "evidence_for": [],
        "evidence_against": [],
    } for category, values in grouped.items()]
    candidates.sort(key=lambda item: item["probability"], reverse=True)
    return {
        "summary": "原始模型物种级叙述已按独立安全裁决隐藏。",
        "infection_probability": normalized.get("infection_probability", 0.0),
        "syndrome_probabilities": normalized.get("syndrome_probabilities") or {},
        "candidates": candidates,
        "coinfection_probability": normalized.get("coinfection_probability", 0.0),
        "coinfection_pairs": [],
        "unknown_probability": normalized.get("unknown_probability", 1.0),
        "next_tests": [],
        "data_quality_warnings": [],
        "distribution_shift_warning": bool(normalized.get("distribution_shift_warning")),
        "abstain": bool(normalized.get("abstain")),
        "abstain_reason": None,
    }


def _validate_provider_destination(
    base_url: Optional[str], boundary: DataBoundary, kind: Optional[ProviderKind] = None
) -> None:
    if not base_url:
        if kind == ProviderKind.OPENAI_COMPATIBLE:
            raise APIError(
                422,
                "openai_compatible_base_url_required",
                "An OpenAI-compatible provider must specify its absolute Base URL",
            )
        if boundary == DataBoundary.LOCAL and kind not in {ProviderKind.OLLAMA}:
            raise APIError(
                422,
                "local_boundary_requires_private_url",
                "A non-Ollama provider marked local must specify a loopback or private-network base URL",
            )
        return
    try:
        validate_outbound_url(base_url, boundary, resolve_dns=False)
    except ProviderInvocationError as exc:
        raise APIError(422, exc.code, exc.safe_message)


COMMERCIAL_KEY_PROVIDER_KINDS = {
    ProviderKind.OPENAI_RESPONSES,
    ProviderKind.ANTHROPIC_MESSAGES,
    ProviderKind.GEMINI_GENERATE_CONTENT,
}


@router.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    db = _db(request)
    db.fetchone("SELECT 1 AS ok")
    return {
        "status": "ok", "service": "OwlPath（鸮径）", "version": "0.1.0-research",
        "clinical_validation": "not_validated", "research_only": True,
    }


@router.get("/architecture")
async def get_architecture(request: Request) -> Dict[str, Any]:
    """Return the versioned current/target architecture declaration verbatim."""
    path = _architecture_config_path(request)
    try:
        payload = json_loads(path.read_text(encoding="utf-8"), {})
    except (OSError, TypeError, ValueError):
        raise APIError(
            503,
            "architecture_config_invalid",
            "The versioned architecture configuration could not be read safely",
        )
    if not isinstance(payload, dict) or not payload.get("schema_version") or not payload.get("views"):
        raise APIError(
            503,
            "architecture_config_invalid",
            "The versioned architecture configuration does not match the required shape",
        )
    return payload


@router.post("/clinical-text/organize", response_model=ClinicalTextOrganizeResponse)
async def organize_text(payload: ClinicalTextOrganizeRequest) -> ClinicalTextOrganizeResponse:
    """Compile pasted clinical text locally without persistence or model calls."""
    organized = organize_clinical_text(payload)
    preview = []
    for index, event in enumerate(organized.events):
        data, quality = _model_event_payload(event.kind.value, event.data, event.quality)
        if data:
            preview.append(ClinicalFactPreviewItem(
                event_index=index, kind=event.kind,
                occurred_at=as_utc(event.occurred_at), visible_at=as_utc(event.visible_at),
                collected_at=as_utc(event.collected_at) if event.collected_at else None,
                issued_at=as_utc(event.issued_at) if event.issued_at else None,
                status=event.status, data=data, quality=quality,
            ))
    return organized.model_copy(update={"model_fact_preview": preview})


@router.post("/clinical-facts/preview", response_model=ClinicalFactsPreviewResponse)
async def preview_clinical_facts(payload: ClinicalFactsPreviewRequest) -> ClinicalFactsPreviewResponse:
    """Preview the exact atomic fact boundary without persistence/model calls."""
    facts: List[ClinicalFactPreviewItem] = []
    excluded = []
    for index, event in enumerate(payload.events):
        data, quality = _model_event_payload(event.kind.value, event.data, event.quality)
        if not data or event.kind.value == "microbiology":
            excluded.append(index)
            continue
        facts.append(ClinicalFactPreviewItem(
            event_index=index, kind=event.kind,
            occurred_at=as_utc(event.occurred_at), visible_at=as_utc(event.visible_at),
            collected_at=as_utc(event.collected_at) if event.collected_at else None,
            issued_at=as_utc(event.issued_at) if event.issued_at else None,
            status=event.status, data=data, quality=quality,
        ))
    return ClinicalFactsPreviewResponse(facts=facts, excluded_event_indexes=excluded)


@router.get("/providers", response_model=List[ProviderPublic])
async def list_providers(request: Request) -> List[ProviderPublic]:
    return [_provider_public(row) for row in _db(request).fetchall("SELECT * FROM providers ORDER BY created_at")]


@router.post("/providers", response_model=ProviderPublic, status_code=201)
async def create_provider(
    payload: ProviderCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> ProviderPublic:
    boundary = payload.data_boundary or DataBoundary.EXTERNAL
    _validate_provider_destination(payload.base_url, boundary, payload.kind)
    if payload.enabled:
        raise APIError(
            422,
            "provider_requires_successful_test_before_enable",
            "Create the provider disabled, pass a synthetic connection test, then enable it explicitly",
        )
    provider_id = new_id("prv")
    now = utc_now().isoformat()
    encrypted = _secrets(request).encrypt(payload.api_key.get_secret_value() if payload.api_key else None)
    _db(request).execute(
        """INSERT INTO providers
           (id, name, kind, model, base_url, encrypted_api_key, enabled, data_boundary, weight,
            extra_headers_json, options_json, created_at, updated_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (provider_id, payload.name, payload.kind.value, payload.model, payload.base_url, encrypted,
         int(payload.enabled), boundary.value, payload.weight, json_dumps(payload.extra_headers),
         json_dumps(payload.options), now, now),
    )
    _db(request).audit(_actor(x_actor), "provider.created", "provider", provider_id, {
        "kind": payload.kind.value, "model": payload.model, "data_boundary": boundary.value,
        "has_api_key": bool(encrypted),
    })
    row = _db(request).fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
    assert row is not None
    return _provider_public(row)


@router.get("/providers/{provider_id}", response_model=ProviderPublic)
async def get_provider(provider_id: str, request: Request) -> ProviderPublic:
    row = _db(request).fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
    if row is None:
        raise APIError(404, "provider_not_found", "Provider was not found")
    return _provider_public(row)


@router.patch("/providers/{provider_id}", response_model=ProviderPublic)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> ProviderPublic:
    db = _db(request)
    row = db.fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
    if row is None:
        raise APIError(404, "provider_not_found", "Provider was not found")
    current = provider_from_row(row)
    updates = payload.model_dump(exclude_unset=True)
    boundary = updates.get("data_boundary", DataBoundary(current["data_boundary"]))
    if isinstance(boundary, str):
        boundary = DataBoundary(boundary)
    base_url = updates.get("base_url", current["base_url"])
    _validate_provider_destination(base_url, boundary, ProviderKind(current["kind"]))
    name = updates.get("name", current["name"])
    model = updates.get("model", current["model"])
    enabled = updates.get("enabled", current["enabled"])
    weight = updates.get("weight", current["weight"])
    headers = updates.get("extra_headers", current["extra_headers"])
    options = updates.get("options", current["options"])
    encrypted = current.get("encrypted_api_key")
    if payload.clear_api_key:
        encrypted = None
        enabled = False
    if payload.api_key is not None:
        encrypted = _secrets(request).encrypt(payload.api_key.get_secret_value())
    provider_kind = ProviderKind(current["kind"])
    connection_changed = bool(
        model != current["model"]
        or base_url != current["base_url"]
        or boundary.value != current["data_boundary"]
        or headers != current["extra_headers"]
        or options != current["options"]
        or payload.api_key is not None
        or payload.clear_api_key
    )
    last_test_ok = current.get("last_test_ok")
    last_tested_at = current.get("last_tested_at")
    last_test_latency_ms = current.get("last_test_latency_ms")
    last_test_error_code = current.get("last_test_error_code")
    if connection_changed:
        enabled = False
        last_test_ok = None
        last_tested_at = None
        last_test_latency_ms = None
        last_test_error_code = None
    if enabled and not bool(last_test_ok):
        raise APIError(
            422,
            "provider_requires_successful_test_before_enable",
            "A provider must pass a synthetic connection test before it can be enabled",
        )
    if (
        enabled
        and boundary == DataBoundary.EXTERNAL
        and provider_kind in COMMERCIAL_KEY_PROVIDER_KINDS
        and not encrypted
    ):
        raise APIError(422, "enabled_cloud_provider_requires_api_key", "An enabled commercial cloud provider requires an API key")
    now = utc_now().isoformat()
    db.execute(
        """UPDATE providers SET name = ?, model = ?, base_url = ?, encrypted_api_key = ?, enabled = ?,
           data_boundary = ?, weight = ?, extra_headers_json = ?, options_json = ?,
           last_test_ok = ?, last_tested_at = ?, last_test_latency_ms = ?, last_test_error_code = ?,
           updated_at = ?, revision = revision + 1 WHERE id = ?""",
        (name, model, base_url, encrypted, int(enabled), boundary.value, weight, json_dumps(headers),
         json_dumps(options), last_test_ok, last_tested_at, last_test_latency_ms,
         last_test_error_code, now, provider_id),
    )
    db.audit(_actor(x_actor), "provider.updated", "provider", provider_id, {
        "changed_fields": sorted(payload.model_fields_set), "data_boundary": boundary.value,
        "has_api_key": bool(encrypted),
    })
    updated = db.fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
    assert updated is not None
    return _provider_public(updated)


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    payload: ProviderTestRequest,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> Dict[str, Any]:
    if not payload.confirm_possible_cost:
        raise APIError(
            422, "provider_test_cost_confirmation_required",
            "A live provider test may incur model charges; confirm_possible_cost must be true.",
        )
    db = _db(request)
    row = db.fetchone("SELECT * FROM providers WHERE id = ?", (provider_id,))
    if row is None:
        raise APIError(404, "provider_not_found", "Provider was not found")
    provider = provider_from_row(row)
    tested_revision = int(row["revision"])
    synthetic_snapshot = {
        "decision_time": utc_now().isoformat(),
        "time_rule": "Synthetic connectivity/schema test only.",
        "case": {"case_alias": "provider-test", "demographics": {"sex": "unknown"},
                 "context": {"primary_syndrome": "other"}},
        "events": [{"event_id": "synthetic", "sequence": 1, "kind": "other",
                    "source": "owlpath-provider-test", "status": "final",
                    "data": {"note": "Synthetic provider schema test; no patient data."}, "quality": {}}],
        "excluded_event_manifest": [], "excluded_event_count": 0,
    }
    started = time.perf_counter()
    try:
        key = _secrets(request).decrypt(provider.get("encrypted_api_key"))
        prediction, _ = await request.app.state.provider_client.invoke(provider, key, synthetic_snapshot)
        latency = int((time.perf_counter() - started) * 1000)
        result = {"ok": True, "latency_ms": latency, "schema_valid": True,
                  "provider_id": provider_id, "model": provider["model"]}
    except ProviderInvocationError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        result = {"ok": False, "latency_ms": latency, "schema_valid": False,
                  "provider_id": provider_id, "model": provider["model"],
                  "error": exc.safe_payload()}
    except Exception:
        latency = int((time.perf_counter() - started) * 1000)
        result = {"ok": False, "latency_ms": latency, "schema_valid": False,
                  "provider_id": provider_id, "model": provider["model"],
                  "error": {"code": "provider_test_internal_error", "message": "Provider test failed safely", "retryable": False}}
    tested_at = utc_now().isoformat()
    applied = db.execute_rowcount(
        """UPDATE providers SET enabled = ?, last_test_ok = ?, last_tested_at = ?,
           last_test_latency_ms = ?, last_test_error_code = ?, updated_at = ?,
           revision = revision + 1 WHERE id = ? AND revision = ?""",
        (
            int(bool(provider["enabled"]) and bool(result["ok"])),
            int(bool(result["ok"])), tested_at, result["latency_ms"],
            result.get("error", {}).get("code"), tested_at, provider_id, tested_revision,
        ),
    )
    if applied != 1:
        db.audit(_actor(x_actor), "provider.test_superseded", "provider", provider_id, {
            "synthetic_data_only": True,
            "possible_cost_confirmed": True,
        })
        raise APIError(
            409,
            "provider_test_superseded",
            "Provider settings changed while the test was running; the old result was discarded. Test the current settings again.",
        )
    db.audit(_actor(x_actor), "provider.tested", "provider", provider_id, {
        "ok": result["ok"], "latency_ms": result["latency_ms"],
        "error_code": result.get("error", {}).get("code"), "synthetic_data_only": True,
        "possible_cost_confirmed": True,
    })
    return result


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> None:
    db = _db(request)
    row = db.fetchone("SELECT id, name FROM providers WHERE id = ?", (provider_id,))
    if row is None:
        raise APIError(404, "provider_not_found", "Provider was not found")
    db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    db.audit(_actor(x_actor), "provider.deleted", "provider", provider_id, {"name": row["name"]})


@router.get("/cases", response_model=List[CaseRead])
async def list_cases(request: Request) -> List[CaseRead]:
    return [_case_read(row) for row in _db(request).fetchall("SELECT * FROM cases ORDER BY created_at DESC")]


@router.post("/cases", response_model=CaseRead, status_code=201)
async def create_case(
    payload: CaseCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> CaseRead:
    case_id = new_id("case")
    now = utc_now().isoformat()
    _db(request).execute(
        """INSERT INTO cases
           (id, case_alias, demographics_json, context_json, external_data_consent, data_origin,
            status, created_at, updated_at)
           VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (case_id, payload.case_alias, json_dumps(payload.demographics.model_dump(mode="json")),
         json_dumps(payload.context.model_dump(mode="json")), int(payload.external_data_consent),
         payload.data_origin.value, now, now),
    )
    _db(request).audit(_actor(x_actor), "case.created", "case", case_id, {
        "external_data_consent": payload.external_data_consent,
        "privacy_contract": "case_alias_and_deidentified_events_only",
    })
    row = _db(request).fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    assert row is not None
    return _case_read(row)


@router.get("/cases/{case_id}", response_model=CaseRead)
async def get_case(case_id: str, request: Request) -> CaseRead:
    row = _db(request).fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    if row is None:
        raise APIError(404, "case_not_found", "Case was not found")
    return _case_read(row)


@router.get("/cases/{case_id}/snapshot-hash")
async def get_case_snapshot_hash(
    case_id: str,
    request: Request,
    decision_time: Optional[datetime] = Query(default=None),
) -> Dict[str, Any]:
    case = _db(request).fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    if case is None:
        raise APIError(404, "case_not_found", "Case was not found")
    if decision_time is not None and decision_time.tzinfo is None:
        raise APIError(422, "timezone_required", "decision_time must include an explicit timezone offset or Z")
    cutoff = as_utc(decision_time or utc_now())
    snapshot = _engine(request).snapshot_case(case_id, cutoff)
    source_codes, _ = _source_case_safety(_db(request), case, cutoff)
    return {
        "input_snapshot_sha256": sha256_json(snapshot),
        "decision_time": cutoff.isoformat(),
        "included_event_count": len(snapshot["events"]),
        "excluded_event_count": snapshot["excluded_event_count"],
        "blocking_warning_codes": source_codes,
    }


@router.patch("/cases/{case_id}", response_model=CaseRead)
async def update_case(
    case_id: str,
    payload: CaseUpdate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> CaseRead:
    db = _db(request)
    row = db.fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    if row is None:
        raise APIError(404, "case_not_found", "Case was not found")
    updates = payload.model_dump(exclude_unset=True, mode="json")
    now = utc_now().isoformat()
    db.execute(
        """UPDATE cases SET case_alias = ?, demographics_json = ?, context_json = ?,
           external_data_consent = ?, status = ?, updated_at = ? WHERE id = ?""",
        (updates.get("case_alias", row["case_alias"]),
         json_dumps(updates["demographics"]) if "demographics" in updates else row["demographics_json"],
         json_dumps(updates["context"]) if "context" in updates else row["context_json"],
         int(updates.get("external_data_consent", bool(row["external_data_consent"]))),
         updates.get("status", row["status"]), now, case_id),
    )
    db.audit(_actor(x_actor), "case.updated", "case", case_id, {
        "changed_fields": sorted(payload.model_fields_set),
        "external_data_consent": updates.get("external_data_consent", bool(row["external_data_consent"])),
    })
    updated = db.fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    assert updated is not None
    return _case_read(updated)


@router.delete("/cases/{case_id}", status_code=204)
async def delete_unstarted_case(
    case_id: str,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> None:
    """Best-effort rollback for a case whose run creation never completed.

    Once any run references the case it becomes part of the immutable audit
    trail and this endpoint refuses deletion.
    """
    db = _db(request)
    row = db.fetchone("SELECT id FROM cases WHERE id = ?", (case_id,))
    if row is None:
        raise APIError(404, "case_not_found", "Case was not found")
    run_count = db.fetchone("SELECT COUNT(*) AS n FROM runs WHERE case_id = ?", (case_id,))
    if run_count and int(run_count["n"]) > 0:
        raise APIError(409, "case_has_runs", "A case with an audit run cannot be deleted")
    event_count = db.fetchone("SELECT COUNT(*) AS n FROM clinical_events WHERE case_id = ?", (case_id,))
    db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    db.audit(_actor(x_actor), "case.rolled_back_before_run", "case", case_id, {
        "deleted_event_count": int(event_count["n"]) if event_count else 0,
    })


@router.get("/cases/{case_id}/events", response_model=List[ClinicalEventRead])
async def list_events(case_id: str, request: Request) -> List[ClinicalEventRead]:
    if _db(request).fetchone("SELECT id FROM cases WHERE id = ?", (case_id,)) is None:
        raise APIError(404, "case_not_found", "Case was not found")
    rows = _db(request).fetchall("SELECT * FROM clinical_events WHERE case_id = ? ORDER BY sequence", (case_id,))
    return [_event_read(row) for row in rows]


@router.post("/cases/{case_id}/events", response_model=ClinicalEventRead, status_code=201)
async def create_event(
    case_id: str,
    payload: ClinicalEventCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> ClinicalEventRead:
    db = _db(request)
    if db.fetchone("SELECT id FROM cases WHERE id = ?", (case_id,)) is None:
        raise APIError(404, "case_not_found", "Case was not found")
    event_id = new_id("evt")
    now = utc_now().isoformat()
    with db.connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM clinical_events WHERE case_id = ?", (case_id,)).fetchone()
        sequence = int(row["sequence"])
        conn.execute(
            """INSERT INTO clinical_events
               (id, case_id, sequence, kind, occurred_at, collected_at, issued_at, visible_at,
                source, status, data_json, quality_json, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, case_id, sequence, payload.kind.value, as_utc(payload.occurred_at).isoformat(),
             as_utc(payload.collected_at).isoformat() if payload.collected_at else None,
             as_utc(payload.issued_at).isoformat() if payload.issued_at else None,
             as_utc(payload.visible_at).isoformat(), payload.source, payload.status.value,
             json_dumps(payload.data), json_dumps(payload.quality), now),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        conn.commit()
    db.audit(_actor(x_actor), "event.created", "clinical_event", event_id, {
        "case_id": case_id, "sequence": sequence, "kind": payload.kind.value,
        "visible_at": as_utc(payload.visible_at).isoformat(),
    })
    row = db.fetchone("SELECT * FROM clinical_events WHERE id = ?", (event_id,))
    assert row is not None
    return _event_read(row)


@router.get("/runs", response_model=List[RunRead])
async def list_runs(
    request: Request,
    case_id: Optional[str] = Query(default=None),
    trace_version: Optional[str] = Query(default=None, min_length=1, max_length=80),
) -> List[RunRead]:
    clauses: List[str] = []
    params: List[Any] = []
    if case_id:
        clauses.append("case_id = ?")
        params.append(case_id)
    if trace_version:
        clauses.append("trace_version = ?")
        params.append(trace_version)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = _db(request).fetchall(
        "SELECT * FROM runs%s ORDER BY requested_at DESC LIMIT 500" % where,
        params,
    )
    return [_run_read(row) for row in rows]


@router.post("/development/runs", response_model=RunRead, status_code=202)
@router.post("/development-demo/runs", response_model=RunRead, status_code=202)
async def create_development_demo_run(
    payload: DevelopmentRunCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> RunRead:
    """Run a full real-provider orchestration using explicitly synthetic text.

    This endpoint is intentionally separate from the clinical and retrospective
    run contract.  It keeps provider readiness, outbound-network security,
    strict provider response schemas, immutable snapshots and manifest hashes,
    while removing clinical-only review, consent, T0, scope and leakage gates.
    """
    db = _db(request)
    provider_ids = list(dict.fromkeys(item.strip() for item in payload.provider_ids))
    placeholders = ",".join("?" for _ in provider_ids)
    provider_rows = db.fetchall(
        "SELECT * FROM providers WHERE id IN (%s)" % placeholders,
        provider_ids,
    )
    found = {row["id"] for row in provider_rows}
    missing = [item for item in provider_ids if item not in found]
    disabled = [row["id"] for row in provider_rows if not bool(row["enabled"])]
    if missing or disabled:
        raise APIError(
            422,
            "providers_unavailable",
            "Every development-demo provider must exist and be enabled",
            {"missing": missing, "disabled": disabled},
        )
    unverified = [row["id"] for row in provider_rows if not bool(row.get("last_test_ok"))]
    if unverified:
        raise APIError(
            422,
            "providers_unverified",
            "Every development-demo provider must pass a real synthetic connection test before use",
            {"unverified": unverified},
        )
    # Priority is a frozen runtime property, not a browser ordering accident.
    # Higher-weight providers receive synthesis and earlier specialist roles.
    provider_rows.sort(key=lambda row: (-float(row.get("weight") or 0.0), str(row["id"])))
    provider_ids = [row["id"] for row in provider_rows]

    decision_time = utc_now()
    source_hash = hashlib.sha256(payload.text.encode("utf-8")).hexdigest()
    organized = None
    organizer_error = False
    try:
        organized = organize_clinical_text(ClinicalTextOrganizeRequest(
            text=payload.text,
            decision_time=decision_time,
            source="development-demo",
        ))
    except Exception:
        # The remote-provider demo must still exercise the complete API path if
        # the optional local convenience parser encounters an unexpected input.
        # The exact synthetic text remains available in the frozen snapshot.
        organizer_error = True

    if organized is not None:
        draft = organized.case_draft
        sex = draft.demographics.sex
        if sex not in {"male", "female"}:
            sex = "unknown"
        care_setting = {
            "inpatient": "ward",
            "outpatient": "outpatient",
            "emergency": "emergency",
            "icu": "icu",
        }.get(draft.demographics.encounter_type or "", "other")
        primary_syndrome = {
            "lower_respiratory": "respiratory",
            "bloodstream": "bloodstream",
            "urinary": "urinary",
            "cns": "central_nervous_system",
            "abdominal": "other",
            "undifferentiated": "other",
        }[draft.scenario]
        demographics = {
            "age_years": draft.demographics.age,
            "sex": sex,
            "pregnant": draft.demographics.pregnant,
            "immunocompromised": draft.demographics.immunocompromised,
            "region_code": None,
            "care_setting": care_setting,
        }
        context = {
            "primary_syndrome": primary_syndrome,
            "acquisition_context": draft.acquisition_context,
            "institution_code": None,
            "unit_code": None,
            "notes_deidentified": "Synthetic-only development demo; not a clinical case.",
        }
        organized_events = organized.events
        warning_codes = sorted({item.code for item in organized.warnings})
        parser_version = organized.parser_version
    else:
        demographics = {
            "age_years": None, "sex": "unknown", "pregnant": None,
            "immunocompromised": None, "region_code": None, "care_setting": "other",
        }
        context = {
            "primary_syndrome": "other", "acquisition_context": "unknown",
            "institution_code": None, "unit_code": None,
            "notes_deidentified": "Synthetic-only development demo; local organizer unavailable.",
        }
        organized_events = []
        warning_codes = ["development_demo_local_organizer_failed"]
        parser_version = None

    case_id = new_id("case")
    case_alias = "DEMO-%s" % case_id.split("_", 1)[-1][:16].upper()
    now = utc_now().isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO cases
               (id, case_alias, demographics_json, context_json, external_data_consent, data_origin,
                status, created_at, updated_at)
               VALUES(?, ?, ?, ?, 0, 'synthetic', 'active', ?, ?)""",
            (case_id, case_alias, json_dumps(demographics), json_dumps(context), now, now),
        )
        for sequence, event in enumerate(organized_events, start=1):
            conn.execute(
                """INSERT INTO clinical_events
                   (id, case_id, sequence, kind, occurred_at, collected_at, issued_at, visible_at,
                    source, status, data_json, quality_json, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id("evt"), case_id, sequence, event.kind.value,
                    as_utc(event.occurred_at).isoformat(),
                    as_utc(event.collected_at).isoformat() if event.collected_at else None,
                    as_utc(event.issued_at).isoformat() if event.issued_at else None,
                    as_utc(event.visible_at).isoformat(), event.source, event.status.value,
                    json_dumps(event.data), json_dumps(event.quality), now,
                ),
            )
        conn.commit()

    snapshot = _engine(request).snapshot_case(case_id, decision_time)
    snapshot.update({
        "run_mode": RunMode.DEVELOPMENT_DEMO.value,
        "development_demo": True,
        "synthetic_only": True,
        "not_for_clinical_use": True,
        "synthetic_source_text": payload.text,
        "synthetic_source_text_sha256": source_hash,
        "local_organizer": {
            "parser_version": parser_version,
            "warning_codes": warning_codes,
            "failed": organizer_error,
        },
        "specialist_config_version": payload.specialist_config_version,
    })
    snapshot_hash = sha256_json(snapshot)

    frozen_provider_configs = []
    for provider_row in provider_rows:
        frozen = provider_from_row(provider_row)
        frozen.pop("encrypted_api_key", None)
        frozen.pop("created_at", None)
        frozen.pop("updated_at", None)
        frozen_provider_configs.append(frozen)
    provider_configs_hash = sha256_json(frozen_provider_configs)
    governance = db.governance()
    governance_payload = governance.model_dump(mode="json")
    governance_hash = sha256_json(governance_payload)
    manifest_hash = immutable_run_manifest_hash(
        case_id=case_id,
        decision_time=decision_time.isoformat(),
        run_mode=RunMode.DEVELOPMENT_DEMO.value,
        retrospective_anchor_id=None,
        provider_ids=provider_ids,
        include_baseline=False,
        input_snapshot_sha256=snapshot_hash,
        provider_configs_sha256=provider_configs_hash,
        governance_config_sha256=governance_hash,
        clinical_review={},
        data_transfer_consent=None,
    )
    execution_manifest = build_execution_manifest(
        provider_ids,
        False,
        True,
        development_source_text=payload.text,
        development_specialist_config_version=payload.specialist_config_version,
    )
    execution_manifest_hash = sha256_json(execution_manifest)
    run_id = new_id("run")
    db.execute(
        """INSERT INTO runs
           (id, case_id, decision_time, run_mode, retrospective_anchor_id, requested_at, status,
            provider_ids_json, include_baseline, input_snapshot_json, provider_configs_json,
            provider_configs_sha256, governance_version, governance_config_json, governance_config_sha256,
            schema_version, engine_version, input_snapshot_sha256, run_manifest_sha256,
            execution_graph_version, execution_manifest_json, execution_manifest_sha256, trace_version, consent_at_run,
            clinical_review_json, data_transfer_consent_json)
           VALUES(?, ?, ?, ?, NULL, ?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)""",
        (
            run_id, case_id, decision_time.isoformat(), RunMode.DEVELOPMENT_DEMO.value, now,
            json_dumps(provider_ids), json_dumps(snapshot), json_dumps(frozen_provider_configs),
            provider_configs_hash, governance.version, json_dumps(governance_payload), governance_hash,
            DEVELOPMENT_RESULT_SCHEMA_VERSION, "0.2.0-development-agents", snapshot_hash, manifest_hash,
            DEVELOPMENT_EXECUTION_GRAPH_VERSION, json_dumps(execution_manifest), execution_manifest_hash,
            DEVELOPMENT_TRACE_VERSION,
        ),
    )
    _engine(request).emit(run_id, "queued", {
        "provider_ids": provider_ids,
        "include_baseline": False,
        "event_count": len(snapshot["events"]),
        "development_demo": True,
        "synthetic_only": True,
    })
    db.audit(_actor(x_actor), "case.created_development_demo", "case", case_id, {
        "run_mode": RunMode.DEVELOPMENT_DEMO.value,
        "synthetic_only": True,
        "source_text_sha256": source_hash,
        "warning_codes": warning_codes,
    })
    db.audit(_actor(x_actor), "run.created_development_demo", "run", run_id, {
        "case_id": case_id,
        "provider_ids": provider_ids,
        "run_mode": RunMode.DEVELOPMENT_DEMO.value,
        "synthetic_only": True,
        "source_text_sha256": source_hash,
    })
    _engine(request).schedule(run_id)
    row = db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    assert row is not None
    return _run_read(row)


@router.post("/runs", response_model=RunRead, status_code=202)
async def create_run(
    payload: RunCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-OwlPath-Admin-Token"),
) -> RunRead:
    db = _db(request)
    case = db.fetchone("SELECT * FROM cases WHERE id = ?", (payload.case_id,))
    if case is None:
        raise APIError(404, "case_not_found", "Case was not found")
    if payload.run_mode == RunMode.DEVELOPMENT_DEMO:
        raise APIError(
            422,
            "development_demo_endpoint_required",
            "Synthetic development demos must use POST /api/development-demo/runs",
        )
    governance = db.governance()
    if not governance.run_enabled:
        raise APIError(423, "runs_disabled", "Governance has disabled new runs")
    if payload.provider_ids is None:
        provider_rows = db.fetchall("SELECT * FROM providers WHERE enabled = 1 ORDER BY created_at")
        provider_ids = [row["id"] for row in provider_rows]
    else:
        provider_ids = list(dict.fromkeys(payload.provider_ids))
        provider_rows = []
        if provider_ids:
            placeholders = ",".join("?" for _ in provider_ids)
            provider_rows = db.fetchall("SELECT * FROM providers WHERE id IN (%s)" % placeholders, provider_ids)
            found = {row["id"] for row in provider_rows}
            missing = [item for item in provider_ids if item not in found]
            disabled = [row["id"] for row in provider_rows if not bool(row["enabled"])]
            if missing or disabled:
                raise APIError(422, "providers_unavailable", "One or more selected providers are missing or disabled", {
                    "missing": missing, "disabled": disabled,
                })
            provider_rows.sort(key=lambda row: provider_ids.index(row["id"]))
    unverified = [row["id"] for row in provider_rows if not bool(row.get("last_test_ok"))]
    if unverified:
        raise APIError(
            422,
            "providers_unverified",
            "Every selected provider must pass a synthetic connection test before use",
            {"unverified": unverified},
        )
    if not payload.include_baseline and not provider_ids:
        raise APIError(422, "no_models_selected", "Select at least one model or include the transparent baseline")
    server_now = utc_now()
    decision_time = as_utc(payload.decision_time or server_now)
    if payload.run_mode == RunMode.LIVE:
        if payload.retrospective_anchor_id:
            raise APIError(422, "live_run_cannot_use_retrospective_anchor", "Live runs cannot carry a retrospective anchor")
        if decision_time < server_now - timedelta(minutes=15) or decision_time > server_now + timedelta(minutes=1):
            raise APIError(
                422,
                "live_decision_time_not_current",
                "A live run decision_time must be within 15 minutes before and 1 minute after server time; refresh the current time and review again",
                {"server_time": server_now.isoformat()},
            )
    else:
        configured = request.app.state.settings.governance_admin_token
        authorized = bool(
            request.app.state.settings.allow_retrospective_runs
            and configured and x_admin_token and hmac.compare_digest(configured, x_admin_token)
            and payload.retrospective_anchor_id
        )
        if not authorized:
            raise APIError(
                403,
                "retrospective_run_admin_required",
                "Retrospective replay is disabled on the clinical API unless explicitly enabled with an administrator token and preregistered anchor",
            )
    external = [row["id"] for row in provider_rows if row["data_boundary"] == DataBoundary.EXTERNAL.value]
    consent = bool(case["external_data_consent"])
    transfer_record = payload.data_transfer_consent
    review = payload.clinical_review
    source_codes, raw_hashes = _source_case_safety(db, case, decision_time)
    snapshot = _engine(request).snapshot_case(payload.case_id, decision_time)
    snapshot_hash = sha256_json(snapshot)
    expected_targets = []
    for provider_row in provider_rows:
        if provider_row["data_boundary"] != DataBoundary.EXTERNAL.value:
            continue
        provider = provider_from_row(provider_row)
        expected_targets.append(provider_transfer_target(provider))
    expected_targets.sort(key=lambda item: item["provider_id"])
    submitted_targets = sorted(
        [item.model_dump(mode="json") for item in transfer_record.provider_targets],
        key=lambda item: item["provider_id"],
    ) if transfer_record else []
    transfer_valid = bool(
        not external
        or (
            consent
            and transfer_record
            and transfer_record.accepted
            and set(transfer_record.external_provider_ids) == set(external)
            and transfer_record.statement_version == "owlpath-external-transfer-v1"
            and transfer_record.confirmed_at <= utc_now() + timedelta(minutes=5)
            and transfer_record.confirmed_at >= utc_now() - timedelta(hours=24)
            and transfer_record.input_snapshot_sha256 == snapshot_hash
            and submitted_targets == expected_targets
        )
    )
    if not transfer_valid:
        db.audit(_actor(x_actor), "run.blocked_no_external_consent", "case", payload.case_id, {
            "provider_ids": external, "case_consent": consent,
            "run_consent_record_valid": False,
        })
        raise APIError(422, "external_data_consent_required", "External transfer consent no longer matches the exact snapshot and provider targets", {
            "external_provider_ids": external,
        })
    review_valid = bool(
        review
        and review.accepted
        and review.statement_version == "owlpath-clinical-review-v1"
        and review.confirmed_at <= utc_now() + timedelta(minutes=5)
        and review.confirmed_at >= utc_now() - timedelta(hours=24)
        and (not raw_hashes or review.source_text_sha256 in raw_hashes)
        and review.input_snapshot_sha256 == snapshot_hash
    )
    if not review_valid:
        db.audit(_actor(x_actor), "run.blocked_clinical_review", "case", payload.case_id, {
            "review_present": review is not None,
            "raw_source_hash_required": bool(raw_hashes),
        })
        raise APIError(
            422,
            "clinical_review_required",
            "A current clinician review record bound to the organized source is required",
        )
    if source_codes:
        db.audit(_actor(x_actor), "run.blocked_input_safety_gate", "case", payload.case_id, {
            "warning_codes": source_codes,
            "decision_time": decision_time.isoformat(),
        })
        raise APIError(
            422,
            "input_safety_gate_blocked",
            "The reviewed source contains a direct identifier, future timestamp, pathogen-label leakage, or a phase-incompatible microbiology result",
            {"warning_codes": source_codes},
        )
    blocking_codes = _snapshot_blocking_text_codes(snapshot, decision_time)
    if blocking_codes:
        db.audit(_actor(x_actor), "run.blocked_input_safety_gate", "case", payload.case_id, {
            "warning_codes": blocking_codes,
            "decision_time": decision_time.isoformat(),
        })
        raise APIError(
            422,
            "input_safety_gate_blocked",
            "The decision-time snapshot contains a direct identifier, future timestamp, or pathogen-label leakage",
            {"warning_codes": blocking_codes},
        )
    frozen_provider_configs = []
    for provider_row in provider_rows:
        frozen = provider_from_row(provider_row)
        frozen.pop("encrypted_api_key", None)
        frozen.pop("created_at", None)
        frozen.pop("updated_at", None)
        frozen_provider_configs.append(frozen)
    run_id = new_id("run")
    requested = server_now.isoformat()
    provider_configs_payload = json_dumps(frozen_provider_configs)
    provider_configs_hash = sha256_json(frozen_provider_configs)
    governance_payload = governance.model_dump(mode="json")
    governance_hash = sha256_json(governance_payload)
    review_payload = review.model_dump(mode="json")
    transfer_payload = transfer_record.model_dump(mode="json") if transfer_record else None
    manifest_hash = immutable_run_manifest_hash(
        case_id=payload.case_id,
        decision_time=decision_time.isoformat(),
        run_mode=payload.run_mode.value,
        retrospective_anchor_id=payload.retrospective_anchor_id,
        provider_ids=provider_ids,
        include_baseline=payload.include_baseline,
        input_snapshot_sha256=snapshot_hash,
        provider_configs_sha256=provider_configs_hash,
        governance_config_sha256=governance_hash,
        clinical_review=review_payload,
        data_transfer_consent=transfer_payload,
    )
    execution_manifest = build_execution_manifest(
        provider_ids, payload.include_baseline, False,
    )
    execution_manifest_hash = sha256_json(execution_manifest)
    db.execute(
        """INSERT INTO runs
           (id, case_id, decision_time, run_mode, retrospective_anchor_id, requested_at, status,
            provider_ids_json, include_baseline, input_snapshot_json, provider_configs_json,
            provider_configs_sha256, governance_version, governance_config_json, governance_config_sha256,
            schema_version, engine_version, input_snapshot_sha256, run_manifest_sha256,
            execution_graph_version, execution_manifest_json, execution_manifest_sha256, trace_version, consent_at_run,
            clinical_review_json, data_transfer_consent_json)
           VALUES(?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, payload.case_id, decision_time.isoformat(), payload.run_mode.value,
         payload.retrospective_anchor_id, requested, json_dumps(provider_ids),
         int(payload.include_baseline), json_dumps(snapshot), provider_configs_payload,
         provider_configs_hash, governance.version, json_dumps(governance_payload), governance_hash,
         "owlpath.result.v2", "0.1.0-research", snapshot_hash, manifest_hash,
         EXECUTION_GRAPH_VERSION, json_dumps(execution_manifest), execution_manifest_hash, TRACE_VERSION,
         int(bool(transfer_record and transfer_record.accepted)), json_dumps(review_payload),
         json_dumps(transfer_payload) if transfer_payload else None),
    )
    _engine(request).emit(run_id, "queued", {
        "provider_ids": provider_ids, "include_baseline": payload.include_baseline,
        "event_count": len(snapshot["events"]),
    })
    db.audit(_actor(x_actor), "run.created", "run", run_id, {
        "case_id": payload.case_id, "provider_ids": provider_ids,
        "external_provider_ids": external, "external_data_consent": bool(transfer_record and transfer_record.accepted),
        "consent_statement_version": transfer_record.statement_version if transfer_record else None,
        "clinical_review_statement_version": review.statement_version,
        "clinical_review_parser_version": review.parser_version,
        "decision_time": decision_time.isoformat(),
        "run_mode": payload.run_mode.value,
        "retrospective_anchor_id": payload.retrospective_anchor_id,
    })
    _engine(request).schedule(run_id)
    row = db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    assert row is not None
    return _run_read(row)


@router.get("/runs/{run_id}", response_model=RunRead)
async def get_run(run_id: str, request: Request) -> RunRead:
    row = _db(request).fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    if row is None:
        raise APIError(404, "run_not_found", "Run was not found")
    return _run_read(row)


@router.get("/runs/{run_id}/trace")
async def get_run_trace(run_id: str, request: Request) -> Dict[str, Any]:
    db = _db(request)
    run = db.fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    if run is None:
        raise APIError(404, "run_not_found", "Run was not found")
    if not run.get("trace_version"):
        raise APIError(
            409,
            "legacy_trace_unavailable",
            "This legacy run predates versioned execution tracing",
        )
    nodes = db.fetchall(
        "SELECT * FROM run_execution_nodes WHERE run_id = ? ORDER BY sequence, started_at",
        (run_id,),
    )
    try:
        manifest = trace_safe_payload(json_loads(run.get("execution_manifest_json"), {}))
    except (TypeError, ValueError):
        manifest = None
    manifest_integrity_ok = bool(
        manifest is not None
        and run.get("execution_manifest_sha256")
        and sha256_json(manifest) == run.get("execution_manifest_sha256")
    )
    node_ids_by_key = {node["node_key"]: node["id"] for node in nodes}
    actual_edges = []
    if manifest_integrity_ok:
        for edge in manifest.get("edges") or []:
            source_id = node_ids_by_key.get(edge.get("from"))
            target_id = node_ids_by_key.get(edge.get("to"))
            if source_id and target_id:
                actual_edges.append({
                    "from": source_id,
                    "to": target_id,
                    "relation": edge.get("relation") or edge.get("kind") or "data",
                })
    return {
        "run_id": run_id,
        "run_mode": run.get("run_mode") or RunMode.LIVE.value,
        "trace_version": run.get("trace_version"),
        "execution_graph_version": run.get("execution_graph_version"),
        "execution_manifest_sha256": run.get("execution_manifest_sha256"),
        "manifest": manifest if manifest_integrity_ok else None,
        "manifest_integrity_ok": manifest_integrity_ok,
        "nodes": [_trace_node_public(node) for node in nodes],
        "edges": actual_edges,
    }


@router.get("/runs/{run_id}/trace/nodes/{node_id}")
async def get_run_trace_node(run_id: str, node_id: str, request: Request) -> Dict[str, Any]:
    db = _db(request)
    run = db.fetchone("SELECT id, trace_version FROM runs WHERE id = ?", (run_id,))
    if run is None:
        raise APIError(404, "run_not_found", "Run was not found")
    if not run.get("trace_version"):
        raise APIError(
            409,
            "legacy_trace_unavailable",
            "This legacy run predates versioned execution tracing",
        )
    node = db.fetchone(
        "SELECT * FROM run_execution_nodes WHERE id = ? AND run_id = ?",
        (node_id, run_id),
    )
    if node is None:
        raise APIError(404, "trace_node_not_found", "Execution-trace node was not found for this run")
    return _trace_node_detail_payload(db, node)


@router.get("/trace/nodes/{node_id}")
async def get_trace_node(node_id: str, request: Request) -> Dict[str, Any]:
    db = _db(request)
    node = db.fetchone("SELECT * FROM run_execution_nodes WHERE id = ?", (node_id,))
    if node is None:
        raise APIError(404, "trace_node_not_found", "Execution-trace node was not found")
    return _trace_node_detail_payload(db, node)


@router.get("/runs/{run_id}/models")
async def get_run_models(run_id: str, request: Request) -> List[Dict[str, Any]]:
    run = _db(request).fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
    if run is None:
        raise APIError(404, "run_not_found", "Run was not found")
    verified_run = _run_read(run)
    development_demo = verified_run.run_mode == RunMode.DEVELOPMENT_DEMO
    rows = _db(request).fetchall("SELECT * FROM run_model_outputs WHERE run_id = ? ORDER BY created_at", (run_id,))
    return [{
        "id": row["id"], "provider_id": row["provider_id"], "provider_name": row["provider_name"],
        "node_run_id": row.get("node_run_id"),
        "status": row["status"],
        "normalized": (
            json_loads(row["normalized_json"])
            if development_demo and verified_run.status.value == "completed" and row["normalized_json"]
            else _safe_model_prediction_for_clinical_view(
                row["normalized_json"], run["result_json"] if verified_run.status.value == "completed" else None,
            )
        ),
        "development_demo": development_demo,
        "synthetic_only": development_demo,
        "not_for_clinical_use": development_demo,
        "provider_kind": row["provider_kind"], "model": row["provider_model"],
        "base_url_origin": row["base_url_origin"], "weight": row["provider_weight"],
        "data_boundary": row["data_boundary"], "model_fingerprint": row["model_fingerprint"],
        "error": json_loads(row["error_json"]) if row["error_json"] else None,
        "latency_ms": row["latency_ms"], "created_at": row["created_at"], "completed_at": row["completed_at"],
    } for row in rows]


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    after_id: Optional[int] = Query(default=None, ge=0),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    db = _db(request)
    if db.fetchone("SELECT id FROM runs WHERE id = ?", (run_id,)) is None:
        raise APIError(404, "run_not_found", "Run was not found")

    resume_after = after_id or 0
    if last_event_id:
        try:
            parsed_last_event_id = int(last_event_id)
            if parsed_last_event_id >= 0:
                resume_after = max(resume_after, parsed_last_event_id)
        except (TypeError, ValueError):
            pass

    async def event_stream() -> Any:
        last_id = resume_after
        idle_ticks = 0
        terminal = {"completed", "failed"}
        while True:
            if await request.is_disconnected():
                break
            rows = db.fetchall("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id", (run_id, last_id))
            saw_terminal = False
            for row in rows:
                last_id = int(row["id"])
                event_type = row["event_type"]
                payload = json_loads(row["payload_json"], {})
                envelope = {"type": event_type, "payload": payload, "created_at": row["created_at"]}
                yield "id: %s\nevent: %s\ndata: %s\n\n" % (last_id, event_type, json_dumps(envelope))
                saw_terminal = saw_terminal or event_type in terminal
            if saw_terminal:
                break
            if not rows:
                run_state = db.fetchone("SELECT status FROM runs WHERE id = ?", (run_id,))
                if run_state and run_state["status"] in terminal:
                    break
            else:
                idle_ticks = 0
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.get("/evaluations", response_model=List[EvaluationRead])
async def list_evaluations(request: Request) -> List[EvaluationRead]:
    db = _db(request)
    rows = db.fetchall("SELECT * FROM evaluations ORDER BY created_at DESC")
    return [
        _evaluation_read(row) for row in rows
        if (run := db.fetchone("SELECT * FROM runs WHERE id = ?", (row["run_id"],)))
        and (run.get("run_mode") or RunMode.LIVE.value) != RunMode.DEVELOPMENT_DEMO.value
        and _run_read(run).status.value == "completed"
    ]


@router.post("/evaluations", response_model=EvaluationRead, status_code=201)
async def create_evaluation(
    payload: EvaluationCreate,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
) -> EvaluationRead:
    db = _db(request)
    run = db.fetchone("SELECT * FROM runs WHERE id = ?", (payload.run_id,))
    if run is None:
        raise APIError(404, "run_not_found", "Run was not found")
    if (run.get("run_mode") or RunMode.LIVE.value) == RunMode.DEVELOPMENT_DEMO.value:
        raise APIError(
            409,
            "development_demo_not_evaluable",
            "Synthetic development-demo runs are excluded from clinical research evaluation",
        )
    verified_run = _run_read(run)
    if verified_run.status.value != "completed" or verified_run.result is None:
        raise APIError(409, "run_not_integrity_verified", "Only integrity-verified completed runs can be evaluated")
    result = verified_run.result
    metrics = evaluate_result(result, payload.label)
    existing = db.fetchone("SELECT * FROM evaluations WHERE run_id = ?", (payload.run_id,))
    now = utc_now().isoformat()
    if existing:
        evaluation_id = existing["id"]
        db.execute(
            "UPDATE evaluations SET label_json = ?, metrics_json = ?, updated_at = ? WHERE id = ?",
            (json_dumps(payload.label.model_dump(mode="json")), json_dumps(metrics), now, evaluation_id),
        )
        action = "evaluation.updated"
    else:
        evaluation_id = new_id("eval")
        db.execute(
            """INSERT INTO evaluations
               (id, run_id, case_id, label_json, metrics_json, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (evaluation_id, payload.run_id, run["case_id"], json_dumps(payload.label.model_dump(mode="json")),
             json_dumps(metrics), now, now),
        )
        action = "evaluation.created"
    db.audit(_actor(x_actor), action, "evaluation", evaluation_id, {
        "run_id": payload.run_id, "adjudication_status": payload.label.adjudication_status,
        "label_version": payload.label.label_version,
    })
    row = db.fetchone("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,))
    assert row is not None
    return _evaluation_read(row)


@router.get("/evaluations/summary")
async def evaluation_summary(request: Request) -> Dict[str, Optional[float]]:
    db = _db(request)
    rows = db.fetchall("SELECT * FROM evaluations")
    metrics = []
    for row in rows:
        run = db.fetchone("SELECT * FROM runs WHERE id = ?", (row["run_id"],))
        if (
            run
            and (run.get("run_mode") or RunMode.LIVE.value) != RunMode.DEVELOPMENT_DEMO.value
            and _run_read(run).status.value == "completed"
        ):
            metrics.append(json_loads(row["metrics_json"], {}))
    return summarize_metrics(metrics)


@router.get("/evaluations/export.csv")
async def export_evaluations_csv(request: Request) -> StreamingResponse:
    db = _db(request)
    rows = [
        row for row in db.fetchall("SELECT * FROM evaluations ORDER BY created_at")
        if (run := db.fetchone("SELECT * FROM runs WHERE id = ?", (row["run_id"],)))
        and (run.get("run_mode") or RunMode.LIVE.value) != RunMode.DEVELOPMENT_DEMO.value
        and _run_read(run).status.value == "completed"
    ]
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["evaluation_id", "run_id", "case_id", "top1", "top3", "top5", "mrr", "pathogen_brier", "infection_brier", "adjudication_status", "label_version"])
    for row in rows:
        metrics = json_loads(row["metrics_json"], {})
        label = json_loads(row["label_json"], {})
        writer.writerow([row["id"], row["run_id"], row["case_id"], metrics.get("top1"), metrics.get("top3"),
                         metrics.get("top5"), metrics.get("mrr"), metrics.get("pathogen_brier"), metrics.get("infection_brier"),
                         label.get("adjudication_status"), label.get("label_version")])
    return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=owlpath-evaluations.csv",
    })


@router.get("/governance", response_model=GovernanceConfig)
async def get_governance(request: Request) -> GovernanceConfig:
    return _db(request).governance()


@router.put("/governance", response_model=GovernanceConfig)
async def put_governance(
    payload: GovernanceConfig,
    request: Request,
    x_actor: Optional[str] = Header(default=None, alias="X-Actor"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-OwlPath-Admin-Token"),
) -> GovernanceConfig:
    db = _db(request)
    configured = request.app.state.settings.governance_admin_token
    if not configured or not x_admin_token or not hmac.compare_digest(configured, x_admin_token):
        db.audit(_actor(x_actor), "governance.update_blocked", "governance", "1", {
            "admin_token_configured": bool(configured),
        })
        raise APIError(403, "governance_admin_required", "Governance updates are locked to an authenticated local administrator")
    old = db.governance()
    db.update_governance(payload)
    db.audit(_actor(x_actor), "governance.updated", "governance", "1", {
        "old_version": old.version, "new_version": payload.version,
        "run_enabled": payload.run_enabled,
    })
    return payload


@router.get("/audit")
async def list_audit(
    request: Request,
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = _db(request).fetchall("SELECT * FROM audit_log%s ORDER BY created_at DESC LIMIT ?" % where, params + [limit])
    return [_audit_public(row) for row in rows]


@router.get("/cases/{case_id}/export")
async def export_case(case_id: str, request: Request) -> JSONResponse:
    db = _db(request)
    case = db.fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
    if case is None:
        raise APIError(404, "case_not_found", "Case was not found")
    events = db.fetchall("SELECT * FROM clinical_events WHERE case_id = ? ORDER BY sequence", (case_id,))
    runs = db.fetchall("SELECT * FROM runs WHERE case_id = ? ORDER BY requested_at", (case_id,))
    run_ids = [row["id"] for row in runs]
    run_result_by_id = {
        row["id"]: row.get("result_json")
        if _run_read(row).status.value == "completed" else None
        for row in runs
    }
    outputs: List[Dict[str, Any]] = []
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        outputs = db.fetchall("SELECT * FROM run_model_outputs WHERE run_id IN (%s) ORDER BY created_at" % placeholders, run_ids)
    verified_run_ids = {
        row["id"] for row in runs if _run_read(row).status.value == "completed"
    }
    evaluations = [
        row for row in db.fetchall("SELECT * FROM evaluations WHERE case_id = ? ORDER BY created_at", (case_id,))
        if row["run_id"] in verified_run_ids
    ]
    audit = db.fetchall("SELECT * FROM audit_log WHERE entity_id = ? OR details_json LIKE ? ORDER BY created_at", (case_id, "%%%s%%" % case_id))
    payload = {
        "exported_at": utc_now().isoformat(),
        "service": "OwlPath（鸮径） 0.1.0-research",
        "warning": "Research-only, not clinically validated. Export may contain sensitive de-identified clinical data; handle under local policy.",
        "case": _case_read(case).model_dump(mode="json"),
        "events": [_event_read(row).model_dump(mode="json") for row in events],
        "runs": [_run_read(row).model_dump(mode="json") for row in runs],
        "model_outputs": [{
            "id": row["id"], "run_id": row["run_id"], "provider_id": row["provider_id"],
            "provider_name": row["provider_name"], "status": row["status"],
            "provider_kind": row["provider_kind"], "model": row["provider_model"],
            "base_url_origin": row["base_url_origin"], "weight": row["provider_weight"],
            "data_boundary": row["data_boundary"], "model_fingerprint": row["model_fingerprint"],
            "normalized": _safe_model_prediction_for_clinical_view(
                row["normalized_json"], run_result_by_id.get(row["run_id"]),
            ),
            "error": json_loads(row["error_json"]) if row["error_json"] else None,
            "latency_ms": row["latency_ms"], "created_at": row["created_at"], "completed_at": row["completed_at"],
        } for row in outputs],
        "evaluations": [_evaluation_read(row).model_dump(mode="json") for row in evaluations],
        "audit": [_audit_public(row) for row in audit],
    }
    return JSONResponse(content=payload, headers={
        "Content-Disposition": "attachment; filename=owlpath-%s.json" % case_id,
    })
