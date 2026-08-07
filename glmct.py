#!/usr/bin/env python3
"""
glm head-to-head
Compares sparse imagery datasets across two S3 buckets using SSO authentication,
a configuration file, dynamic date-based prefix routing, and deep NetCDF metadata comparison
powered by xarray (META-PAVE engine), outputting to a Landscape Word Doc with 1x3 Spatial Plots.
"""
import base_utils
import aws_utils
import json
import sys
import argparse
import tempfile
import os
import csv
from datetime import datetime, timezone

try:
    import numpy as np
    import xarray as xr
    HAS_XARRAY = True
except ImportError:
    HAS_XARRAY = False

try:
    import s3fs
    HAS_S3FS = True
except ImportError:
    HAS_S3FS = False

try:
    import docx
    from docx.shared import Inches
    from docx.enum.section import WD_ORIENT
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import matplotlib
    matplotlib.use('Agg') # Use headless backend so it doesn't pop up windows
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False


# =============================================================================
# META-PAVE CONFIGURATION
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

def generate_spatial_plot(entity_name, lat1, lon1, lat2, lon2, name1, name2):
    """Generates a 1x3 spatial subplot mapping Source 1, Differences, and Source 2."""
    if not HAS_MPL: return None
    try:
        # Mask out NaNs/Fill Values
        mask1 = ~(np.isnan(lat1) | np.isnan(lon1))
        lat1, lon1 = lat1[mask1], lon1[mask1]

        mask2 = ~(np.isnan(lat2) | np.isnan(lon2))
        lat2, lon2 = lat2[mask2], lon2[mask2]

        if len(lat1) == 0 and len(lat2) == 0:
            return None

        fig = plt.figure(figsize=(12, 4))

        # Calculate a common bounding box for all 3 subplots
        all_lats = np.concatenate([lat1, lat2])
        all_lons = np.concatenate([lon1, lon2])
        if len(all_lats) > 0:
            min_lat, max_lat = np.min(all_lats) - 2, np.max(all_lats) + 2
            min_lon, max_lon = np.min(all_lons) - 2, np.max(all_lons) + 2
        else:
            min_lat, max_lat, min_lon, max_lon = -90, 90, -180, 180

        extent = [min_lon, max_lon, min_lat, max_lat]

        axes = []
        # Setup Map Projections and Features if Cartopy is available
        if HAS_CARTOPY:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i, projection=ccrs.PlateCarree())
                ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
                ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=':')
                ax.add_feature(cfeature.STATES, linewidth=0.3, linestyle=':')

                gl = ax.gridlines(draw_labels=True, linestyle='--', alpha=0.5)
                gl.top_labels = False
                gl.right_labels = False
                if i > 1:
                    gl.left_labels = False # Cleaner look for side-by-side maps
                ax.set_extent(extent, crs=ccrs.PlateCarree())
                axes.append(ax)
            scatter_kwargs = {'transform': ccrs.PlateCarree()}
        else:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.set_xlim([min_lon, max_lon])
                ax.set_ylim([min_lat, max_lat])
                if i == 1:
                    ax.set_ylabel("Latitude")
                ax.set_xlabel("Longitude")
                axes.append(ax)
            scatter_kwargs = {}

        # Plot 1 (Left): Source 1
        axes[0].set_title(f"{name1}")
        if len(lat1) > 0:
            axes[0].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', **scatter_kwargs)

        # Plot 2 (Center): Overlay / Differences
        axes[1].set_title("Overlay / Differences")
        if len(lat1) > 0:
            axes[1].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', label=name1, **scatter_kwargs)
        if len(lat2) > 0:
            axes[1].scatter(lon2, lat2, color='tab:red', s=4, alpha=0.6, marker='x', label=name2, **scatter_kwargs)
        axes[1].legend(loc="upper right", fontsize='small')

        # Plot 3 (Right): Source 2
        axes[2].set_title(f"{name2}")
        if len(lat2) > 0:
            axes[2].scatter(lon2, lat2, color='tab:red', s=4, alpha=0.6, marker='x', **scatter_kwargs)

        fig.suptitle(f"{entity_name.capitalize()}s Spatial Distribution", fontsize=14, y=1.05)

        plt.tight_layout()

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(tmp_fd)
        plt.savefig(tmp_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return tmp_path
    except Exception as e:
        print(f"Warning: Failed to generate spatial plot: {e}")
        return None

class MetadataAuditor:
    """Smart metadata comparison engine adapted from META-PAVE."""

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

    def _audit_glm_lcfa(self, ds_p, ds_g, name1, name2):
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

        # Spatial Representations (1x3 Plots)
        types = ['event', 'group', 'flash']
        for t in types:
            lat_var = f"{t}_lat"
            lon_var = f"{t}_lon"
            if lat_var in ds_p.variables and lon_var in ds_p.variables and lat_var in ds_g.variables and lon_var in ds_g.variables:
                lat1, lon1 = ds_p[lat_var].values, ds_p[lon_var].values
                lat2, lon2 = ds_g[lat_var].values, ds_g[lon_var].values
                plot_path = generate_spatial_plot(t, lat1, lon1, lat2, lon2, name1, name2)
                if plot_path:
                    issues.append({
                        "Attribute": f"GLM_Spatial:{t.capitalize()}s",
                        "Status": "PLOT",
                        "Source1": "See spatial overlay plot",
                        "Source2": "See spatial overlay plot",
                        "Plot": plot_path
                    })

        return issues

    def audit_file_pair(self, uri1, uri2, fs, name1="Source 1", name2="Source 2"):
        """Full inventory audit directly from S3 using s3fs streaming."""
        if not HAS_XARRAY or not HAS_S3FS:
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
                                if np.issubdtype(v1_data.dtype, np.number):
                                    diff_count = np.sum(~np.isclose(v1_data, v2_data, atol=NUMERIC_TOLERANCE, equal_nan=True))
                                    valid_mask = ~(np.isnan(v1_data) | np.isnan(v2_data))
                                    if np.any(valid_mask):
                                        max_diff = np.max(np.abs(v1_data[valid_mask] - v2_data[valid_mask]))
                                        msg_s2 = f"Max Diff: {max_diff:.4e}"
                                    else:
                                        msg_s2 = "NaN mismatches"
                                    msg_s1 = f"{diff_count}/{v1_data.size} elements differ"
                                else:
                                    diff_count = np.sum(v1_data != v2_data)
                                    msg_s1 = f"{diff_count}/{v1_data.size} elements differ"
                                    msg_s2 = "Non-numeric mismatch"

                                file_issues.append({
                                    "Attribute": f"DataPayload:{var}",
                                    "Status": "ERROR",
                                    "Source1": msg_s1,
                                    "Source2": msg_s2
                                })
                        except Exception as e:
                            file_issues.append({
                                "Attribute": f"DataPayload:{var}",
                                "Status": "ERROR",
                                "Source1": "Read/Compute Error",
                                "Source2": str(e)
                            })

                    file_issues.extend(self._audit_glm_lcfa(ds_p, ds_g, name1, name2))

        except Exception as e:
            return [{"Attribute": "FILE_READ", "Status": "ERROR", "Source1": str(e), "Source2": "N/A"}]

        return file_issues


# =============================================================================
# DATASET COMPARATOR CORE
# =============================================================================
def resolve_placeholders(template_str, target_date):
    """Replaces date tags in a string with values from the target_date."""
    if not template_str:
        return ""

    replacements = {
        "<YEAR>": target_date.strftime("%Y"),
        "<YYYY>": target_date.strftime("%Y"),
        "<mon>": target_date.strftime("%b").lower(),
        "<MM>": target_date.strftime("%m"),
        "<DD>": target_date.strftime("%d"),
        "<YYYYMMDD>": target_date.strftime("%Y%m%d"),
        "<DOY>": target_date.strftime("%j"),
        "<HH>": target_date.strftime("%H")
    }

    resolved_str = template_str
    for tag, value in replacements.items():
        resolved_str = resolved_str.replace(tag, value)

    return resolved_str

def compare_datasets(s3_client, ds1_cfg, ds2_cfg, target_date, raw_date_str, ext_filter=None, logger=None):
    """Compares two S3 buckets/prefixes and prints the differences."""
    name1 = resolve_placeholders(ds1_cfg.get('name', 'Source 1'), target_date)
    b1 = ds1_cfg.get('bucket')
    p1 = resolve_placeholders(ds1_cfg.get('prefix', ''), target_date)

    name2 = resolve_placeholders(ds2_cfg.get('name', 'Source 2'), target_date)
    b2 = ds2_cfg.get('bucket')
    p2 = resolve_placeholders(ds2_cfg.get('prefix', ''), target_date)

    objs1 = aws_utils.get_s3_objects(s3_client, b1, p1, ext_filter, raw_date_str, logger)
    objs2 = aws_utils.get_s3_objects(s3_client, b2, p2, ext_filter, raw_date_str, logger)

    if objs1 is None or objs2 is None:
        if logger: logger.error("Comparison aborted due to bucket access errors.")
        sys.exit(1)

    set1, set2 = set(objs1.keys()), set(objs2.keys())

    only_in_1 = set1 - set2
    only_in_2 = set2 - set1
    common = set1.intersection(set2)

    identical = []
    mismatched = []
    meta_diffs = {}
    all_raw_issues = {} # Store for Word Doc Generation

    auditor = MetadataAuditor()

    csv_report_file = f"glm_comparison_report_{raw_date_str}.csv"
    doc_report_file = f"glm_comparison_report_{raw_date_str}.docx"

    try:
        with open(csv_report_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Normalized Key", f"{name1} Key", f"{name2} Key", "Attribute", "Status", f"{name1} Value", f"{name2} Value"])
    except Exception as e:
        if logger: logger.error(f"Could not create CSV report: {e}")

    credentials = s3_client._request_signer._credentials
    fs = None
    if HAS_S3FS and credentials:
        fs = s3fs.S3FileSystem(
            key=credentials.access_key,
            secret=credentials.secret_key,
            token=credentials.token,
            client_kwargs={'region_name': s3_client.meta.region_name}
        )

    for key in common:
        file1, file2 = objs1[key], objs2[key]

        if file1['Size'] == file2['Size'] and file1['ETag'] == file2['ETag']:
            identical.append(key)
        else:
            mismatched.append({
                'norm_key': key,
                'b1_key': file1['full_key'], 'b2_key': file2['full_key'],
                'b1_size': file1['Size'], 'b2_size': file2['Size'],
                'b1_etag': file1['ETag'], 'b2_etag': file2['ETag']
            })

            if file1['full_key'].lower().endswith('.nc'):
                if logger: logger.info(f"Streaming mismatched file for deep metadata & data validation: {key}")

                uri1 = f"s3://{b1}/{file1['full_key']}"
                uri2 = f"s3://{b2}/{file2['full_key']}"

                try:
                    raw_issues = auditor.audit_file_pair(uri1, uri2, fs, name1, name2)
                    all_raw_issues[key] = raw_issues

                    filtered_diffs = []
                    with open(csv_report_file, 'a', newline='') as f:
                        writer = csv.writer(f)
                        for issue in raw_issues:
                            if issue['Status'] == 'PLOT':
                                filtered_diffs.append(f"[{issue['Status']:<7}] {issue['Attribute']} | Spatial plot generated for {name1} vs {name2}.")
                                continue

                            if issue['Status'] != 'IGNORE':
                                filtered_diffs.append(
                                    f"[{issue['Status']:<7}] {issue['Attribute']} | {name1}: {issue['Source1']} | {name2}: {issue['Source2']}"
                                )
                                writer.writerow([
                                    key, file1['full_key'], file2['full_key'],
                                    issue['Attribute'], issue['Status'],
                                    issue['Source1'], issue['Source2']
                                ])

                    if filtered_diffs:
                        meta_diffs[key] = filtered_diffs
                    else:
                        meta_diffs[key] = ["No internal differences found. Size/ETag mismatch may be due to NetCDF chunking differences."]
                except Exception as e:
                    meta_diffs[key] = [f"[ERROR  ] S3 Stream/Compare failed: {e}"]

    # --- Print Summary ---
    print("\n" + "="*80)
    print(f" COMPARISON RESULTS (Date Filter: {raw_date_str})")
    print("="*80)
    print(f"{name1}: s3://{b1}/{p1}")
    print(f"{name2}: s3://{b2}/{p2}")
    if ext_filter: print(f"Filtered by: {ext_filter}")
    print("-" * 80)

    print(f"Total files in {name1}: {len(objs1)}")
    print(f"Total files in {name2}: {len(objs2)}")
    print(f"Files ONLY in {name1}:  {len(only_in_1)}")
    print(f"Files ONLY in {name2}:  {len(only_in_2)}")
    print(f"Files in BOTH (Names):   {len(common)}")
    print(f"  -> Identical Content:  {len(identical)}")
    print(f"  -> Mismatched Content: {len(mismatched)}")
    print("="*80)

    # --- Generate Streamlined Word Document Report (Plots Only) ---
    if HAS_DOCX and mismatched:
        if logger: logger.info("Generating Word Document report with spatial plots...")
        try:
            doc = docx.Document()

            # ---> SET TO LANDSCAPE & ADJUST MARGINS <---
            for section in doc.sections:
                new_width, new_height = section.page_height, section.page_width
                section.orientation = WD_ORIENT.LANDSCAPE
                section.page_width = new_width
                section.page_height = new_height

                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)

            doc.add_heading('GLM Dataset Spatial Comparison Report', 0)

            doc.add_heading('Summary', level=1)
            doc.add_paragraph(f"Date Filter: {raw_date_str}")
            doc.add_paragraph(f"{name1}: s3://{b1}/{p1}")
            doc.add_paragraph(f"{name2}: s3://{b2}/{p2}")
            doc.add_paragraph(f"Mismatched Files Analysed: {len(mismatched)}")

            doc.add_heading('Mismatched Files Spatial Analysis', level=1)
            plots_to_delete = []

            for m in mismatched:
                k = m['norm_key']
                issues = all_raw_issues.get(k, [])

                # Filter strictly for spatial plots to keep report streamlined
                spatial_issues = [i for i in issues if i.get('Status') == 'PLOT' and 'GLM_Spatial' in i.get('Attribute', '')]

                if spatial_issues:
                    doc.add_heading(f"{m['b1_key']}", level=2)
                    for issue in spatial_issues:
                        plot_path = issue.get('Plot')
                        if plot_path and os.path.exists(plot_path):
                            p = doc.add_paragraph()
                            p.add_run(f"{issue.get('Attribute').split(':')[-1]}").bold = True

                            # Expand to 9 inches wide to fully utilize the landscape layout
                            doc.add_picture(plot_path, width=Inches(9.0))
                            plots_to_delete.append(plot_path)

            doc.save(doc_report_file)
            print(f"\nDetailed reports generated:\n  - {csv_report_file} (Metadata Diff)\n  - {doc_report_file} (Spatial Plots)")

            for p in plots_to_delete:
                try: os.unlink(p)
                except: pass

        except Exception as e:
            if logger: logger.error(f"Failed to generate Word doc: {e}")
            print(f"\nDetailed CSV report generated: {csv_report_file}")
    else:
        if mismatched:
            print(f"\nDetailed CSV report generated: {csv_report_file}")
            if not HAS_DOCX or not HAS_MPL:
                print("(Note: Install 'python-docx' and 'matplotlib' to generate a graphic Word document report)")


if __name__ == "__main__":
    logger = base_utils.Logger(level="INFO")

    parser = argparse.ArgumentParser(description="Compare imagery datasets with dynamic date placeholders.")
    parser.add_argument(
        "--date",
        type=str,
        default=datetime.now(timezone.utc).strftime("%Y%j%H"),
        help="Target date/time in YYYYDOY[HH[MM[SS]]] format (defaults to current UTC hour)."
    )
    args = parser.parse_args()

    raw_date = args.date
    try:
        if len(raw_date) == 7: target_date = datetime.strptime(raw_date, "%Y%j")
        elif len(raw_date) == 9: target_date = datetime.strptime(raw_date, "%Y%j%H")
        elif len(raw_date) == 11: target_date = datetime.strptime(raw_date, "%Y%j%H%M")
        elif len(raw_date) == 13: target_date = datetime.strptime(raw_date, "%Y%j%H%M%S")
        else: raise ValueError()
    except ValueError:
        logger.error(f"Invalid date format for '{raw_date}'. Please use YYYYDOY, YYYYDOYHH, YYYYDOYHHMM, or YYYYDOYHHMMSS.")
        sys.exit(1)

    config_file = "config.json"
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {config_file}: {e}")
        sys.exit(1)

    data_sources = config.get("data_sources", {})

    dataset_1 = data_sources.get("dataset_1")
    dataset_2 = data_sources.get("dataset_2")
    ext_filter = data_sources.get("extension_filter")

    if not dataset_1 or not dataset_2 or 'bucket' not in dataset_1 or 'bucket' not in dataset_2:
        logger.error("Configuration must contain 'dataset_1' and 'dataset_2' inside 'data_sources', both with a 'bucket' key.")
        sys.exit(1)

    logger.info("Initializing SSO Connection...")
    s3_connector = aws_utils.S3SSOConnector(config_path=config_file)

    logger.info(f"Starting dataset comparison for date filter: {raw_date}...")
    compare_datasets(
        s3_client=s3_connector.s3_client,
        ds1_cfg=dataset_1,
        ds2_cfg=dataset_2,
        target_date=target_date,
        raw_date_str=raw_date,
        ext_filter=ext_filter,
        logger=logger
    )
