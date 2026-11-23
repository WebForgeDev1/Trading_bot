
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data_tracker_state.json"


def load_last_timestamp():
    if not STATE_FILE.exists():
        return None

    with STATE_FILE.open("r") as file:
        state = json.load(file)

    return state.get("last_timestamp")


def save_last_timestamp(timestamp):
    state = {"last_timestamp": str(timestamp)}

    with STATE_FILE.open("w") as file:
        json.dump(state, file, indent=4)


if __name__ == "__main__":
    last = load_last_timestamp()

    print("XAUUSD N°1 — DATA TRACKER")
    print("Dernière minute traitée :", last)
    print("Mode simulation uniquement")
