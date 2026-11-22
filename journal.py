import pandas as pd

trades = pd.read_csv("n1_backtest_2020_2026.csv")

print("\nXAUUSD BOT N°1 — JOURNAL")
print("=" * 40)

print("Trades :", len(trades))
print("Gagnants :", (trades["pnl_usd"] > 0).sum())
print("Perdants :", (trades["pnl_usd"] < 0).sum())
print("Résultat brut :", trades["pnl_usd"].sum(), "$")

print("\n5 DERNIERS TRADES")
print(
    trades[["signal_date", "outcome", "pnl_usd"]]
    .tail(5)
    .to_string(index=False)
)

print("\nMODE SIMULATION UNIQUEMENT")
