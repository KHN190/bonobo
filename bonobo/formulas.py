"""The game's formulas (Java 1.21): pure, no import; counted in every closure that reads them."""


def armor_reduction(points, toughness=0.0, hit=0.0):
    """Fraction of a hit armour removes (Java: min(20, max(a/5, a - 4d/(t+8))) / 25)."""
    return min(20.0, max(points / 5.0, points - 4.0 * hit / (toughness + 8.0))) / 25.0


def explosion_damage(power, distance, exposure=1.0):
    """A blast's damage at `distance` (Java, Normal: impact = (1 - d/2P)·exposure; (impact² + impact)/2·7·2P + 1)."""
    impact = max(0.0, 1.0 - distance / (2.0 * power)) * exposure
    return (impact * impact + impact) / 2.0 * 7.0 * 2.0 * power + 1.0 if impact > 0 else 0.0
