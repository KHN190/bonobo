"""A broad `except Exception` hides the bug it catches. Each one left in the package is a named guard — a thread or
loop that must not die, a bench that records a crash — that says why (`# guard:`) and writes the traceback (itself,
or through `api.unexpected`, which writes it to detail.log). The kept list may only shrink."""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# (file, enclosing function) → why a broad catch is right there. Only shrinks.
KEPT = {
    ("bonobo/brain.py", "Brain.attempt"): "any step failure is the failure policy's to count",
    ("bonobo/brain.py", "autoplay"): "the main loop must not die on a bug in one round",
    ("bonobo/arbiter.py", "Motion.holder"): "a release check that raises must end the lease, not hold the body",
    ("bonobo/tape.py", "_extras"): "a registered snapshot that raises must not stop the tape",
    ("bonobo/tools/incidents.py", "main"): "one unreplayable incident must not stop the listing",
    ("bonobo/bench/runner.py", "prebuild.work"): "a prebuild thread's failure must not kill the bench",
    ("bonobo/bench/runner.py", "_run_row"): "the skill's failure is a result the bench records",
    ("bonobo/bench/runner.py", "check_parts"): "a check word that raised is a readout",
    ("bonobo/bench/runner.py", "_row_verdict"): "the fight readout and the check are recorded, never a pass",
    ("bonobo/bench/runner.py", "run_idle"): "a hook or check of ours that raised is recorded by the idle row",
    ("bonobo/perception.py", "Watcher.run"): "the only watcher for lava, drowning and mobs must not die (4 sites)",
    ("bonobo/perception.py", "perceived"): "a reading we cannot take never stops the threat answer",
    ("bonobo/perception.py", "field_around"): "a field we cannot build keeps the last one",
    ("bonobo/fight_loop.py", "_engagement"): "the engagement's thread: said, and the body handed back",
    ("bonobo/fight_loop.py", "lease_done"): "a release judgement we cannot make keeps the body",
    ("bonobo/fight_loop.py", "carry"): "a failed answer is decided again without it (_refail), not the fight's end",
}
MAX_SITES = 21         # broad catches in the package now (a kept function may hold two); only goes down


def broad_excepts(source, path):
    """Pure: [(path, enclosing function, handler source)] for every `except Exception` / `except BaseException` /
    bare `except:` in `source`."""
    tree = ast.parse(source)
    out = []

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, f"{where}.{child.name}" if where else child.name)
                continue
            if isinstance(child, ast.ExceptHandler):
                t = child.type
                names = [t] if not isinstance(t, ast.Tuple) else list(t.elts)
                if t is None or any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names):
                    out.append((path, where, ast.get_source_segment(source, child) or ""))
            walk(child, where)
    walk(tree, "")
    return out


def unlisted(sites, kept):
    """Pure: the broad catches not in `kept`, and the kept ones that write no traceback."""
    bad = [f"{p}:{w} is not a kept guard" for p, w, _s in sites if (p, w) not in kept]
    bad += [f"{p}:{w} writes no traceback" for p, w, s in sites
            if (p, w) in kept and "traceback" not in s and "unexpected(" not in s]
    bad += [f"{p}:{w} gives no reason ('# guard:')" for p, w, s in sites if (p, w) in kept and "# guard:" not in s]
    return bad


def sites_in_package():
    out = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "bonobo")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/")
            with open(os.path.join(ROOT, path)) as f:
                out += broad_excepts(f.read(), path)
    return out


class BroadExcepts(unittest.TestCase):
    GUARD = ("def f():\n    try:\n        g()\n    except Exception:  # guard: must not die\n"
             "        log(traceback.format_exc())\n")
    SILENT = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    SAID = ("def f():\n    try:\n        g()\n    except Exception as e:  # guard: must not die\n"
            "        api.unexpected('f', e, 'why')\n")
    NARROW = "def f():\n    try:\n        g()\n    except McError:\n        pass\n"

    def test_rows(self):
        kept = {("m.py", "f"): "a guard"}
        # (source, kept) → what is wrong
        rows = [("a narrow catch: nothing to say", self.NARROW, {}, []),
                ("a kept guard with its reason and traceback", self.GUARD, kept, []),
                ("must fail: a broad catch nobody kept", self.SILENT, {}, ["m.py:f is not a kept guard"]),
                ("a kept guard said through api.unexpected (it writes the traceback)", self.SAID, kept, []),
                ("a kept guard that swallows silently", self.SILENT, kept,
                 ["m.py:f writes no traceback", "m.py:f gives no reason ('# guard:')"])]
        for name, source, k, want in rows:
            with self.subTest(name):
                self.assertEqual(unlisted(broad_excepts(source, "m.py"), k), want)

    def test_the_package(self):
        sites = sites_in_package()
        self.assertEqual(unlisted(sites, KEPT), [])
        # the ratchet: never more broad catches than kept guards, and no kept guard that is gone
        self.assertLessEqual(len(sites), MAX_SITES, "more broad catches than the ratchet allows")
        gone = set(KEPT) - {(p, w) for p, w, _s in sites}
        self.assertEqual(gone, set(), "a kept guard went: take it off the list (it only shrinks)")


if __name__ == "__main__":
    unittest.main()
