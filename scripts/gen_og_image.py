"""OGP用のプレビュー画像 (static/og-image.png) を生成するスクリプト。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
BG_TOP = (59, 130, 246)      # blue-500
BG_BOTTOM = (139, 92, 246)   # violet-500

FONT_GOTHIC = "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf"
FONT_EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

OUT_PATH = Path(__file__).resolve().parent.parent / "static" / "og-image.png"


def vertical_gradient(size, top, bottom):
    img = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(img)
    h = size[1]
    for y in range(h):
        t = y / h
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (size[0], y)], fill=color)
    return img


def main():
    img = vertical_gradient((WIDTH, HEIGHT), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(FONT_GOTHIC, 84)
    subtitle_font = ImageFont.truetype(FONT_GOTHIC, 40)
    small_font = ImageFont.truetype(FONT_GOTHIC, 30)

    title = "Civic Lens"
    subtitle = "市民の怒りを情報公開に変換するAIエージェント"
    tagline = "Powered by Gemini x GMI Cloud x 駅すぱあと x YouCam API"

    def centered_text(y, text, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)

    centered_text(220, title, title_font, (255, 255, 255))
    centered_text(340, subtitle, subtitle_font, (240, 240, 255))
    centered_text(420, tagline, small_font, (220, 220, 250))

    OUT_PATH.parent.mkdir(exist_ok=True)
    img.save(OUT_PATH, "PNG")
    print(f"saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
