"""A recent death whose drops still lie there (memory.recent_death: within the despawn window), near with a bag
worth the walk, or far past the walk's worth (reflexes.worth_recovering prices it)."""
NAME = "death"
VALUES = ("none", "near", "far")
SPOTS = {"near": (10, 64, 0), "far": (5000, 64, 0)}
CARRIED = (("minecraft:diamond", 3), ("minecraft:iron_ingot", 16))       # a bag worth recovering


def domain():
    return VALUES


def alpha(a):
    d = a.mem.recent_death(a.snap.dimension)
    if d is None:
        return "none"
    near = min(SPOTS, key=lambda k: sum(abs(p - q) for p, q in zip(SPOTS[k], d["pos"])))
    return near


def gamma(value, f, g):
    if value != "none":
        g.mem.log_death(SPOTS[value], f["dimension"], CARRIED)
