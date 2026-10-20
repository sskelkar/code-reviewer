# agent-personas (not implemented)

This folder owns the future four-persona workflow. Selecting it currently fails before PR preparation, checkout creation, prompt loading or model calls; it never falls back to single-agent review.

The agreed design is in [proposal section 12](../../../../docs/proposal.md#12-agreed-persona-design-and-next-steps), and the [criteria draft](../../../../docs/persona-criteria.md) is not active. Add the scheduler, criteria distribution, flow-specific roles/master/report and prompts/criteria here when implementation is authorized. Shared mechanics belong in `code_reviewer.shared`; do not add persona branches to the single-agent loop.
