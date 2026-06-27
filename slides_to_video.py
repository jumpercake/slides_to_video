#!/usr/bin/env python3
"""
slides_to_video.py  (formerly slides_to_video_4k.py)
Convert a folder of PNG images and/or .pptx/.odp files into a single
looping MP4 video.  All slides from every file are included.  The
last->first crossfade is baked into the video so that VLC's --loop
produces a seamless result.

Dependencies:
    Linux : sudo apt install libreoffice ffmpeg poppler-utils python3-pil
    macOS : brew install libreoffice ffmpeg poppler && pip install pillow

Usage:
    python3 slides_to_video.py [OPTIONS] /path/to/slides/folder

Quality presets (--preset):
    hd   : 1920x1080, pdftoppm 150 DPI  (default -- fast, small files)
    4k   : 3840x2160, pdftoppm 300 DPI  (for 4K TVs -- larger files)

    --preset overrides --resolution and --dpi.  You can still combine
    --preset with other flags (e.g. -d, -t, --no-counter).

Examples:
    python3 slides_to_video.py ~/slides/
    python3 slides_to_video.py --preset 4k -o church_4k.mp4 ~/slides/
    python3 slides_to_video.py -o conference.mp4 -d 6 -t 1.5 ~/slides/
    cvlc --loop --fullscreen output.mp4
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False


# -----------------------------------------------------------------------------
# Supported input extensions
# -----------------------------------------------------------------------------
PRESENTATION_EXTENSIONS = {".pptx", ".odp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SLIDE_EXTENSIONS = PRESENTATION_EXTENSIONS | IMAGE_EXTENSIONS

# Quality presets  {name: (resolution, dpi)}
PRESETS = {
    "hd": ("1920x1080", 150),
    "4k": ("3840x2160", 300),
}

INSTALL_HINT = (
    "  Linux : sudo apt install libreoffice ffmpeg poppler-utils python3-pil\n"
    "  macOS : brew install libreoffice ffmpeg poppler && pip install pillow"
)


# -----------------------------------------------------------------------------
# Colour helpers
# -----------------------------------------------------------------------------
class C:
    CYAN = "\033[0;36m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    RESET = "\033[0m"


def info(msg):
    print(f"{C.CYAN}[INFO]{C.RESET}  {msg}")


def success(msg):
    print(f"{C.GREEN}[OK]{C.RESET}    {msg}")


def warn(msg):
    print(f"{C.YELLOW}[WARN]{C.RESET}  {msg}")


def error(msg):
    print(f"{C.RED}[ERROR]{C.RESET} {msg}", file=sys.stderr)


# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert PNG images and/or .pptx/.odp files into a seamlessly looping MP4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
    parser.add_argument(
        "input_dir",
        metavar="FOLDER",
        help="Folder containing PNG images and/or .pptx/.odp files",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output.mp4",
        metavar="FILE",
        help="Output video file (default: output.mp4)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=10.0,
        metavar="SECS",
        help="Duration per slide in seconds (default: 10)",
    )
    parser.add_argument(
        "-t",
        "--transition",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Crossfade transition duration in seconds (default: 1)",
    )

    # Quality preset -- convenience shortcut that sets both resolution and DPI
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        default=None,
        metavar="NAME",
        help=(
            "Quality preset: hd (1920x1080, 150 DPI) or "
            "4k (3840x2160, 300 DPI). "
            "Overrides --resolution and --dpi when specified."
        ),
    )
    parser.add_argument(
        "-r",
        "--resolution",
        default="1920x1080",
        metavar="WxH",
        help="Output resolution (default: 1920x1080). Ignored if --preset is set.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        metavar="DPI",
        help=(
            "DPI used when rasterising .pptx/.odp via pdftoppm "
            "(default: 150 for HD, use 300 for 4K). "
            "Ignored if --preset is set. Has no effect on PNG inputs."
        ),
    )

    parser.add_argument(
        "-f",
        "--fps",
        type=int,
        default=60,
        metavar="FPS",
        help="Frames per second (default: 60)",
    )
    parser.add_argument(
        "-k",
        "--keep-temp",
        action="store_true",
        help="Keep temporary files after completion",
    )
    parser.add_argument(
        "--no-counter",
        action="store_true",
        help="Disable the slide number overlay (e.g. '2/5')",
    )
    parser.add_argument(
        "--counter-pos",
        default="bottom-right",
        choices=["bottom-right", "bottom-left", "top-right", "top-left"],
        help="Position of the slide counter (default: bottom-right)",
    )
    parser.add_argument(
        "--counter-size",
        type=int,
        default=36,
        metavar="PX",
        help="Font size of the slide counter in pixels (default: 36)",
    )
    return parser.parse_args()


# -----------------------------------------------------------------------------
# Dependency check
# -----------------------------------------------------------------------------
def check_dependencies(need_pillow: bool):
    missing_sys = [
        cmd for cmd in ("libreoffice", "ffmpeg", "pdftoppm") if not shutil.which(cmd)
    ]
    if missing_sys:
        error(f"Missing required tool(s): {', '.join(missing_sys)}")
        error(f"Install:\n{INSTALL_HINT}")
        sys.exit(1)

    if need_pillow and not PILLOW_AVAILABLE:
        error("Pillow is required for the rounded counter badge.")
        error(f"Install:\n{INSTALL_HINT}")
        sys.exit(1)


# -----------------------------------------------------------------------------
# Find slide files
# -----------------------------------------------------------------------------
def find_slide_files(input_dir: Path) -> list[Path]:
    candidates = [
        p
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SLIDE_EXTENSIONS
    ]
    candidates.sort(key=lambda p: p.name.lower())

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def split_inputs(files: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split file list into (presentation_files, image_files)."""
    presentations = [f for f in files if f.suffix.lower() in PRESENTATION_EXTENSIONS]
    images = [f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS]
    return presentations, images


# -----------------------------------------------------------------------------
# Step 1: Convert slide files → PNG images
# -----------------------------------------------------------------------------
def collect_png_inputs(image_files: list[Path]) -> list[Path]:
    """
    PNG/JPG files from Canva (or any source) are used directly — no conversion
    needed.  They are returned as-is in sorted order.
    """
    if image_files:
        info(
            f"  Using {len(image_files)} image file(s) directly (no conversion needed)."
        )
        for p in image_files:
            info(f"    {p.name}")
    return list(image_files)


def convert_presentations_to_images(
    slide_files: list[Path], slides_dir: Path, dpi: int
) -> list[Path]:
    """
    Convert every .pptx/.odp to per-slide PNGs.

    Pipeline per file:
        source  ->  libreoffice --headless -> source.pdf   (all slides preserved)
        source.pdf  ->  pdftoppm -png -r <dpi>  ->  slide-NNN.png per page

    pdftoppm (from poppler-utils) is the correct tool for PDF->PNG on Debian.
    ffmpeg cannot read PDFs -- it is not a PDF renderer.

    DPI guide:
        150 DPI  ->  ~1920x1080 output  (HD, fast)
        300 DPI  ->  ~3840x2160 output  (4K, slower, larger intermediates)
    """
    def page_number(p: Path) -> int:
        m = re.search(r"-(\d+)\.png$", p.name)
        return int(m.group(1)) if m else 0

    all_slides: list[Path] = []

    for slide_file in slide_files:
        safe_name = re.sub(r"[^\w\-]", "_", slide_file.stem)
        subdir = slides_dir / safe_name
        subdir.mkdir(parents=True, exist_ok=True)

        info(f"  Converting: {slide_file.name}  [{slide_file.suffix.lower()}]")

        # 1a: Convert to PDF -- LibreOffice reliably exports all slides to PDF
        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(subdir),
                str(slide_file),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            error(f"LibreOffice failed on: {slide_file.name}")
            error(result.stderr)
            sys.exit(1)

        pdf_files = list(subdir.glob("*.pdf"))
        if not pdf_files:
            error(f"No PDF produced from: {slide_file.name}")
            sys.exit(1)
        pdf_path = pdf_files[0]

        # 1b: Rasterise each PDF page to PNG using pdftoppm
        #     -png            : output format
        #     -r <dpi>        : resolution (150 = HD, 300 = 4K)
        #     output prefix   : subdir/slide -> produces subdir/slide-1.png, slide-2.png ...
        png_prefix = str(subdir / "slide")
        result = subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), png_prefix],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            error(f"pdftoppm failed for: {slide_file.name}")
            error(result.stderr)
            sys.exit(1)

        pngs = sorted(subdir.glob("slide-*.png"), key=page_number)

        if not pngs:
            error(f"No slide images produced from: {slide_file.name}")
            sys.exit(1)

        success(f"    -> {len(pngs)} slide(s) from {slide_file.name}  (DPI: {dpi})")
        for p in pngs:
            info(f"      {p.name}")
        all_slides.extend(pngs)

    return all_slides


# -----------------------------------------------------------------------------
# Step 2: Encode each slide as a short video clip
# -----------------------------------------------------------------------------
def make_badge_overlay(
    label: str, font_size: int, badges_dir: Path, index: int
) -> Path:
    padding_x = font_size
    padding_y = font_size // 2
    radius = font_size // 2

    font = None
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ):
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    badge_w = text_w + padding_x * 2
    badge_h = text_h + padding_y * 2

    img = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [(0, 0), (badge_w - 1, badge_h - 1)],
        radius=radius,
        fill=(0, 0, 0, 140),
    )
    draw.text(
        (padding_x - bbox[0], padding_y - bbox[1]),
        label,
        font=font,
        fill=(255, 255, 255, 255),
    )

    out_path = badges_dir / f"badge_{index:05d}.png"
    img.save(out_path)
    return out_path


def encode_slide_clip(
    slide: Path,
    clip_path: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
    badge_path: Path | None,
    counter_pos: str,
) -> None:
    margin = 20
    pos_expr = {
        "bottom-right": f"W-w-{margin}:H-h-{margin}",
        "bottom-left": f"{margin}:H-h-{margin}",
        "top-right": f"W-w-{margin}:{margin}",
        "top-left": f"{margin}:{margin}",
    }[counter_pos]

    if badge_path is not None:
        escaped = str(badge_path).replace("\\", "\\\\").replace(":", "\\:")
        filter_complex = (
            f"[0:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"format=yuva420p[base];"
            f"movie='{escaped}',format=rgba[badge];"
            f"[base][badge]overlay={pos_expr},format=yuv420p[out]"
        )
        vf_args = ["-filter_complex", filter_complex, "-map", "[out]"]
    else:
        vf_args = [
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"format=yuv420p"
            ),
        ]

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(slide),
            *vf_args,
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            str(clip_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error(f"FFmpeg failed encoding clip for: {slide.name}")
        error(result.stderr)
        sys.exit(1)


def encode_all_clips(
    slides: list[Path],
    clips_dir: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
    show_counter: bool,
    counter_pos: str,
    counter_size: int,
    badges_dir: Path | None,
) -> list[Path]:
    clips: list[Path] = []
    total = len(slides)

    for i, slide in enumerate(slides):
        clip = clips_dir / f"clip_{i:05d}.mp4"
        clips.append(clip)

        badge_path = None
        if show_counter and PILLOW_AVAILABLE and badges_dir is not None:
            badge_path = make_badge_overlay(
                f"{i + 1}/{total}", counter_size, badges_dir, i
            )

        encode_slide_clip(
            slide, clip, width, height, fps, duration, badge_path, counter_pos
        )
        info(f"  Clip {i + 1}/{total}: {slide.name}")

    return clips


# -----------------------------------------------------------------------------
# Step 3: Build xfade filter graph
# -----------------------------------------------------------------------------
def build_xfade_filter(n: int, slide_duration: float, transition: float) -> str:
    """
    Chain n clips with xfade crossfades.
    Offset for transition i = hold * i  where hold = slide_duration - transition.
    """
    hold = slide_duration - transition
    parts = []
    prev = "[0:v]"

    for i in range(1, n):
        offset = round(hold * i, 3)
        out = "[vout]" if i == n - 1 else f"[v{i:04d}]"
        parts.append(
            f"{prev}[{i}:v]xfade=transition=fade"
            f":duration={transition}:offset={offset}{out}"
        )
        prev = out

    return ";".join(parts)


def run_ffmpeg_assemble(
    clips: list[Path],
    output: Path,
    slide_duration: float,
    transition: float,
    trim: float | None = None,
) -> None:
    if len(clips) == 1:
        shutil.copy(clips[0], output)
        return

    input_args: list[str] = []
    for clip in clips:
        input_args += ["-i", str(clip)]

    filter_graph = build_xfade_filter(len(clips), slide_duration, transition)

    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-filter_complex",
        filter_graph,
        "-map",
        "[vout]",
    ]
    if trim is not None:
        cmd += ["-t", str(trim)]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error("FFmpeg failed during assembly.")
        error(result.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------------
# Step 4: Assemble with baked loop transition
# -----------------------------------------------------------------------------
def assemble_looping_video(
    clips: list[Path],
    output: str,
    slide_duration: float,
    transition: float,
    work_dir: Path,
) -> None:
    """
    Produce a video where VLC's --loop gives a seamless crossfade.

    cvlc --loop jumps instantly from the last frame back to frame 0 —
    it cannot crossfade across that boundary. The solution is to bake
    the loop fade into the file so both ends meet at the same frame:

        1. Build the full video including the last→first crossfade.
        2. Find the midpoint of that final fade:
               split_t = hold * n_real + transition / 2
        3. Extract tail = full_video[split_t → end]
           Extract head = full_video[0 → split_t]
        4. Output = concat(tail, head)

    The output now starts and ends at the visual midpoint of the
    last→first fade. VLC's loop jump lands on an identical frame,
    and the fade continues without any visible discontinuity.
    """
    if len(clips) == 1:
        info("  Only one clip — copying directly to output.")
        shutil.copy(clips[0], output)
        return

    n_real = len(clips)
    hold = slide_duration - transition
    half_fade = transition / 2.0

    # Full video: all real clips + copy of first clip to provide the fade target
    full_end = round(hold * n_real + transition, 4)
    split_t = round(hold * n_real + half_fade, 4)

    info(f"  Building full linear video ({n_real} slides + last→first fade)...")
    loop_clips = clips + [clips[0]]
    full_video = work_dir / "full_linear.mp4"
    run_ffmpeg_assemble(
        loop_clips, full_video, slide_duration, transition, trim=full_end
    )

    info(f"  Splitting at fade midpoint {split_t}s...")

    # Extract tail: split_t → end
    tail_video = work_dir / "tail.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(split_t),
            "-i",
            str(full_video),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            str(tail_video),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error("FFmpeg failed extracting tail segment.")
        error(result.stderr)
        sys.exit(1)

    # Extract head: 0 → split_t
    head_video = work_dir / "head.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(full_video),
            "-t",
            str(split_t),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            str(head_video),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error("FFmpeg failed extracting head segment.")
        error(result.stderr)
        sys.exit(1)

    # Concatenate tail + head
    info("  Concatenating tail + head for seamless loop file...")
    concat_list = work_dir / "concat.txt"
    concat_list.write_text(
        f"file '{tail_video.resolve()}'\n" f"file '{head_video.resolve()}'\n"
    )

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            output,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error("FFmpeg failed during final concat.")
        error(result.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    args = parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        error(f"Input folder does not exist: {input_dir}")
        sys.exit(1)

    if args.transition >= args.duration:
        error(
            f"Transition duration ({args.transition}s) must be less than "
            f"slide duration ({args.duration}s)."
        )
        sys.exit(1)

    need_pillow = not args.no_counter
    check_dependencies(need_pillow)

    # Apply preset if given -- overrides --resolution and --dpi
    if args.preset:
        preset_res, preset_dpi = PRESETS[args.preset]
        resolution = preset_res
        dpi = preset_dpi
        info(f"Preset '{args.preset}': resolution={resolution}, DPI={dpi}")
    else:
        resolution = args.resolution
        dpi = args.dpi

    try:
        width, height = (int(x) for x in resolution.lower().split("x"))
    except ValueError:
        error(
            f"Invalid resolution format: {resolution}  (expected WxH, e.g. 1920x1080)"
        )
        sys.exit(1)

    all_files = find_slide_files(input_dir)
    if not all_files:
        error(f"No supported files found in: {input_dir}")
        error(f"Supported: {', '.join(sorted(SLIDE_EXTENSIONS))}")
        sys.exit(1)

    presentations, images = split_inputs(all_files)

    pptx_count = sum(1 for f in presentations if f.suffix.lower() == ".pptx")
    odp_count = sum(1 for f in presentations if f.suffix.lower() == ".odp")
    img_count = len(images)
    type_parts = []
    if pptx_count:
        type_parts.append(f"{pptx_count} .pptx")
    if odp_count:
        type_parts.append(f"{odp_count} .odp")
    if img_count:
        type_parts.append(f"{img_count} image(s)")
    info(f"Found {len(all_files)} input file(s) ({', '.join(type_parts)}):")
    for f in all_files:
        print(f"    {f.name}")

    work_dir = Path(tempfile.mkdtemp(prefix="slides_to_video_"))
    slides_dir = work_dir / "slides"
    clips_dir = work_dir / "clips"
    badges_dir = work_dir / "badges"
    slides_dir.mkdir()
    clips_dir.mkdir()
    badges_dir.mkdir()
    info(f"Working directory: {work_dir}")

    try:
        info("Step 1/3 -- Collecting/converting inputs to PNG images...")
        # PNG/JPG files (e.g. Canva exports) are used directly at full resolution.
        # Presentations (.pptx/.odp) are rasterised via LibreOffice + pdftoppm.
        all_slides = collect_png_inputs(images)
        all_slides += convert_presentations_to_images(presentations, slides_dir, dpi)
        info(f"Total slides: {len(all_slides)}")

        if not all_slides:
            error("No slides found — aborting.")
            sys.exit(1)

        info("Step 2/3 — Encoding slide clips...")
        clips = encode_all_clips(
            all_slides,
            clips_dir,
            width,
            height,
            args.fps,
            args.duration,
            show_counter=not args.no_counter,
            counter_pos=args.counter_pos,
            counter_size=args.counter_size,
            badges_dir=badges_dir,
        )
        success("All clips encoded.")

        info("Step 3/3 — Assembling looping video...")
        assemble_looping_video(
            clips, args.output, args.duration, args.transition, work_dir
        )

    finally:
        if args.keep_temp:
            info(f"Temp files kept at: {work_dir}")
        else:
            info("Cleaning up temporary files...")
            shutil.rmtree(work_dir, ignore_errors=True)

    out_path = Path(args.output)
    size_mb = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0
    hold = args.duration - args.transition
    runtime = round(hold * len(all_slides), 1)

    dpi_note = f"  DPI (pptx/odp) : {dpi}" if presentations else ""
    success(f"Video created: {args.output} ({size_mb:.1f} MB)")
    info(f"  Slides     : {len(all_slides)}")
    info(f"  Per slide  : {args.duration}s  (hold: {hold}s + {args.transition}s fade)")
    info(f"  Resolution : {resolution}")
    if dpi_note:
        info(dpi_note)
    info(f"  Runtime    : ~{runtime}s per loop")
    info(f"  Play with  : cvlc --loop --fullscreen {args.output}")


if __name__ == "__main__":
    main()
