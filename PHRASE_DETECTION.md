# Detailed phrase detection — version 4.1

This document records the version 4.1 phrase implementation and measurements on the original development host. Its pose-model references and test totals are historical, not a description of the current standing-only pipeline or a new accuracy result. Use [Setup and operations](docs/SETUP_AND_OPERATIONS.md) for a fresh clone and [System workflow](docs/SYSTEM_WORKFLOW.md) for current bus behavior.

The Detection Console runs at http://localhost:4479 and the separate bus viewer
at http://localhost:4480. In either input card, open **Detection settings** and
select **Detailed phrases**. Enter one description per line, for example:

```text
a person with a red shirt
a person carrying a backpack
a person using a wheelchair
```

These are example inputs, not guarantees that every appearance of these targets
will be recognized. Each description has an independent match threshold. Start
at 0.30 and 512 px; higher thresholds reject weaker matches but can miss more
targets. Use 640 px when small visible details justify additional inference time.
Semicolons also separate descriptions. Commas stay inside a description. A source
accepts at most eight distinct descriptions, 160 characters each, and a combined
256-token model budget. Oversized input produces a validation error instead of
silently dropping targets. Object mode retains comma-separated categories.

## What changed

- **Separate models for separate jobs.** The default `hybrid` backend routes
  object categories to YOLOE-26m and detailed phrases to Grounding DINO Tiny.
  Both stay loaded; a frame uses only its selected detector. All requested
  phrases share one image forward pass rather than one pass per description.
- **Exact phrase ownership.** Token offsets map output evidence to the complete
  requested label. A geometric mean of content-word scores and a weak-word guard
  reduce generic-noun-only matches. These scores are uncalibrated similarities,
  not the probability that the whole sentence is true.
- **Clothing and subject association.** Simple person/colour/garment expressions
  use an equivalent compact internal query. A shared person query in the same
  image pass associates garment predictions to a unique containing person.
  Ambiguous associations are rejected. Original descriptions remain the output
  labels. Explicit simple colours receive a CPU colour-presence veto; this can
  reject a mismatch but never create a detection or increase its score.
- **Bounded work.** Text tensors use an eight-entry cache. The existing single
  GPU worker and one request in flight per source avoid a frame backlog. A
  numerical failure in mixed precision triggers a full-precision retry.
- **Aligned previews.** Browser phrase video/camera results show the captured
  frame and its observed boxes together. One completed frame is retained and
  expires at 1.5 seconds of capture age. Source switches, seeks and stops discard
  old frames. Preview age remains visible. Object-mode live preview and RTSP
  latest-frame capture retain their existing behavior.
- **Independent settings.** Object and phrase prompts, thresholds and resolution
  choices survive mode switches. The two input cards retain independent state.

## Installation and startup

The original development host had the dependencies and official checkpoint installed.
A source checkout does not include them. After completing setup, start with
`start.cmd` or `start.ps1`; the default is `--backend hybrid`.

For a fresh clone, follow [Setup and operations](docs/SETUP_AND_OPERATIONS.md), then:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\setup_grounding.py
.\.venv\Scripts\python.exe run.py
```

The download script pins the official Grounding DINO Tiny revision
`a2bb814dd30d776dcf7e30523b00659f4f141c71`, verifies its safetensors SHA-256,
and stores tokenizer/configuration files under `models/grounding-dino-tiny`.
It does not download executable repository Python. Model weights and camera
credentials are excluded from the source archive. A missing checkpoint produces
an explicit setup instruction; it does not silently select a weaker detector.

`--backend yoloe` remains available for the earlier object detector and limited
single clothing-colour heuristic. `--phrase-model DIRECTORY` overrides the local
phrase checkpoint in hybrid mode.

## Bus data and limits

One person may satisfy several descriptions. Phrase counts overlap and are not
a unique passenger census. Keep the indoor input in **Object categories** with
`person` for occupancy and seating evidence. Phrase mode skips the unnecessary
pose-model pass and explicitly marks full-cabin posture evidence unavailable;
it cannot release the departure interlock.

Complex relations, subtle attributes, occlusion, lighting and dense groups remain
challenging. A colour-presence check can still see nearby clothing/background
colours and is not a semantic guarantee. Descriptions can influence one another
inside a batch. Unsupported conclusions such as age or pregnancy must not be
inferred from these matches. RFID senior assistance remains a separate input.

The existing one-second count vote reduces count flicker but does not correct
systematically wrong labels. GPU timing excludes the camera's encoder, RTSP
transport and preview delivery; two sources share the GPU. This is a prototype
with virtual bus actions, not a validated physical vehicle control system.

## Reproducible checks

Historically measured on the development host's RTX 4060 Laptop GPU with YOLOE, Grounding DINO and
the pose model resident, using ten warm repeats and a 0.30 match threshold:

| Short edge | 1 phrase | 3 phrases | 8 phrases |
|---|---:|---:|---:|
| 512 px | 154 ms | 158 ms | 157 ms |
| 640 px | 200 ms | 199 ms | 204 ms |

Peak allocated GPU memory was about 1.44 GiB. These are model-pipeline timings,
not full camera-to-screen latency. On the bundled photograph, three requested
coat/bus targets had correct full-object boxes. The red/blue/yellow-shirt test
returned only the visible red-shirted person. The default red-shirt/backpack/
wheelchair test also returned that person without false backpack or wheelchair
matches.

**The eight-description stress test still missed several visible targets.** At
512 px it returned the bus and beige-coat person; at 640 px it returned the bus.
Neither returned the absent red bus or wheelchair. More resolution alone did
not fix phrase competition. Prefer a few clear, distinct descriptions per camera
and validate their exact combination on actual footage. No general accuracy
percentage is established by this small diagnostic.

The running HTTP service was also checked with three phrases: one source had a
200 ms median round trip; two simultaneous sources had a 263 ms median and
360 ms maximum over twelve sample requests. This short local test excludes a
physical camera's own delay and is not a long-running load guarantee. The first
use of a new prompt or image shape may take longer than warm inference.

```powershell
# Stop the server and other GPU jobs before the standalone model benchmark.
.\.venv\Scripts\python.exe scripts\benchmark_phrases.py
# With the hybrid service running, exercise real sample requests and two sources.
.\.venv\Scripts\python.exe scripts\validate_phrases.py
.\.venv\Scripts\python.exe -m pytest -q
npm test
```

The benchmark records 1, 3 and 8 phrases on the bundled public street photograph,
per-phrase outputs and full-object box checks. It is a diagnostic example, not a
representative accuracy dataset. Runtime outputs are written to `validation/`.
Use labelled footage from both installed CCTVs to measure missed targets and
false matches before choosing deployment thresholds.

Delivery verification: 168 Python tests and 86 frontend tests passed, and the
installed environment passed `pip check`. Browser checks covered a real sample
prediction, separate thresholds, mode restoration, phrase-limit validation,
cached-image labeling and keeping unapplied drafts out of existing results.

Model references: [official model](https://huggingface.co/IDEA-Research/grounding-dino-tiny),
[Transformers implementation](https://huggingface.co/docs/transformers/v4.57.1/model_doc/grounding-dino).
