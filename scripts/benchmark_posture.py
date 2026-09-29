"""Compare installed MediaPipe variants on one local image; no images are uploaded."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('YOLO_CONFIG_DIR', str(ROOT / 'data' / 'ultralytics'))
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'data' / 'matplotlib'))
os.environ.setdefault('YOLO_AUTOINSTALL', 'false')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=ROOT / 'static' / 'sample.jpg')
    parser.add_argument('--output', type=Path, default=ROOT / 'validation' / 'mediapipe-precision.json')
    parser.add_argument('--overlay', type=Path, help='Save the last variant preview locally for visual inspection.')
    parser.add_argument('--runs', type=int, default=8)
    parser.add_argument('--variants', nargs='+', choices=['lite', 'full', 'heavy'], default=['lite', 'full', 'heavy'])
    args = parser.parse_args()
    if not 3 <= args.runs <= 100:
        parser.error('--runs must be between 3 and 100 (the first two runs are warm-up).')
    import cv2
    from vision.backends import YoloE
    from vision.config import Settings
    from vision.mediapipe_pose import MediaPipeSeating
    from vision.media import draw_detections, draw_posture
    from vision.schema import Options
    from vision.tracking import StableTracker
    frame = cv2.imread(str(args.image))
    if frame is None:
        parser.error('The local image could not be read.')
    detector = YoloE(Settings(model=str(ROOT / 'yoloe-26m-seg.pt')))
    detector.seating.close()
    tracker = StableTracker(Options(prompt='person'))
    for index in range(3):
        raw = detector.detect(frame, tracker.detection_options)
        tracked = tracker.update(raw, index / 30, frame)
    report = {'image': args.image.name, 'image_shape': list(frame.shape),
              'raw_people': len(raw), 'tracked_people': len(tracked),
              'note': 'One repeated street image with fixed tracks. This measures latency and functional output, not joint accuracy on bus footage.',
              'variants': {}}
    for variant in args.variants:
        model = MediaPipeSeating(ROOT / 'models' / f'pose_landmarker_{variant}.task')
        if not model.info['available']:
            report['variants'][variant] = model.info
            continue
        runs = []
        for index in range(args.runs):
            result = model.estimate(frame, tracked, raw, time.time() * 1000)
            runs.append({key: result[key] for key in ('analysis_ms', 'people', 'standing', 'seated', 'unknown',
                         'complete', 'crop_refinements', 'refined_poses', 'refinement_budget_exhausted')})
        measured = [item['analysis_ms'] for item in runs[2:]]
        report['variants'][variant] = {'info': model.info, 'median_ms': statistics.median(measured),
                                      'min_ms': min(measured), 'max_ms': max(measured), 'runs': runs,
                                      'visible_joints': [row.get('pose_quality', {}).get('visible_joints')
                                                         for row in result['occupants']],
                                      'unassigned_poses': len(result.get('unassigned_poses', []))}
        if args.overlay:
            preview = frame.copy()
            draw_detections(preview, [item.json() for item in tracked])
            draw_posture(preview, result)
            args.overlay.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.overlay), preview):
                raise RuntimeError('The posture preview could not be written.')
        model.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({name: {key: result.get(key) for key in ('median_ms', 'min_ms', 'max_ms')}
                      for name, result in report['variants'].items()}, indent=2))
    print(f'Report: {args.output}')


if __name__ == '__main__':
    main()
