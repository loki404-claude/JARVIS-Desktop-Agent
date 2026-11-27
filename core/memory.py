"""Notes aur yaadein local JSON file (data/memory.json) mein save hoti hain."""
import json
import threading
from datetime import datetime

from core import config

_FILE = config.DATA_DIR / "memory.json"
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("facts", [])
    data.setdefault("notes", [])
    return data


def add(kind: str, text: str) -> None:
    with _LOCK:
        data = _load()
        data[kind].append({"text": text, "time": datetime.now().strftime("%d-%m-%Y %H:%M")})
        data[kind] = data[kind][-200:]
        _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def items(kind: str) -> list:
    with _LOCK:
        return _load()[kind]
