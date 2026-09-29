"""Run locally in an interactive terminal. Never paste an API key into chat or a command argument."""
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def windows_private_principals():
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                             '-File', str(ROOT / 'scripts' / 'private_file_principals.ps1'),
                             '-WorkspacePath', str(ROOT)], capture_output=True, text=True, check=True)
    principals = json.loads(result.stdout)
    if (not isinstance(principals, list) or not 1 <= len(principals) <= 2
            or any(not isinstance(sid, str) or not re.fullmatch(r'S-1-(?:5-21|12-1)-(?:\d+-){3}\d+', sid) for sid in principals)):
        raise OSError('Private desktop identity could not be resolved; run setup in your own Windows terminal.')
    return principals


def save_private_key(key, voice='marin', data_dir=None):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,1024}', key) or voice not in {'marin', 'cedar'}:
        raise ValueError('Use a valid OpenAI API key and choose marin or cedar.')
    folder = Path(data_dir) if data_dir else ROOT / 'data'
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='voice-', suffix='.tmp', dir=folder)
    os.close(descriptor)
    path = Path(name)
    try:
        if os.name == 'nt':
            # Restrict the empty file before any credential is written into it.
            # Normal desktop runs use that user alone. A sandbox run also grants
            # the enclosing desktop profile owner, retaining local service access.
            principals = windows_private_principals()
            result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                                     *(f'*{sid}:(F)' for sid in principals)],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                raise OSError('Private file permissions could not be applied; no key was saved.')
        else:
            path.chmod(0o600)
        path.write_text(f'OPENAI_API_KEY={key}\nBUSGUARD_VOICE={voice}\n', encoding='utf-8')
        path.replace(folder / 'voice.env')
    finally:
        path.unlink(missing_ok=True)


def main():
    if not sys.stdin.isatty():
        raise SystemExit('Run this setup in a local interactive terminal so the key can be entered without echo.')
    print('BusGuard private voice setup. OpenAI API billing is separate from ChatGPT.')
    print('The key stays in data/voice.env on this host; it is excluded from source archives.')
    key = getpass.getpass('OpenAI API key (hidden): ').strip()
    voice = input('Voice: marin or cedar [marin]: ').strip().lower() or 'marin'
    try:
        save_private_key(key, voice)
    except Exception:
        raise SystemExit('Setup could not save the key. Check the key format, voice choice and local file permissions.') from None
    print('Private voice configuration saved. Reload the passenger page; no server restart is needed.')


if __name__ == '__main__':
    main()
