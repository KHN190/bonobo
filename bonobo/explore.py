"""Finding things that aren't in range: surface first, spiral legs over known land."""
import math

from . import api, nav
from .api import NotAvailable, log
from .data import RARE_SIGHTINGS, bare
from .skill import skill
from .skillcore import feet




def _searched(c):
    """A search succeeded when it found one: walking without finding is not the product."""
    return bool(c.result)
from .world import entities, find


def surface_first(ctx, max_climb=90):
    """Animals, trees and land are on the surface: walking 80-block legs through rock at y=-20 got "stuck" every
    30 s. Under rock (sky light ≤ 4), tunnel/pillar straight up with travel until the sky is open."""
    s = api.get("/state")
    if s.get("skyLight", 15) > 4:
        return
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    log(f"   underground at y={y}: heading up to the surface first")
    nav.go_to((x, min(y + max_climb, 120), z), ctx.policy, range_=3, attempts=1)
    if api.get("/state").get("skyLight", 15) <= 4:
        raise api.NavFailed(f"could not reach the surface from y={y}")


@skill(start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
       provides={"explore:mobs": lambda ctx, s: (list(s.detail["types"]),)})
def explore_for(ctx, types, legs=6, leg=40):
    """Find entities of `types`: remembered sightings first, then an outward spiral over known land. Returns the
    matches (maybe empty); walking counts as progress, so only a body that stops moving stalls."""
    for kind in types:
        for s in sorted(ctx.mem.seen(kind, ctx.dimension), key=lambda s: math.dist(s["pos"], feet()))[:2]:
            nav.go_to(tuple(s["pos"]), ctx.policy, range_=8, attempts=1)
            found = entities(64, types)
            if found:
                return found
            yield None
    surface_first(ctx)
    yield None
    x, y, z = feet()
    for i in range(legs):
        dx, dz = [(1, 0), (0, 1), (-1, 0), (0, -1)][i % 4]
        length = leg * (i // 2 + 1)
        tx, tz = x + dx * length, z + dz * length
        # Only explore toward land: a leg ending in water strands the player (drowning risk, no path back).
        # /find only sees nearby blocks, so land 40+ blocks out is rarely "known": prefer known land, otherwise walk
        # (or boat) as far toward the leg's end as the walker gets. Drowning is covered by the mod and find_air.
        land = [h for h in find(["grass_block", "dirt", "stone", "sand", "podzol", "snow_block"], radius=48, limit=60)
                if math.dist((h["x"], h["z"]), (tx, tz)) <= 12]
        if land:
            ty = min(land, key=lambda h: math.dist((h["x"], h["z"]), (tx, tz)))["y"] + 1
        else:
            # Never reuse our own height for a spot 40 blocks away: from a hilltop at y 104 every leg pointed into
            # mid-air and travel answered "no route" for a whole slice. Read that column's ground instead; if even
            # that is unknown, skip this leg rather than walk at the sky.
            from .world import Region
            col = Region((tx, y - 40, tz), (tx, y + 20, tz))
            ground = nav.ground_in_column(col.solid, tx, tz, y, span=40)
            if ground is None:
                yield None
                continue
            ty = ground
        nav.go_to((tx, ty, tz), ctx.policy, range_=6, attempts=1)   # travel: one movement mechanism
        found = entities(64, types)
        if found:
            e = found[0]
            ctx.mem.note_seen(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), ctx.dimension)
            return found
        x, y, z = feet()
        yield None
    return []


@skill(start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
       provides={"explore:blocks": lambda ctx, s: (list(s.detail["blocks"]),)})
def seek_blocks(ctx, blocks, legs=6, leg=40):
    """Find a block type that isn't in range (trees, sand, clay): outward spiral over known land, checking /find
    after every leg. Returns the hits (maybe empty); walking counts as progress."""
    if not find(blocks, radius=48, limit=1):
        surface_first(ctx)
    x, y, z = feet()
    for i in range(legs):
        hits = find(blocks, radius=48, limit=5)
        if hits:
            return hits
        dx, dz = [(1, 0), (0, 1), (-1, 0), (0, -1)][i % 4]
        length = leg * (i // 2 + 1)
        tx, tz = x + dx * length, z + dz * length
        land = [h for h in find(["grass_block", "dirt", "stone", "sand", "podzol", "snow_block"], radius=48, limit=60)
                if math.dist((h["x"], h["z"]), (tx, tz)) <= 16]
        if land:
            ty = min(land, key=lambda h: math.dist((h["x"], h["z"]), (tx, tz)))["y"] + 1
        else:
            # Same rule as explore_for: our own height says nothing about a column 40 blocks away, and aiming at
            # mid-air makes travel answer "no route" every time.
            from .world import Region
            col = Region((tx, y - 40, tz), (tx, y + 20, tz))
            ground = nav.ground_in_column(col.solid, tx, tz, y, span=40)
            if ground is None:
                yield None
                continue
            ty = ground
        log(f"   looking for {bare(blocks[0])}: heading toward ({tx}, {tz})")
        nav.go_to((tx, ty, tz), ctx.policy, range_=6, attempts=1)
        x, y, z = feet()
        yield (x, z)
    hits = find(blocks, radius=48, limit=5)
    if not hits:
        # Said, not implied: "finished without reaching its goal" told nobody what was looked for or how far.
        raise NotAvailable(f"no {bare(blocks[0])} within 48 blocks of {legs} search legs "
                           f"(out to {leg * ((legs - 1) // 2 + 1)} blocks)")
    return hits


@skill(start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
       provides={"seek": lambda ctx, s: (list(s.detail["kinds"]), s.detail.get("pos"))})
def seek(ctx, kinds, pos=None):
    """Go to where one of these is: the nearest in sight, else the spot memory named, else look for one (a spiral).
    Returns [where it went], or what the look found."""
    hits = find(kinds, radius=48, limit=1)
    mobs = [] if hits else entities(64, list(kinds))
    if hits:
        target = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
    elif mobs:
        target = (round(mobs[0]["x"]), round(mobs[0]["y"]), round(mobs[0]["z"]))
    elif pos:
        target = tuple(pos)
    elif any(k.startswith("minecraft:") and "_" not in k.split(":")[-1] for k in kinds):
        return explore_for(ctx, list(kinds))      # an animal: look for it where animals are
    else:
        return seek_blocks(ctx, list(kinds))
    nav.arrive(target, ctx.policy, range_=3)
    ctx.mem.note_here(kinds[0], target, ctx.dimension)     # standing at one: `at:<kind>` for the next plan
    return [target]


@skill(provides={"goto": lambda ctx, s: (tuple(s.detail["pos"]), s.detail.get("range", 2))}, budget=900, stall=120,
       verify=lambda c: math.dist(feet(), c.args[1]) <= (c.args[2] if len(c.args) > 2 else 2) + 1)
def travel_to(ctx, pos, range_=2):
    """Be at `pos` (within `range_`): walk, dig and bridge there leg by leg (`nav.arrive`)."""
    return nav.arrive(tuple(pos), ctx.policy, range_=range_)


def approach_policy(policy):
    """Movement for chasing mobs: walk, swim, bridge — no digging (animals move; tunnels toward them are waste)."""
    import dataclasses
    return dataclasses.replace(policy, allow_dig=False)


# What the travel scan notes (memory.note_seen, kept by data.VOLATILITY): the nearest of each kind in 48 blocks,
# the takeable blocks, the rare blocks and the animals in sight.
SCAN_BLOCKS = {"tree": ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"],
               "water": ["water"], "lava": ["lava"], "iron": ["iron_ore", "deepslate_iron_ore"]}
_ALIAS = {"tree", "water", "lava"}      # scan kinds noted by their own name; the rest by the block that was hit
SCAN_MOBS = ("minecraft:sheep", "minecraft:cow", "minecraft:pig", "minecraft:chicken")


def note_around(mem, dimension):
    """Map resources while travelling, so "where to find" starts from known places: the nearest tree, water, lava,
    iron and coal in 48 blocks, the takeable blocks (beds, chests…), the rare blocks and the animals in sight."""
    from .knowledge import takeable_blocks
    try:
        for kind, blocks in SCAN_BLOCKS.items():
            hits = find(blocks, radius=48, limit=1)
            if hits:
                h = hits[0]
                mem.note_seen(kind if kind in _ALIAS else h["block"], (h["x"], h["y"], h["z"]), dimension)
        for h in (find(takeable_blocks(), radius=48, limit=16) or []) + \
                (find(list(RARE_SIGHTINGS), radius=48, limit=8) or []):
            mem.note_seen(h["block"], (h["x"], h["y"], h["z"]), dimension)
        for e in entities(48, list(SCAN_MOBS)):
            mem.note_seen(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), dimension)
    except api.McError as e:
        api.swallowed("scan_resources: looking around", e)
