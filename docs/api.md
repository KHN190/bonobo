# API

Hands over to LLM.

```python
offer(want: dict, worth_s: float, *, deadline_s=None, expires_s=600, scope=None, note="", id=None) -> Want
    want: {"token": n} or {"end:sheltered": 1} # an effect on the state vector, entering gates.V as a temporary terminal dimension
    # deadline_s only steepens the discount; scope only narrows the candidate pool; neither makes an infeasible skill feasible
stop(id=None, *, hard=False, reason="") -> list[Want]      # soft = remaining value zeroed; hard = arbiter.BODY.preempt
status(id=None) -> list[dict]  # {id, want, worth_s, priced_s, chosen, blocked_by, age_s, expires_in_s, state}
list(active=True) -> list[Want]
Want = {id, want, worth_s, deadline_s, expires_s, scope, note, state}
# state: pending | chosen | running | met | expired | refused(reason)

```

```sh
mc.py want offer <token> <n> --worth-s S [--deadline-s D] [--expires-s E] [--scope name]
mc.py want stop [id] [--hard] | want status [id] | want list

priority.py and directives.py become sugar over this door: a nudge is an offer of extra seconds on an existing goal; goto/skill is an offer with a scope.
```