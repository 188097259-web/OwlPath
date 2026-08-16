#!/usr/bin/env python3
"""Build a deterministic SHA-256 manifest and file inventory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "build", "tmp", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
SKIP_SUFFIXES = {".tsbuildinfo"}


def files_for_release(excluded: set[Path]) -> Iterable[Path]:
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path in excluded:
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if any(relative.name.endswith(suffix) for suffix in SKIP_SUFFIXES):
            continue
        yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(path: Path) -> str:
    first = path.relative_to(ROOT).parts[0]
    return "root" if len(path.relative_to(ROOT).parts) == 1 else first


def render_manifest(records: list[tuple[str, int, str]]) -> str:
    return "".join(f"{digest}  {relative}\n" for relative, _, digest in records)


def render_inventory(records: list[tuple[str, int, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["path", "category", "bytes", "sha256"])
    for relative, size, digest in records:
        writer.writerow([relative, category(ROOT / relative), size, digest])
    return stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", default="releases/v0.1.0/MANIFEST.sha256"
    )
    parser.add_argument(
        "--inventory", default="releases/v0.1.0/SOURCE_INVENTORY.csv"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed outputs without rewriting them",
    )
    args = parser.parse_args()
    manifest = (ROOT / args.manifest).resolve()
    inventory = (ROOT / args.inventory).resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    inventory.parent.mkdir(parents=True, exist_ok=True)
    excluded = {manifest, inventory}
    records = []
    for path in files_for_release(excluded):
        records.append((path.relative_to(ROOT).as_posix(), path.stat().st_size, sha256(path)))
    expected_manifest = render_manifest(records)
    expected_inventory = render_inventory(records)
    if args.check:
        stale = []
        for path, expected in (
            (manifest, expected_manifest),
            (inventory, expected_inventory),
        ):
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(path.relative_to(ROOT).as_posix())
        if stale:
            print("MANIFEST_CHECK_FAILED stale=" + ",".join(stale))
            print("Run: python scripts/build_release_manifest.py")
            return 1
        print(
            f"MANIFEST_CHECK_OK files={len(records)} "
            f"manifest={manifest.relative_to(ROOT)}"
        )
        return 0
    manifest.write_text(expected_manifest, encoding="utf-8")
    with inventory.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(expected_inventory)
    print(f"MANIFEST_BUILT files={len(records)} manifest={manifest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
