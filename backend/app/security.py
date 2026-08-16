import base64
import os
import secrets
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """Encrypt provider secrets at rest with Fernet.

    Production deployments should set OWLPATH_MASTER_KEY from an OS secret manager.
    For local-only research use, a mode-0600 sidecar key is generated beside the DB.
    """

    def __init__(self, database_path: Path, configured_key: Optional[str] = None) -> None:
        key = self._load_key(database_path, configured_key)
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise SecretStoreError("OWLPATH_MASTER_KEY must be a valid Fernet key") from exc

    @staticmethod
    def _load_key(database_path: Path, configured_key: Optional[str]) -> bytes:
        if configured_key:
            return configured_key.encode("ascii")
        key_path = database_path.with_suffix(database_path.suffix + ".master_key")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            os.chmod(key_path, 0o600)
            return key_path.read_bytes().strip()
        raw = secrets.token_bytes(32)
        key = base64.urlsafe_b64encode(raw)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(key_path), flags, 0o600)
        try:
            os.write(fd, key + b"\n")
        finally:
            os.close(fd)
        return key

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError("Stored provider secret cannot be decrypted") from exc
