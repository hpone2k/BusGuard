const status = document.getElementById('setupStatus');
const voiceUrl = new URL(window.location.href);
voiceUrl.protocol = 'https:';
voiceUrl.port = '4482';
voiceUrl.pathname = '/';
voiceUrl.search = voiceUrl.hash = '';
document.getElementById('openVoice').href = voiceUrl.href;
document.getElementById('voiceAddress').textContent = voiceUrl.href;
document.getElementById('localNote').hidden = !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

const abort = new AbortController();
const timeout = setTimeout(() => abort.abort(), 5000);
try {
  const response = await fetch('/api/phone-setup', {cache: 'no-store', signal: abort.signal});
  if (!response.ok) throw new Error('Setup unavailable');
  const setup = await response.json();
  if (setup.certificate_available === true && /^[A-F0-9]{64}$/.test(setup.fingerprint_sha256)) {
    document.getElementById('certificateFingerprint').textContent = setup.fingerprint_sha256.match(/.{1,4}/g).join(' ');
    document.getElementById('fingerprintPanel').hidden = false;
    document.getElementById('downloadCertificate').hidden = false;
    document.getElementById('openVoice').hidden = false;
    status.textContent = 'Public certificate ready · follow the steps below';
    status.dataset.state = 'ready';
  } else {
    status.textContent = 'Ask the operator to prepare the phone certificate on the bus computer.';
    status.dataset.state = 'missing';
  }
} catch {
  status.textContent = 'Cannot check the setup. Reconnect to the bus Wi-Fi and reload this page.';
  status.dataset.state = 'missing';
} finally {
  clearTimeout(timeout);
}
