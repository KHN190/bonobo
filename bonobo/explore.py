"""Finding things that aren't in range: surface first, spiral legs over known land."""
import math

from . import api, nav
from .api import log
from .data import bare
from .skill import skill
from .skillcore import feet
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


@skill(budget=900, stall=120, per_unit=150)
def explore_for(ctx, types, legs=6, leg=40):
    """Find entities of `types`: remembered sightings first, then an outward spiral over known land. Returns the
    matches (maybe empty); walking counts as progress, so only a body that stops moving stalls."""
    for kind in types:
        for s in sorted(ctx.mem.sightings(kind, ctx.dimension), key=lambda s: math.dist(s["pos"], feet()))[:2]:
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
            ctx.mem.add_sighting(e["type"], (round(e["x"]), round(e["y"]), round(e["z"])), ctx.dimension)
            return found
        x, y, z = feet()
        yield None
    return []


@skill(budget=900, stall=120, per_unit=150)
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
    return find(blocks, radius=48, limit=5)


def approach_policy(policy):
    """Movement for chasing mobs: walk, swim, bridge — no digging (animals move; tunnels toward them are waste)."""
    import dataclasses
    return dataclasses.replace(policy, allow_dig=False)
