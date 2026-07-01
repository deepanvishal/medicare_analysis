"""
Shared config for the expanded_scope multi-state pipeline (FL + OH + AZ + IL).

Self-contained: nothing in this folder imports from anywhere else in the repo.
Root-level StepN_* files are reference-only.

All OUTPUT tables use the `_ms_` (multi-state) infix so the current FL production
tables (A870800_medicare_supply_demand_*) are never overwritten during the build.
"""

# NOTE: google.cloud.bigquery is imported lazily inside client() so the transform
# half of the loaders stays runnable without the SDK/auth installed.

import os

# Repo root = parent of expanded_scope/. Lets loaders reference data files
# regardless of the current working directory they are launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_path(*parts):
    """Absolute path to a file at the repo root, independent of CWD."""
    return os.path.join(_REPO_ROOT, *parts)


# Input data files (xlsx / csv) live here. Loaders resolve by glob pattern so the
# exact filename/suffix doesn't matter.
DATA_DIR = "Expanded_scope_medicare"


def data_file(pattern):
    """The single input file in DATA_DIR matching a glob pattern (fails if 0 or >1)."""
    import glob
    matches = sorted(glob.glob(repo_path(DATA_DIR, pattern)))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly 1 file matching {DATA_DIR}/{pattern}, found {len(matches)}: {matches}")
    return matches[0]


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
    """Fully-qualified id for an EXISTING raw table in our dataset (e.g. hosp_list_cmi)."""
    return f"{TABLE_PROJECT}.{DATASET}.{name}"


def base(name: str) -> str:
    """Existing pre-ms A870800_medicare_supply_demand_ table, e.g. base('mbr_with_zip')."""
    return f"{TABLE_PROJECT}.{DATASET}.{BASE_PREFIX}_{name}"


def client():
    """BigQuery client on the billing project (per CLAUDE.md)."""
    from google.cloud import bigquery
    return bigquery.Client(project=CLIENT_PROJECT)


def run_ddl(ddl, checks=None, gate_sql=None, gate_msg=""):
    """Execute a CREATE-TABLE DDL, optional pass/fail gate, then print each check.

    ddl       : the CREATE OR REPLACE TABLE ... statement (string).
    checks    : dict {label: SELECT query} -- rows printed for eyeball validation.
    gate_sql  : SELECT returning a single count; if > 0 the step fails loudly.
    """
    c = client()
    c.query(ddl).result()
    print("table created/replaced")
    if gate_sql:
        n = list(c.query(gate_sql).result())[0][0]
        if n:
            raise SystemExit(f"GATE FAILED -- {gate_msg}: {n}")
        print(f"gate OK ({gate_msg} = 0)")
    for label, q in (checks or {}).items():
        print(f"--- {label} ---")
        for row in c.query(q).result():
            print("  ", dict(row))


def state_fips_sql() -> str:
    """('12', '39', '04', '17') — for WHERE state_fips_code IN (...)."""
    return "(" + ", ".join(f"'{f}'" for f in STATE_FIPS) + ")"


def state_abbr_sql() -> str:
    """('FL', 'OH', 'AZ', 'IL') — for WHERE state IN (...)."""
    return "(" + ", ".join(f"'{s}'" for s in STATE_ABBRS) + ")"
