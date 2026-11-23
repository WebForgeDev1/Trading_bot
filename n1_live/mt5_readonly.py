"""Consumes a local MT5 indicator export. No login, trading SDK or order API."""
import csv
import importlib.util
import platform
import time
from pathlib import Path
from .market import Tick, Dukascopy


def environment_report():
    terminal=Path.home()/'Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/terminal64.exe'
    return dict(system=platform.system(),architecture=platform.machine(),
                official_python_package=importlib.util.find_spec('MetaTrader5') is not None,
                wine_terminal_found=terminal.exists(), terminal_path=str(terminal),
                mode='READ_ONLY_LOCAL_EXPORT',orders_supported=False)


def read_quotes(path, symbol, start=0, end=None):
    end=int(time.time()*1000)+1 if end is None else end
    path=Path(path)
    if not path.is_file():
        raise ConnectionError('Export MT5 absent : activer l’indicateur de lecture seule dans le terminal')
    result=[]
    with path.open(newline='',encoding='utf-8-sig') as file:
        reader=csv.DictReader(file)
        required={'timestamp','symbol','bid','ask'}
        if not required<=set(reader.fieldnames or []):
            raise ValueError('Colonnes de l’export MT5 invalides')
        previous=-1
        for row in reader:
            if any(row.get(k) is None for k in required):
                # An append may be in flight; only tolerate an unfinished final line.
                if file.read():raise ValueError('Ligne MT5 incomplète au milieu du fichier')
                break
            if row['symbol']!=symbol:
                raise ValueError('Symbole MT5 différent du symbole configuré')
            try:
                stamp=int(row['timestamp'])
                tick=Tick.parse(dict(timestamp=stamp,bidPrice=row['bid'],askPrice=row['ask'],source='mt5-readonly'))
            except (ValueError,TypeError,KeyError):
                raise ValueError('Cotation MT5 invalide') from None
            if stamp<previous:raise ValueError('Export MT5 hors ordre')
            previous=stamp
            if start<=stamp<end:
                result.append(dict(timestamp=stamp,bidPrice=tick.bid,askPrice=tick.ask,source='mt5-readonly'))
    return result


class MT5ReadOnlyProvider:
    """MT5 quotes + Dukascopy analysis. Never switches price source mid-position."""
    def __init__(self,config):
        self.config=config
        self.analysis=Dukascopy(config)
        self.cancel_check=None

    def read(self,timeframe,start,end):
        if self.cancel_check and self.cancel_check():
            raise InterruptedError('Lecture interrompue')
        if timeframe=='tick':
            return read_quotes(self.config.mt5_csv_path,self.config.mt5_symbol,start,end)
        self.analysis.cancel_check=self.cancel_check
        return self.analysis.read(timeframe,start,end)
