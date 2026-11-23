import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Config:
    mode: str = 'PAPER_ONLY'
    source: str = 'dukascopy-public-bid'
    database: str = 'runtime/live.sqlite3'
    poll_seconds: int = 15
    request_timeout_seconds: int = 90
    stale_seconds: int = 90
    max_gap_seconds: int = 120
    max_jump_fraction: float = 0.05
    recovery_hours: int = 72
    history_days: int = 3
    bell: bool = False
    execution_model: str = 'ASK_BID'
    quote_source: str = 'dukascopy'
    mt5_csv_path: str | None = None
    mt5_symbol: str = 'XAUUSD'
    commission_per_unit_per_side_usd: float | None = None
    financing_per_unit_per_day_usd: float | None = None
    max_spread_usd: float | None = None
    # Explicit UTC closure intervals [from, to), e.g. official holiday breaks.
    extra_closures: list = field(default_factory=list)

    def validate(self):
        if self.mode != 'PAPER_ONLY' or self.source != 'dukascopy-public-bid':
            raise ValueError('Seuls PAPER_ONLY et la source publique BID sont autorisés')
        if self.quote_source not in ('dukascopy','mt5_csv'):
            raise ValueError('Source de cotations non autorisée')
        if self.quote_source=='mt5_csv' and not self.mt5_csv_path:
            raise ValueError('Chemin de l’export MT5 requis')
        if self.execution_model != 'ASK_BID':
            raise ValueError('Le mode actualisé utilise ASK à l’achat et BID à la sortie')
        for name in ('commission_per_unit_per_side_usd','financing_per_unit_per_day_usd','max_spread_usd'):
            value=getattr(self,name)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise ValueError('Coût ou seuil non numérique : '+name)
            if value is not None and name!='financing_per_unit_per_day_usd' and value<0:
                raise ValueError('Coût ou seuil négatif : '+name)
        for name in ('poll_seconds', 'request_timeout_seconds', 'stale_seconds',
                     'max_gap_seconds', 'recovery_hours', 'history_days'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'Configuration invalide : {name}')
        if self.poll_seconds < 5 or self.history_days > 14 or self.recovery_hours > 168:
            raise ValueError('Fréquence ou fenêtre de récupération excessive')
        if not 0 < self.max_jump_fraction < 1:
            raise ValueError('Seuil de contrôle des prix invalide')
        return self

    @property
    def db_path(self):
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p

    def identity(self):
        data = asdict(self)
        for name in ('database', 'poll_seconds', 'request_timeout_seconds', 'bell'):
            data.pop(name)
        return data


def load_config(path=None):
    path = Path(path or ROOT / 'bot_config.json')
    return Config(**json.loads(path.read_text())).validate() if path.exists() else Config().validate()
