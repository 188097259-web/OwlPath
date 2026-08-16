#!/usr/bin/env python3
"""Verify the public prompt index against the actual runtime source files."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path(__file__).with_name("runtime_prompt_registry.v1.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module_symbols(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result.add(f"{node.name}.{child.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            result.add(node.target.id)
    return result


def literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            return ast.literal_eval(value)
    raise AssertionError(f"runtime assignment not found: {name}")


def main() -> int:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert registry["schema_version"] == "owlpath.prompt-registry.v1"

    parsed: dict[str, ast.Module] = {}
    for source in registry["authoritative_sources"]:
        path = REPO_ROOT / source["path"]
        actual_hash = sha256_file(path)
        assert actual_hash == source["sha256"], (
            f"SHA-256 mismatch for {source['path']}: "
            f"expected {source['sha256']}, got {actual_hash}"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed[source["path"]] = tree
        available = module_symbols(tree)
        missing = sorted(set(source["symbols"]) - available)
        assert not missing, f"missing runtime symbols in {source['path']}: {missing}"

    companion = registry["documentation_companion"]
    companion_path = REPO_ROOT / companion["path"]
    assert sha256_file(companion_path) == companion["sha256"], (
        f"SHA-256 mismatch for {companion['path']}"
    )

    engine_tree = parsed["backend/app/engine.py"]
    runtime_core = [
        {"id": role, "zh_cn": zh_cn, "en": en}
        for role, zh_cn, en in literal_assignment(
            engine_tree, "DEVELOPMENT_CORE_SPECIALIST_ROLES"
        )
    ]
    runtime_dynamic = [
        {"id": role, "zh_cn": zh_cn, "en": en}
        for role, zh_cn, en in literal_assignment(
            engine_tree, "DEVELOPMENT_DYNAMIC_SPECIALIST_ROLES"
        )
    ]
    assert registry["roles"]["core_always_selected"] == runtime_core
    assert registry["roles"]["dynamic_registry"] == runtime_dynamic
    assert registry["roles"]["dynamic_maximum_selected"] == literal_assignment(
        engine_tree, "DEVELOPMENT_MAX_DYNAMIC_SPECIALISTS"
    )

    print(
        "PROMPT_REGISTRY_OK "
        f"sources={len(registry['authoritative_sources'])} "
        f"core_roles={len(runtime_core)} dynamic_roles={len(runtime_dynamic)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
