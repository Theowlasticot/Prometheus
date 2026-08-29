"""OptimalDispatchSolver — unified greedy set-cover for Prometheus.

Pure & offline-testable (no browser needed). One pass covers:
  - role requirements (multi-role collapse via multi_role.json)
  - cumulative water need (prefer water-carrying vehicles when water lacking)
  - cumulative foam need
  - cumulative personnel need (active only when crew data is provided)

Preserves the historical guarantees of greedy_plan:
  - exact-type preference (MCV fills MCV slots, never substitutes BCU)
  - multi-role collapse only for vehicles declared in multi_role.json
  - distance ascending as final tie-break
"""


def solve(vm, remaining, valid_per_req, avail,
          water_needed=0, foam_needed=0, personnel_needed=0,
          require_training=False, crew_trained=None):
    """Best-first weighted set cover with cumulative resource constraints.

    vm: VehicleManager (normalize/primary_name/is_true_multi_role)
    remaining: {req_name: count} (net role needs)
    valid_per_req: {req_name: set(vid)}
    avail: list of (vid, sys_id, dist, can_satisfy, water, foam, crew)
    water_needed / foam_needed / personnel_needed: residual resource needs
    require_training: if True, candidates flagged untrained are skipped
    crew_trained: {vid: bool} training eligibility map (optional)

    Returns (steps, final_remaining, totals):
      steps: [(vid, target_req, is_multi, satisfiable_reqs)]
      final_remaining: {req_name: count} after the pass
      totals: {"water": int, "foam": int, "personnel": int} accumulated
    """
    steps = []
    rem = {k: int(v) for k, v in remaining.items() if int(v) > 0}
    cand = list(avail)
    cur_water = 0
    cur_foam = 0
    cur_personnel = 0

    def _done():
        roles_done = all(c <= 0 for c in rem.values())
        return (roles_done
                and cur_water >= water_needed
                and cur_foam >= foam_needed
                and cur_personnel >= personnel_needed)

    while not _done():
        best = None
        for item in cand:
            vid, sys_id, dist, can_satisfy, w, f, crew = item
            if require_training and crew_trained is not None and not crew_trained.get(str(vid), True):
                continue
            satisfiable = [r for r, c in rem.items() if c > 0 and vid in valid_per_req.get(r, set())]
            contributes_res = (
                (water_needed > cur_water and w > 0)
                or (foam_needed > cur_foam and f > 0)
                or (personnel_needed > cur_personnel and crew > 0)
            )
            if not satisfiable and not contributes_res:
                continue
            exact = 0
            prim = None
            try:
                prim = vm.primary_name(sys_id)
            except Exception:
                prim = None
            if prim:
                norm = vm.normalize(prim)
                if any(vm.normalize(r) == norm for r in satisfiable):
                    exact = 1
            is_multi = False
            try:
                is_multi = bool(sys_id and vm.is_true_multi_role(sys_id))
            except Exception:
                is_multi = False
            can = len(satisfiable)
            # Resource bonus carries MAGNITUDE (not just a flag) so tied
            # candidates prefer the highest capacity — 1 Tanker (10000L)
            # beats 8 Quints (500L each) for the same water need.
            res_bonus = (
                w if (water_needed > cur_water and w > 0) else 0,
                f if (foam_needed > cur_foam and f > 0) else 0,
                crew if (personnel_needed > cur_personnel and crew > 0) else 0,
            )
            score = (exact, 1 if (is_multi and can >= 2) else 0, can, res_bonus, -dist)
            if best is None or score > best[0]:
                best = (score, item, satisfiable)
        if best is None:
            break
        _score, (vid, sys_id, dist, can_satisfy, w, f, crew), satisfiable = best
        # Target req = own role name if requested, else the most demanded one
        target_req = max(satisfiable, key=lambda r: rem[r]) if satisfiable else None
        prim = None
        try:
            prim = vm.primary_name(sys_id)
        except Exception:
            prim = None
        if prim and satisfiable:
            norm = vm.normalize(prim)
            for r in satisfiable:
                if vm.normalize(r) == norm:
                    target_req = r
                    break
        is_multi = False
        try:
            is_multi = bool(sys_id and vm.is_true_multi_role(sys_id))
        except Exception:
            is_multi = False
        # Apply role coverage
        if is_multi and len(satisfiable) >= 2:
            for r in satisfiable:
                if rem[r] > 0:
                    rem[r] -= 1
        elif target_req is not None:
            rem[target_req] -= 1
        # Apply resource accumulation (only while lacking)
        if water_needed > cur_water:
            cur_water += max(0, w)
        if foam_needed > cur_foam:
            cur_foam += max(0, f)
        if personnel_needed > cur_personnel:
            cur_personnel += max(0, crew)
        steps.append((vid, target_req, is_multi and len(satisfiable) >= 2, satisfiable))
        cand.remove((vid, sys_id, dist, can_satisfy, w, f, crew))
    return steps, rem, {"water": cur_water, "foam": cur_foam, "personnel": cur_personnel}
