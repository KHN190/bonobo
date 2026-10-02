"""Where everything lives. The only module that knows a filesystem layout. Code and runtime data are different things with different lifetimes: the package is cloned, read and diffed; the data is one player's world memory, decision recordings, bench output and combat tapes, none of which belongs in a repository. Every module used to spell out `~/minecraft-claude-bridge/<something>` for itself — twenty-five copies of one person's folder name, compiled into a library other people are meant to run. So: one function, one environment variable, one default that follows the platform's convention. MC_DATA      where runtime data goes        (default: $XDG_DATA_HOME/bonobo, else ~/.local/share/bonobo) MC_INSTANCE  the Minecraft instance to read (config token, logs); no default that assumes a launcher MC_API       the mod's HTTP endpoint        (default: http://127.0.0.1:27599) Individual files keep their own overrides (MC_NOTES, MC_ROUTE, …) so an experiment can point one file somewhere else without moving the rest."""

import os

APP = "bonobo"

def data_dir():
    """The directory holding this player's runtime data. Created on demand, never inside the package."""
    override = os.environ.get("MC_DATA")
    if override:
        return os.path.expanduser(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = os.path.expanduser(xdg) if xdg else os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP)

def data(*parts, env=None):
    """A path inside the data directory."""

    if env:
        override = os.environ.get(env)
        if override:
            return os.path.expanduser(override)
    return os.path.join(data_dir(), *parts)

def instance_dir():
    """The Minecraft instance directory — where the mod writes its config (and therefore the API token) and logs."""

    return os.path.expanduser(os.environ.get("MC_INSTANCE", ""))

def api_base():
    return os.environ.get("MC_API", "http://127.0.0.1:27599")

# -- writing and reading our own files: one way each
class Faults:
    """Log writes that failed: counted, the first kept (`mc.py status` shows them). A log never stops the agent; a
    data file's failure is raised by its writer instead."""
    n = 0
    first: "str | None" = None


def _failed(where, err):
    Faults.n += 1
    if Faults.first is None:
        Faults.first = f"{where}: {type(err).__name__}: {err}"


def append(path, text, where):
    """Append `text` to the log at `path` (a failed write counted in Faults, never raised)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(text)
    except OSError as e:
        _failed(where, e)


def rewrite(path, text, log=None):
    """Replace `path`'s content with `text` atomically (a temp file renamed over it). `log`: the file is a log, its
    failure counted in Faults under that name, not raised."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        if log is None:
            raise
        _failed(log, e)


def save_json(path, obj, log=None):
    """`obj` as indented JSON at `path`, written atomically (`rewrite`)."""
    import json
    rewrite(path, json.dumps(obj, indent=1, default=str), log)


def read_json(path, empty):
    """Our own JSON file: `empty` when it does not exist yet; a damaged one raises (a bug, never read as empty)."""
    import json
    if not os.path.exists(path):
        return empty
    with open(path) as f:
        return json.load(f)


def read_jsonl(path, tail=None, offset=None):
    """(records, end) of our own JSON-lines log: all of it, its last `tail` bytes, or what follows byte `offset`;
    ([], offset or 0) when it does not exist. Only the last line may be unfinished (a write in flight: no newline
    yet): left for the next read (`end` stops before it); any other bad line raises."""
    import json
    if not os.path.exists(path):
        return [], offset or 0
    with open(path, "rb") as f:
        start = offset if offset is not None else max(0, os.fstat(f.fileno()).st_size - tail) if tail else 0
        f.seek(start)
        data = f.read()
    done = data.rfind(b"\n") + 1         # through the last newline; after it, a line still being written
    lines = data[:done].decode("utf-8", "replace").split("\n")[:-1]
    if tail and offset is None and start > 0:
        lines = lines[1:]               # cut mid-line by the seek
    return [json.loads(ln) for ln in lines if ln], start + done


# -- session state: what outlives a life (events' bookkeeping, the tape's buffers), created here only, renewed in one
# call (tests' setUp: renew_session). A module's state made any other way is the static check's R8 finding.
_SESSION = {}           # name → (the object handed out, the factory that renews it)


def session(name, factory):
    """A session's container, made by `factory()` (dict, list, set or a dict literal's maker); reset_all renews it in
    place, so every holder keeps seeing the same object."""
    obj = factory()
    _SESSION[name] = (obj, factory)
    return obj


def renew_session():
    """Every registered session state back to its factory's contents (in place)."""
    for obj, factory in _SESSION.values():
        fresh = factory()
        obj.clear()
        (obj.update if isinstance(obj, (dict, set)) else obj.extend)(fresh)
