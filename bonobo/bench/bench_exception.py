"""Bench table, exception tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""
from ..explore import SEEK_RANGE, TRAVEL_RANGE
from .core import KEPT_HP, ORIGIN

FAMILIES = [
    # S6: the player's hold stops every reflex (the clutch among them); its twin: the agent driving, the clutch runs
    ('fall', [('clutch_breaks_the_fall', False)]),
    ('base', [('sleep', 'sealed_pod'), ('smelt', 'nether'), 
         ('chop', 'pickup_lag'), ('craft', 'inventory_lag'),
         ('smelt', 'interrupt_twice'), ('nav', 'contested'),
         ('nav', 'player_takeover'), ('nav', 'buried_by_sand'), 
         ('chop', 'lava_edge'), 
         
         ('mine_iron', 'tool_one_use'),
         
         
         
         
         
         ('nav', None, 'start_cell_on_a_fence'), ]),
    ('one', [
         ('tidy_full_bag', ['room:tidy'], 'a full bag of junk → slots free',
          [('floor',), ('stand',), ('give', 'dirt', 2304)], ('skill', 'tidy_inventory'), ('free_slots', 2), 25),
         ('deposit_home_chest', ['room:deposit'], 'a home chest beside the body, a bag of cobblestone → stored',
          [('floor',), ('chest', ('@', 2, 0, 0)), ('stand',), ('give', 'cobblestone', 1280), ('give', 'dirt', 640)],
          ('seq', 1, ('!remember', 'add_site', ['home', ('@', 2, 0, 0), 'minecraft:overworld'], {'name': 'home'}),
           ('!skill', 'deposit')),
          ('all', ('!free_slots', 8), ('!dropped_nothing',)), 25),
         ('pod_open_ground', ['shelter:wall in'], 'night, open ground, 16 blocks → walled in',
          [('floor',), ('stand',), ('give', 'cobblestone', 16), ('time', 18000)], ('skill', 'pod'),
          ('blocks', ('@', -1, 0, -1), ('@', 1, 2, 1), ('cobblestone',), 9), 25),
         ('build_shelter_flat', ['build:shelter'], "flat stone, the hut's materials → a shelter standing",
          [('floor',), ('stand',), ('give', 'cobblestone', 32), ('give', 'oak_door'), ('give', 'torch', 2)],
          ('skill', 'build_shelter'),
          ('all', ('!blocks', ('@', -6, 0, -6), ('@', 6, 3, 6), ('cobblestone',), 14),
           ('!blocks', ('@', -6, 0, -6), ('@', 6, 3, 6), ('oak_door',), 1),
           ('!blocks', ('@', -6, 0, -6), ('@', 6, 3, 6), ('torch', 'wall_torch'), 1)),
          25),
         ('burrow_hillside', ['burrow'], 'night, a stone hillside beside the body → tunnelled in and sealed',
          [('floor',), ('fill', ('@', 2, 0, -4), ('@', 8, 4, 4), 'stone'), ('stand',), ('give', 'stone_pickaxe'),
           ('give', 'cobblestone', 8), ('time', 18000)],
          ('skill', 'burrow'), ('all', ('!alive', 18), ('!call', 'enclosed', [])), 25),
         ('find_air_capped', ['find_air'], 'under water with a stone cap, out of breath → air',
          [('fill', ('@', -4, -4, -4), ('@', 4, 3, 4), 'stone'), ('fill', ('@', -3, -3, -3), ('@', 3, 2, 3), 'water'),
           ('stand', 0, -3), ('give', 'stone_pickaxe')],
          ('skill', 'find_air'), ('all', ('!alive', 10), ('!breathing',)), 25),
         ('footing_in_water', ['reach:footing'], 'treading water, cobblestone carried → a block underfoot',
          [('tank', -4, 4, -4, 4, 0), ('floor', 'water', 4), ('stand',), ('give', 'cobblestone', 8)],
          ('skill', 'stand_on_a_block'), ('all', ('!state', 'onGround'), ('!under_feet', 'cobblestone')), 20),
         ('withdraw_from_chest', ['withdraw'], 'a chest of iron beside the body → 4 ingots taken out',
          [('floor',), ('chest', ('@', 2, 0, 0), 'iron_ingot 9'), ('stand',)],
          ('skill', 'withdraw', 'minecraft:iron_ingot', 4, ('@', 2, 0, 0)), ('gain', 'minecraft:iron_ingot', 4), 25),
         ]),
    ('place', [('place_observer_up', 'minecraft:observer', 'up', 'up', 'exception'),
         ]),
]
ROWS = [
    # E1/E3 (V4): a drop no walk reaches is left — the jar's approach never digs nor builds to it
    dict(name='collect_unreachable_drop', module='skills',
         doc='A diamond on a 3-high stone pillar, no walk up → the collect fails, the pillar standing, the drop there',
         scene=[('floor',), ('fill', ('@', 3, 0, 0), ('@', 3, 2, 0), 'stone'), ('stand',), ('cmd', 'clear @p'),
                ('give', 'stone_pickaxe'), ('give', 'cobblestone', 16),
                ('summon', 'item', ('@', 3, 3, 0), '{Item:{id:"minecraft:diamond",count:1},PickupDelay:0}')],
         run=('expect_failure', 'collect_unreachable_drop',
              ('!do', 'bonobo.api.run', [{'type': 'collect', 'radius': 6}],
               {'awaits': 'the drop out of every walk: refused', 'wait': 20}), 'reach|path|failed|picking'),
         check=[('unchanged', ('@', 3, 0, 0), ('@', 3, 2, 0), []), ('mobs_near', 'minecraft:item', 1)],
         skills=['travel_to']),
    # S3: inside the home only a table, furnace, chest, bed or light is placed
    dict(name='home_place_refused', module='skills',
         doc='A home box, cobblestone asked into a cell inside it → refused (home), the cell still air',
         scene=[('floor',), ('at', 'fill {0} {1} stone hollow', ('@', 2, 0, -2), ('@', 6, 3, 2)), ('stand',),
                ('cmd', 'clear @p'), ('give', 'cobblestone', 4)],
         before=[('home_box', ('@', 2, 0, -2), ('@', 6, 3, 2))],
         run=('expect_failure', 'home_place_refused', ('!place_into', ('@', 4, 0, 0), 'minecraft:cobblestone'), 'home'),
         check=[('blocks', ('@', 4, 0, 0), ('@', 4, 0, 0), ('cobblestone',), 0, 0)], skills=[]),
    dict(name='recover_items', module='reflexes',
         doc='Died 10 blocks away a minute ago, 3 diamonds lie there → walk back and pick them up.',
         scene=[('fill', ('@', -10, -2, -6), ('@', 12, -1, 6), 'stone'), ('tp', ('@', -6, 0, 0)), ('cmd', 'clear @p')],
         run=('do', 'bonobo.reflexes.recover_items', ['$ctx'], {}),
         before=[('seq', None, ('!remember', 'log_death', [('@', 6, 0, 0), 'minecraft:overworld'], {}),
                  ('!scene_now', [('summon', 'item', ('@', 6, 0, 0),
                                   '{Item:{id:"minecraft:diamond",count:3},Age:-32768}')]))],
         check=[('count', 'minecraft:diamond', '>=', 3)], budget=20,
         expect=[(('@', -10, -1, -6), ('@', 12, -1, 6), 'stone', 299, 299)], skills=['recover_items']),
]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
import threading as _threading
import time
from . import core
from .core import NOTES, _c, _chat, _count_blocks, _drain, at
from .vocab import PORTAL_8_OF_10, ROAD_TIMES, _queue, _road_reuse, _worn_head, one_row, real_row
from .words.brain import _container_noted, _fill_bag, _forget_all, _remembered_any
from .words.checks import _alive, _all, _base_count, _free_slots, _gain, _inv_now, _skill, _start
from .words.fight import NETHER_LAVA
from .words.runs import (_achieve_needs, _brain_rounds, _forget_skill_time, _hooks, _interrupt_when, _resume,
    _skill_within, _timed)
from .words.scene import _floor, _grove, _scene_params, _tp, limit, scene



CODE_ROWS = [
    dict(name="dead_flicker_on_respawn",
         doc="Killed at the start of the run: /state reads dead for a moment while the respawn loads — the brain must "
             "respawn, not call every skill dead, and still chop its 4 logs",
         module="brain", point="A", skills=["item:log"], tags={"base": "chop", "surprise": "dead_flicker"},
         setup=_grove((3, 0), (-3, 2)) + [_tp()],
         before=_hooks(_start("dead_flicker_on_respawn"), lambda ctx: _chat("kill @p")),
         run=lambda ctx: (_brain_rounds(15, lambda: not __import__("bonobo.api", fromlist=["get"]).get("/state")["dead"])(ctx),
                          _skill("chop")(ctx, 4))[1],
         check=_all(_alive(10), lambda api, inv: inv.count("log") >= 4), budget=limit()),
]
