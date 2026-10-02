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
         ('chop', 'valuables_full'), ('nav', None, 'nav_sealed_in'), ('craft', None, 'craft_short_of_planks'),
         ('smelt', None, 'smelt_without_fuel'),
         
         
         
         
         
         ('nav', None, 'start_cell_on_a_fence'),
         ('hunt', 'valuables_full'), ('loot', None, 'empty_chest'), ('eat', None, 'eat_with_nothing'), ]),
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
         ('take_bed', ['take'], 'a village bed 6 blocks away → carried',
          [('floor',), ('setblock', ('@', 6, 0, 0), 'red_bed'), ('stand',)], ('skill', 'take', 'bed', 1, ['red_bed']),
          ('gain', 'bed', 1), 25),
         ('seek_remembered', ['seek'], 'memory says iron ore 16 blocks away → walked there',
          [('floor', 'stone', 10), ('fill', ('@', 10, -1, -8), ('@', 19, -1, 8), 'stone'),
           ('setblock', ('@', 17, 0, 0), 'iron_ore'), ('stand', -6)],
          ('seq', 1, ('!remember', 'note_seen', ['iron_ore', ('@', 17, 0, 0), 'minecraft:overworld'], {}),
           ('!skill', 'seek', ['iron_ore'])),
          ('arrived', ('@', 17, 0, 0), SEEK_RANGE), 25),
         ('open_space_from_shaft', ['move_to_open_space'],
          'a full bag at the bottom of a 1×1 shaft → out where it is open',
          [('floor', 'stone', 8, 4), ('floor', 'air', 0), ('stand', 0, -3), ('give', 'dirt', 2304),
           ('give', 'stone_pickaxe')],
          ('skill', 'move_to_open_space'), ('room_to_work',), 25),
         ('repair_broken_hut', ['repair_site'], 'a remembered hut with two wall blocks knocked out → rebuilt',
          [('floor',), ('at', 'fill {0} {1} cobblestone hollow', ('@', 2, 0, -2), ('@', 6, 2, 2)),
           ('fill', ('@', 2, 0, 0), ('@', 2, 1, 0), 'air'), ('stand',), ('give', 'cobblestone', 8)],
          ('skill', 'repair_site', ('$call', 'broken_hut', '$ctx')),
          ('blocks', ('@', 2, 0, 0), ('@', 2, 1, 0), ('cobblestone',), 2), 25),
         ('wait_out_the_night', ['wait:day'], 'night in a sealed stone room, no bed → waited until morning',
          [('floor',), ('at', 'fill {0} {1} stone hollow', ('@', -2, 0, -2), ('@', 2, 4, 2)), ('stand', 0, 1),
           ('time', 23500)],
          ('skill', 'wait_for_day'), ('is_day',), 25),
         ('trade_bread', ['trade'], 'a farmer selling bread for an emerald, 3 emeralds → bread',
          [('floor',), ('stand',), ('give', 'emerald', 3),
           ('summon', 'villager', ('@', 3, 0, 0),
            '{NoAI:1b,VillagerData:{profession:"minecraft:farmer",level:2,type:"minecraft:plains"},Offers:{Recipes:[{buy:{id:"minecraft:emerald",count:1},sell:{id:"minecraft:bread",count:6},maxUses:12}]}}')],
          ('skill', 'trade', 'minecraft:bread'), ('gain', 'minecraft:bread', 6), 25),
         ('plant_wheat', ['plant_farm'], 'grass, seeds, a hoe, a water bucket → a wheat plot growing',
          [('floor', 'grass_block'), ('stand',), ('give', 'wheat_seeds', 2), ('give', 'stone_hoe'),
           ('give', 'water_bucket')],
          ('skill', 'plant_farm'), ('blocks', ('@', -4, 0, -4), ('@', 4, 0, 4), ('wheat',), 2), 25),
         ]),
    ('real', [
         ('seek_blocks_real', ['seek_blocks'], 'real terrain, a log column 16 blocks off → walked to it',
          ('skill', 'seek_blocks', ['oak_log', 'birch_log', 'spruce_log'], 1, 20),
          ('found_near', ['oak_log', 'birch_log', 'spruce_log'], 8), 25,
          # the real terrain's own trees within 12 cleared first (a 64-radius spread can land in a wood: found
          # within 8 at setup, 0 s), then the column 16 off: the walk to it is the row's
          [('cmd', 'execute at @p run fill ~-12 ~-4 ~-12 ~12 ~12 ~12 air replace #minecraft:logs'),
           ('cmd', 'execute at @p run fill ~16 ~ ~ ~16 ~4 ~ oak_log')], False, (), ['axe']),
         ('strip_mine_real', ['strip_mine_step'], 'real terrain, a stone pickaxe → a mining tunnel started',
          ('skill', 'strip_mine_step', 2), ('gain', 'minecraft:cobblestone', 2), 25,
          [('cmd', 'execute at @p run fill ~-1 17 ~-1 ~1 19 ~1 air'), ('cmd', 'execute at @p run tp @p ~ 17 ~')])]),
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
    dict(name='enter_nether', module='nether',
         doc='A lit 4×5 portal on stone ground → stand in it and arrive in the Nether.',
         scene=[('floor', 'stone', 6, 2), ('fill', ('@', -1, 0, 2), ('@', 2, 4, 2), 'obsidian'),
                ('fill', ('@', 0, 1, 2), ('@', 1, 3, 2), 'nether_portal[axis=x]'), ('tp', ('@', 0, 0, -3)),
                ('cmd', 'clear @p'), ('give', 'flint_and_steel')],
         run=('do', 'bonobo.nether.use_portal', ['$ctx', 'minecraft:the_nether'], {}),
         check=[('state', 'dimension', '==', 'minecraft:the_nether')], budget=20,
         expect=[(('@', 0, 1, 2), ('@', 1, 3, 2), 'nether_portal', 6, 6),
                 (('@', -1, 0, 2), ('@', 2, 4, 2), 'obsidian', 14, 14)],
         mod_extra=['state'], skills=['use_portal']),
    dict(name='locate_stronghold', module='nether',
         doc=("A flat sky plane under the skill's 200-block sideways leg, speed, 12 eyes → two throws, triangulated "
              'estimate within 64 blocks of /locate.'),
         scene=[('cmd', 'tp @p 10400 201 10400'), ('cmd', 'clear @p'), ('give', 'ender_eye', 12),
                ('give', 'cobblestone', 64), ('give', 'stone_pickaxe'), ('give', 'cooked_beef', 16),
                ('cmd', 'locate structure minecraft:stronghold')],
         run=('do', 'bonobo.nether.locate_stronghold', ['$ctx'], {}), before=[('do', 'stronghold_leg', ['$ctx'], {})],
         check=[('call', 'stronghold_error', [], '<=', 64)], budget=25, raw=True, release=True,
         skills=['locate_stronghold']),
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
    one_row("repair_two_pickaxes", ["repair_tool"], "two worn stone pickaxes → one",
            [("floor",), ("stand",), ("give", "stone_pickaxe[damage=100]"), ("give", "stone_pickaxe[damage=100]")],
            lambda ctx: _skill("repair_tool")(ctx, "pickaxe"),
            lambda api, inv: inv.count("minecraft:stone_pickaxe") == 1 and any(
                s_["id"] == "minecraft:stone_pickaxe" and s_.get("damage", 999) < 100 for s_ in inv.slots), 20),
    one_row("look_in_chest", ["look_in"], "an unopened chest of iron beside the body → what it holds noted, nothing moved",
            [("floor",), ("chest", ("@", 2, 0, 0), "iron_ingot 9"), ("stand",)],
            lambda ctx: _skill("look_in")(ctx, at(2, 0, 0)),
            _container_noted(2, 0, 0, "minecraft:iron_ingot", 9), limit()),
]
