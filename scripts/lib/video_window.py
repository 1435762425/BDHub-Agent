"""Calendar eligibility for current B leads, using the existing two-day data lag."""
from datetime import date,datetime,timedelta,timezone

BEIJING=timezone(timedelta(hours=8))
WINDOW_DAYS=30
DATA_LAG_DAYS=2


def bounds(now):
    end=datetime.fromtimestamp(now,BEIJING).date()-timedelta(days=DATA_LAG_DAYS)
    return end-timedelta(days=WINDOW_DAYS-1),end


def current(released_at,now):
    try:
        released=date.fromisoformat(str(released_at))
    except (TypeError,ValueError):
        return False
    start,end=bounds(now)
    return start<=released<=end
