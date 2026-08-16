#!/usr/bin/env python3
"""Add verified license evidence to the locked Python runtime SBOM.

The mapping is intentionally version-specific. It fails closed if pip-audit
produces a component set different from the 25 components verified for the
macOS arm64 / Python 3.11.11 runtime snapshot.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SBOM = ROOT / "releases/v0.1.0/sbom-python.cdx.json"


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def spdx(expression: str, evidence: str, files: str) -> dict[str, Any]:
    if " OR " in expression or " AND " in expression or " WITH " in expression:
        licenses = [{"expression": expression}]
    else:
        licenses = [{"license": {"id": expression}}]
    return {
        "licenses": licenses,
        "properties": [
            {"name": "owlpath:license-evidence", "value": evidence},
            {"name": "owlpath:license-files", "value": files},
        ],
    }


VERIFIED: dict[tuple[str, str], dict[str, Any]] = {
    ("annotated-doc", "0.0.5"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("annotated-types", "0.8.0"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("anyio", "4.14.2"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("certifi", "2026.7.22"): spdx("MPL-2.0", "installed METADATA License and classifier", "LICENSE"),
    ("cffi", "2.1.1"): spdx("MIT-0", "installed METADATA License-Expression", "LICENSE"),
    ("click", "8.4.2"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE.txt"),
    ("cryptography", "50.0.0"): spdx(
        "Apache-2.0 OR BSD-3-Clause",
        "installed METADATA License-Expression",
        "LICENSE; LICENSE.APACHE; LICENSE.BSD",
    ),
    ("fastapi", "0.141.1"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("h11", "0.16.0"): spdx("MIT", "installed METADATA License and classifier", "LICENSE.txt"),
    ("httpcore", "1.0.9"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE.md"),
    ("httptools", "0.8.0"): spdx(
        "MIT",
        "installed METADATA License-Expression",
        "LICENSE; vendor/http-parser/LICENSE-MIT; vendor/llhttp/LICENSE",
    ),
    ("httpx", "0.28.1"): spdx("BSD-3-Clause", "installed METADATA License and classifier", "LICENSE.md"),
    ("idna", "3.18"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE.md"),
    ("pycparser", "3.0"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE"),
    ("pydantic", "2.13.4"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("pydantic-core", "2.46.4"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("python-dotenv", "1.2.2"): spdx("BSD-3-Clause", "installed METADATA License", "LICENSE"),
    ("pyyaml", "6.0.3"): spdx("MIT", "installed METADATA License and classifier", "LICENSE"),
    ("starlette", "1.6.0"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE.md"),
    ("typing-extensions", "4.16.0"): spdx("PSF-2.0", "installed METADATA License-Expression", "LICENSE"),
    ("typing-inspection", "0.4.4"): spdx("MIT", "installed METADATA License-Expression", "LICENSE"),
    ("uvicorn", "0.52.3"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE.md"),
    ("watchfiles", "1.2.0"): spdx("MIT", "installed METADATA License and classifier", "LICENSE"),
    ("websockets", "17.0.1"): spdx("BSD-3-Clause", "installed METADATA License-Expression", "LICENSE"),
}

VERIFIED[("uvloop", "0.22.1")] = {
    "licenses": [
        {
            "license": {
                "name": "METADATA says MIT License; distribution also ships Apache-2.0 and MIT license texts"
            }
        }
    ],
    "properties": [
        {
            "name": "owlpath:license-evidence",
            "value": "installed METADATA License plus Apache/MIT classifiers; no SPDX expression declared",
        },
        {"name": "owlpath:license-files", "value": "LICENSE-APACHE; LICENSE-MIT"},
        {
            "name": "owlpath:license-review",
            "value": "manual review retained: do not infer AND/OR relationship",
        },
    ],
}


def expected_document(document: dict[str, Any]) -> dict[str, Any]:
    actual = {
        (normalize(str(component.get("name", ""))), str(component.get("version", "")))
        for component in document.get("components", [])
    }
    expected = set(VERIFIED)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SystemExit(f"SBOM component set changed; missing={missing} extra={extra}")

    for component in document["components"]:
        key = (normalize(component["name"]), str(component["version"]))
        component.pop("licenses", None)
        existing_properties = [
            item
            for item in component.get("properties", [])
            if not str(item.get("name", "")).startswith("owlpath:license-")
        ]
        component.update(VERIFIED[key])
        component["properties"] = existing_properties + VERIFIED[key]["properties"]
    return document


def rendered(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(expected_document(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = args.sbom.resolve()
    expected = rendered(path)
    if args.write:
        path.write_text(expected, encoding="utf-8")
        print(f"PYTHON_SBOM_LICENSES_WRITTEN components={len(VERIFIED)} path={path}")
        return 0
    current = path.read_text(encoding="utf-8")
    if current != expected:
        raise SystemExit("Python SBOM license enrichment is missing or stale; run with --write")
    print(f"PYTHON_SBOM_LICENSES_OK components={len(VERIFIED)} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
