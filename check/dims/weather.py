"""A thunderstorm (/state thundering): a bed works by day in one (survive.can_sleep)."""
NAME = "weather"
VALUES = ("clear", "thunder")


def domain():
    return VALUES


def alpha(a):
    return "thunder" if a.snap.state.get("thundering") else "clear"


def gamma(value, f, g):
    if value == "thunder":
        g.state["thundering"] = True
