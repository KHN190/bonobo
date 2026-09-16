"""Drive the fight loop itself against a mocked world. The incident library: every crash of a live fight becomes a
round here, and the loop must survive it before it is allowed near the game again.

Until this file existed, 147 offline tests were green while the loop died on its first live round with a NameError
in a line nothing had ever executed. Testing the parts a loop calls is not testing the loop.
"""
import unittest
from unittest import mock

from bonobo import arbiter, end, fight_plan as fp


class World:
    """Just enough game to run rounds: a state, an entity list, and a record of what drove the body."""

    def __init__(self, phase=0, dragon_hp=200.0, hp=20.0, endermen=()):
        self.state = {"x": 20.0, "y": 64.0, "z": 0.0, "blockX": 20, "blockY": 64, "blockZ": 0,
                      "health": hp, "food": 20, "dimension": "minecraft:the_end"}
        self.near = [{"type": "minecraft:ender_dragon", "id": 1, "health": dragon_hp, "phase": phase,
                      "x": 0.0, "y": 70.0, "z": 0.0, "distance": 20.0}]
        for i, (x, z) in enumerate(endermen):
            self.near.append({"type": "minecraft:enderman", "id": 10 + i, "x": x, "y": 64.0, "z": z, "angry": True})
        self.commands = []

    def get(self, path):
        return self.state if path == "/state" else {}

    def run(self, task, **kw):
        self.commands.append(("run", task.get("type")))
        return {"status": "succeeded", "message": "", "seconds": 0.1, "type": task.get("type")}

    def go_to(self, pos, *a, **kw):
        self.commands.append(("go_to", tuple(pos)))
        return True


class Inv:
    def count(self, item):
        return {"bed": 6, "minecraft:water_bucket": 1}.get(item, 0)


def rounds(world, n=2):
    """Run `n` rounds of the loop with every body path stubbed onto `world`."""
    end.PIT[:] = []
    end.PHASE_SINCE[:] = [None, 0.0]
    ctx = mock.Mock()
    ctx.policy = None
    motion = arbiter.Motion()
    motion.engage()
    seen = []
    with mock.patch.object(end.api, "get", world.get), \
         mock.patch.object(end.api, "run", world.run), \
         mock.patch.object(end.api, "run_chain", lambda tasks, **k: [world.run(t) for t in tasks]), \
         mock.patch.object(end.nav, "go_to", world.go_to), \
         mock.patch.object(end, "entities", lambda *a, **k: world.near), \
         mock.patch.object(end, "Inventory", Inv), \
         mock.patch.object(end, "_solid", lambda cell: False), \
         mock.patch.object(end, "build_bed_pit", lambda ctx: world.commands.append(("skill", "build_bed_pit"))), \
         mock.patch.object(end, "shake_enderman", lambda ctx: world.commands.append(("skill", "shake_enderman"))), \
         mock.patch.object(end, "dragon_dead", lambda near: False):
        gen = end._fight_rounds(ctx, motion, fp.Fight())
        for _ in range(n):
            seen.append(next(gen))
    motion.disengage()
    return seen


class TheLoopRuns(unittest.TestCase):
    def test_two_rounds_without_an_exception(self):
        # The live failure: round one planned, submitted, stepped — and died on a leftover line. Two rounds here
        # means the whole body of the loop executed twice.
        seen = rounds(World(), n=2)
        self.assertEqual(len(seen), 2)

    def test_a_round_drives_the_body(self):
        # "It does not walk" in one assertion: after a round, something reached the body.
        w = World()
        rounds(w, n=1)
        self.assertTrue(w.commands, "a round produced a plan and nothing ever moved")

    def test_circling_with_nothing_built_digs(self):
        w = World(phase=0)
        rounds(w, n=1)
        self.assertIn(("skill", "build_bed_pit"), w.commands)

    def test_an_enderman_on_us_is_shaken_off(self):
        # The live run's first plan: water_bucket, worth 108 s, with an enderman adjacent. Correct — and it must
        # actually reach the skill, not stop at the intent.
        w = World(phase=0, endermen=[(20.5, 0.5)])
        rounds(w, n=1)
        self.assertIn(("skill", "shake_enderman"), w.commands)

    def test_the_yielded_progress_is_the_chosen_intent(self):
        seen = rounds(World(), n=1)
        name, hp = seen[0]
        self.assertIn(name, {a.name for a in fp.Fight().actions})
        self.assertEqual(hp, 20)


class PreemptionDuringARound(unittest.TestCase):
    def test_a_safety_preemption_from_another_thread_does_not_break_the_loop(self):
        import threading
        from bonobo import api
        w = World()
        end.PIT[:] = []
        end.PHASE_SINCE[:] = [None, 0.0]
        ctx = mock.Mock(); ctx.policy = None
        motion = arbiter.Motion()
        motion.engage()
        fired = []

        def dig(ctx_):
            # Mid-plan, perception speaks from its own thread. The plan must finish or abandon, never crash.
            t = threading.Thread(target=lambda: motion.preempt("safety", lambda: fired.append("stop"), "breath"))
            t.start(); t.join()
            w.commands.append(("skill", "build_bed_pit"))

        with mock.patch.object(end.api, "get", w.get), mock.patch.object(end.api, "run", w.run), \
             mock.patch.object(end.nav, "go_to", w.go_to), mock.patch.object(end, "entities", lambda *a, **k: w.near), \
             mock.patch.object(end, "Inventory", Inv), mock.patch.object(end, "_solid", lambda c: False), \
             mock.patch.object(end, "build_bed_pit", dig), mock.patch.object(end, "dragon_dead", lambda n: False):
            gen = end._fight_rounds(ctx, motion, fp.Fight())
            next(gen); next(gen)
        motion.disengage()
        self.assertTrue(fired, "the preemption must have run")          # once per round that dug
        self.assertTrue(all(f == "stop" for f in fired))
        self.assertEqual(api.INTERRUPT, "breath", "the message channel carries the preemption to the slow action")
        api.INTERRUPT = None


if __name__ == "__main__":
    unittest.main()
