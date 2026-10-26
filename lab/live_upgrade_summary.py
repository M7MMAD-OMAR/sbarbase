#!/usr/bin/env python3
"""Summary of one live upgrade measurement: pulse readings plus the scenarios that ran.

  python3 lab/live_upgrade_summary.py --pulse pulse.jsonl --scenarios scenarios.json \\
      --facts facts.json --out live-upgrade-measure.json

Inputs:
  pulse.jsonl     the lines lab/pulse.py wrote (readings, marks)
  scenarios.json  a list of {name, start, end, ...} written by lab/live_upgrade_measure.py: `start` and
                  `end` are wall clock seconds, and every other key (what ran, what the logs said, stages
                  that were blocked) is carried into the output untouched
  facts.json      runner facts, versions and the command, carried into the output untouched

For every scenario and for each of the two probes (`env`, `health`) it reports, over the readings that
started between `start` and `end`:

  n, ok, held, wrong, error     counts by verdict (see lab/pulse.py)
  failed                        n minus ok: every request that did not get the expected answer
  latency_ms                    p50, p95, p99 and max of the readings that were answered (`ok`)
  longest_gap_s                 the longest time between two consecutive `ok` answers, counted from one
                                answer's arrival to the next one's start, whose interval overlaps the window;
                                the nearest answers outside the window count, so an outage that spans the
                                window's edge is not cut
  longest_failure_run_s         the longest run of consecutive failed readings, from the start of its first
                                to the arrival of its last

`raw` keeps the readings of each window with a margin (RAW_MARGIN seconds either side) as compact rows
[seconds since the window's start, kind, status, ms, verdict, error], so the committed file can be audited
without the whole run. Only the standard library is used.
"""
import argparse
import json
import math
import sys

RAW_MARGIN = 10.0
KINDS = ('env', 'health')


def load_pulse(path):
    """(readings, marks) from a pulse file; a damaged last line (a killed writer) is skipped."""
    readings, marks = [], []
    with open(path) as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get('kind')
            if kind in KINDS and isinstance(record.get('t'), (int, float)):
                readings.append(record)
            elif kind == 'mark':
                marks.append(record)
    readings.sort(key=lambda row: row['t'])
    return readings, marks


def percentile(values, fraction):
    """The nearest-rank percentile of a list of numbers (fraction 0 to 1); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(round(fraction * len(ordered), 9)))
    return ordered[min(len(ordered), rank) - 1]


def arrival(row):
    return row['t'] + (row.get('ms') or 0) / 1000


def longest_gap(rows, start, end):
    """The longest silence between `ok` answers whose interval overlaps [start, end]; None with fewer than
    two answers. A silence runs from one answer's arrival to the next one's start."""
    answers = [row for row in rows if row['verdict'] == 'ok']
    best = None
    for before, after in zip(answers, answers[1:]):
        gap_start, gap_end = arrival(before), after['t']
        if gap_end < start or gap_start > end:
            continue
        gap = max(0.0, gap_end - gap_start)
        if best is None or gap > best:
            best = gap
    return None if best is None else round(best, 3)


def longest_failure_run(rows, start, end):
    """Seconds from the first start to the last arrival of the longest run of failed readings inside the window."""
    best, current = 0.0, None
    for row in rows:
        if row['t'] < start or row['t'] > end:
            continue
        if row['verdict'] != 'ok':
            current = (row['t'], arrival(row)) if current is None else (current[0], max(current[1], arrival(row)))
            best = max(best, current[1] - current[0])
        else:
            current = None
    return round(best, 3)


def kind_stats(readings, kind, start, end):
    rows = [row for row in readings if row['kind'] == kind]
    inside = [row for row in rows if start <= row['t'] <= end]
    counts = {verdict: sum(1 for row in inside if row['verdict'] == verdict) for verdict in ('ok', 'held', 'wrong', 'error')}
    latency = [row['ms'] for row in inside if row['verdict'] == 'ok']
    stats = {'n': len(inside), **counts, 'failed': len(inside) - counts['ok'],
             'latency_ms': {'p50': percentile(latency, .50), 'p95': percentile(latency, .95),
                            'p99': percentile(latency, .99), 'max': max(latency) if latency else None},
             'longest_gap_s': longest_gap(rows, start, end),
             'longest_failure_run_s': longest_failure_run(rows, start, end)}
    statuses = {}
    for row in inside:
        if row['verdict'] in ('wrong', 'held'):
            key = f"{row['verdict']}:{row['status']}"
            statuses[key] = statuses.get(key, 0) + 1
    if statuses:
        stats['unexpected'] = statuses
    errors = {}
    for row in inside:
        if row['error']:
            errors[row['error']] = errors.get(row['error'], 0) + 1
    if errors:
        stats['errors'] = errors
    return stats


def raw_rows(readings, start, end, margin=RAW_MARGIN):
    return [[round(row['t'] - start, 3), row['kind'], row['status'], row['ms'], row['verdict'], row['error']]
            for row in readings if start - margin <= row['t'] <= end + margin]


def summarize(readings, scenarios, facts=None):
    """The output document: facts, one entry per scenario with its statistics and raw rows."""
    out = {'schema': 1, 'check': 'live-upgrade-measure', **(facts or {})}
    out['readings'] = {'total': len(readings),
                       'first': readings[0]['t'] if readings else None, 'last': readings[-1]['t'] if readings else None}
    entries = []
    for scenario in scenarios:
        start, end = scenario.get('start'), scenario.get('end')
        entry = dict(scenario)
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
            entry['seconds'] = round(end - start, 3)
            entry['stats'] = {kind: kind_stats(readings, kind, start, end) for kind in KINDS}
            entry['raw_columns'] = ['seconds_from_start', 'kind', 'status', 'ms', 'verdict', 'error']
            entry['raw'] = raw_rows(readings, start, end)
        entries.append(entry)
    out['scenarios'] = entries
    out['all'] = {kind: kind_stats(readings, kind, readings[0]['t'], readings[-1]['t']) for kind in KINDS} if readings else {}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    parser.add_argument('--pulse', required=True)
    parser.add_argument('--scenarios', required=True)
    parser.add_argument('--facts')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    readings, marks = load_pulse(args.pulse)
    with open(args.scenarios) as handle:
        scenarios = json.load(handle)
    facts = {}
    if args.facts:
        with open(args.facts) as handle:
            facts = json.load(handle)
    document = summarize(readings, scenarios, facts)
    document['marks'] = [{'t': mark['t'], 'name': mark['name']} for mark in marks]
    with open(args.out, 'w') as handle:
        json.dump(document, handle, indent=1, ensure_ascii=False)
        handle.write('\n')
    print(f'{len(readings)} readings, {len(scenarios)} scenarios -> {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
