# bonobo

A Minecraft agent that plays through [Anaka](https://github.com/KHN190/anaka) — a client-side Fabric
mod exposing a small local HTTP API. The mod moves the player with ordinary inputs; **every decision lives here**.

Nothing in this repository can teleport, spawn items or break blocks instantly, because the mod offers no way to.
The agent walks with real physics, mines with real break progress, and a server sees an ordinary player.

## The split

| | |
|---|---|
| **Mod** (other repo) | Perception and execution. Reads the world, runs tasks, reports what happened. No strategy. |
| **This package** | Everything else: what to do, when, and what to do when it goes wrong. |

The line is load-bearing. A "walk to this block and place a bed" task belongs in the mod; "place a bed *now*, because
the dragon is perched and the window is 4.95 seconds long" belongs here.

## Layout

```
bonobo/
  brain.py        one round: perceive, score the goal pool, run the winner
  priority.py     the scoring — every goal competes on one formula
  skill.py        the skill contract: preconditions, done test, budget, stall detector
  skills.py       gathering, crafting, mining, building
  nav.py          movement, digging down, bridging, water clutch
  perception.py   the ~5 Hz danger loop that interrupts a running skill
  planner.py      recursive "what do I need to make this" resolution
  memory.py       world notes: home, machines, sites, deaths
  route.py        the speedrun route as data — ordered segments with budgets
  fight_plan.py   the dragon fight planner: hard vetoes, then seconds-to-kill
  bunker.py       fight geometry: the tunnel, the firing cell, the retreat cell
  combat_model.py time-to-impact, phase statistics, window analysis
  recovery.py     trigger → fixed action, with a default that always answers
  paths.py        the only module that knows a filesystem layout
  fight.toml      every tunable number for the dragon fight
tests/            offline tests: pure functions and recorded decision replays
```

## Running it

Requires Python 3.11+ (for `tomllib`), the mod installed, and a world open.

```sh
export MC_INSTANCE="$HOME/path/to/your/minecraft/instance"   # where config/agent-bridge.json lives
python3 mc.py state          # look at the world
python3 mc.py plan           # what it thinks it should do
python3 mc.py autoplay       # let it play
python3 mc.py release        # give the controls back
```

### Configuration

| Variable | Meaning | Default |
|---|---|---|
| `MC_INSTANCE` | Minecraft instance directory — the API token and logs are read from it | none; must be set |
| `MC_API` | the mod's HTTP endpoint | `http://127.0.0.1:27599` |
| `MC_DATA` | where runtime data is written | `$XDG_DATA_HOME/bonobo` |

Runtime data — world memory, decision recordings, bench results, combat tapes — is written outside the package and
is not tracked here.

## Testing

Most of this is testable without the game, and most of it is tested that way:

```sh
python3 -m unittest discover tests     # pure functions, geometry, the planner, recorded replays
python3 mc.py decide diff              # replay recorded decisions against the current code
python3 mc.py scenario run NAME        # one scenario in a disposable test world
```

The fight planner in particular is a pure function of a state dictionary: a test constructs a state, asserts which
intent comes back, and never opens Minecraft.

## Design rules

Written down because every failure so far broke one:

1. **Seeing is not arriving.** A block 48 blocks away that the scan can see is not a block the agent has reached.
2. **Estimates never touch the world.** Cost functions read memory and inventory, never the game.
3. **A contract may only demand what the skill controls.** A skill that cannot make it true cannot be asked for it.
4. **Partial success is progress.** Four of five blocks mined is not a failure to retry from scratch.
5. **Fix the class, not the case.** Name what went wrong, find its siblings, fix them in one place.
6. **Tests use the production geometry.** A test that invents its own heights only proves things about its own
   heights — one did, and the reach numbers it blessed were a block off.

## Licence

MIT.
