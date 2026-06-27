# slides-to-video

Convert a folder of PowerPoint (`.pptx`), OpenDocument (`.odp`), or image files
(`.png`, `.jpg`) into a **seamlessly looping MP4 video** — perfect for kiosk
displays, church screens, conference lobbies, or any unattended screen.

The last slide crossfades back into the first so that looping in VLC produces
a completely smooth result with no jump cut.

---

## Features

- Accepts `.pptx`, `.odp`, PNG, and JPG inputs — mix and match in one folder
- Crossfade transitions between every slide (including the loop boundary)
- Optional slide-number badge overlay (e.g. `2/5`) with configurable position
- HD (1920×1080) and 4K (3840×2160) quality presets
- All heavy lifting done by LibreOffice, ffmpeg, and pdftoppm — no cloud services

---

## Dependencies

**Linux (Debian/Ubuntu)**
```bash
sudo apt install libreoffice ffmpeg poppler-utils python3-pil
```

**macOS**
```bash
brew install libreoffice ffmpeg poppler
pip install pillow
```

Python 3.10 or later is required (uses `X | Y` type union syntax).

---

## Usage

```
python3 slides_to_video.py [OPTIONS] /path/to/slides/folder
```

### Basic examples

```bash
# HD output with defaults (10s per slide, 1s crossfade)
python3 slides_to_video.py ~/slides/

# 4K output with a custom filename
python3 slides_to_video.py --preset 4k -o church_4k.mp4 ~/slides/

# Custom duration and transition
python3 slides_to_video.py -o conference.mp4 -d 6 -t 1.5 ~/slides/

# No slide counter, counter in top-left corner
python3 slides_to_video.py --no-counter ~/slides/
python3 slides_to_video.py --counter-pos top-left ~/slides/
```

### Play the result

```bash
cvlc --loop --fullscreen output.mp4
```

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `FOLDER` | *(required)* | Folder containing input files |
| `-o`, `--output` | `output.mp4` | Output video filename |
| `-d`, `--duration` | `10` | Seconds each slide is shown |
| `-t`, `--transition` | `1` | Crossfade duration in seconds |
| `--preset` | *(none)* | `hd` (1920×1080) or `4k` (3840×2160); overrides `--resolution` and `--dpi` |
| `-r`, `--resolution` | `1920x1080` | Output resolution (ignored if `--preset` is set) |
| `--dpi` | `150` | Rasterisation DPI for `.pptx`/`.odp` (ignored if `--preset` is set) |
| `-f`, `--fps` | `60` | Frames per second |
| `--no-counter` | off | Disable the slide number overlay |
| `--counter-pos` | `bottom-right` | Badge position: `bottom-right`, `bottom-left`, `top-right`, `top-left` |
| `--counter-size` | `36` | Badge font size in pixels |
| `-k`, `--keep-temp` | off | Keep intermediate files for debugging |
| `--version` | | Print version and exit |

---

## How it works

1. **Convert** — `.pptx`/`.odp` files are converted to PDF by LibreOffice, then each page is rasterised to PNG by `pdftoppm`. PNG/JPG files are used directly.
2. **Encode clips** — Each slide is encoded into a short H.264 clip (scaled, padded to the target resolution, with optional badge overlay) using ffmpeg.
3. **Assemble** — Clips are joined with ffmpeg's `xfade` filter. To make the loop seamless, a full linear pass including a last→first crossfade is built, then split at the midpoint of that final fade and reassembled in reverse order so VLC's loop jump lands on an identical frame.

---

## Notes

- Slides within each source file are included in file order; source files are processed in alphabetical order.
- The slide counter reflects the total across all input files combined.
- Font rendering for the badge uses DejaVu Sans Bold, Liberation Sans Bold, or FreeSans Bold — whichever is found first. Pillow's built-in bitmap font is the fallback.
- If Pillow is not installed, `--no-counter` is applied automatically.

---

## Licence

MIT — see [LICENSE](LICENSE).
