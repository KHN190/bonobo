"""Bench table, brain tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""
from ..explore import TRAVEL_RANGE
from ..reflexes import EAT_BELOW, UNSTUCK_MOVED
from .words.scene import TREE_HEIGHT

from .core import KEPT_HP, ORIGIN
from ..reflexes import BAG_FULL
from ..survive import DIG_IN_DEPTH, _pod_cells

GAP_X = 4                  # night_dig_in_dirt_unreachable: the drop between the body and the dirt starts at @+4

# two goals queued (l3_*): a grove and a stone face, one order each way
L3_SCENE = [('grove', (3, 0)), ('fill', ('@', -3, 0, 2), ('@', -2, 1, 3), 'stone'), ('stand',), ('give', 'wooden_pickaxe')]
L3_GOALS, L3_EACH = ('log', 'minecraft:cobblestone'), 2
# resume_after_combat: logs wanted, logs carried
LOGS_WANTED, LOGS_CARRIED = 4, 2
IRON_CELLS = (('@', -4, 0, 0), ('@', 4, 0, 0))
IRON_TWO = [('floor',), *[('setblock', c, 'iron_ore') for c in IRON_CELLS], ('stand',)]
FAMILIES = [
    # the interruption lands by progress (the first ore in the bag: the walk to the second one), not a fight
    ('brain', [('ban_needs_a_failure',
                'Two free iron ores, the walk to the second interrupted once (the bench\'s own interrupt) → nothing '
                'banned: an interruption teaches nothing about the place (control)',
                IRON_TWO + [('give', 'stone_pickaxe')], [{'goal': 'have', 'args': {'needs': [['minecraft:raw_iron', 2]]}}],
                ('_count', 'minecraft:raw_iron', 2), 1,
                ('all', ('!gain', 'minecraft:raw_iron', 2), *[('!not_banned', c) for c in IRON_CELLS], ('!interrupted',)),
                [('&clear_bans',), ('interrupt_counted', 'minecraft:raw_iron', 1)], ['_clear_bans', '_interrupt_counted']),
               # at most what was carried plus the one tree's trunk; a zombie summoned beside the body 1.5 s in
               ('resume_after_combat',
                '4 logs wanted, 2 carried, the best axe; a zombie summoned beside it mid-way → fight_loop answers it, '
                'then the chopping resumes for what is still missing',
                [('grove', (3, 0)), ('stand',), ('give', 'iron_sword'), ('give', 'diamond_axe'),
                 ('give', 'oak_log', LOGS_CARRIED), ('cmd', 'item replace entity @p armor.chest with iron_chestplate')],
                [{'goal': 'have', 'args': {'needs': [['log', LOGS_WANTED]]}}], ('now', ('!count', 'log', '>=', LOGS_WANTED)),
                1, ('all', ('!count', 'log', '>=', LOGS_WANTED), ('!count', 'log', '<=', LOGS_CARRIED + TREE_HEIGHT),
                    ('!gone', ['minecraft:zombie']), ('!alive', KEPT_HP)),
                [('summon_after', 1.5, 'zombie', ('@', 1, 0, 1))], ['_summon_after'])]),
    ('upkeep', [('eat', 'hungry, bread carried → eaten (the food bar rises)',
                 [('floor',), ('stand',), ('give', 'bread', 4)], [('&hunger_drained',)],
                 ('now_api', ('!food_up',)), ('food_up',)),
                # hurt with the bar short of full: no regen below 18 and slow below 20 — eaten to full though not hungry;
                # done is the whole outcome: fed alone stopped the rounds before the health could rise (20260928-075617)
                ('eat_to_regen',
                 'food drained to 16 (not hungry: above EAT_BELOW), then hurt (instant damage), bread carried → eaten '
                 '(the bread goes down), the bar to 18 or more, and health rises',
                 [('floor',), ('stand',), ('give', 'bread', 4), ('cmd', 'effect give @p minecraft:hunger 1 0 true')],
                 [('drain_to', 16, ('&LOW_FOOD_MAX_S',), (EAT_BELOW - 1, 18)),
                  ('command_then', 'effect give @p minecraft:instant_damage 1 0 true', 0.5),
                  ('state_before', 'food', 'health')],
                 ('now_api', ('!all', ('&regen_fed',), ('!rose', 'health'))),
                 ('all', ('&regen_fed',), ('!rose', 'health')))]),
    ('brain', [('plan_repair_on_event',
          'Planks + cobblestone + a wooden pickaxe carried (upkeep quiet), a stone pickaxe asked; the table the plan '
          'puts down is taken away → that step is redone, the plan is not started over (≤ 2 plans)',
          [('floor',), ('stand',), ('give', 'oak_planks', 12), ('give', 'cobblestone', 3), ('give', 'wooden_pickaxe')],
          [{'goal': 'have', 'args': {'needs': [['tool', 'pickaxe', 1]]}}],
          ('now', ('!count', 'minecraft:stone_pickaxe', '>=', 1)), 0.75,
          ('all', ('!count', 'minecraft:stone_pickaxe', '>=', 1), ('!replans_at_most', 2)),
          [('&count_replans',), ('&remove_table_when_placed',)], ['_count_replans', '_remove_table_when_placed']),
         ('plan_without_events', 'The same with nothing taken away → one plan (control)',
          [('floor',), ('stand',), ('give', 'oak_planks', 12), ('give', 'cobblestone', 3), ('give', 'wooden_pickaxe')],
          [{'goal': 'have', 'args': {'needs': [['tool', 'pickaxe', 1]]}}],
          ('now', ('!count', 'minecraft:stone_pickaxe', '>=', 1)), 0.5,
          ('all', ('!count', 'minecraft:stone_pickaxe', '>=', 1), ('!replans_at_most', 1)), [('&count_replans',)],
          ['_count_replans']),
         ('ban_then_other_source', 'Two iron ores, one sealed in barrier → that cell is banned, the other is mined',
          [('floor',), ('fill', ('@', -5, -1, -1), ('@', -3, 1, 1), 'barrier'),
           ('setblock', ('@', -4, 0, 0), 'iron_ore'), ('setblock', ('@', 4, 0, 0), 'iron_ore'), ('stand',),
           ('give', 'stone_pickaxe')],
          [{'goal': 'have', 'args': {'needs': [['minecraft:raw_iron', 1]]}}], ('_count', 'minecraft:raw_iron', 1), 1,
          ('all', ('!gain', 'minecraft:raw_iron', 1),
           ('!blocks', ('@', -4, 0, 0), ('@', -4, 0, 0), ('iron_ore',), 1, 1),
           ('!blocks', ('@', 4, 0, 0), ('@', 4, 0, 0), ('iron_ore',), 0, 0)),
          [('&clear_bans',)], ['_clear_bans']),
         ('chop_without_interrupt', 'The same with no zombie → no fight is logged, the same 4 logs (control)',
          [('grove', (3, 0)), ('stand',), ('give', 'iron_sword'), ('give', 'diamond_axe'), ('give', 'oak_log', 2)],
          [{'goal': 'have', 'args': {'needs': [['log', 4]]}}], ('now', ('!count', 'log', '>=', 4)), 0.75,
          ('all', ('!count', 'log', '>=', 4), ('!hp_kept', 20), ('!gone', ['minecraft:zombie'])), [], []),
         *[(name, doc, L3_SCENE, [{'goal': 'have', 'args': {'needs': [[t, L3_EACH]]}} for t in order],
            ('now', ('!all', *[('!thunk', ('!_count', t, L3_EACH)) for t in L3_GOALS])), 1,
            ('all', ('!before_in_bag', *order), *[('!gain', t, L3_EACH) for t in L3_GOALS]),
            [('&first_times',)], ['_first_times'])
           for name, doc, order in (
               ('l3_two_goals_in_order', 'Two goals queued (logs, then cobblestone) → both done, in queue order',
                L3_GOALS),
               ('l3_order_swapped',
                'The same goals queued the other way → done the other way (control: the queue decides, not the cost)',
                L3_GOALS[::-1]))]]),
    ('cell', [('plenty', 'full', 'fresh', 'surface', 'none', 'room'), ('tight', 'full', 'fresh', 'surface', 'none', 'room'),
         ('night', 'full', 'fresh', 'surface', 'none', 'room'), ('plenty', 'low', 'fresh', 'surface', 'none', 'room'),
         ('plenty', 'full', 'one_use', 'surface', 'none', 'room'),
         ('plenty', 'full', 'fresh', 'underground', 'none', 'room'),
         ('plenty', 'full', 'fresh', 'surface', 'none', 'junk_full'),
         ('plenty', 'full', 'fresh', 'surface', 'none', 'valuables_full'),
         ('plenty', 'full', 'fresh', 'surface', 'noted', 'room')]),
    ('upkeep', [('reach_land', 'treading water 6 blocks from a shore → on dry land',
          [('tank', -6, 5, -4, 4, 1, -1, -4, 'glass', 'east'),
           ('fill', ('@', 6, -3, -4), ('@', 9, -1, 4), 'stone'), ('stand',)],
          [], ('now', ('!all', ('!state', 'onGround'), ('!not', ('!state', 'inWater')))),
          ('all', ('!state', 'onGround'), ('!not', ('!state', 'inWater')))),
         ('dig_out', 'daytime, sealed in stone with a pickaxe → out, not enclosed',
          [('fill', ('@', -4, -2, -4), ('@', 4, 3, 4), 'stone'), ('fill', ('@', 0, 0, 0), ('@', 0, 1, 0), 'air'),
           ('tp', ('@', 1.0, 0, 1.0)), ('give', 'stone_pickaxe')],
          [], ('now', ('!not', ('!call', 'enclosed', []))), ('not', ('!call', 'enclosed', []))),
         ('collect_job', 'a finished background smelt remembered at a furnace 2 blocks off → the ingots in the bag',
          [('floor',), ('setblock', ('@', 2, 0, 0), 'furnace'),
           ('at', 'item replace block {0} container.2 with iron_ingot 3', ('@', 2, 0, 0)), ('stand',)],
          [('job_ready_at', ('@', 2, 0, 0), 'minecraft:iron_ingot', 3)], ('_count', 'minecraft:iron_ingot', 3),
          ('gain', 'minecraft:iron_ingot', 3)),
         ('empty_the_bag', 'a full bag (dirt in every slot) → room made',
          [('floor',), ('stand',), ('give', 'dirt', 2304), ('give', 'stone_pickaxe')], [],
          ('now', ('!bag', 'used_slots', [], '<', BAG_FULL)), ('bag', 'used_slots', [], '<', BAG_FULL)),
         ('no_pickaxe', 'no pickaxe, planks + sticks + a table carried → a pickaxe made',
          [('floor',), ('stand',), ('give', 'oak_planks', 6), ('give', 'stick', 4), ('give', 'crafting_table')], [],
          ('now', ('!bag', 'tools', ['pickaxe'])), ('bag', 'tools', ['pickaxe'])),
         ('path_blocked',
          'the last walk failed toward the far side of a 6-block gap, 16 blocks carried → bridged across',
          [('floor',), ('fill', ('@', 2, -3, -8), ('@', 7, -1, 8), 'air'), ('stand',), ('give', 'cobblestone', 16)],
          [('blocked_toward', ('@', 9, 0, 0))], ('now_api', ('!arrived', ('@', 9, 0, 0), TRAVEL_RANGE)), ('arrived', ('@', 9, 0, 0), TRAVEL_RANGE)),
         ('bridge_stock',
          'the same gap with 2 blocks carried (under BRIDGE_MIN), stone underfoot, a pickaxe → blocks fetched first, '
          'to what the way across takes (bridge_stock), then across',
          [('floor',), ('fill', ('@', 2, -3, -8), ('@', 7, -1, 8), 'air'), ('stand',), ('give', 'cobblestone', 2),
           ('give', 'diamond_pickaxe')],
          [('blocked_toward', ('@', 9, 0, 0))],
          ('now', ('!any', ('!api_only', ('!arrived', ('@', 9, 0, 0), TRAVEL_RANGE)), ('!count', 'minecraft:cobblestone', '>=', 9))),
          ('any', ('!arrived', ('@', 9, 0, 0), TRAVEL_RANGE), ('!count', 'minecraft:cobblestone', '>=', 9))),
         ('unstuck', 'a minute in the same block with the same bag (history set), open ground → moved off (UNSTUCK_MOVED)',
          [('floor',), ('stand',)], [('stuck_for', 70)],
          ('now', ('!not', ('!arrived', ('@', 0, 0, 0), UNSTUCK_MOVED))),
          ('not', ('!arrived', ('@', 0, 0, 0), UNSTUCK_MOVED))),
         ('collect_machine', 'a remembered auto smelter whose order is due, 8 ingots in its output chest → taken',
          [('floor',), ('chest', ('@', 3, 0, 0), 'iron_ingot 8'), ('stand',)], [('machine_due', ('@', 3, 0, 0), 8)],
          ('_count', 'minecraft:iron_ingot', 8), ('gain', 'minecraft:iron_ingot', 8)),
         ('eat_when_full', 'fed (food 20), bread carried → not eaten: the bread count unchanged (must not)',
          [('floor',), ('stand',), ('give', 'bread', 4)], [], ('now', ('!constant', False)),
          ('count', 'minecraft:bread', '==', 4)),
         ('shelter_dig_in', 'night, a pickaxe → dug in: below the floor, enclosed',
          [('sheet', '_NIGHT_FLOOR'), ('give', 'stone_pickaxe'), ('give', 'cobblestone', 8)], [], ('&enclosed',),
          ('all', ('!call', 'enclosed', []), ('!state', 'blockY', '<=', ORIGIN[1] - DIG_IN_DEPTH))),
         ('shelter_hut',
          "night, no pickaxe, the hut's materials (cobblestone, a door, a torch) → sheltered by the way the night's "
          'pricing chose',
          [('sheet', '_NIGHT_FLOOR'), ('give', 'cobblestone', 32), ('give', 'oak_door'), ('give', 'torch', 2)], [],
          ('&enclosed',), ('call', 'enclosed', [])),
         ('shelter_wall_in', 'night, no pickaxe, cobblestone only → walled in where it stands',
          [('sheet', '_NIGHT_FLOOR'), ('give', 'cobblestone', 16)], [], ('&enclosed',),
          ('all', ('!call', 'enclosed', []), ('!blocks', ('@', -1, 0, -1), ('@', 1, 2, 1), ('cobblestone',),
                                               len(_pod_cells((0, 0, 0)))))),
         ('shelter_not_with_a_bed', 'night, a bed and cobblestone carried → slept, no shelter built (must not)',
          [('sheet', '_NIGHT_FLOOR'), ('give', 'white_bed'), ('give', 'cobblestone', 16)], [], ('&is_day_now',),
          ('all', ('!is_day',), ('!blocks', ('@', -3, 0, -3), ('@', 3, 2, 3), ('cobblestone',), 0, 0)))]),
    ('dirt', [('night_dig_in_dirt',
          'dusk on stone, an empty bag, dirt three deep 8 blocks along the platform → walked there, dug in by hand: '
          'two or more down in the dirt, sealed overhead',
          [], ('now', ('!call', 'in_the_patch_underground', ['$api', None])), ('&in_the_patch_underground',)),
         ('night_dig_in_dirt_unreachable',
          "the same dirt across a drop to nothing, a pod's blocks carried → never walked to (must not): walled in on "
          'its own side of the gap',
          [('fill', ('@', GAP_X, -3, -8), ('@', GAP_X + 1, -1, 8), 'air'), ('give', 'cobblestone', 10)], ('&enclosed',),
          ('state', 'blockX', '<', ORIGIN[0] + GAP_X))]),
]
ROWS = [

]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
from .vocab import *  # noqa: E402,F401,F403  (the words and helpers a one-off row is written in)
CODE_ROWS = [
    # a search interrupted mid-way (for the night) and taken up again: no section searched twice, no ore scanned again
    dict(name="search_night_resume",
         doc="a remembered diamond past the hill; 6 blocks in, night falls → sheltered the night's way; day again → "
             "the same diamond, straight (no scan for it)",
         module="brain", point="C", skills=[], tier_fixed="brain", combat=False,
         tags={"base": "brain", "family": "search_resume"},
         setup=SEARCH_ARENA + [f"setblock {_c(SEARCH_ORE)} diamond_ore", "give @p diamond_pickaxe", "give @p cobblestone 16"],
         before=_hooks(_start("search_night_resume"), _seen("diamond_ore", SEARCH_ORE), _count_finds,
                       _when(walked_at_least(6), _set_time(13000)),
                       _when(lambda: _enclosed(), lambda: (SEARCH_FLAGS.update(sheltered=True), _set_time(0)()))),
         queue=[_have(("minecraft:diamond", 1))],
         run=_slice(_inv_has("minecraft:diamond", 1), 0.45, queue=[_have(("minecraft:diamond", 1))]),
         check=_all(_gain("minecraft:diamond", 1), _no_scan(), lambda api, inv: SEARCH_FLAGS.get("sheltered", False)),
         budget=limit(), expect=SHEET_EXPECT),
]
