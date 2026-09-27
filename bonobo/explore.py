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


LAND = ["grass_block", "dirt", "stone", "sand", "podzol", "snow_block"]
LOOK_MOBS, LOOK_BLOCKS = 64, 48       # how far one look sees: /entities and /find radii


def _ground(tx, tz, y):
    """The y to walk to at column (tx, tz): known land near it, else the column's own ground; None when neither is
    known (never our own height for a spot far off: from a hilltop every leg pointed into mid-air)."""
    land = [h for h in find(LAND, radius=48, limit=60) if math.dist((h["x"], h["z"]), (tx, tz)) <= 16]
    if land:
        return min(land, key=lambda h: math.dist((h["x"], h["z"]), (tx, tz)))["y"] + 1
    from .world import Region
    col = Region((tx, y - 40, tz), (tx, y + 20, tz))
    return nav.ground_in_column(col.solid, tx, tz, y, span=40)


def _search(ctx, kinds, look, radius, legs):
    """Look, then walk to the nearest section not yet looked over for `kinds` (memory.frontier over the section map,
    at each kind's own depth: `band`), and look again. Every look marks the sections it covered, with what it saw:
    an interrupted search resumes by choosing the frontier again (no walk repeated), and another task's looks count
    for these kinds too."""
    for _ in range(legs):
        here = feet()
        hits = look()
        ctx.mem.see_sections(ctx.dimension, here, radius, _by_kind(hits), kinds)
        if hits:
            return hits
        todo = ctx.mem.frontier(ctx.dimension, here, kinds, band)
        target = None
        for section, (tx, sy, tz) in todo[:4]:
            ty = _ground(tx, tz, sy)          # a cave floor counts: the column's ground near the band's height
            if ty is not None:
                target = (tx, ty, tz)
                break
        if target is None:
            raise NotAvailable(f"no {bare(kinds[0])} found: searched "
                               f"{len(ctx.mem.section_map(ctx.dimension))} sections"
                               + ("" if todo else ", every section near looked over lately"))
        log(f"   looking for {bare(kinds[0])}: heading to section {section} ({target[0]}, {target[1]}, {target[2]})")
        nav.go_to(target, ctx.policy, range_=6, attempts=1, purpose="explore")    # looking: walk, never dig
        yield (target[0], target[2])
    hits = look()
    ctx.mem.see_sections(ctx.dimension, feet(), radius, _by_kind(hits), kinds)
    if not hits:
        raise NotAvailable(f"no {bare(kinds[0])} found: searched {len(ctx.mem.section_map(ctx.dimension))} sections "
                           f"in {legs} legs")
    return hits


def band(kind):
    """The y a kind is richest at (knowledge.FIND_AT, through the MINE row whose blocks it is), None for the surface
    (animals, trees, anything without a depth)."""
    from .knowledge import FIND_AT, MINE
    k = bare(kind)
    item = next((tok for tok, (blocks, _t) in MINE.items() if k in blocks), None)
    return FIND_AT.get(item) if item else None


def _by_kind(hits):
    """{kind: [pos]} of a look's hits (/find blocks or /entities)."""
    out = {}
    for h in hits or ():
        out.setdefault(h.get("block") or h.get("type"), []).append((round(h["x"]), round(h["y"]), round(h["z"])))
    return out


@skill(gives={}, needs={}, speed={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
       provides={"explore:mobs": lambda ctx, s: (list(s.detail["types"]),)})
def explore_for(ctx, types, legs=6, leg=40):
    """Find entities of `types`: remembered sightings first, then the frontier of chunks not yet looked over for
    them (`_search`). Returns the matches; walking counts as progress, so only a body that stops moving stalls."""
    for kind in types:
        for s in sorted(ctx.mem.seen(kind, ctx.dimension), key=lambda s: math.dist(s["pos"], feet()))[:2]:
            nav.go_to(tuple(s["pos"]), ctx.policy, range_=8, attempts=1)
            found = entities(LOOK_MOBS, types)
            if found:
                return found
            yield None
    surface_first(ctx)
    yield None
    found = yield from _search(ctx, list(types), lambda: entities(LOOK_MOBS, types), LOOK_MOBS, legs)
    e = found[0]
    ctx.mem.note_seen(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), ctx.dimension)
    return found


@skill(gives={}, needs={}, speed={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
       provides={"explore:blocks": lambda ctx, s: (list(s.detail["blocks"]),)})
def seek_blocks(ctx, blocks, legs=6, leg=40):
    """Find a block type that isn't in range (trees, sand, clay): the frontier of chunks not yet looked over for it
    (`_search`), looking after every leg. Only what a walk can get to counts as found (nav.reachable, chop's own
    test): trees seen 100 blocks under a sky platform are not a find."""
    if not find(blocks, radius=LOOK_BLOCKS, limit=1):
        surface_first(ctx)

    def look():
        here = feet()
        return [h for h in find(blocks, radius=LOOK_BLOCKS, limit=5)
                if nav.reachable((h["x"], h["y"], h["z"]), ctx.policy, 2.0, feet=here)[0]]
    return (yield from _search(ctx, list(blocks), look, LOOK_BLOCKS, legs))


@skill(gives={}, needs={}, speed={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120, per_unit=150,
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


@skill(gives={}, needs={}, speed={}, provides={"goto": lambda ctx, s: (tuple(s.detail["pos"]), s.detail.get("range", 2))}, budget=900, stall=120,
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


def unknown(mem, dimension, names):
    """Of `names`, those memory holds no live note of here: the only ones a look around asks the world about (a
    noted diamond scanned again every 20 s was a search the bench counted: seen_store__noted)."""
    return [n for n in names if not any(mem.seen(v, dimension) for v in variants(n))]


def variants(block):
    """Pure: the blocks that are the same find as `block` — the ore and its deepslate form (knowledge.MINE's one
    row). A noted diamond_ore made deepslate_diamond_ore look unknown, and the look around scanned for it."""
    from .knowledge import MINE
    return next((list(blocks) for blocks, _tier in MINE.values() if block in blocks), [block])


def note_around(mem, dimension, here):
    """Map resources while travelling, so "where to find" starts from known places: the nearest tree, water, lava,
    iron and coal in 48 blocks, the takeable blocks (beds, chests…), the rare blocks and the animals in sight."""
    from .knowledge import takeable_blocks
    looked_blocks, seen_blocks = [], []
    try:
        for kind, blocks in SCAN_BLOCKS.items():
            if not unknown(mem, dimension, [kind] if kind in _ALIAS else blocks):
                continue           # already held in memory: looking again is a search, not a sighting
            looked_blocks += blocks
            hits = find(blocks, radius=48, limit=1)
            seen_blocks += hits
            if hits:
                h = hits[0]
                mem.note_seen(kind if kind in _ALIAS else h["block"], (h["x"], h["y"], h["z"]), dimension)
        rare = unknown(mem, dimension, list(RARE_SIGHTINGS))
        looked_blocks += rare
        for h in (find(takeable_blocks(), radius=48, limit=16) or []) + \
                ((find(rare, radius=48, limit=8) or []) if rare else []):
            seen_blocks.append(h)
            mem.note_seen(h["block"], (h["x"], h["y"], h["z"]), dimension)
        mobs = entities(48, list(SCAN_MOBS))
        for e in mobs:
            mem.note_seen(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), dimension)
        # What this look covered, section by section — what it asked about and what it saw — for the frontier.
        mem.see_sections(dimension, here, 48, _by_kind(seen_blocks + mobs), looked_blocks + list(SCAN_MOBS))
    except api.McError as e:
        api.swallowed("scan_resources: looking around", e)
