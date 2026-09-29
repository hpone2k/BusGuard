"""Project-local caches for CLI, tests and programmatic API use."""
import os
from pathlib import Path


def configure_environment():
    root = Path(__file__).resolve().parent.parent
    for relative in ('models', 'data/ultralytics', 'data/matplotlib'):
        (root / relative).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('HF_HOME', str(root / 'models' / 'huggingface'))
    os.environ.setdefault('YOLO_CONFIG_DIR', str(root / 'data' / 'ultralytics'))
    os.environ.setdefault('MPLCONFIGDIR', str(root / 'data' / 'matplotlib'))
    os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
