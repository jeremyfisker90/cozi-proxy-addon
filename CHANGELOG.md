# Changelog

## 1.18.1
- `POST /keep/newlist` creates an empty Keep list.

## 1.18.0
- Google Keep -> Cozi: `POST /keep/login` (email + an oauth_token from
  accounts.google.com/EmbeddedSetup, exchanged for a master token on the box)
  then every 60s new unchecked items on a Keep list are copied into the Cozi
  list with the matching name and ticked off in Keep. `GET /keep/lists` shows
  the mapping, `GET /keep/status` the health, `POST /keep/sync` runs it now.

## 1.17.2
- `DELETE /cozi/calendar/{year}/{month}/{id}` removes an appointment.

## 1.17.1
- `POST /voice/config` sets the spoken-name aliases and the mirror settings
  at runtime (stored in /data/voice.json, overriding the add-on options).

## 1.17.0
- Google Calendar mirror: `mirror_calendars` copies newly-added events from
  Home Assistant calendar entities into Cozi (attendee picked up from an
  "Evan:" prefix or a name in the title, description carried into the notes).
  The first pass only records existing events, so nothing backfills; optional
  `mirror_prefix` limits it to titles starting with a keyword. `POST
  /voice/mirror` runs a sweep on demand, `GET /voice/mirror` shows status.
- Quieted py-cozi's DEBUG logging, which was dumping every list on every sync.

## 1.16.2
- `GET /cozi/calendar/{year}/{month}` reads a month of Cozi appointments.

## 1.16.1
- `voice_aliases` option ("mom=Jane, dad=John") maps spoken names onto Cozi
  household members for calendar attendees.

## 1.16.0
- Voice intents: `POST /voice/intent {text}` understands plain sentences and
  writes them into Cozi — "add butter to the Kroger list" (alias table maps
  Aldi to the Kroger list, Lowe's to Home Depot) and "create an appointment
  for Evan Monday at 7 pm description pick up his laundry" (attendee, date,
  time, notes). Returns a `speech` string for the front door to read back.
  `POST /voice/parse` dry-runs a sentence, `GET /voice/log` shows the last 60,
  `GET /voice/persons` lists the household's Cozi attendee ids.

## 1.15.0
- Floorplan layout store: `GET/POST /floorplan` persists the dashboard's
  room + furniture editor layout in `/data/floorplan.json`.

## 1.14.0
- Tidy pass: fuzzy name matching merges wording-drift duplicate chores across
  Cozi / sheet / dashboard ("Mop the 1st floor" == "Mop first floor") while
  keeping genuinely different chores apart. Runs with the daily/weekly rolls.

## 1.13.1
- Rejected chores land back in the offending kid's queue automatically.

## 1.13.0
- Per-kid work queues: `POST /chores/queue` (no PIN, no SMS) drops a chore
  into a kid's queue; `queued_for` survives sheet sync and clears weekly.
  Parent `/chores/assign` also queues.

## 1.12.1
- `POST /sms/test` sends a test text to one family member.

## 1.12.0
- Chore texting via carrier email-to-SMS gateways (Gmail app password —
  free, no Twilio): `POST /chores/assign` texts the kid; completions text
  the other parent. All numbers/credentials live in add-on options; leave
  blank to disable.

## 1.11.0
- `cozi_enabled: false` runs Google-mode: chores sync with the Google Sheet
  only, no Cozi login required. Daily chore frequency added.
