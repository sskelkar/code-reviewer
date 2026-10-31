"""The REPL's static text: the welcome banner, the `help` text and the usage line. Pure data, no logic.

Keep `COMMAND_GROUPS` in step with `Session.COMMANDS` in repl.py; a test fails if a command is missing from the banner.
"""
from __future__ import annotations

from typing import Optional

REVIEW_USAGE = "review PR [--preflight] [--provider claude|openai] [--mode single-agent|agent-personas] [--passes N] [--parallel N] [--model NAME] [--no-self-check] [--keep-checkout] [--verbose] [--dry-run]"

HELP = """\
Production review is gated pending security qualification. Use review <PR URL> --preflight first.
Commands:
  review <PR URL | owner/repo#n> [options]
      Review a pull request (several independent passes, then a merged report). Options:
        --preflight       show release blockers; no fetch, model call or credentials
        --provider NAME   claude or openai; the same security gates apply
        --parallel N      persona-only concurrent calls, 1 to 4 (default 4)
        --mode NAME       single-agent (default) or agent-personas (3 passes each by default)
        --passes N        single-agent: maximum passes (1 for a single, cheap pass); agent-personas: passes per persona
        --model NAME      model for all roles, for example haiku or sonnet
        --no-self-check   skip the reviewer's self-refutation phase (cheaper, findings are not double-checked)
        --keep-checkout   keep the temporary checkout (its location is printed), for debugging
        --verbose         print extra progress detail
        --dry-run         show exactly what the reviewer would be given, without calling the model
      `review --help` shows the options in full.
  show <F-id>                        Show one finding in full.
  drop <F-id> / undrop <F-id>        Leave a finding out of the posted comment (or put it back).
  mark <F-id> real|nit|wrong         Record your judgment of a finding (saved with the run).
  missed "<description>"             Record an issue you found that the agent missed (saved with the run).
  summary [--tiers High,Medium,Low]  Show the review summary again: the text `post` would send, with your drops applied.
  post [--tiers High,Medium,Low]     Preview the PR comment and post it after you confirm.
  open                               Open the full report file.
  help                               Show this help.
  quit                               Leave.
"""


# The commands, grouped by when you would use them. This drives the welcome banner; a test checks that every command is listed.
COMMAND_GROUPS = [
    ("Start a review", [
        ("review <PR URL> --preflight", "list release blockers without fetching code or calling a model"),
        ("review <PR URL>", "review a pull request, for example review https://github.com/OWNER/REPO/pull/123"),
        ("review <PR URL> --passes 1", "one pass per reviewer to try it out (more options: review --help)"),
        ("review <PR URL> --dry-run", "show exactly what the reviewer would be given, without calling the model"),
    ]),
    ("After a review", [
        ("show F-001", "read one finding in full"),
        ("mark F-001 real|nit|wrong", "record whether a finding was right (saved with the run, used later for evals)"),
        ('missed "<text>"', "record a real issue the agent did not find"),
        ("drop F-001", "leave a finding out of the PR comment (undrop F-001 puts it back)"),
        ("post", "preview the PR comment; it is posted only after you confirm"),
        ("summary", "show the review summary again (the text post would send)"),
        ("open", "open the full report"),
    ]),
    ("Anytime", [
        ("help", "all commands and review options"),
        ("quit", "leave"),
    ]),
]


def welcome_text(repo: Optional[str]) -> str:
    """The banner shown when the REPL starts. `repo` is "owner/repo (host)" when the current directory is a clone."""
    width = max(len(command) for _, entries in COMMAND_GROUPS for command, _ in entries) + 2
    lines = ["code-reviewer: multi-pass pull request review", ""]
    lines.append("Repository here: %s" % repo if repo else "Not inside a clone: target-directory discovery is disabled; use a PR URL and --preflight.")
    for title, entries in COMMAND_GROUPS:
        lines += ["", "%s:" % title]
        lines += ["  %s%s" % (command.ljust(width), description) for command, description in entries]
    return "\n".join(lines) + "\n"
