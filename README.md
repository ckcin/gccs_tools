# GCCS Python Utilities and Sample Tools

This package contains helper modules and utilities for working with data retrieved from the GOES GCCS buckets

## Modules

### 1. base_utils.py
This module contains core python utilities for keeping logging and error handling consistent between tools based on this package
- Logger Class: A custom logging engine that provides formatted, timestamped, and color-coded terminal output.
  - Supports multiple severity levels: DEBUG, VERBOSE, INFO, WARN, and ERROR.
  - Automatically detects if the terminal supports colors and gracefully falls back to plain text if not.
- setup_interrupt_handler: A utility function that intercepts Ctrl+C (SIGINT) signals, allowing the script to cleanly halt execution
  and exit without throwing messy Python tracebacks.

### 2. aws_utils.py
This module is dedicated to AWS connectivity, identity management, and S3 retrieval operations.
- S3SSOConnector Class: Handles authentication with AWS using SSO (OIDC) Device Code flow.
  - Automatically prompts you to open a browser window to log in.
  - Discovers available AWS accounts and roles, allowing interactive selection via the terminal if they aren't hardcoded in the config.
  - Credential Caching: Securely stores temporary AWS credentials in a local, hidden .sso_cache.json file. Subsequent runs will use this
    cache to bypass the browser login until the token expires.
- download_s3_byte_range: Extracts a specific byte-rage from an S3 object directly to a local file

### 3. goes_aws.py
This module extends S3 capabilities specifically for GOES-R satellite imagery naming conventions and structures, and handles tool configuration.
- get_s3_objects: Connects to the specified S3 buckets and paginates through the keys, returning a dictionary of files filtered by prefix,
  extension, and timestamp.
- normalize_goes_key: A specialized string parser that strips the dynamic GOES-R creation time (_c...) from filenames so that equivalent
  files across two different processing environments can be successfully matched
- normalize_channels: Masks the ABI channel number (e.g., replacing C13 with CXX) to easily group files from the same observation scan.
-
#### Configuration: (config.json):
```
[json]
{
    "connection": {
        "sso_start_url": "https://my-company.awsapps.com/start",
        "sso_region": "us-east-1",
        "account_id": "123456789012",
        "role_name": "MyDataScientistRole"
    },
    "data_sources": {
        "dataset_1": {
            "name": "Primary (On-Prem)",
            "bucket": "primary-imagery-bucket",
            "prefix": "goes-r/glm/lcfa/<YEAR>/<mon>/<DOY>/"
        },
        "dataset_2": {
            "name": "Backup (Cloud)",
            "bucket": "secondary-imagery-bucket",
            "prefix": "aws-glm-backup/data/<YYYYMMDD>/"
        },
        "extension_filter": ".nc"
    }
}
```
##### Configuration Definition:

- connection: Defines how the script authenticates with AWS SSO.
  - sso_start_url (Required): The portal URL for your AWS IAM Identity Center.
  - sso_region (Required): The AWS region where your Identity Center is hosted.
  - account_id (Optional): If provided, the script will automatically select this AWS Account. If omitted, the script will prompt you to choose from a list in the terminal.
  - role_name (Optional): If provided, the script will automatically assume this IAM Role. If omitted, the script will prompt you to choose.
- data_sources: Defines the two S3 buckets being compared and how to route to the correct data dynamically
  - dataset_1 & dataset_2: Objects containing the parameters for the two sources.
    - name: A friendly display name used in the terminal output and Word document reports.
    - bucket: The exact S3 bucket name.
    - prefix: The S3 prefix (folder path) to search. Supports dynamic placeholders.
  - extension_filter (Optional): Limits the comparison to specific file types (e.g., ".nc").

### 4. meta_utils.py
Handles the deep inspection of mismatched NetCDF metadata.
-  MetadataAuditing: Compares NetCDF global attributes, dimensions, and variables directly from memory. Utilizes a tiered severity scale (ERROR, WARNING, KNOWN, IGNORE) to filter out expected metadata differences like production cluster names or generation times.

### 5. plot_utils.py & data_utils.py
Handles payload auditing and visualization.
- Data Payload Auditing: Streams file contents directly from S3 (via s3fs and xarray) to check internal multidimensional data arrays for numeric drift.
- Visual Plotting: Dynamically generates visual representations of mismatched arrays based on their shape:
  - 1D Arrays: Generates line-plot overlays and delta views (dynamically subsampled for large arrays).
  - 2D/3D Arrays: Generates visual image diffs, cleanly slicing 3D arrays to their first dimension.
  - GLM Spatial Mapping: Generates 1x3 side-by-side geographic overlays using Cartopy to visualize missing or shifted events, groups, and flashes.

## Sample Tools
### 1. . GLM Comparison Tool: glmct.py
The GLM Comparison Tool is a focused utility that retrieves a series of files from the configured buckets. Files are retrieved from Source 1, and then matching files are retrieved from Source 2 (ignoring the "creation time" identified by the _cYYYYDDDhhmmsss block).

Matching files are compared for metadata differences. For GLM Level 2 LCFA files specifically, the tool conducts spatial comparisons mapping out missing or false extraneous occurrences of events, groups, and flashes.

Usage Examples:
```
[bash]
# Compare a specific dataset using dynamic date parameters
python glmct.py --date 202301500
```

### 2. GOES-R Dynamic Comparator: goes_compare.py
A generalized orchestration tool designed to crawl and compare arbitrary GOES-R product directories. It dynamically discovers product directories using wildcard pattern matching.

When mismatches in file size or ETag are found between the latest file pair, it performs a comprehensive NetCDF payload audit using xarray. It evaluates multidimensional data arrays (such as ABI radiances) against a numeric tolerance, catching floating point drift or processing artifacts.

The results, including generated QA visualizations and 2D difference plots, are output to a CSV file and a fully formatted Landscape Word Document.

Usage Examples:
```
[bash]
# Crawl and compare the latest files for a wildcard product using specific satellite and level placeholders
python goes_compare.py "ABI-L1b-RadF" 2023015 --sat G16 --level L1b --tolerance 0.0001
```

### 3. Storage Size Comparator: storage_savings.py
A simple tool to pull and match given dates data between the source and test and reports storage savings

Usage Example:
```
[bash]
./storage_savings.py "*" --level L2 --instr ABI 2026271
```

*Dynamic Date Placeholders*
The name and prefix fields in config.json support dynamic placeholders for both tools. When you run either tool and supply a date string, these tags are automatically replaced with the corresponding values for that specific date:
- \<YEAR> or \<YYYY>: 4-digit year (e.g., 2023)
- \<mon>: 3-letter lowercase month abbreviation (e.g., jan, feb, mar)
- \<MM>: 2-digit month (e.g., 01, 12)
- \<DD>: 2-digit day of the month (e.g., 05, 31)
- \<DOY>: 3-digit day of the year / Julian day (e.g., 001, 365)
- \<YYYYMMDD>: Standard combined date string (e.g., 20230105)
- \<HH>: 2-digit hour (e.g., 00, 14)

Advanced CLI flags (for goes_compare.py):
- \<PRODUCT>: Supported via positional argument for crawling directories.
- \<SAT> / \<LEVEL> / \<INSTR>: Supported via explicit CLI flags (e.g., --sat G16).
- --tolerance: Sets the allowable numeric deviation for array comparisons (Defaults to 0.0001). Differences smaller than this are ignored to suppress minor compression noise.
