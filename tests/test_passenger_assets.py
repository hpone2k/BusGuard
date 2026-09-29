"""The passenger site must serve its complete browser module graph on its own port."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from fastapi.testclient import TestClient

from vision.passenger_site import create_passenger_app


class ModuleScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'script' and values.get('type') == 'module' and values.get('src'):
            self.sources.append(values['src'])


def test_passenger_entry_and_transitive_modules_load_on_passenger_origin():
    client = TestClient(create_passenger_app(), base_url='http://testserver:4481')
    page = client.get('/')
    assert page.status_code == 200
    parser = ModuleScripts()
    parser.feed(page.text)
    assert parser.sources, 'Passenger entry page needs a module script.'
    pending = [urljoin(str(page.url), source) for source in parser.sources]
    visited = set()
    # These files use static ES module imports. Follow the URLs as a browser does,
    # rather than reading from disk, so the passenger site's asset allowlist is tested.
    imports = re.compile(r'''\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?['"]([^'"]+)['"]''')
    while pending:
        url = pending.pop()
        if url in visited:
            continue
        visited.add(url)
        assert urlsplit(url).netloc == 'testserver:4481', f'Unexpected module origin: {url}'
        response = client.get(url)
        assert response.status_code == 200, f'Passenger module is not served: {url}'
        assert 'javascript' in response.headers['content-type'], f'Not a JavaScript response: {url}'
        for source in imports.findall(response.text):
            assert source.startswith(('.', '/')), f'Unresolved bare module: {source}'
            pending.append(urljoin(url, source))
    assert any(urlsplit(url).path.endswith('/departure-notice.js') for url in visited)
    # The fix must not expose operator assets on the passenger port.
    assert client.get('/static/operations.js').status_code == 404
