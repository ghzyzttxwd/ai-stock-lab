import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from engine.universe import is_main_board
from engine.risk import clamp_d_targets
from engine.broker import round_lot, fee_for, _locked_at_limit
from engine.daily_run import (
    _previous_trade_date,
    _pending_is_fresh,
    _merge_recovery_universe,
    _readonly_portfolio_snapshot,
    _decision_diary,
)
from engine.real_market import AKShareMarket, _tx_amount_mode, _tx_amount_and_volume
from engine.schedule_guard import FUND_IDS, scheduled_decision

class EngineTests(unittest.TestCase):
    def test_board_filter(self):
        self.assertTrue(is_main_board('sh.600519'))
        self.assertTrue(is_main_board('sh.603000'))
        self.assertTrue(is_main_board('sz.000001'))
        self.assertTrue(is_main_board('sz.002594'))
        self.assertFalse(is_main_board('sh.688001'))
        self.assertFalse(is_main_board('sz.300750'))
        self.assertFalse(is_main_board('bj.920001'))

    def test_lot_rounding(self):
        self.assertEqual(round_lot(999), 900)
        self.assertEqual(round_lot(99), 0)

    def test_d_risk_cap(self):
        targets=[{'symbol':f'sh.600{i:03d}','name':'x','target_weight':.30} for i in range(12)]
        clean,notes=clamp_d_targets(targets)
        self.assertLessEqual(len(clean),10)
        self.assertLessEqual(max(x['target_weight'] for x in clean),.15)
        self.assertLessEqual(sum(x['target_weight'] for x in clean),.90+1e-9)
        self.assertTrue(notes)

    def test_limit_lock(self):
        self.assertTrue(_locked_at_limit('BUY', {'open':11.0,'preclose':10.0}))
        self.assertTrue(_locked_at_limit('SELL', {'open':9.0,'preclose':10.0}))
        self.assertFalse(_locked_at_limit('BUY', {'open':10.5,'preclose':10.0}))

    def test_min_commission(self):
        self.assertEqual(fee_for('BUY',1000),5.0)
        self.assertGreater(fee_for('SELL',100000), fee_for('BUY',100000))

    def test_previous_trade_date(self):
        histories={
            'sh.600000':[
                {'date':'2026-08-12'},
                {'date':'2026-08-13'},
                {'date':'2026-08-14'},
            ]
        }
        self.assertEqual(_previous_trade_date(histories,'2026-08-14'),'2026-08-13')

    def test_pending_must_be_from_immediately_previous_trade_date(self):
        state={
            'pending_targets':[{'symbol':'sh.600000','target_weight':0.1}],
            'pending_decision_date':'2026-08-13',
            'decisions':[],
        }
        self.assertTrue(_pending_is_fresh(state,'2026-08-13'))
        self.assertFalse(_pending_is_fresh(state,'2026-08-14'))

    def test_recovery_universe_keeps_critical_symbols(self):
        cached=[{'code':'sh.600000','name':'浦发银行','peTTM':6.0}]
        critical={'sh.600000':'浦发银行','sz.000001':'平安银行'}
        merged=_merge_recovery_universe(cached,critical)
        by_code={x['code']:x for x in merged}
        self.assertIn('sh.600000',by_code)
        self.assertIn('sz.000001',by_code)
        self.assertEqual(by_code['sz.000001']['name'],'平安银行')
        self.assertEqual(by_code['sh.600000']['peTTM'],6.0)

    def test_tencent_history_snapshot_uses_current_and_previous_bar(self):
        market=AKShareMarket.__new__(AKShareMarket)
        selected=[{'code':'sh.600000','name':'浦发银行','peTTM':6.0,'pbMRQ':0.7}]
        histories={
            'sh.600000':[
                {'date':'2026-08-13','open':10.0,'high':10.2,'low':9.9,'close':10.1,'amount':100000000},
                {'date':'2026-08-14','open':10.2,'high':10.6,'low':10.1,'close':10.5,'amount':120000000},
            ]
        }
        rows=market.snapshot_from_histories(selected,histories,'2026-08-14')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['open'],10.2)
        self.assertEqual(rows[0]['close'],10.5)
        self.assertEqual(rows[0]['preclose'],10.1)
        self.assertEqual(rows[0]['source'],'tencent-cache')

    def test_duplicate_snapshot_keeps_holdings_without_mutating_curve(self):
        state={
            'cash':500000.0,
            'positions':{
                'sh.600000':{'name':'浦发银行','qty':10000,'avg_cost':10.0,'last_price':10.0}
            },
            'equity_curve':[{'date':'2026-08-14','equity':600000.0}],
        }
        before=list(state['equity_curve'])
        mtm=_readonly_portfolio_snapshot(state,{'sh.600000':{'close':10.5}})
        self.assertEqual(len(mtm['holdings']),1)
        self.assertEqual(mtm['holdings'][0]['market_value'],105000.0)
        self.assertEqual(mtm['equity'],605000.0)
        self.assertEqual(state['equity_curve'],before)

    def test_duplicate_refresh_preserves_real_diary(self):
        state={
            'decisions':[
                {'date':'2026-08-13','diary':'昨天'},
                {'date':'2026-08-14','diary':'今天真实决策日记'},
            ]
        }
        self.assertEqual(_decision_diary(state,'2026-08-14'),'今天真实决策日记')
        self.assertEqual(_decision_diary(state,'2026-08-15'),'当日已处理')

    def test_trading_date_falls_back_to_tencent_index(self):
        class FakeAk:
            def stock_zh_a_hist_tx(self, **_kwargs):
                raise RuntimeError('stock calendar unavailable')
            def stock_zh_index_daily_tx(self, symbol):
                self.symbol=symbol
                return pd.DataFrame([
                    {'date':'2026-08-13','close':100.0},
                    {'date':'2026-08-14','close':101.0},
                    {'date':'2026-08-17','close':102.0},
                ])
        market=AKShareMarket.__new__(AKShareMarket)
        market.ak=FakeAk()
        with patch('engine.real_market.time.sleep',return_value=None):
            td=market.latest_trade_date('2026-08-16')
        self.assertEqual(td,'2026-08-14')
        self.assertEqual(market.ak.symbol,'sh000001')

    def test_tencent_amount_normalization_handles_yuan_or_hands(self):
        self.assertEqual(_tx_amount_mode(500_000_000,10.0,520_000_000),'yuan')
        amount,volume=_tx_amount_and_volume(500_000_000,10.0,'yuan')
        self.assertEqual(amount,500_000_000)
        self.assertEqual(volume,50_000_000)

        self.assertEqual(_tx_amount_mode(500_000,10.0,520_000_000),'hands')
        amount,volume=_tx_amount_and_volume(500_000,10.0,'hands')
        self.assertEqual(amount,500_000_000)
        self.assertEqual(volume,50_000_000)

    def test_exchange_holiday_skips_scheduled_market_and_ai_work(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for fid in FUND_IDS:
                (root/f'{fid}.json').write_text(
                    json.dumps({'last_processed_date':'2026-09-30'}),encoding='utf-8'
                )
            decision=scheduled_decision('2026-10-01','2026-09-30',root)
            self.assertFalse(decision['is_trading_day'])
            self.assertTrue(decision['processed_latest'])
            self.assertFalse(decision['production_run'])
            self.assertFalse(decision['preflight_run'])

            trading_day=scheduled_decision('2026-10-08','2026-10-08',root)
            self.assertTrue(trading_day['production_run'])
            self.assertTrue(trading_day['preflight_run'])

if __name__=='__main__': unittest.main()
