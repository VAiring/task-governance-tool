"""Small, bounded operation-local counters; no per-iteration log records."""
from types import FunctionType

from .debug_log_values import code_location

MAX_GROUPS = 32
MAX_COUNT = 2**63 - 1
RESERVED_EVENTS = MAX_GROUPS + 1
COUNT_UNITS = frozenset({'items', 'bytes'})
STATES = frozenset({'local', 'full_scan', 'reused', 'revalidated', 'unchanged',
                    'contended', 'deadline', 'limit', 'skipped', 'published', 'failed'})


class ItemCount:
    """A caller supplies already-known counts; the logger never walks results."""
    def __init__(self):
        self.value = 0
        self.supplied = self.invalid = self.saturated = False

    def add(self, value):
        if type(value) is not int or value < 0:
            self.invalid = True
            return
        self.supplied = True
        if value > MAX_COUNT - self.value:
            self.value, self.saturated = MAX_COUNT, True
        else:
            self.value += value

    def fields(self):
        return {'item_count': self.value if self.supplied else None,
                'item_count_known': self.supplied and not self.invalid and not self.saturated,
                'count_saturated': self.saturated}


class Aggregates:
    def __init__(self, mode, emit):
        self.mode, self.emit = mode, emit
        self.groups = {}
        self.methods = {}
        self.started = self.finished = self.omitted = self.unknown = self.failures = 0
        self.saturated = self.closed = False

    def add(self, values, key, value):
        total = values[key] + value
        self.saturated |= total > MAX_COUNT
        values[key] = min(total, MAX_COUNT)

    def bump(self, key):
        total = getattr(self, key) + 1
        self.saturated |= total > MAX_COUNT
        setattr(self, key, min(total, MAX_COUNT))

    def begin(self, function, unit='items'):
        if self.closed:
            return None
        self.bump('started')
        unit = unit if type(unit) is str and len(unit) <= 16 and unit in COUNT_UNITS else 'unknown'
        code = function.__code__ if type(function) is FunctionType else None
        cached = self.methods.get((id(code), unit))
        if cached is not None:
            return cached[1]
        location = code_location(code) if code is not None else {}
        if location:
            location['line'] = function.__code__.co_firstlineno
            key = (location['module'], location['function'], location['line'], unit)
        else:
            key, location = ('other', unit), {'function': 'other'}
            self.bump('unknown')
        group = self.groups.get(key)
        if group is None:
            if len(self.groups) >= MAX_GROUPS:
                self.bump('omitted')
                return None
            group = {'measurement_id': len(self.groups) + 1, 'method': location,
                     'count_unit': unit, 'states': {},
                     'calls': 0, 'measured_calls': 0, 'total_ns': 0, 'max_ns': 0,
                     'item_count': 0, 'supplied_count_calls': 0, 'counted_calls': 0, 'unknown_count_calls': 0,
                     'failed_calls': 0, 'cancelled_calls': 0}
            self.groups[key] = group
        if code is not None and location.get('module') and len(self.methods) < MAX_GROUPS:
            # Keep the code alive against id reuse, never the function/arguments.
            self.methods[id(code), unit] = (code, group)
        return group

    def end(self, group, duration, outcome, counts, states=()):
        if self.closed:
            return
        self.bump('finished')
        if group is None or self.mode == 'errors' and outcome == 'succeeded':
            return
        self.add(group, 'calls', 1)
        for state in states:
            group['states'].setdefault(state, 0)
            self.add(group['states'], state, 1)
        if type(duration) is int and duration >= 0:
            self.add(group, 'measured_calls', 1)
            self.add(group, 'total_ns', duration)
            group['max_ns'] = min(MAX_COUNT, max(group['max_ns'], duration))
        if counts.supplied:
            self.add(group, 'supplied_count_calls', 1)
            self.add(group, 'item_count', counts.value)
        self.saturated |= counts.saturated
        self.add(group, 'counted_calls' if counts.supplied and not counts.invalid and not counts.saturated
                 else 'unknown_count_calls', 1)
        if outcome != 'succeeded':
            self.add(group, 'cancelled_calls' if outcome == 'cancelled' else 'failed_calls', 1)

    def finish(self, *, publish):
        if self.closed:
            return
        self.closed = True
        if publish and self.started:
            for group in self.groups.values():
                if group['calls']:
                    self.emit('performance.aggregate', {**group, 'timing_scope': 'inclusive_body',
                        'total_ns': group['total_ns'] if group['measured_calls'] else None,
                        'max_ns': group['max_ns'] if group['measured_calls'] else None,
                        'item_count': group['item_count'] if group['supplied_count_calls'] else None},
                        failed=bool(group['failed_calls'] or group['cancelled_calls']), reserved=True)
            partial = bool(self.omitted or self.unknown or self.failures or self.saturated
                           or self.started != self.finished)
            if self.mode == 'debug' or partial:
                self.emit('performance.summary', {'started_calls': self.started,
                    'finished_calls': self.finished, 'omitted_calls': self.omitted,
                    'unknown_method_calls': self.unknown, 'observation_failures': self.failures,
                    'counter_saturated': self.saturated, 'partial': partial}, failed=partial, reserved=True)
        self.groups.clear()
        self.methods.clear()
