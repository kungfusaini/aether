import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import daily


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root/'inputs'; self.inputs.mkdir(mode=0o700)
        self.output = self.root/'days'; self.output.mkdir(mode=0o700)
        self.media = patch.object(daily, 'verify_opus')
        self.media.start(); self.addCleanup(self.media.stop)
        self.addCleanup(self.temp.cleanup)
        self.manifest = {'schema': 1, 'day': '20261111', 'logs': [self.log('a', '12:30:00')]}

    def put(self, name, data):
        path = self.inputs/name
        path.write_bytes(data); path.chmod(0o600)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    def log(self, letter, time, text=' I forgot what it was called.'):
        raw = self.put(letter+'.json', json.dumps({'transcription': [
            {'text': text, 'offsets': {'from': 43210, 'to': 45000}}]}).encode())
        proposal = self.put(letter+'-proposal.json', json.dumps({
            'source_sha256': raw['sha256'], 'edits': []}).encode())
        audio = self.put(letter+'.opus', b'synthetic audio '+letter.encode())
        return {'source_sha256': hashlib.sha256(letter.encode()).hexdigest(),
                'recorded_at': '2026-11-11T'+time+'+00:00', 'date_basis': 'phone-recording-metadata',
                'raw': raw, 'proposal': proposal, 'opus': audio}

    def run_bundle(self, manifest=None):
        return daily.bundle(manifest or self.manifest, self.inputs, self.output)

    def test_combines_ordered_logs_and_exact_audio(self):
        second = self.log('b', '10:00:00', ' Earlier recording.')
        self.manifest['logs'].append(second)
        receipt = self.run_bundle()
        day = self.output/'20261111'
        text = (day/'transcript.md').read_text()
        self.assertLess(text.index('Earlier recording.'), text.index('I forgot'))
        with zipfile.ZipFile(day/'audio.zip') as archive:
            self.assertEqual(len(archive.namelist()), 2)
            self.assertEqual(archive.read(archive.namelist()[0]), b'synthetic audio b')
        self.assertEqual(receipt['logs'], 2)
        self.assertFalse(receipt['backup_verified'])

    def test_bracketed_guess_and_literal_markdown(self):
        self.manifest['logs'] = [self.log('c', '11:00:00', ' ``` <img src="https://invalid.example/x">')]
        log = self.manifest['logs'][0]
        proposal = {'source_sha256': log['raw']['sha256'], 'edits': [
            {'segment': 0, 'start': 1, 'end': 4, 'kind': 'guess', 'replacement': 'something', 'reason': 'Unverified synthetic guess'}]}
        log['proposal'] = self.put('c-proposal.json', json.dumps(proposal).encode())
        self.run_bundle()
        text = (self.output/'20261111/transcript.md').read_text()
        self.assertIn('[something? @00:43]', text)
        self.assertIn('```text\n', text)

    def test_repeated_delivery_reuses_revision(self):
        first = self.run_bundle(); target = os.readlink(self.output/'20261111')
        second = self.run_bundle()
        self.assertEqual(first, second)
        self.assertEqual(target, os.readlink(self.output/'20261111'))
        self.assertEqual(len(list((self.output/'.revisions/20261111').iterdir())), 1)

    def test_late_arrival_retains_previous_revision(self):
        self.run_bundle(); prior = (self.output/'20261111').resolve()
        self.manifest['logs'].append(self.log('b', '10:00:00'))
        self.run_bundle()
        self.assertNotEqual(prior, (self.output/'20261111').resolve())
        self.assertTrue((prior/'audio.zip').is_file())

    def test_reader_pins_revision_across_publication(self):
        self.run_bundle()
        pinned = daily.resolve_day(self.output, '20261111')
        before = (pinned/'transcript.md').read_bytes()
        self.manifest['logs'].append(self.log('b', '10:00:00'))
        self.run_bundle()
        self.assertNotEqual(pinned, daily.resolve_day(self.output, '20261111'))
        self.assertEqual(before, (pinned/'transcript.md').read_bytes())
        with zipfile.ZipFile(pinned/'audio.zip') as archive:
            self.assertEqual(len(archive.namelist()), 1)

    def test_temporary_audio_mutation_is_bound_to_manifest(self):
        original_write = zipfile.ZipFile.write
        path = self.inputs/'a.opus'
        original = path.read_bytes()
        def swap_write(archive, filename, arcname):
            path.write_bytes(b'unapproved temporary replacement')
            try:
                return original_write(archive, filename, arcname)
            finally:
                path.write_bytes(original)
        with patch.object(daily.zipfile.ZipFile, 'write', swap_write):
            with self.assertRaises(ValueError): self.run_bundle()
        self.assertFalse((self.output/'20261111').exists())
        self.assertEqual(path.read_bytes(), original)

    def test_dropping_a_log_rejected(self):
        self.manifest['logs'].append(self.log('b', '10:00:00'))
        self.run_bundle()
        self.manifest['logs'].pop()
        with self.assertRaises(ValueError): self.run_bundle()

    def test_unknown_date_not_assumed_previous_day(self):
        self.manifest['logs'][0]['date_basis'] = 'upload-time-minus-one-day'
        with self.assertRaises(ValueError): self.run_bundle()
        self.assertFalse((self.output/'20261111').exists())

    def test_invalid_date_timezone_or_day_rejected(self):
        for value in ('2026-11-11T12:00:00', '2026-11-12T00:00:00+00:00', 'not-a-date'):
            with self.subTest(value=value):
                candidate = copy.deepcopy(self.manifest)
                candidate['logs'][0]['recorded_at'] = value
                with self.assertRaises(ValueError): self.run_bundle(candidate)

    def test_wrong_hash_rejected_before_publication(self):
        self.manifest['logs'][0]['opus']['sha256'] = '0'*64
        with self.assertRaises(ValueError): self.run_bundle()
        self.assertFalse((self.output/'20261111').exists())

    def test_lexical_rewrite_without_brackets_rejected(self):
        log = self.manifest['logs'][0]
        log['proposal'] = self.put('bad.json', json.dumps({'source_sha256': log['raw']['sha256'], 'edits': [
            {'segment': 0, 'start': 1, 'end': 2, 'kind': 'format', 'replacement': 'He', 'reason': 'No'}]}).encode())
        with self.assertRaises(ValueError): self.run_bundle()

    def test_traversal_and_symlink_rejected(self):
        log = self.manifest['logs'][0]
        for relative in ('../outside.opus', '/tmp/outside.opus'):
            log['opus']['path'] = relative
            with self.assertRaises(ValueError): self.run_bundle()
        log['opus']['path'] = 'linked.opus'
        (self.inputs/'linked.opus').symlink_to(self.inputs/'a.opus')
        with self.assertRaises(ValueError): self.run_bundle()

    def test_existing_directory_not_overwritten(self):
        (self.output/'20261111').mkdir()
        with self.assertRaises(ValueError): self.run_bundle()

    def test_failed_zip_preserves_previous_pair(self):
        self.run_bundle(); previous = (self.output/'20261111').resolve()
        self.manifest['logs'].append(self.log('b', '15:00:00'))
        with patch.object(daily.zipfile.ZipFile, 'write', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.run_bundle()
        self.assertEqual(previous, (self.output/'20261111').resolve())
        self.assertTrue((previous/'transcript.md').is_file())

    def test_tampered_revision_rejected(self):
        self.run_bundle()
        (self.output/'20261111/transcript.md').write_text('tampered')
        with self.assertRaises(ValueError): self.run_bundle()

    def test_duplicate_sources_rejected(self):
        self.manifest['logs'] *= 2
        with self.assertRaises(ValueError): self.run_bundle()

    def test_public_input_rejected(self):
        (self.inputs/'a.json').chmod(0o644)
        with self.assertRaises(ValueError): self.run_bundle()

    def test_git_output_rejected(self):
        (self.output/'.git').mkdir()
        with self.assertRaises(ValueError): self.run_bundle()


if __name__ == '__main__':
    unittest.main()
