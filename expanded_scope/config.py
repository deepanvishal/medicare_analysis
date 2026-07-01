"""
Shared config for the expanded_scope multi-state pipeline (FL + OH + AZ + IL).

Self-contained: nothing in this folder imports from anywhere else in the repo.
Root-level StepN_* files are reference-only.

All OUTPUT tables use the `_ms_` (multi-state) infix so the current FL production
tables (A870800_medicare_supply_demand_*) are never overwritten during the build.
"""

from google.cloud import bigquery

# --- projects / dataset (per CLAUDE.md) ---
TABLE_PROJECT  = "anbc-hcb-dev"          # where tables live
CLIENT_PROJECT = "anbc-dev-prv-nc-ds"    # billing/auth project for bigquery.Client
DATASET        = "provider_ds_netconf_data_hcb_dev"
BASE_PREFIX    = "A870800_medicare_supply_demand"
MS_INFIX       = "ms"                    # multi-state marker in output table names

# --- geographic scope: state abbrev -> 2-digit FIPS state code ---
STATES = {
    "FL": "12",
    "OH": "39",
    "AZ": "04",
    "IL": "17",
}
STATE_ABBRS = tuple(STATES.keys())        # ('FL', 'OH', 'AZ', 'IL')
STATE_FIPS  = tuple(STATES.values())      # ('12', '39', '04', '17')

# expected county counts per state (used in validation gates)
COUNTY_COUNTS = {"FL": 67, "OH": 88, "AZ": 15, "IL": 102}


def table(name: str) -> str:
    """Fully-qualified multi-state output table id.

    table('ref_hsd_required_counts')
      -> anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_supply_demand_ms_ref_hsd_required_counts
    """
    return f"{TABLE_PROJECT}.{DATASET}.{BASE_PREFIX}_{MS_INFIX}_{name}"


def src(name: str) -> str:
    """Fully-qualified id for an EXISTING (non-ms) table in our dataset."""
    return f"{TABLE_PROJECT}.{DATASET}.{name}"


def client() -> bigquery.Client:
    """BigQuery client on the billing project (per CLAUDE.md)."""
    return bigquery.Client(project=CLIENT_PROJECT)


def state_fips_sql() -> str:
    """('12', '39', '04', '17') — for WHERE state_fips_code IN (...)."""
    return "(" + ", ".join(f"'{f}'" for f in STATE_FIPS) + ")"


def state_abbr_sql() -> str:
    """('FL', 'OH', 'AZ', 'IL') — for WHERE state IN (...)."""
    return "(" + ", ".join(f"'{s}'" for s in STATE_ABBRS) + ")"
