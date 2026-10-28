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
    Control('isolated-acquisition', 'partial', 'public in-VM acquisition and source revocation pass no-key checks; private acquisition and normal review dispatch remain unsupported'),
    Control('vmm-confinement', 'partial', 'jailed VMM and systemd resource controls pass no-key checks; full lifecycle/release evidence remains incomplete'),
    Control('immutable-filesystem', 'partial', 'root-owned runtime and read-only admitted input pass native checks; supported live workflow is not integrated'),
    Control('guest-execution-policy', 'partial', 'non-root/no-new-privileges enforced; custom guest syscall policy is deferred under the simplified scope'),
    Control('crash-recovery', 'partial', 'independent deadlines, supervisor-loss checks and explicit local recovery pass; full live review integration remains incomplete'),
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
