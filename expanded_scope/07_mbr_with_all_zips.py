"""
07 - ms_mbr_with_all_zips   [PYTHON runner / BigQuery DDL]

WHAT : Supply source. Enrich mbr_with_zip with every provider practice zip from
       mdcr_base_provider_mdcr_ntwk (one row per provider x network zip).
WHY  : Providers with multiple locations contribute distance from each zip.
SOURCE: mbr_with_zip (ours, all states) + mdcr_base_provider_mdcr_ntwk
GRAIN : provider x zip x network match
NOTE : Same recipe as FL Step5 -- only the state filter is opened to the scope states.
Run  : python expanded_scope/07_mbr_with_all_zips.py
"""

import config as cfg

OUT   = cfg.table("mbr_with_all_zips")
MBR   = cfg.base("mbr_with_zip")
NTWK  = cfg.src("mdcr_base_provider_mdcr_ntwk")
ABBR  = cfg.state_abbr_sql()

DDL = f"""
CREATE OR REPLACE TABLE `{OUT}`
OPTIONS (labels=[("owner", "deepan_thulasi_aetna_com")])
AS
SELECT
  m.*,
  n.zip_code                                                   AS additional_zip
FROM `{MBR}` m
JOIN (
  SELECT n.*, ntwk.ntwk_id_no AS ntwk_id
  FROM `{NTWK}` n
  CROSS JOIN UNNEST(n.network) AS ntwk
) n
  ON CAST(m.prvdr_id_no AS STRING) = CAST(n.pin AS STRING)
  AND CAST(n.ntwk_id AS STRING)   IN UNNEST(SPLIT(m.network_id, '-'))
WHERE m.state IN {ABBR}
  AND n.zip_code IS NOT NULL
"""

CHECKS = {
    "rows + providers per state (expect all 4 present)":
        f"SELECT state, COUNT(*) AS row_count, COUNT(DISTINCT prvdr_id_no) AS providers "
        f"FROM `{OUT}` GROUP BY state ORDER BY state",
}


def main():
    cfg.run_ddl(DDL, CHECKS)


if __name__ == "__main__":
    main()
