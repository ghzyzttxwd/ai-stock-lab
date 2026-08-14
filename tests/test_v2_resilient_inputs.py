import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from engine_v2.enrichment_resilient import _install_persisted_sentiment
from engine_v2.resilient_snapshot import (
    build_resilient_snapshot,
    _load_decision_snapshot,
    _load_recovery_universe,
    _save_decision_snapshot,
    _save_recovery_universe,
)


class V2ResilientInputTests(unittest.TestCase):
    def test_enrichment_reuses_same_session_persisted_sentiment(self):
        snap={
            'trade_date':'2026-08-14',
            'market':{'sentiment_detail':{
                'trade_date':'2026-08-14',
                'limit_up_count':2,'broken_limit_count':1,'limit_down_count':1,
                'limit_break_rate':1/3,
                'limit_up':[{'code':'600001'}],
                'broken_limit':[{'code':'600002'}],
                'limit_down':[{'code':'600003'}],
            }},
        }
        module, original, detail=_install_persisted_sentiment(snap)
        try:
            reused=module._sentiment_snapshot(None,'2026-08-14')
            self.assertEqual(reused,detail)
            self.assertEqual(reused['limit_up'][0]['code'],'600001')
        finally:
            module._sentiment_snapshot=original

    def test_missing_or_wrong_date_detail_is_rejected_before_network(self):
        with self.assertRaisesRegex(RuntimeError,'same-session persisted sentiment'):
            _install_persisted_sentiment({'trade_date':'2026-08-14','market':{}})
        with self.assertRaisesRegex(RuntimeError,'same-session persisted sentiment'):
            _install_persisted_sentiment({
                'trade_date':'2026-08-14',
                'market':{'sentiment_detail':{'trade_date':'2026-08-13'}},
            })

    def test_recovery_universe_is_v2_owned_and_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'shadow_state'/'v2'/'cache'/'market_universe.json'
            snapshot={
                'trade_date':'2026-08-14',
                'preselection':{'rows':[
                    {'code':f'sh.{600000+i:06d}','raw_code':f'{600000+i:06d}','name':f'S{i}','amount':1_000_000+i}
                    for i in range(60)
                ]},
            }
            _save_recovery_universe(snapshot,path)
            payload=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['cache_version'],'v2-market-universe-1')
            rows,meta=_load_recovery_universe('2026-08-15',path)
            self.assertEqual(len(rows),60)
            self.assertEqual(meta['asof'],'2026-08-14')

    def test_decision_snapshot_cache_requires_exact_date_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'shadow_state'/'v2'/'cache'/'normalized_snapshot.json'
            snapshot={
                'trade_date':'2026-08-14',
                'market':{'sentiment_detail':{'trade_date':'2026-08-14'}},
                'source_notes':{},
                'safety':{
                    'stock_universe_grade':'full',
                    'eligible_for_shadow_decision':True,
                    'calls_sol':False,
                    'writes_ledgers':False,
                },
            }
            _save_decision_snapshot(snapshot,path)
            restored,meta=_load_decision_snapshot('2026-08-14',path)
            self.assertEqual(restored,snapshot)
            self.assertEqual(meta['trade_date'],'2026-08-14')
            with self.assertRaisesRegex(RuntimeError,'date mismatch'):
                _load_decision_snapshot('2026-08-15',path)
            payload=json.loads(path.read_text(encoding='utf-8'))
            payload['snapshot']['trade_date']='2026-08-13'
            path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'payload date mismatch'):
                _load_decision_snapshot('2026-08-14',path)

    def test_decision_snapshot_cache_rejects_degraded_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'normalized_snapshot.json'
            snapshot={
                'trade_date':'2026-08-14',
                'market':{'sentiment_detail':{'trade_date':'2026-08-14'}},
                'safety':{
                    'stock_universe_grade':'degraded',
                    'eligible_for_shadow_decision':False,
                    'calls_sol':False,
                    'writes_ledgers':False,
                },
            }
            with self.assertRaisesRegex(RuntimeError,'non-decision-grade'):
                _save_decision_snapshot(snapshot,path)

    @patch('engine.real_market.AKShareMarket.latest_trade_date',return_value='2026-08-14')
    @patch('engine_v2.resilient_snapshot._install_market_snapshot_recovery')
    @patch('engine_v2.resilient_snapshot._save_recovery_universe')
    @patch('engine_v2.resilient_snapshot._save_decision_snapshot')
    @patch('engine_v2.resilient_snapshot._load_decision_snapshot',side_effect=RuntimeError('cache missing'))
    @patch('engine_v2.resilient_snapshot._build_and_capture_sentiment')
    def test_live_snapshot_retries_once_when_exact_session_cache_is_missing(
        self,build,load_cache,save_snapshot,save_universe,install_recovery,latest_date,
    ):
        live={
            'trade_date':'2026-08-14','market':{},'source_notes':{},
            'safety':{'calls_sol':False,'writes_ledgers':False},
        }
        detail={'trade_date':'2026-08-14','limit_up':[],'broken_limit':[],'limit_down':[]}
        build.side_effect=[RuntimeError('first live timeout'),(live,detail)]
        result=build_resilient_snapshot('2026-08-14')
        self.assertEqual(build.call_count,2)
        self.assertEqual(result['source_notes']['snapshot_recovery_mode'],'live')
        self.assertFalse(result['safety']['snapshot_cache_reused'])
        save_snapshot.assert_called_once()

    @patch('engine.real_market.AKShareMarket.latest_trade_date',return_value='2026-08-14')
    @patch('engine_v2.resilient_snapshot._install_market_snapshot_recovery')
    @patch('engine_v2.resilient_snapshot._load_decision_snapshot')
    @patch('engine_v2.resilient_snapshot._build_and_capture_sentiment',side_effect=RuntimeError('live timeout'))
    def test_live_failure_reuses_only_valid_exact_session_snapshot(
        self,build,load_cache,install_recovery,latest_date,
    ):
        cached={
            'trade_date':'2026-08-14',
            'market':{'sentiment_detail':{'trade_date':'2026-08-14'}},
            'source_notes':{'stock_universe_mode':'full'},
            'safety':{
                'stock_universe_grade':'full','eligible_for_shadow_decision':True,
                'calls_sol':False,'writes_ledgers':False,
            },
        }
        load_cache.return_value=(cached,{'path':'cache.json','trade_date':'2026-08-14','snapshot_sha256':'abc'})
        result=build_resilient_snapshot('2026-08-14')
        self.assertEqual(build.call_count,1)
        self.assertTrue(result['safety']['snapshot_cache_reused'])
        self.assertEqual(result['source_notes']['snapshot_recovery_mode'],'exact-session-v2-cache')
        self.assertIn('live timeout',result['source_notes']['snapshot_cache']['live_error'])


if __name__=='__main__':
    unittest.main()

