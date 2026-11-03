# Contributing

Thank you for helping improve code-reviewer. Use [GitHub issues](https://github.com/sskelkar/code-reviewer/issues) for reproducible bugs and feature proposals, and pull requests for focused changes.

## Find your way around

- [Development guide](docs/development.md): installation, tests, debugging and deployment checks.
- [Architecture](docs/architecture.md): package ownership and review orchestration.
- [Code walkthrough](docs/project-explainer.md): method-level call chains from the CLI to reports.
- [Roadmap](docs/proposal.md#checklist): outstanding work and qualification evidence.
- [Repository guidance](AGENTS.md): compatibility, security and implementation rules.

## Report a bug

Include the command/options, Python and OS versions, execution mode, provider/authentication mode and sanitized error wording. A small synthetic reproducer is preferable to a saved review report.

Keep API keys, OAuth tokens, private source, personal data and confidential PR identifiers out of issues and pull requests. Do not attach unredacted `local.env`, operator configuration, reports or traces. Avoid publishing exploit details in public issues.

## Submit a change

Keep changes scoped and update the guide that owns the behavior. Link to existing explanations rather than copying them into multiple documents. Documentation should explain the supported product to a new reader, without depending on conversations or development history.

Run the standard-library test suite on Python 3.13, as described in the development guide. Unit tests use synthetic repositories and fake providers; they must not use real credentials, network access, model calls or VM boots. Deployment checks are separate, explicit operations.

Changes to prompts, criteria or schemas require separately authorized before/after review comparisons and honest evidence under [the comparison policy](docs/proposal.md#review-quality-experiments). Passing offline tests alone does not establish review quality.
