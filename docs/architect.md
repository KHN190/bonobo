# Architecture

## Split

- jar (Anaka mod): per tick. Tasks (walk, mine, place, travel, attack…), the combat reflex (shield, deflect,
  counter), threat reading (`/entities` tti, impact), safety nets (drown, lava, fall clutch), packets.
- Python (bonobo): decisions. What to do next, how, when to stop; everything over HTTP (`docs/api.md`).

## Layers

One body, one exit (`arbiter.BODY`). Faster layer wins:

```
REFLEX    jar, every tick           shield / deflect / counter (policy from Python: POST /reflex)
SAFETY    hazard (L0)               lava, fire, drowning, falling, buried → rescue
TACTIC    perception → threat,      hostiles: fight, flee, wall in; holds a lease on the body
          fight_loop
MAINTAIN  reflexes                  eat, sleep, shelter, recover items, empty bag, unstuck, jobs
PLAN      needs, tasks, prepare     upkeep needs; the queue (tasks.json) head; idle: PREPARE, then MILESTONES
```

Each brain round: every layer proposes (`arbiter.first_live`), `arbiter.arbitrate` picks one.
A lease holder owns the body; others' posts raise `FightHolds` and wait (`skill.fight_over`).

## Reconcile

- Goal done = world says so: `goals.remainder(goal, snap, mem)` = desired − world (`{}` met).
- Skill contract (`skill.skill`): needs, gives, remaining, verify, budget, stall. One runner judges it.
- Interrupted ≠ failed: never counted, never cooled. What happens next is `arbiter.RESUME_OF[source]`.
- Resume reads the world again (`skill.RESUME`: base, want, anchors), never a step index.
- Failure: `retry` counts (task, cause), cools the cause at the place (round start and where it failed).

## Plan

`decompose.decompose(inv, goal, cost)` → steps. `cost.Cost` prices them (walk + work, ticks).
`dispatch.execute` runs one step through its skill provider. Crafts in a row share one table sitting.

## Estimates (`estimate.py`)

```
arrival_s        when it can touch us
pressure_hp_s    health per second lost while it can act
act_cost_s       an action's health and time as one number
state_price_s    what the future costs from a state
saved_s          price(before) − price(after) − cost: the one decision rule
```

## Modules

```
api          HTTP client: requests, tasks, interrupts
arbiter      one body, priority by time scale, leases
brain        the round: propose, arbitrate, run, count
perception   5 Hz /state watcher: hurt, threats, interrupts
threat       who can hurt us, how soon; fight / walk away / wall in
fight_loop   the running fight (engagement, lease)
fight_plan   fight planner: what next, by when
estimate     the five estimated quantities
field        time-to-reach, what blocks do to it
kernel       planner kernel
hazard       L0 environment rescues
reflexes     maintenance triggers → actions; unstuck
needs        what must be planned before it is needed
goals        goals as data; remainder
tasks        the queue (tasks.json)
decompose    goal + bag → steps
planner      requirement resolution
solve        integer-program solver
actions      solver columns
cost         step prices
dispatch     step → skill
skill        contracts and the runner
skillcore    shared skill primitives
skills       verified routines on mod primitives
knowledge    recipes, sources, remainders
data         static game data
beliefs      play.toml numbers (value, mob)
combat_model what a combat tape means
combat_tape  reading the mod's combat tape
nav          movement through the game's pathfinder
roads        travelled legs as a graph
terrain      pure terrain planners
explore      finding what is out of range
world        snapshots, inventory, regions, searches
bag          what to carry, throw, store
memory       persistent world memory
jobs         things that run on their own (furnace, crops)
craft        crafting, smelting, stations
gather       mining, hunting, taking
wood         felling trunks
farming      crops, saplings, breeding
survive      light, food, water, shelter, sleep
store        chests, deposits, sites
building     blueprints placed
blueprints   machines as data
fluids       water, portals
nether       dimensions, fortress, stronghold
end          end portal, going through
dragon       dragon-fight contracts (to rewrite)
combat       bow, shield, blazes
brewing      potions
loot         chests we did not place
ui           screens with buttons
events       concise event log
intent       what the agent means (HUD)
review       review packet for Claude
tape         round tape, recorded and replayed
lifecycle    per-life state reset
retry        failure policy
fresh        which world; drop the last one's memory
paths        where files live
shapes       types
bench/       scenario bench: tables, vocab, runner
tools/       operator tools (reports, once, rounds, wake)
```

## Data (`MC_DATA`)

```
events.log / events.jsonl   what changed: tasks, goals, hurts, deaths, milestones, anomalies
detail.log                  the working: tasks, chains, rounds
autoplay.log                the run's own log
tasks.json                  the queue
world-notes.json            memory (per save)
milestones.json             milestones said (per save)
rounds.jsonl, tape-mem      round tape
bench/<row>/<time>/         failed-row reports (report.json)
```
