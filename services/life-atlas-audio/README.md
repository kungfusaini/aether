# Private audio transcription quality trial

This is an explicitly invoked trial, **not an automatic transcription workflow**.
There is no service/timer, public endpoint, new upload rule or automatic backfill.

## Engine and reproducibility

- Adopt upstream [whisper.cpp](https://github.com/ggml-org/whisper.cpp), MIT licensed,
  rather than build an ASR engine or web service.
- Use its official ARM64 CPU container, pinned to an immutable architecture-specific
  digest in `trial.lock.json`. Upstream's `main` image currently targets amd64;
  the [publishing workflow](https://github.com/ggml-org/whisper.cpp/blob/master/.github/workflows/docker.yml)
  identifies `main-arm64` as the correct source tag. Never use a floating tag at runtime.
- English-only `small.en`, Q5_1 quantization. The ~190 MB weight download is pinned
  to a Hugging Face repository revision and independently SHA-256/size checked.
- Use existing Aether Docker/FFmpeg. No host compiler stack, Python ML environment,
  cloud transcription API, GPU, login or registry-credential changes are required.
- The downloaded container/model are third-party software, not independently audited
  here. A pinned digest provides identity, not a proof of safety or accuracy.

## Scope and privacy

1. Run the upstream public JFK sample first, comparing normalized words against the
   public reference. This is a smoke gate, not an accuracy benchmark for private speech.
2. With explicit approval, use only the first **90 seconds of log001**.
   The original trial read its compressed archive; a separate `pilot-raw` action
   supports the same excerpt from the original WAV for controlled comparison.
   Compressed pilots require the approved basename in the processed archive.
   Raw pilots require `log001.wav` within the private raw-staging root and an
   explicit SHA-256 from the outside-Git original source receipt. Wrong hashes,
   other basenames, outside paths and symlinks are rejected before inference.
3. Transcribe English, not translate or summarize. No diarization or semantic analysis.
4. Do not print private transcript content, engine logs or audio into agent/tool/chat
   output. JSON receipts contain only timings, hashes, sizes/counts and output paths.
5. Sumeet judges the private transcript's actual accuracy before any workflow is built.
   Do not claim quality from successful execution or a clean public sample alone.

The inference container has no networking, no capabilities, no published ports,
no Docker log storage, a read-only root filesystem, and only a read-only model
and excerpt mount plus a private writable output mount. It has a private cgroup
namespace and limits of 1.5 CPUs, two inference threads, 1.5 GiB RAM/no extra swap,
128 PIDs and an independent container-side 15-minute engine deadline (five-second
forced-termination grace). This deadline survives a lost SSH/Python client. A separate
client timeout includes termination/cleanup allowance. Cleanup checks the exact
random container name and ownership label, then verifies removal; it never removes
unrelated containers. Runtime includes model/container startup and cleanup;
`memory.peak` is measured inside that container cgroup. FFmpeg excerpt extraction
runs separately at low priority with one decode thread.

Runtime assets on Aether (all outside Git/web roots):

- Code/config: `/opt/life-atlas-audio` (deployed only from this source directory).
- Public downloaded weights: `/srv/audio-archive/models`.
- Public test audio: `/srv/audio-archive/public-fixtures`.
- Original-WAV comparison staging: `/srv/audio-archive/transcription-raw-staging/`,
  outside normal audio snapshots. Transfer only the approved original, verify its
  source hash and phone identity, then remove only that exact task-owned staged
  file after successful trial generation. The runner does not delete sources.
- Private trials: `/srv/audio-archive/transcription-trials/<run-id>/`.
  `input/input.wav` is the 90-second mono PCM excerpt; `output/transcript.txt` and
  `output/transcript.json` contain private content. `engine-private.log` can also
  contain private content and must not be copied into reports.
- `receipt.json` records hashes, exact model/image, resource measurements and counts,
  but no transcript text. All trial directories are mode 0700 and files mode 0600.

The trial keeps its excerpt and outputs for review. No audio/archive originals,
phone recordings or previous results are changed or deleted. No automatic retention
or independent off-server backup is introduced by this trial.

## Commands (explicit approval required)

```sh
python3 -B -m unittest discover -s services/life-atlas-audio -v
python3 services/life-atlas-audio/deploy_trial.py
ssh aether 'cd /opt/life-atlas-audio && python3 -B trial.py self-test'
ssh aether 'cd /opt/life-atlas-audio && python3 -B trial.py public'
# Private pilot: choose only the explicitly approved archived log001 input.
ssh aether 'cd /opt/life-atlas-audio && python3 -B trial.py pilot --source /srv/audio-archive/processed/BATCH/log001-SOURCEHASH.opus'
# Original WAV comparison, only after verified private staging and explicit approval:
ssh aether 'cd /opt/life-atlas-audio && python3 -B trial.py pilot-raw --source /srv/audio-archive/transcription-raw-staging/RUN/log001.wav --source-sha256 SOURCE_SHA256'
```

`deploy_trial.py` transfers only an explicit source/config/test/docs allowlist,
then installs hash-verified public assets. It does not read or transfer private
recordings or outputs, restart existing services, install a timer, modify Syncthing,
or touch WhatsApp. Image pulling uses a task-owned empty Docker client config
so existing registry logins are neither read nor changed.

Model/asset download URLs and private diagnostics are suppressed on failures;
inspect only the necessary protected state, never dump private logs into chat.

## Uncertainty-preserving review (manual, not a cloud pipeline)

The project skill is [uncertain-transcript-review](../../.agents/skills/uncertain-transcript-review/SKILL.md).
Pi discovers it when working in this repository; another workspace can explicitly
read that canonical file. It is not installed as a global skill or authority to
read more private content. Deployment copies its policy to
`/opt/life-atlas-audio/transcript-review.SKILL.md` for reference, not automatic
agent registration on the server.

Future explicit ASR runs request full JSON (`-ojf`) so token probabilities are
retained where the engine supplies them. Existing trial files are not rewritten
or rerun. These probabilities are not calibrated word accuracy; a token p<0.5 is
only an initial review-priority heuristic. Missing token information is reported
as unavailable. Timestamps anchor ASR-input segments, not exact words; cropped
snippets need a separately recorded actual source offset before using whole-file
times. Do not infer offsets from text.

`review.py` is a standard-library-only **local artifact helper**. It does not call
an LLM, make network requests, listen to audio, start another ASR pass, or send
anything to this chat. Its outputs contain private speech text even though its
console output contains only operation/path/count metadata. Obtain explicit
model-context sharing permission before reading a private packet into an agent.

After authorization, use an already-existing caller-owned mode-0700 output
parent outside Git. Output directories must be fresh:

```sh
python3 review.py packet /PRIVATE/asr.json --compare /PRIVATE/other-asr.json --out /PRIVATE/new-packet
python3 review.py render /PRIVATE/asr.json --proposal /PRIVATE/proposal.json --out /PRIVATE/new-render
```

The optional comparison flags segments with normalized token disagreement using
bounded sequence matching; it does not vote for a correct transcript. Comparisons
over 10,000 tokens fail rather than spending unbounded CPU. Token scores can be
wrong even when high; two ASR runs can agree and still be wrong.

A packet has `packet.json` and an empty `proposal.template.json`. Proposals are
source-hash-bound edits using original segment IDs and Python character offsets.
Each edit must include a reason and one of these types:

- `format`: must retain the lexical signature (including ordinary negations,
  signed numbers and significant currency/percent symbols). This is a conservative
  syntax guard, not proof of unchanged semantics.
- `guess`: always rendered as `[best guess? @00:43]`.
- `unclear`: always rendered as `[unclear @00:43]`; no hidden replacement allowed.

Rendering produces `reading-draft.md`, `edit-ledger.json` and `provenance.json`.
The ledger preserves exact replaced spans, kinds/reasons and source anchors.
Transcript/replacement text is rendered in a literal fenced block whose delimiter
is longer than any backtick run in the text. HTML comments/images and Markdown
inside speech/guesses therefore cannot hide annotations or make preview requests;
none of that text is executable code. JSON values are also untrusted text, not
HTML for direct insertion by a future UI.
Overlapping/duplicate edits, unknown types, stale source hashes, empty reasons,
unbracketed lexical substitutions and private output under Git are rejected.
No edits are declared verified verbatim. The raw ASR files stay untouched.
Review successful command completion: failed partial output is not accepted.

Names, dates, numbers, negations and statements about the speaker must not be
silently reconstructed. A fluent reading draft is not evidence of exact speech.
Preserve speaker hesitations and uncertainty rather than importing generic
prose-humanization rules. A stronger-model retry of a short important span is
an optional future approved action, not implemented or scheduled here.

The policy adapts [oral-history guidance](https://www.ufic.ufl.edu/ogl/spohp-styleguide.pdf),
[W3C accurate transcription guidance](https://www.w3.org/WAI/media/av/transcribing/),
and [confidence-guided correction research](https://arxiv.org/abs/2509.25048).
The marker syntax is a project convention. Research results on other speech
populations do not establish accuracy improvements on these recordings.

## Initial measurements (13 November 2026)

On Aether with the pinned model/image and 1.5-CPU/two-thread settings:

| Trial | Audio | End-to-end engine command | Cgroup peak RAM | Accuracy |
| --- | --- | --- | --- | --- |
| Public JFK sample | 11 seconds | 32.61 seconds | 509 MiB | 0 normalized word errors against its short reference |
| Private compressed-copy excerpt | 90 seconds | 191.85 seconds | 555 MiB | User found it good but not very accurate; no assistant inspection |
| Same excerpt from original WAV | 90 seconds | 190.16 seconds | 614 MiB | Awaiting Sumeet's comparison; no assistant inspection |

Both private runs produced plain text and 19 timestamped segments. Their
normalized transcripts each contain 251 words and differ by 10 word-level edits.
That is **transcript disagreement, not a word error rate**: there is no private
reference transcript, so it does not prove either result more accurate. Model,
image, excerpt duration and inference settings were unchanged. The phone WAV's
source identity was checked against the prior batch hash; the exact full raw
staging file was removed after the successful comparison and phone-original
integrity check. The separate private 90-second review artifacts are retained.

The compressed-copy private run's SSH caller
later timed out; a completed server receipt established the actual runtime, and
the private input was not rerun. A read-only terminal review was opened directly
for Sumeet without routing transcript text through agent/tool output. Neither the
public result nor successful private execution establishes private word accuracy,
performance on long recordings or workflow/reboot reliability.

Future workflow decisions—quality threshold/model choice, log-prefix selection,
nightly scheduling, raw-vs-compressed transcription order, idempotency/retries,
transcript correction/provenance and backup—are intentionally not implemented.
