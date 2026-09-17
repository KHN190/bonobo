"""One fact, one owner — checked mechanically, because I did not manage it by hand.

Every architectural mess in this planner has been the same shape: the same fact written twice, then the two copies
drifting. `threat.protection` and `survival.protection` both turned armour into a fraction, so a zombie was priced
differently in a fight than in the day's planning. `hp_seconds` had three live versions at once. `walk_s` and
`seek_s` both answered "how far is that". Nobody noticed until a behaviour went wrong and the search led back to a
duplicate.

So the rule is a test, not a resolution: a function name may be defined once in the package, a world constant may
live in one file, and a number that belongs to the belief table may not be typed into code.
"""
import ast
import os
import sys
import unittest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")


def source(mod):
    with open(os.path.join(PKG, mod + ".py")) as f:
        return f.read()


def modules():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py") and name != "__init__.py":
            yield name[:-3], ast.parse(source(name[:-3]))


def public_functions():
    """{name: [modules that define it]} for top-level, non-underscore functions."""
    out = {}
    for mod, tree in modules():
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                out.setdefault(node.name, []).append(mod)
    return out


class OneOwnerPerFact(unittest.TestCase):
    # Names that genuinely mean different things in different layers, each with the reason it is not a duplicate.
    ALLOWED = {
        "main": "every tool under tools/ has one",
        "run": "api.run posts a task; scenarios.run runs a bench scenario",
        "load": "each module loads its own file",
        "plan": "fight_plan.plan is the fight's; planner.plan is the requirement graph's",
        "advance": "survival.advance moves time; route.advance moves the route",
        "describe": "each module describes its own object",
        "save": "each store saves itself",
        # Owned elsewhere, kept for now with the owner named. Each is a real duplicate to merge, not an exemption
        # forever: the module in brackets is where the fact belongs.
        "at": "scenarios.at is a bench coordinate helper [actions.at owns the dimension name]",
        "status": "api.status is the mod's; scenarios.status is a bench verdict",
        "choose": "[kernel.choose owns picking] — priority.choose and route.choose still exist",
        "commitment": "[kernel.commitment owns it] — fight_plan.commitment still exists",
        "value": "[beliefs.value owns the table] — kernel.value is the objective",
        "make_state": "[survival.make_state owns the day's state] — fight_plan.make_state is the fight's",
        "decide": "decide.py is the replay tool; threat.decide answers a threat",
        "remaining": "blueprints.remaining counts parts; fight_plan.remaining is a phase quantile",
        "runnable": "directives.runnable filters directives; planner.runnable filters steps",
        "start": "jobs.start and perception.start start different things",
        "add": "directives.add and world.add are unrelated",
        "current": "directives.current and route.current are unrelated",
    }

    def test_no_public_function_name_is_defined_twice(self):
        dupes = {n: mods for n, mods in public_functions().items()
                 if len(mods) > 1 and n not in self.ALLOWED}
        self.assertEqual(dupes, {}, "same name, two modules: say which one owns the fact, or merge them")


class ConstantsLiveInTheBeliefTable(unittest.TestCase):
    """Numbers that describe the WORLD belong in play.toml, where they can be declared unmeasured and fitted."""

    # Modules that may hold bare world numbers, and why.
    EXEMPT = {"data", "knowledge", "blueprints", "recipes", "scenarios", "route", "api", "paths", "tape",
              "decide", "ui", "review", "world", "nav", "retry", "skill", "arbiter", "combat_tape"}

    def test_the_planner_reads_its_numbers_from_the_table(self):
        from bonobo.survival import CONFIG
        table = set()
        for section in CONFIG.values():
            if isinstance(section, dict):
                table.update(section)
        # The layers that price things must not invent numbers: they read them.
        for mod in ("survival", "threat", "priority", "actions", "solve"):
            tree = ast.parse(source(mod))
            named = {n.targets[0].id for n in tree.body
                     if isinstance(n, ast.Assign) and len(n.targets) == 1
                     and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)
                     and isinstance(n.value.value, float)}
            stray = {n for n in named if n.isupper() and n.lower() not in table}
            self.assertLessEqual(len(stray), 4, f"{mod} keeps world numbers of its own: {sorted(stray)}")


class EveryBeliefIsDeclared(unittest.TestCase):
    def test_nothing_in_the_table_is_silently_assumed(self):
        """A number that has never been measured must say so, or it will be read as fact."""
        from bonobo.survival import CONFIG, UNMEASURED
        measured_sections = ("mobs", "player")     # fitted from tapes elsewhere
        undeclared = []
        for name, section in CONFIG.items():
            if name in measured_sections or not isinstance(section, dict):
                continue
            for key, value in section.items():
                if isinstance(value, (int, float)) and key not in UNMEASURED and key != "unmeasured":
                    undeclared.append(f"{name}.{key}")
        # Not zero yet — the table predates this rule and twenty-two numbers are still bare. The test holds the
        # line where it is: a NEW belief must be declared, and this ceiling may only ever be lowered.
        self.assertLessEqual(len(undeclared), 22, f"undeclared beliefs: {sorted(undeclared)}")


class NothingReferencesWhatIsGone(unittest.TestCase):
    """A function that was moved away must not leave its callers pointing at nothing.

    `combat.hazard_points` was moved; `test_offline` still called it, and the whole file stopped importing — which
    took every check in it out of the suite silently. An import error is not a failing test, it is a missing test,
    and that is worse.
    """

    def defined(self):
        """{module: {public names it defines}} — functions, classes and module-level assignments."""
        out = {}
        for mod, tree in modules():
            names = set()
            # Module level includes what `with` and `try` blocks bind there — `CONFIG` is opened inside a `with`.
            stack = list(tree.body)
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[-1] for a in node.names)
                elif isinstance(node, (ast.With, ast.Try, ast.If)):
                    stack += node.body + getattr(node, "orelse", []) + getattr(node, "finalbody", [])
                    for handler in getattr(node, "handlers", []):
                        stack += handler.body
            out[mod] = names
        return out

    def test_every_dotted_reference_to_a_package_module_resolves(self):
        known = self.defined()
        missing = []
        for mod, tree in modules():
            # Only names this file actually imported as modules; a local variable may share a module's name.
            imported, shadowed = set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                    imported.update((a.asname or a.name) for a in node.names if (a.asname or a.name) in known)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    shadowed.add(node.id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    shadowed.update(a.arg for a in node.args.args + node.args.kwonlyargs)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    target = node.value.id
                    if target in imported and target not in shadowed \
                            and node.attr not in known[target] and not node.attr.startswith("_"):
                        missing.append(f"{mod}: {target}.{node.attr}")
        self.assertEqual(sorted(set(missing)), [], "these call something that no longer exists")

    # Files whose import runs their whole body (check-style suites). The start-up gate leaves them for the
    # check-point, and importing them here would drag that cost back in — three seconds, every restart.
    SLOW = {"test_offline", "test_acceptance", "test_incidents"}

    def test_the_tests_themselves_all_import(self):
        """A test file that cannot be imported reports nothing and looks like success."""
        import importlib
        import os as _os
        here = _os.path.dirname(_os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        broken = []
        for name in sorted(_os.listdir(here)):
            if not (name.startswith("test_") and name.endswith(".py")) or name == _os.path.basename(__file__):
                continue
            module = name[:-3]
            if module in self.SLOW or module in sys.modules:
                continue      # the runner already imported it: importing again just re-runs its module body
            try:
                importlib.import_module(module)
            except unittest.SkipTest:
                continue
            except Exception as e:
                broken.append(f"{name}: {type(e).__name__}: {e}")
        self.assertEqual(broken, [], "a test file that does not import is a test that does not run")


if __name__ == "__main__":
    unittest.main()


class NoNameHidesItsOwnModule(unittest.TestCase):
    """A parameter may not take the name of something its module defines.

    `perception.pressure_now(…, ground=None)` hid `perception.ground` — the walkable field — so inside the one
    function about pressure the field was unreachable, and `beliefs.slots_cost_s(count, …)` hid `beliefs.count`,
    the door that says how much a belief is trusted. Both read perfectly and both are traps for the next edit.
    """

    def test_no_parameter_shadows_a_module_level_name(self):
        import ast
        import pathlib
        package = pathlib.Path(__import__("bonobo").__file__).parent
        hidden = {}
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text())
            top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            top |= {t.id for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                args = fn.args
                names = [p.arg for p in args.posonlyargs + args.args + args.kwonlyargs]
                names += [x.arg for x in (args.vararg, args.kwarg) if x]
                for name in sorted(set(names) & top):
                    hidden[f"{path.name}:{fn.lineno} {fn.name}()"] = name
        self.assertEqual(hidden, {}, "a parameter hiding a name of its own module")
