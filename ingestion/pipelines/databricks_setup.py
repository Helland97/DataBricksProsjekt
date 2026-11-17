"""Shared Databricks destination for every dlt pipeline in this project.

    from pipelines.databricks_setup import destination
    pipeline = dlt.pipeline(..., destination=destination("databricks"))

File format
-----------
Parquet (dlt's default for Databricks). It's typed, so COPY INTO doesn't have to guess column types.

DATABRICKS_FILE_FORMAT=jsonl switches to JSONL, but beware: dlt runs COPY INTO with
inferTimestamp=true, so Spark reads date-like strings such as "2026-10-01" as TIMESTAMP, and the load
fails with DELTA_FAILED_TO_MERGE_FIELDS against the STRING column dlt created. That's why dlt lists
`date` as unsupported for JSONL on Databricks.

History: Parquet first failed here with "En programkontrollpolicy har blokkert denne filen" on pyarrow's
DLL. The cause was uv hard-linking packages into a .venv inside OneDrive; `link-mode = "copy"` in
pyproject.toml fixed it.

The format is set as the destination's *preferred* format, because `pipeline.run(loader_file_format=...)`
only applies to your own resources, while dlt's system table `_dlt_pipeline_state` always uses the
preferred format.

Login without storing a token
-----------------------------
Inside Databricks (a job or a notebook) none of this applies: there is no `az`, and the runtime gives the
SDK the job's own credentials. use_azure_cli_auth() then does nothing.

If .dlt/secrets.toml has no access_token and no client_id/client_secret, dlt falls back to
the Databricks SDK's "unified auth" (WorkspaceClient()). The SDK needs to know the workspace host,
which it normally reads from DATABRICKS_HOST or ~/.databrickscfg. use_azure_cli_auth() fills that in
from dlt's own config, for this process only, and selects Azure CLI auth, so `az login` is all you need.
Anything already set in the environment, or a token / service principal in secrets.toml, wins.
"""

from __future__ import annotations

import logging
import os

import dlt
from dlt.common.destination import Destination

CREDS = "destination.databricks.credentials"


def running_on_databricks() -> bool:
    """Databricks sets DATABRICKS_RUNTIME_VERSION on all its compute, including serverless jobs."""
    return "DATABRICKS_RUNTIME_VERSION" in os.environ


def use_catalog_from_config() -> None:
    """Pass destination.databricks.catalog (config.toml) on to dlt's credentials.catalog.

    The catalog can't be written as [destination.databricks.credentials] in config.toml: dlt treats the whole
    credentials section as a secret, and when there's no secrets.toml (the Databricks job) it refuses to read
    it from config.toml. Without any catalog, dlt would silently use the workspace's default catalog.
    An environment variable that's already set still wins.
    """
    catalog = dlt.config.get("destination.databricks.catalog")
    if catalog:
        os.environ.setdefault("DESTINATION__DATABRICKS__CREDENTIALS__CATALOG", catalog)


def use_azure_cli_auth() -> None:
    if running_on_databricks():
        # There's no `az` here. The Databricks SDK's default auth (the same fallback dlt uses) picks up the
        # job's own credentials from the runtime, so the job writes as its run-as identity.
        return
    has_token = dlt.secrets.get(f"{CREDS}.access_token")
    has_sp = dlt.secrets.get(f"{CREDS}.client_id") and dlt.secrets.get(f"{CREDS}.client_secret")
    if has_token or has_sp:
        return

    hostname = dlt.secrets.get(f"{CREDS}.server_hostname") or dlt.config.get(f"{CREDS}.server_hostname")
    if not hostname:
        raise SystemExit(f"{CREDS}.server_hostname is not set. Copy .dlt/secrets.toml.example to .dlt/secrets.toml.")
    os.environ.setdefault("DATABRICKS_HOST", f"https://{hostname}")
    os.environ.setdefault("DATABRICKS_AUTH_TYPE", "azure-cli")


def destination(name: str) -> str | Destination:
    """'duckdb' for local development, 'databricks' for the real thing. Same pipeline code for both."""
    if name == "duckdb":
        return "duckdb"
    if name == "databricks":
        use_catalog_from_config()
        use_azure_cli_auth()
        # With an Entra (az login) token the SQL connector first tries to swap it for a Databricks token,
        # warns when that fails, and then uses the Entra token as-is, which works. Hide that noise.
        logging.getLogger("databricks.sql.auth.token_federation").setLevel(logging.ERROR)
        file_format = os.environ.get("DATABRICKS_FILE_FORMAT", "parquet")
        return dlt.destinations.databricks(preferred_loader_file_format=file_format)
    raise ValueError(f"unknown destination {name!r}")
