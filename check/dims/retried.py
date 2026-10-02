"""What the retry ledger holds from earlier rounds (brain.retry): nothing; a task replanned REPLAN_LIMIT − 1 times in a
row with nothing done (one more is a failure: retry.Retry.failed's replan count); an explicit hold not run out (the
throttle a crash or a deposit sets: Retry.hold)."""
import time

from bonobo.retry import REPLAN_LIMIT

NAME = "retried"
VALUES = ("none", "replans", "held")
TASK = "task t1"                    # the brain's key for the first task
HELD = "empty the bag"              # a throttled row (a deposit's hold): the task's own cooling is the task dimension's
HOLD_S = 300.0


def domain():
    return VALUES


def prepare(brain, f):
    now = time.time()
    if f[NAME] == "replans":
        for _ in range(REPLAN_LIMIT - 1):
            brain.retry.failed(TASK, "replan", "check: replanned", now)
    elif f[NAME] == "held":
        brain.retry.hold(HELD, HOLD_S, now)


def alpha(a):
    r = a.brain.retry
    if r.holds.get(HELD, 0) > time.time():
        return "held"
    return "replans" if (TASK, "replan") in r.entries else "none"


def gamma(value, f, g):
    return None                      # the brain's own ledger (prepare), not the world
