import json
import threading
from copy import deepcopy
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path):
    """Return one in-process lock per data file.

    Streamlit callbacks and upload workers create separate service instances, so
    an instance lock would not protect read-modify-write operations.
    """
    resolved = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(resolved, threading.RLock())


class LocalJsonStore:
    def __init__(self, path, default):
        self.path = Path(path)
        self.default = default
        self._lock = _lock_for(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.write(default)

    def read(self):
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return deepcopy(self.default)

    def write(self, payload):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            text = json.dumps(payload, indent=2)
            temporary.write_text(text, encoding="utf-8")
            try:
                temporary.replace(self.path)
            except PermissionError:
                self.path.write_text(text, encoding="utf-8")
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return payload
