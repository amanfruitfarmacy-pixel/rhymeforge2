"""YouTube upload + analytics (OAuth). Requires: pip install 'rhymeforge[youtube]'.

One-time setup: create an OAuth client (Desktop app) in Google Cloud, enable
"YouTube Data API v3" and "YouTube Analytics API", download the client JSON to
.secrets/client_secret.json. The first run opens a browser to authorise.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .util import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def next_slots(publish_days: list[str], time_str: str, tz: str, count: int,
               min_lead_hours: float = 24, now: datetime | None = None,
               taken: list[str] | None = None) -> list[datetime]:
    """Next `count` publish slots on the given weekdays, at least min_lead_hours from now."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    hh, mm = (int(x) for x in time_str.split(":"))
    wanted = {DAYS.index(d[:3].lower()) for d in publish_days}
    taken_set = set(taken or [])
    slots = []
    day = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for _ in range(60):
        if day.weekday() in wanted and day - now >= timedelta(hours=min_lead_hours) \
                and day.isoformat() not in taken_set:
            slots.append(day)
            if len(slots) == count:
                break
        day = (day + timedelta(days=1)).replace(hour=hh, minute=mm)
    return slots


def credentials(secrets_dir: Path, interactive: bool = True, open_browser: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    token = secrets_dir / "token.json"
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(f"Not signed in to YouTube ({token} missing or expired). Run `rhymeforge auth` first.")
        client = secrets_dir / "client_secret.json"
        if not client.exists():
            raise RuntimeError(f"Missing {client}. Download your OAuth client (Desktop app) JSON there. "
                               "See README: 'YouTube setup'.")
        flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=open_browser,
                                      authorization_prompt_message="Open this link to sign in to YouTube:\n{url}\n")
        secrets_dir.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json())
    return creds


def whoami(creds) -> list[dict]:
    """The YouTube channel(s) this login can manage, to confirm the right account."""
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=creds)
    r = yt.channels().list(part="snippet,statistics,status", mine=True).execute()
    return [{"id": c["id"], "title": c["snippet"]["title"],
             "subscribers": c["statistics"].get("subscriberCount"),
             "made_for_kids_default": c.get("status", {}).get("madeForKids")} for c in r.get("items", [])]


def upload_video(creds, final: Path, thumb: Path, package: dict, cfg: dict, publish_at: datetime,
                 captions: Path | None = None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": package["title"][:100], "description": package["description"][:5000],
                    "tags": package["tags"], "categoryId": cfg["channel"]["category_id"],
                    "defaultLanguage": cfg["channel"]["language"],
                    "defaultAudioLanguage": cfg["channel"]["language"]},
        "status": {"privacyStatus": "private", "publishAt": publish_at.isoformat(),
                   "selfDeclaredMadeForKids": True, "containsSyntheticMedia": True,
                   "embeddable": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(final), chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"upload {int(status.progress() * 100)}%")
    vid = resp["id"]
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
    except Exception as e:  # noqa: BLE001 - custom thumbnails need a verified channel
        log(f"thumbnail upload failed (verify your channel's phone number?): {e}")
    if captions and captions.exists():
        try:
            yt.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": cfg["channel"]["language"],
                                                                   "name": "Lyrics"}},
                                 media_body=MediaFileUpload(str(captions))).execute()
        except Exception as e:  # noqa: BLE001
            log(f"caption upload failed: {e}")
    if cfg["upload"].get("playlist_id"):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": cfg["upload"]["playlist_id"],
            "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    return {"video_id": vid, "publish_at": publish_at.isoformat(),
            "studio_url": f"https://studio.youtube.com/video/{vid}/edit"}


def fetch_retention(creds, video_id: str, start: str = "2020-01-01", end: str | None = None) -> list[dict]:
    """Audience retention curve: 100 points of (elapsed ratio, audienceWatchRatio)."""
    from googleapiclient.discovery import build
    yta = build("youtubeAnalytics", "v2", credentials=creds)
    end = end or datetime.now().strftime("%Y-%m-%d")
    r = yta.reports().query(ids="channel==MINE", startDate=start, endDate=end,
                            metrics="audienceWatchRatio,relativeRetentionPerformance",
                            dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}").execute()
    return [{"ratio": float(row[0]), "retention": float(row[1]),
             "relative": float(row[2]) if len(row) > 2 else None} for row in r.get("rows", [])]
