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
         ('brew_fire_resistance_stand', ['brew:fire_resistance'],
          'a brewing stand, water bottles, wart, magma cream, blaze powder → fire resistance (the cream spent)',
          [('floor',), ('setblock', ('@', 2, 0, 0), 'brewing_stand'), ('stand',),
           ('give', 'potion[potion_contents={potion:"minecraft:water"}]', 3), ('give', 'nether_wart'),
           ('give', 'magma_cream'), ('give', 'blaze_powder', 2)],
          ('skill', 'brew_fire_resistance'), ('slot_has', 'minecraft:potion', 'fire_resistance'), 25, 60),
         ('collect_auto_smelter', ['collect_machine'],
          'a remembered auto smelter whose output chest holds 8 ingots → taken',
          [('floor',), ('chest', ('@', 3, 0, 0), 'iron_ingot 8'), ('stand',)],
          ('skill', 'collect_machine', ('$call', 'bench_machine', '$ctx', ('@', 3, 0, 0))),
          ('gain', 'minecraft:iron_ingot', 8), 25),
         ('bridge_the_gap', ['bridge_toward'], 'a 6-block gap in the floor toward the target, blocks carried → across',
          [('floor',), ('fill', ('@', 2, -3, -8), ('@', 7, -1, 8), 'air'), ('stand',), ('give', 'cobblestone', 16)],
          ('skill', 'bridge_toward', ('@', 9, 0, 0)), ('arrived', ('@', 9, 0, 0), TRAVEL_RANGE), 25),
         ('breed_cows', ['breed'], 'two cows in a pen, wheat carried → wheat spent on them',
          [('floor', 'grass_block'), ('pen', 'cow', 2), ('stand',), ('give', 'wheat', 4)], ('skill', 'breed'),
          ('mobs_near', 'minecraft:cow', 3), 25),
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
    dict(name='cast_portal', module='building',
         doc=('A 3×3 lava pool beside the body; water bucket, bucket, 16 cobblestone, flint and steel → a portal frame '
              'cast in place and lit (no obsidian carried, no diamond pickaxe).'),
         scene=[('floor',), ('fill', ('@', 2, -1, -1), ('@', 4, -1, 1), 'lava'), ('tp', ('@', 0, 0, 0)),
                ('cmd', 'clear @p'), ('give', 'water_bucket'), ('give', 'bucket'), ('give', 'cobblestone', 16),
                ('give', 'flint_and_steel')],
         run=('do', 'bonobo.building.cast_portal', ['$ctx'], {}),
         check=[('call', 'count_blocks', ['$api', ('@', -8, -1, -8), ('@', 8, 6, 8), 'nether_portal'], '>=', 1)],
         budget=25,
         expect=[(('@', 2, -1, -1), ('@', 4, -1, 1), 'lava', 9, 9), (('@', -8, 0, -8), ('@', 8, 4, 8), '*', 0, 0)],
         skills=['cast_portal']),
    dict(name='build_light_portal', module='building',
         doc='Flat stone ground, 10 obsidian + 4 cobblestone + flint and steel → a lit nether portal.',
         scene=[('floor', 'stone', 8, 2), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'), ('give', 'obsidian', 10),
                ('give', 'cobblestone', 16), ('give', 'flint_and_steel')],
         run=('do', 'bonobo.building.build_blueprint', ['$ctx', 'nether_portal', ('@', 0, 0, 0)], {}),
         check=[('call', 'count_blocks', ['$api', ('@', -8, 0, -8), ('@', 8, 6, 8), 'nether_portal'], '>=', 6)],
         budget=25,
         expect=[(('@', -8, -1, -8), ('@', 8, -1, 8), 'stone', 289, 289), (('@', -8, 0, -8), ('@', 8, 6, 8), '*', 0, 0)],
         skills=['build_blueprint']),
    dict(name='barter_piglin', module='nether',
         doc='Nether platform, 3 piglins, 2 gold ingots and a carried gold helmet → wear it, barter, collect trades.',
         scene=[('floor', 'netherrack', 8, 2), ('at', 'fill {0} {1} glass hollow', ('@', -9, 0, -9), ('@', 9, 4, 9)),
                ('fill', ('@', -8, 0, -8), ('@', 8, 3, 8), 'air'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('cmd', 'item replace entity @p armor.feet with golden_boots'), ('give', 'gold_ingot', 2),
                ('give', 'golden_helmet'), ('give', 'iron_sword'),
                ('summon', 'piglin', ('@', 4, 0, 0), '{PersistenceRequired:1b}'),
                ('summon', 'piglin', ('@', -4, 0, 2), '{PersistenceRequired:1b}'),
                ('summon', 'piglin', ('@', 2, 0, -4), '{PersistenceRequired:1b}')],
         run=('do', 'bonobo.nether.barter_piglin', ['$ctx', 2], {}),
         check=[('count', 'minecraft:gold_ingot', '<=', 1), ('call', 'trades', ['$inv'], '>=', 1)],
         no_detail='the report line is code: <lambda>: a lambda; a lambda of (inv)', budget=25,
         dimension='minecraft:the_nether', combat=True,
         expect=[(('@', -8, -1, -8), ('@', 8, -1, 8), 'netherrack', 289, 289)],
         expect_entities=[('minecraft:piglin', 3)], tick_rate=60, skills=['barter_piglin']),
    dict(name='await_collect_job', module='farming',
         doc='a remembered furnace job, due, its 3 ingots in the output 2 blocks off → awaited and collected',
         scene=[('floor',), ('setblock', ('@', 2, 0, 0), 'furnace'),
                ('at', 'item replace block {0} container.2 with iron_ingot 3', ('@', 2, 0, 0)), ('stand',)],
         run=('skill', 'await_job', 'minecraft:iron_ingot', 3),
         before=[('start', 'await_collect_job'), ('job_ready_at', ('@', 2, 0, 0), 'minecraft:iron_ingot', 3)],
         check=[('gain', 'minecraft:iron_ingot', 3)],
         budget=25, point='C', skills=['await_job'], tier_fixed='exception', tags={'base': 'sources', 'source': 'job'}),
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
    dict(name="activate_end_portal",
         doc="A stronghold portal ring, 9 frames with eyes and the 3 nearest empty, 3 eyes and a block → a block over "
             "the middle, stood on, the 3 eyes from there: an open end portal ≤ 3 s.",
         module="end", skills=["activate_end_portal"], mod=["travel", "use"], target_s=3.0,
         # 9 frames already hold an eye: the three on our side are the job, so the row fits the limit
         setup=[f"fill {_c(at(-6, -2, -6))} {_c(at(6, -1, 6))} stone_bricks",
                f"fill {_c(at(-1, 0, -2))} {_c(at(1, 0, -2))} end_portal_frame[facing=south]",
                f"fill {_c(at(-1, 0, 2))} {_c(at(1, 0, 2))} end_portal_frame[facing=north,eye=true]",
                f"fill {_c(at(-2, 0, -1))} {_c(at(-2, 0, 1))} end_portal_frame[facing=east,eye=true]",
                f"fill {_c(at(2, 0, -1))} {_c(at(2, 0, 1))} end_portal_frame[facing=west,eye=true]",
                f"fill {_c(at(-1, -1, -1))} {_c(at(1, -1, 1))} lava",
                # beside the ring, a block to put over the middle's lava (end.eye_plan)
                f"tp @p {_c(at(0, 0, -3))}", "clear @p", "give @p ender_eye 3", "give @p cobblestone 1"],
         expect=[(at(-2, 0, -2), at(2, 0, 2), "end_portal_frame", 12, 12)],
         # the speedrun standard from where the job is done: 12 eyes from one spot ≤ 3 s
         run=_timed(lambda ctx: _drain(__import__("bonobo.end", fromlist=["activate_end_portal"]).activate_end_portal(ctx))),
         # open: the 9 portal blocks, or already fallen through them (standing in the middle is the speedrun way)
         check=lambda api, inv: api.get("/state")["dimension"] == "minecraft:the_end"
         or _count_blocks(api, at(-1, 0, -1), at(1, 0, 1), "end_portal") == 9,
         budget=limit()),
    one_row("smelt_in_background", ["start_smelt_job", "collect_job"], "load a furnace, walk off, come back → ingots",
            [("floor",), ("stand",), ("give", "furnace"), ("give", "raw_iron", 2), ("give", "coal", 1)],
            lambda ctx: (_skill("start_smelt_job")(ctx, "minecraft:iron_ingot", "minecraft:raw_iron", 2, "coal"),
                         _chat("tick sprint 400"), time.sleep(2),
                         _skill("collect_job")(ctx, ctx.mem.jobs("minecraft:overworld")[0]))[2],
            _gain("minecraft:iron_ingot", 2), limit()),
    one_row("anvil_repair_pickaxe", ["repair"], "an anvil, a worn diamond pickaxe, diamonds, levels → repaired",
            [("floor",), ("setblock", ("@", 2, 0, 0), "anvil"), ("stand",), ("give", "diamond_pickaxe[damage=1200]"),
             ("give", "diamond", 2), ("cmd", "experience add @p 20 levels")],
            lambda ctx: _skill("anvil_repair")(ctx, "minecraft:diamond_pickaxe", "minecraft:diamond"),
            lambda api, inv: any(s_["id"] == "minecraft:diamond_pickaxe" and s_.get("damage", 0) < 1200
                                 for s_ in inv.slots), limit()),
]
