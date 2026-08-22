# Cozi Proxy + Family Chores API (Home Assistant add-on)

A local FastAPI add-on that does two things:

1. **Cozi bridge** — a clean local REST API over your Cozi account's to-do and
   shopping lists (Cozi has no public API).
2. **Family chores engine** — a points-based chore system that
   three-way syncs between a dashboard, two Cozi lists, and a shared Google
   Sheet. Built for the
   [El Dashboardio](https://github.com/jeremyfisker90/el-dashboardio) wall
   dashboard, but usable standalone.

## Chores engine features

- **Points per chore**, weekly per-kid totals, all-time totals, weekly
  win-streaks — everything a dashboard needs to render a scoreboard.
- **Required vs optional chores** — optional (bonus) chores stay locked until
  every required chore on the board is claimed.
- **Frequency rotation** — `daily` / `weekly` / `bi-weekly` / `monthly`.
  The board re-posts every Monday; a chore only returns once its interval has
  elapsed since it was last completed. Daily chores reopen every morning and
  the points bank across the week.
- **Parent rejection** — a claimed chore can be sent back with a comment; the
  kid loses the points and the chore reopens with the note attached.
- **Assignment** — chores can be assigned to a specific person or left open.
- **Three-way sync** (name-matched, the sheet wins for anything it lists):
  - **Google Sheet**: publish your chores sheet as CSV (File → Share →
    Publish to web → CSV) and POST the link to `/chores/sheeturl`. Columns
    are matched by keyword, so headers can be worded freely
    (name/points/steps/type/frequency/assigned-to).
  - **Cozi**: two lists, `Chores Required` and `Chores Optional`. Items use a
    parseable format the add-on maintains and documents in-list:
    `Chore name [points] @Who ~frequency  instructions`
    (`~daily` / `~weekly` / `~bi-weekly` / `~monthly`; `#` lines are notes).
  - **Local/API**: whatever a dashboard adds via the API.

## Running without Cozi (Google mode)

Cozi is **optional**. Set `cozi_enabled: false` in the add-on configuration
(or just leave the credentials blank) and the chores engine runs on the local
board + the published **Google Sheet** only — claiming, points, rotation,
parent rejection, streaks and history all work identically. The Cozi
passthrough endpoints simply report "not connected" and the sync note shows
sheet-only. `GET /chores` exposes `cozi_enabled` so front-ends can adapt.

## Install

1. Home Assistant → **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
   → add `https://github.com/jeremyfisker90/cozi-proxy-addon`.
2. Install **Cozi Proxy API**, enter your Cozi **username/password** in the
   add-on configuration (or set `cozi_enabled: false` for Google mode), and
   start it.
3. The API listens on port **5000** of your HA host (host networking, no
   auth — treat it as LAN-only and keep it off the internet).
4. Optional: wire up the Google Sheet (`POST /chores/sheeturl`) and seed some
   chores (see the dashboard repo's `chore_catalog.py` for a starter set).

## API

Cozi passthrough:

| Endpoint | What it does |
| --- | --- |
| `GET /lists` | All Cozi lists with items |
| `POST /add_item` `/edit_item` `/mark_item` `/remove_items` `/reorder_items` | Item operations |
| `POST /add_list` `/reorder_lists` | List operations |
| `GET /status`, `GET/POST /relogin` | Session health / re-auth |

Chores engine:

| Endpoint | What it does |
| --- | --- |
| `GET /chores` | Full board: chores, totals, gate state, rotation, people |
| `POST /chores/add` `/edit` `/delete` | Manage chores |
| `POST /chores/claim` | Kid claims a chore (409 already claimed, 423 optional locked, 425 not due) |
| `POST /chores/unclaim` | Undo a claim |
| `POST /chores/reject` | Parent sends a chore back with a comment; points removed |
| `POST /chores/clear_rejection` | Clear the rejection note |
| `POST /chores/target` | Set the weekly points target |
| `POST /chores/sheeturl` | Set (or clear) the published-CSV sheet link |
| `POST /chores/sync` | Force a three-way sync now |
| `GET /chores/history` | Closed weeks: totals + completions (streaks/all-time feed) |
| `POST /chores/newweek` | Manually close out the week |

State lives in `/data/chores.json` inside the add-on (survives restarts and
updates). Weekly history keeps a rolling year.

## Notes

- The board auto-rolls Monday (catching up if HA was off), daily chores
  re-open each morning — both happen during syncs, no cron needed.
- Cozi item text is capped at ~250 chars; the add-on trims at word boundaries
  and keeps the longer local description.
- No warranty: Cozi's private API can change. If login breaks, check
  `GET /status` and `/relogin`.
