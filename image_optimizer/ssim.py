"""Structural similarity (SSIM) on luminance.

Two backends:

* numpy: the classic Wang et al. formulation, 11x11 Gaussian window
  (sigma 1.5), "valid" region only, mean over the SSIM map.
* pillow: no numpy needed. Non-overlapping 8x8 blocks, statistics from
  ImageStat, covariance derived from the variance of the difference image.
  Coarser and slower, but dependency-free.

Both compare 8-bit "L" images of identical size and return a float
(1.0 means identical).
"""

from PIL import Image, ImageChops, ImageStat

try:  # numpy is optional
    import numpy as np
except ImportError:  # pragma: no cover - exercised via backend="pillow"
    np = None

C1 = (0.01 * 255) ** 2
C2 = (0.03 * 255) ** 2


def backend_name(backend=None):
    """Return the backend that ``ssim`` would use ("numpy" or "pillow")."""
    if backend is None:
        return "numpy" if np is not None else "pillow"
    if backend == "numpy" and np is None:
        raise RuntimeError("numpy backend requested but numpy is not installed")
    if backend not in ("numpy", "pillow"):
        raise ValueError("unknown SSIM backend: %r" % (backend,))
    return backend


def ssim(a, b, backend=None):
    """SSIM between two same-size "L" images."""
    if a.size != b.size:
        raise ValueError("images must have the same size: %s vs %s" % (a.size, b.size))
    if a.mode != "L" or b.mode != "L":
        raise ValueError("ssim expects 'L' (luminance) images")
    if backend_name(backend) == "numpy":
        return _ssim_numpy(a, b)
    return _ssim_pillow(a, b)


def _gauss_kernel(size, sigma):
    x = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    g = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return g / g.sum()


def _filter_valid(arr, k):
    n = len(k)
    h, w = arr.shape
    tmp = k[0] * arr[:, 0 : w - n + 1]
    for i in range(1, n):
        tmp = tmp + k[i] * arr[:, i : w - n + 1 + i]
    out = k[0] * tmp[0 : h - n + 1, :]
    for i in range(1, n):
        out = out + k[i] * tmp[i : h - n + 1 + i, :]
    return out


def _ssim_numpy(a, b):
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    h, w = x.shape
    n = min(11, h, w)
    if n % 2 == 0:
        n -= 1
    k = _gauss_kernel(n, 1.5 * n / 11.0)
    mx = _filter_valid(x, k)
    my = _filter_valid(y, k)
    sxx = _filter_valid(x * x, k) - mx * mx
    syy = _filter_valid(y * y, k) - my * my
    sxy = _filter_valid(x * y, k) - mx * my
    num = (2 * mx * my + C1) * (2 * sxy + C2)
    den = (mx * mx + my * my + C1) * (sxx + syy + C2)
    return float((num / den).mean())


def _ssim_pillow(a, b, block=8):
    w, h = a.size
    # x - y + 128 (clamped): var(x - y) gives the covariance via
    # cov = (var x + var y - var(x - y)) / 2
    diff = ImageChops.subtract(a, b, 1.0, 128)
    if w < block or h < block:
        boxes = [(0, 0, w, h)]
    else:
        boxes = [
            (x, y, x + block, y + block)
            for y in range(0, h - block + 1, block)
            for x in range(0, w - block + 1, block)
        ]
    total = 0.0
    for box in boxes:
        sa = ImageStat.Stat(a.crop(box))
        sb = ImageStat.Stat(b.crop(box))
        sd = ImageStat.Stat(diff.crop(box))
        mx, my = sa.mean[0], sb.mean[0]
        vx, vy = sa.var[0], sb.var[0]
        cov = (vx + vy - sd.var[0]) / 2.0
        total += ((2 * mx * my + C1) * (2 * cov + C2)) / (
            (mx * mx + my * my + C1) * (vx + vy + C2)
        )
    return total / len(boxes)


def luminance(im):
    """8-bit luminance of an image; transparency is flattened onto white."""
    if im.mode == "RGBA":
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("L")
