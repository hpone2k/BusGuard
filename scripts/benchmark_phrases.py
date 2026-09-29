"""Reproduce warm hybrid-model timing and sample phrase checks on this device.

This is a small diagnostic on a bundled public example, not an accuracy dataset
or an end-to-end camera latency benchmark. Recheck with labeled deployment data.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run  # Establish project-local model caches before heavyweight imports.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--confidence', type=float, default=.30)
    parser.add_argument('--output', default='validation/grounding-environment.json')
    args = parser.parse_args()
    import cv2
    import numpy as np
    import torch
    from vision.backends import Hybrid
    from vision.config import Settings
    from vision.schema import Options, iou

    image = cv2.imread(str(ROOT / 'static' / 'sample.jpg'))
    if image is None:
        raise RuntimeError('Bundled sample image is unavailable')
    settings = Settings(backend='hybrid', model=str(ROOT / 'yoloe-26m-seg.pt'))
    start = time.perf_counter()
    model = Hybrid(settings)
    model.warmup()
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    results = {
        'purpose': 'Single public-image diagnostic, not a dataset accuracy claim or camera latency measurement.',
        'sample': 'static/sample.jpg (bundled Ultralytics bus example)',
        'versions': {name: importlib.metadata.version(name) for name in (
            'torch', 'torchvision', 'transformers', 'huggingface-hub', 'tokenizers', 'safetensors')},
        'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu',
        'model_revision': 'a2bb814dd30d776dcf7e30523b00659f4f141c71',
        'startup_seconds': round(time.perf_counter() - start, 2),
        'confidence': args.confidence, 'repeats': args.repeats,
        'all_models_loaded': ['YOLOE 26m', 'Grounding DINO Tiny', 'YOLO26n pose'],
        'timings': [],
    }
    phrases = [
        'a person wearing a beige coat',
        'a person wearing a black coat',
        'a blue bus',
        'a person wearing sunglasses',
        'a person wearing blue pants',
        'a person wearing a red shirt',
        'a red bus',
        'a person in a wheelchair',
    ]
    reference_boxes = {
        phrases[0]: [.058, .367, .307, .837],
        phrases[1]: [.271, .366, .434, .803],
        phrases[2]: [.004, .207, .996, .683],
    }
    for size in (512, 640):
        for count in (1, 3, 8):
            options = Options(mode='phrase', prompt='; '.join(phrases[:count]),
                              size=size, confidence=args.confidence, stabilization='off')
            for _ in range(3):
                model.detect(image, options)
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            durations = []
            for _ in range(max(1, args.repeats)):
                start = time.perf_counter()
                detections = model.detect(image, options)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                durations.append(1000 * (time.perf_counter() - start))
            row = {
                'size_shortest_edge': size, 'phrase_count': count,
                'median_ms': round(float(np.median(durations)), 2),
                'p95_ms': round(float(np.percentile(durations, 95)), 2),
                'peak_allocated_mb': round(torch.cuda.max_memory_allocated() / 1024**2, 1) if torch.cuda.is_available() else None,
                'counts': dict(Counter(d.label for d in detections)),
                'detections': [d.json() for d in detections],
                'manual_reference_checks': {
                    phrase: any(d.label == phrase and iou(d.bbox, box) >= .5 for d in detections)
                    for phrase, box in reference_boxes.items() if phrase in phrases[:count]
                },
                'absent_target_false_positives': [d.label for d in detections if d.label in phrases[6:]],
            }
            results['timings'].append(row)
            print(json.dumps({k: v for k, v in row.items() if k != 'detections'}), flush=True)
    results['precision'] = model.phrases.info['precision']
    results['manual_reference'] = {
        'clearly_visible': ['one beige-coat person at left', 'one black-coat person near center', 'one blue bus'],
        'absent': ['red bus', 'person in a wheelchair'],
        'ambiguous_or_occluded': ['partially covered red shirts', 'partially cropped people at both image edges'],
        'note': 'Inspect individual boxes; phrase counts overlap and cannot be summed into passenger counts.',
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {output}', flush=True)


if __name__ == '__main__':
    main()
