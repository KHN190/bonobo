"""The checker's fact dimensions beyond the base ones (check/facts.py): one module per dimension, found by listing this
package — a new dimension is a new file here, never an edit to a shared one.

A dimension module defines:
  NAME                      the fact's name
  domain() -> tuple         its finite values, each a production predicate's answer; the first is the state without it
  alpha(a) -> value         the fact read back by its production predicate (a: facts.Alpha — snap, mem, world, ready)
  gamma(value, facts, g)    the concrete world for the value (g: gamma.Build — blocks, give(), entities, state, mem)
optional:
  DEPENDS = (pred, witness) read only while pred(facts) holds, else its first value; witness: facts that turn it on
  CORE = True               combined in the full product (default: pairwise with the other non-core facts)
  WORLD = True              the world changes it on its own (explore.moves)
  prepare(brain, facts)     after the round's brain is made, before it decides (a failure recorded, a held choice)
  failure(facts)            the exception a failed step ends in for this state (round: D5's failure, `cooled`)
  step(facts, d, ctx)       {fact: value} a decision's declared effect changes (explore.step)
"""
import importlib
import pkgutil

DIMS = [importlib.import_module(f"{__name__}.{m.name}")
        for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name) if not m.name.startswith("_")]
