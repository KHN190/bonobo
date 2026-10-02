"""Bench table, common tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""
from .core import ORIGIN
from ..survive import DIG_IN_DEPTH

FAMILIES = [
    ('lava_strip', [('cross_lava_8', 8, 25, 'B')]),
    ('base', [('mine_stone', 'full_bag'), ('mine_iron', None, 'ore_buried'), ('smelt', None, 'furnace_on_slab')]),
    ('one', [
         ('reach_land_swim', ['reach:land'], 'night, treading water 10 blocks from shore → on dry land',
          [('tank', -8, 9, -8, 8, 1, -1, -4, 'glass', 'east'), ('fill', ('@', 10, -3, -8), ('@', 14, -1, 8), 'stone'),
           ('stand',), ('time', 18000)],
          ('skill', 'reach_land'), ('all', ('!state', 'onGround'), ('!not', ('!state', 'inWater'))), 25),
         ('dig_in_night', ['shelter:dig in'], 'night on stone, a pickaxe → three down, sealed',
          [('floor', 'stone', 8, 4), ('stand',), ('give', 'stone_pickaxe'), ('give', 'cobblestone', 8),
           ('time', 18000)],
          ('skill', 'dig_in'), ('all', ('!state', 'blockY', '<=', ORIGIN[1] - DIG_IN_DEPTH), ('!call', 'enclosed', [])), 25)]),
    ('place', [('place_furnace_north', 'minecraft:furnace', 'north', 'north', 'common')]),
    ('start', [('nav_from_ladder', 'a ladder on a wall',
          [('fill', ('@', 1, 0, 0), ('@', 1, 3, 0), 'stone'),
           ('fill', ('@', 0, 0, 0), ('@', 0, 3, 0), 'ladder[facing=west]')],
          (0, 2))]),
    ('door', [('press_door_to_chest', 'wall', 'iron', 2, False, True, 'outside', 'inside', False)]),
]
ROWS = [
    dict(name='cave_escape', module='nav',
         doc='Sealed in a dark 1×2 pocket 4 blocks under the platform, pickaxe + blocks → back on the surface platform.',
         scene=[('floor', 'stone', 6, 12), ('fill', ('@', 0, -5, 0), ('@', 0, -4, 0), 'air'), ('stand', 0, -5),
                ('cmd', 'clear @p'), ('give', 'stone_pickaxe'), ('give', 'cobblestone', 32)],
         run=('do', 'bonobo.nav.go_to', [('@', 3, 0, 3), ('$ctx', 'policy')], {'range_': 0.6}),
         check=[('arrived', ('@', 3, 0, 3), 0.6)], budget=25,
         expect=[(('@', -6, -1, -6), ('@', 6, -1, 6), 'stone', 169, 169)], mod=['travel'], point='B',
         skills=['travel_to']),
    dict(name='slice_nether_kit', module='brain',
         doc=('Slice: at a lit portal, the kit two steps short (one block, the gold helmet) → kit complete (food, '
              'blocks, gold helmet) without stepping into the Nether early, no loops, idle ≤ 15 s.'),
         scene=[('floor', 'grass_block', 8, 2), ('fill', ('@', 1, 0, -1), ('@', 1, 1, 1), 'stone'),
                ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'), ('cmd', 'time set day'), ('give', 'iron_pickaxe'),
                ('give', 'iron_sword'), ('give', 'bucket'), ('give', 'flint_and_steel'), ('give', 'gold_ingot', 5),
                ('give', 'crafting_table'), ('give', 'cooked_beef', 8), ('give', 'cobblestone', 31),
                ('summon', 'cow', ('@', -2, 0, 1))],
         run=('slice', ('!now', ('!any', ('!call', 'nether_kit_ready', []), ('!not', ('!call', 'in_overworld', [])))),
              0.4, None, [{'goal': 'milestone', 'args': {'name': 'nether kit'}}]),
         before=[('do', 'portal_beside_player', ['$ctx'], {})],
         check=[('slice_check', ('!now', ('!all', ('!call', 'nether_kit_ready', []), ('!call', 'in_overworld', []))))],
         detail=('&slice_detail',), expect=[(('@', 1, 0, -1), ('@', 1, 1, 1), 'stone', 6, 6)],
         expect_entities=[('minecraft:cow', 1)], point='C', chain=2),
    dict(name='eat_while_walking', module='skills',
         doc=('Hungry, cooked beef carried, a walk east until fed → fed on the way without an eat task, still walking '
              "forward while it chewed (ate_on_the_way over the walk's trace)"),
         scene=[('floor',), ('fill', ('@', 8, -3, -3), ('@', 20, -1, 3), 'stone'), ('stand', -2),
                ('give', 'cooked_beef', 4)],
         run=('&walk_once',), before=[('start', 'eat_while_walking'), ('&hunger_drained',)],
         check=[('call', 'walk_ate', [])], budget=25, skills=['goto'],
         tier_fixed='common', combat=False, stochastic=False, tags={'base': 'nav', 'state': 'hungry'}),
]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
from . import core
from .bench_bases import BASES
from .core import _achieve, _c, at
from .vocab import base_row
from .words.brain import SMELT_FURNACES, _interrupt_once_loaded, _iron_in_furnaces, _load_the_rest
from .words.checks import INTERRUPTS, RESUMED_LEFT, _all, _inv_now, _skill, _start
from .words.runs import _hooks, _resume
from .words.scene import _floor, _tp
CODE_ROWS = [
    base_row("chest_or_tree", "chop", surprise=dict(
        base="chop", doc="4 logs in a chest by the body, a tree 12 away: the brain takes the cheaper (plan-driven, "
                         "test point C)", point="C", replace_setup=True,
        # the tree 12 away as the doc says (the chop base's own oak stands 2 off: chopping it is the cheaper)
        scene=[("grove", (12, 0)), ("fill", ("@", 9, -1, -3), ("@", 15, -1, 3), "grass_block"), ("stand",),
               ("chest", ("@", 1, 0, 1), "oak_log 4")],
        # the planner takes from containers as last seen open (memory.note_container): opened once
        before=lambda ctx: core.BRAIN.mem.note_container(
            at(1, 0, 1), "minecraft:overworld", [{"id": "minecraft:oak_log", "count": 4}]),
        run=lambda ctx: _achieve(ctx, [("log", 4)], lambda: _inv_now().count("log") >= 4),
        # 4 logs gained, the base's tree left whole (its trunk, where the base built it)
        check=("all", ("!gain", "log", 4), ("!blocks", ("@", 12, 0, 0), ("@", 12, 6, 0), ("oak_log",), 3)))),
    dict(name="smelt_job_interrupted",
         doc="Three furnaces, 6 raw iron and coal; interrupted once the first furnace took its share → resumed by what "
             "is left in the bag: all 6 in the furnaces, none twice, the bag empty of raw iron",
         module="skills", point="A", skills=["start_smelt_job"], tier_fixed="common", combat=False, stochastic=False,
         tags={"base": "smelt", "timing": "interrupt_mid_work"},
         setup=_floor() + [f"setblock {_c(p)} furnace" for p in SMELT_FURNACES]
         + [_tp(), "give @p raw_iron 6", "give @p coal 3"],
         before=_hooks(_start("smelt_job_interrupted"), _interrupt_once_loaded),
         run=_resume("smelt_job_interrupted",
                     lambda ctx: _skill("start_smelt_job")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", 6, "coal"),
                     _load_the_rest),
         check=_all(lambda api, inv: inv.count("minecraft:raw_iron") == 0, lambda api, inv: _iron_in_furnaces() == 6,
                    lambda api, inv: INTERRUPTS.get("smelt_job_interrupted", 0) >= 1,
                    # mid-work: the resume found raw iron still to load (an interrupt landing after the last furnace
                    # counted as one, and the row passed in 1.6 s with nothing resumed)
                    lambda api, inv: RESUMED_LEFT.get("smelt_job_interrupted", 0) >= 1),
         budget=BASES["smelt"]["budget"]),       # an interrupted run keeps its base's time
]
