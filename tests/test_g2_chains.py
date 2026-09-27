"""G (single send → run_chain), the pure halves: fluids.light_commands and end.crystal_commands' partial failure."""
import unittest

from bonobo import end, fluids
from bonobo.api import NotAvailable
from tests.test_skill_contract import CAGE, CRYSTAL, body, cells, types, world
from tests.world import inventory

FLINT = inventory(flint_and_steel=1)


class LightCommands(unittest.TestCase):
    ROWS = [
        ("first try, facing east: the inner bottom obsidian's top, then the settle",
         (FLINT, ((0, 64, 0), 0, 0)),
         [{"type": "use_item", "item": "minecraft:flint_and_steel", "x": 1.5, "y": 65.0, "z": 0.5, "onBlock": True},
          {"type": "wait", "ticks": 10}]),
        ("turned once: the aim turns with the frame", (FLINT, ((0, 64, 0), 1, 0)),
         (0.5, 65.0, 1.5)),
        ("second try: one block further in", (FLINT, ((10, 70, -3), 0, 1)),
         (12.5, 71.0, -2.5)),
        ("must fail: no flint and steel", (inventory(), ((0, 64, 0), 0, 0)), NotAvailable),
    ]

    def test_rows(self):
        for name, (inv, args), want in self.ROWS:
            with self.subTest(name):
                st = {"inv": body(inv=inv)["inv"]}
                if want is NotAvailable:
                    self.assertRaises(NotAvailable, fluids.light_commands, st, args)
                    continue
                got = fluids.light_commands(st, args)
                if isinstance(want, tuple):
                    self.assertEqual((got[0]["x"], got[0]["y"], got[0]["z"]), want)
                    self.assertEqual(types(got), ["use_item", "wait"])
                else:
                    self.assertEqual(got, want)


def result(t, ok=True, msg=""):
    return dict(t, status="succeeded" if ok else "failed", message=msg)


class CrystalPartial(unittest.TestCase):
    """"N of M failed": what broke stays broken (recomputed from the world), only "cannot reach" cells are banned."""

    def chain(self):
        return end.crystal_commands(body(world(*CAGE), feet=(0, 70, 0), _args=(CRYSTAL,)), (CRYSTAL,))

    def test_bans_only_cannot_reach(self):
        mines = self.chain()
        far = cells(mines, "mine")[5]
        done = [result(t) for t in mines[:5]] + [result(mines[5], False, "cannot reach %d, %d, %d: no path" % far)]
        self.assertEqual(end.unreachable(done), {far})

    def test_other_failures_ban_nothing(self):
        mines = self.chain()
        done = [result(mines[0], False, "interrupted"), result(mines[1], False, "tool broke")]
        self.assertEqual(end.unreachable(done), set())

    def test_after_partial_failure_only_the_rest_minus_banned(self):
        mines = self.chain()
        ordered = cells(mines, "mine")
        broke, far = ordered[:5], ordered[5]
        left = [(c, n) for c, n in CAGE if c not in broke]
        again = end.crystal_commands(body(world(*left), feet=(0, 70, 0), banned={far}), (CRYSTAL,))
        self.assertEqual(sorted(cells(again, "mine")), sorted(ordered[6:]))

    def test_all_failed_all_unreachable_nothing_left(self):
        mines = self.chain()
        done = [result(t, False, "cannot reach %d, %d, %d" % (t["x"], t["y"], t["z"])) for t in mines]
        banned = end.unreachable(done)
        self.assertEqual(len(banned), 8)
        self.assertEqual(end.crystal_commands(body(world(*CAGE), feet=(0, 70, 0), banned=banned), (CRYSTAL,)), [])


if __name__ == "__main__":
    unittest.main()
