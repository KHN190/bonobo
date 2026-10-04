"""The game's own numbers, imported by nothing else of ours: light levels, projectile sizes, the eye height. A leaf of pure
constants (no import, no function): any module may read it without its re-run key growing (tests/test_layers)."""

COVERED_SKY = 4                  # sky light at most this: rock overhead
SPAWN_BLOCK_LIGHT = 0            # hostiles spawn only at this block light or less (1.18+)
DAYLIT_SKY = 7                   # sky light above this by day: daylight reaches the cell (no spawns)
OPEN_SKY = 14                    # sky light at least this: open to the sky overhead
# a projectile's radius (blocks): what a shot in flight keeps us out of
ARROWS = {"minecraft:arrow": 1.0, "minecraft:spectral_arrow": 1.0, "minecraft:trident": 1.0}
EYE_HEIGHT = 1.62                # the jar's WorldUtil.EYE_HEIGHT: eyes above the feet
JUMP_BLOCKS = 1                  # a jump climbs one block (Minecraft Wiki, "Jumping": 1.25 blocks high)
# mobs, Java 1.21 Normal difficulty (Minecraft Wiki infoboxes; /state does not report the difficulty)
MELEE_TICKS = 20                 # MeleeAttackGoal's interval between hits
CREEPER_FUSE_TICKS = 30          # lit at 3 blocks, defused past 7
CREEPER_STOP_BLOCKS = 7.0
EXPLOSION_POWER = {"minecraft:creeper": 3.0, "minecraft:fireball": 1.0}
DETONATES_WITHIN = {"minecraft:creeper": 3.0, "minecraft:fireball": 0.0}    # lit at 3 blocks; a fireball on contact
MOB_HP = {"minecraft:zombie": 20, "minecraft:husk": 20, "minecraft:zombie_villager": 20, "minecraft:drowned": 20,
          "minecraft:skeleton": 20, "minecraft:stray": 20, "minecraft:pillager": 24, "minecraft:witch": 26,
          "minecraft:creeper": 20, "minecraft:spider": 16, "minecraft:cave_spider": 12, "minecraft:phantom": 20,
          "minecraft:slime": 16, "minecraft:vindicator": 24, "minecraft:wither_skeleton": 20, "minecraft:ghast": 10,
          "minecraft:piglin": 16, "minecraft:zombified_piglin": 20, "minecraft:blaze": 20,
          "minecraft:ender_dragon": 200, "minecraft:enderman": 40}
# one hit (a range's mean); the creeper's at point blank; the ghast's fireball on impact; the dragon's body
MOB_HIT = {"minecraft:zombie": 3.0, "minecraft:husk": 3.0, "minecraft:zombie_villager": 3.0, "minecraft:drowned": 3.0,
           "minecraft:skeleton": 4.0, "minecraft:stray": 4.0, "minecraft:pillager": 4.0, "minecraft:witch": 6.0,
           "minecraft:creeper": 43.0, "minecraft:spider": 2.0, "minecraft:cave_spider": 2.0, "minecraft:phantom": 2.0,
           "minecraft:slime": 4.0, "minecraft:vindicator": 13.0, "minecraft:wither_skeleton": 8.0,
           "minecraft:ghast": 6.0, "minecraft:piglin": 8.0, "minecraft:zombified_piglin": 8.0, "minecraft:blaze": 5.0,
           "minecraft:ender_dragon": 10.0, "minecraft:enderman": 7.0, "minecraft:small_fireball": 5.0,
           "minecraft:fireball": 6.0, "minecraft:dragon_fireball": 6.0, "minecraft:area_effect_cloud": 6.0}
# ticks between hits; the blaze: a 60-tick charge, 3 shots 6 apart, 6 more, 100 waiting — 178 ticks per 3
MOB_CADENCE_TICKS = {"minecraft:zombie": MELEE_TICKS, "minecraft:husk": MELEE_TICKS,
                     "minecraft:zombie_villager": MELEE_TICKS, "minecraft:drowned": MELEE_TICKS,
                     "minecraft:skeleton": 60, "minecraft:stray": 60, "minecraft:pillager": 60, "minecraft:witch": 60,
                     "minecraft:creeper": CREEPER_FUSE_TICKS, "minecraft:spider": MELEE_TICKS,
                     "minecraft:cave_spider": MELEE_TICKS, "minecraft:phantom": 200,
                     "minecraft:vindicator": MELEE_TICKS, "minecraft:wither_skeleton": MELEE_TICKS,
                     "minecraft:ghast": 60, "minecraft:piglin": MELEE_TICKS, "minecraft:zombified_piglin": MELEE_TICKS,
                     "minecraft:blaze": 178 / 3, "minecraft:enderman": MELEE_TICKS,
                     "minecraft:area_effect_cloud": 20}
# a hit pushes its target 0.4 blocks/tick, slowed ×0.6×0.91 a tick on the ground (LivingEntity.knockback, travel)
HIT_KNOCKBACK = 0.4
GROUND_DRAG = 0.6 * 0.91
TICKS_PER_S = 20                 # the game clock: ticks in one second
SWEEP_DAMAGE = 1.0              # a sword's sweep: each mob beside the one struck (on the ground, not sprinting)
# Mob.isWithinMeleeAttackRange (Java 1.21): the attacker's box grown sqrt(2.04) − 0.6 a side must meet the target's
MELEE_INFLATE = 2.04 ** 0.5 - 0.6
PLAYER_WIDTH = 0.6
MOB_WIDTH = {"minecraft:zombie": 0.6, "minecraft:husk": 0.6, "minecraft:zombie_villager": 0.6, "minecraft:drowned": 0.6,
             "minecraft:spider": 1.4, "minecraft:cave_spider": 0.7, "minecraft:phantom": 0.9, "minecraft:vindicator": 0.6,
             "minecraft:wither_skeleton": 0.7, "minecraft:piglin": 0.6, "minecraft:zombified_piglin": 0.6,
             "minecraft:enderman": 0.6}
EAT_TICKS = 32                   # one food eaten (Minecraft Wiki, Food: 1.6 s)
BREAK_COOLDOWN = 5               # ticks before the next block's break begins (ClientPlayerInteractionManager.blockBreakingCooldown)
PLAYER_SPRINT = 5.612            # blocks/s sprinting (Minecraft Wiki, Sprinting)
PLAYER_SPRINT_JUMP = 7.127       # blocks/s sprint-jumping (Minecraft Wiki, Sprinting)
# RangedBowAttackGoal strafe(-0.5) × MoveControl.strafe 0.25 × movement_speed 0.25 × 43.17 b/s per unit; not measured
BOW_RETREAT = {"minecraft:skeleton": 0.5 * 0.25 * 0.25 * 43.17, "minecraft:stray": 0.5 * 0.25 * 0.25 * 43.17}
WAYPOINT_R = 6                   # a road's ends within this many blocks are the same waypoint
SUFFOCATION = ("inWall",)        # the damage source name of a head inside a block (DamageTypes.IN_WALL, /state lastDamage)
