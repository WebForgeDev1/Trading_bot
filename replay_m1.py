
from pathlib import Path
from market_data import load_market_data

FILENAME = "xauusd-m1-bid-2026-01-01-2026-10-31T23.csv"

paths = [
    Path("data") / FILENAME,
    Path("CSV data") / FILENAME,
]

path = next((p for p in paths if p.exists()), None)

if path is None:
    raise FileNotFoundError("Fichier XAUUSD introuvable")

prices = load_market_data(path)

# Lire les 5 premières bougies dans l'ordre.
sample = prices.head(5)

print("\nXAUUSD N°1 — REPLAY HISTORIQUE M1")
print("-" * 45)

for timestamp, candle in sample.iterrows():
    print(
        f"{timestamp} | "
        f"Open: {candle['open']:.3f} | "
        f"High: {candle['high']:.3f} | "
        f"Low: {candle['low']:.3f} | "
        f"Close: {candle['close']:.3f}"
    )

print("\nBougies lues :", len(sample))
print("Mode : simulation historique uniquement")
