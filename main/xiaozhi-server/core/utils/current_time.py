"""
Time utility module
Provides unified access to the time
"""

from datetime import datetime

WEEKDAY_MAP = {
    d: d
    for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
}


def get_current_time() -> str:
    """
    Get the current time string (format: HH:MM)
    """
    return datetime.now().strftime("%H:%M")


def get_current_date() -> str:
    """
    Get today's date string (format: YYYY-MM-DD)
    """
    return datetime.now().strftime("%Y-%m-%d")


def get_current_weekday() -> str:
    """
    Get today's day of the week
    """
    now = datetime.now()
    return WEEKDAY_MAP[now.strftime("%A")]


def get_current_time_info() -> tuple:
    """
    Get the current time information
    Returns: (current time string, today's date, today's weekday)
    """
    current_time = get_current_time()
    today_date = get_current_date()
    today_weekday = get_current_weekday()
    
    return current_time, today_date, today_weekday
