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
    dict(name='accept_build_portal', module='brain',
         doc='Acceptance: flat stone ground, obsidian and flint carried, brain queues build nether_portal → autonomous cerebellum plans, places and lights the Nether portal.',
         scene=[('floor', 'stone', 8, 2),
                ('fill', ('@', -8, 0, -8), ('@', 8, 6, 8), 'air'),
                ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'), ('time', 0),
                ('give', 'obsidian', 10), ('give', 'cobblestone', 16), ('give', 'flint_and_steel'),
                ('cmd', 'effect give @p saturation 60 255 true')],
         run=('slice', ('!now', ('!blocks', ('@', -8, 0, -8), ('@', 8, 6, 8), 'nether_portal', 1)),
              2.0, None, [{'goal': 'build', 'args': {'bp': 'nether_portal'}}], 30, True),
         check=[('slice_check', ('!now', ('!blocks', ('@', -8, 0, -8), ('@', 8, 6, 8), 'nether_portal', 1)), 30)],
         detail=('&slice_detail',), est=('step', 'build', 'nether_portal', 1, {}), raw=True, release=True, point='D', tags={'base': 'acceptance'}),
]
