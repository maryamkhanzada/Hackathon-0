#!/usr/bin/env python3
"""Generate a simple quote-card image for Instagram post."""
import sys, struct, zlib

# Try PIL first, fall back to raw PNG
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

OUT = "D:/Hackathon-0/ig_post.png"

CAPTION_LINES = [
    "",
    "The best investment",
    "you will ever make",
    "is in your own",
    "education.",
    "",
    "Every book. Every course.",
    "Every question.",
    "It all compounds.",
    "",
    "#Education  #GrowthMindset",
    "#LifelongLearning",
]

BG_COLOR = (15, 23, 42)       # dark navy
TEXT_COLOR = (248, 250, 252)   # near-white
ACCENT = (99, 179, 237)        # light blue

if HAS_PIL:
    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Draw accent rectangle top
    draw.rectangle([0, 0, W, 12], fill=ACCENT)
    draw.rectangle([0, H-12, W, H], fill=ACCENT)
    draw.rectangle([0, 0, 12, H], fill=ACCENT)
    draw.rectangle([W-12, 0, W, H], fill=ACCENT)

    # Try to get a font, fall back to default
    try:
        font_lg = ImageFont.truetype("arial.ttf", 72)
        font_sm = ImageFont.truetype("arial.ttf", 40)
        font_xs = ImageFont.truetype("arial.ttf", 32)
    except Exception:
        try:
            font_lg = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 72)
            font_sm = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
            font_xs = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
        except Exception:
            font_lg = ImageFont.load_default()
            font_sm = ImageFont.load_default()
            font_xs = ImageFont.load_default()

    # Main quote lines
    quote = [
        ("The best investment", font_lg, TEXT_COLOR),
        ("you will ever make", font_lg, TEXT_COLOR),
        ("is in your own", font_lg, TEXT_COLOR),
        ("EDUCATION.", font_lg, ACCENT),
    ]

    y = 140
    for text, font, color in quote:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font, fill=color)
        y += 90

    # Separator
    y += 20
    draw.rectangle([W//4, y, 3*W//4, y+3], fill=ACCENT)
    y += 30

    # Sub-lines
    sub = [
        "Every book you read.",
        "Every course you take.",
        "Every question you ask.",
        "It all compounds over time.",
    ]
    for text in sub:
        bbox = draw.textbbox((0, 0), text, font=font_sm)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font_sm, fill=TEXT_COLOR)
        y += 55

    y += 20
    hashtags = "#Education  #GrowthMindset  #LifelongLearning"
    bbox = draw.textbbox((0, 0), hashtags, font=font_xs)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, y), hashtags, font=font_xs, fill=ACCENT)

    img.save(OUT, "PNG")
    print(f"PIL: saved {OUT}")
    sys.exit(0)

# --- Fallback: write a minimal valid PNG without PIL ---
# Create a 1080x1080 PNG with solid dark navy background + white text block
def make_raw_png(width, height, r, g, b):
    """Create a solid-color PNG as bytes."""
    def write_chunk(tag, data):
        chunk = tag + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'
    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = write_chunk(b'IHDR', ihdr_data)
    # IDAT - raw pixel data
    row = bytes([0] + [r, g, b] * width)  # filter byte 0 + RGB pixels
    raw = row * height
    compressed = zlib.compress(raw, 6)
    idat = write_chunk(b'IDAT', compressed)
    # IEND
    iend = write_chunk(b'IEND', b'')
    return sig + ihdr + idat + iend

png_bytes = make_raw_png(1080, 1080, 15, 23, 42)
with open(OUT, 'wb') as f:
    f.write(png_bytes)
print(f"Fallback PNG: saved {OUT} ({len(png_bytes)} bytes)")
