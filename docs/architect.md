# Planner Architecture

## Survival

```python
brain.round()                                    # fixed order; the first layer with something to do takes it
  1 player holds control      → wait
  2 L0                        hazard.due(state) → hazard.handle (lava, drowning, buried); a fight lease → yield
  3 upkeep (one table)        recover · eat · reach land · leave Nether · dig out · sleep · shelter · furnace job
                              · empty bag · unstuck;  queue at the front: pickaxe, food, bed
                              food:  food_lasts_s < plan_s(have food) × LEAD      LEAD = 1.5
                              bed:   dusk_s = (12000 − timeOfDay)/20 < plan_s(have bed) × LEAD
  4 queue head (tasks.json)   held plan (sequence) → valid(next step)? → run it
                              event (failed · interrupted · bag changed) → repair → else every solver
  5 idle                      prepare: pickaxe, sword, food, torches; else wait

decompose(inv, goal, cost, solver) -> [Step]     goals: have craft milestone goto road build sleep skill
  solvers   planner (default) · solve (fallback; actions.table columns)
  cost      Cost.estimate(step) = walk(distance) + work(measured after 3 samples, else prior)   ticks
  dispatch  skill.provider(ctx, step): @skill(provides={"kind:token" | "item:token" | "kind": adapter})

outcome   ok | failed(cause) | interrupted          retry: count (task, cause); cool cause@place; 3 → task failed
skill     needs · run (commands batch or closed loop) · verify via settle(read, ok, timeout, stable_s) · outcome
```

## Combat

```python
estimate.py
  arrival_s(here, row, ground)                  # inf = outside the account, not "far"
  pressure_hp_s(here, rows, prot, ground, shape, horizon)      # hp/s; a burst is burst_hp, not a rate
  act_cost_s(seconds, hp, price)                # time + blood, one number
  price(dhp)
  state_price_s(model, state)
  saved_s(price, before, after, cost_s) = price(before) − price(after) − cost_s     # only scoring rule
  also  burst_hp · fatal_chance(hp, damage, cap) · time_to_die_s · fight_cost · leaving_hp
        reaches_share(shape, mob) · damage_over(rate, s) · horizon_s()
  one account: horizon_s = engage.work_horizon_s; arrival truncates and returns on the same clock

kernel.choose(model, state) → Choice
  score = price(state) − price(action.effect(state)) − action.cost_s      # = estimate.saved_s
  model   price · actions · admissible · default · fault · assumptions
  action  name · cost_s · commitment_s(default=cost_s) · effect
  commitment(action)

  # horizon    commitment
  #
  # normal  a day      released when assumptions fail
  #   price = threat.expected_loss (the price of health)
  #
  # combat  seconds    the action's atomicity
  #   price = Fight.objective

threat.py
  aliases  row · arrival · pressure · burst_damage · time_to_die · fight_cost · leaving_cost · hide_ratio
  options  ignore(hp=0, leaves=press, blast_after) · fight · evade · eat · shield · reshape · wall_in
  owed(option, work_s) = leaves × work_s + blast_after        # Option.effect is this
  saves = estimate.saved_s(...) ; decide = kernel.choose(Field) ; no_go(state) → [(centre, r)]

fight_plan.Fight
  price = objective ; benefit = saved_s(objective, …)
  dps_here = pressure_hp_s(horizon→0) 
  death_risk/immediate_risk = fatal_chance(spare, damage_over(rate, s), cap)
  admissible  hp>0 · phase · commitment ≤ remaining(p10) · requires · needs · best_step can retreat
              · expected damage < hp − floor

hand-off         perception → hazard.py (environment, SAFETY) | fight_loop.offer (hostiles, TACTIC lease)
                 fight_loop.engage runs the answer; the survival brain yields while the lease stands

arbiter   REFLEX 0.05 · SAFETY 0.2 | TACTIC 1.0 · PLAN 10.0
```

## Cost

Survival: ticks — distance walked plus work, measured per skill key once there are three samples. Combat: seconds
and blood (`estimate`, `threat.hp_seconds`). No scoring outside the fight.

Assess only the architecture and the code, not the behaviour. Behaviour is an outcome, which should not be predicted.
