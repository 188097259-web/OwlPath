#!/usr/bin/env python3
"""Fail-closed static audit for files considered for an OwlPath release.

The audit deliberately avoids opening local runtime databases. It checks only
the repository candidate and reports paths, never file contents or secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "build", "tmp", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "coverage", "htmlcov",
}
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".master_key", ".p12", ".pfx",
}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".sh", ".command", ".csv", ".cff",
    ".html", ".css", ".svg", "",
}
SECRET_PATTERNS = {
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
ABSOLUTE_USER_PATH = re.compile(r"/(?:Users|home)/[^/\s]+/")
SQLITE_HEADER = b"SQLite format 3\x00"
MAX_FILE_BYTES = 10 * 1024 * 1024


def iter_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_file() or path.is_symlink():
            yield path


def audit_file(path: Path) -> List[Tuple[str, str]]:
    issues: List[Tuple[str, str]] = []
    relative = path.relative_to(ROOT).as_posix()
    if path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(ROOT)
        except (OSError, ValueError):
            issues.append((relative, "symlink resolves outside the repository"))
        return issues
    stat = path.stat()
    if stat.st_size > MAX_FILE_BYTES:
        issues.append((relative, f"file exceeds {MAX_FILE_BYTES // 1024 // 1024} MiB"))
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
        issues.append((relative, "runtime data or secret-bearing filename is forbidden"))
    if path.name.startswith(".env.") and path.name != ".env.example":
        issues.append((relative, "environment file is forbidden"))
    try:
        prefix = path.read_bytes()[: len(SQLITE_HEADER)]
    except OSError:
        issues.append((relative, "file could not be read"))
        return issues
    if prefix == SQLITE_HEADER:
        issues.append((relative, "SQLite database payload is forbidden"))
    if suffix not in TEXT_SUFFIXES or stat.st_size > 2_000_000:
        return issues
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return issues
    if ABSOLUTE_USER_PATH.search(text):
        issues.append((relative, "contains a machine-specific user path"))
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            issues.append((relative, f"contains a possible {label}"))
    return issues


def audit_public_fixture() -> List[Tuple[str, str]]:
    relative = "examples/public_synthetic_case_matrix.v1.json"
    path = ROOT / relative
    if not path.exists():
        return [(relative, "required public synthetic fixture is missing")]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [(relative, "fixture is not valid JSON")]
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or not cases:
        return [(relative, "fixture must contain at least one case")]
    issues: List[Tuple[str, str]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            issues.append((relative, f"case {index} is not an object"))
            continue
        if case.get("is_synthetic") is not True:
            issues.append((relative, f"case {index} is not explicitly synthetic"))
        if case.get("contains_real_patient_data") is not False:
            issues.append((relative, f"case {index} does not reject real patient data"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ci", action="store_true", help="reserved for CI-stable output")
    parser.parse_args()
    issues: List[Tuple[str, str]] = []
    files = list(iter_files())
    for path in files:
        issues.extend(audit_file(path))
    issues.extend(audit_public_fixture())
    if issues:
        print(f"REPOSITORY_AUDIT_FAILED files={len(files)} issues={len(issues)}")
        for relative, reason in sorted(issues):
            print(f"- {relative}: {reason}")
        return 1
    print(f"REPOSITORY_AUDIT_OK files={len(files)} issues=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

