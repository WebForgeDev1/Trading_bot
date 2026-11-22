
from pathlib import Path
from signal_tracker import load_processed_signals, save_processed_signal
from market_data import load_market_data, build_daily_candles
from strategy_n1 import StrategyN1

BASE_DIR = Path(__file__).resolve().parent
FILENAME = "xauusd-m1-bid-2026-01-01-2026-10-31T23.csv"


def scan_completed_days():
    paths = [
        BASE_DIR / "data" / FILENAME,
        BASE_DIR / "CSV data" / FILENAME,
    ]

    path = next((p for p in paths if p.exists()), None)

    if path is None:
        raise FileNotFoundError("CSV XAUUSD introuvable")

    prices = load_market_data(path)
    daily = build_daily_candles(prices)

    strategy = StrategyN1()
    signals = []

    # Exclure la dernière journée :
    # elle pourrait être encore incomplète.
    for day, candle in daily.iloc[:-1].iterrows():
        signal = strategy.check_signal(
            day=day.date(),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            position_open=False,
        )

        if signal:
            signals.append(day.date())

    return signals

if __name__ == "__main__":
    signals = scan_completed_days()
    processed = load_processed_signals()

    print("XAUUSD N°1 — SIGNAL ENGINE")
    print("Signaux candidats :", len(signals))

    new_signals = 0

    for day in signals:
        if str(day) in processed:
            continue

        print("Nouveau signal historique :", day)
        save_processed_signal(day)
        new_signals += 1

    print("Nouveaux signaux enregistrés :", new_signals)
    print("Simulation historique uniquement")