WITH cl AS (
  SELECT DISTINCT UPPER(REPLACE(TRIM(pri_icd9_dx_cd), '.', '')) AS dx
  FROM `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.A870800_medicare_analysis_2025_claims`
  WHERE EXTRACT(YEAR FROM srv_start_dt) = 2024
)
SELECT
  COUNT(*) AS claims_distinct_codes,
  COUNTIF(m.diagnosis_code IS NOT NULL) AS found_in_map,
  ROUND(COUNTIF(m.diagnosis_code IS NOT NULL) / COUNT(*), 4) AS hit_rate
FROM cl
LEFT JOIN `anbc-hcb-dev.provider_ds_netconf_data_hcb_dev.HCC_ICD_Mapping_2025` m
  ON cl.dx = UPPER(TRIM(m.diagnosis_code));
