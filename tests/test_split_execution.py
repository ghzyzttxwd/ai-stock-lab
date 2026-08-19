# Retail board-policy + conditional-plan regression coverage.
import unittest

from engine.broker import (
    execute_conditional_buys,
    execute_conditional_sells,
    execute_target_weights,
)
from engine.pipeline import targets_for
from engine.trading_plan import PLAN_VERSION, build_conditional_targets, pending_is_conditional
import engine.morning_run  # noqa: F401 - import is a syntax/regression check
import engine.evening_split_run  # noqa: F401 - import is a syntax/regression check


def conditional_target(symbol='sh.600001', mode='breakout', trigger=10.5, low=10.5, high=10.7, weight=0.5):
    return {
        'symbol':symbol,'name':'条件股','target_weight':weight,'opportunity_score':75.0,
        'trade_plan':{
            'plan_version':PLAN_VERSION,'decision_date':'2026-08-18','setup':mode,
            'entry':{
                'mode':mode,
                'operator':'>=' if mode=='breakout' else '<=' if mode=='pullback' else 'inside',
                'trigger_price':trigger,'valid_min':low,'valid_max':high,
            },
            'exit':{
                'hard_stop_pct':0.03,'reward_risk':1.8,'trailing_activation_rr':1.0,
                'trailing_drawdown_pct':0.025,'max_hold_days':3,
            },
        },
    }


class SplitExecutionTests(unittest.TestCase):
    def test_legacy_sell_only_still_available_for_historical_corrections(self):
        state = {
            'fund_id': 'A','cash': 0.0,
            'positions': {'sh.600000': {'name':'测试股','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
        }
        bars = {'sh.600000': {'open':10.0,'close':12.0,'preclose':11.5,'tradestatus':'1'}}
        fills = execute_target_weights(state, [], bars, '2026-08-19', sides=('SELL',), price_field='close', note='legacy')
        self.assertEqual([x['side'] for x in fills], ['SELL'])
        self.assertEqual(state['positions'], {})

    def test_breakout_does_not_buy_when_trigger_never_touched(self):
        state={'fund_id':'A','cash':100000.0,'positions':{}}
        target=conditional_target(trigger=10.5,low=10.5,high=10.7,weight=0.5)
        bars={'sh.600001':{'open':10.0,'high':10.49,'low':9.9,'close':10.2,'preclose':10.0,'tradestatus':'1'}}
        fills,skipped=execute_conditional_buys(state,[target],bars,'2026-08-19')
        self.assertEqual(fills,[])
        self.assertEqual(state['positions'],{})
        self.assertTrue(any(x['reason']=='breakout_not_triggered' for x in skipped))

    def test_breakout_buys_at_declared_trigger_not_at_close(self):
        state={'fund_id':'A','cash':100000.0,'positions':{}}
        target=conditional_target(trigger=10.5,low=10.5,high=10.7,weight=0.5)
        bars={'sh.600001':{'open':10.0,'high':10.8,'low':9.9,'close':9.95,'preclose':10.0,'tradestatus':'1'}}
        fills,skipped=execute_conditional_buys(state,[target],bars,'2026-08-19')
        self.assertEqual(len(fills),1)
        self.assertEqual(fills[0]['reference_price'],10.5)
        self.assertEqual(fills[0]['execution_price_field'],'conditional_trigger')
        self.assertEqual(fills[0]['trigger_reason'],'intraday_breakout_triggered')
        self.assertIn('sh.600001',state['positions'])
        self.assertIn('sh.600001',state['exit_plans'])

    def test_breakout_gap_above_max_chase_is_rejected(self):
        state={'fund_id':'A','cash':100000.0,'positions':{}}
        target=conditional_target(trigger=10.5,low=10.5,high=10.7,weight=0.5)
        bars={'sh.600001':{'open':11.0,'high':11.2,'low':10.9,'close':11.1,'preclose':10.4,'tradestatus':'1'}}
        fills,skipped=execute_conditional_buys(state,[target],bars,'2026-08-19')
        self.assertEqual(fills,[])
        self.assertTrue(any(x['reason']=='gap_above_max_chase' for x in skipped))

    def test_pullback_only_buys_after_price_reaches_zone(self):
        state={'fund_id':'A','cash':100000.0,'positions':{}}
        target=conditional_target(mode='pullback',trigger=9.6,low=9.3,high=9.6,weight=0.5)
        bars={'sh.600001':{'open':10.0,'high':10.1,'low':9.55,'close':9.8,'preclose':10.0,'tradestatus':'1'}}
        fills,_=execute_conditional_buys(state,[target],bars,'2026-08-19')
        self.assertEqual(len(fills),1)
        self.assertEqual(fills[0]['reference_price'],9.6)

    def test_condition_not_met_means_hold_not_forced_sell(self):
        state={
            'fund_id':'A','cash':0.0,
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':9.5,'take_profit_price':10.5,
                'trailing_activation_price':10.3,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':9.8,'preclose':10.0,'tradestatus':'1'}}
        fills,checks=execute_conditional_sells(state,bars,'2026-08-19',clock='09:40')
        self.assertEqual(fills,[])
        self.assertIn('sh.600000',state['positions'])
        self.assertEqual(checks[0]['action'],'HOLD')

    def test_hard_stop_can_sell_below_take_profit_floor(self):
        state={
            'fund_id':'A','cash':0.0,
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':9.5,'take_profit_price':10.5,
                'trailing_activation_price':10.3,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':9.4,'preclose':10.0,'tradestatus':'1'}}
        fills,_=execute_conditional_sells(state,bars,'2026-08-19',clock='10:30')
        self.assertEqual(len(fills),1)
        self.assertEqual(fills[0]['exit_reason'],'hard_stop')
        self.assertEqual(state['positions'],{})

    def test_trailing_stop_uses_intraday_high_not_only_checkpoint_price(self):
        state={
            'fund_id':'A','cash':0.0,
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':100.0,'acquired_date':'2026-08-18','last_price':100.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':90.0,'take_profit_price':120.0,
                'trailing_activation_price':103.0,'trailing_drawdown_pct':0.025,'highest_price':100.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':105.0,'high':110.0,'preclose':100.0,'tradestatus':'1'}}
        fills,_=execute_conditional_sells(state,bars,'2026-08-19',clock='10:30')
        self.assertEqual(len(fills),1)
        self.assertEqual(fills[0]['exit_reason'],'trailing_stop')
        self.assertEqual(state['positions'],{})

    def test_take_profit_is_partial_then_trailing_can_manage_remainder(self):
        state={
            'fund_id':'A','cash':0.0,
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':9.5,'take_profit_price':10.5,
                'trailing_activation_price':10.3,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':10.6,'preclose':10.0,'tradestatus':'1'}}
        fills,_=execute_conditional_sells(state,bars,'2026-08-19',clock='10:30')
        self.assertEqual(len(fills),1)
        self.assertEqual(fills[0]['exit_reason'],'take_profit')
        self.assertEqual(state['positions']['sh.600000']['qty'],500)
        self.assertTrue(state['exit_plans']['sh.600000']['partial_taken'])

    def test_weak_opportunity_can_leave_cash_instead_of_forcing_target(self):
        raw=[{'symbol':'sh.600001','name':'弱机会','target_weight':0.2}]
        candidate={
            'symbol':'sh.600001','name':'弱机会','close':10.0,'ma5':10.0,'recent_high_3':10.1,
            'opportunity_score':30.0,'market_relative_3':-0.02,'close_position':0.4,
            'amount_ratio_3_20':0.9,'overheat_score':0,'atr14_pct':0.025,
        }
        planned=build_conditional_targets('D',raw,[candidate],'2026-08-19',state={'positions':{}})
        self.assertEqual(planned,[])

    def test_conditional_target_schema_is_machine_detectable(self):
        self.assertTrue(pending_is_conditional([conditional_target()]))
        legacy=[{'symbol':'sh.600001','target_weight':0.1}]
        self.assertFalse(pending_is_conditional(legacy))

    def test_target_layer_removes_chinext_and_star_before_strategy(self):
        candidates=[]
        for i in range(10):
            candidates.append({
                'symbol':f'sh.600{i:03d}','name':f'M{i}','risk':50,'trend':50,'liquidity':50,
                'momentum':50,'quality':50,'valuation':50,'score_d':50,'opportunity_score':60,
            })
        for symbol in ('sz.300001','sz.301001','sh.688001','sh.689001'):
            candidates.append({
                'symbol':symbol,'name':symbol,'risk':100,'trend':100,'liquidity':100,
                'momentum':100,'quality':100,'valuation':100,'score_d':100,'opportunity_score':100,
            })
        targets,_=targets_for('A',candidates,80.0,{'positions':{}},use_ai=False)
        selected={x['symbol'] for x in targets}
        self.assertTrue(selected)
        self.assertTrue(all(x.startswith('sh.600') for x in selected))
        self.assertFalse(selected & {'sz.300001','sz.301001','sh.688001','sh.689001'})

    def test_retail_policy_hard_blocks_chinext_and_star_buys(self):
        state={'fund_id':'A','cash':100000.0,'positions':{}}
        bars={
            'sz.300001':{'open':10.0,'close':11.0,'preclose':10.0,'tradestatus':'1'},
            'sz.301001':{'open':10.0,'close':11.0,'preclose':10.0,'tradestatus':'1'},
            'sh.688001':{'open':10.0,'close':11.0,'preclose':10.0,'tradestatus':'1'},
            'sh.689001':{'open':10.0,'close':11.0,'preclose':10.0,'tradestatus':'1'},
        }
        targets=[{'symbol':symbol,'name':symbol,'target_weight':0.2} for symbol in bars]
        fills=execute_target_weights(state,targets,bars,'2026-08-19',sides=('BUY',),price_field='open',note='retail-policy-test')
        self.assertEqual(fills,[])
        self.assertEqual(state['positions'],{})
        self.assertEqual(state['cash'],100000.0)


if __name__ == '__main__':
    unittest.main()