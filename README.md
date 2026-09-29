# image-optimizer

Batch-optimize images for the web with Pillow. It resizes to a set of widths
(never upscaling), converts to WebP (and AVIF when your install can encode
it), and searches for the **lowest quality that still meets a structural
similarity (SSIM) target**. It strips metadata, keeps the ICC profile, applies
EXIF orientation, and writes a ready-to-paste `<picture>` snippet plus a JSON
manifest with before/after bytes.

Python 3.10+. Only hard dependency: Pillow. numpy is optional (faster, exact
SSIM); AVIF is optional (see below). MIT licensed.

## Install

```
pip install .            # Pillow only
pip install ".[fast]"    # + numpy for the standard SSIM
pip install ".[avif]"    # + pillow-avif-plugin (only if your Pillow cannot write AVIF)
```

It also runs without installing: `python -m image_optimizer ...` from the
project folder.

## Usage

```
python -m image_optimizer photos/ hero.jpg -o web-img
python -m image_optimizer photos/ -o web-img --widths 400,800,1600 --ssim 0.97
python -m image_optimizer photos/ -o web-img --formats webp --url-prefix /img/
python -m image_optimizer photos/ -o web-img --dry-run --json > report.json
```

Directories are searched recursively (jpg, jpeg, png, webp; GIF with `--gif`).
Output files are named `<name>-<width>.<ext>`, keeping sub-folder structure.
Written to the output dir: the images, `manifest.json`, `snippets.html`.

Exit codes: 0 ok, 1 some file failed (the rest are still processed), 2 usage
error or no images found.

## Options

| Option | Default | Meaning |
|---|---|---|
| `INPUT...` | | files and/or directories |
| `-o, --outdir` | `optimized` | output directory |
| `--widths` | `480,768,1200,1600` | target widths; never upscaled. If the source is narrower than the largest target, its own width is added |
| `--formats` | `webp,avif` | `webp` and/or `avif`; AVIF is skipped with a clear message when unavailable |
| `--ssim` | `0.95` | minimum luminance SSIM to keep |
| `--min-quality` / `--max-quality` | `30` / `95` | search range |
| `--sizes` | `(max-width: 1200px) 100vw, 1200px` | `sizes` attribute in the snippet |
| `--url-prefix` | empty | prefix for URLs in the snippet, e.g. `/img/` |
| `--alt` | `TODO: describe this image` | alt placeholder (deliberately not empty, so you do not ship it by accident) |
| `--loading` | `lazy` | `lazy`, `eager` or `none` (omit) |
| `--decoding` | `async` | `async`, `sync`, `auto` or `none` (omit) |
| `--gif` | off | also process GIFs, first frame only |
| `--dry-run` | off | do all the work in memory and report; write no files |
| `--json` | off | manifest on stdout; human output goes to stderr |
| `--ssim-backend` | auto | force `numpy` or `pillow` |

Input files are never overwritten: if an output path would equal an input
file, that image is refused with an error.

## How it works

1. Open the image, apply EXIF orientation (`exif_transpose`), normalize to
   RGB/RGBA. Only the ICC profile is carried over (RGB profiles only; a gray or
   CMYK profile would not match the converted pixels, so it is dropped). EXIF,
   XMP and comments are not written.
2. For each target width (Lanczos resize, no upscaling) and each format, binary
   search the integer quality in `[min, max]` for the smallest value whose
   encode -> decode round trip scores SSIM >= target against the *resized*
   image. The top quality is tried first: if it misses the target, that encode
   is kept and flagged `met_target: false`.
3. Write files, `manifest.json`, `snippets.html`.

`--dry-run` still encodes in memory (that is the only way to know the real
byte counts), so it takes as long as a real run, but touches no files.

### SSIM

Computed on 8-bit luminance (`L = 0.299 R + 0.587 G + 0.114 B`; transparent
pixels are flattened onto white first):

```
SSIM = ((2*mx*my + C1) * (2*sxy + C2)) / ((mx^2 + my^2 + C1) * (sx^2 + sy^2 + C2))
C1 = (0.01*255)^2   C2 = (0.03*255)^2
```

* **numpy backend**: 11x11 Gaussian window (sigma 1.5), valid region, mean of
  the SSIM map (the standard Wang et al. setup).
* **pillow backend** (no numpy): non-overlapping 8x8 blocks using ImageStat;
  covariance comes from `(var x + var y - var(x-y)) / 2`. It is coarser and
  slower, and scores are close to but not identical with the numpy ones. Use
  one backend consistently within a project. The header line of every run says
  which one was used.

Limits: it ignores colour (chroma damage can hide), it compares against the
resized image (so it measures codec loss, not resampling), it ignores the alpha
channel, and 0.95 is a sensible default, not a perceptual guarantee. Look at
your images.

### HTML snippet

```html
<picture>
  <source type="image/avif" srcset="a-480.avif 480w, a-768.avif 768w" sizes="...">
  <source type="image/webp" srcset="a-480.webp 480w, a-768.webp 768w" sizes="...">
  <img src="a-768.webp" srcset="a-480.webp 480w, a-768.webp 768w" sizes="..."
       width="768" height="512" alt="TODO: describe this image" loading="lazy" decoding="async">
</picture>
```

`width`/`height` are those of the largest output, so the browser can reserve
space (no layout shift). The `sizes` default is a guess for a content column
up to 1200px; set it to match your layout. Do not lazy-load your LCP image
(use `--loading eager` for it).

## AVIF

AVIF is written only if it can be: Pillow 11.3+ built with libavif (`python -c
"from PIL import features; print(features.check('avif'))"`), or the
`pillow-avif-plugin` package installed. Otherwise the CLI prints
`AVIF: UNAVAILABLE` in its first line, a `note:` explaining the skip, records
`"avif_support": "unavailable"` in the manifest, and produces WebP only.

## Limitations

* Output is WebP/AVIF only; there is no JPEG/PNG fallback file. The fallback
  `<img>` is WebP, which every current browser supports.
* "After" bytes in the report are the **largest** variant per format (what a
  wide desktop screen downloads), compared with the original file. Sources
  wider than the largest target also gain from the downscale, not only from
  the codec. "All variants together" is reported too. Very small or already
  well-compressed sources can come out **larger**; the report shows negative
  savings honestly and does not skip them.
* If the quality floor (`--min-quality`) is hit, SSIM is above your target and
  the file could be smaller with a lower floor.
* Animated GIFs lose their animation (first frame only, opt-in).
* SSIM is a proxy. The search runs one encode per step, so AVIF on large
  batches is slow.
* No parallelism, no cropping/art direction, no CMYK colour-managed conversion.

## Tests

```
python -m pytest -q -p no:cacheprovider --import-mode=importlib image-optimizer
```

Test images are generated with Pillow (gradients, noise, EXIF orientation, ICC
profile, alpha); no network is used.

## Measured result

Test suite: `49 passed in 2.86s` (Windows, Python 3.12, Pillow 12.3, numpy 2.5).

Smoke run on two **synthetic** images generated with Pillow (a grainy 2400x1600
JPEG and a flat 1000x600 PNG). These are not real-world photos, and both hit
the quality floor (q30), so the percentages only show the pipeline works; they
are not a benchmark:

```
image-optimizer 1.0.0 | SSIM backend: numpy | AVIF: available (Pillow)
...
TOTAL 2 image(s), 14 output file(s)
  before (originals):              393.3 KB
  after, largest webp variant:      14.7 KB   saved 378.6 KB (96.3%)
  all webp variants together:       33.5 KB
  after, largest avif variant:       7.7 KB   saved 385.6 KB (98.1%)
  all avif variants together:       19.9 KB
```

`examples/` holds the manifest and snippet from that run.

Real run on a copy of the five large images of my portfolio (marvin.demarkstudio.ca/img; the originals were not touched). Four are already WebP screenshots at 1200 px, one is a JPEG portrait:

```
$ image-optimizer scratch/portfolio-img -o scratch/portfolio-out --widths 640,1200 --sizes "(max-width: 700px) 100vw, 640px"
...

TOTAL 5 image(s), 18 output file(s)
  before (originals):              206.8 KB
  after, largest webp variant:     104.2 KB   saved 102.6 KB (49.6%)
  all webp variants together:      147.6 KB
  after, largest avif variant:      79.8 KB   saved 127.0 KB (61.4%)
  all avif variants together:      115.6 KB
wrote manifest.json and snippets.html to scratch/portfolio-out
```

With the default threshold (SSIM 0.95) the WebP at 1200 px is about half the size. I compared the triple-a screenshot side by side at 4x zoom: the text is as sharp as before, but the wood texture in the photo is visibly smoother. For photos with fine texture a stricter threshold is the honest choice:

```
$ image-optimizer scratch/portfolio-img -o scratch/portfolio-out-097 --widths 640,1200 --ssim 0.97 --formats webp
TOTAL 5 image(s), 9 output file(s)
  before (originals):              206.8 KB
  after, largest webp variant:     134.0 KB   saved 72.8 KB (35.2%)
```

So on images that were already WebP the realistic saving is about a third, not half. The site itself is unchanged; this is an offline result.

---

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.
