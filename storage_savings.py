#!/usr/bin/env python3
"""
GOES Storage Savings Analyzer

Quickly scans and compares file sizes across two S3 datasets for a specific
product or an entire day's worth of products. Outputs a table of total
storage savings and average file deltas.
"""
import argparse
import sys
import json
import fnmatch
from datetime import datetime, timezone

import base_utils
import aws_utils
import goes_aws

def format_bytes(size):
    """Converts bytes to human-readable format."""
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB', 5: 'PB'}
    while size > power and n < 5:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

def format_bytes_signed(size):
    """Converts bytes to human-readable format with a +/- prefix."""
    sign = "+" if size >= 0 else "-"
    return f"{sign}{format_bytes(abs(size))}"

def resolve_placeholders(template_str, target_date, product_name, sat=None, level=None, instr=None):
    """Replaces dynamic tags in a string."""
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

    if sat: replacements["<SAT>"] = sat
    if level: replacements["<LEVEL>"] = level
    if instr: replacements["<INSTR>"] = instr

    resolved_str = template_str
    for tag, value in replacements.items():
        resolved_str = resolved_str.replace(tag, value)

    return resolved_str

def discover_products(s3_client, ds_cfg, target_date, product_pattern, sat, level, instr, logger):
    """Crawls S3 to find directory names matching the wildcard pattern."""
    template = ds_cfg.get('prefix', '')
    if '<PRODUCT>' not in template:
        logger.warn("No <PRODUCT> placeholder found in prefix template. Using literal pattern.")
        return [product_pattern] if '*' not in product_pattern and '?' not in product_pattern else []

    parts = template.split('<PRODUCT>')
    base_template = parts[0]
    base_prefix = resolve_placeholders(base_template, target_date, "", sat, level, instr)

    logger.info(f"Discovering products under: s3://{ds_cfg['bucket']}/{base_prefix}")

    paginator = s3_client.get_paginator('list_objects_v2')
    products = []

    try:
        pages = paginator.paginate(Bucket=ds_cfg['bucket'], Prefix=base_prefix, Delimiter='/')
        for page in pages:
            for pfx in page.get('CommonPrefixes', []):
                sub_prefix = pfx['Prefix'][len(base_prefix):].strip('/')
                if sub_prefix and fnmatch.fnmatch(sub_prefix, product_pattern):
                    products.append(sub_prefix)
        return sorted(products)
    except Exception as e:
        logger.error(f"Failed to discover products: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="Analyze storage savings between two GOES datasets.")
    parser.add_argument("product", type=str, help="Product name or wildcard (e.g., ABI-L1b-RadF, GLM-*, *)")
    parser.add_argument(
        "timestamp",
        type=str,
        nargs="?",
        default=datetime.now(timezone.utc).strftime("%Y%j"),
        help="Target date/time in YYYYDOY format (defaults to current UTC DOY)"
    )
    parser.add_argument("--sat", type=str, default=None, help="Optional satellite ID (e.g., G16)")
    parser.add_argument("--level", type=str, default=None, help="Optional processing level (e.g., L1b)")
    parser.add_argument("--instr", type=str, default=None, help="Optional instrument ID (e.g., ABI)")
    args = parser.parse_args()

    logger = base_utils.Logger(level="INFO")
    base_utils.setup_interrupt_handler(logger)

    # Parse Date
    raw_date = args.timestamp
    try:
        if len(raw_date) == 7: target_date = datetime.strptime(raw_date, "%Y%j")
        elif len(raw_date) == 9: target_date = datetime.strptime(raw_date, "%Y%j%H")
        elif len(raw_date) >= 11: target_date = datetime.strptime(raw_date[:11], "%Y%j%H%M")
        else: raise ValueError()
    except ValueError:
        logger.error(f"Invalid date format: {raw_date}. Use YYYYDOY or YYYYDOYHH.")
        sys.exit(1)

    # Load Config
    config_file = "config.json"
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {config_file}: {e}")
        sys.exit(1)

    ds1_cfg = config.get("data_sources", {}).get("dataset_1")
    ds2_cfg = config.get("data_sources", {}).get("dataset_2")
    ext_filter = config.get("data_sources", {}).get("extension_filter", ".nc")

    if not ds1_cfg or not ds2_cfg:
        logger.error("Config must contain 'dataset_1' and 'dataset_2' in 'data_sources'.")
        sys.exit(1)

    # Init SSO
    logger.info("Initializing SSO Connection...")
    s3_connector = aws_utils.S3SSOConnector(config_path=config_file)
    s3_client = s3_connector.s3_client

    # Discover Products
    is_wildcard = '*' in args.product or '?' in args.product
    if is_wildcard:
        product_list = discover_products(s3_client, ds1_cfg, target_date, args.product, args.sat, args.level, args.instr, logger)
    else:
        product_list = [args.product]

    if not product_list:
        logger.error(f"No products found matching '{args.product}'.")
        sys.exit(0)

    b1_name = ds1_cfg.get('bucket', 'Unknown')
    b2_name = ds2_cfg.get('bucket', 'Unknown')

    print("\n" + "="*100)
    print(f" STORAGE SAVINGS ANALYSIS | Date: {raw_date} | Products: {len(product_list)}")
    print(f" Source 1 (Src1): s3://{b1_name}")
    print(f" Source 2 (Src2): s3://{b2_name}")
    print("="*100)
    print(f"{'Product':<30} | {'Matched':<7} | {'Src1 Size':<10} | {'Src2 Size':<10} | {'Savings':<11} | {'% Saved':<8} | {'Avg Delta'}")
    print("-" * 100)

    grand_count = 0
    grand_s1 = 0
    grand_s2 = 0

    # Process Products
    for prod in product_list:
        p1 = resolve_placeholders(ds1_cfg.get('prefix', ''), target_date, prod, args.sat, args.level, args.instr)
        b1 = ds1_cfg.get('bucket')
        p2 = resolve_placeholders(ds2_cfg.get('prefix', ''), target_date, prod, args.sat, args.level, args.instr)
        b2 = ds2_cfg.get('bucket')

        # Fetch quietly
        objs1 = goes_aws.get_goes_s3_objects(s3_client, b1, p1, ext_filter, None, logger=None)
        objs2 = goes_aws.get_goes_s3_objects(s3_client, b2, p2, ext_filter, None, logger=None)

        if not objs1 or not objs2:
            continue

        common = set(objs1.keys()).intersection(set(objs2.keys()))
        if not common:
            continue

        prod_count = len(common)
        prod_s1 = sum(objs1[k]['Size'] for k in common)
        prod_s2 = sum(objs2[k]['Size'] for k in common)

        prod_savings = prod_s1 - prod_s2
        prod_avg_delta = prod_savings / prod_count
        prod_pct_saved = (prod_savings / prod_s1 * 100) if prod_s1 > 0 else 0

        grand_count += prod_count
        grand_s1 += prod_s1
        grand_s2 += prod_s2

        display_name = prod if len(prod) <= 28 else prod[:25] + "..."
        pct_str = f"{prod_pct_saved:+.2f}%"

        print(f"{display_name:<30} | {prod_count:<7} | {format_bytes(prod_s1):<10} | {format_bytes(prod_s2):<10} | {format_bytes_signed(prod_savings):<11} | {pct_str:>8} | {format_bytes_signed(prod_avg_delta)}")

    print("-" * 100)

    if grand_count > 0:
        total_savings = grand_s1 - grand_s2
        avg_delta = total_savings / grand_count
        pct_reduction = (total_savings / grand_s1 * 100) if grand_s1 > 0 else 0
        total_pct_str = f"{pct_reduction:+.2f}%"

        print(f"{'GRAND TOTALS':<30} | {grand_count:<7} | {format_bytes(grand_s1):<10} | {format_bytes(grand_s2):<10} | {format_bytes_signed(total_savings):<11} | {total_pct_str:>8} | {format_bytes_signed(avg_delta)}")
        print(f"\nOVERALL STORAGE REDUCTION: {pct_reduction:.2f}%")
    else:
        print("No matching files found across datasets to compare.")

    print("="*100 + "\n")

if __name__ == "__main__":
    main()
