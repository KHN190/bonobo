"""G (single send → run_chain), the pure half: fluids.light_commands."""
import unittest

from bonobo import fluids
from bonobo.api import NotAvailable
from tests.test_skill_contract import body, types
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


if __name__ == "__main__":
    unittest.main()
