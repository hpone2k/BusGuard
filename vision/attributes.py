"""Conservative upper-clothing color checks, not general phrase grounding.

The detector finds people. A deterministic HSV check then looks for a dominant
color in their segmented central torso. Lighting, occlusion, patterned clothing
and truncated people can make the answer unknown; unknown never passes a filter.
Color support is pixel coverage, not a calibrated probability or model score.
"""

import re
from dataclasses import dataclass

import cv2
import numpy as np


COLORS = ('red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple', 'pink', 'black', 'white', 'gray')
COLOR_NAMES = '|'.join((*COLORS, 'grey'))
_PHRASE = re.compile(
    rf'(?:a |the )?(?:person|people) (?:wearing |with |in )(?:a |an )?'
    rf'(?P<color>{COLOR_NAMES}) (?:shirt|t-shirt|t shirt|top)', re.IGNORECASE)
PHRASE_HELP = ('Clothing phrase mode supports a person wearing one solid-color shirt or top, '
               'for example "a person with a red shirt". Supported colors: ' + ', '.join(COLORS) +
               '. Other attributes, actions, relations and multi-color descriptions are not supported by this detector.')
CAPABILITY = {
    'phrases': True,
    'phrase_scope': 'person-clothing-color',
    'phrase_colors': list(COLORS),
    'phrase_examples': ['a person with a red shirt', 'person wearing a blue top'],
    'phrase_method': 'person segmentation + central torso HSV heuristic',
    'phrase_score': 'person detection confidence; clothing color is an uncalibrated filter',
    'phrase_limitations': 'Solid-color visible upper clothing only. Lighting, patterned clothes, occlusion and cropped bodies may produce no match. Not general phrase grounding.',
}


@dataclass(frozen=True)
class ClothingPhrase:
    color: str
    label: str


def parse_clothing_phrase(prompt):
    text = ' '.join(str(prompt).strip().split())
    match = _PHRASE.fullmatch(text)
    if not match:
        raise ValueError(PHRASE_HELP)
    color = match['color'].lower()
    return ClothingPhrase('gray' if color == 'grey' else color, str(prompt).strip())


def validate_yoloe_options(options):
    """Shared API/backend validation: no silent object-only phrase fallback."""
    if options.mode == 'phrase':
        return parse_clothing_phrase(options.prompt)
    for label in options.categories():
        if (_PHRASE.fullmatch(' '.join(label.split())) or
                re.search(r'\b(?:wearing|with|holding|beside|behind|next to|in front of)\b', label, re.I)):
            raise ValueError('Descriptions need clothing phrase mode. ' + PHRASE_HELP)
    return None


def _color_masks(hsv):
    h, s, v = cv2.split(hsv)
    chromatic = (s >= 75) & (v >= 60)
    bands = {'red': (h <= 8) | (h >= 170), 'orange': (h >= 9) & (h <= 22),
             'yellow': (h >= 23) & (h <= 35), 'green': (h >= 36) & (h <= 85),
             'cyan': (h >= 86) & (h <= 99), 'blue': (h >= 100) & (h <= 129),
             'purple': (h >= 130) & (h <= 149), 'pink': (h >= 150) & (h <= 169)}
    masks = {name: region & chromatic for name, region in bands.items()}
    masks.update(black=v <= 50, white=(s <= 45) & (v >= 190), gray=(s <= 45) & (v >= 65) & (v < 175))
    return masks


def match_clothing_color(frame, bbox, color, polygon, other_boxes=()):
    """Return evidence only for a dominant color across a visible masked torso.

    ``polygon`` uses original-image normalized coordinates (Ultralytics xyn).
    Requiring the instance mask avoids treating surrounding red objects as a
    shirt. Excluding overlap with other people avoids assigning their clothes
    to this track. No mask or insufficient unambiguous area means unknown.
    """
    if color not in COLORS or frame.ndim != 3 or frame.shape[2] != 3:
        return None
    h, w = frame.shape[:2]
    box = np.asarray(bbox, dtype=float)
    points = np.asarray(polygon if polygon is not None else [], dtype=float)
    if (box.shape != (4,) or not np.isfinite(box).all() or points.ndim != 2
            or points.shape[1:] != (2,) or len(points) < 3 or not np.isfinite(points).all()):
        return None
    x1, y1, x2, y2 = box * [w, h, w, h]
    bw, bh = x2 - x1, y2 - y1
    # A missing head or lower body shifts a box-based torso into the wrong region.
    if bw < 20 or bh < 48 or bh / bw < 1.15 or y1 < 1 or y2 > h - 1:
        return None
    left, right = max(0, round(x1 + .25 * bw)), min(w, round(x1 + .75 * bw))
    top, bottom = max(0, round(y1 + .20 * bh)), min(h, round(y1 + .52 * bh))
    if right - left < 10 or bottom - top < 12:
        return None
    patch = frame[top:bottom, left:right]
    mask = np.zeros(patch.shape[:2], dtype=np.uint8)
    local_points = np.rint(points * [w, h] - [left, top]).astype(np.int32)
    cv2.fillPoly(mask, [local_points], 1)
    for other in other_boxes:
        other = np.asarray(other, dtype=float)
        if other.shape != (4,) or not np.isfinite(other).all():
            continue
        ox1, oy1, ox2, oy2 = np.rint(other * [w, h, w, h]).astype(int)
        a, b = max(0, ox1 - left), min(right - left, ox2 - left)
        c, d = max(0, oy1 - top), min(bottom - top, oy2 - top)
        if a < b and c < d:
            mask[c:d, a:b] = 0
    valid = mask.astype(bool)
    area = np.count_nonzero(valid)
    if area < 96 or area < mask.size * .6:
        return None
    # Bound per-person CPU work even for large uploaded images.
    scale = min(1, 96 / max(patch.shape[:2]))
    if scale < 1:
        size = (max(1, round(patch.shape[1] * scale)), max(1, round(patch.shape[0] * scale)))
        patch = cv2.resize(patch, size, interpolation=cv2.INTER_AREA)
        valid = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST).astype(bool)
        area = np.count_nonzero(valid)
    color_masks = _color_masks(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV))
    coverage = {name: np.count_nonzero(region & valid) / area for name, region in color_masks.items()}
    support = coverage[color]
    runner_up = max(value for name, value in coverage.items() if name != color)
    if support < .55 or support - runner_up < .20:
        return None
    # A single colored bag, stripe or patch should not pass as a whole shirt.
    selected = color_masks[color] & valid
    for half in (np.s_[:patch.shape[0] // 2, :], np.s_[patch.shape[0] // 2:, :],
                 np.s_[:, :patch.shape[1] // 2], np.s_[:, patch.shape[1] // 2:]):
        available = np.count_nonzero(valid[half])
        if available < 12 or np.count_nonzero(selected[half]) / available < .35:
            return None
    return {'color': color, 'coverage': round(float(support), 3), 'method': 'segmented torso HSV heuristic'}
