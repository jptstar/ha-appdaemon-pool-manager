import datetime as dt
import unittest
from pool_season import decide_season

T = dt.datetime(2026, 9, 25, 12)
def evaluate(**kw):
    return decide_season(T, mode="end_season", **kw)

class SeasonPolicyTests(unittest.TestCase):
    def test_short_cold_snap_is_reversible(self):
        x=evaluate(mean_7d_c=15, cold_since=T-dt.timedelta(hours=48),
                   forecast_scores=[30]*15, recoverable=False)
        self.assertFalse(x.permanent)
    def test_cold_and_unrecoverable_closes_early(self):
        x=evaluate(mean_7d_c=15, cold_since=T-dt.timedelta(hours=73),
                   forecast_scores=[30]*15, recoverable=False)
        self.assertEqual(x.state,"closed_early")
    def test_sunny_weekend_prevents_early_closure(self):
        x=evaluate(mean_7d_c=15, cold_since=T-dt.timedelta(hours=73),
                   forecast_scores=[30]*14+[75], recoverable=True)
        self.assertTrue(x.heat_allowed)
    def test_cutoff(self):
        x=decide_season(dt.datetime(2026,10,10), mode="end_season")
        self.assertEqual(x.state,"closed_date")
    def test_persisted_closure(self):
        self.assertTrue(evaluate(already_closed=True).permanent)
    def test_override_expires(self):
        self.assertFalse(evaluate(already_closed=True,override_until=T-dt.timedelta(seconds=1)).heat_allowed)
    def test_override_temporary(self):
        self.assertTrue(evaluate(already_closed=True,override_until=T+dt.timedelta(hours=1)).heat_allowed)
    def test_missing_forecast_not_permanent(self):
        self.assertFalse(evaluate(mean_7d_c=15,cold_since=T-dt.timedelta(days=4),recoverable=False).permanent)
    def test_other_mode_unaffected(self):
        self.assertTrue(decide_season(T,mode="auto",already_closed=True).heat_allowed)
if __name__=="__main__":
    unittest.main()
