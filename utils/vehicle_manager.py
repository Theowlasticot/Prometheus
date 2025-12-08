import json
import os
import glob
import re

class VehicleManager:
    def __init__(self, data_folder="us"):
        self.data_folder = data_folder
        # Main mapping: "clean_name" -> [list of IDs]
        self.index = {} 
        # Special resource categories
        self.special_categories = {}
        
        self.load_database()

    def normalize(self, text):
        """
        Converts text to a standard comparison format:
        - Lowercase
        - Removes spaces, underscores, dashes
        - Removes common suffixes like 's' (plural), 'unit', 'vehicle' for fuzzy matching
        """
        if not text: return ""
        text = text.lower()
        # Remove non-alphanumeric characters
        text = re.sub(r'[^a-z0-9]', '', text)
        return text

    def load_database(self):
        # 1. Load Categories from Vehicle.mscv (e.g., "police_cars": [10, 19...])
        vehicle_file = os.path.join(self.data_folder, "Vehicle.mscv")
        if os.path.exists(vehicle_file):
            try:
                with open(vehicle_file, 'r', encoding='utf-8') as f:
                    data = json.loads(f.read())
                    
                    for category, ids in data.items():
                        # Store raw category for special lookups (water, etc)
                        self.special_categories[category] = ids
                        
                        # Index the category name itself
                        # e.g., "police_cars" -> IDs
                        clean_cat = self.normalize(category)
                        self.add_to_index(clean_cat, ids)
                        
                        # Create singular version alias
                        # e.g. "police_cars" -> "policecar"
                        if clean_cat.endswith('s'):
                            self.add_to_index(clean_cat[:-1], ids)
                            
            except Exception as e:
                print(f"Error loading Vehicle.mscv: {e}")

        # 2. Load Patterns from Individual Files (e.g., 0.mscv)
        pattern_files = glob.glob(os.path.join(self.data_folder, "*.mscv"))
        for filepath in pattern_files:
            if "Vehicle.mscv" in filepath: continue
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.loads(f.read())
                    vehicle_id = data.get('object')
                    if vehicle_id is None: continue
                    
                    # Get all text patterns for this vehicle
                    patterns = data.get('pattern', {}).get('base', [])
                    
                    for pattern in patterns:
                        clean_pat = self.normalize(pattern)
                        self.add_to_index(clean_pat, [vehicle_id])
                        
            except Exception: pass

    def add_to_index(self, key, ids):
        if key not in self.index:
            self.index[key] = []
        # Add new IDs without duplicates
        self.index[key].extend([x for x in ids if x not in self.index[key]])

    def get_valid_ids(self, requirement_text):
        """
        Smart lookup for a requirement string.
        """
        # 1. Normalize the input (e.g. "Police Car" -> "policecar")
        clean_req = self.normalize(requirement_text)
        
        # 2. Direct Match
        if clean_req in self.index:
            return self.index[clean_req]
            
        # 3. Fuzzy / Suffix Stripping
        # If DB has "policecar" but input is "policecars", or vice versa
        if clean_req + "s" in self.index:
            return self.index[clean_req + "s"]
        if clean_req.endswith('s') and clean_req[:-1] in self.index:
            return self.index[clean_req[:-1]]
            
        # 4. Aggressive Matching (Category Fallback)
        # Only use this if direct matches fail. 
        # Tries to match input "police" to DB "policecar" or "policevehicle"
        candidates = []
        for key, ids in self.index.items():
            # Check if one is a substring of the other
            # e.g. input "heavyrescue" matches key "heavyrescuevehicle"
            if clean_req in key or key in clean_req:
                candidates.extend(ids)
                
        if candidates:
            return list(set(candidates))

        return []

    def get_water_carriers(self):
        # Returns IDs that are explicitly defined as carrying water
        return self.special_categories.get("water_needed", [])