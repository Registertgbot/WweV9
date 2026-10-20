"""Decorative Unicode fonts for headings (Telegram renders these everywhere).

Only use them for fixed labels — never for player names (they would break search and screen readers).
"""
from __future__ import annotations

_DOUBLE_EXC = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}
_SMALL_CAPS = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))


def _shift(text: str, upper: int, lower: int, digit: int | None = None, exc: dict | None = None) -> str:
    out = []
    for ch in text:
        if exc and ch in exc:
            out.append(exc[ch])
        elif "A" <= ch <= "Z":
            out.append(chr(upper + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(lower + ord(ch) - 97))
        elif digit is not None and "0" <= ch <= "9":
            out.append(chr(digit + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)


def sans_bold(text: str) -> str:
    """𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀 — headings and announcements."""
    return _shift(text, 0x1D5D4, 0x1D5EE, 0x1D7EC)


def script(text: str) -> str:
    """𝓑𝓸𝓵𝓭 𝓢𝓬𝓻𝓲𝓹𝓽 — team names and flair."""
    return _shift(text, 0x1D4D0, 0x1D4EA)


def fraktur(text: str) -> str:
    """𝕭𝖔𝖑𝖉 𝕱𝖗𝖆𝖐𝖙𝖚𝖗 — title banners."""
    return _shift(text, 0x1D56C, 0x1D586)


def double(text: str) -> str:
    """𝔻𝕠𝕦𝕓𝕝𝕖-𝕤𝕥𝕣𝕦𝕔𝕜 — numbers and big labels."""
    return _shift(text, 0x1D538, 0x1D552, 0x1D7D8, _DOUBLE_EXC)


def small_caps(text: str) -> str:
    """sᴍᴀʟʟ ᴄᴀᴘs — little labels (ʜᴘ, ᴀᴘʀᴏɴ)."""
    return "".join(_SMALL_CAPS.get(ch, _SMALL_CAPS.get(ch.lower(), ch)) if ch.isalpha() else ch for ch in text)
