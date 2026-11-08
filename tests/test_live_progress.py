"""Synthetic advisory progress: bounds, terminal safety, heartbeats and flow previews."""
from __future__ import annotations

import copy
import io
import json
import struct
import os
import unittest
from unittest.mock import Mock, patch

from code_reviewer.progress import LiveProgress, ProgressPrinter
from code_reviewer.sandbox import progress
from code_reviewer.shared.events import Event, round_preview, reviewer_preview
from code_reviewer.review.master import MasterList


def frame(event='stage', **data):
    return dict(kind='progress', event=event, data=data or dict(stage='criteria'))


def preview():
    return frame('round_preview', round=1, completed=4, failed=0, total=1, new=1,
                 findings=[dict(id='F-001', severity='medium', title='Synthetic finding', file='app.py', line=2)])


class ProgressTests(unittest.TestCase):
    def test_host_checkout_path_is_always_visible_with_its_retention_policy(self):
        for kept in (False, True):
            with self.subTest(kept=kept):
                output = io.StringIO()
                ProgressPrinter(output)(Event('checkout_ready', dict(path='/tmp/synthetic-review/checkout',
                    files=3, skipped_links=0, kept=kept)))
                text = output.getvalue()
                self.assertIn('/tmp/synthetic-review/checkout', text)
                self.assertIn('kept after review' if kept else 'removed after review', text)
                self.assertIn('model tools are read-only', text)

    def test_host_checkout_location_cannot_inject_terminal_lines_or_controls(self):
        output = io.StringIO()
        ProgressPrinter(output)(Event('checkout_ready', dict(path='/tmp/test\n\x1b[31m/checkout',
            files=1, skipped_links=0, kept=False)))
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn('\x1b', output.getvalue())

    def test_vm_checkout_stage_has_only_a_fixed_guest_path(self):
        with patch.object(progress, 'send_json') as send:
            progress.GuestProgress(None)(Event('checkout_ready', dict(path='/private/HOST_PATH_CANARY')))
        message = send.call_args.args[1]
        progress.validate(message)
        self.assertEqual(message, frame(stage='checkout'))
        self.assertNotIn('HOST_PATH_CANARY', json.dumps(message))
        output = io.StringIO()
        LiveProgress(output)(message)
        self.assertIn('/work/snapshot', output.getvalue())
        self.assertIn('inside the disposable VM', output.getvalue())

    def test_closed_schema_refuses_early_previews_injections_and_terminal_controls(self):
        progress.validate(preview(), allow_preview=True)
        bad = [frame(stage='untrusted phase'), frame(stage='criteria', prompt='private'),
               frame('review_started', persona='unknown', round=1),
               frame('review_started', persona='correctness', round=True)]
        for key, value in (('title', '\x1b]52;c;private\x07'), ('file', '\u202eevil'),
                           ('line', True), ('severity', 'critical')):
            value_ = preview()
            value_['data']['findings'][0][key] = value
            bad.append(value_)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                progress.validate(value, allow_preview=True)
        with self.assertRaises(ValueError):
            progress.validate(preview())
        with self.assertRaises(ValueError):
            progress.validate(preview(), allow_preview=True, secret='Synthetic finding')
        quoted = preview()
        quoted['data']['findings'][0]['title'] = 'Do not expose quoted"key'
        with self.assertRaises(ValueError):
            progress.validate(quoted, allow_preview=True, secret='quoted"key')

    def test_stream_counts_across_protocol_phases_without_resetting_limits(self):
        received = []
        stream = progress.Stream(received.append)
        reader = Mock(side_effect=[frame(stage='acquiring'), {'kind': 'ready'}, preview(), {'kind': 'result'}])
        self.assertEqual(stream.receive(None, 65536, reader=reader), {'kind': 'ready'})
        stream.allow_preview = True
        self.assertEqual(stream.receive(None, 65536, reader=reader), {'kind': 'result'})
        self.assertEqual(len(received), 2)
        with patch.object(progress, 'MAX_EVENTS', 2), self.assertRaises(ValueError):
            stream.accept(frame())
        with patch.object(progress, 'MAX_BYTES', 1), self.assertRaises(ValueError):
            progress.Stream().accept(frame())
        too_many = preview()
        too_many['data']['findings'] *= 11
        with self.assertRaises(ValueError):
            progress.validate(too_many, allow_preview=True)

    def test_guest_redacts_credentials_and_stops_advisory_output_at_its_cap(self):
        sink = progress.GuestProgress(None)
        sink.secret = 'synthetic-key'
        value = preview()['data']
        value['findings'][0]['title'] = 'Do not expose synthetic-key'
        with patch.object(progress, 'send_json') as send:
            sink(Event('round_preview', value))
            self.assertNotIn('synthetic-key', json.dumps(send.call_args.args[1]))
            sink.count = progress.MAX_EVENTS - 8
            sink.stage('criteria')
            self.assertEqual(send.call_count, 1)
        # Worst-case escaped Unicode still fits the per-frame limit.
        sink = progress.GuestProgress(None)
        value = preview()['data']
        value.update(new=10, total=10)
        value['findings'] = [dict(value['findings'][0], title='字'*240, file='字'*240) for _ in range(10)]
        with patch.object(progress, 'send_json') as send:
            sink(Event('round_preview', value))
            self.assertLess(len(json.dumps(send.call_args.args[1], ensure_ascii=True).encode()), progress.MAX_FRAME)

    def test_wire_limits_count_actual_bytes_including_json_whitespace(self):
        body = json.dumps(frame()).encode() + b' ' * progress.MAX_FRAME
        raw = io.BytesIO(struct.pack('!Q', len(body)) + body)
        sock = Mock(recv=raw.read)
        callback = Mock()
        with self.assertRaisesRegex(ValueError, 'frame limit'):
            progress.Stream(callback).receive(sock, 2 * progress.MAX_FRAME)
        callback.assert_not_called()
        body = json.dumps(frame()).encode() + b' ' * 100
        end = b'{"kind":"result"}'
        raw = io.BytesIO(struct.pack('!Q', len(body)) + body + struct.pack('!Q', len(end)) + end)
        stream = progress.Stream()
        self.assertEqual(stream.receive(Mock(recv=raw.read), progress.MAX_FRAME), {'kind':'result'})
        self.assertEqual(stream.bytes, len(body))

    def test_heartbeat_is_inactivity_based_and_thread_stops_on_failure(self):
        out = io.StringIO()
        live = LiveProgress(out, interval=15)
        with patch('code_reviewer.progress.time.monotonic', return_value=100):
            live.stage('images')
        with patch('code_reviewer.progress.time.monotonic', return_value=114):
            live.tick()
        self.assertNotIn('still waiting', out.getvalue())
        with patch('code_reviewer.progress.time.monotonic', return_value=115):
            live.tick()
        self.assertIn('still waiting — preparing pinned reviewer images', out.getvalue())
        with self.assertRaises(RuntimeError):
            with live:
                raise RuntimeError('synthetic failure')
        self.assertFalse(live.thread.is_alive())
        self.assertNotIn('\x1b', out.getvalue())

    def test_animation_is_tty_only_and_cleared_before_logs_and_on_interruption(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        out = TTY()
        with patch.dict(os.environ, {'TERM': 'xterm'}):
            live = LiveProgress(out)
        with self.assertRaises(KeyboardInterrupt):
            with live:
                live.stage('images')
                live.tick()
                self.assertTrue(live.visible)
                first = live.frame
                live.tick()
                self.assertGreater(live.frame, first)
                live.note('images offline reused, connected built')
                self.assertFalse(live.visible)
                self.assertIn('\r\x1b[2K', out.getvalue())
                live.tick()
                raise KeyboardInterrupt
        self.assertFalse(live.visible)
        self.assertFalse(live.thread.is_alive())
        self.assertTrue(out.getvalue().endswith('\r\x1b[2K'))
        with patch.dict(os.environ, {'TERM': 'dumb'}):
            self.assertFalse(LiveProgress(TTY()).animated)
        self.assertFalse(LiveProgress(io.StringIO()).animated)

    def test_reviewer_glimpse_is_bounded_sanitized_and_cannot_arrive_before_authentication(self):
        row = dict(title='PRIVATE_TITLE_CANARY\n\x1b[31m', severity='medium', file='app.py', line=2,
                   description='PRIVATE_CANARY', quote='PRIVATE_CANARY')
        raw = {'findings': [dict(row) for _ in range(5)] + [dict(row, self_check={'verdict':'rejected'}),
                                                         dict(row, line=True), None]}
        data = reviewer_preview(raw)
        self.assertEqual((data['raised'], data['self_rejected'], len(data['findings'])), (8, 1, 0))
        self.assertNotIn('PRIVATE_CANARY', json.dumps(data))
        self.assertNotIn('PRIVATE_TITLE_CANARY', json.dumps(data))
        event = Event('persona_review_finished', dict(persona='correctness', round_no=1, ok=True, **data))
        with patch.object(progress, 'send_json') as send:
            sink = progress.GuestProgress(None)
            sink(event)
        result = send.call_args.args[1]
        progress.validate(result, allow_preview=True)
        with self.assertRaises(ValueError):
            progress.validate(result)
        out = io.StringIO()
        live = LiveProgress(out)
        live(result)
        self.assertIn('8 proposed', out.getvalue())
        self.assertIn('Verification pending', out.getvalue())
        self.assertNotIn('\x1b', out.getvalue())
        self.assertEqual(raw['findings'][0]['title'], row['title'])
        from code_reviewer.tracing import Recorder, activate, observation, event as trace_event
        recorder = Recorder()
        with activate(recorder), observation('review.run'):
            trace_event(event.type, event.data)
        self.assertNotIn('PRIVATE_TITLE_CANARY', json.dumps(recorder.snapshot()))

    def test_preview_only_contains_new_actionable_sightings_and_is_provisional(self):
        master = MasterList()
        def item(ident, passes):
            return dict(id=ident, seen_in_passes=passes,
                        canonical=dict(title='Issue\n\x1b[31m', severity='low', file='app.py', line=2,
                                       description='PRIVATE_SOURCE_CANARY', quote='PRIVATE_SOURCE_CANARY'))
        master.items = {'F-001': item('F-001', [1]), 'F-002': item('F-002', []), 'F-003': item('F-003', [2])}
        before = copy.deepcopy(master.items)
        data = round_preview(2, master, {'F-001'}, 1, 0)
        self.assertEqual(data['findings'], [])
        self.assertEqual(data['total'], 2)
        self.assertNotIn('PRIVATE_SOURCE_CANARY', json.dumps(data))
        self.assertEqual(master.items, before)
        out = io.StringIO()
        ProgressPrinter(out)(Event('round_preview', data))
        self.assertIn('UNCONFIRMED', out.getvalue())
        self.assertNotIn('\x1b', out.getvalue())

    def test_complete_review_emit_previews_after_consolidation_without_repeating_findings(self):
        from code_reviewer.engine import Engine
        from code_reviewer.sandbox import openai_smoke, smoke
        events = []
        def engine(cfg, runner):
            return Engine(cfg, runner, emit=events.append)
        with patch.object(smoke, 'Engine', side_effect=engine):
            openai_smoke.run_offline()
        previews = [e.data for e in events if e.type == 'round_preview']
        self.assertEqual([(e['round'], e['new']) for e in previews],
                         [(1, 1)])
        self.assertEqual(previews[-1]['completed'], 4)
        self.assertEqual(previews[-1]['total'], 1)
        self.assertEqual(sum(e.type == 'persona_review_finished' for e in events), 4)
        # Transport exactly the same events through the guest's projection.
        with patch.object(progress, 'send_json') as send:
            sink = progress.GuestProgress(None)
            for event in events:
                sink(event)
        frames = [call.args[1] for call in send.call_args_list]
        self.assertEqual(sum(f['event'] == 'round_preview' for f in frames), 1)
        self.assertTrue(any(f['event'] == 'review_finished' for f in frames))


if __name__ == '__main__':
    unittest.main()
