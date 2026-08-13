from __future__ import annotations
import json
from pathlib import Path
from .config import CONFIG


def load_state(path: Path, fund_id: str, name: str) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {
        'fund_id': fund_id, 'name': name, 'initial_cash': CONFIG.initial_cash,
        'cash': CONFIG.initial_cash, 'positions': {}, 'fills': [], 'equity_curve': [],
        'pending_targets': [], 'decisions': [], 'created_at': None
    }


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
