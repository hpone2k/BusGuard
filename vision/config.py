from dataclasses import dataclass, field
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    backend: str = "hybrid"
    model: str = "yoloe-26m-seg.pt"
    phrase_model: str = field(default_factory=lambda: str(ROOT / 'models' / 'grounding-dino-tiny'))
    pose_model: str = field(default_factory=lambda: str(ROOT / 'models' / 'pose_landmarker_full.task'))
    pose_max_people: int = 27  # 26 passenger places plus one overflow sentinel.
    locate_wsl_distro: str = field(default_factory=lambda: os.getenv('BUSTECH_LOCATE_WSL_DISTRO', 'Ubuntu'))
    locate_python: str = field(default_factory=lambda: os.getenv('BUSTECH_LOCATE_PYTHON', '/home/your-user/miniforge3/envs/la/bin/python'))
    device: str = "auto"
    quant: str = "4bit"
    generation: str = "hybrid"
    revision: str | None = None
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    image_bytes: int = 12 * 1024 * 1024
    image_pixels: int = 24_000_000
    upload_bytes: int = 512 * 1024 * 1024
    disk_budget: int = 4 * 1024**3
    max_duration: float = 900
    max_sessions: int = 24
    session_ttl: float = 300
    retention_hours: float = 24
    queue_size: int = 12
    max_jobs: int = 3
    inference_timeout: float = 120
    ffmpeg: str | None = None
