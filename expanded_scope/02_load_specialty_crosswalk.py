"""
02 - Load specialty crosswalk (Aetna specialty_cd -> 43 CMS specialties).   [PYTHON / BigQuery load]

WHAT   : Load the specialty mapping CSV to ms_ref_specialty_crosswalk_expanded and
         derive ms_ref_specialty_crosswalk.
WHY    : Supply-side specialty normalization. State-agnostic -- identical for every
         state. Replaces the hardcoded UNNEST in the FL Step3.
INPUT  : cms_to_aetna_final (2).csv
OUTPUT : ms_ref_specialty_crosswalk_expanded   grain: cms_specialty x aetna_code
         ms_ref_specialty_crosswalk             grain: aetna_cd
NOTE   : Excludes pediatric / telehealth / palliative / PH catch-all codes.
"""
# TODO: implement
