"""Bounded discovery of explicitly designated repository code-review guidance."""
from __future__ import annotations

from collections import deque
import posixpath
import re
from pathlib import PurePosixPath

from .criteria import (REGULAR_MODES, MAX_CANDIDATES, _directories_of, _inline_rule,
                       extract_candidates, glob_to_regex, rules_from_markdown,
                       split_front_matter, split_sections)

_REVIEW = re.compile(r'\b(?:code[ -]?review(?:s|ing)?|review(?:er)?[ -]+(?:instructions|guidelines|criteria|checklist|policy)|pull[ -]+request[ -]+reviews?|reviewing[ -]+(?:code|changes|pull[ -]+requests))\b', re.I)


def discover(tree, show, changed_paths, pc, max_chars):
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
        parts = tuple(part.lower() for part in PurePosixPath(path).parts[:-1])
        # A package named review is implementation, not an implicit policy source.
        dedicated |= bool(parts and parts[0] in {'review', 'code-review', 'code-reviewing'})
        dedicated |= any(parts[i] in {'docs', 'documentation', 'skills', 'rules'} and
                         parts[i + 1] in {'review', 'code-review', 'code-reviewing'}
                         for i in range(len(parts) - 1))
        if path.lower().endswith('.md') and (dedicated or name == 'skill.md' or
                (parent in ancestors and name in {'agents.md', 'readme.md'})):
            queue.append((path, dedicated, 'explicit review document' if dedicated else 'review sections in project instructions'))
    # Entry documents are inspected first so unrelated skills cannot starve root guidance.
    queue.sort(key=lambda item: (0 if PurePosixPath(item[0]).name.lower() in {'agents.md', 'readme.md'} else 1 if item[1] else 2,
                                 len(PurePosixPath(item[0]).parts), item[0]))
    queue = deque(queue)
    cache = {}
    processed = {}
    rules_by_path = {}
    remaining = max_chars
    while queue:
        path, whole, reason = queue.popleft()
        if path not in regular or (path in processed and (processed[path] or not whole)):
            continue
        if path not in cache:
            if len(cache) >= MAX_CANDIDATES:
                pc.warnings.append('review discovery candidate cap reached; some guidance was not inspected')
                continue
            text = show(path)
            cache[path] = None
            if text is None:
                pc.warnings.append('review guidance could not be read at base commit: %s' % path)
                continue
            if len(text) > remaining:
                pc.warnings.append('review discovery size cap reached; skipped %s' % path)
                continue
            remaining -= len(text)
            cache[path] = text
        text = cache[path]
        if text is None:
            continue
        processed[path] = whole
        globs, body = split_front_matter(text)
        if globs and not any(glob_to_regex(g).match(c) for g in globs for c in changed_paths):
            continue
        # A skill explicitly identifying code review in its metadata is review guidance.
        if PurePosixPath(path).name.lower() == 'skill.md':
            metadata = text[:len(text) - len(body)]
            whole = whole or bool(_REVIEW.search(metadata))
            processed[path] = whole
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
        ids = set()
        for rule in selected:
            original = rule.rule_id
            suffix = 2
            while rule.rule_id in ids:
                rule.rule_id = '%s-%d' % (original, suffix)
                suffix += 1
            ids.add(rule.rule_id)
        rules_by_path[path] = selected
        if path in pc.files_used:
            pc.auto_included = [item for item in pc.auto_included if item['path'] != path]
        else:
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
            elif reference in regular:
                targets.add(reference)
            elif reference.lower().endswith('.md'):
                pc.ignored.append({'reference': reference, 'reason': 'missing or non-regular repository reference'})
                pc.warnings.append('review guidance reference unavailable in %s: %s' % (path, reference))
        for target in sorted(targets, reverse=True):
            if target.lower().endswith('.md'):
                queue.appendleft((target, True, 'referenced by code-review instructions in %s' % path))
        for ref in dict.fromkeys(external + root_external):
            pc.ignored.append({'reference': ref, 'reason': 'external reference; not loaded'})
    pc.manifest = {'criteria_files': [{'path': p, 'reason': 'explicit review guidance'} for p in pc.files_used]}
    pc.warnings = list(dict.fromkeys(pc.warnings))
    return [rule for rules in rules_by_path.values() for rule in rules]
