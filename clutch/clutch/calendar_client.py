"""
Real Google Calendar integration (OAuth in *testing* mode — your own account, no
app verification). Kept entirely optional: the tools in `tools.py` use this only
when `CLUTCH_REAL_CALENDAR=1` AND a token exists; otherwise they fall back to the
deterministic in-memory stub so the demo and eval stay reproducible offline.

One-time setup
--------------
1. In Google Cloud console: APIs & Services -> Credentials -> create an
   **OAuth client ID** of type *Desktop app*. Download it as `credentials.json`
   into this `clutch/` folder. Add your own email as a **test user** on the
   OAuth consent screen (keep the app in "Testing").
2. Authorize once (opens a browser, writes `token.json`):
       python -m clutch.calendar_client
3. Run the app with real calendar on:
       CLUTCH_REAL_CALENDAR=1 ./run_web.sh

Both `credentials.json` and `token.json` are gitignored — never commit them.
"""

from __future__ import annotations

import datetime as _dt
import os

# Full calendar scope: read free/busy + create events.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_HERE = os.path.dirname(__file__)
CLIENT_FILE = os.environ.get("CLUTCH_OAUTH_CLIENT", os.path.join(_HERE, "credentials.json"))
TOKEN_FILE = os.environ.get("CLUTCH_OAUTH_TOKEN", os.path.join(_HERE, "token.json"))

# Daily window we're willing to schedule focus time in (local-naive hours, UTC).
_DAY_START, _DAY_END = 8, 22


def is_enabled() -> bool:
    """True only when the user has opted in AND completed OAuth."""
    return os.environ.get("CLUTCH_REAL_CALENDAR") == "1" and os.path.exists(TOKEN_FILE)


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def authorize() -> str:
    """Run the one-time OAuth flow, writing token.json. Returns the token path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(CLIENT_FILE):
        raise FileNotFoundError(
            f"Missing OAuth client at {CLIENT_FILE}. Create a Desktop OAuth client "
            "in Google Cloud and download it as credentials.json into clutch/."
        )
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return TOKEN_FILE


def _iso(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).isoformat()


def free_slots(start_iso: str, end_iso: str, min_minutes: int = 60) -> list[dict]:
    """Free intervals in [start, end] within the daily window, via the freebusy API."""
    svc = _service()
    start = _dt.datetime.fromisoformat(start_iso)
    end = _dt.datetime.fromisoformat(end_iso)
    body = {"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": "primary"}]}
    busy = svc.freebusy().query(body=body).execute()["calendars"]["primary"]["busy"]
    busy = [(_dt.datetime.fromisoformat(b["start"]), _dt.datetime.fromisoformat(b["end"])) for b in busy]
    busy.sort()

    slots: list[dict] = []
    cursor = start
    for b_start, b_end in busy + [(end, end)]:
        if b_start > cursor:
            # carve the gap [cursor, b_start] into per-day working-hour windows
            _emit_window(cursor, min(b_start, end), slots, min_minutes)
        cursor = max(cursor, b_end)
        if cursor >= end:
            break
    return slots


def _emit_window(a: _dt.datetime, b: _dt.datetime, out: list[dict], min_minutes: int) -> None:
    day = a
    while day < b:
        win_start = day.replace(hour=_DAY_START, minute=0, second=0, microsecond=0)
        win_end = day.replace(hour=_DAY_END, minute=0, second=0, microsecond=0)
        s = max(a, win_start)
        e = min(b, win_end)
        if (e - s).total_seconds() / 60 >= min_minutes:
            out.append({"start": _iso(s), "end": _iso(e)})
        day = (day + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def insert_event(summary: str, start_iso: str, end_iso: str) -> dict:
    """Create a real event on the primary calendar; returns id + htmlLink."""
    svc = _service()
    ev = svc.events().insert(calendarId="primary", body={
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "description": "Focus block reserved by Clutch.",
    }).execute()
    return {"id": ev["id"], "summary": ev.get("summary", summary),
            "start": start_iso, "end": end_iso, "htmlLink": ev.get("htmlLink")}


if __name__ == "__main__":
    path = authorize()
    print(f"✓ Authorized. Token written to {path}. Run with CLUTCH_REAL_CALENDAR=1 to use it.")
