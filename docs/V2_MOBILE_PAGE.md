# V2 mobile read-only page

## Route and source

- Route: `/web/v2/`
- Live source: `v2-shadow/shadow_state/v2/summary.json`
- Same-origin fallback: `/web/v2/data.json`
- Funds: A/B/C/D/L

`engine_v2.shadow_reporting` derives both JSON files from `shadow_state/v2/ledgers/` and the immutable V2 audit chain. It never reads `state/`, `web/d/data.json`, or `web/e/data.json`.

The page is display-only. It contains no brokerage endpoint, no order-writing endpoint, and no Sol call. Its header and footer always identify it as “V2 影子盘 / 非实盘 / 当前未替代 V1”.

## Refresh the fallback locally

```bash
python -m engine_v2.shadow_reporting \
  --state-root shadow_state/v2 \
  --output shadow_state/v2/summary.json \
  --web-output web/v2/data.json
```

## Safe GitHub Pages publication

GitHub Pages is currently deployed from `main` through Actions. Do not switch the Pages source to `v2-shadow`, and do not deploy the whole shadow branch over the existing V1 site.

After the V2 page has passed branch checks, prepare a narrow, reviewable publish change based on `main` containing only the `/web/v2/` page, the landing-page link, and the minimal read-only summary fields needed by the page. Keep all V1 ledger files and V1 trading code byte-for-byte unchanged. The page can continue reading the public `v2-shadow/shadow_state/v2/summary.json` directly, so publishing the page shell does not give V2 control of V1 and does not require copying V2 ledgers into `main`.

