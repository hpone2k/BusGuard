import pytest

from vision.schema import Detection, Options, filter_confidence


def test_phrase_targets_preserve_commas_and_split_only_lines_or_semicolons():
    options = Options(mode='phrase', prompt=(
        ' a person with a red shirt\r\n'
        'a person carrying a bag, standing near the door; a blue backpack\n'
        'A PERSON WITH A RED SHIRT;\n'
    ))
    assert options.categories() == [
        'a person with a red shirt',
        'a person carrying a bag, standing near the door',
        'a blue backpack',
    ]


def test_multiple_phrase_thresholds_are_independent_even_for_same_object():
    red, blue = 'a person with a red shirt', 'a person with a blue shirt'
    options = Options(mode='phrase', prompt=f'{red}\n{blue}', confidence=.35,
                      class_confidences={red: .75, blue: .25})
    detections = [Detection(red, [.1, .1, .3, .8], .6),
                  Detection(blue, [.5, .1, .7, .8], .3)]
    assert options.minimum_confidence == .25
    assert options.confidence_for(red.upper()) == .75
    assert options.confidence_for('person') == .35
    assert [d.label for d in filter_confidence(detections, options)] == [blue]


def test_multiple_phrases_do_not_accept_a_threshold_for_a_partial_label():
    with pytest.raises(ValueError, match='unknown object types'):
        Options(mode='phrase', prompt='a red backpack\na blue backpack',
                class_confidences={'backpack': .2})


def test_single_phrase_partial_label_compatibility():
    options = Options(mode='phrase', prompt='a person, wearing red;',
                      class_confidences={'a person, wearing red': .7})
    assert options.confidence_for('person') == .7


def test_phrase_limits_bound_work_without_silently_discarding_targets():
    assert len(Options(mode='phrase', prompt='\n'.join(f'a target {i}' for i in range(8))).categories()) == 8
    with pytest.raises(ValueError, match='at most 8'):
        Options(mode='phrase', prompt='\n'.join(f'a target {i}' for i in range(9)))
    with pytest.raises(ValueError, match='160 characters'):
        Options(mode='phrase', prompt='a' * 161)
    with pytest.raises(ValueError, match='at least one'):
        Options(mode='phrase', prompt='; \n ;')
    with pytest.raises(ValueError):
        Options(mode='phrase', prompt='a' * 2049)
    # The complete prompt can exceed the former 512-character limit.
    assert len(Options(mode='phrase', prompt='\n'.join(f'{i} ' + 'x' * 100 for i in range(8))).categories()) == 8


def test_object_categories_keep_existing_comma_grammar_and_limits():
    assert Options(prompt=' person, laptop, PERSON ').categories() == ['person', 'laptop']
    with pytest.raises(ValueError, match='at most 32'):
        Options(prompt=','.join(f'item{i}' for i in range(33)))


@pytest.mark.parametrize('prompt', ['<target>', 'target\x00', 'target\x1f'])
def test_phrase_prompt_still_rejects_control_tokens(prompt):
    with pytest.raises(ValueError, match='plain text'):
        Options(mode='phrase', prompt=prompt)
