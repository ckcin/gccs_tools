#!/usr/bin/env python3
"""
Metadata comparison tools for GOES data.
Provides deep attribute verification for variables, dimensions, and globals.
"""
try:
    import numpy as np
except ImportError:
    pass

CRITICAL_KEYS = [
    "_FillValue",
    "valid_range",
    "valid_min",
    "valid_max",
    "scale_factor",
    "add_offset"
]

IGNORE_STRINGS = [
    "date_created",
    "id",
    "production_site",
    "production_cluster",
    "dataset_name",
    "timeline_id"
]

WARN_STRINGS = [
    "data_name",
    "title",
    "summary"
]

KNOWN_STRINGS = [
    "algorithm_dynamic_input_data_container"
]

class MetadataAuditor:
    """Smart metadata comparison engine."""

    def __init__(self, tolerance=0.0001):
        self.tolerance = tolerance

    def determine_status(self, identity):
        """Tiered severity logic for mismatches."""
        if any(s in identity for s in IGNORE_STRINGS): return "IGNORE"
        if any(s in identity for s in KNOWN_STRINGS): return "KNOWN"
        if any(s in identity for s in WARN_STRINGS): return "WARNING"
        return "ERROR"

    def values_match(self, key, p, g):
        """Handles exact vs fuzzy matching based on key importance."""
        if p is g: return True
        if p is None or g is None: return False

        if isinstance(p, (np.ndarray, list, float, int)):
            p_arr = np.asanyarray(p)
            g_arr = np.asanyarray(g)

            if p_arr.shape != g_arr.shape:
                return False

            is_critical = any(ck in key for ck in CRITICAL_KEYS)
            if is_critical:
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            else:
                return np.allclose(p_arr, g_arr, atol=self.tolerance, equal_nan=True)

        return str(p).strip() == str(g).strip()

    def compare_attributes(self, group_name, p_dict, g_dict):
        """Compares attribute sets and identifies tiered issues."""
        issues = []
        all_keys = set(p_dict.keys()) | set(g_dict.keys())

        for key in sorted(all_keys):
            p_val = p_dict.get(key)
            g_val = g_dict.get(key)

            if not self.values_match(key, p_val, g_val):
                identity = f"{group_name}:{key}"
                status = self.determine_status(identity)

                issues.append({
                    "Attribute": identity,
                    "Status": status,
                    "Source1": str(p_val),
                    "Source2": str(g_val)
                })
        return issues
