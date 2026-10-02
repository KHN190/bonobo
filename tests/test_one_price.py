"""One price per step (D6, K6): every column the solver plans with is priced by the cost model — a unit of it is its
step's work as Cost.work_ticks prices it (actions.priced) — and a step it plans carries Cost.estimate (its work and
the walk to it: actions.to_step), never a second arithmetic."""
import unittest

from bonobo import actions
from bonobo.solve import Action
from tests.world import cost, places, snapshot, state

TICKS_PER_S = actions.TICKS_PER_S


def twice_priced(cost_model, column):
    """Pure: the column priced two ways — (its seconds per unit, the cost model's for its unit step) — or None when
    they agree (to the tick)."""
    own, model = column.cost_s * TICKS_PER_S, max(1, cost_model.work_ticks(actions._shape(column, 1)))
    return None if abs(own - model) <= 1 else (own, model)


def body_and_night(cost_model):
    """The columns that are not in the base table: up for air, to land, a block underfoot, waiting for day."""
    return actions._body(cost_model, {}) + actions._shelter(cost_model, {})


class OnePrice(unittest.TestCase):
    WORLDS = [("nothing known", places(None)), ("everything 30 s away", places(30.0)),
              ("a night half gone", cost(snapshot(state(timeOfDay=18000)))), ("by day", cost(snapshot(state(timeOfDay=1000))))]

    def test_every_column_over_the_worlds(self):
        for name, c in self.WORLDS:
            cols = list(actions.table(c, {}))
            self.assertTrue({a.tag[0] for a in cols if a.tag} >= {"seek", "reach", "wait", "gather", "mine", "take",
                                                                  "craft", "smelt", "shelter", "room", "sleep"}, name)
            for col in cols:
                with self.subTest(world=name, column=col.name):
                    self.assertIsNone(twice_priced(c, col))

    def test_a_planned_step_carries_the_estimate(self):
        for name, c in self.WORLDS[2:]:          # with a body to walk from (a planned step's walk is from the feet)
            for col in actions.table(c, {}):
                with self.subTest(world=name, column=col.name):
                    step = actions.to_step(col, 2, c)
                    self.assertEqual(step.est, int(c.estimate(actions._shape(col, 2))))

    def test_a_column_with_its_own_arithmetic_fails(self):
        """Must fail: a seek column priced by itself (the old _seek: seek_s / find_p computed in the column, rounded)."""
        c = places(30.0)
        col = actions._seek(c)[0]
        own = Action(col.name, col.effect, col.cost_s * 1.5, requires=col.requires, limit=col.limit, tag=col.tag)
        self.assertIsNotNone(twice_priced(c, own))

    def test_the_wait_is_the_night_left(self):
        """By the clock: half a night left costs about half a night's wait; nothing on the clock, a whole night."""
        half = cost(snapshot(state(timeOfDay=18000)))
        wait = next(a for a in body_and_night(half) if a.name == "wait:day")
        self.assertAlmostEqual(wait.cost_s, (actions.NIGHT_END - 18000) / 20.0, delta=0.1)
        blind = places(None)
        wait = next(a for a in body_and_night(blind) if a.name == "wait:day")
        self.assertEqual(wait.cost_s, actions.NIGHT_S)


if __name__ == "__main__":
    unittest.main()
