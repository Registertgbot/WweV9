"""Image cards for tournaments: match winner card, champion card and league standings table.

Pure Pillow, no Telegram and no database: every ``render_*`` function takes plain dataclasses (names,
optional avatar bytes, numbers) and returns JPEG bytes, so it is easy to test and safe to run in a worker
thread (``asyncio.to_thread``). Fonts are bundled in ``assets/fonts`` (Poppins, SIL OFL).
"""
from __future__ import annotations

import io
import math
import random
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FILES = {"bold": "Poppins-Bold.ttf", "medium": "Poppins-Medium.ttf", "regular": "Poppins-Regular.ttf"}

W, H = 1280, 720
GOLD, GOLD_DARK, GOLD_LIGHT = (255, 200, 64), (214, 140, 24), (255, 232, 150)
SILVER, SILVER_DARK = (214, 221, 233), (140, 150, 168)
BRONZE, BRONZE_DARK = (226, 148, 98), (150, 86, 48)
RED, BLUE, GREEN = (232, 50, 72), (74, 128, 255), (52, 211, 153)
WHITE, MUTED, DIM = (255, 255, 255), (170, 178, 200), (110, 118, 142)
INK = (9, 12, 26)
AVATAR_PALETTE = [((255, 94, 98), (255, 153, 102)), ((79, 172, 254), (0, 242, 254)), ((161, 140, 209), (251, 194, 235)),
                  ((67, 233, 123), (56, 249, 215)), ((250, 112, 154), (254, 225, 64)), ((102, 126, 234), (118, 75, 162)),
                  ((247, 151, 30), (255, 210, 0)), ((17, 153, 142), (56, 239, 125))]
NO_ACTION = {"walkover": ("Opponent did not", "show up in time"), "owner": ("Result set by the", "tournament owner")}
METHOD_LABEL = {"knockout": "BY KNOCKOUT", "timeout": "BY TIMEOUT", "walkover": "BY WALKOVER", "owner": "OWNER'S DECISION"}


# ------------------------------------------------------------------------------------------ data

@dataclass
class Person:
    user_id: int
    name: str
    avatar: bytes | None = None          # raw image bytes of the Telegram profile photo (None = initials avatar)


@dataclass
class MatchCard:
    title: str
    round_label: str
    winner: Person
    loser: Person
    method: str = "knockout"             # knockout | timeout | walkover | owner
    winner_hp: int | None = None
    loser_hp: int | None = None
    turns: int | None = None
    finishers: int | None = None
    damage: int | None = None
    points: int | None = None            # league: points the winner just earned
    note: str = ""                       # e.g. "Match 2 of 5"
    date_text: str = ""
    brand: str = "WWE ARENA"


@dataclass
class ChampionCard:
    title: str
    champion: Person
    wins: int | None = None
    points: int | None = None
    runner_up: Person | None = None
    third: list[Person] = field(default_factory=list)
    date_text: str = ""
    brand: str = "WWE ARENA"


@dataclass
class StandRow:
    person: Person
    played: int
    wins: int
    losses: int
    margin: int
    points: int
    dq: bool = False


@dataclass
class StandingsCard:
    title: str
    subtitle: str
    rows: list[StandRow]
    playoff_spots: int = 0
    show_cut: bool = False               # draw the "playoff line" under the last qualifying row
    footer: str = "Win = 3 pts  •  Tiebreak: HP margin"
    brand: str = "WWE ARENA"


# ---------------------------------------------------------------------------------------- fonts / text

@lru_cache(maxsize=96)
def font(weight: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / FONT_FILES[weight]), size)
    except OSError:                                          # fonts missing: degrade, never crash
        try:
            return ImageFont.load_default(size)
        except TypeError:                                    # Pillow < 10.1
            return ImageFont.load_default()


@lru_cache(maxsize=8192)
def _glyph_sig(ch: str) -> bytes:
    img = Image.new("L", (80, 80), 0)
    ImageDraw.Draw(img).text((8, 4), ch, font=font("bold", 48), fill=255)
    return img.tobytes()


@lru_cache(maxsize=1)
def _notdef_sig() -> bytes:
    return _glyph_sig("\uffff")


def clean_name(name: str | None, limit: int = 40) -> str:
    """Make a Telegram display name safe for the card font: fold fancy letters, drop emoji / unsupported glyphs."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKC", name or ""):
        if ch in "\n\r\t":
            ch = " "
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") or 0xFE00 <= ord(ch) <= 0xFE0F:
            continue
        if ch == " " or _glyph_sig(ch) != _notdef_sig():
            out.append(ch)
    text = " ".join("".join(out).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text or "Superstar"


def fit(draw: ImageDraw.ImageDraw, text: str, weight: str, size: int, max_w: int, min_size: int = 16):
    """Largest font (>= min_size) in which ``text`` fits ``max_w``; ellipsis as the last resort."""
    while size > min_size and draw.textlength(text, font=font(weight, size)) > max_w:
        size -= 2
    f = font(weight, size)
    if draw.textlength(text, font=f) > max_w:
        while len(text) > 1 and draw.textlength(text + "…", font=f) > max_w:
            text = text[:-1]
        text = text.rstrip() + "…"
    return text, f


def spaced(draw: ImageDraw.ImageDraw, center: tuple[float, float], text: str, f, fill, spacing: float = 6,
           shadow: bool = False, max_w: float | None = None, weight: str = "bold") -> None:
    """Letter-spaced text centred on ``center`` (headings). With ``max_w`` the font shrinks until it fits."""
    size = getattr(f, "size", 24)
    while True:
        widths = [draw.textlength(c, font=f) for c in text]
        total = sum(widths) + spacing * (len(text) - 1)
        if max_w is None or total <= max_w or size <= 16:
            break
        size -= 2
        spacing = max(2.0, spacing * 0.92)
        f = font(weight, size)
    if max_w is not None and total > max_w:                     # still too wide: cut with an ellipsis
        while len(text) > 3 and total > max_w:
            text = text[:-2].rstrip() + "…"
            widths = [draw.textlength(c, font=f) for c in text]
            total = sum(widths) + spacing * (len(text) - 1)
    x = center[0] - total / 2
    for c, w in zip(text, widths):
        if shadow:
            draw.text((x + 2, center[1] + 3), c, font=f, fill=(12, 8, 18), anchor="lm")
        draw.text((x, center[1]), c, font=f, fill=fill, anchor="lm")
        x += w + spacing


def _initials(name: str) -> str:
    words = [w for w in clean_name(name).split() if w]
    letters = [w[0] for w in words[:2] if w[0].isalnum()]
    return ("".join(letters) or "?").upper()


# --------------------------------------------------------------------------------------- drawing helpers

def vgrad(size: tuple[int, int], top, bottom) -> Image.Image:
    mask = Image.linear_gradient("L").resize(size, Image.BILINEAR)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), mask)


def dgrad(size: tuple[int, int], c1, c2) -> Image.Image:
    """Diagonal (top-left -> bottom-right) gradient."""
    mask = Image.linear_gradient("L").resize((size[0] + size[1], size[0] + size[1]), Image.BILINEAR).rotate(45, expand=False)
    mask = ImageOps.fit(mask, size)
    return Image.composite(Image.new("RGB", size, c2), Image.new("RGB", size, c1), mask)


_RADIAL_EDGE = 181.0                                     # Image.radial_gradient reaches 255 at the corners (128*sqrt2)


def radial_falloff(w: int, h: int, power: float = 2.0, strength: float = 1.0) -> Image.Image:
    """L mask: 255 * strength in the centre, fading smoothly to 0 at the edge midpoints."""
    lut = [int(max(0.0, 1.0 - min(1.0, v / _RADIAL_EDGE)) ** power * 255 * strength) for v in range(256)]
    return Image.radial_gradient("L").point(lut).resize((w, h), Image.BILINEAR)


def glow(base: Image.Image, center: tuple[float, float], radius: int, color, strength: float) -> None:
    """Soft radial light (adds colour around ``center``)."""
    r = max(8, int(radius))
    layer = Image.new("RGBA", (r * 2, r * 2), tuple(color) + (255,))
    layer.putalpha(radial_falloff(r * 2, r * 2, power=1.6, strength=strength))
    _paste_clipped(base, layer, (int(center[0] - r), int(center[1] - r)))


def _paste_clipped(base: Image.Image, layer: Image.Image, pos: tuple[int, int]) -> None:
    x, y = pos
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(layer.width, base.width - x), min(layer.height, base.height - y)
    if ex <= sx or ey <= sy:
        return
    base.alpha_composite(layer.crop((sx, sy, ex, ey)), (x + sx, y + sy))


def circle_mask(d: int, ss: int = 4) -> Image.Image:
    m = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=255)
    return m.resize((d, d), Image.LANCZOS)


def rrect_mask(size: tuple[int, int], radius: int, ss: int = 3) -> Image.Image:
    m = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] * ss - 1, size[1] * ss - 1), radius=radius * ss, fill=255)
    return m.resize(size, Image.LANCZOS)


def drop_shadow(base: Image.Image, box: tuple[int, int, int, int], radius: int, blur: int = 18,
                offset: tuple[int, int] = (0, 10), alpha: int = 110) -> None:
    pad = blur * 3
    w, h = box[2] - box[0], box[3] - box[1]
    m = Image.new("L", (w + pad * 2, h + pad * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + w, pad + h), radius=radius, fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new("RGBA", m.size, (0, 0, 0, 255))
    layer.putalpha(m)
    _paste_clipped(base, layer, (box[0] - pad + offset[0], box[1] - pad + offset[1]))


def glass(base: Image.Image, box: tuple[int, int, int, int], radius: int = 22, tint=(255, 255, 255), tint_alpha: int = 22,
          border_alpha: int = 46, shadow: bool = True) -> None:
    """Frosted-glass panel: blurred backdrop + translucent tint + hairline border."""
    x0, y0, x1, y1 = [int(v) for v in box]
    size = (x1 - x0, y1 - y0)
    if shadow:
        drop_shadow(base, (x0, y0, x1, y1), radius)
    region = base.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(10))
    region.alpha_composite(Image.new("RGBA", size, tuple(tint) + (tint_alpha,)))
    mask = rrect_mask(size, radius)
    base.paste(region, (x0, y0), mask)
    border = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, outline=(255, 255, 255, border_alpha), width=2)
    base.alpha_composite(border, (x0, y0))


def pill(base: Image.Image, box: tuple[int, int, int, int], fill, text: str, f, text_fill=INK, gradient=None) -> None:
    x0, y0, x1, y1 = [int(v) for v in box]
    size = (x1 - x0, y1 - y0)
    mask = rrect_mask(size, size[1] // 2)
    body = (gradient if gradient is not None else Image.new("RGB", size, fill)).convert("RGBA")
    if gradient is not None:
        body = body.resize(size)
    base.paste(body, (x0, y0), mask)
    ImageDraw.Draw(base, "RGBA").text(((x0 + x1) / 2, (y0 + y1) / 2 + 1), text, font=f, fill=text_fill, anchor="mm")


def background(w: int, h: int, accent=RED, seed: int = 0) -> Image.Image:
    """Dark arena backdrop: gradient, coloured glows, spotlight cone, speed streaks, ring ropes, vignette, grain."""
    rng = random.Random(seed)
    base = vgrad((w, h), (8, 11, 24), (24, 10, 38)).convert("RGBA")
    glow(base, (w * 0.5, h * 0.45), int(max(w, h) * 0.55), accent, 0.38)
    glow(base, (w * 0.05, h * 0.05), int(max(w, h) * 0.45), BLUE, 0.22)
    glow(base, (w * 0.97, h * 0.98), int(max(w, h) * 0.45), accent, 0.22)

    cone = Image.new("L", (w, h), 0)
    ImageDraw.Draw(cone).polygon([(w * 0.5 - 50, 0), (w * 0.5 + 50, 0), (w * 0.5 + w * 0.33, h), (w * 0.5 - w * 0.33, h)], fill=46)
    cone = cone.filter(ImageFilter.GaussianBlur(36))
    spot = Image.new("RGBA", (w, h), (255, 244, 220, 255))
    spot.putalpha(cone)
    base.alpha_composite(spot)

    streaks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(streaks)
    for _ in range(9):
        x = rng.randint(-200, w)
        wd = rng.randint(18, 70)
        sd.polygon([(x, 0), (x + wd, 0), (x + wd - h * 0.55, h), (x - h * 0.55, h)], fill=(255, 255, 255, rng.randint(5, 13)))
    base.alpha_composite(streaks.filter(ImageFilter.GaussianBlur(1.5)))

    ropes = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ropes)
    for i, col in enumerate([(232, 50, 72, 70), (255, 255, 255, 46), (74, 128, 255, 70)]):
        y = h - 70 + i * 13
        rd.line([(0, y + 6), (w, y - 6)], fill=col, width=3)
    base.alpha_composite(ropes.filter(ImageFilter.GaussianBlur(0.8)))

    edge = radial_falloff(w, h, power=1.0).point(lambda v: 255 - v)          # 0 centre -> 255 edges
    vig = edge.point(lambda v: int((v / 255) ** 2.2 * 255 * 0.85))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dark.putalpha(vig)
    base.alpha_composite(dark)

    grain = Image.effect_noise((w, h), 18).point(lambda v: 128 + (v - 128) // 2)
    base = Image.blend(base.convert("RGB"), ImageChops.overlay(base.convert("RGB"), Image.merge("RGB", (grain,) * 3)), 0.10)
    return base.convert("RGBA")


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, subsampling=0)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------- avatars

def avatar_image(p: Person, d: int, gray: bool = False) -> Image.Image:
    """Circular avatar (RGBA, ``d`` x ``d``): the profile photo, or a gradient disc with initials."""
    img: Image.Image | None = None
    if p.avatar:
        try:
            img = ImageOps.fit(Image.open(io.BytesIO(p.avatar)).convert("RGB"), (d, d), Image.LANCZOS, centering=(0.5, 0.4))
        except Exception:                                    # corrupt / unsupported photo -> initials
            img = None
    if img is None:
        c1, c2 = AVATAR_PALETTE[abs(p.user_id) % len(AVATAR_PALETTE)]
        img = dgrad((d, d), c1, c2)
        ImageDraw.Draw(img, "RGBA").text((d / 2, d / 2 + d * 0.02), _initials(p.name), font=font("bold", int(d * 0.42)),
                                 fill=(255, 255, 255), anchor="mm")
    if gray:
        img = ImageEnhance.Brightness(ImageOps.grayscale(img).convert("RGB")).enhance(0.72)
    out = img.convert("RGBA")
    out.putalpha(circle_mask(d))
    return out


def place_avatar(base: Image.Image, p: Person, center: tuple[float, float], d: int, ring: tuple | None = None,
                 ring_w: int = 8, gray: bool = False, glow_color=None) -> None:
    """Avatar with an optional gradient ring and an outer glow."""
    cx, cy = int(center[0]), int(center[1])
    if glow_color is not None:
        glow(base, (cx, cy), int(d * 0.95), glow_color, 0.55)
    if ring is not None:
        outer = d + ring_w * 2
        m = Image.new("L", (outer * 4, outer * 4), 0)
        md = ImageDraw.Draw(m)
        md.ellipse((0, 0, outer * 4 - 1, outer * 4 - 1), fill=255)
        md.ellipse((ring_w * 4, ring_w * 4, outer * 4 - ring_w * 4 - 1, outer * 4 - ring_w * 4 - 1), fill=0)
        m = m.resize((outer, outer), Image.LANCZOS)
        base.paste(dgrad((outer, outer), ring[0], ring[1]).convert("RGBA"), (cx - outer // 2, cy - outer // 2), m)
        # dark gap between ring and photo
        gap = Image.new("L", (d + 8, d + 8), 0)
        ImageDraw.Draw(gap).ellipse((0, 0, d + 7, d + 7), fill=255)
    base.alpha_composite(avatar_image(p, d, gray), (cx - d // 2, cy - d // 2))


def draw_crown(base: Image.Image, cx: int, bottom: int, width: int) -> None:
    ss = 4
    w, h = width, int(width * 0.62)
    mask = Image.new("L", (w * ss, h * ss), 0)
    md = ImageDraw.Draw(mask)
    pts = [(0.04, 1.0), (0.0, 0.18), (0.24, 0.55), (0.5, 0.0), (0.76, 0.55), (1.0, 0.18), (0.96, 1.0)]
    md.polygon([(x * w * ss, y * h * ss) for x, y in pts], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    drop_shadow(base, (cx - w // 2, bottom - h, cx + w // 2, bottom), 6, blur=10, offset=(0, 6), alpha=120)
    body = vgrad((w, h), GOLD_LIGHT, GOLD_DARK).convert("RGBA")
    base.paste(body, (cx - w // 2, bottom - h), mask)
    d = ImageDraw.Draw(base, "RGBA")
    d.rounded_rectangle((cx - w * 0.46, bottom - h * 0.17, cx + w * 0.46, bottom - h * 0.03), radius=4, fill=GOLD_DARK)
    for fx, col, r in ((0.0, RED, 0.06), (0.5, BLUE, 0.075), (1.0, GREEN, 0.06)):
        x = cx - w / 2 + fx * w + (w * 0.0 if fx == 0.5 else (w * 0.0))
        y = bottom - h * (0.82 if fx == 0.0 or fx == 1.0 else 0.93)
        rr = max(4, int(w * r))
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col, outline=GOLD_LIGHT, width=2)


def medal(base: Image.Image, center: tuple[int, int], d: int, rank: int, f) -> None:
    cols = {1: (GOLD_LIGHT, GOLD_DARK), 2: (SILVER, SILVER_DARK), 3: (BRONZE, BRONZE_DARK)}
    cx, cy = center
    if rank in cols:
        base.paste(dgrad((d, d), *cols[rank]).convert("RGBA"), (cx - d // 2, cy - d // 2), circle_mask(d))
        ImageDraw.Draw(base, "RGBA").text((cx, cy + 1), str(rank), font=f, fill=INK, anchor="mm")
    else:
        glass(base, (cx - d // 2, cy - d // 2, cx + d // 2, cy + d // 2), d // 2, tint_alpha=30, shadow=False)
        ImageDraw.Draw(base, "RGBA").text((cx, cy + 1), str(rank), font=f, fill=MUTED, anchor="mm")


def confetti(base: Image.Image, seed: int, avoid: list[tuple[int, int, int, int]] | None = None, count: int = 70) -> None:
    rng = random.Random(seed)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    palette = [GOLD, GOLD_LIGHT, RED, BLUE, WHITE, GREEN]
    for _ in range(count):
        w, h = rng.randint(7, 16), rng.randint(14, 30)
        x, y = rng.randint(0, base.width), rng.randint(0, int(base.height * 0.8))
        if avoid and any(a[0] < x < a[2] and a[1] < y < a[3] for a in avoid):
            continue
        piece = Image.new("RGBA", (w, h), rng.choice(palette) + (rng.randint(150, 235),))
        piece = piece.rotate(rng.randint(0, 359), expand=True, resample=Image.BICUBIC)
        layer.alpha_composite(piece, (min(max(0, x), base.width - piece.width), min(max(0, y), base.height - piece.height)))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.7)))


def laurel(base: Image.Image, center: tuple[int, int], radius: int, color=GOLD) -> None:
    """Two laurel branches hugging a circle (left and right, open at the top)."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for side in (-1, 1):
        for i in range(7):
            theta = math.radians(66 + i * 14)                # measured from the top, round the bottom of the circle
            x = center[0] + side * radius * math.sin(theta)
            y = center[1] - radius * math.cos(theta)
            leaf = Image.new("RGBA", (44, 18), (0, 0, 0, 0))
            ImageDraw.Draw(leaf).ellipse((0, 0, 43, 17), fill=color + (230,))
            tangent = math.degrees(theta)                    # leaf points along the branch, tilted outwards
            leaf = leaf.rotate(-side * (tangent - 90) + (28 * side), expand=True, resample=Image.BICUBIC)
            _paste_clipped(layer, leaf, (int(x - leaf.width / 2), int(y - leaf.height / 2)))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0.5)))


def _footer(base: Image.Image, left: str, right: str, y: int | None = None) -> None:
    d = ImageDraw.Draw(base, "RGBA")
    y = y if y is not None else base.height - 34
    d.text((48, y), left, font=font("bold", 18), fill=GOLD, anchor="lm")
    d.text((base.width - 48, y), right, font=font("medium", 18), fill=DIM, anchor="rm")


# ------------------------------------------------------------------------------------------- winner card

def render_match_card(c: MatchCard) -> bytes:
    img = background(W, H, accent=RED, seed=c.winner.user_id)
    d = ImageDraw.Draw(img, "RGBA")
    winner, loser = clean_name(c.winner.name), clean_name(c.loser.name)

    # header: tournament title + round chip
    title, tf = fit(d, c.title.upper(), "bold", 38, 900, 22)
    spaced(d, (W / 2, 56), title, tf, GOLD, spacing=7, shadow=True, max_w=1000)
    chip = f"{c.round_label.upper()}" + (f"   •   {c.note.upper()}" if c.note else "")
    cf = font("medium", 19)
    cw = int(d.textlength(chip, font=cf)) + 56
    glass(img, (W // 2 - cw // 2, 92, W // 2 + cw // 2, 130), 19, tint_alpha=28, shadow=False)
    d.text((W / 2, 111), chip, font=cf, fill=(235, 238, 250), anchor="mm")

    # centre: winner
    cx, cy, ad = W // 2, 338, 236
    place_avatar(img, c.winner, (cx, cy), ad, ring=(GOLD_LIGHT, GOLD_DARK), ring_w=9, glow_color=GOLD)
    draw_crown(img, cx, cy - ad // 2 - 4, 104)
    pill(img, (cx - 90, cy + ad // 2 - 10, cx + 90, cy + ad // 2 + 30), None, "WINNER", font("bold", 20), INK,
         gradient=vgrad((180, 40), GOLD_LIGHT, GOLD_DARK))
    d = ImageDraw.Draw(img, "RGBA")
    text, nf = fit(d, winner, "bold", 54, 640, 28)
    d.text((cx + 2, cy + ad // 2 + 72), text, font=nf, fill=(12, 8, 18), anchor="mm")
    d.text((cx, cy + ad // 2 + 69), text, font=nf, fill=WHITE, anchor="mm")
    d.text((cx, cy + ad // 2 + 110), METHOD_LABEL.get(c.method, "BY KNOCKOUT"), font=font("medium", 18), fill=MUTED, anchor="mm")

    # left: winner's battle report
    px0, py0, px1, py1 = 52, 188, 372, 540
    glass(img, (px0, py0, px1, py1), 26)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((px0 + 28, py0 + 34), "BATTLE REPORT", font=font("bold", 17), fill=GOLD, anchor="lm")
    rows = []
    if c.winner_hp is not None:
        rows.append(("HP LEFT", f"{c.winner_hp}", c.winner_hp))
    if c.damage is not None:
        rows.append(("DAMAGE DEALT", f"{c.damage}", None))
    if c.finishers is not None:
        rows.append(("FINISHERS", f"{c.finishers}", None))
    if c.turns is not None:
        rows.append(("TURNS", f"{c.turns}", None))
    if not rows:
        for k, line in enumerate(NO_ACTION.get(c.method, ("No ring action", "in this match"))):
            d.text((px0 + 28, py0 + 120 + k * 32), line, font=font("medium", 20), fill=MUTED, anchor="lm")
    y = py0 + 84
    for label, value, bar in rows:
        d.text((px0 + 28, y), label, font=font("medium", 14), fill=DIM, anchor="lm")
        d.text((px1 - 28, y), value, font=font("bold", 26), fill=WHITE, anchor="rm")
        if bar is not None:
            d.rounded_rectangle((px0 + 28, y + 20, px1 - 28, y + 28), radius=4, fill=(60, 64, 84))
            fillw = int((px1 - px0 - 56) * max(0, min(bar, 100)) / 100)
            if fillw > 0:
                d.rounded_rectangle((px0 + 28, y + 20, px0 + 28 + max(fillw, 8), y + 28), radius=4, fill=GREEN if bar >= 40 else GOLD)
            y += 18
        y += 62

    # right: the defeated fighter
    qx0, qy0, qx1, qy1 = W - 372, 188, W - 52, 540
    glass(img, (qx0, qy0, qx1, qy1), 26)
    qcx = (qx0 + qx1) // 2
    place_avatar(img, c.loser, (qcx, qy0 + 120), 128, ring=(DIM, (60, 66, 88)), ring_w=5, gray=True)
    d = ImageDraw.Draw(img, "RGBA")
    pill(img, (qcx - 62, qy0 + 200, qcx + 62, qy0 + 230), (150, 40, 58), "DEFEATED", font("bold", 15), WHITE)
    d = ImageDraw.Draw(img, "RGBA")
    text, lf = fit(d, loser, "bold", 30, qx1 - qx0 - 48, 18)
    d.text((qcx, qy0 + 268), text, font=lf, fill=(225, 229, 240), anchor="mm")
    if c.loser_hp is not None:
        d.text((qcx, qy0 + 306), f"HP  {c.loser_hp}", font=font("medium", 18), fill=DIM, anchor="mm")

    if c.points:
        pill(img, (cx - 74, 614, cx + 74, 648), (36, 160, 112), f"+{c.points} POINTS", font("bold", 17), WHITE)
    _footer(img, c.brand, c.date_text)
    return to_jpeg(img)


# ----------------------------------------------------------------------------------------- champion card

def render_champion_card(c: ChampionCard) -> bytes:
    img = background(W, H, accent=(240, 170, 30), seed=c.champion.user_id + 7)
    confetti(img, seed=c.champion.user_id, avoid=[(330, 90, 950, 660), (60, 340, 330, 620), (950, 340, 1230, 620), (0, 20, 1280, 80)])
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy, ad = W // 2, 352, 256
    low = cy + ad // 2

    title, tf = fit(d, c.title.upper(), "bold", 32, 900, 20)
    spaced(d, (W / 2, 44), title, tf, (235, 238, 250), spacing=8, shadow=True, max_w=1000)

    laurel(img, (cx, cy), ad // 2 + 46)
    place_avatar(img, c.champion, (cx, cy), ad, ring=(GOLD_LIGHT, GOLD_DARK), ring_w=11, glow_color=GOLD)
    draw_crown(img, cx, cy - ad // 2 - 6, 132)
    d = ImageDraw.Draw(img, "RGBA")
    spaced(d, (W / 2, low + 56), "CHAMPION", font("bold", 50), GOLD, spacing=14, shadow=True)
    name, nf = fit(d, clean_name(c.champion.name), "bold", 44, 700, 26)
    d.text((cx, low + 112), name, font=nf, fill=WHITE, anchor="mm")
    bits = ([f"{c.wins} WINS"] if c.wins is not None else []) + ([f"{c.points} PTS"] if c.points is not None else [])
    if bits:
        d.text((cx, low + 152), "   •   ".join(bits), font=font("medium", 19), fill=MUTED, anchor="mm")

    def podium(people: list[Person], center_x: int, label: str, ring, col) -> None:
        if not people:
            return
        n = len(people)
        dia = 112 if n == 1 else 92
        gap = dia + 40
        start = center_x - (n - 1) * gap / 2
        for i, p in enumerate(people):
            x = int(start + i * gap)
            place_avatar(img, p, (x, 470), dia, ring=ring, ring_w=5)
            dd = ImageDraw.Draw(img, "RGBA")
            nm, f2 = fit(dd, clean_name(p.name), "medium", 20, gap - 6 if n > 1 else 230, 13)
            dd.text((x, 470 + dia // 2 + 28), nm, font=f2, fill=(225, 229, 240), anchor="mm")
        pill(img, (center_x - 44, 392 - 18, center_x + 44, 392 + 14), col, label, font("bold", 16), INK)

    podium([c.runner_up] if c.runner_up else [], 176, "2ND", (SILVER, SILVER_DARK), SILVER)
    podium(c.third[:2], W - 176, "3RD", (BRONZE, BRONZE_DARK), BRONZE)
    _footer(img, c.brand, c.date_text)
    return to_jpeg(img)


# ---------------------------------------------------------------------------------------- standings card

def render_standings_card(c: StandingsCard) -> bytes:
    rows = c.rows[:24]
    row_h, gap, top = 64, 8, 232
    width = 1200
    height = top + len(rows) * (row_h + gap) + (34 if c.show_cut else 0) + 96
    img = background(width, height, accent=BLUE, seed=len(rows) + 3)
    d = ImageDraw.Draw(img, "RGBA")

    title, tf = fit(d, c.title.upper(), "bold", 40, 1000, 22)
    spaced(d, (width / 2, 58), title, tf, GOLD, spacing=7, shadow=True, max_w=width - 140)
    spaced(d, (width / 2, 112), "STANDINGS", font("bold", 30), WHITE, spacing=10)
    sub = c.subtitle.upper()
    sf = font("medium", 18)
    sw = int(d.textlength(sub, font=sf)) + 52
    glass(img, (width // 2 - sw // 2, 140, width // 2 + sw // 2, 176), 18, tint_alpha=26, shadow=False)
    d.text((width / 2, 158), sub, font=sf, fill=(220, 225, 240), anchor="mm")

    hx = {"p": 742, "w": 818, "l": 894, "m": 996, "pts": 1104}
    for key, label in (("p", "P"), ("w", "W"), ("l", "L"), ("m", "HP ±"), ("pts", "PTS")):
        d.text((hx[key], 208), label, font=font("bold", 15), fill=DIM, anchor="mm")

    y = top
    for i, r in enumerate(rows, start=1):
        box = (40, y, width - 40, y + row_h)
        leader = i == 1 and not r.dq
        qualified = bool(c.playoff_spots) and i <= c.playoff_spots and not r.dq
        glass(img, box, 20, tint=GOLD if leader else WHITE, tint_alpha=40 if leader else (20 if i % 2 else 28),
              border_alpha=90 if leader else 36, shadow=False)
        if qualified:
            bar = Image.new("RGBA", (6, row_h - 22), GREEN + (255,))
            img.alpha_composite(bar, (40 + 10, y + 11))
        d = ImageDraw.Draw(img, "RGBA")
        medal(img, (104, y + row_h // 2), 40, i, font("bold", 19))
        place_avatar(img, r.person, (178, y + row_h // 2), 46, ring=(GOLD_LIGHT, GOLD_DARK) if leader else None, ring_w=3,
                     gray=r.dq)
        d = ImageDraw.Draw(img, "RGBA")
        name, nf = fit(d, clean_name(r.person.name), "bold", 25, 400, 15)
        d.text((216, y + row_h // 2), name, font=nf, fill=(130, 136, 156) if r.dq else WHITE, anchor="lm")
        if r.dq:
            nw = int(d.textlength(name, font=nf))
            d.line([(216, y + row_h // 2), (216 + nw, y + row_h // 2)], fill=(130, 136, 156), width=2)
            pill(img, (216 + nw + 12, y + row_h // 2 - 12, 216 + nw + 56, y + row_h // 2 + 12), (150, 40, 58), "DQ",
                 font("bold", 12), WHITE)
            d = ImageDraw.Draw(img, "RGBA")
        d.text((hx["p"], y + row_h // 2), str(r.played), font=font("medium", 23), fill=MUTED, anchor="mm")
        d.text((hx["w"], y + row_h // 2), str(r.wins), font=font("bold", 23), fill=(120, 230, 180), anchor="mm")
        d.text((hx["l"], y + row_h // 2), str(r.losses), font=font("medium", 23), fill=(240, 130, 140), anchor="mm")
        margin = f"+{r.margin}" if r.margin > 0 else (f"−{abs(r.margin)}" if r.margin < 0 else "0")
        d.text((hx["m"], y + row_h // 2), margin, font=font("medium", 21), fill=MUTED, anchor="mm")
        pill(img, (hx["pts"] - 48, y + 12, hx["pts"] + 48, y + row_h - 12), None, str(r.points), font("bold", 25), INK,
             gradient=vgrad((96, row_h - 24), GOLD_LIGHT, GOLD_DARK) if leader else vgrad((96, row_h - 24), (236, 240, 250), (176, 186, 206)))
        d = ImageDraw.Draw(img, "RGBA")
        y += row_h + gap
        if c.show_cut and c.playoff_spots and i == c.playoff_spots and i < len(rows):
            for x in range(60, width - 60, 22):
                d.line([(x, y + 6), (x + 12, y + 6)], fill=GREEN + (255,), width=3)
            d.text((width / 2, y + 22), "PLAYOFF LINE", font=font("bold", 13), fill=GREEN, anchor="mm")
            y += 34
    _footer(img, c.brand, c.footer, y=height - 34)
    return to_jpeg(img)


# ------------------------------------------------------------------------------------------ profile card

@dataclass
class ProfileCard:
    player: Person
    tier: str = "ROOKIE"
    wins: int = 0
    losses: int = 0
    knockouts: int = 0
    titles: int = 0
    finishers: int = 0
    streak: int = 0
    handle: str = ""                      # "@username"
    since: str = ""                       # "Member since Oct 2026"
    theme: str = "gold"                   # gold | fire | ice | royal
    held_belts: list[str] | None = None   # None = no belts row; else the belt names the player holds right now
    brand: str = "WWE ARENA"


BELTS = [("UNDISPUTED", "Undisputed WWE Championship"), ("WORLD HW", "World Heavyweight Championship"),
         ("INTERCONT.", "Intercontinental Championship"), ("U.S.", "United States Championship"),
         ("TAG TEAM", "World Tag Team Championship")]
THEMES = {"gold": ((240, 170, 30), GOLD, GOLD_DARK), "fire": ((255, 70, 30), (255, 140, 60), (200, 40, 20)),
          "ice": ((60, 150, 255), (150, 220, 255), (50, 110, 220)), "royal": ((150, 70, 255), (210, 160, 255), (110, 50, 200))}
TIER_COLORS = {"ROOKIE": (BRONZE, BRONZE_DARK), "CONTENDER": (SILVER, SILVER_DARK), "MAIN EVENTER": (GOLD_LIGHT, GOLD_DARK),
               "LEGEND": ((150, 230, 255), (70, 120, 255))}


def render_profile_card(c: ProfileCard) -> bytes:
    belts_on = c.held_belts is not None
    oy = 170 if belts_on else 0                    # everything under the name moves down to make room for the belts row
    w, h = 1080, 1350 + oy
    ring = TIER_COLORS.get(c.tier, TIER_COLORS["ROOKIE"])
    champion = c.titles > 0
    t_accent, t_line, t_dark = THEMES.get(c.theme, THEMES["gold"])
    img = background(w, h, accent=t_accent if c.theme != "gold" or champion else RED, seed=c.player.user_id + 11)
    if champion:
        confetti(img, seed=c.player.user_id, avoid=[(150, 150, 930, 1250)], count=40)
    d = ImageDraw.Draw(img, "RGBA")

    # premium frame
    glass(img, (60, 60, w - 60, h - 60), 44, tint_alpha=14, border_alpha=0)
    frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle((60, 60, w - 60, h - 60), radius=44, outline=t_line + (255,), width=3)
    ImageDraw.Draw(frame).rounded_rectangle((74, 74, w - 74, h - 74), radius=34, outline=t_dark + (110,), width=1)
    img.alpha_composite(frame)
    d = ImageDraw.Draw(img, "RGBA")

    spaced(d, (w / 2, 128), c.brand + "  •  MY CARD", font("bold", 26), t_line, spacing=8, shadow=True, max_w=800)

    # avatar + crown
    cx, cy, ad = w // 2, 450, 330
    place_avatar(img, c.player, (cx, cy), ad, ring=ring, ring_w=12, glow_color=ring[0])
    if champion:
        draw_crown(img, cx, cy - ad // 2 - 6, 170)
    tw = int(d.textlength(c.tier, font=font("bold", 22))) + 64
    pill(img, (cx - tw // 2, cy + ad // 2 - 6, cx + tw // 2, cy + ad // 2 + 38), None, c.tier, font("bold", 22), INK,
         gradient=vgrad((tw, 44), ring[0], ring[1]))
    d = ImageDraw.Draw(img, "RGBA")

    name, nf = fit(d, clean_name(c.player.name), "bold", 64, 800, 32)
    d.text((cx + 2, 706), name, font=nf, fill=(12, 8, 18), anchor="mm")
    d.text((cx, 702), name, font=nf, fill=WHITE, anchor="mm")
    sub = "   •   ".join(x for x in (c.handle, f"{c.titles}x CHAMPION" if champion else "") if x)
    if sub:
        d.text((cx, 754), sub, font=font("medium", 24), fill=GOLD if champion else MUTED, anchor="mm")

    # win-rate bar
    total = c.wins + c.losses
    rate = round(100 * c.wins / total) if total else 0
    if belts_on:
        d.text((w / 2, 836), "CHAMPIONSHIPS", font=font("bold", 18), fill=GOLD, anchor="mm")
        pw, pg = 152, 15
        px = (w - (5 * pw + 4 * pg)) // 2
        for i, (short, full) in enumerate(BELTS):
            x, held = px + i * (pw + pg), full in c.held_belts
            if held:
                pill(img, (x, 862, x + pw, 946), None, "", font("bold", 16), INK, gradient=vgrad((pw, 84), GOLD_LIGHT, GOLD_DARK))
            else:
                glass(img, (x, 862, x + pw, 946), 28, tint_alpha=14, shadow=False)
            d = ImageDraw.Draw(img, "RGBA")
            d.ellipse((x + pw / 2 - 13, 872, x + pw / 2 + 13, 898), outline=INK if held else DIM, width=3)
            lab, lf = fit(d, short, "bold", 17, pw - 16, 11)
            d.text((x + pw / 2, 922), lab, font=lf, fill=INK if held else DIM, anchor="mm")
    bx0, bx1, by = 130, w - 130, 838 + oy
    d.text((bx0, by - 26), "WIN RATE", font=font("bold", 18), fill=DIM, anchor="lm")
    d.text((bx1, by - 26), f"{rate}%", font=font("bold", 26), fill=WHITE, anchor="rm")
    d.rounded_rectangle((bx0, by, bx1, by + 16), radius=8, fill=(60, 64, 84))
    if rate > 0:
        d.rounded_rectangle((bx0, by, bx0 + max(16, int((bx1 - bx0) * rate / 100)), by + 16), radius=8,
                            fill=GREEN if rate >= 50 else GOLD)

    # stat tiles
    tiles = [("WINS", c.wins, (120, 230, 180)), ("LOSSES", c.losses, (240, 130, 140)), ("MATCHES", total, WHITE),
             ("KNOCKOUTS", c.knockouts, GOLD), ("FINISHERS", c.finishers, GOLD_LIGHT), ("BEST STREAK", c.streak, (150, 200, 255))]
    tw_, th_, gx, gy = 268, 140, 22, 22
    x0 = (w - (3 * tw_ + 2 * gx)) // 2
    for i, (label, value, col) in enumerate(tiles):
        x, y = x0 + (i % 3) * (tw_ + gx), 894 + oy + (i // 3) * (th_ + gy)
        glass(img, (x, y, x + tw_, y + th_), 24, tint_alpha=24, shadow=False)
        d = ImageDraw.Draw(img, "RGBA")
        d.text((x + tw_ / 2, y + 62), str(value), font=font("bold", 54), fill=col, anchor="mm")
        d.text((x + tw_ / 2, y + 108), label, font=font("medium", 17), fill=MUTED, anchor="mm")
    d.text((w / 2, h - 100), c.since, font=font("medium", 20), fill=DIM, anchor="mm")
    return to_jpeg(img)


# ------------------------------------------------------------------------------------- championship cards

@dataclass
class BeltCard:
    headline: str                         # "NEW CHAMPION" | "TITLE RETAINED"
    belt_name: str
    champions: list[Person]
    beaten: list[Person] = field(default_factory=list)
    belt_image: bytes | None = None
    reign: int = 1
    defenses: int = 0
    date_text: str = ""
    brand: str = "WWE ARENA"


@dataclass
class TitleRow:
    belt_name: str
    short: str
    holders: list[Person]
    detail: str = ""                      # "Reign #2 • 14 days" or "VACANT"
    belt_image: bytes | None = None


def belt_plate(name: str, size: tuple[int, int], image: bytes | None = None) -> Image.Image:
    """The belt picture the owner supplied (scaled to fit) or a drawn gold plate."""
    if image:
        try:
            im = Image.open(io.BytesIO(image)).convert("RGBA")
            im.thumbnail(size, Image.LANCZOS)
            return im
        except Exception:
            pass
    w, h = size
    plate = Image.new("RGBA", size, (0, 0, 0, 0))
    mask = rrect_mask(size, h // 3)
    plate.paste(vgrad(size, GOLD_LIGHT, GOLD_DARK).convert("RGBA"), (0, 0), mask)
    inner = rrect_mask((w - 28, h - 28), h // 3 - 10)
    plate.paste(vgrad((w - 28, h - 28), (40, 30, 12), (14, 10, 6)).convert("RGBA"), (14, 14), inner)
    d = ImageDraw.Draw(plate, "RGBA")
    r = int(h * 0.16)
    for cx in (int(w * 0.10), int(w * 0.90)):
        d.ellipse((cx - r, h / 2 - r, cx + r, h / 2 + r), outline=GOLD, width=max(2, h // 50))
    label = name.upper().replace(" CHAMPIONSHIP", "")
    text, f = fit(d, label, "bold", max(14, h // 6), int(w * 0.64), 10)
    d.text((w / 2, h / 2 - h * 0.05), text, font=f, fill=GOLD_LIGHT, anchor="mm")
    d.text((w / 2, h / 2 + h * 0.22), "CHAMPIONSHIP", font=font("bold", max(10, h // 11)), fill=GOLD_DARK, anchor="mm")
    return plate


def render_belt_card(c: BeltCard) -> bytes:
    img = background(W, H, accent=(240, 170, 30), seed=len(c.belt_name) + c.champions[0].user_id)
    confetti(img, seed=c.champions[0].user_id, avoid=[(250, 0, 1030, 100), (300, 100, 980, 700)], count=50)
    d = ImageDraw.Draw(img, "RGBA")
    spaced(d, (W / 2, 52), c.headline, font("bold", 40), GOLD, spacing=12, shadow=True, max_w=1000)
    belt = belt_plate(c.belt_name, (700, 200), c.belt_image)
    glow(img, (W / 2, 230), 330, GOLD, 0.45)
    drop_shadow(img, (W // 2 - belt.width // 2, 120, W // 2 + belt.width // 2, 120 + belt.height), 20, blur=16)
    img.alpha_composite(belt, (W // 2 - belt.width // 2, 120 + (200 - belt.height) // 2))
    d = ImageDraw.Draw(img, "RGBA")
    if c.belt_image:
        d.text((W / 2, 340), c.belt_name.upper(), font=font("bold", 22), fill=(235, 238, 250), anchor="mm")

    n = len(c.champions)
    dia = 160 if n == 1 else 136
    gap = dia + 150
    for i, p in enumerate(c.champions):
        x = int(W / 2 - (n - 1) * gap / 2 + i * gap)
        place_avatar(img, p, (x, 505), dia, ring=(GOLD_LIGHT, GOLD_DARK), ring_w=7, glow_color=GOLD)
        draw_crown(img, x, 505 - dia // 2 - 2, 72)
        dd = ImageDraw.Draw(img, "RGBA")
        nm, nf = fit(dd, clean_name(p.name), "bold", 30, gap - 20 if n > 1 else 520, 16)
        dd.text((x, 505 + dia // 2 + 28), nm, font=nf, fill=WHITE, anchor="mm")
    d = ImageDraw.Draw(img, "RGBA")
    bits = [f"REIGN #{c.reign}"] + ([f"{c.defenses} DEFENSES"] if c.defenses else [])
    if c.beaten:
        bits.append("DEF. " + " & ".join(clean_name(p.name, 16) for p in c.beaten))
    d.text((W / 2, 652), "   •   ".join(bits), font=font("medium", 19), fill=MUTED, anchor="mm")
    _footer(img, c.brand, c.date_text)
    return to_jpeg(img)


def render_titles_card(rows: list[TitleRow], title: str = "CHAMPIONS", brand: str = "WWE ARENA") -> bytes:
    width, row_h, gap, top = 1200, 112, 12, 190
    height = top + len(rows) * (row_h + gap) + 90
    img = background(width, height, accent=(240, 170, 30), seed=len(rows) + 5)
    d = ImageDraw.Draw(img, "RGBA")
    spaced(d, (width / 2, 66), title, font("bold", 44), GOLD, spacing=14, shadow=True)
    spaced(d, (width / 2, 122), "CURRENT TITLE HOLDERS", font("medium", 20), MUTED, spacing=6)
    y = top
    for r in rows:
        glass(img, (40, y, width - 40, y + row_h), 24, tint_alpha=22, shadow=False)
        plate = belt_plate(r.belt_name, (230, 84), r.belt_image)
        img.alpha_composite(plate, (64 + (230 - plate.width) // 2, y + (row_h - plate.height) // 2))
        d = ImageDraw.Draw(img, "RGBA")
        d.text((330, y + 38), r.short, font=font("bold", 26), fill=WHITE, anchor="lm")
        d.text((330, y + 74), r.detail, font=font("medium", 18), fill=GOLD if r.holders else DIM, anchor="lm")
        x = 700
        for p in r.holders[:2]:
            place_avatar(img, p, (x + 36, y + row_h // 2), 64, ring=(GOLD_LIGHT, GOLD_DARK), ring_w=3)
            dd = ImageDraw.Draw(img, "RGBA")
            nm, nf = fit(dd, clean_name(p.name, 20), "bold", 22, 150 if len(r.holders) > 1 else 330, 13)
            dd.text((x + 84, y + row_h // 2), nm, font=nf, fill=WHITE, anchor="lm")
            x += 240 if len(r.holders) > 1 else 0
        if not r.holders:
            d.text((700, y + row_h // 2), "— VACANT —", font=font("bold", 24), fill=DIM, anchor="lm")
        y += row_h + gap
    _footer(img, brand, "Use the 👑 Championships button to fight for a belt", y=height - 34)
    return to_jpeg(img)
