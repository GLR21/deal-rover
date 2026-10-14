"""T2: broken-page detection (core._is_broken_page).

The most important case here is the false positive: a LEGITIMATE product
page served behind Cloudflare usually references "cloudflare" in some
<script> or <meta> attribute of the raw HTML, which would make a detector
that matched the whole HTML throw away good readings exactly in the stores it
should protect. That is why the markers are matched against the RENDERED
TEXT (soup.get_text()), not the raw HTML.
"""

from bs4 import BeautifulSoup

import core


def _broken(html: str, model: str = None) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return core._is_broken_page(html, soup, model=model)


def test_cloudflare_challenge_is_broken():
    html = """
    <html><body>
        <h1>Checking your browser before accessing site...</h1>
        <p>This process is automatic. Please wait.</p>
    </body></html>
    """
    assert _broken(html) is True


def test_leaked_source_code_is_broken():
    html = """
    <html><body>
        function get_price() { $this->value = 10; }
    </body></html>
    """
    assert _broken(html) is True


def test_short_page_without_model_is_broken():
    html = "<html><body><h1>Page not found</h1></body></html>"
    assert _broken(html) is True


def test_short_page_with_the_model_in_the_text_is_not_broken():
    html = "<html><body><h1>CMW16GX4M2C3200C16</h1></body></html>"
    # short, but the model is there -> suspicious, not discarded
    assert _broken(html, model="CMW16GX4M2C3200C16") is False


def test_valid_page_behind_cloudflare_is_not_a_false_positive():
    # 'cloudflare' only shows up in a <script src> attribute, never in the
    # visible text, and get_text() ignores attributes, so it must not trigger.
    html = """
    <html>
    <head><script src="https://cdn.cloudflare.com/analytics.js"></script></head>
    <body>
        <h1>Memoria RAM Corsair Vengeance 16GB</h1>
        <p>Modelo: CMW16GX4M2C3200C16</p>
        <p>Preco: R$ 599,90</p>
        <p>Comprar agora - Adicionar ao carrinho - Em estoque</p>
        <p>Memoria DDR4 de alta performance com dissipador de calor em aluminio,
        compativel com placas-mae AMD e Intel de ultima geracao.</p>
    </body>
    </html>
    """
    assert _broken(html, model="CMW16GX4M2C3200C16") is False


def test_empty_html_is_broken():
    assert _broken("") is True
