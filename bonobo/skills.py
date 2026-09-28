"""Verified routines built on the mod's primitives. Every skill carries a contract (see skill.py): preconditions, goal check, verification, time budget and a stall limit on its own goal metric. Skills never plan: inputs must be present. `python3 mc.py skills` lists the contracts."""

from . import craft, gather, survive, store  # noqa: F401
