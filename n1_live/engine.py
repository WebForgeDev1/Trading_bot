"""Strict N°1 paper execution. All effects commit in one SQLite transaction."""
import json
from datetime import date, timedelta
from strategy_n1 import StrategyN1
from .calendar import Calendar, dt, PARIS, day_bounds
from .market import Tick, candle, SOURCE
from .storage import encode
from .execution import open_paper, close_paper
from .control import halted, EmergencyStop


class Engine:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.calendar = Calendar(config.extra_closures)
        self.strategy = StrategyN1()

    def _check_emergency(self):
        if halted(self.config.db_path):
            raise EmergencyStop('Arrêt d’urgence persistant : reprise explicite requise')

    def initial(self, now):
        return dict(identity=self.store.identity(self.config), started_at=now,
                    current_day=dt(now).astimezone(PARIS).date().isoformat(),
                    last_quote=None, last_analyzed_minute=None, last_finalized_day=None,
                    pending=None, position=None, connection='STARTING', last_error=None,
                    heartbeat=now, last_event_id=0)

    def _bars(self, rows, now):
        previous = -1
        seen = {}
        for raw in rows:
            bar = candle(raw)
            ts = bar['timestamp']
            if ts < previous:
                raise ValueError('Bougies fournisseur hors ordre')
            previous = ts
            if ts in seen and seen[ts] != bar:
                raise ValueError('Doublon M1 contradictoire')
            seen[ts] = bar
            if ts + 60000 > now:
                continue  # In-progress candles cannot finalize a day.
            placeholder = bar.get('volume') == 0 and bar['open'] == bar['high'] == bar['low'] == bar['close']
            if placeholder:
                old_day = dt(ts).astimezone(PARIS).date().isoformat()
                if self.store.db.execute("SELECT 1 FROM candles WHERE ts=? AND kind='provider'", (ts,)).fetchone() and self._locked_day(old_day):
                    raise ValueError('Bougie finalisée remplacée par un prix sans activité : audit requis')
                self.store.db.execute("INSERT OR REPLACE INTO candles VALUES(?,'placeholder',?)", (ts, encode(bar)))
                self.store.db.execute("DELETE FROM candles WHERE ts=? AND kind='provider'", (ts,))
                continue  # No-trade flat padding is not a valid observed minute.
            old = self.store.db.execute("SELECT payload FROM candles WHERE ts=? AND kind='provider'", (ts,)).fetchone()
            if old and any(json.loads(old[0])[k] != bar[k] for k in ('open','high','low','close')):
                day = dt(ts).astimezone(PARIS).date().isoformat()
                if self._locked_day(day):
                    raise ValueError('Révision de données sur une journée déjà finalisée : audit requis')
            self.store.db.execute("INSERT OR REPLACE INTO candles VALUES(?,'provider',?)", (ts, encode(bar)))

    def _summary(self, day):
        key = day.isoformat()
        a, b = day_bounds(day)
        bars = [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT payload FROM candles WHERE kind='provider' AND ts>=? AND ts<? ORDER BY ts", (a,b))]
        expected = self.calendar.expected_minutes(day)
        actual = {r['timestamp'] for r in bars}
        missing, unexpected = expected-actual, actual-expected
        complete = bool(expected) and not missing and not unexpected
        summary = dict(day=key, complete=complete, expected_minutes=len(expected),
                       observed_minutes=len(actual), missing_minutes=len(missing),
                       unexpected_minutes=len(unexpected), missing_sample=sorted(missing)[:10], source=SOURCE,
                       quality='COMPLETE' if complete else 'MARKET_CLOSED' if not expected and not actual else 'INCOMPLETE')
        candidate = False
        if bars:
            summary.update(open=bars[0]['open'], high=max(x['high'] for x in bars),
                           low=min(x['low'] for x in bars), close=bars[-1]['close'])
            candidate = self.strategy.check_signal(day, summary['high'], summary['low'], summary['close'])
        summary['candidate'] = candidate
        return summary

    def _locked_day(self, key):
        row = self.store.db.execute('SELECT payload FROM days WHERE day=?', (key,)).fetchone()
        if not row:
            return False
        old = json.loads(row[0])
        return old['complete'] and not old.get('historical_only', False)

    def _finalize(self, state, day, now, historical_only=False):
        key = day.isoformat()
        if day_bounds(day)[1] > now:
            return
        old = self.store.db.execute('SELECT payload FROM days WHERE day=?', (key,)).fetchone()
        if old and self._locked_day(key):
            return
        summary = self._summary(day)
        summary['historical_only'] = historical_only
        if old and json.loads(old[0]) == summary:
            return
        self.store.put('days', key, summary)
        self.store.event(now, 'DAY_RECHECKED' if old else 'DAY_FINALIZED', **summary)
        state['last_finalized_day'] = max(state.get('last_finalized_day') or key, key)
        if summary['candidate'] and not self.store.db.execute('SELECT 1 FROM signals WHERE day=?', (key,)).fetchone():
            status, reason = 'ENTRY_PENDING', 'N1_VALID'
            if not summary['complete']:
                status, reason = 'REJECTED', 'INCOMPLETE_DAY'
            elif historical_only:
                status, reason = 'MISSED', 'HISTORICAL_NO_ENTRY'
            elif state['position'] is not None or state['pending'] is not None or key in state.get('blocked_days', []):
                status, reason = 'REJECTED', 'POSITION_OR_ENTRY_EXISTS'
            signal = dict(day=key, candidate=True, status=status, reason=reason, source=SOURCE)
            self.store.put('signals', key, signal)
            self.store.event(now, 'SIGNAL', **signal)
            if status == 'ENTRY_PENDING':
                state['pending'] = signal

    def _audit_history(self, state, now):
        # Bootstrap/recheck downloaded closed days, never open retrospective trades.
        today = dt(now).astimezone(PARIS).date()
        boundary = min(today, date.fromisoformat(state['current_day']))
        start = today-timedelta(days=self.config.history_days)
        first = self.store.db.execute('SELECT MIN(ts) FROM candles').fetchone()[0]
        if first is None:
            return
        start = max(start, dt(first).astimezone(PARIS).date())
        while start < boundary:
            self._finalize(state, start, now, historical_only=True)
            start += timedelta(days=1)

    def _roll(self, state, target, now, bars_asof):
        current = date.fromisoformat(state['current_day'])
        while current < target:
            end = day_bounds(current)[1]
            if state['position'] is not None:
                blocked = state.setdefault('blocked_days', [])
                if current.isoformat() not in blocked:
                    blocked.append(current.isoformat())
            # Never decide from a snapshot taken before midnight. Allow one
            # freshness window for a delayed last candle; retain the tick cursor.
            ready = bars_asof >= end
            summary = self._summary(current) if ready else None
            waiting = not ready or (summary['quality']=='INCOMPLETE' and now-end < self.config.stale_seconds*1000)
            if waiting:
                state['waiting_day'] = current.isoformat()
                return False
            self._finalize(state, current, now)
            current += timedelta(days=1)
            state['current_day'] = current.isoformat()
        state['waiting_day'] = None
        return True

    def _uncertain(self, state, now, reason):
        position = state['position']
        if position and position['status'] == 'OPEN':
            position.update(status='AMBIGUOUS', uncertainty=reason)
            self.store.put('trades', position['id'], position)
            self.store.event(now, 'POSITION_AMBIGUOUS', id=position['id'], reason=reason,
                             message='Position conservée, résultat inconnu, nouvelles entrées bloquées')

    def _sample(self, tick):
        minute = tick.ts // 60000 * 60000
        old = self.store.db.execute("SELECT payload FROM candles WHERE ts=? AND kind='ticks'", (minute,)).fetchone()
        if old:
            bar = json.loads(old[0])
            bar.update(high=max(bar['high'],tick.bid), low=min(bar['low'],tick.bid), close=tick.bid,
                       count=bar['count']+1, last_tick=tick.ts)
        else:
            bar = dict(timestamp=minute, open=tick.bid, high=tick.bid, low=tick.bid,
                       close=tick.bid, count=1, first_tick=tick.ts, last_tick=tick.ts)
        if tick.bid_volume is not None:
            bar['quoted_bid_volume_sum'] = bar.get('quoted_bid_volume_sum', 0) + tick.bid_volume
        self.store.db.execute("INSERT OR REPLACE INTO candles VALUES(?,'ticks',?)", (minute, encode(bar)))

    def _tick(self, state, tick, now, bars_asof):
        if self.store.db.execute('SELECT 1 FROM ticks WHERE id=?', (tick.key,)).fetchone():
            return False
        previous = state['last_quote']
        if tick.ts > now + 2000:
            raise ValueError('Cotation datée dans le futur')
        if previous and tick.ts < previous['timestamp']:
            self.store.event(now, 'OUT_OF_ORDER', timestamp=tick.ts)
            return False
        if previous and abs(tick.bid / previous['bid'] - 1) > self.config.max_jump_fraction:
            raise ValueError('Variation de prix aberrante : cotation refusée, audit requis')
        day = dt(tick.ts).astimezone(PARIS).date()
        if not self._roll(state, day, now, bars_asof) and state['position'] is None:
            return False  # Next poll refetches from the unchanged processed cursor.
        self.store.db.execute('INSERT INTO ticks VALUES(?,?,?,?,?)', (tick.key,tick.ts,tick.bid,tick.ask,now))
        self._sample(tick)
        day = dt(tick.ts).astimezone(PARIS).date()
        if previous and self.calendar.missing_open_time(previous['timestamp'], tick.ts, self.config.max_gap_seconds):
            self.store.event(now, 'QUOTE_GAP', start=previous['timestamp'], end=tick.ts)
            self._uncertain(state, now, 'UNRECOVERED_QUOTE_GAP')
        fresh = 0 <= now-tick.ts <= self.config.stale_seconds*1000
        market_open = self.calendar.is_open(tick.ts)
        pending = state['pending']
        if pending and day.isoformat() > pending['day'] and market_open:
            self._check_emergency()
            # The first available post-signal tick is the only entry opportunity.
            if not fresh:
                pending.update(status='MISSED', reason='FIRST_QUOTE_STALE')
                self.store.put('signals', pending['day'], pending)
                self.store.event(now, 'ENTRY_MISSED', **pending)
                state['pending'] = None
            elif self.config.max_spread_usd is not None and tick.ask-tick.bid > self.config.max_spread_usd:
                pending.update(status='MISSED', reason='SPREAD_LIMIT')
                self.store.put('signals',pending['day'],pending)
                self.store.event(now,'ENTRY_MISSED',**pending)
                state['pending']=None
            elif state['position'] is None:
                position = open_paper(pending['day'],tick,self.config)
                state['position'] = position
                self.store.put('trades', position['id'], position)
                pending.update(status='EXECUTED', trade_id=position['id'])
                self.store.put('signals', pending['day'], pending)
                self.store.event(now, 'PAPER_OPEN', **position)
                state['pending'] = None
        position = state['position']
        if position and position['status'] == 'OPEN' and market_open:
            outcome = 'SL' if tick.bid <= position['sl'] else 'TP' if tick.bid >= position['tp'] else None
            if outcome:
                # First observed BID at/beyond the level: gap/slippage retained.
                position = close_paper(position,tick,outcome,recovered=not fresh)
                self.store.put('trades', position['id'], position)
                self.store.event(now, 'PAPER_CLOSE', **position)
                state['position'] = None
        if previous and previous['timestamp']//60000 < tick.ts//60000:
            state['last_analyzed_minute'] = previous['timestamp']//60000*60000
        state['last_quote'] = dict(timestamp=tick.ts, bid=tick.bid, ask=tick.ask, received_at=now, source=tick.source)
        return True

    def cycle(self, raw_ticks, raw_bars, now, recovery_lost=False, bars_asof=None, processing_clock=None):
        self._check_emergency()
        bars_asof = now if bars_asof is None else bars_asof
        ticks = [Tick.parse(row) for row in raw_ticks]
        expected_source=SOURCE if self.config.quote_source=='dukascopy' else 'mt5-readonly'
        if any(t.source!=expected_source for t in ticks):
            raise ValueError('Changement de source de prix interdit dans cette base')
        if any(t.ts > now+2000 for t in ticks):
            raise ValueError('Cotation datée dans le futur')
        if any(a.ts > b.ts for a,b in zip(ticks,ticks[1:])):
            raise ValueError('Cotations fournisseur hors ordre')
        with self.store.transaction():
            state = self.store.state() or self.initial(now)
            identity=self.store.identity(self.config)
            if state['identity'] != identity:
                additions={'execution_model','commission_per_unit_per_side_usd','financing_per_unit_per_day_usd','max_spread_usd','quote_source','mt5_csv_path','mt5_symbol'}
                old=state['identity']
                base={k:v for k,v in identity['config'].items() if k not in additions}
                if self.config.quote_source=='dukascopy' and 'execution_model' not in old['config'] and old['config']==base and old['strategy']==identity['strategy'] and old['schema']==identity['schema']:
                    state['identity']=identity
                    self.store.event(now,'EXECUTION_MODEL_UPGRADED',model='ASK_BID',legacy_positions_preserved=True)
                elif old['strategy']==identity['strategy'] and old['schema']==identity['schema'] and {k:v for k,v in old['config'].items() if k not in {'commission_per_unit_per_side_usd','financing_per_unit_per_day_usd','max_spread_usd'}} == {k:v for k,v in identity['config'].items() if k not in {'commission_per_unit_per_side_usd','financing_per_unit_per_day_usd','max_spread_usd'}}:
                    state['identity']=identity
                    self.store.event(now,'EXECUTION_COSTS_UPDATED',existing_trade_costs_preserved=True)
                else:
                    raise ValueError('Configuration ou stratégie modifiée : utiliser une autre base')
            self._bars(raw_bars, min(now, bars_asof))
            self._audit_history(state, now)
            if recovery_lost:
                self.store.event(now, 'RECOVERY_LIMIT', message='Historique de ticks hors fenêtre de récupération')
                self._uncertain(state, now, 'RECOVERY_LIMIT')
                if state['pending']:
                    state['pending'].update(status='MISSED',reason='RECOVERY_LIMIT')
                    self.store.put('signals',state['pending']['day'],state['pending'])
                    state['pending']=None
            received_at = now
            count = 0
            for index,tick in enumerate(ticks):
                if index % 256 == 0:
                    self._check_emergency()
                tick_now = max(now, processing_clock()) if processing_clock else now
                count += self._tick(state, tick, tick_now, bars_asof)
            if processing_clock:
                now = max(now, processing_clock())
            self._roll(state, dt(now).astimezone(PARIS).date(), now, bars_asof)
            if ticks:
                last = ticks[-1]
                received = state.get('latest_received_quote')
                if not received or last.ts >= received['timestamp']:
                    state['latest_received_quote'] = dict(timestamp=last.ts,bid=last.bid,ask=last.ask,received_at=received_at,source=last.source)
            quote = state.get('latest_received_quote') or state['last_quote']
            state['current_day_progress'] = self._summary(dt(now).astimezone(PARIS).date())
            days = self.store.records('days')
            if days:
                state['last_day_summary'] = max(days,key=lambda d:d['day'])
                market_days = [d for d in days if d['expected_minutes']]
                state['last_market_day_summary'] = max(market_days,key=lambda d:d['day']) if market_days else None
            connection = 'MARKET_CLOSED' if not self.calendar.is_open(now) else (
                'CONNECTED' if quote and 0 <= now-quote['timestamp'] <= self.config.stale_seconds*1000 else 'STALE')
            if connection != state['connection']:
                self.store.event(now, 'CONNECTION', status=connection)
            state.update(connection=connection, heartbeat=now, last_error=None)
            state['last_event_id'] = self.store.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            self._check_emergency()
            self.store.save(state)
        return state, count

    def error(self, now, message):
        with self.store.transaction():
            state = self.store.state() or self.initial(now)
            state.update(connection='ERROR', last_error=message, heartbeat=now)
            self.store.event(now, 'ERROR', message=message)
            state['last_event_id'] = self.store.db.execute('SELECT MAX(id) FROM events').fetchone()[0]
            self.store.save(state)
