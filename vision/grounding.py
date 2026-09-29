"""Batched referring-expression detection with explicit phrase/token ownership.

Grounding DINO's usual decoded-text postprocessor can combine words from distinct
queries and its maximum-token score can accept only the generic noun in a longer
description. Here, each output label comes from the requested phrase and scores
aggregate its content words. These similarities are not calibrated probabilities.
"""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import re

import numpy as np
from PIL import Image

from .schema import Detection, Options, iou, sanitize


MAX_PHRASES = 8
MAX_TOKENS = 256
MAX_LABEL_LENGTH = 160
_WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
# Remove grammar words only. Colors, attributes, nouns and relationship verbs
# must contribute: a strong 'person' token cannot hide a weak 'red' token.
_GRAMMAR = frozenset('a an the with in on at of and or to for is are that who which'.split())
_COLORS = 'red|orange|yellow|green|cyan|blue|purple|pink|black|white|gray|grey|beige|brown'
_CLOTHING = re.compile(
    rf'(?:a |the )?(?:person|people) (?:wearing |with |in )(?:a |an )?'
    rf'(?P<color>{_COLORS}) (?P<garment>shirt|t-shirt|t shirt|top|coat|jacket|sweater|hoodie)', re.I)
_COLORED_OBJECT = re.compile(rf'(?:a |an |the )?(?P<color>{_COLORS}) [\w-]+(?: [\w-]+)?', re.I)
_GARMENT_ADJECTIVE = {'shirt': 'shirted', 't-shirt': 'shirted', 't shirt': 'shirted',
                      'top': 'shirted', 'coat': 'coated', 'jacket': 'jacketed',
                      'sweater': 'sweater-wearing', 'hoodie': 'hoodie-wearing'}


@dataclass(frozen=True)
class PhraseTokens:
    label: str
    words: tuple[tuple[int, ...], ...]
    color: str | None = None
    person_clothing: bool = False


@dataclass
class EncodedPhrases:
    caption: str
    phrases: tuple[PhraseTokens, ...]
    inputs: dict
    people: PhraseTokens | None = None


def _query(label):
    text = ' '.join(label.lower().rstrip('.!? ').split())
    clothing = _CLOTHING.fullmatch(text)
    if clothing:
        color = clothing['color'].replace('grey', 'gray')
        # DINO often assigns 'person wearing a coat' to two unrelated boxes.
        # This equivalent adjectival form binds the clothing to its subject.
        return f"{color}-{_GARMENT_ADJECTIVE[clothing['garment']]} person", color, True
    colored = _COLORED_OBJECT.fullmatch(text)
    return text, colored['color'].replace('grey', 'gray') if colored else None, False


def encode_phrases(tokenizer, labels):
    """Tokenize once without truncation and map lexical words to their subtokens."""
    labels = tuple(str(label).strip() for label in labels)
    if not 1 <= len(labels) <= MAX_PHRASES:
        raise ValueError(f'Use between 1 and {MAX_PHRASES} detailed phrases per source.')
    if len({label.casefold() for label in labels}) != len(labels):
        raise ValueError('Detailed phrases must be unique.')
    caption, spans = '', []
    for label in labels:
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise ValueError(f'Each detailed phrase must contain 1–{MAX_LABEL_LENGTH} characters.')
        phrase, color, person_clothing = _query(label)
        if not phrase:
            raise ValueError('Enter words describing the object to detect.')
        begin = len(caption)
        caption += phrase + '. '
        spans.append((begin, phrase, color, person_clothing))
    # A shared subject query repairs a garment-only box to its containing person.
    # It is an internal query, not an extra user target or a second image forward.
    has_people = any(span[3] for span in spans)
    if has_people:
        spans.append((len(caption), 'person', None, False))
        caption += 'person. '
    encoded = tokenizer(caption, return_tensors='pt', return_offsets_mapping=True,
                        truncation=False, padding=False)
    offsets = encoded.pop('offset_mapping')[0].tolist()
    if len(offsets) > MAX_TOKENS:
        raise ValueError(f'These phrases use {len(offsets)} text tokens; shorten them to at most {MAX_TOKENS}.')
    phrases = []
    for label, (begin, phrase, color, person_clothing) in zip(
            (*labels, '__grounded_person__') if has_people else labels, spans):
        all_words = list(_WORDS.finditer(phrase))
        words = [word for word in all_words if word.group().casefold() not in _GRAMMAR]
        if not words:
            raise ValueError('Each detailed phrase must describe an object, not only grammar words.')
        groups = []
        for word in words:
            start, end = begin + word.start(), begin + word.end()
            indices = tuple(index for index, (left, right) in enumerate(offsets)
                            if right > left and left < end and right > start)
            if not indices:
                raise ValueError('The model could not tokenize a complete phrase. Use plain descriptive words.')
            groups.append(indices)
        phrases.append(PhraseTokens(label, tuple(groups), color, person_clothing))
    return EncodedPhrases(caption, tuple(phrases[:-1] if has_people else phrases),
                          dict(encoded), phrases[-1] if has_people else None)


def _contains(parent, child):
    x1, y1 = max(parent[0], child[0]), max(parent[1], child[1])
    x2, y2 = min(parent[2], child[2]), min(parent[3], child[3])
    area = max(0., child[2] - child[0]) * max(0., child[3] - child[1])
    return max(0., x2 - x1) * max(0., y2 - y1) / max(area, 1e-9)


def color_present(bgr, box, color, minimum_coverage=.045):
    """Veto absent explicit colors without adding confidence or new detections.

    Presence is deliberately weaker than classification. It cannot verify an
    action or a complex relation; ambiguous/patterned colors can remain unknown.
    """
    import cv2
    height, width = bgr.shape[:2]
    x1, y1, x2, y2 = np.rint(np.clip(box, 0, 1) * [width, height, width, height]).astype(int)
    patch = bgr[y1:y2, x1:x2]
    if min(patch.shape[:2]) < 6:
        return False
    scale = min(1., 96 / max(patch.shape[:2]))
    if scale < 1:
        patch = cv2.resize(patch, (max(1, round(patch.shape[1] * scale)),
                                   max(1, round(patch.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    h, s, v = cv2.split(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV))
    chromatic = (s >= 55) & (v >= 45)
    bands = {'red': (h <= 9) | (h >= 170), 'orange': (h >= 10) & (h <= 22),
             'yellow': (h >= 23) & (h <= 35), 'green': (h >= 36) & (h <= 85),
             'cyan': (h >= 86) & (h <= 99), 'blue': (h >= 100) & (h <= 129),
             'purple': (h >= 130) & (h <= 149), 'pink': (h >= 150) & (h <= 169)}
    bands = {name: region & chromatic for name, region in bands.items()}
    bands.update(black=v <= 65, white=(s <= 50) & (v >= 180),
                 gray=(s <= 55) & (v >= 60) & (v < 190),
                 beige=(h >= 10) & (h <= 35) & (s >= 15) & (s <= 120) & (v >= 115),
                 brown=(h >= 5) & (h <= 28) & (s >= 55) & (v >= 35) & (v <= 170))
    return float(np.count_nonzero(bands[color])) / max(1, h.size) >= minimum_coverage


def verify_attributes(bgr, detections, phrases, people, options):
    """Associate garment predictions uniquely and reject absent explicit colors."""
    metadata = {phrase.label: phrase for phrase in phrases}
    result = []
    for det in detections:
        phrase = metadata[det.label]
        color_box = det.bbox
        box, score = det.bbox, det.score
        if phrase.person_clothing:
            matches = [person for person in people
                       if person.score >= .2
                       and (_contains(person.bbox, box) >= .85 or iou(person.bbox, box) >= .65)]
            if len(matches) != 1:
                continue
            person = matches[0]
            box = person.bbox
            left, top, right, bottom = box
            bw, bh = right - left, bottom - top
            torso = [left + .25 * bw, top + .20 * bh, right - .25 * bw, top + .52 * bh]
            # Full-person predictions need clothing evidence in the torso; an
            # independently localized shirt/coat keeps its tighter original crop.
            det_area = (det.bbox[2] - det.bbox[0]) * (det.bbox[3] - det.bbox[1])
            person_area = (box[2] - box[0]) * (box[3] - box[1])
            if det_area > person_area * .55:
                color_box = torso
            else:
                # A matching pair of trousers, shoes or hat does not establish
                # the requested shirt/coat color. A garment part must be in the
                # person's upper-body region before it can inherit that subject.
                center = (det.bbox[1] + det.bbox[3]) / 2
                relative_height = (center - box[1]) / max(box[3] - box[1], 1e-9)
                if not .14 <= relative_height <= .62:
                    continue
                color_box = [max(det.bbox[0], torso[0]), max(det.bbox[1], torso[1]),
                             min(det.bbox[2], torso[2]), min(det.bbox[3], torso[3])]
            # This auxiliary query establishes the subject's extent, not the
            # phrase confidence. Keep the already-thresholded grounding score;
            # otherwise a prompt-dependent subject score would double-filter
            # an independently strong phrase match.
        if phrase.color and not color_present(bgr, color_box, phrase.color,
                                               minimum_coverage=.10 if phrase.person_clothing else .045):
            continue
        result.append(Detection(det.label, box, score))
    return sanitize(result)


def phrase_detections(probabilities, boxes, phrases, options, nms_iou=.65):
    """Turn normalized cxcywh boxes and token similarities into exact labels.

    Subword similarities are averaged within a word; the phrase score is their
    geometric mean. In addition, every content word must reach 45% of the chosen
    phrase threshold. This conservative agreement check prevents a high generic
    noun score from overwhelming missing attributes. It is not a probability of
    the natural-language statement being true.
    """
    probabilities = np.asarray(probabilities, dtype=np.float32)
    boxes = np.asarray(boxes, dtype=np.float32)
    if probabilities.ndim != 2 or boxes.shape != (len(probabilities), 4):
        raise RuntimeError('The phrase model returned invalid detection shapes.')
    results = []
    for phrase in phrases:
        word_scores = np.stack([probabilities[:, group].mean(axis=1) for group in phrase.words], axis=1)
        finite = np.isfinite(word_scores).all(axis=1) & np.isfinite(boxes).all(axis=1)
        safe_scores = np.where(np.isfinite(word_scores), word_scores, 0)
        scores = np.exp(np.log(np.clip(safe_scores, 1e-9, 1)).mean(axis=1))
        threshold = float(options.confidence_for(phrase.label))
        selected = np.flatnonzero(finite & (scores >= threshold) &
                                 (safe_scores.min(axis=1) >= threshold * .45))
        selected = selected[np.argsort(-scores[selected], kind='stable')]
        kept = []
        for index in selected[:200]:
            cx, cy, width, height = (float(value) for value in boxes[index])
            box = [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2]
            if width <= 0 or height <= 0 or any(iou(box, det.bbox) > nms_iou for det in kept):
                continue
            kept.append(Detection(phrase.label, box, float(scores[index])))
            if len(kept) >= 50:
                break
        results.extend(kept)
    # Suppress only within a phrase: an object can legitimately satisfy several
    # descriptions. Consumers must not treat summed phrase counts as unique people.
    return sanitize(results)[:200]


class GroundedPhrases:
    """One model forward for all phrases in a frame; owned by the GPU worker."""

    def __init__(self, model_path, device='cpu', revision=None):
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.device = str(device)
        path = str(model_path)
        kwargs = dict(revision=revision, trust_remote_code=False,
                      local_files_only=Path(path).is_dir())
        # Offset ownership requires the fast tokenizer; the processor's use_fast
        # option applies to both tokenizer and image preprocessing.
        self.processor = AutoProcessor.from_pretrained(path, use_fast=True, **kwargs)
        if not getattr(self.processor.tokenizer, 'is_fast', False):
            raise RuntimeError('Grounding requires a fast tokenizer with exact text offsets.')
        # The PyTorch attention implementation works on Windows without NVCC or
        # runtime compilation and avoids a costly failed custom-kernel build.
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            path, use_safetensors=True, disable_custom_kernels=True, **kwargs).to(self.device).eval()
        self.text_cache = OrderedDict()
        self.use_amp = self.device.startswith('cuda')
        self.info = {'name': 'Grounding DINO Tiny', 'model': path, 'device': self.device,
                     'phrases': True, 'max_phrases': MAX_PHRASES, 'phrase_max': MAX_PHRASES,
                     'max_text_tokens': MAX_TOKENS,
                     'phrase_scope': 'referring-expressions',
                     'phrase_score': 'Uncalibrated geometric mean of content-word grounding similarities; subject/color checks are independent filters.',
                     'phrase_method': 'Batched text grounding, equivalent clothing queries, unique person association, and explicit-color veto.',
                     'phrase_limitations': 'Complex actions, relationships, occlusion and subtle attributes may be missed. Color checks depend on lighting; phrase matches can overlap.',
                     'phrase_overlap': 'One object may match several phrases; their sum is not a unique passenger count.',
                     'scores': True, 'demo': False,
                     'precision': 'mixed-fp16' if self.use_amp else 'fp32'}

    def _text(self, options):
        key = tuple(options.categories())
        if key not in self.text_cache:
            encoded = encode_phrases(self.processor.tokenizer, key)
            encoded.inputs = {name: value.to(self.device) for name, value in encoded.inputs.items()}
            self.text_cache[key] = encoded
            if len(self.text_cache) > 8:
                self.text_cache.popitem(last=False)
        self.text_cache.move_to_end(key)
        return self.text_cache[key]

    def validate_options(self, options):
        """Validate outside the worker without touching CUDA or its text cache."""
        encode_phrases(self.processor.tokenizer, options.categories())

    validate = validate_options

    def _forward(self, inputs, encoded):
        import torch
        all_phrases = (*encoded.phrases, encoded.people) if getattr(encoded, 'people', None) else encoded.phrases
        indices = sorted({index for phrase in all_phrases for word in phrase.words for index in word})
        with torch.inference_mode():
            with torch.autocast(device_type='cuda', dtype=torch.float16, enabled=self.use_amp):
                outputs = self.model(**inputs)
            # DINO intentionally masks unused token logits with -inf. Only the
            # requested word logits and boxes must be finite.
            valid = torch.isfinite(outputs.logits[..., indices]).all() & torch.isfinite(outputs.pred_boxes).all()
            if not bool(valid):
                if not self.use_amp:
                    raise RuntimeError('The phrase model produced non-finite predictions.')
                self.use_amp = False
                self.info['precision'] = 'fp32 (numerical fallback)'
                outputs = self.model(**inputs)
                valid = torch.isfinite(outputs.logits[..., indices]).all() & torch.isfinite(outputs.pred_boxes).all()
                if not bool(valid):
                    raise RuntimeError('The phrase model produced non-finite predictions in full precision.')
        return outputs

    def detect(self, bgr, options):
        encoded = self._text(options)
        image = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
        inputs = self.processor.image_processor(
            images=image, return_tensors='pt',
            size={'shortest_edge': options.size, 'longest_edge': options.size * 2})
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        inputs.update(encoded.inputs)
        outputs = self._forward(inputs, encoded)
        probabilities = outputs.logits[0].float().sigmoid().cpu().numpy()
        boxes = outputs.pred_boxes[0].float().cpu().numpy()
        detections = phrase_detections(probabilities, boxes, encoded.phrases, options)
        people_options = SimpleNamespace(confidence_for=lambda _: .2)
        people = phrase_detections(probabilities, boxes, (encoded.people,), people_options) if encoded.people else []
        return verify_attributes(bgr, detections, encoded.phrases, people, options)

    def warmup(self):
        self.detect(np.zeros((384, 512, 3), dtype=np.uint8),
                    Options(prompt='a person wearing a red shirt', mode='phrase', size=512))
