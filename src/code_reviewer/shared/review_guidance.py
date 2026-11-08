"""Bounded discovery of explicitly designated repository code-review guidance."""
from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

from .criteria import (REGULAR_MODES, MAX_CANDIDATES, _directories_of, _inline_rule,
                       extract_candidates, glob_to_regex, rules_from_markdown,
                       split_front_matter, split_sections)

_REVIEW = re.compile(r'\b(?:code[ -]?review(?:s|ing)?|review(?:er)?[ -]+(?:instructions|guidelines|criteria|checklist|policy)|pull[ -]+request[ -]+reviews?|reviewing[ -]+(?:code|changes|pull[ -]+requests))\b', re.I)


def discover(tree, show, agents_text, changed_paths, pc, max_chars):
    regular = {p for p, mode in tree.items() if mode in REGULAR_MODES}
    ancestors = set(_directories_of(changed_paths)) | {''}
    queue = []
    # Inspect entry documents and clearly named review documents/skills only.
    for path in sorted(regular):
        name = PurePosixPath(path).name.lower()
        parent = str(PurePosixPath(path).parent)
        parent = '' if parent == '.' else parent
        stem = PurePosixPath(path).stem.lower().replace('_', '-').replace(' ', '-')
        dedicated = stem in {'review', 'reviews', 'reviewing', 'code-review', 'code-reviewing', 'review-guidelines', 'review-checklist', 'code-review-guidelines', 'code-review-checklist'}
        dedicated |= any(part.lower() in {'review', 'code-review', 'code-reviewing'} for part in PurePosixPath(path).parts[:-1])
        if path.lower().endswith('.md') and (dedicated or name == 'skill.md' or
                (parent in ancestors and name in {'agents.md', 'readme.md'})):
            queue.append((path, dedicated, 'explicit review document' if dedicated else 'review sections in project instructions'))
    seen = set()
    rules = []
    remaining = max_chars
    while queue and len(seen) < MAX_CANDIDATES:
        path, whole, reason = queue.pop(0)
        if path in seen or path not in regular:
            continue
        seen.add(path)
        text = agents_text if path == 'AGENTS.md' and agents_text is not None else show(path)
        if text is None:
            pc.warnings.append('review guidance could not be read at base commit: %s' % path)
            continue
        if len(text) > remaining:
            pc.warnings.append('review discovery size cap reached; skipped %s' % path)
            continue
        remaining -= len(text)
        globs, body = split_front_matter(text)
        if globs and not any(glob_to_regex(g).match(c) for g in globs for c in changed_paths):
            continue
        # A skill explicitly identifying code review in its metadata is review guidance.
        if PurePosixPath(path).name.lower() == 'skill.md':
            metadata = text[:len(text) - len(body)]
            whole = whole or bool(_REVIEW.search(metadata))
        _, sections = split_sections(body)
        selected = []
        if whole:
            selected = rules_from_markdown(path, body)
        else:
            covered_until = -1
            for i, section in enumerate(sections):
                if i <= covered_until or not _REVIEW.search(section.heading):
                    continue
                rule = _inline_rule(path, sections, i)
                if rule:
                    selected.append(rule)
                covered_until = i
                for j in range(i + 1, len(sections)):
                    if sections[j].level <= section.level:
                        break
                    covered_until = j
        if not selected:
            continue
        rules.extend(selected)
        pc.files_used.append(path)
        pc.auto_included.append({'path': path, 'reason': reason})
        # Only references inside accepted review guidance can designate more files.
        guidance = '\n'.join(r.text for r in selected)
        # Resolve relative references in nested documents without leaving the repo.
        relative_tree = {}
        directory = posixpath.dirname(path)
        for target in regular:
            relative = posixpath.relpath(target, directory or '.')
            if not relative.startswith('../'):
                relative_tree[relative] = tree[target]
        references, external = extract_candidates(guidance, relative_tree)
        root_refs, root_external = extract_candidates(guidance, tree)
        targets = {posixpath.normpath(posixpath.join(directory, ref)) for ref in references}
        targets.update(ref for ref in root_refs if ref not in references)
        for reference in re.findall(r'\]\(([^)\s]+)\)', guidance):
            reference = reference.split('#', 1)[0]
            if not reference or reference.startswith(('/', '~')) or '://' in reference:
                continue
            target = posixpath.normpath(posixpath.join(directory, reference))
            if target in regular and not target.startswith('../'):
                targets.add(target)
            elif target not in regular and reference.lower().endswith('.md'):
                pc.ignored.append({'reference': reference, 'reason': 'missing or non-regular repository reference'})
                pc.warnings.append('review guidance reference unavailable in %s: %s' % (path, reference))
        for target in sorted(targets):
            if target.lower().endswith('.md'):
                queue.append((target, True, 'referenced by code-review instructions in %s' % path))
        for ref in dict.fromkeys(external + root_external):
            pc.ignored.append({'reference': ref, 'reason': 'external reference; not loaded'})
    if queue:
        pc.warnings.append('review discovery candidate cap reached; some guidance was not inspected')
    pc.manifest = {'criteria_files': [{'path': p, 'reason': 'explicit review guidance'} for p in pc.files_used]}
    return rules
