import pytest

from treant.docling import DoclingParser


@pytest.fixture
def parser():
    return DoclingParser()


def test_get_page_idx(parser):
    block_with_prov = {"prov": [{"page_no": 3}]}
    assert parser._get_page_idx(block_with_prov) == 3

    block_no_prov = {"prov": []}
    assert parser._get_page_idx(block_no_prov) == 0

    block_empty = {}
    assert parser._get_page_idx(block_empty) == 0


def test_write_image(parser, tmp_path):
    import base64

    image_path = tmp_path / "test.png"
    # Base64 for a 1x1 transparent PNG
    base64_str = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

    parser._write_image(image_path, base64_str)

    assert image_path.exists()
    content = image_path.read_bytes()
    assert content == base64.b64decode(base64_str)


def test_get_converter_caching(parser):
    # First call
    converter1 = parser._get_converter()

    # Second call - should use cache
    converter2 = parser._get_converter()

    assert converter1 is converter2
