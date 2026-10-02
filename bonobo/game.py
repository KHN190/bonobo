"""The game's own numbers, imported by nothing else of ours: light levels, projectile sizes, the eye height. A leaf of pure
constants (no import, no function): any module may read it without its re-run key growing (tests/test_layers)."""

COVERED_SKY = 4                  # sky light at most this: rock overhead
SPAWN_BLOCK_LIGHT = 0            # hostiles spawn only at this block light or less (1.18+)
DAYLIT_SKY = 7                   # sky light above this by day: daylight reaches the cell (no spawns)
OPEN_SKY = 14                    # sky light at least this: open to the sky overhead
# a projectile's radius (blocks): what a shot in flight keeps us out of
ARROWS = {"minecraft:arrow": 1.0, "minecraft:spectral_arrow": 1.0, "minecraft:trident": 1.0}
EYE_HEIGHT = 1.62                # the jar's WorldUtil.EYE_HEIGHT: eyes above the feet
