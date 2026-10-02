"""Room in the bag (Inventory.free_slots): room, or full to the last slot (the empty-bag row, the Nether retreat,
the fight's bag loss read it)."""
from bonobo.world import BAG_SLOTS

NAME = "bag"
VALUES = ("room", "full")
JUNK = "string"                      # neither building, food, fuel nor a tool: room taken, nothing else


def domain():
    return VALUES


def alpha(a):
    return "full" if a.snap.inv.free_slots() <= 1 else "room"


def gamma(value, f, g):
    if value == "full":
        while len(g.slots) < BAG_SLOTS:
            g.give(JUNK, 64)


def step(facts, d, ctx):
    """Emptying the bag (reflexes' "empty the bag": deposit or drop) leaves room."""
    return {NAME: "room"} if "empty the bag" in (d.name or "").lower() else {}
