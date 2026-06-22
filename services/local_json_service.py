import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"


class LocalJsonStore:
    def __init__(self, path, default):
        self.path = Path(path)
        self.default = default
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.write(default)

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.default.copy() if isinstance(self.default, dict) else list(self.default)

    def write(self, payload):
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
