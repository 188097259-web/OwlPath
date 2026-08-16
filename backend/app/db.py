import json
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence

from .models import GovernanceConfig, utc_now


REDACTED_KEYS = {
    "api_key", "encrypted_api_key", "authorization", "proxy-authorization",
    "x-api-key", "api-key", "cookie", "set-cookie", "password", "secret",
    "token", "access_token", "refresh_token", "x-goog-api-key",
    "ocp-apim-subscription-key",
}


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in REDACTED_KEYS:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    base_url TEXT,
    encrypted_api_key TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    data_boundary TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    extra_headers_json TEXT NOT NULL DEFAULT '{}',
    options_json TEXT NOT NULL DEFAULT '{}',
    last_test_ok INTEGER,
    last_tested_at TEXT,
    last_test_latency_ms INTEGER,
    last_test_error_code TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    case_alias TEXT NOT NULL,
    demographics_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    external_data_consent INTEGER NOT NULL DEFAULT 0,
    data_origin TEXT NOT NULL DEFAULT 'clinical',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_events (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    collected_at TEXT,
    issued_at TEXT,
    visible_at TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(case_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_case_visible
ON clinical_events(case_id, visible_at, sequence);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id),
    decision_time TEXT NOT NULL,
    run_mode TEXT NOT NULL DEFAULT 'live',
    retrospective_anchor_id TEXT,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ids_json TEXT NOT NULL,
    include_baseline INTEGER NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    provider_configs_json TEXT,
    provider_configs_sha256 TEXT,
    governance_version TEXT NOT NULL,
    governance_config_json TEXT,
    governance_config_sha256 TEXT,
    schema_version TEXT,
    engine_version TEXT,
    input_snapshot_sha256 TEXT,
    run_manifest_sha256 TEXT,
    execution_graph_version TEXT,
    execution_manifest_json TEXT,
    execution_manifest_sha256 TEXT,
    trace_version TEXT,
    result_sha256 TEXT,
    consent_at_run INTEGER NOT NULL,
    clinical_review_json TEXT,
    data_transfer_consent_json TEXT,
    result_json TEXT,
    error_json TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_case_requested
ON runs(case_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS run_execution_nodes (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    display_name_json TEXT NOT NULL DEFAULT '{}',
    parent_node_id TEXT REFERENCES run_execution_nodes(id) ON DELETE SET NULL,
    provider_id TEXT,
    provider_model TEXT,
    status TEXT NOT NULL,
    outcome TEXT,
    sequence INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    input_artifact_id TEXT,
    output_artifact_id TEXT,
    error_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    latency_ms INTEGER,
    UNIQUE(run_id, node_key, attempt)
);

CREATE INDEX IF NOT EXISTS idx_execution_nodes_run_sequence
ON run_execution_nodes(run_id, sequence, started_at);

CREATE TABLE IF NOT EXISTS run_node_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_run_id TEXT NOT NULL REFERENCES run_execution_nodes(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'trace_safe',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_artifacts_node
ON run_node_artifacts(node_run_id, created_at);

CREATE TABLE IF NOT EXISTS run_model_outputs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_run_id TEXT REFERENCES run_execution_nodes(id) ON DELETE SET NULL,
    provider_id TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    provider_kind TEXT,
    provider_model TEXT,
    base_url_origin TEXT,
    provider_weight REAL,
    data_boundary TEXT,
    model_fingerprint TEXT,
    status TEXT NOT NULL,
    raw_response_json TEXT,
    normalized_json TEXT,
    error_json TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_outputs_run
ON run_model_outputs(run_id, created_at);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_events_stream
ON run_events(run_id, id);

CREATE TABLE IF NOT EXISTS governance (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    label_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created
ON audit_log(created_at DESC);
"""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_dumps(value).encode("utf-8")).hexdigest()


def json_loads(value: Optional[str], default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._harden_permissions(include_directory=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn, "providers", {
                "last_test_ok": "INTEGER",
                "last_tested_at": "TEXT",
                "last_test_latency_ms": "INTEGER",
                "last_test_error_code": "TEXT",
                "revision": "INTEGER NOT NULL DEFAULT 1",
            })
            self._ensure_columns(conn, "cases", {
                "data_origin": "TEXT NOT NULL DEFAULT 'clinical'",
            })
            self._ensure_columns(conn, "runs", {
                "schema_version": "TEXT", "engine_version": "TEXT",
                "input_snapshot_sha256": "TEXT", "result_sha256": "TEXT",
                "provider_configs_json": "TEXT", "governance_config_json": "TEXT",
                "clinical_review_json": "TEXT", "data_transfer_consent_json": "TEXT",
                "run_mode": "TEXT NOT NULL DEFAULT 'live'",
                "retrospective_anchor_id": "TEXT",
                "provider_configs_sha256": "TEXT",
                "governance_config_sha256": "TEXT",
                "run_manifest_sha256": "TEXT",
                "execution_graph_version": "TEXT",
                "execution_manifest_json": "TEXT",
                "execution_manifest_sha256": "TEXT",
                "trace_version": "TEXT",
            })
            self._ensure_columns(conn, "run_model_outputs", {
                "provider_kind": "TEXT", "provider_model": "TEXT", "base_url_origin": "TEXT",
                "provider_weight": "REAL", "data_boundary": "TEXT", "model_fingerprint": "TEXT",
                "node_run_id": "TEXT",
            })
            self._ensure_columns(conn, "run_execution_nodes", {
                "outcome": "TEXT",
            })
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_trace_version "
                "ON runs(trace_version, requested_at DESC)"
            )
            existing = conn.execute("SELECT singleton_id FROM governance WHERE singleton_id = 1").fetchone()
            if existing is None:
                config = GovernanceConfig()
                conn.execute(
                    "INSERT INTO governance(singleton_id, config_json, updated_at) VALUES(1, ?, ?)",
                    (json_dumps(config.model_dump(mode="json")), utc_now().isoformat()),
                )
            conn.commit()
        self._harden_permissions(include_directory=True)

    def _harden_permissions(self, include_directory: bool = False) -> None:
        """Keep clinical SQLite artifacts private to the current OS account.

        SQLite creates WAL/SHM sidecars lazily, so this is called both when a
        connection opens and after it closes.  This protects the local research
        default; production still needs encrypted storage and host hardening.
        """
        if include_directory:
            try:
                os.chmod(self.path.parent, 0o700)
            except FileNotFoundError:
                pass
        for candidate in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            try:
                os.chmod(candidate, 0o600)
            except FileNotFoundError:
                pass

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table).fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, definition))

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        self._harden_permissions()
        try:
            yield conn
        finally:
            conn.close()
            self._harden_permissions()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return int(cursor.lastrowid or 0)

    def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute a write and return the number of rows actually changed.

        This is intentionally separate from ``execute`` because insert callers
        use that method's SQLite row id.  Conditional updates use rowcount as an
        optimistic-concurrency guard.
        """
        with self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return int(cursor.rowcount)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]

    def audit(
        self,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: Optional[str],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_details = redact_secrets(dict(details or {}))
        self.execute(
            "INSERT INTO audit_log(actor, action, entity_type, entity_id, details_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (actor, action, entity_type, entity_id, json_dumps(safe_details), utc_now().isoformat()),
        )

    def governance(self) -> GovernanceConfig:
        row = self.fetchone("SELECT config_json FROM governance WHERE singleton_id = 1")
        if row is None:
            return GovernanceConfig()
        payload = json_loads(row["config_json"])
        # One-time compatibility for databases created by the earliest research build.
        if "min_external_models_for_species" in payload:
            payload["min_independent_nonbaseline_models_for_species"] = payload.pop("min_external_models_for_species")
        # The earliest runnable build exposed all syndrome routes even though
        # the signed v1 intended-use contract only covers adult community-onset
        # lower respiratory infection. Narrow that untouched legacy default on
        # read; explicitly versioned user configurations remain unchanged.
        legacy_syndromes = ["respiratory", "bloodstream", "urinary", "central_nervous_system", "other"]
        if payload.get("version") == "0.1.0-research" and payload.get("allowed_syndromes") == legacy_syndromes:
            payload["version"] = "0.2.0-research"
            payload["intended_use"] = GovernanceConfig().intended_use
            payload["minimum_age_years"] = 18.0
            payload["allowed_syndromes"] = ["respiratory"]
            payload["excluded_populations"] = ["immunocompromised", "pregnancy"]
        return GovernanceConfig.model_validate(payload)

    def update_governance(self, config: GovernanceConfig) -> None:
        self.execute(
            "UPDATE governance SET config_json = ?, updated_at = ? WHERE singleton_id = 1",
            (json_dumps(config.model_dump(mode="json")), utc_now().isoformat()),
        )
