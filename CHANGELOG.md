# Changelog

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
