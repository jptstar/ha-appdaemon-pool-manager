"""Heat pump drawing power while Home Assistant shows it off (offline check).

Reproduces the 2 November 2026 incident: the Pro Elyo Touch started its own
winter antifreeze protection, its compressor drew ~1.4 kW while `climate`
read `off`, and only a manual "heat, wait, off" sequence stopped it.

Checks, on the real controller in the simulator:
  * recovery: the compressor is stopped within RECOVERY_LIMIT_H hours, both
    when a plain `off` is enough and when only a heat pulse then `off` works;
  * wintering mode ("Hivernage • PAC hors tension"): the heat pump is never
    commanded and no PAC fault is raised, whatever it does electrically;
  * the recovery never fires while heating is legitimately requested.
"""
import argparse
import json

from simulate_pool import simulate

RECOVERY_LIMIT_H = 0.5   # 30 minutes after the fault starts
MAX_COMMANDS = 3          # off, heat pulse, off
BASE = dict(weather='freeze', water=13., cover='closed', solar='none', load=500., choice='eco',
            fault='none', start_time='2026-11-02T06:00:00', target_date='2026-11-03', target_hour=12)


def recovery_case(mode, needs_cycle, heating='Désactivé'):
    return dict(BASE, mode=mode, heating=heating,
                ghost=dict(at_h=1.0, needs_cycle=needs_cycle))


def run_recovery():
    rows = []
    for mode in ('Intelligent', 'Température', 'Marche Forcée', 'Hors Gel'):
        for needs_cycle in (False, True):
            result = simulate(recovery_case(mode, needs_cycle), hours=3, step_s=30)
            ghost = result['ghost']
            ok = (not ghost['active'] and ghost['cleared_h'] is not None
                  and ghost['cleared_h'] <= RECOVERY_LIMIT_H and not result['violations']
                  and ghost['commands'] <= MAX_COMMANDS)
            rows.append(dict(kind='recovery', mode=mode, needs_cycle=needs_cycle, ok=ok,
                             cleared_h=ghost['cleared_h'], commands=ghost['commands'],
                             violations=result['violations']))
    return rows


def run_wintering():
    rows = []
    case = recovery_case('Intelligent', True, heating='Hivernage • PAC hors tension')
    result = simulate(case, hours=3, step_s=30)
    ghost = result['ghost']
    errors = [e for e in result['errors'] if 'PAC' in e]
    ok = ghost['commands'] == 0 and not errors and result['violations'] == ['pac_ghost_not_stopped']
    rows.append(dict(kind='wintering', ok=ok, commands=ghost['commands'], errors=errors,
                     violations=result['violations']))
    return rows


def run_no_false_positive():
    """A PAC that runs because heat was really requested must never be 'recovered'."""
    import simulate_pool
    messages = []
    original = simulate_pool.FakeHass.log

    def log(self, message, **kwargs):
        messages.append(str(message))
        return original(self, message, **kwargs)
    simulate_pool.FakeHass.log = log
    try:
        rows = []
        for heating in ('Turbo • 12 h', 'Fin de saison • Smart', 'Automatique'):
            messages.clear()
            result = simulate(dict(BASE, weather='sun', water=24., solar='strong', mode='Intelligent',
                                   heating=heating), hours=6, step_s=30)
            reprise = [m for m in messages if 'PAC reprise' in m or 'PAC active (' in m]
            rows.append(dict(kind='no_false_positive', heating=heating, ok=not reprise,
                             pac_kwh=result['energy']['pac_kwh'], reprise=reprise[:2]))
        return rows
    finally:
        simulate_pool.FakeHass.log = original


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    rows = run_recovery() + run_wintering() + run_no_false_positive()
    failed = [r for r in rows if not r['ok']]
    print(json.dumps(dict(checked=len(rows), failed=len(failed), examples=failed[:6]), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
