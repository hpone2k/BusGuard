"""Install the small official pose checkpoint used by indoor seating estimates."""
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parent.parent
request = Request('https://api.github.com/repos/ultralytics/assets/releases/latest',
                  headers={'User-Agent': 'BusTech-local-prototype'})
with urlopen(request, timeout=30) as response:
    release = json.load(response)
asset = next(item for item in release['assets'] if item['name'] == 'yolo26n-pose.pt')
url = asset['browser_download_url']
if not url.startswith('https://github.com/ultralytics/assets/releases/download/'):
    raise RuntimeError('Unexpected model download source')
with urlopen(url, timeout=45) as response:
    data = response.read()
digest = hashlib.sha256(data).hexdigest()
if len(data) != asset['size'] or asset.get('digest') and asset['digest'] != 'sha256:' + digest:
    raise RuntimeError('Checkpoint integrity verification failed')
folder = root / 'models'
folder.mkdir(exist_ok=True)
destination = folder / asset['name']
destination.write_bytes(data)
metadata = {'release': release['tag_name'], 'url': url, 'sha256': digest, 'size': len(data)}
(folder / 'pose-source.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
print(json.dumps({'path': str(destination), **metadata}, indent=2))
