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
         ('dig_out_morning', ['dig_out'], 'morning, sealed in a 1×1 pocket → out',
          [('floor',), ('fill', ('@', -1, 0, -1), ('@', 1, 2, 1), 'stone'),
           ('fill', ('@', 0, 0, 0), ('@', 0, 1, 0), 'air'), ('stand',), ('give', 'stone_pickaxe')],
          ('skill', 'dig_out'), ('not', ('!call', 'enclosed', [])), 25),
         ('contain_lava_pool', ['contain_lava'], 'an open lava pool beside the body → covered',
          [('floor',), ('fill', ('@', 2, -1, -1), ('@', 3, -1, 1), 'lava'), ('stand',), ('give', 'cobblestone', 16)],
          ('skill', 'contain_lava'), ('blocks', ('@', 2, -1, -1), ('@', 3, -1, 1), ('lava',), 0, 0), 25),
         ('light_the_room', ['light_area'], 'a dark 9×9 room, 8 torches → several placed',
          [('floor',), ('at', 'fill {0} {1} stone hollow', ('@', -6, 0, -6), ('@', 6, 3, 6)),
           ('fill', ('@', -5, 0, -5), ('@', 5, 2, 5), 'air'), ('stand',), ('give', 'torch', 8)],
          ('skill', 'light_area', 6, 4), ('blocks', ('@', -5, 0, -5), ('@', 5, 2, 5), ('torch', 'wall_torch'), 2), 25),
         ('enchant_pickaxe', ['enchant'],
          'an enchanting table, lapis, levels, an iron pickaxe → enchanted (lapis spent)',
          [('floor',), ('setblock', ('@', 2, 0, 0), 'enchanting_table'), ('stand',), ('give', 'iron_pickaxe'),
           ('give', 'lapis_lazuli', 6), ('cmd', 'experience add @p 30 levels')],
          ('skill', 'enchant_item', 'minecraft:iron_pickaxe'),
          ('all', ('!count', 'minecraft:lapis_lazuli', '<', 6), ('!slot_has', 'minecraft:iron_pickaxe', 'enchant')),
          25),
         ('fill_bottles_at_pond', ['fill_bottles'], 'a pond, 3 glass bottles → 3 water bottles',
          [('floor',), ('fill', ('@', 2, -1, -1), ('@', 3, -1, 1), 'water'), ('stand',), ('give', 'glass_bottle', 3)],
          ('skill', 'fill_bottles', 3), ('gain', 'minecraft:potion', 3), 25)
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
    dict(name='fill_water_bucket', module='fluids', doc='A 3×3 pond next to the player, empty bucket → water bucket.',
         scene=[('floor', 'stone', 4, 2), ('fill', ('@', 1, -1, -1), ('@', 3, -1, 1), 'water'), ('tp', ('@', -1, 0, 0)),
                ('cmd', 'clear @p'), ('give', 'bucket')],
         run=('do', 'bonobo.fluids.fill_water_bucket', ['$ctx'], {}),
         check=[('count', 'minecraft:water_bucket', '>=', 1)], budget=25,
         expect=[(('@', 1, -1, -1), ('@', 3, -1, 1), 'water', 9, 9),
                 (('@', -4, -1, -4), ('@', 4, -1, 4), 'stone', 72, 72), (('@', -4, 0, -4), ('@', 4, 3, 4), '*', 0, 0)],
         skills=['fill_water_bucket']),
    dict(name='enter_end', module='end', doc='An open end portal in a stronghold room → jump in and arrive in the End.',
         scene=[('floor', 'stone_bricks', 6, 2),
                ('fill', ('@', -1, 0, -2), ('@', 1, 0, -2), 'end_portal_frame[facing=south,eye=true]'),
                ('fill', ('@', -1, 0, 2), ('@', 1, 0, 2), 'end_portal_frame[facing=north,eye=true]'),
                ('fill', ('@', -2, 0, -1), ('@', -2, 0, 1), 'end_portal_frame[facing=east,eye=true]'),
                ('fill', ('@', 2, 0, -1), ('@', 2, 0, 1), 'end_portal_frame[facing=west,eye=true]'),
                ('fill', ('@', -1, 0, -1), ('@', 1, 0, 1), 'end_portal'), ('tp', ('@', 0, 0, -5)), ('cmd', 'clear @p')],
         run=('do', 'bonobo.end.enter_end', ['$ctx'], {}), check=[('state', 'dimension', '==', 'minecraft:the_end')],
         budget=20, expect=[(('@', -1, 0, -1), ('@', 1, 0, 1), 'end_portal', 9, 9)], mod=['travel'],
         skills=['enter_end']),
    dict(name='find_portal_room_fresh', module='end',
         doc=('A stronghold piece built every run (a brick corridor, the room 64 blocks on, past the scan), standing '
              'on the estimate → bricks followed, portal frame found and reached.'),
         scene=[('cmd', 'clear @p'), ('give', 'diamond_pickaxe'), ('give', 'cobblestone', 64),
                ('give', 'cooked_beef', 16), ('give', 'torch', 32), ('give', 'water_bucket')],
         run=('&portal_room_run',), before=[('&built_stronghold',)], check=[('call', 'portal_room_found', [])],
         budget=25, raw=True, release=True, skills=['find_portal_room']),
    dict(name='craft_chain_one_sitting', module='skills',
         doc=('3 logs and a table carried → planks, sticks and a wooden pickaxe crafted in one sitting (craft_chain: '
              'the table placed and taken back once, not per recipe)'),
         scene=[('floor',), ('stand',), ('cmd', 'clear @p'), ('give', 'oak_log', 3), ('give', 'crafting_table')],
         run=('skill', 'craft_chain', [('planks', 2), ('minecraft:stick', 1), ('minecraft:wooden_pickaxe', 1)]),
         before=[('start', 'craft_chain_one_sitting')], check=[('gain', 'minecraft:wooden_pickaxe', 1, 1)], budget=15,
         skills=['craft_chain'], tier_fixed='exception', tags={'base': 'craft'})
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
    dict(name="find_fortress",
         doc="Nether platform, a nether brick hall 18 blocks away → found and remembered as the fortress site.",
         module="nether", dimension="minecraft:the_nether", skills=["find_fortress"],
         setup=[f"fill {_c(at(-6, -2, -6))} {_c(at(20, -1, 6))} netherrack",
                f"fill {_c(at(16, 0, -3))} {_c(at(20, 4, 3))} nether_bricks hollow",
                f"tp @p {_c(at(0, 0, 0))}", "clear @p", "give @p cobblestone 32", "give @p cooked_beef 16"],
         expect=[(at(16, 0, -3), at(20, 4, 3), "nether_bricks", 60, 200)],
         run=lambda ctx: __import__("bonobo.nether", fromlist=["find_fortress"]).find_fortress(ctx),
         check=lambda api, inv: bool(__import__("bonobo.memory", fromlist=["Memory"]).Memory(NOTES)
                                     .sites("minecraft:the_nether", kinds=["fortress"])),
         budget=15),
    real_row("explore_for_animals_real", ["explore_for"], "real terrain, two cows 16 blocks off → found",
             ("skill", "explore_for", ["minecraft:cow", "minecraft:sheep", "minecraft:pig"], 1, 20),
             # judged by what the skill found and noted, never by animals near at setup (the old check, "within 24",
             # held the moment the cows were summoned 16 off: the row passed in 0 s whatever the skill did)
             _remembered_any(["minecraft:cow", "minecraft:sheep", "minecraft:pig"]), limit(),
             [("cmd", "execute at @p run summon cow ~16 ~3 ~"), ("cmd", "execute at @p run summon cow ~16 ~3 ~1")],
             True, before=[_forget_all("minecraft:cow"), _forget_all("minecraft:sheep"), _forget_all("minecraft:pig")])
]
