"""gather._go_way: a way's own step the jar reports it cannot reach (api.Unreachable, api.NavFailed — nav.run_way,
api.run_chain) is banned (ctx.ban, same use as every other refusal) and the way fails quietly, so a fresh pass can
plan another one — not an exception that crashes straight through the chain."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import api, gather, nav, skillcore, world  # noqa: E402
from tests.world import memory  # noqa: E402

START, TARGET, FAIL_CELL = (0, 64, 0), (5, 64, 0), (2, 64, 0)
STEPS = [{"type": "mine", "x": 2, "y": 64, "z": 0}, {"type": "mine", "x": 5, "y": 64, "z": 0}]


class WayUnreachableIsBanned(unittest.TestCase):
    def run_way(self, exc):
        ctx = skillcore.Context(memory(), nav.Policy(), "minecraft:overworld")
        banned = []
        with mock.patch.object(nav, "plan_way", lambda *a, **k: (STEPS, None, 12.0)), \
                mock.patch.object(nav, "run_way", mock.Mock(side_effect=exc)), \
                mock.patch.object(api, "detail", lambda *a: None), \
                mock.patch.object(gather, "Inventory", lambda *a: world.Inventory({"slots": [], "equipment": {}})), \
                mock.patch.object(ctx, "ban", lambda p, **k: banned.append(tuple(p))):
            try:
                got = gather._go_way(ctx, None, START, TARGET, [], "minecraft:coal_ore")
            except (api.Unreachable, api.NavFailed) as e:
                got = e
        return got, banned

    def test_unreachable_bans_the_named_cell(self):
        got, banned = self.run_way(api.Unreachable("mine: cannot reach, no path found", [FAIL_CELL]))
        self.assertIs(got, False)
        self.assertEqual(banned, [FAIL_CELL])

    def test_nav_failed_bans_its_pos(self):
        got, banned = self.run_way(api.NavFailed("no stand reached", pos=FAIL_CELL))
        self.assertIs(got, False)
        self.assertEqual(banned, [FAIL_CELL])


if __name__ == "__main__":
    unittest.main()
