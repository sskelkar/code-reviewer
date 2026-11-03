"""Posting formats the shared summary and uses temporary body files; no network."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from code_reviewer import post
from code_reviewer.shared.git_ops import PRRef


class PostingTests(unittest.TestCase):
    def test_summary_marker_and_limits(self):
        result = object()
        with patch.object(post, 'render_summary', return_value='Synthetic grouped review') as render:
            self.assertEqual(post.format_comment(result, ['High'], []), post.MARKER + '\nSynthetic grouped review')
            render.assert_called_once_with(result, ['High'], [])
        with patch.object(post, 'render_summary', return_value='x' * 70000):
            self.assertIn('[comment truncated]', post.format_comment(result, ['High'], []))

    def test_create_and_update_send_exact_body_and_remove_temporary_files(self):
        ref = PRRef('github.com', 'example', 'project', 1)
        for existing in ('', '123\n'):
            with self.subTest(update=bool(existing)):
                paths = []
                commands = []
                def run(command):
                    commands.append(command)
                    if command[-1] == '.login':
                        return SimpleNamespace(stdout='synthetic-user\n')
                    if '--paginate' in command:
                        return SimpleNamespace(stdout=existing)
                    value = command[command.index('-F') + 1][6:] if '-F' in command else command[command.index('--body-file') + 1]
                    path = Path(value)
                    paths.append(path)
                    self.assertEqual(path.read_text(), 'Synthetic review body\n')
                    return SimpleNamespace(stdout='https://example.invalid/comment\n')
                with patch.object(post, '_run', side_effect=run):
                    self.assertEqual(post.post_comment(ref, 'Synthetic review body\n'), 'https://example.invalid/comment')
                self.assertTrue(paths)
                self.assertTrue(all(not path.exists() for path in paths))
                self.assertEqual('PATCH' in commands[-1], bool(existing))
