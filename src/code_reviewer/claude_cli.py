"""Compatibility import; implementation lives in code_reviewer.shared.claude_cli."""
from __future__ import annotations

import sys
from importlib import import_module

sys.modules[__name__] = import_module('code_reviewer.shared.claude_cli')
