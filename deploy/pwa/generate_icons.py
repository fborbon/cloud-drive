"""Generate PWA icons using Pillow. Run once during deploy."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "icons"
OUT.mkdir(exist_ok=True)

BG   = (15, 17, 23)
BLUE = (31, 119, 180)

def draw_icon(size):
    img = Image.new("RGBA", (size, size), BG)
    d   = ImageDraw.Draw(img)
    pad = size // 6
    s   = size - 2 * pad

    # Cloud body — rounded rect
    r = s // 4
    d.rounded_rectangle([pad, pad + s//4, pad + s, pad + s], radius=r, fill=BLUE)

    # Cloud bumps
    cx = pad + s // 2
    cy = pad + s // 2
    bw = s // 2.8
    d.ellipse([cx - bw,     cy - bw//1.2, cx,          cy + bw//2], fill=BLUE)
    d.ellipse([cx - bw//2,  cy - bw,      cx + bw//1.2,cy + bw//2], fill=BLUE)
    d.ellipse([cx,           cy - bw//1.5, cx + bw,     cy + bw//2], fill=BLUE)

    return img

for sz in (192, 512):
    draw_icon(sz).save(OUT / f"icon-{sz}.png", "PNG")
    print(f"Generated icon-{sz}.png")
