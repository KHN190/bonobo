"""Fitting the threat parameters from recorded ordinary play. Pure: rows in, numbers out.

`play.toml` lists `notice_r` and `follow_p` under `unmeasured`, which is honest and useless on its own — a
declared guess is still a guess. These are what make them stop being guesses, from the decision tape the brain
already writes (`decisions.jsonl`): every round carries where we stood, what was near, and how much health we had.

Two numbers, two questions the tape can answer:

  notice_r   Beyond what distance does a hostile mob's presence stop showing up as damage? Group the sightings by
             distance, ask what fraction were followed by being hurt, and take the distance where that fraction
             falls through half. A mob that never reaches us is not a threat at that range, whatever the table says.

  follow_p   After walking away from a threat, how much of it is back on us a few seconds later? The ratio of the
             pressure after to the pressure before, over the rounds where the agent chose to leave. Zero would
             mean fleeing solves things permanently, which is what the model used to assume, and why it never once
             chose to fight.

Neither answer is a fit to a curve with parameters of its own. They are order statistics over what happened,
because there is no theory here to fit — only a world that either hurt us or did not.
"""

HURT_WINDOW_S = 6.0          # how long after a sighting the damage still counts as that sighting's
MIN_SAMPLES = 8              # below this, say so rather than return a number nobody should trust


def _median(xs):
    s = sorted(xs)
    if not s:
        return None
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def notice_radius(sightings, bin_size=4.0, floor=0.5):
    """sightings: [(distance, hurt_soon)] → the distance at which being hurt stops being more likely than not.

    Binned, not fitted: with a few hundred sightings a curve would be fitting noise, and what the planner needs is
    one number — where awareness starts to fade.
    """
    if len(sightings) < MIN_SAMPLES:
        return None
    bins = {}
    for d, hurt in sightings:
        b = int(float(d) // bin_size)
        hit, n = bins.get(b, (0, 0))
        bins[b] = (hit + bool(hurt), n + 1)
    edge = None
    for b in sorted(bins):
        hit, n = bins[b]
        if n and hit / n >= floor:
            edge = (b + 1) * bin_size      # this whole bin still bites: the radius reaches its far edge
        elif edge is not None:
            break                          # first quiet bin after a biting one: that is where it stops
    return edge


def follow_fraction(escapes):
    """escapes: [(pressure_before, pressure_after)] → the share of a threat that comes with us when we walk away.

    Clamped to [0, 1]: more pressure after than before is another mob arriving, not this one following harder.
    """
    ratios = [min(1.0, after / before) for before, after in escapes if before > 0]
    return _median(ratios) if len(ratios) >= MIN_SAMPLES else None


def sightings_from(rounds, hp_of, entities_of, pos_of, time_of):
    """Pull (distance, hurt_soon) out of recorded rounds, given accessors for the four things needed.

    The accessors are arguments because the tape's shape is the tape's business: this module knows that a sighting
    is a distance and an outcome, and nothing about JSON.
    """
    import math
    out = []
    for i, row in enumerate(rounds):
        here, now = pos_of(row), time_of(row)
        if here is None:
            continue
        later = [r for r in rounds[i + 1:] if time_of(r) - now <= HURT_WINDOW_S]
        if not later:
            continue
        hurt = min([hp_of(r) for r in later] + [hp_of(row)]) < hp_of(row)
        for e in entities_of(row) or []:
            try:
                d = math.dist(here, (e["x"], e["y"], e["z"]))
            except (KeyError, TypeError):
                continue
            out.append((d, hurt))
    return out


def escapes_from(rounds, pick_of, pressure_of, time_of, gap_s=5.0):
    """Pull (before, after) pressure pairs out of the rounds where the agent chose to leave."""
    out = []
    for i, row in enumerate(rounds):
        if pick_of(row) != "threat:evade":
            continue
        before = pressure_of(row)
        after = next((pressure_of(r) for r in rounds[i + 1:] if time_of(r) - time_of(row) >= gap_s), None)
        if before is not None and after is not None:
            out.append((before, after))
    return out
