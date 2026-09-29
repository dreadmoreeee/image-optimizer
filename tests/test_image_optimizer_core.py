import io
import json
from pathlib import Path

import pytest
from PIL import Image

from image_optimizer import core
from image_optimizer.core import Settings

FAST = dict(formats=("webp",), widths=(64, 128, 256))


def run(paths, out, **kw):
    opts = dict(FAST)
    opts.update(kw)
    return core.run([str(p) for p in paths], out, Settings(**opts))


def test_target_widths_never_upscale():
    assert core.target_widths(1000, (480, 768, 1200, 1600)) == [480, 768, 1000]
    assert core.target_widths(2000, (480, 768, 1200, 1600)) == [480, 768, 1200, 1600]
    assert core.target_widths(300, (480, 768)) == [300]
    assert core.target_widths(480, (480, 768)) == [480]


def test_resize_and_no_upscale(gradient, imgdir, tmp_path):
    gradient(200, 100).save(imgdir / "a.png")
    m = run([imgdir / "a.png"], tmp_path / "out")
    widths = [o["width"] for o in m["images"][0]["outputs"]]
    assert widths == [64, 128, 200]
    for o in m["images"][0]["outputs"]:
        with Image.open(tmp_path / "out" / o["path"]) as im:
            assert im.size == (o["width"], o["height"])
    assert m["images"][0]["outputs"][0]["height"] == 32


def test_quality_search_hits_target_and_is_minimal(gradient, noise):
    im = noise(96, 96)
    q, score, data, met = core.search_quality(im, "webp", 0.9, 10, 95)
    assert met and score >= 0.9
    if q > 10:  # one step lower must miss (or be the floor)
        q2, s2, _, _ = core.search_quality(im, "webp", 0.9, q - 1, q - 1)
        assert s2 < 0.9
    assert core.search_quality(im, "webp", 0.9, 10, 95)[0] == q


def test_higher_target_needs_higher_quality(noise):
    im = noise(96, 96)
    lo = core.search_quality(im, "webp", 0.80, 5, 100)
    hi = core.search_quality(im, "webp", 0.97, 5, 100)
    assert hi[0] >= lo[0] and len(hi[2]) >= len(lo[2])


def test_unreachable_target_reported(noise):
    im = noise(64, 64)
    q, score, data, met = core.search_quality(im, "webp", 0.9999, 30, 40)
    assert not met and q == 40 and score < 0.9999


def test_exif_orientation_applied(gradient, imgdir, tmp_path):
    im = gradient(120, 60)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display -> 60x120
    im.save(imgdir / "r.jpg", exif=exif.tobytes())
    m = run([imgdir / "r.jpg"], tmp_path / "out")
    e = m["images"][0]
    assert (e["source_width"], e["source_height"]) == (60, 120)
    o = e["outputs"][-1]
    assert (o["width"], o["height"]) == (60, 120)


def test_metadata_stripped_icc_kept(gradient, imgdir, tmp_path, srgb_icc):
    im = gradient(100, 80)
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    exif[0x9286] = "private note"
    im.save(imgdir / "m.jpg", exif=exif.tobytes(), icc_profile=srgb_icc,
            comment=b"hello comment")
    m = run([imgdir / "m.jpg"], tmp_path / "out")
    assert m["images"][0]["icc_profile_kept"]
    out = tmp_path / "out" / m["images"][0]["outputs"][0]["path"]
    raw = out.read_bytes()
    assert b"SecretCamera" not in raw and b"private note" not in raw
    assert b"hello comment" not in raw
    with Image.open(out) as res:
        assert not res.getexif()
        assert res.info.get("icc_profile") == srgb_icc
        assert "xmp" not in res.info and "comment" not in res.info


def test_xmp_not_carried_over(gradient, imgdir, tmp_path):
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF>SECRETXMP</rdf:RDF></x:xmpmeta>')
    gradient(64, 64).save(imgdir / "x.webp", xmp=xmp, quality=90)
    m = run([imgdir / "x.webp"], tmp_path / "out")
    out = tmp_path / "out" / m["images"][0]["outputs"][0]["path"]
    assert b"SECRETXMP" not in out.read_bytes()


def test_alpha_preserved(imgdir, tmp_path):
    im = Image.new("RGBA", (80, 80), (255, 0, 0, 0))
    for y in range(20, 60):
        for x in range(20, 60):
            im.putpixel((x, y), (0, 0, 255, 255))
    im.save(imgdir / "a.png")
    m = run([imgdir / "a.png"], tmp_path / "out")
    with Image.open(tmp_path / "out" / m["images"][0]["outputs"][-1]["path"]) as res:
        assert res.mode == "RGBA"
        assert res.getpixel((5, 5))[3] == 0
        assert res.getpixel((40, 40))[3] == 255


def test_palette_and_gray_inputs(imgdir, tmp_path, gradient):
    gradient(70, 70).convert("P").save(imgdir / "p.png")
    Image.new("L", (50, 50), 128).save(imgdir / "g.png")
    m = run([imgdir], tmp_path / "out")
    assert len(m["images"]) == 2 and not m["errors"]


def test_dry_run_writes_nothing_but_reports_bytes(gradient, imgdir, tmp_path):
    gradient(150, 100).save(imgdir / "a.jpg")
    out = tmp_path / "out"
    m = run([imgdir / "a.jpg"], out, dry_run=True)
    assert not out.exists()
    assert m["dry_run"] and m["totals"]["outputs"] == 3
    assert all(o["bytes"] > 0 for o in m["images"][0]["outputs"])


def test_manifest_and_snippets_written(gradient, imgdir, tmp_path):
    gradient(150, 100).save(imgdir / "a.jpg")
    out = tmp_path / "out"
    m = run([imgdir / "a.jpg"], out)
    on_disk = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(m))
    o = on_disk["images"][0]["outputs"][0]
    assert set(o) == {"path", "width", "height", "format", "quality", "ssim", "bytes", "met_target"}
    assert o["bytes"] == (out / o["path"]).stat().st_size
    assert "<picture>" in (out / "snippets.html").read_text(encoding="utf-8")


def test_totals_and_savings(gradient, imgdir, tmp_path):
    gradient(300, 200).save(imgdir / "a.png")
    gradient(300, 200).save(imgdir / "b.png")
    m = run([imgdir], tmp_path / "out", widths=(150, 300))
    t = m["totals"]
    src = sum(i["source_bytes"] for i in m["images"])
    assert t["source_bytes"] == src
    w = t["per_format"]["webp"]
    assert w["saved_bytes"] == src - w["largest_variants_bytes"]
    assert w["all_variants_bytes"] >= w["largest_variants_bytes"]
    e = m["images"][0]
    big = [o for o in e["outputs"] if o["width"] == 300][0]
    assert e["per_format"]["webp"]["largest_bytes"] == big["bytes"]


def test_never_overwrites_inputs(gradient, imgdir):
    # a.png would produce a-64.webp, which is itself an input; out dir == in dir
    gradient(64, 64).save(imgdir / "a.png")
    src = imgdir / "a-64.webp"
    gradient(64, 64).save(src, quality=90)
    before = src.read_bytes()
    m = core.run([str(imgdir / "a.png"), str(src)], str(imgdir),
                 Settings(formats=("webp",), widths=(64,)))
    assert src.read_bytes() == before
    assert any("overwrite" in e["error"] for e in m["errors"])


def test_outdir_inside_input_dir_not_reprocessed(gradient, imgdir):
    gradient(64, 64).save(imgdir / "a.png")
    out = imgdir / "out"
    run([imgdir], out)
    again = run([imgdir], out)
    assert len(again["images"]) == 1


def test_name_collisions_get_unique_stems(gradient, imgdir, tmp_path):
    gradient(64, 64).save(imgdir / "photo.png")
    gradient(64, 64).save(imgdir / "photo.jpg")
    m = run([imgdir], tmp_path / "out", widths=(64,))
    paths = [o["path"] for i in m["images"] for o in i["outputs"]]
    assert len(paths) == len(set(paths)) == 2


def test_directory_structure_preserved(gradient, imgdir, tmp_path):
    (imgdir / "sub").mkdir()
    gradient(64, 64).save(imgdir / "sub" / "a.png")
    m = run([imgdir], tmp_path / "out", widths=(64,))
    assert m["images"][0]["outputs"][0]["path"] == "sub/a-64.webp"
    assert (tmp_path / "out" / "sub" / "a-64.webp").exists()


def test_gif_first_frame_optional(imgdir, tmp_path):
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "blue")]
    frames[0].save(imgdir / "g.gif", save_all=True, append_images=frames[1:])
    m = run([imgdir], tmp_path / "o1")
    assert not m["images"] and any("GIF" in n for n in m["notes"])
    m = run([imgdir], tmp_path / "o2", include_gif=True)
    assert len(m["images"]) == 1 and m["images"][0]["animated_source"]


def test_bad_file_does_not_abort_batch(gradient, imgdir, tmp_path):
    (imgdir / "bad.png").write_bytes(b"not an image")
    gradient(64, 64).save(imgdir / "good.png")
    m = run([imgdir], tmp_path / "out")
    assert len(m["images"]) == 1 and len(m["errors"]) == 1


def test_missing_and_unsupported_notes(tmp_path):
    (tmp_path / "x.txt").write_text("hi")
    m = run([tmp_path / "nope.png", tmp_path / "x.txt"], tmp_path / "out")
    assert len(m["notes"]) == 2 and not m["images"]


def test_avif_unavailable_is_reported(monkeypatch, gradient, imgdir, tmp_path):
    monkeypatch.setattr(core, "avif_support", lambda: None)
    gradient(64, 64).save(imgdir / "a.png")
    m = run([imgdir], tmp_path / "out", formats=("webp", "avif"), widths=(64,))
    assert m["avif_support"] == "unavailable"
    assert any("AVIF unavailable" in n for n in m["notes"])
    assert {o["format"] for o in m["images"][0]["outputs"]} == {"webp"}


@pytest.mark.skipif(core.avif_support() is None, reason="no AVIF encoder")
def test_avif_output(gradient, imgdir, tmp_path):
    gradient(96, 64).save(imgdir / "a.png")
    m = run([imgdir], tmp_path / "out", formats=("webp", "avif"), widths=(96,))
    fmts = {o["format"] for o in m["images"][0]["outputs"]}
    assert fmts == {"webp", "avif"}
    avif = [o for o in m["images"][0]["outputs"] if o["format"] == "avif"][0]
    assert (tmp_path / "out" / avif["path"]).read_bytes()[4:12] == b"ftypavif"


def test_pillow_backend_pipeline(gradient, imgdir, tmp_path):
    gradient(96, 64).save(imgdir / "a.png")
    m = run([imgdir], tmp_path / "out", widths=(96,), ssim_backend="pillow")
    assert m["settings"]["ssim_backend"] == "pillow"
    assert m["images"][0]["outputs"][0]["ssim"] >= 0.95
