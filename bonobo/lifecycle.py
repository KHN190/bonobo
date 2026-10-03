"""Per-life state, forgotten in one place. A module that keeps mutable state belonging to one life (one bench row, one
life between deaths, one dimension) registers its reset beside that state: `on_reset(fn, covers=names)`.
`reset_all()` runs every registered reset — the bench at each row's setup, the brain on death and on a dimension
change. A new piece of such state is covered by registering it, not by another call at the bench's row setup
(the leaks this replaced: api.AT_BOUNDARY, fight_loop.HELD, bench SWEEP, each patched where it bit).
tests/test_lifecycle.py holds every `global`-assigned module name to a registered reset or a stated reason."""

from typing import Any

_RESETS = []        # [(module name, names covered, fn)], in registration (import) order
CACHES = set()      # (module, names) whose value is a pure function of the inputs (D8: a cached value = recomputed)


def on_reset(fn, covers=()):
    """Register `fn` (no arguments) as the reset of `covers`, names in fn's own module. Returns fn."""
    _RESETS.append((fn.__module__, tuple(covers), fn))
    return fn


class State:
    """Base of a module's shared state object (a dataclass whose every field has a default, plus a `lock`). LIFE names
    the fields one life owns: `reset` puts them back to their defaults, the rest outlive a life. Each field is
    rebound, never emptied in place: a thread holding the old value finishes on it."""

    LIFE: "tuple[str, ...]" = ()
    lock: "Any"            # each subclass's own field (a Lock or RLock)

    def reset(self):
        fresh = type(self)()
        with self.lock:
            for f in self.LIFE:
                setattr(self, f, getattr(fresh, f))


def owns(module, state):
    """Register `state` (a State, module-level name STATE in `module`) for reset_all. Returns state."""
    _RESETS.append((module, ("STATE",), state.reset))
    return state


def in_place(module, *names, cache=False):
    """Register a reset putting each named module-level container of `module` back to its contents now (at import),
    IN PLACE: a name imported elsewhere (`from .runner import LAST_FEEDBACK`, nav's alias of world.ROUTES) keeps
    seeing the same object. Call it after the containers are defined, with `__name__`. `cache`: their value is
    a pure function of the inputs, kept by `reset_all(caches=False)` (the D8 check's warm round)."""
    import copy
    import sys
    mod = sys.modules[module]
    seed = {n: copy.deepcopy(getattr(mod, n)) for n in names}

    def reset():
        for n, v in seed.items():
            c = getattr(mod, n)
            c.clear()
            (c.update if isinstance(c, (dict, set)) else c.extend)(copy.deepcopy(v))
    _RESETS.append((module, tuple(names), reset))
    if cache:
        CACHES.add((module, tuple(names)))
    return reset


def reset_all(caches=True):
    """Every registered reset, once: nothing the last life (row, death, dimension) left carries into the next;
    `caches` False keeps the declared caches (in_place's `cache`)."""
    for mod, names, fn in list(_RESETS):
        if caches or (mod, names) not in CACHES:
            fn()


def registered():
    """[(module, names)] as registered."""
    return [(mod, names) for mod, names, _fn in _RESETS]
