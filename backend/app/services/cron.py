"""
Five-field cron matching (minute hour day-of-month month day-of-week) for schedules.
Supports *, lists (a,b), ranges (a-b), steps (*/n, a-b/n). Day-of-week 0 and 7 are Sunday.
When both day fields are restricted, a time matches if either matches, as in standard cron.
"""

from datetime import datetime
from typing import List, Set

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _field(spec: str, low: int, high: int) -> Set[int]:
    values: Set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step < 1:
                raise ValueError("step must be at least 1")
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start < low or end > high or start > end:
            raise ValueError(f"value out of range in '{spec}'")
        values.update(range(start, end + 1, step))
    return values


def parse(expression: str) -> List[Set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("cron expression needs five fields: minute hour day month weekday")
    fields = [_field(spec, low, high) for spec, (low, high) in zip(parts, RANGES)]
    if 7 in fields[4]:
        fields[4].discard(7)
        fields[4].add(0)
    return fields


def matches(expression: str, moment: datetime) -> bool:
    minute, hour, dom, month, dow = parse(expression)
    if moment.minute not in minute or moment.hour not in hour or moment.month not in month:
        return False
    day_ok = moment.day in dom
    weekday_ok = (moment.isoweekday() % 7) in dow
    dom_restricted = expression.split()[2] != "*"
    dow_restricted = expression.split()[4] != "*"
    if dom_restricted and dow_restricted:
        return day_ok or weekday_ok
    return day_ok and weekday_ok
