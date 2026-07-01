"""
12 - ms_provider_par_flag   [PYTHON runner / BigQuery DDL]   *** DEFERRED ***

WHAT : Provider participation flags -- Aetna claims activity + CMS Original Medicare
       participation, classified into status categories.
WHY  : Supplementary participation tabs. NOT part of the core compliance report
       (Test 1 + Test 2), so deferred until the core pipeline (01-11, 13) is validated.
SOURCE: mdcr_base_claim + ms_stg_providers_multi_specialty + ms_ref_county
        + xwalk_pin_npi_all + cms_medicare_physician_ffs_2023
GRAIN : provider x plan x specialty x county
NOTE : CMS FFS filter rndrng_prvdr_state_abrvtn IN scope states (not just FL).
Run  : python expanded_scope/12_provider_par_flag.py
"""

import config as cfg

# TODO: implement (deferred). Follows the 04-11 runner pattern:
#   DDL = f"CREATE OR REPLACE TABLE `{cfg.table('provider_par_flag')}` AS ..."
#   cfg.run_ddl(DDL, CHECKS)


def main():
    raise SystemExit("12_provider_par_flag is deferred -- not yet implemented.")


if __name__ == "__main__":
    main()
