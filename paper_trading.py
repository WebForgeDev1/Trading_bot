
from pathlib import Path
import pandas as pd

from market_data import load_market_data, build_daily_candles
from strategy_n1 import StrategyN1


# Configuration officielle N°1
SL_DISTANCE = 5.0
TP_DISTANCE = 8.0
RISK_USD = 50.0
INITIAL_CAPITAL = 1000.0

# Hypothèse simplifiée :
# 10 unités d'exposition pour un risque de 50 $ sur 5 $.
EXPOSURE = RISK_USD / SL_DISTANCE

FILENAME = "xauusd-m1-bid-2020-01-01-2026-10-31T23.csv"

paths = [
    Path("data") / FILENAME,
    Path("CSV data") / FILENAME,
]

csv_path = next((p for p in paths if p.exists()), None)

if csv_path is None:
    raise FileNotFoundError("CSV XAUUSD 2025 introuvable")

prices = load_market_data(csv_path)
daily = build_daily_candles(prices)

# Nombre de minutes présentes dans chaque journée.
minute_counts = prices["close"].resample("1D").count()

bot = StrategyN1()

position = None
pending_signal = None
trades = []
candidates = []
skipped_days = []

# Regrouper les prix M1 par journée de Paris.
sessions = {
    day: group
    for day, group in prices.groupby(
        prices.index.normalize(), sort=True
    )
}

for day, candle in daily.iterrows():

    minutes = sessions.get(day)

    if minutes is None or minutes.empty:
        continue

    # 1. Entrée au premier prix disponible
    #    du jour de trading suivant le signal.
    if position is None and pending_signal is not None:

        entry_time = minutes.index[0]
        entry_price = float(minutes.iloc[0]["open"])

        position = {
            "signal_date": pending_signal.date(),
            "entry_time": entry_time,
            "entry": entry_price,
            "sl": entry_price - SL_DISTANCE,
            "tp": entry_price + TP_DISTANCE,
        }

        pending_signal = None

    # 2. Examiner les minutes tant qu'une
    #    position est ouverte.
    if position is not None:

        for minute in minutes.itertuples():

            # Ne jamais lire une minute antérieure
            # à l'entrée.
            if minute.Index < position["entry_time"]:
                continue

            open_price = float(minute.open)
            high = float(minute.high)
            low = float(minute.low)

            sl = position["sl"]
            tp = position["tp"]

            exit_price = None
            outcome = None

            # Si le marché ouvre déjà au-delà
            # d'un niveau, sortie au prix d'ouverture.
            if open_price <= sl:
                exit_price = open_price
                outcome = "SL"

            elif open_price >= tp:
                exit_price = open_price
                outcome = "TP"

            elif low <= sl and high >= tp:
                # L'ordre intraminute est inconnu :
                # hypothèse prudente = SL d'abord.
                exit_price = sl
                outcome = "SL_AMBIGUOUS"

            elif low <= sl:
                exit_price = sl
                outcome = "SL"

            elif high >= tp:
                exit_price = tp
                outcome = "TP"

            if exit_price is not None:

                pnl = (
                    exit_price - position["entry"]
                ) * EXPOSURE

                trades.append({
                    **position,
                    "exit_time": minute.Index,
                    "exit": exit_price,
                    "outcome": outcome,
                    "pnl_usd": round(pnl, 2),
                })

                position = None
                break

    # 3. Vérifier le signal à la fin de la journée.
    #    On ne retient que les journées contenant
    #    au moins 1000 minutes de cotation.
    #    C'est un filtre provisoire de qualité.
    count = int(minute_counts.get(day, 0))

    if count < 1000:
        skipped_days.append((day.date(), count))
        continue

    # Une position encore ouverte bloque
    # la création d'un nouveau signal.
    if position is not None or pending_signal is not None:
        continue

    signal = bot.check_signal(
        day=day.date(),
        high=float(candle["high"]),
        low=float(candle["low"]),
        close=float(candle["close"]),
        position_open=False,
    )

    if signal:
        candidates.append(day.date())
        pending_signal = day


# 4. Résultats
results = pd.DataFrame(trades)

print("\nXAUUSD N1 - PAPER BACKTEST 2020-2026")
print("=" * 55)

print("Signaux retenus :", len(candidates))
print("Trades clôturés :", len(trades))
print("Journées sous 1000 minutes :", len(skipped_days))

if not results.empty:

    pnl_total = results["pnl_usd"].sum()

    capital = INITIAL_CAPITAL + pnl_total

    wins = (results["pnl_usd"] > 0).sum()
    losses = (results["pnl_usd"] < 0).sum()

    print("\nTRADES :")

    for trade in trades:
        print(
            f"{trade['signal_date']} | "
            f"Entrée: {trade['entry']:.3f} | "
            f"Sortie: {trade['exit']:.3f} | "
            f"{trade['outcome']} | "
            f"PnL: {trade['pnl_usd']:+.2f} $"
        )

    print("\nRÉSULTATS :")
    print("Wins :", wins)
    print("Losses :", losses)
    print(f"PnL total : {pnl_total:+.2f} $")
    print(f"Capital théorique final : {capital:.2f} $")

    output = Path("n1_backtest_2020_2026.csv")
    results.to_csv(output, index=False)

    print(f"\nRésultats sauvegardés : {output}")

else:
    print("Aucun trade clôturé.")

if position is not None:
    print("\nATTENTION : une position reste ouverte à la fin du CSV.")

if pending_signal is not None:
    print("\nATTENTION : un signal attend encore son entrée.")
