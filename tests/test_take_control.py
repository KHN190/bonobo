"""A script that posts at its start takes the body first, through the one api.take_control (supervise2 08:39:
leave_bench posted with the player's toggle paused → PlayerTookControl, the gamerules never restored)."""
import ast
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bonobo import api  # noqa: E402
from bonobo.bench import core  # noqa: E402
from bonobo.tools import leave_bench  # noqa: E402


class PausedJar:
    """The jar with the player's toggle paused: every POST but /control refused until it is lifted."""

    def __init__(self):
        self.paused, self.posted = True, []

    def __call__(self, method, path, body=None, timeout=None):
        if method == "GET":
            return {"paused": self.paused} if path == "/status" else \
                {"x": 0.5, "y": 64.0, "z": 0.5, "dimension": "minecraft:overworld", "dead": False}
        if path == "/control":
            self.paused = body["paused"]
        elif self.paused:
            raise api.PlayerTookControl()
        self.posted.append(path)
        return {}


class TakeControl(unittest.TestCase):
    def test_leave_bench_with_the_toggle_paused(self):
        # must fail: the restore posted first, refused (PlayerTookControl), the world's settings left the bench's
        tmp = tempfile.mkdtemp()
        flag, chat = os.path.join(tmp, "test-world"), os.path.join(tmp, "latest.log")
        for p in (flag, chat):
            open(p, "w").close()
        jar = PausedJar()
        with mock.patch.object(api, "api", jar), mock.patch.object(leave_bench, "FLAG", flag), \
                mock.patch.object(core, "_chat_log", lambda: chat), mock.patch.object(core, "REPLY_POLL_S", 0.0):
            self.assertEqual(leave_bench.main(), 0)
        self.assertEqual(jar.posted.count("/chat"), len(core.restore_commands()))

    def test_every_posting_tool_takes_control(self):
        # a tool whose main posts (api.post, a bench send) starts with api.take_control
        tools = os.path.join(ROOT, "bonobo", "tools")
        for name in sorted(os.listdir(tools)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(tools, name), encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
            main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
            if main is None or not any(k in src for k in ("api.post(", "_send(", "api.run(")):
                continue
            with self.subTest(name):
                self.assertIn("api.take_control()", ast.get_source_segment(src, main))


if __name__ == "__main__":
    unittest.main()
