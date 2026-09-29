"""Bench table, common tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""

FAMILIES = [
    ('base', [('nav', 'canopy'), ('loot', 'canopy'), ('mine_stone', 'cave'), ('chop', 'night'),
         ('mine_stone', 'interrupt_mid_work'), ('mine_stone', 'full_bag')]),
    ('one', [('dig_in_night', ['shelter:dig in'], 'night on stone, a pickaxe → three down, sealed',
          [('floor', 'stone', 8, 4), ('stand',), ('give', 'stone_pickaxe'), ('give', 'cobblestone', 8),
           ('time', 18000)],
          ('skill', 'dig_in'), ('all', ('!state', 'blockY', '<', 200), ('!call', 'enclosed', [])), 25),
         ('reach_land_swim', ['reach:land'], 'night, treading water 10 blocks from shore → on dry land',
          [('tank', -8, 9, -8, 8, 1, -1, -4, 'glass', 'east'), ('fill', ('@', 10, -3, -8), ('@', 14, -1, 8), 'stone'),
           ('stand',), ('time', 18000)],
          ('skill', 'reach_land'), ('all', ('!state', 'onGround'), ('!not', ('!state', 'inWater'))), 25)]),
    ('place', [('place_furnace_north', 'minecraft:furnace', 'north', 'north', 'common'),
         ('place_furnace_south', 'minecraft:furnace', 'south', 'south', 'common'),
         ('place_furnace_east', 'minecraft:furnace', 'east', 'east', 'common'),
         ('place_furnace_west', 'minecraft:furnace', 'west', 'west', 'common')]),
    ('start', [('nav_from_stairs', 'a stair step', [('setblock', ('@', 0, 0, 0), 'oak_stairs[facing=east]')], (0, 0.5)),
         ('nav_from_slab', 'a bottom slab', [('setblock', ('@', 0, 0, 0), 'stone_slab')], (0, 0.5)),
         ('nav_from_farmland', 'farmland', [('setblock', ('@', 0, -1, 0), 'farmland')], ()),
         ('nav_from_ladder', 'a ladder on a wall',
          [('fill', ('@', 1, 0, 0), ('@', 1, 3, 0), 'stone'),
           ('fill', ('@', 0, 0, 0), ('@', 0, 3, 0), 'ladder[facing=west]')],
          (0, 2)),
         ('nav_from_water', 'a pool one deep', [('fill', ('@', -1, 0, -1), ('@', 1, 0, 1), 'water')], ())]),
]
# the taught-door room, one set of offsets for its scene and its lesson: walls x 4..8, z -3..3, roof y 3
DOOR_ROOM = {"lo": ('@', 4, 0, -3), "hi": ('@', 8, 3, 3), "in_lo": ('@', 5, 0, -2), "in_hi": ('@', 7, 2, 2),
             "door": (('@', 4, 0, 0), ('@', 4, 1, 0)), "bulb": ('@', 4, 1, -2), "comparator": ('@', 4, 1, -1),
             "press_out": ('@', 3, 1, -2), "press_in": ('@', 5, 1, -2), "chest": ('@', 7, 0, 0),
             "inside": ('@', 6, 0, 0), "reach": 0.5}     # reach + the walker's slack short of the door cell
# the hatch, one set of offsets for its scene and its lesson: a room below the floor (x 1..3, y -3..-2, z 0..3), the
# 2×2 hatch in its ceiling, the pistons either side in the floor layer, the powering ring outside the room
DOOR_HATCH = {"lo_ground": ('@', -4, -5, -3), "hi_ground": ('@', 7, -1, 5),
              "room_lo": ('@', 1, -3, 0), "room_hi": ('@', 3, -2, 3),
              "steps": (('@', 2, -3, 1), ('@', 2, -2, 0)),
              "hatch": (('@', 1, -1, 0), ('@', 2, -1, 0), ('@', 1, -1, 1), ('@', 2, -1, 1)),
              "pistons_w": (('@', -1, -1, 0), ('@', -1, -1, 1)), "pistons_e": (('@', 4, -1, 0), ('@', 4, -1, 1)),
              "repeaters_w": (('@', -2, -1, 0), ('@', -2, -1, 1)), "repeaters_e": (('@', 5, -1, 0), ('@', 5, -1, 1)),
              "wire": tuple([('@', 5, -1, 3), ('@', 6, -1, 3), ('@', 6, -1, 2), ('@', 6, -1, 1), ('@', 6, -1, 0),
                             ('@', 5, -1, 4)] + [('@', x, -1, 4) for x in range(4, -4, -1)]
                            + [('@', -3, -1, z) for z in (3, 2, 1, 0)]),
              "bulb": ('@', 3, -1, 3), "comparator": ('@', 4, -1, 3),
              "press_out": ('@', 3, 0, 3), "press_in": ('@', 3, -2, 3),
              "shell_lo": ('@', 0, -4, -1), "shell_hi": ('@', 4, -1, 4),
              "above": ('@', 2, 0, -2), "below": ('@', 3, -3, 2), "reach": 0.5}
# the side room: walls x 4..8, y 0..3, z -1..3, a 1×2 door in the west wall at z 1, its pistons in the wall corner
DOOR_SIDE = {"lo": ('@', 4, 0, -1), "hi": ('@', 8, 3, 3), "in_lo": ('@', 5, 0, 0), "in_hi": ('@', 7, 2, 2),
             "door": (('@', 4, 0, 1), ('@', 4, 1, 1)), "pistons": (('@', 4, 0, -1), ('@', 4, 1, -1)),
             "repeater_low": ('@', 4, 0, -2), "support": ('@', 3, 0, -1), "repeater_high": ('@', 3, 1, -1),
             "wire_block": ('@', 2, 0, -1), "wire": (('@', 4, 0, -3), ('@', 2, 1, -1)),
             "bulb": ('@', 2, 0, -3), "comparator_low": ('@', 3, 0, -3), "comparator_high": ('@', 2, 0, -2),
             "press": ('@', 1, 0, -3), "inside": ('@', 6, 0, 1), "outside": ('@', 1, 0, 1), "reach": 0.5}
ROWS = [
    dict(name='cave_escape', module='nav',
         doc='Sealed in a dark 1×2 pocket 4 blocks under the platform, pickaxe + blocks → back on the surface platform.',
         scene=[('floor', 'stone', 6, 12), ('fill', ('@', 0, -5, 0), ('@', 0, -4, 0), 'air'), ('stand', 0, -5),
                ('cmd', 'clear @p'), ('give', 'stone_pickaxe'), ('give', 'cobblestone', 32)],
         run=('do', 'bonobo.nav.go_to', [('@', 3, 0, 3), ('$ctx', 'policy')], {'range_': 0.6}),
         check=[('state', 'y', '>=', 199.5), ('state', 'onGround')], budget=25,
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
         detail=('&slice_detail',), budget=25, expect=[(('@', 1, 0, -1), ('@', 1, 1, 1), 'stone', 6, 6)],
         expect_entities=[('minecraft:cow', 1)], point='C', chain=2),
    dict(name='cross_lava_8', module='nav',
         doc='A 8-block lava strip between two stone platforms; cobblestone → reach the far side alive.',
         scene=[('fill', ('@', -3, -3, -4), ('@', 14, -3, 4), 'stone'),
                ('fill', ('@', -2, -2, -3), ('@', 13, -1, 3), 'lava'),
                ('fill', ('@', -2, -1, -3), ('@', 1, -1, 3), 'stone'),
                ('fill', ('@', 10, -1, -3), ('@', 13, -1, 3), 'stone'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('give', 'cobblestone', 64), ('give', 'diamond_pickaxe')],
         run=('do', 'bonobo.nav.go_to', [('@', 11, 0, 0), ('$ctx', 'policy')], {'range_': 1.5}),
         check=[('call', 'near', ['$api', ('@', 11, 0, 0), 2.5]), ('state', 'health', '>', 10)], budget=25,
         expect=[(('@', 2, -1, -3), ('@', 9, -1, 3), 'lava', 56, 56),
                 (('@', -2, -1, -3), ('@', 1, -1, 3), 'stone', 28, 28), (('@', -2, 0, -3), ('@', 13, 4, 3), '*', 0, 0)],
         point='B', skills=['travel_to']),
    dict(name='eat_while_walking', module='skills',
         doc=('Hungry, cooked beef carried, a walk east until fed → fed on the way without an eat task, still walking '
              "forward while it chewed (ate_on_the_way over the walk's trace)"),
         scene=[('floor',), ('fill', ('@', 8, -3, -3), ('@', 20, -1, 3), 'stone'), ('stand', -2),
                ('give', 'cooked_beef', 4)],
         run=('&walk_once',), before=[('start', 'eat_while_walking'), ('&hunger_drained',)],
         check=[('call', 'walk_ate', [])], budget=25, skills=['goto'],
         tier_fixed='common', combat=False, stochastic=False, tags={'base': 'nav', 'state': 'hungry'},
         expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='mine_while_hungry', module='skills',
         doc=('Hungry, cooked beef carried, 3 cobblestone to mine → mined without a pause to eat: no eat task, every '
              "bite inside the running mine, the bar no lower at the end (worked_fed over the run's trace)"),
         scene=[('floor',), ('stand',), ('give', 'wooden_pickaxe'), ('give', 'cooked_beef', 4)],
         run=('&mine_hungry',), before=[('start', 'mine_while_hungry'), ('&hunger_drained',)],
         check=[('gain', 'minecraft:cobblestone', 3), ('call', 'mine_fed', [])], budget=15,
         skills=['mine'], tier_fixed='common', combat=False, stochastic=False,
         tags={'base': 'mine_stone', 'state': 'hungry'},
         expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    # a taught door: a closed stone room, an iron door in its west wall held by a toggle — a waxed copper bulb in the
    # wall with a button on each face (outside, inside), read by a comparator beside the door's top half: one press
    # opens it and it stays open — and a chest within. Every cell below from DOOR_ROOM (scene and lesson alike)
    *[dict(name=name, module='skills', point='A', skills=skills, stochastic=False,
           doc=doc,
           scene=[('floor',), ('fill', R['lo'], R['hi'], 'stone'), ('fill', R['in_lo'], R['in_hi'], 'air'),
                  ('setblock', R['door'][0], 'iron_door[facing=east,half=lower]'),
                  ('setblock', R['door'][1], 'iron_door[facing=east,half=upper]'),
                  ('setblock', R['bulb'], 'waxed_copper_bulb[lit=false,powered=false]'),
                  ('setblock', R['comparator'], 'comparator[facing=north]'),
                  ('setblock', R['press_out'], 'oak_button[face=wall,facing=west]'),
                  ('setblock', R['press_in'], 'oak_button[face=wall,facing=east]'),
                  ('setblock', R['chest'], 'chest'), ('stand',), ('cmd', 'clear @p')],
           before=[(hook, R['press_out'], R['press_in'], list(R['door']))],
           run=('into_room', R['inside'], R['reach']),
           check=[inside, ('door_intact', R['lo'], R['hi'], list(R['door']))] + seen, budget=25, tier_fixed='common',
           expect=[(R['door'][0], R['door'][1], 'iron_door', 2, 2)])
      for R in [DOOR_ROOM]
      for name, skills, hook, inside, seen, doc in (
          ('press_door_to_chest', ['press_mechanism'], 'door_taught', ('arrived', R['inside'], R['reach']), [('door_seen',)],
           'The door taught → pressed open from outside, walked in to the chest; walls and door intact'),
          ('untaught_door_stays_shut', [], 'door_untaught', ('not', ('!arrived', R['inside'], R['reach'])), [],
           'Must fail to enter: the same door NOT taught → stays outside, and still digs nothing'))],
    # a hatch: a 2×2 opening in the floor over a room, four sticky pistons in the floor layer pulling its blocks
    # aside (open) or pushing them in (shut), powered from a waxed copper bulb (a toggle) through a comparator and a
    # dust ring into repeaters; one button on the bulb's top (the surface), one under it (the room's ceiling). Both
    # taught with close=True: the door is shut again behind the body. Every cell from DOOR_HATCH.
    *[dict(name=name, module='skills', point='A', skills=['press_mechanism'], stochastic=False, doc=doc,
           scene=[('fill', H['lo_ground'], H['hi_ground'], 'stone'), ('fill', H['room_lo'], H['room_hi'], 'air'),
                  *[('setblock', c, 'stone') for c in H['steps']],
                  *[('setblock', c, 'air') for c in H['hatch']],
                  *[('setblock', c, 'sticky_piston[facing=east]') for c in H['pistons_w']],
                  *[('setblock', c, 'sticky_piston[facing=west]') for c in H['pistons_e']],
                  *[('setblock', c, 'repeater[facing=west]') for c in H['repeaters_w']],
                  *[('setblock', c, 'repeater[facing=east]') for c in H['repeaters_e']],
                  *[('setblock', c, 'redstone_wire') for c in H['wire']],
                  ('setblock', H['bulb'], 'waxed_copper_bulb[lit=false,powered=false]'),
                  ('setblock', H['comparator'], 'comparator[facing=west]'),
                  ('setblock', H['press_out'], 'oak_button[face=floor,facing=north]'),
                  ('setblock', H['press_in'], 'oak_button[face=ceiling,facing=north]'),
                  # lit last: the pistons push in, the hatch starts shut
                  ('setblock', H['bulb'], 'waxed_copper_bulb[lit=true,powered=false]'),
                  ('stand', start[1], start[2], start[3]), ('cmd', 'clear @p')],
           before=[('door_taught_as', [H['press_out'], H['press_in']], list(H['hatch']), True)],
           run=('into_room', goal, H['reach']),
           check=[('arrived', goal, H['reach']), ('door_shut', list(H['hatch'])),
                  ('shell_intact', H['shell_lo'], H['shell_hi'], list(H['hatch'])), ('door_seen',)],
           budget=25, tier_fixed='common', expect=[(H['bulb'], H['bulb'], 'waxed_copper_bulb', 1, 1)])
      for H in [DOOR_HATCH]
      for name, start, goal, doc in (
          ('hatch_in_and_close', H['above'], H['below'],
           'A shut floor hatch, its buttons taught (close) → pressed open from above, down into the room, shut behind; '
           'nothing dug'),
          ('hatch_out_and_close', H['below'], H['above'],
           'The same hatch from below → pressed open from the room, up to the surface, shut behind; nothing dug'))],
    # a side room: a 1×2 door in its west wall, two stacked sticky pistons in the wall pulling the door blocks aside,
    # one button outside on a bulb (a toggle) read by two comparators into repeaters; taught with close=False: left
    # open. Into the room and back out. Every cell from DOOR_SIDE.
    *[dict(name='side_room_single_button', module='skills', point='A', skills=['press_mechanism'], stochastic=False,
           doc='A shut 1×2 piston door, one button outside, taught (stays open) → pressed, into the room and back '
               'out; the door left open, nothing dug (must fail: pressed shut again)',
           scene=[('floor',), ('fill', R['lo'], R['hi'], 'stone'), ('fill', R['in_lo'], R['in_hi'], 'air'),
                  *[('setblock', c, 'air') for c in R['door']],
                  *[('setblock', c, 'sticky_piston[facing=south]') for c in R['pistons']],
                  ('setblock', R['repeater_low'], 'repeater[facing=north]'),
                  ('setblock', R['support'], 'stone'), ('setblock', R['repeater_high'], 'repeater[facing=west]'),
                  ('setblock', R['wire_block'], 'stone'),
                  *[('setblock', c, 'redstone_wire') for c in R['wire']],
                  ('setblock', R['bulb'], 'waxed_copper_bulb[lit=false,powered=false]'),
                  ('setblock', R['comparator_low'], 'comparator[facing=west]'),
                  ('setblock', R['comparator_high'], 'comparator[facing=north]'),
                  ('setblock', R['press'], 'oak_button[face=wall,facing=west]'),
                  ('setblock', R['bulb'], 'waxed_copper_bulb[lit=true,powered=false]'),     # shut to start
                  ('stand', R['outside'][1], R['outside'][2], R['outside'][3]), ('cmd', 'clear @p')],
           before=[('door_taught_as', [R['press']], list(R['door']), False)],
           run=('through_and_back', R['inside'], R['outside'], R['reach']),
           check=[('arrived', R['outside'], R['reach']), ('door_open', list(R['door'])),
                  ('shell_intact', R['lo'], R['hi'], list(R['door'])), ('door_seen',)],
           budget=25, tier_fixed='common', expect=[(R['bulb'], R['bulb'], 'waxed_copper_bulb', 1, 1)])
      for R in [DOOR_SIDE]],
]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
from .vocab import *  # noqa: E402,F401,F403  (the words and helpers a one-off row is written in)
CODE_ROWS = [
    base_row("chest_or_tree", "chop", surprise=dict(
        base="chop", doc="4 logs in a chest by the body, a tree 12 away: the brain takes the cheaper (plan-driven, "
                         "test point C)", point="C", scene=[("chest", ("@", 1, 0, 1), "oak_log 4")],
        # the planner takes from containers as last seen open (memory.note_container): opened once
        before=lambda ctx: core.BRAIN.mem.note_container(
            at(1, 0, 1), "minecraft:overworld", [{"id": "minecraft:oak_log", "count": 4}]),
        run=lambda ctx: _achieve(ctx, [("log", 4)], lambda: _inv_now().count("log") >= 4),
        # 4 logs gained, the base's tree left whole (its trunk, where the base built it)
        check=("all", ("!gain", "log", 4), ("!blocks", ("@", CHOP_TREE[0], 0, CHOP_TREE[1]),
                                            ("@", CHOP_TREE[0], 6, CHOP_TREE[1]), ("oak_log",), 3)))),
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
         budget=BASES["smelt"]["budget"], expect=SHEET_EXPECT),       # an interrupted run keeps its base's time
]
