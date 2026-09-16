"""Potions for the Nether and the End: water bottles, awkward potions (nether wart), fire resistance (magma cream).
A brewing stand's slots: 0–2 bottles, 3 ingredient, 4 fuel (blaze powder). Pure `brew_steps` is offline-tested."""
import time

from . import api, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, find

FIRE_RES_CHAIN = [("minecraft:nether_wart", "awkward"), ("minecraft:magma_cream", "fire_resistance")]


def brew_steps(have):
    """Pure: which ingredients still need brewing, given item counts `have`. Water bottles → awkward (nether wart)
    → fire resistance (magma cream). Returns [] when potions are already there, None when inputs are missing."""
    if have.get("minecraft:potion:fire_resistance", 0) >= 3:
        return []
    bottles = have.get("minecraft:potion:water", 0) + have.get("minecraft:potion:awkward", 0)
    if bottles < 3:
        return None
    steps = []
    if have.get("minecraft:potion:awkward", 0) < 3:
        if have.get("minecraft:nether_wart", 0) < 1:
            return None
        steps.append("minecraft:nether_wart")
    if have.get("minecraft:magma_cream", 0) < 1 or have.get("minecraft:blaze_powder", 0) < 1:
        return None
    steps.append("minecraft:magma_cream")
    return steps


@skill(budget=180, stall=60, per_unit=10)
def fill_bottles(ctx, count=3):
    """Fill glass bottles at water (use the bottle while looking at a water source)."""
    from . import fluids
    if Inventory().count("minecraft:glass_bottle") < 1:
        raise NotAvailable("no glass bottles")
    here = nav.feet_now()
    hits = sorted(find(["water"], radius=32, limit=30), key=lambda h: h["distance"])
    for h in hits[:4]:
        c = (h["x"], h["y"], h["z"])
        from .world import Region, add
        spot = fluids.fill_spot(Region(add(c, (-5, -3, -5)), add(c, (5, 3, 5)), props=True), here)
        if spot is None:
            continue
        stand, source = spot
        if not nav.go_to(stand, ctx.policy, range_=0.6, attempts=1):
            continue
        for _ in range(min(count, Inventory().count("minecraft:glass_bottle"))):
            api.run({"type": "use_item", "item": "minecraft:glass_bottle", "x": source[0] + 0.5,
                     "y": source[1] + 0.5, "z": source[2] + 0.5}, wait=10)
            yield None
        return True
    raise NotAvailable("no reachable still water for bottles")


@skill(budget=300, stall=120, per_unit=60)
def brew_fire_resistance(ctx):
    """At a brewing stand (found or placed from the bag): 3 water bottles + nether wart → awkward, + magma cream →
    fire resistance, blaze powder as fuel. Each ingredient takes 20 s."""
    from .skills import Station, _open_container
    inv = Inventory()
    if inv.count("minecraft:blaze_powder") < 1:
        raise NotAvailable("need blaze powder as brewing fuel")
    with Station(ctx, "minecraft:brewing_stand") as station:
        _open_container(station.pos)
        try:
            from .world import container
            view = {s["index"]: s for s in container()["slots"] if s["owner"] == "player"}
            potions = [s for s in view.values() if s["id"] == "minecraft:potion"][:3]
            if len(potions) < 3:
                raise NotAvailable("need 3 water bottles")
            for i, s in enumerate(potions):
                api.post("/click", {"slot": s["slot"], "button": 0, "action": "PICKUP"})
                api.post("/click", {"slot": i, "button": 0, "action": "PICKUP"})
            for item in ("minecraft:blaze_powder",):
                fuel = next(s for s in view.values() if s["id"] == item)
                api.post("/click", {"slot": fuel["slot"], "button": 1, "action": "PICKUP"})
                api.post("/click", {"slot": 4, "button": 1, "action": "PICKUP"})
            for ingredient, _stage in FIRE_RES_CHAIN:
                src = next((s for s in container()["slots"] if s["owner"] == "player" and s["id"] == ingredient), None)
                if src is None:
                    raise NotAvailable(f"missing {ingredient.split(':')[1]}")
                api.post("/click", {"slot": src["slot"], "button": 1, "action": "PICKUP"})
                api.post("/click", {"slot": 3, "button": 1, "action": "PICKUP"})
                deadline = time.time() + 25
                while time.time() < deadline:
                    time.sleep(1)
                    yield None
            for i in range(3):
                api.post("/click", {"slot": i, "button": 0, "action": "QUICK_MOVE"})
        finally:
            api.post("/close")
    log("brewed fire resistance potions")
    return True
