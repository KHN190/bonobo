# Planner Architecture

## Survival

```python
brain.round(snap)
  sit  = brain.situation(snap, costs, region)   # facts: state·columns·region·policy·route·mem·hp·inv_free·dark
  pool = candidates(ctx, snap, night)           # offer -> pool.admit (thaw relaxes cooldown only)
  pick = priority.choose(pool, committed, held=assumptions_hold(snap))

four doors (gates.py; every estimate comes out of these)
  gates.V(sit)            seconds from state to done = end dims (max) + run seconds (sum) + survival.expected_loss
  gates.takes_s(sit, w)   w in Go|Seek|Do|Run|Short -- how long one piece of work takes
  gates.p(sit, event)     yield·encounter·success·tool_use·tool_left·stale
  gates.marginal(res)     slot·slots·staleness·pickup·detour·blood -- marginal price of scarcity
  gates.exposure_s        = p(encounter) x k(blood); not a fifth door

pricing identity (value.worth_s; brain.worth_of_change is the only entry)
  worth(x) = sum_t g(t)·[V(s_t) - V(s_t+x, t+dt)]·dt/H - k(slots)
  each dim capped at min(worth, price); tool dim x Brain.uses_of; priors in play.toml and listed in unmeasured,
  measurements override via pseudo-counts (beliefs.cautious reads unmeasured pessimistically)

priority.Candidate
  score = success x benefit_s - cost_s # both in seconds

want.py (docs/api.md) -- the cerebrum's door
  offer(want, worth_s, ...)   wanted state + its seconds -> temporary terminal dim in gates.V
  stop(hard=)                 soft = value zeroed; hard = arbiter.BODY.preempt
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
        reaches_share(shape, mob) · damage_over(rate, s) · sunk_s(rate, elapsed, cost_s) · horizon_s()
  one account: horizon_s = engage.work_horizon_s; arrival truncates and returns on the same clock

kernel.choose(model, state) → Choice
  score = price(state) − price(action.effect(state)) − action.cost_s      # = estimate.saved_s
  model   price · actions · admissible · default · fault · assumptions
  action  name · cost_s · commitment_s(default=cost_s) · effect
  commitment(action)

  # horizon    commitment
  #
  # normal  a day      released when assumptions fail
  #   price = survival.expected_loss
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

combat → normal   pressure_hp_s → perception.pressure_now (model vs measured, worse) → brain.hp_tax_rate
                  → LiveCost.risk_s ; no_go → pool.gate_step
normal → combat   Intent.interrupt_cost_s = sunk_s → arbiter.preempt(worth_s=)

arbiter   REFLEX 0.05 · SAFETY 0.2 | TACTIC 1.0 · PLAN 10.0
```

## Cost and benefit

4 factors — V (seconds from a state to done), Δt (how long a piece of work takes), p (chance and rate: success,
encounter, staleness), κ (marginal price of scarcity: one bag slot, one second of staleness).

5 quantities — arrival time, pressure (hp/s), an action's blood and seconds, the price of health, the price of
a state.

Assess only the architecture and the code, not the behaviour. Behaviour is an outcome, which should not be predicted.

