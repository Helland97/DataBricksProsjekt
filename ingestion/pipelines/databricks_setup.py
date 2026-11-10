"""Shared Databricks destination for every dlt pipeline in this project.

    from pipelines.databricks_setup import destination
    pipeline = dlt.pipeline(..., destination=destination("databricks"))

File format
-----------
dlt prefers Parquet for Databricks, but writing Parquet needs pyarrow's native Parquet DLL, and
Windows application control (Smart App Control) blocks that DLL on this machine. JSONL also works with
COPY INTO; the one restriction is that timestamps must be timezone-aware, and every timestamp from the
fake API is UTC ("...Z").

It has to be set as the destination's *preferred* format. `pipeline.run(loader_file_format=...)` only
applies to your own resources, while dlt's system table `_dlt_pipeline_state` always uses the
preferred format. On a machine (or Databricks job) where pyarrow works, Parquet is the faster choice:
set DATABRICKS_FILE_FORMAT=parquet.

Login without storing a token
-----------------------------
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


def use_azure_cli_auth() -> None:
    has_token = dlt.secrets.get(f"{CREDS}.access_token")
    has_sp = dlt.secrets.get(f"{CREDS}.client_id") and dlt.secrets.get(f"{CREDS}.client_secret")
    if has_token or has_sp:
        return

    hostname = dlt.secrets.get(f"{CREDS}.server_hostname") or dlt.config.get(f"{CREDS}.server_hostname")
    if not hostname:
        raise SystemExit(
            f"{CREDS}.server_hostname is not set. Copy .dlt/secrets.toml.example to .dlt/secrets.toml."
        )
    os.environ.setdefault("DATABRICKS_HOST", f"https://{hostname}")
    os.environ.setdefault("DATABRICKS_AUTH_TYPE", "azure-cli")


def destination(name: str) -> str | Destination:
    """'duckdb' for local development, 'databricks' for the real thing. Same pipeline code for both."""
    if name == "duckdb":
        return "duckdb"
    if name == "databricks":
        use_azure_cli_auth()
        # With an Entra (az login) token the SQL connector first tries to swap it for a Databricks token,
        # warns when that fails, and then uses the Entra token as-is, which works. Hide that noise.
        logging.getLogger("databricks.sql.auth.token_federation").setLevel(logging.ERROR)
        file_format = os.environ.get("DATABRICKS_FILE_FORMAT", "jsonl")
        return dlt.destinations.databricks(preferred_loader_file_format=file_format)
    raise ValueError(f"unknown destination {name!r}")
