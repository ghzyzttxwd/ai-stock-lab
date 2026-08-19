import unittest

from engine_v2.targets import build_shadow_targets


class V2TargetTests(unittest.TestCase):
    def _enriched(self, ready=True):
        candidates=[]
        for i in range(120):
            industry=f'I{i % 12}'
            candidates.append({
                'raw_code': f'{600000+i:06d}',
                'code': f'sh.{600000+i:06d}',
                'name': f'S{i}',
                'industry': industry,
                'correlation_cluster': industry,
                'fundamental_ready': True,
                'financial_distress': False,
                'quality_score': 62 + (i % 25),
                'cashflow_score': 58 + (i % 22),
                'valuation_score': 45 + ((i * 7) % 50),
                'risk': 55 + ((i * 3) % 40),
                'trend': 58 + ((i * 5) % 38),
                'momentum': 50 + ((i * 11) % 45),
                'liquidity': 55 + ((i * 13) % 40),
                'industry_score': 56 + ((i * 17) % 40),
                'leader_score': 58 + ((i * 19) % 38),
                'theme_score': 55 + ((i * 23) % 40),
                'sentiment_score': 48 + ((i * 29) % 48),
                'breakout_quality': 50 + ((i * 31) % 45),
                'crowding_score': 35 + ((i * 37) % 55),
                'vol20': 0.016 + (i % 12) * 0.0015,
                'one_word_limit': False,
            })
        return {
            'trade_date': '2026-08-14',
            'market': {'regime': {'label':'neutral','score':60,'confidence':70,'reasons':[]}},
            'candidates': candidates,
            'safety': {
                'ready_for_strategy_targets': ready,
                'stock_universe_grade': 'full' if ready else 'degraded',
                'decision_block_reason': None if ready else 'test degraded universe',
            },
        }

    def test_targets_are_read_only_validated_and_policy_bounded(self):
        result=build_shadow_targets(self._enriched())
        self.assertTrue(result['safety']['targets_valid'])
        self.assertFalse(result['safety']['writes_ledgers'])
        self.assertFalse(result['safety']['calls_sol'])
        self.assertFalse(result['safety']['executes_orders'])
        self.assertEqual(set(result['targets']), {'A','B','C','D','L'})
        self.assertEqual(set(result['concentration_flags']), {'B','C'})
        self.assertEqual(result['board_policy']['scope'], 'SH_SZ_MAINBOARD_ONLY')
        self.assertEqual(result['board_policy']['excluded_non_mainboard_count'], 0)
        for label, targets in result['targets'].items():
            self.assertGreater(len(targets), 0, label)
            for row in targets:
                self.assertTrue(row['thesis'])
                self.assertTrue(row['invalidation'])
                self.assertGreater(row['target_weight'], 0)
        for stats in result['stats'].values():
            self.assertIn('industry_hhi', stats)
            self.assertIn('effective_industries', stats)
            self.assertLessEqual(stats['top2_industry_share_of_invested'], 1.0)

    def test_non_mainboard_candidates_are_removed_before_strategy_selection(self):
        enriched = self._enriched()
        forbidden = ('sz.300001', 'sz.301001', 'sh.688001', 'sh.689001')
        for i, symbol in enumerate(forbidden):
            code = symbol[-6:]
            enriched['candidates'].append({
                'raw_code': code,
                'code': symbol,
                'name': f'FORBIDDEN{i}',
                'industry': 'HOT',
                'correlation_cluster': 'HOT',
                'fundamental_ready': True,
                'financial_distress': False,
                'quality_score': 100,
                'cashflow_score': 100,
                'valuation_score': 100,
                'risk': 100,
                'trend': 100,
                'momentum': 100,
                'liquidity': 100,
                'industry_score': 100,
                'leader_score': 100,
                'theme_score': 100,
                'sentiment_score': 100,
                'breakout_quality': 100,
                'crowding_score': 0,
                'vol20': 0.01,
                'one_word_limit': False,
            })
        result = build_shadow_targets(enriched)
        self.assertTrue(result['safety']['targets_valid'])
        self.assertEqual(result['board_policy']['excluded_non_mainboard_count'], 4)
        selected = {
            row['code'] if row.get('code') else row.get('symbol')
            for targets in result['targets'].values()
            for row in targets
        }
        self.assertTrue(all(symbol not in selected for symbol in forbidden))
        excluded = set(result['board_policy']['excluded_non_mainboard_symbols'])
        self.assertTrue(set(forbidden).issubset(excluded))

    def test_degraded_upstream_universe_blocks_target_generation(self):
        with self.assertRaisesRegex(RuntimeError, 'blocked by upstream safety'):
            build_shadow_targets(self._enriched(ready=False))

    def test_risk_off_forces_c_flat(self):
        e=self._enriched()
        e['market']['regime']['label']='risk_off'
        result=build_shadow_targets(e)
        self.assertEqual(result['targets']['C'], [])
        self.assertTrue(result['safety']['targets_valid'])


if __name__ == '__main__':
    unittest.main()
