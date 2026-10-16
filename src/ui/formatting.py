"""Small text formatting helpers shared by the UI."""

import time


def days_ago(ts):
    """Whole calendar days between `ts` and today (0 = today, 1 = yesterday)."""
    then = time.localtime(ts)
    now = time.localtime()
    then_day = time.mktime((then.tm_year, then.tm_mon, then.tm_mday, 0, 0, 0, 0, 0, -1))
    today = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    return round((today - then_day) / 86400)


def fmt_when(ts):
    """'14:05' today, 'Yesterday', otherwise 'Oct 12'."""
    days = days_ago(ts)
    if days == 0:
        return time.strftime('%H:%M', time.localtime(ts))
    if days == 1:
        return 'Yesterday'
    return time.strftime('%b %d', time.localtime(ts))


def day_group(ts):
    days = days_ago(ts)
    if days == 0:
        return 'TODAY'
    if days == 1:
        return 'YESTERDAY'
    if days < 7:
        return 'THIS WEEK'
    return 'EARLIER'


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f'{seconds // 3600}h {seconds % 3600 // 60}m'
    if seconds >= 60:
        return f'{seconds // 60} min'
    return f'{seconds} s'


def fmt_clock(seconds):
    seconds = int(seconds or 0)
    return f'{seconds // 60}:{seconds % 60:02d}'


def plural(n, word):
    return f'{n:,} {word}{"" if n == 1 else "s"}'


def hotkey_parts(combo):
    """'ctrl+shift+space' -> ['Ctrl', 'Shift', 'Space']."""
    return [p.strip().capitalize() for p in (combo or '').split('+') if p.strip()]
