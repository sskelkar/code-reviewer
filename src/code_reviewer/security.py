"""Release evidence belongs to code, never a target setting or a model's assertion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .shared.models import ReviewRefused


@dataclass(frozen=True)
class Control:
    id: str
    status: str
    reason: str


# Partial controls deliberately block release. Replace a row only alongside the
# enforcement implementation and its containment evidence, not an operator flag.
COMMON_CONTROLS: Tuple[Control, ...] = (
    Control('isolated-acquisition', 'missing', 'PR fetching/parsing is not integrated inside a qualified boundary'),
    Control('vmm-confinement', 'partial', 'production jailer and host resource containment are not qualified'),
    Control('immutable-filesystem', 'partial', 'guest runtime/head snapshot are not immutable against native compromise'),
    Control('guest-execution-policy', 'missing', 'guest execution/syscall allow-list enforcement is not implemented'),
    Control('crash-recovery', 'partial', 'deadlines and stale-state refusal exist; independent recovery is not qualified'),
    Control('private-report-workflow', 'partial', 'bounded export exists; integrated private local retention/rendering is incomplete'),
)
PROVIDER_CONTROLS = {
    'claude': (
        Control('provider-credentials', 'missing', 'authenticated Claude guest credentials/lifetime are not qualified'),
        Control('provider-egress-tools', 'partial', 'offline startup passes; authenticated egress and native tools are not qualified'),
    ),
    'openai': (
        Control('provider-credentials', 'partial', 'the current adapter places a reusable API key inside the guest'),
        Control('provider-egress-tools', 'partial', 'no-key CONNECT probes pass; authenticated backend qualification is incomplete'),
    ),
}


def readiness(provider: str) -> Dict[str, Any]:
    if provider not in PROVIDER_CONTROLS:
        raise ReviewRefused('unsupported provider; no fallback')
    rows = COMMON_CONTROLS + PROVIDER_CONTROLS[provider]
    blockers = [row.id for row in rows if row.status != 'qualified']
    return {'ready': not blockers, 'provider': provider, 'blockers': blockers,
            'controls': [{'id': row.id, 'status': row.status, 'reason': row.reason} for row in rows]}


def require_ready(provider: str) -> None:
    report = readiness(provider)
    if not report['ready']:
        raise ReviewRefused('production review unavailable: ' + ', '.join(report['blockers'])
                            + '; use review --preflight for details; no host fallback')
