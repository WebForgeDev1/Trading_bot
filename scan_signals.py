
from pathlib import Path

from market_data import load_market_data, build_daily_candles
from strategy_n1 import StrategyN1


# Choisir automatiquement le bon dossier
filename = "xauusd-m1-bid-2025-01-01-2026-01-02.csv"

paths = [
    Path("CSV data") / filename,
    Path("data") / filename,
]

csv_path = next(
    (p for p in paths if p.exists()), None
)

if csv_path is None:
    raise FileNotFoundError(
        "Fichier XAUUSD 2025 introuvable"
    )

# Lire les prix M1 et créer les bougies D1
prices = load_market_data(csv_path)
daily = build_daily_candles(prices)

# Initialiser notre stratégie officielle
bot = StrategyN1()

print("XAUUSD N1 - SCAN 2025")
print("-" * 45)

signals = []

for timestamp, candle in daily.iterrows():
    day = timestamp.date()

    is_buy = bot.check_signal(
        day=day,
        high=float(candle["high"]),
        low=float(candle["low"]),
        close=float(candle["close"]),
        position_open=False,
    )

    if is_buy:
        signals.append(day)

        print(
            f"BUY | {day} | "
            f"High: {candle['high']:.3f} | "
            f"Low: {candle['low']:.3f} | "
            f"Close: {candle['close']:.3f}"
        )

print("-" * 45)
print("Total de signaux candidats :", len(signals))
print("Bougies journalières :", len(daily))
