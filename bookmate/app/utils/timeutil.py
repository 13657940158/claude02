"""时间工具：业务日期按 APP_TIMEZONE 计算，存储统一 UTC naive。"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo

from app.core.config import get_settings


def app_tz() -> tzinfo:
    name = get_settings().app_timezone or "Asia/Shanghai"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - Windows 缺 tzdata 时退化固定 +8
        return timezone(timedelta(hours=8), "UTC+8")


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def today_local() -> date:
    return datetime.now(app_tz()).date()


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return aware.astimezone(app_tz())


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return aware.isoformat().replace("+00:00", "Z")


def day_start_utc(day: date) -> datetime:
    local = datetime.combine(day, time.min, tzinfo=app_tz())
    return local.astimezone(UTC).replace(tzinfo=None)


def date_diff_days(later: date, earlier: date) -> int:
    return (later - earlier).days


def human_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小时{(' ' + str(rest) + ' 分钟') if rest else ''}"


def weekday_cn(day: date) -> str:
    return "周" + "一二三四五六日"[day.weekday()]
