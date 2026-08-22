from __future__ import annotations

from datetime import date


def next_trade_session(trade_date: str, sessions: list[str] | None = None) -> str:
    current = date.fromisoformat(trade_date)
    if sessions is None:
        import akshare as ak

        from engine_v2.provider import bounded_call

        frame = bounded_call(45, ak.tool_trade_date_hist_sina, "A-share exchange calendar")
        sessions = [str(x)[:10] for x in frame["trade_date"].tolist()]
    future = sorted({x for x in sessions if x and date.fromisoformat(x) > current})
    if not future:
        raise RuntimeError(f"cannot resolve next A-share session after {trade_date}")
    return future[0]

