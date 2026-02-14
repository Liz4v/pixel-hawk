import io

import pytest

from pixel_hawk.palette import PALETTE


def test_lookup_transparent():
    # alpha 0 should map to palette index 0
    report = {}
    assert PALETTE.lookup(report, (1, 2, 3, 0)) == 0
    assert report == {}


def test_lookup_unknown_color_tracked():
    # use an unlikely RGB value that's not in palette
    report = {}
    result = PALETTE.lookup(report, (250, 251, 252, 255))
    assert result == 0
    rgb = (250 << 16) | (251 << 8) | 252
    assert report == {rgb: 1}


def test_new_creates_paletted_image():
    im = PALETTE.new((2, 2))
    assert im.mode == "P"
    # transparency should be set to palette index 0
    assert im.info.get("transparency") == 0


def test_ensure_converts_rgba_and_lookup_valid_color():
    from PIL import Image

    # pick a known palette color from internal index list
    rgb_int = PALETTE._idx[0]
    r = (rgb_int >> 16) & 0xFF
    g = (rgb_int >> 8) & 0xFF
    b = rgb_int & 0xFF

    # create an RGBA image with that color and ensure conversion
    im = Image.new("RGBA", (2, 2), (r, g, b, 255))
    pal = PALETTE.ensure(im)
    assert pal.mode == "P"
    # ensure lookup for the color returns a non-zero palette index
    report = {}
    assert PALETTE.lookup(report, (r, g, b, 255)) != 0
    assert report == {}


def test_open_file_with_existing_paletted_file(tmp_path):
    path = tmp_path / "pal.png"
    im = PALETTE.new((2, 2))
    im.putdata([0, 1, 2, 3])
    im.save(path)

    opened = PALETTE.open_file(path)
    assert opened.mode == "P"


def test_ensure_rgba_conversion_for_rgb_image():
    from PIL import Image

    from pixel_hawk.palette import _ensure_rgba

    rgb_im = Image.new("RGB", (1, 1), (1, 2, 3))
    rgba = _ensure_rgba(rgb_im)
    assert rgba.mode == "RGBA"


def test_lookup_wrong_teal_mapping():
    # The palette maps 0x10AE82 to the same index as 0x10AEA6
    teal = 0x10AE82
    r = (teal >> 16) & 0xFF
    g = (teal >> 8) & 0xFF
    b = teal & 0xFF
    report = {}
    idx = PALETTE.lookup(report, (r, g, b, 255))
    assert isinstance(idx, int)
    assert report == {}


# --- Async tests (aopen_file / aopen_bytes on Palette) ---


@pytest.mark.asyncio
async def test_aopen_file(tmp_path):
    path = tmp_path / "pal.png"
    im = PALETTE.new((2, 2))
    im.putdata([0, 1, 2, 3])
    im.save(path)
    im.close()

    async with PALETTE.aopen_file(path) as image:
        assert image.mode == "P"
        assert image.size == (2, 2)
        assert list(image.get_flattened_data()) == [0, 1, 2, 3]
    # image should be closed after exiting context
    assert getattr(image, "fp", None) is None


@pytest.mark.asyncio
async def test_aopen_file_converts_non_paletted(tmp_path):
    from PIL import Image

    # pick a known palette color
    rgb_int = PALETTE._idx[0]
    r, g, b = (rgb_int >> 16) & 0xFF, (rgb_int >> 8) & 0xFF, rgb_int & 0xFF

    path = tmp_path / "rgba.png"
    Image.new("RGBA", (1, 1), (r, g, b, 255)).save(path)

    async with PALETTE.aopen_file(path) as image:
        assert image.mode == "P"


@pytest.mark.asyncio
async def test_aopen_bytes():
    im = PALETTE.new((3, 3))
    im.putdata([0, 1, 2, 3, 4, 5, 6, 7, 8])
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    im.close()

    async with PALETTE.aopen_bytes(buf.getvalue()) as image:
        assert image.mode == "P"
        assert image.size == (3, 3)
        assert list(image.get_flattened_data()) == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert getattr(image, "fp", None) is None


@pytest.mark.asyncio
async def test_aopen_bytes_converts_non_paletted():
    from PIL import Image

    rgb_int = PALETTE._idx[0]
    r, g, b = (rgb_int >> 16) & 0xFF, (rgb_int >> 8) & 0xFF, rgb_int & 0xFF

    buf = io.BytesIO()
    Image.new("RGBA", (1, 1), (r, g, b, 255)).save(buf, format="PNG")

    async with PALETTE.aopen_bytes(buf.getvalue()) as image:
        assert image.mode == "P"


@pytest.mark.asyncio
async def test_aopen_file_closes_on_exception(tmp_path):
    path = tmp_path / "pal.png"
    im = PALETTE.new((1, 1))
    im.save(path)
    im.close()

    with pytest.raises(RuntimeError, match="test"):
        async with PALETTE.aopen_file(path) as image:
            raise RuntimeError("test")
    assert getattr(image, "fp", None) is None


@pytest.mark.asyncio
async def test_aopen_bytes_closes_on_exception():
    im = PALETTE.new((1, 1))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    im.close()

    with pytest.raises(RuntimeError, match="test"):
        async with PALETTE.aopen_bytes(buf.getvalue()) as image:
            raise RuntimeError("test")
    assert getattr(image, "fp", None) is None
