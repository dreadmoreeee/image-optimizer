"""Build the responsive <picture> snippet for one optimized image."""

import html as _html
from urllib.parse import quote

DEFAULT_SIZES = "(max-width: 1200px) 100vw, 1200px"
DEFAULT_ALT = "TODO: describe this image"
MIME = {"avif": "image/avif", "webp": "image/webp"}
FORMAT_ORDER = ("avif", "webp")  # most efficient first, browsers pick the first they support


def _url(prefix, rel_path):
    return prefix + quote(rel_path, safe="/")


def _srcset(outputs, prefix):
    return ", ".join(
        "%s %dw" % (_url(prefix, o["path"]), o["width"])
        for o in sorted(outputs, key=lambda o: o["width"])
    )


def build_picture(outputs, url_prefix="", sizes=DEFAULT_SIZES, alt=DEFAULT_ALT,
                  loading="lazy", decoding="async"):
    """Return a <picture> element for the outputs of a single source image.

    ``outputs`` is a list of dicts with path (posix, relative to the output
    dir), width, height and format. The fallback <img> uses WebP when present
    (AVIF only if that is all that was produced); its width/height are those
    of the largest variant so the browser can reserve space before loading.
    """
    by_format = {}
    for o in outputs:
        by_format.setdefault(o["format"], []).append(o)
    if not by_format:
        raise ValueError("no outputs to build a snippet from")
    formats = [f for f in FORMAT_ORDER if f in by_format]
    fallback_fmt = "webp" if "webp" in by_format else formats[0]
    fallback = by_format[fallback_fmt]
    largest = max(fallback, key=lambda o: o["width"])
    esc = lambda s: _html.escape(s, quote=True)

    lines = ["<picture>"]
    for fmt in formats:
        lines.append('  <source type="%s" srcset="%s" sizes="%s">'
                     % (MIME[fmt], esc(_srcset(by_format[fmt], url_prefix)), esc(sizes)))
    img = [
        'src="%s"' % esc(_url(url_prefix, largest["path"])),
        'srcset="%s"' % esc(_srcset(fallback, url_prefix)),
        'sizes="%s"' % esc(sizes),
        'width="%d"' % largest["width"],
        'height="%d"' % largest["height"],
        'alt="%s"' % esc(alt),
    ]
    if loading != "none":
        img.append('loading="%s"' % loading)
    if decoding != "none":
        img.append('decoding="%s"' % decoding)
    lines.append("  <img " + " ".join(img) + ">")
    lines.append("</picture>")
    return "\n".join(lines)


def comment_safe(text):
    """Make text safe to place inside an HTML comment."""
    return text.replace("--", "- -").replace(">", "&gt;")
