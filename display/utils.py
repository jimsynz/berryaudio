from datetime import datetime

from PIL import ImageColor


def power_state_name(state):
    if state == "standby":
        return "Standby"
    if state == "shutdown":
        return "Power Off"
    if state == "shutdown":
        return "Power Off"
    if state == "reboot":
        return "Restarting..."


def format_time(timestamp):
    if not timestamp:
        return "--:-- --"

    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"

    dt = datetime.fromisoformat(timestamp)
    am_pm = "AM" if dt.hour < 12 else "PM"
    hour = dt.hour % 12
    if hour == 0:
        hour = 12
    return f"{hour:02d}:{dt.minute:02d} {am_pm}"


def parse_colour(colour):
    if isinstance(colour, tuple):
        return colour
    return ImageColor.getrgb(colour)


def scale_colour(colour, factor):
    r, g, b = parse_colour(colour)
    return (
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )


def mix_colour(colour, other, amount):
    r, g, b = parse_colour(colour)
    _r, _g, _b = parse_colour(other)
    return (
        int(r + (_r - r) * amount),
        int(g + (_g - g) * amount),
        int(b + (_b - b) * amount),
    )
