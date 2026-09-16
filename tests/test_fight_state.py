"""Offline tests for the whole chain: world → fight_state() → Fight.plan().

These exist because the planner's own tests passed while the fight was frozen: they built states by hand, so they
only ever exercised the planner, and both bugs that made the agent stand still and die were in `fight_state()` —
the function that BUILDS the state from the world. The world here is a dict; nothing opens Minecraft.
"""
import time
import unittest
from unittest import mock

from bonobo import end, fight_plan as fp


def world(dragon_phase=0, dragon_hp=200.0, hp=20.0, dist=40.0, extra=()):
    state = {"x": 20.0, "y": 64.0, "z": 0.0, "blockX": 20, "blockY": 64, "blockZ": 0,
             "health": hp, "food": 20, "dimension": "minecraft:the_end"}
    near = list(extra)
    if dragon_phase is not None:
        near.append({"type": "minecraft:ender_dragon", "id": 1, "health": dragon_hp, "phase": dragon_phase,
                     "x": 0.0, "y": 70.0, "z": 0.0, "distance": dist})
    return state, near


class Inv:
    def __init__(self, beds=6, obsidian=8):
        self._c = {"bed": beds, "minecraft:obsidian": obsidian, "minecraft:water_bucket": 1}

    def count(self, item):
        return self._c.get(item, 0)


def build(state, near, beds=6, pit=None, bed_placed=False):
    end.PIT[:] = pit or []
    with mock.patch.object(end, "Inventory", lambda: Inv(beds)), \
         mock.patch.object(end, "_solid", lambda cell: bed_placed):
        return end.fight_state(near, state, ctx=None)


F = fp.Fight()


class Shape(unittest.TestCase):
    def test_fight_state_is_the_structure_the_planner_validates(self):
        state, near = world()
        end._track_phase(near)
        s = build(state, near)
        self.assertEqual(set(s), {"self", "boss", "threats", "resources", "terrain"})
        self.assertEqual(fp.validate_state(s), [])

    def test_threat_rows_carry_their_kind_and_a_velocity(self):
        state, near = world(extra=[{"type": "minecraft:enderman", "id": 9, "x": 5.0, "y": 64.0, "z": 0.0,
                                    "angry": True}])
        end._track_phase(near)
        s = build(state, near)
        kinds = {t[3] for t in s["threats"]}
        self.assertIn("minecraft:enderman", kinds)
        # (centre, reach, velocity, kind) at least; awareness and dps follow for rows that have them.
        self.assertTrue(all(len(t) >= 4 for t in s["threats"]))

    def test_neutral_endermen_are_not_threats(self):
        state, near = world(extra=[{"type": "minecraft:enderman", "id": 9, "x": 5.0, "y": 64.0, "z": 0.0,
                                    "angry": False}])
        end._track_phase(near)
        s = build(state, near)
        self.assertNotIn("minecraft:enderman", {t[3] for t in s["threats"]})


class PhaseClock(unittest.TestCase):
    """The clock started at the epoch, so every phase looked long over and every action was refused.

    Reaching that path needs care: `_track_phase` resets the clock, and the subtraction only runs when the stored
    phase equals the reported one. The first version of these tests did both wrong and passed against broken code.
    """

    def test_an_epoch_clock_does_not_become_a_huge_elapsed_time(self):
        state, near = world(dragon_phase=0)
        end.PHASE_SINCE[:] = [0, 0.0]
        self.assertLess(build(state, near)["boss"]["phase_elapsed_s"], 300.0)

    def test_an_epoch_clock_still_leaves_time_to_act(self):
        state, near = world(dragon_phase=0)
        end.PHASE_SINCE[:] = [0, 0.0]
        s = build(state, near)
        self.assertGreater(fp.remaining(s["boss"]["phase"], s["boss"]["phase_elapsed_s"]), 0.0)

    def test_the_module_never_starts_at_the_epoch(self):
        import importlib
        self.assertGreater(importlib.reload(end).PHASE_SINCE[1], 1e9)

    def test_the_clock_advances_with_real_time(self):
        state, near = world(dragon_phase=6)
        end._track_phase(near)
        time.sleep(0.05)
        e = build(state, near)["boss"]["phase_elapsed_s"]
        self.assertGreater(e, 0.0)
        self.assertLess(e, 5.0)


class EndToEnd(unittest.TestCase):
    """world → fight_state → plan. `retreat` for a healthy agent with work to do means it is frozen."""

    def setUp(self):
        end.PHASE_SINCE[:] = [None, 0.0]

    def _stale(self, phase):
        end.PHASE_SINCE[:] = [phase, 0.0]

    def test_circling_with_nothing_built_digs(self):
        state, near = world(dragon_phase=0)
        self._stale(0)
        p = F.plan(build(state, near))
        self.assertEqual(p["intent"], "dig_tunnel", p["rejected"])

    def test_perched_with_cover_and_a_bed_fires(self):
        state, near = world(dragon_phase=6)
        state.update(x=8.5, y=65.0, z=0.5)
        self._stale(6)
        pit = [(5, 65, 0), (5, 65, 0), (8, 65, 0), (2, 68, 0), 67]
        p = F.plan(build(state, near, pit=pit, bed_placed=True))
        self.assertEqual(p["intent"], "fire_window", p["rejected"])

    def test_a_hurt_agent_can_still_build_cover(self):
        state, near = world(dragon_phase=0, hp=8.0)
        self._stale(0)
        p = F.plan(build(state, near))
        self.assertNotEqual(p["intent"], "retreat", p["rejected"])

    def test_a_dead_agent_retreats_and_says_why(self):
        state, near = world(dragon_phase=0, hp=0.0)
        self._stale(0)
        p = F.plan(build(state, near))
        self.assertEqual(p["intent"], "retreat")
        self.assertTrue(any("health" in why for _, why in p["rejected"]))

    def test_no_dragon_still_answers(self):
        state, near = world(dragon_phase=None)
        self._stale(None)
        self.assertTrue(F.plan(build(state, near))["intent"])


class RetreatIsAnAction(unittest.TestCase):
    """Everything falls back to `retreat`; if `retreat` does nothing, every upstream bug is the same silent death."""

    def _run(self, pit, threats, calls):
        state = {"x": 20.0, "y": 64.0, "z": 0.0, "blockX": 20, "blockY": 64, "blockZ": 0,
                 "health": 20.0, "food": 20, "dimension": "minecraft:the_end"}
        end.PIT[:] = pit
        with mock.patch.object(end.api, "get", lambda p: state), \
             mock.patch.object(end.api, "run", lambda *a, **k: calls.append(("wait", a)) or {"status": "succeeded"}), \
             mock.patch.object(end.nav, "go_to", lambda *a, **k: calls.append(("go_to", a[0])) or True), \
             mock.patch.object(end, "entities", lambda *a, **k: threats):
            return end._retreat(ctx=mock.Mock(), near=threats)

    def test_with_a_corridor_it_walks_into_it(self):
        calls = []
        pit = [(5, 62, 0), (5, 62, 0), (8, 62, 0), (2, 65, 0), 64]
        self.assertEqual(self._run(pit, [], calls), "corridor")
        self.assertEqual(calls[0], ("go_to", (8, 62, 0)), "the retreat cell, not the mouth")

    def test_without_a_corridor_it_still_moves_away(self):
        import math
        calls = []
        dragon = [{"type": "minecraft:ender_dragon", "id": 1, "health": 200.0, "phase": 6,
                   "x": 0.0, "y": 64.0, "z": 0.0, "distance": 20.0}]
        self.assertEqual(self._run([], dragon, calls), "away")
        self.assertEqual(calls[0][0], "go_to")
        here, moved = (20.0, 64.0, 0.0), tuple(calls[0][1])[:3]
        self.assertGreater(math.dist(moved, (0.0, 64.0, 0.0)), math.dist(here, (0.0, 64.0, 0.0)),
                           f"{moved} must put more room between us and the dragon than standing still")

    def test_with_nothing_nearby_waiting_is_honest(self):
        calls = []
        self.assertEqual(self._run([], [], calls), "clear")
        self.assertEqual(calls[0][0], "wait")


class SoftInterrupt(unittest.TestCase):
    def test_soft_interrupt_returns_and_clears_without_raising(self):
        from bonobo import api
        api.INTERRUPT = "breath"
        self.assertEqual(end._soft_interrupt(), "breath")
        self.assertIsNone(api.INTERRUPT)
        self.assertIsNone(end._soft_interrupt())


if __name__ == "__main__":
    unittest.main()
