# Code quality default criteria

Canonical rules for this persona. Static assignments and focus instructions are in [personas.json](../personas.json); common review requirements are in [review-standard.md](../review-standard.md). Project rules take precedence.

## [CQ-001] Keep logic and responsibilities understandable and maintainable

**Inspect:** Changed branching/nesting, responsibility boundaries, abstractions, coupling and duplicated logic. Look for one business decision repeated across implementations, hidden dependencies on call ordering, mutable globals or process state, or an abstraction that obscures a constraint the surrounding code needs to handle.

**Report:** Avoidable complexity, coupling or duplication creates a specific maintenance problem demonstrated by the repository. Explain what becomes hard to change correctly, which implementations must stay synchronized, or which hidden dependency makes a concrete modification error-prone. Keep any suggested restructuring proportionate to that consequence.

**Do not report:** A design-pattern preference, a SOLID/open-closed slogan without a practical consequence, function length or nesting alone, an extraction based only on superficial similarity, or speculation about future extensibility. An if/else chain is not automatically wrong. Do not repeat a correctness defect solely to recommend refactoring its location.

## [CQ-002] Keep code explanations and contract documentation accurate

**Inspect:** Changed names, comments, instructions and examples against the implementation. For changed API, event, schema or configuration contracts, inspect the corresponding repository-visible contract documentation. Check whether explanations expose a non-obvious constraint that the repository demonstrates users must understand.

**Report:** A misleading name/description/example, an unexplained demonstrated constraint, or missing/inaccurate contract details enables a concrete misunderstanding or misuse. Identify the affected code/document, what changed, and the incorrect use or interpretation it causes. For a missing document update, establish the appropriate document or documentation location from the repository.

**Do not report:** Personal naming/formatting preferences, comments that merely restate code, drift in planning/intent documents, or assumptions that an external document exists. Do not invent a required documentation location or request explanations without identifying what needs explaining and why.
