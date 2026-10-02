"""Scan the package for estimators, for the same quantity written twice, and for clamps that hide errors. Every number this agent acts on is an estimate of something — when a mob arrives, what a fight costs, how much of a day a bed saves. Four bugs found in one live pass were all the same shape: one quantity with two definitions that had drifted apart (`saves` against kernel's score, delay against line-of-sight, arrival in two modules, carrying on counted as both a state and a cost). So the scan is structural, not about any one number: estimators()   every module-level function that returns a quantity, with the unit its name declares twins()        estimators of the same quantity under different names — the bug class, before it bites clamps()       min/max against a literal on a returned value: often a symptom held down rather than fixed `python3 -m bonobo.tools.estimators` prints all three. `tests/test_estimators_are_singular.py` holds the result to a declared list, so a new twin or a new clamp has to be argued for rather than merged."""

import ast
import os
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# what a name says it returns, by suffix (_s seconds, _hp health, _rate per second, _ratio/_chance fractions)
UNITS = (("_s", "seconds"), ("_seconds", "seconds"), ("_hp", "hp"), ("_rate", "per_second"),
         ("_ratio", "fraction"), ("_chance", "probability"), ("_risk", "probability"), ("_cost", "cost"),
         ("_worth", "seconds"), ("_points", "points"), ("_factor", "fraction"))

# the quantity a name is about: two functions sharing a concept and a unit are two definitions of one number
CONCEPTS = {
    "arrival": ("arrival", "tti", "arrive", "reach_time"),
    "pressure": ("pressure", "tax", "dps_here", "hp_tax"),
    "fight": ("fight_cost", "encounter_damage", "kill_cost"),
    "escape": ("evade_cost", "exposure", "escape_cost"),
    "delay": ("delay_ratio", "hide_ratio", "squeeze"),
    "death": ("fatal_chance", "death_risk", "immediate_risk", "time_to_die"),
    "horizon": ("horizon", "work_horizon"),
    "price": ("hp_seconds", "expected_loss", "objective", "price"),
    "benefit": ("benefit", "saves", "value_s", "worth_s"),
    "terrain": ("terrain", "factor_of", "bucket_of"),
}

def _modules(pkg=PKG):
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py") and not name.startswith("_"):
            yield name[:-3], os.path.join(pkg, name)

def _unit(name):
    for suffix, unit in UNITS:
        if name.endswith(suffix):
            return unit
    return None

def _concept(module, name):
    for concept, words in CONCEPTS.items():
        if any(w in name for w in words):
            return concept
    return None

def estimators(pkg=PKG):
    """[(module, name, line, unit, concept)] — every function whose name declares a quantity."""
    out = []
    for module, path in _modules(pkg):
        tree = ast.parse(open(path).read())     # a module that does not parse is an error here, not skipped
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("__"):
                continue
            unit, concept = _unit(node.name), _concept(module, node.name)
            if unit or concept:
                out.append((module, node.name, node.lineno, unit, concept))
    return out

def twins(pkg=PKG):
    """{(concept, unit): [(module, name, line)]} for every quantity defined more than once."""

    groups = {}
    for module, name, line, unit, concept in estimators(pkg):
        if concept is None:
            continue
        groups.setdefault((concept, unit), []).append((module, name, line))
    return {k: v for k, v in groups.items() if len(v) > 1}

def clamps(pkg=PKG):
    """[(module, line, source)] — min/max against a number, on the way out of a function."""

    out = []
    for module, path in _modules(pkg):
        source = open(path).read()
        tree = ast.parse(source)
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            for call in ast.walk(node.value):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in ("min", "max")
                        and any(isinstance(a, ast.Constant) and isinstance(a.value, (int, float))
                                for a in call.args)):
                    out.append((module, node.lineno, lines[node.lineno - 1].strip()))
                    break
    return out

def report(pkg=PKG):
    lines = ["ESTIMATORS"]
    for module, name, line, unit, concept in sorted(estimators(pkg), key=lambda e: (e[0], e[1])):
        lines.append(f"  {module}.{name}:{line}  unit={unit or '-':10} concept={concept or '-'}")
    lines.append("")
    lines.append("TWINS (one quantity, two definitions)")
    for (concept, unit), members in sorted(twins(pkg).items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        lines.append(f"  {concept}/{unit}: " + ", ".join(f"{m}.{n}:{l}" for m, n, l in members))
    lines.append("")
    lines.append("CLAMPS ON RETURNED VALUES")
    for module, line, text in sorted(clamps(pkg)):
        lines.append(f"  {module}:{line}  {text}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(report())
    sys.exit(0)
