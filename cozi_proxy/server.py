import os
import json
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from cozi import Cozi
from cozi.exceptions import InvalidLoginException, CoziException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import aiohttp

app = FastAPI(title="Cozi Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cozi_client: Cozi | None = None
logged_in = False
# False = "Google mode": chores run on the local board + Google Sheet only,
# no Cozi login and no Cozi mirroring. Set via the add-on's cozi_enabled
# option, or automatically when no Cozi credentials are configured.
COZI_ENABLED = True

# ====================== IMPROVED AUTO LOGIN WITH BROWSER HEADERS ======================
async def auto_login():
    global cozi_client, logged_in, COZI_ENABLED
    print("=== Cozi Proxy: Auto-login starting ===")

    options_path = "/data/options.json"
    if not os.path.exists(options_path):
        print("❌ options.json not found!")
        return

    with open(options_path, "r") as f:
        options = json.load(f)
        username = options.get("username")
        password = options.get("password")

    if not options.get("cozi_enabled", True):
        COZI_ENABLED = False
        print("ℹ️ Cozi integration disabled — running in Google/sheet-only mode.")
        return

    if not username or not password:
        COZI_ENABLED = False
        print("ℹ️ No Cozi credentials configured — running in Google/sheet-only mode.")
        return

    print(f"Logging in with username: {username}")

    # Create a real browser-like session
    session = aiohttp.ClientSession(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.cozi.com/",
            "Origin": "https://www.cozi.com",
        }
    )

    cozi_client = Cozi(username, password)
    cozi_client._session = session  # Override with our browser session

    for attempt in range(6):
        try:
            await cozi_client.login()
            print("✅ Login successful!")
            logged_in = True
            return
        except Exception as e:
            print(f"❌ Login attempt {attempt+1}/6 failed: {e}")
            await asyncio.sleep(12)

    print("⚠️ All login attempts failed. Use /relogin to try again.")

@app.on_event("startup")
async def startup_event():
    # py-cozi turns on DEBUG logging for everything at import time, which dumps
    # every list in full on every sync. Quiet it before it fills the log.
    import logging
    logging.getLogger("cozi").setLevel(logging.WARNING)
    logging.getLogger().setLevel(logging.INFO)
    _load_sms_options()
    _load_mirror_options()
    await auto_login()
    asyncio.create_task(_sync_loop())      # keeps chores reconciled with Cozi + sheet
    asyncio.create_task(_mirror_loop())    # Google Calendar -> Cozi, if configured
    asyncio.create_task(_keep_loop())      # Google Keep -> Cozi, if configured

@app.on_event("shutdown")
async def shutdown_event():
    global cozi_client
    if cozi_client and hasattr(cozi_client, '_session'):
        try:
            await cozi_client._session.close()
            print("✅ Session closed on shutdown")
        except:
            pass

# ====================== RELOGIN PAGE (browser friendly) ======================
@app.get("/relogin", response_class=HTMLResponse)
async def relogin_get():
    status = "✅ Logged in" if logged_in else "❌ Not logged in - Click the button below"
    html = f"""
    <html>
    <head><title>Cozi Proxy - Relogin</title></head>
    <body style="font-family:Arial; text-align:center; padding:80px; background:#f8f9fa;">
        <h1>Cozi Proxy Status</h1>
        <p style="font-size:20px;"><strong>{status}</strong></p>
        <button onclick="retry()" style="font-size:20px; padding:18px 50px; background:#ff9800; color:white; border:none; border-radius:12px; cursor:pointer;">
            🔄 Retry Login Now
        </button>
        <p id="result" style="margin-top:30px; font-size:18px; min-height:30px;"></p>
        <script>
        async function retry() {{
            const res = await fetch('/relogin', {{ method: 'POST' }});
            const data = await res.json();
            document.getElementById('result').innerHTML = '<strong>' + data.message + '</strong>';
            if (data.status === 'success') setTimeout(() => location.reload(), 1000);
        }}
        </script>
    </body>
    </html>
    """
    return html

@app.post("/relogin")
async def relogin_post():
    global logged_in
    if not cozi_client:
        raise HTTPException(status_code=400, detail="Client not initialized")
    try:
        await cozi_client.login()
        logged_in = True
        return {"status": "success", "message": "✅ Login successful!"}
    except Exception as e:
        logged_in = False
        return {"status": "error", "message": f"❌ Login failed: {str(e)}"}

# ====================== STATUS ======================
@app.get("/status")
async def status():
    if not COZI_ENABLED:
        msg = "Cozi disabled - chores run on the local board + Google Sheet"
    elif logged_in:
        msg = "Ready - lists should load"
    else:
        msg = "Not logged in - go to /relogin"
    return {"logged_in": logged_in, "cozi_enabled": COZI_ENABLED,
            "sms_enabled": bool(SMS["user"] and SMS["pw"]),
            "sms_phones": [p for p, n in SMS["phones"].items() if n],
            "message": msg}

# ====================== SERVE YOUR HTML ======================
@app.get("/", response_class=HTMLResponse)
async def serve_html():
    try:
        with open("/cozi_proxy/cozi-interface.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse("<h1>cozi-interface.html not found in add-on</h1>")

# ====================== YOUR ORIGINAL ENDPOINTS ======================
class AddItemRequest(BaseModel):
    list_id: str
    item_text: str
    item_pos: int

class EditItemRequest(BaseModel):
    list_id: str
    item_id: str
    item_text: str

class MarkItemRequest(BaseModel):
    list_id: str
    item_id: str
    status: str

class RemoveItemsRequest(BaseModel):
    list_id: str
    item_ids: list[str]

class ReorderRequest(BaseModel):
    list_id: str
    list_title: str
    items_list: list
    list_type: str

class AddListRequest(BaseModel):
    list_title: str
    list_type: str = "shopping"

class ReorderListsRequest(BaseModel):
    lists: list


@app.get("/lists")
async def get_lists():
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in. Go to /relogin first")
    try:
        lists = await cozi_client.get_lists()
        return {"lists": lists}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/add_item")
async def add_item(req: AddItemRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.add_item(req.list_id, req.item_text, req.item_pos)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/edit_item")
async def edit_item(req: EditItemRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.edit_item(req.list_id, req.item_id, req.item_text)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/mark_item")
async def mark_item(req: MarkItemRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.mark_item(req.list_id, req.item_id, req.status)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/remove_items")
async def remove_items(req: RemoveItemsRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.remove_items(req.list_id, req.item_ids)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/reorder_items")
async def reorder_items(req: ReorderRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.reorder_list(req.list_id, req.list_title, req.items_list, req.list_type)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/add_list")
async def add_list(req: AddListRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.add_list(req.list_title, req.list_type)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


@app.post("/reorder_lists")
async def reorder_lists(req: ReorderListsRequest):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Not logged in")
    try:
        await cozi_client.reorder_lists(req.lists)
        return {"status": "ok"}
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))

# ====================== CHORES / POINTS ======================
# Local JSON is the store; Cozi lists and an optional published-CSV spreadsheet are
# additional INPUT channels that stay reconciled with it. Chores are matched across
# all three sources by normalised name, so the same chore never lands twice.
#
# FREQUENCY ROTATION: the board is re-posted once a week (anchored to Monday). A
# chore only goes back up once its interval has elapsed since it was last completed,
# so a monthly job done on the 3rd stays off the board until the 2nd of next month.
# The spreadsheet is the source of truth for each chore's frequency; the "last done"
# dates live here because only this service sees claims as they happen.
import re
import csv
import io
import datetime

CHORES_DB = "/data/chores.json"
_chores_lock = asyncio.Lock()

COZI_LISTS = {"chores required": "required", "chores optional": "optional"}
SYNC_EVERY = 300  # seconds

FREQ_DAYS = {"daily": 1, "weekly": 7, "bi-weekly": 14, "biweekly": 14, "monthly": 30,
             "once": 3650}   # one-off (ad-hoc): effectively never reposts once done
FREQ_CANON = {"daily": "daily", "weekly": "weekly", "bi-weekly": "bi-weekly",
              "biweekly": "bi-weekly", "monthly": "monthly"}
DEFAULT_FREQ = "weekly"

# Who a chore belongs to. "na" = nobody in particular, anyone can grab it.
PEOPLE = ["ian", "evan", "dad", "mom", "na"]
PEOPLE_LABEL = {"ian": "Ian", "evan": "Evan", "dad": "Dad", "mom": "Mom", "na": "NA"}

# ====================== SMS (carrier email-to-text) ======================
# Texts are plain emails to <number>@<carrier gateway> sent through Gmail
# with an app password. All values come from the add-on options; leaving
# smtp_user/smtp_pass blank disables texting without breaking anything.
import smtplib
from email.mime.text import MIMEText

SMS = {"user": "", "pw": "", "gateway": "vtext.com", "tz": "America/New_York",
       "phones": {}}


def _load_sms_options():
    try:
        with open("/data/options.json") as f:
            o = json.load(f)
    except Exception:
        return
    SMS["user"] = (o.get("smtp_user") or "").strip()
    SMS["pw"] = (o.get("smtp_pass") or "").strip()
    SMS["gateway"] = (o.get("sms_gateway") or "").strip() or "vtext.com"
    SMS["tz"] = (o.get("timezone") or "").strip() or "America/New_York"
    SMS["phones"] = {p: re.sub(r"\D", "", o.get("phone_" + p) or "")
                     for p in ("ian", "evan", "mom", "dad")}
    ready = [p for p, n in SMS["phones"].items() if n]
    if SMS["user"] and SMS["pw"]:
        print(f"SMS: texting enabled via {SMS['gateway']} for {ready or 'nobody'}")
    else:
        print("SMS: texting disabled (no smtp_user/smtp_pass configured)")


def _sms_ready(who):
    return bool(SMS["user"] and SMS["pw"] and SMS["phones"].get(who))


def _smtp_text(addr, body, label):
    """One text out through Gmail. Returns True when the SMTP handoff worked."""
    msg = MIMEText(body)
    msg["From"] = SMS["user"]
    msg["To"] = addr
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(SMS["user"], SMS["pw"])
            s.sendmail(SMS["user"], [addr], msg.as_string())
        print(f"SMS sent to {label}: {body[:70]}")
        return True
    except Exception as e:
        print(f"SMS to {label} FAILED: {e}")
        return False


def _send_sms_sync(who, body):
    if not _sms_ready(who):
        print(f"SMS skipped ({who}): texting not configured")
        return False
    return _smtp_text("%s@%s" % (SMS["phones"][who], SMS["gateway"]), body, who)


async def _send_sms(who, body):
    return await asyncio.to_thread(_send_sms_sync, who, body)


def _sms_number(value):
    """'(513) 417-7196', '15134177196' -> '5134177196'. None if it isn't a
    10-digit US number. A name (ian/evan/mom/dad) resolves to that phone."""
    v = (value or "").strip().lower()
    if v in SMS["phones"]:
        v = SMS["phones"][v]
    n = re.sub(r"\D", "", v or "")
    if len(n) == 11 and n.startswith("1"):
        n = n[1:]
    return n if len(n) == 10 else None


def _send_sms_raw_sync(number, body, gateway=None):
    """Text any number, not just the four family phones."""
    if not (SMS["user"] and SMS["pw"]):
        print("SMS skipped: texting not configured")
        return False
    num = _sms_number(number)
    if not num:
        print(f"SMS skipped: {number!r} is not a 10-digit number")
        return False
    gw = (gateway or "").strip() or SMS["gateway"]
    return _smtp_text("%s@%s" % (num, gw), body, num)


async def _send_sms_raw(number, body, gateway=None):
    return await asyncio.to_thread(_send_sms_raw_sync, number, body, gateway)


def _now_local():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(SMS["tz"]))
    except Exception:
        return datetime.datetime.now()


def _stamp(dt):
    """'9:59am August 21st' — the format the completion texts use."""
    h = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    day = dt.day
    suf = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return "%d:%02d%s %s %d%s" % (h, dt.minute, ampm, dt.strftime("%B"), day, suf)


def _person(value):
    v = re.sub(r"[^a-z]", "", (value or "").lower())
    if not v:
        return "na"
    for p in PEOPLE:
        if v.startswith(p):
            return p
    if v.startswith("ash"):        # Mom by name
        return "mom"
    if v.startswith("tom"):        # Dad by name
        return "dad"
    if v in ("none", "anyone", "any", "unassigned"):
        return "na"
    return "na"


def _freq(value):
    v = re.sub(r"[^a-z]", "", (value or "").lower())
    if v.startswith("da") or v.startswith("everyday") or v.startswith("eachday"):
        return "daily"
    if v.startswith("bi") or v.startswith("every2") or v.startswith("fort"):
        return "bi-weekly"
    if v.startswith("mo"):
        return "monthly"
    if v.startswith("we") or v.startswith("每"):
        return "weekly"
    return None


def _freq_days(f):
    return FREQ_DAYS.get((f or DEFAULT_FREQ).lower(), 7)


def _today():
    return datetime.date.today()


def _monday(d):
    return d - datetime.timedelta(days=d.weekday())


def _parse_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def _chores_default():
    return {"target": 100, "chores": [], "log": {"ian": [], "evan": []}, "week_start": None,
            "next_id": 1, "sheet_url": "", "last_sync": None, "sync_error": "", "sync_note": "",
            "history": [], "rejections": [], "schema": 0}


def _chores_read():
    if not os.path.exists(CHORES_DB):
        return _chores_default()
    try:
        with open(CHORES_DB, "r") as f:
            d = json.load(f)
        base = _chores_default()
        base.update(d or {})
        base.setdefault("log", {}).setdefault("ian", [])
        base["log"].setdefault("evan", [])
        for c in base.get("chores", []):          # migrate older rows
            c.setdefault("kind", "required")
            c.setdefault("source", "dashboard")
            c.setdefault("frequency", DEFAULT_FREQ)
            c.setdefault("last_done", None)
            c.setdefault("posted", True)
            c.setdefault("from_sheet", False)
            c.setdefault("assigned_to", "na")
            # kid work queue — LOCAL ONLY, never overwritten by sheet/Cozi sync
            c.setdefault("queued_for", "na")
        return base
    except Exception:
        return _chores_default()


def _chores_write(d):
    tmp = CHORES_DB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, CHORES_DB)


_NORM_ORDINALS = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth",
                  "5th": "fifth"}
_NORM_STOPWORDS = {"the", "a", "an"}


def _norm(name):
    """Match key across Cozi / sheet / dashboard. Deliberately fuzzy about
    wording drift ("Mop the 1st floor" == "Mop first floor") but keeps real
    distinguishing words, so genuinely different chores never collapse."""
    s = re.sub(r"[^\w\s()/&-]", "", (name or "").strip().lower())
    words = [_NORM_ORDINALS.get(w, w) for w in s.split()
             if w not in _NORM_STOPWORDS]
    return " ".join(words)


def _dedupe(d):
    """Tidy pass: collapse chores whose names differ only in wording drift.
    Keeps the row with real state (done > queued > sheet-backed > oldest),
    folds anything the duplicates knew into the keeper."""
    seen, keep, removed = {}, [], 0
    for c in d["chores"]:
        k = _norm(c.get("name"))
        if k not in seen:
            seen[k] = c
            keep.append(c)
            continue
        a, b = seen[k], c

        def _rank(x):
            return (bool(x.get("done_by")), x.get("queued_for", "na") != "na",
                    bool(x.get("from_sheet")))
        winner, loser = (a, b) if _rank(a) >= _rank(b) else (b, a)
        if winner is b:
            keep[keep.index(a)] = b
            seen[k] = b
        if len(loser.get("description") or "") > len(winner.get("description") or ""):
            winner["description"] = loser["description"]
        if winner.get("queued_for", "na") == "na":
            winner["queued_for"] = loser.get("queued_for", "na")
        if winner.get("assigned_to", "na") == "na":
            winner["assigned_to"] = loser.get("assigned_to", "na")
        if not winner.get("rejected") and loser.get("rejected"):
            winner["rejected"] = loser["rejected"]
        if loser.get("from_sheet"):
            winner["from_sheet"] = True
        removed += 1
    if removed:
        d["chores"] = keep
        print("tidy: merged %d duplicate chore(s)" % removed)
    return removed


_PTS_RE = re.compile(r"\[\s*(\d+)\s*\]")
_WHO_RE = re.compile(r"^@([\w]+)\s*")
_FRQ_RE = re.compile(r"^~([\w-]+)\s*")
# A line beginning with # is a note for the family, not a chore.
NOTE_PREFIX = "#"
COZI_GUIDE = ("# FORMAT:  Chore name [points] @Who ~frequency  then the instructions."
              "   @Who = Ian/Evan/Dad/Mom (leave off = anyone).  ~frequency = ~daily /"
              " ~weekly / ~bi-weekly / ~monthly (leave off = weekly).  Required vs optional is"
              " whichever list you put it in.   EXAMPLE:  Walk the dog [10] @Ian ~weekly"
              " A real walk round the block, take a bag, fresh water when you get back.")


def _parse_item(text):
    """`Walk the dog [10] @Ian ~weekly A real walk round the block`
       -> (name, 10, 'ian', 'weekly', description).
    Both the @who and ~frequency tokens are optional and may appear in either
    order, so an item written before this convention still parses."""
    text = (text or "").strip()
    m = _PTS_RE.search(text)
    if not m:
        return text, 0, "na", None, ""
    name = text[:m.start()].strip(" -:–—")
    rest = text[m.end():].lstrip(" -:–—")
    who, freq = "na", None
    for _ in range(2):                       # accept @who ~freq or ~freq @who
        wm = _WHO_RE.match(rest)
        if wm:
            who = _person(wm.group(1))
            rest = rest[wm.end():]
            continue
        fm = _FRQ_RE.match(rest)
        if fm:
            freq = _freq(fm.group(1))
            rest = rest[fm.end():]
            continue
        break
    return name, int(m.group(1)), who, freq, rest.strip(" -:–—")


COZI_MAX = 250          # Cozi silently truncates list-item text around here


def _fmt_item(c):
    """Inverse of _parse_item, for pushing dashboard-added chores into Cozi.
    Trimmed on a word boundary so Cozi's own truncation never cuts mid-word."""
    s = "%s [%d]" % (c["name"], int(c.get("points", 0)))
    who = c.get("assigned_to") or "na"
    if who != "na":
        s += " @%s" % PEOPLE_LABEL.get(who, who.title())
    freq = c.get("frequency") or DEFAULT_FREQ
    if freq != DEFAULT_FREQ:
        s += " ~%s" % freq
    desc = (c.get("description") or "").strip()
    if desc:
        room = COZI_MAX - len(s) - 1
        if len(desc) > room:
            desc = desc[:room].rsplit(" ", 1)[0].rstrip(" ,.;—-") + "…"
        s += " " + desc
    return s


def _merge_desc(old, new):
    """Cozi caps item text, so the description that comes back can be a truncated
    prefix of what we hold. Never let that shorter copy overwrite the full one."""
    old, new = (old or "").strip(), (new or "").strip()
    if not new:
        return old
    if old and len(new) < len(old):
        stem = new.rstrip("…").rstrip()
        if old.startswith(stem[:max(1, len(stem) - 2)]):
            return old
    return new


def _due_date(c):
    """When this chore is next allowed on the board."""
    ld = _parse_date(c.get("last_done") or "")
    if not ld:
        return None                      # never done -> eligible now
    return ld + datetime.timedelta(days=_freq_days(c.get("frequency")))


def _is_due(c, on=None):
    d = _due_date(c)
    return True if d is None else d <= (on or _today())


def _repost(d, on=None):
    """Decide what sits on the board. Unfinished chores stay up; completed ones
    come back only once their frequency interval has elapsed."""
    on = on or _today()
    for c in d["chores"]:
        # a chore sent back by a parent stays up until it's redone
        c["posted"] = True if c.get("rejected") else _is_due(c, on)


def _gate(chores):
    """Optional chores stay locked until every POSTED required chore is claimed."""
    req = [c for c in chores
           if c.get("kind", "required") == "required" and c.get("posted", True)]
    left = [c for c in req if not c.get("done_by")]
    return {"required_total": len(req), "required_left": len(left),
            "optional_unlocked": len(left) == 0}


def _roll_week(d, on=None):
    """Close out the week: stamp completions, clear the board, re-post what's due."""
    on = on or _today()
    done = [c for c in d["chores"] if c.get("done_by")]
    for c in done:
        c["last_done"] = on.isoformat()
    if done:
        d.setdefault("history", []).append({
            "week_start": d.get("week_start"),
            "closed": on.isoformat(),
            "totals": {k: sum(int(e.get("points", 0)) for e in v) for k, v in d["log"].items()},
            "completed": [{"name": c["name"], "by": c["done_by"],
                           "points": c.get("points", 0)} for c in done],
        })
        d["history"] = d["history"][-52:]          # keep a year
    # one-off ad-hoc chores that got done just disappear; they don't repost
    d["chores"] = [c for c in d["chores"]
                   if not (c.get("frequency") == "once" and c.get("done_by"))]
    for c in d["chores"]:
        c["done_by"] = None
        c.pop("rejected", None)
        c["queued_for"] = "na"       # queues start fresh each week
    d["log"] = {"ian": [], "evan": []}
    d["week_start"] = _monday(on).isoformat()
    _repost(d, on)
    return len(done)


def _roll_daily(d, on=None):
    """Daily chores reopen every morning; the points already banked stay banked."""
    on = on or _today()
    n = 0
    for c in d["chores"]:
        if c.get("frequency") != "daily" or not c.get("done_by"):
            continue
        done_on = _parse_date(c.get("done_on") or "") or _parse_date(d.get("week_start") or "")
        if done_on and done_on < on:
            c["last_done"] = done_on.isoformat()
            c["done_by"] = None
            c.pop("rejected", None)
            c["posted"] = True
            n += 1
    return n


def _maybe_roll(d):
    """Auto-advance on Monday, reopen dailies each morning, and run the
    duplicate-tidy pass; catches up if the box was off."""
    today = _today()
    this_monday = _monday(today)
    ws = _parse_date(d.get("week_start") or "")
    changed = False
    if ws is None or ws < this_monday:
        _roll_week(d, today)
        changed = True
    elif _roll_daily(d) > 0:
        changed = True
    if _dedupe(d):
        _repost(d, today)
        changed = True
    return changed


# ---------------------------------------------------------------- sync sources
async def _from_cozi():
    """{norm_name: chore-ish} from the two Cozi lists, plus the list ids we found.
    Cozi item text can't carry a frequency, so those entries leave it as None and
    the reconcile step keeps whatever the sheet (or an earlier add) already set."""
    out, list_ids, item_ids, raw_text, guides = {}, {}, {}, {}, {}
    if not COZI_ENABLED:
        return out, list_ids, item_ids, raw_text, guides, ""
    if not cozi_client or not logged_in:
        return out, list_ids, item_ids, raw_text, guides, "Cozi not connected"
    lists = await cozi_client.get_lists()
    for l in (lists or []):
        kind = COZI_LISTS.get((l.get("title") or "").strip().lower())
        if not kind:
            continue
        list_ids[kind] = l.get("listId") or l.get("list_id")
        for it in (l.get("items") or []):
            if it.get("itemType") == "header":
                continue
            if (it.get("text") or "").lstrip().startswith(NOTE_PREFIX):
                guides[kind] = True           # the format guide, not a chore
                continue
            name, pts, who, freq_tok, desc = _parse_item(it.get("text"))
            if not name:
                continue
            key = _norm(name)
            out[key] = {"name": name, "points": pts, "description": desc,
                        "kind": kind, "source": "cozi", "frequency": freq_tok,
                        "assigned_to": who}
            item_ids[key] = (list_ids[kind], it.get("itemId") or it.get("item_id"))
            raw_text[key] = (it.get("text") or "").strip()
    return out, list_ids, item_ids, raw_text, guides, ""


async def _from_sheet(url):
    """{norm_name: chore-ish} from a Google Sheet published as CSV."""
    out = {}
    if not url:
        return out, ""
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return out, "sheet HTTP %d" % r.status
                body = await r.text()
    except Exception as ex:
        return out, "sheet fetch failed: %s" % str(ex)[:80]

    try:
        body = body.lstrip("﻿")                # Google prepends a BOM
        rows = list(csv.DictReader(io.StringIO(body)))
    except Exception as ex:
        return out, "sheet parse failed: %s" % str(ex)[:80]
    if not rows:
        return out, ""

    cols = _map_columns(rows[0].keys())
    if "name" not in cols:
        return out, "sheet: no chore-name column found"

    def val(row, field):
        return (row.get(cols[field]) or "").strip() if field in cols else ""

    for row in rows:
        name = val(row, "name")
        if not name:
            continue
        try:
            pts = int(float(val(row, "points") or 0))
        except ValueError:
            pts = 0
        kind = "optional" if val(row, "kind").lower().startswith("o") else "required"
        out[_norm(name)] = {"name": name, "points": pts,
                            "description": val(row, "description"),
                            "kind": kind, "source": "sheet",
                            "frequency": _freq(val(row, "frequency")),
                            "assigned_to": _person(val(row, "assigned"))}
    return out, ""


def _map_columns(fieldnames):
    """Map whatever the family typed as headers onto our fields.

    Real headers in use are things like "Chore Title", "Chore Steps/Details" and
    "Type (Required / Optional)", so match on substrings rather than exact names.
    Order matters: "chore" appears in BOTH the title and the steps column, so the
    more specific patterns get to claim their column first.
    """
    heads = [h for h in fieldnames if h]
    cols, taken = {}, set()

    def claim(field, *needles):
        for h in heads:
            if h in taken:
                continue
            lh = h.strip().lower()
            if any(n in lh for n in needles):
                cols[field] = h
                taken.add(h)
                return

    claim("assigned", "assign", "who", "owner", "person", "whose")
    claim("frequency", "frequen", "how often", "repeat", "cadence", "schedule")
    claim("description", "detail", "step", "descri", "how", "note", "instruction")
    claim("points", "point", "pts", "value", "worth")
    claim("kind", "type", "kind", "required", "optional", "categ")
    claim("name", "chore", "task", "job", "title", "name")
    return cols


_sync_gate = asyncio.Lock()


async def _sync_chores():
    """Serialised so concurrent callers can't each push the same new chore."""
    async with _sync_gate:
        return await _do_sync()


async def _do_sync():
    """Reconcile local chores with Cozi + sheet. Never drops a claimed chore."""
    cozi_items, list_ids, cozi_item_ids, cozi_raw, have_guide, cozi_err = await _from_cozi()
    d0 = _chores_read()
    sheet_items, sheet_err = await _from_sheet(d0.get("sheet_url") or "")

    # Cozi wins on the text fields (it's the shared list the family edits), but the
    # sheet keeps ownership of frequency since Cozi can't express one.
    sheet_ok = bool(sheet_items) and not sheet_err
    # Cozi first, then let the sheet overwrite. Cozi can't express a frequency and
    # its required/optional is just which list an item sits in, so for anything the
    # sheet lists the sheet's points/type/frequency/description are the truth.
    ext = dict(cozi_items)
    for k, v in sheet_items.items():
        ext[k] = v

    push = []
    async with _chores_lock:
        d = _chores_read()
        local = {}
        for c in d["chores"]:
            local.setdefault(_norm(c["name"]), c)

        for key, e in ext.items():
            c = local.get(key)
            if c:
                c.update({"name": e["name"], "points": e["points"],
                          "description": _merge_desc(c.get("description"), e["description"]),
                          "kind": e["kind"],
                          "assigned_to": e.get("assigned_to", c.get("assigned_to", "na"))})
                if e.get("frequency"):
                    c["frequency"] = e["frequency"]
                if key in sheet_items:
                    c["from_sheet"] = True
                if c.get("source") == "dashboard":
                    c["source"] = e["source"]
            else:
                cid = d.get("next_id", 1)
                d["next_id"] = cid + 1
                nc = {"id": cid, "name": e["name"], "points": e["points"],
                      "description": e["description"], "kind": e["kind"],
                      "frequency": e.get("frequency") or DEFAULT_FREQ,
                      "last_done": None, "posted": True,
                      "from_sheet": key in sheet_items,
                      "assigned_to": e.get("assigned_to", "na"),
                      "done_by": None, "source": e["source"]}
                d["chores"].append(nc)
                local[key] = nc

        # One-time: everything currently held originated from the seeded catalog that
        # the spreadsheet now mirrors, so hand ownership of it to the sheet.
        if d.get("schema", 0) < 2:
            if sheet_ok:
                for c in d["chores"]:
                    c["from_sheet"] = True
                d["schema"] = 2

        move_in_cozi, edit_in_cozi, add_to_cozi = [], [], []
        for kind, lid in list_ids.items():
            if lid and not have_guide.get(kind):
                add_to_cozi.append((lid, COZI_GUIDE))
        if sheet_ok and list_ids:
            for key, e in sheet_items.items():
                want_text = _fmt_item({"name": e["name"], "points": e["points"],
                                       "description": e["description"],
                                       "assigned_to": e.get("assigned_to", "na"),
                                       "frequency": e.get("frequency") or DEFAULT_FREQ})
                want_list = list_ids.get(e.get("kind", "required"))
                if not want_list:
                    continue
                if key not in cozi_item_ids:
                    add_to_cozi.append((want_list, want_text))       # new in the sheet
                    continue
                cur_list, item_id = cozi_item_ids[key]
                if cur_list != want_list:
                    move_in_cozi.append((cur_list, item_id, want_list, want_text))
                elif cozi_raw.get(key, "") != want_text:
                    edit_in_cozi.append((cur_list, item_id, want_text))

        drop_from_cozi = []
        if not cozi_err:
            keep = []
            for c in d["chores"]:
                key = _norm(c["name"])
                # a chore the sheet introduced and has since dropped (a rename counts)
                # goes away even though Cozi still mirrors the old name
                sheet_dropped = sheet_ok and c.get("from_sheet") and key not in sheet_items
                gone = c.get("source") in ("cozi", "sheet") and key not in ext
                if (gone or sheet_dropped) and not c.get("done_by"):
                    if key in cozi_item_ids:
                        drop_from_cozi.append(cozi_item_ids[key])
                    continue
                keep.append(c)
            d["chores"] = keep

        # Only push what Cozi doesn't already have. Without this the same chore is
        # re-pushed on every sync until the read-back flips its source, which piles
        # up hundreds of duplicate list items.
        for c in d["chores"]:
            if (c.get("source") == "dashboard"
                    and list_ids.get(c.get("kind", "required"))
                    and _norm(c["name"]) not in cozi_items):
                push.append((list_ids[c["kind"]], _fmt_item(c)))

        rolled = _maybe_roll(d)
        if not rolled:
            _repost(d)
        d["last_sync"] = datetime.datetime.now().isoformat(timespec="seconds")
        d["sync_error"] = cozi_err or sheet_err or ""
        d["sync_note"] = "cozi:%d sheet:%d" % (len(cozi_items), len(sheet_items))
        _chores_write(d)

    for list_id, item_id in drop_from_cozi:
        try:
            await cozi_client.remove_items(list_id, [item_id])
        except Exception:
            pass
    for old_list, item_id, new_list, text in move_in_cozi:
        try:
            await cozi_client.remove_items(old_list, [item_id])
            await cozi_client.add_item(new_list, text, 0)
        except Exception:
            pass
    for list_id, item_id, text in edit_in_cozi:
        try:
            await cozi_client.edit_item(list_id, item_id, text)
        except Exception:
            pass
    for list_id, text in add_to_cozi:
        try:
            await cozi_client.add_item(list_id, text, 0)
        except Exception:
            pass
    for list_id, text in push:
        try:
            await cozi_client.add_item(list_id, text, 0)
        except Exception:
            pass
    return {"cozi": len(cozi_items), "sheet": len(sheet_items), "pushed": len(push),
            "removed": len(drop_from_cozi), "moved": len(move_in_cozi),
            "edited": len(edit_in_cozi), "added_to_cozi": len(add_to_cozi), "rolled": rolled,
            "error": cozi_err or sheet_err or ""}


async def _sync_loop():
    while True:
        try:
            await _sync_chores()
        except Exception as ex:
            print("chores sync failed:", ex)
        await asyncio.sleep(SYNC_EVERY)


class ChoreAdd(BaseModel):
    name: str
    points: int = 0
    description: str = ""
    kind: str = "required"
    frequency: str = DEFAULT_FREQ
    assigned_to: str = "na"

class ChoreEdit(BaseModel):
    id: int
    name: str | None = None
    points: int | None = None
    description: str | None = None
    kind: str | None = None
    frequency: str | None = None
    assigned_to: str | None = None

class ChoreId(BaseModel):
    id: int

class ChoreClaim(BaseModel):
    id: int
    kid: str

class ChoreTarget(BaseModel):
    target: int

class ChoreReject(BaseModel):
    id: int
    comment: str = ""

class SheetUrl(BaseModel):
    url: str


def _decorate(c):
    out = dict(c)
    dd = _due_date(c)
    out["next_due"] = dd.isoformat() if dd else None
    out["days_until_due"] = max(0, (dd - _today()).days) if dd else 0
    return out


@app.get("/chores")
async def chores_get():
    d = _chores_read()
    totals = {k: sum(int(e.get("points", 0)) for e in v) for k, v in d["log"].items()}
    chores = [_decorate(c) for c in d["chores"]]
    out = {"target": d.get("target", 100), "chores": chores, "log": d["log"],
           "totals": totals, "week_start": d.get("week_start"),
           "sheet_url": d.get("sheet_url", ""), "last_sync": d.get("last_sync"),
           "sync_error": d.get("sync_error", ""), "sync_note": d.get("sync_note", ""),
           "today": _today().isoformat(),
           "frequencies": ["daily", "weekly", "bi-weekly", "monthly"],
           "people": PEOPLE, "people_labels": PEOPLE_LABEL,
           "cozi_enabled": COZI_ENABLED}
    out.update(_gate(d["chores"]))
    return out


@app.post("/chores/add")
async def chores_add(req: ChoreAdd):
    async with _chores_lock:
        d = _chores_read()
        cid = d.get("next_id", 1)
        d["chores"].append({"id": cid, "name": req.name.strip(), "points": int(req.points),
                            "description": (req.description or "").strip(),
                            "kind": "optional" if req.kind == "optional" else "required",
                            "frequency": _freq(req.frequency) or DEFAULT_FREQ,
                            "assigned_to": _person(req.assigned_to),
                            "last_done": None, "posted": True,
                            "done_by": None, "source": "dashboard"})
        d["next_id"] = cid + 1
        _chores_write(d)
    return {"status": "ok", "id": cid}


class ChoreAdHoc(BaseModel):
    name: str
    description: str = ""
    points: int = 0
    kid: str                     # ian or evan


@app.post("/chores/adhoc")
async def chores_adhoc(req: ChoreAdHoc):
    """Parent adds a one-off chore straight into a kid's queue and texts them.
    It's a normal chore (claimable for points) but frequency 'once' so the
    weekly roll drops it instead of reposting."""
    kid = req.kid.lower()
    if kid not in ("ian", "evan"):
        raise HTTPException(status_code=400, detail="kid must be ian or evan")
    async with _chores_lock:
        d = _chores_read()
        cid = d.get("next_id", 1)
        c = {"id": cid, "name": req.name.strip(),
             "points": int(req.points), "description": (req.description or "").strip(),
             "kind": "required", "frequency": "once", "assigned_to": kid,
             "queued_for": kid, "last_done": None, "posted": True,
             "done_by": None, "source": "adhoc"}
        d["chores"].append(c)
        d["next_id"] = cid + 1
        _chores_write(d)
    what = c["description"] or c["name"]
    body = ("Times are tough, cupcake. We need you to do something a little "
            "different today — we need you to %s (%s pts)." % (what, c["points"]))
    sent = await _send_sms(kid, body)
    return {"status": "ok", "id": cid, "sms": sent}


@app.post("/chores/edit")
async def chores_edit(req: ChoreEdit):
    async with _chores_lock:
        d = _chores_read()
        for c in d["chores"]:
            if c["id"] == req.id:
                if req.name is not None: c["name"] = req.name.strip()
                if req.points is not None: c["points"] = int(req.points)
                if req.description is not None: c["description"] = req.description.strip()
                if req.kind is not None:
                    c["kind"] = "optional" if req.kind == "optional" else "required"
                if req.frequency is not None:
                    c["frequency"] = _freq(req.frequency) or DEFAULT_FREQ
                if req.assigned_to is not None:
                    c["assigned_to"] = _person(req.assigned_to)
        _repost(d)
        _chores_write(d)
    return {"status": "ok"}


@app.post("/chores/delete")
async def chores_delete(req: ChoreId):
    async with _chores_lock:
        d = _chores_read()
        d["chores"] = [c for c in d["chores"] if c["id"] != req.id]
        _chores_write(d)
    return {"status": "ok"}


# ====================== FLOORPLAN (furniture layout store) ======================
FLOORPLAN_DB = "/data/floorplan.json"


@app.get("/floorplan")
async def floorplan_get():
    try:
        with open(FLOORPLAN_DB) as f:
            return json.load(f)
    except Exception:
        return {}


class FloorplanBody(BaseModel):
    floors: dict


@app.post("/floorplan")
async def floorplan_set(req: FloorplanBody):
    with open(FLOORPLAN_DB, "w") as f:
        json.dump(req.floors, f)
    return {"status": "ok"}


@app.post("/chores/queue")
async def chores_queue(req: ChoreClaim):
    """Kid drops a chore into their own work queue (or 'na' to release it).
    No SMS, no code — completing it later is what needs the kid's code."""
    kid = req.kid.lower()
    if kid not in ("ian", "evan", "na"):
        raise HTTPException(status_code=400, detail="kid must be ian, evan or na")
    async with _chores_lock:
        d = _chores_read()
        c = next((x for x in d["chores"] if x["id"] == req.id), None)
        if not c:
            raise HTTPException(status_code=404, detail="chore not found")
        if c.get("done_by"):
            raise HTTPException(status_code=409, detail="already completed")
        c["queued_for"] = kid
        _chores_write(d)
    return {"status": "ok", "queued_for": kid}


class SmsTest(BaseModel):
    who: str = "dad"


@app.post("/sms/test")
async def sms_test(req: SmsTest):
    """Send a test text to one family member (ian/evan/mom/dad)."""
    who = req.who.lower()
    if who not in SMS["phones"]:
        raise HTTPException(status_code=400, detail="who must be ian/evan/mom/dad")
    sent = await _send_sms(who, "El Dashboardio test text — texting works! (%s)"
                                % _stamp(_now_local()))
    return {"sent": sent, "who": who, "gateway": SMS["gateway"],
            "sms_enabled": bool(SMS["user"] and SMS["pw"]),
            "phone_set": bool(SMS["phones"].get(who))}


class SmsSend(BaseModel):
    to: str                       # 10-digit number, or ian/evan/mom/dad
    body: str
    gateway: str | None = None    # override vtext.com for one send


@app.post("/sms/send")
async def sms_send(req: SmsSend):
    """Send arbitrary text to any number through the carrier email gateway.

    This is the generic hook anything on the LAN can POST to (reminders,
    scheduled alerts, scripts) instead of holding the Gmail app password."""
    if not (SMS["user"] and SMS["pw"]):
        raise HTTPException(status_code=503, detail="texting not configured")
    num = _sms_number(req.to)
    if not num:
        raise HTTPException(status_code=400,
                            detail="to must be a 10-digit number or ian/evan/mom/dad")
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is empty")
    sent = await _send_sms_raw(num, body, req.gateway)
    if not sent:
        raise HTTPException(status_code=502, detail="SMTP send failed")
    return {"sent": True, "to": num, "gateway": (req.gateway or SMS["gateway"]),
            "chars": len(body)}


@app.post("/chores/assign")
async def chores_assign(req: ChoreClaim):
    """Parent assigns a chore to a kid and texts them to go do it."""
    kid = req.kid.lower()
    if kid not in ("ian", "evan"):
        raise HTTPException(status_code=400, detail="kid must be ian or evan")
    async with _chores_lock:
        d = _chores_read()
        c = next((x for x in d["chores"] if x["id"] == req.id), None)
        if not c:
            raise HTTPException(status_code=404, detail="chore not found")
        c["assigned_to"] = kid
        c["queued_for"] = kid        # parent assignment lands in the kid's queue
        _chores_write(d)
    body = "You need to do this chore now: " + c["name"]
    if c.get("description"):
        body += " — " + c["description"]
    body += " (%s pts)" % c.get("points", 0)
    sent = await _send_sms(kid, body)
    return {"status": "ok", "sms": sent}


@app.post("/chores/claim")
async def chores_claim(req: ChoreClaim):
    kid = req.kid.lower()
    async with _chores_lock:
        d = _chores_read()
        if kid not in d["log"]:
            d["log"][kid] = []
        target = next((c for c in d["chores"] if c["id"] == req.id), None)
        if not target:
            raise HTTPException(status_code=404, detail="chore not found")
        if target.get("done_by"):
            raise HTTPException(status_code=409, detail="already claimed")
        if not target.get("posted", True):
            dd = _due_date(target)
            raise HTTPException(status_code=425,
                                detail="Not due yet — back on the board %s"
                                       % (dd.strftime("%b %-d") if dd else "soon"))
        gate = _gate(d["chores"])
        if target.get("kind", "required") == "optional" and not gate["optional_unlocked"]:
            raise HTTPException(status_code=423,
                                detail="Finish the %d required chore(s) first"
                                       % gate["required_left"])
        target["done_by"] = kid
        target["done_on"] = _today().isoformat()
        target.pop("rejected", None)          # redone -> clear the parent's note
        d["log"][kid].append({"chore_id": target["id"], "name": target["name"],
                              "points": int(target.get("points", 0))})
        _chores_write(d)
    # let Mom know, without holding up the kid's tap
    kid_label = PEOPLE_LABEL.get(kid, kid.title())
    asyncio.create_task(_send_sms(
        "mom", "%s just completed chore [%s] at %s"
               % (kid_label, target["name"], _stamp(_now_local()))))
    return {"status": "ok"}


@app.post("/chores/unclaim")
async def chores_unclaim(req: ChoreId):
    async with _chores_lock:
        d = _chores_read()
        for c in d["chores"]:
            if c["id"] == req.id and c.get("done_by"):
                kid = c["done_by"]
                c["done_by"] = None
                d["log"][kid] = [e for e in d["log"].get(kid, []) if e.get("chore_id") != req.id]
        _chores_write(d)
    return {"status": "ok"}


@app.post("/chores/reject")
async def chores_reject(req: ChoreReject):
    """Parent sends a claimed chore back: points come off that kid's total, the
    chore returns to the board, and the comment rides along so the kid sees why."""
    async with _chores_lock:
        d = _chores_read()
        c = next((x for x in d["chores"] if x["id"] == req.id), None)
        if not c:
            raise HTTPException(status_code=404, detail="chore not found")
        kid = c.get("done_by")
        if not kid:
            raise HTTPException(status_code=409, detail="that chore isn't claimed")
        # drop it from the kid's log -> the points go with it
        d["log"][kid] = [e for e in d["log"].get(kid, []) if e.get("chore_id") != req.id]
        c["done_by"] = None
        c["posted"] = True
        c["queued_for"] = kid                   # back into the offender's queue
        c["rejected"] = {
            "kid": kid,
            "comment": (req.comment or "").strip(),
            "at": datetime.datetime.now().isoformat(timespec="minutes"),
        }
        d.setdefault("rejections", []).append({
            "chore": c["name"], "kid": kid, "points": int(c.get("points", 0)),
            "comment": (req.comment or "").strip(),
            "at": datetime.datetime.now().isoformat(timespec="minutes"),
        })
        d["rejections"] = d["rejections"][-100:]
        _chores_write(d)
    return {"status": "ok", "kid": kid, "points_removed": int(c.get("points", 0))}


@app.post("/chores/clear_rejection")
async def chores_clear_rejection(req: ChoreId):
    async with _chores_lock:
        d = _chores_read()
        for c in d["chores"]:
            if c["id"] == req.id:
                c.pop("rejected", None)
        _chores_write(d)
    return {"status": "ok"}


@app.post("/chores/target")
async def chores_target(req: ChoreTarget):
    async with _chores_lock:
        d = _chores_read()
        d["target"] = int(req.target)
        _chores_write(d)
    return {"status": "ok"}


@app.post("/chores/sheeturl")
async def chores_sheeturl(req: SheetUrl):
    async with _chores_lock:
        d = _chores_read()
        d["sheet_url"] = (req.url or "").strip()
        _chores_write(d)
    return await _sync_chores()


@app.post("/chores/sync")
async def chores_sync():
    return await _sync_chores()


@app.get("/chores/history")
async def chores_history():
    d = _chores_read()
    return {"history": d.get("history", []), "rejections": d.get("rejections", [])}


@app.post("/chores/newweek")
async def chores_newweek():
    async with _chores_lock:
        d = _chores_read()
        n = _roll_week(d)
        _chores_write(d)
    return {"status": "ok", "stamped": n, "week_start": d.get("week_start")}


def _reset_all(d, on=None):
    """Wipe ALL tracking back to week one: no history, no banked totals, no
    streaks, empty logs, every chore reopened and available. Unlike _roll_week
    (which banks the closing week into history), this erases history entirely."""
    on = on or _today()
    # completed one-off ad-hoc chores just disappear, same as a weekly roll
    d["chores"] = [c for c in d["chores"]
                   if not (c.get("frequency") == "once" and c.get("done_by"))]
    for c in d["chores"]:
        c["done_by"] = None
        c.pop("rejected", None)
        c["queued_for"] = "na"
        c["last_done"] = None
        c["posted"] = True          # everything on the board and claimable
    d["log"] = {"ian": [], "evan": []}
    d["history"] = []
    d["rejections"] = []
    d["week_start"] = _monday(on).isoformat()
    return len(d["chores"])


@app.post("/chores/reset")
async def chores_reset():
    """FULL reset to week one — erases history/totals/streaks/queues/logs and
    reopens every chore. (Use /chores/newweek for a normal week roll that keeps
    the history and the Grand Champion running totals.)"""
    async with _chores_lock:
        d = _chores_read()
        n = _reset_all(d)
        _chores_write(d)
    return {"status": "ok", "reset": n, "week_start": d.get("week_start")}


# ========================================================== voice intents
# One grammar, many front doors. A Google-Assistant relay, Home Assistant
# Assist, the dashboard mic and plain curl all POST the same raw sentence to
# /voice/intent; everything about *understanding* it and writing it into Cozi
# lives here, so swapping the front door never touches this file.

VOICE_DB = "/data/voice.json"
VOICE_LOG_MAX = 60

# Spoken list name -> the list we actually keep it on. The Kroger list doubles
# as the Aldi list, Home Depot doubles as Lowe's (same trip, same list).
LIST_ALIASES = {
    "kroger": "kroger", "krogers": "kroger", "aldi": "kroger", "aldis": "kroger",
    "grocery": "kroger", "groceries": "kroger", "food": "kroger",
    "shopping": "kroger", "supermarket": "kroger", "store": "kroger",
    "costco": "costco", "warehouse": "costco", "bulk": "costco",
    "home depot": "home depot", "homedepot": "home depot", "depot": "home depot",
    "lowes": "home depot", "lowe's": "home depot", "hardware": "home depot",
    "menards": "home depot", "ace": "home depot",
}

_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
             "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3,
             "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
             "sunday": 6, "sun": 6}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
           "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7,
           "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
           "october": 10, "oct": 10, "november": 11, "nov": 11,
           "december": 12, "dec": 12}

# wake words and politeness the relay may pass through verbatim
_VOICE_STRIP = re.compile(
    r"^(?:hey|ok|okay)\s+(?:google|home|jarvis|dashboardio)\b[\s,]*"
    r"|^(?:please|can you|could you|would you|i want you to|i need you to)\s+",
    re.I)

_LIST_RE = re.compile(
    r"^(?:add|put|place|stick|throw|toss)\s+(?P<items>.+?)\s+"
    r"(?:to|on|onto|in|into)\s+(?:the\s+|my\s+|our\s+)?(?P<list>.+?)"
    r"(?:\s+list)?\s*$", re.I)

_CAL_RE = re.compile(
    r"^(?:create|add|make|schedule|book|set\s+up|put|enter)\s+"
    r"(?:an?\s+|the\s+)?(?:new\s+)?"
    r"(?P<kind>appointment|event|calendar\s*(?:event|entry|item)?|meeting)\b"
    r"(?:\s+(?:in|on|to|onto)\s+(?:the\s+|our\s+|my\s+)?"
    r"(?:cozi(?:\s+calendar)?|calendar))?"
    r"\s*(?P<rest>.*)$", re.I)

_TITLE_RE = re.compile(r"\b(?:called|titled|named|for the)\s+(?P<t>.+?)\s*$", re.I)
_DESC_RE = re.compile(
    r"\b(?:description|desc|notes?|note that|details?|about|remind(?:er)?(?:\s+to)?)"
    r"\s+(?P<d>.+?)\s*$", re.I)


def _voice_read():
    try:
        with open(VOICE_DB) as f:
            return json.load(f)
    except Exception:
        return {"log": []}


def _voice_write(d):
    d["log"] = d.get("log", [])[-VOICE_LOG_MAX:]
    with open(VOICE_DB, "w") as f:
        json.dump(d, f)


def _voice_log(entry):
    d = _voice_read()
    d.setdefault("log", []).append(entry)
    _voice_write(d)


def _cut(text, m):
    """Drop a matched span out of the sentence, leaving tidy spacing."""
    return re.sub(r"\s{2,}", " ", (text[:m.start()] + " " + text[m.end():])).strip()


def _clean_tail(s):
    """Trim the connective words a stripped-out date/time leaves behind."""
    s = re.sub(r"\s{2,}", " ", (s or "")).strip(" ,.;")
    s = re.sub(r"^(?:for|on|at|to|of|about|that|is|the)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+(?:for|on|at|to|of|and|the)$", "", s, flags=re.I)
    return s.strip(" ,.;")


def _v_date(text):
    """First date phrase in the sentence -> (date, sentence without it).
    Falls back to today. Deliberately forgiving: speech-to-text mangles
    dates and a wrong-but-close day beats a rejected sentence."""
    today = _now_local().date()

    m = re.search(r"\b(today|tonight|tomorrow|day after tomorrow)\b", text, re.I)
    if m:
        word = m.group(1).lower()
        off = {"today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2}[word]
        return today + datetime.timedelta(days=off), _cut(text, m)

    m = re.search(r"\b(?:on\s+)?(this|next|coming)?\s*("
                  + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b",
                  text, re.I)
    if m:
        want = _WEEKDAYS[m.group(2).lower()]
        ahead = (want - today.weekday()) % 7 or 7        # always the coming one
        d = today + datetime.timedelta(days=ahead)
        if (m.group(1) or "").lower() == "next" and d.isocalendar()[1] == today.isocalendar()[1]:
            d += datetime.timedelta(days=7)              # "next Monday" != this week
        return d, _cut(text, m)

    m = re.search(r"\b(?:on\s+)?(" + "|".join(sorted(_MONTHS, key=len, reverse=True))
                  + r")\w*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, re.I)
    if m:
        mo, day = _MONTHS[m.group(1).lower()], int(m.group(2))
        try:
            d = datetime.date(today.year, mo, day)
        except ValueError:
            return today, _cut(text, m)
        if (today - d).days > 60:                        # said in December about January
            d = datetime.date(today.year + 1, mo, day)
        return d, _cut(text, m)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        yr = int(m.group(3) or today.year)
        if yr < 100:
            yr += 2000
        try:
            return datetime.date(yr, mo, day), _cut(text, m)
        except ValueError:
            return today, _cut(text, m)

    m = re.search(r"\bin\s+(\d{1,2})\s+days?\b", text, re.I)
    if m:
        return today + datetime.timedelta(days=int(m.group(1))), _cut(text, m)

    return today, text


def _v_time(text):
    """First clock time -> ('HH:MM', sentence without it), or (None, text).
    A bare hour with no am/pm is read the way a family means it: 1-7 is the
    evening, 8-11 is the morning."""
    m = re.search(r"\b(noon|midday|midnight)\b", text, re.I)
    if m:
        return ("12:00" if m.group(1).lower() != "midnight" else "00:00"), _cut(text, m)

    # A number only counts as a time if something anchors it: an "at", a colon,
    # or an am/pm. Otherwise "5 pounds of flour" becomes 5 o'clock.
    pat = re.compile(r"\b(?P<lead>at|from|around|about)?\s*(?P<h>\d{1,2})"
                     r"(?::(?P<m>\d{2}))?\s*(?P<mark>a\.?m\.?|p\.?m\.?|o'?clock)?\b",
                     re.I)
    for m in pat.finditer(text):
        hh, mm = int(m.group("h")), int(m.group("m") or 0)
        mark = (m.group("mark") or "").lower()
        if hh > 24 or mm > 59:
            continue
        if not (m.group("lead") or m.group("m") or mark):
            continue
        if mark.startswith("p") and hh < 12:
            hh += 12
        elif mark.startswith("a") and hh == 12:
            hh = 0
        elif not mark.startswith(("a", "p")) and 1 <= hh <= 7:
            hh += 12                     # "at 7" on a school night means 7pm
        return "%02d:%02d" % (hh % 24, mm), _cut(text, m)
    return None, text


def _plus_hour(hhmm, hours=1):
    h, m = (int(x) for x in hhmm.split(":"))
    return "%02d:%02d" % ((h + hours) % 24, m)


def _speak_time(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


_persons_cache = {"at": 0.0, "people": []}


async def _cozi_persons(force=False):
    """[{id, name}] for the household, cached — calendar attendees need ids."""
    import time
    if not force and _persons_cache["people"] and time.time() - _persons_cache["at"] < 3600:
        return _persons_cache["people"]
    if not cozi_client or not logged_in:
        return []
    raw = await cozi_client.get_persons()
    people = []
    for p in (raw or []):
        if not isinstance(p, dict):
            continue
        pid = (p.get("accountPersonId") or p.get("personId") or p.get("id")
               or p.get("accountPersonID"))
        name = (p.get("name") or p.get("displayName") or "").strip()
        if pid and name:
            people.append({"id": pid, "name": name})
    _persons_cache.update({"at": time.time(), "people": people})
    return people


def _voice_cfg():
    """Voice settings live in /data/voice.json so they can be changed over the
    API; the add-on options are the fallback defaults."""
    try:
        with open(VOICE_DB) as f:
            return json.load(f).get("cfg") or {}
    except Exception:
        return {}


def _voice_aliases():
    """'mom=Jane, dad=John' — what a person is called out loud vs. the name on
    the Cozi household."""
    cfg = _voice_cfg().get("aliases")
    if isinstance(cfg, dict):
        return {re.sub(r"[^a-z]", "", k.lower()): re.sub(r"[^a-z]", "", str(v).lower())
                for k, v in cfg.items()}
    try:
        with open("/data/options.json") as f:
            raw = json.load(f).get("voice_aliases") or ""
    except Exception:
        raw = ""
    out = {}
    for pair in raw.split(","):
        if "=" in pair:
            said, real = pair.split("=", 1)
            out[re.sub(r"[^a-z]", "", said.lower())] = re.sub(r"[^a-z]", "", real.lower())
    return out


def _match_person(word, people):
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return None
    w = _voice_aliases().get(w, w)
    for p in people:
        n = re.sub(r"[^a-z]", "", p["name"].lower())
        if n == w or n.startswith(w) or w.startswith(n):
            return p
    return None


def _pick_list(spoken, lists):
    """Spoken list name -> the real Cozi list, through the alias table."""
    want = re.sub(r"\s+list$", "", (spoken or "").strip().lower()).strip(" .,'")
    if not want:
        return None
    titles = [(l, (l.get("title") or "").strip().lower()) for l in lists]
    for l, t in titles:                                  # said the title exactly
        if t == want:
            return l
    canon = LIST_ALIASES.get(want)
    if not canon:
        for alias, c in LIST_ALIASES.items():            # "the kroger one"
            if alias in want:
                canon = c
                break
    if canon:
        for l, t in titles:
            if canon.split()[0] in t:
                return l
    for l, t in titles:                                  # loose contains, either way
        if want in t or t in want:
            return l
    return None


async def _voice_list_add(items_text, list_name):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Cozi not connected")
    lists = await cozi_client.get_lists()
    target = _pick_list(list_name, lists)
    if not target:
        have = ", ".join((l.get("title") or "") for l in lists if l.get("title"))
        return {"ok": False, "intent": "list",
                "speech": "I couldn't find a list called %s. You have: %s."
                          % (list_name, have),
                "detail": {"asked_for": list_name, "lists": have}}
    parts = [p.strip(" .,") for p in
             re.split(r"\s*(?:,|\band\b|&|\bplus\b)\s*", items_text, flags=re.I)]
    parts = [p for p in parts if p]
    for p in parts:
        await cozi_client.add_item(target.get("listId") or target.get("list_id"), p, 0)
    title = target.get("title") or list_name
    return {"ok": True, "intent": "list",
            "speech": "Added %s to the %s list." % (_and_join(parts), title),
            "detail": {"list": title, "items": parts}}


def _and_join(parts):
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return ", ".join(parts[:-1]) + " and " + parts[-1]


async def _voice_calendar_add(rest):
    """'for evan on monday at 7pm description pick up his laundry' -> Cozi appt."""
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Cozi not connected")
    people = await _cozi_persons()
    text = rest

    notes = ""
    m = _DESC_RE.search(text)
    if m:
        notes = _clean_tail(m.group("d"))
        text = _cut(text, m)

    attendees, who_label = [], ""
    m = re.search(r"\bfor\s+([A-Za-z']+)\b", text, re.I)
    if m:
        p = _match_person(m.group(1), people)
        if p:
            attendees = [p["id"]]
            who_label = p["name"]
            text = _cut(text, m)

    day, text = _v_date(text)
    start, text = _v_time(text)
    end = _plus_hour(start) if start else None

    subject = ""
    m = _TITLE_RE.search(text)
    if m:
        subject = _clean_tail(m.group("t"))
    else:
        subject = _clean_tail(text)
    if not subject and notes:
        subject = notes[:60]           # a described-only event reads better titled
    if not subject:
        subject = ("%s's appointment" % who_label) if who_label else "Appointment"

    await cozi_client.add_appointment(
        day.year, day.month, day.day,
        start or "00:00", end or "00:00",
        0 if start else 1,                 # no time given -> all-day
        attendees, "", notes, subject)

    when = "%s, %s %d" % (day.strftime("%A"), day.strftime("%B"), day.day)
    speech = "Put %s on the Cozi calendar for %s%s%s." % (
        subject, when,
        (" at " + _speak_time(start)) if start else "",
        (" for " + who_label) if who_label else "")
    return {"ok": True, "intent": "calendar", "speech": speech,
            "detail": {"subject": subject, "date": day.isoformat(), "start": start,
                       "end": end, "who": who_label, "notes": notes}}


async def _voice_dispatch(text):
    said = _VOICE_STRIP.sub("", (text or "").strip()).strip(" .!?")
    if not said:
        raise HTTPException(status_code=400, detail="nothing to do")

    m = _CAL_RE.match(said)               # calendar first: "on Monday" looks like a list
    if m:
        return await _voice_calendar_add(m.group("rest"))

    m = _LIST_RE.match(said)
    if m:
        return await _voice_list_add(m.group("items"), m.group("list"))

    return {"ok": False, "intent": "unknown",
            "speech": "I didn't catch that. Try 'add butter to the Kroger list' or "
                      "'create an appointment for Evan Monday at 7 pm'.",
            "detail": {"heard": said}}


class VoiceIntent(BaseModel):
    text: str
    source: str = "api"


@app.post("/voice/intent")
async def voice_intent(req: VoiceIntent):
    """The single front door. Returns `speech` for the relay to read back."""
    try:
        res = await _voice_dispatch(req.text)
    except HTTPException:
        raise
    except Exception as e:
        res = {"ok": False, "intent": "error", "speech": "Sorry, that didn't save.",
               "detail": {"error": str(e)}}
    _voice_log({"at": _now_local().isoformat(timespec="seconds"),
                "text": req.text, "source": req.source, "ok": res.get("ok"),
                "intent": res.get("intent"), "speech": res.get("speech"),
                "detail": res.get("detail")})
    return res


@app.post("/voice/parse")
async def voice_parse(req: VoiceIntent):
    """Dry run — shows how a sentence is understood without writing to Cozi."""
    said = _VOICE_STRIP.sub("", (req.text or "").strip()).strip(" .!?")
    m = _CAL_RE.match(said)
    if m:
        rest = m.group("rest")
        notes = ""
        d = _DESC_RE.search(rest)
        if d:
            notes, rest = _clean_tail(d.group("d")), _cut(rest, d)
        who = ""
        p = re.search(r"\bfor\s+([A-Za-z']+)\b", rest, re.I)
        if p:
            hit = _match_person(p.group(1), await _cozi_persons())
            if hit:
                who, rest = hit["name"], _cut(rest, p)
        day, rest = _v_date(rest)
        start, rest = _v_time(rest)
        t = _TITLE_RE.search(rest)
        subject = _clean_tail(t.group("t")) if t else _clean_tail(rest)
        return {"intent": "calendar", "subject": subject, "date": day.isoformat(),
                "start": start, "who": who, "notes": notes}
    m = _LIST_RE.match(said)
    if m:
        lists = await cozi_client.get_lists() if (cozi_client and logged_in) else []
        target = _pick_list(m.group("list"), lists)
        return {"intent": "list", "items": m.group("items"),
                "said_list": m.group("list"),
                "list": (target or {}).get("title")}
    return {"intent": "unknown", "heard": said}


class VoiceConfig(BaseModel):
    aliases: dict | None = None
    mirror_calendars: list | None = None
    mirror_days: int | None = None
    mirror_prefix: str | None = None
    reseed: bool = False


@app.post("/voice/config")
async def voice_config_set(req: VoiceConfig):
    """Change voice settings without touching the add-on's option store."""
    d = _voice_read()
    cfg = d.setdefault("cfg", {})
    for k in ("aliases", "mirror_calendars", "mirror_days", "mirror_prefix"):
        v = getattr(req, k)
        if v is not None:
            cfg[k] = v
    if req.reseed:
        d["mirrored"] = {}
        d["mirror_seeded"] = False       # next sweep re-reads without copying
    _voice_write(d)
    _load_mirror_options()
    return {"status": "ok", "cfg": cfg, "watching": MIRROR["cals"]}


@app.get("/voice/config")
async def voice_config_get():
    return {"cfg": _voice_cfg(), "aliases_in_effect": _voice_aliases(),
            "watching": MIRROR["cals"], "days": MIRROR["days"],
            "prefix": MIRROR["prefix"]}


@app.get("/voice/log")
async def voice_log():
    return _voice_read()


# ------------------------------------------------- Google Calendar -> Cozi
# Google's own assistant already runs a proper booking dialog ("what's the
# title?", "what time?") and drops the result on the account's calendar. Home
# Assistant already reads that calendar, so the cheapest voice front door is to
# let Google take the sentence and mirror whatever lands into Cozi.
#
# First pass only *seeds* — every event already on the calendar is recorded as
# seen and left alone, so turning this on never backfills months of history
# into the family's Cozi. Only events that appear afterwards get mirrored.

MIRROR = {"cals": [], "days": 60, "prefix": "", "every": 300}
SUPERVISOR = "http://supervisor/core/api"
SUPERVISOR_IP = "http://172.30.32.2/core/api"       # host_network can't resolve the name


def _load_mirror_options():
    try:
        with open("/data/options.json") as f:
            o = json.load(f)
    except Exception:
        return
    cfg = _voice_cfg()
    cals = cfg.get("mirror_calendars")
    if cals is None:
        cals = [c.strip() for c in (o.get("mirror_calendars") or "").split(",") if c.strip()]
    MIRROR["cals"] = [c for c in cals if c]
    MIRROR["days"] = int(cfg.get("mirror_days") or o.get("mirror_days") or 60)
    MIRROR["prefix"] = str(cfg.get("mirror_prefix")
                           if cfg.get("mirror_prefix") is not None
                           else (o.get("mirror_prefix") or "")).strip().lower()
    if MIRROR["cals"]:
        print("Calendar mirror: watching %s" % ", ".join(MIRROR["cals"]))


async def _ha_get(path):
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise RuntimeError("no SUPERVISOR_TOKEN — set homeassistant_api: true")
    headers = {"Authorization": "Bearer " + token}
    last = None
    for base in (SUPERVISOR, SUPERVISOR_IP):
        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.get(base + path, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status < 400:
                        return await r.json()
                    last = "HTTP %d" % r.status
        except Exception as e:
            last = str(e)
    raise RuntimeError("Home Assistant API unreachable: %s" % last)


def _ev_when(part):
    """HA hands back {'dateTime': '...-04:00'} or {'date': 'YYYY-MM-DD'} for
    all-day. Already local, so read the wall-clock straight off the string."""
    if not isinstance(part, dict):
        part = {"dateTime": str(part)}
    raw = part.get("dateTime") or part.get("date") or ""
    day = raw[:10]
    hhmm = raw[11:16] if "T" in raw else None
    return day, hhmm


def _mirror_attendees(summary, people):
    """'Evan: haircut' or 'haircut for Evan' -> Evan on the Cozi appointment."""
    m = re.match(r"^\s*([A-Za-z]+)\s*:\s*(.+)$", summary or "")
    if m:
        p = _match_person(m.group(1), people)
        if p:
            return [p["id"]], p["name"], m.group(2).strip()
    m = re.search(r"\bfor\s+([A-Za-z]+)\b", summary or "", re.I)
    if m:
        p = _match_person(m.group(1), people)
        if p:
            return [p["id"]], p["name"], _clean_tail(_cut(summary, m))
    for p in people:                       # bare name anywhere in the title
        if re.search(r"\b%s\b" % re.escape(p["name"]), summary or "", re.I):
            return [p["id"]], p["name"], summary
    return [], "", summary


async def _mirror_once():
    if not MIRROR["cals"] or not cozi_client or not logged_in:
        return {"mirrored": 0, "skipped": "not configured"}
    state = _voice_read()
    seen = state.setdefault("mirrored", {})
    seeding = not state.get("mirror_seeded")
    today = _now_local().date()
    start = today.isoformat() + "T00:00:00"
    end = (today + datetime.timedelta(days=MIRROR["days"])).isoformat() + "T00:00:00"
    people = await _cozi_persons()
    made = 0
    for cal in MIRROR["cals"]:
        try:
            evs = await _ha_get("/calendars/%s?start=%s&end=%s" % (cal, start, end))
        except Exception as e:
            print("Calendar mirror: %s" % e)
            continue
        for e in evs or []:
            summary = (e.get("summary") or "").strip()
            day, hhmm = _ev_when(e.get("start"))
            key = "%s|%s|%s|%s" % (cal, day, hhmm or "allday", summary.lower())
            if key in seen:
                continue
            if seeding:
                seen[key] = "seed"          # pre-existing: remember, don't copy
                continue
            if MIRROR["prefix"] and not summary.lower().startswith(MIRROR["prefix"]):
                seen[key] = "skipped"
                continue
            eday, ehhmm = _ev_when(e.get("end"))
            att, who, subject = _mirror_attendees(summary, people)
            if MIRROR["prefix"]:
                subject = subject[len(MIRROR["prefix"]):].strip(" :-") or subject
            try:
                y, mo, dd = (int(x) for x in day.split("-"))
                await cozi_client.add_appointment(
                    y, mo, dd, hhmm or "00:00",
                    (ehhmm or _plus_hour(hhmm)) if hhmm else "00:00",
                    0 if hhmm else 1, att, (e.get("location") or ""),
                    (e.get("description") or ""), subject or "Appointment")
            except Exception as ex:
                print("Calendar mirror: could not copy %r (%s)" % (summary, ex))
                continue
            seen[key] = _now_local().isoformat(timespec="seconds")
            made += 1
            _voice_log({"at": _now_local().isoformat(timespec="seconds"),
                        "text": summary, "source": "gcal", "ok": True,
                        "intent": "calendar",
                        "speech": "Copied %s to Cozi for %s%s."
                                  % (subject, day, (" (" + who + ")") if who else ""),
                        "detail": {"calendar": cal, "date": day, "start": hhmm}})
            state = _voice_read()           # _voice_log rewrote the file
            seen = state.setdefault("mirrored", {})
            seen[key] = _now_local().isoformat(timespec="seconds")
    if len(seen) > 800:                     # keep the ledger from growing forever
        for k in list(seen)[:len(seen) - 800]:
            seen.pop(k, None)
    state["mirror_seeded"] = True
    _voice_write(state)
    if seeding:
        print("Calendar mirror: seeded %d existing events (none copied)" % len(seen))
    return {"mirrored": made, "seeded": seeding, "watching": MIRROR["cals"]}


async def _mirror_loop():
    while True:
        try:
            if MIRROR["cals"]:
                await _mirror_once()
        except Exception as e:
            print("Calendar mirror loop: %s" % e)
        await asyncio.sleep(MIRROR["every"])


@app.post("/voice/mirror")
async def voice_mirror_now():
    """Run the Google-Calendar sweep immediately."""
    return await _mirror_once()


@app.get("/voice/mirror")
async def voice_mirror_status():
    state = _voice_read()
    return {"watching": MIRROR["cals"], "days": MIRROR["days"],
            "prefix": MIRROR["prefix"], "seeded": bool(state.get("mirror_seeded")),
            "known_events": len(state.get("mirrored", {}))}


@app.get("/cozi/calendar/{year}/{month}")
async def cozi_calendar(year: int, month: int):
    """A month of Cozi appointments — verification, and the dedupe key for any
    calendar mirror that feeds /voice/intent."""
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Cozi not connected")
    return await cozi_client.get_calendar(year, month)


# ------------------------------------------------------- Google Keep -> Cozi
# "Hey Google, add butter to my Kroger list" lands in Google Keep, because Keep
# is the only notes app Google still talks to. There is no official Keep API
# for personal accounts, so this rides on gkeepapi with a master token the
# owner mints once. New (unchecked) items on a Keep list are copied into the
# Cozi list with the matching name and then ticked off in Keep, so the tick is
# the "already synced" marker and nothing arrives twice.

KEEP_DB = "/data/keep.json"
KEEP = {"every": 60, "err": "", "last": "", "count": 0}


def _keep_read():
    try:
        with open(KEEP_DB) as f:
            return json.load(f)
    except Exception:
        return {}


def _keep_write(d):
    with open(KEEP_DB, "w") as f:
        json.dump(d, f)
    try:
        os.chmod(KEEP_DB, 0o600)          # it holds an account token
    except Exception:
        pass


def _android_id(d):
    if not d.get("android_id"):
        import random
        d["android_id"] = "%016x" % random.getrandbits(64)
    return d["android_id"]


def _keep_sync_sync():
    """Blocking half of the Keep poll — gkeepapi is synchronous."""
    d = _keep_read()
    if not d.get("email") or not d.get("master_token"):
        return {"ok": False, "reason": "not configured"}
    import gkeepapi
    keep = gkeepapi.Keep()
    state = d.get("state")
    ok = False
    for attempt in ("authenticate", "resume"):
        fn = getattr(keep, attempt, None)
        if not fn:
            continue
        try:
            fn(d["email"], d["master_token"], state=state)
            ok = True
            break
        except TypeError:
            try:
                fn(d["email"], d["master_token"])
                ok = True
                break
            except Exception as e:
                last = e
        except Exception as e:
            last = e
    if not ok:
        return {"ok": False, "reason": "login failed: %s" % last}
    lists = [n for n in keep.all() if getattr(n, "type", None)
             and str(n.type).endswith("List")]
    return {"ok": True, "keep": keep, "lists": lists, "state": keep.dump()}


async def _keep_once():
    if not cozi_client or not logged_in:
        return {"synced": 0, "reason": "Cozi not connected"}
    res = await asyncio.to_thread(_keep_sync_sync)
    if not res.get("ok"):
        KEEP["err"] = res.get("reason", "")
        return {"synced": 0, "reason": KEEP["err"]}
    KEEP["err"] = ""
    keep, lists = res["keep"], res["lists"]
    cozi_lists = await cozi_client.get_lists()
    d = _keep_read()
    made, touched = 0, []
    for note in lists:
        title = (note.title or "").strip()
        # Only shopping lists sync from Keep. Otherwise Keep-native planning
        # lists (Spring break, Christmas, …) that happen to share a Cozi name
        # get dumped in and duplicated. The alias table is the shopping filter.
        if not LIST_ALIASES.get(re.sub(r"\s+list$", "", title.lower()).strip()):
            continue
        target = _pick_list(title, cozi_lists)
        if not target:
            continue
        for item in list(note.items):
            if item.checked:
                continue
            text = (item.text or "").strip()
            if not text:
                continue
            await cozi_client.add_item(target.get("listId") or target.get("list_id"), text, 0)
            item.checked = True            # the tick is the synced marker
            made += 1
            touched.append("%s -> %s" % (text, target.get("title")))
            _voice_log({"at": _now_local().isoformat(timespec="seconds"),
                        "text": text, "source": "keep", "ok": True, "intent": "list",
                        "speech": "Copied %s from the %s Keep list to %s."
                                  % (text, title, target.get("title")),
                        "detail": {"keep_list": title, "cozi_list": target.get("title")}})
    if made:
        await asyncio.to_thread(keep.sync)
    d["state"] = keep.dump()
    d["last"] = _now_local().isoformat(timespec="seconds")
    _keep_write(d)
    KEEP["last"] = d["last"]
    KEEP["count"] += made
    return {"synced": made, "items": touched}


async def _keep_loop():
    while True:
        try:
            if _keep_read().get("master_token"):
                await _keep_once()
        except Exception as e:
            KEEP["err"] = str(e)
            print("Keep sync: %s" % e)
        await asyncio.sleep(KEEP["every"])


class KeepLogin(BaseModel):
    email: str
    oauth_token: str | None = None     # from accounts.google.com/EmbeddedSetup
    master_token: str | None = None    # if you already minted one


@app.post("/keep/login")
async def keep_login(req: KeepLogin):
    """Store Keep credentials. Give an oauth_token and the add-on trades it for
    a master token itself, so the long-lived secret never leaves this box."""
    d = _keep_read()
    d["email"] = req.email.strip()
    if req.master_token:
        d["master_token"] = req.master_token.strip()
    elif req.oauth_token:
        import gpsoauth
        aid = _android_id(d)
        res = await asyncio.to_thread(gpsoauth.exchange_token,
                                      d["email"], req.oauth_token.strip(), aid)
        tok = res.get("Token")
        if not tok:
            raise HTTPException(status_code=400,
                                detail="Google refused the oauth_token: %s"
                                       % (res.get("Error") or res))
        d["master_token"] = tok
    else:
        raise HTTPException(status_code=400, detail="need oauth_token or master_token")
    d.pop("state", None)
    _keep_write(d)
    out = await _keep_once()
    return {"status": "ok", "email": d["email"], "first_sync": out}


@app.get("/keep/status")
async def keep_status():
    d = _keep_read()
    return {"configured": bool(d.get("master_token")), "email": d.get("email", ""),
            "last_sync": d.get("last", ""), "synced_this_run": KEEP["count"],
            "error": KEEP["err"]}


@app.post("/keep/sync")
async def keep_sync_now():
    return await _keep_once()


class KeepNewList(BaseModel):
    title: str


@app.post("/keep/newlist")
async def keep_newlist(req: KeepNewList):
    """Create an empty Keep list, so 'add X to my <name> list' has a home."""
    def _mk():
        res = _keep_sync_sync()
        if not res.get("ok"):
            return res
        keep = res["keep"]
        for n in res["lists"]:
            if (n.title or "").strip().lower() == req.title.strip().lower():
                return {"ok": True, "existed": True}
        keep.createList(req.title.strip(), [])
        keep.sync()
        return {"ok": True, "existed": False, "state": keep.dump()}
    out = await asyncio.to_thread(_mk)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("reason"))
    if out.get("state"):
        d = _keep_read()
        d["state"] = out["state"]
        _keep_write(d)
    return {"status": "ok", "title": req.title, "already_there": out.get("existed")}


@app.get("/keep/lists")
async def keep_lists():
    """What Keep has, and which Cozi list each one would land on."""
    res = await asyncio.to_thread(_keep_sync_sync)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason"))
    cozi_lists = await cozi_client.get_lists() if (cozi_client and logged_in) else []
    out = []
    for note in res["lists"]:
        target = _pick_list((note.title or "").strip(), cozi_lists)
        out.append({"keep": note.title,
                    "cozi": (target or {}).get("title"),
                    "open_items": [i.text for i in note.items if not i.checked]})
    return {"lists": out}


@app.delete("/cozi/calendar/{year}/{month}/{appt_id}")
async def cozi_calendar_remove(year: int, month: int, appt_id: str):
    if not cozi_client or not logged_in:
        raise HTTPException(status_code=503, detail="Cozi not connected")
    await cozi_client.remove_appointment(year, month, appt_id)
    return {"status": "ok", "removed": appt_id}


@app.get("/voice/persons")
async def voice_persons():
    """Household members and their Cozi ids — the calendar attendee map."""
    return {"people": await _cozi_persons(force=True)}
