"""Static screens and stats formatting (kept short so they fit on one phone screen)."""
from __future__ import annotations

from datetime import datetime

from services.scoreboard import esc

WELCOME = (
    "🥊 <b>WELCOME TO WWE ARENA</b>\n\nChallenge players, hit big moves, reverse attacks and climb the leaderboard.\n\n"
    "Choose your destination below.")

WELCOME_GROUP = ("🥊 <b>WWE ARENA</b> is open!\n\nSend /play for the arena menu with every game mode (or /match for a quick 1v1). "
                 "Moves are chosen privately — open me in DM and press Start first.")

HELP = (
    "📜 <b>WWE ARENA — RULEBOOK</b>\n\n❤️ 100 HP • first to 0 HP loses\n"
    "🔥 Finisher 40 (1 per match) • 💣 F5 30 • 💥 Spear 25 • ⚡ Super Kick 10 • 💢 DDT 5\n"
    "🛡️ Reversal: reflects the damage (2 per match)\n"
    "🎴 Wild Draw: once per match, a random move or a bonus Reversal\n"
    "⚠️ No repeating the same attack twice in a row (Wild Draw ignores this)\n"
    "🔁 The turn always passes to the other fighter\n\n"
    "<b>Commands</b>\n/match • /cancelmatch\n/rumble (3-12 players) • /tag [2-4] • /mitb [8|16|32] [slow]\n"
    "/briefcase • /mitbboard • /cancelmitb\n/rumbleboard • /tagboard • /cancelrumble • /canceltag\n"
    "/profile • /mystats • /history • /leaderboard • /id")


def fmt_rate(rate: int | None) -> str:
    return "—" if rate is None else f"{rate}%"


def profile_text(name: str, wins: int, losses: int, total: int, knockouts: int, dealt: int,
                 rate: int | None) -> str:
    tail = "" if total else "\n\nNo completed matches yet — step into the ring with /match!"
    return (f"👑 <b>SUPERSTAR PROFILE</b>\n\n👤 <b>{esc(name)}</b>\n🏆 Wins: {wins} • 💔 Losses: {losses}\n"
            f"🥊 Matches: {total} • ☠️ KOs: {knockouts}\n💥 Damage dealt: {dealt}\n🔥 Win rate: {fmt_rate(rate)}{tail}")


def mystats_text(name: str, s: dict) -> str:
    return (f"📊 <b>STATS — {esc(name)}</b>\n\n🏆 Wins: {s['wins']} • 💔 Losses: {s['losses']}\n"
            f"🥊 Matches: {s['total']} • ☠️ KOs: {s['knockouts']}\n"
            f"💥 Dealt: {s['dealt']} • 🩹 Received: {s['received']}\n"
            f"🔥 Finishers: {s['finishers']} • 🛡️ Reversals: {s['reversals']}\n"
            f"🔥 Win rate: {fmt_rate(s['rate'])}\n\n/history for recent matches")


def leaderboard_text(rows: list[tuple[str, int]], page: int, page_size: int, total_pages: int) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    if not rows:
        body = "No champions yet. Be the first — use /match in a group!"
    else:
        lines = []
        for i, (name, wins) in enumerate(rows, start=page * page_size + 1):
            lines.append(f"{medals.get(i, '▫️')} {i}. {esc(name)} — {wins} WIN{'S' if wins != 1 else ''}")
        body = "\n".join(lines)
    return (f"🏆 <b>CHAMPIONS BOARD</b>\n\n{body}\n\n"
            f"Page {page + 1}/{max(total_pages, 1)} • ranked by wins, win rate, matches")


def history_text(items: list[dict], page: int, total_pages: int) -> str:
    if not items:
        return "📜 <b>MATCH HISTORY</b>\n\nNo completed matches yet. Use /match in a group to get started!"
    out = []
    for it in items:
        icon = "🏆 WIN" if it["won"] else "💔 LOSS"
        when = it["when"].strftime("%Y-%m-%d %H:%M UTC") if isinstance(it["when"], datetime) else "—"
        how = {"timeout": " (timeout)", "knockout": " (KO)"}.get(it["reason"], "")
        out.append(f"{icon}{how} vs <b>{esc(it['opponent'])}</b> • 🕒 {when}\n"
                   f"🔢 {it['turns']} turns • 💥 {it['damage']} • 🔥 {it['finishers']} • 🛡️ {it['reversals']}")
    return ("📜 <b>MATCH HISTORY</b>\n\n" + "\n\n".join(out) + f"\n\nPage {page + 1}/{max(total_pages, 1)}")
