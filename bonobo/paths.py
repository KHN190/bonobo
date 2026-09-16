"""Where everything lives. The only module that knows a filesystem layout.

Code and runtime data are different things with different lifetimes: the package is cloned, read and diffed; the data
is one player's world memory, decision recordings, bench output and combat tapes, none of which belongs in a
repository. Every module used to spell out `~/minecraft-claude-bridge/<something>` for itself — twenty-five copies of
one person's folder name, compiled into a library other people are meant to run.

So: one function, one environment variable, one default that follows the platform's convention.

    MC_DATA      where runtime data goes        (default: $XDG_DATA_HOME/bonobo, else ~/.local/share/bonobo)
    MC_INSTANCE  the Minecraft instance to read (config token, logs); no default that assumes a launcher
    MC_API       the mod's HTTP endpoint        (default: http://127.0.0.1:27599)

Individual files keep their own overrides (MC_NOTES, MC_ROUTE, …) so an experiment can point one file somewhere else
without moving the rest.
"""
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
    """A path inside the data directory. `env` names an environment variable that overrides this one file.

    Nothing is created here: callers that write use `ensure()` first, and callers that read tolerate absence — a
    fresh clone has no world memory and that is not an error.
    """
    if env:
        override = os.environ.get(env)
        if override:
            return os.path.expanduser(override)
    return os.path.join(data_dir(), *parts)


def ensure(path):
    """Make sure a file's directory exists; returns the path, so it can wrap a `data(...)` call inline."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def instance_dir():
    """The Minecraft instance directory — where the mod writes its config (and therefore the API token) and logs.

    No default: launchers put profiles in different places and guessing one person's launcher is how a library ends
    up only working on one machine. Callers that need it explain how to set it when it is missing.
    """
    return os.path.expanduser(os.environ.get("MC_INSTANCE", ""))


def api_base():
    return os.environ.get("MC_API", "http://127.0.0.1:27599")
