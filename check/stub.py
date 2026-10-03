"""The world a γ state stands for, answering the jar's GET endpoints from data (state, bag, blocks, entities), so the
PRODUCTION round runs offline. A POST during a decision is recorded (a decision that acts), never executed."""
import functools
import heapq
import math
import re
from urllib.parse import parse_qs, urlparse

from bonobo.data import bare, mid

VERSION = "0.1.63"       # the jar's mod_version (anaka gradle.properties): every feature the round asks about


@functools.cache
def _bare(name):
    return bare(name.split("[")[0])


class StubWorld:
    def __init__(self, state, slots, blocks, entities=(), walks=None, equipment=None, base=None):
        """`base`: a StubWorld at the same feet whose blocks lie under `blocks`; its index is shared, not rebuilt."""
        self.state, self.slots = dict(state), list(slots)
        self.base, self.near = base, {}     # near: name → its cells by distance from the feet (fixed in a round)
        self.blocks = dict(base.blocks) if base is not None else {}
        self.added, self.gone = {}, {}      # name → cells `blocks` put on top of the base / took from it
        for c, n in blocks.items():
            old = self.blocks.get(c)
            if old is not None:
                self.gone.setdefault(_bare(old), set()).add(c)
            self.blocks[c] = n
            self.added.setdefault(_bare(n), []).append(c)
        if base is not None:
            self.by_name = dict(base.by_name)
            for name in self.added.keys() | self.gone.keys():
                cut = self.gone.get(name, ())
                self.by_name[name] = [c for c in base.by_name.get(name, ()) if c not in cut] + self.added.get(name, [])
        else:
            self.by_name = self.added
        self.entities, self.walks = list(entities), walks or {}
        self.equipment = equipment or {}
        self.posts = []

    # -- the transport (api.api's signature)
    def api(self, method, path, body=None, timeout=None):
        if method != "GET":
            self.posts.append((path.split("?")[0], body))
            return {"status": "succeeded", "tasks": [], "id": 0}
        url = urlparse(path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        return getattr(self, "_" + url.path.strip("/").replace("/", "_"), self._empty)(q)

    def _empty(self, q):
        return {"blocks": [], "entities": [], "slots": [], "tasks": [], "palette": ["minecraft:air"], "spots": [],
                "found": 0}

    def _status(self, q):
        return {"version": VERSION}

    def _biomes(self, q):
        """The state's biome facts as the jar's /biomes answers them: none known unless the state names some."""
        return {"chunks": [{"cx": cx, "cz": cz, "biome": b} for cx, cz, b in self.state.get("biomes", [])]}

    def _state(self, q):
        return dict(self.state)

    def _inventory(self, q):
        return {"slots": [dict(s) for s in self.slots], "equipment": dict(self.equipment)}

    def _cells(self, a, b):
        lo, hi = [tuple(min(x, y) for x, y in zip(a, b)), tuple(max(x, y) for x, y in zip(a, b))]
        get = self.blocks.get
        return [((x, y, z), n) for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
                for z in range(lo[2], hi[2] + 1) if (n := get((x, y, z))) is not None]

    def _blocks(self, q):
        a = tuple(int(v) for v in q["from"].split(","))
        b = tuple(int(v) for v in q["to"].split(","))
        palette, out = [], []
        for c, n in self._cells(a, b):
            name = mid(n)
            if name not in palette:
                palette.append(name)
            out.append([c[0], c[1], c[2], palette.index(name)])
        return {"palette": palette or ["minecraft:air"], "blocks": out}

    def feet(self):
        return self.state["blockX"], self.state["blockY"], self.state["blockZ"]

    def exposed(self, c):
        return any(self.blocks.get((c[0] + d[0], c[1] + d[1], c[2] + d[2]), "air") == "air"
                   for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)))

    def _find(self, q):
        want = {bare(b) for b in q.get("blocks", "").split(",") if b}
        radius, limit = float(q.get("radius", 32)), int(q.get("limit", 50))
        each = min(limit, int(q.get("perBlock", limit)))      # the jar's perBlock: at most this many of each block
        hits = []
        for name in want:
            mine = 0
            for dist, c in self._near(name):
                if dist > radius or mine == each:       # by distance: the rest are farther, or past the limit
                    break
                if q.get("exposed") == "true" and not self.exposed(c):
                    continue
                hits.append({"x": c[0], "y": c[1], "z": c[2], "block": mid(name), "distance": dist})
                mine += 1
        return {"blocks": sorted(hits, key=lambda h: h["distance"])[:limit]}

    def _near(self, name):
        """`name`'s cells by distance from the feet (the base's sorted list, merged with what this world changed)."""
        if name not in self.near:
            here = self.feet()
            if self.base is not None and name not in self.added and name not in self.gone:
                self.near[name] = self.base._near(name)
            elif self.base is not None:
                cut = self.gone.get(name, ())
                self.near[name] = list(heapq.merge([h for h in self.base._near(name) if h[1] not in cut],
                                                   sorted((math.dist(here, c), c) for c in self.added.get(name, ()))))
            else:
                self.near[name] = sorted((math.dist(here, c), c) for c in self.by_name.get(name, ()))
        return self.near[name]

    def _entities(self, q):
        radius = float(q.get("radius", 16))
        here = (self.state["x"], self.state["y"], self.state["z"])
        out = []
        for e in self.entities:
            d = math.dist(here, (e["x"], e["y"], e["z"]))
            if d <= radius:
                out.append(dict(e, distance=d))
        return {"entities": out}

    def _dark(self, q):
        spots = [{"x": c[0], "y": c[1] + 1, "z": c[2], "blockLight": 0, "skyLight": 0}
                 for c, n in self.blocks.items() if self.blocks.get((c[0], c[1] + 1, c[2]), "air") == "air"
                 and math.dist(self.feet(), c) <= float(q.get("radius", 8))]
        return {"found": len(spots), "spots": spots[:int(q.get("limit", 30))]}

    def _plan(self, q):
        to = tuple(int(v) for v in re.findall(r"-?\d+", q.get("to", "")))
        found = self.walks.get(to, self.blocks.get(to, "air") == "air")     # an open cell on the floor: walked to
        return {"found": found, "seconds": math.dist(self.feet(), to) / 4.3 if found else None, "steps": []}

    def _container(self, q):
        return {"slots": []}
