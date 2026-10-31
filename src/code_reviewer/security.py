"""Offline configuration checks shared by doctor and review preflight."""
from __future__ import annotations

from .backends import factory


def readiness(cfg, ref=None, options=None):
    model_errors = factory.configuration_errors(cfg, dry_run=getattr(options, 'dry_run', False))
    vm_errors = []
    if cfg.sandbox:
        from .sandbox.review import configuration_errors
        vm_errors = configuration_errors(cfg, ref, options)
    errors = list(dict.fromkeys(model_errors + vm_errors))
    return {'ready': not errors, 'provider': cfg.provider, 'auth_mode': cfg.auth_mode, 'execution': 'vm' if cfg.sandbox else 'host',
            'configuration_errors': errors,
            'blockers': (['model-configuration'] if model_errors else []) + (['vm-configuration'] if vm_errors else []),
            'runtime_checked': False,
            'diagnostic': 'Offline configuration only; no artifact, worker, credential, target or model checks were performed.'}
