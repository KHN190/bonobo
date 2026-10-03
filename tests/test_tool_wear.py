"""arm's tool-wear cut (skillcore.arm, knowledge.tool_for/tool_uses_left/working/spare_uses): a chain never silently
downgrades past where its tool runs out -- it drops only the worn-out tool's own mines, keeps everything else in
order, and raises ToolMissing naming the prefix still armed (empty when even the first mine can't be done)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import knowledge as K, retry, skillcore  # noqa: E402
try:
    from bonobo.api import ToolMissing  # noqa: E402
except ImportError:
    from bonobo.skillcore import ToolMissing  # noqa: E402  (pre-fix base: skillcore's own class, no done/pos)
from bonobo.world import Inventory  # noqa: E402


class _NamedRegion:
    """A region stand-in that names every cell the same block: arm only ever asks `.name(cell)`."""

    def __init__(self, name):
        self._name = name

    def name(self, cell):
        return self._name


def tool_slot(item, uses_left, slot=0, max_damage=1000):
    return {"id": f"minecraft:{item}", "count": 1, "slot": slot, "damage": max_damage - uses_left, "maxDamage": max_damage}


def bag(*tools):
    return Inventory({"slots": [tool_slot(item, left, n) for n, (item, left) in enumerate(tools)],
                      "selectedSlot": 0, "equipment": {}})


def mine_at(i):
    return {"type": "mine", "x": i, "y": 64, "z": 0}


PICK_TIER = K.item_tier("minecraft:stone_pickaxe")


class ToolWear(unittest.TestCase):
    """Rows compared against 6ba6f1b (base, before this fix): arm there never cuts a chain at all -- it silently
    arms every mine with whatever tool_for picks, so every `assertRaises(ToolMissing)` row below fails on it (no
    exception comes out); the two rows that assert nothing is raised (two same-id tools, hand-mineable blocks)
    pass on the base too, for the wrong reason -- it never checks durability either way."""

    def test_cuts_the_chain_where_the_tool_runs_out(self):
        # must fail on 6ba6f1b: arm there returns all 145 armed with stone_pickaxe, no exception
        inv = bag(("stone_pickaxe", 30))
        tasks = [mine_at(i) for i in range(145)]
        safe = K.spare_uses(30)             # 30 - TOOL_WORKING(3) = 27
        with mock.patch.object(skillcore, "box", lambda lo, hi: _NamedRegion("stone")):
            with self.assertRaises(ToolMissing) as cm:
                skillcore.arm(tasks, inv=inv)
        e = cm.exception
        self.assertEqual(len(e.done), safe)
        self.assertEqual(e.done[-1]["x"], safe - 1)
        self.assertEqual(e.pos, (safe, 64, 0))      # the first cell the worn-out tool can't do
        self.assertEqual(e.kind, "pickaxe")
        self.assertEqual(e.tier, PICK_TIER)

    def test_the_first_mine_cut_raises_before_anything_runs(self):
        # must fail on 6ba6f1b: no exception; here arm must raise with an empty prefix, nothing to send
        inv = bag(("stone_pickaxe", 3))     # spare_uses(3) == 0: not even one more use
        tasks = [mine_at(0), mine_at(1)]
        with mock.patch.object(skillcore, "box", lambda lo, hi: _NamedRegion("stone")):
            with self.assertRaises(ToolMissing) as cm:
                skillcore.arm(tasks, inv=inv)
        self.assertEqual(cm.exception.done, [])
        self.assertEqual(cm.exception.pos, (0, 64, 0))

    def test_trailing_non_mine_tasks_survive_the_cut(self):
        # must fail on 6ba6f1b: no exception, so there is no "done" prefix to check at all
        inv = bag(("stone_pickaxe", 4))     # spare_uses(4) == 1: exactly one mine goes through
        goto, collect = {"type": "goto", "x": 9, "y": 64, "z": 0}, {"type": "collect", "radius": 5}
        tasks = [mine_at(0), mine_at(1), goto, collect]
        with mock.patch.object(skillcore, "box", lambda lo, hi: _NamedRegion("stone")):
            with self.assertRaises(ToolMissing) as cm:
                skillcore.arm(tasks, inv=inv)
        done = cm.exception.done
        self.assertEqual([t["type"] for t in done], ["mine", "goto", "collect"])
        self.assertEqual(done[1], goto)
        self.assertEqual(done[2], collect)

    def test_two_same_id_tools_sum_their_uses(self):
        # passes on 6ba6f1b too, but for the wrong reason: it never reads durability at all
        inv = bag(("stone_pickaxe", 2), ("stone_pickaxe", 30))   # first slot alone: spare_uses(2) == 0
        tasks = [mine_at(i) for i in range(5)]
        with mock.patch.object(skillcore, "box", lambda lo, hi: _NamedRegion("stone")):
            armed = skillcore.arm(tasks, inv=inv)   # must not raise: the SUM (32) comfortably covers 5
        self.assertEqual(len(armed), 5)

    def test_hand_mineable_blocks_are_never_counted(self):
        # passes on 6ba6f1b too, same reason as above
        inv = bag(("stone_pickaxe", 3))     # 0 spare uses for stone -- irrelevant, leaves need no tool at all
        self.assertIsNone(K.tool_kind("oak_leaves"))     # the premise: genuinely hand-mineable, not just shovel-ish
        tasks = [mine_at(i) for i in range(50)]
        with mock.patch.object(skillcore, "box", lambda lo, hi: _NamedRegion("oak_leaves")):
            armed = skillcore.arm(tasks, inv=inv)
        self.assertEqual(len(armed), 50)

    def test_tool_missing_maps_to_cause_tool(self):
        # holds on both: a name-matched row in data.EXCEPTIONS, untouched by this fix
        self.assertEqual(retry.cause_of(ToolMissing("pickaxe", 1)), "tool")


if __name__ == "__main__":
    unittest.main()
