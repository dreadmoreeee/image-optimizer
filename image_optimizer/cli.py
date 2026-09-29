"""Command-line interface."""

import argparse
import json
import re
import sys

from . import __version__
from .core import (DEFAULT_FORMATS, DEFAULT_SSIM, DEFAULT_WIDTHS, Settings,
                   avif_support, run)
from .html import DEFAULT_ALT, DEFAULT_SIZES
from .ssim import backend_name


def fmt_bytes(n):
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 1024:
        return "%s%d B" % (sign, n)
    if n < 1024 * 1024:
        return "%s%.1f KB" % (sign, n / 1024.0)
    return "%s%.2f MB" % (sign, n / 1024.0 / 1024.0)


def _split(value):
    return [p for p in re.split(r"[,\s]+", value.strip()) if p]


def build_parser():
    p = argparse.ArgumentParser(
        prog="image-optimizer",
        description="Batch-optimize images for the web: resize, convert to WebP/AVIF "
                    "at the smallest quality that keeps SSIM above a threshold, "
                    "and emit <picture> snippets.")
    p.add_argument("inputs", nargs="+", metavar="INPUT", help="image files and/or directories (recursive)")
    p.add_argument("-o", "--outdir", default="optimized", help="output directory (default: optimized)")
    p.add_argument("--widths", default=",".join(map(str, DEFAULT_WIDTHS)),
                   help="comma-separated target widths, never upscaled (default: %(default)s)")
    p.add_argument("--formats", default=",".join(DEFAULT_FORMATS),
                   help="comma-separated: webp, avif (default: %(default)s; AVIF only if available)")
    p.add_argument("--ssim", type=float, default=DEFAULT_SSIM, metavar="T",
                   help="minimum luminance SSIM to keep (default: %(default)s)")
    p.add_argument("--min-quality", type=int, default=30, help="lowest quality tried (default: 30)")
    p.add_argument("--max-quality", type=int, default=95, help="highest quality tried (default: 95)")
    p.add_argument("--sizes", default=DEFAULT_SIZES, help="value of the sizes attribute (default: %(default)r)")
    p.add_argument("--url-prefix", default="", help="prefix for URLs in snippets, e.g. /img/")
    p.add_argument("--alt", default=DEFAULT_ALT, help="alt text placeholder in snippets")
    p.add_argument("--loading", choices=["lazy", "eager", "none"], default="lazy",
                   help="loading attribute (default: lazy; none omits it)")
    p.add_argument("--decoding", choices=["async", "sync", "auto", "none"], default="async",
                   help="decoding attribute (default: async; none omits it)")
    p.add_argument("--gif", action="store_true", help="also process GIFs (first frame only)")
    p.add_argument("--dry-run", action="store_true",
                   help="do all the work in memory and report, but write no files")
    p.add_argument("--json", action="store_true", help="print the manifest as JSON on stdout")
    p.add_argument("--ssim-backend", choices=["numpy", "pillow"], default=None,
                   help="force an SSIM backend (default: numpy if installed)")
    p.add_argument("--version", action="version", version="image-optimizer " + __version__)
    return p


def parse_settings(args, parser):
    try:
        widths = tuple(sorted({int(w) for w in _split(args.widths)}))
    except ValueError:
        parser.error("--widths must be integers, e.g. 480,768,1200")
    if not widths or widths[0] < 1:
        parser.error("--widths needs at least one positive width")
    formats = []
    for f in _split(args.formats.lower()):
        if f not in ("webp", "avif"):
            parser.error("unknown format %r (use webp and/or avif)" % f)
        if f not in formats:
            formats.append(f)
    if not formats:
        parser.error("--formats needs at least one format")
    if not 0 < args.ssim <= 1:
        parser.error("--ssim must be in (0, 1]")
    if not 1 <= args.min_quality <= args.max_quality <= 100:
        parser.error("need 1 <= --min-quality <= --max-quality <= 100")
    if args.ssim_backend == "numpy" and backend_name() != "numpy":
        parser.error("--ssim-backend numpy requires numpy")
    return Settings(
        widths=widths, formats=tuple(formats), ssim_target=args.ssim,
        min_quality=args.min_quality, max_quality=args.max_quality, sizes=args.sizes,
        url_prefix=args.url_prefix, alt=args.alt, loading=args.loading,
        decoding=args.decoding, include_gif=args.gif, dry_run=args.dry_run,
        ssim_backend=args.ssim_backend)


def _print_image(entry, variants, out):
    out.write("%s  %dx%d  %s\n" % (entry["source"], entry["source_width"],
                                   entry["source_height"], fmt_bytes(entry["source_bytes"])))
    for v in variants:
        flag = "" if v.met_target else "  (below target at max quality)"
        out.write("  %5dw %-4s q%-3d ssim %.4f  %10s%s\n" % (
            v.width, v.format, v.quality, v.ssim, fmt_bytes(v.bytes), flag))
    for fmt, r in entry["per_format"].items():
        out.write("  -> %s %dw: %s vs %s original, saved %s (%.1f%%)\n" % (
            fmt, r["largest_width"], fmt_bytes(r["largest_bytes"]),
            fmt_bytes(entry["source_bytes"]), fmt_bytes(r["saved_bytes"]), r["saved_percent"]))
    out.flush()


def print_summary(manifest, out):
    t = manifest["totals"]
    out.write("\nTOTAL %d image(s), %d output file(s)%s\n" % (
        t["images"], t["outputs"], " [dry run: nothing written]" if manifest["dry_run"] else ""))
    out.write("  before (originals):          %12s\n" % fmt_bytes(t["source_bytes"]))
    for fmt, r in t["per_format"].items():
        out.write("  after, largest %-4s variant: %12s   saved %s (%.1f%%)\n" % (
            fmt, fmt_bytes(r["largest_variants_bytes"]), fmt_bytes(r["saved_bytes"]),
            r["saved_percent"]))
        out.write("  all %-4s variants together:  %12s\n" % (fmt, fmt_bytes(r["all_variants_bytes"])))
    for e in manifest["errors"]:
        out.write("ERROR %s: %s\n" % (e["source"], e["error"]))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = parse_settings(args, parser)
    text_out = sys.stderr if args.json else sys.stdout

    avif = avif_support()
    text_out.write("image-optimizer %s | SSIM backend: %s | AVIF: %s\n" % (
        __version__, backend_name(settings.ssim_backend),
        {"native": "available (Pillow)", "plugin": "available (pillow-avif-plugin)",
         None: "UNAVAILABLE (install pillow-avif-plugin); only WebP will be written"}[avif]))

    manifest = run(args.inputs, args.outdir, settings,
                   progress=lambda e, v: _print_image(e, v, text_out))
    for note in manifest["notes"]:
        text_out.write("note: %s\n" % note)
    if args.json:
        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    else:
        print_summary(manifest, sys.stdout)
        if not manifest["dry_run"] and manifest["images"]:
            sys.stdout.write("wrote manifest.json and snippets.html to %s\n" % args.outdir)
    if not manifest["images"] and not manifest["errors"]:
        sys.stderr.write("error: no supported images found\n")
        return 2
    return 1 if manifest["errors"] else 0
