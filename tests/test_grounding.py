from collections import OrderedDict
from types import SimpleNamespace
import re

import numpy as np
import pytest

from vision.grounding import (GroundedPhrases, PhraseTokens, color_present, encode_phrases,
                              phrase_detections, verify_attributes)
from vision.schema import Detection


class Array:
    def __init__(self, values): self.values = np.asarray(values)
    def __getitem__(self, item): return Array(self.values[item])
    def tolist(self): return self.values.tolist()
    def to(self, device): return self


class Tokenizer:
    """A deterministic offset tokenizer so tests require no model/network/GPU."""
    calls = 0
    is_fast = True
    def __call__(self, caption, **kwargs):
        assert kwargs['truncation'] is False
        self.calls += 1
        offsets = [(0, 0)]
        for match in re.finditer(r'\w+|[^\w\s]', caption):
            offsets.append(match.span())
        offsets.append((0, 0))
        return {'input_ids': Array([list(range(len(offsets)))]),
                'offset_mapping': Array([offsets]), 'attention_mask': Array([[1] * len(offsets)])}


def opts(labels, thresholds=None):
    thresholds = thresholds or {}
    return SimpleNamespace(categories=lambda: labels, confidence_for=lambda label: thresholds.get(label, .35))


def test_exact_phrase_ownership_with_shared_nouns_and_no_grammar_tokens():
    encoded = encode_phrases(Tokenizer(), ['A person with a red shirt', 'person with a blue shirt', 'a yellow bag'])
    assert encoded.caption == 'red-shirted person. blue-shirted person. a yellow bag. person. '
    assert [p.label for p in encoded.phrases] == ['A person with a red shirt', 'person with a blue shirt', 'a yellow bag']
    assert [len(p.words) for p in encoded.phrases] == [3, 3, 2]
    first = {token for word in encoded.phrases[0].words for token in word}
    second = {token for word in encoded.phrases[1].words for token in word}
    assert not first & second
    assert encoded.people is not None and encoded.people.label == '__grounded_person__'
    assert encoded.phrases[0].color == 'red' and encoded.phrases[0].person_clothing


def test_reject_empty_duplicate_too_many_overlong_and_uninformative_phrases():
    for labels in ([], ['person', 'Person'], ['person'] * 9, ['x' * 161], ['...'], ['the with an']):
        with pytest.raises(ValueError): encode_phrases(Tokenizer(), labels)


def test_token_budget_rejected_without_silent_truncation():
    labels = [' '.join([str(i)] * 70) for i in range(4)]
    with pytest.raises(ValueError, match='text tokens'):
        encode_phrases(Tokenizer(), labels)


def test_subwords_stay_grouped_with_their_lexical_word():
    class SubwordTokenizer(Tokenizer):
        def __call__(self, caption, **kwargs):
            return {'input_ids': Array([[0, 1, 2, 3, 4]]),
                    'offset_mapping': Array([[(0, 0), (0, 5), (5, 10), (10, 11), (0, 0)]])}
    encoded = encode_phrases(SubwordTokenizer(), ['wheelchair'])
    assert encoded.phrases[0].words == ((1, 2),)


def test_generic_noun_alone_cannot_match_a_detailed_phrase():
    phrase = PhraseTokens('person with a red shirt', ((0,), (1,), (2,)))
    probabilities = np.array([[.99, .02, .85], [.60, .55, .50]])
    boxes = [[.2, .5, .2, .8], [.7, .5, .2, .8]]
    results = phrase_detections(probabilities, boxes, [phrase], opts([phrase.label]))
    assert len(results) == 1
    assert results[0].bbox[0] == pytest.approx(.6)
    assert results[0].score == pytest.approx((.6 * .55 * .5) ** (1 / 3))


def test_weak_word_guard_rejects_one_missing_attribute_even_when_mean_passes():
    phrase = PhraseTokens('person wearing a red shirt', ((0,), (1,), (2,), (3,)))
    assert phrase_detections([[.9, .9, .1, .9]], [[.5,.5,.4,.8]], [phrase], opts([phrase.label])) == []


def test_three_queries_keep_original_labels_thresholds_and_cross_phrase_overlap():
    phrases = [PhraseTokens('a red shirt', ((0,), (1,))),
               PhraseTokens('person with a bag', ((2,), (3,))),
               PhraseTokens('a yellow bag', ((4,), (5,)))]
    results = phrase_detections([[.8,.8,.7,.7,.6,.6]], [[.5,.5,.4,.8]], phrases,
                               opts([p.label for p in phrases], {'a yellow bag':.65}))
    assert [d.label for d in results] == ['a red shirt', 'person with a bag']
    assert results[0].bbox == results[1].bbox


def test_per_phrase_nms_removes_duplicate_boxes_but_keeps_distinct_objects():
    phrase = PhraseTokens('bag', ((0,),))
    results = phrase_detections([[.8], [.7], [.6]],
                               [[.2,.5,.2,.4], [.21,.5,.2,.4], [.8,.5,.2,.4]],
                               [phrase], opts(['bag']))
    assert len(results) == 2
    assert [d.score for d in results] == pytest.approx([.8, .6])


def test_invalid_outputs_fail_closed_without_manufacturing_scores():
    phrase = PhraseTokens('bag', ((0,),))
    assert phrase_detections([[float('nan')], [.8]],
                             [[.2,.5,.2,.4], [float('inf'),.5,.2,.4]], [phrase], opts(['bag'])) == []
    with pytest.raises(RuntimeError, match='shapes'):
        phrase_detections([[.8]], [[.1,.2,.3]], [phrase], opts(['bag']))


def test_eight_entry_text_cache_reuses_encoding_across_threshold_changes():
    backend = object.__new__(GroundedPhrases)
    tokenizer = Tokenizer()
    backend.processor = SimpleNamespace(tokenizer=tokenizer)
    backend.device, backend.text_cache = 'cpu', OrderedDict()
    first = backend._text(opts(['red bag']))
    assert backend._text(opts(['red bag'], {'red bag':.8})) is first
    assert tokenizer.calls == 1
    for i in range(8): backend._text(opts([f'bag number {i}']))
    assert len(backend.text_cache) == 8
    assert ('red bag',) not in backend.text_cache


def test_request_validation_does_not_touch_device_or_worker_cache():
    backend = object.__new__(GroundedPhrases)
    backend.processor = SimpleNamespace(tokenizer=Tokenizer())
    # No device or cache exists: accepting a request must remain CPU-only.
    backend.validate_options(opts(['a person in red', 'a blue bag']))
    assert not hasattr(backend, 'text_cache')


def test_three_phrases_share_one_image_forward():
    torch = pytest.importorskip('torch')
    backend = object.__new__(GroundedPhrases)
    seen = []
    def preprocess(**kwargs):
        seen.append(('image', kwargs['size']))
        return {'pixel_values': torch.zeros(1, 3, 16, 16)}
    def forward(inputs, encoded):
        seen.append(('forward', len(encoded.phrases)))
        return SimpleNamespace(logits=torch.full((1, 1, 256), 1.),
                               pred_boxes=torch.tensor([[[.5, .5, .4, .8]]]))
    backend.processor = SimpleNamespace(tokenizer=Tokenizer(), image_processor=preprocess)
    backend.device, backend.text_cache, backend._forward = 'cpu', OrderedDict(), forward
    options = opts(['a small bag', 'a large suitcase', 'a round ball'])
    options.size = 512
    results = backend.detect(np.zeros((16, 16, 3), np.uint8), options)
    assert len(results) == 3
    assert seen == [('image', {'shortest_edge': 512, 'longest_edge': 1024}), ('forward', 3)]


def test_numeric_guard_accepts_masked_padding_but_rejects_bad_meaningful_token():
    torch = pytest.importorskip('torch')
    backend = object.__new__(GroundedPhrases)
    backend.use_amp, backend.info = False, {}
    encoded = SimpleNamespace(phrases=[PhraseTokens('bag', ((0,),))])
    outputs = SimpleNamespace(logits=torch.tensor([[[1., float('-inf')]]]),
                              pred_boxes=torch.tensor([[[.5, .5, .4, .8]]]))
    backend.model = lambda **kwargs: outputs
    assert backend._forward({}, encoded) is outputs
    outputs.logits[0, 0, 0] = float('nan')
    with pytest.raises(RuntimeError, match='non-finite'):
        backend._forward({}, encoded)


def test_mixed_precision_numeric_failure_retries_full_precision_and_stays_there(monkeypatch):
    from contextlib import nullcontext
    torch = pytest.importorskip('torch')
    backend = object.__new__(GroundedPhrases)
    backend.use_amp, backend.info = True, {}
    calls = []
    def model(**kwargs):
        calls.append(backend.use_amp)
        return SimpleNamespace(logits=torch.tensor([[[float('nan') if backend.use_amp else 1.]]]),
                               pred_boxes=torch.tensor([[[.5, .5, .4, .8]]]))
    # Exercise fallback state without requiring a CUDA device in the test runner.
    monkeypatch.setattr(torch, 'autocast', lambda **kwargs: nullcontext())
    backend.model = model
    encoded = SimpleNamespace(phrases=[PhraseTokens('bag', ((0,),))])
    result = backend._forward({}, encoded)
    assert calls == [True, False] and not backend.use_amp
    assert torch.isfinite(result.logits).all()
    assert backend.info['precision'] == 'fp32 (numerical fallback)'
    backend._forward({}, encoded)
    assert calls == [True, False, False]


def test_color_veto_rejects_absent_explicit_color_but_never_adds_detections():
    frame = np.zeros((100, 100, 3), np.uint8)
    frame[:] = [230, 80, 30]  # blue BGR
    assert color_present(frame, [0, 0, 1, 1], 'blue')
    assert not color_present(frame, [0, 0, 1, 1], 'red')
    phrases = [PhraseTokens('a red bus', ((0,),), 'red'), PhraseTokens('a blue bus', ((1,),), 'blue')]
    detections = [Detection(phrase.label, [0, 0, 1, 1], .5) for phrase in phrases]
    result = verify_attributes(frame, detections, phrases, [], opts([p.label for p in phrases]))
    assert [d.label for d in result] == ['a blue bus']
    assert result[0].score == .5
    assert verify_attributes(frame, [], phrases, [], opts([p.label for p in phrases])) == []


def test_garment_box_returns_unique_containing_person_and_rejects_ambiguity():
    frame = np.full((100, 100, 3), [20, 20, 230], np.uint8)
    phrase = PhraseTokens('a person in a red shirt', ((0,),), 'red', True)
    garment = Detection(phrase.label, [.3, .25, .5, .5], .65)
    person = Detection('__grounded_person__', [.2, .05, .6, .95], .8)
    options = opts([phrase.label])
    result = verify_attributes(frame, [garment], [phrase], [person], options)
    assert len(result) == 1 and result[0].bbox == person.bbox
    assert result[0].score == .65
    assert verify_attributes(frame, [garment], [phrase], [], options) == []
    second = Detection('__grounded_person__', [.25, .05, .7, .95], .8)
    assert verify_attributes(frame, [garment], [phrase], [person, second], options) == []


def test_background_color_does_not_validate_full_person_clothing():
    frame = np.full((100, 100, 3), [20, 20, 230], np.uint8)
    frame[5:95, 20:60] = [230, 80, 30]
    phrase = PhraseTokens('a person in a red shirt', ((0,),), 'red', True)
    person_box = [.2, .05, .6, .95]
    assert verify_attributes(frame, [Detection(phrase.label, person_box, .8)], [phrase],
                             [Detection('__grounded_person__', person_box, .8)], opts([phrase.label])) == []


def test_colored_trousers_cannot_be_associated_as_a_shirt():
    frame = np.full((100, 100, 3), [230, 80, 30], np.uint8)
    phrase = PhraseTokens('a person in a blue shirt', ((0,),), 'blue', True)
    trousers = Detection(phrase.label, [.25, .65, .55, .85], .7)
    person = Detection('__grounded_person__', [.2, .05, .6, .95], .8)
    assert verify_attributes(frame, [trousers], [phrase], [person], opts([phrase.label])) == []


def test_auxiliary_subject_score_is_not_a_second_phrase_threshold():
    frame = np.full((100, 100, 3), [20, 20, 230], np.uint8)
    phrase = PhraseTokens('a person in a red shirt', ((0,),), 'red', True)
    garment = Detection(phrase.label, [.3, .25, .5, .5], .65)
    person = Detection('__grounded_person__', [.2, .05, .6, .95], .25)
    result = verify_attributes(frame, [garment], [phrase], [person], opts([phrase.label], {phrase.label:.5}))
    assert len(result) == 1 and result[0].score == .65
