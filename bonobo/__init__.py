"""bonobo (package bonobo) — strategy for playing Minecraft through the Agent Bridge mod (which only
executes).

Layers (each only imports the ones above it):
    api      HTTP client, task running, stuck/timeout detection. No strategy.
    data     Static game knowledge: recipes, tiers, drops, block sets.
    world    Snapshots of player/inventory, block regions, searches, time helpers.
    memory   Persistent world memory: sites (home/shelters), sightings, veins, stations, nights.
    nav      Movement policy: walking, tunnelling, escaping, digging down.
    actions  Bounded routines that need their inputs present (craft, smelt, place, light, build, repair).
    gather   Getting raw resources (logs, stone, ore, food, wool), exploring.
    planner  Turns a need into a budgeted step plan and executes it.
    brain    Reflex guards + evening/shelter mode + utility-scored goals; the autoplay loop.
"""
