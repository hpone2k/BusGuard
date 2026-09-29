import pytest
import socket
import sys
from types import SimpleNamespace
from fastapi import HTTPException
from starlette.requests import Request

from vision.network import OperatorAccess, local_addresses, public_api, trusted_hosts


def request(address='192.168.20.25', cookie='', host='testserver'):
    headers = [(b'host', host.encode())]
    if cookie:
        headers.append((b'cookie', cookie.encode()))
    return Request(dict(type='http', method='GET', scheme='http', path='/api/access',
                        query_string=b'', headers=headers, client=(address, 32100),
                        server=(host, 4479)))


def test_only_loopback_clients_are_implicitly_operators():
    access = OperatorAccess(lan=True)
    assert access.authorized(request('127.0.0.1'))
    assert access.authorized(request('::1'))
    assert not access.authorized(request())
    assert not access.authorized(request('8.8.8.8'))
    assert not access.authorized(request('localhost'))


def test_local_pairing_code_never_exposed_to_remote_operator():
    access = OperatorAccess(lan=True)
    assert access.status(request('127.0.0.1'))['pairing_code'] == access.pin
    assert access.status(request())['pairing_code'] is None
    token = access.pair(request(), access.pin)
    remote = request(cookie=f'bustech_operator={token}')
    assert access.status(remote)['operator']
    assert access.status(remote)['pairing_code'] is None


def test_pairing_uses_cookie_expiry_and_rejects_forged_tokens(monkeypatch):
    now = [1000.]
    monkeypatch.setattr('vision.network.time.monotonic', lambda: now[0])
    access = OperatorAccess(lan=True)
    token = access.pair(request(), access.pin)
    paired = request(cookie=f'bustech_operator={token}')
    assert access.authorized(paired)
    assert not access.authorized(request(cookie='bustech_operator=forged'))
    now[0] += 12 * 60 * 60 + 1
    assert not access.authorized(paired)


def test_pairing_attempts_are_limited_per_device_and_expire(monkeypatch):
    now = [1000.]
    monkeypatch.setattr('vision.network.time.monotonic', lambda: now[0])
    access = OperatorAccess(lan=True)
    wrong = '999999' if access.pin != '999999' else '888888'
    for _ in range(5):
        with pytest.raises(HTTPException) as failed:
            access.pair(request(), wrong)
        assert failed.value.status_code == 403
    with pytest.raises(HTTPException) as limited:
        access.pair(request(), access.pin)
    assert limited.value.status_code == 429
    assert access.pair(request('192.168.20.26'), access.pin)
    now[0] += 61
    assert access.pair(request(), access.pin)


def test_pairing_tables_stay_bounded():
    access = OperatorAccess(lan=True)
    for index in range(300):
        access.pair(request(f'10.0.{index // 250}.{index % 250 + 1}'), access.pin)
    assert len(access.sessions) == 64
    assert len(access.attempts) == 256


def test_hosts_are_explicit_and_discovery_excludes_loopback_and_public(monkeypatch):
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(net_if_stats=lambda: {}, net_if_addrs=lambda: {}))
    def addresses(*_):
        return [(None, None, None, None, (ip, 0)) for ip in
                ['127.0.0.1', '192.168.20.15', '8.8.8.8', '169.254.10.1', '192.168.20.15']]
    monkeypatch.setattr('vision.network.socket.getaddrinfo', addresses)
    monkeypatch.setattr('vision.network.socket.gethostname', lambda: 'demo-laptop')
    assert local_addresses() == ['192.168.20.15']
    assert 'demo-laptop' in trusted_hosts(True)
    assert '192.168.20.15' in trusted_hosts(True)
    assert '192.168.20.15' not in trusted_hosts(False)
    assert '*' not in trusted_hosts(True)


def test_network_discovery_prefers_wifi_and_excludes_inactive_interfaces(monkeypatch):
    entries = {'Wi-Fi': [SimpleNamespace(family=socket.AF_INET, address='192.168.20.15')],
               'Ethernet': [SimpleNamespace(family=socket.AF_INET, address='10.0.0.8')],
               'Disconnected': [SimpleNamespace(family=socket.AF_INET, address='192.168.30.9')]}
    statuses = {name: SimpleNamespace(isup=name != 'Disconnected') for name in entries}
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(net_if_stats=lambda: statuses,
                                                              net_if_addrs=lambda: entries))
    monkeypatch.setattr('vision.network.socket.getaddrinfo', lambda *_: [])
    assert local_addresses() == ['192.168.20.15', '10.0.0.8']


@pytest.mark.parametrize('path', ['/api/bus/cameras', '/api/bus/cameras/inside/preview.jpg',
                                 '/api/sessions', '/api/videos', '/api/bus/rfid',
                                 '/api/assistance/control', '/api/bus/sources'])
def test_sensitive_routes_are_not_public(path):
    assert not public_api(path)


@pytest.mark.parametrize('path', ['/api/assistance/config', '/api/assistance/state',
                                 '/api/assistance/requests', '/api/assistance/requests/abc/complete',
                                 '/api/bus/state', '/api/access'])
def test_passenger_routes_are_public_but_ownership_is_enforced_by_controller(path):
    assert public_api(path)
