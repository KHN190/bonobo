"""What a row's pass condition reads: the world, or a proxy. A check word is WORLD when it reads the server — blocks,
entities, the bag, the body's /state (health, dead, place, air), a command's reply, or a harness watcher sampling those
over the run; PROXY when it reads the bot's own state — the fight loop's record, the brain, memory, the api calls it
made, its log, its timings. A proxy may be a readout (a failed row's report), never a pass condition: a row passes on
what the world shows (a mob gone is no kill, hp read after a respawn is no hp kept, a decision rhythm is no fight).
tests/test_judged.py holds every row to this (its PASS_ALLOW: the few proxies a pass still reads, each with why)."""
# words that read the server (commands, /state, /inventory, /entities, regions) or a harness watcher over those
WORLD = {
    "gain", "count", "state", "bag", "alive", "blocks", "same_bag", "same_bag_and_place", "placed_facing",
    "gone", "killed", "is_day", "food_up", "no_block_suffix", "free_slots", "breathing",
    "slot_has", "has_stone_pickaxe", "in_the_patch_underground", "surfaced", "shield_kept", "threat_resolved",
    "away_or_walled", "dropped_nothing", "under_feet", "room_to_work", "mobs_near", "found_near", "in_overworld",
    "head_clear", "enclosed", "count_blocks", "trades", "hostiles", "wave_cleared", "nether_kit_ready",
    "trek_check",               # the body's end position (/state) against the target
    "before_in_bag",            # FIRST: a watcher reading the bag (and the world's clock) through the run
    "walk_ate", "mine_fed",     # /state frames: food rose while x grew / while the jar's work task ran
    "ghast_answered",           # the server's ghast Health, the fireballs /entities showed, the body's worst health
    "endermen_calm",            # the server's anger of each enderman
    "kept_health",              # the body's /state: alive, health against the row's start
    "rose",                     # a /state field against where it stood as the run began
    "escaped",                  # an escape cell's outcome: alive, health lost (the body's /state at the window's end)
    "took_cover_alcove",        # the runner's /state trace: where the body stood, its health
    "deflected",                # the server's player Health, each volley fireball's end read off /entities
    "arrived",                  # the body's /state by the walker's own arrival test (nav.there)
    "door_seen",                # a watcher reading the door's block state through the run
    "door_state",               # the door cells' block states read at the end
    "unchanged",                # the box's walls and roof standing
    "worn",                     # the bag: a tool's damage as the mod reports it
    "dug_with",                 # the box's blocks (what was broken) and the tool's damage in the bag
    "no_reflex",                # a watcher sampling the body's /state `blocking` through the window
    "held",                     # the body's /state: the main hand
}

# words that hold a state over the window: true at its start, they pass only if still true at its end (idle mode
# judges them there, never early)
HOLD = {"alive", "kept_health", "escaped", "shield_kept", "endermen_calm"}

# words that read the bot: the fight loop, the brain, memory, the api calls made, the log, timings, the bench's own
# records of what the bot did
PROXY = {
    "kills_by_the_fight": "FIGHT_LOG: kills inferred from the fight's own samples and bids",
    "no_stall": "FIGHT_LOG + trace task: the decision rhythm",
    "answered_with": "FIGHT_LOG: the answer kinds the fight carried",
    "decision_gaps_ok": "FIGHT_LOG: the decision rhythm",
    "last_seen": "FIGHT_LOG samples: each mob that went, as the fight last read it (a readout)",
    "failed_as_expected": "the skill's own exception message",
    "interrupted": "INTERRUPTS / RESUMED_LEFT: the bench's count of the interrupts it injected",
    "slice_check": "the cerebellum's log (loops, idle) and SLICE",
    "replans_at_most": "brain.replan calls",
    "brain_rule": "a brain family's rule: seen_store reads memory and the api's scans",
    "behaviour": "the recorded answer that went out (_went_out) beside the recorded outcome",
    "answers_are_closed": "a fight sweep's recorded decision (held column, options, no-go)",
    "shapes_fit_the_enemy": "a fight sweep's recorded decision",
    "more_of_them_costs_more": "a fight sweep's recorded prices",
    "no_scan": "the api calls the bot made (FINDS)",
    "not_remembered": "the bot's memory",
    "remembered_any": "the bot's memory",
    "memory": "the bot's memory file (a site noted)",
    "stronghold_error": "the bot's memory against /locate",
    "found_fortress_now": "the bot's memory of a fortress site",
    "portal_room_found": "PORTAL_ROOM_OK: the skill returned",
    "skill_within": "skill.LAST_S: the skill's own timing",
    "road_times": "ROAD_TIMES: the trips' timings",
    "not_banned": "the brain's blacklist",
}

# composition and containers: not words, their arguments are
STRUCTURE = {"all", "any", "not", "now", "now_api", "api_only", "thunk", "call", "named_all", "sweep_check"}

# a hand-written check (a lambda in a code row) is read by the names its code uses: these mean it reads the bot
MARKS = {"FIGHT_LOG": "kills_by_the_fight", "fight_loop": "no_stall", "INTERRUPTS": "interrupted",
         "RESUMED_LEFT": "interrupted", "FAILED_AS_EXPECTED": "failed_as_expected", "BRAIN_LOG": "replans_at_most",
         "FINDS": "no_scan", "SLICE": "slice_check", "LAST_LINES": "slice_check", "Memory": "memory",
         "mem": "memory", "BRAIN": "not_banned", "blacklist": "not_banned", "LAST_S": "skill_within",
         "ROAD_TIMES": "road_times", "PORTAL_ROOM_OK": "portal_room_found"}


def code_names(code):
    """Pure: every name and string constant a code object (and the code nested in it) uses."""
    out = set(code.co_names) | set(code.co_freevars) | {c for c in code.co_consts if isinstance(c, str)}
    for c in code.co_consts:
        if hasattr(c, "co_names"):
            out |= code_names(c)
    return out


def norm(word):
    """Pure: a word as the classes name it — no "!"/"&", no module path, no leading underscore."""
    word = word.lstrip("!&").rsplit(":", 1)[-1]
    return word.lstrip("_")


def data_words(item, top=True):
    """Pure: the words a check's data names ((kind, *args) at the top, ("!kind", ...) / ("&name",) inside)."""
    out = set()
    if callable(item):
        return callable_words(item)
    if isinstance(item, tuple) and item and isinstance(item[0], str) and (top or item[0][:1] in "!&"):
        w = norm(item[0])
        if w == "call":
            out.add(norm(item[1]))
        elif w not in STRUCTURE:
            out.add(w)
        for a in item[1:]:
            out |= data_words(a, False)
    elif isinstance(item, (list, tuple)):
        for a in item:
            out |= data_words(a, False)
    elif isinstance(item, dict):
        for a in item.values():
            out |= data_words(a, False)
    return out


def callable_words(fn):
    """The words a built check reads: its recorded data, its parts, the factory that made it, else its source."""
    if getattr(fn, "__table__", None):
        return data_words(tuple(fn.__table__))
    parts = getattr(fn, "parts", None)
    if parts:
        return set().union(*(callable_words(p) for p in parts))
    head = getattr(fn, "__qualname__", "").split(".<locals>")[0]
    if "<locals>" in getattr(fn, "__qualname__", "") and norm(head) in WORLD | set(PROXY):
        return {norm(head)}             # a closure a word's factory made: the word
    code = getattr(fn, "__code__", None)
    if code is None:
        return {"?" + repr(fn)}
    names = code_names(code)
    return {w for m, w in MARKS.items() if m in names} or {"lambda:world"}

