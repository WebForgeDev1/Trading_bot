import ast
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from n1_live.config import Config
from n1_live.market import Tick, Dukascopy
from n1_live.execution import open_paper, close_paper
from n1_live.analytics import statistics
from n1_live.storage import Store, restore_database
from n1_live.control import emergency_stop, halted, clear_emergency, EmergencyStop
from n1_live.engine import Engine
from n1_live.app import show, main, run
from n1_live.mt5_readonly import read_quotes, MT5ReadOnlyProvider
from n1_live.calendar import Calendar, day_bounds


def bars(day):
    return [dict(timestamp=t,open=3501.,high=3525.,low=3500.,close=3501.) for t in sorted(Calendar().expected_minutes(day))]


def quote(ts,bid=3501,ask=3501.4):
    return dict(timestamp=ts,bidPrice=bid,askPrice=ask)


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=replace(Config(),database=str(self.root/'paper.db'))
        self.store=Store(self.config.db_path);self.addCleanup(self.store.close)
        self.engine=Engine(self.store,self.config)
        self.day=date(2026,11,19);self.end=day_bounds(self.day)[1]
        self.engine.cycle([],bars(self.day),self.end-1)

    def test_buy_ask_and_tp_bid_cycle(self):
        state,_=self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        p=state['position']
        self.assertAlmostEqual(p['entry'],3501.4)
        self.assertAlmostEqual(p['sl'],3496.4)
        self.assertAlmostEqual(p['tp'],3509.4)
        state,_=self.engine.cycle([quote(self.end+3000,3509.3,3510)],[],self.end+4000)
        self.assertIsNotNone(state['position']) # ASK hitting TP does not close BUY.
        state,_=self.engine.cycle([quote(self.end+5000,3509.4,3510)],[],self.end+6000)
        self.assertIsNone(state['position'])
        trade=self.store.records('trades')[0]
        self.assertEqual(trade['pnl_usd'],80)
        self.assertIsNone(trade['estimated_net_pnl_usd'])
        self.assertEqual(trade['entry_spread_cost_usd'],4)

    def test_buy_ask_and_stop_bid(self):
        self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        self.engine.cycle([quote(self.end+3000,3496.4,3497)],[],self.end+4000)
        self.assertEqual(self.store.records('trades')[0]['pnl_usd'],-50)

    def test_known_costs_and_no_double_spread(self):
        config=replace(self.config,commission_per_unit_per_side_usd=.05,financing_per_unit_per_day_usd=.2)
        opened=open_paper(str(self.day),Tick.parse(quote(self.end)),config)
        closed=close_paper(opened,Tick.parse(quote(self.end+86400000,3509.4,3510)),'TP',False)
        self.assertEqual(closed['commission_usd'],1)
        self.assertEqual(closed['financing_usd'],2)
        self.assertEqual(closed['estimated_net_pnl_usd'],77)
        self.assertEqual(statistics([closed],[])['estimated_net_pnl'],77)

    def test_partial_costs_are_not_presented_as_net(self):
        config=replace(self.config,commission_per_unit_per_side_usd=.05)
        opened=open_paper(str(self.day),Tick.parse(quote(self.end)),config)
        closed=close_paper(opened,Tick.parse(quote(self.end+5000,3509.4,3510)),'TP',False)
        self.assertEqual(closed['known_costs_usd'],1)
        self.assertIsNone(closed['estimated_net_pnl_usd'])
        self.assertIsNone(statistics([closed],[])['estimated_net_pnl'])

    def test_legacy_bid_identity_migrates_without_changing_open_position(self):
        additions={'execution_model','commission_per_unit_per_side_usd','financing_per_unit_per_day_usd','max_spread_usd','quote_source','mt5_csv_path','mt5_symbol'}
        with self.store.transaction():
            state=self.store.state()
            for key in additions:state['identity']['config'].pop(key)
            position=dict(id='old',entry=3501.,sl=3496.,tp=3509.,exposure=10,status='OPEN',entry_time=self.end-1000,price_side='BID')
            state['position']=position
            self.store.save(state)
        state,_=self.engine.cycle([],[],self.end-1)
        self.assertEqual(state['position'],position)
        self.assertEqual(state['identity']['config']['execution_model'],'ASK_BID')

    def test_cost_update_does_not_reprice_existing_position(self):
        self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        config=replace(self.config,commission_per_unit_per_side_usd=.1,financing_per_unit_per_day_usd=0.)
        engine=Engine(self.store,config)
        state,_=engine.cycle([],[],self.end+2000)
        self.assertIsNone(state['position']['commission_per_unit_per_side_usd'])
        engine.cycle([quote(self.end+3000,3509.4,3510)],[],self.end+4000)
        self.assertIsNone(self.store.records('trades')[0]['estimated_net_pnl_usd'])

    def test_spread_limit_rejects_without_delayed_entry(self):
        # Separate base: an execution constraint change is never silent.
        config=replace(self.config,database=str(self.root/'spread.db'),max_spread_usd=.2)
        store=Store(config.db_path)
        try:
            engine=Engine(store,config);engine.cycle([],bars(self.day),self.end-1)
            state,_=engine.cycle([quote(self.end+1000)],[],self.end+2000)
            self.assertIsNone(state['position'])
            self.assertEqual(store.records('signals')[0]['reason'],'SPREAD_LIMIT')
        finally:store.close()

    def test_emergency_latch_survives_new_engine(self):
        emergency_stop(self.config.db_path)
        with self.assertRaises(EmergencyStop):self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        with self.assertRaises(EmergencyStop):Engine(self.store,self.config).cycle([],[],self.end+3000)
        self.assertEqual(self.store.records('trades'),[])
        clear_emergency(self.config.db_path)
        self.assertFalse(halted(self.config.db_path))

    def test_emergency_during_entry_rolls_back_transaction(self):
        with patch('n1_live.engine.halted',side_effect=[False,False,True]):
            with self.assertRaises(EmergencyStop):self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        self.assertEqual(self.store.records('trades'),[])
        self.assertEqual(self.store.records('signals'),[])

    def test_emergency_preserves_open_position(self):
        state,_=self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        position=state['position']
        emergency_stop(self.config.db_path)
        with self.assertRaises(EmergencyStop):self.engine.cycle([quote(self.end+3000,3520,3521)],[],self.end+4000)
        self.assertEqual(self.store.state()['position'],position)

    def test_emergency_cli_and_explicit_resume(self):
        configfile=self.root/'config.json'
        from dataclasses import asdict
        configfile.write_text(json.dumps(asdict(self.config)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config',str(configfile),'emergency-stop']),0)
            self.assertTrue(halted(self.config.db_path))
            self.assertEqual(main(['--config',str(configfile),'resume']),0)
            self.assertFalse(halted(self.config.db_path))

    def test_network_request_cancelled(self):
        provider=Dukascopy(self.config);provider.cancel_check=lambda:True
        process=MagicMock();process.poll.return_value=None;process.communicate.return_value=('','')
        with patch('n1_live.market.subprocess.Popen',return_value=process):
            with self.assertRaises(InterruptedError):provider.read('tick',1000,2000)
        process.terminate.assert_called_once()

    def test_backup_restore_pending_and_deduplication(self):
        self.engine.cycle([],[],self.end+1000)
        backup=self.store.backup(self.root/'copy.sqlite3')
        restored=self.root/'restored.sqlite3';restore_database(backup,restored)
        store=Store(restored)
        try:
            engine=Engine(store,replace(self.config,database=str(restored)))
            self.assertIsNotNone(store.state()['pending'])
            engine.cycle([quote(self.end+2000)],[],self.end+3000)
            engine.cycle([quote(self.end+2000)],[],self.end+4000)
            self.assertEqual(len(store.records('trades')),1)
            self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:store.close()

    def test_backup_does_not_overwrite_existing_file(self):
        target=self.root/'copy.sqlite3';target.write_text('original')
        with self.assertRaises(ValueError):self.store.backup(target)
        self.assertEqual(target.read_text(),'original')

    def test_dashboard_prices_position_costs(self):
        state,_=self.engine.cycle([quote(self.end+1000)],[],self.end+2000)
        out=io.StringIO()
        with contextlib.redirect_stdout(out):show(state,self.config,self.store)
        for label in ('BID','ASK','spread','SL','TP','Dernier signal','Résultat net estimé','inconnus'):
            self.assertIn(label,out.getvalue())

    def test_mt5_export_validation(self):
        file=self.root/'quotes.csv';file.write_text('timestamp,symbol,bid,ask\n1000,XAUUSD,3500,3501\n2000,XAUUSD,3501,3502\n')
        rows=read_quotes(file,'XAUUSD',1000,2000)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source'],'mt5-readonly')
        with self.assertRaises(ValueError):read_quotes(file,'GOLD',0,3000)
        with self.assertRaises(ConnectionError):read_quotes(self.root/'absent.csv','XAUUSD')

    def test_mt5_missing_ask_and_out_of_order(self):
        file=self.root/'quotes.csv'
        file.write_text('timestamp,symbol,bid,ask\n2000,XAUUSD,3501,3502\n1000,XAUUSD,3500,3501\n')
        with self.assertRaises(ValueError):read_quotes(file,'XAUUSD',0,3000)
        file.write_text('timestamp,symbol,bid,ask\n1000,XAUUSD,3501,0\n')
        with self.assertRaises(ValueError):read_quotes(file,'XAUUSD',0,3000)

    def test_mt5_readonly_can_supply_paper_quotes(self):
        file=self.root/'quotes.csv';file.write_text(f'timestamp,symbol,bid,ask\n{self.end+1000},XAUUSD,3501,3501.4\n')
        config=replace(self.config,database=str(self.root/'mt5.db'),quote_source='mt5_csv',mt5_csv_path=str(file))
        store=Store(config.db_path)
        try:
            engine=Engine(store,config);engine.cycle([],bars(self.day),self.end-1)
            provider=MT5ReadOnlyProvider(config)
            state,_=engine.cycle(provider.read('tick',self.end,self.end+2000),[],self.end+3000)
            self.assertEqual(state['position']['source'],'mt5-readonly')
            self.assertEqual(state['position']['entry'],3501.4)
            with self.assertRaises(ValueError):engine.cycle([quote(self.end+4000)],[],self.end+5000)
        finally:store.close()

    def test_no_order_api_calls_in_active_code(self):
        root=Path(__file__).resolve().parents[1]
        banned={'order_send','order_check','OrderSend','OrderSendAsync','CTrade','login'}
        for file in (root/'n1_live').glob('*.py'):
            tree=ast.parse(file.read_text())
            called={node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else '' for node in ast.walk(tree) if isinstance(node,ast.Call)}
            self.assertFalse(called & banned,file.name)
        bridge=(root/'mt5_readonly/XAUUSD_ReadOnly.mq5').read_text()
        for name in banned:self.assertNotIn(name+'(',bridge)

if __name__=='__main__':unittest.main()
