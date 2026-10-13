"""When a sign-in session has to end.

A session is ended after too long without a renewal (idle), or too long after sign-in (absolute). The browser renews
its access token when the user makes a request, so a renewal is a good measure of activity.
"""

from datetime import datetime, timedelta
from typing import Optional

IDLE_REASON = "idle timeout"
ABSOLUTE_REASON = "session limit"


def session_timeout(signed_in_at: datetime, last_used_at: datetime, now: datetime,
                    idle_minutes: int, max_hours: int) -> Optional[str]:
    """The reason the session must end, or None while it is still allowed."""
    if now - signed_in_at > timedelta(hours=max_hours):
        return ABSOLUTE_REASON
    if now - last_used_at > timedelta(minutes=idle_minutes):
        return IDLE_REASON
    return None
