"""D1: a held plan with no runnable step still names why (brain.step_reason), never the bare "no step of the plan can
run from here" while steps exist — accept5's three idle situations (short, cooling, surface-only underground) plus
the one case the bare default is actually right (no steps at all)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain as brainmod  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import brain_fixture  # noqa: E402


class StepReasonNamesTheStep(unittest.TestCase):
    def test_rows(self):
        gather_wood = Step("gather", "minecraft:oak_log", 1, {})
        mine_stone = Step("mine", "minecraft:stone", 1, {})
        make_table = Step("craft", "minecraft:crafting_table", 1, {"inputs": {"minecraft:oak_planks": 4}})
        # (name, steps, skip, runnable, ready) -> want (brain.step_key(the named step): its reason)
        rows = [
            ("short of what it needs: a craft with nothing to craft it from",
             [make_table], (lambda st: False), (lambda st, inv: False), (lambda name: True),
             f"{brainmod.step_key(make_table)}: short of what it needs"),
            ("cooling, no seek found a way (the same source failed before)",
             [mine_stone], (lambda st: False), (lambda st, inv: True), (lambda name: False),
             f"{brainmod.step_key(mine_stone)}: cooling, no seek alternative found it a way"),
            ("accept5: a surface step, underground by night — every step skipped, still named",
             [gather_wood], (lambda st: True), (lambda st, inv: True), (lambda name: True),
             f"{brainmod.step_key(gather_wood)}: a surface step, underground by night"),
            ("its own precondition refuses it (station, fight line)",
             [mine_stone], (lambda st: False), (lambda st, inv: True), (lambda name: True),
             f"{brainmod.step_key(mine_stone)}: its own preconditions (station, fight line) refuse it"),
            ("must fail: no steps at all is the one case the bare default is right",
             [], (lambda st: False), (lambda st, inv: True), (lambda name: True),
             "no step of the plan can run from here"),
        ]
        b = brain_fixture()
        snap = mock.Mock(inv=None, night=True)
        for name, steps, skip, runnable, ready, want in rows:
            with self.subTest(name):
                with mock.patch.object(brainmod, "runnable", runnable), mock.patch.object(b, "ready", ready):
                    got = b.step_reason(steps, snap, None, skip=skip)
                self.assertEqual(got, want)
