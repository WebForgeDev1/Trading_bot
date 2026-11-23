import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
import fcntl
from dataclasses import asdict
from strategy_n1 import StrategyN1


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class Store:
    def __init__(self, path):
        path = Path(path)
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ticks (id TEXT PRIMARY KEY, ts INTEGER NOT NULL, bid REAL, ask REAL, received INTEGER);
        CREATE INDEX IF NOT EXISTS tick_time ON ticks(ts);
        CREATE TABLE IF NOT EXISTS candles (ts INTEGER, kind TEXT, payload TEXT NOT NULL, PRIMARY KEY(ts,kind));
        CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signals (day TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, payload TEXT NOT NULL);
        ''')
        self.db.commit()

    def backup(self, destination):
        destination=Path(destination)
        if destination.resolve()==self.path.resolve() or destination.exists():
            raise ValueError('La destination de sauvegarde doit être nouvelle')
        destination.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(destination)
        try:
            self.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Sauvegarde non intègre')
        finally:
            target.close()
        return destination

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def state(self):
        row = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save(self, state):
        self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (encode(state),))

    def event(self, ts, kind, **payload):
        self.db.execute('INSERT INTO events(ts,kind,payload) VALUES(?,?,?)', (ts, kind, encode(payload)))

    def put(self, table, key, value):
        if table not in ('days', 'signals', 'trades'):
            raise ValueError('Table invalide')
        self.db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?)', (key, encode(value)))

    def records(self, table):
        if table not in ('days', 'signals', 'trades'):
            raise ValueError('Table invalide')
        return [json.loads(r[0]) for r in self.db.execute(f'SELECT payload FROM {table} ORDER BY 1')]

    def identity(self, config):
        return dict(config=config.identity(), strategy=asdict(StrategyN1()), schema=1)


@contextmanager
def process_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Le suivi fonctionne déjà pour cette base') from None
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def restore_database(source, destination):
    source,destination=Path(source),Path(destination)
    if source.resolve()==destination.resolve() or not source.is_file():
        raise ValueError('Source de restauration invalide')
    connection=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Sauvegarde corrompue')
        required={'state','ticks','candles','days','signals','trades','events'}
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required<=tables:
            raise ValueError('Ce fichier n’est pas une sauvegarde du bot')
        destination.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(destination)
        try:
            connection.backup(target)
            target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            target.close()
    finally:
        connection.close()
