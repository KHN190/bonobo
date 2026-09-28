"""Bench table, combat tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""

from typing import TYPE_CHECKING

from .core import BEST_TOOLS

if TYPE_CHECKING:   # CODE_ROWS pulls vocab's words in at run time; pyright reads them here
    from .vocab import (BASE, EDGE_Y, FIRST, SHEET_EXPECT, _ARENA, _alive, _all, _brain_rounds, _c, _fight_until,
                        _first_times, _gone, _hooks, _hostiles, _hp_kept, _near, _record_bids, _start, _tp, at, limit)
# -- the dimensions of a fight cell (the combat table's own data; vocab's `_build` turns a cell into commands) -------
# enemies named by what they do ("pack" is walker × count=three: the same world under another name)
ENEMY = {"none": None, "walker": "minecraft:zombie", "archer": "minecraft:skeleton", "climber": "minecraft:spider",
         "bomb": "minecraft:creeper", "teleporter": "minecraft:enderman"}
COUNT = {"one": 1, "three": 3}
# ground that gives each shaping column something to be worth (a flat arena prices `reshape` at nothing)
GROUND = {"open": [("fill", ("@", 4, 0, 2), ("@", 5, 1, 3), "stone")],                  # a step to stand up on
          # a 1-wide passage at the feet (START_Y is the feet: walls from y 1 left a 3-wide open channel under a
          # raised wall, 6 blocks to seal and none offered), shut behind us, open 11 ahead: two blocks seal it
          "corridor": [("fill", ("@", -1, 0, -1), ("@", 11, 2, -1), "cobblestone"),
                       ("fill", ("@", -1, 0, 1), ("@", 11, 2, 1), "cobblestone"),
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
    ('siege', [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]),
    ('arena', [(1, 'none', 'open', 'full', 'whole'), (2, 'walker', 'open', 'full', 'whole'),
         (3, 'walker', 'corridor', 'full', 'whole'), (4, 'walker', 'roofed', 'full', 'whole'),
         (5, 'archer', 'open', 'full', 'whole'), (6, 'archer', 'corridor', 'full', 'whole'),
         (7, 'archer', 'roofed', 'full', 'whole'), (8, 'climber', 'open', 'full', 'whole'),
         (9, 'climber', 'corridor', 'full', 'whole'), (10, 'climber', 'roofed', 'full', 'whole'),
         (11, 'bomb', 'open', 'full', 'whole'), (12, 'bomb', 'corridor', 'full', 'whole'),
         (13, 'bomb', 'roofed', 'full', 'whole'), (14, 'teleporter', 'open', 'full', 'whole'),
         (15, 'teleporter', 'corridor', 'full', 'whole'), (16, 'teleporter', 'roofed', 'full', 'whole'),
         (17, 'walker', 'open', 'nothing', 'whole'), (18, 'walker', 'open', 'blocks', 'whole'),
         (19, 'walker', 'open', 'food', 'whole'), (20, 'walker', 'open', 'shield', 'whole'),
         (21, 'walker', 'open', 'full', 'hurt')]),
    ('escape', [('walker', 'open', 'blocks'), ('none', 'open', 'blocks'), ('archer', 'open', 'blocks'),
         ('climber', 'open', 'blocks'), ('bomb', 'open', 'blocks'), ('teleporter', 'open', 'blocks'),
         ('walker', 'corridor', 'blocks'), ('walker', 'roofed', 'blocks'), ('walker', 'open', 'nothing'),
         ('walker', 'open', 'food'), ('walker', 'open', 'shield'), ('walker', 'open', 'full')]),
    ('behaviour', [('block_gap',), ('dig_in',), ('pillar',), ('shield_arrows',), ('fight_without_shield',), ('fight_and_block',),
         ('wall_in',), ('surrounded_low',)]),
    ('fight_cell', [('fight_zombie_1', 'zombie', 1, 'common', 25, 12, True),
         ('fight_zombie_3', 'zombie', 3, 'exception', 25, 6, True),
         ('fight_skeleton_1', 'skeleton', 1, 'common', 25, 10, True),
         ('fight_creeper_1', 'creeper', 1, 'common', 25, 16, True),
         ('fight_blaze_3', 'blaze', 3, 'exception', 25, 6, True),
         ('fight_enderman_1', 'enderman', 1, 'exception', 25, 20, False)]),
]
ROWS = [
    dict(name='collect_blaze_rods', module='combat',
         doc=('Nether platform, 3 blazes, sword + shield + iron armor → fight_loop fights them, the step picks up at '
              'least one rod.'),
         scene=[('floor', 'nether_bricks', 8, 2),
                ('at', 'fill {0} {1} nether_bricks hollow', ('@', -9, 0, -9), ('@', 9, 5, 9)),
                ('fill', ('@', -8, 0, -8), ('@', 8, 4, 8), 'air'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('give', 'diamond_sword'), ('cmd', 'item replace entity @p weapon.offhand with shield'),
                ('cmd', 'item replace entity @p armor.chest with iron_chestplate'),
                ('cmd', 'item replace entity @p armor.head with iron_helmet'), ('give', 'cooked_beef', 16),
                ('give', 'cobblestone', 32), ('summon', 'blaze', ('@', 3, 1, 0), '{PersistenceRequired:1b,Health:4f}'),
                ('summon', 'blaze', ('@', -3, 1, 2), '{PersistenceRequired:1b,Health:4f}'),
                ('summon', 'blaze', ('@', 0, 1, -3), '{PersistenceRequired:1b,Health:4f}')],
         run=('do', 'bonobo.combat.collect_blaze_rods', ['$ctx', 1], {}),
         check=[('count', 'minecraft:blaze_rod', '>=', 1), ('state', 'health', '>', 0)], budget=25,
         dimension='minecraft:the_nether', combat=True,
         expect=[(('@', -8, -1, -8), ('@', 8, -1, 8), 'nether_bricks', 289, 289)],
         expect_entities=[('minecraft:blaze', 3)], skills=['collect_blaze_rods']),
    dict(name='ghast_fireball', module='brain',
         doc=('Nether hall open to one side, a ghast 20 blocks out, sword + armor → the reflexes until the ghast is '
              'hurt or dead or its fireball resolved (≤ 20 s): the ghast hurt or dead, or it fired and nothing hit us'),
         scene=[('floor', 'netherrack', 6, 2),
                ('at', 'fill {0} {1} netherrack hollow', ('@', -6, 0, -6), ('@', 6, 0, 6)),
                ('fill', ('@', -5, 0, -5), ('@', 5, 0, 5), 'air'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('give', 'diamond_sword'), ('cmd', 'item replace entity @p armor.chest with iron_chestplate'),
                ('cmd', 'item replace entity @p armor.head with golden_helmet'), ('give', 'cooked_beef', 16)],
         run=('ghast_watch', 20),
         before=[('do', 'chat',
                  ['execute in minecraft:the_nether run summon ghast 10018 206 10000 {PersistenceRequired:1b}'], {})],
         check=[('ghast_answered',)], budget=25,
         dimension='minecraft:the_nether', combat=True,
         expect=[(('@', -6, -1, -6), ('@', 6, -1, 6), 'netherrack', 169, 169)]),
    dict(name='fight_zombie_1_full_bag', module='fight_loop',
         doc=('Walled platform, iron kit, the bag full of dirt: 1 zombie → killed (credited by the server), health '
              '≥ 12: the drop it cannot pick up changes nothing'),
         scene=[('sheet', '_ARENA'), ('summon', 'zombie', ('@', 4, 0, 0), '{PersistenceRequired:1b}')]
         + [('cmd', 'scoreboard objectives add bk_zombie minecraft.killed:minecraft.zombie'),
            ('cmd', 'scoreboard players set @p bk_zombie 0')],      # the kill statistic (fight.kill_stat_scene)
         run=('fight_until', ['minecraft:zombie'], 23),
         before=[('hooks', ('!start', 'fight_zombie_1'), ('&record_bids',)), ('fill_bag', 0)],
         check=[('hp_kept', 12), ('gone', ['minecraft:zombie']), ('killed', ['minecraft:zombie'], 1)], budget=25, combat=True,
         point='B', tier_fixed='exception',
         tags={'base': 'fight', 'enemy': 'zombie', 'count': 1, 'inventory': 'full_bag'},
         expect_entities=[('minecraft:zombie', 1)], expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='combat__low_hp_eat', module='fight_loop',
         doc=('6 hp, food 6 with no saturation (no natural regen), one zombie 2 blocks off, blocks and cooked beef → '
              'away from it or walled in first, then fed: the beef eaten, the zombie 3 or more off (or walled in), '
              'health above 6'),
         # the zombie summoned still (NoAI) so the food drain runs unhurt; it wakes once the bar is at 6
         scene=[('sheet', '_ARENA'), ('cmd', 'damage @p 14 minecraft:magic'),
                ('summon', 'zombie', ('@', 2, 0, 0), '{PersistenceRequired:1b,NoAI:1b}')],
         # the window is the fight's alone (the drain and the wake are `before` hooks, off the clock): 14 s to back
         # off or wall in and eat, well inside 25 s once the rounds' own time is added (20 s ran to 25.0 s)
         run=('fight_until', ['minecraft:zombie'], 14, False),
         before=[('start', 'combat__low_hp_eat'), ('drain_to', 6, 20, (4, 8)), ('loose', 'zombie'), ('&record_bids',)],
         check=[('state', 'health', '>', 6), ('alive',), ('count', 'minecraft:cooked_beef', '<', 16),
                ('away_or_walled', ['minecraft:zombie'])],
         budget=25, point='B', combat=True, stochastic=True, tags={'base': 'fight', 'enemy': 'zombie', 'blood': 'low'},
         expect_entities=[('minecraft:zombie', 1, 1)],
         expect=[(('@', -9, -1, -9), ('@', 9, -1, 9), 'stone', 361, 361), (('@', -9, 4, -9), ('@', 9, 4, 9), 'stone', 361, 361),
                 (('@', -9, 0, -9), ('@', 9, 3, 9), 'glass', 288, 288)],      # the _ARENA: floor, roof, walls
         expect_gear={'items': [['minecraft:iron_sword', 1]], 'offhand': 'minecraft:shield'}),
    dict(name='fight_creeper_sword', module='fight_loop',
         doc='Iron sword, a creeper 4 blocks off → the creeper gone (dead or blown up in the air), health ≥ 16',
         scene=[('sheet', '_ARENA'), ('summon', 'creeper', ('@', 4, 0, 0), '{PersistenceRequired:1b}')],
         run=('fight_until', ['minecraft:creeper'], 25), before=[('start', 'fight_creeper_sword'), ('&record_bids',)],
         check=[('gone', ['minecraft:creeper']), ('hp_kept', 16)], budget=25, point='B', combat=True, stochastic=True,
         tags={'base': 'fight', 'enemy': 'creeper', 'ground': 'open'}, expect_entities=[('minecraft:creeper', 1)],
         expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='fight_creeper_by_home', module='fight_loop',
         doc=('Iron sword, a creeper 4 blocks off, a bed and a furnace of ours within 3 of it → the creeper gone (dead '
              'or blown up in the air), health ≥ 16, the bed and the furnace still standing'),
         scene=[('sheet', '_ARENA'), ('setblock', ('@', 4, 0, 2), 'red_bed[facing=east,part=foot]'),
                ('setblock', ('@', 5, 0, 2), 'red_bed[facing=east,part=head]'),
                ('setblock', ('@', 4, 0, -2), 'furnace'),
                ('summon', 'creeper', ('@', 4, 0, 0), '{PersistenceRequired:1b}')],
         run=('fight_until', ['minecraft:creeper'], 25),
         before=[('start', 'fight_creeper_by_home'), ('&record_bids',), ('&home_is_ours',)],
         check=[('gone', ['minecraft:creeper']), ('hp_kept', 16),
                ('blocks', ('@', 4, 0, 2), ('@', 5, 0, 2), ('red_bed',), 2),
                ('blocks', ('@', 4, 0, -2), ('@', 4, 0, -2), ('furnace',), 1)],
         budget=25, point='B', combat=True, stochastic=True,
         tags={'base': 'fight', 'enemy': 'creeper', 'ground': 'home'}, expect_entities=[('minecraft:creeper', 1)],
         expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
]
# -- one-off rows written in code (no word earns its place): kept as the old sheet wrote them ------------------------
def CODE_ROWS():
    """The one-off rows, built when the sheet is (they are written in vocab's words, and vocab reads this table's
    dimensions at its own import: no import-time cycle)."""
    from . import vocab
    globals().update({k: getattr(vocab, k) for k in vocab.__all__ if k not in globals()})
    return [
        dict(name="fight_before_upkeep",
             doc="Arena, iron sword and armour but no pickaxe, a zombie 4 blocks off, nothing queued → the zombie dead "
                 "before any log is gathered (must not), the player never leaves the arena",
             module="brain", point="C", skills=[], tier_fixed="brain", combat=True, stochastic=True,
             tags={"base": "brain", "family": "fight_first"},
             setup=[c for c in _ARENA if "stone_pickaxe" not in c] + [f"summon zombie {_c(at(4, 0, 0))} {{PersistenceRequired:1b}}"],
             expect_entities=[("minecraft:zombie", 1)],
             before=_hooks(_start("fight_before_upkeep"), _first_times, _record_bids),
             run=_brain_rounds(24, lambda: not _hostiles(24, {"minecraft:zombie"})),
             check=_all(_gone(["minecraft:zombie"]), _hp_kept(10), lambda api, inv: _near(api, at(0, 0, 0), 9),
                        lambda api, inv: FIRST.get("log") is None),
             budget=limit(), expect=SHEET_EXPECT),
        dict(name="combat__knocked_off_edge",
             doc="A zombie that hits hard enough to throw us off a platform 20 blocks up, iron kit + water bucket → "
                 "knocked off, the fall caught: alive, health within 4 of the start, the bucket back in the bag",
             module="fight_loop", point="B", skills=[], combat=True, stochastic=True,
             tags={"base": "fight", "enemy": "zombie", "ground": "edge"},
             setup=[f"fill {_c(at(-8, -17, -8))} {_c(at(8, -17, 8))} stone",
                    f"fill {_c(at(-8, -16, -8))} {_c(at(8, EDGE_Y + 3, 8))} air",
                    f"fill {_c(at(-2, EDGE_Y - 1, -2))} {_c(at(2, EDGE_Y - 1, 2))} stone",
                    _tp(2, EDGE_Y, 0), "give @p iron_sword", "give @p water_bucket",
                    "item replace entity @p armor.chest with iron_chestplate",
                    f"summon zombie {_c(at(0, EDGE_Y, 0))} {{PersistenceRequired:1b,"
                    f"attributes:[{{id:\"minecraft:attack_knockback\",base:3.0}}]}}"],
             expect_entities=[("minecraft:zombie", 1)],
             before=_hooks(_start("combat__knocked_off_edge"), _record_bids),
             run=_fight_until(["minecraft:zombie"], 22, False),
             check=_all(_alive(1), lambda api, inv: api.get("/state")["y"] < at(0, EDGE_Y - 10, 0)[1],
                        lambda api, inv: api.get("/state")["health"] >= BASE["state"]["health"] - 4,
                        lambda api, inv: inv.count("minecraft:water_bucket") >= 1),
             budget=limit(), expect=SHEET_EXPECT),
    ]
