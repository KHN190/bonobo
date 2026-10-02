"""Invariant functions beyond check/oracle.py's: one module per family, found by listing this package. A module
defines CHECKS = {id: fn(before, d, after, ctx) -> None | str | oracle.Unchecked}; an id here replaces oracle.py's
own (an Unchecked placeholder made real), a new id is added."""
import importlib
import pkgutil

CHECKS = {}
for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if not _m.name.startswith("_"):
        CHECKS.update(importlib.import_module(f"{__name__}.{_m.name}").CHECKS)
