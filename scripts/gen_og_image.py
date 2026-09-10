"""OGP用のプレビュー画像 (static/og-image.png) を生成するスクリプト。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
BG = (11, 17, 32)        # #0B1120 deep indigo navy (brand ground)
BG2 = (20, 30, 54)       # #141D33 vignette core
AMBER = (242, 169, 59)   # #F2A93B focus / rescue light
TEAL = (95, 212, 196)    # #5FD4C4 citizens / data points

FONT_GOTHIC = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"

OUT_PATH = Path(__file__).resolve().parent.parent / "static" / "og-image.png"


def radial_ground(size, center_color, edge_color):
    img = Image.new("RGB", size, edge_color)
    draw = ImageDraw.Draw(img)
    cx, cy = size[0] / 2, size[1] * 0.38
    import math
    max_r = math.hypot(size[0], size[1]) / 2
    steps = 48
    for s in range(steps, 0, -1):
        t = s / steps
        r = max_r * t
        color = tuple(int(edge_color[i] + (center_color[i] - edge_color[i]) * (1 - t)) for i in range(3))
        draw.ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.8], fill=color)
    return img


def main():
    import random
    random.seed(11)

    img = radial_ground((WIDTH, HEIGHT), BG2, BG)
    draw = ImageDraw.Draw(img, "RGBA")

    # scattered citizen/issue points, matching the CIVIC LENS brand motif
    for _ in range(38):
        x = random.uniform(30, WIDTH - 30)
        y = random.uniform(20, HEIGHT * 0.55)
        lit = random.random() < 0.4
        col = AMBER if lit else TEAL
        r = random.uniform(2.5, 4.5) if lit else random.uniform(1.5, 3)
        alpha = 230 if lit else 150
        if lit:
            gr = r * 2.4
            draw.ellipse([x - gr, y - gr, x + gr, y + gr], fill=(*AMBER, 40))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(*col, alpha))

    title_font = ImageFont.truetype(FONT_GOTHIC, 78)
    subtitle_font = ImageFont.truetype(FONT_GOTHIC, 34)
    small_font = ImageFont.truetype(FONT_GOTHIC, 24)

    title = "CIVIC LENS"
    subtitle = "見えない課題に、焦点を。"
    tagline = "エージェンティックAIが人を救う。"

    def centered_text(y, text, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)

    centered_text(410, title, title_font, (237, 237, 242))
    centered_text(500, subtitle, subtitle_font, AMBER)
    centered_text(548, tagline, small_font, AMBER)

    OUT_PATH.parent.mkdir(exist_ok=True)
    img.save(OUT_PATH, "PNG")
    print(f"saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
