"""paths: our own files read and written one way each — a missing data file is empty, a damaged one raises; a log's
last unfinished line waits for the next read, any other bad line raises; a log write never raises (Faults counts
it), a data write does."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import paths  # noqa: E402
from check.round import renew_session  # noqa: E402


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))
        paths.Faults.n, paths.Faults.first = 0, None

    def at(self, name, text=None):
        p = os.path.join(self.dir, name)
        if text is not None:
            with open(p, "w") as f:
                f.write(text)
        return p

    def test_read_json(self):
        rows = [("missing: empty", None, {}, {}),
                ("whole", '{"a": 1}', {}, {"a": 1}),
                ("must fail: damaged raises, never read as empty", '{"a": ', {}, ValueError)]
        for why, text, empty, want in rows:
            with self.subTest(why):
                p = self.at("d.json", text) if text is not None else self.at("none.json")
                if want is ValueError:
                    with self.assertRaises(ValueError):
                        paths.read_json(p, empty)
                else:
                    self.assertEqual(paths.read_json(p, empty), want)

    def test_read_jsonl(self):
        a, b = json.dumps({"n": 1}), json.dumps({"n": 2})
        rows = [("missing", None, {}, ([], 0)),
                ("whole lines", f"{a}\n{b}\n", {}, ([{"n": 1}, {"n": 2}], len(a) + len(b) + 2)),
                ("the last line unfinished: left for the next read", f"{a}\n{b[:4]}", {},
                 ([{"n": 1}], len(a) + 1)),
                ("from an offset", f"{a}\n{b}\n", {"offset": len(a) + 1}, ([{"n": 2}], len(a) + len(b) + 2)),
                ("a tail cuts its first line", f"{a}\n{b}\n", {"tail": len(b) + 3}, ([{"n": 2}], len(a) + len(b) + 2)),
                ("must fail: a bad middle line raises", f"{a}\n{{bad\n{b}\n", {}, ValueError)]
        for why, text, kw, want in rows:
            with self.subTest(why):
                p = self.at("l.jsonl", text) if text is not None else self.at("none.jsonl")
                if want is ValueError:
                    with self.assertRaises(ValueError):
                        paths.read_jsonl(p, **kw)
                else:
                    self.assertEqual(paths.read_jsonl(p, **kw), want)

    def test_writes(self):
        p = self.at("w.json")
        paths.save_json(p, {"a": 1})
        self.assertEqual(paths.read_json(p, {}), {"a": 1})
        self.assertFalse(os.path.exists(p + ".tmp"))
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            paths.append(p, "x\n", "the log")                       # a log: counted, not raised
            paths.save_json(p, {"a": 2}, log="the state file")
            with self.assertRaises(OSError):                        # must fail: a data write never shrugged off
                paths.save_json(p, {"a": 3})
        self.assertEqual(paths.Faults.n, 2)
        self.assertTrue(paths.Faults.first.startswith("the log: OSError"))
        self.assertEqual(paths.read_json(p, {}), {"a": 1})          # the failed writes left the file whole


class Session(unittest.TestCase):
    def test_renewed_in_place(self):
        rows = [("a dict back to its factory's", dict, lambda d: d.update(a=1), {}),
                ("a list emptied", list, lambda x: x.append(1), []),
                ("a dict literal's maker", lambda: {"t": 0}, lambda d: d.update(t=5), {"t": 0})]
        for why, factory, use, want in rows:
            with self.subTest(why):
                obj = paths.session(f"test.{why}", factory)
                use(obj)
                held = obj                                          # a holder from before the renewal
                renew_session()
                self.assertEqual(held, want)                        # must fail: renewed by rebinding, a holder keeps the old
                self.assertIs(held, obj)


if __name__ == "__main__":
    unittest.main()
