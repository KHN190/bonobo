"""Collect dragon-behaviour tapes in bulk, fast. The model needs many perch cycles, breath clouds and flight paths before it can say anything about how long a phase lasts or where the breath lands. Fighting the dragon for real produces one tape per five minutes and needs the fight skills to work; watching it does not. So this runs the dragon over and over with the player parked out of the way and `/tick sprint` driving the game as fast as the machine allows. What it gives: dragon phases, flight paths, breath clouds, enderman positions — the world's behaviour. What it does NOT give: our own damage model. The observer is protected so it survives unattended, which makes its health readings meaningless; damage has to come from real fights. Usage: dragon_data.py [CYCLES] [TICKS_PER_CYCLE]"""

import sys
import time

from .. import api, combat_tape           # noqa: E402

END = "minecraft:the_end"
# far enough not to be swept or breathed on, close enough that the perch stays in scan
OBSERVE = (40, 70, 0)
# sprint in chunks, draining between: the buffer holds 6000 ticks
CHUNK = 1200

def chat(cmd):
    api.post("/chat", {"message": "/" + cmd})
    time.sleep(0.25)

def in_end(cmd):
    chat(f"execute in {END} run {cmd}")

def park_observer():
    """Put the player in the End, out of the fight, and keep it alive unattended."""

    in_end(f"tp @p {OBSERVE[0]} {OBSERVE[1]} {OBSERVE[2]}")
    in_end("gamemode spectator @p")

# the bait on the fountain makes the dragon perch and use its sitting attacks (a spectator is no target); dropped in, its top read from where it lands
BAIT = (0, 72, 0)

def park_bait():
    """Put the player where the dragon will attack it, and keep it alive through phases that would kill it."""

    in_end("gamemode spectator @p")
    in_end(f"tp @p {BAIT[0]} {BAIT[1]} {BAIT[2]}")
    in_end("effect give @p minecraft:resistance 99999 4 true")
    in_end("effect give @p minecraft:regeneration 99999 4 true")
    in_end("effect give @p minecraft:fire_resistance 99999 1 true")
    in_end("gamemode survival @p")
    time.sleep(1.0)      # let it fall the last blocks onto the fountain before anything is recorded
    return bait_alive()

def bait_alive():
    """True when the bait is still in the End and able to be attacked."""

    s = api.get("/state")
    return s["dimension"] == END and not s.get("dead") and s["health"] > 0

def fresh_dragon():
    in_end("kill @e[type=minecraft:ender_dragon]")
    in_end("kill @e[type=minecraft:area_effect_cloud]")
    time.sleep(0.3)
    in_end("summon minecraft:ender_dragon 0 80 0 {DragonPhase:0}")
    time.sleep(0.5)

def cycle(n, ticks, tag="observe", park=None):
    """One recorded fight-length observation."""

    if park is not None and not bait_alive():
        park()
    fresh_dragon()
    tape = combat_tape.Tape(f"{tag}-{n:03d}")
    done = 0
    while done < ticks:
        step = min(CHUNK, ticks - done)
        start = tape.last_tick()
        chat(f"tick sprint {step}")
        # wait for the ticks themselves, not a quiet spell (that fired before the sprint started)
        deadline = time.time() + 120
        while tape.last_tick() - start < step and time.time() < deadline:
            tape.poll()
            time.sleep(0.3)
        tape.poll()
        done += step
    chat("tick rate 20")
    return tape.save(), len(tape.frames), tape.errors

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    bait = "--bait" in sys.argv
    cycles = int(args[0]) if args else 5
    ticks = int(args[1]) if len(args) > 1 else 3600              # 3 min of game time per cycle
    park = park_bait if bait else park_observer
    api.take_control()
    park()
    t0 = time.time()
    tag = "bait" if bait else "observe"
    for n in range(cycles):
        # re-park every cycle: a bait knocked off records nothing worth having
        park()
        path, frames, errors = cycle(n, ticks, tag)
        print(f"cycle {n}: {frames} frames → {path}" + (f" errors={errors[:1]}" if errors else ""), flush=True)
    in_end("kill @e[type=minecraft:ender_dragon]")
    chat("tick rate 20")
    print(f"{cycles} cycles in {time.time() - t0:.0f}s wall")

if __name__ == "__main__":
    main()
