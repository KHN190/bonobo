"""Incidents: captured planner states from live failures, replayed offline, adopted into the test library. mc.py incidents               list captured incidents in the data directory with the planner's current answer mc.py incidents adopt NAME    copy one into tests/incidents/ (edit `expect` afterwards) The bench writes a capture whenever a combat scenario dies; nothing here talks to the game."""

import json
import os
import shutil
import sys
import traceback

from .. import fight_plan as fp, paths

DIR = paths.data("incidents")
LIBRARY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "tests", "incidents")


def thaw(state):
    """JSON has lists where the planner wants tuples."""
    s = json.loads(json.dumps(state))
    s["self"]["pos"] = tuple(s["self"]["pos"])
    if s["self"].get("cover") is not None:
        s["self"]["cover"] = tuple(s["self"]["cover"])
    s["threats"] = [(tuple(t[0]), t[1], tuple(t[2]), t[3]) for t in s["threats"]]
    return s


def replay(path):
    inc = json.load(open(path))
    plan = fp.Fight().plan(thaw(inc["state"]))
    return inc, plan

def main():
    args = sys.argv[1:]
    if args[:1] == ["adopt"]:
        src = args[1] if os.path.isabs(args[1]) else os.path.join(DIR, args[1])
        os.makedirs(LIBRARY, exist_ok=True)
        dst = os.path.join(LIBRARY, os.path.basename(src))
        shutil.copy2(src, dst)
        print(f"adopted → {dst}  (now add an `expect` block)")
        return 0
    try:
        names = sorted(n for n in os.listdir(DIR) if n.endswith(".json"))
    except OSError:
        names = []
    if not names:
        print(f"no incidents in {DIR}")
        return 0
    for n in names:
        try:
            inc, plan = replay(os.path.join(DIR, n))
            print(f"{n}\n   then: {(inc.get('intent') or {}).get('intent')}   now: {plan['intent']}   "
                  f"fault: {plan['fault'] or '-'}\n   {inc.get('note', '')[:110]}")
        except Exception as e:  # guard: one unreplayable incident file must not stop the listing of the rest
            print(f"{n}\n   unreplayable: {e}\n" + traceback.format_exc(limit=3))
    return 0
