"""Download the pinned, official Grounding DINO Tiny checkpoint for local use.

Run after installing requirements-phrases.txt. This downloads data/config files
only, never repository Python code or pickle weights, and verifies the official
LFS SHA-256 before the checkpoint can replace an existing download.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


REPOSITORY = "IDEA-Research/grounding-dino-tiny"
REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
WEIGHT_SIZE = 689359096
WEIGHT_SHA256 = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
FILES = (
    "config.json", "preprocessor_config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "vocab.txt", "README.md", "model.safetensors",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    records = {}
    for name in FILES:
        target = destination / name
        if name == "model.safetensors" and target.exists() and target.stat().st_size == WEIGHT_SIZE and sha256(target) == WEIGHT_SHA256:
            records[name] = {"bytes": WEIGHT_SIZE, "sha256": WEIGHT_SHA256}
            print(f"Verified existing {name}", flush=True)
            continue
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
        request = Request(url, headers={"User-Agent": "BusTech-local-phrase-setup/1"})
        temporary = None
        try:
            with urlopen(request, timeout=120) as response, tempfile.NamedTemporaryFile(dir=destination, suffix=".download", delete=False) as output:
                temporary = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            digest = sha256(temporary)
            size = temporary.stat().st_size
            if name == "model.safetensors" and (size != WEIGHT_SIZE or digest != WEIGHT_SHA256):
                raise RuntimeError("Official checkpoint integrity verification failed")
            temporary.replace(target)
            records[name] = {"bytes": size, "sha256": digest}
            print(f"Downloaded {name}: {size:,} bytes", flush=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    metadata = {
        "repository": REPOSITORY, "revision": REVISION,
        "url": f"https://huggingface.co/{REPOSITORY}/tree/{REVISION}",
        "license": "Apache-2.0", "weight_format": "safetensors",
        "remote_code": False, "files": records,
    }
    (destination / "source.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    target = root / "models" / "grounding-dino-tiny"
    install(target)
    print(f"Ready for offline inference: {target}")
