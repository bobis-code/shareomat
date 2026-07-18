"""Render the SELF LEG icon/logo mark locally with Pillow (no browser needed).

Mirrors the SVG geometry from self_leg_logo.html exactly, drawn supersampled
then downsampled for anti-aliased edges.
"""
from PIL import Image, ImageDraw, ImageFont

PAPER = (237, 239, 231, 255)
SOLAR = (201, 122, 31, 255)
GRIDBLUE = (53, 86, 107, 255)
INK = (28, 35, 31, 255)
MONO_INK = (75, 81, 71, 255)

FONT_DIR = "C:/Windows/Fonts/"
SS = 8  # supersampling factor


def house_path(x, y):
    """Return the 5 points of a house pentagon anchored at its own local (0,0)."""
    return [(x + 12, y + 0), (x + 24, y + 10), (x + 24, y + 24), (x + 0, y + 24), (x + 0, y + 10)]


def draw_mark(draw, ox, oy, scale):
    """Draw the 3-house mark at origin (ox, oy) with the given scale (base unit = SVG px)."""
    def p(x, y):
        return (ox + x * scale, oy + y * scale)

    lw = max(1, round(1.6 * scale))
    # Amber flow lines (roof -> lower houses)
    draw.line([p(30, 20), p(18, 42)], fill=SOLAR, width=lw, joint="curve")
    draw.line([p(34, 20), p(46, 42)], fill=SOLAR, width=lw, joint="curve")
    # Grid-blue flow line (between the two lower houses)
    draw.line([p(20, 46), p(44, 46)], fill=GRIDBLUE, width=lw, joint="curve")

    houses = [(32 - 12, 8), (10, 40), (46, 40)]
    hlw = max(1, round(1.8 * scale))
    for hx, hy in houses:
        pts = [p(*pt) for pt in house_path(hx, hy)]
        draw.polygon(pts, fill=PAPER)
        draw.line(pts + [pts[0]], fill=INK, width=hlw, joint="curve")
        # Round off each vertex so joins look mitered instead of clipped/blunt.
        r = hlw / 2
        for vx, vy in pts:
            draw.ellipse([vx - r, vy - r, vx + r, vy + r], fill=INK)


def render_icon(path, size=128):
    ss_size = size * SS
    img = Image.new("RGBA", (ss_size, ss_size), PAPER)
    draw = ImageDraw.Draw(img)
    scale = ss_size / 64
    draw_mark(draw, 0, 0, scale)
    img = img.resize((size, size), Image.LANCZOS)
    img.save(path)


def render_logo(path, width=250, height=100):
    ss_w, ss_h = width * SS, height * SS
    img = Image.new("RGBA", (ss_w, ss_h), PAPER)
    draw = ImageDraw.Draw(img)
    scale = SS * 1.05
    draw_mark(draw, 10 * SS, 18 * SS, scale)

    bold = ImageFont.truetype(FONT_DIR + "arialbd.ttf", int(30 * SS * 0.92))
    reg = ImageFont.truetype(FONT_DIR + "arial.ttf", int(30 * SS * 0.92))
    mono = ImageFont.truetype(FONT_DIR + "consola.ttf", int(11 * SS))

    text_x = 86 * SS
    baseline_y = 52 * SS
    draw.text((text_x, baseline_y), "SELF", font=bold, fill=INK, anchor="ls")
    w_self = draw.textlength("SELF", font=bold)
    draw.text((text_x + w_self, baseline_y), " LEG", font=reg, fill=GRIDBLUE, anchor="ls")

    draw.text((text_x, 72 * SS), "built by bobis code", font=mono, fill=MONO_INK, anchor="ls")

    img = img.resize((width, height), Image.LANCZOS)
    img.save(path)


if __name__ == "__main__":
    render_icon("icon.png")
    render_logo("logo.png")
    print("done")
