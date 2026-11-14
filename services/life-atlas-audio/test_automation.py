import contextlib
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import automation
import trial


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'incoming').mkdir(mode=0o700)
        self.patch = patch.object(automation, 'ROOT', self.root); self.patch.start(); self.addCleanup(self.patch.stop)
        self.space = patch.object(automation.shutil, 'disk_usage', return_value=SimpleNamespace(free=20_000_000_000))
        self.space.start(); self.addCleanup(self.space.stop)
        self.cfg = {'schema': 1, 'enabled': True, 'timezone': 'Europe/London',
                    'timezone_basis': 'phone observed at setup; not historical travel proof',
                    'not_before': '2099-01-01T01:00:00+00:00',
                    'excluded_names': ['log001.wav', 'log002.wav', 'log003.wav']}

    def test_not_before_gate_never_processes_real_test_early(self):
        (self.root/'incoming/log004.wav').write_bytes(b'synthetic')
        with patch.object(automation, 'config', return_value=self.cfg), patch.object(automation, 'process_source') as process:
            self.assertEqual(automation.scan()['processed'], 0)
            process.assert_not_called()

    def test_selected_only_and_prior_backfill_excluded(self):
        for name in ['log004.wav', 'LOGhello.M4A', 'log-20261114.opus']:
            self.assertTrue(automation.allowed_name(name, self.cfg), name)
        for name in ['log001.wav', 'LOG002.WAV', 'log003.wav', 'recording.wav', '.syncthing.log004.wav.tmp', '../log004.wav', 'log004.txt', 'sub/log004.wav']:
            self.assertFalse(automation.allowed_name(name, self.cfg), name)

    def test_bad_source_does_not_block_independent_valid_source(self):
        for name in ('log-a-broken.wav', 'log-b-good.wav'):
            (self.root/'incoming'/name).write_bytes(b'synthetic')
        cfg = dict(self.cfg, not_before='2000-01-01T00:00:00+00:00')
        def process(path, config):
            if 'broken' in path.name: raise ValueError('synthetic invalid audio')
            return 'a'*64
        with patch.object(automation, 'config', return_value=cfg), patch.object(automation, 'process_source', side_effect=process) as worker, patch.object(automation.trial, 'STATE', self.root):
            result = automation.scan()
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['observed_selected'], 1)
        self.assertEqual(result['failed_sources'], 1)

    def test_uncertain_launch_blocks_all_further_inference(self):
        for name in ('log-a.wav', 'log-b.wav'):
            (self.root/'incoming'/name).write_bytes(b'synthetic')
        intent = self.root/'transcription-control'; intent.mkdir()
        (intent/'launch-intent.json').write_text('{}')
        cfg = dict(self.cfg, not_before='2000-01-01T00:00:00+00:00')
        with patch.object(automation, 'config', return_value=cfg), patch.object(automation, 'process_source', side_effect=RuntimeError('uncertain')) as worker, patch.object(automation.trial, 'STATE', self.root):
            result = automation.scan()
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(result['status'], 'blocked-inference')

    def test_job_id_is_not_a_path_or_shell_fragment(self):
        for identity in ['../escape', 'x;id', 'a'*63, 'A'*64]:
            with self.assertRaises(RuntimeError): automation.job_path(identity)

    def test_accept_requires_bound_receipt_model_and_packet(self):
        job = automation.job_path('a'*64); job.mkdir(mode=0o700)
        raw = {'transcription': [{'text': ' synthetic', 'offsets': {'from': 0, 'to': 1000}}]}
        (job/'raw.json').write_text(json.dumps(raw)); (job/'raw.json').chmod(0o600)
        raw_hash = trial.sha256(job/'raw.json')
        proposal = json.dumps({'source_sha256': raw_hash, 'edits': []})
        packet_hash = hashlib.sha256(json.dumps(automation.review.packet(job/'raw.json'), ensure_ascii=False).encode()).hexdigest()
        receipt = {'provider': automation.MODEL_PROVIDER, 'model': automation.MODEL_ID,
                   'status': 'proposed-awaiting-server-validation', 'audio_shared': False,
                   'source_sha256': raw_hash, 'packet_sha256': packet_hash,
                   'proposal_sha256': hashlib.sha256(proposal.encode()).hexdigest()}
        with patch.object(automation, 'validate_job'):
            wrong = dict(receipt, model='other')
            with self.assertRaises(RuntimeError): automation.accept('a'*64, {'proposal': proposal, 'receipt': wrong})
            wrong = dict(receipt, packet_sha256='f'*64)
            with self.assertRaises(RuntimeError): automation.accept('a'*64, {'proposal': proposal, 'receipt': wrong})
            result = automation.accept('a'*64, {'proposal': proposal, 'receipt': receipt})
            self.assertEqual(result['status'], 'reviewed')
            self.assertTrue((job/'reading-draft.md').is_file())
            self.assertEqual(automation.accept('a'*64, {'proposal': proposal, 'receipt': receipt})['status'], 'already-reviewed')

    def test_no_lexical_rewrite_is_accepted_as_format(self):
        job = automation.job_path('a'*64); job.mkdir(mode=0o700)
        (job/'raw.json').write_text(json.dumps({'transcription': [{'text': ' I did not go.', 'offsets': {'from': 0, 'to': 1000}}]}))
        raw_hash = trial.sha256(job/'raw.json')
        proposal = json.dumps({'source_sha256': raw_hash, 'edits': [
            {'segment': 0, 'start': 7, 'end': 10, 'kind': 'format', 'replacement': '', 'reason': 'unsafe'}]})
        receipt = {'provider': automation.MODEL_PROVIDER, 'model': automation.MODEL_ID,
                   'status': 'proposed-awaiting-server-validation', 'audio_shared': False,
                   'source_sha256': raw_hash, 'packet_sha256': hashlib.sha256(json.dumps(automation.review.packet(job/'raw.json'), ensure_ascii=False).encode()).hexdigest(),
                   'proposal_sha256': hashlib.sha256(proposal.encode()).hexdigest()}
        with patch.object(automation, 'validate_job'):
            result = automation.accept('a'*64, {'proposal': proposal, 'receipt': receipt})
            self.assertEqual(result['status'], 'rejected-proposal')
        self.assertFalse((job/'review-receipt.json').exists())

    def test_source_symlink_and_missing_file_rejected(self):
        path = self.root/'incoming/log004.wav'
        with self.assertRaises(RuntimeError): automation.inspect_source(path, self.cfg)
        elsewhere = self.root/'elsewhere'; elsewhere.write_bytes(b'synthetic')
        path.symlink_to(elsewhere)
        with self.assertRaises(RuntimeError): automation.inspect_source(path, self.cfg)

    def test_complete_source_is_not_retranscribed(self):
        source = self.root/'incoming/log004.wav'; source.write_bytes(b'synthetic')
        identity = trial.sha256(source)
        job = automation.job_path(identity); job.mkdir(mode=0o700)
        automation.save(job/'source.json', {'source_sha256': identity, 'source_size': source.stat().st_size})
        (job/'asr-receipt.json').write_text('{}'); (job/'audio-receipt.json').write_text('{}')
        with patch.object(automation, 'inspect_source', return_value=(identity, source.stat())), patch.object(automation, 'validate_job'), patch.object(automation.batch, 'transcribe') as transcribe:
            self.assertEqual(automation.process_source(source, self.cfg), identity)
            transcribe.assert_not_called()

    def prepared_job(self):
        source_bytes = b'synthetic original'
        identity = hashlib.sha256(source_bytes).hexdigest()
        job = automation.job_path(identity); job.mkdir(mode=0o700)
        raw = {'transcription': [{'text': ' synthetic recording.', 'offsets': {'from': 0, 'to': 1000}}]}
        def put(path, data):
            path.write_bytes(data); path.chmod(0o600)
        put(job/'raw.json', json.dumps(raw).encode()); put(job/'raw.txt', b'synthetic recording.')
        put(job/'audio.opus', b'synthetic Opus')
        stage = self.root/'staging'/('auto-'+identity); stage.mkdir(mode=0o700, parents=True)
        pcm = stage/'input.wav'; put(pcm, b'synthetic PCM')
        put(stage/'log-test.wav', source_bytes)
        put(self.root/'incoming/log-test.wav', source_bytes)
        automation.save(job/'source.json', {'source_name': 'log-test.wav', 'source_sha256': identity,
            'source_size': len(source_bytes), 'recorded_at': '2000-01-01T12:00:00+00:00',
            'day': '20000101', 'date_basis': 'phone-recording-metadata'})
        automation.save(job/'asr-receipt.json', {'source_sha256': identity, 'json_sha256': trial.sha256(job/'raw.json'),
            'transcript_sha256': trial.sha256(job/'raw.txt'), 'pcm_path': str(pcm), 'pcm_sha256': trial.sha256(pcm)})
        automation.save(job/'audio-receipt.json', {'sha256': trial.sha256(job/'audio.opus')})
        packet = automation.packet(identity)
        proposal = json.dumps({'source_sha256': packet['source_sha256'], 'edits': []})
        receipt = {'provider': automation.MODEL_PROVIDER, 'model': automation.MODEL_ID,
            'status': 'proposed-awaiting-server-validation', 'audio_shared': False,
            'source_sha256': packet['source_sha256'],
            'packet_sha256': hashlib.sha256(json.dumps(packet, ensure_ascii=False).encode()).hexdigest(),
            'proposal_sha256': hashlib.sha256(proposal.encode()).hexdigest()}
        automation.accept(identity, {'proposal': proposal, 'receipt': receipt})
        return identity, stage, pcm

    def test_backup_failure_keeps_stage_and_pcm(self):
        identity, stage, pcm = self.prepared_job()
        with patch.object(automation.daily, 'verify_opus'), patch.object(automation, 'restic', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): automation.finish(identity)
        self.assertTrue((stage/'log-test.wav').is_file()); self.assertTrue(pcm.is_file())
        self.assertFalse((automation.job_path(identity)/'completion.json').exists())

    def test_low_disk_prevents_backup_and_cleanup(self):
        identity, stage, pcm = self.prepared_job()
        with patch.object(automation.daily, 'verify_opus'), patch.object(automation.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)), patch.object(automation, 'restic') as backup:
            with self.assertRaises(RuntimeError): automation.finish(identity)
            backup.assert_not_called()
        self.assertTrue((stage/'log-test.wav').exists()); self.assertTrue(pcm.exists())

    def test_bad_restored_bytes_prevent_cleanup(self):
        identity, stage, pcm = self.prepared_job()
        def restic(*args):
            if args[0] == 'backup': return b'{"message_type":"summary","snapshot_id":"synthetic"}\n'
            return b'wrong restored bytes'
        with patch.object(automation.daily, 'verify_opus'), patch.object(automation, 'restic', side_effect=restic):
            with self.assertRaises(RuntimeError): automation.finish(identity)
        self.assertTrue((stage/'log-test.wav').exists()); self.assertTrue(pcm.exists())

    def test_changed_pcm_prevents_any_cleanup(self):
        identity, stage, pcm = self.prepared_job()
        pcm.write_bytes(b'changed PCM')
        def restic(*args):
            if args[0] == 'backup': return b'{"message_type":"summary","snapshot_id":"synthetic"}\n'
            if args[0] == 'dump': return Path(args[2]).read_bytes()
            return b''
        with patch.object(automation.daily, 'verify_opus'), patch.object(automation, 'restic', side_effect=restic):
            with self.assertRaises(RuntimeError): automation.finish(identity)
        self.assertTrue((stage/'log-test.wav').exists()); self.assertTrue(pcm.exists())

    def test_group_traversable_parent_and_exact_recovery_before_cleanup(self):
        identity, stage, pcm = self.prepared_job()
        self.root.chmod(0o710)
        def restic(*args):
            if args[0] == 'backup': return b'{"message_type":"summary","snapshot_id":"synthetic"}\n'
            if args[0] == 'dump': return Path(args[2]).read_bytes()
            return b''
        with patch.object(automation.daily, 'verify_opus'), patch.object(automation, 'restic', side_effect=restic):
            completion = automation.finish(identity)
            self.assertEqual(automation.finish(identity), completion)
        self.assertTrue(completion['backup_restore_verified'])
        self.assertFalse((stage/'log-test.wav').exists()); self.assertFalse(pcm.exists())
        self.assertTrue((self.root/'incoming/log-test.wav').exists())
        self.assertTrue((self.root/'daily/20000101/transcript.md').exists())

    def test_pending_requires_validated_artifacts(self):
        job = automation.job_path('a'*64); job.mkdir(mode=0o700)
        (job/'asr-receipt.json').write_text('{}'); (job/'audio-receipt.json').write_text('{}')
        with patch.object(automation, 'validate_job') as validate:
            self.assertEqual(automation.pending(), [{'id': 'a'*64, 'has_review': False}])
            validate.assert_called_once()
        (job/'completion.json').write_text('{}')
        with patch.object(automation, 'validate_job'):
            self.assertEqual(automation.pending(), [])


if __name__ == '__main__': unittest.main()
