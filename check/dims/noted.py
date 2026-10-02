"""A source the memory noted out of sight (memory.seen): a tree or an ore site the planner may walk to rather than
search for (decompose's sources, knowledge.where_it_lives). α: memory.seen of each kind."""
NAME = "noted"
VALUES = ("none", "tree", "iron_ore")

FAR = (40, 64, 40)                  # beyond the /find radius the round looks in


def domain():
    return VALUES


def alpha(a):
    return next((k for k in VALUES[1:] if a.mem.seen(k, a.snap.dimension)), "none")


def gamma(value, facts, g):
    if value != "none":
        g.mem.note_seen(value, FAR, facts["dimension"])
