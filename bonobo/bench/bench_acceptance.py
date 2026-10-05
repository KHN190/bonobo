"""Bench table, acceptance tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
Built into the one SCENARIOS by table.py."""

ACCEPT_LIMIT_S = 300       # an iron pickaxe from nothing: faster than a plain player (3–10 minutes)
SMELT_S = 10               # a furnace's seconds an item (Minecraft Wiki, Smelting)
OVERLAP_SLACK_S = 25       # loading, the walk to the furnace and back, collecting: what overlapping still costs

KIT = [('cmd', 'clear @p'), ('floor', 'stone', 8, 4), ('stand',), ('time', 1000),
       ('give', 'iron_pickaxe'), ('give', 'furnace'), ('give', 'crafting_table'), ('give', 'coal', 8),
       ('give', 'cooked_beef', 16)]


def overlap_row(name, iron, stone, doc):
    """Smelt `iron` and mine `stone` stone as one goal: done within the furnace's own seconds plus the slack (the body
    mines while it burns, the furnace emptied when done) — serial work (wait at the furnace, then mine) runs past it."""
    needs = [['minecraft:iron_ingot', iron], ['stone', stone]]
    done = ('!now', ('!all', ('!count', 'minecraft:iron_ingot', '>=', iron),
                     ('!count', 'minecraft:cobblestone', '>=', stone)))
    limit_s = max(iron * SMELT_S, stone * 1.5) + OVERLAP_SLACK_S
    return dict(name=name, module='brain', doc=doc,
                scene=KIT + [('give', 'raw_iron', iron)],
                run=('slice', done, limit_s / 60, None, [{'goal': 'have', 'args': {'needs': needs}}], 60),
                check=[('slice_check', done, 60)],
                detail=('&slice_detail',), est=('plan', [tuple(n) for n in needs]), raw=True, release=True, point='D',
                tags={'base': 'acceptance'})

FAMILIES = [

]
ROWS = [
    overlap_row('accept_smelt_beside_mining', 8, 32,
                'Acceptance: 8 raw iron and a furnace, 32 stone wanted too → the furnace burns while the body mines; '
                'both had within the furnace\'s 80 s plus the slack, never 80 s waited and the mining after'),
    overlap_row('accept_furnace_done_mid_mining', 8, 96,
                'Acceptance: a long mine (96 stone) with 8 iron in the furnace → the mine hands the body back when the '
                'furnace is done (a slice), the ingots taken and the mine resumed; all within the mining\'s seconds '
                'plus the slack'),
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
]
