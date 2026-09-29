"""Passenger website: an allowlisted same-origin proxy to the shared controller."""
import asyncio
import hashlib
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request as URLRequest, build_opener

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT
from .network import trusted_hosts
from .voice import voice_proxy_router


def create_passenger_app(api_port=4479, allowed_hosts=None):
    if not isinstance(api_port, int) or not 1 <= api_port <= 65535:
        raise ValueError('Invalid controller port.')
    app = FastAPI(title='BusTech Passenger', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or trusted_hosts())
    app.include_router(voice_proxy_router(api_port, allow_sessions=True))

    @app.middleware('http')
    async def headers(request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store' if request.url.path.startswith('/api/') else 'no-cache',
                                 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @app.get('/')
    @app.get('/passenger')
    @app.get('/passenger/')
    async def index():
        return FileResponse(ROOT / 'static' / 'passenger' / 'index.html')

    @app.get('/setup')
    @app.get('/setup/')
    @app.get('/setup/phone')
    async def phone_setup():
        return FileResponse(ROOT / 'static' / 'passenger' / 'phone-setup.html')

    def public_certificate():
        # This one public CA is downloadable. Never expose a directory, accept
        # a requested filename, or serve the private server key beside it.
        path = ROOT / 'data' / 'tls' / 'BusGuard-demo-root.cer'
        try:
            with path.open('rb') as handle:
                content = handle.read(16385)
            return content if 0 < len(content) <= 16384 else None
        except OSError:
            return None

    @app.get('/api/phone-setup')
    def phone_setup_information():
        certificate = public_certificate()
        return {'certificate_available': certificate is not None,
                'fingerprint_sha256': hashlib.sha256(certificate).hexdigest().upper() if certificate is not None else None}

    @app.get('/setup/certificate.cer')
    def phone_certificate():
        certificate = public_certificate()
        if certificate is None:
            return JSONResponse({'detail': 'The public demo certificate is not available. Ask the host operator to prepare phone HTTPS.'}, status_code=404)
        return Response(certificate, media_type='application/pkix-cert',
                        headers={'Content-Disposition': 'attachment; filename="BusGuard-demo-root.cer"'})

    @app.api_route('/api/assistance/{path:path}', methods=['GET', 'POST'])
    async def forward(path: str, request: Request):
        read = request.method == 'GET' and (path in {'config', 'state'} or re.fullmatch(r'requests/[a-zA-Z0-9_-]{1,80}', path))
        write = request.method == 'POST' and (path == 'requests' or re.fullmatch(r'requests/[a-zA-Z0-9_-]{1,80}/(complete|cancel)', path))
        if not (read or write):
            return JSONResponse({'detail': 'Endpoint not available on the passenger website.'}, status_code=404)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                return JSONResponse({'detail': 'Request is too large.'}, status_code=413)
        token = request.headers.get('x-request-token', '')
        if len(token) > 256:
            return JSONResponse({'detail': 'Invalid request token.'}, status_code=400)

        def send():
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Request-Token'] = token
            upstream = URLRequest(f'http://127.0.0.1:{api_port}/api/assistance/{path}',
                                  data=bytes(body) if write else None, headers=headers, method=request.method)
            try:
                result = build_opener(ProxyHandler({})).open(upstream, timeout=4)
            except HTTPError as error:
                result = error
            with result:
                content = result.read(1_000_001)
                if len(content) > 1_000_000:
                    raise ValueError('Response too large.')
                return Response(content, status_code=result.status, media_type='application/json')

        try:
            return await asyncio.to_thread(send)
        except Exception:
            return JSONResponse({'detail': 'The local bus server is offline. Your request has not been confirmed.'}, status_code=503)

    app.mount('/static/passenger', StaticFiles(directory=ROOT / 'static' / 'passenger', check_dir=False), name='passenger-assets')
    return app
