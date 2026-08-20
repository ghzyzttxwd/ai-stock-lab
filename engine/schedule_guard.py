from __future__ import annotations
import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(os.getenv('FUND_STATE_DIR', str(ROOT / 'state')))
FUND_IDS = ('D_MAIN', 'A', 'B', 'C', 'D', 'L')


def all_funds_processed_on(trade_date: str, state_root: Path = STATE_ROOT) -> bool:
    """True only when all six persisted ledgers completed the same exchange session."""
    for fid in FUND_IDS:
        path = state_root / f'{fid}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return False
        if str(state.get('last_processed_date') or '')[:10] != trade_date:
            return False
    return True


def exchange_calendar_latest_session(requested_date: str, ak=None) -> str:
    """Resolve the exchange session independently from quote/daily-bar freshness.

    A quote provider can legitimately lag after the close. Treating that lag as evidence that
    today was a holiday caused the 2026-08-20 V1 settlement to be skipped. The schedule guard
    therefore uses an exchange calendar and fails closed if that calendar cannot classify the
    requested weekday.
    """
    requested = date.fromisoformat(requested_date)
    if ak is None:
        import akshare as ak

    try:
        frame = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        raise RuntimeError(f'Cannot load exchange trading calendar: {exc}') from exc

    if frame is None or frame.empty or 'trade_date' not in frame.columns:
        raise RuntimeError('Exchange trading calendar is empty or missing trade_date')

    dates = sorted({str(value)[:10] for value in frame['trade_date'].tolist() if str(value)[:10]})
    if not dates:
        raise RuntimeError('Exchange trading calendar contains no usable dates')
    if requested_date > dates[-1]:
        raise RuntimeError(
            f'Exchange trading calendar ends at {dates[-1]} and cannot classify {requested_date}'
        )

    valid = [value for value in dates if value <= requested_date]
    if not valid:
        raise RuntimeError(f'Exchange trading calendar has no session on or before {requested_date}')
    return valid[-1]


def scheduled_decision(requested_date: str, latest_trade_date: str, state_root: Path = STATE_ROOT) -> dict:
    """Pure decision used by workflows and unit tests.

    On an exchange holiday, a scheduled production run may safely skip market/Sol work only
    after every ledger already contains the most recent real trading session. AI preflight
    skips whenever today itself is not an exchange session.
    """
    is_trading_day = latest_trade_date == requested_date
    processed_latest = all_funds_processed_on(latest_trade_date, state_root)
    return {
        'requested_date': requested_date,
        'latest_trade_date': latest_trade_date,
        'is_trading_day': is_trading_day,
        'processed_latest': processed_latest,
        'production_run': is_trading_day or not processed_latest,
        'preflight_run': is_trading_day,
    }


def _write_github_output(values: dict) -> None:
    output = os.getenv('GITHUB_OUTPUT')
    if not output:
        return
    with open(output, 'a', encoding='utf-8') as f:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            f.write(f'{key}={value}\n')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=('production', 'preflight'))
    ap.add_argument('--date')
    args = ap.parse_args()

    requested = args.date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    latest = exchange_calendar_latest_session(requested)
    decision = scheduled_decision(requested, latest)
    should_run = decision['production_run'] if args.mode == 'production' else decision['preflight_run']

    _write_github_output({
        'run': should_run,
        'is_trading_day': decision['is_trading_day'],
        'latest_trade_date': latest,
        'processed_latest': decision['processed_latest'],
    })
    print(
        f'[schedule-guard] source=exchange-calendar mode={args.mode} requested={requested} latest={latest} '
        f'is_trading_day={decision["is_trading_day"]} '
        f'processed_latest={decision["processed_latest"]} run={should_run}'
    )


if __name__ == '__main__':
    main()
