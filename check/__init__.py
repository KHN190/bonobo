"""The offline checker. Runtime data (memory, tasks, tape) goes to a private temp dir: set before any bonobo module
resolves its paths (paths.data runs at import)."""
import os
import tempfile

DATA = tempfile.mkdtemp(prefix="check-")
os.environ["MC_DATA"] = DATA
os.environ.setdefault("MC_INSTANCE", DATA)
