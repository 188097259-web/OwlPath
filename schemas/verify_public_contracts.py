#!/usr/bin/env python3
"""Verify public contract indexes without inventing duplicate runtime models."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = Path(__file__).with_name("contracts.index.v1.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def symbols(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result.add(f"{node.name}.{child.name}")
        elif isinstance(node, ast.Assign):
            result.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
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


def checked_source(source: dict[str, Any]) -> tuple[Path, ast.Module]:
    path = REPO_ROOT / source["path"]
    assert sha256_file(path) == source["sha256"], f"SHA-256 mismatch: {source['path']}"
    return path, parse_module(path)


def main() -> int:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    assert index["schema_version"] == "owlpath.public-contract-index.v1"
    contracts = {item["contract_version"]: item for item in index["contracts"]}
    assert set(contracts) == {
        "owlpath.result.v3",
        "owlpath.execution-graph.v4",
        "owlpath.trace.v2",
    }

    result_contract = contracts["owlpath.result.v3"]
    _, models_tree = checked_source(result_contract["runtime_source"])
    assert result_contract["runtime_source"]["symbol"] in symbols(models_tree)

    graph_contract = contracts["owlpath.execution-graph.v4"]
    _, engine_tree = checked_source(graph_contract["runtime_source"])
    engine_symbols = symbols(engine_tree)
    assert graph_contract["runtime_source"]["builder_symbol"] in engine_symbols
    assert literal_assignment(
        engine_tree, graph_contract["runtime_source"]["version_symbol"]
    ) == graph_contract["contract_version"]

    trace_contract = contracts["owlpath.trace.v2"]
    trace_engine_source, trace_api_source = trace_contract["runtime_sources"]
    _, trace_engine_tree = checked_source(trace_engine_source)
    assert literal_assignment(
        trace_engine_tree, trace_engine_source["version_symbol"]
    ) == trace_contract["contract_version"]
    _, api_tree = checked_source(trace_api_source)
    missing_api_symbols = sorted(set(trace_api_source["symbols"]) - symbols(api_tree))
    assert not missing_api_symbols, f"missing trace API symbols: {missing_api_symbols}"

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from export_public_schemas import rendered_result_schema

    schema_path = REPO_ROOT / result_contract["machine_readable_schema"]
    assert schema_path.read_text(encoding="utf-8") == rendered_result_schema(), (
        "generated result schema drift"
    )
    parsed_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert parsed_schema["$id"] == "urn:owlpath:schema:result:v3"
    assert parsed_schema["properties"]["schema_version"]["const"] == "owlpath.result.v3"

    print("PUBLIC_CONTRACTS_OK result=v3 graph=v4 trace=v2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
