#!/usr/bin/env python3
"""Local review artifacts, not an LLM client. Never print transcript contents."""
import argparse
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

MAX_BYTES = 16 * 1024 * 1024
# Preserve words, negations, signed numbers/decimal structure and significant symbols.
LEXICAL = re.compile(r"[+-]?\d+(?:[.,:/-]\d+)*|[^\W\d_]+(?:['’][^\W\d_]+)*|_|[^\w\s.,;:!?\"'’“”()\[\]—–]", re.UNICODE)


def words(text):
    return LEXICAL.findall(text.replace('’', "'").casefold())


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Unsafe or oversized input')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def segments(data):
    result = []
    entries = data.get('transcription')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Expected nonempty whisper.cpp transcription segments')
    for index, item in enumerate(entries):
        text = item.get('text')
        offsets = item.get('offsets', {})
        start, end = offsets.get('from'), offsets.get('to')
        if not isinstance(text, str) or not integer(start) or not integer(end) or not 0 <= start <= end:
            raise ValueError('Invalid ASR text or millisecond offsets')
        probabilities = []
        for token in item.get('tokens', []):
            p = token.get('p')
            if isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1:
                probabilities.append(float(p))
        result.append({'id': index, 'start_ms': start, 'end_ms': end,
            'asr_text': text, 'token_probabilities': probabilities})
    return result


def disagreement_ids(base, other):
    a, owners = [], []
    for segment in base:
        tokens = words(segment['asr_text'])
        a.extend(tokens); owners.extend([segment['id']] * len(tokens))
    b = [word for segment in other for word in words(segment['asr_text'])]
    flagged = set()
    if max(len(a), len(b)) > 10000:
        raise ValueError('Comparison exceeds the bounded token budget')
    for tag, i, j, _, _ in difflib.SequenceMatcher(a=a, b=b, autojunk=True).get_opcodes():
        if tag != 'equal' and owners:
            flagged.update(owners[i:j] or [owners[min(i, len(owners)-1)]])
    return flagged


def timestamp(ms):
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def output_dir(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Output must not be a symlink')
    path = path.resolve()
    if any((parent / '.git').exists() for parent in [path, *path.parents]):
        raise ValueError('Private review output must be outside Git')
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o077:
        raise ValueError('Output parent must already be private and owned by the caller')
    path.mkdir(mode=0o700)  # Refuse overwriting any existing output directory.
    return path


def write_json(path, data):
    with path.open('x') as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump(data, out, ensure_ascii=False, indent=2)
        out.write('\n')


def packet(source, compare=None):
    data, digest = read_json(source)
    entries = segments(data)
    alternate_hash = None
    disagreements = set()
    if compare:
        alternate, alternate_hash = read_json(compare)
        disagreements = disagreement_ids(entries, segments(alternate))
    for item in entries:
        probs = item.pop('token_probabilities')
        signals = []
        if any(p < 0.5 for p in probs):
            signals.append('some_token_p_below_0.5_uncalibrated_heuristic')
        if item['id'] in disagreements:
            signals.append('alternate_asr_disagreement_not_proof_of_error')
        item.update(review_signals=signals, token_probability_available=bool(probs),
                    minimum_token_p=min(probs) if probs else None)
    return {'schema': 1, 'source_sha256': digest, 'compare_sha256': alternate_hash,
        'timestamps': 'ASR-input-relative segment anchors, not exact word boundaries',
        'confidence': 'review priority only; token p is not word-correctness probability',
        'segments': entries}


def apply_proposal(data, digest, proposal):
    if not isinstance(proposal, dict) or set(proposal) != {'source_sha256', 'edits'} or proposal['source_sha256'] != digest:
        raise ValueError('Proposal must match the exact raw ASR hash')
    edits = proposal['edits']
    if not isinstance(edits, list) or len(edits) > 2000:
        raise ValueError('Invalid edit list')
    base = segments(data)
    by_id = {s['id']: [] for s in base}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'segment', 'start', 'end', 'kind', 'replacement', 'reason'}:
            raise ValueError('Unexpected edit fields')
        sid = edit['segment']
        if not integer(sid) or sid not in by_id:
            raise ValueError('Unknown segment')
        if not integer(edit['start']) or not integer(edit['end']) or not 0 <= edit['start'] <= edit['end'] <= len(base[sid]['asr_text']):
            raise ValueError('Invalid character offsets')
        if edit['kind'] not in {'format', 'guess', 'unclear'} or not isinstance(edit['reason'], str) or not edit['reason'].strip():
            raise ValueError('Invalid edit kind or missing reason')
        replacement = edit['replacement']
        if not isinstance(replacement, str) or len(replacement) > 2000:
            raise ValueError('Invalid replacement')
        original = base[sid]['asr_text'][edit['start']:edit['end']]
        if edit['kind'] == 'format' and words(original) != words(replacement):
            raise ValueError('Unbracketed formatting must preserve lexical content')
        if edit['kind'] == 'guess' and (not replacement.strip() or any(c in replacement for c in '[]\r\n')):
            raise ValueError('Guess needs a single bracket-free replacement phrase')
        if edit['kind'] == 'unclear' and replacement:
            raise ValueError('Unclear spans must not hide replacement text')
        by_id[sid].append(edit)
    paragraphs, ledger = [], []
    for segment in base:
        text = segment['asr_text']; cursor = 0; pieces = []; previous = None
        for edit in sorted(by_id[segment['id']], key=lambda e: (e['start'], e['end'])):
            start, end = edit['start'], edit['end']
            if start < cursor or previous == (start, end):
                raise ValueError('Overlapping or duplicate edits')
            replacement = edit['replacement']
            anchor = timestamp(segment['start_ms'])
            if edit['kind'] == 'guess':
                rendered = '[' + replacement.strip() + '? @' + anchor + ']'
            elif edit['kind'] == 'unclear':
                rendered = '[unclear @' + anchor + ']'
            else:
                rendered = replacement
            pieces.extend([text[cursor:start], rendered]); cursor = end; previous = (start, end)
            ledger.append({**edit, 'original_text': text[start:end], 'rendered': rendered,
                           'segment_start_ms': segment['start_ms'], 'verified_verbatim': False})
        pieces.append(text[cursor:])
        formatting = text
        for edit in sorted((e for e in by_id[segment['id']] if e['kind'] == 'format'), key=lambda e: e['start'], reverse=True):
            formatting = formatting[:edit['start']] + edit['replacement'] + formatting[edit['end']:]
        if words(formatting) != words(text):
            raise ValueError('Formatting changes lexical boundaries')
        paragraphs.append(''.join(pieces).strip())
    header = ('# Edited reading draft, not verified verbatim\n\n'
        'Bracketed text is editorial uncertainty. Timestamps anchor ASR-input segments, '
        'not exact word boundaries; no source-recording offset is inferred.\n\n')
    body = '\n\n'.join(paragraphs)
    # Transcript text is untrusted literal data. A fence longer than every
    # backtick run prevents HTML/images/Markdown in speech or guesses from
    # hiding uncertainty or making requests in a Markdown preview.
    fence = '`' * (max([2] + [len(run) for run in re.findall(r'`+', body)]) + 1)
    return header + fence + 'text\n' + body + '\n' + fence + '\n', ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['packet', 'render'])
    parser.add_argument('source')
    parser.add_argument('--compare')
    parser.add_argument('--proposal')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    data, digest = read_json(args.source)
    if args.action == 'packet':
        result = packet(args.source, args.compare)
        if result['source_sha256'] != digest:
            raise ValueError('Raw source changed while preparing the packet')
        out = output_dir(args.out)
        write_json(out/'packet.json', result)
        write_json(out/'proposal.template.json', {'source_sha256': digest, 'edits': []})
        count = len(result['segments'])
    else:
        if not args.proposal:
            raise ValueError('Render requires an explicitly supplied proposal')
        proposal, proposal_hash = read_json(args.proposal)
        text, ledger = apply_proposal(data, digest, proposal)
        out = output_dir(args.out)
        with (out/'reading-draft.md').open('x') as draft:
            os.fchmod(draft.fileno(), 0o600); draft.write(text)
        write_json(out/'edit-ledger.json', ledger)
        write_json(out/'provenance.json', {'source_sha256': digest,
            'proposal_sha256': proposal_hash, 'draft_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'raw_asr_modified': False, 'verified_verbatim': False})
        count = len(ledger)
    if read_json(args.source)[1] != digest:
        raise ValueError('Raw source changed during review artifact creation')
    print(json.dumps({'action': args.action, 'output_directory': str(out), 'items': count,
                      'private_text_printed': False}))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as exc:
        print('Transcript review failed:', type(exc).__name__, '(private diagnostics suppressed)', file=sys.stderr)
        raise SystemExit(1)
