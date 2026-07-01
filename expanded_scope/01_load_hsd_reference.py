"""
01 - Load CMS HSD reference workbook -> multi-state required counts.   [PYTHON / BigQuery load]

WHAT   : Read data/ma_reference_file_12-17-2025.xlsx (wide 'Minimum Provider #s' +
         'Minimum Facility #s' sheets), unpivot to long grain, and build
         ms_ref_hsd_required_counts for all scope states. Also load the wide sheets
         to ms_ref_hsd_provider_min / ms_ref_hsd_facility_min.
WHY    : Replaces the 707 KB hand-typed UNNEST in the FL Step2. required_count is
         CMS-precalculated -> loaded as-is, never recalculated.
INPUT  : data/ma_reference_file_12-17-2025.xlsx   (national; keep scope states only)
         bigquery-public-data.geo_us_boundaries.counties  (county name+state -> county_fips)
OUTPUT : ms_ref_hsd_required_counts        grain: county_fips x cms_specialty
         ms_ref_hsd_provider_min, ms_ref_hsd_facility_min   (wide, per county)
NOTE   : Carries state_cd + county_fips. Explicit BQ schema, WRITE_TRUNCATE.
         Validate: FL reproduces 2,881 rows / 43 specialties.
"""
# TODO: implement
