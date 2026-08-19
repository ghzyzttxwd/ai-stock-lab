import unittest
from pathlib import Path

from engine_v2.conditional_plan import PLAN_VERSION
from engine_v2.split_execution import (
    combine_phase_executions,
    execute_conditional_buy_side,
    execute_conditional_exit_scan,
    execute_pending_side,
)
from engine_v2.morning_sell_run import CHECKPOINTS, SCHEDULED_MORNING_TIME, _audit_path, _execution_snapshot
from engine_v2.shadow_reporting import _summary_source_ref
import engine_v2.shadow_run_split  # noqa: F401


def conditional_pending(mode='breakout', trigger=10.5, low=10.5, high=10.7):
    return {
        'decision_date':'2026-08-18','plan_version':PLAN_VERSION,
        'targets':[{
            'symbol':'sh.600001','name':'条件股','target_weight':0.5,'opportunity_score':75.0,
            'trade_plan':{
                'plan_version':PLAN_VERSION,'decision_date':'2026-08-18','setup':mode,
                'entry':{'mode':mode,'operator':'>=' if mode=='breakout' else '<=',
                         'trigger_price':trigger,'valid_min':low,'valid_max':high},
                'exit':{'hard_stop_pct':0.03,'reward_risk':1.8,'trailing_drawdown_pct':0.025,'max_hold_days':3},
            },
        }],
    }


class V2SplitExecutionTests(unittest.TestCase):
    def test_legacy_executor_remains_only_for_historical_compatibility(self):
        state = {
            'fund_id': 'A','cash': 0.0,
            'positions': {'sh.600000': {'name':'测试股','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'fills': [], 'rejected_orders': [],
        }
        pending = {'decision_date': '2026-08-18', 'targets': []}
        bars = {'sh.600000': {'open':10.0,'close':12.0,'preclose':11.5,'tradestatus':'1'}}
        result = execute_pending_side(state,pending,bars,'2026-08-19',side='SELL',price_field='close')
        self.assertEqual([x['side'] for x in result['fills']], ['SELL'])
        self.assertEqual(state['positions'], {})

    def test_morning_snapshot_distinguishes_schedule_from_actual_execution_time(self):
        state = {'cash':1000.0,'positions':{'sh.600000':{'qty':100,'avg_cost':10.0,'last_price':10.0}}}
        bars = {'sh.600000': {'close': 10.5}}
        executed_at = '2026-08-19T10:32:43+08:00'
        snapshot = _execution_snapshot(state, bars, '2026-08-19', 3.5, executed_at)
        self.assertEqual(SCHEDULED_MORNING_TIME, '09:40')
        self.assertEqual(snapshot['phase'], 'conditional_exit_scan')
        self.assertEqual(snapshot['scheduled_time'], '09:40')
        self.assertEqual(snapshot['executed_at'], executed_at)

    def test_each_v2_checkpoint_has_distinct_audit_slot(self):
        self.assertEqual(CHECKPOINTS, ('09:40','10:30','11:20','13:30','14:30','14:55'))
        root=Path('/tmp/v2')
        first=_audit_path(root,'2026-08-20','09:40')
        second=_audit_path(root,'2026-08-20','10:30')
        final=_audit_path(root,'2026-08-20','14:55')
        self.assertEqual(first.name,'2026-08-20-execution-0940.json')
        self.assertEqual(second.name,'2026-08-20-execution-1030.json')
        self.assertEqual(final.name,'2026-08-20-execution-1455.json')
        self.assertEqual(len({first,second,final}),3)

    def test_legacy_morning_audit_never_claims_scheduled_time_is_actual(self):
        legacy = {'event_kind':'morning_sell','source_ref':{'execution_bar_source':'sina-intraday','note':'Previous-session SELL/reduce intents executed at the 09:40 live quote.'}}
        source_ref = _summary_source_ref(legacy)
        self.assertEqual(source_ref['scheduled_time'], '09:40')
        self.assertIsNone(source_ref['executed_at'])
        self.assertEqual(source_ref['timing_status'], 'LEGACY_ACTUAL_TIME_UNRECORDED')

    def test_conditional_breakout_not_touched_means_no_buy(self):
        state={'fund_id':'A','cash':100000.0,'positions':{},'fills':[],'rejected_orders':[]}
        pending=conditional_pending(trigger=10.5,low=10.5,high=10.7)
        bars={'sh.600001':{'open':10.0,'high':10.49,'low':9.9,'close':10.2,'preclose':10.0,'tradestatus':'1'}}
        result=execute_conditional_buy_side(state,pending,bars,'2026-08-19')
        self.assertEqual(result['fills'],[])
        self.assertEqual(state['positions'],{})
        self.assertTrue(any(x['reason']=='breakout_not_triggered' for x in result['rejected_orders']))

    def test_conditional_breakout_buys_at_declared_trigger_not_close(self):
        state={'fund_id':'A','cash':100000.0,'positions':{},'fills':[],'rejected_orders':[]}
        pending=conditional_pending(trigger=10.5,low=10.5,high=10.7)
        bars={'sh.600001':{'open':10.0,'high':10.8,'low':9.9,'close':9.95,'preclose':10.0,'tradestatus':'1'}}
        result=execute_conditional_buy_side(state,pending,bars,'2026-08-19')
        self.assertEqual(len(result['fills']),1)
        fill=result['fills'][0]
        self.assertEqual(fill['reference_price'],10.5)
        self.assertEqual(fill['execution_price_field'],'conditional_trigger')
        self.assertEqual(fill['trigger_reason'],'intraday_breakout_triggered')
        self.assertIn('sh.600001',state['exit_plans'])

    def test_gap_above_max_chase_is_not_bought(self):
        state={'fund_id':'A','cash':100000.0,'positions':{},'fills':[],'rejected_orders':[]}
        pending=conditional_pending(trigger=10.5,low=10.5,high=10.7)
        bars={'sh.600001':{'open':10.9,'high':11.0,'low':10.8,'close':10.9,'preclose':10.4,'tradestatus':'1'}}
        result=execute_conditional_buy_side(state,pending,bars,'2026-08-19')
        self.assertEqual(result['fills'],[])
        self.assertTrue(any(x['reason']=='gap_above_max_chase' for x in result['rejected_orders']))

    def test_conditional_exit_does_not_sell_just_because_0940_arrived(self):
        state={
            'fund_id':'A','cash':0.0,'fills':[],'rejected_orders':[],
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':9.5,'take_profit_price':10.5,
                'trailing_activation_price':10.3,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':9.8,'preclose':10.0,'tradestatus':'1'}}
        result=execute_conditional_exit_scan(state,bars,'2026-08-19',clock='09:40')
        self.assertEqual(result['fills'],[])
        self.assertIn('sh.600000',state['positions'])
        self.assertEqual(result['checks'][0]['action'],'HOLD')

    def test_hard_stop_sells_even_when_profit_target_not_reached(self):
        state={
            'fund_id':'A','cash':0.0,'fills':[],'rejected_orders':[],
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-18','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':9.5,'take_profit_price':10.5,
                'trailing_activation_price':10.3,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':9.4,'preclose':10.0,'tradestatus':'1'}}
        result=execute_conditional_exit_scan(state,bars,'2026-08-19',clock='09:40')
        self.assertEqual(len(result['fills']),1)
        self.assertEqual(result['fills'][0]['exit_reason'],'hard_stop')
        self.assertEqual(state['positions'],{})

    def test_trailing_stop_uses_intraday_high_not_only_checkpoint_price(self):
        state={
            'fund_id':'A','cash':0.0,'fills':[],'rejected_orders':[],
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':100.0,'acquired_date':'2026-08-18','last_price':100.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':90.0,'take_profit_price':120.0,
                'trailing_activation_price':103.0,'trailing_drawdown_pct':0.025,'highest_price':100.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':0,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':105.0,'high':110.0,'preclose':100.0,'tradestatus':'1'}}
        result=execute_conditional_exit_scan(state,bars,'2026-08-19',clock='10:30')
        self.assertEqual(len(result['fills']),1)
        self.assertEqual(result['fills'][0]['exit_reason'],'trailing_stop')
        self.assertEqual(state['positions'],{})

    def test_1455_checkpoint_can_execute_max_hold_exit(self):
        state={
            'fund_id':'A','cash':0.0,'fills':[],'rejected_orders':[],
            'positions':{'sh.600000':{'name':'持仓','qty':1000,'avg_cost':10.0,'acquired_date':'2026-08-17','last_price':10.0}},
            'exit_plans':{'sh.600000':{
                'plan_version':PLAN_VERSION,'hard_stop_price':8.0,'take_profit_price':12.0,
                'trailing_activation_price':12.0,'trailing_drawdown_pct':0.025,'highest_price':10.0,
                'partial_taken':False,'max_hold_days':3,'sessions_held':3,'rotation_exit':False,
            }},
        }
        bars={'sh.600000':{'close':10.0,'high':10.0,'preclose':10.0,'tradestatus':'1'}}
        result=execute_conditional_exit_scan(state,bars,'2026-08-20',clock='14:55')
        self.assertEqual(len(result['fills']),1)
        self.assertEqual(result['fills'][0]['exit_reason'],'max_hold_time_exit')
        self.assertEqual(state['positions'],{})

    def test_conditional_buy_still_hard_blocks_chinext_and_star(self):
        state={'fund_id':'A','cash':100000.0,'positions':{},'fills':[],'rejected_orders':[]}
        symbols=('sz.300001','sz.301001','sh.688001','sh.689001')
        pending={'decision_date':'2026-08-18','plan_version':PLAN_VERSION,'targets':[]}
        for symbol in symbols:
            t=conditional_pending()['targets'][0]
            t={**t,'symbol':symbol,'name':symbol}
            pending['targets'].append(t)
        bars={s:{'open':10.0,'high':11.0,'low':9.9,'close':10.8,'preclose':10.0,'tradestatus':'1'} for s in symbols}
        result=execute_conditional_buy_side(state,pending,bars,'2026-08-19')
        self.assertEqual(result['fills'],[])
        self.assertEqual(state['positions'],{})
        blocked=[x for x in result['policy_adjustments'] if x.get('reason')=='retail_mainboard_only']
        self.assertEqual({x['symbol'] for x in blocked},set(symbols))

    def test_phase_combiner_keeps_both_sides_and_fees(self):
        combined = combine_phase_executions(
            {'phase':'sell','decision_date':'2026-08-18','trade_date':'2026-08-19','fills':[{'side':'SELL'}],'rejected_orders':[],'policy_adjustments':[],'valuation_fallback_symbols':[],'fees':5.0},
            {'phase':'buy','decision_date':'2026-08-18','trade_date':'2026-08-19','fills':[{'side':'BUY'}],'rejected_orders':[],'policy_adjustments':[],'valuation_fallback_symbols':[],'fees':6.0},
        )
        self.assertEqual(combined['phases'], ['sell', 'buy'])
        self.assertEqual(combined['fees'], 11.0)


if __name__ == '__main__':
    unittest.main()