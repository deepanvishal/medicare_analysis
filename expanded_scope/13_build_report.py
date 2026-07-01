"""
13 - Build the combined multi-state compliance workbook.   [PYTHON / openpyxl]

WHAT   : Query ms_fact_gap_analysis (+ supporting tables) and build ONE Excel
         workbook covering all scope states, with a State filter column and a
         per-state rollup tab.
WHY    : Final deliverable. Replaces the FL-only Step4 report.
INPUT  : ms_fact_gap_analysis, ms_ref_hsd_required_counts,
         ms_ref_county_classification, ms_provider_par_flag,
         ms_stg_providers_multi_specialty
OUTPUT : medicare_supply_demand_ms.xlsx
         (tabs: overview, compliance, exec summary, county risk, methodology,
          data decisions)
NOTE   : Every tab carries a State column; county list is query-driven (no
         hardcoded COUNTY_DATA); all "Florida / 67 counties" labels generalized.
"""
# TODO: implement
