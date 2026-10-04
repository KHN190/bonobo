"""Bench table, acceptance tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
Built into the one SCENARIOS by table.py."""

ACCEPT_LIMIT_S = 300       # an iron pickaxe from nothing: faster than a plain player (3–10 minutes)

FAMILIES = [

]
ROWS = [
    dict(name='accept_fresh_iron_pickaxe', module='brain',
         doc=('Acceptance: a fresh spot of a real world, empty-handed, the whole cerebellum → an iron pickaxe within '
              f'{ACCEPT_LIMIT_S // 60} minutes (stone tools → iron pickaxe milestones)'),
         scene=[('cmd', 'spreadplayers 13000 13000 0 4 false @p'), ('cmd', 'clear @p'), ('time', 0)],
         run=('slice', ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)), ACCEPT_LIMIT_S / 60, None,
              [{'goal': 'milestone', 'args': {'name': 'stone tools'}},
               {'goal': 'milestone', 'args': {'name': 'iron pickaxe'}}],
              60),
         check=[('slice_check', ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)), 60)],
         detail=('&slice_detail',), est=('plan', [('tool', 'pickaxe', 2)]), raw=True, release=True, point='D', tags={'base': 'acceptance'}),
    dict(name='accept_nether_portal', module='brain',
         doc=('Acceptance: early stages complete (iron tools, 64 cobblestone, wood, crafting table, furnace, bed, buckets) '
              'on natural ground near lava → autonomous cerebellum plans, speedrun-casts or builds and lights the Nether portal.'),
         scene=[('cmd', 'spreadplayers 13000 13000 0 4 false @p'),
                ('cmd', 'clear @p'), ('time', 0),
                ('cmd', 'execute at @p run fill ~6 ~-1 ~-2 ~10 ~-1 ~2 lava'),
                ('cmd', 'execute at @p run fill ~6 ~ ~-2 ~10 ~3 ~2 air'),
                ('give', 'iron_pickaxe'), ('give', 'iron_sword'), ('give', 'shield'),
                ('give', 'water_bucket'), ('give', 'bucket'), ('give', 'flint_and_steel'),
                ('give', 'cobblestone', 64), ('give', 'oak_log', 8), ('give', 'oak_planks', 16),
                ('give', 'crafting_table'), ('give', 'furnace'), ('give', 'white_bed'),
                ('give', 'cooked_beef', 16),
                ('cmd', 'effect give @p saturation 120 255 true')],
         run=('slice', ('!now', ('!call', 'portal_made', [])),
              5.0, None, [{'goal': 'build', 'args': {'bp': 'nether_portal'}}], 120, True),
         check=[('slice_check', ('!now', ('!call', 'portal_made', [])), 120)],
         detail=('&slice_detail',), est=('step', 'cast', 'nether_portal', 1, {}), raw=True, release=True, point='D', tags={'base': 'acceptance'}),
]
