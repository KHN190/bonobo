"""Bench table, combat tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""


from ..data import MAX_HP

IRON_CHEST, IRON_HELMET = 6, 2           # armour points the game gives them (/state "armor")
from .core import BEST_TOOLS, fight_line_hp

# -- the dimensions of a fight cell (the combat table's own data; vocab's `_build` turns a cell into commands) -------
# enemies named by what they do ("pack" is walker × count=three: the same world under another name)
ENEMY = {"none": None, "walker": "minecraft:zombie", "archer": "minecraft:skeleton", "climber": "minecraft:spider",
         "bomb": "minecraft:creeper", "teleporter": "minecraft:enderman"}
COUNT = {"one": 1, "three": 3}
# ground that gives each shaping column something to be worth (a flat arena prices `reshape` at nothing)
CORRIDOR_END = 11      # the corridor runs from behind us to this x, open there
WALK_X = 7             # the arena's inside, a block off each wall: a walk across is -WALK_X → WALK_X
ALCOVE = (4, 2)        # (x of its mouth, depth): a 1-wide, 2-high alcove
GROUND = {"open": [("fill", ("@", 4, 0, 2), ("@", 5, 1, 3), "stone")],                  # a step to stand up on
          # a 1-wide passage at the feet (START_Y is the feet: walls from y 1 left a 3-wide open channel under a
          # raised wall, 6 blocks to seal and none offered), shut behind us, open 11 ahead: two blocks seal it
          "corridor": [("fill", ("@", -1, 0, -1), ("@", CORRIDOR_END, 2, -1), "cobblestone"),
                       ("fill", ("@", -1, 0, 1), ("@", CORRIDOR_END, 2, 1), "cobblestone"),
                       ("fill", ("@", -1, 0, 0), ("@", -1, 2, 0), "cobblestone")],
          "roofed": [("fill", ("@", -4, 3, -4), ("@", 9, 3, 4), "cobblestone"),
                     ("fill", ("@", -4, 1, -4), ("@", -4, 2, 4), "cobblestone"),
                     ("fill", ("@", -2, -3, -2), ("@", 2, -1, 2), "dirt")]}             # a floor worth digging into
# the body's real dimensions, one at a time (the shield is a kit, not armour)
WEAPON = {"fist": [], "iron": [("give", "iron_sword")]}
ARMOUR = {"skin": [], "iron": [("cmd", "item replace entity @p armor.chest with iron_chestplate"),
                               ("cmd", "item replace entity @p armor.head with iron_helmet")]}
BLOOD = {"whole": [], "hurt": [("cmd", "damage @p 12 minecraft:magic")]}
_BLOCKS = [("cmd", BEST_TOOLS["pickaxe"]), ("give", "cobblestone", 64), ("give", "dirt", 64)]
_SHIELD = [("cmd", "item replace entity @p weapon.offhand with shield")]
KIT = {"nothing": [], "blocks": _BLOCKS, "food": [("give", "cooked_beef", 8)], "shield": _SHIELD,
       "full": _BLOCKS + [("give", "cooked_beef", 8)] + _SHIELD}
# what each kit's column needs to exist, read off the cell (so "bag read as empty" can be caught)
# (a shield is no column: the jar's reflex raises it for any predicted hit — proven by /state blocking, `_blocked`)
NEEDS = {"blocks": ("reshape", "wall_in"), "food": ("eat",), "shield": (),
         "full": ("reshape", "wall_in", "eat"), "nothing": ()}
DISTANCE = {"near": 5, "across": 10}

DIMS = {"enemy": ENEMY, "count": COUNT, "ground": GROUND, "weapon": WEAPON, "armour": ARMOUR, "blood": BLOOD,
        "kit": KIT, "distance": DISTANCE}
ARMED = {"enemy": "walker", "count": "one", "ground": "open", "weapon": "iron", "armour": "iron",
         "blood": "whole", "kit": "full", "distance": "near"}
UNARMED = dict(ARMED, weapon="fist", armour="skin", kit="blocks", distance="across")
# the siege: waves each asking an answer the last did not; `carry` (hp lost, meals, blocks) what earlier waves leave
WAVES = (
    ("one walker", (("walker", 1),), (0, 0, 0)),
    ("three walkers", (("walker", 3),), (2, 1, 4)),
    ("two archers", (("archer", 2),), (4, 2, 10)),
    ("walkers and climbers", (("walker", 2), ("climber", 3)), (6, 3, 18)),
    ("two bombs", (("bomb", 2),), (7, 4, 28)),
    ("three teleporters", (("teleporter", 3),), (8, 5, 34)),
    ("everything", (("walker", 4), ("archer", 2), ("climber", 2), ("bomb", 1)), (9, 6, 38)),
)
FAMILIES = [
    ('behaviour', [('shield_arrows',), 
         ]),
    ('deflect', [('deflect__volley',)]),
    ('fight_cell', [
         ('fight_zombie_3', 'zombie', 3, 'exception', 25, 6, True),
         ('fight_skeleton_1', 'skeleton', 1, 'common', 25, 10, True),
         ('fight_creeper_1', 'creeper', 1, 'common', 25, MAX_HP, True),
         ('fight_blaze_3', 'blaze', 3, 'exception', 25, 6, True),
         # design-bc §13 R7: two kinds at once, judged on the world (the answer switches: a readout); its time the
         # fight's own estimate × the slack
         ]),
]
ROWS = [
    dict(name='collect_blaze_rods', module='combat',
         # Looting III: no rod from a blaze (1/2)^4, from all three ≈ 2e-4
         doc=('Nether platform, 3 blazes, Looting III sword + shield + iron armor → fight_loop fights them, the step '
              'picks up at least one rod.'),
         scene=[('floor', 'nether_bricks', 8, 2),
                ('at', 'fill {0} {1} nether_bricks hollow', ('@', -9, 0, -9), ('@', 9, 5, 9)),
                ('fill', ('@', -8, 0, -8), ('@', 8, 4, 8), 'air'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('cmd', 'give @p diamond_sword[enchantments={"minecraft:looting":3}]'), ('cmd', 'item replace entity @p weapon.offhand with shield'),
                ('cmd', 'item replace entity @p armor.chest with iron_chestplate'),
                ('cmd', 'item replace entity @p armor.head with iron_helmet'), ('give', 'cooked_beef', 16),
                ('give', 'cobblestone', 32), ('summon', 'blaze', ('@', 3, 1, 0), '{PersistenceRequired:1b,Health:4f}'),
                ('summon', 'blaze', ('@', -3, 1, 2), '{PersistenceRequired:1b,Health:4f}'),
                ('summon', 'blaze', ('@', 0, 1, -3), '{PersistenceRequired:1b,Health:4f}')],
         run=('do', 'bonobo.combat.collect_blaze_rods', ['$ctx', 1], {}),
         # S5: an optional fight: the line held (diamond sword, iron chest + helmet, a shield)
         check=[('count', 'minecraft:blaze_rod', '>=', 1),
                ('state', 'health', '>=', fight_line_hp('minecraft:blaze', 'minecraft:diamond_sword', IRON_CHEST + IRON_HELMET, True))],
         est=('fight', ['blaze'], 3),
         dimension='minecraft:the_nether', combat=True,
         expect=[(('@', -8, -1, -8), ('@', 8, -1, 8), 'nether_bricks', 289, 289)],
         expect_entities=[('minecraft:blaze', 3)], skills=['collect_blaze_rods']),
    dict(name='fight_enderman_1', module='nav',
         doc=('Four endermen about the arena, a walk 14 blocks across through them → reached, none provoked '
              '(server AngerTime 0 each), health kept'),
         scene=[('sheet', '_ARENA'), ('tp', ('@', -WALK_X, 0, 0)),
                ('built', 'endermen_off_path', ('@', -WALK_X, 0, 0), ('@', WALK_X, 0, 0), 4)],
         run=('do', 'bonobo.nav.go_to', [('@', WALK_X, 0, 0), ('$ctx', 'policy')], {'range_': 1.5}),
         check=[('arrived', ('@', WALK_X, 0, 0), 1.5), ('endermen_calm',), ('alive', 20)],
         est=('step', 'goto', '', 1, {'pos': ('@', WALK_X, 0, 0)}), point='B', combat=True, stochastic=True,
         tags={'base': 'fight', 'enemy': 'enderman'},
         expect_entities=[('minecraft:enderman', 4, 4)],
         expect=[(('@', -9, -1, -9), ('@', 9, -1, 9), 'stone', 361, 361), (('@', -9, 4, -9), ('@', 9, 4, 9), 'stone', 361, 361),
                 (('@', -9, 0, -9), ('@', 9, 3, 9), 'glass', 288, 288)]),
    dict(name='fight_enderman_provoked', module='fight_loop',
         doc=('One enderman provoked (stared at until the jar reads it angry), no sword, a 2-high alcove 4 off → '
              'under it within 5 s, no hit after, alive'),
         scene=[('sheet', '_ARENA'), ('cmd', 'clear @p minecraft:iron_sword'),
                ('built', 'alcove', *ALCOVE),
                ('summon', 'enderman', ('@', -ALCOVE[0], 0, 0), '{PersistenceRequired:1b}')],
         before=[('&provoke_by_stare',), ('start', 'fight_enderman_provoked'), ('&record_bids',)],
         run=('fight_until', ['minecraft:enderman'], 14, False),
         check=[('took_cover_alcove', *ALCOVE, 5.0), ('alive',)],
point='B', combat=True, stochastic=True, tags={'base': 'fight', 'enemy': 'enderman'},
         expect_entities=[('minecraft:enderman', 1, 1)],
         expect=[(('@', -9, -1, -9), ('@', 9, -1, 9), 'stone', 361, 361), (('@', -9, 4, -9), ('@', 9, 4, 9), 'stone', 361, 361),
                 (('@', -9, 0, -9), ('@', 9, 3, 9), 'glass', 288, 288)]),
]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
def CODE_ROWS():
    """The one-off rows, built when the sheet is (they are written in vocab's words, and vocab reads this table's
    dimensions at its own import: no import-time cycle)."""
    from .core import _c, at
    from .words.brain import EDGE_Y, _first_times
    from .words.checks import BASE, FIRST, _alive, _all, _start, arrived
    from .words.fight import _ARENA, _fight_until, _gone, _hostiles, _record_bids
    from .words.runs import _brain_rounds, _hooks
    from .words.scene import _tp, limit
    return [
    ]
