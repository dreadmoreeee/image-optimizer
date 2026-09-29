"""Planning, resizing, encoding and quality search."""

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps, features

from . import __version__
from .html import DEFAULT_ALT, DEFAULT_SIZES, build_picture, comment_safe
from .ssim import backend_name, luminance, ssim

DEFAULT_WIDTHS = (480, 768, 1200, 1600)
DEFAULT_FORMATS = ("webp", "avif")
DEFAULT_SSIM = 0.95
INPUT_EXTS = (".jpg", ".jpeg", ".png", ".webp")
GIF_EXT = ".gif"
EXT = {"webp": ".webp", "avif": ".avif"}


def avif_support():
    """Return "native", "plugin" or None for AVIF encoding support."""
    if features.check("avif"):
        return "native"
    try:
        import pillow_avif  # noqa: F401  (registers the AVIF plugin)
    except ImportError:
        return None
    return "plugin"


@dataclass
class Settings:
    widths: tuple = DEFAULT_WIDTHS
    formats: tuple = DEFAULT_FORMATS
    ssim_target: float = DEFAULT_SSIM
    min_quality: int = 30
    max_quality: int = 95
    sizes: str = DEFAULT_SIZES
    url_prefix: str = ""
    alt: str = DEFAULT_ALT
    loading: str = "lazy"
    decoding: str = "async"
    include_gif: bool = False
    dry_run: bool = False
    ssim_backend: str = None


@dataclass
class Variant:
    path: str  # posix, relative to the output dir
    width: int
    height: int
    format: str
    quality: int
    ssim: float
    bytes: int
    met_target: bool
    data: bytes = field(default=None, repr=False)

    def as_dict(self):
        return {
            "path": self.path, "width": self.width, "height": self.height,
            "format": self.format, "quality": self.quality,
            "ssim": round(self.ssim, 4), "bytes": self.bytes,
            "met_target": self.met_target,
        }


# ---------------------------------------------------------------- inputs

def collect_inputs(paths, outdir, include_gif=False):
    """Expand files/dirs into [(file, rel_dir)] plus human-readable notes.

    Files found by walking a directory are skipped when they sit inside
    ``outdir`` (so re-running into a folder under an input dir does not
    re-process earlier outputs). Files named explicitly are always kept.
    """
    exts = INPUT_EXTS + ((GIF_EXT,) if include_gif else ())
    outdir = Path(outdir).resolve()
    found, seen, notes = [], set(), []

    def add(f, rel, walked):
        key = f.resolve()
        if key in seen:
            return
        if walked and (outdir == key.parent or outdir in key.parents):
            return
        seen.add(key)
        found.append((f, rel))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                if f.suffix.lower() in exts:
                    add(f, f.parent.relative_to(p), True)
                elif f.suffix.lower() == GIF_EXT:
                    notes.append("skipped %s (GIF: pass --gif to use the first frame)" % f)
        elif p.is_file():
            if p.suffix.lower() in exts:
                add(p, Path("."), False)
            elif p.suffix.lower() == GIF_EXT:
                notes.append("skipped %s (GIF: pass --gif to use the first frame)" % p)
            else:
                notes.append("skipped %s (unsupported type)" % p)
        else:
            notes.append("not found: %s" % p)
    return found, notes


def assign_stems(found):
    """Give every source a unique output stem (relative path without ext)."""
    used, stems = set(), {}
    for f, rel in found:
        base = (rel / f.stem).as_posix()
        if base.startswith("./"):
            base = base[2:]
        cand = base
        if cand.lower() in used:
            cand = "%s-%s" % (base, f.suffix.lower().lstrip("."))
        n = 2
        while cand.lower() in used:
            cand = "%s-%s-%d" % (base, f.suffix.lower().lstrip("."), n)
            n += 1
        used.add(cand.lower())
        stems[f] = cand
    return stems


def target_widths(orig_width, targets):
    """Widths to produce for an image ``orig_width`` wide. Never upscales."""
    widths = {w for w in targets if w <= orig_width}
    if orig_width < max(targets):
        widths.add(orig_width)
    return sorted(widths)


# ---------------------------------------------------------------- pixels

def _icc_is_rgb(icc):
    return bool(icc) and len(icc) > 20 and icc[16:20] == b"RGB "


def load_image(path):
    """Open, apply EXIF orientation and normalize to RGB/RGBA.

    Returns (image, icc_profile_or_None, animated_flag). Metadata other than
    the ICC profile is not carried over.
    """
    with Image.open(path) as im:
        animated = getattr(im, "n_frames", 1) > 1
        im.seek(0)
        icc = im.info.get("icc_profile")
        im.load()
        im = ImageOps.exif_transpose(im)
    if im.mode == "P":
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    elif im.mode in ("LA", "PA"):
        im = im.convert("RGBA")
    elif im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    if not _icc_is_rgb(icc):
        icc = None  # e.g. gray/CMYK profile no longer matches the RGB pixels
    return im, icc, animated


def resize_to(im, width):
    if width == im.width:
        return im
    height = max(1, round(im.height * width / im.width))
    return im.resize((width, height), Image.LANCZOS)


def encode(im, fmt, quality, icc=None):
    buf = io.BytesIO()
    kw = {"quality": quality}
    if icc:
        kw["icc_profile"] = icc
    if fmt == "webp":
        im.save(buf, "WEBP", method=4, **kw)
    elif fmt == "avif":
        im.save(buf, "AVIF", speed=6, **kw)
    else:
        raise ValueError("unsupported format: %s" % fmt)
    return buf.getvalue()


def search_quality(im, fmt, target, lo, hi, icc=None, backend=None):
    """Smallest quality in [lo, hi] whose SSIM (luminance) >= target.

    Binary search, assuming SSIM grows with quality. If even ``hi`` misses
    the target, the ``hi`` encode is returned with met=False.
    Returns (quality, ssim, data, met).
    """
    ref = luminance(im)

    def trial(q):
        data = encode(im, fmt, q, icc)
        with Image.open(io.BytesIO(data)) as dec:
            dec.load()
            score = ssim(ref, luminance(dec.convert("RGBA" if im.mode == "RGBA" else "RGB")), backend)
        return q, score, data

    best = trial(hi)
    if best[1] < target:
        return best + (False,)
    while lo < hi:
        mid = (lo + hi) // 2
        cand = trial(mid)
        if cand[1] >= target:
            best, hi = cand, mid
        else:
            lo = mid + 1
    return best + (True,)


# ---------------------------------------------------------------- pipeline

def optimize_image(path, stem, formats, settings):
    """Compute all variants for one source. Nothing is written here."""
    im, icc, animated = load_image(path)
    src_bytes = Path(path).stat().st_size
    variants = []
    for width in target_widths(im.width, settings.widths):
        resized = resize_to(im, width)
        for fmt in formats:
            q, score, data, met = search_quality(
                resized, fmt, settings.ssim_target, settings.min_quality,
                settings.max_quality, icc, settings.ssim_backend)
            variants.append(Variant(
                path="%s-%d%s" % (stem, width, EXT[fmt]), width=resized.width,
                height=resized.height, format=fmt, quality=q, ssim=score,
                bytes=len(data), met_target=met, data=data))
    entry = {
        "source": str(path), "source_bytes": src_bytes,
        "source_width": im.width, "source_height": im.height,
        "icc_profile_kept": bool(icc), "animated_source": animated,
    }
    return entry, variants


def summarize(entry, variants, formats):
    """Attach per-format before/after figures to an image entry."""
    per_format = {}
    for fmt in formats:
        vs = [v for v in variants if v.format == fmt]
        if not vs:
            continue
        largest = max(vs, key=lambda v: v.width)
        saved = entry["source_bytes"] - largest.bytes
        per_format[fmt] = {
            "largest_width": largest.width, "largest_bytes": largest.bytes,
            "saved_bytes": saved,
            "saved_percent": round(100.0 * saved / entry["source_bytes"], 1)
            if entry["source_bytes"] else 0.0,
            "all_variants_bytes": sum(v.bytes for v in vs),
        }
    entry["per_format"] = per_format
    return entry


def run(paths, outdir, settings, progress=None):
    """Optimize everything; write files unless ``settings.dry_run``.

    ``progress(entry, variants)`` is called after each image. Returns the
    manifest dict (also what --json prints).
    """
    outdir = Path(outdir)
    avif = avif_support()
    notes = []
    formats = []
    for fmt in settings.formats:
        if fmt == "avif" and not avif:
            notes.append("AVIF unavailable: install pillow-avif-plugin or a Pillow "
                         "build with AVIF support; skipping AVIF output")
            continue
        if fmt not in formats:
            formats.append(fmt)
    found, in_notes = collect_inputs(paths, outdir, settings.include_gif)
    notes.extend(in_notes)
    stems = assign_stems(found)
    input_set = {f.resolve() for f, _ in found}
    outdir_abs = outdir.resolve()

    images, errors, snippets = [], [], []
    for f, _rel in found:
        try:
            entry, variants = optimize_image(f, stems[f], formats, settings)
            for v in variants:
                if (outdir_abs / v.path).resolve() in input_set:
                    raise ValueError("refusing to overwrite input file %s" % (outdir_abs / v.path))
            if not settings.dry_run:
                for v in variants:
                    dest = outdir / v.path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(v.data)
            entry["outputs"] = [v.as_dict() for v in variants]
            for v in variants:
                v.data = None
            summarize(entry, variants, formats)
            snippets.append((entry, build_picture(
                entry["outputs"], settings.url_prefix, settings.sizes, settings.alt,
                settings.loading, settings.decoding)))
            images.append(entry)
            if progress:
                progress(entry, variants)
        except Exception as exc:  # one bad file must not abort the batch
            errors.append({"source": str(f), "error": "%s: %s" % (type(exc).__name__, exc)})

    manifest = {
        "tool": "image-optimizer", "version": __version__,
        "dry_run": settings.dry_run,
        "settings": {
            "widths": list(settings.widths), "formats": formats,
            "ssim_target": settings.ssim_target,
            "min_quality": settings.min_quality, "max_quality": settings.max_quality,
            "sizes": settings.sizes, "ssim_backend": backend_name(settings.ssim_backend),
        },
        "avif_support": avif or "unavailable",
        "notes": notes, "images": images, "errors": errors,
        "totals": totals(images, formats),
    }
    if not settings.dry_run and images:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        parts = ["<!-- generated by image-optimizer %s -->" % __version__]
        for entry, snippet in snippets:
            parts.append("<!-- %s -->\n%s" % (comment_safe(Path(entry["source"]).name), snippet))
        (outdir / "snippets.html").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return manifest


def totals(images, formats):
    src = sum(i["source_bytes"] for i in images)
    per_format = {}
    for fmt in formats:
        rows = [i["per_format"][fmt] for i in images if fmt in i["per_format"]]
        if not rows:
            continue
        after = sum(r["largest_bytes"] for r in rows)
        per_format[fmt] = {
            "largest_variants_bytes": after, "saved_bytes": src - after,
            "saved_percent": round(100.0 * (src - after) / src, 1) if src else 0.0,
            "all_variants_bytes": sum(r["all_variants_bytes"] for r in rows),
        }
    return {
        "images": len(images), "source_bytes": src,
        "outputs": sum(len(i["outputs"]) for i in images),
        "headline_format": formats[0] if formats else None,
        "per_format": per_format,
    }
