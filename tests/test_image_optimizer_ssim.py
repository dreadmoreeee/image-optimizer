import pytest
from PIL import Image, ImageFilter

from image_optimizer import ssim as S

BACKENDS = ["pillow"] + (["numpy"] if S.np is not None else [])


@pytest.mark.parametrize("backend", BACKENDS)
def test_identical_is_one(gradient, backend):
    a = gradient(96, 64).convert("L")
    assert S.ssim(a, a.copy(), backend) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("backend", BACKENDS)
def test_monotonic_with_degradation(gradient, noise, backend):
    a = noise(96, 64).convert("L")
    light = a.filter(ImageFilter.GaussianBlur(0.6))
    heavy = a.filter(ImageFilter.GaussianBlur(3))
    s_light = S.ssim(a, light, backend)
    s_heavy = S.ssim(a, heavy, backend)
    assert 1.0 > s_light > s_heavy
    assert s_heavy < 0.5


@pytest.mark.parametrize("backend", BACKENDS)
def test_unrelated_images_score_low(noise, backend):
    a = noise(64, 64, seed=1).convert("L")
    b = noise(64, 64, seed=2).convert("L")
    assert S.ssim(a, b, backend) < 0.1


@pytest.mark.parametrize("backend", BACKENDS)
def test_tiny_images_do_not_crash(backend):
    a = Image.new("L", (3, 2), 100)
    b = Image.new("L", (3, 2), 100)
    assert S.ssim(a, b, backend) == pytest.approx(1.0)
    one = Image.new("L", (1, 1), 10)
    assert S.ssim(one, Image.new("L", (1, 1), 10), backend) == pytest.approx(1.0)


def test_backends_agree_roughly(noise):
    if S.np is None:
        pytest.skip("numpy not installed")
    a = noise(128, 96).convert("L")
    b = a.filter(ImageFilter.GaussianBlur(1.2))
    assert abs(S.ssim(a, b, "numpy") - S.ssim(a, b, "pillow")) < 0.08


def test_size_and_mode_validation():
    with pytest.raises(ValueError):
        S.ssim(Image.new("L", (4, 4)), Image.new("L", (5, 4)))
    with pytest.raises(ValueError):
        S.ssim(Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4)))
    with pytest.raises(ValueError):
        S.backend_name("bogus")


def test_luminance_flattens_alpha_on_white():
    im = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    assert S.luminance(im).getextrema() == (255, 255)
