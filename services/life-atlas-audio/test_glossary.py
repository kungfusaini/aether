import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import automation
import batch
import glossary
import trial


class GlossaryTests(unittest.TestCase):
    def test_distinct_candidates_and_bounded_names(self):
        self.assertEqual(glossary.validate(['Mira', 'Mina', "O'Neil"]), ['Mira', 'Mina', "O'Neil"])
        for names in (['Mira', 'mira'], ['bad\nname'], ['$(echo secret)'], [''], ['a'*49], ['a'*48, 'b'*48, 'c'*48, 'd'*48, 'e'*48, 'f'*48]):
            with self.subTest(names=names):
                with self.assertRaises(ValueError): glossary.validate(names)

    def test_private_file_and_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root/'names.json'
            self.assertEqual(glossary.read(path, missing_ok=True), {'schema': 1, 'names': []})
            path.write_text(json.dumps({'schema': 1, 'names': ['Mira', 'Mina']}));path.chmod(0o600)
            self.assertEqual(glossary.read(path)['names'], ['Mira', 'Mina'])
            path.chmod(0o644)
            with self.assertRaises(ValueError): glossary.read(path)
            path.chmod(0o600)
            linked = root/'link.json'; linked.symlink_to(path)
            with self.assertRaises(ValueError): glossary.read(linked)
            path.write_text(json.dumps({'schema': 1, 'names': [], 'aliases': {}}))
            with self.assertRaises(ValueError): glossary.read(path)

    def test_docker_arguments_never_contain_hint_text(self):
        lock = trial.read_lock()
        args = batch.full_engine_args(lock, Path('/private/input'), Path('/private/out'),
                                      'life-atlas-audio-trial-'+'a'*32, 'synthetic', with_names=True)
        script = args[args.index('-c')+1]
        self.assertIn('name_prompt=$(cat /input/names-prompt.txt) || exit 65', script)
        self.assertIn('--prompt "$name_prompt"', script)
        self.assertIn('--carry-initial-prompt', script)
        self.assertIn('7200s', script)
        self.assertNotIn('Mira', ' '.join(args))
        original = batch.full_engine_args(lock, Path('/private/input'), Path('/private/out'),
                                          'life-atlas-audio-trial-'+'a'*32, 'synthetic')
        self.assertNotIn('names-prompt', ' '.join(original))

    def test_packet_is_frozen_and_old_job_context_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); job = root/'job'; job.mkdir(mode=0o700)
            raw = {'transcription': [{'text': ' Mira and Mina.', 'offsets': {'from': 0, 'to': 1000}}]}
            (job/'raw.json').write_text(json.dumps(raw)); (job/'raw.json').chmod(0o600)
            automation.save(job/'source.json', {})
            with patch.object(automation, 'job_path', return_value=job), patch.object(automation, 'validate_job'):
                old = automation.packet('unused')
                self.assertNotIn('name_hints', old)
                automation.save(job/'names.json', {'schema': 1, 'names': ['Mira', 'Mina']})
                frozen_hash = trial.sha256(job/'names.json')
                automation.save(job/'source.json', {'names_sha256': frozen_hash})
                packet = automation.packet('unused')
                self.assertEqual(packet['name_hints'], ['Mira', 'Mina'])
                unrelated = root/'current.json'
                unrelated.write_text(json.dumps({'schema': 1, 'names': ['Other']})); unrelated.chmod(0o600)
                with patch.object(automation, 'NAMES_FILE', unrelated):
                    self.assertEqual(automation.packet('unused'), packet)
                automation.save(job/'names.json', {'schema': 1, 'names': ['Other']})
                with self.assertRaises(RuntimeError): automation.packet('unused')

    def test_asr_receipt_must_bind_the_actual_prompt(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)/('a'*64); job.mkdir(mode=0o700)
            automation.save(job/'names.json', {'schema': 1, 'names': ['Mira', 'Mina']})
            frozen_hash = trial.sha256(job/'names.json')
            automation.save(job/'source.json', {'names_sha256': frozen_hash})
            for name in ('raw.json', 'raw.txt', 'audio.opus'):
                (job/name).write_bytes(b'synthetic'); (job/name).chmod(0o600)
            receipt = {'source_sha256': job.name, 'json_sha256': trial.sha256(job/'raw.json'),
                'transcript_sha256': trial.sha256(job/'raw.txt'), 'names_sha256': frozen_hash, 'names_prompt_sha256': None}
            automation.save(job/'asr-receipt.json', receipt)
            automation.save(job/'audio-receipt.json', {'sha256': trial.sha256(job/'audio.opus')})
            with self.assertRaises(RuntimeError): automation.validate_job(job)
            receipt['names_prompt_sha256'] = hashlib.sha256(glossary.prompt(['Mira', 'Mina']).encode()).hexdigest()
            automation.save(job/'asr-receipt.json', receipt)
            automation.validate_job(job)

    def test_editor_receipt_must_bind_frozen_hint_list(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)/'job'; job.mkdir(mode=0o700)
            automation.save(job/'names.json', {'schema': 1, 'names': ['Mira', 'Mina']})
            automation.save(job/'source.json', {'names_sha256': trial.sha256(job/'names.json')})
            automation.save(job/'raw.json', {'transcription': [{'text': ' Mira and Mina.', 'offsets': {'from': 0, 'to': 1000}}]})
            raw_hash = trial.sha256(job/'raw.json')
            proposal = json.dumps({'source_sha256': raw_hash, 'edits': []})
            with patch.object(automation, 'job_path', return_value=job), patch.object(automation, 'validate_job'):
                packet = automation.packet('a'*64)
                receipt = {'provider': automation.MODEL_PROVIDER, 'model': automation.MODEL_ID,
                    'status': 'proposed-awaiting-server-validation', 'audio_shared': False,
                    'source_sha256': raw_hash, 'packet_sha256': hashlib.sha256(json.dumps(packet, ensure_ascii=False).encode()).hexdigest(),
                    'proposal_sha256': hashlib.sha256(proposal.encode()).hexdigest()}
                with self.assertRaises(RuntimeError): automation.accept_locked('a'*64, {'proposal': proposal, 'receipt': receipt})
                receipt['name_hints_sha256'] = hashlib.sha256(json.dumps(['Mira', 'Mina'], ensure_ascii=False).encode()).hexdigest()
                self.assertEqual(automation.accept_locked('a'*64, {'proposal': proposal, 'receipt': receipt})['status'], 'reviewed')

    def test_invalid_configuration_fails_without_returning_private_data(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'names.json'; path.write_text('{"schema":1,"names":["bad\\nname"]}');path.chmod(0o600)
            with self.assertRaises(ValueError) as error: glossary.read(path)
            self.assertNotIn('bad', str(error.exception))


if __name__ == '__main__': unittest.main()
