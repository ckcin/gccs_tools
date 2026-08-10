"""
A module dedicated to AWS connections and credential handling, along with generic S3 retrieval utilities.

This module simplifies the process of authenticating via AWS IAM Identity Center (SSO)
using the Device Code flow and provides helper functions to query S3 buckets.
"""
import sys
import os
import datetime
import boto3
import json
import time
import webbrowser
from pathlib import Path

# =============================================================================
# AWS SSO Connector
# =============================================================================
class S3SSOConnector:
    """
    A helper class to connect to AWS S3 using direct SSO OIDC authentication.

    Automatically discovers and prompts for accounts and roles if needed,
    reads them from a specified JSON configuration file, and caches temporary
    credentials to prevent repeated browser logins.

    Attributes:
        config_path (str): The path to the configuration JSON file.
        cache_file (str): The local hidden file used to cache the SSO credentials.
        sso_start_url (str): The portal URL for your AWS IAM Identity Center.
        sso_region (str): The AWS region where your Identity Center is hosted.
        account_id (str, optional): The target AWS Account ID.
        role_name (str, optional): The target IAM Role to assume.
        s3_client (boto3.client): The authenticated boto3 S3 client instance.
    """

    def __init__(self, config_path="config.json"):
        """
        Initializes the S3SSOConnector, loads configuration, and authenticates.

        Args:
            config_path (str): Path to the JSON configuration file containing
                               the 'connection' block. Defaults to 'config.json'.
        """
        self.config_path = config_path
        self.cache_file = ".sso_cache.json"

        self.sso_start_url = None
        self.sso_region = None
        self.account_id = None
        self.role_name = None

        self.s3_client = None

        self._load_config()
        self._initialize_session()

    def _load_config(self):
        """
        Loads basic SSO parameters from the config file.

        Expected JSON structure for the connection configuration:
        {
            "connection": {
                "sso_start_url": "https://...", # (Required) URL for AWS IAM Identity Center
                "sso_region": "us-east-1",      # (Required) AWS region for Identity Center
                "account_id": "123...",         # (Optional) Auto-select this AWS Account ID
                "role_name": "MyRole"           # (Optional) Auto-select this IAM Role
            }
        }

        Raises:
            SystemExit: If the file is not found, contains invalid JSON, or
                        is missing required parameters.
        """
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)

                conn_config = config.get("connection", {})

                self.sso_start_url = conn_config.get("sso_start_url")
                self.sso_region = conn_config.get("sso_region")
                self.account_id = conn_config.get("account_id")
                self.role_name = conn_config.get("role_name")

                if not self.sso_start_url or not self.sso_region:
                    print(f"Error: Both 'sso_start_url' and 'sso_region' are required in {self.config_path} under 'connection'")
                    sys.exit(1)
        except FileNotFoundError:
            print(f"Error: Config file '{self.config_path}' not found.")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Error: Config file '{self.config_path}' is not valid JSON.")
            sys.exit(1)

    def _load_cached_credentials(self):
        """
        Attempts to load and validate unexpired SSO credentials from the local cache.

        Validates that the cached token matches the configured account ID and role name
        (if provided in the config) and ensures the token will not expire within the
        next 5 minutes.

        Returns:
            dict or None: The cached credential payload if valid, otherwise None.
        """
        if not os.path.exists(self.cache_file):
            return None

        try:
            with open(self.cache_file, 'r') as f:
                cache = json.load(f)

            # If config enforces a specific account or role, ensure the cache matches
            if self.account_id and cache.get("account_id") != self.account_id:
                return None
            if self.role_name and cache.get("role_name") != self.role_name:
                return None

            # Check expiration (AWS returns this in milliseconds)
            expiration_ms = cache.get("expiration", 0)
            current_time_ms = int(time.time() * 1000)
            buffer_ms = 5 * 60 * 1000 # 5 minute safety buffer

            if (current_time_ms + buffer_ms) < expiration_ms:
                return cache

        except Exception:
            pass # Fails gracefully if cache is corrupt or missing fields

        return None

    def _save_cached_credentials(self, creds, account_id, role_name):
        """
        Saves temporary AWS credentials to a local hidden file for reuse.

        Args:
            creds (dict): The credential payload returned by AWS SSO containing
                          access keys, session tokens, and expiration metadata.
            account_id (str): The AWS Account ID the credentials belong to.
            role_name (str): The IAM Role the credentials belong to.
        """
        cache = {
            "account_id": account_id,
            "role_name": role_name,
            "accessKeyId": creds['accessKeyId'],
            "secretAccessKey": creds['secretAccessKey'],
            "sessionToken": creds['sessionToken'],
            "expiration": creds['expiration']
        }
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(cache, f)
        except Exception as e:
            print(f"Warning: Could not save SSO cache file: {e}")

    def _initialize_session(self):
        """
        Authenticates with AWS SSO and establishes an S3 connection.

        If valid cached credentials exist, they are utilized immediately.
        Otherwise, it triggers an OIDC Device Code flow, opens the user's web
        browser for authorization, discovers available accounts/roles, and
        caches the resulting temporary credentials.

        Raises:
            SystemExit: If authentication times out or required accounts/roles
                        cannot be found.
        """
        try:
            # --- 1. Check for valid cached credentials ---
            cached_creds = self._load_cached_credentials()
            if cached_creds:
                exp_time = datetime.datetime.fromtimestamp(cached_creds['expiration'] / 1000.0)
                print(f"Using cached SSO credentials for role '{cached_creds['role_name']}' (Valid until {exp_time.strftime('%H:%M:%S')})")

                self.s3_client = boto3.client(
                    's3',
                    aws_access_key_id=cached_creds['accessKeyId'],
                    aws_secret_access_key=cached_creds['secretAccessKey'],
                    aws_session_token=cached_creds['sessionToken'],
                    region_name=self.sso_region
                )
                return

            # --- 2. Perform fresh SSO Login ---
            print(f"Initiating direct SSO authentication to {self.sso_start_url}...")
            oidc_client = boto3.client('sso-oidc', region_name=self.sso_region)

            client_res = oidc_client.register_client(
                clientName='s3-custom-sso-connector',
                clientType='public'
            )

            auth_res = oidc_client.start_device_authorization(
                clientId=client_res['clientId'],
                clientSecret=client_res['clientSecret'],
                startUrl=self.sso_start_url
            )

            url = auth_res['verificationUriComplete']
            print(f"\nAction Required! Opening browser to authenticate...")
            print(f"If it doesn't open automatically, please click this link:\n{url}\n")

            webbrowser.open(url)

            # Poll for token
            print("Waiting for browser authorization", end="")
            access_token = None

            interval = auth_res.get('interval', 5)
            expires_in = auth_res.get('expiresIn', 600)

            for _ in range(expires_in // interval):
                sys.stdout.write(".")
                sys.stdout.flush()
                time.sleep(interval)

                try:
                    token_res = oidc_client.create_token(
                        clientId=client_res['clientId'],
                        clientSecret=client_res['clientSecret'],
                        grantType='urn:ietf:params:oauth:grant-type:device_code',
                        deviceCode=auth_res['deviceCode']
                    )
                    access_token = token_res['accessToken']
                    print("\nAuthentication successful!")
                    break
                except oidc_client.exceptions.AuthorizationPendingException:
                    continue
                except Exception as e:
                    print(f"\nError retrieving token: {e}")
                    sys.exit(1)

            if not access_token:
                print("\nAuthentication timed out. Please try again.")
                sys.exit(1)

            # --- 3. Discover Accounts ---
            sso_client = boto3.client('sso', region_name=self.sso_region)
            accounts_res = sso_client.list_accounts(accessToken=access_token)
            accounts = accounts_res.get('accountList', [])

            if not accounts:
                print("Error: No AWS accounts assigned to this user.")
                sys.exit(1)

            selected_account = None
            if self.account_id:
                selected_account = next((acc for acc in accounts if acc['accountId'] == self.account_id), None)
                if not selected_account:
                    print(f"Error: Configured account_id '{self.account_id}' not found or unauthorized.")
                    sys.exit(1)
                print(f"Using configured account: {selected_account['accountName']} ({selected_account['accountId']})")
            else:
                selected_account = accounts[0]
                if len(accounts) > 1:
                    print("\nAvailable Accounts:")
                    for i, acc in enumerate(accounts):
                        print(f"[{i+1}] {acc['accountName']} ({acc['accountId']})")

                    while True:
                        try:
                            choice = int(input("\nSelect an account (number): ")) - 1
                            if 0 <= choice < len(accounts):
                                selected_account = accounts[choice]
                                break
                            print("Invalid selection.")
                        except ValueError:
                            print("Please enter a valid number.")
                else:
                    print(f"Auto-selected only account: {selected_account['accountName']} ({selected_account['accountId']})")

            # --- 4. Discover Roles ---
            roles_res = sso_client.list_account_roles(
                accessToken=access_token,
                accountId=selected_account['accountId']
            )
            roles = roles_res.get('roleList', [])

            if not roles:
                print(f"Error: No roles assigned in account '{selected_account['accountName']}'.")
                sys.exit(1)

            selected_role = None
            if self.role_name:
                selected_role = next((role for role in roles if role['roleName'] == self.role_name), None)
                if not selected_role:
                    print(f"Error: Configured role_name '{self.role_name}' not found in account '{selected_account['accountName']}'.")
                    sys.exit(1)
                print(f"Using configured role: {selected_role['roleName']}")
            else:
                selected_role = roles[0]
                if len(roles) > 1:
                    print("\nAvailable Roles:")
                    for i, role in enumerate(roles):
                        print(f"[{i+1}] {role['roleName']}")

                    while True:
                        try:
                            choice = int(input("\nSelect a role (number): ")) - 1
                            if 0 <= choice < len(roles):
                                selected_role = roles[choice]
                                break
                            print("Invalid selection.")
                        except ValueError:
                            print("Please enter a valid number.")
                else:
                    print(f"Auto-selected only role: {selected_role['roleName']}")

            # --- 5. Exchange token for AWS Credentials & Cache Them ---
            print(f"\nAssuming role '{selected_role['roleName']}'...")
            creds_res = sso_client.get_role_credentials(
                roleName=selected_role['roleName'],
                accountId=selected_account['accountId'],
                accessToken=access_token
            )

            creds = creds_res['roleCredentials']

            self._save_cached_credentials(creds, selected_account['accountId'], selected_role['roleName'])

            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=creds['accessKeyId'],
                aws_secret_access_key=creds['secretAccessKey'],
                aws_session_token=creds['sessionToken'],
                region_name=self.sso_region
            )
            print("Successfully established S3 connection.\n")

        except Exception as e:
            print(f"\nFailed to initialize SSO session: {e}")
            sys.exit(1)

    def list_buckets(self):
        """
        Lists all S3 buckets available to the currently assumed role.

        Returns:
            list: A list of string bucket names. Returns an empty list if
                  permission is denied or an error occurs.
        """
        try:
            response = self.s3_client.list_buckets()
            return [bucket['Name'] for bucket in response.get('Buckets', [])]
        except Exception as e:
            print(f"Failed to list buckets: {e}")
            return []


# =============================================================================
# S3 Retrieval Utilities
# =============================================================================

def download_s3_byte_range(s3_client, bucket, key, offset, size, target_path):
    """
    Extracts a specific byte-range from an S3 object directly to a local file.

    This is highly useful for pulling individual files directly out of uncompressed
    tarballs stored on S3 if you possess an index sidecar, saving massive
    amounts of bandwidth.

    Args:
        s3_client (boto3.client): Authenticated S3 client.
        bucket (str): S3 Bucket Name.
        key (str): S3 Object Key (e.g., the large tarball).
        offset (int): The starting byte offset.
        size (int): The number of bytes to read.
        target_path (str or Path): Local filepath to save the extracted bytes.

    Raises:
        Exception: Passes through any Boto3 HTTP or S3 errors.
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    resp = s3_client.get_object(
        Bucket=bucket,
        Key=key,
        Range=f"bytes={offset}-{offset+size-1}"
    )

    with open(target, 'wb') as f:
        f.write(resp['Body'].read())


def get_s3_objects(s3_client, bucket_name, prefix='', ext_filter=None, logger=None):
    """
    Fetches and filters all objects in an S3 bucket under a specific prefix.

    Paginates through the specified S3 bucket to retrieve all object keys.
    It can optionally filter the results by file extension.

    Args:
        s3_client (boto3.client): An authenticated boto3 S3 client.
        bucket_name (str): The name of the S3 bucket to scan.
        prefix (str, optional): The S3 prefix (folder path) to limit the search. Defaults to ''.
        ext_filter (str, optional): A file extension to filter by (e.g., '.nc'). Defaults to None.
        logger (object, optional): A logging instance with `.info()` and `.error()`
                                   methods to print status. Defaults to None.

    Returns:
        dict: A dictionary mapping the relative object keys to their raw metadata
              (full key, Size, ETag). Returns None if an access error occurs.
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

                    rel_key = key[len(prefix):] if key.startswith(prefix) else key
                    rel_key = rel_key.lstrip('/')

                    if not rel_key: continue

                    objects[rel_key] = {
                        'full_key': key,
                        'Size': obj['Size'],
                        'ETag': obj['ETag'].strip('"')
                    }
    except Exception as e:
        if logger: logger.error(f"Error accessing bucket '{bucket_name}': {e}")
        return None

    if logger: logger.info(f"Found {len(objects)} files in s3://{bucket_name}/{prefix}")
    return objects
