-- 58_check_source_columns.sql
-- PURPOSE: find the procedure-code and modifier columns in the raw claims-line
--          table, for the extract rebuild feeding modules 60-61.
-- SOURCE : edp-prod-hcbstorage.edp_hcb_core_cnsv.EMIS_CLAIM_LINE
--          (copied verbatim from the FROM clause of expanded_scope/test_sql.sql,
--          the script that builds A870800_medicare_analysis_2025_claims).
-- Run manually in BigQuery console; paste results back.

SELECT
  column_name,
  data_type
FROM `edp-prod-hcbstorage.edp_hcb_core_cnsv.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'EMIS_CLAIM_LINE'
  AND REGEXP_CONTAINS(LOWER(column_name), r'prcdr|proc|hcpcs|cpt|mod')
ORDER BY ordinal_position;
