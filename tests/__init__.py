"""The offline tests never read or write the player's data: a run that did not come through runtests.py (which
gives each process a sandbox) still gets its own empty data directory here, before bonobo is imported."""
import os
import tempfile

if not os.environ.get("MC_DATA"):
    os.environ["MC_DATA"] = tempfile.mkdtemp(prefix="bonobo-tests-")
