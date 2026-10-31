"""Usage and cost accounting for a review run."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..backends.contract import TOKEN_KEYS, LLMResult


class Stats:
    """Records every model invocation, including failed and retried ones."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def record(self, role: str, pass_no: Optional[int], result: LLMResult, attempt: int, model: str) -> None:
        self.calls.append({
            "role": role,
            "pass": pass_no,
            "attempt": attempt,
            "ok": result.ok,
            "error": result.error,
            "model": result.model or model,
            "cost_usd": result.cost_usd,
            "tokens": dict(result.tokens),
            **({"usage_known": False} if not result.usage_known else {}),
            **({"budget_tokens_used": result.budget_tokens_used} if result.budget_tokens_used is not None else {}),
            "duration_ms": result.duration_ms,
            "num_turns": result.num_turns,
            **({"billing_mode": result.billing_mode} if result.billing_mode != "unknown" else {}),
            **({"http_errors": result.http_errors} if result.http_errors else {}),
        })

    def total_cost(self) -> float:
        return sum(c["cost_usd"] or 0.0 for c in self.calls)

    def cost_known(self) -> bool:
        return all(c["cost_usd"] is not None for c in self.calls)

    def total_tokens(self) -> int:
        """Every token the adapter reported, including cache reads."""
        return sum(sum(c["tokens"].values()) for c in self.calls)

    def budget_tokens(self) -> int:
        """API: fresh input/output/cache writes; subscription: all tokens or reserved unknown usage."""
        return sum(c.get("budget_tokens_used", c["tokens"].get("input", 0) + c["tokens"].get("output", 0) + c["tokens"].get("cache_creation", 0))
                   for c in self.calls)

    def token_totals(self) -> Dict[str, int]:
        return {key: sum(c["tokens"].get(key, 0) for c in self.calls) for key in TOKEN_KEYS}

    def duration_s(self) -> float:
        return sum((c["duration_ms"] or 0) for c in self.calls) / 1000.0

    def retries_and_failures(self) -> Dict[str, int]:
        return {
            "calls": len(self.calls),
            "retries": sum(1 for c in self.calls if c["attempt"] > 1),
            "failed_calls": sum(1 for c in self.calls if not c["ok"]),
        }

    def by_role(self) -> Dict[str, Dict[str, Any]]:
        roles: Dict[str, Dict[str, Any]] = {}
        for call in self.calls:
            entry = roles.setdefault(call["role"], {"calls": 0, "cost_usd": 0.0, "tokens": 0})
            entry["calls"] += 1
            entry["cost_usd"] += call["cost_usd"] or 0.0
            entry["tokens"] += sum(call["tokens"].values())
        return roles

    def by_pass_role(self, pass_no: int, role: str) -> Dict[str, Any]:
        calls = [c for c in self.calls if c["pass"] == pass_no and c["role"] == role]
        return {
            "cost_usd": sum(c["cost_usd"] or 0.0 for c in calls),
            "tokens": sum(sum(c["tokens"].values()) for c in calls),
            "calls": len(calls),
        }

    def to_dict(self) -> Dict[str, Any]:
        roles = self.by_role()
        subscription = bool(self.calls) and all(c.get('billing_mode') == 'subscription' for c in self.calls)
        if subscription:
            roles = {role: {**value, 'cost_usd': None} for role, value in roles.items()}
        return {
            "total_cost_usd_estimated": None if self.calls and all(c.get("billing_mode") == "subscription" for c in self.calls) else self.total_cost(),
            "cost_known_for_all_calls": self.cost_known(),
            "total_tokens": self.total_tokens(),
            "budget_tokens": self.budget_tokens(),
            "token_totals": self.token_totals(),
            "duration_s": self.duration_s(),
            **self.retries_and_failures(),
            "by_role": roles,
            "calls": self.calls,
        }
