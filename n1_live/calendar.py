from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

UTC = timezone.utc
PARIS = ZoneInfo('Europe/Paris')
NEW_YORK = ZoneInfo('America/New_York')


def dt(ms):
    return datetime.fromtimestamp(ms / 1000, UTC)


def ms(value):
    return int(value.timestamp() * 1000)


def day_bounds(day):
    start = datetime.combine(day, time(), PARIS)
    end = datetime.combine(day + timedelta(days=1), time(), PARIS)
    return ms(start), ms(end)


class Calendar:
    """Dukascopy XAU: Sun 18:00 to Fri 17:00 New York, daily 17–18 break.

    Exceptional holidays are supplied explicitly. Unconfigured missing minutes
    invalidate a day; they are never silently treated as market closure.
    """
    def __init__(self, closures=()):
        self.closures = []
        for start, end in closures:
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if a.tzinfo is None or b.tzinfo is None or a >= b:
                raise ValueError('Fermeture exceptionnelle invalide : UTC explicite requis')
            self.closures.append((ms(a), ms(b)))

    def is_open(self, timestamp):
        if any(a <= timestamp < b for a, b in self.closures):
            return False
        local = dt(timestamp).astimezone(NEW_YORK)
        weekday, hour = local.weekday(), local.hour
        if weekday == 5 or (weekday == 6 and hour < 18) or (weekday == 4 and hour >= 17):
            return False
        return not 17 <= hour < 18

    def expected_minutes(self, day):
        a, b = day_bounds(day)
        return {t for t in range(a, b, 60000) if self.is_open(t)}

    def missing_open_time(self, start, end, tolerance_seconds):
        # Count scheduled open minutes, excluding daily/weekend closures.
        if end - start <= tolerance_seconds * 1000:
            return False
        count = 0
        for t in range((start // 60000 + 1) * 60000, end, 60000):
            if self.is_open(t):
                count += 60
                if count > tolerance_seconds:
                    return True
        return False
