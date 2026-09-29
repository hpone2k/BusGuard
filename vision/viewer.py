"""Independent, read-only bus website backed by the single detection service.

Only a small JSON state snapshot crosses the loopback connection. No second
detector is loaded, and the viewer never receives camera credentials or frames.
"""
import asyncio
import json
from urllib.request import ProxyHandler, build_opener

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT
from .network import trusted_hosts
from .voice import voice_proxy_router


def create_viewer_app(api_port=4479, fetch_state=None, allowed_hosts=None):
    if not isinstance(api_port, int) or not 1 <= api_port <= 65535:
        raise ValueError('The detector port must be between 1 and 65535.')
    endpoint = f'http://127.0.0.1:{api_port}/api/bus/state'

    def read_state():
        # Ignore system HTTP proxies: passenger state never leaves loopback.
        with build_opener(ProxyHandler({})).open(endpoint, timeout=2) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError('State response is too large.')
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get('sources'), dict):
            raise ValueError('Invalid detection state.')
        return data

    fetch = fetch_state or read_state
    app = FastAPI(title='BusTech Bus Viewer', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or trusted_hosts())
    app.include_router(voice_proxy_router(api_port))

    @app.middleware('http')
    async def secure_response(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'")
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        else:
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/')
    @app.get('/bus')
    @app.get('/bus/')
    async def index():
        return FileResponse(ROOT / 'static' / 'bus' / 'index.html')

    @app.get('/api/site')
    async def site():
        return {'api_port': api_port}

    @app.get('/api/bus/state')
    async def state():
        try:
            return await asyncio.to_thread(fetch)
        except Exception:
            # Never serve a cached observation as current after detection stops.
            return JSONResponse({'detail': 'The detection console is offline. Passenger data is unavailable.'},
                                status_code=503)

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app
