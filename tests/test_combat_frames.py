"""CT2 — real threat frames → the answer the fight chooses, against answers set by hand.

The frames are what the game gave the threat layer in the bench's combat arena (tests/data/combat_frames.json,
recorded 2026-09-17): the body, what was coming at it, the ground. The expected answer is written here by hand from
what a player would do — not copied from what the model said then — so a row goes red when the model disagrees with
play, including the rows where the only sane answer is to give up the fight and leave.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import field, threat  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "combat_frames.json")
FRAMES = json.load(open(DATA))["frames"]


def frame(name, hp=None, hazards=None, away=None, more=0):
    """A recorded threat state, with the one thing a row changes: health, what is coming, how far it is, or `more`
    copies of the recorded mob beside it (the arena's pack frames recorded only the nearest of the three)."""
    rec = dict(FRAMES[name]["state"])
    rows = [tuple(tuple(v) if isinstance(v, list) else v for v in r) for r in rec["hazards"]]
    for i in range(more):
        p, *rest = rows[0]
        rows.append(((p[0] + 1.5 * (i + 1), p[1], p[2] - 1.5 * (i + 1)), *rest))
    if hazards is not None:
        rows = hazards
    if away is not None:
        here = tuple(rec["here"])
        rows = [((here[0] + away + i, p[1], here[2]), *rest) for i, (p, *rest) in enumerate(rows)]
    st = dict(rec, here=tuple(rec["here"]), hazards=rows, ids=list(range(len(rows))),
              field=field.Field(bucket=rec["field"]["bucket"], blocks=rec["field"]["blocks"]))
    if hp is not None:
        st["hp"] = hp
    return st


def price(st):
    s = threat.price_state(hp=max(1, int(st["hp"])), sword=st["sword"], food_items=st["food_items"],
                           shield=st["shield"])
    return lambda dhp: threat.hp_seconds(s, dhp)


# (situation, the state) → the answer, set by hand
ROWS = [
    ("must fail: nothing coming: carry on", lambda: frame("walker", hazards=[]), "ignore"),
    ("one zombie close, full health: kill it", lambda: frame("walker"), "fight"),
    ("one zombie 24 blocks off: not worth stopping for", lambda: frame("walker", away=24), "ignore"),
    ("a skeleton close, full health: close in and kill it", lambda: frame("archer"), "fight"),
    ("a creeper close, a sword: hit it and keep out of its blast", lambda: frame("bomb"), "fight"),
    ("a creeper close at 2 hp: get out, no question", lambda: frame("bomb", hp=2), "evade"),
    # bench/combat.jsonl pack run 792679999: 20 hp lost in 11.8 s at 6.88 hp/s, the hurt-immunity cap; priced 19.29 > 19
    ("three zombies, full health, an iron sword, chest and helmet only: leave", lambda: frame("pack", more=2), "evade"),
    ("three zombies at 4 hp: give it up and leave", lambda: frame("pack", hp=4, more=2), "evade"),
    ("the nearest of a mixed group, full health: fight", lambda: frame("mixed"), "fight"),
    ("zombies close at 3 hp: give it up and leave", lambda: frame("mixed", hp=3, more=2), "evade"),
]


class Frames(unittest.TestCase):
    def test_answers(self):
        for name, make, want in ROWS:
            with self.subTest(name):
                st = make()
                self.assertEqual(threat.decide(st, price(st)).kind, want)

    def test_every_recorded_frame_is_used(self):
        used = {"walker", "archer", "bomb", "pack", "mixed"}
        self.assertEqual({k for k, v in FRAMES.items() if v["state"]} - used, set())


if __name__ == "__main__":
    unittest.main()
