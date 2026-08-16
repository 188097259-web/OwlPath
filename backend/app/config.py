import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def _origins_from_env() -> List[str]:
    raw = os.getenv("OWLPATH_CORS_ORIGINS", "")
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@dataclass
class Settings:
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    database_path: Optional[Path] = None
    master_key: Optional[str] = None
    governance_admin_token: Optional[str] = None
    allow_retrospective_runs: bool = False
    cors_origins: List[str] = field(default_factory=_origins_from_env)
    # Full pathogen JSON is materially larger than the tiny connection-test
    # payload. Cloud providers can legitimately need around a minute to
    # produce it, especially on their first request, so keep a finite but
    # development-friendly ceiling.
    provider_timeout_seconds: float = 120.0
    max_provider_response_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if self.database_path is None:
            configured = os.getenv("OWLPATH_DB_PATH")
            data_dir = os.getenv("OWLPATH_DATA_DIR")
            if configured:
                self.database_path = Path(configured)
            elif data_dir:
                self.database_path = Path(data_dir) / "owlpath.db"
            else:
                self.database_path = self.base_dir / "data" / "owlpath.db"
        if self.master_key is None:
            self.master_key = os.getenv("OWLPATH_MASTER_KEY")
        if self.governance_admin_token is None:
            self.governance_admin_token = os.getenv("OWLPATH_GOVERNANCE_ADMIN_TOKEN")
        retrospective = os.getenv("OWLPATH_ALLOW_RETROSPECTIVE_RUNS")
        if retrospective is not None:
            self.allow_retrospective_runs = retrospective.strip().lower() in {"1", "true", "yes", "on"}
        timeout = os.getenv("OWLPATH_PROVIDER_TIMEOUT_SECONDS")
        if timeout:
            self.provider_timeout_seconds = float(timeout)
