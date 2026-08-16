#!/usr/bin/env python3
"""Run privacy-minimized synthetic-case regressions against a local OwlPath API.

This command deliberately requires ``--confirm-real-api`` before it makes any
HTTP request.  A development run can call configured cloud models several
times and may therefore incur provider charges.

Only compact regression facts are persisted.  Case text, provider secrets,
node artifacts, and raw provider responses are never written to the artifact
directory.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_FIXTURE = PROJECT_ROOT / "examples" / "public_synthetic_case_matrix.v1.json"
DEFAULT_ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "synthetic-regression"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TERMINAL_RUN_STATUSES = {"completed", "failed"}
PASSING_RESULT_STATUSES = {"completed", "completed_with_warnings"}

CORE_SPECIALIST_ROLE_IDS = (
    "infectious_diseases",
    "critical_care_emergency",
    "clinical_epidemiology",
    "laboratory_medicine",
    "clinical_microbiology_culture",
)
DYNAMIC_SPECIALIST_ROLE_IDS = (
    "radiology",
    "pulmonology",
    "gastroenterology",
    "hepatobiliary_pancreatic",
    "urology",
    "nephrology",
    "neurology_neuroinfection",
    "cardiology_endocarditis",
    "hematology_immunology",
    "transplant_infectious_diseases",
    "surgery_source_control",
    "orthopedics_bone_joint",
    "dermatology_soft_tissue",
    "obstetrics_gynecology",
    "pediatrics_neonatology",
    "tropical_medicine_parasitology",
    "medical_mycology",
    "clinical_virology_molecular",
    "antimicrobial_stewardship",
    "healthcare_device_infection",
)
CORE_SPECIALIST_NODE_KEYS = {
    "specialist:%s" % role for role in CORE_SPECIALIST_ROLE_IDS
}
DYNAMIC_SPECIALIST_NODE_KEYS = {
    "specialist:%s" % role for role in DYNAMIC_SPECIALIST_ROLE_IDS
}
SPECIALIST_NODE_KEYS = CORE_SPECIALIST_NODE_KEYS | DYNAMIC_SPECIALIST_NODE_KEYS
ACTIVE_SPECIALIST_ROLE_IDS = set(CORE_SPECIALIST_ROLE_IDS) | set(
    DYNAMIC_SPECIALIST_ROLE_IDS
)
REQUIRED_AGENT_NODE_KEYS = SPECIALIST_NODE_KEYS | {
    "literature_retrieval",
    "public_health_retrieval",
    "synthesis",
    "critic",
}
REQUIRED_V4_NODE_KEYS = REQUIRED_AGENT_NODE_KEYS | {
    "snapshot",
    "preflight",
    "applicability",
    "input_quality",
    "source_compiler",
    "complexity_router",
    "evidence_board",
    "retrieval_planner",
    "evidence_verifier",
    "contract_validator",
    "revision",
    "candidate_evidence_enrichment",
    "result_compiler",
    "persistence",
}
TRACE_DETAIL_ORACLE_NODE_KEYS = SPECIALIST_NODE_KEYS | {
    "evidence_board",
    "literature_retrieval",
    "public_health_retrieval",
    "evidence_verifier",
    "synthesis",
}

# Exact generic labels are invalid.  Valid organism names such as
# "Influenza A virus" must not be rejected merely because they contain
# the word "virus".
GENERIC_PATHOGEN_LABELS = {
    "bacteria",
    "bacterium",
    "bacterial pathogen",
    "other bacteria",
    "virus",
    "viral pathogen",
    "other virus",
    "fungus",
    "fungi",
    "fungal pathogen",
    "other fungus",
    "pathogen",
    "unknown",
    "unknown pathogen",
    "unidentified pathogen",
    "细菌",
    "病毒",
    "真菌",
    "病原体",
    "未知",
    "未知病原体",
    "其他细菌",
    "其他病毒",
    "其他真菌",
}
FORBIDDEN_ABSTENTION_TERMS = ("abstain", "弃答", "转人工")
SAFE_CODE_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]+")
MAX_CASE_TEXT_CHARACTERS = 30000
MAX_PROVIDER_COUNT = 12
MAXIMUM_PROVIDER_NETWORK_REQUESTS_PER_RUN = 18
SPECIALIST_PROVIDER_REQUEST_CEILING = 12
MAXIMUM_DYNAMIC_SPECIALISTS = 6
MAXIMUM_SELECTED_SPECIALISTS = len(CORE_SPECIALIST_ROLE_IDS) + MAXIMUM_DYNAMIC_SPECIALISTS


class RunnerError(RuntimeError):
    """An expected runner failure represented by a privacy-safe code."""

    def __init__(self, code: str, exit_code: int = 2) -> None:
        self.code = sanitize_code(code)
        self.exit_code = exit_code
        super().__init__(self.code)


class ApiError(RunnerError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent a local endpoint from redirecting case text elsewhere."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class BehaviorExpectations:
    """Private, local-only behavioral assertions for a synthetic case."""

    summary_must_contain_any: Tuple[str, ...] = ()
    result_must_contain_any: Tuple[str, ...] = ()
    min_unknown_score: Optional[float] = None
    max_top1_model_score: Optional[float] = None
    minimum_coinfection_hypotheses: Optional[int] = None


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    title: str
    text: str
    expected_any_of: Tuple[str, ...]
    required_fragments: Tuple[str, ...]
    minimum_expected_set_overlap: int
    expectations: BehaviorExpectations = BehaviorExpectations()


def sanitize_code(value: Any) -> str:
    cleaned = SAFE_CODE_PATTERN.sub("_", str(value or "unknown")).strip("_")
    return cleaned[:160] or "unknown"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def deduplicate_expected_names(values: Sequence[str]) -> Tuple[str, ...]:
    """Keep first spelling while collapsing equivalent private oracle names."""

    output: List[str] = []
    seen = set()
    for value in values:
        normalized = normalized_name(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(value)
    return tuple(output)


def normalized_oracle_text(value: Any) -> str:
    """Normalize harmless typography without weakening factual assertions."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Clinical prose commonly renders the same numeric interval as ``10至12``,
    # ``10-12`` or ``10–12``.  Treat only separators *between two digits* as
    # typographic equivalents; words, units and the interval bounds remain
    # unchanged, so this cannot turn a different factual value into a match.
    normalized = re.sub(
        r"(?<=\d)\s*(?:至|到|[-‐‑‒–—―~～])\s*(?=\d)",
        "-",
        normalized,
    )
    return "".join(normalized.split())


def behavior_expectation_count(expectations: BehaviorExpectations) -> int:
    return sum(
        (
            bool(expectations.summary_must_contain_any),
            bool(expectations.result_must_contain_any),
            expectations.min_unknown_score is not None,
            expectations.max_top1_model_score is not None,
            expectations.minimum_coinfection_hypotheses is not None,
        )
    )


def _string_list(value: Any, *, preferred_keys: Sequence[str]) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RunnerError("fixture_field_must_be_array")
    output: List[str] = []
    for item in value:
        candidate: Any = item
        if isinstance(item, Mapping):
            candidate = next((item.get(key) for key in preferred_keys if item.get(key)), None)
        if not isinstance(candidate, str) or not candidate.strip():
            raise RunnerError("fixture_array_item_must_be_nonempty_string")
        output.append(candidate.strip())
    return tuple(output)


def _parse_behavior_expectations(value: Any, case_index: int) -> BehaviorExpectations:
    if value is None:
        return BehaviorExpectations()
    if not isinstance(value, Mapping):
        raise RunnerError("fixture_case_%d_expectations_not_object" % case_index)
    allowed_fields = {
        "summary_must_contain_any",
        "result_must_contain_any",
        "min_unknown_score",
        "max_top1_model_score",
        "minimum_coinfection_hypotheses",
    }
    if set(value) - allowed_fields:
        raise RunnerError("fixture_case_%d_expectations_unknown_field" % case_index)

    summary_terms = _string_list(
        value.get("summary_must_contain_any"),
        preferred_keys=("text", "term", "value"),
    )
    if "summary_must_contain_any" in value and not summary_terms:
        raise RunnerError("fixture_case_%d_summary_expectation_empty" % case_index)
    if len(summary_terms) > 20 or any(len(term) > 160 for term in summary_terms):
        raise RunnerError("fixture_case_%d_summary_expectation_too_large" % case_index)

    result_terms = _string_list(
        value.get("result_must_contain_any"),
        preferred_keys=("text", "term", "value"),
    )
    if "result_must_contain_any" in value and not result_terms:
        raise RunnerError("fixture_case_%d_result_expectation_empty" % case_index)
    if len(result_terms) > 20 or any(len(term) > 160 for term in result_terms):
        raise RunnerError("fixture_case_%d_result_expectation_too_large" % case_index)

    def optional_score(field_name: str) -> Optional[float]:
        raw = value.get(field_name)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RunnerError("fixture_case_%d_expectation_score_invalid" % case_index)
        score = float(raw)
        if not 0 <= score <= 1:
            raise RunnerError("fixture_case_%d_expectation_score_out_of_range" % case_index)
        return score

    raw_minimum_coinfections = value.get("minimum_coinfection_hypotheses")
    minimum_coinfections: Optional[int]
    if raw_minimum_coinfections is None:
        minimum_coinfections = None
    elif (
        isinstance(raw_minimum_coinfections, bool)
        or not isinstance(raw_minimum_coinfections, int)
        or not 0 <= raw_minimum_coinfections <= 10
    ):
        raise RunnerError(
            "fixture_case_%d_minimum_coinfection_expectation_invalid" % case_index
        )
    else:
        minimum_coinfections = raw_minimum_coinfections

    return BehaviorExpectations(
        summary_must_contain_any=summary_terms,
        result_must_contain_any=result_terms,
        min_unknown_score=optional_score("min_unknown_score"),
        max_top1_model_score=optional_score("max_top1_model_score"),
        minimum_coinfection_hypotheses=minimum_coinfections,
    )


def _parse_minimum_expected_set_overlap(
    private_oracle: Any,
    expected_any_of: Sequence[str],
    case_index: int,
) -> int:
    """Read one local-only oracle control without retaining the oracle object."""

    default = 1 if expected_any_of else 0
    if private_oracle is None:
        return default
    if not isinstance(private_oracle, Mapping):
        raise RunnerError("fixture_case_%d_private_oracle_not_object" % case_index)
    raw_minimum = private_oracle.get("minimum_expected_set_overlap")
    if raw_minimum is None:
        return default
    if (
        isinstance(raw_minimum, bool)
        or not isinstance(raw_minimum, int)
        or raw_minimum < 0
        or raw_minimum > 5
        or raw_minimum > len(expected_any_of)
    ):
        raise RunnerError(
            "fixture_case_%d_minimum_expected_set_overlap_invalid" % case_index
        )
    return raw_minimum


def load_fixture(path: Path) -> List[SyntheticCase]:
    if not path.exists():
        raise RunnerError("fixture_not_found")
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise RunnerError("fixture_invalid_json_at_line_%d" % exc.lineno) from None
    except OSError:
        raise RunnerError("fixture_unreadable") from None

    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, Mapping) and isinstance(raw.get("cases"), list):
        entries = raw["cases"]
    else:
        raise RunnerError("fixture_requires_cases_array")

    cases: List[SyntheticCase] = []
    seen_ids = set()
    for index, item in enumerate(entries, start=1):
        if not isinstance(item, Mapping):
            raise RunnerError("fixture_case_%d_not_object" % index)
        case_id = str(item.get("id") or item.get("case_id") or "").strip()
        if not case_id:
            raise RunnerError("fixture_case_%d_missing_id" % index)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", case_id):
            raise RunnerError("fixture_case_%d_invalid_id" % index)
        if case_id in seen_ids:
            raise RunnerError("fixture_duplicate_case_id")
        seen_ids.add(case_id)

        # A positive synthetic declaration is required before any fixture can
        # ever be sent to configured external models.
        if item.get("is_synthetic") is not True:
            raise RunnerError("fixture_case_%d_not_explicitly_synthetic" % index)
        if item.get("contains_real_patient_data") is not False:
            raise RunnerError("fixture_case_%d_not_explicitly_free_of_real_data" % index)

        text = item.get("text") or item.get("source_text") or item.get("case_text")
        if not isinstance(text, str) or not text.strip():
            raise RunnerError("fixture_case_%d_missing_text" % index)
        if len(text) > MAX_CASE_TEXT_CHARACTERS:
            raise RunnerError("fixture_case_%d_text_exceeds_30000_characters" % index)
        expected = deduplicate_expected_names(
            _string_list(
                item.get("expected_any_of"),
                preferred_keys=("canonical_latin_name", "latin_name", "name"),
            )
        )
        fragments = _string_list(
            item.get("required_fragments"),
            preferred_keys=("text", "fragment", "value"),
        )
        minimum_expected_set_overlap = _parse_minimum_expected_set_overlap(
            item.get("private_oracle"), expected, index
        )
        expectations = _parse_behavior_expectations(item.get("expectations"), index)
        title = str(item.get("title") or case_id).strip()[:240]
        cases.append(
            SyntheticCase(
                case_id=case_id,
                title=title,
                text=text.strip(),
                expected_any_of=expected,
                required_fragments=fragments,
                minimum_expected_set_overlap=minimum_expected_set_overlap,
                expectations=expectations,
            )
        )
    if not cases:
        raise RunnerError("fixture_has_no_cases")
    return cases


def select_cases(
    cases: Sequence[SyntheticCase], requested_ids: Sequence[str], limit: Optional[int]
) -> List[SyntheticCase]:
    selected = list(cases)
    if requested_ids:
        wanted = list(dict.fromkeys(requested_ids))
        by_id = {case.case_id: case for case in cases}
        missing = [case_id for case_id in wanted if case_id not in by_id]
        if missing:
            raise RunnerError("requested_case_not_found_%s" % sanitize_code(missing[0]))
        selected = [by_id[case_id] for case_id in wanted]
    if limit is not None:
        if limit < 1:
            raise RunnerError("limit_must_be_positive")
        selected = selected[:limit]
    return selected


def validate_fixture_oracles(cases: Sequence[SyntheticCase]) -> List[str]:
    errors: List[str] = []
    for case in cases:
        text_folded = normalized_oracle_text(case.text)
        for index, fragment in enumerate(case.required_fragments, start=1):
            if normalized_oracle_text(fragment) not in text_folded:
                errors.append(
                    "%s:fixture_required_fragment_absent_%d" % (case.case_id, index)
                )
    return errors


class ApiClient:
    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RunnerError("invalid_base_url")
        self.base_url = base_url.rstrip("/")
        # Do not inherit HTTP_PROXY for a loopback-only clinical development
        # tool, and never follow redirects that could replay a POST body.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirectHandler(),
        )

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json", "X-Actor": "synthetic-regression-runner"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            api_code = "unknown"
            try:
                parsed_error = json.loads(exc.read(64 * 1024).decode("utf-8", errors="replace"))
                if isinstance(parsed_error, Mapping):
                    error = parsed_error.get("error")
                    if isinstance(error, Mapping):
                        api_code = sanitize_code(error.get("code"))
            except (ValueError, OSError):
                pass
            raise ApiError("http_%d_%s" % (exc.code, api_code)) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise ApiError("http_timeout") from None
            reason_name = type(reason).__name__
            raise ApiError("connection_failed_%s" % sanitize_code(reason_name)) from None
        except (TimeoutError, socket.timeout):
            raise ApiError("http_timeout") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("invalid_json_response") from None

    def get(self, path: str, timeout: float = 30.0) -> Any:
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, payload: Mapping[str, Any], timeout: float = 30.0) -> Any:
        return self.request("POST", path, payload=payload, timeout=timeout)


def select_providers(
    providers: Any, requested_ids: Sequence[str]
) -> Tuple[List[str], List[Dict[str, str]]]:
    if not isinstance(providers, list):
        raise RunnerError("providers_response_not_array")
    provider_rows = [row for row in providers if isinstance(row, Mapping)]
    by_id = {str(row.get("id")): row for row in provider_rows if row.get("id")}

    if requested_ids:
        selected_rows = []
        unique_requested_ids = list(dict.fromkeys(requested_ids))
        if any(not str(provider_id).strip() for provider_id in unique_requested_ids):
            raise RunnerError("provider_id_must_not_be_blank")
        if len(unique_requested_ids) > MAX_PROVIDER_COUNT:
            raise RunnerError("provider_count_exceeds_12")
        for provider_id in unique_requested_ids:
            row = by_id.get(provider_id)
            if row is None:
                raise RunnerError("provider_not_found_%s" % sanitize_code(provider_id))
            if row.get("enabled") is not True:
                raise RunnerError("provider_not_enabled_%s" % sanitize_code(provider_id))
            if row.get("last_test_ok") is not True:
                raise RunnerError("provider_not_ready_%s" % sanitize_code(provider_id))
            selected_rows.append(row)
    else:
        selected_rows = [
            row
            for row in provider_rows
            if row.get("enabled") is True
            and row.get("last_test_ok") is True
            and row.get("data_boundary") == "external"
        ]
        selected_rows.sort(
            key=lambda row: (-float(row.get("weight") or 0.0), str(row.get("id") or ""))
        )
        if not selected_rows:
            raise RunnerError("no_enabled_ready_external_provider")
        selected_rows = selected_rows[:MAX_PROVIDER_COUNT]

    ids = [str(row["id"]) for row in selected_rows]
    # Provider IDs are enough to make a run reproducible.  Do not persist
    # user-editable provider labels or connection settings in regression
    # artifacts, because those fields might accidentally contain a secret.
    public = [{"id": str(row["id"])} for row in selected_rows]
    return ids, public


def poll_run(client: ApiClient, run_id: str, timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    delay = 1.0
    encoded_id = urllib.parse.quote(run_id, safe="")
    while True:
        run = client.get("/api/runs/%s" % encoded_id, timeout=min(30.0, timeout_seconds))
        if not isinstance(run, dict):
            raise ApiError("run_response_not_object")
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        if time.monotonic() >= deadline:
            raise ApiError("run_poll_timeout")
        time.sleep(min(delay, max(0.05, deadline - time.monotonic())))
        delay = min(4.0, delay * 1.25)


def fetch_trace(client: ApiClient, run_id: str) -> Dict[str, Any]:
    encoded_id = urllib.parse.quote(run_id, safe="")
    trace = client.get("/api/runs/%s/trace" % encoded_id)
    if not isinstance(trace, dict):
        raise ApiError("trace_response_not_object")
    return trace


def fetch_oracle_trace_text(
    client: ApiClient, run_id: str, trace: Mapping[str, Any]
) -> str:
    """Read safe node details into memory without returning them to artifacts."""
    chunks: List[str] = []
    encoded_run_id = urllib.parse.quote(run_id, safe="")
    nodes = trace.get("nodes")
    if not isinstance(nodes, list):
        return ""
    for node in nodes:
        if not isinstance(node, Mapping) or node.get("node_key") not in TRACE_DETAIL_ORACLE_NODE_KEYS:
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        detail = client.get(
            "/api/runs/%s/trace/nodes/%s"
            % (encoded_run_id, urllib.parse.quote(str(node_id), safe=""))
        )
        if not isinstance(detail, Mapping):
            continue
        # Inspect output artifacts only.  Input artifacts can contain the full
        # source text, which would make a required-fragment oracle pass without
        # proving that any Agent actually retained the fact.
        artifacts = detail.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        output_content = [
            artifact.get("content")
            for artifact in artifacts
            if isinstance(artifact, Mapping)
            and artifact.get("direction") == "output"
            and artifact.get("content") is not None
        ]
        # This representation is used only for in-memory weak-oracle matching
        # and is intentionally never included in a regression record.
        chunks.append(json.dumps(output_content, ensure_ascii=False, sort_keys=True))
    return "\n".join(chunks)


def validate_dag(trace: Mapping[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    errors: List[str] = []
    manifest = trace.get("manifest")
    if not isinstance(manifest, Mapping):
        return ["trace_manifest_missing"], {"node_count": 0, "edge_count": 0}
    raw_nodes = manifest.get("nodes")
    raw_edges = manifest.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return ["trace_manifest_nodes_or_edges_invalid"], {"node_count": 0, "edge_count": 0}

    keys: List[str] = []
    for node in raw_nodes:
        if isinstance(node, Mapping) and isinstance(node.get("key"), str):
            keys.append(node["key"])
        else:
            errors.append("trace_manifest_node_key_missing")
    if len(keys) != len(set(keys)):
        errors.append("trace_manifest_duplicate_node_key")
    key_set = set(keys)
    incoming = {key: 0 for key in key_set}
    outgoing: Dict[str, List[str]] = {key: [] for key in key_set}
    valid_edge_count = 0
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            errors.append("trace_manifest_edge_invalid")
            continue
        source = edge.get("from")
        target = edge.get("to")
        if source not in key_set or target not in key_set:
            errors.append("trace_manifest_edge_endpoint_missing")
            continue
        outgoing[str(source)].append(str(target))
        incoming[str(target)] += 1
        valid_edge_count += 1

    queue = [key for key, degree in incoming.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if visited != len(key_set):
        errors.append("trace_manifest_not_dag")

    actual_nodes = trace.get("nodes")
    actual_nodes_by_key = {
        str(node.get("node_key")): node
        for node in actual_nodes
        if isinstance(node, Mapping) and node.get("node_key")
    } if isinstance(actual_nodes, list) else {}
    actual_keys = set(actual_nodes_by_key)
    if not key_set.issubset(actual_keys):
        errors.append("trace_execution_nodes_incomplete")
    if isinstance(actual_nodes, list):
        for node in actual_nodes:
            if (
                isinstance(node, Mapping)
                and node.get("node_key") in REQUIRED_V4_NODE_KEYS
                and node.get("status") not in {"completed", "failed", "skipped"}
            ):
                errors.append("trace_required_v4_node_not_terminal")
    missing_nodes = sorted(REQUIRED_V4_NODE_KEYS - key_set)
    if missing_nodes:
        errors.append("trace_required_v4_nodes_missing")
    raw_selected_core = manifest.get("selected_core_roles")
    selected_core = [
        str(item)
        for item in (raw_selected_core or [])
        if isinstance(item, str)
    ]
    if selected_core != list(CORE_SPECIALIST_ROLE_IDS):
        errors.append("trace_core_specialist_registry_invalid")
    raw_selected_dynamic = manifest.get("selected_dynamic_roles")
    selected_dynamic_list = [
        str(item)
        for item in (raw_selected_dynamic or [])
        if isinstance(item, str)
    ]
    selected_dynamic = set(selected_dynamic_list)
    if not isinstance(raw_selected_dynamic, list):
        errors.append("trace_dynamic_specialist_selection_not_array")
    elif len(selected_dynamic_list) != len(selected_dynamic):
        errors.append("trace_dynamic_specialist_selection_duplicate")
    if len(selected_dynamic) > MAXIMUM_DYNAMIC_SPECIALISTS:
        errors.append("trace_dynamic_specialist_selection_exceeds_six")
    declared_dynamic = {
        key.split(":", 1)[1]
        for key in DYNAMIC_SPECIALIST_NODE_KEYS
        if key in key_set
    }
    if not selected_dynamic.issubset(declared_dynamic):
        errors.append("trace_dynamic_specialist_selection_unknown")
    manifest_nodes_by_key = {
        str(node.get("key")): node
        for node in raw_nodes
        if isinstance(node, Mapping) and node.get("key")
    }
    for key in CORE_SPECIALIST_NODE_KEYS:
        if key in key_set and manifest_nodes_by_key.get(key, {}).get("selected") is not True:
            errors.append("trace_core_specialist_not_selected")
    for key in DYNAMIC_SPECIALIST_NODE_KEYS:
        role = key.split(":", 1)[1]
        node = manifest_nodes_by_key.get(key, {})
        if key in key_set and bool(node.get("selected")) != (role in selected_dynamic):
            errors.append("trace_dynamic_specialist_selection_mismatch")
        actual = actual_nodes_by_key.get(key, {})
        if key in key_set and role not in selected_dynamic and actual.get("status") != "skipped":
            errors.append("trace_unselected_dynamic_specialist_not_skipped")
    limits = manifest.get("limits")
    expected_limits = {
        "maximum_provider_network_requests_per_run": (
            MAXIMUM_PROVIDER_NETWORK_REQUESTS_PER_RUN
        ),
        "specialist_provider_request_ceiling": SPECIALIST_PROVIDER_REQUEST_CEILING,
        "maximum_dynamic_specialists": MAXIMUM_DYNAMIC_SPECIALISTS,
        "maximum_selected_specialists": MAXIMUM_SELECTED_SPECIALISTS,
        "hard_timeout_seconds": 420,
    }
    if not isinstance(limits, Mapping) or any(
        limits.get(key) != expected for key, expected in expected_limits.items()
    ):
        errors.append("trace_v4_execution_limits_invalid")
    elif (
        limits.get("normal_llm_calls")
        != len(CORE_SPECIALIST_ROLE_IDS) + len(selected_dynamic) + 2
        or limits.get("maximum_llm_calls_with_revision")
        != len(CORE_SPECIALIST_ROLE_IDS) + len(selected_dynamic) + 3
    ):
        errors.append("trace_v4_llm_call_limits_invalid")
    if len(CORE_SPECIALIST_ROLE_IDS) + len(selected_dynamic) > MAXIMUM_SELECTED_SPECIALISTS:
        errors.append("trace_selected_specialist_count_exceeds_eleven")
    if trace.get("manifest_integrity_ok") is not True:
        errors.append("trace_manifest_integrity_not_verified")

    metadata = {
        "node_count": len(key_set),
        "edge_count": valid_edge_count,
        "agent_node_count": len(REQUIRED_AGENT_NODE_KEYS & key_set),
        "required_agent_node_count": len(REQUIRED_AGENT_NODE_KEYS),
        "core_specialist_count": len(CORE_SPECIALIST_NODE_KEYS & key_set),
        "declared_dynamic_specialist_count": len(DYNAMIC_SPECIALIST_NODE_KEYS & key_set),
        "selected_dynamic_specialist_count": len(selected_dynamic),
        "selected_specialist_count": len(CORE_SPECIALIST_ROLE_IDS) + len(selected_dynamic),
    }
    return list(dict.fromkeys(errors)), metadata


def evaluate_behavior_expectations(
    expectations: BehaviorExpectations,
    result: Mapping[str, Any],
    pathogens: Sequence[Any],
) -> Tuple[List[str], Dict[str, bool]]:
    """Evaluate private fixture expectations without returning their values."""

    errors: List[str] = []
    checks: Dict[str, bool] = {}

    if expectations.summary_must_contain_any:
        raw_summary = result.get("summary_i18n")
        summary_parts: List[str] = []
        if isinstance(raw_summary, Mapping):
            summary_parts = [
                value
                for key, value in raw_summary.items()
                if key in {"zh_cn", "en"} and isinstance(value, str)
            ]
        elif isinstance(raw_summary, str):
            summary_parts = [raw_summary]
        normalized_summary = normalized_oracle_text("\n".join(summary_parts))
        passed = any(
            normalized_oracle_text(term) in normalized_summary
            for term in expectations.summary_must_contain_any
        )
        checks["summary_must_contain_any"] = passed
        if not passed:
            errors.append("behavior_oracle_summary_must_contain_any_failed")

    if expectations.result_must_contain_any:
        # Search the complete public result contract, not merely the summary.
        # The private phrases themselves are never copied into checks or errors.
        normalized_result = normalized_oracle_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        passed = any(
            normalized_oracle_text(term) in normalized_result
            for term in expectations.result_must_contain_any
        )
        checks["result_must_contain_any"] = passed
        if not passed:
            errors.append("behavior_oracle_result_must_contain_any_failed")

    if expectations.min_unknown_score is not None:
        raw_unknown_score = result.get("unknown_score")
        passed = (
            isinstance(raw_unknown_score, (int, float))
            and not isinstance(raw_unknown_score, bool)
            and float(raw_unknown_score) >= expectations.min_unknown_score
        )
        checks["min_unknown_score"] = passed
        if not passed:
            errors.append("behavior_oracle_min_unknown_score_failed")

    if expectations.max_top1_model_score is not None:
        top1 = next(
            (
                candidate
                for candidate in pathogens
                if isinstance(candidate, Mapping) and candidate.get("rank") == 1
            ),
            None,
        )
        raw_top1_score = top1.get("model_score") if isinstance(top1, Mapping) else None
        passed = (
            isinstance(raw_top1_score, (int, float))
            and not isinstance(raw_top1_score, bool)
            and float(raw_top1_score) <= expectations.max_top1_model_score
        )
        checks["max_top1_model_score"] = passed
        if not passed:
            errors.append("behavior_oracle_max_top1_model_score_failed")

    if expectations.minimum_coinfection_hypotheses is not None:
        raw_hypotheses = result.get("coinfection_hypotheses")
        passed = (
            isinstance(raw_hypotheses, list)
            and len(raw_hypotheses) >= expectations.minimum_coinfection_hypotheses
        )
        checks["minimum_coinfection_hypotheses"] = passed
        if not passed:
            errors.append("behavior_oracle_minimum_coinfection_hypotheses_failed")

    return errors, checks


def validate_completed_run(
    case: SyntheticCase,
    run: Mapping[str, Any],
    trace: Mapping[str, Any],
    oracle_trace_text: str,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    errors: List[str] = []
    warnings: List[str] = []
    checks: Dict[str, Any] = {}

    result = run.get("result")
    if run.get("status") != "completed":
        errors.append("run_status_not_completed")
    if run.get("schema_version") != "owlpath.result.v3":
        errors.append("run_schema_not_v3")
    if run.get("execution_graph_version") != "owlpath.execution-graph.v4":
        errors.append("execution_graph_not_v4")
    if run.get("trace_version") != "owlpath.trace.v2":
        errors.append("trace_not_v2")
    if not isinstance(result, Mapping):
        errors.append("v3_result_missing")
        result = {}
    if result.get("schema_version") != "owlpath.result.v3":
        errors.append("result_schema_not_v3")
    if result.get("status") not in PASSING_RESULT_STATUSES:
        errors.append("development_result_not_completed")

    pathogens = result.get("concrete_pathogens")
    if not isinstance(pathogens, list):
        pathogens = []
    if len(pathogens) != 5:
        errors.append("concrete_top5_count_not_five")

    raw_result_warnings = result.get("warnings")
    if raw_result_warnings is None:
        raw_result_warnings = []
    if not isinstance(raw_result_warnings, list):
        errors.append("result_warnings_not_array")
        raw_result_warnings = []
    disclosed_literature_gap = any(
        warning == "candidate_specific_evidence_coverage_partial"
        or (warning.startswith("retrieval_") and warning.endswith("_unavailable"))
        for warning in raw_result_warnings
        if isinstance(warning, str)
    )

    raw_evidence_sources = result.get("evidence_sources")
    if not isinstance(raw_evidence_sources, list):
        errors.append("evidence_sources_not_array")
        raw_evidence_sources = []
    evidence_source_ids: List[str] = []
    for source in raw_evidence_sources:
        source_id = source.get("evidence_source_id") if isinstance(source, Mapping) else None
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append("evidence_source_registry_entry_invalid")
            continue
        evidence_source_ids.append(source_id.strip())
    if len(evidence_source_ids) != len(set(evidence_source_ids)):
        errors.append("evidence_source_registry_duplicate_id")
    evidence_source_id_set = set(evidence_source_ids)
    referenced_evidence_source_ids: set[str] = set()
    trace_manifest = trace.get("manifest")
    manifest_selected_roles = set(CORE_SPECIALIST_ROLE_IDS)
    if isinstance(trace_manifest, Mapping):
        manifest_selected_roles.update(
            str(role)
            for role in (trace_manifest.get("selected_dynamic_roles") or [])
            if isinstance(role, str)
        )

    names: List[str] = []
    taxonomy_ids: List[int] = []
    model_scores: List[float] = []
    safe_top5: List[Dict[str, Any]] = []
    for expected_rank, candidate in enumerate(pathogens, start=1):
        if not isinstance(candidate, Mapping):
            errors.append("top5_candidate_not_object")
            continue
        name = str(candidate.get("canonical_latin_name") or "").strip()
        normalized = normalized_name(name)
        names.append(normalized)
        if candidate.get("rank") != expected_rank:
            errors.append("top5_rank_sequence_invalid")
        if not normalized:
            errors.append("top5_name_missing")
        elif (
            normalized in GENERIC_PATHOGEN_LABELS
            or normalized.endswith(" sp.")
            or normalized.endswith(" spp.")
            or normalized.startswith(("unknown ", "unidentified ", "uncultured ", "other "))
        ):
            errors.append("top5_contains_generic_pathogen_label")
        if candidate.get("taxonomic_rank") not in {"species", "species_complex", "virus_type"}:
            errors.append("top5_taxonomic_rank_not_concrete")
        taxonomy_id = candidate.get("ncbi_taxonomy_id")
        if not isinstance(taxonomy_id, int) or isinstance(taxonomy_id, bool) or taxonomy_id <= 0:
            errors.append("top5_taxonomy_id_invalid")
        else:
            taxonomy_ids.append(taxonomy_id)
        if candidate.get("taxonomy_resolution_status") not in {"resolved", "cache_resolved"}:
            errors.append("top5_taxonomy_unresolved")
        roles = candidate.get("proposed_by_agent_roles")
        if not isinstance(roles, list) or not roles:
            errors.append("top5_agent_provenance_missing")
            roles = []
        normalized_roles = [str(role) for role in roles if isinstance(role, str)]
        if len(normalized_roles) != len(roles) or any(
            role not in ACTIVE_SPECIALIST_ROLE_IDS for role in normalized_roles
        ):
            errors.append("top5_agent_provenance_role_not_active_rank_%d" % expected_rank)
        if any(role not in manifest_selected_roles for role in normalized_roles):
            errors.append("top5_agent_provenance_role_not_selected_rank_%d" % expected_rank)
        if len(normalized_roles) != len(set(normalized_roles)):
            errors.append("top5_agent_provenance_role_duplicate_rank_%d" % expected_rank)
        support = candidate.get("supporting_evidence")
        has_source = isinstance(support, list) and any(
            isinstance(link, Mapping)
            and isinstance(link.get("source_fragment_ids"), list)
            and bool(link.get("source_fragment_ids"))
            for link in support
        )
        if not has_source:
            errors.append("top5_source_provenance_missing")
        candidate_evidence_source_ids: set[str] = set()
        evidence_links = []
        for field_name in ("supporting_evidence", "opposing_evidence"):
            raw_links = candidate.get(field_name)
            if not isinstance(raw_links, list):
                errors.append("top5_evidence_links_not_array_rank_%d" % expected_rank)
                continue
            evidence_links.extend(raw_links)
        for link in evidence_links:
            if not isinstance(link, Mapping):
                errors.append("top5_evidence_link_invalid_rank_%d" % expected_rank)
                continue
            link_source_ids = link.get("evidence_source_ids")
            if not isinstance(link_source_ids, list):
                errors.append("top5_evidence_source_ids_not_array_rank_%d" % expected_rank)
                continue
            for source_id in link_source_ids:
                if not isinstance(source_id, str) or not source_id.strip():
                    errors.append("top5_evidence_source_id_invalid_rank_%d" % expected_rank)
                    continue
                candidate_evidence_source_ids.add(source_id.strip())
        referenced_evidence_source_ids.update(candidate_evidence_source_ids)
        missing_registry_ids = candidate_evidence_source_ids - evidence_source_id_set
        if missing_registry_ids:
            # Record only the candidate rank, never the citation identifiers.
            errors.append("top5_evidence_source_reference_missing_rank_%d" % expected_rank)
        valid_literature_source_count = len(
            candidate_evidence_source_ids & evidence_source_id_set
        )
        if valid_literature_source_count == 0:
            if disclosed_literature_gap:
                warnings.append(
                    "top5_literature_coverage_partial_disclosed_rank_%d" % expected_rank
                )
            else:
                errors.append(
                    "top5_literature_coverage_missing_undisclosed_rank_%d" % expected_rank
                )
        raw_score = candidate.get("model_score")
        if (
            not isinstance(raw_score, (int, float))
            or isinstance(raw_score, bool)
            or not 0 <= float(raw_score) <= 1
        ):
            errors.append("top5_model_score_invalid")
        else:
            model_scores.append(float(raw_score))
        safe_top5.append(
            {
                "rank": candidate.get("rank"),
                "canonical_latin_name": " ".join(name.split())[:240],
                "ncbi_taxonomy_id": taxonomy_id,
                "taxonomy_resolution_status": candidate.get("taxonomy_resolution_status"),
                "model_score": candidate.get("model_score"),
                "proposed_by_agent_roles": [str(role)[:80] for role in roles],
                "literature_source_count": valid_literature_source_count,
            }
        )
    if len(names) != len(set(names)):
        errors.append("top5_names_not_unique")
    if len(taxonomy_ids) != len(set(taxonomy_ids)):
        errors.append("top5_taxonomy_ids_not_unique")
    if len(model_scores) == 5 and any(
        model_scores[index] < model_scores[index + 1] for index in range(4)
    ):
        errors.append("top5_model_scores_not_descending")
    orphan_evidence_source_count = len(
        evidence_source_id_set - referenced_evidence_source_ids
    )
    if orphan_evidence_source_count:
        errors.append("evidence_sources_orphaned")

    rendered_result = json.dumps(result, ensure_ascii=False, sort_keys=True).casefold()
    if any(term in rendered_result for term in FORBIDDEN_ABSTENTION_TERMS):
        errors.append("result_contains_abstention_wording")

    expected_names = {normalized_name(item) for item in case.expected_any_of}
    if expected_names:
        expected_pass = (
            len(expected_names.intersection(names))
            >= case.minimum_expected_set_overlap
        )
        if not expected_pass:
            # Keep both the expected names and threshold private.
            errors.append("private_oracle_expected_set_overlap_failed")
    else:
        expected_pass = None
        warnings.append("weak_oracle_expected_any_of_not_configured")

    oracle_haystack = normalized_oracle_text(rendered_result + "\n" + oracle_trace_text)
    missing_fragment_indexes = [
        index
        for index, fragment in enumerate(case.required_fragments, start=1)
        if normalized_oracle_text(fragment) not in oracle_haystack
    ]
    for index in missing_fragment_indexes:
        # Never place the fragment itself in logs or artifacts.
        errors.append("weak_oracle_required_fragment_missing_%d" % index)
    if not case.required_fragments:
        warnings.append("weak_oracle_required_fragments_not_configured")

    behavior_errors, behavior_checks = evaluate_behavior_expectations(
        case.expectations, result, pathogens
    )
    errors.extend(behavior_errors)

    dag_errors, trace_metadata = validate_dag(trace)
    errors.extend(dag_errors)
    checks.update(
        {
            "v3_contract": not any(
                code.startswith(("run_", "result_", "development_", "concrete_"))
                for code in errors
            ),
            "top5_count": len(pathogens),
            "unique_concrete_names": len(names) == 5 and len(set(names)) == 5,
            "taxonomy_resolved": not any("taxonomy" in code for code in errors),
            "source_and_agent_provenance": not any("provenance" in code for code in errors),
            "evidence_source_integrity": not any(
                "evidence_source" in code or "evidence_sources" in code for code in errors
            ),
            "evidence_registry_source_count": len(evidence_source_id_set),
            "evidence_referenced_source_count": len(
                evidence_source_id_set & referenced_evidence_source_ids
            ),
            "evidence_orphan_source_count": orphan_evidence_source_count,
            "top5_candidates_with_literature": sum(
                1 for item in safe_top5 if item.get("literature_source_count", 0) > 0
            ),
            "no_abstention_wording": "result_contains_abstention_wording" not in errors,
            # Persist only a boolean outcome. The expected set, achieved count,
            # and required threshold remain private to the local fixture.
            "expected_set_overlap_pass": expected_pass,
            "required_fragment_count": len(case.required_fragments),
            "required_fragments_found": len(case.required_fragments) - len(missing_fragment_indexes),
            # Boolean outcomes only: private phrases and numeric thresholds are
            # deliberately absent from persisted regression artifacts.
            "behavior_oracles": behavior_checks,
            "trace_dag": "trace_manifest_not_dag" not in errors,
            "trace": trace_metadata,
            "top5": safe_top5,
        }
    )
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings)), checks


def allocate_output_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / timestamp
    counter = 1
    while candidate.exists():
        candidate = root / (timestamp + "-%02d" % counter)
        counter += 1
    candidate.mkdir(parents=False)
    return candidate


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def render_markdown(records: Sequence[Mapping[str, Any]], providers: Sequence[Mapping[str, str]]) -> str:
    passed = sum(1 for record in records if record.get("passed") is True)
    lines = [
        "# OwlPath 纯虚构病例回归摘要",
        "",
        "> 本批运行使用纯虚构病例与真实配置模型 API，可能产生费用。分数未校准，结果仅用于开发回归。",
        "",
        "- 生成时间：%s" % utc_iso(),
        "- 用例数：%d" % len(records),
        "- 通过：%d" % passed,
        "- 失败：%d" % (len(records) - passed),
        "- Provider ID：%s" % (", ".join(row.get("id") or "?" for row in providers) or "未记录"),
        "",
        "| 虚构病例 | Run ID | 运行结果 | 回归 | 具体 Top-5 | 问题代码 |",
        "|---|---|---|---|---|---|",
    ]
    for record in records:
        top5 = record.get("top5") or []
        names = "<br>".join(
            "%s. %s（文献 %s）" % (
                html.escape(str(item.get("rank", "?"))),
                html.escape(str(item.get("canonical_latin_name", "?"))),
                html.escape(str(item.get("literature_source_count", 0))),
            )
            for item in top5
            if isinstance(item, Mapping)
        ) or "—"
        errors = "<br>".join(str(code) for code in record.get("errors") or []) or "—"
        lines.append(
            "| %s | `%s` | %s | %s | %s | %s |"
            % (
                html.escape(str(record.get("case_id") or "?")).replace("|", "\\|"),
                html.escape(str(record.get("run_id") or "—")),
                html.escape(str(record.get("development_status") or record.get("run_status") or "unknown")),
                "通过" if record.get("passed") else "失败",
                names.replace("|", "\\|"),
                errors.replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "说明：本摘要不保存病例全文、API Key、节点原始输入输出或 Provider 原始响应。",
            "",
        ]
    )
    return "\n".join(lines)


def write_markdown(path: Path, records: Sequence[Mapping[str, Any]], providers: Sequence[Mapping[str, str]]) -> None:
    content = render_markdown(records, providers)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def safe_failure_record(
    case: SyntheticCase,
    provider_ids: Sequence[str],
    started_at: str,
    started_monotonic: float,
    code: str,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "owlpath.synthetic-regression.result.v1",
        "case_id": case.case_id,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": utc_iso(),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "provider_ids": list(provider_ids),
        "run_status": "unknown",
        "development_status": "unknown",
        "passed": False,
        "errors": [sanitize_code(code)],
        "warnings": [],
        "top5": [],
        "checks": {},
    }


def create_outcome_unknown_record(
    case: SyntheticCase,
    provider_ids: Sequence[str],
    started_at: str,
    started_monotonic: float,
) -> Dict[str, Any]:
    """Record a timed-out creation request without pretending it did not run.

    Once the POST body has been sent, a client timeout cannot tell us whether
    the server committed and started the run.  The runner therefore never
    retries that POST and exposes the ambiguity explicitly.
    """

    record = safe_failure_record(
        case,
        provider_ids,
        started_at,
        started_monotonic,
        "create_run_outcome_unknown_http_timeout",
    )
    record["run_status"] = "outcome_unknown"
    record["development_status"] = "outcome_unknown"
    return record


def development_run_payload(
    case: SyntheticCase, provider_ids: Sequence[str]
) -> Dict[str, Any]:
    """Build the complete outbound body from an explicit allowlist.

    In particular, local behavior expectations, weak oracle terms, fixture
    titles, and private oracle metadata can never enter this payload.
    """

    return {
        "text": case.text,
        "provider_ids": list(provider_ids),
        "specialist_config_version": "owlpath.development-agents.synthetic-regression.v3",
    }


def run_one_case(
    client: ApiClient,
    case: SyntheticCase,
    provider_ids: Sequence[str],
    timeout_seconds: float,
) -> Dict[str, Any]:
    started_at = utc_iso()
    started_monotonic = time.monotonic()
    run_id: Optional[str] = None
    try:
        try:
            created = client.post(
                "/api/development/runs",
                development_run_payload(case, provider_ids),
            )
        except ApiError as exc:
            if exc.code == "http_timeout":
                return create_outcome_unknown_record(
                    case,
                    provider_ids,
                    started_at,
                    started_monotonic,
                )
            raise
        if not isinstance(created, Mapping) or not created.get("id"):
            raise ApiError("create_run_response_missing_id")
        run_id = str(created["id"])
        run = poll_run(client, run_id, timeout_seconds)
        trace = fetch_trace(client, run_id)
        oracle_text = ""
        if case.required_fragments:
            oracle_text = fetch_oracle_trace_text(client, run_id, trace)
        errors, warnings, checks = validate_completed_run(case, run, trace, oracle_text)
        result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
        return {
            "schema_version": "owlpath.synthetic-regression.result.v1",
            "case_id": case.case_id,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": utc_iso(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
            "provider_ids": list(provider_ids),
            "run_status": run.get("status"),
            "development_status": result.get("status"),
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "top5": checks.pop("top5", []),
            "checks": checks,
        }
    except RunnerError as exc:
        return safe_failure_record(
            case, provider_ids, started_at, started_monotonic, exc.code, run_id
        )
    except Exception as exc:  # Defensive: never serialize an exception message.
        return safe_failure_record(
            case,
            provider_ids,
            started_at,
            started_monotonic,
            "unexpected_%s" % type(exc).__name__,
            run_id,
        )


def dry_run(cases: Sequence[SyntheticCase], requested_provider_ids: Sequence[str]) -> int:
    oracle_errors = validate_fixture_oracles(cases)
    print("离线 dry-run：未连接本地服务，未调用任何模型 API。")
    print("已加载 %d 个显式标记为纯虚构的病例。" % len(cases))
    for case in cases:
        print(
            "- %s：expected_any_of=%d，required_fragments=%d，behavior_expectations=%d"
            % (
                case.case_id,
                len(case.expected_any_of),
                len(case.required_fragments),
                behavior_expectation_count(case.expectations),
            )
        )
    if requested_provider_ids:
        print("执行时将指定 Provider：%s" % ", ".join(requested_provider_ids))
    else:
        print("执行时将自动选择 enabled + ready + external Provider。")
    if oracle_errors:
        for error in oracle_errors:
            print("夹具错误：%s" % error, file=sys.stderr)
        return 1
    return 0


def _good_pathogen(rank: int, name: str, taxonomy_id: int) -> Dict[str, Any]:
    return {
        "rank": rank,
        "canonical_latin_name": name,
        "taxonomic_rank": "species",
        "category": "bacteria",
        "ncbi_taxonomy_id": taxonomy_id,
        "taxonomy_resolution_status": "cache_resolved",
        "model_score": 1.0 - rank / 10.0,
        "supporting_evidence": [
            {
                "source_fragment_ids": ["src-1"],
                "evidence_source_ids": ["self-test-evidence-%d" % rank],
            }
        ],
        "opposing_evidence": [],
        "proposed_by_agent_roles": ["infectious_diseases"],
    }


def self_test() -> int:
    """Exercise the validators and artifact redaction without HTTP."""
    numeric_range_baseline = normalized_oracle_text("每日10至12次")
    assert numeric_range_baseline == normalized_oracle_text("每日10-12次")
    assert numeric_range_baseline == normalized_oracle_text("每日10–12次")
    assert numeric_range_baseline != normalized_oracle_text("每日10至13次")

    sentinel = "SENTINEL_SYNTHETIC_CASE_TEXT_MUST_NOT_BE_PERSISTED"
    private_behavior_term = "PRIVATE_BEHAVIOR_TERM_MUST_NOT_BE_PERSISTED"
    case = SyntheticCase(
        case_id="SELF-TEST-001",
        # A title might accidentally repeat source prose; it must not be
        # persisted either, so use the same sentinel here.
        title=sentinel,
        text=sentinel,
        expected_any_of=("Streptococcus pneumoniae",),
        required_fragments=(sentinel,),
        minimum_expected_set_overlap=1,
        expectations=BehaviorExpectations(
            summary_must_contain_any=(private_behavior_term,),
            result_must_contain_any=(private_behavior_term,),
            min_unknown_score=0.5,
            max_top1_model_score=0.95,
            minimum_coinfection_hypotheses=1,
        ),
    )
    pathogens = [
        _good_pathogen(1, "Streptococcus pneumoniae", 1313),
        _good_pathogen(2, "Staphylococcus aureus", 1280),
        _good_pathogen(3, "Escherichia coli", 562),
        _good_pathogen(4, "Klebsiella pneumoniae", 573),
        _good_pathogen(5, "Pseudomonas aeruginosa", 287),
    ]
    selected_dynamic_roles = [
        "radiology",
        "pulmonology",
        "hepatobiliary_pancreatic",
        "neurology_neuroinfection",
        "tropical_medicine_parasitology",
        "antimicrobial_stewardship",
    ]
    selected_dynamic_role_set = set(selected_dynamic_roles)
    ordered_keys = [
        "snapshot",
        "preflight",
        "applicability",
        "input_quality",
        "source_compiler",
        "complexity_router",
        *["specialist:%s" % role for role in CORE_SPECIALIST_ROLE_IDS],
        *["specialist:%s" % role for role in DYNAMIC_SPECIALIST_ROLE_IDS],
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
    ]
    trace = {
        "manifest_integrity_ok": True,
        "manifest": {
            "selected_core_roles": list(CORE_SPECIALIST_ROLE_IDS),
            "selected_dynamic_roles": selected_dynamic_roles,
            "limits": {
                "normal_llm_calls": 13,
                "maximum_llm_calls_with_revision": 14,
                "maximum_provider_network_requests_per_run": (
                    MAXIMUM_PROVIDER_NETWORK_REQUESTS_PER_RUN
                ),
                "specialist_provider_request_ceiling": (
                    SPECIALIST_PROVIDER_REQUEST_CEILING
                ),
                "maximum_dynamic_specialists": MAXIMUM_DYNAMIC_SPECIALISTS,
                "maximum_selected_specialists": MAXIMUM_SELECTED_SPECIALISTS,
                "hard_timeout_seconds": 420,
            },
            "nodes": [
                {
                    "key": key,
                    **(
                        {
                            "selected": key.split(":", 1)[1]
                            in selected_dynamic_role_set
                        }
                        if key in DYNAMIC_SPECIALIST_NODE_KEYS
                        else {"selected": True} if key in CORE_SPECIALIST_NODE_KEYS else {}
                    ),
                }
                for key in ordered_keys
            ],
            "edges": [
                {"from": ordered_keys[index], "to": ordered_keys[index + 1]}
                for index in range(len(ordered_keys) - 1)
            ],
        },
        "nodes": [
            {
                "id": "node-%d" % index,
                "node_key": key,
                "status": (
                    "skipped"
                    if key in DYNAMIC_SPECIALIST_NODE_KEYS
                    and key.split(":", 1)[1] not in selected_dynamic_role_set
                    else "completed"
                ),
            }
            for index, key in enumerate(ordered_keys)
        ],
    }
    run = {
        "status": "completed",
        "schema_version": "owlpath.result.v3",
        "execution_graph_version": "owlpath.execution-graph.v4",
        "trace_version": "owlpath.trace.v2",
        "result": {
            "schema_version": "owlpath.result.v3",
            "status": "completed",
            "concrete_pathogens": pathogens,
            "summary_i18n": {
                "zh_cn": private_behavior_term,
                "en": None,
                "status": "partial",
            },
            "unknown_score": 0.6,
            "coinfection_hypotheses": [{"model_score": 0.5}],
            "evidence_sources": [
                {"evidence_source_id": "self-test-evidence-%d" % rank}
                for rank in range(1, 6)
            ],
            "warnings": [],
        },
    }
    errors, warnings, checks = validate_completed_run(case, run, trace, sentinel)
    assert not errors, errors
    assert not warnings, warnings
    assert checks["trace_dag"] is True
    assert checks["trace"] == {
        "node_count": len(ordered_keys),
        "edge_count": len(ordered_keys) - 1,
        "agent_node_count": len(REQUIRED_AGENT_NODE_KEYS),
        "required_agent_node_count": len(REQUIRED_AGENT_NODE_KEYS),
        "core_specialist_count": 5,
        "declared_dynamic_specialist_count": 20,
        "selected_dynamic_specialist_count": 6,
        "selected_specialist_count": 11,
    }
    assert checks["evidence_source_integrity"] is True
    assert checks["evidence_registry_source_count"] == 5
    assert checks["evidence_referenced_source_count"] == 5
    assert checks["evidence_orphan_source_count"] == 0
    assert checks["top5_candidates_with_literature"] == 5
    assert [item["literature_source_count"] for item in checks["top5"]] == [1] * 5
    assert checks["expected_set_overlap_pass"] is True
    assert checks["behavior_oracles"] == {
        "summary_must_contain_any": True,
        "result_must_contain_any": True,
        "min_unknown_score": True,
        "max_top1_model_score": True,
        "minimum_coinfection_hypotheses": True,
    }

    strict_overlap_case = SyntheticCase(
        case_id="SELF-TEST-STRICT-OVERLAP",
        title="private overlap",
        text=sentinel,
        expected_any_of=("Streptococcus pneumoniae", "Listeria monocytogenes"),
        required_fragments=(sentinel,),
        minimum_expected_set_overlap=2,
    )
    strict_errors, _, strict_checks = validate_completed_run(
        strict_overlap_case, run, trace, sentinel
    )
    assert "private_oracle_expected_set_overlap_failed" in strict_errors
    assert strict_checks["expected_set_overlap_pass"] is False
    assert not any("Listeria" in error or "2" in error for error in strict_errors)

    anywhere_expectations = BehaviorExpectations(
        result_must_contain_any=("chemical pneumonitis", "化学性肺炎")
    )
    result_with_competitor = json.loads(json.dumps(run["result"]))
    result_with_competitor["summary_i18n"]["zh_cn"] = "aspiration syndrome"
    result_with_competitor["agent_observations"] = [
        {"observation": "应同时考虑化学性肺炎这一非感染性竞争解释"}
    ]
    anywhere_errors, anywhere_checks = evaluate_behavior_expectations(
        anywhere_expectations, result_with_competitor, pathogens
    )
    assert not anywhere_errors
    assert anywhere_checks == {"result_must_contain_any": True}

    outbound = development_run_payload(case, ["prv_self_test"])
    assert set(outbound) == {"text", "provider_ids", "specialist_config_version"}
    rendered_outbound = json.dumps(outbound, ensure_ascii=False)
    assert private_behavior_term not in rendered_outbound
    assert "Streptococcus pneumoniae" not in rendered_outbound

    class WrappedTimeoutOpener:
        def open(self, _request: Any, timeout: float = 30.0) -> Any:
            raise urllib.error.URLError(TimeoutError("synthetic timeout"))

    timeout_api = ApiClient(DEFAULT_BASE_URL)
    timeout_api.opener = WrappedTimeoutOpener()  # type: ignore[assignment]
    try:
        timeout_api.post("/api/development/runs", outbound)
    except ApiError as exc:
        assert exc.code == "http_timeout"
    else:
        raise AssertionError("a wrapped urllib timeout must remain a timeout")

    class DirectSocketTimeoutOpener:
        def open(self, _request: Any, timeout: float = 30.0) -> Any:
            raise socket.timeout("synthetic socket timeout")

    timeout_api.opener = DirectSocketTimeoutOpener()  # type: ignore[assignment]
    try:
        timeout_api.post("/api/development/runs", outbound)
    except ApiError as exc:
        assert exc.code == "http_timeout"
    else:
        raise AssertionError("a direct socket timeout must remain a timeout")

    class TimeoutOnCreateClient:
        post_calls = 0

        def post(self, _path: str, _payload: Mapping[str, Any]) -> Any:
            self.post_calls += 1
            raise ApiError("http_timeout")

        def get(self, _path: str, timeout: float = 30.0) -> Any:
            raise AssertionError("a timed-out creation must not be polled or guessed")

    timeout_client = TimeoutOnCreateClient()
    unknown_record = run_one_case(
        timeout_client,  # type: ignore[arg-type]
        case,
        ["prv_self_test"],
        1.0,
    )
    assert timeout_client.post_calls == 1
    assert unknown_record["run_id"] is None
    assert unknown_record["run_status"] == "outcome_unknown"
    assert unknown_record["development_status"] == "outcome_unknown"
    assert unknown_record["errors"] == [
        "create_run_outcome_unknown_http_timeout"
    ]

    invalid_run = json.loads(json.dumps(run))
    invalid_run["result"]["concrete_pathogens"][0]["canonical_latin_name"] = "bacteria"
    invalid_run["result"]["concrete_pathogens"][1]["taxonomy_resolution_status"] = "unresolved"
    invalid_errors, _, _ = validate_completed_run(case, invalid_run, trace, sentinel)
    assert "top5_contains_generic_pathogen_label" in invalid_errors
    assert "top5_taxonomy_unresolved" in invalid_errors

    legacy_provenance_run = json.loads(json.dumps(run))
    legacy_provenance_run["result"]["concrete_pathogens"][0][
        "proposed_by_agent_roles"
    ] = ["timeline_course"]
    legacy_provenance_errors, _, _ = validate_completed_run(
        case, legacy_provenance_run, trace, sentinel
    )
    assert "top5_agent_provenance_role_not_active_rank_1" in legacy_provenance_errors
    assert "top5_agent_provenance_role_not_selected_rank_1" in legacy_provenance_errors

    behavior_failure_run = json.loads(json.dumps(run))
    behavior_failure_run["result"]["summary_i18n"]["zh_cn"] = "no local match"
    behavior_failure_run["result"]["unknown_score"] = 0.1
    behavior_failure_run["result"]["concrete_pathogens"][0]["model_score"] = 0.99
    behavior_failure_run["result"]["coinfection_hypotheses"] = []
    behavior_errors, _, behavior_checks = validate_completed_run(
        case, behavior_failure_run, trace, sentinel
    )
    assert "behavior_oracle_summary_must_contain_any_failed" in behavior_errors
    assert "behavior_oracle_min_unknown_score_failed" in behavior_errors
    assert "behavior_oracle_max_top1_model_score_failed" in behavior_errors
    assert "behavior_oracle_minimum_coinfection_hypotheses_failed" in behavior_errors
    assert behavior_checks["behavior_oracles"] == {
        "summary_must_contain_any": False,
        "result_must_contain_any": False,
        "min_unknown_score": False,
        "max_top1_model_score": False,
        "minimum_coinfection_hypotheses": False,
    }

    dangling_run = json.loads(json.dumps(run))
    dangling_run["result"]["concrete_pathogens"][0]["supporting_evidence"][0][
        "evidence_source_ids"
    ].append("self-test-missing-evidence")
    dangling_errors, _, _ = validate_completed_run(case, dangling_run, trace, sentinel)
    assert "top5_evidence_source_reference_missing_rank_1" in dangling_errors

    orphan_run = json.loads(json.dumps(run))
    orphan_run["result"]["evidence_sources"].append(
        {"evidence_source_id": "self-test-orphan-evidence"}
    )
    orphan_errors, _, _ = validate_completed_run(case, orphan_run, trace, sentinel)
    assert "evidence_sources_orphaned" in orphan_errors

    disclosed_gap_run = json.loads(json.dumps(run))
    disclosed_gap_run["result"]["concrete_pathogens"][1]["supporting_evidence"][0][
        "evidence_source_ids"
    ] = []
    disclosed_gap_run["result"]["evidence_sources"] = [
        source
        for source in disclosed_gap_run["result"]["evidence_sources"]
        if source["evidence_source_id"] != "self-test-evidence-2"
    ]
    disclosed_gap_run["result"]["warnings"] = [
        "candidate_specific_evidence_coverage_partial"
    ]
    disclosed_errors, disclosed_warnings, disclosed_checks = validate_completed_run(
        case, disclosed_gap_run, trace, sentinel
    )
    assert not disclosed_errors, disclosed_errors
    assert "top5_literature_coverage_partial_disclosed_rank_2" in disclosed_warnings
    assert disclosed_checks["top5"][1]["literature_source_count"] == 0

    unavailable_gap_run = json.loads(json.dumps(disclosed_gap_run))
    unavailable_gap_run["result"]["warnings"] = ["retrieval_pubmed_unavailable"]
    unavailable_errors, unavailable_warnings, _ = validate_completed_run(
        case, unavailable_gap_run, trace, sentinel
    )
    assert not unavailable_errors, unavailable_errors
    assert "top5_literature_coverage_partial_disclosed_rank_2" in unavailable_warnings

    undisclosed_gap_run = json.loads(json.dumps(disclosed_gap_run))
    undisclosed_gap_run["result"]["warnings"] = []
    undisclosed_errors, _, _ = validate_completed_run(
        case, undisclosed_gap_run, trace, sentinel
    )
    assert "top5_literature_coverage_missing_undisclosed_rank_2" in undisclosed_errors

    cyclic_trace = json.loads(json.dumps(trace))
    cyclic_trace["manifest"]["edges"].append(
        {"from": ordered_keys[-1], "to": ordered_keys[0]}
    )
    dag_errors, _ = validate_dag(cyclic_trace)
    assert "trace_manifest_not_dag" in dag_errors

    over_selected_trace = json.loads(json.dumps(trace))
    seventh_dynamic_role = "gastroenterology"
    over_selected_trace["manifest"]["selected_dynamic_roles"].append(
        seventh_dynamic_role
    )
    over_selected_trace["manifest"]["limits"]["normal_llm_calls"] = 14
    over_selected_trace["manifest"]["limits"][
        "maximum_llm_calls_with_revision"
    ] = 15
    seventh_node_key = "specialist:%s" % seventh_dynamic_role
    next(
        node
        for node in over_selected_trace["manifest"]["nodes"]
        if node["key"] == seventh_node_key
    )["selected"] = True
    next(
        node
        for node in over_selected_trace["nodes"]
        if node["node_key"] == seventh_node_key
    )["status"] = "completed"
    over_selected_errors, _ = validate_dag(over_selected_trace)
    assert "trace_dynamic_specialist_selection_exceeds_six" in over_selected_errors
    assert "trace_selected_specialist_count_exceeds_eleven" in over_selected_errors

    record = {
        "case_id": case.case_id,
        "run_id": "run_self_test",
        "passed": True,
        "top5": checks["top5"],
        "checks": checks,
        "errors": [],
        "development_status": "completed",
    }
    with tempfile.TemporaryDirectory(prefix="owlpath-regression-self-test-") as temp_dir:
        directory = Path(temp_dir)
        fixture_path = directory / "fixture.json"
        fixture_path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": case.case_id,
                            "title": case.title,
                            "is_synthetic": True,
                            "contains_real_patient_data": False,
                            "text": case.text,
                            "expected_any_of": list(case.expected_any_of),
                            "required_fragments": list(case.required_fragments),
                            "private_oracle": {
                                "minimum_expected_set_overlap": (
                                    case.minimum_expected_set_overlap
                                ),
                                "private_canary": (
                                    "PRIVATE_ORACLE_CANARY_MUST_NOT_BE_PERSISTED"
                                ),
                            },
                            "expectations": {
                                "summary_must_contain_any": list(
                                    case.expectations.summary_must_contain_any
                                ),
                                "result_must_contain_any": list(
                                    case.expectations.result_must_contain_any
                                ),
                                "min_unknown_score": case.expectations.min_unknown_score,
                                "max_top1_model_score": case.expectations.max_top1_model_score,
                                "minimum_coinfection_hypotheses": (
                                    case.expectations.minimum_coinfection_hypotheses
                                ),
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        loaded = load_fixture(fixture_path)
        assert len(loaded) == 1 and loaded[0].case_id == case.case_id
        assert loaded[0].expectations == case.expectations
        assert loaded[0].minimum_expected_set_overlap == 1
        loaded_outbound = development_run_payload(loaded[0], ["prv_self_test"])
        assert set(loaded_outbound) == {
            "text",
            "provider_ids",
            "specialist_config_version",
        }
        assert "PRIVATE_ORACLE_CANARY_MUST_NOT_BE_PERSISTED" not in json.dumps(
            loaded_outbound, ensure_ascii=False
        )

        defaulted = json.loads(fixture_path.read_text(encoding="utf-8"))
        defaulted["cases"][0].pop("private_oracle")
        fixture_path.write_text(json.dumps(defaulted), encoding="utf-8")
        assert load_fixture(fixture_path)[0].minimum_expected_set_overlap == 1

        defaulted["cases"][0]["expected_any_of"] = []
        fixture_path.write_text(json.dumps(defaulted), encoding="utf-8")
        assert load_fixture(fixture_path)[0].minimum_expected_set_overlap == 0

        invalid_overlap = json.loads(fixture_path.read_text(encoding="utf-8"))
        invalid_overlap["cases"][0]["private_oracle"] = {
            "minimum_expected_set_overlap": 1
        }
        fixture_path.write_text(json.dumps(invalid_overlap), encoding="utf-8")
        try:
            load_fixture(fixture_path)
        except RunnerError as exc:
            assert exc.code == "fixture_case_1_minimum_expected_set_overlap_invalid"
        else:
            raise AssertionError("overlap cannot exceed the private expected set")

        normalized_duplicates = json.loads(
            fixture_path.read_text(encoding="utf-8")
        )
        normalized_duplicates["cases"][0]["expected_any_of"] = [
            "Streptococcus pneumoniae",
            "  streptococcus   pneumoniae  ",
        ]
        normalized_duplicates["cases"][0]["private_oracle"] = {
            "minimum_expected_set_overlap": 1
        }
        fixture_path.write_text(
            json.dumps(normalized_duplicates), encoding="utf-8"
        )
        deduplicated = load_fixture(fixture_path)[0]
        assert deduplicated.expected_any_of == ("Streptococcus pneumoniae",)
        assert deduplicated.minimum_expected_set_overlap == 1

        normalized_duplicates["cases"][0]["private_oracle"] = {
            "minimum_expected_set_overlap": 2
        }
        fixture_path.write_text(
            json.dumps(normalized_duplicates), encoding="utf-8"
        )
        try:
            load_fixture(fixture_path)
        except RunnerError as exc:
            assert exc.code == "fixture_case_1_minimum_expected_set_overlap_invalid"
        else:
            raise AssertionError(
                "overlap cannot exceed the normalized unique expected set"
            )

        fixture_path.write_text(json.dumps(defaulted), encoding="utf-8")
        oversized = json.loads(fixture_path.read_text(encoding="utf-8"))
        oversized["cases"][0]["text"] = "x" * (MAX_CASE_TEXT_CHARACTERS + 1)
        fixture_path.write_text(json.dumps(oversized), encoding="utf-8")
        try:
            load_fixture(fixture_path)
        except RunnerError as exc:
            assert exc.code == "fixture_case_1_text_exceeds_30000_characters"
        else:
            raise AssertionError("oversized case text must fail before any API call")
        append_jsonl(directory / "results.jsonl", record)
        write_markdown(directory / "summary.md", [record], [])
        rendered = (directory / "results.jsonl").read_text(encoding="utf-8")
        rendered += (directory / "summary.md").read_text(encoding="utf-8")
        assert sentinel not in rendered
        assert private_behavior_term not in rendered
        assert "PRIVATE_ORACLE_CANARY_MUST_NOT_BE_PERSISTED" not in rendered
        assert "expected_any_of" not in rendered
        assert "minimum_expected_set_overlap" not in rendered
        assert "private_oracle" not in rendered
        assert "expectations" not in rendered
        assert "self-test-evidence-" not in rendered
    try:
        ApiClient("https://example.com")
    except RunnerError as exc:
        assert exc.code == "invalid_base_url"
    else:
        raise AssertionError("the regression runner must be loopback-only")
    print("离线自测通过：未连接本地服务，未调用任何模型 API。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "用纯虚构病例回归 OwlPath 开发 Agent。"
            "真实模型 API 可能产生费用，必须显式传入 --confirm-real-api。"
        )
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--case", action="append", default=[], dest="case_ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--provider-id", action="append", default=[], dest="provider_ids")
    parser.add_argument(
        "--timeout",
        type=float,
        default=420.0,
        help="每个病例的最长轮询时间（秒，默认 420）",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--confirm-real-api",
        action="store_true",
        help="确认可以调用真实模型 API 且可能产生费用",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查纯虚构夹具与选择条件，不连接任何 API",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="运行内置离线自测，不读夹具、不连接任何 API",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    if args.timeout <= 0:
        print("错误：timeout_must_be_positive", file=sys.stderr)
        return 2

    try:
        cases = select_cases(load_fixture(args.fixture), args.case_ids, args.limit)
    except RunnerError as exc:
        if args.dry_run and exc.code == "fixture_not_found":
            print(
                "离线 dry-run：夹具尚未创建（%s）；未连接任何 API。" % args.fixture
            )
            return 0
        print("错误：%s" % exc.code, file=sys.stderr)
        return exc.exit_code

    if args.dry_run:
        return dry_run(cases, args.provider_ids)
    if not args.confirm_real_api:
        print(
            "未执行：每个病例会发起多次真实模型 API 调用，可能产生费用。\n"
            "请确认后显式加上 --confirm-real-api。",
            file=sys.stderr,
        )
        return 2

    oracle_errors = validate_fixture_oracles(cases)
    if oracle_errors:
        for error in oracle_errors:
            print("错误：%s" % error, file=sys.stderr)
        return 2

    print("警告：即将使用纯虚构病例调用真实模型 API，可能产生费用。")
    try:
        client = ApiClient(args.base_url)
        provider_ids, provider_public = select_providers(
            client.get("/api/providers"), args.provider_ids
        )
        output_dir = allocate_output_dir(args.output_root)
    except RunnerError as exc:
        print("错误：%s" % exc.code, file=sys.stderr)
        return exc.exit_code

    print("已选择 Provider：%s" % ", ".join(provider_ids))
    print("安全摘要目录：%s" % output_dir)
    records: List[Dict[str, Any]] = []
    stopped_for_unknown_creation = False
    jsonl_path = output_dir / "results.jsonl"
    markdown_path = output_dir / "summary.md"
    for index, case in enumerate(cases, start=1):
        print("[%d/%d] 运行虚构病例 %s ..." % (index, len(cases), case.case_id))
        record = run_one_case(client, case, provider_ids, args.timeout)
        records.append(record)
        append_jsonl(jsonl_path, record)
        write_markdown(markdown_path, records, provider_public)
        print(
            "[%d/%d] %s：%s"
            % (
                index,
                len(cases),
                case.case_id,
                "通过" if record["passed"] else "失败(%s)" % ",".join(record["errors"]),
            )
        )
        if record.get("run_status") == "outcome_unknown":
            stopped_for_unknown_creation = True
            print(
                "已停止剩余病例：创建请求超时后无法安全判断运行是否已启动；"
                "为避免重复调用和费用，不自动重试或继续并发。",
                file=sys.stderr,
            )
            break

    failed = sum(1 for record in records if not record["passed"])
    if stopped_for_unknown_creation:
        print(
            "停止：%d 例通过，%d 例失败，%d 例未运行。"
            % (len(records) - failed, failed, len(cases) - len(records))
        )
    else:
        print("完成：%d 例通过，%d 例失败。" % (len(records) - failed, failed))
    print("摘要：%s" % markdown_path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
