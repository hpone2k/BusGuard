"""Install a checksum-pinned Google Pose Landmarker model for local CPU use."""
import argparse
from hashlib import sha256
from pathlib import Path
import tempfile
from urllib.request import urlopen


CHECKSUMS = {
    'lite': '59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a',
    'full': '5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1',
    'heavy': '64437af838a65d18e5ba7a0d39b465540069bc8aae8308de3e318aad31fcbc7b',
}


def install(destination, variant='full'):
    checksum = CHECKSUMS[variant]
    url = (f'https://storage.googleapis.com/mediapipe-models/pose_landmarker/'
           f'pose_landmarker_{variant}/float16/1/pose_landmarker_{variant}.task')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256(destination.read_bytes()).hexdigest() == checksum:
        print(f'Verified existing MediaPipe Pose Landmarker {variant.title()}.')
        return
    temporary = None
    try:
        with urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix='.download', delete=False) as output:
            temporary = Path(output.name)
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > 40 * 1024 * 1024:
                    raise RuntimeError('MediaPipe checkpoint exceeds its expected size limit.')
                output.write(chunk)
        if sha256(temporary.read_bytes()).hexdigest() != checksum:
            raise RuntimeError('MediaPipe checkpoint integrity verification failed.')
        temporary.replace(destination)
        print(f'Installed and verified MediaPipe Pose Landmarker {variant.title()}.')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=tuple(CHECKSUMS), default='full')
    args = parser.parse_args()
    install(Path(__file__).resolve().parent.parent / 'models' / f'pose_landmarker_{args.variant}.task', args.variant)
