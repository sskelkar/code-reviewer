"""Reserve whole call allowances atomically; unused pools never subsidize another role."""
from __future__ import annotations

import math
import threading
from typing import Any

from ...shared.stats import Stats
from .criteria import PERSONAS


class RunStopped(RuntimeError):
    def __init__(self, pool: str):
        self.pool = pool
        super().__init__('%s budget exhausted' % pool)


class Budget:
    def __init__(self, cfg: Any):
        self.cfg = cfg
        cents = math.floor((cfg.run_budget_usd + 1e-9) * 100)
        shared = [cents * weight // 30 for weight in (3, 3, 4)]
        remainder = cents - sum(shared)
        each, extra = divmod(remainder, len(PERSONAS))
        allocations = dict(zip(('criteria', 'comparator', 'verifier'), shared))
        allocations.update({p: each + (n < extra) for n, p in enumerate(PERSONAS)})
        self.pools = {p: {'allocated_usd': c / 100, 'spent_usd': 0.0, 'reserved_usd': 0.0,
                          'unknown_cost_calls': 0} for p, c in allocations.items()}
        self.tokens = 0
        self.cancelled = threading.Event()
        self.lock = threading.RLock()

    def check(self, pool: str) -> None:
        with self.lock:
            if self.cancelled.is_set():
                raise KeyboardInterrupt
            if self.tokens >= self.cfg.run_budget_tokens:
                raise RunStopped('global')
            p = self.pools[pool]
            if p['allocated_usd'] - p['spent_usd'] - p['reserved_usd'] + 1e-9 < .01:
                raise RunStopped(pool)

    def reserve(self, pool: str, requested: float) -> float:
        with self.lock:
            self.check(pool)
            p = self.pools[pool]
            amount = math.floor((min(requested, p['allocated_usd'] - p['spent_usd'] - p['reserved_usd']) + 1e-9) * 100) / 100
            p['reserved_usd'] += amount
            return amount

    def settle(self, pool: str, allowance: float, result: Any) -> None:
        with self.lock:
            p = self.pools[pool]
            p['reserved_usd'] -= allowance
            cost = result.cost_usd if result is not None else None
            p['spent_usd'] += allowance if cost is None else cost
            p['unknown_cost_calls'] += cost is None
            if result is not None:
                self.tokens += sum(result.tokens.get(k, 0) for k in ('input', 'output', 'cache_creation'))

    def snapshot(self) -> Any:
        with self.lock:
            return {p: dict(values) for p, values in self.pools.items()}


class ConcurrentStats(Stats):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()

    def record(self, *args, **kwargs):
        with self.lock:
            super().record(*args, **kwargs)
