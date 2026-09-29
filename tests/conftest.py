import random
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageCms

TOOL_DIR = str(Path(__file__).resolve().parent.parent)
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)


def make_gradient(w, h):
    """Smooth diagonal colour gradient with a few hard edges."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 255 // max(1, w - 1), y * 255 // max(1, h - 1),
                        (x + y) * 255 // max(1, w + h - 2))
    for i in range(0, w, max(8, w // 6)):
        for y in range(h):
            px[min(i, w - 1), y] = (0, 0, 0)
    return im


def make_noise(w, h, seed=1):
    rnd = random.Random(seed)
    data = bytes(rnd.randrange(256) for _ in range(w * h * 3))
    return Image.frombytes("RGB", (w, h), data)


@pytest.fixture
def gradient():
    return make_gradient


@pytest.fixture
def noise():
    return make_noise


@pytest.fixture
def srgb_icc():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@pytest.fixture
def imgdir(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    return d
