#!/usr/bin/env python3
"""
GOES-R Dynamic Head-to-Head Comparator

Dynamically discovers product directories using wildcards, identifies the latest
matching pair of files between two S3 buckets, and performs deep NetCDF metadata
and data validation (verifying internal data despite compression differences).
Outputs results to a consolidated CSV and a landscape Word Doc.
"""
import argparse
import sys
import os
import json
import csv
import tempfile
import fnmatch
import re
from datetime import datetime, timezone

# Internal GCCS Modules
import base_utils
import aws_utils
import goes_aws
import meta_utils

try:
    import s3fs
    HAS_S3FS = True
except ImportError:
    HAS_S3FS = False

try:
    import docx
    from docx.shared import Inches, Pt
    from docx.enum.section import WD_ORIENT
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import numpy as np
    import matplotlib
    matplotlib.use('Agg') # Headless plotting
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
# REPORTING CALLBACKS
# =============================================================================
def generate_diff_plot(var_name, v1_data, v2_data, name1, name2):
    """Generates a graphical plot of the numeric differences between two arrays."""
    if not HAS_MPL: return None
    try:
        v1_arr = np.asanyarray(v1_data)
        v2_arr = np.asanyarray(v2_data)

        if v1_arr.shape != v2_arr.shape:
            return None

        # Only plot 2D variables
        if v1_arr.ndim != 2:
            return None

        # --- 2D Array (Image) Visualization ---
        # Downsample massive arrays (like ABI Full Disk) to avoid MemoryErrors in matplotlib
        if v1_arr.shape[0] > 1500 or v1_arr.shape[1] > 1500:
            skip_y = max(1, v1_arr.shape[0] // 1000)
            skip_x = max(1, v1_arr.shape[1] // 1000)
            v1_arr = v1_arr[::skip_y, ::skip_x]
            v2_arr = v2_arr[::skip_y, ::skip_x]

        diff = v1_arr.astype(float) - v2_arr.astype(float)

        fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))

        im0 = axes[0].imshow(v1_arr, cmap='viridis', aspect='auto')
        axes[0].set_title(f"{name1}")
        fig.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)

        vmax = np.nanmax(np.abs(diff))
        if vmax == 0 or np.isnan(vmax): vmax = 1
        im1 = axes[1].imshow(diff, cmap='coolwarm', vmin=-vmax, vmax=vmax, aspect='auto')
        axes[1].set_title("Delta (Difference Locations)")
        fig.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)

        im2 = axes[2].imshow(v2_arr, cmap='viridis', aspect='auto')
        axes[2].set_title(f"{name2}")
        fig.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

        fig.suptitle(f"{var_name} Mismatch (2D View)", fontsize=12, y=1.05)
        plt.tight_layout()

        # Save Plot
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(tmp_fd)
        plt.savefig(tmp_path, dpi=150, bbox_inches='tight')
        plt.close()
        return tmp_path

    except Exception as e:
        print(f"Warning: Failed to generate diff plot: {e}")
        return None

def generate_spatial_plot(entity_name, lat1, lon1, lat2, lon2, name1, name2):
    """Generates a 1x3 spatial subplot mapping Source 1, Differences, and Source 2."""
    if not HAS_MPL: return None
    try:
        mask1 = ~(np.isnan(lat1) | np.isnan(lon1))
        lat1, lon1 = lat1[mask1], lon1[mask1]

        mask2 = ~(np.isnan(lat2) | np.isnan(lon2))
        lat2, lon2 = lat2[mask2], lon2[mask2]

        if len(lat1) == 0 and len(lat2) == 0:
            return None

        fig = plt.figure(figsize=(12, 4))

        all_lats = np.concatenate([lat1, lat2])
        all_lons = np.concatenate([lon1, lon2])
        if len(all_lats) > 0:
            min_lat, max_lat = np.min(all_lats) - 2, np.max(all_lats) + 2
            min_lon, max_lon = np.min(all_lons) - 2, np.max(all_lons) + 2
        else:
            min_lat, max_lat, min_lon, max_lon = -90, 90, -180, 180

        extent = [min_lon, max_lon, min_lat, max_lat]
        axes = []

        if HAS_CARTOPY:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i, projection=ccrs.PlateCarree())
                ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
                ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=':')
                ax.add_feature(cfeature.STATES, linewidth=0.3, linestyle=':')
                gl = ax.gridlines(draw_labels=True, linestyle='--', alpha=0.5)
                gl.top_labels = False
                gl.right_labels = False
                if i > 1: gl.left_labels = False
                ax.set_extent(extent, crs=ccrs.PlateCarree())
                axes.append(ax)
            scatter_kwargs = {'transform': ccrs.PlateCarree()}
        else:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.set_xlim([min_lon, max_lon])
                ax.set_ylim([min_lat, max_lat])
                if i == 1: ax.set_ylabel("Latitude")
                ax.set_xlabel("Longitude")
                axes.append(ax)
            scatter_kwargs = {}

        axes[0].set_title(f"{name1}")
        if len(lat1) > 0:
            axes[0].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', **scatter_kwargs)

        axes[1].set_title("Overlay / Differences")
        if len(lat1) > 0:
            axes[1].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', label=name1, **scatter_kwargs)
        if len(lat2) > 0:
            axes[1].scatter(lon2, lat2, color='tab:red', s=4, alpha=0.6, marker='x', label=name2, **scatter_kwargs)
        axes[1].legend(loc="upper right", fontsize='small')

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


# =============================================================================
# DATASET COMPARATOR CORE
# =============================================================================
def resolve_placeholders(template_str, target_date, product_name, sat=None, level=None, instr=None):
    """Replaces date, product, and optional metadata tags in a string."""
    if not template_str:
        return ""

    replacements = {
        "<PRODUCT>": product_name,
        "<YEAR>": target_date.strftime("%Y"),
        "<YYYY>": target_date.strftime("%Y"),
        "<mon>": target_date.strftime("%b").lower(),
        "<MM>": target_date.strftime("%m"),
        "<DD>": target_date.strftime("%d"),
        "<YYYYMMDD>": target_date.strftime("%Y%m%d"),
        "<DOY>": target_date.strftime("%j"),
        "<HH>": target_date.strftime("%H")
    }

    if sat:
        replacements["<SAT>"] = sat
    if level:
        replacements["<LEVEL>"] = level
    if instr:
        replacements["<INSTR>"] = instr

    resolved_str = template_str
    for tag, value in replacements.items():
        resolved_str = resolved_str.replace(tag, value)

    return resolved_str

def discover_products(s3_client, ds_cfg, target_date, product_pattern, sat, level, instr, logger):
    """
    Crawls S3 to find physical directory names matching the wildcard pattern.
    Assumes <PRODUCT> placeholder dictates the directory structure.
    """
    template = ds_cfg.get('prefix', '')
    if '<PRODUCT>' not in template:
        logger.warn("No <PRODUCT> placeholder found in prefix template. Using literal pattern.")
        return [product_pattern] if '*' not in product_pattern and '?' not in product_pattern else []

    # Find the base prefix path up to the <PRODUCT> placeholder
    parts = template.split('<PRODUCT>')
    base_template = parts[0]
    base_prefix = resolve_placeholders(base_template, target_date, "", sat, level, instr)

    logger.info(f"Crawling S3 CommonPrefixes under: s3://{ds_cfg['bucket']}/{base_prefix}")

    paginator = s3_client.get_paginator('list_objects_v2')
    products = []

    try:
        # Delimiter '/' allows us to list top-level directories under the base prefix
        pages = paginator.paginate(Bucket=ds_cfg['bucket'], Prefix=base_prefix, Delimiter='/')
        for page in pages:
            for pfx in page.get('CommonPrefixes', []):
                # Extract the directory name that directly replaces <PRODUCT>
                sub_prefix = pfx['Prefix'][len(base_prefix):].strip('/')

                # Check if it matches the user's wildcard pattern
                if sub_prefix and fnmatch.fnmatch(sub_prefix, product_pattern):
                    products.append(sub_prefix)
        return sorted(products)
    except Exception as e:
        logger.error(f"Failed to discover products: {e}")
        return []

def execute_single_comparison(s3_client, ds1_cfg, ds2_cfg, target_date, product_name, ext_filter, fs, auditor, sat, level, instr, logger):
    """
    Fetches the lists of objects for a single product, identifies the LATEST matching pair,
    and runs a deep inspection against that pair.
    """
    name1 = resolve_placeholders(ds1_cfg.get('name', 'Source 1'), target_date, product_name, sat, level, instr)
    b1 = ds1_cfg.get('bucket')
    p1 = resolve_placeholders(ds1_cfg.get('prefix', ''), target_date, product_name, sat, level, instr)

    name2 = resolve_placeholders(ds2_cfg.get('name', 'Source 2'), target_date, product_name, sat, level, instr)
    b2 = ds2_cfg.get('bucket')
    p2 = resolve_placeholders(ds2_cfg.get('prefix', ''), target_date, product_name, sat, level, instr)

    logger.verbose(f"Scanning s3://{b1}/{p1}")
    objs1 = goes_aws.get_goes_s3_objects(s3_client, b1, p1, ext_filter, None, logger=None)

    logger.verbose(f"Scanning s3://{b2}/{p2}")
    objs2 = goes_aws.get_goes_s3_objects(s3_client, b2, p2, ext_filter, None, logger=None)

    if objs1 is None or objs2 is None:
        logger.warn(f"Failed to retrieve objects for product {product_name}.")
        return None

    common = set(objs1.keys()).intersection(set(objs2.keys()))
    if not common:
        logger.warn(f"No common files found for {product_name} on {target_date.strftime('%Y%j')}.")
        return None

    # Isolate a safe file (prefer second-to-last to avoid in-progress uploads)
    sorted_common = sorted(list(common))
    if len(sorted_common) >= 2:
        target_key = sorted_common[-2]
        logger.info(f"[{product_name}] Processing second-to-last pair (safe from upload collision): {target_key}")
    else:
        target_key = sorted_common[-1]
        logger.warn(f"[{product_name}] Only one matching pair found. Proceeding with absolute latest (could be actively uploading): {target_key}")

    file1 = objs1[target_key]
    file2 = objs2[target_key]

    result = {
        'product_name': product_name,
        'norm_key': target_key,
        'b1_key': file1['full_key'], 'b2_key': file2['full_key'],
        'b1_size': file1['Size'], 'b2_size': file2['Size'],
        'identical_bytes': False,
        'issues': []
    }

    # Calculate percent change in file size
    if file1['Size'] > 0:
        pct_change = ((file2['Size'] - file1['Size']) / file1['Size']) * 100
        result['size_pct_change'] = f"{pct_change:+.2f}%"
    elif file2['Size'] > 0:
        result['size_pct_change'] = "+inf%"
    else:
        result['size_pct_change'] = "0.00%"

    # Verify if compression altered the byte footprint
    if file1['Size'] == file2['Size'] and file1['ETag'] == file2['ETag']:
        result['identical_bytes'] = True
        logger.verbose(f" -> {product_name}: Files are bit-for-bit identical.")
    else:
        logger.verbose(f" -> {product_name}: Compression difference detected (S1: {file1['Size']} bytes | S2: {file2['Size']} bytes). Running deep scan...")
        if fs:
            uri1, uri2 = f"s3://{b1}/{file1['full_key']}", f"s3://{b2}/{file2['full_key']}"
            try:
                raw_issues = auditor.audit_file_pair(
                    uri1, uri2, fs, name1, name2,
                    diff_plot_cb=generate_diff_plot,
                    spatial_plot_cb=generate_spatial_plot
                )
                result['issues'] = raw_issues
            except Exception as e:
                logger.error(f"Deep scan failed for {target_key}: {e}")
                result['issues'] = [{"Attribute": "FILE_READ", "Status": "ERROR", "Source1": str(e), "Source2": "N/A"}]
        else:
            result['issues'] = [{"Attribute": "SYSTEM", "Status": "WARNING", "Source1": "Missing s3fs", "Source2": "Cannot verify internal data array match."}]

    return result

def parse_args():
    parser = argparse.ArgumentParser(description="Dynamically crawl and compare the latest GOES-R product files.")
    parser.add_argument("product", type=str, help="Product name or wildcard (e.g., ABI-L1b-RadF, GLM-*, *)")
    parser.add_argument(
        "timestamp",
        type=str,
        nargs="?",
        default=datetime.now(timezone.utc).strftime("%Y%j"),
        help="Target date/time in YYYYDOY[HH[MM[SS]]] format (defaults to current UTC DOY)"
    )
    parser.add_argument("--sat", type=str, default=None, help="Optional satellite identifier (e.g., G16, G17) for <SAT> placeholder")
    parser.add_argument("--level", type=str, default=None, help="Optional data processing level (e.g., L1b, L2) for <LEVEL> placeholder")
    parser.add_argument("--instr", type=str, default=None, help="Optional instrument identifier (e.g., ABI, GLM) for <INSTR> placeholder")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file (default: config.json)")
    parser.add_argument("--tolerance", type=float, default=0.0001, help="Numeric tolerance for array diffs (default: 0.0001)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal logging")
    return parser.parse_args()


def main():
    args = parse_args()

    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    logger = base_utils.Logger(lvl)
    base_utils.setup_interrupt_handler(logger)

    raw_date = args.timestamp
    try:
        if len(raw_date) == 7: target_date = datetime.strptime(raw_date, "%Y%j")
        elif len(raw_date) == 9: target_date = datetime.strptime(raw_date, "%Y%j%H")
        elif len(raw_date) == 11: target_date = datetime.strptime(raw_date, "%Y%j%H%M")
        elif len(raw_date) == 13: target_date = datetime.strptime(raw_date, "%Y%j%H%M%S")
        else: raise ValueError()
    except ValueError:
        logger.error(f"Invalid date format for '{raw_date}'. Please use YYYYDOY, YYYYDOYHH, YYYYDOYHHMM, or YYYYDOYHHMMSS.")
        sys.exit(1)

    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {args.config}: {e}")
        sys.exit(1)

    data_sources = config.get("data_sources", {})
    dataset_1 = data_sources.get("dataset_1")
    dataset_2 = data_sources.get("dataset_2")
    ext_filter = data_sources.get("extension_filter", ".nc")

    if not dataset_1 or not dataset_2 or 'bucket' not in dataset_1 or 'bucket' not in dataset_2:
        logger.error("Configuration must contain 'dataset_1' and 'dataset_2' inside 'data_sources'.")
        sys.exit(1)

    logger.info("Initializing AWS SSO Connection...")
    s3_connector = aws_utils.S3SSOConnector(config_path=args.config)

    # 1. Product Discovery
    is_wildcard = '*' in args.product or '?' in args.product
    if is_wildcard:
        logger.info(f"Wildcard detected. Searching for product folders matching '{args.product}'...")
        product_list = discover_products(s3_connector.s3_client, dataset_1, target_date, args.product, args.sat, args.level, args.instr, logger)
        logger.info(f"Discovered {len(product_list)} matching products.")
    else:
        product_list = [args.product]

    if not product_list:
        logger.error("No products to process. Exiting.")
        sys.exit(0)

    # 2. Setup S3FS for deep inspection
    credentials = s3_connector.s3_client._request_signer._credentials
    fs = None
    if HAS_S3FS and credentials:
        fs = s3fs.S3FileSystem(
            key=credentials.access_key,
            secret=credentials.secret_key,
            token=credentials.token,
            client_kwargs={'region_name': s3_connector.s3_client.meta.region_name}
        )

    auditor = meta_utils.MetadataAuditor(tolerance=args.tolerance)
    results = []

    print("\n" + "="*80)
    print(f" CRAWLING DATASETS: {args.product} | LATEST FILE ONLY | DOY: {raw_date}")
    print("="*80)

    # 3. Process each discovered product
    for prod in product_list:
        res = execute_single_comparison(
            s3_client=s3_connector.s3_client,
            ds1_cfg=dataset_1, ds2_cfg=dataset_2,
            target_date=target_date, product_name=prod,
            ext_filter=ext_filter, fs=fs, auditor=auditor,
            sat=args.sat, level=args.level, instr=args.instr, logger=logger
        )
        if res:
            results.append(res)

    if not results:
        logger.warn("No files could be compared across any of the selected products.")
        sys.exit(0)

    # 4. Check for Mismatches Before Generating Files
    has_mismatches = False
    for res in results:
        if not res.get('identical_bytes', False):
            # Filter out IGNORE-level metadata differences
            reportable_issues = [i for i in res.get('issues', []) if i.get('Status') != 'IGNORE']
            if reportable_issues:
                has_mismatches = True
                break

    if not has_mismatches:
        print("\n" + "="*80)
        print(f" SUCCESS: All {len(results)} processed product(s) perfectly match!")
        print(" Internal data arrays and attributes are identical. No reports generated.")
        print("="*80 + "\n")
        sys.exit(0)

    # 5. Consolidate Reports
    logger.info("Generating consolidated reports...")

    # Extract satellite ID (e.g., G16, G17, G18) from the first processed file
    sat_id = "GOES"
    for res in results:
        match = re.search(r'_(G\d{2})_', res.get('norm_key', ''))
        if match:
            sat_id = match.group(1)
            break

    base_name = args.product.replace('*', 'ALL').replace('?', 'X')
    csv_report_file = f"{sat_id}_{base_name}_comparison_{raw_date}.csv"
    doc_report_file = f"{sat_id}_{base_name}_comparison_{raw_date}.docx"

    with open(csv_report_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Product", "Normalized Key", "Source 1 Size", "Source 2 Size", "Size % Change", "Attribute", "Status", "Source 1 Value", "Source 2 Value"])

        for res in results:
            b1_size = res.get('b1_size', 'N/A')
            b2_size = res.get('b2_size', 'N/A')
            pct = res.get('size_pct_change', 'N/A')

            if res['identical_bytes']:
                writer.writerow([res['product_name'], res['norm_key'], b1_size, b2_size, pct, "Entire File", "IDENTICAL", "Bit-for-bit Match", "Bit-for-bit Match"])
            else:
                if not res['issues']:
                    # If sizes differ but xarray finds no issues, log that the data arrays match
                    writer.writerow([res['product_name'], res['norm_key'], b1_size, b2_size, pct, "Data Payload", "MATCH", "Internal arrays identical", "Internal arrays identical"])
                for issue in res['issues']:
                    if issue['Status'] not in ['IGNORE', 'PLOT']:
                        writer.writerow([
                            res['product_name'], res['norm_key'], b1_size, b2_size, pct,
                            issue.get('Attribute',''), issue.get('Status',''),
                            issue.get('Source1',''), issue.get('Source2','')
                        ])

    if HAS_DOCX:
        try:
            doc = docx.Document()
            for section in doc.sections:
                new_width, new_height = section.page_height, section.page_width
                section.orientation = WD_ORIENT.LANDSCAPE
                section.page_width, section.page_height = new_width, new_height
                section.top_margin, section.bottom_margin = Inches(1), Inches(1)
                section.left_margin, section.right_margin = Inches(1), Inches(1)

            doc.add_heading(f'GOES-R Dynamic Comparison Report: {args.product}', 0)
            doc.add_heading('Summary', level=1)
            doc.add_paragraph(f"Date Filter: {raw_date}")
            doc.add_paragraph(f"Products Analyzed: {len(results)}")

            plots_to_delete = []

            for res in results:
                if res['identical_bytes'] or not res['issues']:
                    continue

                table_issues = [i for i in res['issues'] if i.get('Status') not in ['IGNORE', 'PLOT']]
                plot_issues = [i for i in res['issues'] if i.get('Plot')]

                if not table_issues and not plot_issues:
                    continue

                doc.add_heading(f"{res['product_name']}", level=2)
                doc.add_paragraph(f"Latest File: {res['norm_key']}")

                b1_size = res.get('b1_size', 'N/A')
                b2_size = res.get('b2_size', 'N/A')
                pct = res.get('size_pct_change', 'N/A')

                p_size = doc.add_paragraph()
                p_size.add_run("Source 1 Size: ").bold = True
                p_size.add_run(f"{b1_size} bytes | ")
                p_size.add_run("Source 2 Size: ").bold = True
                p_size.add_run(f"{b2_size} bytes | ")
                p_size.add_run("Size Change: ").bold = True
                p_size.add_run(f"{pct}")

                if table_issues:
                    table = doc.add_table(rows=1, cols=4)
                    table.style = 'Light Shading Accent 1'
                    hdr_cells = table.rows[0].cells
                    hdr_cells[0].text, hdr_cells[1].text = 'Status', 'Attribute'
                    hdr_cells[2].text, hdr_cells[3].text = 'Source 1', 'Source 2'

                    for issue in table_issues:
                        row_cells = table.add_row().cells
                        row_cells[0].text = issue.get('Status', '')
                        row_cells[1].text = issue.get('Attribute', '')
                        row_cells[2].text = str(issue.get('Source1', ''))
                        row_cells[3].text = str(issue.get('Source2', ''))

                for issue in plot_issues:
                    plot_path = issue.get('Plot')
                    if plot_path and os.path.exists(plot_path):
                        p = doc.add_paragraph()
                        p.add_run(f"\n{issue.get('Attribute')} Spatial/Diff Plot:").bold = True
                        doc.add_picture(plot_path, width=Inches(9.0))
                        plots_to_delete.append(plot_path)

            doc.save(doc_report_file)
            print(f"\nConsolidated reports generated:\n  - {csv_report_file}\n  - {doc_report_file}")

            for p in plots_to_delete:
                try: os.unlink(p)
                except: pass

        except Exception as e:
            logger.error(f"Failed to generate Word doc: {e}")
            print(f"\nConsolidated CSV report generated: {csv_report_file}")
    else:
        print(f"\nConsolidated CSV report generated: {csv_report_file}")

if __name__ == "__main__":
    main()
