import math
import re
from dataclasses import dataclass, asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Confidence = Annotated[float, Field(ge=0.05, le=0.95, allow_inf_nan=False)]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt: str = Field(default="person, laptop", min_length=1, max_length=2048)
    mode: Literal["objects", "phrase"] = "objects"
    size: Literal[384, 512, 640, 960] = 640
    confidence: float = Field(default=0.35, ge=0.05, le=0.95)
    class_confidences: dict[str, Confidence] = Field(default_factory=dict, max_length=32)
    stabilization: Literal["balanced", "responsive", "off"] = "balanced"
    camera_motion: bool = True
    posture_enabled: bool = False
    standing_confidence: Confidence = .10

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value):
        if "<" in value or ">" in value or any(ord(x) < 32 and x not in "\r\n\t" for x in value):
            raise ValueError("Use plain text without model control tokens.")
        value = value.strip()
        if not value:
            raise ValueError("Enter at least one detection target.")
        return value

    @field_validator('class_confidences')
    @classmethod
    def normalize_confidences(cls, values):
        normalized = {}
        for label, threshold in values.items():
            key = label.strip().casefold()
            if not key or key in normalized:
                raise ValueError('Confidence settings need unique, nonempty object names.')
            normalized[key] = threshold
        return dict(sorted(normalized.items()))

    @model_validator(mode='after')
    def check_confidence_labels(self):
        categories = self.categories()
        if self.mode == 'phrase':
            if len(categories) > 8:
                raise ValueError('Use at most 8 detailed phrases, separated by new lines or semicolons.')
            if any(len(category) > 160 for category in categories):
                raise ValueError('Keep each detailed phrase within 160 characters.')
        elif len(categories) > 32:
            raise ValueError('Enter at most 32 object categories.')
        if self.class_confidences:
            unknown = self.class_confidences.keys() - {c.casefold() for c in categories}
            if unknown:
                raise ValueError('Confidence settings contain unknown object types: ' + ', '.join(sorted(unknown)))
        return self

    def confidence_for(self, label):
        # Preserve the legacy single-phrase adapter's partial output labels.
        # Multiple phrases must always use their own full-label threshold.
        categories = self.categories() if self.mode == 'phrase' else []
        key = categories[0] if len(categories) == 1 else label
        return self.class_confidences.get(key.strip().casefold(), self.confidence)

    @property
    def minimum_confidence(self):
        return min(self.confidence_for(label) for label in self.categories())

    def categories(self):
        seen = set()
        result = []
        parts = re.split(r'[\r\n;]+', self.prompt) if self.mode == 'phrase' else self.prompt.split(',')
        for category in parts:
            category = category.strip()
            if category and category.casefold() not in seen:
                seen.add(category.casefold())
                result.append(category)
        if not result:
            raise ValueError("Enter at least one detection target.")
        return result


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: Options = Field(default_factory=Options)
    kind: Literal["image", "live", "video"] = "image"
    source_role: Literal["outside", "inside", "unassigned"] = "unassigned"
    source_name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator('source_name')
    @classmethod
    def clean_source_name(cls, value):
        if value is not None:
            value = value.strip()
            if not value or any(ord(char) < 32 for char in value):
                raise ValueError('Use a nonempty plain-text source name.')
        return value


class RFIDRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passenger_type: Literal['senior', 'assistance']
    event_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    source_name: str | None = Field(default=None, min_length=1, max_length=80)

    _clean_source_name = field_validator('source_name')(SessionRequest.clean_source_name.__func__)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    options: Options = Field(default_factory=Options)
    stride: int = Field(default=1, ge=1, le=30)


@dataclass
class Detection:
    label: str
    bbox: list[float]
    score: float | None = None
    track_id: int | None = None
    velocity: list[float] | None = None
    predicted: bool = False
    observed_at_ms: float | None = None
    prediction_expires_at_ms: float | None = None

    def json(self):
        return asdict(self)


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    aa = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    bb = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return intersection / max(aa + bb - intersection, 1e-9)


def sanitize(detections):
    """Validate backend output without manufacturing detector confidence."""
    valid = []
    for det in detections:
        if len(det.bbox) != 4 or not all(math.isfinite(float(v)) for v in det.bbox):
            continue
        box = [min(1.0, max(0.0, float(v))) for v in det.bbox]
        if box[2] - box[0] < 0.001 or box[3] - box[1] < 0.001:
            continue
        score = det.score
        if score is not None and (not math.isfinite(score) or not 0 <= score <= 1):
            continue
        label = det.label.strip()[:160] or "object"
        valid.append(Detection(label, box, score))
    valid.sort(key=lambda d: d.score if d.score is not None else 1, reverse=True)
    result = []
    for det in valid[:300]:
        if not any(d.label == det.label and iou(d.bbox, det.bbox) > 0.95 for d in result):
            result.append(det)
    return result


def filter_confidence(detections, options):
    return [d for d in detections if d.score is None or d.score >= options.confidence_for(d.label)]
