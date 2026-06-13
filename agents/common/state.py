"""Bus di stato condiviso tra agenti: file JSON in agents/state/."""
import json
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def write_state(name: str, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def read_state(name: str, default=None):
    p = STATE_DIR / name
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return default
