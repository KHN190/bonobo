"""The maintenance reflexes: a fixed trigger in, a fixed action out — no planning, a second's work. One ordered
table (the shape of `recovery.TABLE`), the arbiter's MAINTAIN layer: faster than any plan, slower than a fight.

    eat on a hungry stomach with food carried · out of the water · home from the Nether when it turns bad ·
    dug out in the morning · into a bed at night · the night's shelter from what is carried · a finished furnace
    or machine emptied · a full bag emptied · a way made where a walk was blocked · unstuck

What must be PLANNED to be had — a bed, food stock, a tool, a bucket, blocks, the night's ore — is not here: those
are PLAN proposals (upkeep's needs, `decompose`). A trigger reads only its view (`view`: readings made once per
round, lazily); the action is the upkeep executor's.
"""
BAG_FULL = 34              # slots used before the bag is emptied
BRIDGE_MIN = 8             # building blocks it takes to bridge a blocked path
EAT_BELOW = 14             # hunger points: eat below this, while there is something to eat (standing)

# (name, trigger over the round's view) — in order: the first that fires is the reflex the layer proposes first.
TABLE = [
    ("recover items", lambda v: v["died_recently"]),
    ("eat", lambda v: v["food"] < EAT_BELOW and v["edible"]),
    ("reach land", lambda v: v["swimming"]),
    ("leave the Nether", lambda v: v["nether_bad"]),
    ("dig out", lambda v: not v["night"] and v["enclosed"]),
    ("sleep", lambda v: v["overworld"] and v["night"] and v["bed_works"] and (v["bed_carried"] or v["bed_near"])),
    ("shelter", lambda v: v["shelter_ready"]),
    ("collect job", lambda v: v["job_ready"]),
    ("collect machine", lambda v: v["machine_ready"]),
    ("empty the bag", lambda v: v["used_slots"] >= BAG_FULL),
    ("path blocked", lambda v: v["blocked"] and v["building"] >= BRIDGE_MIN),
    ("unstuck", lambda v: v["stuck"]),
]
NAMES = tuple(name for name, _t in TABLE)


class View(dict):
    """The round's readings, each made on first ask (`providers`: {key: zero-argument reader}), then kept."""

    def __init__(self, providers, **known):
        super().__init__(**known)
        self.providers = providers

    def __missing__(self, key):
        value = self.providers[key]()
        self[key] = value
        return value


def due(view, ready=lambda name: True):
    """[(seq, name)] of the reflexes whose trigger fires, in table order, skipping those cooling (`ready`)."""
    return [(i, name) for i, (name, trigger) in enumerate(TABLE) if ready(name) and trigger(view)]
