import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


def helper():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'setup_voice.py'))


def test_private_identity_resolver_uses_workspace_and_returns_explicit_sids(monkeypatch):
    calls = []
    expected = ['S-1-5-21-1-2-3-1001', 'S-1-5-21-1-2-3-1010']
    def run(command, **options):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(expected))
    monkeypatch.setattr('subprocess.run', run)
    namespace = helper()
    assert namespace['windows_private_principals']() == expected
    assert '-WorkspacePath' in calls[0]
    assert calls[0][-1] == str(namespace['ROOT'])
    assert 'private_file_principals.ps1' in calls[0][5]


@pytest.mark.parametrize('response', ['"Everyone"', '[]', '["Users"]', '["S-1-1-0"]', '["S-1-5-32-545"]',
                                     '["S-1-5-21-1-2-3-1001", "Everyone"]'])
def test_private_identity_resolver_fails_closed_on_unbounded_or_malformed_grants(monkeypatch, response):
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: SimpleNamespace(stdout=response))
    with pytest.raises(OSError):
        helper()['windows_private_principals']()


@pytest.mark.skipif(os.name != 'nt', reason='Windows file ACL workflow')
def test_key_is_not_written_if_identity_lookup_fails(tmp_path, monkeypatch):
    namespace = helper()
    def fail(*args, **kwargs):
        raise OSError('Desktop profile unavailable')
    # Exercise the Windows path without changing any real file permissions.
    monkeypatch.setattr('subprocess.run', fail)
    with pytest.raises(OSError):
        namespace['save_private_key']('sk-test-only-not-a-real-credential', data_dir=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows file ACL workflow')
def test_sandbox_save_grants_exact_desktop_and_service_sids_without_real_acl_changes(tmp_path, monkeypatch):
    calls = []
    expected = ['S-1-5-21-1-2-3-1001', 'S-1-5-21-1-2-3-1010']
    def run(command, **options):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(expected), returncode=0)
    monkeypatch.setattr('subprocess.run', run)
    helper()['save_private_key']('sk-test-only-not-a-real-credential', data_dir=tmp_path)
    assert calls[1][0] == 'icacls'
    assert calls[1][2:] == ['/inheritance:r', '/grant:r', *(f'*{sid}:(F)' for sid in expected)]
    assert (tmp_path / 'voice.env').is_file()
    assert not list(tmp_path.glob('voice-*.tmp'))
