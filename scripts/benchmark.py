"""Measure warm backend latency; this does not claim webcam end-to-end speed."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run  # establishes project-local cache locations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="yoloe", choices=["yoloe", "demo", "grounding-dino", "locateanything"])
    parser.add_argument("--model")
    parser.add_argument("--image", default="static/sample.jpg")
    parser.add_argument("--prompt", default="person, bus")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", default="benchmark.json")
    args = parser.parse_args()
    os.chdir(ROOT)
    import cv2
    import numpy as np
    import torch
    from vision.backends import create_backend
    from vision.config import Settings
    from vision.schema import Options
    defaults = {"yoloe": "yoloe-26m-seg.pt", "grounding-dino": "IDEA-Research/grounding-dino-tiny",
                "locateanything": "nvidia/LocateAnything-3B", "demo": "color-components"}
    started = time.perf_counter()
    backend = create_backend(Settings(backend=args.backend, model=args.model or defaults[args.backend]))
    backend.warmup()
    startup = time.perf_counter() - started
    image = cv2.imread(args.image)
    if image is None:
        raise SystemExit("Cannot read benchmark image.")
    result = {"backend": backend.info, "torch": torch.__version__, "startup_seconds": round(startup, 2),
              "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
              "image": args.image, "prompt": args.prompt, "repeats": args.repeats, "results": []}
    for size in [384, 512, 640, 960]:
        options = Options(prompt=args.prompt, size=size)
        for _ in range(5):
            backend.detect(image, options)
        samples = []
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        for _ in range(max(1, args.repeats)):
            start = time.perf_counter()
            detections = backend.detect(image, options)
            samples.append((time.perf_counter() - start) * 1000)
        row = {"size": size, "median_ms": round(float(np.median(samples)), 2),
               "p95_ms": round(float(np.percentile(samples, 95)), 2), "objects": len(detections),
               "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 1) if torch.cuda.is_available() else None}
        result["results"].append(row)
        print(json.dumps(row), flush=True)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
