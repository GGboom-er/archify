"""Validate and attach observations without changing static resolution claims."""
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError('Runtime evidence: ' + message)


def read_trace(path, report):
    with Path(path).open(encoding='utf-8') as stream:
        trace = json.load(stream)
    require(isinstance(trace, dict), 'expected an object')
    require(trace.get('kind') == 'archify-python-runtime' and trace.get('schema_version') == 1,
            'unsupported report schema')
    require(trace.get('integrity') == 'matching', 'source changed during execution; rerun')
    require(trace.get('status') in ('PASS', 'PARTIAL'), 'invalid run status')
    require(isinstance(trace.get('outcome'), dict) and
            trace['outcome'].get('status') in ('success', 'failed'), 'missing target outcome')
    require(isinstance(trace.get('reasons'), list) and
            all(isinstance(r, str) for r in trace['reasons']), 'invalid reasons')
    require(trace['status'] != 'PASS' or
            trace['outcome']['status'] == 'success' and not trace['reasons'], 'inconsistent run status')
    files = trace.get('files')
    require(isinstance(files, list) and all(isinstance(f, dict) for f in files), 'invalid files')
    require(all(isinstance(f.get('path'), str) and isinstance(f.get('sha256'), str) for f in files),
            'invalid file identity')
    hashes = {f.get('path'): f.get('sha256') for f in files}
    current = {f['path']: f['sha256'] for f in report['files']}
    require(len(hashes) == len(files) and hashes == current,
            'file inventory or SHA-256 differs from current source; rerun')
    edges = trace.get('edges')
    require(isinstance(edges, list), 'invalid edges')
    for edge in edges:
        require(isinstance(edge, dict) and type(edge.get('count')) is int and edge['count'] > 0,
                'invalid call count')
        for descriptor in (edge.get('target'), edge.get('caller')):
            if descriptor is None:
                continue
            require(isinstance(descriptor, dict) and isinstance(descriptor.get('path'), str) and
                    descriptor['path'] in current and
                    isinstance(descriptor.get('name'), str) and
                    type(descriptor.get('first_line')) is int and descriptor['first_line'] > 0 and
                    descriptor.get('code_verified') is True, 'invalid code identity')
        require(edge.get('target') is not None, 'missing target')
        if edge.get('caller') is not None:
            site = edge.get('site')
            require(isinstance(site, dict) and type(site.get('line')) is int and site['line'] > 0,
                    'invalid call site')
            for key in ('column', 'end_column', 'end_line'):
                require(site.get(key) is None or type(site[key]) is int and site[key] >= 0,
                        'invalid source position')
    return trace


def attach_runtime(report, paths):
    symbol_index = {}
    for symbol in report['symbols']:
        key = (symbol['path'], symbol['runtime_line'], symbol['name'])
        symbol_index.setdefault(key, []).append(symbol['id'])
        symbol['runtime_observed'] = False
    symbols = {s['id']: s for s in report['symbols']}
    calls = {c['id']: c for c in report['calls']}
    by_owner = {}
    for call in calls.values():
        by_owner.setdefault(call['caller'], []).append(call)
        call['runtime_observations'] = []
    result = {'runs': [], 'edges': [], 'observed_symbols': 0,
              'observed_call_sites': 0, 'behavior_verified': False}

    def identify(descriptor):
        if not descriptor:
            return None
        matches = symbol_index.get((descriptor['path'], descriptor['first_line'], descriptor['name']), [])
        return matches[0] if len(matches) == 1 else None

    for path in paths:
        trace = read_trace(path, report)
        run_id = len(result['runs'])
        result['runs'].append({'id': run_id, 'trace': str(Path(path).resolve()),
            'status': trace['status'], 'outcome': trace['outcome'],
            'reasons': trace.get('reasons', []), 'scope': trace.get('scope'),
            'limits': trace.get('limits', [])})
        for edge in trace['edges']:
            target, caller = identify(edge['target']), identify(edge.get('caller'))
            if target:
                symbols[target]['runtime_observed'] = True
            site = edge.get('site')
            candidates = []
            if caller and site:
                for call in by_owner.get(caller, []):
                    if site.get('column') is not None:
                        exact = (call['line'], call['column'], call['end_line'], call['end_column'])
                        observed = (site['line'], site['column'], site.get('end_line'), site.get('end_column'))
                        if exact == observed:
                            candidates.append(call['id'])
                    elif call['line'] <= site['line'] <= call['end_line']:
                        candidates.append(call['id'])
            # A line-only event may come from __getitem__/a descriptor rather
            # than the one explicit AST Call on that line. Never promote it.
            match = candidates[0] if len(candidates) == 1 and site.get('column') is not None else None
            observation = {'run': run_id, **edge, 'caller_symbol': caller,
                'target_symbol': target, 'call_site': match, 'candidate_sites': candidates,
                'match': 'site' if match else ('line_candidates' if candidates else 'unmapped')}
            result['edges'].append(observation)
            if match:
                calls[match]['runtime_observations'].append(len(result['edges']) - 1)
    result['observed_symbols'] = sum(s['runtime_observed'] for s in symbols.values())
    result['observed_call_sites'] = sum(bool(c['runtime_observations']) for c in calls.values())
    result['unobserved_call_sites'] = len(calls) - result['observed_call_sites']
    result['ambiguous_edges'] = sum(e['match'] == 'line_candidates' for e in result['edges'])
    report['runtime'] = result
    return result
