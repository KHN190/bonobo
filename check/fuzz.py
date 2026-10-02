"""python3 -m check.fuzz SECONDS [report.md] [--save] [--stall=S]: coverage-guided search over the α facts (Hypothesis).

Each example is a state drawn from the facts' domains (check/facts.DOMAINS: the base facts and check/dims' domain()),
run through the production round (round.decide) under the coverage gate (sys.monitoring); hypothesis.target steers
toward states that hit branch arms no earlier example hit. With --save, such a state is kept in check/corpus (one
JSON file each): check.run judges the corpus beside its own states, every run. A violation the oracle reports is
shrunk by Hypothesis to its smallest state (the fewest facts off their first value) and written to the report.
Exit 1 when an invariant outside check/known.txt is violated. SECONDS bounds the whole run (the gate gives 60);
--stall=S ends the search once S seconds pass without a new branch arm. Progress is logged per chunk as it runs."""
import contextlib
import hashlib
import io
import json
import os
import sys
import time

from . import oracle, round as rnd
from .coverage import Gate
from .facts import DOMAINS, key, of

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")
KNOWN = os.path.join(HERE, "known.txt")
SEARCH_SHARE = 0.7              # of the budget: the search; the rest shrinks what it found
CHUNK = 50                      # examples per Hypothesis run between the budget's and the stall's checks
SHRINK_EXAMPLES = 200           # tries Hypothesis may spend shrinking one invariant's state


def corpus():
    """The kept states (facts dicts), each a state that once hit a new branch arm."""
    if not os.path.isdir(CORPUS):
        return []
    out = []
    for name in sorted(os.listdir(CORPUS)):
        if name.endswith(".json"):
            with open(os.path.join(CORPUS, name)) as fh:
                out.append(of(**json.load(fh)))
    return out


def keep(f):
    os.makedirs(CORPUS, exist_ok=True)
    name = hashlib.sha1(repr(key(f)).encode()).hexdigest()[:16]
    with open(os.path.join(CORPUS, f"{name}.json"), "w") as fh:
        json.dump(dict(f), fh, indent=1, sort_keys=True)


def known():
    with open(KNOWN) as fh:
        return {ln.split()[0] for ln in fh if ln.strip() and not ln.startswith("#")}


def states():
    from hypothesis import strategies as st
    return st.fixed_dictionaries({k: st.sampled_from(v) for k, v in DOMAINS.items()}).map(lambda kw: of(**kw))


def judged(f):
    """(the round's decision, the invariants it violates) on state `f`, the round's prints dropped."""
    with contextlib.redirect_stdout(io.StringIO()):
        d, _got, ctx = rnd.decide(f)
    return d, {inv for inv, _why in oracle.violations(f, d, f, ctx)}


def off_default(f):
    return {k: v for k, v in f.items() if v != DOMAINS[k][0]}


def run(seconds, save=False, stall=None, log=print):
    """{"examples", "kept", "new_arms", "found": {inv: smallest state}, "hit", "total"} of one bounded search: chunks
    of CHUNK examples until SECONDS × SEARCH_SHARE is spent or `stall` seconds pass with no new branch arm; each
    chunk logged as it ends (a long run is watched, not waited on)."""
    from hypothesis import HealthCheck, Phase, find, given, settings, target
    from hypothesis.errors import NoSuchExample
    t0, gate = time.time(), Gate()
    kept, found, examples = [], {}, [0]
    with gate:
        @settings(max_examples=CHUNK, deadline=None, database=None,
                  phases=[Phase.generate, Phase.target], suppress_health_check=[HealthCheck.too_slow])
        @given(states())
        def search(f):
            examples[0] += 1
            before = sum(gate.arms.values())
            _d, invs = judged(f)
            new = sum(gate.arms.values()) - before
            target(float(new), label="new branch arms")
            if new:
                kept.append(f)
                if save:
                    keep(f)
            for inv in invs:
                found.setdefault(inv, f)

        last_new = time.time()
        while time.time() - t0 < seconds * SEARCH_SHARE:
            arms = sum(gate.arms.values())
            search()  # pyright: ignore[reportCallIssue]  (@given supplies f)
            now = time.time()
            if sum(gate.arms.values()) > arms:
                last_new = now
            log(f"fuzz {now - t0:.0f}s: {examples[0]} states, {sum(gate.arms.values())} arms, {len(kept)} kept, "
                f"violated {sorted(found)}")
            if stall is not None and now - last_new >= stall:
                log(f"fuzz: no new arm for {stall:.0f}s, stopped")
                break
        new_arms = sum(gate.arms.values())
        for inv in sorted(found):
            if seconds - (time.time() - t0) <= 0:
                break
            try:
                found[inv] = find(states(), lambda f, inv=inv: inv in judged(f)[1],
                                  settings=settings(max_examples=SHRINK_EXAMPLES, deadline=None, database=None,
                                                    suppress_health_check=[HealthCheck.too_slow]))
            except NoSuchExample:
                pass                     # the shrink drew no violating state in its tries: the one the search saw stands
            log(f"fuzz: {inv} shrunk to {off_default(found[inv])}")
    hit, total, _unhit = gate.report()
    return {"examples": examples[0], "kept": len(kept), "new_arms": new_arms, "found": found, "hit": hit,
            "total": total, "seconds": time.time() - t0}


def report(got, unknown):
    lines = [f"# fuzz: {got['examples']} states in {got['seconds']:.0f} s, {got['kept']} hit new arms, "
             f"coverage {got['hit']}/{got['total']}", "", "## violations, each shrunk to its smallest state",
             "| inv | known | smallest state (facts off their first value) |", "|---|---|---|"]
    lines += [f"| {inv} | {'no' if inv in unknown else 'yes'} | {off_default(f)} |" for inv, f in sorted(got["found"].items())]
    return "\n".join(lines) + "\n"


def main(argv):
    seconds = float(argv[0]) if argv else 60.0
    out = next((a for a in argv[1:] if not a.startswith("--")), None)
    stall = next((float(a.split("=", 1)[1]) for a in argv if a.startswith("--stall=")), None)
    got = run(seconds, save="--save" in argv, stall=stall, log=lambda line: print(line, flush=True))
    unknown = set(got["found"]) - known()
    text = report(got, unknown)
    if out:
        with open(out, "w") as fh:
            fh.write(text)
    print(text.splitlines()[0] + (f"; NEW violations: {sorted(unknown)}" if unknown else ""))
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
