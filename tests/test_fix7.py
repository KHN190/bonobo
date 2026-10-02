"""Remembered ore is not searched again (search_night_resume 042014: an exposed /find counted as a scan); a refused
travel leg is a walk that got no further, never an error past the walk (ban_then_other_source 041621: the task
cooled, the caged cell never banned, the free ore never tried)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, gather, nav  # noqa: E402

ORE = (4, 64, 0)


class Stop(Exception):
    pass


class NotedNotScanned(unittest.TestCase):
    def test_rows(self):
        # (noted?) → /find calls before the vein is judged
        for name, noted, finds in [("noted: no look", True, 0), ("nothing noted: looked for", False, 1)]:
            with self.subTest(name):
                calls = []
                ctx = type("C", (), {"mem": type("M", (), {"seen": lambda s, b, d: []})(), "dimension": "o",
                                     "blocked": lambda s, p: False,
                                     "policy": type("P", (), {"protected": set()})()})()
                hit = {"x": ORE[0], "y": ORE[1], "z": ORE[2], "block": "minecraft:diamond_ore", "noted": True}

                def find(*a, **k):
                    calls.append(k.get("exposed", False))
                    return [dict(hit, noted=False)]

                def stop(*a, **k):
                    raise Stop()
                with mock.patch.object(gather, "noted_hits", lambda *a: [hit] if noted else []), \
                        mock.patch.object(gather, "find", find), mock.patch.object(gather, "seek_hits", stop), \
                        mock.patch.object(gather, "require_pickaxe", lambda t: None), \
                        mock.patch.object(gather, "Inventory", lambda: type("I", (), {"count": lambda s, t: 0})()), \
                        mock.patch.object(gather.nav, "mod_features", lambda: {"travel"}):
                    gen = gather.mine.__wrapped__(ctx, "minecraft:diamond", 1, ["diamond_ore"], 2)
                    with self.assertRaises(Stop):
                        for _ in gen:
                            pass
                # must fail: a noted ore still scanned
                self.assertEqual(len(calls), finds)


class RefusedLeg(unittest.TestCase):
    def test_a_refused_leg_is_no_way_there(self):
        here = {"x": 0.5, "y": 64.0, "z": 0.5, "blockX": 0, "blockY": 64, "blockZ": 0, "onGround": True,
                "dimension": "minecraft:overworld"}

        def run(task, **k):
            raise api.Unreachable("travel: target unreachable; stopped at the closest reachable point", ())
        with mock.patch.object(api, "run", run), mock.patch.object(api, "get", lambda p: here), \
                mock.patch.object(nav, "feet", lambda: (0, 64, 0)), mock.patch.object(nav, "DOORS", None), \
                mock.patch.object(nav, "ROAD_MEM", None), mock.patch.object(nav, "_doorways_between", lambda a, b: {}), \
                mock.patch.object(nav, "mod_features", lambda: {"travel"}), \
                mock.patch.object(nav, "Inventory", lambda: type("I", (), {"count": lambda s, k: 0})()), \
                mock.patch.object(api, "at_boundary", lambda: None), \
                mock.patch.object(nav.arbiter.BODY, "owns", lambda *a: True):
            # must fail: the refusal raised out of the walk (the task failed and cooled, no ban)
            self.assertFalse(nav.arrived_near((ORE[0] * 3, ORE[1], ORE[2]), nav.Policy(), range_=3.5, attempts=1))


if __name__ == "__main__":
    unittest.main()
