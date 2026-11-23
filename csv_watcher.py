
from pathlib import Path
import pandas as pd

from market_data import load_market_data
from data_tracker import load_last_timestamp, save_last_timestamp

FILENAME = "xauusd-m1-bid-2026-01-01-2026-10-31T23.csv"

paths = [
    Path("data") / FILENAME,
    Path("CSV data") / FILENAME,
]

csv_path = next((p for p in paths if p.exists()), None)

if csv_path is None:
    raise FileNotFoundError("CSV XAUUSD introuvable")

prices = load_market_data(csv_path)
last = load_last_timestamp()

if last is None:
    print("Aucune minute enregistrée.")
    print("Initialisation sur la dernière bougie disponible.")
    save_last_timestamp(prices.index[-1])
else:
    last_time = pd.Timestamp(last)

    new_candles = prices[prices.index > last_time]

    print("XAUUSD — NOUVELLES BOUGIES M1")
    print("Dernière minute enregistrée :", last_time)
    print("Nouvelles bougies :", len(new_candles))

    if not new_candles.empty:
        for timestamp, candle in new_candles.iterrows():
            print(
                timestamp,
                "| Close :", round(float(candle["close"]), 3)
            )

        save_last_timestamp(new_candles.index[-1])
        print("Dernière minute mise à jour.")
    else:
        print("Aucune nouvelle bougie.")

print("Mode simulation uniquement")
