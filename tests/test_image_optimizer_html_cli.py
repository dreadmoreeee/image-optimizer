import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from image_optimizer import cli
from image_optimizer.html import build_picture

TOOL_DIR = Path(__file__).resolve().parent.parent


def outs(fmts=("avif", "webp"), widths=(480, 768, 1000)):
    return [{"path": "img/a-%d.%s" % (w, f), "width": w, "height": w // 2, "format": f}
            for f in fmts for w in widths]


def test_picture_structure():
    html = build_picture(outs(), url_prefix="/static/", sizes="100vw")
    assert html.startswith("<picture>") and html.endswith("</picture>")
    assert html.index('type="image/avif"') < html.index('type="image/webp"') < html.index("<img")
    assert 'srcset="/static/img/a-480.webp 480w, /static/img/a-768.webp 768w, /static/img/a-1000.webp 1000w"' in html
    assert 'src="/static/img/a-1000.webp"' in html
    assert 'width="1000" height="500"' in html
    assert 'sizes="100vw"' in html
    assert 'loading="lazy"' in html and 'decoding="async"' in html
    assert 'alt="TODO: describe this image"' in html


def test_picture_without_avif_and_options():
    html = build_picture(outs(fmts=("webp",)), loading="none", decoding="none", alt='a "b" <c>')
    assert "image/avif" not in html and html.count("<source") == 1
    assert "loading=" not in html and "decoding=" not in html
    assert 'alt="a &quot;b&quot; &lt;c&gt;"' in html


def test_picture_escapes_paths():
    o = [{"path": "my pics/a b-100.webp", "width": 100, "height": 50, "format": "webp"}]
    assert "my%20pics/a%20b-100.webp" in build_picture(o)


def test_picture_needs_outputs():
    with pytest.raises(ValueError):
        build_picture([])


def test_fmt_bytes():
    assert cli.fmt_bytes(512) == "512 B"
    assert cli.fmt_bytes(2048) == "2.0 KB"
    assert cli.fmt_bytes(-2048) == "-2.0 KB"
    assert cli.fmt_bytes(3 * 1024 * 1024) == "3.00 MB"


def test_cli_text_run(gradient, imgdir, tmp_path, capsys):
    gradient(200, 100).save(imgdir / "a.png")
    rc = cli.main([str(imgdir), "-o", str(tmp_path / "out"), "--widths", "100,200",
                   "--formats", "webp"])
    out = capsys.readouterr().out
    assert rc == 0 and "TOTAL 1 image(s)" in out and "saved" in out
    assert "SSIM backend" in out
    assert (tmp_path / "out" / "a-200.webp").exists()


def test_cli_json_is_clean_stdout(gradient, imgdir, tmp_path, capsys):
    gradient(200, 100).save(imgdir / "a.png")
    rc = cli.main([str(imgdir), "-o", str(tmp_path / "out"), "--widths", "200",
                   "--formats", "webp", "--json", "--dry-run"])
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert rc == 0 and data["dry_run"] and data["totals"]["images"] == 1
    assert not (tmp_path / "out").exists()
    assert "SSIM backend" in cap.err


def test_cli_reports_avif_unavailable(monkeypatch, gradient, imgdir, tmp_path, capsys):
    monkeypatch.setattr(cli, "avif_support", lambda: None)
    from image_optimizer import core
    monkeypatch.setattr(core, "avif_support", lambda: None)
    gradient(64, 64).save(imgdir / "a.png")
    cli.main([str(imgdir), "-o", str(tmp_path / "o"), "--widths", "64", "--dry-run"])
    out = capsys.readouterr().out
    assert "UNAVAILABLE" in out and "AVIF unavailable" in out


def test_cli_no_images_exit_2(tmp_path, capsys):
    assert cli.main([str(tmp_path), "-o", str(tmp_path / "o")]) == 2


@pytest.mark.parametrize("bad", [["--widths", "abc"], ["--ssim", "1.5"],
                                 ["--formats", "png"], ["--min-quality", "90", "--max-quality", "50"]])
def test_cli_rejects_bad_options(bad, tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main([str(tmp_path)] + bad)
    assert e.value.code == 2


def test_python_dash_m(gradient, imgdir, tmp_path):
    gradient(64, 64).save(imgdir / "a.png")
    res = subprocess.run(
        [sys.executable, "-m", "image_optimizer", str(imgdir), "-o", str(tmp_path / "o"),
         "--widths", "64", "--formats", "webp", "--dry-run"],
        cwd=str(TOOL_DIR), capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert re.search(r"TOTAL 1 image", res.stdout)


def test_sources_are_ascii():
    for f in (TOOL_DIR / "image_optimizer").glob("*.py"):
        f.read_bytes().decode("ascii")
