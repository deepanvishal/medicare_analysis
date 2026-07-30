# Provider Capacity & County Risk — Data Model (v2, frozen build spec)

Companion to `capacity_methodology_v2.md`. 15 tables. Projects: `anbc-hcb-dev` (tables), `anbc-dev-prv-nc-ds` (billing). SAFE_CAST / SAFE_DIVIDE throughout.

---

## 0. Lineage

```
ref_mpfs_time ──┐                      ref_segment ──────────────┐
                ▼                                                │
CMS PUF ─► cap_observed_detail ─► cap_hours_annual ──┐           │
Aetna MA claims ─┘    │                              ▼           ▼
                      ▼                    cap_provider_year ─► cap_provider_segment ◄─ cap_cohort_bench
            cap_hours_daily ─► cap_daily_capped ─────┘           │        ▲
                                                                 │        │
cap_params (calibration) — feeds every stage                     │   dc_v2 demand forecast
                                                                 ▼        │
                                              cap_fill_result ◄─ dem_segment_split
                                                    │
                                                    ▼
                                     cap_willing   cap_county_risk
                                                    │
                                                    ▼
                                              cap_validation
```

**Two-path constraint:** internal claims have `svc_dt` (true daily grain); CMS PUF is annual only (FTE-days estimated, tagged). `src` carried everywhere.

---

## 1. `ref_mpfs_time` — Stage 0 (module 60) (unchanged from v1)
Grain: hcpcs_cd.

| Column | Type | Notes |
|---|---|---|
| hcpcs_cd | STRING | PK |
| intra_mins | FLOAT64 | Only minutes used downstream |
| pre_mins, post_mins | FLOAT64 | Audit only |
| code_class_cd | STRING | 'EM'/'PROC'/'OTHER' |
| code_family_cd | STRING | For fallback-ladder averages |
| mpfs_cy | INT64 | 2025 |

## 2. `ref_segment` — NEW (Stage 0 (module 60))
Grain: segment_cd. 8 rows, single source of segment truth.

| Column | Type | Notes |
|---|---|---|
| segment_cd | STRING | PK, e.g. 'NEW_CHR_75P' |
| new_flag | INT64 | 1 new / 0 returning (12-mo lookback, DD-series) |
| chronic_flag | INT64 | Member has >=1 claim ICD mapping to HCC_v24 (HCC_v24 IS NOT NULL), 24-month lookback — identical to the live definition in 46/48. This is the only chronic definition in this repo. |
| age_band_cd | STRING | '60_74' / '75P' |
| segment_nm | STRING | Display name |

## 3. `cap_params` — Stage 2b (module 63) (extended)
Grain: param_nm × param_scope.

| Column | Type | Notes |
|---|---|---|
| param_nm | STRING | 'DEFLATION','DAILY_CAP_HRS','BENCH_PCTL','MIN_COHORT_N','FTE_DAY_HRS','CRED_K','HORIZON_FACTOR' |
| param_scope | STRING | code_class / specialty group / 'GLOBAL' |
| param_val | FLOAT64 | |
| derivation_nt | STRING | Method note |
| run_ts | TIMESTAMP | |

## 4. `cap_observed_detail` — Stage 1 (module 61) (modified: Type filter)
Key: npi × prvdr_county × src × period_start, plus hcpcs_cd for AETNA_MA rows; CMS_FFS rows have hcpcs_cd = NULL and one row per npi × county × year. Grouping is by BOTH provider keys (npi + epdb_dw_prvdr_id).

| Column | Type | Notes |
|---|---|---|
| npi | STRING | PK part. **NPI Type 1 only** (Type 2 excluded upstream) |
| epdb_dw_prvdr_id | STRING | Key part, internal Aetna id; npi NULL when xwalk-unmatched |
| specialty_ctg_cd | STRING | |
| prvdr_county | STRING | PK part. **Never mbr_county_cd** |
| prvdr_state_cd | STRING | Internal: UPPER(LEFT(prvdr_submarket, 2)); CMS: rndrng_prvdr_state_abrvtn |
| hcpcs_cd | STRING | Key part for AETNA_MA rows only; NULL on CMS_FFS rows |
| src | STRING | PK part. 'CMS_FFS'/'AETNA_MA'. 'CMS_FFS' rows come from cms_medicare_physician_ffs_2023 at provider-year grain with hcpcs_cd = NULL (the summary file has no procedure detail); only 'AETNA_MA' rows carry hcpcs_cd |
| period_type | STRING | 'YEAR' (CMS) / 'DAY' (Aetna; period_start = svc_start_dt — one claims scan serves modules 61 and 62) |
| period_start | DATE | PK part |
| svc_cnt | INT64 | |
| mbr_cnt | INT64 | CMS suppressed <11 → NULL, never 0 |
| load_ts | TIMESTAMP | |

## 5. `cap_hours_daily` — Stage 2 (module 62) (unchanged; internal only)
Grain: npi × prvdr_county × svc_dt.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county, svc_dt | | PK |
| raw_hrs | FLOAT64 | No deflation |
| defl_hrs | FLOAT64 | Deflated |
| unmapped_svc_cnt | INT64 | Feeds V1 |
| fallback_svc_cnt | INT64 | Services timed via fallback ladder |
| src | STRING | 'AETNA_MA' |

## 6. `cap_hours_annual` — Stage 2 (module 62) (unchanged)
Grain: npi × prvdr_county × src.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county, src | | PK |
| period_yr | INT64 | 2023 CMS / 2024 / 2025 internal — vintages never mixed |
| specialty_ctg_cd | STRING | |
| raw_hrs_yr | FLOAT64 | For src = 'CMS_FFS': med_tot_srvcs x avg_mins_per_svc — code-level minutes apply to the internal side only |
| defl_hrs_yr | FLOAT64 | NULL until module 64 |
| avg_mins_src_cd | STRING | 'OWN'/'COHORT'/NULL |
| svc_cnt_yr, mapped_svc_cnt, unmapped_svc_cnt | INT64 | |
| avg_mins_per_svc | FLOAT64 | Hours↔visits conversion |
| ceiling_unit_cd | STRING | 'HOURS' / 'VISITS' (per-specialty fallback, CD-19) |

## 7. `cap_daily_capped` — Stage 3 (module 64) (unchanged)
Grain: npi × prvdr_county × svc_dt.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county, svc_dt | | PK |
| defl_hrs, capped_hrs | FLOAT64 | Cap from cap_params |
| frac_day | FLOAT64 | min(defl_hrs/8, 1) |
| impossible_day_flag | INT64 | raw > 24 |
| high_day_flag | INT64 | defl > cap (team-billing signal) |

## 8. `cap_provider_year` — Stage 4 (module 65) (modified)
Grain: npi × prvdr_county.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county | | PK |
| specialty_ctg_cd, county_band_cd | STRING | Band from CMS SSA file |
| capped_hrs_yr | FLOAT64 | |
| fte_days_yr | FLOAT64 | Internal Σ frac_day; CMS estimated |
| fte_days_src_cd | STRING | 'OBSERVED'/'ESTIMATED' — must survive to outputs |
| hrs_per_fte_day | FLOAT64 | |
| ceiling_low_hrs | FLOAT64 | bench_rate × own fte_days × county_alloc_share |
| ceiling_high_hrs | FLOAT64 | bench_rate × cohort median days × county_alloc_share |
| county_alloc_share | FLOAT64 | Σ per npi = 1.0 (checked) |
| spare_hrs | FLOAT64 | ceiling_low − observed capped hrs, floor 0 |
| util_ratio | FLOAT64 | observed ÷ ceiling_low |
| impossible_day_cnt | INT64 | NULL for CMS-only |
| src_mix_cd | STRING | 'AETNA_ONLY'/'CMS_ONLY'/'BOTH' |

## 9. `cap_cohort_bench` — Stage 5 (module 66 cohort side, module 67 provider side) (modified: intake rates added)
Grain: specialty_ctg_cd × county_band_cd × segment_cd (segment_cd = 'ALL' row for ceiling benchmarks).

| Column | Type | Notes |
|---|---|---|
| specialty_ctg_cd, county_band_cd, segment_cd | | PK ('ALL' = ceiling row) |
| bench_rate_hrs_day | FLOAT64 | Populated on 'ALL' row |
| median_fte_days | FLOAT64 | 'ALL' row |
| cohort_intake_rate | FLOAT64 | New patients/active month, per segment row |
| avg_first_yr_hrs | FLOAT64 | Per segment: hours a new patient consumes in yr 1 (for total-constraint conversion) |
| n_npi | INT64 | |
| fallback_flag | INT64 | Rolled to state × specialty |
| boot_ci_width_pct | FLOAT64 | Resampling result |

## 10. `cap_provider_segment` — NEW (Stage 6). The matrix.
Grain: npi × prvdr_county × segment_cd.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county, segment_cd | | PK |
| panel_cnt | INT64 | Current patients in segment |
| panel_share | FLOAT64 | Σ per npi×county = 1.0 |
| own_intake_rate | FLOAT64 | New patients/active month |
| n_cell | INT64 | Patient count behind own rate |
| cred_w | FLOAT64 | n_cell / (n_cell + k) |
| blended_rate | FLOAT64 | w×own + (1−w)×cohort |
| signal_src_cd | STRING | 'OWN' (w≥0.5) / 'BORROWED' |
| closed_door_flag | INT64 | Zero intake across ALL segments → 1 (all cells) |
| cell_cap_cnt | FLOAT64 | blended_rate × 12 × horizon_factor |
| cell_cap_scaled_cnt | FLOAT64 | After proportional scale-down to total constraint (spare_hrs ÷ avg_first_yr_hrs) |

## 11. `dem_segment_split` — NEW (Stage 7 (module 68)). Demand side.
Grain: mbr_county_cd × cms_specialty × segment_cd. **Demand grain = member county** (attribution rule).

| Column | Type | Notes |
|---|---|---|
| mbr_county_cd, cms_specialty, segment_cd | | PK |
| anchor_demand | FLOAT64 | From dc_v2 forecast (county × specialty total) |
| segment_share | FLOAT64 | Observed; Σ per county×specialty = 1.0 (V5) |
| segment_demand | FLOAT64 | anchor × share |
| growth_demand | FLOAT64 | Projected/scenario growth in segment (new patients to place) |
| growth_src_cd | STRING | 'FORECAST' / 'SCENARIO' (dashboard slider) |

Specialty bridge (`ref_specialty_crosswalk`) applied exactly once, at the fill join — same rule as notebook 55.

## 12. `cap_fill_result` — NEW (Stage 8 (module 69))
Grain: npi × prvdr_county × segment_cd (provider rows) + county remainder rows.

| Column | Type | Notes |
|---|---|---|
| mbr_county_cd | STRING | PK part — demand origin |
| cms_specialty, segment_cd | STRING | PK part |
| npi | STRING | PK part; NULL on remainder rows |
| prvdr_county | STRING | NULL on remainder rows |
| seg_market_share | FLOAT64 | Re-normalized over open doors |
| pass1_alloc_cnt | FLOAT64 | |
| returned_cnt | FLOAT64 | Above caps |
| pass2_alloc_cnt | FLOAT64 | |
| placed_cnt | FLOAT64 | pass1 − returned + pass2 |
| unplaced_cnt | FLOAT64 | Remainder rows only |
| conservation_ok_flag | INT64 | Σ placed + unplaced = growth_demand (V6) |

## 13. `cap_willing` — Stage 9 (module 70) (simplified)
Grain: npi × prvdr_county.

| Column | Type | Notes |
|---|---|---|
| npi, prvdr_county | | PK |
| contracted_flag | INT64 | From ms_ network table |
| aetna_ma_svc_cnt, cms_ffs_svc_cnt | INT64 | |
| aetna_share | FLOAT64 | SAFE_DIVIDE(a, a+c), [0,1] |
| share_stability_flag | INT64 | Quarterly swing beyond tolerance |
| zero_utilization_flag | INT64 | Forces willing to 0 |
| willing_spare_hrs | FLOAT64 | spare_hrs × aetna_share (0 if flagged) |
| willing_placed_cnt | FLOAT64 | Σ placed × aetna_share |

## 14. `cap_county_risk` — NEW (Stage 10 (module 71)). Final deliverable.
Grain: mbr_county_cd × cms_specialty × segment_cd.

| Column | Type | Notes |
|---|---|---|
| mbr_county_cd, cms_specialty, segment_cd | | PK |
| growth_demand | FLOAT64 | |
| placed_cnt | FLOAT64 | |
| unplaced_cnt | FLOAT64 | The risk number |
| unplaced_pct | FLOAT64 | unplaced ÷ growth_demand |
| n_open_providers | INT64 | |
| n_maxed_providers | INT64 | Crossed 100% in simulation |
| borrowed_signal_pct | FLOAT64 | Share of placed volume routed via BORROWED cells — honesty metric |
| risk_rank | INT64 | Within state, by unplaced_pct then unplaced_cnt |

## 15. `cap_validation` — Stage 11 (extended)
Grain: metric_cd × scope. Long format.

| Column | Type | Notes |
|---|---|---|
| metric_cd | STRING | 'V1'–'V10', 'M1'–'M3' |
| scope | STRING | county×specialty / specialty / 'GLOBAL' |
| metric_val | FLOAT64 | |
| pass_flag | INT64 | NULL for report-only |
| run_ts | TIMESTAMP | |
| note_txt | STRING | |

---

## Cross-cutting rules

1. **Attribution:** capacity tables = `prvdr_county`; demand tables = `mbr_county_cd`; the fill join is the only place both appear, each in its own role. Mandatory review check in every build script.
2. **NPI Type 1 filter** applied once, in Stage 1, inherited everywhere.
3. **`cap_params` is the only tuning source** — literals downstream are defects.
4. **`fte_days_src_cd` and `signal_src_cd` must survive to final outputs** — estimated vs observed, own vs borrowed always separable.
5. **county_alloc_share and panel_share sum to 1.0** per NPI — checked, logged.
6. **Specialty bridge exactly once** (fill join), per notebook 55 rule.
7. **Suppressed CMS values** → SAFE_CAST → NULL, never 0.
8. **Aetna share applied only in Stage 9** — never in matrix, fill, or ceiling.
9. Vintages carried in `period_start`; never mixed silently.
10. All cap_/ref_ tables in modules 59-72 are created and read via cfg.table() (house prefix). A bare table name in any 59-72 script is a defect.
11. CMS-only providers (no internal rows) carry NULL hours and no specialty_ctg_cd — acceptable because zero Aetna volume means zero willing capacity under Stage 9 rules; revisit only for total-market analyses (decision CD-21).
12. County is NEVER a key alone — every join, group-by, and output grain that uses prvdr_county or mbr_county_cd must pair it with its state column. County names repeat across the four scope states (Crawford, Marion, etc.).

## Open items for confirmation

| # | Item | Default |
|---|---|---|
| 1 | Contract source for `contracted_flag` | ms_ pipeline network table |
| 2 | `avg_first_yr_hrs` per segment computed from internal claims (new-patient first-12-month hours) | Yes |
| 3 | Growth input for v1: dc_v2 forecast delta or dashboard scenario slider | Both supported via `growth_src_cd` |
