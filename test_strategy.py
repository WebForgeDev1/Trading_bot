
import unittest
from datetime import date
from strategy_n1 import StrategyN1


class TestStrategyN1(unittest.TestCase):

    def setUp(self):
        self.bot = StrategyN1()

    def test_valid_buy(self):
        self.assertTrue(
            self.bot.check_signal(
                date(2026, 11, 19),
                high=4100,
                low=4050,
                close=4052
            )
        )

    def test_no_friday_signal(self):
        self.assertFalse(
            self.bot.check_signal(
                date(2026, 11, 20),
                high=4100,
                low=4050,
                close=4052
            )
        )

    def test_range_too_small(self):
        self.assertFalse(
            self.bot.check_signal(
                date(2026, 11, 19),
                high=4070,
                low=4050,
                close=4051
            )
        )

    def test_existing_position(self):
        self.assertFalse(
            self.bot.check_signal(
                date(2026, 11, 19),
                high=4100,
                low=4050,
                close=4052,
                position_open=True
            )
        )

    def test_sl_tp(self):
        levels = self.bot.calculate_levels(4055)

        self.assertEqual(levels["sl"], 4050)
        self.assertEqual(levels["tp"], 4063)


if __name__ == "__main__":
    unittest.main()
