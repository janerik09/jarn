from datetime import datetime, time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo


def _parse(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        hours, minutes = value.strip().split(":")[:2]
        return time(int(hours), int(minutes))
    except ValueError:
        return None


def window_status(settings_row, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Whether now falls inside the maintenance window. No window configured means no restriction."""
    start = _parse(getattr(settings_row, "maintenance_window_start", None))
    end = _parse(getattr(settings_row, "maintenance_window_end", None))
    zone_name = ((getattr(settings_row, "general_config", None) or {}).get("timezone")) or "UTC"
    zone = ZoneInfo(zone_name)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if start is None or end is None:
        return {"configured": False, "inside": True, "timezone": zone_name, "window": None}
    current = local_now.time()
    if start <= end:
        inside = start <= current < end
    else:
        inside = current >= start or current < end
    return {
        "configured": True,
        "inside": inside,
        "timezone": zone_name,
        "window": f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')} {zone_name}",
        "now": local_now.strftime("%H:%M %Z"),
    }
