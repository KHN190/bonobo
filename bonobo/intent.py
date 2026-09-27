"""What the agent means to do, as the layers that decided it — for the screen, not the log. The HUD showed the current mod task ("mine -447 61 68") and nothing above it, so "walk halfway there and turn back" looked like a bug when it was a decision (dusk: heading home). Each layer that takes a round says what it is doing in one line; the stack is published every round to MC_DATA/intent.json and to the jar as POST /hud {"lines": [...]} (rendered under the task line; a jar without the route answers 404 and nothing is lost). Layers, top first: survival · safety · threat · goal · step · alternatives. A layer that did not act this round is cleared, so the stack always reads as the reason for the task on screen."""

import json
import os

from . import api, paths

LAYERS = ("survival", "safety", "threat", "goal", "step", "alternatives")
FILE = paths.data("intent.json")
MAX_CHAT = 230        # one chat line; the file keeps the full stack

_state = {}
_last_sent = None

def say(layer, text):
    """Log a line and make it the layer's intention (one call, one text: the log and the screen never disagree)."""
    api.log(text)
    set(layer, text)

def set(layer, text):
    if layer not in LAYERS:
        raise ValueError(f"unknown intent layer {layer!r}")
    _state[layer] = text

def clear(*layers):
    for layer in layers or LAYERS:
        _state.pop(layer, None)

def lines():
    """Two lines, and only two: what the agent is doing, and what it does next."""

    top = next((l for l in LAYERS if _state.get(l)), None)
    if top is None:
        return []
    # "next: …" belongs to the goal: under survival or safety it would read as still meaning the goal
    if top == "goal" and _state.get("step"):
        return [_state["goal"], _state["step"]]
    return [_state[top]]

def full():
    """Every layer, for the file and `mc.py intent`."""
    return [f"{layer}: {_state[layer]}" if layer not in ("goal", "step", "alternatives") else _state[layer]
            for layer in LAYERS if _state.get(layer)]

def publish():
    """Write the stack and push it to the jar. Only when it changed: the HUD is repainted by the jar, not by us."""
    global _last_sent
    out = lines()
    if out == _last_sent:
        return
    _last_sent = out
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        with open(FILE, "w") as f:
            json.dump({"lines": full(), "hud": out}, f)
    except OSError:
        pass
    try:
        api.post("/hud", {"lines": out})
        return
    except Exception:
        pass          # no /hud route on this jar: fall back to the chat line, which scrolls and holds several
    try:
        # the chat line is the only screen channel: the stack in one short message, only when it changed
        text = "[plan] " + " | ".join(out)
        api.post("/chat", {"message": text[:MAX_CHAT] + ("…" if len(text) > MAX_CHAT else "")})
    except Exception:
        pass          # the game is away: the file still says what we meant
