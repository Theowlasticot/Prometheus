import json
import os
import glob
import re
from pathlib import Path
from utils.pretty_print import display_error, display_warning

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
        
        self.load_database()

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
        # Keywords to detect capabilities in ANY language (for future updates uWu)
        KEYWORD_MAP = {
            "FOAM": ["foam", "mousse", "schaum", "schuim", "écume"],
            "WATER": ["water", "eau", "wasser", "liters", "gallons", "gal\\.", "l\\."],
            "PERSONNEL": ["personnel", "pompier", "firefighter", "feuerwehrmann", "policier", "police", "swat"],
            "PRISONER": ["prisoner", "prisonnier", "gefangene", "arrest"],
            "PATIENT": ["patient", "transport", "ambulance", "hospital"],
            "TOW": ["tow", "wrecker", "abschlepp"]
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
                        "is_matchless": data.get('is_matchless', False)
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

    def get_water_carriers(self):
        cap_ids = self.get_ids_with_capability("WATER")
        if not cap_ids:
            return self.index.get('waterneeded', [])
        return cap_ids