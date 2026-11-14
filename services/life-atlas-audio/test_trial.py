"""No network, Docker or personal content required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import trial


class TrialTests(unittest.TestCase):
    def test_pins(self):
        lock = trial.read_lock()
        self.assertIn('@sha256:', lock['image'])
        self.assertEqual(lock['image_platform'], 'linux/arm64')
        self.assertEqual(len(lock['model']['sha256']), 64)
        self.assertEqual(lock['limits']['pilot_seconds'], 90)

    def test_engine_isolation(self):
        args = trial.engine_args(trial.read_lock(), Path('/private/input'), Path('/private/output'), 'synthetic')
        for flag, value in [('--network', 'none'), ('--cgroupns', 'private'), ('--cap-drop', 'ALL'),
                            ('--log-driver', 'none'), ('--memory', '1536m'), ('--cpus', '1.5')]:
            self.assertEqual(args[args.index(flag)+1], value)
        self.assertIn('--read-only', args)
        self.assertIn('-ojf', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('-p', args)
        mounts = [args[i+1] for i, v in enumerate(args) if v == '--mount']
        self.assertTrue(all('readonly' in x for x in mounts if 'target=/output' not in x))
        self.assertEqual(sum('target=/output' in x for x in mounts), 1)

    def test_container_deadline_is_independent(self):
        args = trial.engine_args(trial.read_lock(), Path('/input'), Path('/output'), 'synthetic')
        script = args[args.index('-c')+1]
        self.assertIn('timeout --signal=TERM --kill-after=5s 900s', script)
        self.assertIn('"$@"', script)
        with self.assertRaises(RuntimeError):
            trial.deadline_script(0)

    def test_cleanup_checks_ownership_and_removal(self):
        name = 'life-atlas-audio-trial-' + 'a'*32
        with patch.object(trial, 'checked', side_effect=[(name+'|1'+chr(10)).encode(), b'', b'']) as command:
            trial.cleanup_container(name)
            self.assertEqual(command.call_count, 3)
            self.assertEqual(command.call_args_list[1].args[0], ['docker', 'rm', '-f', name])
        with patch.object(trial, 'checked', return_value=(name+'|unrelated').encode()) as command:
            with self.assertRaises(RuntimeError):
                trial.cleanup_container(name)
            self.assertEqual(command.call_count, 1)
        with patch.object(trial, 'checked', side_effect=[(name+'|1').encode(), b'', (name+'|1').encode()]):
            with self.assertRaises(RuntimeError):
                trial.cleanup_container(name)
        with patch.object(trial, 'checked') as command:
            with self.assertRaises(RuntimeError):
                trial.cleanup_container('unrelated-service')
            command.assert_not_called()

    def test_public_quality_metric(self):
        self.assertEqual(trial.word_error_rate('Ask not!', 'ask not'), 0)
        self.assertEqual(trial.word_error_rate('one two three', 'one three'), 1/3)

    def test_corrupt_asset_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'fixture.bin'
            p.write_bytes(b'not the expected public fixture')
            with patch('urllib.request.urlopen') as network:
                with self.assertRaises(RuntimeError):
                    trial.download({'filename':p.name, 'sha256':'0'*64}, Path(directory))
                network.assert_not_called()
            self.assertEqual(p.read_bytes(), b'not the expected public fixture')

    def test_raw_pilot_requires_exact_name_root_and_hash(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(trial, 'STATE', Path(directory)):
            root = Path(directory)
            stage = root/'transcription-raw-staging'/'synthetic-run'
            stage.mkdir(parents=True)
            source = stage/'log001.wav'
            source.write_bytes(b'synthetic WAV identity fixture, not private audio')
            checksum = trial.sha256(source)
            self.assertEqual(trial.select_pilot(source, checksum), (source, 'private-90s-original-wav'))
            with self.assertRaises(RuntimeError):
                trial.select_pilot(source, '0'*64)
            other = stage/'log002.wav'; other.write_bytes(source.read_bytes())
            with self.assertRaises(RuntimeError):
                trial.select_pilot(other, checksum)
            outside = root/'log001.wav'; outside.write_bytes(source.read_bytes())
            with self.assertRaises(RuntimeError):
                trial.select_pilot(outside, checksum)
            link = stage/'linked'; link.symlink_to(source)
            with self.assertRaises(RuntimeError):
                trial.select_pilot(link, checksum)
            with self.assertRaises(RuntimeError):
                trial.select_pilot(source)  # Raw path must not enter through compressed mode.

    def test_no_unapproved_pilot_path(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'unrelated.txt'
            p.write_text('synthetic unrelated input')
            with self.assertRaises(RuntimeError):
                trial.run_trial(trial.read_lock(), p)


if __name__ == '__main__':
    unittest.main()
