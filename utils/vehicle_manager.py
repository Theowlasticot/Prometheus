import json
import os
import glob
import re
from pathlib import Path
from utils.pretty_print import display_error, display_warning

# ---------------------------------------------------------------------------
# Capability bitmasks (R5) — derived from multi_role.json + .mscv data,
# never hardcoded per server. Used as a resolution fallback and for
# capability queries. Bit values are stable across servers.
# ---------------------------------------------------------------------------
CAP_ENGINE = 1 << 0
CAP_LADDER = 1 << 1
CAP_HEAVY_RESCUE = 1 << 2
CAP_TANKER = 1 << 3
CAP_HAZMAT = 1 << 4
CAP_AIR = 1 << 5
CAP_COMMAND = 1 << 6
CAP_WATER = 1 << 7
CAP_FOAM = 1 << 8
CAP_AMBULANCE = 1 << 9
CAP_POLICE = 1 << 10
CAP_ARFF = 1 << 11
CAP_BOAT = 1 << 12
CAP_TOW = 1 << 13
CAP_ALS = 1 << 14
CAP_PRISONER = 1 << 15
CAP_PERSONNEL = 1 << 16

# keyword (i18n-friendly) -> capability bit
CAPABILITY_KEYWORDS = {
    "ENGINE": (CAP_ENGINE, ["engine", "firetruck", "pumper", "feuerwehr", "pompe", "fourgon"]),
    "LADDER": (CAP_LADDER, ["ladder", "platform", "quint", "tower", "echelle", "échelle"]),
    "HEAVY_RESCUE": (CAP_HEAVY_RESCUE, ["heavy rescue", "rescue", "désincarcération", "secours"]),
    "TANKER": (CAP_TANKER, ["tanker", "tender", "citerne", "tlf"]),
    "HAZMAT": (CAP_HAZMAT, ["hazmat", "haz mat"]),
    "AIR": (CAP_AIR, ["air", "cascade", "breathe", "breathing"]),
    "COMMAND": (CAP_COMMAND, ["command", "chief", "mcv", "battalion", "elk"]),
    "WATER": (CAP_WATER, ["water", "eau", "wasser", "liter", "gallon"]),
    "FOAM": (CAP_FOAM, ["foam", "mousse", "schaum"]),
    "AMBULANCE": (CAP_AMBULANCE, ["ambulance", "ems", "rtw", "ktw", "paramedic"]),
    "POLICE": (CAP_POLICE, ["police", "patrol", "sheriff", "fbi", "swat", "fu stw", "fustw"]),
    "ARFF": (CAP_ARFF, ["arff", "airport", "crash tender"]),
    "BOAT": (CAP_BOAT, ["boat", "swift water", "swiftwater", "ocean"]),
    "TOW": (CAP_TOW, ["tow", "wrecker", "abschlepp"]),
    "ALS": (CAP_ALS, ["als", "advanced life support"]),
    "PRISONER": (CAP_PRISONER, ["prisoner", "gefangene", "arrest", "prisonnier"]),
    "PERSONNEL": (CAP_PERSONNEL, ["personnel", "crew", "personal"]),
}

# existing KEYWORD_MAP capability names -> bitmask bits
_KEYWORD_MAP_BITS = {
    "FOAM": CAP_FOAM, "WATER": CAP_WATER, "PERSONNEL": CAP_PERSONNEL,
    "PRISONER": CAP_PRISONER, "PATIENT": CAP_AMBULANCE, "TOW": CAP_TOW,
    "HAZMAT": CAP_HAZMAT, "HRV": CAP_HEAVY_RESCUE, "PLATFORM": CAP_LADDER,
    "ARFF": CAP_ARFF, "SWIFTWATER": CAP_BOAT, "TACTICAL": CAP_POLICE,
    "ALS": CAP_ALS, "OCEAN_NAV": CAP_BOAT,
}

def text_mask(text):
    """Capability mask for a requirement/vehicle name (pure helper)."""
    mask = 0
    low = (text or "").lower()
    for _name, (bit, keywords) in CAPABILITY_KEYWORDS.items():
        if any(k in low for k in keywords):
            mask |= bit
    return mask

class VehicleManager:
    def __init__(self, data_folder=None, code=None):
        # Dynamic code + cache resolution: prefers assets_cache/{code}, falls back to bundled us/
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if code is None:
            try:
                from data.config_settings import get_server_code
                code = get_server_code()
            except Exception:
                code = "us"
        code = (code or "us").lower()
        if data_folder is None:
            # Prefer remote cache if present
            try:
                from data.config_settings import get_server_cache_dir
                cache_dir = get_server_cache_dir()
            except Exception:
                cache_dir = "assets_cache"
            candidate = os.path.join(base, cache_dir, code)
            has_mscv = False
            if os.path.isdir(candidate):
                try:
                    has_mscv = any(f.endswith(".mscv") for f in os.listdir(candidate))
                except Exception:
                    has_mscv = False
            if has_mscv:
                data_folder = candidate
            else:
                if code == "us":
                    data_folder = os.path.join(base, "us")
                else:
                    data_folder = candidate
        else:
            # Explicit folder provided — resolve relative to base
            if not os.path.isabs(data_folder):
                candidate = os.path.join(base, data_folder)
                if os.path.exists(candidate):
                    data_folder = candidate
        self.data_folder = data_folder
        self.code = code
        self.index = {}          # Map: Simple Name -> List of System IDs
        self.regex_rules = []    # List of {regex: compiled_re, id: system_id}
        self.vehicle_properties = {} # Map: System ID -> {extend: dict, is_matchless: bool}
        self.vehicle_capabilities = {} # Map: System ID -> set(["FOAM", "WATER", "SWAT", "PRISONER", ...])
        self.capability_masks = {} # Map: System ID -> int bitmask (derived, R5)
        
        self.load_database()
        self.load_additional_data()
        self._build_capability_masks()

    def load_additional_data(self):
        """Load multi-role, trailers, training, equipment_capacity, automation from data/ directory (wiki/forum + your table). Keep in sync with GitHub .mscv via remote sync for vehicle definitions, but these matrices are local and updated via git."""
        base = Path(__file__).resolve().parent.parent / "data"
        try:
            # Multi-role
            mr_path = base / "multi_role.json"
            if mr_path.exists():
                self.multi_role = json.loads(mr_path.read_text(encoding="utf-8"))
            else:
                self.multi_role = {}
        except Exception as e:
            display_warning(f"Could not load multi_role.json: {e}")
            self.multi_role = {}
        try:
            tr_path = base / "trailers.json"
            if tr_path.exists():
                self.trailers = json.loads(tr_path.read_text(encoding="utf-8"))
            else:
                self.trailers = {}
        except Exception as e:
            display_warning(f"Could not load trailers.json: {e}")
            self.trailers = {}
        try:
            trn_path = base / "training.json"
            if trn_path.exists():
                self.training = json.loads(trn_path.read_text(encoding="utf-8"))
            else:
                self.training = {}
        except Exception as e:
            display_warning(f"Could not load training.json: {e}")
            self.training = {}
        try:
            eq_path = base / "equipment_capacity.json"
            if eq_path.exists():
                self.equipment_capacity = json.loads(eq_path.read_text(encoding="utf-8"))
            else:
                self.equipment_capacity = {}
        except Exception as e:
            display_warning(f"Could not load equipment_capacity.json: {e}")
            self.equipment_capacity = {}
        try:
            auto_path = base / "automation.json"
            if auto_path.exists():
                self.automation = json.loads(auto_path.read_text(encoding="utf-8"))
            else:
                self.automation = {}
        except Exception as e:
            display_warning(f"Could not load automation.json: {e}")
            self.automation = {}

    def _build_capability_masks(self):
        """Derive per-vehicle capability bitmasks from .mscv keyword caps +
        multi_role.json roles — server-agnostic, no hardcoded vehicle ids."""
        self.capability_masks = {}
        for vid, caps in self.vehicle_capabilities.items():
            mask = 0
            for c in caps:
                mask |= _KEYWORD_MAP_BITS.get(str(c).upper(), 0)
            self.capability_masks[vid] = mask
        try:
            for mname, minfo in self.multi_role.items():
                role_mask = 0
                for role in minfo.get("roles", []):
                    role_mask |= text_mask(role)
                for vid in minfo.get("mscv_ids", []):
                    self.capability_masks[vid] = self.capability_masks.get(vid, 0) | role_mask
        except Exception:
            pass

    def capability_mask(self, system_id):
        """Capability bitmask of a vehicle type (0 = unknown)."""
        return self.capability_masks.get(system_id, 0)

    def requirement_mask(self, text):
        """Capability bitmask implied by a requirement name."""
        return text_mask(text)

    def get_ids_by_mask(self, mask, exclude_matchless=True):
        """Vehicle ids whose capability mask overlaps the given mask."""
        return [
            vid for vid, m in self.capability_masks.items()
            if (m & mask)
            and (not exclude_matchless
                 or not self.vehicle_properties.get(vid, {}).get('is_matchless', False))
        ]

    def get_required_training(self, vehicle_id: int) -> list:
        """Return list of required trainings for a vehicle from training.json (search by mscv_ids)."""
        trainings = []
        try:
            for academy, courses in self.training.items():
                for course, info in courses.items():
                    if vehicle_id in info.get("mscv_ids", []):
                        trainings.append(f"{academy}: {course} ({info.get('days')}d)")
        except Exception:
            pass
        return trainings

    def get_equipment_capacity(self, vehicle_name: str) -> int:
        """Get equipment capacity for vehicle name (e.g., Quint 30, Type 1 20)."""
        try:
            # Direct match
            if vehicle_name in self.equipment_capacity:
                return self.equipment_capacity[vehicle_name].get("capacity", 0)
            # Normalize lookup
            norm = self.normalize(vehicle_name)
            for k, v in self.equipment_capacity.items():
                if k == "Equipment":
                    continue
                if self.normalize(k) == norm:
                    return v.get("capacity", 0)
        except Exception:
            pass
        return 0

    def is_trailer(self, vehicle_id: int) -> bool:
        """Check if vehicle is a trailer (needs towing)."""
        try:
            for tname, tinfo in self.trailers.items():
                if tinfo.get("id") == vehicle_id:
                    return True
        except Exception:
            pass
        return False

    def get_towing_vehicles(self, trailer_id: int) -> list:
        """Get eligible towing vehicle ids for a trailer."""
        try:
            for tname, tinfo in self.trailers.items():
                if tinfo.get("id") == trailer_id:
                    return tinfo.get("towing_ids", [])
        except Exception:
            pass
        return []

    def normalize(self, text):
        """Standardizes text for index lookups."""
        if not text: return ""
        text = text.lower()
        # Keep alphanumeric only for strict index keys
        text = re.sub(r'[^a-z0-9]', '', text)
        return text

    def sanitize_pattern(self, pattern):
        """
        Converts .NET/C# Regex syntax to Python Regex syntax.
        Specifically fixes named groups: (?'name'...) -> (?P<name>...)
        """
        if "(?'" in pattern:
            pattern = re.sub(r"\(\?'(\w+)'", r"(?P<\1>", pattern)
        return pattern

    def load_database(self):
        if not os.path.isdir(self.data_folder):
            display_warning(f"Vehicle data folder missing: {self.data_folder} (code={self.code}) — will be empty until dashboard sync")
            # Fallback to bundled us if non-us cache empty
            if self.code != "us":
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                fallback = os.path.join(base, "us")
                if os.path.isdir(fallback):
                    display_warning(f"Falling back to bundled us/ for code {self.code} until sync")
                    self.data_folder = fallback
                else:
                    return
            else:
                return
        # Keywords to detect capabilities in ANY language (wiki/forum + your table)
        KEYWORD_MAP = {
            "FOAM": ["foam", "mousse", "schaum", "schuim", "écume"],
            "WATER": ["water", "eau", "wasser", "liters", "gallons", "gal\\.", "l\\."],
            "PERSONNEL": ["personnel", "pompier", "firefighter", "feuerwehrmann", "policier", "police", "swat"],
            "PRISONER": ["prisoner", "prisonnier", "gefangene", "arrest"],
            "PATIENT": ["patient", "transport", "ambulance", "hospital"],
            "TOW": ["tow", "wrecker", "abschlepp", "tiller"],
            "HAZMAT": ["hazmat", "haz mat", "hazmat", "hazmat", "hazmat"],
            "HRV": ["heavy rescue", "heavy rescue", "rescue"],
            "PLATFORM": ["platform", "ladder", "quint", "tower ladder"],
            "ARFF": ["arff", "crash tender", "airport"],
            "SWIFTWATER": ["swift water", "swiftwater", "water rescue", "boat"],
            "TACTICAL": ["tactical", "tactical"],
            "ALS": ["als", "advanced life support"],
            "OCEAN_NAV": ["ocean navigation", "ocean nav", "coastal"],
        }

        # 1. Load Generic Categories (Vehicle.mscv)
        vehicle_file = os.path.join(self.data_folder, "Vehicle.mscv")
        if os.path.exists(vehicle_file):
            try:
                with open(vehicle_file, 'r', encoding='utf-8') as f:
                    data = json.loads(f.read())
                    for category, ids in data.items():
                        clean_cat = self.normalize(category)
                        self.add_to_index(clean_cat, ids)
                        if clean_cat.endswith('s'):
                            self.add_to_index(clean_cat[:-1], ids)
                        
                        # Add explicit capabilities from category names
                        for capability, keywords in KEYWORD_MAP.items():
                            if any(k in clean_cat for k in keywords):
                                for vid in ids:
                                    if vid not in self.vehicle_capabilities:
                                        self.vehicle_capabilities[vid] = set()
                                    self.vehicle_capabilities[vid].add(capability)

            except (OSError, json.JSONDecodeError, ValueError) as e:
                display_error(f"Error loading Vehicle.mscv: {e}")

        # 2. Load Specific Patterns (*.mscv)
        pattern_files = glob.glob(os.path.join(self.data_folder, "*.mscv"))
        for filepath in pattern_files:
            if "Vehicle.mscv" in filepath: continue
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.loads(f.read())
                    vehicle_id = data.get('object')
                    if vehicle_id is None: continue
                    
                    # Initialize capability set
                    if vehicle_id not in self.vehicle_capabilities:
                        self.vehicle_capabilities[vehicle_id] = set()

                    # Sanitize 'extend' patterns
                    raw_extends = data.get('pattern', {}).get('extend', {})
                    sanitized_extends = {}
                    for pat, val in raw_extends.items():
                        clean_pat_key = self.sanitize_pattern(pat)
                        sanitized_extends[clean_pat_key] = val
                        
                        # CAPABILITY DETECTION from Regex
                        pat_lower = clean_pat_key.lower()
                        for capability, keywords in KEYWORD_MAP.items():
                            if any(k in pat_lower for k in keywords):
                                self.vehicle_capabilities[vehicle_id].add(capability)

                    # Store Properties
                    self.vehicle_properties[vehicle_id] = {
                        "extend": sanitized_extends,
                        "is_matchless": data.get('is_matchless', False),
                        "base": [self.sanitize_pattern(p) for p in data.get('pattern', {}).get('base', [])]
                    }

                    # Parse Base Patterns
                    patterns = data.get('pattern', {}).get('base', [])
                    for pattern in patterns:
                        pattern = self.sanitize_pattern(pattern)
                        
                        # Hybrid Matching Logic
                        if any(char in pattern for char in "[]|()^$*+?\\"):
                            try:
                                self.regex_rules.append({
                                    "regex": re.compile(pattern, re.IGNORECASE),
                                    "id": vehicle_id
                                })
                            except re.error:
                                clean_pat = self.normalize(pattern)
                                self.add_to_index(clean_pat, [vehicle_id])
                        else:
                            clean_pat = self.normalize(pattern)
                            self.add_to_index(clean_pat, [vehicle_id])
                            
                        # CAPABILITY DETECTION from Name
                        name_lower = pattern.lower()
                        for capability, keywords in KEYWORD_MAP.items():
                            if any(k in name_lower for k in keywords):
                                self.vehicle_capabilities[vehicle_id].add(capability)
                            
            except (OSError, json.JSONDecodeError, ValueError, re.error) as e:
                display_error(f"Skipping {filepath}: {e}")
                continue

    def add_to_index(self, key, ids):
        if key not in self.index:
            self.index[key] = []
        for x in ids:
            if x not in self.index[key]:
                self.index[key].append(x)

    def get_valid_ids(self, requirement_text):
        """Return list of System IDs matching the text."""
        found_ids = set()
        clean_req = self.normalize(requirement_text)
        
        # 1. Simple Index Match
        if clean_req in self.index:
            found_ids.update(self.index[clean_req])
            
        # 2. Regex Rule Match
        for rule in self.regex_rules:
            if rule["regex"].search(requirement_text):
                found_ids.add(rule["id"])
                
        # 3. Fuzzy Fallback (Only if no direct match) — bounded to avoid over-matching
        if not found_ids:
            if len(clean_req) >= 3:
                if clean_req + "s" in self.index:
                    found_ids.update(self.index[clean_req + "s"])
                elif clean_req.endswith('s') and clean_req[:-1] in self.index:
                    found_ids.update(self.index[clean_req[:-1]])
            
            if not found_ids and len(clean_req) >= 4:
                for key, ids in self.index.items():
                    # Require substantial overlap, not just substring of short word
                    if len(key) < 4:
                        continue
                    if clean_req in key or key in clean_req:
                        # Avoid matching very short substrings like "boat" in "motorboat" is ok, but "engine" in "search engine" not
                        # Require at least 4 chars overlap or ratio
                        if len(clean_req) < 4 or len(key) < 4:
                            continue
                        valid_fuzzy = [vid for vid in ids if not self.vehicle_properties.get(vid, {}).get('is_matchless', False)]
                        found_ids.update(valid_fuzzy)
                # Also handle is_matchless for direct/regex: if direct matched is_matchless, keep but warn
                # (is_matchless vehicles like EMS Chief should still be dispatchable via direct)

        # 3bis. Capability bitmask fallback (R5): index/regex/fuzzy found nothing —
        # resolve by derived capability overlap (multi-server safe)
        if not found_ids:
            req_mask = text_mask(requirement_text)
            if req_mask:
                for vid, mask in self.capability_masks.items():
                    if (mask & req_mask) and not self.vehicle_properties.get(vid, {}).get('is_matchless', False):
                        found_ids.add(vid)

        return list(found_ids)

    def get_required_quantity(self, system_id, requirement_name, html_count):
        """Calculates vehicle quantity using regex rules (e.g. 24 SWAT -> 5 Vehicles)."""
        props = self.vehicle_properties.get(system_id, {})
        extends = props.get("extend", {})
        
        if not extends:
            return html_count
            
        candidates = [
            f"{html_count} {requirement_name}",
            requirement_name
        ]
        
        for text in candidates:
            for pattern, qty in extends.items():
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        return qty
                except re.error:
                    continue
                    
        return html_count

    def get_ids_with_capability(self, capability):
        return [
            vid for vid, caps in self.vehicle_capabilities.items() 
            if capability.upper() in caps
        ]

    def primary_name(self, system_id):
        """First base pattern of the mscv file — the vehicle's 'own' role name.

        Used to prefer exact-type dispatch: MCV (12) lists 'Mobile Command Vehicle'
        first (and 'Battalion chief unit' second), so it fills MCV slots before BCU.
        """
        props = self.vehicle_properties.get(system_id, {})
        base = props.get("base") or []
        return base[0] if base else None

    def is_true_multi_role(self, system_id):
        """True only for vehicles declared in multi_role.json (Quint, Rescue Engine,
        Pumper-Tanker, etc.) — category overlaps like MCV->BCU are NOT multi-role."""
        try:
            for mname, minfo in self.multi_role.items():
                if system_id in minfo.get("mscv_ids", []):
                    return True
        except Exception:
            pass
        return False

def get_manager_for_code(code=None):
    """Helper to get VehicleManager for current server code (DRY for dispatcher/mission_data)."""
    if code is None:
        try:
            from data.config_settings import get_server_code
            code = get_server_code()
        except Exception:
            code = "us"
    return VehicleManager(code=code)