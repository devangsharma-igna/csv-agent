import json
from pathlib import Path

CONTEXT_PATH = Path(__file__).parent.parent / "context.json"


def load_context() -> dict | None:
    """Returns parsed JSON dict, or None if file does not exist."""
    if not CONTEXT_PATH.exists():
        return None
    with CONTEXT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_context(data: dict) -> None:
    """Writes data to context.json with indent=2."""
    with CONTEXT_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
