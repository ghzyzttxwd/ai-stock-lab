import unittest

from engine_v2.enrichment_resilient import _install_persisted_sentiment


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


if __name__=='__main__':
    unittest.main()
