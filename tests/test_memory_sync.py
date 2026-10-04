"""Regression tests for memory consistency and world synchronization:
- Cost.site prefers close live vision over far memory sightings
- gather.mine retires broken cells in memory even when interrupted
- see_sections does not blind subterranean sections from surface looks
- recent_death respects game ticks under wall-clock pauses, and jobs.start records ready_tick
- pending_outputs filters out stale/abandoned phantom furnace jobs
- collect_job does not prematurely drop distant furnace jobs on unloaded chunks
- remove_station purges stations from both memory.stations and homes.json
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, craft, decompose, explore, gather, jobs, memory, nav  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.data import ITEM_DESPAWN_S  # noqa: E402
from tests.world import FakeRegion, inventory, snapshot, state  # noqa: E402

DIM = "minecraft:overworld"


class MemorySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.notes_path = os.path.join(self.tmp.name, "notes.json")
        self.mem = memory.Memory(self.notes_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cost_site_prefers_closer_live_vision_over_far_memory(self):
        """Cost.site must choose a close live seen block over an older, farther remembered one."""
        self.mem.note_seen("iron_ore", (50, 64, 0), DIM)
        snap = snapshot(
            state(dimension=DIM, blockX=0, blockY=64, blockZ=0),
            inventory(),
            iron_ore=2.0
        )
        cost = Cost(snap, self.mem)
        step = type("Step", (), {"kind": "mine", "token": "minecraft:raw_iron", "detail": {"blocks": ["iron_ore"]}})()
        chosen = cost.site(step)
        self.assertEqual(chosen, (2, 64, 0), "Cost.site chose far memory over close live vision")

    def test_see_sections_does_not_blind_subterranean_frontier(self):
        """Surface looks must not stamp subterranean sections as looked, keeping them on the frontier."""
        self.mem.clock = 1000
        surface_pos = (8, 70, 8)  # section (0, 4, 0)
        self.mem.see_sections(DIM, surface_pos, 32, {}, ["iron_ore"])

        smap = self.mem.section_map(DIM)
        sub_sec = (0, 3, 0)  # y=48..63, underground
        # Subterranean section must NOT be marked as looked for iron_ore from a surface look
        if sub_sec in smap:
            self.assertNotIn("iron_ore", smap[sub_sec].get("looked", {}))

        # Frontier searching for underground ore (band Y=48 -> cy=3) must still include (0, 3, 0)
        todo = [s for s, _c in self.mem.frontier(DIM, surface_pos, ["iron_ore"], band=lambda k: 48)]
        self.assertIn(sub_sec, todo, "subterranean section blinded on frontier by surface look")

    def test_recent_death_respects_game_ticks_under_wall_clock_pause(self):
        """When paused or lagging, deaths must not expire if game ticks haven't elapsed."""
        t0 = 1000.0
        self.mem.clock = 2000
        with mock.patch.object(memory.time, "time", return_value=t0):
            self.mem.log_death((10, 64, 10), DIM, carried=[("iron_pickaxe", 1)])

        # Suppose 600 seconds of real wall clock passed (game paused in singleplayer menu),
        # but only 1000 game ticks passed (< 6000 item despawn ticks).
        death = self.mem.recent_death(DIM, within_s=ITEM_DESPAWN_S, now=t0 + 600, tick=3000)
        self.assertIsNotNone(death, "death erroneously expired by wall clock while game ticks were paused")
        self.assertEqual(tuple(death["pos"]), (10, 64, 10))

        # Once 6000 game ticks actually pass in-game, it expires:
        expired = self.mem.recent_death(DIM, within_s=ITEM_DESPAWN_S, now=t0 + 600, tick=2000 + 6001)
        self.assertIsNone(expired, "death failed to expire after 6000 game ticks")

    def test_jobs_start_sets_ready_tick(self):
        """jobs.start must record ready_tick alongside ready_at when memory has a tick."""
        self.mem.clock = 5000
        job = jobs.start(self.mem, "crop", (5, 64, 5), DIM, item="wheat", count=3, seconds=60)
        self.assertIn("ready_tick", job)
        self.assertEqual(job["ready_tick"], 5000 + 60 * 20)

    def test_pending_outputs_filters_stale_phantom_jobs(self):
        """pending_outputs must not report items from jobs that expired long ago without collection."""
        t0 = 1000.0
        with mock.patch.object(memory.time, "time", return_value=t0):
            self.mem.add_job("furnace", (2, 64, 0), DIM, "minecraft:iron_ingot", 3, t0 + 30, False)

        # Right after ready (e.g. t0 + 40), it is still active/pending
        with mock.patch.object(memory.time, "time", return_value=t0 + 40):
            pending = self.mem.pending_outputs(DIM)
            self.assertEqual(pending.get("minecraft:iron_ingot"), 3)

        # 15 minutes later (t0 + 1000, > ready_at + 600), it must be ignored as a stale phantom job
        with mock.patch.object(memory.time, "time", return_value=t0 + 1000):
            stale_pending = self.mem.pending_outputs(DIM)
            self.assertEqual(stale_pending.get("minecraft:iron_ingot", 0), 0)

    def test_remove_station_removes_from_homes_and_stations(self):
        """remove_station must purge stations from both memory stations and homes.json parts."""
        station_pos = (7, 64, 2)
        home_blocks = {(1, 64, 1): "minecraft:red_bed", station_pos: "minecraft:crafting_table",
                       (0, 64, 0): "minecraft:stone_bricks"}
        self.mem.add_home("base", [((0, 64, 0), (10, 70, 10))], DIM, home_blocks)

        # Station is initially known
        self.assertIn(station_pos, self.mem.known_stations("crafting_table", DIM))

        # Remove the station
        self.mem.remove_station(station_pos)

        # Station must be gone from known_stations and homes.json
        self.assertNotIn(station_pos, self.mem.known_stations("crafting_table", DIM))
        home_parts = self.mem.home_sites()[0]["parts"]
        self.assertEqual(home_parts["stations"], [])

    def test_collect_job_does_not_drop_far_job_on_unloaded_chunk(self):
        """collect_job must not drop a job whose furnace is far away before attempting navigation."""
        job = {"id": "furnace-1", "pos": [100, 64, 100], "dimension": DIM, "item": "minecraft:iron_ingot", "count": 3}
        self.mem.data["jobs"].append(job)
        self.mem.save()

        ctx = type("Ctx", (), {"mem": self.mem, "policy": None, "dimension": DIM})()

        # Player is at (0, 64, 0), furnace is at (100, 64, 100) -> distance ~141 blocks
        with mock.patch("bonobo.craft.feet", return_value=(0, 64, 0)), \
             mock.patch("bonobo.craft.Region") as mock_region, \
             mock.patch("bonobo.craft.nav.arrived_near", return_value=False) as mock_nav:
            # Region returns "air" because chunk is unloaded
            mock_region.return_value.name.return_value = "air"
            with self.assertRaises(api.NavFailed):
                list(craft.collect_job(ctx, job))

        # Job must NOT have been finished / dropped from memory because of unloaded chunk
        self.assertIn("furnace-1", [j["id"] for j in self.mem.jobs(DIM)],
                      "collect_job prematurely deleted furnace job on unloaded chunk")

    def test_gather_mine_retires_spent_cells_on_interruption(self):
        """gather.mine must retire broken cells from memory even if interrupted before target met."""
        cell = (2, 64, 0)
        self.mem.note_seen("iron_ore", cell, DIM)
        ctx = type("Ctx", (), {
            "mem": self.mem,
            "policy": nav.Policy(allow_dig=True, protected=set()),
            "dimension": DIM,
            "blocked": lambda *a: False,
            "ban": lambda *a: None
        })()

        self.mem.clock = 1000
        # Simulate mining that breaks the cell, notes it in sent, but then gets interrupted
        with mock.patch("bonobo.gather.Inventory") as mock_inv, \
             mock.patch("bonobo.gather.require_pickaxe"), \
             mock.patch("bonobo.gather.find", return_value=[{"x": 2, "y": 64, "z": 0, "block": "iron_ore"}]), \
             mock.patch("bonobo.gather.feet", return_value=(0, 64, 0)), \
             mock.patch("bonobo.gather.swimming", return_value=False), \
             mock.patch("bonobo.gather.region_around") as mock_region_around, \
             mock.patch("bonobo.gather.connected", return_value=[cell]), \
             mock.patch("bonobo.gather.mineable", return_value=[cell]), \
             mock.patch("bonobo.gather.approach_cell", return_value=(1, 64, 0)), \
             mock.patch("bonobo.gather.nav.mod_features", return_value={"travel"}), \
             mock.patch("bonobo.gather.nav.arrived_near", return_value=True), \
             mock.patch("bonobo.gather.seal_or_wet", return_value=([], set(), None)), \
             mock.patch("bonobo.gather.opener_pairs", return_value=[]), \
             mock.patch("bonobo.gather.nav.run_cells", side_effect=api.NavFailed("interrupted")):

            # Inventory starts with 0 and gains 0 (target is 3)
            mock_inv.return_value.count.return_value = 0
            mock_inv.return_value.used_slots.return_value = 5

            # Region around cell reports it is now air (broken)
            mock_reg = mock.MagicMock()
            mock_reg.name.side_effect = lambda c: "air" if c == cell else "stone"
            mock_reg.solid.return_value = False
            mock_region_around.return_value = mock_reg

            with self.assertRaises(api.NavFailed):
                gather.mine(ctx, "minecraft:raw_iron", 3, ["iron_ore"], None)

        # Even though target was not met and exception was raised, broken cell must be retired
        self.assertEqual(self.mem.seen("iron_ore", DIM), [],
                         "broken cell was not retired from memory upon interruption")


if __name__ == "__main__":
    unittest.main()
