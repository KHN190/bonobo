"""Bench table, brain tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""

FAMILIES = [
    # food low: food before the task, cooking counted (the food source a plan picks by price)
    ('cell', [('plenty', 'low', 'fresh', 'surface', 'none', 'room')]),
]
ROWS = []
CODE_ROWS = []
