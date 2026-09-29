from types import SimpleNamespace

import numpy as np

from vision.backends import Hybrid
from vision.schema import Detection, Options


class Engine:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def detect(self, frame, options):
        self.calls.append(options)
        return [Detection(self.label, [.1, .2, .4, .8], .8)]


def test_modes_select_exactly_one_engine_without_weak_phrase_fallback():
    hybrid = object.__new__(Hybrid)
    hybrid.objects = Engine('person')
    hybrid.phrases = Engine('a person with a red shirt')
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    options = Options(prompt='a person with a red shirt\na blue bus', mode='phrase')
    result = hybrid.detect(image, options)
    assert result[0].label == 'a person with a red shirt'
    assert len(hybrid.phrases.calls) == 1
    assert hybrid.phrases.calls[0].categories() == ['a person with a red shirt', 'a blue bus']
    assert not hybrid.objects.calls
    assert hybrid.engine_for(options) == 'Grounding DINO'
    hybrid.detect(image, Options(prompt='person'))
    assert len(hybrid.objects.calls) == 1 and len(hybrid.phrases.calls) == 1


def test_invalid_grounding_input_is_rejected_instead_of_routed_to_objects():
    hybrid = object.__new__(Hybrid)
    hybrid.objects = Engine('person')
    calls = []

    def validate(options):
        calls.append(options)
        raise ValueError('Too many text tokens; shorten descriptions.')

    hybrid.phrases = SimpleNamespace(validate_options=validate)
    import pytest
    with pytest.raises(ValueError, match='text tokens'):
        hybrid.validate_options(Options(mode='phrase', prompt='a person with a backpack'))
    assert len(calls) == 1 and not hybrid.objects.calls
