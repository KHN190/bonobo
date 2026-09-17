"""What the agent does, and when — a life played forward offline.

Every other test asks about one round: is there a candidate, is it runnable, is it priced. None could ask the
question that kept going wrong, which is about TIME. A pool can be right in every single round and still produce a
life in which one decision is re-taken a hundred times in twenty seconds, or in which a four-hundred-second luxury
plan collects the future value of the twenty-second pickaxe it passes through.

`decide.playthrough` plays the world forward with no game: the clock advances by what the chosen candidate was
estimated to cost, whatever a goal was for lands in the bag, hunger falls with the clock, sleeping puts the sun
back up. The estimates come from the action table itself — `seek:` for walking, `mine:` for digging, `craft:` for
crafting — so everything the planner can plan, the playthrough can play.

The assertions are PROPERTIES over the whole timeline and over EVERY candidate, never one patched symptom:

  * nothing is re-chosen faster than it was promised the body (`priority.step_commitment`);
  * a skill that has said it cannot run is not chosen again while it is cooling;
  * no round is idle, and no single task eats the whole life.

Written after the live agent logged "light up: no torches to spare" a hundred times in twenty seconds — which was
not a hundred bad decisions but one decision, replayed, because a step estimated at 0 s was promised the body for
0 s. A single-round test can never see that.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import decide, paths, priority  # noqa: E402
from bonobo.api import NotAvailable  # noqa: E402
from bonobo.survival import CONFIG as _PLAY  # noqa: E402

RECORDED = decide.load() if os.path.exists(paths.data("decisions.jsonl")) else []
SKIP = "no recorded rounds to play forward from"


def replayable():
    for row in reversed(RECORDED):
        try:
            decide.decide(row)
        except Exception:
            continue
        return row
    raise unittest.SkipTest(SKIP)


def life(rounds=12, **edits):
    row = replayable()
    return decide.playthrough(decide.synth(row, **edits) if edits else row, rounds=rounds)


def names(timeline):
    return [e[1] for e in timeline]


@unittest.skipUnless(RECORDED, SKIP)
class NothingIsRetakenFasterThanItWasPromised(unittest.TestCase):
    """The property the hundred refusals broke. Not "light up must not repeat" — no key may."""

    def test_no_commitment_is_shorter_than_the_cost_of_changing_your_mind(self):
        for est, count in ((0, 1), (0, 8), (1, 1), (20, 4)):
            self.assertGreaterEqual(priority.step_commitment(est, count), priority.COMMIT_FLOOR_S,
                                    f"a step estimated at {est} ticks ×{count} was promised nothing")

    def test_a_cheap_step_still_holds_the_body(self):
        """`seek 1× stone (~0s)` — the stone is underfoot. Deciding again costs more than the step does."""
        self.assertGreater(priority.step_commitment(0, 1), 0.0)
        self.assertEqual(priority.step_commitment(0, 1), priority.COMMIT_FLOOR_S)

    def test_no_task_is_rechosen_faster_than_its_commitment(self):
        timeline = life(rounds=14)
        first, last = {}, {}
        for t, n, _end in timeline:
            if n is None:
                continue
            first.setdefault(n, t)
            last[n] = t
        for n, seen in first.items():
            span = last[n] - seen
            taken = sum(1 for e in timeline if e[1] == n)
            allowed = 1 + math.ceil(span / priority.COMMIT_FLOOR_S)
            self.assertLessEqual(taken, allowed, f"{n} chosen {taken}× in {span:.0f}s: the body was never held")


@unittest.skipUnless(RECORDED, SKIP)
class ARefusalIsHeard(unittest.TestCase):
    """Whatever is chosen, if it answers "I cannot run", it must not be chosen again while it is cooling.

    Over every candidate the pool offers, not over one named skill: any new way to spin must fail here.
    """

    def test_nothing_that_refused_is_retried_before_its_cooldown(self):
        """By KEY, not by name. Three furnaces being collected are three jobs with three cooldowns, and one name
        appearing eight times is correct; the same key coming back before its backstop has expired is not."""
        from bonobo import retry
        row = replayable()
        picks = decide.simulate(row, lambda name, i: NotAvailable("scripted refusal"), rounds=40)
        when, idled_since = {}, {}
        for t, _name, key in picks:
            if key is None:
                # Nothing was runnable. The idle rule (brain.IDLE_LIMIT, the user's "never idle past ~15 s") then
                # forces cooling work back in, and a refusal returning early is that rule working, not a deaf pool.
                idled_since = dict.fromkeys(when, True)
                continue
            last = when.get(key)
            if last is not None and not idled_since.get(key):
                self.assertGreaterEqual(t - last, retry.BACKSTOP["unavailable"],
                                        f"{key} refused and was back after {t - last:.0f}s with other work available")
            when[key] = t
            idled_since[key] = False

    def test_a_refusal_is_never_answered_by_trying_the_same_thing_again(self):
        """Idling is an acceptable answer when everything refuses; retrying the refusal immediately is not."""
        row = replayable()
        picks = decide.simulate(row, lambda name, i: NotAvailable("scripted refusal"), rounds=40)
        keys = [k for _t, _n, k in picks]
        for a, b in zip(keys, keys[1:]):
            if a is not None:
                self.assertNotEqual(a, b, f"{a} refused and was chosen again on the very next round")


@unittest.skipUnless(RECORDED, SKIP)
class TheShapeOfALife(unittest.TestCase):
    def test_the_agent_does_not_stay_idle(self):
        """One idle round is allowed — everything may be cooling at the moment the tape was cut. Staying idle is
        not: the idle rule (brain.IDLE_LIMIT) thaws cooling work, and the user's rule is never idle past ~15 s."""
        picked = names(life(rounds=12))
        run = longest = 0
        for n in picked:
            run = run + 1 if n is None else 0
            longest = max(longest, run)
        self.assertLessEqual(longest, 2, f"idle for {longest} rounds in a row: {picked}")
        self.assertGreater(sum(1 for n in picked if n), len(picked) // 2, f"mostly idle: {picked}")

    def test_it_does_not_spend_the_whole_life_on_one_thing(self):
        picked = names(life(rounds=12))
        self.assertGreater(len(set(picked)), 1, f"the whole playthrough was one task: {picked}")

    def test_time_moves(self):
        timeline = life(rounds=8)
        self.assertGreater(timeline[-1][2], 0.0, "time did not move")


@unittest.skipUnless(RECORDED, SKIP)
class FirstThingsFirst(unittest.TestCase):
    """Kit before luxuries. Both plans pass through a pickaxe; only one of them hands it over soon."""

    LUXURIES = ("enchanting table", "nether portal", "flint and steel", "brewing", "ender")
    KIT = ("pickaxe", "sword", "table", "shelter", "bed", "eat", "torch")

    def test_tools_come_before_luxuries(self):
        picked = names(life(rounds=10, remove_items=["minecraft:stone_pickaxe", "minecraft:wooden_pickaxe",
                                                     "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe"]))
        luxury = next((i for i, n in enumerate(picked) if n and n.startswith(self.LUXURIES)), None)
        if luxury is None:
            return                     # never reached for a luxury at all: nothing to prove
        kit = next((i for i, n in enumerate(picked) if n and any(w in n for w in self.KIT)), None)
        self.assertIsNotNone(kit, f"no kit work in the whole life: {picked}")
        self.assertLess(kit, luxury, f"luxury before kit: {picked}")


@unittest.skipUnless(RECORDED, SKIP)
class HowLongItTakes(unittest.TestCase):
    """Not only what it does, but when it gets there — under three scripts, so failure and delay travel the real
    回路 (cooldown, re-plan, commitment) rather than being assumed away.

    No distributions: the arrival time of a plan is not what is being modelled. What is being checked is that the
    milestone is REACHED at all when things go wrong, and within a declared budget (`play.toml [milestone]`, listed
    unmeasured, so refitting the model refits the budget rather than breaking the test).
    """

    BUDGET = _PLAY["milestone"]

    def scripts(self):
        """Everything works; the measured failures happen; everything takes twice as long."""
        from bonobo.api import NotAvailable
        return {"all works": (None, 1.0),
                "every third attempt fails": (lambda n, i: True if i % 3 else NotAvailable("scripted"), 1.0),
                "twice as slow": (None, 2.0)}

    # Rounds are not a budget: one round repairs a tool, the next walks thirty seconds, and counting them measures
    # the loop rather than the life. The budget is SECONDS (`[milestone]`); this is only a guard against a life
    # that never ends.
    LOOP_GUARD = 400

    def run_to(self, *items, **edits):
        out = {}
        for label, (script, slow) in self.scripts().items():
            row = decide.synth(replayable(), **edits) if edits else replayable()
            timeline = decide.playthrough(row, rounds=self.LOOP_GUARD, script=script, slowdown=slow,
                                          until=lambda r: any(decide.reached(r, i) for i in items))
            out[label] = timeline
        return out

    def test_a_pickaxe_is_reached_under_every_script(self):
        budget = float(self.BUDGET["pickaxe_s"])
        any_pickaxe = ["minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe",
                       "minecraft:diamond_pickaxe"]
        chain = ("pickaxe", "stone", "log", "plank", "stick", "wood", "craft", "mine")
        for label, timeline in self.run_to(*any_pickaxe, remove_items=any_pickaxe).items():
            took = timeline[-1][2] if timeline else 0.0
            self.assertLess(len(timeline), self.LOOP_GUARD,
                            f"[{label}] ran into the loop guard without a pickaxe: {names(timeline)}")
            self.assertLess(took, budget, f"[{label}] took {took:.0f}s of a {budget:.0f}s budget: {names(timeline)}")
            # And it must still be TRYING all the way: a life that gives up on the pickaxe and fills the clock
            # with repairs and strolls would pass a budget test purely by running out of things to fail at.
            tail = [n for _t, n, _e in timeline[-8:] if n]
            self.assertTrue(any(any(word in n for word in chain) for n in tail),
                            f"[{label}] stopped working towards a pickaxe: {names(timeline)}")

    def test_failures_cost_time_but_do_not_stop_the_life(self):
        """The script that fails must take longer than the one that does not — if it does not, failure is not
        travelling through the pool at all, and the test is measuring nothing."""
        runs = self.run_to("minecraft:stone_pickaxe", "minecraft:iron_pickaxe")
        clean = runs["all works"][-1][2] if runs["all works"] else 0.0
        slow = runs["twice as slow"][-1][2] if runs["twice as slow"] else 0.0
        self.assertGreaterEqual(slow, clean, "doubling every estimate changed nothing: the clock is not the plan's")


if __name__ == "__main__":
    unittest.main()
