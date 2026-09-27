"""What a bench cell IS: the dimensions a situation can vary along, and one way to move along them. The sheets used to carry their own dimensions as dictionaries of setup commands, and the offline sweep carried its own as dictionaries of state vectors. Two vocabularies for one idea: a dimension added to one was missing from the other, and a relation that should hold "wherever this varies" quietly held in half the places. So the names live here, once. A cell is a typed thing with a `with_` that moves exactly one dimension — which is how every rule in a sheet is phrased: the same situation, one variable moved, and the number may only go one way. The sheets say how to BUILD each value in a running game; `tests/world.py` says how to build it as a state vector. Neither owns the vocabulary."""

# name -> the values it can take. The first value of each is the baseline a relation is stated against.
DIMENSIONS = {
    # what ordinary play varies
    "resource": ("bare", "village", "seam", "herd"),
    "terrain": ("flat", "room", "water", "lava"),
    "self": ("ready", "full_bag", "hungry", "hurt", "swimming", "drowning", "falling"),
    "stock": ("none", "has_wool", "has_iron", "has_tools", "has_station", "has_kit"),
    "memory": ("blank", "remembers"),
    # what a fight varies. The body is four dimensions, not one bundle: moving a bundle moves the weapon, the
    # armour, the blood and the bag at once, and no single-variable relation can be stated about any of them.
    "enemy": ("none", "walker", "archer", "climber", "bomb", "teleporter", "pack", "mixed"),
    "count": ("one", "three"),
    "ground": ("open", "corridor", "roofed"),
    "weapon": ("iron", "fist"),
    "armour": ("iron", "skin"),
    "blood": ("whole", "hurt"),
    "kit": ("full", "blocks", "food", "shield", "nothing"),
    "distance": ("near", "across"),
}

BASELINE = {name: values[0] for name, values in DIMENSIONS.items()}

class Cell:
    """One situation of a sweep, named by where it sits along each dimension."""

    __slots__ = ("dims",)

    def __init__(_cell, **dims):          # noqa: N805  ("self" is a dimension name here, not the instance
        self = _cell                      #             — the vocabulary wins over the convention)
        unknown = set(dims) - set(DIMENSIONS)
        if unknown:
            raise KeyError(f"no such dimension: {sorted(unknown)}")
        for name, value in dims.items():
            if value not in DIMENSIONS[name]:
                raise ValueError(f"{name} has no value {value!r}: {DIMENSIONS[name]}")
        self.dims = dict(BASELINE, **dims)

    def __getattr__(_cell, name):         # noqa: N805
        dims = object.__getattribute__(_cell, "dims") if name != "dims" else {}
        if name in dims:
            return dims[name]
        raise AttributeError(name)

    def with_(_cell, **changes):          # noqa: N805  (same reason)
        """The same cell with one dimension moved — the only way a rule should name a second cell."""
        return Cell(**dict(_cell.dims, **changes))

    def key(_cell):                       # noqa: N805
        return tuple(_cell.dims[name] for name in DIMENSIONS)

    def row(_cell):                       # noqa: N805
        """The dimensions as a row, for whatever a sheet writes out."""
        return dict(_cell.dims)

    # A cell spreads like the mapping it used to be (`{**cell, **record(cell)}` in `core._sweep`), so the sheets
    # that still hand dictionaries around keep working while they move over.
    def keys(_cell):                      # noqa: N805
        return _cell.dims.keys()

    def __getitem__(_cell, name):         # noqa: N805
        return _cell.dims[name]

    def __repr__(_cell):                  # noqa: N805
        return "Cell(" + "/".join(f"{_cell.dims[n]}" for n in DIMENSIONS) + ")"

def sweep(**fixed):
    """Cells over the dimensions NAMED here; everything else sits at its baseline."""

    import itertools
    names = [n for n in DIMENSIONS if n in fixed]
    if not names:
        yield Cell()
        return
    values = [tuple(fixed[n]) if not isinstance(fixed[n], str) else (fixed[n],) for n in names]
    for combination in itertools.product(*values):
        yield Cell(**dict(zip(names, combination)))

def key_of(row):
    """The key of a row a sheet wrote out — the same tuple `Cell.key` gives, so rows and cells meet."""
    return tuple(row.get(name, BASELINE[name]) for name in DIMENSIONS)

def moved(cells, dim, value):
    """[(baseline cell, moved cell)] for every cell in `cells` that can move along `dim` to `value`."""

    out = []
    for cell in cells:
        # Anchored at the dimension's BASELINE, never at wherever this cell happens to sit: "lava, and now water
        # instead" is not the statement "water is in the way", and comparing those two was a red about nothing.
        if cell.dims[dim] != BASELINE[dim] or value == BASELINE[dim]:
            continue
        out.append((cell, cell.with_(**{dim: value})))
    return out
