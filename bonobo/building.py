"""Machines from blueprints: spot finding, oriented placing, bottom-up builds (portal frames, shelters)."""
import math
import re

from . import api, blueprints, nav, world
from .beliefs import CONFIG as _PLAY
from .api import McError, NotAvailable, log
from .data import GROUPS, bare, mid
from .knowledge import members
from .skill import skill
from .skillcore import _collect_only, feet, snapshot, mine_cell
from .world import Inventory, Region, add


def _mod_at_least(version):
    def check(c):
        have = str(api.status().get("version", "0"))
        if tuple(int(x) for x in re.findall(r"\d+", have)[:3]) < tuple(int(x) for x in version.split(".")):
            raise NotAvailable(f"machines need mod >= {version} (running {have}); restart the game to load it")
    return check


def _open_container(pos):
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=60)
    if r["status"] != "succeeded" or r["result"].get("screen") in (None, "none"):
        raise McError(f"could not open the container at {pos}: {r['message']}")


def _empty_container_slot():
    for s in world.container()["slots"]:
        if s["owner"] != "player" and s["id"] == "minecraft:air":
            return s["slot"]
    raise NotAvailable("container is full")


def _machine_roles(machine):
    bp = blueprints.REGISTRY[machine["blueprint"]]
    return {part.role: pos for pos, part, _, _ in blueprints.placed(bp, tuple(machine["origin"]), machine["turns"])
            if part.role}


def _go_to_machine(ctx, machine):
    bp = blueprints.REGISTRY[machine["blueprint"]]
    access = blueprints.access_spot(bp, tuple(machine["origin"]), machine["turns"])
    if not nav.go_to(access, ctx.policy, range_=2, attempts=2):
        raise NotAvailable(f"{machine['name']} not reachable")


def spot_options(bp, near, region, policy, radius=8, body=None):
    """Pure: [(prepare cost, origin, turns, prepare)] for building `bp` around `near`, cheapest first.

    Ground that is already perfect — every cell free, every bottom cell on solid ground — is what this used to
    demand, and on real terrain that is rare: "no clear spot for shelter within 6 blocks" in a forest, with a bag
    of blocks and a pickaxe in hand. But a patch of ground is something you MAKE. A sapling in the way is one
    break; a dip under a wall is one placed block; a hillside is more work than walking somewhere else.

    So levelling is priced rather than demanded, in blocks of work, and a ready spot still wins because it costs
    nothing. `prepare` is what has to happen first: ("break", cell) and ("fill", cell), in the order to do them.
    Lava, water and bedrock are not work: those spots are simply not offered.
    """
    nx, ny, nz = near
    budget = int(_PLAY["build"]["max_prepare_blocks"])
    occupied = set()
    if body is not None:
        bx, by, bz = body
        occupied = {(bx + dx, by + dy, bz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) for dy in (0, 1)}

    def clearable(c):
        """None when the cell is already free, ("break", c) when it can be made free, False when it cannot."""
        if not region.inside(c) or region.hazard(c) or c in occupied:
            return False
        if not region.solid(c):
            return None
        if region.unbreakable(c) or c in policy.protected or region.player_made(c):
            return False
        return ("break", c)

    def standable(c):
        """None when the cell already holds weight, ("fill", c) when a block can be put there, False otherwise."""
        if not region.inside(c) or region.hazard(c):
            return False
        return None if region.solid(c) else ("fill", c)

    out = []
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            for dy in (0, 1, -1, 2, -2):
                origin = (nx + dx, ny + dy, nz + dz)
                for turns in range(4):
                    prepare = _prepare_for(bp, origin, turns, clearable, standable)
                    if prepare is None or len(prepare) > budget:
                        continue
                    out.append((len(prepare), math.dist(origin, near), origin, turns, tuple(prepare)))
                    break
    out.sort()
    return [(cost, origin, turns, prepare) for cost, _d, origin, turns, prepare in out]


def _prepare_for(bp, origin, turns, clearable, standable):
    """The breaks and fills this spot needs, or None when it cannot be made into a spot at all.

    Breaks first, then fills: the floor goes in under walls that are no longer buried.
    """
    cells = blueprints.placed(bp, origin, turns)
    clear = blueprints.clear_cells(bp, origin, turns)
    breaks, fills = [], []
    for c in [pos for pos, *_ in cells] + list(clear):
        got = clearable(c)
        if got is False:
            return None
        if got:
            breaks.append(got)
    bottom = [pos for pos, part, *_ in cells if part.offset[1] == 0] + [c for c in clear if c[1] == origin[1]]
    for pos in bottom:
        got = standable(add(pos, (0, -1, 0)))
        if got is False:
            return None
        if got:
            fills.append(got)
    access = blueprints.access_spot(bp, origin, turns)
    for c in (access, add(access, (0, 1, 0))):
        got = clearable(c)
        if got is False:
            return None
        if got:
            breaks.append(got)
    got = standable(add(access, (0, -1, 0)))
    if got is False:
        return None
    if got:
        fills.append(got)
    seen, ordered = set(), []
    for item in breaks + fills:
        if item[1] not in seen:
            seen.add(item[1])
            ordered.append(item)
    return ordered


def find_machine_spot(bp, near, policy, radius=8, body=None):
    """Nearest origin + rotation around `near` that can be built on, with what must be done to the ground first.

    Returns (origin, turns); `plan_machine_spot` returns the preparation with it. The region is read here, and the
    choosing is `spot_options` — pure, so what counts as buildable ground can be tested without a world.
    """
    origin, turns, _prepare = plan_machine_spot(bp, near, policy, radius=radius, body=body)
    return origin, turns


def plan_machine_spot(bp, near, policy, radius=8, body=None):
    """(origin, turns, prepare) for the cheapest spot: what to build on and what to do to the ground first."""
    nx, ny, nz = near
    height = max(p.offset[1] for p in bp.parts) + 2
    region = Region((nx - radius - 3, ny - 4, nz - radius - 3), (nx + radius + 3, ny + height + 3, nz + radius + 3))
    options = spot_options(bp, near, region, policy, radius=radius, body=body)
    if not options:
        raise NotAvailable(f"no ground for {bp.name} within {radius} blocks, and none that could be made")
    _cost, origin, turns, prepare = options[0]
    return origin, turns, prepare


def prepare_spot(ctx, prepare):
    """Do what the ground needs before a build: break what is in the way, fill what nothing stands on.

    One place, for every blueprint. The blocks come out of the same bag the build uses, so this runs before the
    materials are spent rather than after."""
    if not prepare:
        return
    log(f"   levelling the spot: {sum(1 for k, _c in prepare if k == 'break')} to break, "
        f"{sum(1 for k, _c in prepare if k == 'fill')} to fill")
    for kind, c in prepare:
        if kind == "break":
            mine_cell(ctx.policy, c, wait=60)
        else:
            block = next((b for b in GROUPS["building"] if Inventory().usable(b)), None)
            if block is None:
                raise NotAvailable("nothing left to fill the ground with")
            place_oriented(block, c, None, None)


def resolve_item(token):
    """A concrete held item for a blueprint token: group tokens ("stone", "door") pick the largest held stack."""
    if token not in GROUPS:
        return mid(token)
    inv = Inventory()
    held = [m for m in members(token) if inv.usable(m)]
    if not held:
        raise NotAvailable(f"no {token} in the inventory")
    return max(held, key=inv.usable)


def block_matches(name, token):
    name = bare(name)
    if mid(token) == "minecraft:torch":
        return name in ("torch", "wall_torch")
    return name in {bare(m) for m in members(token)}


def place_oriented(ctx, pos, token, facing=None, against=None, either_way=False):
    """Place a blueprint part and verify its `facing` state. A body-oriented block that comes out mirrored teaches
    the token's rule (memory) and is placed again; `either_way` accepts the opposite facing (doors)."""
    for attempt in range(2):
        item = resolve_item(token)
        task = {"type": "place", "item": item, "x": pos[0], "y": pos[1], "z": pos[2]}
        if against is not None:
            task["against"] = {"x": against[0], "y": against[1], "z": against[2]}
        elif facing is not None:
            yaw, pitch = blueprints.look_for(facing, ctx.mem.orientation_rule(token))
            task["yaw"] = yaw if yaw is not None else api.get("/state")["yaw"]
            task["pitch"] = pitch
        r = api.run(task, wait=90)
        if r["status"] != "succeeded":
            raise McError(f"placing {bare(item)} at {pos} failed: {r['message']}")
        if facing is None:
            return
        actual = Region(pos, pos, props=True).prop(pos, "facing")
        if actual is None or actual == facing or (either_way and actual == blueprints.OPPOSITE[facing]):
            return
        if against is None and attempt == 0 and actual == blueprints.OPPOSITE[facing]:
            rule = ctx.mem.orientation_rule(token)
            ctx.mem.set_orientation_rule(token, "away_from_player" if rule == "toward_player" else "toward_player")
            log(f"   {bare(item)} came out facing {actual}; learned the other orientation rule, placing again")
            api.run({"type": "mine", "x": pos[0], "y": pos[1], "z": pos[2], "collect": True, **_collect_only([item]), "requireDrops": True},
                    wait=60)
            continue
        raise McError(f"{bare(item)} at {pos} faces {actual}, wanted {facing}")


def materials_missing(bp):
    inv = Inventory()
    return {bare(k): n - inv.usable(k) for k, n in blueprints.materials(bp).items() if inv.usable(k) < n}


def _build_parts(ctx, bp, origin, turns):
    """Place every part bottom-up (list order within a layer), then verify the block ids."""
    cells = sorted(blueprints.placed(bp, origin, turns), key=lambda t: t[0][1])
    access = blueprints.access_spot(bp, origin, turns)
    done_region = Region(tuple(min(p[0][i] for p in cells) for i in range(3)),
                         tuple(max(p[0][i] for p in cells) for i in range(3)))
    # Leaves and vines around the build block the line of sight to the faces we must click (the portal's top row
    # failed under a birch canopy): clear them from the build box and the space in front of it first.
    lo = tuple(min(min(p[0][i] for p in cells), access[i]) - 1 for i in range(3))
    hi = tuple(max(max(p[0][i] for p in cells), access[i]) + 1 for i in range(3))
    around = Region(lo, hi)
    foliage = [p for p, n in around.blocks.items()
               if (n.endswith("_leaves") or n in ("vine", "glow_lichen")) and p not in ctx.policy.protected]
    if foliage:
        log(f"   clearing {len(foliage)} leaves/vines around the {bp.name} build")
        api.run({"type": "mine_many", "collect": False, "requireDrops": False,
                 "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in foliage]}, wait=180)
    for pos, part, facing, against in cells:
        if block_matches(done_region.name(pos), part.item):
            continue   # resuming an interrupted build: this part is already in place
        # Stay at the build: the place task's own approach search is short (6 000 nodes), so a part 40 blocks away
        # (the agent wandered off between parts or rounds) failed with "no reachable face" — walk back first.
        if math.dist(feet(), pos) > 4.5 and not nav.go_to(access, ctx.policy, range_=1.5, attempts=1):
            raise api.NavFailed(f"can't get back to the {bp.name} build at {origin}")
        if pos[1] - feet()[1] >= 2:
            # The only face to click (the top of the part below, at y = pos.y) must be below the eye (feet + 1.62):
            # stand on the access spot and pillar straight up until the feet are at pos.y - 1. (Stopping one lower
            # left the eye at 121.6 under a face at 122: still "no reachable face".)
            f = feet()
            if (f[0], f[2]) != (access[0], access[2]):
                api.run({"type": "goto", "x": access[0], "y": f[1], "z": access[2], "range": 0.3, "partial": False},
                        wait=20)
            for _ in range(6):
                if pos[1] - feet()[1] < 2:
                    break
                block = resolve_item("building")
                r = api.run({"type": "pillar", "item": block}, wait=20)
                if r["status"] != "succeeded" and "headroom" in r["message"]:
                    # Leaves or a branch over the pillar spot: clear the cell above the head (never a protected or
                    # frame cell), then pillar again.
                    fx, fy, fz = feet()
                    above = (fx, fy + 2, fz)
                    if above in ctx.policy.protected:
                        raise McError(f"can't clear {above} above the pillar (protected)")
                    api.run({"type": "mine", "x": above[0], "y": above[1], "z": above[2], "collect": False,
                             "requireDrops": False}, wait=30)
                    r = api.run({"type": "pillar", "item": block}, wait=20)
                if r["status"] != "succeeded":
                    raise McError(f"couldn't pillar up to reach {pos}: {r['message']}")
                yield feet()
        place_oriented(ctx, pos, part.item, facing, against, part.either_way)
        yield pos
    lo = tuple(min(p[0][i] for p in cells) for i in range(3))
    hi = tuple(max(p[0][i] for p in cells) for i in range(3))
    region = Region(lo, hi)
    wrong = [(pos, bare(part.item)) for pos, part, *_ in cells if not block_matches(region.name(pos), part.item)]
    if wrong:
        raise McError(f"{bp.name} incomplete: {wrong}")


@skill(pre=[_mod_at_least("0.1.14")], verify=lambda c: c.result is not None, budget=900, stall=120)
def build_blueprint(ctx, name, near):
    """Build a machine from blueprints.REGISTRY near `near`: clear spot, bottom-up, oriented, verified, remembered."""
    bp = blueprints.REGISTRY[name]
    builds = ctx.mem.data.setdefault("builds", {})
    started = builds.get(name)
    if started and started.get("dimension") == ctx.dimension:
        # Resume the site already started: a new site every attempt left three half-built frames (4 obsidian lost).
        origin, turns = tuple(started["origin"]), started["turns"]
    else:
        missing = materials_missing(bp)
        if missing:
            raise NotAvailable(f"missing materials for {name}: {missing}")
        origin, turns, prepare = plan_machine_spot(bp, tuple(near), ctx.policy, body=feet())
        prepare_spot(ctx, prepare)
        builds[name] = {"origin": list(origin), "turns": turns, "dimension": ctx.dimension}
        ctx.mem.save()
    log(f"building {name} at {origin} (rotation {turns})")
    if not nav.go_to(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.5, attempts=2):
        raise api.NavFailed(f"can't reach the build spot for {name}")
    yield from _build_parts(ctx, bp, origin, turns)
    if "portal" in bp.tags:
        from . import fluids
        nav.go_to(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.0, attempts=1)
        fluids.light_portal(ctx, origin, turns)
    machine = ctx.mem.add_machine(name, origin, turns, ctx.dimension, bp.tags)
    ctx.mem.data.get("builds", {}).pop(name, None)
    ctx.mem.save()
    log(f"built {machine}")
    return machine


@skill(pre=[_mod_at_least("0.1.14")], verify=lambda c: c.result is not None, budget=360, stall=90, per_unit=60)
def build_shelter(ctx):
    """Put up the SHELTER hut (door, torch, room for a bed) near here and register it as a shelter site: one more
    safe place to sleep in the area being worked."""
    bp = blueprints.SHELTER
    missing = materials_missing(bp)
    if missing:
        raise NotAvailable(f"missing for a shelter: {missing}")
    origin, turns, prepare = plan_machine_spot(bp, feet(), ctx.policy, radius=6)
    prepare_spot(ctx, prepare)
    if not nav.go_to(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.5, attempts=2):
        raise api.NavFailed("can't reach the shelter spot")
    log(f"building a shelter at {origin} (rotation {turns})")
    yield from _build_parts(ctx, bp, origin, turns)
    interior = [list(c) for c in blueprints.clear_cells(bp, origin, turns) if c[1] == origin[1]]
    site = ctx.mem.add_site("shelter", origin, ctx.dimension, snapshot=snapshot(origin, half=2, down=1, up=3))
    ctx.mem.update_site(site["name"], interior=interior)
    log(f"shelter {site['name']} ready")
    return site["name"]
