import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from strategy_n1 import StrategyN1
from n1_live.analytics import statistics
from n1_live.calendar import Calendar, day_bounds, dt, PARIS
from n1_live.config import Config
from n1_live.engine import Engine
from n1_live.market import Tick, candle, Dukascopy
from n1_live.storage import Store, process_lock


def quote(ts, bid=101., ask=None):
    # Zero-spread baseline; dedicated execution tests cover nonzero BID/ASK.
    return dict(timestamp=ts,bidPrice=bid,askPrice=ask if ask is not None else bid)


def full_day(day):
    return [dict(timestamp=t,open=101.,high=125.,low=100.,close=101.)
            for t in sorted(Calendar().expected_minutes(day))]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'live.db'
        self.config=replace(Config(),database=str(self.path),max_jump_fraction=0.10)
        self.store=Store(self.path)
        self.addCleanup(lambda:self.store.close())
        self.engine=Engine(self.store,self.config)
        self.day=date(2026,11,19)
        self.start,self.end=day_bounds(self.day)
        self.bars=full_day(self.day)
        self.engine.cycle([],self.bars,self.end-1)

    def enter(self):
        return self.engine.cycle([quote(self.end+1000)],[],self.end+2000)[0]

    def test_buy_levels_and_single_position(self):
        state=self.enter()
        p=state['position']
        self.assertEqual((p['entry'],p['sl'],p['tp'],p['exposure']),(101,96,109,10))
        self.engine.cycle([quote(self.end+1000),quote(self.end+2000)],[],self.end+3000)
        self.assertEqual(len(self.store.records('trades')),1)
        self.assertEqual(self.store.records('signals')[0]['status'],'EXECUTED')

    def test_tp_and_restart_no_duplicates(self):
        self.enter()
        self.store.close()
        self.store=Store(self.path)
        self.engine=Engine(self.store,self.config)
        q=quote(self.end+3000,109.)
        state,count=self.engine.cycle([q],[],self.end+4000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('trades')[0]['pnl_usd'],80)
        _,count=self.engine.cycle([q],[],self.end+5000)
        self.assertEqual(count,0)
        self.assertEqual(len(self.store.records('trades')),1)

    def test_sl_at_first_observed_price(self):
        self.enter()
        state,_=self.engine.cycle([quote(self.end+3000,96.)],[],self.end+4000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('trades')[0]['pnl_usd'],-50)

    def test_pending_survives_restart(self):
        state,_=self.engine.cycle([],[],self.end+100)
        self.assertIsNotNone(state['pending'])
        self.store.close()
        self.store=Store(self.path)
        self.engine=Engine(self.store,self.config)
        self.assertIsNotNone(self.enter()['position'])

    def test_stale_first_quote_missed_not_deferred(self):
        state,_=self.engine.cycle([quote(self.end+1000)],[],self.end+200000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('signals')[0]['status'],'MISSED')
        state,_=self.engine.cycle([quote(self.end+201000)],[],self.end+202000)
        self.assertIsNone(state['position'])

    def test_missing_minute_rejects_candidate(self):
        with self.store.transaction():
            self.store.db.execute("DELETE FROM candles WHERE ts=? AND kind='provider'",(self.bars[10]['timestamp'],))
        self.assertIsNone(self.enter()['position'])
        self.engine.cycle([],[],self.end+100000)
        self.assertEqual(self.store.records('signals')[0]['reason'],'INCOMPLETE_DAY')

    def test_incomplete_current_day_no_signal(self):
        self.assertEqual(self.store.records('signals'),[])
        self.assertEqual(self.store.records('days'),[])

    def test_friday_no_signal(self):
        self.engine.cycle([],full_day(date(2026,11,20)),day_bounds(date(2026,11,21))[0]+1)
        self.assertFalse(any(s['day']=='2026-11-20' for s in self.store.records('signals')))

    def test_unrecoverable_gap_marks_ambiguous(self):
        self.enter()
        state,_=self.engine.cycle([quote(self.end+600000,109)],[],self.end+601000)
        self.assertEqual(state['position']['status'],'AMBIGUOUS')
        self.assertNotIn('pnl_usd',self.store.records('trades')[0])

    def test_recovered_ticks_close_existing_without_opening_old_trade(self):
        self.enter()
        ticks=[quote(self.end+t*60000,101.) for t in range(1,6)]
        ticks.append(quote(self.end+360000,109.))
        state,_=self.engine.cycle(ticks,[],self.end+600000)
        self.assertIsNone(state['position'])
        self.assertTrue(self.store.records('trades')[0]['recovered'])

    def test_recovery_limit_blocks_position_result(self):
        self.enter()
        state,_=self.engine.cycle([],[],self.end+10000,recovery_lost=True)
        self.assertEqual(state['position']['status'],'AMBIGUOUS')

    def test_out_of_order_batch_atomic_rejection(self):
        before=self.store.state()
        with self.assertRaises(ValueError):
            self.engine.cycle([quote(self.end+2000),quote(self.end+1000)],[],self.end+3000)
        self.assertEqual(self.store.state(),before)
        self.assertEqual(self.store.records('trades'),[])

    def test_future_tick_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.cycle([quote(self.end+100000)],[],self.end+2000)

    def test_price_outlier_rejected(self):
        self.enter()
        before=self.store.state()
        with self.assertRaises(ValueError):
            self.engine.cycle([quote(self.end+3000,1000)],[],self.end+4000)
        self.assertEqual(self.store.state(),before)

    def test_failed_commit_rolls_back_trade_and_quote(self):
        with patch.object(self.store,'save',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.enter()
        self.assertEqual(self.store.records('trades'),[])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ticks').fetchone()[0],0)
        self.assertIsNotNone(self.enter()['position'])

    def test_m1_built_from_ticks(self):
        self.engine.cycle([quote(self.end+1000,101),quote(self.end+2000,103),quote(self.end+3000,100)],[],self.end+4000)
        bar=json.loads(self.store.db.execute("SELECT payload FROM candles WHERE kind='ticks'").fetchone()[0])
        self.assertEqual((bar['open'],bar['high'],bar['low'],bar['close']),(101,103,100,100))

    def test_changed_rules_refused(self):
        engine=Engine(self.store,replace(self.config,stale_seconds=60))
        with self.assertRaises(ValueError):
            engine.cycle([],[],self.end+1000)

    def test_errors_and_reconnect_persist(self):
        self.engine.error(self.end,'Disconnected')
        self.assertEqual(self.store.state()['connection'],'ERROR')
        self.enter()
        self.assertEqual(self.store.state()['connection'],'CONNECTED')

    def test_data_revision_after_finalization_refused(self):
        self.enter()
        revised=dict(self.bars[0],high=126)
        with self.assertRaises(ValueError):
            self.engine.cycle([],[revised],self.end+4000)

    def test_conflicting_duplicate_candles_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.cycle([],[self.bars[0],dict(self.bars[0],high=126)],self.end-1)

    def test_zero_volume_padding_is_not_data(self):
        placeholder=dict(self.bars[10],open=101.,high=101.,low=101.,close=101.,volume=0)
        self.engine.cycle([],[placeholder],self.end-1)
        self.assertIsNone(self.enter()['position'])
        self.engine.cycle([],[],self.end+100000)
        self.assertEqual(self.store.records('days')[0]['missing_minutes'],1)

    def test_first_recovered_quote_stale_even_if_latest_is_fresh(self):
        state,_=self.engine.cycle([quote(self.end+1000),quote(self.end+201000)],[],self.end+202000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('signals')[0]['status'],'MISSED')

    def test_existing_position_rejects_next_signal(self):
        self.enter()
        monday=date(2026,11,23)
        end=day_bounds(monday)[1]
        self.engine.cycle([],full_day(monday),end+1)
        signals=self.store.records('signals')
        self.assertEqual(next(s for s in signals if s['day']==str(monday))['reason'],'POSITION_OR_ENTRY_EXISTS')

    def test_position_holds_without_time_limit(self):
        self.enter()
        # Weekend gap is a scheduled closure, not missing surveillance.
        with self.store.transaction():
            state=self.store.state()
            friday_close=day_bounds(date(2026,11,21))[0]-3600000-1000
            state['last_quote']=dict(timestamp=friday_close,bid=101,ask=101.2,received_at=friday_close)
            self.store.save(state)
        monday=day_bounds(date(2026,11,23))[0]
        state,_=self.engine.cycle([quote(monday+1000)],[],monday+2000)
        self.assertEqual(state['position']['status'],'OPEN')


    def test_bootstrap_reports_closed_days_without_old_entries(self):
        # New install starts Friday, but has Thursday's complete data.
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'bootstrap.db')
            try:
                engine=Engine(store,self.config)
                state,_=engine.cycle([quote(self.end+1000)],self.bars,self.end+2000)
                self.assertEqual(state['last_finalized_day'],str(self.day))
                self.assertTrue(state['last_market_day_summary']['complete'])
                self.assertIsNone(state['position'])
                self.assertIsNone(state['pending'])
                self.assertEqual(store.records('signals')[0]['reason'],'HISTORICAL_NO_ENTRY')
            finally:
                store.close()

    def test_midnight_cache_cannot_finalize_or_consume_first_tick(self):
        first=quote(self.end+1000)
        state,count=self.engine.cycle([first],[],self.end+2000,bars_asof=self.end-1000)
        self.assertEqual(count,0)
        self.assertEqual(state['waiting_day'],str(self.day))
        self.assertIsNone(state['last_quote'])
        self.assertIsNone(state['position'])
        state,_=self.engine.cycle([first],self.bars,self.end+15000,bars_asof=self.end+14000)
        self.assertEqual(state['position']['entry_time'],first['timestamp'])

    def test_delayed_last_candle_waits_then_enters_first_price(self):
        missing=self.bars[-1]
        with self.store.transaction():
            self.store.db.execute("DELETE FROM candles WHERE ts=? AND kind='provider'",(missing['timestamp'],))
        first=quote(self.end+1000)
        state,count=self.engine.cycle([first],[],self.end+2000)
        self.assertEqual(count,0)
        self.assertEqual(state['waiting_day'],str(self.day))
        state,_=self.engine.cycle([first,quote(self.end+15000,102)],[missing],self.end+16000)
        self.assertEqual(state['position']['entry'],101)
        self.assertEqual(state['position']['entry_time'],first['timestamp'])

    def test_quote_aging_during_recovery_is_not_used_for_entry(self):
        state,_=self.engine.cycle([quote(self.end+1000)],[],self.end+2000,
                                  processing_clock=lambda:self.end+100000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('signals')[0]['status'],'MISSED')
        self.assertEqual(state['heartbeat'],self.end+100000)

    def test_too_late_candle_recovery_never_opens_old_signal(self):
        with self.store.transaction():
            self.store.db.execute("DELETE FROM candles WHERE ts=? AND kind='provider'",(self.bars[-1]['timestamp'],))
        first=quote(self.end+1000)
        self.engine.cycle([first],[],self.end+2000)
        state,_=self.engine.cycle([first],self.bars,self.end+200000)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('signals')[0]['status'],'MISSED')

    def test_candle_failure_does_not_stop_position_exit(self):
        from n1_live.app import Poller
        self.enter()
        class Provider:
            def read(inner, timeframe, start, end):
                if timeframe=='m1':
                    raise ConnectionError('fixture M1 outage')
                return [quote(self.end+3000,109)]
        state,_,warning=Poller(self.engine,Provider()).step(self.end+4000,self.end+4000)
        self.assertIsNotNone(warning)
        self.assertIsNone(state['position'])
        self.assertEqual(self.store.records('trades')[0]['status'],'CLOSED_TP')

    def test_position_at_midnight_blocks_signal_after_delayed_daily_data(self):
        self.enter()
        day=date(2026,11,23)
        boundary=day_bounds(day)[1]
        with self.store.transaction():
            state=self.store.state()
            state['current_day']=str(day)
            state['last_quote']=dict(timestamp=boundary-3600001,bid=101,ask=101.2,received_at=boundary-3600001)
            self.store.save(state)
        state,_=self.engine.cycle([quote(boundary+1000,109)],[],boundary+2000,bars_asof=boundary-1)
        self.assertIsNone(state['position'])
        state,_=self.engine.cycle([quote(boundary+3000)],full_day(day),boundary+4000)
        self.assertIsNone(state['position'])
        self.assertEqual(next(x for x in self.store.records('signals') if x['day']==str(day))['reason'],'POSITION_OR_ENTRY_EXISTS')

    def test_incomplete_history_rechecked_when_data_arrives_without_trade(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'history.db')
            try:
                engine=Engine(store,self.config)
                state,_=engine.cycle([],self.bars[:-1],self.end+200000)
                self.assertFalse(state['last_market_day_summary']['complete'])
                state,_=engine.cycle([],self.bars,self.end+201000)
                self.assertTrue(state['last_market_day_summary']['complete'])
                self.assertIsNone(state['pending'])
                self.assertIsNone(state['position'])
            finally:
                store.close()

    def test_full_application_signal_entry_tp_sl_restarts(self):
        # Exercise the actual app acquisition -> strategy -> persisted trade path.
        import contextlib,io
        from n1_live.app import run
        for final_price,outcome,pnl in ((109,'CLOSED_TP',80),(96,'CLOSED_SL',-50)):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as folder:
                config=replace(self.config,database=str(Path(folder)/'app.db'))
                stages=[(self.end-1000,[],self.bars),
                        (self.end+2000,[quote(self.end+1000)],self.bars),
                        (self.end+4000,[quote(self.end+3000,final_price)],self.bars),
                        (self.end+5000,[quote(self.end+3000,final_price)],self.bars)]
                for now,ticks,bars in stages:
                    class Provider:
                        def read(inner,timeframe,start,end):
                            return ticks if timeframe=='tick' else bars
                    with patch('n1_live.app.Dukascopy',return_value=Provider()),patch('n1_live.app.now_ms',return_value=now),contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(run(config,once=True),0)
                store=Store(config.db_path)
                try:
                    trades=store.records('trades')
                    self.assertEqual(len(trades),1)
                    self.assertEqual((trades[0]['status'],trades[0]['pnl_usd']),(outcome,pnl))
                    self.assertEqual(trades[0]['entry_time'],self.end+1000)
                    self.assertIsNone(store.state()['position'])
                    kinds=[r[0] for r in store.db.execute('SELECT kind FROM events')]
                    self.assertEqual(kinds.count('PAPER_OPEN'),1)
                    self.assertEqual(kinds.count('PAPER_CLOSE'),1)
                    self.assertIn('DAY_FINALIZED',kinds)
                finally:
                    store.close()

    def test_continuous_loop_reconnects_without_manual_restart(self):
        import contextlib,io
        from n1_live.app import run
        clock=[self.end-1000]
        class Stop:
            done=False
            waits=0
            elapsed=0
            def is_set(inner):return inner.done
            def set(inner):inner.done=True
            def wait(inner,delay):
                inner.waits+=1
                inner.elapsed+=delay
                inner.done=inner.elapsed>=self.config.poll_seconds*3
        class Provider:
            failed=False
            def read(inner,timeframe,start,end):
                if not inner.failed:
                    inner.failed=True
                    clock[0]=self.end+2000
                    raise ConnectionError('fixture transient outage')
                return [quote(self.end+1000)] if timeframe=='tick' else self.bars
        stop=Stop()
        with patch('n1_live.app.Dukascopy',return_value=Provider()),patch('n1_live.app.threading.Event',return_value=stop),patch('n1_live.app.now_ms',side_effect=lambda:clock[0]),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.config),0)
        self.assertEqual(stop.elapsed,self.config.poll_seconds*3)
        self.assertEqual(len(self.store.records('trades')),1)
        self.assertEqual(self.store.state()['connection'],'STOPPED')
        kinds=[r[0] for r in self.store.db.execute('SELECT kind FROM events')]
        self.assertIn('ERROR',kinds)
        self.assertIn('PAPER_OPEN',kinds)

    def test_app_network_failure_and_next_run_recovers(self):
        import contextlib,io
        from n1_live.app import run
        class FailedProvider:
            def read(inner,*args):
                raise ConnectionError('fixture disconnect')
        class GoodProvider:
            def read(inner,timeframe,*args):
                return [quote(self.end+1000)] if timeframe=='tick' else self.bars
        with patch('n1_live.app.Dukascopy',return_value=FailedProvider()),patch('n1_live.app.now_ms',return_value=self.end-1000),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.config,once=True),1)
        with patch('n1_live.app.Dukascopy',return_value=GoodProvider()),patch('n1_live.app.now_ms',return_value=self.end+2000),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.config,once=True),0)
        self.assertIsNotNone(self.store.state()['position'])



class ValidationTests(unittest.TestCase):
    def test_strategy_boundaries(self):
        s=StrategyN1()
        day=date(2026,11,19)
        self.assertTrue(s.check_signal(day,125,100,101.5))
        self.assertFalse(s.check_signal(day,125,100,101.501))
        self.assertFalse(s.check_signal(day,124.99,100,100))
        self.assertFalse(s.check_signal(day,125,100,101,position_open=True))

    def test_dst_day_lengths(self):
        spring=day_bounds(date(2026,3,29)); autumn=day_bounds(date(2026,10,25))
        self.assertEqual(spring[1]-spring[0],23*3600000)
        self.assertEqual(autumn[1]-autumn[0],25*3600000)
        calendar=Calendar()
        self.assertFalse(calendar.expected_minutes(date(2026,11,21)))
        self.assertEqual(len(calendar.expected_minutes(date(2026,11,19))),1380)

    def test_exceptional_closure(self):
        cal=Calendar([['2026-11-19T15:00:00+00:00','2026-11-19T16:00:00+00:00']])
        self.assertEqual(len(cal.expected_minutes(date(2026,11,19))),1320)

    def test_bad_prices(self):
        for price in (float('nan'),float('inf'),-1,0,True,None,{}):
            with self.subTest(price=price),self.assertRaises(ValueError):
                Tick.parse(quote(1000,price,101))
        with self.assertRaises(ValueError):
            Tick.parse(quote(1000,101,100))

    def test_ohlc_and_timestamp(self):
        with self.assertRaises(ValueError):
            candle(dict(timestamp=60000,open=100,high=99,low=90,close=95))
        with self.assertRaises(ValueError):
            candle(dict(timestamp=60001,open=100,high=101,low=90,close=95))

    def test_process_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lock'
            with process_lock(path):
                with self.assertRaises(RuntimeError):
                    with process_lock(path):
                        pass

    def test_readonly_config(self):
        with self.assertRaises(ValueError):
            replace(Config(),mode='LIVE').validate()

    def test_feed_failure_is_explicit(self):
        with patch('n1_live.market.subprocess.run') as run:
            run.return_value.returncode=1
            with self.assertRaises(ConnectionError):
                Dukascopy(Config()).read('tick',1000,2000)

    def test_annual_reset_and_stats(self):
        t=day_bounds(date(2026,11,19))[0]
        u=day_bounds(date(2027,1,4))[0]
        trades=[dict(status='CLOSED_TP',pnl_usd=80,entry_time=t,exit_time=t+60000),
                dict(status='CLOSED_SL',pnl_usd=-50,entry_time=u,exit_time=u+120000)]
        stats=statistics(trades,[])
        self.assertEqual(stats['gross_pnl'],30)
        self.assertEqual(stats['annual_capital_reset']['2027']['capital'],950)
        self.assertEqual(stats['max_closed_trade_drawdown'],50)
        self.assertEqual(stats['average_duration_seconds'],90)

if __name__=='__main__':
    unittest.main()
