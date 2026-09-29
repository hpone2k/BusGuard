import hashlib

from fastapi.testclient import TestClient

from vision.passenger_site import create_passenger_app


def test_setup_page_and_assets_work_under_existing_content_policy():
    client = TestClient(create_passenger_app())
    page = client.get('/setup')
    assert page.status_code == 200
    assert 'Choose to trust it' in page.text
    assert 'phone-setup.js' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert client.get('/setup/phone').status_code == 200
    assert client.get('/static/passenger/phone-setup.css').status_code == 200
    assert client.get('/static/passenger/phone-setup.js').status_code == 200


def test_certificate_endpoint_serves_only_fixed_public_file(tmp_path, monkeypatch):
    monkeypatch.setattr('vision.passenger_site.ROOT', tmp_path)
    folder = tmp_path / 'data' / 'tls'
    folder.mkdir(parents=True)
    certificate = b'\x30\x82example-public-DER-certificate'
    (folder / 'BusGuard-demo-root.cer').write_bytes(certificate)
    (folder / 'passenger-key.pem').write_text('PRIVATE-KEY-MUST-NEVER-BE-SERVED')
    client = TestClient(create_passenger_app())
    response = client.get('/setup/certificate.cer?filename=passenger-key.pem')
    assert response.status_code == 200
    assert response.content == certificate
    assert response.headers['content-type'] == 'application/pkix-cert'
    assert response.headers['content-disposition'] == 'attachment; filename="BusGuard-demo-root.cer"'
    information = client.get('/api/phone-setup')
    assert information.json() == {'certificate_available': True,
                                  'fingerprint_sha256': hashlib.sha256(certificate).hexdigest().upper()}
    assert information.headers['cache-control'] == 'no-store'
    for path in ['/setup/passenger-key.pem', '/data/tls/passenger-key.pem', '/setup/..%2fdata%2ftls%2fpassenger-key.pem']:
        assert client.get(path).status_code == 404


def test_missing_or_oversized_certificate_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr('vision.passenger_site.ROOT', tmp_path)
    client = TestClient(create_passenger_app())
    for content in [None, b'', b'x' * 16385]:
        if content is not None:
            folder = tmp_path / 'data' / 'tls'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'BusGuard-demo-root.cer').write_bytes(content)
        response = client.get('/setup/certificate.cer')
        assert response.status_code == 404
        assert client.get('/api/phone-setup').json() == {'certificate_available': False, 'fingerprint_sha256': None}
        assert str(tmp_path) not in response.text
