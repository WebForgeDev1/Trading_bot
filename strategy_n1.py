
from dataclasses import dataclass
from datetime import date
from math import isfinite


@dataclass(frozen=True)
class StrategyN1:
    min_range: float = 25.0
    bottom_threshold: float = 0.06
    sl_distance: float = 5.0
    tp_distance: float = 8.0
    risk_usd: float = 50.0
    initial_capital: float = 1000.0

    def check_signal(
        self,
        day: date,
        high: float,
        low: float,
        close: float,
        position_open: bool = False,
    ) -> bool:

        # Aucun signal le vendredi
        if day.weekday() == 4:
            return False

        # Une position maximum
        if position_open:
            return False

        # Refuser les prix invalides
        if not all(
            isfinite(x) for x in (high, low, close)
        ):
            return False

        if not low <= close <= high:
            return False

        daily_range = high - low

        if daily_range < self.min_range:
            return False

        close_ratio = (close - low) / daily_range

        return close_ratio <= self.bottom_threshold

    def calculate_levels(self, entry_price: float):
        if not isfinite(entry_price) or entry_price <= 0:
            raise ValueError("Prix d'entrée invalide")

        return {
            "entry": entry_price,
            "sl": entry_price - self.sl_distance,
            "tp": entry_price + self.tp_distance,
        }
