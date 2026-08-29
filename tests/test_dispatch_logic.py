"""Offline tests for the dispatch planner (no browser needed).

Run: venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.vehicle_manager import VehicleManager
from utils.dispatch_solver import solve as solve_dispatch
from utils.dispatch_solver import prioritize_requirements_by_scarcity
from utils.vehicle_lock import VehicleLockManager
from utils.api_client import backoff_delay
import utils.dispatcher as disp
from utils.dispatcher import (
    greedy_plan,
    order_ambulance_ids,
    load_vehicle_data,
    get_valid_ids_for_type,
    _mission_needs_signature,
    _same_station,
    within_dispatch_radius,
    trailer_local_towers,
)



def _load_garage():
    """vehicle_data.json -> by_type map (both legacy and new schema)."""
    vd = json.loads((Path(__file__).resolve().parent.parent / "data" / "vehicle_data.json").read_text())
    if "by_type" in vd:
        return vd["by_type"]
    return {k: v for k, v in vd.items() if k not in ("by_type", "crew")}


def _garage_candidates(vm, valid_per_req):
    """All garage (vid, sys_id) pairs usable for planning."""
    out = []
    for sys_id, ids in _load_garage().items():
        for vid in ids:
            out.append((vid, int(sys_id)))
    return out


def _avail_for(vm, valid_per_req):
    avail = []
    for vid, sys_id in _garage_candidates(vm, valid_per_req):
        can = 0
        for req, ids in valid_per_req.items():
            if vid in ids:
                can += 1
        if can > 0:
            avail.append((vid, sys_id, float("inf"), can))
    return avail


class TestDispatchPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VehicleManager(code="us")
        asyncio.run(load_vehicle_data(force=True))

    def _valid_per_req(self, reqs):
        async def _build():
            return {r: set(await get_valid_ids_for_type(r)) for r in reqs}
        return asyncio.run(_build())

    def test_quint_covers_engine_and_ladder(self):
        reqs = {"Firetruck": 1, "Platform Truck": 1}
        valid = self._valid_per_req(reqs)
        avail = _avail_for(self.vm, valid)
        steps, rem = greedy_plan(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        # At least one multi-role step using a Quint (sys 13)
        multi_steps = [s for s in steps if s[2]]
        self.assertTrue(multi_steps, "expected a multi-role step")
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertIn(13, sys_ids, "expected a Quint in the plan")
        # Total vehicles <= total requirements (no over-send)
        self.assertLessEqual(len(steps), sum(reqs.values()))

    def test_mcv_does_not_substitute_bcu(self):
        # User case: 4 BCU + 1 MCV -> exactly 4 BCU (sys 3) + 1 MCV (sys 12)
        reqs = {"battalion chief unit": 4, "mobile command vehicle": 1}
        valid = self._valid_per_req(reqs)
        avail = _avail_for(self.vm, valid)
        steps, rem = greedy_plan(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertEqual(sys_ids.count(12), 1, f"MCV count should be 1, got {sys_ids}")
        self.assertEqual(sys_ids.count(3), 4, f"BCU count should be 4, got {sys_ids}")

    def test_pumper_tanker_covers_firetruck_and_water(self):
        reqs = {"Firetruck": 2, "Water Tanker": 1}
        valid = self._valid_per_req(reqs)
        avail = _avail_for(self.vm, valid)
        steps, rem = greedy_plan(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertIn(33, sys_ids, "expected a Pumper-Tanker in the plan")

    def test_user_carpentry_case(self):
        # 9 firetrucks, 1 platform, 4 BCU, 1 MCV, 1 water, 2 patrol
        reqs = {
            "firetruck": 9,
            "platform truck": 1,
            "battalion chief unit": 4,
            "mobile command vehicle": 1,
            "water tanker": 1,
            "patrol car": 2,
        }
        valid = self._valid_per_req(reqs)
        avail = _avail_for(self.vm, valid)
        steps, rem = greedy_plan(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertEqual(sys_ids.count(12), 1, "exactly one MCV expected")
        self.assertEqual(sys_ids.count(3), 4, "exactly four BCU expected")
        self.assertLessEqual(len(steps), sum(reqs.values()), "no over-send allowed")

    def test_ambulance_order_prefers_pure(self):
        async def _build():
            from utils.dispatcher import get_valid_ids_for_type
            ids = await get_valid_ids_for_type("ambulance")
            return order_ambulance_ids(self.vm, ids, disp.USER_TO_SYSTEM_MAP)
        ordered = asyncio.run(_build())
        PURE = {5, 11, 20}
        COMBI = {48, 49, 50}
        # First items must be pure (5/11/20); combined vehicles last
        if ordered:
            first_sys = disp.USER_TO_SYSTEM_MAP.get(str(ordered[0]))
            self.assertIn(first_sys, PURE, "first ambulance should be a pure one")
        pure_section = [v for v in ordered if disp.USER_TO_SYSTEM_MAP.get(str(v)) in PURE]
        combi_section = [v for v in ordered if disp.USER_TO_SYSTEM_MAP.get(str(v)) in COMBI]
        self.assertEqual(ordered, pure_section + combi_section)

    def test_trailer_needs_tower(self):
        self.assertTrue(self.vm.is_trailer(77))
        self.assertEqual(self.vm.get_towing_vehicles(77), [41])
        self.assertTrue(self.vm.is_trailer(78))
        self.assertEqual(set(self.vm.get_towing_vehicles(78)), {8, 1, 10, 4, 18})


# Water capacity per system id for solver tests (from equipment_capacity.json)
_WATER = {33: 2500, 6: 10000, 13: 500, 4: 750, 18: 750}


class TestUnifiedSolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VehicleManager(code="us")
        asyncio.run(load_vehicle_data(force=True))

    def _valid_per_req(self, reqs):
        async def _build():
            return {r: set(await get_valid_ids_for_type(r)) for r in reqs}
        return asyncio.run(_build())

    def _avail(self, valid_per_req, water_map=None, crew_map=None):
        water_map = water_map or {}
        crew_map = crew_map or {}
        avail = []
        for sys_id, ids in _load_garage().items():
            for vid in ids:
                if any(vid in ids_set for ids_set in valid_per_req.values()):
                    avail.append((vid, int(sys_id), float("inf"), 0,
                                  water_map.get(int(sys_id), 0), 0,
                                  crew_map.get(str(vid), 0)))
        return avail

    def test_water_aware_picks_pumper_tankers_over_dry_engines(self):
        reqs = {"Firetruck": 2}
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid, water_map=_WATER)
        steps, rem, totals = solve_dispatch(
            self.vm, dict(reqs), valid, avail, water_needed=4000)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertEqual(sys_ids.count(33), 2, f"expected 2 Pumper-Tankers, got {sys_ids}")
        self.assertEqual(len(steps), 2, "no extra tanker needed — water covered by engines")
        self.assertGreaterEqual(totals["water"], 4000)

    def test_solver_needs_tanker_when_engines_dry(self):
        reqs = {"Firetruck": 1}
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid, water_map={})  # no water anywhere
        # Add a tanker (sys 6) as candidate — valid for no role but carries water
        tanker_vids = _load_garage().get("6", [])[:1]
        for vid in tanker_vids:
            avail.append((vid, 6, float("inf"), 0, 10000, 0, 0))
        steps, rem, totals = solve_dispatch(
            self.vm, dict(reqs), valid, avail, water_needed=8000)
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertIn(6, sys_ids, "tanker needed when engines carry no water")
        self.assertGreaterEqual(totals["water"], 8000)

    def test_solver_parity_quint_covers_engine_and_ladder(self):
        reqs = {"Firetruck": 1, "Platform Truck": 1}
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid)
        steps, rem, _ = solve_dispatch(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertIn(13, sys_ids, "expected a Quint in the plan")
        self.assertLessEqual(len(steps), sum(reqs.values()))

    def test_solver_parity_mcv_not_bcu(self):
        reqs = {"battalion chief unit": 4, "mobile command vehicle": 1}
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid)
        steps, rem, _ = solve_dispatch(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertEqual(sys_ids.count(12), 1)
        self.assertEqual(sys_ids.count(3), 4)

    def test_solver_parity_carpentry_case(self):
        reqs = {
            "firetruck": 9,
            "platform truck": 1,
            "battalion chief unit": 4,
            "mobile command vehicle": 1,
            "water tanker": 1,
            "patrol car": 2,
        }
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid)
        steps, rem, _ = solve_dispatch(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        sys_ids = [disp.USER_TO_SYSTEM_MAP.get(str(s[0])) for s in steps]
        self.assertEqual(sys_ids.count(12), 1)
        self.assertEqual(sys_ids.count(3), 4)
        self.assertLessEqual(len(steps), sum(reqs.values()))

    def test_solver_personnel_constraint(self):
        reqs = {"Firetruck": 1}
        valid = self._valid_per_req(reqs)
        avail = self._avail(valid, crew_map={v: 6 for v in disp.USER_TO_SYSTEM_MAP})
        steps, _, totals = solve_dispatch(
            self.vm, dict(reqs), valid, avail, personnel_needed=6)
        self.assertEqual(len(steps), 1)
        self.assertGreaterEqual(totals["personnel"], 6)

    def test_solver_no_needs_no_steps(self):
        steps, rem, totals = solve_dispatch(self.vm, {}, {}, [])
        self.assertEqual(steps, [])
        self.assertEqual(totals, {"water": 0, "foam": 0, "personnel": 0})


class TestVehicleLocks(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        import utils.vehicle_lock as vl
        self._orig_state_path = vl.STATE_PATH
        vl.STATE_PATH = Path(self.tmpdir.name) / "dispatch_state.json"
        self.m = VehicleLockManager()

    def tearDown(self):
        import utils.vehicle_lock as vl
        vl.STATE_PATH = self._orig_state_path
        self.tmpdir.cleanup()

    def test_inflight_ttl_expiry(self):
        self.m.lock_batch(["9515320", "12742431"], "m1")
        self.assertTrue(self.m.is_locked("9515320"))
        # Simulate TTL elapsed
        self.m._inflight = {"9515320": (time.time() - 999, "m1")}
        self.assertFalse(self.m.is_locked("9515320"))

    def test_sent_persists_and_release(self):
        self.m.mark_sent(["9515320"], "m1")
        self.assertTrue(self.m.is_sent("9515320"))
        # Reload from disk (simulate restart)
        m2 = VehicleLockManager()
        m2.load_state()
        self.assertTrue(m2.is_sent("9515320"))
        self.m.release_mission("m1")
        self.assertFalse(self.m.is_sent("9515320"))
        m3 = VehicleLockManager()
        m3.load_state()
        self.assertFalse(m3.is_sent("9515320"))

    def test_wave_still_in_flight(self):
        sig = '{"needs": 1}'
        self.m.set_wave("m1", sig, ["9515320"])
        self.m.mark_sent(["9515320"], "m1")
        self.assertTrue(self.m.wave_still_in_flight("m1", sig))
        self.assertFalse(self.m.wave_still_in_flight("m1", '{"needs": 2}'))
        self.assertFalse(self.m.wave_still_in_flight("m2", sig))
        self.m.release_mission("m1")
        self.assertFalse(self.m.wave_still_in_flight("m1", sig))

    def test_unlock_on_failure_frees_wave(self):
        self.m.set_wave("m1", "sig", ["9515320"])
        self.m.mark_sent(["9515320"], "m1")
        self.m.unlock_on_failure("m1")
        self.assertFalse(self.m.is_sent("9515320"))
        self.assertIsNone(self.m.get_wave("m1"))

    def test_mission_needs_signature_stable(self):
        a = {"vehicles": [{"name": "Firetruck", "count": 2}, {"name": "Police Car", "count": 1}],
             "water_needed": 4000, "foam_needed": 0, "patients": 1, "crashed_cars": 0}
        b = {"vehicles": [{"name": "Police Car", "count": 1}, {"name": "Firetruck", "count": 2}],
             "water_needed": 4000, "foam_needed": 0, "patients": 1, "crashed_cars": 0}
        self.assertEqual(_mission_needs_signature(a), _mission_needs_signature(b))
        c = dict(b, water_needed=5000)
        self.assertNotEqual(_mission_needs_signature(a), _mission_needs_signature(c))


class TestTrainingGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VehicleManager(code="us")

    def test_no_training_required_allows(self):
        # Patrol car (sys 10) requires no training
        self.assertTrue(disp._crew_qualified(self.vm, 10,
            {"personnel": 2, "educations": []}))

    def test_swat_requires_training(self):
        # SWAT SUV (sys 26) requires SWAT Training (training.json)
        self.assertFalse(disp._crew_qualified(self.vm, 26,
            {"personnel": 2, "educations": ["EMS"]}))
        self.assertTrue(disp._crew_qualified(self.vm, 26,
            {"personnel": 2, "educations": ["SWAT Training"]}))

    def test_fail_open_without_crew_data(self):
        self.assertTrue(disp._crew_qualified(self.vm, 26, None))
        self.assertTrue(disp._crew_qualified(self.vm, 26, {"personnel": 0, "educations": []}))

    def test_tractor_requires_truck_license(self):
        # Crew Cab Semi (sys 41) requires Truck Driver's License (training.json)
        self.assertFalse(disp._crew_qualified(self.vm, 41,
            {"personnel": 2, "educations": ["EMS"]}))
        self.assertTrue(disp._crew_qualified(self.vm, 41,
            {"personnel": 2, "educations": ["Truck Driver's License"]}))


class TestScarcityRarity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VehicleManager(code="us")

    def test_prioritize_requirements_by_scarcity(self):
        remaining = {"Firetruck": 2, "HazMat": 1}
        valid = {"Firetruck": {"a", "b", "c"}, "HazMat": {"z"}}
        avail = [("a", 0, 0, 0, 0, 0, 0), ("b", 0, 0, 0, 0, 0, 0),
                 ("c", 0, 0, 0, 0, 0, 0), ("z", 9, 0, 0, 0, 0, 0)]
        order = prioritize_requirements_by_scarcity(remaining, valid, avail)
        self.assertEqual(order[0][0], "HazMat", "scarcest requirement first")
        self.assertEqual(order[1][0], "Firetruck")

    def test_rarity_targets_scarce_requirement(self):
        # 1 Rescue Engine (18) + 2 plain engines; mission needs Engine + Heavy Rescue.
        # The only Heavy Rescue provider must target the scarce slot,
        # not be burned on the generic Engine slot.
        reqs = {"Firetruck": 1, "Heavy Rescue Vehicle": 1}
        valid = {
            "Firetruck": {"re1", "e1", "e2"},
            "Heavy Rescue Vehicle": {"re1"},
        }
        avail = [
            ("re1", 18, float("inf"), 0, 750, 0, 0),
            ("e1", 1, float("inf"), 0, 0, 0, 0),
            ("e2", 1, float("inf"), 0, 0, 0, 0),
        ]
        steps, rem, _ = solve_dispatch(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        re_step = next(s for s in steps if s[0] == "re1")
        self.assertEqual(re_step[1], "Heavy Rescue Vehicle",
                         "Rescue Engine must target the scarce Heavy Rescue slot")
        self.assertLessEqual(len(steps), sum(reqs.values()))

    def test_rarity_non_regression_quint(self):
        # Non-régression multi-role: Quint covers Engine + Ladder alone
        reqs = {"Firetruck": 1, "Platform Truck": 1}
        valid = {
            "Firetruck": {"q1", "e1"},
            "Platform Truck": {"q1"},
        }
        avail = [
            ("q1", 13, float("inf"), 0, 500, 0, 0),
            ("e1", 1, float("inf"), 0, 0, 0, 0),
        ]
        steps, rem, _ = solve_dispatch(self.vm, dict(reqs), valid, avail)
        self.assertTrue(all(c == 0 for c in rem.values()))
        self.assertEqual(len(steps), 1, "Quint covers both roles — no extra vehicle")


class TestTrailerEligibility(unittest.TestCase):
    def _pool(self, **overrides):
        entry = {"vid": "t1", "sys_id": 41, "building_id": "111_222",
                 "fms": "2", "checked": False, "locked": False}
        entry.update(overrides)
        return [entry]

    def test_local_tower_required(self):
        pool = self._pool() + [
            {"vid": "t2", "sys_id": 41, "building_id": "999", "fms": "2",
             "checked": False, "locked": False},
        ]
        towers = trailer_local_towers("111_222", [41], pool)
        self.assertEqual([t["vid"] for t in towers], ["t1"])

    def test_tower_fms_must_be_available(self):
        self.assertEqual(trailer_local_towers("111_222", [41], self._pool(fms="3")), [])
        self.assertEqual(trailer_local_towers("111_222", [41], self._pool(fms="2")), self._pool())

    def test_tower_locked_or_checked_skipped(self):
        self.assertEqual(trailer_local_towers("111_222", [41], self._pool(checked=True)), [])
        self.assertEqual(trailer_local_towers("111_222", [41], self._pool(locked=True)), [])

    def test_tower_training_gate(self):
        pool = self._pool()
        trained = {"t1": False}
        self.assertEqual(
            trailer_local_towers("111_222", [41], pool, require_training=True, trained_map=trained), [])
        self.assertEqual(len(trailer_local_towers("111_222", [41], pool, require_training=False)), 1)
        trained_ok = {"t1": True}
        self.assertEqual(
            len(trailer_local_towers("111_222", [41], pool, require_training=True, trained_map=trained_ok)), 1)

    def test_tower_wrong_type_excluded(self):
        self.assertEqual(trailer_local_towers("111_222", [41], self._pool(sys_id=10)), [])


class TestCapabilityMasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = VehicleManager(code="us")
        from utils.vehicle_manager import (
            CAP_ENGINE, CAP_LADDER, CAP_HEAVY_RESCUE, CAP_TANKER,
            CAP_WATER, CAP_FOAM, CAP_HAZMAT,
        )
        cls.CAP = dict(ENGINE=CAP_ENGINE, LADDER=CAP_LADDER,
                       HEAVY_RESCUE=CAP_HEAVY_RESCUE, TANKER=CAP_TANKER,
                       WATER=CAP_WATER, FOAM=CAP_FOAM, HAZMAT=CAP_HAZMAT)

    def test_masks_derived_from_data(self):
        self.assertTrue(self.vm.capability_masks, "masks must be derived at load time")
        quint = self.vm.capability_mask(13)
        self.assertTrue(quint & self.CAP["ENGINE"], "Quint must carry ENGINE")
        self.assertTrue(quint & self.CAP["LADDER"], "Quint must carry LADDER")
        re_m = self.vm.capability_mask(18)
        self.assertTrue(re_m & self.CAP["ENGINE"])
        self.assertTrue(re_m & self.CAP["HEAVY_RESCUE"])
        pt = self.vm.capability_mask(33)
        self.assertTrue(pt & self.CAP["ENGINE"])
        self.assertTrue(pt & self.CAP["TANKER"])

    def test_requirement_mask(self):
        self.assertTrue(self.vm.requirement_mask("Heavy Rescue Vehicle") & self.CAP["HEAVY_RESCUE"])
        wm = self.vm.requirement_mask("Water Tanker")
        self.assertTrue(wm & self.CAP["TANKER"] and wm & self.CAP["WATER"])
        self.assertTrue(self.vm.requirement_mask("HazMat Unit") & self.CAP["HAZMAT"])

    def test_mask_fallback_resolves_unknown_name(self):
        # Index/regex/fuzzy must fail for this synthetic name -> mask fallback
        ids = self.vm.get_valid_ids("Advanced Foam Sprayer")
        self.assertTrue(ids, "mask fallback should resolve via FOAM capability")
        for vid in ids:
            self.assertTrue(self.vm.capability_mask(vid) & self.CAP["FOAM"],
                            f"{vid} must carry FOAM bit")

    def test_direct_match_still_wins(self):
        self.assertTrue(self.vm.get_valid_ids("ambulance"))
        self.assertTrue(self.vm.get_valid_ids("battalion chief unit"))


class TestRadiusAndStation(unittest.TestCase):
    def test_radius_gate(self):
        self.assertTrue(within_dispatch_radius(5.0, 0))
        self.assertTrue(within_dispatch_radius(5.0, 10))
        self.assertFalse(within_dispatch_radius(15.0, 10))
        self.assertTrue(within_dispatch_radius(float("inf"), 10))

    def test_same_station_composite_ids(self):
        # checkbox building_id can be composite '111111_222222'
        self.assertTrue(_same_station("111111_222222", "111111"))
        self.assertTrue(_same_station("111111_222222", "222222_999"))
        self.assertFalse(_same_station("111111_222222", "777"))
        self.assertFalse(_same_station("", "111111"))
        self.assertFalse(_same_station("", ""))


class TestApiBackoff(unittest.TestCase):
    def test_backoff_exponential(self):
        self.assertAlmostEqual(backoff_delay(1, 1.5), 1.5)
        self.assertAlmostEqual(backoff_delay(2, 1.5), 2.25)
        self.assertAlmostEqual(backoff_delay(3, 2.0), 8.0)

    def test_backoff_retry_after_takes_precedence(self):
        self.assertEqual(backoff_delay(1, 1.5, retry_after="4"), 4.0)
        self.assertEqual(backoff_delay(1, 1.5, retry_after="0"), 0.0)
        self.assertEqual(backoff_delay(1, 1.5, retry_after=None), 1.5)
        self.assertEqual(backoff_delay(1, 1.5, retry_after="bogus"), 1.5)


class TestMissionMeta(unittest.TestCase):
    def setUp(self):
        import utils.mission_data as md
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig = md.MISSION_META_PATH
        md.MISSION_META_PATH = Path(self._tmpdir.name) / "mission_meta.json"

    def tearDown(self):
        import utils.mission_data as md
        md.MISSION_META_PATH = self._orig
        self._tmpdir.cleanup()

    def test_first_seen_and_age(self):
        import time
        import utils.mission_data as md
        md.MISSION_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        md.update_mission_meta(["111111"])
        age = md.get_mission_age("111111")
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)
        self.assertLess(age, 5)
        self.assertIsNone(md.get_mission_age("424242"))
        # second update must NOT reset first_seen
        first = md.load_mission_meta()["111111"]["first_seen"]
        time.sleep(0.05)
        md.update_mission_meta(["111111"])
        self.assertEqual(md.load_mission_meta()["111111"]["first_seen"], first)


if __name__ == "__main__":
    unittest.main()
