#!/usr/bin/env python3
"""
Metadata comparison tools for GOES data.
Provides deep NetCDF inspection and diffing via xarray.
(Isolated from plotting libraries; uses callbacks for visualizations)
"""

try:
    import numpy as np
    import xarray as xr
    HAS_XARRAY = True
except ImportError:
    HAS_XARRAY = False

# =============================================================================
# CONFIGURATION
# =============================================================================
CRITICAL_KEYS = [
    "_FillValue",
    "valid_range",
    "valid_min",
    "valid_max",
    "scale_factor",
    "add_offset"
]
NUMERIC_TOLERANCE = 1e-6

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
    """Smart metadata comparison engine adapted from META-PAVE."""

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

        if isinstance(p, (np.ndarray, list, float, int, np.number)):
            p_arr = np.asanyarray(p)
            g_arr = np.asanyarray(g)

            if p_arr.shape != g_arr.shape:
                return False

            is_critical = any(ck in key for ck in CRITICAL_KEYS)
            if is_critical:
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            else:
                return np.allclose(p_arr, g_arr, atol=NUMERIC_TOLERANCE, equal_nan=True)

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

    def _audit_glm_lcfa(self, ds_p, ds_g, name1, name2, spatial_plot_cb):
        """Performs GLM LCFA specific validation (events, groups, flashes, and spatial layout)."""
        issues = []
        glm_dims = ['number_of_events', 'number_of_groups', 'number_of_flashes']

        if not any(d in ds_p.sizes for d in glm_dims) and not any(d in ds_g.sizes for d in glm_dims):
            return issues

        # Dimensional Verification
        for dim in glm_dims:
            c1 = ds_p.sizes.get(dim, 0)
            c2 = ds_g.sizes.get(dim, 0)
            if c1 != c2:
                diff = c2 - c1
                issues.append({
                    "Attribute": f"GLM_Count:{dim}",
                    "Status": "WARNING",
                    "Source1": str(c1),
                    "Source2": f"{c2} ({'+' if diff > 0 else ''}{diff})"
                })

        # ID Verification (Missing vs False Extraneous)
        id_vars = {'number_of_events': 'event_id', 'number_of_groups': 'group_id', 'number_of_flashes': 'flash_id'}
        for dim, var_name in id_vars.items():
            if var_name in ds_p.variables and var_name in ds_g.variables:
                ids1 = set(ds_p[var_name].values)
                ids2 = set(ds_g[var_name].values)

                missing = len(ids1 - ids2)
                false_extra = len(ids2 - ids1)

                if missing > 0 or false_extra > 0:
                    entity_name = var_name.split('_')[0].capitalize()
                    issues.append({
                        "Attribute": f"GLM_Validation:{entity_name}s",
                        "Status": "ERROR",
                        "Source1": f"{missing} missing in Source 2",
                        "Source2": f"{false_extra} extra ('false') in Source 2"
                    })

        # Spatial Representations (Triggered via callback)
        types = ['event', 'group', 'flash']
        for t in types:
            lat_var = f"{t}_lat"
            lon_var = f"{t}_lon"
            if lat_var in ds_p.variables and lon_var in ds_p.variables and lat_var in ds_g.variables and lon_var in ds_g.variables:
                lat1, lon1 = ds_p[lat_var].values, ds_p[lon_var].values
                lat2, lon2 = ds_g[lat_var].values, ds_g[lon_var].values

                if spatial_plot_cb:
                    plot_path = spatial_plot_cb(t, lat1, lon1, lat2, lon2, name1, name2)
                    if plot_path:
                        issues.append({
                            "Attribute": f"GLM_Spatial:{t.capitalize()}s",
                            "Status": "PLOT",
                            "Source1": "See spatial overlay plot",
                            "Source2": "See spatial overlay plot",
                            "Plot": plot_path
                        })

        return issues

    def audit_file_pair(self, uri1, uri2, fs, name1="Source 1", name2="Source 2", diff_plot_cb=None, spatial_plot_cb=None):
        """Full inventory audit directly from S3 using s3fs streaming."""
        if not HAS_XARRAY or not fs:
            return [{"Attribute": "DEPENDENCY", "Status": "ERROR", "Source1": "Missing dependencies", "Source2": "Run 'pip install xarray numpy s3fs h5netcdf'"}]

        file_issues = []
        try:
            with fs.open(uri1, 'rb') as f1, fs.open(uri2, 'rb') as f2:
                with xr.open_dataset(f1, engine='h5netcdf', cache=False) as ds_p, \
                     xr.open_dataset(f2, engine='h5netcdf', cache=False) as ds_g:

                    p_dims = {k: v for k, v in ds_p.sizes.items()}
                    g_dims = {k: v for k, v in ds_g.sizes.items()}
                    file_issues.extend(self.compare_attributes("Dimensions", p_dims, g_dims))

                    file_issues.extend(self.compare_attributes("Global", ds_p.attrs, ds_g.attrs))

                    common_vars = set(ds_p.variables.keys()) & set(ds_g.variables.keys())
                    for var in sorted(common_vars):
                        file_issues.extend(self.compare_attributes(
                            f"Variable:{var}",
                            ds_p.variables[var].attrs,
                            ds_g.variables[var].attrs
                        ))

                    vars_only_in_1 = set(ds_p.variables.keys()) - set(ds_g.variables.keys())
                    vars_only_in_2 = set(ds_g.variables.keys()) - set(ds_p.variables.keys())
                    for v in vars_only_in_1:
                        file_issues.append({"Attribute": f"Variable:{v}", "Status": "ERROR", "Source1": "Present", "Source2": "Missing"})
                    for v in vars_only_in_2:
                        file_issues.append({"Attribute": f"Variable:{v}", "Status": "ERROR", "Source1": "Missing", "Source2": "Present"})

                    for var in sorted(common_vars):
                        try:
                            v1_data = ds_p[var].values
                            v2_data = ds_g[var].values

                            if v1_data.shape != v2_data.shape:
                                file_issues.append({
                                    "Attribute": f"DataPayload:{var}",
                                    "Status": "ERROR",
                                    "Source1": f"Shape {v1_data.shape}",
                                    "Source2": f"Shape {v2_data.shape}"
                                })
                                continue

                            if np.issubdtype(v1_data.dtype, np.number):
                                match = np.allclose(v1_data, v2_data, atol=NUMERIC_TOLERANCE, equal_nan=True)
                            else:
                                match = np.array_equal(v1_data, v2_data)

                            if not match:
                                plot_path = None
                                if np.issubdtype(v1_data.dtype, np.number):
                                    diff_count = np.sum(~np.isclose(v1_data, v2_data, atol=NUMERIC_TOLERANCE, equal_nan=True))
                                    valid_mask = ~(np.isnan(v1_data) | np.isnan(v2_data))
                                    if np.any(valid_mask):
                                        max_diff = np.max(np.abs(v1_data[valid_mask] - v2_data[valid_mask]))
                                        msg_s2 = f"Max Diff: {max_diff:.4e}"
                                    else:
                                        msg_s2 = "NaN mismatches"
                                    msg_s1 = f"{diff_count}/{v1_data.size} elements differ"

                                    if diff_plot_cb:
                                        plot_path = diff_plot_cb(var, v1_data, v2_data, name1, name2)
                                else:
                                    diff_count = np.sum(v1_data != v2_data)
                                    msg_s1 = f"{diff_count}/{v1_data.size} elements differ"
                                    msg_s2 = "Non-numeric mismatch"

                                file_issues.append({
                                    "Attribute": f"DataPayload:{var}",
                                    "Status": "ERROR",
                                    "Source1": msg_s1,
                                    "Source2": msg_s2,
                                    "Plot": plot_path
                                })
                        except Exception as e:
                            file_issues.append({
                                "Attribute": f"DataPayload:{var}",
                                "Status": "ERROR",
                                "Source1": "Read/Compute Error",
                                "Source2": str(e)
                            })

                    file_issues.extend(self._audit_glm_lcfa(ds_p, ds_g, name1, name2, spatial_plot_cb))

        except Exception as e:
            return [{"Attribute": "FILE_READ", "Status": "ERROR", "Source1": str(e), "Source2": "N/A"}]

        return file_issues
