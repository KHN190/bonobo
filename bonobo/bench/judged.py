"""What a row's pass condition reads: the world, or a proxy. A check word is WORLD when it reads the server — blocks,
entities, the bag, the body's /state (health, dead, place, air), a command's reply, or a harness watcher sampling those
over the run; PROXY when it reads the bot's own state — the fight loop's record, the brain, memory, the api calls it
made, its log, its timings. A proxy may be a readout (a failed row's report), never a pass condition: a row passes on
what the world shows (a mob gone is no kill, hp read after a respawn is no hp kept, a decision rhythm is no fight).
PASS_ALLOW names the few proxies a pass still reads, each with why; tests/test_judged.py holds every row to this."""
# words that read the server (commands, /state, /inventory, /entities, regions) or a harness watcher over those
WORLD = {
    "gain", "count", "state", "bag", "alive", "blocks", "at", "same_bag", "same_bag_and_place", "placed_facing",
    "gone", "hp_kept", "killed", "is_day", "food_up", "kept", "no_block_suffix", "free_slots", "breathing",
    "slot_has", "has_stone_pickaxe", "in_the_patch_underground", "surfaced", "shield_kept", "threat_resolved",
    "away_or_walled", "dropped_nothing", "under_feet", "room_to_work", "mobs_near", "found_near", "in_overworld",
    "head_clear", "enclosed", "near", "count_blocks", "trades", "hostiles", "wave_cleared", "nether_kit_ready",
    "trek_check",               # the body's end position (/state) against the target
    "before_in_bag",            # FIRST: a watcher reading the bag (and the world's clock) through the run
    "walk_ate", "mine_fed",     # /state frames: food rose while x grew / while the jar's work task ran
    "ghast_answered",           # the server's ghast Health, the fireballs /entities showed, the body's worst health
    "endermen_calm",            # the server's AngerTime of each enderman
    "took_cover_alcove",        # the runner's /state trace: where the body stood, its health
    "deflected",                # the server's player Health, each volley fireball's end read off /entities
}

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

_EXPECTED = "an expect_failure row: the expected failure IS the outcome under test; the world parts (alive, the bag " \
            "unchanged) ride beside it"
_DECISION = "a decision-table row: its claim is which answer the bot chose; the recorded outcome (hp, gap, bag) " \
            "rides beside it"
_NOTE = "a find/scout skill: its product is the note it leaves (the site), there is no other world trace of 'found'"
_SPEED = "a speed row: its claim is the time; the world effect is judged beside it"

# proxies a pass condition may still read, each with why — keep it short: every entry is a row that trusts the bot
PASS_ALLOW = {
    "failed_as_expected": _EXPECTED,
    "interrupted": "proves the bench's own injected interrupt landed (the scene), never the success",
    "slice_check": "loops and idle time have no world reading; the slice's done() is judged by the bag",
    "replans_at_most": _DECISION, "brain_rule": _DECISION, "not_banned": _DECISION, "behaviour": _DECISION,
    "no_scan": _DECISION,
    "answers_are_closed": _DECISION, "shapes_fit_the_enemy": _DECISION, "more_of_them_costs_more": _DECISION,
    "remembered_any": _NOTE, "memory": _NOTE, "stronghold_error": _NOTE,
    "found_fortress_now": _NOTE, "portal_room_found": _NOTE,
    "skill_within": _SPEED, "road_times": _SPEED,
}

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


def row_pass_words(row):
    """The words a table row's pass condition (its `check`) reads."""
    check = row["check"]
    if callable(check):
        return callable_words(check)
    return set().union(*(data_words(i) for i in check)) if check else set()


def verdict_of_words(words, allow=None):
    """Pure: (proxies the pass reads that are not allowed, words in neither class)."""
    allow = PASS_ALLOW if allow is None else allow
    known = WORLD | set(PROXY) | {"lambda:world"}
    return sorted(w for w in words if w in PROXY and w not in allow), sorted(w for w in words if w not in known)
