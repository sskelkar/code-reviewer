"""Render local or privately exported multi-perspective review results."""
from __future__ import annotations

from .review import report

TIERS = ('High', 'Medium', 'Low')


def render_summary(result, tiers, dropped):
    from .sandbox.review import ExportedReview
    if isinstance(result, ExportedReview):
        if list(tiers) != result.cfg.post_tiers or dropped:
            raise ValueError('VM summaries use the filters selected before review')
        return result.summary_text
    return report.render_summary(result, tiers, dropped)


def run_line(result):
    from .sandbox.review import ExportedReview
    if isinstance(result, ExportedReview):
        return 'VM review: %s (%s); usage is in stats.json' % (result.stop_reason, result.cfg.provider)
    return report.run_line(result)


def render_dry_run(dry):
    return report.render_dry_run(dry)


def render_finding(result, item):
    return report.render_finding(result, item)
