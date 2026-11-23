import hashlib
import json
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SOURCE = 'dukascopy-public-bid'


def number(value):
    if isinstance(value, bool):
        raise ValueError('Prix booléen invalide')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('Prix non numérique') from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Prix non positif ou non fini')
    return value


def volume(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('Volume non numérique') from None
    if not math.isfinite(result) or result < 0:
        raise ValueError('Volume invalide')
    return result


def timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or value <= 0:
        raise ValueError('Timestamp millisecondes invalide')
    return int(value)


@dataclass(frozen=True)
class Tick:
    ts: int
    bid: float
    ask: float
    bid_volume: float | None = None
    source: str = SOURCE

    @classmethod
    def parse(cls, row):
        tick = cls(timestamp(row['timestamp']), number(row['bidPrice']), number(row['askPrice']), volume(row.get('bidVolume')), row.get('source',SOURCE))
        if tick.source not in (SOURCE,'mt5-readonly'):
            raise ValueError('Source de cotation inconnue')
        if tick.ask < tick.bid:
            raise ValueError('Ask inférieur au Bid')
        return tick

    @property
    def key(self):
        return hashlib.sha256((f'{self.ts}|{self.bid:.10f}|{self.ask:.10f}' if self.source==SOURCE else f'{self.source}|{self.ts}|{self.bid:.10f}|{self.ask:.10f}').encode()).hexdigest()


def candle(row):
    ts = timestamp(row['timestamp'])
    if ts % 60000:
        raise ValueError('Bougie M1 non alignée')
    data = dict(timestamp=ts, **{k:number(row[k]) for k in ('open','high','low','close')})
    if not data['low'] <= min(data['open'], data['close']) <= max(data['open'], data['close']) <= data['high']:
        raise ValueError('OHLC incohérent')
    if 'volume' in row:
        data['volume'] = volume(row['volume'])
    return data


class Dukascopy:
    """Read-only public feed. Node is limited to market data downloads."""
    def __init__(self, config):
        self.config = config
        self.cancel_check = None
        self.bridge = Path(__file__).resolve().parents[1] / 'feed_bridge/read.mjs'

    def read(self, timeframe, start, end):
        if start >= end:
            return []
        command=['node', str(self.bridge), timeframe, str(int(start)), str(int(end))]
        try:
            if self.cancel_check is None:
                result=subprocess.run(command,capture_output=True,text=True,timeout=self.config.request_timeout_seconds)
            else:
                process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                deadline=time.monotonic()+self.config.request_timeout_seconds
                try:
                    while True:
                        if self.cancel_check():
                            raise InterruptedError('Collecte interrompue par arrêt demandé')
                        if time.monotonic()>=deadline:
                            raise ConnectionError('Délai du fournisseur dépassé')
                        try:
                            stdout,stderr=process.communicate(timeout=.2)
                            result=subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill();process.communicate()
        except subprocess.TimeoutExpired:
            raise ConnectionError('Délai du fournisseur dépassé') from None
        if result.returncode:
            raise ConnectionError('Dukascopy indisponible ; réessai automatique')
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValueError('Réponse fournisseur invalide') from None
        if not isinstance(rows, list):
            raise ValueError('Réponse fournisseur non tabulaire')
        for row in rows:
            if not isinstance(row, dict) or 'timestamp' not in row:
                raise ValueError('Ligne fournisseur invalide')
            ts = timestamp(row['timestamp'])
            if not start <= ts < end:
                raise ValueError('Cotation hors de la période demandée')
        return rows
