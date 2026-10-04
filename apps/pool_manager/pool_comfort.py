"""Keep a comfort commitment separate from continually changing weather."""
import datetime
from pool_predictive import find_swim_opportunities


class ComfortCommitment:
    def __init__(self, saved=None):
        self.date = None
        self.status = 'none'
        self.reason = ''
        if saved:
            try:
                self.date = datetime.date.fromisoformat(saved.get('comfort_target_date', ''))
                self.status = saved.get('comfort_status', 'planned')
            except (ValueError, TypeError):
                pass

    def prepare(self, forecast, now, *, score_min=55, min_air_c=21, weekend_bonus=10):
        rows = [dict(row) for row in forecast]
        if self.date and now >= datetime.datetime.combine(self.date, datetime.time(20)):
            self.date = None
        if self.date is None:
            opportunities = find_swim_opportunities(rows, now.date(), score_min,
                                                  min_air_c, weekend_bonus)
            nearby = [o['date'] for o in opportunities
                      if 0 <= (o['date']-now.date()).days <= 3]
            if nearby:
                self.date = min(nearby)
                self.status, self.reason = 'planned', 'objectif baignade retenu'
        if self.date is None:
            return rows
        row = next((r for r in rows if r.get('date') == self.date), None)
        if row is None:
            self.status, self.reason = 'forecast_missing', 'prévision cible manquante; objectif conservé'
            return rows
        condition = str(row.get('condition') or '').lower()
        score = float(row.get('usage_score', row.get('score', 0)) or 0)
        ambient = float(row.get('usage_temperature', row.get('temperature', 0)) or 0)
        if ('lightning' in condition or condition in {'hail', 'pouring', 'snowy'}
                or ambient < min_air_c-3 or score < score_min-20):
            self.status, self.reason = 'cancelled_weather', 'objectif annulé explicitement: météo défavorable'
            return rows
        self.status, self.reason = 'planned', 'objectif conservé malgré les révisions météo'
        # Retain the commitment without falsifying the weather displayed by HA.
        row['_comfort_committed'] = True
        return rows

    def annotate(self, plan, now, water, target, hour, margin=.2):
        plan = dict(plan)
        if self.date is None:
            return plan
        deadline = datetime.datetime.combine(self.date, datetime.time(hour))
        if self.status not in {'cancelled_weather', 'forecast_missing'}:
            missed_target = self.date in (plan.get('missed_swim_dates') or [])
            self.status = ('ready' if water >= target-margin else 'late' if now >= deadline
                           else 'at_risk' if missed_target else 'planned')
            if self.status == 'at_risk':
                self.reason = 'échéance menacée selon le modèle; objectif conservé'
            if self.status == 'late':
                missed = set(plan.get('missed_swim_dates') or [])
                missed.add(self.date)
                plan['missed_swim_dates'] = sorted(missed)
            plan['candidate'] = next((o for o in plan.get('opportunities', [])
                                      if o['date'] == self.date), {'date': self.date})
        plan.update(comfort_target_date=self.date.isoformat(), comfort_target_hour=hour,
                    comfort_target_temperature=target, comfort_status=self.status,
                    comfort_reason=self.reason)
        return plan

    def recover_daylight(self, plan, now, water, target, *, daylight, smart_preset='Smart', margin=.2):
        """Do not let an unreachable committed deadline become a later WAIT.

        Bounded best effort only, never a temperature guarantee or permission
        for night/Turbo. Explicit suspension and weather cancellation prevail.
        """
        if (self.date is None or self.status not in {'at_risk', 'late'}
                or not daylight or plan.get('decision_choice') == 'skip'
                or water >= target-margin or (self.date-now.date()).days > 1):
            return plan
        result = dict(plan)
        result.update(should_heat=True, action='PREHEAT', preset=smart_preset,
                      heat_target_c=float(target), trajectory_target_c=float(target),
                      reason='objectif conservé: rattrapage Smart de jour, échéance non garantie')
        return result
