"""Original configured bootstrap followed by bounded owned cron and HTTP effects."""
from native_defaults_inspect import main
from native_bootstrap_inspect import probe as bootstrap_probe
from native_worker_inspect import probe as worker_probe


def probe(report, sql, native, container):
    bootstrap_probe(report, sql, native, container)
    if not report['bootstrap'].get('passed') or any(not e['passed'] for e in report['bootstrap']['helper_cleanup']):
        raise RuntimeError('Configured bootstrap did not finish before effects')
    worker_probe(report, sql, native, container, configured=True)


if __name__ == '__main__':
    raise SystemExit(main(probe, bootstrap=True, configured_effects=True))
