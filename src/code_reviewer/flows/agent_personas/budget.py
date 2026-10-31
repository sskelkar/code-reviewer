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
        self.subscription = cfg.auth_mode == 'subscription'
        self.unit = 'tokens' if self.subscription else 'usd'
        cents = cfg.run_budget_tokens if self.subscription else math.floor((cfg.run_budget_usd + 1e-9) * 100)
        shared = [cents * weight // 30 for weight in (3, 3, 4)]
        remainder = cents - sum(shared)
        each, extra = divmod(remainder, len(PERSONAS))
        allocations = dict(zip(('criteria', 'comparator', 'verifier'), shared))
        allocations.update({p: each + (n < extra) for n, p in enumerate(PERSONAS)})
        self.pools = {p: {'allocated_' + self.unit: c if self.subscription else c / 100,
                          'spent_' + self.unit: 0, 'reserved_' + self.unit: 0,
                          'unknown_usage_calls' if self.subscription else 'unknown_cost_calls': 0}
                      for p, c in allocations.items()}
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
            if p['allocated_' + self.unit] - p['spent_' + self.unit] - p['reserved_' + self.unit] + 1e-9 < (1 if self.subscription else .01):
                raise RunStopped(pool)

    def reserve(self, pool: str, requested: float) -> float:
        with self.lock:
            self.check(pool)
            p = self.pools[pool]
            available = min(requested, p['allocated_' + self.unit] - p['spent_' + self.unit] - p['reserved_' + self.unit])
            amount = int(available) if self.subscription else math.floor((available + 1e-9) * 100) / 100
            p['reserved_' + self.unit] += amount
            return amount

    def settle(self, pool: str, allowance: float, result: Any) -> None:
        with self.lock:
            p = self.pools[pool]
            p['reserved_' + self.unit] -= allowance
            if self.subscription:
                known = result is not None and result.usage_known
                used = result.total_tokens if known else max(allowance, result.total_tokens if result is not None else 0)
                p['spent_tokens'] += used
                p['unknown_usage_calls'] += not known
                self.tokens += used
                if result is not None:
                    result.budget_tokens_used = used
            else:
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
