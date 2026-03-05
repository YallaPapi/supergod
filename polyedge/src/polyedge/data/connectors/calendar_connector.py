"""Calendar and date-based features -- pure Python, no API calls."""
import calendar
from datetime import date, timedelta

from polyedge.data.base_connector import BaseConnector
from polyedge.data.registry import register

# Major US federal holidays (fixed-date and rule-based)
_FIXED_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (7, 4): "Independence Day",
    (12, 25): "Christmas Day",
}


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """Return the nth occurrence of weekday in month/year. weekday: 0=Mon."""
    first = date(year, month, 1)
    first_weekday = first.weekday()
    offset = (weekday - first_weekday) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in month/year."""
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _us_holidays(year: int) -> list[date]:
    """Return list of US federal holiday dates for a given year."""
    holidays = []
    # Fixed-date holidays
    for (m, d), _ in _FIXED_HOLIDAYS.items():
        holidays.append(date(year, m, d))
    # MLK Day: 3rd Monday of January
    holidays.append(_nth_weekday(year, 1, 3, 0))
    # Presidents Day: 3rd Monday of February
    holidays.append(_nth_weekday(year, 2, 3, 0))
    # Memorial Day: last Monday of May
    holidays.append(_last_weekday(year, 5, 0))
    # Labor Day: 1st Monday of September
    holidays.append(_nth_weekday(year, 9, 1, 0))
    # Thanksgiving: 4th Thursday of November
    holidays.append(_nth_weekday(year, 11, 4, 3))
    return sorted(holidays)


def _days_until_next_holiday(dt: date) -> int:
    """Days until the next US holiday from dt (inclusive of today)."""
    holidays = _us_holidays(dt.year) + _us_holidays(dt.year + 1)
    for h in holidays:
        if h >= dt:
            return (h - dt).days
    return 365  # fallback


@register
class CalendarConnector(BaseConnector):
    source = "calendar"
    category = "temporal"

    def fetch_date(self, dt: date) -> list[tuple[str, float]]:
        _, days_in_month = calendar.monthrange(dt.year, dt.month)
        is_month_end = dt.day == days_in_month
        is_quarter_end = dt.month in (3, 6, 9, 12) and is_month_end
        is_year_end = dt.month == 12 and dt.day == 31

        holidays = _us_holidays(dt.year)
        is_holiday = 1.0 if dt in holidays else 0.0

        return [
            ("day_of_week", float(dt.weekday())),
            ("day_of_month", float(dt.day)),
            ("month", float(dt.month)),
            ("week_of_year", float(dt.isocalendar()[1])),
            ("is_weekend", 1.0 if dt.weekday() >= 5 else 0.0),
            ("is_month_start", 1.0 if dt.day == 1 else 0.0),
            ("is_month_end", 1.0 if is_month_end else 0.0),
            ("is_quarter_end", 1.0 if is_quarter_end else 0.0),
            ("is_year_end", 1.0 if is_year_end else 0.0),
            ("quarter", float((dt.month - 1) // 3 + 1)),
            ("days_in_month", float(days_in_month)),
            ("is_leap_year", 1.0 if calendar.isleap(dt.year) else 0.0),
            ("is_us_holiday", is_holiday),
            ("days_until_next_holiday", float(_days_until_next_holiday(dt))),
        ]
