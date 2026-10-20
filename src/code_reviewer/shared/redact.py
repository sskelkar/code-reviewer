"""Redaction of personal data in the pull request text before it is sent to the model.

Only the PR title, description and commit messages are redacted. The diff and the repository files are the subject of the
review and are sent as they are.
"""
from __future__ import annotations

import re
from typing import Tuple

PLACEHOLDER = "[email]"

# local part, "@", then a domain whose last label is letters only. That excludes versions such as `lodash@4.17.21`.
_EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]*)@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")


def redact_emails(text: str) -> Tuple[str, int]:
    """Replace email addresses with `[email]`. Returns the new text and how many were replaced."""
    count = 0

    def replace(match: "re.Match[str]") -> str:
        nonlocal count
        if match.group(1).lower() == "git":  # ssh clone addresses such as git@github.com:org/repo are not personal data
            return match.group(0)
        count += 1
        return PLACEHOLDER

    return _EMAIL.sub(replace, text or ""), count
