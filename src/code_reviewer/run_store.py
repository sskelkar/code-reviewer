"""The on-disk layout of one review run (see proposal section 7). Only this module knows file names."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict

from .criteria import CriteriaSet
from .models import PRContext, ReviewInput


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.input_dir = root / "input"
        self.checkout = root / "checkout"
        self.report_path = root / "report.md"
        self.summary_path = root / "summary.md"
        self.input_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(cls, runs_path: Path, ctx: PRContext) -> "RunStore":
        runs_path.mkdir(parents=True, exist_ok=True)
        name = "%s_%s_%d" % (time.strftime("%Y%m%d-%H%M%S"), ctx.repo.replace("/", "-"), ctx.number)
        path, suffix = runs_path / name, 1
        while path.exists():
            suffix += 1
            path = runs_path / ("%s-%d" % (name, suffix))
        path.mkdir()
        return cls(path)

    def pass_dir(self, pass_no: int) -> Path:
        path = self.root / ("pass-%02d" % pass_no)
        path.mkdir(exist_ok=True)
        return path

    def dump(self, relative: str, data: Any) -> None:
        dump_json(self.root / relative, data)

    def save_inputs(self, review_input: ReviewInput, criteria: CriteriaSet) -> None:
        (self.input_dir / "diff.txt").write_text(review_input.diff_block + "\n", encoding="utf-8")
        (self.input_dir / "criteria_block.md").write_text(review_input.criteria_block + "\n", encoding="utf-8")
        dump_json(self.input_dir / "criteria_index.json", criteria.index())
        project = criteria.project
        dump_json(self.input_dir / "criteria_manifest.json", {
            "manifest": project.manifest, "source": project.source, "files_used": project.files_used,
            "ignored": project.ignored, "notes": project.notes, "warnings": criteria.warnings,
        })

    def save_outputs(self, master: Dict[str, Any], stats: Dict[str, Any]) -> None:
        self.dump("master.json", master)
        self.dump("stats.json", stats)
        self.dump("labels.json", {"marks": {}, "dropped": [], "missed": []})

    def cleanup_checkout(self) -> None:
        shutil.rmtree(self.checkout, ignore_errors=True)
