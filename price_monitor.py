
from pathlib import Path

from market_data import load_market_data
from position_manager import PositionManager


def check_price(price):
    manager = PositionManager()

    if manager.position is None:
        print("Aucune position fictive à surveiller.")
        return

    position = manager.position

    if float(price) <= position["sl"]:
        print("SL atteint — fermeture simulée.")
        manager.close_position()

    elif float(price) >= position["tp"]:
        print("TP atteint — fermeture simulée.")
        manager.close_position()

    else:
        print("Position fictive toujours ouverte.")


def check_candle(high, low):
    manager = PositionManager()

    if manager.position is None:
        print("Aucune position fictive à surveiller.")
        return

    position = manager.position

    sl_hit = float(low) <= position["sl"]
    tp_hit = float(high) >= position["tp"]

    if sl_hit and tp_hit:
        print("SL et TP touchés : ordre inconnu.")
        print("Position conservée pour vérification.")

    elif sl_hit:
        print("SL atteint — fermeture simulée.")
        manager.close_position()

    elif tp_hit:
        print("TP atteint — fermeture simulée.")
        manager.close_position()

    else:
        print("Position fictive toujours ouverte.")


def check_latest_csv():
    filename = "xauusd-m1-bid-2026-01-01-2026-10-31T23.csv"

    paths = [
        Path("data") / filename,
        Path("CSV data") / filename,
    ]

    path = next((p for p in paths if p.exists()), None)

    if path is None:
        print("CSV introuvable.")
        return

    prices = load_market_data(path)
    latest = prices.iloc[-1]

    print("Dernière bougie :", prices.index[-1])
    print("Close historique :", latest["close"])

    # Lecture uniquement :
    # Ne jamais appliquer un vieux prix
    # à une position de test actuelle.


if __name__ == "__main__":
    check_latest_csv()
