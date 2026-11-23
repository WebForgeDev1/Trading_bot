
import json
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "position_state.json"


class PositionManager:

    def __init__(self):
        self.position = self.load_position()

    def load_position(self):
        if not STATE_FILE.exists():
            return None

        with open(STATE_FILE, "r") as file:
            return json.load(file)

    def save_position(self):
        with open(STATE_FILE, "w") as file:
            json.dump(self.position, file, indent=4)

    def open_position(self, entry_price):
        if self.position is not None:
            print("Une position est déjà ouverte.")
            return False

        self.position = {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": float(entry_price),
            "sl": float(entry_price) - 5.0,
            "tp": float(entry_price) + 8.0,
            "status": "OPEN",
        }

        self.save_position()
        print("Position fictive enregistrée.")
        return True

    def close_position(self):
        if self.position is None:
            print("Aucune position ouverte.")
            return False

        self.position = None
        self.save_position()
        print("Position fictive fermée.")
        return True

    def show_position(self):
        if self.position is None:
            print("Aucune position ouverte.")
        else:
            print("\nPOSITION FICTIVE ACTIVE")
            for key, value in self.position.items():
                print(f"{key}: {value}")


if __name__ == "__main__":
    manager = PositionManager()
    manager.show_position()

