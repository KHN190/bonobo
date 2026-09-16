"""Pathfinding bench without walking (mostly): ask the mod for plans (/plan, mod ≥0.1.27 gives estimated seconds) to
targets around the player, then walk a few of them to calibrate the estimate against real time.

    plan_bench.py plans [N]        — N plans in all directions (100–300 blocks, loaded chunks only), no movement
    plan_bench.py walk [N]         — walk N of them, print estimate vs real seconds and seconds per 100 blocks

Results are appended to plan-bench.jsonl in the data directory so cost constants can be tuned from real data
(BuildPathfinder WALK / STEP_UP / BRIDGE / PILLAR / break times). Test world only for `walk`: it moves the player.
"""
import json
import math
import os
import random
import sys
import time

from .. import api, nav, paths, scenarios
from ..world import Snapshot

OUT = paths.data("plan-bench.jsonl")


def targets(here, n, lo=100, hi=300, seed=1):
    """Pure: n horizontal targets at distances lo..hi in spread-out directions, same y as here (range covers terrain)."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        ang = 2 * math.pi * (i + rnd.random() * 0.5) / n
        d = rnd.uniform(lo, hi)
        out.append((round(here[0] + math.cos(ang) * d), here[1], round(here[2] + math.sin(ang) * d)))
    return out


def plan(t, range_=8):
    t0 = time.time()
    r = api.get(f"/plan?to={t[0]},{t[1]},{t[2]}&range={range_}&nodes=120000")
    kinds = {}
    for st in r["steps"]:
        for a in st["actions"]:
            kinds[a.split()[0]] = kinds.get(a.split()[0], 0) + 1
    return {"target": t, "found": r["found"], "expanded": r["expanded"], "est_s": r.get("seconds"),
            "plan_ms": round((time.time() - t0) * 1000), "steps": len(r["steps"]), "actions": kinds}


def log(row):
    with open(OUT, "a") as f:
        f.write(json.dumps({"t": int(time.time()), **row}) + "\n")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plans"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    s = api.get("/state")
    here = (s["blockX"], s["blockY"], s["blockZ"])
    rows = []
    for t in targets(here, n):
        row = {"mode": mode, "from": here, "dist": round(math.dist(here, t)), **plan(t)}
        if mode == "walk":
            if not os.path.exists(scenarios.FLAG):
                raise SystemExit("walk mode moves the player: test world only (mc.py scenario enable)")
            snap = Snapshot()
            from bonobo.brain import Brain
            policy = Brain().policy(snap, snap.night)
            t0 = time.time()
            ok = nav.go_to(t, policy, range_=8, attempts=1)
            end = api.get("/state")
            row.update(walked=ok, real_s=round(time.time() - t0, 1),
                       left=round(math.dist((end["x"], end["y"], end["z"]), t), 1))
            row["s_per_100"] = round(row["real_s"] / max(row["dist"], 1) * 100, 1)
            here = (end["blockX"], end["blockY"], end["blockZ"])
        rows.append(row)
        log(row)
        print(json.dumps(row))
    found = [r for r in rows if r["found"]]
    print(f"\n{len(found)}/{len(rows)} planned; mean plan {sum(r['plan_ms'] for r in rows) / len(rows):.0f} ms")
    walked = [r for r in rows if r.get("walked")]
    if walked:
        ratio = sum(r["real_s"] for r in walked) / max(sum(r["est_s"] or 0 for r in walked), 0.1)
        print(f"walked {len(walked)}: real/estimate {ratio:.2f}, "
              f"{sum(r['s_per_100'] for r in walked) / len(walked):.1f} s per 100 blocks")


if __name__ == "__main__":
    main()
