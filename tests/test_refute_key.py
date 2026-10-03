"""The refuted price's key (api.Overrun's `pos` vs Cost.site): gather.mine's buried-vein branch prices its way to
`near` (gather.approach_cell: the vein's own nearest/open-faced cell, _go_way's nav target), but Cost.refuted_ticks
always looks the price up at Cost.site(step) — the nearest REMEMBERED cell of the kind (cost.py's `first`). A vein
of more than one cell whose approach cell differs from the remembered one never shares a key with what Cost reads,
so a price the run refuted is never found: the same wall is replanned into forever at the same low price. Drives the
real gather.mine generator and dispatch.execute's own refute write (not a copy of either), so this is red on the
base by its estimate assertion (reads the price back unrefuted), not an ImportError."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, cost as costmod, dispatch, gather, nav, skillcore, world  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import inventory, memory, state  # noqa: E402

DIM = "minecraft:overworld"
FEET = (12960, 67, 12928)
ORE = (12995, 57, 12995)         # remembered (mem.note_seen): what Cost.site reads back
NEAR = (12994, 57, 12995)        # one block nearer the body: the vein's own approach cell (gather.approach_cell)
LO, HI = (12955, 50, 12923), (13000, 72, 13000)


def _region():
    blocks = {(x, y, z): "stone" for x in range(LO[0], HI[0] + 1) for z in range(LO[2], HI[2] + 1)
              for y in range(LO[1], FEET[1])}
    blocks[ORE] = blocks[NEAR] = "iron_ore"          # a 2-cell vein, both fully buried (stand_spot: True regardless)
    return world.Region.of(LO, HI, blocks)


class MineOverrunKey(unittest.TestCase):
    """A vein 10 down and far off: its way is priced past the 61 s step's budget before any digging (api.afford),
    refuted by dispatch.execute; the vein's second cell (NEAR) sits a touch closer to the body than the remembered
    one (ORE), so approach_cell goes for it first."""

    def test_refuted_price_is_read_back_at_cost_site(self):
        region = _region()
        mem = memory()
        mem.note_seen("iron_ore", ORE, DIM)
        bag = world.Inventory(inventory(("stone_pickaxe", 1)))
        ctx = skillcore.Context(mem, nav.Policy(), DIM)
        step = Step("mine", "minecraft:raw_iron", 3, {"blocks": ["iron_ore"], "tier": 1, "breaks": 3},
                    61 * TICKS_PER_S)

        def fake_run_step(ctx, step, night, seek=True):
            run = gather.mine.__wrapped__(ctx, step.token, step.count, step.detail["blocks"], step.detail["tier"],
                                           step.detail.get("breaks"))
            next(run)      # the first pass's top
            next(run)      # resumes past it: the vein's near cell picked, the way priced — raises here

        def cost_of():
            snap = world.Snapshot.from_readings(state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5), bag, {}, [], region)
            return costmod.Cost(snap, mem)

        base_ticks = cost_of().estimate(step)
        # the ban/refute state is (feet, way-relevant kinds) read off api.STATE (skillcore.ban_state), live by real
        # /state and /inventory reads (api.get's side effect) that this offline run never makes — pinned here to the
        # same feet and bag Cost's snapshot uses, so the write (dispatch.execute) and the read (Cost) agree on it
        kinds = frozenset(s["id"] for s in bag.slots if s.get("count"))
        with mock.patch.object(dispatch, "run_step", fake_run_step), \
                mock.patch.object(dispatch, "trace", lambda *a, **k: None), \
                mock.patch.object(gather, "Inventory", lambda *a: bag), \
                mock.patch.object(gather, "find", lambda *a, **k: []), \
                mock.patch.object(gather, "feet", lambda: FEET), \
                mock.patch.object(gather, "region_around", lambda *a, **k: region), \
                mock.patch.object(api, "get", lambda *a, **k: state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)), \
                mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(api, "run_chain", lambda *a, **k: []), \
                mock.patch.object(api.STATE, "feet_seen", FEET), \
                mock.patch.object(api.STATE, "kinds_seen", kinds), \
                self.assertRaises(api.Overrun) as raised:
            dispatch.execute(ctx, step, False)

        # the step's hand-set price is not Cost's, so the refuted rest can land under base_ticks: compare to it instead
        refuted_ticks = round(raised.exception.remaining_s * TICKS_PER_S)
        self.assertNotEqual(refuted_ticks, base_ticks)
        self.assertEqual(cost_of().estimate(step), refuted_ticks,
                         "must fail: the refuted price written at the approach cell, never read at Cost.site")


if __name__ == "__main__":
    unittest.main()
