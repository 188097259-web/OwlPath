"""Vercel entrypoint for OwlPath.

Vercel's Python runtime expects a FastAPI instance named `app` in a recognized
root entry file. The backend package lives under `backend/`, so this shim adds
it to the import path and re-exports the existing application unchanged.

Vercel Functions have a writable `/tmp` directory but no persistent filesystem,
so runtime data defaults to `/tmp` unless the operator configures
`OWLPATH_DATA_DIR` or `OWLPATH_DB_PATH` in the deployment environment.
"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "backend"))
os.environ.setdefault("OWLPATH_DATA_DIR", "/tmp/owlpath-data")

from app.main import app  # noqa: E402,F401  (re-exported as the Vercel ASGI app)
