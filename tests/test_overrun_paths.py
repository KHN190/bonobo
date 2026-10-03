"""The overrun rule (data.OVERRUN) through the production path, each kind of step: a planned step run by
dispatch.run_priced is stopped once it runs past its budget — a dug way before it is dug, a chase before its next
leg, a seek before its next look. Imports only what the base (before the rule) has too, so each row is red there by
its assertion (the whole way sent, every chase and leg run), not by an ImportError; the rule's own API rows are in
test_way_overrun."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402

SEGMENT = 6                       # api.run_chain's own segment size: its before_segment runs once per part


class AMineStepWhoseWayRunsLong(unittest.TestCase):
    """The production path (dispatch.run_priced → gather._go_way → nav): an ore 67 off and 10 down
    through stone, the step priced 61 s. Its way (~200 s of digging) is past 1.5× the price: refused before a block is
    dug, a McError for the round (must fail: the whole way sent, the ~138 s tunnel)."""

    def test_the_way_past_the_price_is_never_dug(self):
        from bonobo import dispatch, gather, skillcore, world
        from bonobo.planner import Step
        from tests.world import inventory, memory
        feet, target = (12960, 67, 12928), (12995, 57, 12995)
        lo, hi = (12955, 50, 12923), (13000, 72, 13000)
        blocks = {(x, y, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)
                  for y in range(lo[1], feet[1])}
        blocks[target] = "iron_ore"
        region = world.Region.of(lo, hi, blocks)
        bag = world.Inventory(inventory(("stone_pickaxe", 1)))
        step = Step("mine", "minecraft:raw_iron", 3, {"blocks": ["iron_ore"], "tier": 1, "breaks": 3},
                    61 * TICKS_PER_S)
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        sent = []

        def run_chain(tasks, stop_on_failure=False, before_segment=None, **_kw):
            for i in range(0, len(tasks), SEGMENT):
                if before_segment:
                    before_segment(tasks[i:i + SEGMENT])
                sent.extend(tasks[i:i + SEGMENT])
            return []
        with mock.patch.object(api, "run_chain", run_chain), mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(gather, "Inventory", lambda *a: bag), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: gather._go_way(ctx, region, feet, target, [], "minecraft:raw_iron"))
        self.assertEqual([t for t in sent if t["type"] == "mine"], [])


def _reads(st, inv_payload):
    """api.get for the skill runner's own reads (the body, the bag): anything else is a read the row did not expect."""
    def api_get(path):
        if path.startswith("/state"):
            return st
        if path.startswith("/inventory"):
            return inv_payload
        raise AssertionError(f"unexpected api.get {path}")
    return api_get


class HuntChaseOverrun(unittest.TestCase):
    """The production path (dispatch.run_priced → gather.hunt → skill._drive): a prey that never closes in (nav.chase
    stubbed "moved" every try, a fake clock 10 s a chase). Priced 10 s, its budget 15 s: stopped after 2 chases, before
    a 3rd is ever tried (must fail: every one of count*3+3 tries runs)."""

    def test_stopped_before_the_3rd_chase(self):
        from bonobo import dispatch, gather, skillcore
        from bonobo.planner import Step
        from tests.world import inventory, memory, state

        clock = [0.0]
        chases = []
        prey = {"id": 7, "type": "minecraft:cow", "health": 10.0, "distance": 10.0, "x": 5.0, "y": 64.0, "z": 0.0}

        def chase(*a, **k):
            chases.append(1)
            clock[0] += 10.0
            return "moved", None

        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        step = Step("hunt", "minecraft:beef", 1, {"types": ["minecraft:cow"]}, 10 * TICKS_PER_S)
        with mock.patch.object(api, "get", _reads(state(x=.5, y=64.0, z=.5), inventory())), \
                mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(gather, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(gather, "entities", lambda *a, **k: [prey]), \
                mock.patch.object(gather.nav, "chase", chase), \
                mock.patch.object(nav.time, "time", lambda: clock[0]), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: gather.hunt(ctx, "minecraft:beef", 1, ("minecraft:cow",), False))
        self.assertLessEqual(len(chases), 2, "must fail: a 3rd chase tried past the step's budget")


class SeekLegOverrun(unittest.TestCase):
    """The production path (dispatch.run_priced → explore.seek → seek_blocks → skill._drive): nothing in sight or
    remembered, so seek falls through to seek_blocks's `_search`, stubbed to legs that find nothing (a fake clock
    +30 s each). Priced 20 s, its budget 30 s: stopped after 2 legs, never the SEARCH_LEGS (must fail: every leg runs).
    The skill runner reads the bag (its needs, the bag check): served like the body's /state."""

    def test_stopped_after_two_legs(self):
        from bonobo import dispatch, explore, skillcore
        from bonobo.planner import Step
        from tests.world import inventory, memory, state

        clock = [0.0]
        legs = []

        def fake_search(ctx, kinds, look, radius, legs_n):
            for _ in range(legs_n):
                legs.append(1)
                clock[0] += 30.0
                yield (0, 0)
            raise api.NotAvailable("stub: never found")

        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        step = Step("seek", "minecraft:iron_ore", 1, {"kinds": ["minecraft:iron_ore"]}, 20 * TICKS_PER_S)
        with mock.patch.object(api, "get", _reads(state(x=.5, y=64.0, z=.5), inventory())), \
                mock.patch.object(explore, "log", lambda *a: None), \
                mock.patch.object(explore, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(explore, "find", lambda *a, **k: []), \
                mock.patch.object(explore, "entities", lambda *a, **k: []), \
                mock.patch.object(explore, "_search", fake_search), \
                mock.patch.object(nav.time, "time", lambda: clock[0]), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                self.assertRaises(api.McError):
            dispatch.run_priced("minecraft:overworld", step, False,
                                lambda: explore.seek(ctx, ["minecraft:iron_ore"]))
        self.assertLessEqual(len(legs), 2, "must fail: a 3rd leg tried past the step's budget")


if __name__ == "__main__":
    unittest.main()
