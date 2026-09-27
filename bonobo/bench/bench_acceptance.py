"""Bench table, acceptance tier: every row as plain data in `bench/vocab.py`'s words, run through `bench/table.py`.
Written from the old sheet (scenarios.SCENARIOS) by `bench/tabulate.py`; tests/test_bench_tables.py proves each row
builds back to the old one (setup, queue, budget, tier, and the same words for run, check and hooks). Not wired to
the runner yet. NOT_EXPRESSED: the old rows whose code has no words here yet, with the reason."""


ROWS = [
    {'name': 'accept_fresh_iron_pickaxe',
     'module': 'brain',
     'doc': 'Acceptance: a fresh spot of a real world, empty-handed, the whole cerebellum → an iron pickaxe within '
            '30 minutes (stone tools → iron pickaxe milestones)',
     'scene': [('cmd', 'spreadplayers 13000 13000 0 4 false @p'), ('cmd', 'clear @p'), ('time', 0)],
     'run': ('slice',
             ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)),
             30,
             None,
             [{'goal': 'milestone', 'args': {'name': 'stone tools'}},
              {'goal': 'milestone', 'args': {'name': 'iron pickaxe'}}],
             60),
     'check': [('slice_check', ('!now', ('!count', 'minecraft:iron_pickaxe', '>=', 1)), 60)],
     'detail': ('&slice_detail',),
     'budget': 1800,
     'raw': True,
     'release': True,
     'point': 'D',
     'skills': [],
     'tags': {'base': 'acceptance'}},
]

NOT_EXPRESSED = {
}
