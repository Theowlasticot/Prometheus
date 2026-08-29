"""Offline tests for the dispatch planner (no browser needed).

Run: venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.vehicle_manager import VehicleManager
import utils.dispatcher as disp
from utils.dispatcher import (
    greedy_plan,
    order_ambulance_ids,
    load_vehicle_data,
    get_valid_ids_for_type,
)



def _garage_candidates(vm, valid_per_req):
    """All garage (vid, sys_id) pairs usable for planning."""
    vd = json.loads((Path(__file__).resolve().parent.parent / "data" / "vehicle_data.json").read_text())
    out = []
    for sys_id, ids in vd.items():
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
        multi_sys = set()
        for minfo in self.vm.multi_role.values():
            multi_sys.update(minfo.get("mscv_ids", []))
        # First items must be pure (sys not multi-role); combined vehicles last
        if ordered:
            first_sys = disp.USER_TO_SYSTEM_MAP.get(str(ordered[0]))
            self.assertNotIn(first_sys, multi_sys, "first ambulance should be a pure one")
        pure_section = [v for v in ordered if disp.USER_TO_SYSTEM_MAP.get(str(v)) not in multi_sys]
        combi_section = [v for v in ordered if disp.USER_TO_SYSTEM_MAP.get(str(v)) in multi_sys]
        self.assertEqual(ordered, pure_section + combi_section)

    def test_trailer_needs_tower(self):
        self.assertTrue(self.vm.is_trailer(77))
        self.assertEqual(self.vm.get_towing_vehicles(77), [41])
        self.assertTrue(self.vm.is_trailer(78))
        self.assertEqual(set(self.vm.get_towing_vehicles(78)), {8, 1, 10, 4, 18})


if __name__ == "__main__":
    unittest.main()
