# V2 Refactor Plan

## Goal

Reduce operational complexity without changing V2 trading economics, historical ledgers, audit history, execution model, or public data semantics.

This refactor must be behavior-preserving first. No cleanup PR may silently recompute historical trades or rewrite append-only audit history.

## Current problems

1. Too much operational/business validation is embedded directly in large GitHub Actions YAML files.
2. Multiple date-specific repair workflows and incident scripts remain beside production workflows.
3. Several overlapping verification/status files can disagree about which one is authoritative.
4. The same invariants are checked in multiple Bash/Python snippets, making drift likely.
5. main / v2-shadow / Pages synchronization and receipt generation are spread across several workflows.
6. Historical incident tooling and normal production tooling are insufficiently separated.

## Non-negotiable invariants

- Do not rewrite or delete historical V2 audit events.
- Do not alter historical fills, quantities, prices, fees, cash, NAV, or positions unless a separately approved audited restatement is explicitly requested.
- `V2_CONDITIONAL_PLAN_V1` and `v2-conditional-plan-v1` remain the production model/version.
- Conditional targets must preserve valid per-target `trade_plan` data.
- Legacy conditional-state `execution_catchup` stays fail-closed.
- No negative cash.
- A session is terminal only when the canonical audit head and all ledger content hashes agree.
- Public Pages acceptance requires byte-for-byte SHA256 equality with the exact main artifact.
- Failed historical receipts must never masquerade as current health status.

## Target architecture

### 1. Put verification logic in Python, not YAML

Create a small reusable module, e.g.:

- `engine_v2/invariants.py`
- `engine_v2/ops.py`

Suggested CLI:

```text
python -m engine_v2.ops verify-state --trade-date YYYY-MM-DD
python -m engine_v2.ops build-web
python -m engine_v2.ops verify-public --expected-file web/v2/data.json --url ...
python -m engine_v2.ops write-receipt --trade-date YYYY-MM-DD ...
```

GitHub Actions should orchestrate these commands instead of containing long inline Python programs.

### 2. One authoritative current-health receipt

Introduce a single current status file, for example:

`state/v2_current_status.json`

It should point to immutable/historical receipts rather than duplicating all history.

Recommended fields:

- `status`
- `trade_date`
- `end_to_end_verified`
- `audit_head`
- `v2_shadow_commit`
- `main_commit`
- `expected_sha256`
- `public_sha256`
- `verified_at_utc`
- `source_receipt`

Historical failures/restatements remain preserved but must carry `superseded` metadata when no longer current.

### 3. Separate production from incident recovery

Production workflows should be few and stable. Target set:

- V2 checkpoint / intraday guard
- V2 canonical settlement
- V2 code regression
- canonical Pages deployment / public verification

Date-specific repair workflows should be moved out of the normal production path after acceptance, preferably into an archive/documented incident area or disabled manual-only form.

### 4. Reusable workflow or composite action

Factor repeated checkout / v2-shadow sync / hash / Pages polling / receipt writeback steps into one reusable workflow or composite action.

Do not maintain multiple slightly different implementations of the same terminal acceptance logic.

### 5. Explicit state machine

Represent V2 lifecycle states explicitly:

`checkpointed -> settled -> web_built -> main_synced -> pages_deployed -> public_verified`

A failure must record the failed stage, but current health must be derived from the latest successful chain for the same canonical artifact, not from whichever status file happens to be read first.

## Refactor PR sequence

### PR 1 — Characterization tests only

- Add tests that capture current production behavior and receipts.
- No production logic changes.
- Snapshot exact current V2 state/public-contract semantics.

### PR 2 — Extract terminal invariants from YAML

- Move inline verification Python to `engine_v2/invariants.py` / `engine_v2/ops.py`.
- Workflows call the module.
- Exact same acceptance results before/after.

### PR 3 — Unify current status / receipt precedence

- Add `state/v2_current_status.json`.
- Make stale failed receipts explicitly historical/superseded.
- Add a test proving an older failure cannot override a newer verified chain.

### PR 4 — Consolidate GitHub Actions

- Remove duplicate terminal/public verification implementations.
- Reuse one canonical deployment + public SHA verification path.
- Preserve schedules and permissions.

### PR 5 — Archive incident-only tooling

- Archive or disable completed date-specific repair workflows after proving they are no longer part of production execution.
- Keep immutable audit evidence and receipts.

### PR 6 — Final architecture regression

Require all of the following:

- full V2 unit test suite passes
- audit chain PASS
- current canonical state unchanged economically
- current ledger content hashes unchanged
- web artifact semantics unchanged
- no production workflow calls forbidden legacy catch-up for conditional state
- exactly one canonical settlement implementation
- exactly one canonical public-byte verification implementation
- latest authoritative status is unambiguous

## Codex execution instructions

Work in small PRs. Do not perform a broad rewrite in one branch.

For every PR:

1. Explain what duplication/debt is being removed.
2. Prove behavior preservation with tests before changing architecture.
3. Show before/after workflow entry points.
4. State whether any ledger/audit/web bytes changed and why.
5. Stop if a proposed refactor changes trading economics.
6. Never delete historical audit evidence merely to simplify code.

The preferred result is boring infrastructure: thin YAML, tested Python invariants, one status authority, one settlement path, one publication verification path.