"""portal: what memory knows of the places a thing lives in (decompose.where_it_lives, LIVES_IN; THEN's seeks) —
none; a portal remembered in this dimension (portal_known: no portal is cast first); the fortress and the stronghold
remembered as sites (a seek whose site is known is skipped)."""
NAME = "portal"
VALUES = ("none", "known", "sites")

PORTAL = (30, 64, -30)
FORTRESS, STRONGHOLD = (200, 70, 200), (-400, 30, 400)


def domain():
    return VALUES


def alpha(a):
    mem, dim = a.mem, a.snap.dimension
    if mem.sites(None, kinds=["fortress"]) or mem.sites(None, kinds=["stronghold"]):
        return "sites"
    return "known" if mem.sites(dim, kinds=["portal"]) or mem.machines(dim, "portal") else "none"


def gamma(value, facts, g):
    if value == "known":
        g.mem.add_site("portal", PORTAL, facts["dimension"])
    elif value == "sites":
        g.mem.add_site("fortress", FORTRESS, "minecraft:the_nether")
        g.mem.add_site("stronghold", STRONGHOLD, "minecraft:overworld")
