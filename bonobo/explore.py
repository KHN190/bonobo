"""Finding things that aren't in range: surface first, spiral legs over known land."""
import math

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import api, nav
from .api import NotAvailable, log
from .data import RARE_SIGHTINGS, SEARCH_LEGS, SEARCH_LOOK_R, SEARCH_MOB_R, bare
from .skill import skill
from .world import biomes_around, feet
from .knowledge import FIND_AT, MINE, search_target, takeable_blocks

def _searched(c):
    """A search succeeded when it found one: walking without finding is not the product."""
    return bool(c.result)
from .world import entities, find

def surface_first(ctx, max_climb=90):
    """Animals, trees and land are on the surface: legs through rock at y=-20 got stuck every 30 s."""

    s = api.get("/state")
    if not _k.under_rock(s.get("skyLight", 15)):
        return
    x, y, z = s["blockX"], s["blockY"], s["blockZ"]
    log(f"   underground at y={y}: heading up to the surface first")
    nav.go_to((x, min(y + max_climb, 120), z), ctx.policy, range_=3, attempts=1)
    if _k.under_rock(api.get("/state").get("skyLight", 15)):
        raise api.NavFailed(f"could not reach the surface from y={y}")

LAND = ["grass_block", "dirt", "stone", "sand", "podzol", "snow_block"]
TRAVEL_RANGE = 2          # travel_to's arrival range unless asked
SEEK_RANGE = 3            # seek stops this near what it found
LOOK_MOBS, LOOK_BLOCKS = SEARCH_MOB_R, SEARCH_LOOK_R       # how far one look sees: /entities and /find radii

CANOPY = ("leaves", "_log")      # a tree top is sky to a search, not ground to stand on


def surface_cell(name, solid, x, z, top, bottom):
    """Pure: the standing y under open sky in column (x, z): the first ground from `top` down (a canopy passed
    through), feet and head clear; None when the column has none or its first ground is covered."""
    for y in range(top, bottom - 1, -1):
        if solid((x, y, z)) and not name((x, y, z)).endswith(CANOPY):
            return y + 1 if not solid((x, y + 1, z)) and not solid((x, y + 2, z)) else None
    return None


def _ground(tx, tz, y, surface=True, top=None):
    """The y to walk to at (tx, tz). A surface search: the open-sky cell over the known land nearest it (a buried
    hit's surface, never its y+1). An underground one: known land, else the column's ground near `y` (a cave floor)."""
    from .world import Region
    land = [h for h in find(LAND, radius=LOOK_BLOCKS, limit=60) if math.dist((h["x"], h["z"]), (tx, tz)) <= 16]
    if surface:
        near = min(land, key=lambda h: math.dist((h["x"], h["z"]), (tx, tz))) if land else None
        x, z = (near["x"], near["z"]) if near else (tx, tz)
        top = top if top is not None else y + LOOK_BLOCKS
        col = Region((x, y - LOOK_BLOCKS, z), (x, top + 2, z))
        got = surface_cell(col.name, col.solid, x, z, top, y - LOOK_BLOCKS)
        return None if got is None else (x, got, z)
    if land:
        return tx, min(land, key=lambda h: math.dist((h["x"], h["z"]), (tx, tz)))["y"] + 1, tz
    col = Region((tx, y - 40, tz), (tx, y + 20, tz))
    got = nav.ground_in_column(col.solid, tx, tz, y, span=40)
    return None if got is None else (tx, got, tz)


def underground_search(kinds):
    """Pure: does a search for `kinds` go under the ground (an ore's band) — the one search that may dig?"""
    return any(band(k) is not None for k in kinds)


SKYLESS = ("minecraft:the_nether",)     # a column's top here is the roof: no open sky to stand under

def stand_in_look_range(stand_y, band_y, radius, can_dig):
    """Pure: a look can cover a section at `band_y` — from the ground stood on (within `radius`), or from a stand dug
    down to it (a dig-down stand counts when digging is allowed)."""
    return abs(stand_y - band_y) <= radius or bool(can_dig)

def _search(ctx, kinds, look, radius, legs):
    """Look, then walk to the nearest section not yet looked over for `kinds` at their own depth, and look again."""

    for _ in range(legs):
        here = feet()
        hits = look()
        ctx.mem.see_sections(ctx.dimension, here, radius, _by_kind(hits), kinds)
        if hits:
            return hits
        todo = ctx.mem.frontier(ctx.dimension, here, kinds, band)
        toward = search_target(kinds[0], {"feet": here, "biomes": biomes_around()})
        if toward is not None:
            col = toward
            todo = sorted(todo, key=lambda s: math.dist((s[1][0], s[1][2]), col))     # into the biome that holds it
        target, tried = None, 0
        deep = underground_search(kinds)
        surface = not deep and ctx.dimension not in SKYLESS
        for section, (tx, sy, tz) in todo[:4]:
            # a surface search stands under open sky; an underground one on a cave floor near the band
            cell = _ground(tx, tz, sy, surface=surface, top=max(sy, here[1]) + LOOK_BLOCKS)
            if cell is None:
                continue
            tx, ty, tz = cell
            if not stand_in_look_range(ty, sy, radius, can_dig=False):     # a leg's stand must see the band
                # the band lies past a look's reach from the column's ground (coal's y 48 under a y 200 floor) and no
                # stand down there is reachable: a skip for this search, never "looked over" (no look happened) —
                # before, every leg walked there and re-picked it (×6, 20 s)
                ctx.mem.skip_section(ctx.dimension, section)
                log(f"   looking for {bare(kinds[0])}: section {section} lies {abs(ty - sy)} blocks under the ground at "
                    f"({tx}, {ty}, {tz}), out of a look's reach with no stand down there: skipped")
                continue
            tried += 1
            log(f"   looking for {bare(kinds[0])}: heading to section {section} ({tx}, {ty}, {tz})")
            try:
                if nav.moved(nav.go_to((tx, ty, tz), ctx.policy, range_=6, attempts=1,
                                       purpose="explore_deep" if deep else "explore")):
                    target = (tx, ty, tz)          # a surface search walks; only an underground one digs
                    break
            except api.Overrun as e:
                raise api.Overrun(f"search for {bare(kinds[0])} overran: {e}", pos=None,
                                  remaining_s=e.remaining_s, spent=e.spent)
            # out of reach from here (walled in: the frontier lies past the walls) — the next candidate, never the
            # same one again next leg (a sealed bench arena walked into its walls six times, 20 s)
        if target is None:
            raise NotAvailable(f"no {bare(kinds[0])} found: searched "
                               f"{len(ctx.mem.section_map(ctx.dimension))} sections"
                               + (", the rest out of reach" if tried else "")
                               + ("" if todo else ", every section near looked over lately"))
        yield (target[0], target[2])
    hits = look()
    ctx.mem.see_sections(ctx.dimension, feet(), radius, _by_kind(hits), kinds)
    if not hits:
        raise NotAvailable(f"no {bare(kinds[0])} found: searched {len(ctx.mem.section_map(ctx.dimension))} sections "
                           f"in {legs} legs")
    return hits

def band(kind):
    """The y a kind is richest at (knowledge.FIND_AT), None for surface kinds."""

    k = bare(kind)
    item = next((tok for tok, (blocks, _t) in MINE.items() if k in blocks), None)
    return FIND_AT.get(item) if item else None

def _by_kind(hits):
    """{kind: [pos]} of a look's hits (/find blocks or /entities)."""
    out = {}
    for h in hits or ():
        out.setdefault(h.get("block") or h.get("type"), []).append((round(h["x"]), round(h["y"]), round(h["z"])))
    return out

@skill(gives=["state:seen"], remaining=_k.some_of(lambda c: c.args[1]), needs={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120,
       provides={"explore:mobs": lambda ctx, s: (list(s.detail["types"]),)})
def explore_for(ctx, types, legs=SEARCH_LEGS, leg=40):
    """Find entities of `types`: remembered sightings first, then the unsearched frontier (`_search`)."""

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

@skill(gives=["state:found"], remaining=_k.found(lambda c: c.args[1]), needs={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120,
       provides={"explore:blocks": lambda ctx, s: (list(s.detail["blocks"]),)})
def seek_blocks(ctx, blocks, legs=SEARCH_LEGS, leg=40):
    """Find a block type not in range: the unsearched frontier (`_search`), looking after every leg."""

    if not find(blocks, radius=LOOK_BLOCKS, limit=1):
        surface_first(ctx)

    def look():
        here = feet()
        return [h for h in find(blocks, radius=LOOK_BLOCKS, limit=5)
                if nav.reachable((h["x"], h["y"], h["z"]), ctx.policy, 2.0, feet=here)[0]]
    return (yield from _search(ctx, list(blocks), look, LOOK_BLOCKS, legs))

@skill(gives=["state:found"], remaining=_k.found(lambda c: c.args[1]), needs={}, start=lambda c: feet(), verify=_searched, budget=900, stall=120,
       provides={"seek": lambda ctx, s: (list(s.detail["kinds"]), s.detail.get("pos"))})
def seek(ctx, kinds, pos=None):
    """Go to where one of these is: the nearest in sight, else the spot memory named, else look for one (a spiral)."""

    hits = [h for h in find(kinds, radius=LOOK_BLOCKS, limit=8) if not ctx.blocked((h["x"], h["y"], h["z"]))][:1]
    mobs = [] if hits else [m for m in entities(LOOK_MOBS, list(kinds)) if not ctx.blocked((m["id"], 0, 0))]
    if hits:
        target = (hits[0]["x"], hits[0]["y"], hits[0]["z"])
    elif mobs:
        _how, e = nav.chase(mobs[0]["id"], [mobs[0]["type"]], ctx.policy, SEEK_RANGE)
        if e is None:
            return explore_for(ctx, list(kinds))       # walked off out of sight: look for one again
        target = (round(e["x"]), round(e["y"]), round(e["z"]))
    elif pos:
        target = tuple(pos)
    elif any(k.startswith("minecraft:") and "_" not in k.split(":")[-1] for k in kinds):
        return explore_for(ctx, list(kinds))      # an animal: look for it where animals are
    else:
        return seek_blocks(ctx, list(kinds))
    nav.arrive(target, ctx.policy, range_=SEEK_RANGE)
    ctx.mem.note_here(kinds[0], target, ctx.dimension)     # standing at one: `at:<kind>` for the next plan
    return [target]

@skill(gives=["state:there"], remaining=_k.near(lambda c: c.args[1], lambda c: c.args[2] if len(c.args) > 2 else TRAVEL_RANGE), needs={}, provides={"goto": lambda ctx, s: (tuple(s.detail["pos"]), s.detail.get("range", TRAVEL_RANGE))}, budget=900, stall=120,
       verify=lambda c: nav.there(api.get("/state"), tuple(c.args[1]), c.args[2] if len(c.args) > 2 else TRAVEL_RANGE))
def travel_to(ctx, pos, range_=TRAVEL_RANGE):
    """Be at `pos` (within `range_`): walk, dig and bridge there leg by leg (`nav.arrive`)."""
    return nav.arrive(tuple(pos), ctx.policy, range_=range_)

def approach_policy(policy):
    """Movement for chasing mobs: walk, swim, bridge — never dig (animals move)."""
    import dataclasses
    return dataclasses.replace(policy, allow_dig=False)

# what the travel scan notes: the nearest of each kind in a look (LOOK_BLOCKS), takeable and rare blocks, animals in sight
SCAN_BLOCKS = {"tree": ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"],
               "water": ["water"], "lava": ["lava"], "iron": ["iron_ore", "deepslate_iron_ore"]}
_ALIAS = {"tree", "water", "lava"}      # scan kinds noted by their own name; the rest by the block that was hit
SCAN_MOBS = ("minecraft:sheep", "minecraft:cow", "minecraft:pig", "minecraft:chicken")

def unknown(mem, dimension, names):
    """Of `names`, those memory holds no live note of here: the only ones a look asks the world about."""

    return [n for n in names if not any(mem.seen(v, dimension) for v in variants(n))]

def variants(block):
    """Pure: the blocks that are the same find as `block` (the ore and its deepslate form)."""

    return next((list(blocks) for blocks, _tier in MINE.values() if block in blocks), [block])

def note_around(mem, dimension, here):
    """Note resources in sight while travelling (trees, water, lava, ores, takeable and rare blocks, animals) for later searches."""

    looked_blocks, seen_blocks = [], []
    try:
        for kind, blocks in SCAN_BLOCKS.items():
            if not unknown(mem, dimension, [kind] if kind in _ALIAS else blocks):
                continue           # already held in memory: looking again is a search, not a sighting
            looked_blocks += blocks
            hits = find(blocks, radius=LOOK_BLOCKS, limit=1)
            seen_blocks += hits
            if hits:
                h = hits[0]
                mem.note_seen(kind if kind in _ALIAS else h["block"], (h["x"], h["y"], h["z"]), dimension)
        rare = unknown(mem, dimension, list(RARE_SIGHTINGS))
        looked_blocks += rare
        for h in (find(takeable_blocks(), radius=LOOK_BLOCKS, limit=16) or []) + \
                ((find(rare, radius=LOOK_BLOCKS, limit=8) or []) if rare else []):
            seen_blocks.append(h)
            mem.note_seen(h["block"], (h["x"], h["y"], h["z"]), dimension)
        mobs = entities(LOOK_MOBS, list(SCAN_MOBS))
        for e in mobs:
            mem.note_seen(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), dimension)
        # what this look covered, per section, for the frontier
        mem.see_sections(dimension, here, LOOK_BLOCKS, _by_kind(seen_blocks + mobs), looked_blocks + list(SCAN_MOBS))
    except api.McError as e:
        api.swallowed("scan_resources: looking around", e)
