# AI Stock Lab V0.1

Two independent mobile-first virtual A-share fund web apps:

- `/web/d/` — AI 综合基金 D, one independent ¥1,000,000 virtual account.
- `/web/e/` — AI 基金竞技场, three independent ¥1,000,000 virtual accounts (A/B/D).
- `/web/v2/` — read-only V2 shadow arena for five isolated A/B/C/D/L accounts; it does not replace V1.

## Hard boundaries

- No brokerage login.
- No real order endpoint.
- Main-board Shanghai/Shenzhen only; STAR, ChiNext and BSE excluded.
- AI proposes target weights only; deterministic code owns position limits, lot rounding, fees, slippage and T+1 accounting.

## Run V0.1 locally

```bash
python -m engine.daily_run --demo
python -m unittest discover -s tests -v
python -m http.server 8080 -d web
```

Open `http://localhost:8080/d/` and `http://localhost:8080/e/`.

The V2 shadow branch also exposes `http://localhost:8080/v2/`. Its primary data source is
`v2-shadow/shadow_state/v2/summary.json`; `web/v2/data.json` is an identical same-origin fallback.

## API configuration

Copy `.env.example` to your deployment secrets/environment variables. Never put a real key in front-end JavaScript.

- `AI_API_KEY`
- `AI_BASE_URL` (OpenAI-compatible `/v1` base)
- `AI_MODEL`

If no API is configured or the request fails, D uses a deterministic rule-based fallback rather than inventing a trade.

## Current milestone

V0.1 is the functioning UI + portfolio/risk/simulation skeleton with deterministic synthetic preview data. The real-market adapter exists for BaoStock, but the full-market bootstrap/cache job is intentionally the next milestone; the scheduled production job should not be enabled until that is tested.

## Beginner first deployment (V0.3)

For the first deployment, **do not configure an API key and do not enable the production schedule yet**.

1. Upload the repository to GitHub.
2. In Settings -> Pages, choose **GitHub Actions** as the source.
3. Open Actions -> **Deploy preview website** -> Run workflow.
4. Confirm the workflow succeeds and the `/d/` and `/e/` pages open correctly.
5. Only after the preview works should you configure `AI_API_KEY`, `AI_BASE_URL`, and `AI_MODEL`, then manually run **Daily virtual-fund run** once.
6. Re-enable the weekday cron only after that manual production run succeeds.

The preview workflow uses synthetic demo data and cannot place real trades or access brokerage accounts.

