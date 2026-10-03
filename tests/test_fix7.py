"""Remembered ore stays a candidate beside what the look sees (design-f1 V1); a refused
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


class NotedIsACandidate(unittest.TestCase):
    def test_rows(self):
        # design-f1 V1: every known vein is a candidate, priced by its way — the noted one and the ones the look sees
        # (one look a pass: exposed and all); (noted?) → (/find calls, the noted cell among the candidates)
        for name, noted, finds, among in [("noted: a candidate beside what the look sees", True, 2, True),
                                          ("nothing noted: the look alone", False, 2, False)]:
            with self.subTest(name):
                calls, asked = [], []
                ctx = type("C", (), {"mem": type("M", (), {"seen": lambda s, b, d: []})(), "dimension": "o",
                                     "blocked": lambda s, p: False,
                                     "policy": type("P", (), {"protected": set()})()})()
                hit = {"x": ORE[0], "y": ORE[1], "z": ORE[2], "block": "minecraft:diamond_ore", "noted": True}

                def find(*a, **k):
                    calls.append(k.get("exposed", False))
                    return []               # the look sees none: the noted vein is the only candidate

                def stop(blocks, fresh, *a, **k):
                    asked.extend((h["x"], h["y"], h["z"], h.get("noted")) for h in fresh)
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
                self.assertEqual(len(calls), finds)
                # must fail: the noted vein dropped because the look did not see it
                self.assertEqual((*ORE, True) in asked, among)


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
