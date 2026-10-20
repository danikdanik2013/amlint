"""Alertmanager time intervals: read and validated the way timeinterval.go does it, and
evaluated day by day to tell when an interval is active.

An interval is a list of entries; it is active when any entry is. An entry is active when
each field it sets matches (an entry with no fields always is). Fields are lists of
ranges; an explicit empty list matches nothing.
"""

import calendar
import datetime
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

Range = Tuple[int, int]  # inclusive, except times: [start, end) in minutes of the day

WEEKDAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
            "friday": 5, "saturday": 6}
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_TIME = re.compile(r"^(([01][0-9])|(2[0-3])):[0-5][0-9]$|^24:00$")
_INT = re.compile(r"[+-]?[0-9]+")
_FIELDS = ("times", "weekdays", "days_of_month", "months", "years", "location")
DAY = 24 * 60

# How many years of days to evaluate: weekdays and dates repeat every 28 years (until 2100).
CYCLE = 28


class Entry(NamedTuple):
    times: Optional[List[Range]]
    weekdays: Optional[List[Range]]
    days_of_month: Optional[List[Range]]
    months: Optional[List[Range]]
    years: Optional[List[Range]]
    location: Optional[str]


class IntervalError(ValueError):
    def __init__(self, message: str, where: str) -> None:
        super().__init__(message)
        self.where = where  # path below the entry, such as "times[0]"


def _text(value) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    return str(value)


def _go_int(text: str) -> Optional[int]:
    return int(text) if _INT.fullmatch(text) else None


def _range(text: str, member) -> Range:
    """stringableRangeFromString: `a` or `a:b`, lower-cased, each end read by `member`."""
    text = text.lower()
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 2:
            raise ValueError(f"couldn't parse range {text}, invalid format")
        return member(parts[0]), member(parts[1])
    value = member(text)
    return value, value


def _weekday(text: str) -> int:
    if text not in WEEKDAYS:
        raise ValueError(f"{text} is not a valid weekday")
    return WEEKDAYS[text]


def _month(text: str) -> int:
    if text in MONTHS:
        return MONTHS[text]
    value = _go_int(text)
    if value is None:
        raise ValueError(f"{text} is not a valid month")
    return value


def _number(text: str) -> int:
    value = _go_int(text)
    if value is None:
        raise ValueError(f'strconv.Atoi: parsing "{text}": invalid syntax')
    return value


def _minutes(text: str) -> int:
    if not _TIME.match(text):
        raise ValueError(f"couldn't parse timestamp {text}, invalid format")
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def _list(entry: dict, key: str, parse) -> Optional[List[Range]]:
    """The ranges of one field, None when the field isn't set."""
    if key not in entry or entry[key] is None:
        return None
    items = entry[key]
    if not isinstance(items, list):
        raise IntervalError(f"{key} must be a list", key)
    out = []
    for i, item in enumerate(items):
        try:
            out.append(parse(item))
        except ValueError as e:
            raise IntervalError(str(e), f"{key}[{i}]") from None
    return out


def _times(item) -> Range:
    if not isinstance(item, dict):
        raise ValueError("times entries need start_time and end_time")
    start, end = _text(item.get("start_time")), _text(item.get("end_time"))
    if not start or not end:
        raise ValueError("both start and end times must be provided")
    begin, finish = _minutes(start), _minutes(end)
    if begin >= finish:
        raise ValueError("start time cannot be equal or greater than end time")
    return begin, finish


def _weekdays(item) -> Range:
    text = _text(item) or ""
    begin, end = _range(text, _weekday)
    if begin > end:
        raise ValueError("start day cannot be before end day")
    return begin, end


def _days(item) -> Range:
    if isinstance(item, dict):  # an AlertmanagerConfig daysOfMonth entry: {start, end}
        item = f"{item.get('start')}:{item.get('end', item.get('start'))}"
    begin, end = _range(_text(item) or "", _number)
    for day in (begin, end):
        if day == 0 or not -31 <= day <= 31:
            raise ValueError(f"{day} is not a valid day of the month: out of range")
    if begin < 0 < end:
        raise ValueError("end day must be negative if start day is negative")
    if (28 + begin if begin < 0 else begin) > (28 + end if end < 0 else end):
        raise ValueError(f"end day {end} is always before start day {begin}")
    return begin, end


def _months(item) -> Range:
    begin, end = _range(_text(item) or "", _month)
    if begin > end:
        names = {v: k for k, v in MONTHS.items()}
        raise ValueError(f"end month {names.get(end, '')} is before start month "
                         f"{names.get(begin, '')}")
    return begin, end


def _years(item) -> Range:
    begin, end = _range(_text(item) or "", _number)
    if begin > end:
        raise ValueError(f"end year {end} is before start year {begin}")
    return begin, end


def parse_entry(entry) -> Entry:
    """One entry of an interval's time_intervals: list; IntervalError if Alertmanager
    rejects it. Locations are not checked here (see location_problem)."""
    if not isinstance(entry, dict):
        raise IntervalError("an entry must be a mapping", "")
    location = entry.get("location")
    return Entry(
        times=_list(entry, "times", _times),
        weekdays=_list(entry, "weekdays", _weekdays),
        days_of_month=_list(entry, "days_of_month", _days),
        months=_list(entry, "months", _months),
        years=_list(entry, "years", _years),
        location=None if location is None else _text(location),
    )


_ZONES = None


def location_problem(name: str) -> Optional[str]:
    """Why time.LoadLocation fails for `name`, or None if it works (or amlint can't tell:
    there is no time zone database here)."""
    global _ZONES
    if name in ("", "UTC", "Local"):
        return None
    if _ZONES is None:
        try:
            import zoneinfo
            _ZONES = zoneinfo.available_timezones()
        except Exception:
            _ZONES = set()
    if not _ZONES or name in _ZONES:
        return None
    return f"unknown time zone {name}"


# ── When an entry is active ────────────────────────────────────────────

def _in(ranges: List[Range], value: int) -> bool:
    return any(begin <= value <= end for begin, end in ranges)


def _day_matches(e: Entry, day: datetime.date) -> bool:
    if e.days_of_month is not None:
        size = calendar.monthrange(day.year, day.month)[1]
        hit = False
        for begin, end in e.days_of_month:
            begin = size + begin + 1 if begin < 0 else begin
            end = size + end + 1 if end < 0 else end
            if begin > size:
                continue
            begin, end = max(-size, min(begin, size)), max(-size, min(end, size))
            if begin <= day.day <= end:
                hit = True
                break
        if not hit:
            return False
    if e.months is not None and not _in(e.months, day.month):
        return False
    # Go's Weekday: Sunday is 0
    if e.weekdays is not None and not _in(e.weekdays, (day.weekday() + 1) % 7):
        return False
    if e.years is not None and not _in(e.years, day.year):
        return False
    return True


def minutes_on(e: Entry, day: datetime.date) -> List[Range]:
    """The minutes of `day` (in the entry's location) during which the entry is active."""
    if not _day_matches(e, day):
        return []
    if e.times is None:
        return [(0, DAY)]
    return list(e.times)


def days(entries: List[Entry], today: datetime.date) -> List[datetime.date]:
    """The days to evaluate: from today for CYCLE years, or to the last year an entry
    names, at most 400 years on."""
    last = today.year + CYCLE
    for e in entries:
        for _, end in e.years or ():
            last = max(last, min(end, today.year + 400))
    count = (datetime.date(last, 12, 31) - today).days + 1
    return [today + datetime.timedelta(days=n) for n in range(max(count, 0))]


def ever_active(entries: List[Entry], today: datetime.date) -> bool:
    """Whether the interval is active at any time from today on."""
    return any(minutes_on(e, day) for day in days(entries, today) for e in entries)


def union(ranges: List[Range]) -> List[Range]:
    out: List[Range] = []
    for begin, end in sorted(ranges):
        if out and begin <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((begin, end))
    return out


def never_sends(mute: List[List[Entry]], active: List[List[Entry]],
                today: datetime.date) -> bool:
    """Whether a route muted by the `mute` intervals, and active only during the `active`
    ones (if any), has no minute left to send from today on. All entries must share one
    location: their days are compared as calendar days."""
    every = [e for interval in mute + active for e in interval]
    for day in days(every, today):
        muted = union([r for interval in mute for e in interval for r in minutes_on(e, day)])
        if active:
            open_ = union([r for interval in active for e in interval
                           for r in minutes_on(e, day)])
        else:
            open_ = [(0, DAY)]
        if _minus(open_, muted):
            return False
    return True


def _minus(ranges: List[Range], cut: List[Range]) -> bool:
    """Whether any minute of `ranges` is outside `cut` (both unions)."""
    for begin, end in ranges:
        at = begin
        for c_begin, c_end in cut:
            if c_end <= at or c_begin >= end:
                continue
            if c_begin > at:
                return True
            at = max(at, c_end)
            if at >= end:
                break
        if at < end:
            return True
    return False


def always_active(entries: List[Entry]) -> bool:
    """Whether an interval is active at every minute, wherever it is evaluated."""
    for e in entries:
        if all(getattr(e, f) is None for f in ("weekdays", "days_of_month", "months", "years")) \
                and (e.times is None or union(e.times) == [(0, DAY)]):
            return True
    return False


def same_location(intervals: Dict[str, List[Entry]]) -> bool:
    places = {e.location or "UTC" for entries in intervals.values() for e in entries}
    return len(places) <= 1
