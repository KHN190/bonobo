"""The dragon fight's skills, contracts only: to be rewritten."""

from . import knowledge as _k
from .skill import skill


@skill(gives=["state:in_pit"], remaining=_k.walled_sides, needs={}, budget=240, stall=90, soft=True)
def build_bed_pit(ctx):
    raise NotImplementedError("dragon: to be rewritten")


@skill(gives=["state:dragon_perched"], remaining=_k.dragon_phase({5, 6, 7}), needs={}, budget=180, stall=120, soft=True)
def await_perch(ctx):
    raise NotImplementedError("dragon: to be rewritten")


@skill(gives=["state:window_used"], remaining=_k.window_over({6, 7}), needs={"bed": 1}, budget=120, stall=60, soft=True)
def bed_bomb_window(ctx):
    raise NotImplementedError("dragon: to be rewritten")


@skill(gives=["state:enderman_off"], remaining=_k.none_of("minecraft:enderman", within=8.0), needs={}, budget=90, stall=45, soft=True)
def shake_enderman(ctx):
    raise NotImplementedError("dragon: to be rewritten")


@skill(gives=["state:dragon_dead"], remaining=_k.none_of("minecraft:ender_dragon", within=512.0), needs={}, budget=1800, stall=300, soft=True)
def slay_dragon(ctx):
    raise NotImplementedError("dragon: to be rewritten")
