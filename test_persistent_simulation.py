import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from persistent_simulation import simulate
from replay_engine import run_replay

class PersistentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)/'state.json'
        self.output = Path(self.tmp.name)/'trades.csv'
        index = pd.date_range('2026-11-19', periods=1000, freq='min', tz='Europe/Paris')
        self.prices = pd.DataFrame(dict(open=101., high=125., low=100., close=101.), index=index)
        self.prices.loc[pd.Timestamp('2026-11-20', tz='Europe/Paris')] = [101.,102.,100.,101.]
        self.prices.loc[pd.Timestamp('2026-11-20 00:01', tz='Europe/Paris')] = [101.,110.,95.,101.]

    def run_sim(self, prices):
        return simulate(prices, self.state, self.output, 'fixture')

    def test_resume_open_position_and_no_duplicates(self):
        first, _ = self.run_sim(self.prices.iloc[:-1])
        self.assertIsNotNone(first['position'])
        self.assertEqual(len(first['signals']), 1)
        result, count = self.run_sim(self.prices)
        self.assertEqual(count, 1)
        self.assertEqual(len(result['trades']), 1)
        self.assertEqual(result['trades'][0]['outcome'], 'SL_AMBIGUOUS')
        again, count = self.run_sim(self.prices)
        self.assertEqual(count, 0)
        self.assertEqual(result, again)
        self.assertEqual(result['trades'][0]['pnl_usd'], run_replay(self.prices)['trades'].iloc[0].pnl_usd)

    def test_last_day_not_finalized_early(self):
        result, _ = self.run_sim(self.prices.iloc[:1000])
        self.assertEqual(result['signals'], [])
        result, _ = self.run_sim(self.prices)
        self.assertEqual(len(result['signals']), 1)

    def test_history_change_rejected_without_state_change(self):
        self.run_sim(self.prices)
        saved = self.state.read_bytes()
        revised = self.prices.copy()
        revised.iloc[0, 0] = 102.
        with self.assertRaises(ValueError):
            self.run_sim(revised)
        self.assertEqual(saved, self.state.read_bytes())

    def test_failed_commit_can_retry(self):
        with patch('persistent_simulation.os.replace', side_effect=OSError('interruption')):
            with self.assertRaises(OSError):
                self.run_sim(self.prices)
        self.assertFalse(self.state.exists())
        result, _ = self.run_sim(self.prices)
        self.assertEqual(len(result['trades']), 1)

    def test_missing_export_recreated(self):
        self.run_sim(self.prices)
        self.output.unlink()
        _, count = self.run_sim(self.prices)
        self.assertEqual(count, 0)
        self.assertEqual(len(pd.read_csv(self.output)), 1)

if __name__ == '__main__':
    unittest.main()
