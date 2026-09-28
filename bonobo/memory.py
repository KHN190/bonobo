"""Persistent world memory shared across sessions: sites, stations, what was seen where, deaths, night record. A *site* is any protected structure: the home base or a built shelter. Each may carry a snapshot of its solid blocks so damage can be detected and repaired. Digging near sites is allowed; digging *their blocks* is not."""

import json
import math
import os
import time
from . import beliefs, paths
from .data import GROUPS, VOLATILITY, bare, mid, seen_class

NOTES_FILE = paths.data("world-notes.json", env="MC_NOTES")

def _now():
    return time.strftime("%Y-%m-%d %H:%M")

# -- the section grid explore searches (Minecraft's 16³ sections): looked over when, holding what, and the frontier; pure
SECTION = 16

def section_of(pos):
    """Pure: the (cx, cy, cz) section a position lies in."""
    return int(math.floor(pos[0])) // SECTION, int(math.floor(pos[1])) // SECTION, int(math.floor(pos[2])) // SECTION

def sections_within(pos, radius):
    """Pure: every section whose centre lies within `radius` blocks of `pos` (in 3-D: a look sees above and below)."""
    cx, cy, cz = section_of(pos)
    r = int(radius) // SECTION + 1
    return [(cx + dx, cy + dy, cz + dz) for dx in range(-r, r + 1) for dy in range(-r, r + 1) for dz in range(-r, r + 1)
            if math.dist(((cx + dx) * SECTION + 8, (cy + dy) * SECTION + 8, (cz + dz) * SECTION + 8),
                         pos) <= radius]

def absent_ttl(kind):
    """Game ticks "looked over, none here" holds for `kind` (data.VOLATILITY's `absent`, by the kind's class)."""
    rule = VOLATILITY.get(seen_class(kind)) or VOLATILITY["slow"]
    return rule.get("absent") or VOLATILITY["slow"]["absent"]

def covered(row, kinds, tick):
    """Pure: this section's looks still answer every one of `kinds` within its `absent` TTL."""

    if row is None:
        return False
    looked = row.get("looked", {})
    for k in map(bare, kinds):
        if k in row["kinds"]:
            continue
        t = looked.get(k)
        if t is None:
            return False        # never looked, or a look with no game time: not an answer that can expire
        if tick is not None and tick - t > absent_ttl(k):
            return False
    return True

def out_of_look(skips, section, kinds, tick):
    """Pure: `section` was found out of look range from any reachable stand, recently enough for `kinds` (the absent
    TTL): a skip for the search, never a look — the section is not answered."""
    t = (skips or {}).get(section)
    if t is None:
        return False
    return tick is None or all(tick - t <= absent_ttl(k) for k in kinds)

def frontier(smap, here, kinds, tick, band=lambda kind: None, radius=12, skips=None):
    """Pure: sections to look next for `kinds`, nearest first, at each kind's own height, never or long ago looked
    over — and not out of look range from any reachable stand lately (`skips`)."""

    hx, hy, hz = section_of(here)
    layers = {(hy if band(k) is None else int(band(k)) // SECTION) for k in kinds}
    out = set()
    for cy in layers:
        want = [k for k in kinds if (hy if band(k) is None else int(band(k)) // SECTION) == cy]
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                s = (hx + dx, cy, hz + dz)
                if s != (hx, hy, hz) and not covered(smap.get(s), want, tick) and not out_of_look(skips, s, want, tick):
                    out.add(s)
    return sorted(out, key=lambda s: (math.dist(s, (hx, hy, hz)), s))

def section_centre(section):
    """Pure: the block at a section's centre (x, y, z)."""
    return tuple(c * SECTION + 8 for c in section)

SECTION_CAP = 4096      # sections explore remembers per dimension (the oldest, farthest go first)

TICK_READ_S = 1.0       # a tick read outside a round serves this long (a burst of stamps and judgements)
TICK_READER = None      # fn() → the game's tick now (skillcore wires /state gameTime): a look outside a round reads it

class Memory:
    def tick(self):
        """The game time a look or a note is stamped with and judged by: the round's clock, else — a skill run with
        no brain round (the bench's achieve, the CLI) — the game's tick read now (else None: nothing to read)."""
        if self.clock is not None or TICK_READER is None:
            return self.clock
        cached = getattr(self, "_tick_read", None)
        if cached is not None and time.time() - cached[0] < TICK_READ_S:
            return cached[1]                 # one read serves a burst (seen() judges every note)
        try:
            value = TICK_READER()
        except Exception:        # no game to ask (an offline test): unstamped, as before
            value = None
        self._tick_read = (time.time(), value)
        return value

    def __init__(self, path=NOTES_FILE):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        d = self.data
        self.clock = None     # game ticks (/state gameTime), set each round; what every "seen" note is stamped with
        for key, default in (("sites", []), ("stations", []), ("seen", []), ("deaths", []),
                             ("night", {"phase": "day", "slept": False, "missed": 0}), ("machines", []),
                             ("stats", {}), ("durations", {}), ("jobs", [])):
            d.setdefault(key, default)
        self._migrate()

    def _migrate(self):
        """Older notes had places.home, shelters[], base_snapshot and flags; fold them into sites."""
        d = self.data
        changed = False
        home = d.get("places", {}).get("home")
        if home and not any(s["kind"] == "home" for s in d["sites"]):
            site = {"name": "home", "kind": "home", "pos": home["pos"], "dimension": home["dimension"],
                    "snapshot": None, "dirty": False}
            snap = d.get("base_snapshot")
            if snap:
                site["snapshot"] = {"lo": snap["lo"], "hi": snap["hi"], "blocks": snap["blocks"]}
            d["sites"].append(site)
            changed = True
        for old in d.pop("shelters", []):
            if old.get("name") == "home" or any(s["pos"] == old["pos"] for s in d["sites"]):
                continue
            snap = old.get("snapshot")
            d["sites"].append({"name": old["name"], "kind": "shelter", "pos": old["pos"],
                               "dimension": old["dimension"], "dirty": old.get("dirty", False),
                               "snapshot": {"lo": [old["pos"][0] - 2, old["pos"][1] - 1, old["pos"][2] - 2],
                                            "hi": [old["pos"][0] + 2, old["pos"][1] + 2, old["pos"][2] + 2],
                                            "blocks": snap} if snap else None})
            changed = True
        flags = d.pop("flags", None)
        if flags is not None:
            d["night"]["missed"] = 0  # the old counter was unreliable
            d["lit"] = flags.get("base_lit", False)
            changed = True
        d.pop("base_snapshot", None)
        # Duplicates from before the writers deduplicated.
        for key, ident in (("stations", lambda s: (tuple(s["pos"]), s["dimension"])),):
            seen, unique = set(), []
            for item in d[key]:
                if ident(item) not in seen:
                    seen.add(ident(item))
                    unique.append(item)
            if len(unique) != len(d[key]):
                d[key] = unique
                changed = True
        if d.pop("progress", None) is not None:      # half-finished work is no longer kept: re-plan by search
            changed = True
        changed = self._fold_old_notes() or changed
        if changed:
            self.save()

    def _fold_old_notes(self):
        """Four old stores of "seen X at Y" on the wall clock, folded into one."""

        d = self.data
        old = [(r["kind"], r["pos"], r["dimension"]) for r in d.pop("resources", []) if not r.get("depleted")]
        old += [(k, x["pos"], x["dimension"]) for k, rows in d.pop("sightings", {}).items() for x in rows]
        old += [("lava", p["pos"], p["dimension"]) for p in d.pop("lava", [])]
        had_veins = d.pop("veins", None) is not None
        for kind, pos, dim in old:
            if seen_class(kind) in ("static", "slow"):
                self._put(kind, pos, dim, verify=True)
        return bool(old) or had_veins

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    # -- sites
    def sites(self, dimension=None, kinds=None):
        return [s for s in self.data["sites"]
                if (dimension is None or s["dimension"] == dimension) and (kinds is None or s["kind"] in kinds)]

    def home(self):
        return next((s for s in self.data["sites"] if s["kind"] == "home"), None)

    def nearest_site(self, pos, dimension, kinds=None):
        options = self.sites(dimension, kinds)
        return min(options, key=lambda s: math.dist(s["pos"], pos), default=None)

    def add_site(self, kind, pos, dimension, snapshot=None, name=None):
        name = name or f"{kind}-{len(self.data['sites']) + 1}"
        site = {"name": name, "kind": kind, "pos": list(pos), "dimension": dimension, "snapshot": snapshot,
                "dirty": False, "created": _now()}
        self.data["sites"] = [s for s in self.data["sites"] if s["name"] != name] + [site]
        self.save()
        return site

    def update_site(self, name, **fields):
        for s in self.data["sites"]:
            if s["name"] == name:
                s.update(fields)
        self.save()

    def protected_cells(self, dimension):
        """Every recorded structure block of every site: planners must not dig these."""
        cells = set()
        for s in self.sites(dimension):
            snap = s.get("snapshot")
            if snap:
                cells.update(tuple(int(v) for v in key.split(",")) for key in snap["blocks"])
        return cells | self.machine_cells(dimension) | self.build_cells(dimension)

    def build_cells(self, dimension):
        """Cells of started, unfinished builds: never mined (the portal goal once took its own frame apart)."""

        from . import blueprints
        cells = set()
        for name, b in self.data.get("builds", {}).items():
            bp = blueprints.REGISTRY.get(name)
            if bp and b.get("dimension") == dimension:
                cells.update(pos for pos, *_ in blueprints.placed(bp, tuple(b["origin"]), b["turns"]))
        return cells

    # -- machines built from blueprints, and what they are still processing
    def machines(self, dimension=None, tag=None):
        return [m for m in self.data["machines"]
                if (dimension is None or m["dimension"] == dimension) and (tag is None or tag in m.get("tags", []))]

    def add_machine(self, blueprint, origin, turns, dimension, tags):
        name = f"{blueprint}-{len(self.data['machines']) + 1}"
        self.data["machines"].append({"name": name, "blueprint": blueprint, "origin": list(origin), "turns": turns,
                                      "dimension": dimension, "tags": list(tags), "pending": [], "created": _now()})
        self.save()
        return name

    def add_pending(self, name, item, count, ready_at):
        for m in self.data["machines"]:
            if m["name"] == name:
                m.setdefault("pending", []).append({"item": item, "count": count, "ready_at": ready_at})
        self.save()

    def settle_pending(self, name, got):
        """Subtract collected items from a machine's pending outputs; empty ones reschedule a minute later."""

        got, now = dict(got), time.time()
        for m in self.data["machines"]:
            if m["name"] != name:
                continue
            left = []
            for p in m.get("pending", []):
                take = min(p["count"], max(0, got.get(p["item"], 0)))
                got[p["item"]] = got.get(p["item"], 0) - take
                p["count"] -= take
                if p["count"] > 0 and now < p["ready_at"] + 600:
                    if take == 0:
                        p["ready_at"] = now + 60
                    left.append(p)
            m["pending"] = left
        self.save()

    def pending_outputs(self, dimension):
        """Items on their way: machine outputs and furnace jobs left running while the agent works elsewhere."""
        out = {}
        for m in self.machines(dimension):
            for p in m.get("pending", []):
                out[p["item"]] = out.get(p["item"], 0) + p["count"]
        for j in self.jobs(dimension):
            out[j["item"]] = out.get(j["item"], 0) + j["count"]
        return out

    # -- background jobs: a furnace smelting on its own (time-estimated), collected when ready
    def jobs(self, dimension=None):
        return [j for j in self.data["jobs"] if dimension is None or j["dimension"] == dimension]

    def add_job(self, kind, pos, dimension, item, count, ready_at, carried, **contents):
        """A background job; `contents` records what went in, so what the furnace holds is known, never guessed."""

        # one id per job: jobs started in the same second shared an id and finished together
        self.data["job_seq"] = self.data.get("job_seq", 0) + 1
        job = {"id": f"{kind}-{int(time.time())}-{self.data['job_seq']}", "kind": kind, "pos": list(pos), "dimension": dimension,
               "item": item, "count": count, "ready_at": ready_at, "carried": carried, **contents}
        self.data["jobs"].append(job)
        self.save()
        return job

    def update_job(self, job_id, **fields):
        for j in self.data["jobs"]:
            if j["id"] == job_id:
                j.update(fields)
        self.save()

    def finish_job(self, job_id):
        self.data["jobs"] = [j for j in self.data["jobs"] if j["id"] != job_id]
        self.save()

    def postpone_job(self, job_id, seconds):
        for j in self.data["jobs"]:
            if j["id"] == job_id:
                j["ready_at"] = time.time() + seconds
        self.save()

    def machine_cells(self, dimension):
        from . import blueprints
        cells = set()
        for m in self.machines(dimension):
            bp = blueprints.REGISTRY.get(m["blueprint"])
            if bp:
                cells.update(pos for pos, *_ in blueprints.placed(bp, tuple(m["origin"]), m["turns"]))
        return cells

    # -- skill outcomes (DEPS-style selector: plans through steps that keep failing get dearer)
    def record_outcome(self, key, ok):
        s = self.data["stats"].setdefault(key, {"ok": 0.0, "fail": 0.0})
        # exponential forgetting: new tools or a new area can redeem a step
        s["ok"] = s["ok"] * 0.9 + (1 if ok else 0)
        s["fail"] = s["fail"] * 0.9 + (0 if ok else 1)
        self.save()

    # -- measured skill durations (seconds per unit, exponential moving average)
    def record_duration(self, key, seconds, units=1):
        per = seconds / max(1, units)
        d = self.data["durations"].get(key)
        if d is None:
            self.data["durations"][key] = {"per": per, "n": 1}
        else:
            d["per"] = d["per"] * 0.7 + per * 0.3
            d["n"] += 1
        self.save()

    def duration(self, key, min_samples=3):
        d = self.data["durations"].get(key)
        return d["per"] if d and d["n"] >= min_samples else None

    def success_rate(self, key):
        s = self.data["stats"].get(key)
        if not s:
            return 1.0
        return (s["ok"] + 1) / (s["ok"] + s["fail"] + 1)

    def mark_dirty_near(self, positions, dimension, radius=6):
        """Our own digging: sites near it turn dirty, slow notes to-verify, and a static note on the dug cell goes."""

        changed = False
        dug = [tuple(p) for p in positions]
        keep = []
        for r in self.data["seen"]:
            if r["dimension"] == dimension:
                cls = seen_class(r["kind"])
                if cls == "static" and any(math.dist(r["pos"], p) <= 1 for p in dug):
                    changed = True
                    continue
                if cls == "slow" and not r.get("verify") and any(math.dist(r["pos"], p) <= radius for p in dug):
                    r["verify"] = True
                    changed = True
            keep.append(r)
        self.data["seen"] = keep
        for s in self.sites(dimension):
            if s.get("snapshot") and not s.get("dirty") and any(
                    math.dist((p[0], p[2]), (s["pos"][0], s["pos"][2])) <= radius for p in positions):
                s["dirty"] = True
                changed = True
        if changed:
            self.save()

    # -- stations we placed (persisted so they get picked back up)
    def add_station(self, block, pos, dimension):
        if any(s["pos"] == list(pos) and s["dimension"] == dimension for s in self.data["stations"]):
            return
        self.data["stations"].append({"block": block, "pos": list(pos), "dimension": dimension})
        self.save()

    def stations(self, dimension=None, near=None, within=None):
        """Placed single-block stations (a crafting table, a furnace), optionally near a point."""

        out = [s for s in self.data["stations"] if dimension is None or s["dimension"] == dimension]
        if near is not None and within is not None:
            out = [s for s in out if math.dist(s["pos"], near) <= within]
        return out

    def remove_station(self, pos):
        self.data["stations"] = [s for s in self.data["stations"] if s["pos"] != list(pos)]
        self.save()

    # -- sections looked over and what they held: explore's frontier
    def see_sections(self, dimension, pos, radius, found, looked=()):
        """Sections within `radius` of `pos` were looked over now for `looked`; each `found` kind marks its own section."""

        smap = self.data.setdefault("sections", {}).setdefault(dimension, {})
        now = self.tick()
        asked = {bare(k) for k in looked} | {bare(k) for k in found}
        for c in sections_within(pos, radius):
            row = smap.setdefault(",".join(map(str, c)), {"t": now, "kinds": {}, "looked": {}})
            row["t"] = now
            row.setdefault("looked", {}).update({k: now for k in asked})
        for kind, spots in found.items():
            for p in spots:
                key = ",".join(map(str, section_of(p)))
                smap.setdefault(key, {"t": now, "kinds": {}, "looked": {}})["kinds"][bare(kind)] = now
        if len(smap) > SECTION_CAP:
            here = section_of(pos)
            by = sorted(smap, key=lambda k: ((smap[k]["t"] or 0), -math.dist(here, tuple(map(int, k.split(","))))))
            for k in by[:len(smap) - SECTION_CAP]:
                del smap[k]

    def frontier(self, dimension, here, kinds, band=lambda kind: None):
        """[(section, centre)] to look next for `kinds` from `here`, nearest first."""

        skips = {tuple(map(int, k.split(","))): t
                 for k, t in self.data.get("out_of_look", {}).get(dimension, {}).items()}
        return [(s, section_centre(s))
                for s in frontier(self.section_map(dimension), here, kinds, self.tick(), band, skips=skips)]

    def skip_section(self, dimension, section):
        """`section` is out of look range from any reachable stand (a band far under the ground we stand on): the
        search skips it until its absence TTL runs out. Not a look: nothing is recorded as seen or looked over."""
        self.data.setdefault("out_of_look", {}).setdefault(dimension, {})[",".join(map(str, section))] = self.tick()

    def section_map(self, dimension):
        """{(cx, cy, cz): {"t", "kinds"}} of this dimension (`frontier` reads it)."""
        return {tuple(map(int, k.split(","))): v for k, v in self.data.get("sections", {}).get(dimension, {}).items()}

    # -- what was seen where: one store by volatility, on the game clock
    CONFIRM_R = 12.0      # a note and a sighting within this are the same thing

    def _put(self, kind, pos, dimension, verify=False, cls=None):
        kind = bare(kind)
        cls = cls or seen_class(kind)
        rule = VOLATILITY.get(cls)
        if rule is None:
            return None                                   # hostile: perception only
        pos = [int(c) for c in pos]
        if rule["area"]:
            a = rule["area"]
            pos = [pos[0] // a * a + a // 2, pos[1], pos[2] // a * a + a // 2]
        for row in self.data["seen"]:
            if row["kind"] == kind and row["dimension"] == dimension \
                    and math.dist(row["pos"], pos) <= max(rule["merge"], 0.5):
                row.update(t=self.tick(), verify=verify)
                return row
        row = {"kind": kind, "pos": pos, "dimension": dimension, "t": self.tick(), "verify": verify}
        if cls != seen_class(kind):
            row["cls"] = cls
        self.data["seen"].append(row)
        return row

    def _fresh(self, row, within=None):
        """Within its class's TTL (and `within` ticks, when asked). A note or a clock we cannot date is kept."""
        rule = VOLATILITY.get(row.get("cls") or seen_class(row["kind"]))
        if rule is None:
            return False
        limit = min(x for x in (rule["ttl"], within, float("inf")) if x is not None)
        now = self.tick() if limit != float("inf") and row.get("t") is not None else None
        if now is None:
            return True
        return now - row["t"] <= limit

    def note_seen(self, kind, pos, dimension):
        """One of `kind` is at `pos` (a block or mob name, or an alias: "tree", "herd")."""

        self.data["seen"] = [r for r in self.data["seen"] if self._fresh(r)]
        if self._put(kind, pos, dimension) is not None:
            self.save()

    def note_here(self, kind, pos, dimension):
        """Standing at one of `kind`: noted as its class keeps it, else for two minutes ("here") — never a map of common blocks."""

        self.data["seen"] = [r for r in self.data["seen"] if self._fresh(r)]
        cls = seen_class(kind)
        if self._put(kind, pos, dimension, cls=cls if VOLATILITY.get(cls) else "here") is not None:
            self.save()

    def seen(self, kind, dimension, within=None):
        """Live notes of this kind here, newest first: {kind, pos, dimension, t, verify}."""

        kind = bare(kind)
        rows = [r for r in self.data["seen"] if r["kind"] == kind and r["dimension"] == dimension
                and self._fresh(r, within)]
        return sorted(rows, key=lambda r: r.get("t") or 0, reverse=True)

    def forget_seen(self, kind, pos, dimension, radius=CONFIRM_R):
        """The world said no (we took it, mined it, or it was not there): retire the notes of `kind` near `pos`."""
        kind, before = bare(kind), len(self.data["seen"])
        self.data["seen"] = [r for r in self.data["seen"]
                             if not (r["kind"] == kind and r["dimension"] == dimension
                                     and math.dist(r["pos"], pos) <= radius)]
        if len(self.data["seen"]) != before:
            self.save()

    @staticmethod
    def blocks_of(kind):
        """The blocks that prove a note on arrival."""
        return GROUPS["log"] if bare(kind) == "tree" else [bare(kind)]

    def confirm(self, kind, pos, dimension, found):
        """Arriving settles a note: `found` keeps it (and clears to-verify), otherwise it is retired at once."""

        if found:
            if kind != "site":
                self.note_here(kind, pos, dimension)
            return True
        if kind == "site":
            before = len(self.data.get("sites", []))
            self.data["sites"] = [x for x in self.data.get("sites", [])
                                  if not (x.get("dimension") == dimension
                                          and math.dist(x["pos"], pos) <= self.CONFIRM_R)]
            if len(self.data["sites"]) != before:
                self.save()
        else:
            self.forget_seen(kind, pos, dimension)
        return False

    # -- what containers held when last open: decompose's "take it from a chest"
    def note_container(self, pos, dimension, slots):
        """Record what a container held when it was last open (`slots`: /container rows; the player's own skipped)."""
        items = {}
        for s in slots:
            if s.get("owner") != "player" and s.get("id") not in (None, "minecraft:air"):
                items[s["id"]] = items.get(s["id"], 0) + int(s.get("count", 1))
        key = ",".join(str(int(c)) for c in pos)
        self.data.setdefault("containers", {})[key] = {"pos": [int(c) for c in pos], "dimension": dimension,
                                                      "items": items, "t": self.tick()}
        self.save()

    def forget_container(self, pos):
        if self.data.setdefault("containers", {}).pop(",".join(str(int(c)) for c in pos), None) is not None:
            self.save()

    def stored(self, token, dimension):
        """[(pos, item id, count)] of `token` (an item or a group) in containers seen here."""
        ids = {mid(x) for x in GROUPS.get(token, [token])}
        return [(tuple(c["pos"]), item, n) for c in self.data.get("containers", {}).values()
                if c["dimension"] == dimension for item, n in c["items"].items() if item in ids and n > 0]

    def log_death(self, pos, dimension, carried=()):
        """Record a death and what was carried."""

        self.data["deaths"].append({"pos": list(pos), "dimension": dimension, "at": _now(), "t": time.time(),
                                    "carried": [[str(i), int(n)] for i, n in carried]})
        self.save()

    def forget_death(self, pos=None):
        """Mark the last death recovered — or this one, by position."""

        for d in reversed(self.data["deaths"]):
            if d.get("recovered"):
                continue
            if pos is None or tuple(d["pos"]) == tuple(int(c) for c in pos):
                d["recovered"] = True
                self.save()
                # a death is paid for when the walk back is done: measured from the death, what `time.death_cost_s` claims to know
                if d.get("t"):
                    took = time.time() - float(d["t"])
                    if 1.0 <= took <= 3600.0:
                        beliefs.note("time.death_cost_s", took, where="death recovered")
                return d
        return None

    def recent_death(self, dimension, within_s=300, now=None):  # noqa: D401
        """The last death if its dropped items are still there (they despawn after 5 minutes), else None."""
        now = now or time.time()
        d = next((d for d in reversed(self.data["deaths"]) if d.get("t") and not d.get("recovered")), None)
        if d and d["dimension"] == dimension and now - d["t"] < within_s:
            d.setdefault("carried", [])      # deaths recorded before the bag was kept
            return d
        return None

    # -- nights: counted once, on the night→day transition
    def observe_phase(self, night):
        n = self.data["night"]
        phase = "night" if night else "day"
        if phase == n["phase"]:
            return
        if phase == "day" and not n["slept"]:
            n["missed"] += 1
        if phase == "night":
            n["slept"] = False
        n["phase"] = phase
        self.save()

    def slept(self):
        self.data["night"]["slept"] = True
        self.data["night"]["missed"] = 0
        self.save()

