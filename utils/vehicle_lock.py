"""Vehicle reservation locks — anti double-dispatch.

Two complementary layers:

1. In-flight lock (memory, TTL = config `lock_ttl`): set the moment a vehicle
   checkbox is clicked / the alarm order is emitted. Covers the server's
   status-update latency (~0.5-2s) so the next loop tick cannot re-select the
   same vehicle while the server still reports it available.

2. Sent map (persisted to data/dispatch_state.json): vid -> mission_id with no
   TTL. Freed only when the mission leaves the board (active_mission_ids.json
   cleanup). Prevents re-dispatching vehicles that are still driving to a
   mission when it escalates, and survives bot restarts.

`unlock_on_failure(mission_id)` releases both layers for a mission whose
dispatch failed (AAR error, disabled button, page crash...) so the vehicles
stay usable elsewhere instead of being locked indefinitely.
"""
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "dispatch_state.json"


class VehicleLockManager:
    def __init__(self):
        self._inflight = {}  # vid -> (locked_at_ts, mission_id)
        self._sent = {}      # vid -> mission_id (persisted)
        self._waves = {}     # mission_id -> {"needs": signature, "vids": [...], "ts": epoch}

    # ---------------------------------------------------------------- config
    @staticmethod
    def _ttl() -> float:
        try:
            from data.config_settings import get_lock_ttl
            return float(max(3, int(get_lock_ttl())))
        except Exception:
            return 12.0

    # ------------------------------------------------------------- in-flight
    def cleanup(self) -> int:
        now = time.time()
        ttl = self._ttl()
        stale = [vid for vid, (ts, _mid) in self._inflight.items() if now - ts >= ttl]
        for vid in stale:
            del self._inflight[vid]
        return len(stale)

    def lock_batch(self, vids, mission_id: str):
        now = time.time()
        for vid in vids:
            self._inflight[str(vid)] = (now, mission_id)

    def unlock_vehicle(self, vid):
        self._inflight.pop(str(vid), None)
        self._sent.pop(str(vid), None)
        self.persist()

    # ---------------------------------------------------------------- sent
    def mark_sent(self, vids, mission_id: str):
        for vid in vids:
            self._sent[str(vid)] = mission_id
        self.persist()

    def is_sent(self, vid) -> bool:
        return str(vid) in self._sent

    def sent_mission(self, vid):
        return self._sent.get(str(vid))

    def release_mission(self, mission_id: str):
        self._inflight = {vid: (ts, mid) for vid, (ts, mid) in self._inflight.items() if mid != mission_id}
        self._sent = {vid: mid for vid, mid in self._sent.items() if mid != mission_id}
        self._waves.pop(mission_id, None)
        self.persist()

    # Alias: failed dispatch frees everything reserved for that mission
    unlock_on_failure = release_mission

    def free_all(self):
        self._inflight.clear()
        self._sent.clear()
        self._waves.clear()
        self.persist()

    def sent_missions(self):
        return {mid for mid in self._sent.values()}

    def sent_vehicles_of(self, mission_id: str):
        return [vid for vid, mid in self._sent.items() if mid == mission_id]

    # ---------------------------------------------------------------- waves
    def get_wave(self, mission_id: str):
        return self._waves.get(mission_id)

    def set_wave(self, mission_id: str, needs_signature: str, vids):
        self._waves[mission_id] = {
            "needs": needs_signature,
            "vids": [str(v) for v in vids],
            "ts": time.time(),
        }
        self.persist()

    def wave_still_in_flight(self, mission_id: str, needs_signature: str) -> bool:
        """True when the same needs were already dispatched and the sent
        vehicles are still reserved (server not updated yet / still driving)."""
        wave = self._waves.get(mission_id)
        if not wave or wave.get("needs") != needs_signature:
            return False
        return any(str(v) in self._sent for v in wave.get("vids", []))

    # ------------------------------------------------------------ queries
    def is_locked(self, vid) -> bool:
        vid = str(vid)
        self.cleanup()
        return vid in self._inflight or vid in self._sent

    def __len__(self):
        self.cleanup()
        return len(self._inflight) + len(self._sent)

    # ---------------------------------------------------------- persistence
    def load_state(self):
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    sent = data.get("sent", {}) or {}
                    self._sent = {str(vid): str(mid) for vid, mid in sent.items()}
                    waves = data.get("waves", {}) or {}
                    self._waves = {str(mid): dict(w) for mid, w in waves.items()}
        except Exception:
            self._sent = {}
            self._waves = {}
        return self._sent

    def persist(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(STATE_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"sent": self._sent, "waves": self._waves}, f, indent=2)
            STATE_PATH.parent.joinpath(tmp).replace(STATE_PATH)
        except Exception:
            pass


# Process-wide singleton (dispatch runs on a single browser, single process)
LOCK_MANAGER = VehicleLockManager()
