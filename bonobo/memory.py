"""Persistent world memory shared across sessions: sites, stations, sightings, veins, deaths, night record.

A *site* is any protected structure: the home base or a built shelter. Each may carry a snapshot of its solid
blocks so damage can be detected and repaired. Digging near sites is allowed; digging *their blocks* is not.
"""
import json
import math
import os
import time
from . import paths

NOTES_FILE = paths.data("world-notes.json", env="MC_NOTES")


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


class Memory:
    def __init__(self, path=NOTES_FILE):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        d = self.data
        for key, default in (("sites", []), ("stations", []), ("sightings", {}), ("veins", []), ("deaths", []),
                             ("night", {"phase": "day", "slept": False, "missed": 0}), ("machines", []),
                             ("orientation", {}), ("stats", {}), ("durations", {}), ("jobs", [])):
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
        for key, ident in (("stations", lambda s: (tuple(s["pos"]), s["dimension"])),
                           ("veins", lambda v: (v["ore"], tuple(v["pos"]), v["dimension"]))):
            seen, unique = set(), []
            for item in d[key]:
                if ident(item) not in seen:
                    seen.add(ident(item))
                    unique.append(item)
            if len(unique) != len(d[key]):
                d[key] = unique
                changed = True
        if changed:
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    # ---- sites
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
        """Cells of blueprint builds that were started but not finished: never mined (the portal goal once took its
        own half-built frame apart for obsidian)."""
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
        """Subtract collected items from a machine's pending outputs; stale or empty entries go away, entries that
        yielded nothing yet are rescheduled a minute later."""
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

    def add_job(self, kind, pos, dimension, item, count, ready_at, carried):
        job = {"id": f"{kind}-{int(time.time())}", "kind": kind, "pos": list(pos), "dimension": dimension,
               "item": item, "count": count, "ready_at": ready_at, "carried": carried}
        self.data["jobs"].append(job)
        self.save()
        return job

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
        # Exponential forgetting: new tools or a new area can redeem a step that used to fail.
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

    def orientation_rule(self, item):
        """How an item's `facing` follows the body at placement: learned per item, default toward the player."""
        return self.data["orientation"].get(item, "toward_player")

    def set_orientation_rule(self, item, rule):
        self.data["orientation"][item] = rule
        self.save()

    def mark_dirty_near(self, positions, dimension, radius=6):
        changed = False
        for s in self.sites(dimension):
            if s.get("snapshot") and not s.get("dirty") and any(
                    math.dist((p[0], p[2]), (s["pos"][0], s["pos"][2])) <= radius for p in positions):
                s["dirty"] = True
                changed = True
        if changed:
            self.save()

    # ---- stations we placed (persist across restarts so they get picked back up)
    def add_station(self, block, pos, dimension):
        if any(s["pos"] == list(pos) and s["dimension"] == dimension for s in self.data["stations"]):
            return
        self.data["stations"].append({"block": block, "pos": list(pos), "dimension": dimension})
        self.save()

    def remove_station(self, pos):
        self.data["stations"] = [s for s in self.data["stations"] if s["pos"] != list(pos)]
        self.save()

    # ---- sightings, veins, deaths
    def add_sighting(self, kind, pos, dimension):
        self.data["sightings"].setdefault(kind, []).append({"pos": list(pos), "dimension": dimension, "at": _now()})
        self.save()

    def sightings(self, kind, dimension, max_age_min=30):
        """Recent sightings only: animals wander off, and old ones sent exploration to empty fields."""
        cutoff = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - max_age_min * 60))
        return [s for s in self.data["sightings"].get(kind, [])
                if s["dimension"] == dimension and s.get("at", "") >= cutoff]

    def log_vein(self, ore, pos, size, dimension):
        if any(v["ore"] == ore and v["dimension"] == dimension and math.dist(v["pos"], pos) <= 3
               for v in self.data["veins"]):
            return
        self.data["veins"].append({"ore": ore, "pos": list(pos), "size": size, "dimension": dimension, "at": _now()})
        self.save()

    # ---- resource points: trees, herds, water — a map that changes (harvested, depleted, regrown)
    REGROW_S = {"tree": 20 * 60, "herd": 10 * 60, "water": 0, "grass": 5 * 60}

    def note_resource(self, kind, pos, dimension, depleted=False):
        """Record (or refresh) a resource point; points within 12 blocks of one another are the same point."""
        res = self.data.setdefault("resources", [])
        now = time.time()
        for r in res:
            if r["kind"] == kind and r["dimension"] == dimension and math.dist(r["pos"], pos) <= 12:
                r["last"] = now
                r["depleted"] = depleted
                break
        else:
            res.append({"kind": kind, "pos": list(pos), "dimension": dimension, "last": now, "depleted": depleted})
        self.save()

    def resources(self, kind, dimension, now=None):
        """Available points of a kind: never depleted, or depleted long enough ago to have regrown."""
        now = now or time.time()
        regrow = self.REGROW_S.get(kind, 600)
        return [r["pos"] for r in self.data.get("resources", [])
                if r["kind"] == kind and r["dimension"] == dimension
                and (not r.get("depleted") or now - r.get("last", 0) >= regrow)]

    # ---- lava pools (obsidian casting): remembered so the portal goal can go back to one
    def add_lava(self, hit, dimension):
        pos = [hit["x"], hit["y"], hit["z"]] if isinstance(hit, dict) else list(hit)
        pools = self.data.setdefault("lava", [])
        if any(p["dimension"] == dimension and math.dist(p["pos"], pos) <= 16 for p in pools):
            return
        pools.append({"pos": pos, "dimension": dimension, "at": _now()})
        self.save()

    def lava_pools(self, dimension):
        return [p["pos"] for p in self.data.get("lava", []) if p["dimension"] == dimension]

    def forget_lava(self, pos):
        self.data["lava"] = [p for p in self.data.get("lava", []) if math.dist(p["pos"], pos) > 16]
        self.save()

    def log_death(self, pos, dimension):
        self.data["deaths"].append({"pos": list(pos), "dimension": dimension, "at": _now(), "t": time.time()})
        self.save()

    def recent_death(self, dimension, within_s=300, now=None):
        """The last death if its dropped items are still there (they despawn after 5 minutes), else None."""
        now = now or time.time()
        d = next((d for d in reversed(self.data["deaths"]) if d.get("t") and not d.get("recovered")), None)
        if d and d["dimension"] == dimension and now - d["t"] < within_s:
            return d
        return None

    # ---- nights: counted once per night, on the night→day transition
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

    @property
    def nights_missed(self):
        return self.data["night"]["missed"]
