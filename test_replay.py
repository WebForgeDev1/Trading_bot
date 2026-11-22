
from pathlib import Path
from market_data import load_market_data

filename = "xauusd-m1-bid-2026-01-01-2026-10-31T23.csv"

path = next(
    p for p in [
        Path("data") / filename,
        Path("CSV data") / filename
    ]
    if p.exists()
)

prices = load_market_data(path)
sample = prices.head(5)

# Position entièrement fictive, sans écriture
# dans position_state.json
entry = 4323.698
sl = entry - 5.0
tp = entry + 8.0
status = "OPEN"

print("REPLAY XAUUSD M1 — TEST")
print(f"Entrée : {entry:.3f}")
print(f"SL : {sl:.3f}")
print(f"TP : {tp:.3f}")

for timestamp, candle in sample.iterrows():
    if status != "OPEN":
        break

    high = float(candle["high"])
    low = float(candle["low"])

    print(f"{timestamp} | High={high:.3f} Low={low:.3f}")

    if low <= sl and high >= tp:
        status = "AMBIGUOUS"
    elif low <= sl:
        status = "SL"
    elif high >= tp:
        status = "TP"

print("Résultat :", status)
print("Simulation uniquement")
