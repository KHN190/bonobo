"""Per-life state, forgotten in one place. A module that keeps mutable state belonging to one life (one bench row, one
life between deaths, one dimension) registers its reset beside that state: `on_reset(fn, covers=names)`.
`reset_all()` runs every registered reset — the bench at each row's setup, the brain on death and on a dimension
change. A new piece of such state is covered by registering it, not by another call at the bench's row setup
(the leaks this replaced: api.AT_BOUNDARY, fight_loop.HELD, bench SWEEP, each patched where it bit).
tests/test_lifecycle.py holds every `global`-assigned module name to a registered reset or a stated reason."""

_RESETS = []        # [(module name, names covered, fn)], in registration (import) order


def on_reset(fn, covers=()):
    """Register `fn` (no arguments) as the reset of `covers`, names in fn's own module. Returns fn."""
    _RESETS.append((fn.__module__, tuple(covers), fn))
    return fn


def reset_all():
    """Every registered reset, once: nothing the last life (row, death, dimension) left carries into the next."""
    for _mod, _names, fn in list(_RESETS):
        fn()


def covered():
    """{(module, name)} every registered reset puts back."""
    return {(mod, name) for mod, names, _fn in _RESETS for name in names}


def registered():
    """[(module, names)] as registered."""
    return [(mod, names) for mod, names, _fn in _RESETS]
