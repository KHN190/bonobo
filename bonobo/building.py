"""Machines from blueprints: spot finding, oriented placing, bottom-up builds (portal frames, shelters)."""
import math
import re

from . import api, blueprints, nav, world
from .beliefs import CONFIG as _PLAY
from .api import McError, NotAvailable, log
from .data import GROUPS, bare, mid
from .knowledge import members
from .skill import skill
from .skillcore import _collect_only, body_state, feet, snapshot, mine_cell, place
from .world import Inventory, Region, add


def _mod_at_least(version):
    def check(c):
        have = str(api.status().get("version", "0"))
        if tuple(int(x) for x in re.findall(r"\d+", have)[:3]) < tuple(int(x) for x in version.split(".")):
            raise NotAvailable(f"machines need mod >= {version} (running {have}); restart the game to load it")
    return check


def _open_container(pos):
    r = api.run({"type": "use", "x": pos[0], "y": pos[1], "z": pos[2]}, wait=60, awaits="the caller reads the opened container's slots next")
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
    if not nav.arrived(access, ctx.policy, range_=2, attempts=2):
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
    # A build already standing in part is resumed where it stands, before any fresh ground: the rest of it is the
    # cheapest work there is (and a fresh frame beside a half-cast one wastes the half).
    return ([(0, origin, turns, ()) for origin, turns in started_builds(bp, region, near)]
            + [(cost, origin, turns, prepare) for cost, _d, origin, turns, prepare in out])


def _free(region, c):
    return not region.solid(c) or region.name(c) == "nether_portal"


def started_builds(bp, region, near):
    """Pure: [(origin, turns)] of builds of `bp` already half standing in `region` or more, the most complete and then
    the nearest first. Only the blueprint's own items count (explicit ids, not group tokens like "stone"): a wall
    of cobblestone is not half a shelter. Its clear cells must be free, or it could never work."""
    own = [p for p in bp.parts if ":" in p.item]
    if not own:
        return []
    items = {bare(p.item) for p in own}
    seen, out = set(), []
    for pos, name in region.blocks.items():
        if name not in items:
            continue
        for part in own:
            if bare(part.item) != name:
                continue
            for turns in range(4):
                d = blueprints.rotate_offset(part.offset, turns)
                origin = (pos[0] - d[0], pos[1] - d[1], pos[2] - d[2])
                if (origin, turns) in seen:
                    continue
                seen.add((origin, turns))
                standing = sum(1 for c, q, *_ in blueprints.placed(bp, origin, turns)
                               if ":" in q.item and region.name(c) == bare(q.item))
                if standing * 2 >= len(own) and all(_free(region, c) for c in blueprints.clear_cells(bp, origin, turns)):
                    out.append((-standing, math.dist(origin, near), origin, turns))
    out.sort()
    return [(origin, turns) for _s, _d, origin, turns in out]


def portal_todo(bp, origin, turns, name):
    """Pure: (frame cells still to cast, whether it still needs lighting) for a portal at `origin`. `name(pos)` →
    block name. Empty frame: every cell, and light; all cast: light only; lit: nothing."""
    cast = [pos for pos, part, *_ in blueprints.placed(bp, origin, turns)
            if part.item == "minecraft:obsidian" and name(pos) != "obsidian"]
    lit = any(name(c) == "nether_portal" for c in blueprints.clear_cells(bp, origin, turns))
    return cast, not lit


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
    """Place a blueprint part and verify its `facing` state. The jar turns the body so the block's own placement rule
    gives `facing`; `either_way` accepts the opposite facing (doors)."""
    item = resolve_item(token)
    task = {"type": "place", "item": item, "x": pos[0], "y": pos[1], "z": pos[2]}
    if against is not None:
        task["against"] = {"x": against[0], "y": against[1], "z": against[2]}
    elif facing is not None:
        task["facing"] = facing
    r = api.run(task, wait=90, awaits="the placed block's `facing` is read back before the next part (a mirrored stair is re-done)")
    if r["status"] != "succeeded":
        raise McError(f"placing {bare(item)} at {pos} failed: {r['message']}")
    if facing is None:
        return
    actual = Region(pos, pos, props=True).prop(pos, "facing")
    if actual is None or actual == facing or (either_way and actual == blueprints.OPPOSITE[facing]):
        return
    raise McError(f"{bare(item)} at {pos} faces {actual}, wanted {facing}")


def materials_missing(bp):
    inv = Inventory()
    return {bare(k): n - inv.usable(k) for k, n in blueprints.materials(bp).items() if inv.usable(k) < n}


def blueprint_region(bp, origin, turns):
    """The box a build reads: every part, the access spot, and one block around them (foliage in the way)."""
    cells = [p for p, *_ in blueprints.placed(bp, origin, turns)]
    access = blueprints.access_spot(bp, origin, turns)
    lo = tuple(min(min(c[i] for c in cells), access[i]) - 1 for i in range(3))
    hi = tuple(max(max(c[i] for c in cells), access[i]) + 1 for i in range(3))
    return Region(lo, hi)


def blueprint_wrong(bp, origin, turns):
    """[(cell, wanted)] for every part the world does not show where the blueprint puts it. Empty = built."""
    cells = blueprints.placed(bp, origin, turns)
    lo = tuple(min(p[0][i] for p in cells) for i in range(3))
    hi = tuple(max(p[0][i] for p in cells) for i in range(3))
    region = Region(lo, hi)
    return [(pos, bare(part.item)) for pos, part, *_ in cells if not block_matches(region.name(pos), part.item)]


def blueprint_commands(state, args):
    """Pure: the whole build as one batch, from the access spot — clear the foliage in the way, then every part not
    yet in place, bottom-up, pillaring under the body wherever a part's only face is above the eye.

    `state` is `body_state` with `region` = `blueprint_region(...)`; the body
    is assumed to stand on the access spot (the skill walks there first). Items come out of `state["inv"]` as the
    batch spends them, so a group token picks a member there will still be some of.
    """
    bp, origin, turns = args
    region, inv, protected = state["region"], state["inv"], state["protected"]
    access = blueprints.access_spot(bp, origin, turns)
    spent = {}

    def item_for(token):
        if token not in GROUPS:
            item = mid(token)
        else:
            held = [m for m in members(token) if inv.usable(m) - spent.get(m, 0) > 0]
            if not held:
                raise NotAvailable(f"no {token} in the inventory")
            item = max(held, key=lambda m: inv.usable(m) - spent.get(m, 0))
        spent[item] = spent.get(item, 0) + 1
        return item

    tasks = []
    foliage = [p for p, n in region.blocks.items()
               if (n.endswith("_leaves") or n in ("vine", "glow_lichen")) and p not in protected]
    if foliage:
        tasks.append({"type": "mine_many", "collect": False, "requireDrops": False,
                      "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in foliage]})
    fx, fy, fz = state["feet"]
    for pos, part, facing, against in sorted(blueprints.placed(bp, origin, turns), key=lambda t: t[0][1]):
        if block_matches(region.name(pos), part.item):
            continue   # resuming an interrupted build: this part is already in place
        if pos[1] - fy >= 2:
            # The only face to click (the top of the part below) must be below the eye: stand on the access column
            # and pillar straight up until the feet are at pos.y - 1.
            if (fx, fz) != (access[0], access[2]):
                tasks.append({"type": "goto", "x": access[0], "y": fy, "z": access[2], "range": 0.3,
                              "partial": False})
                fx, fz = access[0], access[2]
            while pos[1] - fy >= 2:
                tasks.append({"type": "pillar", "item": item_for("building")})
                fy += 1
        task = {"type": "place", "item": item_for(part.item), "x": pos[0], "y": pos[1], "z": pos[2]}
        if against is not None:
            task["against"] = {"x": against[0], "y": against[1], "z": against[2]}
        elif facing is not None:
            task["facing"] = facing
        tasks.append(task)
    return tasks


def _build_state(ctx, bp, origin, turns):
    return body_state(ctx, blueprint_region(bp, origin, turns))


def _build_parts(ctx, bp, origin, turns):
    """Place every part bottom-up (list order within a layer), then verify the block ids.

    The batch (`blueprint_commands`) goes first, as one chain. What it could not do — a pillar with leaves over it,
    a body-oriented block that came out mirrored — is finished part by part below, where each placement is looked
    at before the next."""
    batch = blueprint_commands(_build_state(ctx, bp, origin, turns), (bp, origin, turns))
    # One chain per layer, bottom-up: a layer is the support of the next, so each is read back from the world before
    # the next is sent; a layer short of what it should hold hands over to the part-by-part finish below.
    for chunk in by_layer(batch):
        api.run_chain(chunk, stop_on_failure=True)
        yield feet()
        placed_ys = [t["y"] for t in chunk if t["type"] == "place"]
        if placed_ys and any(pos[1] == max(placed_ys) for pos, _item in blueprint_wrong(bp, origin, turns)):
            break
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
        # one mine_many task: every leaf in one send already
        api.run({"type": "mine_many", "collect": False, "requireDrops": False,
                 "blocks": [{"x": p[0], "y": p[1], "z": p[2]} for p in foliage]}, wait=180, awaits="the site cleared: the build reads the region after")
    for pos, part, facing, against in cells:
        if block_matches(done_region.name(pos), part.item):
            continue   # resuming an interrupted build: this part is already in place
        # Stay at the build: the place task's own approach search is short (6 000 nodes), so a part 40 blocks away
        # (the agent wandered off between parts or rounds) failed with "no reachable face" — walk back first.
        if math.dist(feet(), pos) > 4.5 and not nav.arrived(access, ctx.policy, range_=1.5, attempts=1):
            raise api.NavFailed(f"can't get back to the {bp.name} build at {origin}")
        if pos[1] - feet()[1] >= 2:
            # The only face to click (the top of the part below, at y = pos.y) must be below the eye (feet + 1.62):
            # stand on the access spot and pillar straight up until the feet are at pos.y - 1. (Stopping one lower
            # left the eye at 121.6 under a face at 122: still "no reachable face".)
            f = feet()
            if (f[0], f[2]) != (access[0], access[2]):
                api.run({"type": "goto", "x": access[0], "y": f[1], "z": access[2], "range": 0.3, "partial": False},
                        wait=20, awaits="the pillar below starts from where this step left the feet")
            for _ in range(6):
                if pos[1] - feet()[1] < 2:
                    break
                block = resolve_item("building")
                r = api.run({"type": "pillar", "item": block}, wait=20, awaits="each pillar step's height and 'headroom' answer decide the next (the fallback after blueprint_commands' batch, for what the batch could not place)")
                if r["status"] != "succeeded" and "headroom" in r["message"]:
                    # Leaves or a branch over the pillar spot: clear the cell above the head (never a protected or
                    # frame cell), then pillar again.
                    fx, fy, fz = feet()
                    above = (fx, fy + 2, fz)
                    if above in ctx.policy.protected:
                        raise McError(f"can't clear {above} above the pillar (protected)")
                    api.run({"type": "mine", "x": above[0], "y": above[1], "z": above[2], "collect": False,
                             "requireDrops": False}, wait=30, awaits="the head cell cleared before the next pillar step")
                    r = api.run({"type": "pillar", "item": block}, wait=20, awaits="each pillar step's height decides the next")
                if r["status"] != "succeeded":
                    raise McError(f"couldn't pillar up to reach {pos}: {r['message']}")
                yield feet()
        place_oriented(ctx, pos, part.item, facing, against, part.either_way)
        yield pos
    wrong = blueprint_wrong(bp, origin, turns)
    if wrong:
        raise McError(f"{bp.name} incomplete: {wrong}")


def by_layer(tasks):
    """Pure: a build's tasks split into chunks, one per height of the blocks it places, in order. What comes between
    two layers (a walk back, a pillar up) opens the next chunk; tasks after the last place stay in the last one."""
    chunks, cur, y = [], [], None
    for t in tasks:
        if t["type"] == "place":
            if y is not None and t["y"] != y:
                cut = max(i for i, c in enumerate(cur) if c["type"] == "place") + 1
                chunks.append(cur[:cut])
                cur = cur[cut:]
            y = t["y"]
        cur.append(t)
    if cur:
        chunks.append(cur)
    return chunks


def _build_args(ctx, s):
    """(blueprint, near) for a build step: where it asked, else home (never for a portal), else here."""
    at = s.detail.get("at")
    if at:
        return s.token, tuple(at)
    home = ctx.mem.home()
    return s.token, tuple(home["pos"]) if home and s.token != "nether_portal" else feet()


def _machine_built(ctx, name):
    """Is the machine this name was given standing in the world, part for part?"""
    m = next((m for m in ctx.mem.data.get("machines", ()) if m["name"] == name), None)
    return m is not None and not blueprint_wrong(blueprints.REGISTRY[m["blueprint"]], tuple(m["origin"]), m["turns"])


def _shelter_built(ctx, name):
    s = next((s for s in ctx.mem.data.get("sites", ()) if s["name"] == name), None)
    return s is not None and not blueprint_wrong(blueprints.SHELTER, tuple(s["pos"]), s.get("turns", 0))


def _blueprint_commands_for(state, args):
    """`commands` for build_blueprint(ctx, name, near): the batch where the build stands — a build already started
    (`state["started"]`, memory's `builds`), else the cheapest spot around `near` in `state["region"]`
    (`spot_options`), its levelling first: break what is in the way, fill what nothing stands on."""
    bp = blueprints.REGISTRY[args[0]]
    started = state.get("started")
    if started:
        return blueprint_commands(state, (bp, tuple(started["origin"]), started["turns"]))
    if state.get("region") is None:
        return []
    near = tuple(args[1]) if len(args) > 1 and args[1] is not None else tuple(state["feet"])
    options = spot_options(bp, near, state["region"], nav.Policy(protected=set(state.get("protected") or ())),
                           body=state.get("feet"))
    if not options:
        return []
    _cost, origin, turns, prepare = options[0]
    filler = next((b for b in GROUPS["building"] if state["inv"].count(b)), None)
    level = [nav.mine_task(c) if kind == "break" else {"type": "place", "item": filler, "x": c[0], "y": c[1], "z": c[2]}
             for kind, c in prepare if kind == "break" or filler]
    try:
        return level + blueprint_commands(state, (bp, origin, turns))
    except NotAvailable:
        return []


def _shelter_commands_for(state, args):
    """`commands` for build_shelter: the batch at the spot `state["spot"]` = (origin, turns)."""
    origin, turns = state["spot"]
    return blueprint_commands(state, (blueprints.SHELTER, origin, turns))


@skill(gives={}, needs={}, speed={}, pre=[_mod_at_least("0.1.14")], verify=lambda c: c.result is not None and _machine_built(c.args[0], c.result),
       commands=_blueprint_commands_for, budget=900, stall=120, provides={"build": lambda ctx, s: _build_args(ctx, s)})
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
    if not nav.arrived(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.5, attempts=2):
        raise api.NavFailed(f"can't reach the build spot for {name}")
    yield from _build_parts(ctx, bp, origin, turns)
    if "portal" in bp.tags:
        from . import fluids
        nav.arrived(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.0, attempts=1)
        fluids.light_portal(ctx, origin, turns)
    machine = ctx.mem.add_machine(name, origin, turns, ctx.dimension, bp.tags)
    ctx.mem.data.get("builds", {}).pop(name, None)
    ctx.mem.save()
    log(f"built {machine}")
    return machine


@skill(gives=["state:sheltered"], needs=blueprints.materials(blueprints.SHELTER), speed={}, pre=[_mod_at_least("0.1.14")],
       verify=lambda c: c.result is not None and _shelter_built(c.args[0], c.result),
       commands=_shelter_commands_for, budget=360, stall=90, per_unit=60,
       provides={"build:shelter": lambda ctx, s: (), "state:sheltered": lambda ctx, s: (),
                 "shelter:hut": lambda ctx, s: ()})
def build_shelter(ctx):
    """Put up the SHELTER hut (door, torch, room for a bed) near here and register it as a shelter site: one more
    safe place to sleep in the area being worked."""
    bp = blueprints.SHELTER
    missing = materials_missing(bp)
    if missing:
        raise NotAvailable(f"missing for a shelter: {missing}")
    origin, turns, prepare = plan_machine_spot(bp, feet(), ctx.policy, radius=6)
    prepare_spot(ctx, prepare)
    if not nav.arrived(blueprints.access_spot(bp, origin, turns), ctx.policy, range_=1.5, attempts=2):
        raise api.NavFailed("can't reach the shelter spot")
    log(f"building a shelter at {origin} (rotation {turns})")
    yield from _build_parts(ctx, bp, origin, turns)
    interior = [list(c) for c in blueprints.clear_cells(bp, origin, turns) if c[1] == origin[1]]
    site = ctx.mem.add_site("shelter", origin, ctx.dimension, snapshot=snapshot(origin, half=2, down=1, up=3))
    ctx.mem.update_site(site["name"], interior=interior, turns=turns)
    log(f"shelter {site['name']} ready")
    return site["name"]


# ---- the portal cast in place (the speedrun way: no obsidian carried, no diamond pickaxe)
_CAST = {}      # where the last frame was cast: what the verify looks at


def _portal_cast(c):
    from . import fluids
    return _CAST.get("origin") is not None and fluids.portal_lit(_CAST["origin"])


@skill(gives={}, speed={}, needs={"minecraft:water_bucket": 1, "minecraft:bucket": 1, "minecraft:flint_and_steel": 1, "building": 16},
       verify=_portal_cast, budget=900, stall=240, per_unit=600, provides={"cast:nether_portal": lambda ctx, s: ()})
def cast_portal(ctx):
    """Cast a Nether portal frame in place (no obsidian carried, no diamond pickaxe): pick the spot, and for each
    frame cell bottom-up wall it in with mould (`fluids.cast_frame_plan`), pour lava in, pour water on it, take the
    water back; break the mould inside the frame, light it. The lava comes from a carried lava bucket or the nearest
    source (`fluids._lava_bucket`)."""
    from . import fluids
    inv = Inventory()
    for item in ("minecraft:water_bucket", "minecraft:flint_and_steel"):
        if not inv.count(item):
            raise NotAvailable(f"casting a portal needs a {item.split(':')[1]}")
    block = nav.building_item()
    if not block:
        raise NotAvailable("no blocks to mould the frame with")
    bp = blueprints.NETHER_PORTAL
    here = feet()
    # A frame already standing in part is picked first (spot_options → started_builds), and only its missing
    # cells are cast: the rest of the job, not the whole of it again.
    origin, turns, prepare = plan_machine_spot(bp, here, ctx.policy, body=here)
    prepare_spot(ctx, prepare)
    _CAST.update(origin=origin)
    region = Region(add(origin, (-5, -2, -5)), add(origin, (5, 6, 5)))
    todo, unlit = portal_todo(bp, origin, turns, region.name)
    for pos, part, *_ in blueprints.placed(bp, origin, turns):
        if todo and part.item != "minecraft:obsidian" and not region.solid(pos):
            place(block, pos)            # the corners: what the lava is held against
    access = blueprints.access_spot(bp, origin, turns)
    placed = []
    for cell, mould in [(c, m) for c, m in fluids.cast_frame_plan(bp, origin, turns, region.solid) if c in todo]:
        fluids._lava_bucket(ctx, feet())
        nav.arrive(access, ctx.policy, range_=1.5)
        mould = [m for m in mould if not Region(m, m).solid(m)]
        # One chain, no round trips: mould, lava, water on it, set, the water back (fight_loop's batch mechanism).
        done = api.run_chain(
            [{"type": "place", "item": block, "x": m[0], "y": m[1], "z": m[2]} for m in mould]
            + [fluids.use_task("minecraft:lava_bucket", fluids.floor_aim(cell), True),
               fluids.use_task("minecraft:water_bucket", fluids.floor_aim(cell), True),
               {"type": "wait", "ticks": 10},
               fluids.use_task("minecraft:bucket", fluids.surface_aim(add(cell, (0, 1, 0))), False)])
        placed += mould
        if Region(cell, cell).name(cell) != "obsidian":
            bad = next((t["message"] for t in done if t["status"] != "succeeded"), "")
            raise McError(f"no obsidian formed at {cell} {bad}".rstrip())
        if not Inventory().count("minecraft:water_bucket"):
            fluids.fill_water_bucket(ctx)
        breaks = [nav.mine_task(m) for m in fluids.mould_to_break(bp, origin, turns, placed) if Region(m, m).solid(m)]
        if breaks:
            api.run_chain(breaks)
        yield cell
    if unlit:
        nav.arrive(access, ctx.policy, range_=1.0)
        fluids.light_portal(ctx, origin, turns)
    ctx.mem.add_machine("nether_portal", origin, turns, ctx.dimension, bp.tags)
    return origin
