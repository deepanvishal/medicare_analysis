"""
03 - Load CMS time & distance standards.   [PYTHON / BigQuery load]

WHAT   : Build ms_ref_time_distance -- max time (min), max distance (miles), and
         min_ratio_per_1000 per cms_specialty x county_type.
WHY    : Drives Test 1 distance thresholds and county_type sensitivity.
         State-agnostic (keyed on county_type, not state).
INPUT  : ma_reference_file_12-17-2025.xlsx sheets 'Provider Time & Distance' +
         'Facility Time & Distance'   (national CMS standards)
OUTPUT : ms_ref_time_distance   grain: cms_specialty x county_type
NOTE   : Facility types -> min = 1 flat; Acute Inpatient -> 12.2 beds / 1000.
"""
# TODO: implement
