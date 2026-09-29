"""Prompt changes keep one warmed runtime while updating its copied model."""
from collections import OrderedDict
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from vision.backends import YoloE
from vision.schema import Options


class ArrayResult:
    def __init__(self, value):
        self.value = np.asarray(value)

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class PromptModel:
    def __init__(self):
        self.model = [SimpleNamespace(is_fused=False, nc=0)]

    def set_classes(self, names, embeddings):
        assert tuple(names) == embeddings
        self.names = dict(enumerate(names))
        self.embeddings = embeddings
        self.model[-1].nc = len(names)


class Runtime:
    format = 'pt'

    def __init__(self, source):
        model = copy.deepcopy(source)
        self.backend = SimpleNamespace(model=model, names=model.names)

    @property
    def names(self):
        return self.backend.names


class Model:
    """Mirror the pinned library's reset, deep copy and runtime names contract."""
    def __init__(self):
        self.model = PromptModel()
        self.predictor = None
        self.setup_calls = 0
        self.prompt_calls = []
        self.predictions = []

    def get_text_pe(self, labels):
        self.prompt_calls.append(tuple(labels))
        return tuple(labels)

    def set_classes(self, labels, embeddings):
        self.model.set_classes(labels, embeddings)
        self.predictor = None

    def predict(self, **kwargs):
        if self.predictor is None:
            self.setup_calls += 1
            self.predictor = SimpleNamespace(model=Runtime(self.model), args=SimpleNamespace(compile=False))
        runtime = self.predictor.model
        model = runtime.backend.model
        assert model is not self.model
        assert model.names == runtime.names == self.model.names
        assert model.embeddings == self.model.embeddings
        assert model.model[-1].nc == len(runtime.names)
        self.predictions.append((tuple(runtime.names.values()), kwargs))
        count = len(runtime.names)
        return [SimpleNamespace(boxes=SimpleNamespace(
            xyxyn=ArrayResult([[.1, .1, .4, .9]] * count),
            conf=ArrayResult([.9] * count), cls=ArrayResult(range(count))))]


def detector():
    result = YoloE.__new__(YoloE)
    result.model, result.device = Model(), 'cpu'
    result.current, result.embeddings = None, OrderedDict()
    return result


def test_alternating_person_standing_and_outside_prompts_reuses_one_runtime():
    backend = detector()
    image = np.zeros((16, 16, 3), np.uint8)
    options = [Options(prompt='person', confidence=.55, size=512),
               Options(prompt='standing person', confidence=.10, size=640),
               Options(prompt='person, wheelchair, laptop', confidence=.25, size=384)]
    for index in range(12):
        option = options[index % len(options)]
        rows = backend.detect(image, option)
        assert [row.label for row in rows] == option.categories()
        categories, args = backend.model.predictions[-1]
        assert categories == tuple(option.categories())
        assert args['imgsz'] == option.size and args['conf'] == option.confidence
        assert args['quantize'] == 32
    assert backend.model.setup_calls == 1
    assert backend.model.prompt_calls == [tuple(option.categories()) for option in options]


@pytest.mark.parametrize('unsupported', ['compiled', 'exported', 'fused', 'prompt_free', 'foreign'])
def test_incompatible_runtime_falls_back_to_normal_model_recreation(unsupported):
    backend = detector()
    image = np.zeros((16, 16, 3), np.uint8)
    backend.detect(image, Options(prompt='person'))
    predictor = backend.model.predictor
    if unsupported == 'compiled':
        predictor.args.compile = True
    elif unsupported == 'exported':
        predictor.model.format = 'onnx'
    elif unsupported == 'fused':
        predictor.model.backend.model.model[-1].is_fused = True
    elif unsupported == 'prompt_free':
        predictor.model.backend.model.model[-1].lrpc = object()
    else:
        predictor.model.backend.model = SimpleNamespace(model=[SimpleNamespace(is_fused=False)])
    rows = backend.detect(image, Options(prompt='standing person', confidence=.1))
    assert backend.model.predictor is not predictor
    assert backend.model.setup_calls == 2
    assert [row.label for row in rows] == ['standing person']


def test_new_prompt_after_embedding_cache_eviction_updates_existing_runtime():
    backend = detector()
    image = np.zeros((16, 16, 3), np.uint8)
    prompts = ['person'] + [f'item{index}' for index in range(8)] + ['person']
    for prompt in prompts:
        assert backend.detect(image, Options(prompt=prompt))[0].label == prompt
    assert backend.model.setup_calls == 1
    assert len(backend.embeddings) == 8
    assert backend.model.prompt_calls.count(('person',)) == 2


def test_failed_runtime_switch_does_not_restore_partial_model_or_stale_prompt_key():
    backend = detector()
    image = np.zeros((16, 16, 3), np.uint8)
    backend.detect(image, Options(prompt='person'))
    failed_predictor = backend.model.predictor

    def fail(names, embeddings):
        raise RuntimeError('Synthetic prompt update failure')

    failed_predictor.model.backend.model.set_classes = fail
    with pytest.raises(RuntimeError, match='Synthetic'):
        backend.detect(image, Options(prompt='standing person', confidence=.1))
    assert backend.model.predictor is None
    assert backend.current is None
    assert backend.model.model.names == {0: 'standing person'}
    rows = backend.detect(image, Options(prompt='person'))
    assert backend.model.predictor is not failed_predictor
    assert backend.model.setup_calls == 2
    assert [row.label for row in rows] == ['person']
