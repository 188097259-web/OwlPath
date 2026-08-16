#!/usr/bin/env python3
"""Generate public JSON Schema artifacts from the authoritative Pydantic model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = Path(__file__).with_name("owlpath.result.v3.schema.json")


def rendered_result_schema() -> str:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.models import DevelopmentResultV3

    schema = DevelopmentResultV3.model_json_schema(
        ref_template="#/$defs/{model}"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:owlpath:schema:result:v3"
    schema["title"] = "OwlPath Development Result v3"
    schema["description"] = (
        "Generated from backend/app/models.py::DevelopmentResultV3. "
        "Runtime Pydantic and deterministic validators remain authoritative "
        "for cross-field and semantic Top-5 requirements."
    )
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed artifact differs; do not write",
    )
    args = parser.parse_args()
    expected = rendered_result_schema()

    if args.check:
        actual = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if actual != expected:
            print(
                "SCHEMA_DRIFT: run `python3 schemas/export_public_schemas.py`",
                file=sys.stderr,
            )
            return 1
        print("PUBLIC_SCHEMA_OK owlpath.result.v3")
        return 0

    OUTPUT_PATH.write_text(expected, encoding="utf-8")
    print(f"WROTE {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
