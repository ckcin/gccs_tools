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
- get_s3_objects: Connects to the specified S3 buckets and paginates through the keys, returning a dictionary of files filtered by prefix,
  extension, and timestamp.
- normalize_goes_key: A specialized string parser that strips the dynamic GOES-R creation time (_c...) from filenames so that equivalent
  files across two different processing environments can be successfully matched

- config.json section:
```
[json]
{
    "connection": {
        "sso_start_url": "[https://my-company.awsapps.com/start](https://my-company.awsapps.com/start)",
        "sso_region": "us-east-1",
        "account_id": "123456789012",
        "role_name": "MyDataScientistRole"
    }
}
```
config description: 

connection: Defines how the script authenticates with AWS SSO.
- sso_start_url (Required): The portal URL for your AWS IAM Identity Center.
- sso_region (Required): The AWS region where your Identity Center is hosted.
- account_id (Optional): If provided, the script will automatically select this AWS Account. If omitted, the script will prompt you to choose from a list in the terminal.
- role_name (Optional): If provided, the script will automatically assume this IAM Role. If omitted, the script will prompt you to choose.


## Tools
### 1. GLM Comparison Tool: glmct.py
The GLM Comparison Tool is a relative simple tool that provide with configuration entries (described below) retrieves a series of files from
the configured buckets. Files are retrieved from source set 1, and then matching files retrieved from source 2 ignoring the "creation time" 
identified by the "`_cYYYYDDDhhmmsss`". Matching files are compared for metadata differences and events, groups and flashes are compared based
on their locations. 

Example config.json
```
[json]
{
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
configuration definition:

data_sources: Defines the two S3 buckets being compared and how to route to the correct data dynamically.

- dataset_1 & dataset_2: Objects containing the parameters for the two sources.
  - name: A friendly display name used in the terminal output and Word document reports.
	- bucket: The exact S3 bucket name.
	- prefix: The S3 prefix (folder path) to search.
- extension_filter (Optional): Limits the comparison to specific file types (e.g., ".nc").

Dynamic Date Placeholders
- The name and prefix fields support dynamic placeholders. When you run glmct.py --date YYYYDOYHH, these tags are automatically replaced with the corresponding values for that specific date:
  - <YEAR> or <YYYY>: 4-digit year (e.g., 2023)
  - <mon>: 3-letter lowercase month abbreviation (e.g., jan, feb, mar)
  - <MM>: 2-digit month (e.g., 01, 12)
  - <DD>: 2-digit day of the month (e.g., 05, 31)
  - <DOY>: 3-digit day of the year / Julian day (e.g., 001, 365)
  - <YYYYMMDD>: Standard combined date string (e.g., 20230105)
  - <HH>: 2-digit hour (e.g., 00, 14)
