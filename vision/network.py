"""Local network discovery and bounded operator pairing for the LAN demo."""
import ipaddress
import secrets
import socket
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request


def local_addresses():
    found = set()
    preferred = set()
    try:
        import psutil
        stats = psutil.net_if_stats()
        for name, entries in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for item in entries:
                if item.family != socket.AF_INET:
                    continue
                address = ipaddress.ip_address(item.address)
                if address.is_private and not address.is_loopback and not address.is_link_local:
                    found.add(str(address))
                    if 'wi-fi' in name.lower() or 'wlan' in name.lower():
                        preferred.add(str(address))
    except (ImportError, OSError):
        pass
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = ipaddress.ip_address(item[4][0])
            if address.is_private and not address.is_loopback and not address.is_link_local:
                found.add(str(address))
    except OSError:
        pass
    return sorted(found, key=lambda value: (value not in preferred, value))


def trusted_hosts(lan=False):
    hosts = ['localhost', '127.0.0.1', '[::1]', 'testserver']
    if lan:
        hosts += [socket.gethostname(), *local_addresses()]
    return hosts


class OperatorAccess:
    def __init__(self, lan=False, api_port=4479, viewer_port=4480, passenger_port=4481):
        self.lan, self.ports = lan, (api_port, viewer_port, passenger_port)
        self.pin = f'{secrets.randbelow(1_000_000):06d}'
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def local(request):
        host = request.client.host if request.client else ''
        if host == 'testclient':  # Starlette's in-process test transport.
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def authorized(self, request):
        if self.local(request):
            return True
        token = request.cookies.get('bustech_operator', '')
        with self.lock:
            return self.sessions.get(token, 0) > time.monotonic()

    def require(self, request: Request):
        if not self.authorized(request):
            raise HTTPException(403, 'Pair this device using the code on the host computer’s detection console.')

    def pair(self, request, code):
        host, now = request.client.host, time.monotonic()
        with self.lock:
            attempts = self.attempts.setdefault(host, deque(maxlen=6))
            while attempts and now - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(429, 'Too many pairing attempts. Try again in one minute.')
            attempts.append(now)
            if len(self.attempts) > 256:
                self.attempts.popitem(last=False)
            if not secrets.compare_digest(str(code), self.pin):
                raise HTTPException(403, 'That pairing code did not match.')
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 12 * 60 * 60
            if len(self.sessions) > 64:
                self.sessions.popitem(last=False)
            return token

    def status(self, request):
        api, viewer, passenger = self.ports
        addresses = local_addresses() if self.lan else []
        hostname = request.url.hostname or 'localhost'
        addresses = addresses or [hostname]
        return {
            'operator': self.authorized(request), 'local': self.local(request), 'lan_enabled': self.lan,
            'pairing_code': self.pin if self.local(request) and self.lan else None,
            'links': [{'host': host, 'detection': f'http://{host}:{api}/',
                       'bus': f'http://{host}:{viewer}/' if viewer else f'http://{host}:{api}/bus',
                       'passenger': f'http://{host}:{passenger}/' if passenger else f'http://{host}:{api}/passenger/'}
                      for host in addresses],
        }


def public_api(path):
    return (path in {'/api/status', '/api/site', '/api/access', '/api/access/pair', '/api/bus/state',
                     '/api/assistance/config', '/api/assistance/state', '/api/voice/config',
                     '/api/voice/session', '/api/voice/session/close', '/api/voice/speech'}
            or path == '/api/assistance/requests' or path.startswith('/api/assistance/requests/'))
