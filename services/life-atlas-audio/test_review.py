"""Synthetic data only. No personal transcript fixtures or model calls."""
import copy
from contextlib import redirect_stdout
import io
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import review


def sample(text='Please bring the blue box.'):
    return {'transcription': [{'text': text, 'offsets': {'from': 12340, 'to': 16000},
        'tokens': [{'p': 0.2}, {'p': 0.95}]}]}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = sample()
        self.digest = 'a'*64

    def proposal(self, **changes):
        edit = {'segment': 0, 'start': 17, 'end': 21, 'kind': 'guess',
                'replacement': 'green', 'reason': 'Synthetic alternate hypothesis'}
        edit.update(changes)
        return {'source_sha256': self.digest, 'edits': [edit]}

    def test_guess_marker_and_exact_ledger(self):
        draft, ledger = review.apply_proposal(self.data, self.digest, self.proposal())
        self.assertIn('[green? @00:12]', draft)
        self.assertEqual(ledger[0]['original_text'], 'blue')
        self.assertFalse(ledger[0]['verified_verbatim'])
        self.assertEqual(self.data, sample())

    def test_markdown_and_html_are_inert_inside_dynamic_fence(self):
        for payload in ['<!--', '<img src="https://example.invalid/private">',
                        '![remote](https://example.invalid/private)',
                        '```'+chr(10)+'<script>bad</script>'+chr(10)+'```', '**bold** _hidden_']:
            if '\n' in payload or '[' in payload:
                data = sample(payload)
                proposal = {'source_sha256': self.digest, 'edits': []}
            else:
                data = self.data
                proposal = self.proposal(replacement=payload)
            draft, _ = review.apply_proposal(data, self.digest, proposal)
            lines = draft.splitlines()
            opening = next(line for line in lines if line.endswith('text') and line.startswith('```'))
            fence = opening[:-4]
            self.assertEqual(lines[-1], fence)
            body = '\n'.join(lines[lines.index(opening)+1:-1])
            self.assertIn(payload, body)
            self.assertGreater(len(fence), max([0] + [len(x) for x in re.findall(r'`+', body)]))
            self.assertNotIn(payload, '\n'.join(lines[:lines.index(opening)]))

    def test_unclear_and_no_hidden_replacement(self):
        draft, _ = review.apply_proposal(self.data, self.digest, self.proposal(kind='unclear', replacement=''))
        self.assertIn('[unclear @00:12]', draft)
        with self.assertRaises(ValueError):
            review.apply_proposal(self.data, self.digest, self.proposal(kind='unclear'))

    def test_format_can_only_preserve_content(self):
        edit = self.proposal(start=0, end=len(self.data['transcription'][0]['text']),
            kind='format', replacement='Please bring the blue box!', reason='Punctuation only')
        draft, _ = review.apply_proposal(self.data, self.digest, edit)
        self.assertIn('Please bring the blue box!', draft)
        for original, replacement in [('We do not agree.', 'We do agree.'),
            ('Pay -5 dollars.', 'Pay 5 dollars.'), ('Pay - 5 dollars.', 'Pay 5 dollars.'),
            ('It costs $5.', 'It costs 5.'),
            ('Set 1.5 volts.', 'Set 15 volts.'), ('Ask Ana.', 'Ask Ann.'),
            ('The café.', 'The cafe.')]:
            data = sample(original)
            with self.assertRaises(ValueError):
                review.apply_proposal(data, self.digest, self.proposal(start=0, end=len(original),
                    kind='format', replacement=replacement))

    def test_format_cannot_merge_words_by_removing_space(self):
        data = sample('We meet now here.')
        with self.assertRaises(ValueError):
            review.apply_proposal(data, self.digest, self.proposal(start=11, end=12,
                kind='format', replacement='', reason='Synthetic dangerous space removal'))

    def test_reject_stale_and_misleading_proposal(self):
        proposal = self.proposal()
        with self.assertRaises(ValueError):
            review.apply_proposal(self.data, 'b'*64, proposal)
        proposal['verified'] = True
        with self.assertRaises(ValueError):
            review.apply_proposal(self.data, self.digest, proposal)

    def test_reject_invalid_or_overlapping_edits(self):
        for changes in [{'start':-1}, {'end':999}, {'segment':True}, {'kind':'verified'},
                        {'reason':''}, {'replacement':'bad] marker'}, {'replacement':'two\nlines'}]:
            with self.assertRaises(ValueError):
                review.apply_proposal(self.data, self.digest, self.proposal(**changes))
        proposal = self.proposal(); proposal['edits'].append(copy.deepcopy(proposal['edits'][0]))
        with self.assertRaises(ValueError):
            review.apply_proposal(self.data, self.digest, proposal)

    def test_packet_hints_are_not_accuracy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'source.json'; source.write_text(json.dumps(self.data))
            other = Path(directory)/'other.json'; other.write_text(json.dumps(sample('Please bring the green box.')))
            packet = review.packet(source, other)
            self.assertEqual(len(packet['segments'][0]['review_signals']), 2)
            self.assertIn('not word-correctness', packet['confidence'])
            self.assertEqual(packet['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            missing = sample(); missing['transcription'][0].pop('tokens')
            source.write_text(json.dumps(missing))
            self.assertFalse(review.packet(source)['segments'][0]['token_probability_available'])
            self.assertIsNone(review.packet(source)['segments'][0]['minimum_token_p'])

    def test_timestamp_and_comparison_bound(self):
        self.assertEqual(review.timestamp(3601000), '01:00:01')
        self.assertEqual(review.disagreement_ids(review.segments(self.data), review.segments(self.data)), set())
        with self.assertRaises(ValueError):
            review.disagreement_ids(review.segments(sample('word '*10001)), review.segments(self.data))

    def test_output_privacy_overwrite_and_git_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            out = review.output_dir(parent/'private-result')
            review.write_json(out/'metadata.json', {'synthetic': True})
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertEqual((out/'metadata.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                review.output_dir(out)
            repo = parent/'repo'; repo.mkdir(mode=0o700); (repo/'.git').mkdir()
            hidden = repo/'private'; hidden.mkdir(mode=0o700)
            alias = parent/'alias'; alias.symlink_to(hidden)
            with self.assertRaises(ValueError):
                review.output_dir(alias/'must-not-enter-git')
            public = parent/'public'; public.mkdir(mode=0o755); public.chmod(0o755)
            with self.assertRaises(ValueError):
                review.output_dir(public/'unsafe-result')

    def test_cli_keeps_raw_source_and_does_not_print_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent/'source.json'; source.write_text(json.dumps(self.data))
            before = source.read_bytes()
            packet_dir = parent/'packet'
            console = io.StringIO()
            with patch('sys.argv', ['review.py', 'packet', str(source), '--out', str(packet_dir)]), redirect_stdout(console):
                review.main()
            template = json.loads((packet_dir/'proposal.template.json').read_text())
            proposal = self.proposal(); proposal['source_sha256'] = template['source_sha256']
            prop = parent/'proposal.json'; prop.write_text(json.dumps(proposal))
            render = parent/'render'
            with patch('sys.argv', ['review.py', 'render', str(source), '--proposal', str(prop), '--out', str(render)]), redirect_stdout(console):
                review.main()
            self.assertEqual(source.read_bytes(), before)
            self.assertIn('[green? @00:12]', (render/'reading-draft.md').read_text())
            self.assertNotIn('Please bring', console.getvalue())
            self.assertNotIn('green', console.getvalue())
            self.assertFalse(json.loads((render/'provenance.json').read_text())['raw_asr_modified'])

    def test_project_skill_metadata_and_helper_reference(self):
        repo = Path(__file__).resolve().parents[2]
        skill = repo/'.agents/skills/uncertain-transcript-review/SKILL.md'
        in_repo = skill.is_file()
        if not in_repo:
            skill = Path(__file__).with_name('transcript-review.SKILL.md')
        text = skill.read_text()
        self.assertTrue(text.startswith('---\nname: uncertain-transcript-review\n'))
        self.assertIn('description:', text.split('---')[1])
        helper = (skill.parent/'../../../services/life-atlas-audio/review.py').resolve() if in_repo else Path(__file__).with_name('review.py')
        self.assertTrue(helper.is_file())


if __name__ == '__main__':
    unittest.main()
