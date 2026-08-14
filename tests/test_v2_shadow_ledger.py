import copy
import json
import tempfile
import unittest
from pathlib import Path

from engine.broker import (
    _locked_at_limit as v1_locked_at_limit,
    fee_for as v1_fee_for,
    round_lot as v1_round_lot,
    slipped_price as v1_slipped_price,
)
from engine_v2.history_cache import adjustment_drift, merge_history_rows
from engine_v2.shadow_ledger import (
    EXECUTION_POLICY_VERSION,
    execute_pending,
    fee_for,
    immutable_write,
    locked_at_limit,
    new_ledger,
    round_lot,
    slipped_price,
)
from engine_v2.shadow_run import run_shadow_session
from engine_v2.shadow_reporting import ledger_metrics, verify_audit_chain
from engine_v2.shadow_guard import processed_session


def target(symbol='sh.600000', weight=0.10, name='样本银行'):
    return {
        'symbol': symbol, 'name': name, 'industry': '银行',
        'target_weight': weight, 'v2_score': 80,
        'thesis': '测试买入逻辑', 'invalidation': '测试失效条件',
    }


def pending(decision_date='2026-08-14', targets=None):
    return {
        'decision_date': decision_date, 'targets': [target()] if targets is None else targets,
        'target_version': 'test', 'calls_sol': False,
    }


def bar(opening=10.0, close=10.2, previous=9.8, tradestatus='1'):
    return {
        'open': opening, 'high': max(opening, close), 'low': min(opening, close),
        'close': close, 'preclose': previous, 'tradestatus': tradestatus,
    }


class V2ShadowLedgerTests(unittest.TestCase):
    def test_execution_policy_matches_v1_assumptions(self):
        self.assertEqual(EXECUTION_POLICY_VERSION, 'v1-parity-2026-08-14')
        for gross in (1_000, 100_000):
            for side in ('BUY', 'SELL'):
                self.assertEqual(fee_for(side, gross), v1_fee_for(side, gross))
                self.assertEqual(slipped_price(side, 12.34), v1_slipped_price(side, 12.34))
        self.assertEqual(round_lot(999), v1_round_lot(999))
        for side in ('BUY', 'SELL'):
            sample = {'open': 11.0 if side == 'BUY' else 9.0, 'preclose': 10.0}
            self.assertEqual(locked_at_limit(side, sample), v1_locked_at_limit(side, sample))

    def test_buy_uses_next_open_slippage_fee_and_board_lot(self):
        state = new_ledger('A', '2026-08-14')
        result = execute_pending(state, pending(), {'sh.600000': bar()}, '2026-08-17')
        self.assertEqual(len(result['fills']), 1)
        fill = result['fills'][0]
        self.assertEqual(fill['side'], 'BUY')
        self.assertEqual(fill['price'], 10.005)
        self.assertEqual(fill['qty'] % 100, 0)
        self.assertLess(state['cash'], 1_000_000)
        self.assertEqual(state['positions']['sh.600000']['acquired_date'], '2026-08-17')

    def test_limit_lock_and_t_plus_one_are_audited(self):
        state = new_ledger('A', '2026-08-14')
        result = execute_pending(
            state, pending(), {'sh.600000': bar(opening=11.0, previous=10.0)}, '2026-08-17',
        )
        self.assertFalse(result['fills'])
        self.assertEqual(result['rejected_orders'][0]['reason'], 'limit_up_locked')

        state = new_ledger('A', '2026-08-14')
        state['cash'] = 900_000
        state['positions'] = {
            'sh.600000': {'name': '样本银行', 'qty': 10_000, 'avg_cost': 10.0, 'last_price': 10.0, 'acquired_date': '2026-08-17'}
        }
        result = execute_pending(state, pending(targets=[]), {'sh.600000': bar()}, '2026-08-17')
        self.assertFalse(result['fills'])
        self.assertEqual(result['rejected_orders'][0]['reason'], 't_plus_one_locked')

    def test_d_new_position_cap_is_rechecked_at_execution(self):
        state = new_ledger('D', '2026-08-14')
        result = execute_pending(
            state, pending(targets=[target(weight=0.15)]), {'sh.600000': bar()}, '2026-08-17',
        )
        self.assertEqual(result['policy_adjustments'][0]['applied_weight'], 0.10)
        self.assertLessEqual(result['fills'][0]['gross'], 100_000)

    def test_ledger_metrics_include_cost_turnover_and_industry_concentration(self):
        state=new_ledger('B','2026-08-14')
        state['cash']=500_000
        state['positions']={
            'sh.600000':{'qty':30_000,'avg_cost':10,'last_price':10,'industry':'银行'},
            'sh.600001':{'qty':20_000,'avg_cost':10,'last_price':10,'industry':'有色'},
        }
        state['fills']=[{'gross':500_000,'fees':155}]
        state['equity_curve']=[{'date':'2026-08-14','equity':1_000_000},{'date':'2026-08-17','equity':1_010_000}]
        metrics=ledger_metrics(state)
        self.assertEqual(metrics['return_pct'],1.0)
        self.assertEqual(metrics['fees'],155)
        self.assertEqual(metrics['turnover_on_initial_capital'],0.5)
        self.assertAlmostEqual(metrics['industry_hhi'],0.52)

    def test_audit_event_is_immutable_but_identical_retry_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '2026-08-14.json'
            self.assertTrue(immutable_write(path, {'date': '2026-08-14', 'value': 1}))
            self.assertFalse(immutable_write(path, {'date': '2026-08-14', 'value': 1}))
            with self.assertRaisesRegex(RuntimeError, 'refusing to rewrite'):
                immutable_write(path, {'date': '2026-08-14', 'value': 2})

    def test_history_cache_merge_is_deduplicated_and_bounded(self):
        cached = [{'date': f'2026-07-{i:02d}', 'close': i} for i in range(1, 21)]
        fetched = [{'date': '2026-07-20', 'close': 200}, {'date': '2026-07-21', 'close': 21}]
        merged = merge_history_rows(cached, fetched, history_limit=10)
        self.assertEqual(len(merged), 10)
        self.assertEqual(merged[-2]['close'], 200)
        self.assertEqual(merged[-1]['date'], '2026-07-21')
        self.assertFalse(adjustment_drift(cached, [{'date': '2026-07-20', 'close': 20.0}]))
        self.assertTrue(adjustment_drift(cached, [{'date': '2026-07-20', 'close': 18.0}]))

    def _enriched(self, trade_date):
        candidates = []
        for i in range(120):
            industry = f'I{i % 12}'
            previous = 9.8 + i * 0.01
            opening = previous * 1.01
            close = opening * 1.01
            candidates.append({
                'raw_code': f'{600000+i:06d}', 'code': f'sh.{600000+i:06d}',
                'symbol': f'sh.{600000+i:06d}', 'name': f'S{i}',
                'industry': industry, 'industry_code': f'{i % 12:02d}',
                'correlation_cluster': industry,
                'fundamental_ready': True, 'financial_distress': False,
                'quality_score': 62 + (i % 25), 'cashflow_score': 58 + (i % 22),
                'valuation_score': 45 + ((i * 7) % 50), 'risk': 55 + ((i * 3) % 40),
                'trend': 58 + ((i * 5) % 38), 'momentum': 50 + ((i * 11) % 45),
                'liquidity': 55 + ((i * 13) % 40), 'industry_score': 56 + ((i * 17) % 40),
                'leader_score': 58 + ((i * 19) % 38), 'theme_score': 55 + ((i * 23) % 40),
                'sentiment_score': 48 + ((i * 29) % 48),
                'breakout_quality': 50 + ((i * 31) % 45),
                'crowding_score': 35 + ((i * 37) % 55), 'vol20': 0.016 + (i % 12) * 0.0015,
                'one_word_limit': False, 'open': opening, 'close': close,
                'high': close, 'low': previous, 'preclose': previous,
                'tradestatus': '1', 'source': 'fixture',
            })
        return {
            'enrichment_version': 'fixture-1', 'trade_date': trade_date,
            'market': {'regime': {'label': 'neutral', 'score': 60, 'confidence': 70, 'reasons': []}},
            'industry': {'counts': {'industries': 12, 'mainboard_stocks': 120}},
            'coverage': {'preselected': 120, 'feature_ratio': 1.0},
            'candidates': candidates,
            'safety': {'ready_for_strategy_targets': True, 'calls_sol': False},
        }

    def _snapshot(self, trade_date):
        return {
            'snapshot_version': 'fixture-1', 'trade_date': trade_date,
            'source_notes': {'stock_snapshot': 'fixture'},
            'industry': {'counts': {'industries': 12, 'mainboard_stocks': 120}},
            'safety': {'writes_ledgers': False, 'calls_sol': False},
        }

    def test_full_five_fund_session_is_idempotent_and_executes_previous_targets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = run_shadow_session(
                self._snapshot('2026-08-14'), self._enriched('2026-08-14'), root,
                previous_trade_date='2026-08-13', allow_network_bars=False,
            )
            self.assertEqual(first['status'], 'processed')
            retry = run_shadow_session(
                self._snapshot('2026-08-14'), self._enriched('2026-08-14'), root,
                previous_trade_date='2026-08-13', allow_network_bars=False,
            )
            self.assertEqual(retry['status'], 'already_processed')
            guarded=processed_session(root,'2026-08-14')
            self.assertEqual(guarded['event_hash'],first['event_hash'])
            self.assertIsNone(processed_session(root,'2026-08-17'))

            second = run_shadow_session(
                self._snapshot('2026-08-17'), self._enriched('2026-08-17'), root,
                previous_trade_date='2026-08-14', allow_network_bars=False,
            )
            self.assertEqual(second['status'], 'processed')
            self.assertTrue(all(count > 0 for count in second['fills'].values()))
            verification=verify_audit_chain(root)
            self.assertEqual(verification['status'],'PASS')
            self.assertEqual(verification['events'],2)
            for fund_id in ('A', 'B', 'C', 'D', 'L'):
                state = json.loads((root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
                self.assertEqual(state['initial_cash'], 1_000_000)
                self.assertEqual(len(state['equity_curve']), 2)
                self.assertFalse(state['pending_decision']['calls_sol'])
                self.assertTrue(state['audit_head'])


if __name__ == '__main__':
    unittest.main()
