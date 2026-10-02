"""Screens with buttons (mod ≥ 0.1.23: /button, /trade, /rename and container details): enchanting, villager trades, anvils. Pure choosers (`choose_enchant`, `choose_trade`, `anvil_ok`) are offline-tested; skills open the block/villager, arrange slots with /click and press the buttons. Container JSON extras: enchanting → "enchant": [{"cost", "id", "level"}×3] and "lapis"; merchant → "offers": [{"buy", "buyCount", "buy2", "buy2Count", "sell", "sellCount", "disabled"}]; anvil → "levelCost"."""

from . import knowledge as _k  # noqa: E402  (skills' world remainders: knowledge's readers)
from . import bag as _bag
from . import knowledge as K
from . import api
from .api import NotAvailable, log
from .data import mid, MATERIAL_TOKEN
from .skill import skill
from .world import Inventory, container, entities
from .building import _open_container
from .craft import Station
from .knowledge import members

def choose_enchant(options, xp_level, lapis):
    """Pure: the best enchant button the player can pay for (level ≤ xp, lapis ≥ button + 1)."""

    best = None
    for i, o in enumerate(options):
        cost = o.get("cost", 0)
        if cost <= 0 or cost > xp_level or lapis < i + 1:
            continue
        if best is None or cost > options[best]["cost"]:
            best = i
    return best

def choose_trade(offers, want, have):
    """Pure: index of an enabled offer selling `want` that the inventory can pay (`have`: item id → count)."""

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

def _enchanted(item):
    """How many of `item` carried are enchanted (the jar reports `enchanted` on a stack from 0.1.39)."""
    return sum(1 for s in Inventory().slots if s["id"] == item and s.get("enchanted"))

@skill(gives=["state:enchanted"], remaining=_k.enchanted(lambda c: c.args[1]), needs={"minecraft:lapis_lazuli": 1}, start=lambda c: _enchanted(c.args[1]), verify=lambda c: _enchanted(c.args[1]) > c.base, budget=180, stall=60, provides={"enchant": lambda ctx, s: (mid(s.token),)})
def enchant_item(ctx, item):
    """At an enchanting table (found or carried): put the item and lapis in, press the best affordable option, take the item back."""

    xp = api.get("/state").get("xpLevel", 0)
    if xp < 1:
        raise NotAvailable("no experience levels to enchant with")
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
            api.post("/click", _bag.quick_move(0))
            api.post("/click", _bag.quick_move(1))
        finally:
            api.post("/close")
    log(f"enchanted {item.split(':')[1]} (option {pick + 1})")
    return pick

@skill(gives=K.GIVES_TRADE, needs=lambda a: {} if str(a[1]).endswith("emerald") else {"minecraft:emerald": 1}, start=lambda c: Inventory().count(c.args[1]), verify=lambda c: Inventory().count(c.args[1]) > c.base,
       budget=180, stall=60, provides={"trade": lambda ctx, s: (mid(s.token),)})
def trade(ctx, want):
    """Buy `want` from a nearby villager: open its trades, pick an affordable offer, take the result."""
    villagers = [e for e in entities(24) if e["type"] == "minecraft:villager" and not ctx.blocked((e["id"], 0, 0))]
    if not villagers:
        raise NotAvailable("no villager nearby")
    v = min(villagers, key=lambda e: e["distance"])
    r = api.run({"type": "interact", "entity": v["id"]}, wait=45, awaits="the trade window's offers are what the choice reads (choose_trade), only there once it opens")
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
        api.post("/click", _bag.quick_move(2))
    finally:
        api.post("/close")
    log(f"traded for {want.split(':')[1]}")
    return index

def _worn(item):
    """Damage on the most worn one of `item` carried (0 when none)."""
    return max((s.get("damage", 0) for s in Inventory().slots if s["id"] == item), default=0)

def _anvil_args(ctx, step):
    """(most worn tool of the step's kind, the material an anvil repairs it with), when both are carried."""
    inv = Inventory()
    tools = [s for s in inv.slots if s["id"].endswith("_" + step.token) and s.get("damage")]
    if not tools:
        return None
    item = max(tools, key=lambda s: s.get("damage", 0))["id"]
    token = MATERIAL_TOKEN.get(item.split(":")[-1].rpartition("_")[0])
    material = next((m for m in members(token) if inv.count(m)), None) if token else None
    return (item, material) if material else None

@skill(gives=["state:repaired"], remaining=_k.worn(lambda c: c.args[1]), needs={}, start=lambda c: _worn(c.args[1]), verify=lambda c: _worn(c.args[1]) < c.base,
       budget=180, stall=60, prefer=-1, provides={"repair": _anvil_args})
def anvil_repair(ctx, item, material):
    """At an anvil: item + repair material (e.g. diamond pickaxe + diamonds), take the result when the level cost is affordable."""

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
            api.post("/click", _bag.quick_move(2))
            for slot in (0, 1):
                api.post("/click", _bag.quick_move(slot))
        finally:
            api.post("/close")
    log(f"repaired {item.split(':')[1]} at an anvil ({cost} levels)")
    return cost
