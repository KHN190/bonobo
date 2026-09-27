"""Bench table, combat tier: rows as data in `bench/vocab.py`'s words, built by `bench/table.py`.
FAMILIES: (template, [params, ...]) — one entry, many rows (vocab.TEMPLATES). ROWS: the one-off rows, each in words.
CODE_ROWS: the one-off rows no word earns its place for, written in code with vocab's helpers."""

from .core import BEST_TOOLS
# -- the dimensions of a fight cell (the combat table's own data; vocab's `_build` turns a cell into commands) -------
# enemies named by what they do ("pack" is walker × count=three: the same world under another name)
ENEMY = {"none": None, "walker": "minecraft:zombie", "archer": "minecraft:skeleton", "climber": "minecraft:spider",
         "bomb": "minecraft:creeper", "teleporter": "minecraft:enderman"}
COUNT = {"one": 1, "three": 3}
# ground that gives each shaping column something to be worth (a flat arena prices `reshape` at nothing)
GROUND = {"open": [("fill", ("@", 4, 0, 2), ("@", 5, 1, 3), "stone")],                  # a step to stand up on
          "corridor": [("fill", ("@", -1, 1, -2), ("@", 9, 3, -2), "cobblestone"),
                       ("fill", ("@", -1, 1, 2), ("@", 9, 3, 2), "cobblestone"),
                       ("fill", ("@", 4, 1, -1), ("@", 4, 3, 1), "air")],                # one gap, wide enough to close
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
NEEDS = {"blocks": ("reshape", "wall_in"), "food": ("eat",), "shield": ("shield",),
         "full": ("reshape", "wall_in", "eat", "shield"), "nothing": ()}
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
         ('fight_creeper_1', 'creeper', 1, 'common', 25, 14, 'resolved'),
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
         doc='Nether hall open to one side, a ghast 20 blocks out, sword + armor → 30 s of reflexes, still healthy.',
         scene=[('floor', 'netherrack', 6, 2),
                ('at', 'fill {0} {1} netherrack hollow', ('@', -6, 0, -6), ('@', 6, 0, 6)),
                ('fill', ('@', -5, 0, -5), ('@', 5, 0, 5), 'air'), ('tp', ('@', 0, 0, 0)), ('cmd', 'clear @p'),
                ('give', 'diamond_sword'), ('cmd', 'item replace entity @p armor.chest with iron_chestplate'),
                ('cmd', 'item replace entity @p armor.head with golden_helmet'), ('give', 'cooked_beef', 16)],
         run=('reflex_for', 30),
         before=[('do', 'chat',
                  ['execute in minecraft:the_nether run summon ghast 10018 206 10000 {PersistenceRequired:1b}'], {})],
         check=[('state', 'health', '>=', 10), ('not', ('!state', 'dead'))], budget=25,
         dimension='minecraft:the_nether', combat=True,
         expect=[(('@', -6, -1, -6), ('@', 6, -1, 6), 'netherrack', 169, 169)]),
    dict(name='bed_bomb_kill', module='end',
         doc='Speedrun End kit, the dragon perched and worn (crystals gone, 8 hp) → dead by a bed bomb.',
         scene=[('sheet', 'SPEEDRUN_END_KIT')], run=('do', 'bonobo.end.slay_dragon', ['$ctx'], {}),
         before=[('&worn_perched_dragon',)],
         check=[('call', 'dragon_health', [], 'is', None), ('not', ('!state', 'dead'))], budget=25, raw=True,
         combat=True, dimension='minecraft:the_end', release=True, skills=['slay_dragon']),
    dict(name='fight_zombie_1_full_bag', module='fight_loop',
         doc=('Walled platform, iron kit, the bag full of dirt: 1 zombie → dead, health ≥ 12, decisions as often as '
              'ever: the drop it cannot pick up changes nothing'),
         scene=[('sheet', '_ARENA'), ('summon', 'zombie', ('@', 4, 0, 0), '{PersistenceRequired:1b}')],
         run=('fight_until', ['minecraft:zombie'], 23),
         before=[('hooks', ('!start', 'fight_zombie_1'), ('&record_bids',)), ('fill_bag', 0)],
         check=[('hp_kept', 12), ('gone', ['minecraft:zombie']), ('decision_gaps_ok',)], budget=25, combat=True,
         point='B', tier_fixed='exception',
         tags={'base': 'fight', 'enemy': 'zombie', 'count': 1, 'inventory': 'full_bag'},
         expect_entities=[('minecraft:zombie', 1)], expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
    dict(name='combat__low_hp_eat', module='fight_loop',
         doc=('6 hp, one zombie 2 blocks off, blocks and cooked beef → away from it or walled in first, then fed: '
              'health ends above 6'),
         scene=[('sheet', '_ARENA'), ('cmd', 'damage @p 14 minecraft:magic'),
                ('summon', 'zombie', ('@', 2, 0, 0), '{PersistenceRequired:1b}')],
         run=('fight_until', ['minecraft:zombie'], 22, False),
         before=[('start', 'combat__low_hp_eat'), ('&record_bids',)], check=[('state', 'health', '>', 6), ('alive',)],
         budget=25, point='B', combat=True, stochastic=True, tags={'base': 'fight', 'enemy': 'zombie', 'blood': 'low'},
         expect_entities=[('minecraft:zombie', 1)], expect=[(('@', -10, -17, -10), ('@', 20, 9, 10), '*', 1, 1000000)]),
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
from .vocab import *  # noqa: E402,F401,F403  (the words and helpers a one-off row is written in)
CODE_ROWS = [
    dict(name="fight_dragon",
         doc="The End's main island, the dragon perched and worn (crystals gone, 8 hp), diamond sword, shield, iron "
             "armour, food, blocks → dragon dead.",
         module="end", raw=True, combat=True, dimension="minecraft:the_end", release=True, skills=["slay_dragon"],
         # a dragon spawns once per world: the fight's last phase is built (WORN_DRAGON)
         setup=["clear @p", "give @p diamond_sword", "item replace entity @p weapon.offhand with shield",
                "item replace entity @p armor.chest with iron_chestplate",
                "item replace entity @p armor.head with iron_helmet",
                "item replace entity @p armor.legs with iron_leggings", "item replace entity @p armor.feet with iron_boots",
                "give @p cooked_beef 32", "give @p cobblestone 64", "give @p water_bucket"],
         before=_worn_perched_dragon,
         run=lambda ctx: __import__("bonobo.end", fromlist=["slay_dragon"]).slay_dragon(ctx),
         check=lambda api, inv: not any(e["type"] == "minecraft:ender_dragon"
                                        for e in __import__("bonobo.world", fromlist=["entities"]).entities(200)),
         budget=limit()),
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
