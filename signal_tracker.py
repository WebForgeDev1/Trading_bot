
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "signal_tracker_state.json"


def load_processed_signals():
    if not STATE_FILE.exists():
        return set()

    with STATE_FILE.open("r") as file:
        data = json.load(file)

    return set(data.get("processed_signals", []))


def save_processed_signal(signal_date):
    processed = load_processed_signals()
    processed.add(str(signal_date))

    with STATE_FILE.open("w") as file:
        json.dump(
            {"processed_signals": sorted(processed)},
            file,
            indent=4
        )


if __name__ == "__main__":
    processed = load_processed_signals()

    print("XAUUSD N°1 — SIGNAL TRACKER")
    print("Signaux déjà enregistrés :", len(processed))
    print("Mode simulation uniquement")
