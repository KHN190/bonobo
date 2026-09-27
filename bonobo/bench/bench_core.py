"""Bench table, core tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
Built into the one SCENARIOS by table.py."""

FAMILIES = [
    ('base', [('nav',), ('chop',), ('mine_stone',), ('mine_iron',), ('craft',), ('smelt',), ('hunt',), ('eat',), ('sleep',),
         ('loot',), ('sleep', None, 'bed_in_nether')]),
]
ROWS = [
    dict(name='iron_ingots', module='skills',
         doc=('Stone room, 3 furnaces placed side by side, 3 raw iron + 3 coal carried → 3 iron ingots, one per '
              'furnace in parallel (load, start, wait, collect).'),
         scene=[('floor', 'stone', 8, 4), ('fill', ('@', 2, 0, -1), ('@', 2, 0, 1), 'furnace'), ('tp', ('@', 0, 0, 0)),
                ('cmd', 'clear @p'), ('give', 'raw_iron', 3), ('give', 'coal', 3)],
         run=('do', 'achieve', ['$ctx', [('minecraft:iron_ingot', 3)], ('!inv_has', 'minecraft:iron_ingot', 3)],
              {'rounds': 10}),
         before=[('start', 'iron_ingots'), ('sprint_after', 4, 700), ('sprint_after', 10, 700)],
         check=[('count', 'minecraft:iron_ingot', '>=', 3), ('count', 'minecraft:raw_iron', '==', 0)], budget=25,
         expect=[(('@', 2, 0, -1), ('@', 2, 0, 1), 'furnace', 3, 3)], tick_rate=60, point='C', chain=1,
         skills=['load_smelter', 'start_smelt_job', 'smelt']),
    dict(name='water_clutch', module='perception',
         doc=('Dropped 30 blocks above stone, a water bucket in the main bag (not the hotbar) → water poured in time '
              '(health ≥ 16) and scooped back (the bucket full again)'),
         scene=[('sheet', '_FALL_FLOOR'), ('give', 'water_bucket')], run=('&wait_landed',),
         before=[('do', 'chat', ['tp @p 10000.5 230 10000.5'], {})],
         check=[('state', 'health', '>=', 16), ('not', ('!state', 'dead')),
                ('count', 'minecraft:water_bucket', '>=', 1)],
         budget=15, stochastic=False, expect=[(('@', -6, -1, -6), ('@', 6, -1, 6), 'stone', 169, 169)],
         mod=['nets', 'use'], point='B'),
    dict(name='slice_start_tools', module='brain',
         doc=('Slice: the whole cerebellum, a crafting table beside it, planks, sticks and 3 cobblestone carried, a '
              'pickaxe asked → the wooden-then-stone chain by crafting alone, a stone pickaxe in the bag, no loops '
              "(core: what gathering costs is the other rows' job)."),
         scene=[('floor', 'stone', 6, 2), ('setblock', ('@', 1, 0, 1), 'crafting_table'), ('tp', ('@', 0, 0, 0)),
                ('cmd', 'clear @p'), ('cmd', 'time set day'), ('give', 'oak_planks', 6), ('give', 'stick', 4),
                ('give', 'cobblestone', 3)],
         run=('slice', ('&has_stone_pickaxe',), 0.5, None,
              [{'goal': 'have', 'args': {'needs': [['tool', 'pickaxe', 1]]}}]),
         check=[('slice_check', ('&has_stone_pickaxe',))], detail=('&slice_detail',), budget=25,
         expect=[(('@', 1, 0, 1), ('@', 1, 0, 1), 'crafting_table', 1, 1)], point='C', chain=0),
    dict(name='lava_edge_walk', module='nav',
         doc='A 1-wide stone path between two lava pools to a target 12 blocks on → there, not burnt',
         scene=[('fill', ('@', -3, -3, -4), ('@', 15, -1, 4), 'stone'),
                ('fill', ('@', 0, -1, -3), ('@', 13, -1, -1), 'lava'),
                ('fill', ('@', 0, -1, 1), ('@', 13, -1, 3), 'lava'), ('stand', -1), ('give', 'cobblestone', 32)],
         run=('skill', 'travel_to', ('@', 14, 0, 0), 1.5), before=[('start', 'lava_edge_walk')],
         check=[('_at', ('@', 14, 0, 0), 2.5), ('alive', 16)], budget=25, point='B', skills=['goto'],
         tags={'base': 'nav', 'hazard': 'lava'}, expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='buried_by_sand', module='brain',
         doc="Sand dropped on the body mid-task → L0 rescues (unbury) through the brain's own round, then alive",
         scene=[('floor',), ('stand',), ('give', 'stone_pickaxe')], run=('brain_rounds', 15, ('&head_clear',)),
         before=[('start', 'buried_by_sand'), ('do', 'chat', ['fill 10000 200 10000 10000 203 10000 sand'], {})],
         check=[('call', 'head_clear', []), ('alive', 10)], budget=20, point='B', skills=['unbury'],
         tags={'base': 'l0', 'hazard': 'suffocating'},
         expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='drowning_in_a_pit', module='brain',
         doc=('Deep in a flooded shaft with little air → L0 surfaces (find_air / surface) before anything else: on the '
              'rim, or breathing with the head out for 2 s'),
         scene=[('tank', -1, 1, -1, 1, 3, None, -4, 'stone'), ('fill', ('@', -1, -3, -1), ('@', 1, 3, 1), 'water'),
                ('fill', ('@', -4, 4, -4), ('@', 4, 4, 4), 'stone'),
                ('fill', ('@', -1, 4, -1), ('@', 1, 4, 1), 'water'), ('fill', ('@', -4, 5, -4), ('@', 4, 7, 4), 'air'),
                ('stand', 0, -3)],
         run=('brain_rounds', 28, ('!now_api', ('!surfaced', 4, 0))), before=[('start', 'drowning_in_a_pit')],
         check=[('alive', 8), ('surfaced', 4)], budget=25, point='B', skills=['reach:air'],
         tags={'base': 'l0', 'hazard': 'drowning'}, expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
]
