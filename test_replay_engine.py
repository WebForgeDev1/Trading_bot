import unittest
import pandas as pd
from replay_engine import run_replay

class ReplayTests(unittest.TestCase):
    def prices(self, exit_bar):
        index = pd.date_range('2026-11-19', periods=1000, freq='min', tz='Europe/Paris')
        prices = pd.DataFrame({'open': 100., 'high': 125., 'low': 100., 'close': 101.}, index=index)
        prices.loc[pd.Timestamp('2026-11-20', tz='Europe/Paris')] = exit_bar
        return prices

    def test_next_day_entry_and_ambiguous_stop(self):
        result = run_replay(self.prices([101., 110., 95., 101.]))
        trade = result['trades'].iloc[0]
        self.assertEqual(str(trade.entry_time.date()), '2026-11-20')
        self.assertEqual(trade.outcome, 'SL_AMBIGUOUS')
        self.assertEqual(trade.pnl_usd, -50.)
        self.assertIsNone(result['position'])

    def test_open_position_is_reported(self):
        result = run_replay(self.prices([101., 102., 100., 101.]))
        self.assertTrue(result['trades'].empty)
        self.assertIsNotNone(result['position'])

    def test_empty_input(self):
        with self.assertRaises(ValueError):
            run_replay(pd.DataFrame())

if __name__ == '__main__':
    unittest.main()
