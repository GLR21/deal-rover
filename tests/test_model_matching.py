"""T5: model code matching (providers.base.title_matches_model_exact and
providers.base.model_matches).

Born from a real bug: without telling close variants apart, the monitor can
alert the price of the WRONG product (e.g. confusing 'KF432C16BBA/8' with
'KF432C16BB/8', which has no RGB).
"""

from providers.base import title_matches_model_exact, model_matches


def test_title_matches_its_own_model():
    # Real store titles put the code at the end or followed by a comma or
    # parenthesis, which is what the algorithm (which removes spaces, not
    # punctuation) expects.
    assert title_matches_model_exact(
        "KF432C16BBA/8", "Memoria DDR4 Kingston Fury Beast RGB, 8GB, KF432C16BBA/8"
    ) is True


def test_title_does_not_match_the_variant_without_suffix():
    # KF432C16BB/8 (without the 'A' for RGB) is a DIFFERENT product.
    assert title_matches_model_exact(
        "KF432C16BBA/8", "Memoria Kingston KF432C16BB/8 RGB"
    ) is False


def test_title_does_not_match_another_capacity():
    assert title_matches_model_exact(
        "KF432C16BBA/8", "Memoria Kingston KF432C16BBA/16 RGB"
    ) is False


def test_title_does_not_match_as_prefix_of_a_longer_code():
    # 'KF432C16BBA/8' must not match inside 'KF432C16BBA/816'.
    assert title_matches_model_exact(
        "KF432C16BBA/8", "Memoria KF432C16BBA/816"
    ) is False


def test_missing_title_is_false():
    assert title_matches_model_exact("KF432C16BBA/8", None) is False


def testmodel_matches_in_the_url_slug():
    assert model_matches(
        "KF432C16BBA/8", "/produto/123/memoria-kf432c16bba-8gb-rgb"
    ) is True


def testmodel_matches_with_hyphens_in_the_code():
    assert model_matches(
        "F4-3200C16D-16GTZN", "/produto/algo-f43200c16d16gtzn-outro"
    ) is True


def testmodel_matches_a_different_product_is_false():
    assert model_matches(
        "KF432C16BBA/8", "/produto/outro-produto-completamente-diferente"
    ) is False
