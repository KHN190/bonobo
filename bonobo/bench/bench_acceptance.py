"""Bench table, acceptance tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
Built into the one SCENARIOS by table.py."""


FAMILIES = [
]

ROWS = [
    dict(name='accept_fresh_iron_pickaxe', module='brain',
         doc=('Acceptance: a fresh spot of a real world, empty-handed, the whole cerebellum → an iron pickaxe '
              'within 30 minutes (stone tools → iron pickaxe milestones)'),
         scene=[('cmd', 'spreadplayers 13000 13000 0 4 false @p'), ('cmd', 'clear @p'), ('time', 0)],
         run=('slice', ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)), 30, None,
              [{'goal': 'milestone', 'args': {'name': 'stone tools'}},
               {'goal': 'milestone', 'args': {'name': 'iron pickaxe'}}],
              60),
         check=[('slice_check', ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)), 60)],
         detail=('&slice_detail',), budget=1800, raw=True, release=True, point='D', tags={'base': 'acceptance'}),
]
