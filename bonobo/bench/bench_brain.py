"""Bench table, brain tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""

FAMILIES = [
    # food low: never starved, food secured, the task done (order free); the base: no bed (must not)
    ('cell', [('plenty', 'full', 'fresh', 'surface', 'none', 'room'), ('plenty', 'low', 'fresh', 'surface', 'none', 'room')]),
    ('home_night', [('home_night_bed', 'night inside the home, its bed in the hall: slept in it, nothing of the home dug')]),
]
ROWS = []
CODE_ROWS = []
