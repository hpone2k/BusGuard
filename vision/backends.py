"""Detector adapters. Heavy imports happen only inside the GPU owner thread."""

import re
from collections import OrderedDict

import numpy as np
from PIL import Image

from .config import ROOT
from .schema import Detection, Options, sanitize, filter_confidence
from .attributes import CAPABILITY, match_clothing_color, validate_yoloe_options


def device_for(requested):
    import torch
    if requested == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Install CUDA-enabled PyTorch or select --device cpu.")
    return requested


class YoloE:
    posture_async = False
    posture_options = True

    def __init__(self, settings):
        from ultralytics import YOLOE, settings as ultralytics_settings
        ultralytics_settings.update({"sync": False})
        self.device = device_for(settings.device)
        self.model = YOLOE(settings.model)
        self.current = None
        self.embeddings = OrderedDict()
        from .standing import YoloStanding
        self.info_model = settings.model
        self.seating = YoloStanding(self)
        self.info = {"name": "YOLOE", "model": settings.model, "device": self.device,
                     **CAPABILITY, "scores": True, "demo": False, 'seating': self.seating.info}

    def warmup(self):
        # Fixed input shapes avoid a new kernel warm-up for each camera aspect ratio.
        # A real sample also warms NMS/mask postprocessing, which an empty frame skips.
        import cv2
        sample = cv2.imread(str(ROOT / "static" / "sample.jpg"))
        if sample is None:
            sample = np.zeros((640, 640, 3), dtype=np.uint8)
        for size in (384, 512, 640, 960):
            self.detect(sample, Options(size=size))
        from .tracking import StableTracker
        tracker = StableTracker()
        detections = self.detect(sample, tracker.detection_options)
        for index in range(3):
            tracker.update(detections, index / 30, sample)
        self.seating.warmup(sample)

    def estimate_seating(self, bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs):
        return self.seating.estimate(bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs)

    def estimate_seating_image(self, bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs):
        return self.seating.estimate(bgr, tracked_detections, raw_detections, captured_at_ms, blocking=True, **kwargs)

    def close(self):
        if getattr(self, 'seating', None) is not None:
            self.seating.close()

    def invalidate_seating(self, session_id):
        self.seating.invalidate(session_id)

    def _set_classes(self, categories, embeddings):
        # Ultralytics clears its predictor in set_classes. Rebuilding it copies,
        # fuses and warms the whole model, which otherwise happens twice per
        # inside frame as the person and standing prompts alternate. Its native
        # PyTorch runtime has a separate model copy: update that copy as well as
        # the source model, including names used by segmentation postprocessing.
        predictor = getattr(self.model, 'predictor', None)
        runtime = getattr(predictor, 'model', None)
        native = getattr(runtime, 'backend', None)
        model = getattr(native, 'model', None)
        layers = getattr(model, 'model', ())
        head = layers[-1] if layers else None
        reusable = (getattr(runtime, 'format', None) == 'pt'
                    and not getattr(getattr(predictor, 'args', None), 'compile', False)
                    and type(model) is type(getattr(self.model, 'model', None))
                    and callable(getattr(model, 'set_classes', None))
                    and getattr(head, 'is_fused', None) is False
                    and not hasattr(head, 'lrpc'))
        # A failed switch must not leave the previous cache key pointing at a
        # partially changed model. The next frame must apply its prompt again.
        self.current = None
        self.model.set_classes(list(categories), embeddings)
        if reusable:
            model.set_classes(list(categories), embeddings)
            native.names = model.names
            self.model.predictor = predictor

    def detect(self, bgr, options):
        phrase = validate_yoloe_options(options)
        categories = ('person',) if phrase else tuple(options.categories())
        if self.current != categories:
            # Text embeddings are reused when returning to an earlier prompt.
            if categories not in self.embeddings:
                self.embeddings[categories] = self.model.get_text_pe(list(categories))
                if len(self.embeddings) > 8:
                    self.embeddings.popitem(last=False)
            self.embeddings.move_to_end(categories)
            self._set_classes(categories, self.embeddings[categories])
            self.current = categories
        result = self.model.predict(
            source=bgr, imgsz=options.size, conf=options.minimum_confidence, iou=0.6,
            max_det=200, device=self.device, quantize=16 if self.device.startswith("cuda") else 32,
            rect=False, verbose=False, save=False)[0]
        if result.boxes is None:
            return []
        boxes = result.boxes.xyxyn.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        classes = result.boxes.cls.cpu().numpy().astype(int)
        if phrase:
            # The original-image normalized instance polygon excludes scenery.
            # Do not silently fall back to an unmasked box when masks are absent.
            masks = getattr(result, 'masks', None)
            polygons = masks.xyn if masks is not None else []
            found = []
            for index, (box, score, category) in enumerate(zip(boxes, scores, classes)):
                if category != 0 or index >= len(polygons) or score < options.minimum_confidence:
                    continue
                other_boxes = [other for n, other in enumerate(boxes) if n != index]
                evidence = match_clothing_color(bgr, box, phrase.color, polygons[index], other_boxes)
                if evidence:
                    # Keep detector confidence unchanged. Color coverage is not a
                    # probability. The phrase label is a subset, not all people.
                    found.append(Detection(phrase.label, box.tolist(), float(score)))
            return filter_confidence(sanitize(found), options)
        return filter_confidence(sanitize([Detection(categories[c], box.tolist(), float(score))
                         for box, score, c in zip(boxes, scores, classes) if 0 <= c < len(categories)]), options)


class GroundingDino:
    def __init__(self, settings):
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        self.device = device_for(settings.device)
        self.processor = AutoProcessor.from_pretrained(settings.model, revision=settings.revision)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            settings.model, revision=settings.revision).to(self.device).eval()
        self.info = {"name": "Grounding DINO", "model": settings.model, "device": self.device,
                     "phrases": True, "scores": True, "demo": False}

    def warmup(self):
        self.detect(np.zeros((512, 512, 3), dtype=np.uint8), Options(size=512))

    def detect(self, bgr, options):
        import torch
        image = Image.fromarray(bgr[:, :, ::-1])
        text = ". ".join(options.categories()) + "."
        inputs = self.processor(images=image, text=text, return_tensors="pt",
                                size={"shortest_edge": options.size, "longest_edge": options.size * 2}).to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=options.minimum_confidence, text_threshold=0.25,
            target_sizes=[image.size[::-1]])[0]
        h, w = bgr.shape[:2]
        labels = result.get("text_labels", result.get("labels", []))
        return filter_confidence(sanitize([Detection(str(label), (box.cpu().numpy() / [w, h, w, h]).tolist(), float(score))
                         for box, score, label in zip(result["boxes"], result["scores"], labels)]), options)


class Hybrid:
    """Fast category detector and phrase grounder owned by the same GPU worker.

    Routing follows the explicit mode, never falls back from an unsupported
    phrase to generic object boxes. Both models stay loaded across source
    switches, avoiding model-loading delays during capture.
    """
    posture_async = False
    posture_options = True
    def __init__(self, settings):
        from .grounding import GroundedPhrases
        from pathlib import Path
        if settings.phrase_model == str(ROOT / 'models' / 'grounding-dino-tiny') and not (
                Path(settings.phrase_model) / 'model.safetensors').is_file():
            raise RuntimeError('The detailed-phrase checkpoint is missing. Run python scripts/setup_grounding.py first.')
        self.objects = YoloE(settings)
        self.phrases = GroundedPhrases(settings.phrase_model, self.objects.device)
        self.info = {**self.objects.info, **self.phrases.info,
                     'name': 'YOLOE + Grounding DINO', 'model': settings.model,
                     'object_model': settings.model, 'phrase_model': settings.phrase_model,
                     'phrase_scope': 'open-vocabulary-grounding', 'phrase_max': 8,
                     'device': self.objects.device, 'phrases': True,
                     'scores': True, 'demo': False, 'seating': self.objects.seating.info,
                     'engines': {'objects': 'YOLOE', 'phrase': 'Grounding DINO'}}
        for key in ('phrase_colors', 'phrase_method', 'phrase_score', 'phrase_examples', 'phrase_limitations'):
            # Do not carry restricted color-filter metadata into the grounder.
            if key not in self.phrases.info:
                self.info.pop(key, None)

    def warmup(self):
        self.objects.warmup()
        self.phrases.warmup()

    def validate_options(self, options):
        if options.mode == 'phrase':
            validator = getattr(self.phrases, 'validate_options', None)
            if validator:
                validator(options)
        else:
            validate_yoloe_options(options)

    @staticmethod
    def engine_for(options):
        return 'Grounding DINO' if options.mode == 'phrase' else 'YOLOE'

    def detect(self, bgr, options):
        return (self.phrases if options.mode == 'phrase' else self.objects).detect(bgr, options)

    def estimate_seating(self, bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs):
        return self.objects.estimate_seating(bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs)

    def estimate_seating_image(self, bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs):
        return self.objects.estimate_seating_image(bgr, tracked_detections, raw_detections, captured_at_ms, **kwargs)

    def close(self):
        self.objects.close()

    def invalidate_seating(self, session_id):
        self.objects.invalidate_seating(session_id)


_TOKEN = re.compile(r"<ref>(.*?)</ref>|<box>\s*<(-?\d+)>\s*<(-?\d+)>\s*<(-?\d+)>\s*<(-?\d+)>\s*</box>", re.S)


def parse_locate(answer, categories):
    exact = {c.casefold(): c for c in categories}
    label = categories[0] if len(categories) == 1 else "object"
    detections = []
    previous_end = 0
    for token in _TOKEN.finditer(str(answer)):
        if token.group(1) is not None:
            raw = token.group(1).strip()
            label = exact.get(raw.casefold(), raw or "object")
        else:
            if "<ref>" not in str(answer):
                bare = str(answer)[previous_end:token.start()].strip(" \n\t,.:;")
                if bare and "<" not in bare and ">" not in bare:
                    label = exact.get(bare.casefold(), bare)
            detections.append(Detection(label, [int(token.group(i)) / 1000 for i in range(2, 6)]))
        previous_end = token.end()
    return sanitize(detections)


class LocateAnything:
    def __init__(self, settings):
        import torch
        from transformers import AutoModel, AutoTokenizer, AutoProcessor, BitsAndBytesConfig
        from huggingface_hub import HfApi
        self.device = device_for(settings.device)
        if not self.device.startswith("cuda"):
            raise ValueError("This LocateAnything adapter requires CUDA. Use YOLOE or Grounding DINO for CPU.")
        # Resolve once; tokenizer, processor and executable model code use the same immutable revision.
        from pathlib import Path
        revision = settings.revision
        if not Path(settings.model).exists() and revision is None:
            revision = HfApi().model_info(settings.model).sha
        kw = {"trust_remote_code": True, "revision": revision}
        self.tokenizer = AutoTokenizer.from_pretrained(settings.model, **kw)
        self.processor = AutoProcessor.from_pretrained(settings.model, **kw)
        self.dtype = torch.bfloat16
        model_kw = dict(kw, torch_dtype=self.dtype)
        if settings.quant != "none":
            model_kw["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=settings.quant == "4bit", load_in_8bit=settings.quant == "8bit",
                bnb_4bit_compute_dtype=self.dtype, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)
            model_kw["device_map"] = {"": self.device}
        self.model = AutoModel.from_pretrained(settings.model, **model_kw).eval()
        if settings.quant == "none":
            self.model.to(self.device)
        self.generation = settings.generation
        self.info = {"name": "LocateAnything", "model": settings.model, "device": self.device,
                     "phrases": True, "scores": False, "demo": False, "revision": revision}

    def warmup(self):
        # Loading is expensive; first real frame performs the first generation.
        pass

    def detect(self, bgr, options):
        import torch
        image = Image.fromarray(bgr[:, :, ::-1])
        image.thumbnail((options.size, options.size))
        cats = options.categories()
        description = options.prompt if options.mode == "phrase" else "</c>".join(cats)
        verb = "match" if options.mode == "phrase" else "matches"
        question = f"Locate all the instances that {verb} the following description: {description}."
        messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": question}]}]
        text = self.processor.py_apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        images, videos = self.processor.process_vision_info(messages)
        inputs = self.processor(text=[text], images=images, videos=videos, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            response = self.model.generate(
                pixel_values=inputs["pixel_values"].to(self.dtype), input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"], image_grid_hws=inputs.get("image_grid_hws"),
                tokenizer=self.tokenizer, max_new_tokens=2048, use_cache=True,
                generation_mode=self.generation, temperature=0.15, do_sample=True, top_p=0.9,
                repetition_penalty=1.1, verbose=False)
        return parse_locate(response[0] if isinstance(response, tuple) else response, cats)


class Demo:
    """Explicit offline test backend: finds colored regions, not semantic objects."""
    info = {"name": "Demo · colored regions only", "model": "color-components", "device": "cpu",
            "phrases": False, "scores": False, "demo": True}

    def warmup(self):
        pass

    def detect(self, bgr, options):
        import cv2
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 120, 80]), np.array([179, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = bgr.shape[:2]
        result = []
        for contour in contours:
            if cv2.contourArea(contour) < w * h * 0.001:
                continue
            x, y, ww, hh = cv2.boundingRect(contour)
            result.append(Detection("colored region", [x / w, y / h, (x + ww) / w, (y + hh) / h]))
        return sanitize(result)


def create_backend(settings):
    return {"hybrid": Hybrid, "yoloe": YoloE, "grounding-dino": GroundingDino, "locateanything": LocateAnything,
            "demo": lambda _: Demo()}[settings.backend](settings)
