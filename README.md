# 🏆 WWE Arena — Multiplayer Telegram Wrestling Bot

Players challenge each other in a Telegram **group** (`/match`), choose moves **privately in the bot's DM**,
and watch the fight unfold in the group with GIFs, captions, health updates and a live scoreboard.
Python 3.12 · aiogram 3 · PostgreSQL · async SQLAlchemy · Alembic · Railway.

> **Status:** the pure game rules (including the guaranteed (100%) Reversal and Wild Draw logic) have automated tests that were run.
> The database-backed tests, the Telegram handlers and the Alembic migrations (`0001`, `0002`) were written but **not executed**
> in the environment this project was generated in (no network, so aiogram/SQLAlchemy/PostgreSQL were unavailable).
> Run `alembic upgrade head`, `pytest` and a smoke test on your machine before deploying (see below).

## Rules

| Move | Damage | Limit |
|---|---|---|
| 🔥 Finisher | 40 | 1 per match |
| 💣 F5 | 30 | – |
| 💥 Spear | 25 | – |
| ⚡ Super Kick | 10 | – |
| 💢 DDT | 5 | – |
| 🛡️ Reversal | 100% — always reflects the incoming damage | 2 uses per match |
| 🎴 Wild Draw | random move (weighted) or a bonus Reversal | 1 per match |

* 100 HP each; first to 0 HP loses.
* The same attacking move can't be used on consecutive turns (enforced server-side).
* A Reversal can't reverse a Reversal. **100% to reflect** the damage (`REVERSAL_CHANCE` in `services/rules.py`).
  Every use costs one charge (2 per match).
* 🎴 **Wild Draw** (once per match, instead of picking an attack): F5 20% · Spear 20% · Super Kick 20% · DDT 10% · Finisher 10% ·
  Reversal 20%. An attack result is played immediately, ignores the no-repeat rule and still gives the opponent their Reversal window
  (a Finisher result counts as a Finisher use and can't come up if you have none left). A Reversal result gives you **+1 bonus Reversal
  charge** and you still choose an attack this turn. Weights live in `services/rules.py` (`WILD_WEIGHTS`).

### How a turn works (design decisions)

1. The active player picks an attack in DM. The opponent gets a private **"incoming attack"** prompt
   (Reversal / Take the hit) with a 15 s window. By default the move is **hidden** from the defender, so the defender has to guess; set `REVEAL_INCOMING_MOVE=true` to show it. If the defender has no Reversals left, the hit lands immediately.
2. Reversal → always succeeds: the damage is reflected onto the attacker (defender takes nothing). No answer / "Take the hit" → normal damage and no charge spent.
3. The turn **always passes to the other fighter** after a move, whether or not a Reversal was tried (so a successful reverser attacks next,
   and Reversal chains can't happen).
4. Each turn is one row in `match_turns` with a unique `(match_id, turn_number)`; the match row is locked
   (`SELECT … FOR UPDATE`) and phase/turn are re-checked, so duplicate or simultaneous callbacks resolve a turn once.

### Timeouts (all stored in PostgreSQL, restart-safe)

* Challenge expiry 60 s · Reversal window 15 s · Turn timeout 60 s (all configurable).
* **Turn-timeout policy** (`TIMEOUT_POLICY`, default `pause`):
  * `pause` – match pauses, both players are notified, either can press **Resume**; auto-cancelled after `PAUSE_EXPIRY_SECONDS` (24 h).
  * `cancel` – match cancelled, no stats recorded.
  * `forfeit` – the opponent gets a timeout win (counts as a win/loss, not a knockout).
* Reversal-window expiry is not a policy case: normal damage is applied.
* After a restart, deadlines that lapsed while the bot was offline get a short grace period (`RESTART_GRACE_SECONDS`).
* A deadline loop polls the database every ~1.5 s; there are no in-memory-only timers.

## Royal Rumble mode

`/rumble` in a group opens a 60 s lobby (JOIN / Leave / host Start-now buttons, 3-12 wrestlers; everyone must have pressed Start in the bot's DM).

* Entry order is random. **Two wrestlers start in the ring, then a new one walks in every 45 s** (sooner if the ring is down to one).
* **150 HP each** (a longer fight than the 1v1's 100), **1 Finisher, 2 Reversals** (100%) per wrestler, same moves and damage, no repeating a move twice in a row.
* **Eliminate someone → heal +25 HP** (adrenaline). A Finisher knockout uses the "over the top rope" GIF.
* On your turn (in DM): pick a move, then pick a target. The target gets the Reversal / Take-the-hit prompt (15 s).
* Turns go round the ring in entry order. Idle 45 s = turn skipped; two skips in a row = eliminated.
* The group sees one live **ring board** message that is edited every turn, plus GIFs for entrances, hits, eliminations, final two and the winner.
* Last wrestler standing wins. `/rumbleboard` = wins / eliminations; `/cancelrumble` = host only.
* Tunable through env vars: `RUMBLE_LOBBY_SECONDS`, `RUMBLE_ENTRY_SECONDS`, `RUMBLE_TURN_SECONDS`, `RUMBLE_MIN_PLAYERS`, `RUMBLE_MAX_PLAYERS`. HP / heal / AFK limit live in `services/rumble_rules.py`.
* Run `alembic upgrade head` (migration `0003`) before starting the new version.

Rumble GIFs (all optional, text fallback otherwise): `rumble_start`, `rumble_entry`, `rumble_elimination`, `rumble_over_the_top`, `rumble_final_two`, `rumble_winner` (`.gif` or `.mp4` in `assets/gifs/`).

## Tag Team mode (🦁 Lions vs 🐯 Tigers)

`/tag` (2v2), `/tag 3` (3v3) or `/tag 4` (4v4) in a group opens a lobby with **Join Lions / Join Tigers** (players can switch sides).
A match needs at least 2 per team; the teams may differ by one player (e.g. 3v2) and then the smaller team gets **+1 Reversal per wrestler**. The host can start early, otherwise it starts when the lobby timer ends.

* One wrestler per team in the ring; the others wait on the **apron** and heal **+5 HP every turn**.
* Teams take turns. On your turn: attack (the other team's ring wrestler may Reversal) **or press TAG** to swap with your partner. You can't tag twice in a row — attack first.
* Same stats as 1v1: 100 HP, 1 Finisher, 2 Reversals (100%) per wrestler, no repeating a move.
* A knocked-out wrestler is replaced by the healthiest partner (⚠️ HANDICAP when a team is down to one man). All wrestlers of a team down = the other team loses.
* Idle 45 s = turn skipped, 2 in a row = eliminated. A "your turn" ping with an *Open the bot* button is posted in the group.
* Headings use decorative Unicode fonts (`services/fancy.py`: 𝗯𝗼𝗹𝗱 𝘀𝗮𝗻𝘀, 𝓼𝓬𝓻𝓲𝓹𝓽, 𝕭𝖑𝖆𝖈𝖐𝖑𝖊𝖙𝖙𝖊𝖗, 𝕕𝕠𝕦𝕓𝕝𝕖-𝕤𝕥𝕣𝕦𝕔𝕜, sᴍᴀʟʟ ᴄᴀᴘs).
* `/tagboard` = wins, `/canceltag` = host only. Run `alembic upgrade head` (migrations `0004` and `0005`).

Tag Team GIFs (all optional, `.gif` or `.mp4` in `assets/gifs/`): `tag_start`, `tag_in`, `tag_ko`, `tag_handicap`, `tag_winner`.

## Money in the Bank tournament

`/mitb` (8 wrestlers), `/mitb 16`, `/mitb 32`; add `slow` (e.g. `/mitb 16 slow`) for a day-long mode. 4 to 32 players; it starts when the lobby is full, the lobby timer ends (min 4) or the host presses Start.

1. **Qualifier** — random 1v1 duels in parallel until 4 or fewer wrestlers are left (odd numbers: one random bye, never the same player twice if avoidable). Duels are **quiet**: moves happen in the players' DMs, the group only sees one live bracket board, the round announcement (with @-mentions) and each result.
2. **Ladder match** (3-4 wrestlers, public): climb 5 rungs to grab the briefcase. Attack instead and the target is knocked down a rung (Finisher: two). HP 0 = back to the floor with 30 HP and a lost turn. Reversal stops it and shakes the attacker down a rung. 12 turns each at most, then the highest rung / HP / damage wins.
3. **Briefcase** — the winner holds it for 7 days. After any 1v1 match in the group a 20 s **💼 CASH IN** button appears for the oldest valid holder (not one of the two fighters). The match is then holder (100 HP) vs the winner of that match (at least 70 HP), and the defender strikes first. Win or lose, the briefcase is used up.

Fair play: random draw and first move, identical stats (100 HP, 1 Finisher, 2 Reversals) for every duel, idle turns are skipped and two in a row forfeit, duels have a time limit (fast 12 min, slow 2 h) decided on HP, then damage, then a coin flip. Fast mode takes roughly 30-50 minutes.

Commands: `/mitb`, `/briefcase`, `/mitbboard`, `/cancelmitb` (host). Run `alembic upgrade head` (migration `0006`).

GIFs (all optional, `.gif` or `.mp4` in `assets/gifs/`): `mitb_start`, `mitb_round`, `mitb_duel_end`, `mitb_ladder`, `mitb_climb`, `mitb_fall`, `mitb_winner`, `mitb_cashin`, `mitb_champion`.

## Solo Tournament (🏆 button in the arena menu)

Owner-run **league + playoffs** made of normal 1v1 matches. Open **/play → 🏆 Tournament → 🧍 Solo Mode** (or `/tournament`).
Team Mode is a placeholder ("coming soon").

* **Only the bot owner** (`OWNER_ID`) can create a tournament and use the admin panel. The owner sends the name
  (reply to the bot's prompt), picks 4 / 6 / 8 / 10 / 12 / 16 players, and gets the **admin panel in DM**.
* Players tap **Join / Leave** on the registration card (they must have pressed Start in the bot's DM first).
  Extra players go to a **waiting list** and are promoted automatically. One open tournament per group, and a user can
  be in only one open tournament at a time (database-enforced).
* **League stage**: everybody plays everybody once (10 players = 9 matchdays, 45 matches). **Win = 3 points.**
  Table order: points → HP margin (winner HP − loser HP summed over your matches) → draw seed.
* **Playoffs**: top 4 → Semifinals (1st v 4th, 2nd v 3rd) → Final. With 3 players the top 2 play a Final; with 2 players
  the table decides.
* **Automatic matches**: when a fixture's time (IST) arrives the bot starts the 1v1 itself and pings both fighters.
  The first mover has the no-show window (default 5 min, owner-adjustable); an idle fighter forfeits (tournament
  matches never "pause"). A fighter busy in another match gets the same window, then loses by no-show (not while he is
  still playing an earlier fixture of this tournament). Walkovers and owner-set results count as a win with a 100 margin.
* **Admin panel** (all buttons): registration close time, first-match time, max players, start now, kick/disqualify,
  fixtures (change any fixture time in IST, or set a result), pause/resume, matchday gap / match gap / no-show window,
  re-draw, announce to the group, JSON backup, cancel. Times are typed in **IST**: `9:30pm`, `21:30`, `tomorrow 8pm`,
  `20-10 9:30pm`, `+30m`, `in 2 hours`.
* Matchday gaps re-time every fixture you didn't edit by hand. The live message shows the standings, the current
  matchday and, later, the playoffs; it is edited after every result.
* History, personal stats and a tournament leaderboard are under the same menu.
* **Restart-safe**: everything is stored in the database. After a restart the loop continues, finished matches are
  picked up automatically, and a JSON backup is sent to the owner after every matchday.
* Run `alembic upgrade head` (migrations `0007` and `0008`). Optional GIFs: `tour_start`, `tour_match`, `tour_champion`.
* **My Match / Standings buttons** on the live card: *My Match* DMs the fighter his next match; from there he can
  **request a reschedule** (reply a time in IST). The opponent (or owner) gets Accept/Decline buttons; max 24h later, 2 moves
  per match, closes 10 min before the match, expires after 20 min. Accepted moves lock the new time and are announced.
* **Image cards** (Pillow, fonts in `assets/fonts`): a winner card with both fighters' profile pictures after every match,
  a champion card, and a standings image after each matchday / league end (also via the Standings button).
* **Results channel**: set `RESULTS_CHANNEL_ID` (add the bot as channel admin) and every card is mirrored there. Check with `/channeltest`.
* Run `alembic upgrade head` (migration `0009`).
* The engine still contains the knockout-bracket format (`format="knockout"`), but the menu creates leagues.

## Group access (owner approval)

The bot only works in `DEFAULT_GROUP_ID` groups and groups you approve. In any other group it answers commands with a
"not authorized" notice (and blocks buttons). Owner commands: `/allow` (in the group, or `/allow <chat_id>` in DM),
`/disallow`, `/groups`. Run `alembic upgrade head` (migration `0010`).

Event media can also be a **picture**: put `tour_start.png` / `.jpg` / `.webp` (same names as the GIFs) in `assets/gifs/`.
A GIF/MP4 with the same name wins if both exist. If you replace a file, clear its cached id: `UPDATE media_assets SET telegram_file_id=NULL;`

## Championships

`/play` → **👑 Championships** shows the 5 belts (Undisputed WWE, World Heavyweight, Intercontinental, United States, World Tag Team).
Tap one: a title-match lobby opens (1v1 challenge, or a 2v2 tag lobby for the tag belt). When the match ends the winner takes the
belt, a NEW CHAMPION / TITLE RETAINED card is posted (group + results channel) and the belts row of **My Card** lights up.
While a belt is held its champion must be in the match: a challenge to a champion can only be accepted by him (24h, then it expires); a champion's own challenge is open; a vacant belt is open. Tag belt: a title lobby where no champion plays leaves the belt unchanged. `/titles` shows the champions board. Owner: `/givebelt <belt> [id id2]` (or reply), `/vacate <belt>`. Belt keys: undisputed, worldhw, ic, us, tag.
Put your own belt pictures in `assets/belts/` (`undisputed.png`, `worldhw.png`, `ic.png`, `us.png`, `tag.png`); a drawn plate is used otherwise.
Run `alembic upgrade head` (migration `0012`).

## My Card

`/mycard` (or reply to someone with `/mycard`) and the 🪪 button on the tournament card show a premium player card: profile
photo (or the `/fighter` wrestler), tier (Rookie < 10 wins, Contender 10, Main Eventer 25, Legend 50), tournament titles with a
crown, and stats. Theme unlocks with titles: Gold, Fire (1), Ice (3), Royal (5). The belts row exists in `cards.ProfileCard.held_belts`
for the future championships feature.

## Wrestler pictures

Drop images into `assets/wrestlers/` (file name = display name, e.g. `john_cena.jpg`). Players send `/fighter`, pick one, and it
replaces their profile photo on winner / champion / standings cards. Run `alembic upgrade head` (migration `0011`).
You are responsible for having the rights to any images you add.

## Commands

`/start` `/help` `/match` `/profile` `/mystats` `/leaderboard` `/history` `/cancelmatch` `/id`
Owner only: `/settings`, `/setsetting <key> <value>` (keys: `challenge_timeout`, `turn_timeout`, `reversal_timeout`,
`timeout_policy`, `maintenance_mode`, `matches_enabled`). Owner = the Telegram ID in `OWNER_ID` (send `/id` to find yours).

* `/cancelmatch` cancels an open challenge immediately; for a running match it asks the opponent to agree. Spectators can't cancel anything.
* `/history` is DM-only and only ever shows the requesting player's own matches.
* Leaderboard: completed matches only; ordered by wins → win rate → matches played → oldest account.

## Project layout

```
bot.py  config.py  database.py  models.py
handlers/   start help match moves rumble profile leaderboard history admin common
keyboards/  main challenge moves rumble
services/   rules (pure game logic) · match_engine (DB, atomic transitions) · turn_manager (Telegram publishing + deadline loop)
            rumble_rules (pure) · rumble_engine (DB) · rumble_manager (publishing + loop) · rumble_text
            scoreboard (captions) · texts · stats · media_manager (GIFs) · runtime_settings · tg (safe Telegram calls)
migrations/ Alembic (async)   tests/   assets/gifs/
```

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # fill in BOT_TOKEN, DATABASE_URL, OWNER_ID
alembic upgrade head            # create tables in PostgreSQL
python bot.py
```

Local Postgres URL example: `postgresql://user:pass@localhost:5432/wwe_arena` (it's converted to `postgresql+asyncpg://` automatically).

**Before you play:** every fighter must open the bot privately and press **Start** once (Telegram doesn't allow bots to DM people first).
Disable nothing in BotFather except, optionally, group privacy isn't required for commands.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

* `test_moves.py`, `test_reversal.py`, `test_config.py` – pure logic (no database); includes Wild Draw weights and the Reversal roll.
* `test_wild_draw.py` – Wild Draw through the engine (bonus Reversal, repeat-rule skip, once per match).
* `test_match_engine.py`, `test_timeouts.py`, `test_persistence.py` – use a temporary SQLite file through the same SQLAlchemy code.
  SQLite ignores `FOR UPDATE`, so the simultaneous-callback test checks the outcome (one resolution, consistent HP) rather than lock behaviour;
  repeat that scenario against PostgreSQL for a production-grade check.
* `test_media.py` – GIF missing / rejected → text fallback, `file_id` caching.

## Add the GIFs

Copy the files into `assets/gifs/` (names must match exactly; each can be `.gif` **or** `.mp4`):
`spear f5 finisher superkick ddt reversal knockout` (e.g. `spear.mp4`).
No GIFs are bundled and none are downloaded for you. On first successful send the bot caches Telegram's `file_id`
in the `media_assets` table and reuses it. Missing/rejected GIFs automatically fall back to the same message as text.
On Railway the filesystem is ephemeral, so commit the GIFs to your repository (or rebuild with them included).

## Deploy on Railway

1. Push this folder to a GitHub repository (GIFs included, `.env` excluded).
2. Railway → **New Project → Deploy from GitHub**, then **+ New → Database → PostgreSQL**.
3. In the bot service → **Variables**: set `BOT_TOKEN`, `OWNER_ID`, `TIMEOUT_POLICY` etc., and
   `DATABASE_URL` = reference to the Postgres service's `DATABASE_URL`.
4. Start command: the included `Procfile` runs `alembic upgrade head && python bot.py`. If Railway doesn't pick it up,
   set that string as the **Start Command** in the service settings.
5. Run **one** instance only (long polling). Redeploys are safe: matches and deadlines live in PostgreSQL.
6. Check **Deployments → Logs** for `Starting @yourbot` and the GIF warning list.

### Troubleshooting

* `DATABASE_URL is not set` – add the variable/reference. `postgres://` and `postgresql://` are both accepted; `sslmode=require` is honoured.
* "I can't message X" in the group – that player hasn't pressed Start in the bot's DM (or blocked it).
* Buttons say "outdated" – the turn already advanced or timed out; use the newest prompt.
* `TelegramConflictError` – two instances are polling with the same token; stop the other one.
* Alembic errors – run `alembic upgrade head` manually from a Railway shell and read the traceback. Verify the migration matches the models with `alembic revision --autogenerate` on a scratch database (it should detect no changes).
