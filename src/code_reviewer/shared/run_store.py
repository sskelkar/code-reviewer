"""The on-disk layout of one review run (see proposal section 7). Only this module knows file names."""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .criteria import CriteriaSet
from .models import PRContext, ReviewInput


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


class RunStore:
    """Files of one run. The Reviewer's checkout is deliberately NOT in here: it lives in its own temporary directory,
    so earlier findings, the master list and reports are outside its working directory. Claude Code enforces
    access to that directory; the temporary directory itself is not an OS sandbox."""

    def __init__(self, root: Path):
        self.root = root
        self.input_dir = root / "input"
        self.report_path = root / "report.md"
        self.summary_path = root / "summary.md"
        self.input_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._checkout_parent: Optional[Path] = None

    @classmethod
    def create(cls, runs_path: Path, ctx: PRContext, label: str = "") -> "RunStore":
        runs_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = "%s_%s_%d%s" % (time.strftime("%Y%m%d-%H%M%S"), ctx.repo.replace("/", "-"), ctx.number, "_%s" % label if label else "")
        path, suffix = runs_path / name, 1
        while True:
            try:
                path.mkdir(mode=0o700)
                break
            except FileExistsError:
                suffix += 1
                path = runs_path / ("%s-%d" % (name, suffix))
        return cls(path)

    def prepare_checkout(self) -> Path:
        """Create the temporary directory for the Reviewer's read-only checkout (outside the run directory)."""
        if self._checkout_parent is None:
            self._checkout_parent = Path(tempfile.mkdtemp(prefix="code-reviewer-checkout-"))
        return self._checkout_parent / "checkout"

    @property
    def checkout(self) -> Path:
        if self._checkout_parent is None:
            raise RuntimeError("the checkout has not been prepared yet")
        return self._checkout_parent / "checkout"

    def pass_dir(self, pass_no: int) -> Path:
        path = self.root / ("pass-%02d" % pass_no)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def dump(self, relative: str, data: Any) -> None:
        dump_json(self.root / relative, data)

    def save_pr_text(self, ctx: PRContext, redacted_emails: int = 0) -> None:
        """The PR text the Reviewer is given (title, description, commit messages), as sent: after any email redaction."""
        dump_json(self.input_dir / "pr.json", {
            "redacted_emails": redacted_emails,
            "repo": ctx.repo, "number": ctx.number, "url": ctx.url, "author": ctx.author, "state": ctx.state,
            "title": ctx.title, "description": ctx.body, "commit_messages": ctx.commit_messages,
            "base_ref": ctx.base_ref, "base_sha": ctx.base_sha, "head_sha": ctx.head_sha, "merge_base": ctx.merge_base,
        })

    def save_prompts(self, system: str, user: str) -> None:
        """The exact prompts the Reviewer receives. They are identical in every pass."""
        (self.input_dir / "reviewer.system.md").write_text(system, encoding="utf-8")
        (self.input_dir / "reviewer.user.md").write_text(user, encoding="utf-8")

    def save_inputs(self, review_input: ReviewInput, criteria: CriteriaSet) -> None:
        (self.input_dir / "diff.txt").write_text(review_input.diff_block + "\n", encoding="utf-8")
        (self.input_dir / "criteria_block.md").write_text(review_input.criteria_block + "\n", encoding="utf-8")
        dump_json(self.input_dir / "criteria_index.json", criteria.index())
        project = criteria.project
        dump_json(self.input_dir / "criteria_manifest.json", {
            "manifest": project.manifest, "source": project.source, "files_used": project.files_used,
            "auto_included": project.auto_included, "ignored": project.ignored, "notes": project.notes, "warnings": criteria.warnings,
        })

    def save_outputs(self, master: Dict[str, Any], stats: Dict[str, Any]) -> None:
        self.dump("master.json", master)
        self.dump("stats.json", stats)
        self.dump("labels.json", {"marks": {}, "dropped": [], "missed": []})

    def save_binary_omissions(self, ctx: PRContext) -> None:
        if ctx.binary_omissions:
            stats = json.loads((self.root / 'stats.json').read_text(encoding='utf-8'))
            stats['binary_omissions'] = ctx.binary_omissions
            self.dump('stats.json', stats)

    def cleanup_checkout(self) -> None:
        if self._checkout_parent is not None:
            shutil.rmtree(self._checkout_parent, ignore_errors=True)
            self._checkout_parent = None
