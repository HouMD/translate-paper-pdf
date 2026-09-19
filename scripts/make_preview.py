#!/usr/bin/env python3
"""Prepare images for Codex view_image calls without needless quality loss.

Defaults preserve quality:
- sources <= --pass-through-mb (1.5 MB) are returned unchanged, no re-encode;
- larger sources are resized to --max-edge px on the long edge (2000) as JPEG q90;
- --crop L,T,W,H extracts a full-resolution region (lossless PNG when small);
- --scale N magnifies a crop (e.g. 2 for reading small subscripts).

Usage:
    python make_preview.py IMAGE [IMAGE ...] [--out DIR] [--max-edge 2000]
        [--quality 90] [--pass-through-mb 1.5] [--force]
        [--crop L,T,W,H] [--scale 2] [--png]

Output (TSV, one line per input):
    path<TAB>out_bytes<TAB>src_bytes<TAB>ratio<TAB>mode
mode is passthrough, jpeg, or png. Pass-through keeps the original path.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

PNG_FALLBACK_MB = 4.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("images", nargs="+", help="source image paths")
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument(
        "--max-edge", type=int, default=2000, help="longest edge in px (default: 2000)"
    )
    parser.add_argument("--quality", type=int, default=90, help="JPEG quality (default: 90)")
    parser.add_argument(
        "--pass-through-mb",
        type=float,
        default=1.5,
        help="return sources at or below this size unchanged (default: 1.5)",
    )
    parser.add_argument(
        "--force", action="store_true", help="convert even when the source is small"
    )
    parser.add_argument(
        "--crop",
        default=None,
        metavar="L,T,W,H",
        help="crop in source pixels before any resizing",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="magnify the crop before the optional downscale (default: 1)",
    )
    parser.add_argument("--png", action="store_true", help="force lossless PNG output")
    return parser


def parse_crop(text: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("expected L,T,W,H")
    return tuple(int(round(float(part))) for part in parts)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print(
            "Pillow is required; run with the Codex bundled python (see SKILL.md)",
            file=sys.stderr,
        )
        return 3

    Image.MAX_IMAGE_PIXELS = None

    try:
        crop = parse_crop(args.crop) if args.crop else None
    except ValueError as exc:
        print(f"bad --crop: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "codex-previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    pass_bytes = int(args.pass_through_mb * 1048576)

    failures = 0
    for raw in args.images:
        src = Path(raw)
        if not src.is_file():
            print(f"missing: {src}", file=sys.stderr)
            failures += 1
            continue
        src_bytes = src.stat().st_size
        if crop is None and not args.force and not args.png and src_bytes <= pass_bytes:
            print(f"{src}\t{src_bytes}\t{src_bytes}\t1.000\tpassthrough")
            continue
        try:
            with Image.open(src) as opened:
                image = ImageOps.exif_transpose(opened)
                if crop is not None:
                    left, top, width, height = crop
                    box = (
                        max(0, left),
                        max(0, top),
                        min(image.width, left + width),
                        min(image.height, top + height),
                    )
                    if box[2] <= box[0] or box[3] <= box[1]:
                        raise ValueError(f"crop {crop} outside image bounds {image.size}")
                    image = image.crop(box)
                if args.scale and args.scale != 1.0:
                    size = (
                        max(1, int(round(image.width * args.scale))),
                        max(1, int(round(image.height * args.scale))),
                    )
                    image = image.resize(size, Image.LANCZOS)
                if max(image.size) > args.max_edge:
                    image.thumbnail((args.max_edge, args.max_edge), Image.LANCZOS)

                stat = src.stat()
                digest = hashlib.sha1(
                    (
                        f"{src.resolve()}|{src_bytes}|{stat.st_mtime_ns}|"
                        f"{args.max_edge}|{args.scale}|{crop}"
                    ).encode("utf-8")
                ).hexdigest()[:8]
                stem = src.stem
                if crop:
                    stem += f"_crop{crop[0]}_{crop[1]}_{crop[2]}_{crop[3]}"

                wants_png = args.png or crop is not None
                if wants_png:
                    if image.mode not in ("RGB", "RGBA", "L", "LA"):
                        image = image.convert("RGBA" if image.mode in ("P", "PA") else "RGB")
                    out = out_dir / f"{stem}_s{args.scale:g}_{digest}.png"
                    image.save(out, optimize=True)
                    if out.stat().st_size > PNG_FALLBACK_MB * 1048576:
                        out.unlink()
                        out = out.with_suffix(".jpg")
                        fallback = image.convert("RGB") if image.mode != "RGB" else image
                        fallback.save(out, quality=args.quality, optimize=True)
                    mode = "png" if out.suffix.lower() == ".png" else "jpeg"
                else:
                    if image.mode in ("RGBA", "LA", "P"):
                        rgba = image.convert("RGBA")
                        background = Image.new("RGB", rgba.size, (255, 255, 255))
                        background.paste(rgba, mask=rgba.split()[-1])
                        image = background
                    else:
                        image = image.convert("RGB")
                    out = out_dir / f"{stem}_{args.max_edge}_{digest}.jpg"
                    image.save(out, quality=args.quality, optimize=True)
                    mode = "jpeg"
        except Exception as exc:  # noqa: BLE001
            print(f"failed: {src}: {exc}", file=sys.stderr)
            failures += 1
            continue

        out_bytes = out.stat().st_size
        ratio = out_bytes / src_bytes if src_bytes else 0.0
        print(f"{out}\t{out_bytes}\t{src_bytes}\t{ratio:.3f}\t{mode}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
