# Private audio transcription and review

Quality trials and the **one-off full three-log batch** remain explicitly scoped
manual commands. A separate approved arrival-processing worker (`automation.py`)
now supports selected-file nightly intake and the text-only laptop review queue;
it does not relax the pilot/three-log runners or backfill old recordings. There
is no public endpoint. See the automatic workflow section for setup and limits.

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
or touch WhatsApp. It installs the inactive manual full-batch service template;
it does not start or enable any instance. Image pulling uses a task-owned empty Docker client config
so existing registry logins are neither read nor changed.

Model/asset download URLs and private diagnostics are suppressed on failures;
inspect only the necessary protected state, never dump private logs into chat.

## Explicit full three-log batch

`batch.py` is separate from the 90-second trial runner. It accepts only
`log001.wav`, `log002.wav` and `log003.wav`, in that order, from private staging
with a runtime manifest containing their previously approved SHA-256/size and
full durations. No private manifest or transcript belongs in Git. File durations
are limited to one hour each; the current selection is about 58 minutes total.

Staging layout: `/srv/audio-archive/staging/full-logs-YYYYMMDD/` containing the
three exact originals plus `source-manifest.json`. Model/image configuration is
the existing pinned `trial.lock.json`; no new model or larger inference setting
is selected by the batch.

The worker decodes the **whole source**, not 90 seconds, to 16 kHz mono PCM
under that same temporary staging root, then transcribes sequentially with the
existing 1.5-CPU/two-thread/1.5-GiB container restrictions. Full-file containers
have a separate independent two-hour deadline/termination grace. The trial's
15-minute deadline and excerpt restrictions are not relaxed. Runtime model pulls
are disabled. A 1.8-GiB available-memory check runs before each new file.

A private singleton lock prevents simultaneous full batches. A shared inference
lock also coordinates pilots and full runs. A fsynced launch-intent marker is
published before any Docker CLI can start, closing the invisible-container launch
window after supervisor death. A surviving intent or owned container refuses
new inference, even if Docker currently lists no containers. Intent is removed
only after normal scope completion and verified cleanup. Interrupted/failed scopes
retain it for explicit recovery; it is never expired automatically. Inspect the
prior worker/container and pending launch state before clearing a task-owned marker.
The worker does not silently overlap or terminate another job. Control metadata
lives privately under `/srv/audio-archive/transcription-control/`.
Per-file completion receipts record source/model/image/duration, exact output
hashes, timings, container peak RAM and temporary PCM identity. Artifacts and their
ancestor directory entries are fsynced before publishing a completion receipt;
atomic progress replacements also fsync their parent directory. On resume, only
outputs passing those checks are skipped. Changed manifests/config/sources or tampered completed
outputs are rejected; failed attempts are retained rather than overwritten.
Progress contains metadata only, never transcript text.

Results live under `/srv/audio-archive/transcription-batches/full-logs-YYYYMMDD/`.
The worker finishes at `transcribed-awaiting-editorial-review`, **not complete**.
Editorial drafts/ledgers, encrypted backup/restore verification and source-identity
checks must follow before deleting only the verified batch-owned staged originals
and PCM files. The worker does not delete source audio or invoke a cloud editor.
Keep the existing compact Opus archive; do not back up staging WAVs by including
the staging parent in restic. Phone originals are never modified.

`life-atlas-audio-batch@.service` is a manual template, not enabled or scheduled.
It runs with protected system/home, private network/devices/tmp, a private empty
Docker client config, low CPU/I/O priority and a 512-MiB supervisor/decoder limit.
The engine has its own separate container limits; Docker daemon access is trusted
host administration, not a claim that the supervisor is a security sandbox.
Stopping the worker requests cleanup; a lost/killed supervisor still leaves the
container-side deadline. No other containers/services are stopped.

```sh
# Only after the exact originals and manifest have been transferred/verified:
ssh aether systemctl start --no-block life-atlas-audio-batch@full-logs-YYYYMMDD.service
# Metadata-only progress (do not dump engine-private.log):
ssh aether 'cat /srv/audio-archive/transcription-batches/full-logs-YYYYMMDD/status.json'
```

`source-manifest.json` schema is `{ "batch_id": "full-logs-YYYYMMDD", "files": [...] }`;
each file has `name`, `size`, `sha256` and `duration_seconds`. Identifiers and source
hashes are runtime evidence, not credentials or guessed values.

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

## Daily output assembly

`daily.py` assembles already-transcribed, explicitly reviewed recordings into
`YYYYMMDD/transcript.md` and `YYYYMMDD/audio.zip`. This component performs no
inference, cloud calls, ingestion, deletion, automatic backfill or scheduling.
It reuses the existing uncertainty-preserving renderer and Python's ZIP support
rather than adding an archive service/dependency. Opus already compresses audio;
ZIP uses stored entries and merely bundles those individual recordings.

The caller supplies an outside-Git, caller-owned mode-0700 input root containing
private raw ASR JSON, source-bound editorial proposals, verified mono Opus files
and a daily manifest. Each artifact reference has `path` (relative to that root)
and `sha256`. Manifest schema:

```json
{
  "schema": 1,
  "day": "20261111",
  "logs": [{
    "source_sha256": "ORIGINAL_AUDIO_SHA256",
    "recorded_at": "2026-11-11T12:30:00+00:00",
    "date_basis": "phone-recording-metadata",
    "raw": {"path": "log/raw.json", "sha256": "RAW_JSON_SHA256"},
    "proposal": {"path": "log/proposal.json", "sha256": "PROPOSAL_SHA256"},
    "opus": {"path": "log/audio.opus", "sha256": "OPUS_SHA256"}
  }]
}
```

The example contains placeholders, not valid hashes. Dates must be offset-aware,
belong to the requested phone-local day, and have an explicit date basis:
`phone-recording-metadata` or `user-confirmed`. This tool does not establish that
metadata's truth; intake must do so. It refuses to silently infer a recording day
from upload time or assume every arrival is from yesterday. No phone-date metadata
capture is implemented yet, especially for timezone changes while travelling.

```sh
python3 -B daily.py --manifest /PRIVATE/input/daily.json \
  --input-root /PRIVATE/input --output-root /PRIVATE/days
```

The output root must already be mode 0700 and outside Git. Recordings are sorted
by recording instant, with source identity as a deterministic tie-break. Source
hashes, private ownership/modes, safe paths, source-bound edits, Opus codec/mono
format/duration and full decoding are checked. ZIP members recover byte-exactly.
Transcript speech remains literal fenced text; no summary is generated. Duplicate
source identities, unverified dates, missing/changed sources, lexical formatting
rewrites, and updates omitting an earlier recording fail closed.

Both daily files and a provenance manifest live in an immutable
`.revisions/YYYYMMDD/INPUT_MANIFEST_HASH/` directory. A single atomic symlink swap
selects a complete revision for the dated folder. **Reader contract:** call
`daily.resolve_day(output_root, day)` once (it verifies both output hashes), then
open both files through that returned immutable revision path. Resolving the daily
symlink separately for each file can mix revisions if a late arrival publishes
between those reads; the symlink alone does not provide a multi-open transaction.
Late logs can publish a new revision without deleting earlier ones. Failed private builds are retained;
existing unrelated directories are never replaced. Neither this assembler nor
its successful return establishes encrypted backup: its receipt explicitly says
`backup_verified: false`. Back up the actual revision tree (not just the symlinks),
prove recovery, and only then apply a separately identity-verified temporary-source
cleanup policy. It never deletes raw audio or changes the existing sync/snapshot
jobs. The assembler alone is not a complete workflow; the separate automation
layer below provides intake, review-queue acceptance and encrypted preservation.

## Approved automatic selected-log workflow

`automation.py` and `life-atlas-audio-intake.{service,timer}` are separate from the
explicit pilot/three-log interfaces. Runtime configuration is root-private at
`/etc/life-atlas-audio/automation.json`: schema 1, enabled boolean, observed phone
`timezone`, descriptive `timezone_basis`, offset-aware `not_before`, and
`excluded_names` for already-archived recordings. The scanner refuses to inspect
incoming files before `not_before`; use that to reserve a real recording for a
natural scheduled overnight test, never manually start it.

Only complete, root-level, supported audio filenames beginning with `log`
(case-insensitive) are accepted. Syncthing both-end rooted first-match allowlists
must be installed BEFORE pointing the phone share at Recorder. Exclude old
already-archived filenames; do not silently backfill. Existing server reception
remains once daily, 01:00–02:00 Europe/London. The intake timer checks arrival
state every minute, not more frequent uploads. Temporary transfer filenames,
symlinks and unsupported/oversized/over-hour files are rejected. Per-source
failures do not starve independent valid files; an unresolved inference launch
intent remains a global fail-closed barrier needing explicit reconciliation.

Preserved phone file save time (mtime), interpreted in the verified configured
phone timezone, determines day/order. It is **not exact speech start**. Timezone
is observed at setup, not embedded historical recording-zone evidence and not
automatically refreshed during travel; capture/update it before relying on a new
travel timezone. In particular, do not claim dynamic phone-local grouping across
travel from this first London overnight test.

The worker hash-verifies a private staging copy, probes duration, verifies pinned
model identity and memory headroom, then uses the existing sequential full-source
ASR/deadline/lock/launch-intent machinery BEFORE Opus compression. It preserves
unchanged raw ASR text/full JSON and receipts in root-private per-content jobs at
`/srv/audio-archive/automation/jobs/SOURCE_SHA256/`. Immutable source identity
skips repeat deliveries; changed content gets a separate job. Ordinary failures
retain evidence and never overwrite accepted output. There is no stronger-model
retry or external audio API.

The managed laptop's text-only Pi bridge uses these machine-facing commands:

```sh
python3 -B automation.py pending
python3 -B automation.py packet SOURCE_SHA256  # PRIVATE text: capture, never print in chat
python3 -B automation.py accept SOURCE_SHA256  # private JSON proposal/receipt on stdin
python3 -B automation.py finish SOURCE_SHA256
```

`pending` exposes identities/status only. `packet` is private raw transcript text.
`accept` requires the approved exact provider/model, packet/source/proposal hashes,
and successful source-bound edit rendering. A deterministic rejected proposal is
explicitly identified so the bridge can use bounded regeneration; transport errors
must reuse rather than silently rerun the model. No summaries or life analysis.

Finalization assembles `/srv/audio-archive/daily/YYYYMMDD/{transcript.md,audio.zip}`
with old revisions retained. It backs up actual daily revision directories and
compact jobs (raw ASR, edited draft, edit ledger, model/codec/source receipts and
Opus), never full incoming/staging WAVs. Every retained current-job/revision file
is byte-exactly recovered from that snapshot and restic full read-data checking
passes before deleting only exact worker-owned staged source/PCM. A completion
receipt outside that snapshot binds its ID; subsequent backups preserve it.
Late arrivals publish another same-day revision without dropping existing logs.

**Incoming Syncthing originals are deliberately retained** to avoid an unproven
receive-only deletion/reupload strategy. Phone/Downloads originals remain intact.
Disable the legacy incoming-raw snapshot timer when enabling this compact-payload
preservation path; keep the independent upload-window timer unchanged. Intake and
backup require at least 10 GB free, with no automatic history pruning. Incoming
raw retention still needs a later no-reupload retention policy; this test does
not solve long-term server raw growth or independent off-server backup.

The laptop bridge waits while asleep/offline or logged out, and retries pending
work when available; it does not force wakeup. Server intake runs independently.
Content-free service health is at `automation/health/`; completion receipts carry
exact snapshots/revisions. A timer being active is not proof a real recording
completed: verify source identity, recorded job phases, published files and
recovered snapshot before claiming success. Preserve unrelated WhatsApp/GPS sync.

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

These initial measurements predate the separately approved automation above.
Stronger-model selection, historical/dynamic travel timezone capture, incoming
raw-retention policy and independent off-server backup remain separate work.
