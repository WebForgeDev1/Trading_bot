"""Replay local N°1 : aucune connexion réseau, aucun ordre réel."""
from pathlib import Path
import argparse
import pandas as pd
from market_data import load_market_data, build_daily_candles
from strategy_n1 import StrategyN1

BASE_DIR = Path(__file__).resolve().parent
FILENAME = "xauusd-m1-bid-2020-01-01-2026-10-31T23.csv"
SL_DISTANCE = 5.0
TP_DISTANCE = 8.0
RISK_USD = 50.0
INITIAL_CAPITAL = 1000.0
EXPOSURE = RISK_USD / SL_DISTANCE
TRADE_COLUMNS = ["signal_date", "entry_time", "entry", "sl", "tp",
                 "exit_time", "exit", "outcome", "pnl_usd"]


def run_replay(prices):
    """Rejoue les données sans écrire de fichier ni modifier les trackers.

    Conserve les hypothèses existantes : journées >= 1000 minutes,
    entrée à l'ouverture disponible suivante, SL prioritaire en cas
    d'ambiguïté intraminute, exécution à l'ouverture en cas de gap.
    Le seuil de 1000 minutes ne garantit pas une journée complète.
    """
    # 1. Charger les cotations M1
    if prices.empty:
        raise ValueError("Le CSV historique ne contient aucune bougie")
    daily = build_daily_candles(prices)

    minute_counts = prices["close"].resample("1D").count()

    sessions = {
        day: group
        for day, group in prices.groupby(
            prices.index.normalize(),
            sort=True
        )
    }

    strategy = StrategyN1()

    # État indépendant du position_manager.py
    position = None
    pending_signal = None
    trades = []
    signals = []

    # 2. Parcourir les journées dans l'ordre
    for day, candle in daily.iterrows():

        minutes = sessions.get(day)

        if minutes is None or minutes.empty:
            continue

        # 3. Entrée au premier prix du jour
        #    de trading suivant le signal
        if position is None and pending_signal is not None:

            entry_time = minutes.index[0]
            entry = float(minutes.iloc[0]["open"])

            position = {
                "signal_date": pending_signal.date(),
                "entry_time": entry_time,
                "entry": entry,
                "sl": entry - SL_DISTANCE,
                "tp": entry + TP_DISTANCE,
            }

            pending_signal = None

        # 4. Surveiller la position minute par minute
        if position is not None:

            for minute in minutes.itertuples():

                if minute.Index < position["entry_time"]:
                    continue

                price_open = float(minute.open)
                high = float(minute.high)
                low = float(minute.low)

                sl = position["sl"]
                tp = position["tp"]

                exit_price = None
                outcome = None

                if price_open <= sl:
                    exit_price = price_open
                    outcome = "SL"

                elif price_open >= tp:
                    exit_price = price_open
                    outcome = "TP"

                elif low <= sl and high >= tp:
                    # Hypothèse prudente si l'ordre
                    # intraminute est inconnu
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

        # 5. Vérifier les journées suffisamment couvertes
        #    Filtre provisoire identique au backtest
        count = int(minute_counts.get(day, 0))

        if count < 1000:
            continue

        # Une seule position à la fois
        if position is not None or pending_signal is not None:
            continue

        # 6. Détection du signal N°1
        signal = strategy.check_signal(
            day=day.date(),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            position_open=False,
        )

        if signal:
            signals.append(day.date())
            pending_signal = day

    return {
        "trades": pd.DataFrame(trades, columns=TRADE_COLUMNS),
        "signals": signals,
        "position": position,
        "pending_signal": pending_signal,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="XAUUSD N°1 — simulation historique uniquement")
    parser.add_argument("--csv", type=Path, help="Fichier local de cotations M1")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "n1_replay_results.csv")
    args = parser.parse_args(argv)
    path = args.csv
    if path is None:
        path = next((BASE_DIR / folder / FILENAME for folder in ("data", "CSV data")
                     if (BASE_DIR / folder / FILENAME).is_file()), None)
    if path is None or not path.is_file():
        parser.error("CSV historique introuvable ; préciser --csv")
    try:
        replay = run_replay(load_market_data(path))
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    results = replay["trades"]
    results.to_csv(args.output, index=False)
    print("XAUUSD N°1 — SIMULATION HISTORIQUE UNIQUEMENT")
    print("Signaux retenus :", len(replay["signals"]))
    print("Trades clôturés :", len(results))
    print(f"Résultat brut : {results['pnl_usd'].sum():+.2f} $")
    print("Résultats :", args.output)
    if replay["position"] is not None:
        print("ATTENTION : position fictive encore ouverte")
    if replay["pending_signal"] is not None:
        print("ATTENTION : signal en attente d'entrée")


if __name__ == "__main__":
    main()
