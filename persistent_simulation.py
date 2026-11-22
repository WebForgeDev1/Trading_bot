"""Simulation historique locale avec état atomique et reprise chronologique."""
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
import fcntl
import pandas as pd
from strategy_n1 import StrategyN1
from replay_engine import TRADE_COLUMNS


def atomic_write(path, content):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fingerprint(prices):
    return hashlib.sha256(pd.util.hash_pandas_object(prices[['open', 'high', 'low', 'close']], index=True).values.tobytes()).hexdigest()


def advance(state, prices):
    strategy = StrategyN1()
    for row in prices.itertuples():
        timestamp = row.Index.isoformat()
        day = row.Index.date().isoformat()
        current = state['day']
        if current is not None and current['date'] != day:
            # La journée précédente n'est évaluée qu'à l'arrivée du jour suivant.
            if current['count'] >= 1000 and strategy.check_signal(
                pd.Timestamp(current['date']).date(), current['high'], current['low'],
                current['close'], position_open=state['position'] is not None
            ):
                state['signals'].append(current['date'])
                state['pending_signal'] = current['date']
            state['day'] = None
        if state['position'] is None and state['pending_signal'] is not None:
            state['position'] = dict(strategy.calculate_levels(float(row.open)),
                signal_date=state['pending_signal'], entry_time=timestamp)
            state['pending_signal'] = None
        position = state['position']
        if position is not None:
            sl, tp = position['sl'], position['tp']
            exit_price = outcome = None
            if row.open <= sl:
                exit_price, outcome = float(row.open), 'SL'
            elif row.open >= tp:
                exit_price, outcome = float(row.open), 'TP'
            elif row.low <= sl and row.high >= tp:
                exit_price, outcome = sl, 'SL_AMBIGUOUS'
            elif row.low <= sl:
                exit_price, outcome = sl, 'SL'
            elif row.high >= tp:
                exit_price, outcome = tp, 'TP'
            if outcome:
                state['trades'].append(dict(position, exit_time=timestamp,
                    exit=exit_price, outcome=outcome,
                    pnl_usd=round((exit_price-position['entry'])*strategy.risk_usd/strategy.sl_distance, 2)))
                state['position'] = None
        if state['day'] is None:
            state['day'] = dict(date=day, high=float(row.high), low=float(row.low), close=float(row.close), count=1)
        else:
            current = state['day']
            current.update(high=max(current['high'], float(row.high)),
                           low=min(current['low'], float(row.low)), close=float(row.close), count=current['count']+1)
        state['last_timestamp'] = timestamp


def simulate(prices, state_path, output_path, source):
    state_path, output_path = Path(state_path), Path(output_path)
    if state_path.resolve() == output_path.resolve():
        raise ValueError('Les chemins état et résultats doivent être différents')
    if prices.empty or prices.index.tz is None or not prices.index.is_monotonic_increasing or not prices.index.is_unique:
        raise ValueError('Cotations vides ou horodatages invalides')
    values = prices[['open', 'high', 'low', 'close']]
    import numpy as np
    if not np.isfinite(values.to_numpy(dtype=float)).all() or (values <= 0).any().any():
        raise ValueError('Prix invalides')
    if ((prices.low > prices[['open', 'close']].min(axis=1)) | (prices.high < prices[['open', 'close']].max(axis=1))).any():
        raise ValueError('Bougies OHLC incohérentes')
    config = dict(asdict(StrategyN1()), minimum_minutes=1000, timezone='Europe/Paris')
    with state_path.with_suffix(state_path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state['version'] != 1 or state['source'] != str(source) or state['config'] != config:
                raise ValueError('État incompatible avec le fichier ou les règles ; utiliser un nouvel état')
            last = pd.Timestamp(state['last_timestamp'])
            previous = prices.loc[prices.index <= last]
            if previous.empty or previous.index[-1] != last or fingerprint(previous) != state['fingerprint']:
                raise ValueError('Historique déjà traité modifié ou tronqué ; utiliser un nouvel état')
            remaining = prices.loc[prices.index > last]
        else:
            state = dict(version=1, source=str(source), config=config, last_timestamp=None,
                         day=None, position=None, pending_signal=None, signals=[], trades=[])
            remaining = prices
        advance(state, remaining)
        state['fingerprint'] = fingerprint(prices)
        # Curseur, position et journal constituent une seule transaction.
        atomic_write(state_path, json.dumps(state, indent=2, allow_nan=False))
        # Export dérivé : régénéré au prochain lancement si l'écriture échoue.
        atomic_write(output_path, pd.DataFrame(state['trades'], columns=TRADE_COLUMNS).to_csv(index=False))
        return state, len(remaining)
