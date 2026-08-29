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


def prioritize_requirements_by_scarcity(remaining, valid_per_req, avail):
    """MRV ordering: most constrained requirements first.

    remaining: {req_name: count}
    valid_per_req: {req_name: set(vid)}
    avail: list of (vid, sys_id, dist, can_satisfy, water, foam, crew)

    Returns [(req_name, scarcity, count)] sorted by scarcity ascending
    (scarcity = number of available candidates able to cover the req).
    """
    scored = []
    for req_name, count in remaining.items():
        vids = valid_per_req.get(req_name, set())
        scarcity = sum(1 for v in avail if v[0] in vids)
        scored.append((scarcity, req_name, count))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [(name, scarcity, count) for scarcity, name, count in scored]


def solve(vm, remaining, valid_per_req, avail,
          water_needed=0, foam_needed=0, personnel_needed=0,
          require_training=False, crew_trained=None,
          personnel_needs=None, crew_educations=None):
    """Best-first weighted set cover with cumulative resource constraints.

    vm: VehicleManager (normalize/primary_name/is_true_multi_role)
    remaining: {req_name: count} (net role needs)
    valid_per_req: {req_name: set(vid)}
    avail: list of (vid, sys_id, dist, can_satisfy, water, foam, crew)
    water_needed / foam_needed / personnel_needed: residual resource needs
    require_training: if True, candidates flagged untrained are skipped
    crew_trained: {vid: bool} training eligibility map (optional)

    G1 — education-aware personnel (optional):
    personnel_needs: [{name, count}] mission personnel education needs
      (e.g. [{"name": "HazMat", "count": 8}]). When provided, the scalar
      personnel_needed is ignored and each crew member must hold a matching
      education (normalized containment) to contribute.
    crew_educations: {vid: [normalized education names]} (optional)

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
    edu_mode = bool(personnel_needs)
    edu_need = {}
    cur_edu = {}
    if edu_mode:
        for e in personnel_needs:
            if not isinstance(e, dict):
                continue
            try:
                n = vm.normalize(e.get("name", ""))
                c = int(e.get("count", 0) or 0)
            except Exception:
                continue
            if n and c > 0:
                edu_need[n] = edu_need.get(n, 0) + c
                cur_edu[n] = 0

    def _edu_matches(vid):
        if not edu_mode or not crew_educations:
            return []
        eds = crew_educations.get(str(vid)) or []
        return [n for n, c in edu_need.items()
                if c > cur_edu.get(n, 0)
                and any(n in e or e in n for e in eds)]

    def _done():
        roles_done = all(c <= 0 for c in rem.values())
        base = (roles_done
                and cur_water >= water_needed
                and cur_foam >= foam_needed)
        if edu_mode:
            return base and all(cur_edu.get(n, 0) >= c for n, c in edu_need.items())
        return base and cur_personnel >= personnel_needed

    while not _done():
        # MRV: requirements with the fewest candidate providers get priority.
        # Protects rare/polyvalent vehicles (e.g. the only Rescue Engine)
        # from being burned on a generic slot while the scarce slot stays empty.
        scarcity = {}
        for r in rem:
            vids = valid_per_req.get(r, set())
            scarcity[r] = sum(1 for item in cand if item[0] in vids)
        min_scarcity = min(scarcity.values()) if scarcity else 0
        best = None
        for item in cand:
            vid, sys_id, dist, can_satisfy, w, f, crew = item
            if require_training and crew_trained is not None and not crew_trained.get(str(vid), True):
                continue
            satisfiable = [r for r, c in rem.items() if c > 0 and vid in valid_per_req.get(r, set())]
            edu_hits = _edu_matches(vid)
            contributes_res = (
                (water_needed > cur_water and w > 0)
                or (foam_needed > cur_foam and f > 0)
                or (not edu_mode and personnel_needed > cur_personnel and crew > 0)
                or (edu_mode and bool(edu_hits))
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
            # Rare-need boost: candidate covers the currently most
            # constrained requirement (MRV heuristic)
            rare_need = 1 if any(scarcity.get(r, 0) == min_scarcity for r in satisfiable) else 0
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
                (len(edu_hits) if edu_mode
                 else (crew if (personnel_needed > cur_personnel and crew > 0) else 0)),
            )
            score = (exact, rare_need, 1 if (is_multi and can >= 2) else 0, can, res_bonus, -dist)
            if best is None or score > best[0]:
                best = (score, item, satisfiable)
        if best is None:
            break
        _score, (vid, sys_id, dist, can_satisfy, w, f, crew), satisfiable = best
        # Target req = own role name if requested, else the scarcest
        # satisfiable requirement (tie-break: most demanded)
        target_req = max(satisfiable, key=lambda r: (-scarcity.get(r, 0), -rem[r], r)) if satisfiable else None
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
        if edu_mode:
            for n in _edu_matches(vid):
                cur_edu[n] = cur_edu.get(n, 0) + 1
        elif personnel_needed > cur_personnel:
            cur_personnel += max(0, crew)
        steps.append((vid, target_req, is_multi and len(satisfiable) >= 2, satisfiable))
        cand.remove((vid, sys_id, dist, can_satisfy, w, f, crew))
    return steps, rem, {"water": cur_water, "foam": cur_foam, "personnel": cur_personnel}
