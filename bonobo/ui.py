"""Screens with buttons (mod ≥ 0.1.23: /button, /trade, /rename and container details): enchanting, villager
trades, anvils. Pure choosers (`choose_enchant`, `choose_trade`, `anvil_ok`) are offline-tested; skills open the
block/villager, arrange slots with /click and press the buttons.

Container JSON extras: enchanting → "enchant": [{"cost", "id", "level"}×3] and "lapis"; merchant → "offers":
[{"buy", "buyCount", "buy2", "buy2Count", "sell", "sellCount", "disabled"}]; anvil → "levelCost"."""
import math

from . import api, nav
from .api import McError, NotAvailable, log
from .skill import skill
from .world import Inventory, container, entities, find


def choose_enchant(options, xp_level, lapis):
    """Pure: the enchant button to press — the best option whose level cost the player can pay (level requirement
    ≤ xp, lapis ≥ button + 1). None when nothing is affordable or offered."""
    best = None
    for i, o in enumerate(options):
        cost = o.get("cost", 0)
        if cost <= 0 or cost > xp_level or lapis < i + 1:
            continue
        if best is None or cost > options[best]["cost"]:
            best = i
    return best


def choose_trade(offers, want, have):
    """Pure: index of an enabled offer selling `want` that the inventory can pay (`have`: item id → count). Cheapest
    first payment wins."""
    best = None
    for i, o in enumerate(offers):
        if o.get("disabled") or o.get("sell") != want:
            continue
        if have.get(o.get("buy"), 0) < o.get("buyCount", 1):
            continue
        if o.get("buy2") and have.get(o["buy2"], 0) < o.get("buy2Count", 1):
            continue
        if best is None or o.get("buyCount", 1) < offers[best].get("buyCount", 1):
            best = i
    return best


def anvil_ok(level_cost, xp_level):
    """Pure: an anvil result can be taken (cost known, affordable, below the 'too expensive' cap of 40)."""
    return 0 < level_cost <= xp_level and level_cost < 40


def _slot_of(item):
    return next((s for s in container()["slots"] if s["owner"] == "player" and s["id"] == item), None)


def _put(item, target_slot, count_button=0):
    src = _slot_of(item)
    if src is None:
        raise NotAvailable(f"no {item.split(':')[1]} in the inventory")
    api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})
    api.post("/click", {"slot": target_slot, "button": count_button, "action": "PICKUP"})
    cursor = container().get("cursor") or {}
    if cursor.get("id") not in (None, "minecraft:air"):
        api.post("/click", {"slot": src["slot"], "button": 0, "action": "PICKUP"})   # put the rest back


@skill(budget=180, stall=60, per_unit=30)
def enchant_item(ctx, item):
    """At an enchanting table (found or carried): put the item and lapis in, press the best affordable option,
    take the item back."""
    from .skills import Station, _open_container
    xp = api.get("/state").get("xpLevel", 0)
    if xp < 1:
        raise NotAvailable("no experience levels to enchant with")
    if not Inventory().count("minecraft:lapis_lazuli"):
        raise NotAvailable("no lapis lazuli")
    with Station(ctx, "minecraft:enchanting_table") as station:
        _open_container(station.pos)
        try:
            _put(item, 0, count_button=1)
            _put("minecraft:lapis_lazuli", 1)
            yield None
            view = container()
            pick = choose_enchant(view.get("enchant", []), xp, view.get("lapis", 0))
            if pick is None:
                raise NotAvailable("no affordable enchantment offered (more bookshelves or levels needed)")
            api.post("/button", {"id": pick})
            yield pick
            api.post("/click", {"slot": 0, "button": 0, "action": "QUICK_MOVE"})
            api.post("/click", {"slot": 1, "button": 0, "action": "QUICK_MOVE"})
        finally:
            api.post("/close")
    log(f"enchanted {item.split(':')[1]} (option {pick + 1})")
    return pick


@skill(start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=60, per_unit=30)
def trade(ctx, want):
    """Buy `want` from a nearby villager: open its trades, pick an affordable offer, take the result."""
    villagers = [e for e in entities(24) if e["type"] == "minecraft:villager" and not ctx.blocked((e["id"], 0, 0))]
    if not villagers:
        raise NotAvailable("no villager nearby")
    v = min(villagers, key=lambda e: e["distance"])
    r = api.run({"type": "interact", "entity": v["id"]}, wait=45)
    if r["status"] != "succeeded":
        raise api.NavFailed(f"could not reach the villager: {r['message']}")
    try:
        view = container()
        have = {}
        for s in Inventory().slots:
            have[s["id"]] = have.get(s["id"], 0) + s.get("count", 1)
        index = choose_trade(view.get("offers", []), want, have)
        if index is None:
            ctx.ban((v["id"], 0, 0), 1800)
            raise NotAvailable(f"this villager has no affordable {want.split(':')[1]} trade")
        api.post("/trade", {"index": index})
        yield index
        api.post("/click", {"slot": 2, "button": 0, "action": "QUICK_MOVE"})
    finally:
        api.post("/close")
    log(f"traded for {want.split(':')[1]}")
    return index


@skill(budget=180, stall=60, per_unit=30)
def anvil_repair(ctx, item, material):
    """At an anvil: the damaged item + its repair material (e.g. diamond pickaxe + diamonds), take the result when
    the level cost is affordable."""
    from .skills import Station, _open_container
    xp = api.get("/state").get("xpLevel", 0)
    with Station(ctx, "minecraft:anvil") as station:
        _open_container(station.pos)
        try:
            _put(item, 0, count_button=1)
            _put(material, 1)
            api.post("/rename", {"name": ""})
            yield None
            cost = container().get("levelCost", 0)
            if not anvil_ok(cost, xp):
                raise NotAvailable(f"anvil cost {cost} levels, have {xp}")
            api.post("/click", {"slot": 2, "button": 0, "action": "QUICK_MOVE"})
            for slot in (0, 1):
                api.post("/click", {"slot": slot, "button": 0, "action": "QUICK_MOVE"})
        finally:
            api.post("/close")
    log(f"repaired {item.split(':')[1]} at an anvil ({cost} levels)")
    return cost
