"""Observe Python call events without collecting arguments or local values."""
import dis
import hashlib
from pathlib import Path
import sys
import threading
from types import CodeType

from .model import Index


def code_tree(code):
    yield code
    for value in code.co_consts:
        if isinstance(value, CodeType):
            yield from code_tree(value)


class Recorder:
    def __init__(self, root, max_events=200000):
        if not 1 <= max_events <= 10000000:
            raise ValueError('max-events must be between 1 and 10000000')
        self.index = Index(root).load()
        self.max_events = max_events
        self.expected, self.metadata, self.positions = {}, {}, {}
        self.sites = {}
        self.edges, self.observed = {}, {}
        self.events, self.unverified = 0, 0
        self.truncated = False
        self.active = False
        self.profile_changes = 0
        self.thread_failures = []
        self.lock = threading.RLock()
        for row in self.index.files:
            if not row['parsed']:
                continue
            path = self.index.root / row['path']
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Source changed during inventory: ' + row['path'])
            code = compile(raw, str(path), 'exec', dont_inherit=True)
            self.expected[row['path']] = set(code_tree(code))

    def identify(self, code):
        # Code objects compare independently of filenames; describe caches by
        # identity so identical code in different files stays distinct.
        path = Path(code.co_filename)
        if not path.is_absolute():
            return None
        try:
            relative = path.resolve().relative_to(self.index.root).as_posix()
        except ValueError:
            return None
        if relative not in self.expected:
            return None
        return {'path': relative, 'name': code.co_name,
                'first_line': code.co_firstlineno,
                'code_verified': code in self.expected[relative]}

    def describe(self, code):
        key = id(code)
        if key not in self.metadata:
            self.metadata[key] = (code, self.identify(code))
        return self.metadata[key][1]

    def position(self, frame):
        key = id(frame.f_code)
        site_key = (key, frame.f_lasti, frame.f_lineno)
        if site_key in self.sites:
            return self.sites[site_key]
        if key not in self.positions:
            instructions = dis.get_instructions(frame.f_code)
            self.positions[key] = {item.offset: getattr(item, 'positions', None)
                                   for item in instructions}
        offsets = self.positions[key]
        offset = max((n for n in offsets if n <= frame.f_lasti), default=-1)
        pos = offsets.get(offset)
        self.sites[site_key] = {'line': frame.f_lineno,
                'column': getattr(pos, 'col_offset', None),
                'end_line': getattr(pos, 'end_lineno', None),
                'end_column': getattr(pos, 'end_col_offset', None)}
        return self.sites[site_key]

    def record(self, frame, event, arg):
        if not self.active:
            return
        if event == 'c_call' and arg is sys.setprofile:
            self.profile_changes += 1
        if event == 'call' and frame.f_code is threading.setprofile.__code__:
            self.profile_changes += 1
        if event != 'call':
            return
        with self.lock:
            target = self.describe(frame.f_code)
            if target is None:
                return
            if self.events >= self.max_events:
                self.truncated = True
                return
            self.events += 1
            caller = self.describe(frame.f_back.f_code) if frame.f_back else None
            if not target['code_verified'] or (caller and not caller['code_verified']):
                self.unverified += 1
                return
            site = self.position(frame.f_back) if caller else None
            target_key = (target['path'], target['first_line'], target['name'])
            self.observed[target_key] = self.observed.get(target_key, 0) + 1
            caller_key = tuple(caller.values()) if caller else None
            key = (caller_key, tuple(site.values()) if site else None, target_key)
            row = self.edges.setdefault(key, {'caller': caller, 'site': site,
                'target': target, 'count': 0, 'relation': 'observed_python_stack'})
            row['count'] += 1

    def start(self):
        if self.active or sys.getprofile() is not None or threading.getprofile() is not None:
            raise ValueError('An existing profiler is active; use a separate interpreter')
        self.active = True
        self.owner = threading.get_ident()
        self.initial_threads = {t.ident for t in threading.enumerate()}
        self.old_excepthook = threading.excepthook
        threading.excepthook = self.thread_exception
        threading.setprofile(self.record)
        sys.setprofile(self.record)

    def thread_exception(self, args):
        with self.lock:
            self.thread_failures.append({'exception_type': args.exc_type.__name__})
        self.old_excepthook(args)

    def stop(self):
        self.active = False
        replaced = sys.getprofile() != self.record or threading.getprofile() != self.record
        # Do not remove a profiler installed by the target itself.
        if sys.getprofile() == self.record:
            sys.setprofile(None)
        if threading.getprofile() == self.record:
            threading.setprofile(None)
        exception_hook_replaced = threading.excepthook != self.thread_exception
        if not exception_hook_replaced:
            threading.excepthook = self.old_excepthook
        before = {f['path']: f['sha256'] for f in self.index.files}
        try:
            after = {f['path']: f['sha256'] for f in Index(self.index.root).load().files}
        except (OSError, ValueError):
            after = {}
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        alive = [t for t in threading.enumerate() if t.ident not in self.initial_threads]
        reasons = []
        if changed:
            reasons.append('source_changed')
        if replaced:
            reasons.append('profiler_replaced')
        if self.profile_changes:
            reasons.append('profiler_changed_during_run')
        if self.thread_failures:
            reasons.append('thread_failed')
        if exception_hook_replaced:
            reasons.append('thread_exception_hook_replaced')
        if self.truncated:
            reasons.append('event_limit')
        if self.unverified:
            reasons.append('runtime_code_differs_from_source')
        if alive:
            reasons.append('threads_still_running')
        return {'schema_version': 1, 'kind': 'archify-python-runtime',
            'source_root': str(self.index.root), 'files': self.index.files,
            'python': sys.version.split()[0], 'status': 'PARTIAL' if reasons else 'PASS',
            'integrity': 'changed' if changed else 'matching', 'changed_files': changed,
            'reasons': reasons, 'events': self.events, 'unverified_events': self.unverified,
            'thread_failures': self.thread_failures,
            'edges': list(self.edges.values()),
            'scope': 'current and newly started Python threads, in this process',
            'limits': ['Only observed Python calls are evidence; unexecuted paths remain unknown.',
                       'C/native internals, subprocesses and previously running threads are not traced.',
                       'Edges connect visible Python frames; native callback intermediaries can be hidden.',
                       'Generator/coroutine resumptions can emit additional call events.',
                       'Successful execution does not verify output correctness or performance.']}
