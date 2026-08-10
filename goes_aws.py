"""
A module dedicated to specific AWS S3 retrieval and filename formatting utilities
for GOES-R series satellite imagery datasets.
"""
import re

def format_channel_list(channels):
    """
    Standardizes a list of loosely formatted GOES-R channels to the 2-digit 'cXX' format.

    Example: [1, "2", "C13"] -> ['c01', 'c02', 'c13']

    Args:
        channels (list): A list of channel indicators (ints or strings).

    Returns:
        list: A normalized list of 'cXX' formatted strings.
    """
    if not channels: return []
    normalized = []
    for c in channels:
        c_str = str(c).lower()
        if c_str.startswith('c'):
            normalized.append(c_str)
        else:
            normalized.append(f"c{c_str.zfill(2)}")
    return normalized


def mask_channel_in_filename(rel_key, placeholder="CXX"):
    """
    Normalizes GOES-R ABI filenames by masking the embedded channel number.

    GOES-R Advanced Baseline Imager (ABI) files contain a channel indicator
    (e.g., 'C01' to 'C16') in the product string (e.g., 'OR_ABI-L1b-RadF-M6C13_G16...').
    This function replaces the specific channel with a placeholder so that files
    from the same observation scan can be easily grouped or compared.

    Args:
        rel_key (str): The relative S3 object key or filename.
        placeholder (str, optional): The string to replace the channel with. Defaults to 'CXX'.

    Returns:
        str: The filename with the channel masked (e.g., '...-M6CXX_G16...').
             Returns the original string if no channel pattern is found.
    """
    # Matches the standard GOES ABI pattern: M<mode>C<channel> (e.g., M6C13)
    # and replaces the C<channel> portion with the placeholder.
    return re.sub(r'(M\d)C(\d{2})', rf'\g<1>{placeholder}', rel_key)


def normalize_goes_key(rel_key):
    """
    Strips the GOES-R creation time (_c...) from the filename for comparison.

    GOES-R netCDF files contain start (_s), end (_e), and creation (_c) timestamps.
    Since files generated in different processing environments will naturally have
    different creation times, this function removes the `_c` timestamp to allow
    for direct 1-to-1 comparison of equivalent files.

    Args:
        rel_key (str): The relative S3 object key (filename).

    Returns:
        str: The normalized filename with the creation timestamp removed.
    """
    if "_c" in rel_key and "_s" in rel_key and "_e" in rel_key:
        last_c = rel_key.rfind("_c")
        ext_idx = rel_key.find(".", last_c)

        if ext_idx != -1:
            return rel_key[:last_c] + rel_key[ext_idx:]
        else:
            return rel_key[:last_c]
    return rel_key


def extract_abi_channel(rel_key):
    """
    Extracts the GOES-R ABI channel number from a filename.

    Args:
        rel_key (str): The relative S3 object key or filename.

    Returns:
        int or None: The integer channel number (1-16), or None if no channel is found.
    """
    match = re.search(r'M\dC(\d{2})', rel_key)
    if match:
        return int(match.group(1))
    return None


def get_goes_s3_objects(s3_client, bucket_name, prefix='', ext_filter=None, time_filter=None, logger=None):
    """
    Fetches and filters GOES-R objects in an S3 bucket under a specific prefix.

    Paginates through the specified S3 bucket to retrieve all object keys.
    It normalizes keys by stripping creation times for cross-environment comparisons,
    and can filter by extension or start time (`_s`).

    Args:
        s3_client (boto3.client): An authenticated boto3 S3 client.
        bucket_name (str): The name of the S3 bucket to scan.
        prefix (str, optional): The S3 prefix (folder path) to limit the search. Defaults to ''.
        ext_filter (str, optional): A file extension to filter by (e.g., '.nc'). Defaults to None.
        time_filter (str, optional): A time string expected in the start time (`_s`) part
                                     of the filename. Defaults to None.
        logger (object, optional): A logging instance with `.info()` and `.error()`
                                   methods to print status. Defaults to None.

    Returns:
        dict: A dictionary mapping the normalized object keys (stripped of creation times)
              to their raw metadata (full key, Size, ETag). Returns None if an access
              error occurs.
    """
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

    objects = {}
    if logger:
        logger.info(f"Scanning s3://{bucket_name}/{prefix} ...")

    try:
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']

                    if key.endswith('/'): continue
                    if ext_filter and not key.lower().endswith(ext_filter.lower()): continue
                    if time_filter and f"_s{time_filter}" not in key: continue

                    rel_key = key[len(prefix):] if key.startswith(prefix) else key
                    rel_key = rel_key.lstrip('/')

                    if not rel_key: continue

                    norm_key = normalize_goes_key(rel_key)

                    objects[norm_key] = {
                        'full_key': key,
                        'Size': obj['Size'],
                        'ETag': obj['ETag'].strip('"')
                    }
    except Exception as e:
        if logger: logger.error(f"Error accessing bucket '{bucket_name}': {e}")
        return None

    if logger: logger.info(f"Found {len(objects)} files in s3://{bucket_name}/{prefix}")
    return objects
