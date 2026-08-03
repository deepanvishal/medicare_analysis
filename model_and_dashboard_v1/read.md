# Dashboard launch recipes

## v2 (current) — whatif_dashboard_v2.py

```
python model_and_dashboard_v1/07_dashboard/whatif_dashboard_v2.py
```

- Plain python, no env var needed: the proxy prefix defaults to
  `/proxy/8051/` (override with `DASH_PROXY_PREFIX` only if your proxy
  differs).
- Port **8051**; open `<host>/proxy/8051/` behind the office proxy, or
  `http://localhost:8051/proxy/8051/` locally.
- Watch the console: `[startup]` and `[county-risk]` progress prints show
  extract load, capacity-table load, and precompute; the server line
  `starting server on :8051` means it is ready.
- Needs the eight parquet extracts + manifest.json in
  `07_dashboard/extracts/` (regenerate with `21_dashboard_extracts.py`)
  and BigQuery access for the County Risk tab (tab degrades to an error
  card without it).

## v1 (kept as-is) — whatif_dashboard.py

```
python model_and_dashboard_v1/whatif_dashboard.py
```

- Port **8050**; proxy prefix only applied when the `DASH_PROXY_PREFIX`
  env var is set (unlike v2 there is no default).
