"""Report cached demo announcements; --generate explicitly enables paid preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.route import STOPS
from vision.scenario import StopScenario
from vision.voice import PREVIEW, SPEECH_INSTRUCTIONS, SPEECH_MODEL, VOICES, VoiceService


# A fixed, finite catalog. The CLI never accepts arbitrary text for paid speech.
ANNOUNCEMENTS = (
    *(f"Approaching {stop['name']}. The bus is slowing down. Please remain seated until the doors open." for stop in STOPS),
    *(f"The bus has arrived at {stop['name']}. Please wait for the doors to open." for stop in STOPS),
    *(f"Your bus has arrived at {stop['name']}. Please wait for the doors to open, then request the assistance you need." for stop in STOPS),
    'The doors are open for boarding and alighting. Please board or exit safely.',
    StopScenario.WARNING,
    'Boarding has ended. The doors are closing. Please remain seated.',
    'The bus is departing. Please remain seated. Thank you.',
    'Please take a seat. Standing posture was observed.',
    StopScenario.FULL,
    PREVIEW,
    'The doors are closed. Please remain seated while the final departure checks are completed.',
    'Boarding activity continues. Departure has been delayed. Please remain seated.',
    StopScenario.YIELD,
    'This is a test arrival alert. Your bus location has not changed.',
)
MIN_REQUEST_INTERVAL = 2.1  # At most 29 starts in any rolling minute in this process.


def cache_entry(data_dir, text, voice):
    # Keep the identity exactly aligned with VoiceService.speech; tests cover reuse.
    digest = hashlib.sha256(json.dumps([text, voice, SPEECH_MODEL, SPEECH_INSTRUCTIONS]).encode()).hexdigest()
    return digest, Path(data_dir) / 'voice-cache' / (digest + '.mp3')


def cached(path):
    try:
        return path.is_file() and 0 < path.stat().st_size <= 2_000_000
    except OSError:
        return False


def prepare(service, *, generate=False, voice=None, report=print, clock=time.monotonic, sleep=time.sleep):
    selected = service._voice(voice)
    entries = [(*cache_entry(service.data_dir, text, selected), text) for text in ANNOUNCEMENTS]
    missing = [(digest, path, text) for digest, path, text in entries if not cached(path)]
    counts = dict(total=len(entries), cached=len(entries) - len(missing), generated=0, failed=0)
    report(f"Announcements: {counts['total']}; cached: {counts['cached']}; missing: {len(missing)}.")
    if not generate:
        report('Report only. No API requests made. Add --generate to prepare missing clips using the configured API account.')
        return counts
    if not missing:
        report('All announcements are cached. No API requests needed.')
        return counts
    # Load the key only through the existing private configuration provider. Never
    # expose it in CLI arguments, environment dumps, progress or error messages.
    key = service._key()
    last_started = None
    for digest, path, text in missing:
        if cached(path):
            counts['cached'] += 1
            continue
        if last_started is not None:
            sleep(max(0., MIN_REQUEST_INTERVAL - (clock() - last_started)))
        last_started = clock()
        service._prepare_speech(digest, text, selected, key, path.parent, path)
        if not cached(path):
            counts['failed'] += 1
            report('Preparation stopped after one failed clip. Check host connectivity, API access and billing before retrying.')
            break
        counts['generated'] += 1
        report(f"Prepared: {counts['generated']}; already cached: {counts['cached']}; failed: {counts['failed']}.")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generate', action='store_true', help='Explicitly enable paid API calls for missing clips only.')
    parser.add_argument('--voice', choices=VOICES, help='Use this voice; otherwise use the private host setting.')
    args = parser.parse_args(argv)
    try:
        result = prepare(VoiceService(ROOT / 'data'), generate=args.generate, voice=args.voice)
    except KeyboardInterrupt:
        print('Preparation cancelled. Previously cached clips are retained.')
        return 130
    except Exception:
        print('Preparation unavailable. Check private voice configuration, connectivity and local cache permissions.')
        return 1
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
