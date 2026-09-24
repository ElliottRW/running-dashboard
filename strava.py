"""Talking to Strava: login (OAuth), token refresh, and polite API requests.

Your Client ID and Secret are read from .env on this computer only. They are
never sent to the browser and never printed.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import db

API = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:read_all"

# Leave a little headroom under Strava's limits (100 per 15 min, 1,000 per day)
SHORT_MARGIN = 3
DAILY_MARGIN = 10


class NotConfigured(Exception):
    """.env is missing the Client ID or Secret."""


class AuthError(Exception):
    """Login expired or was revoked – you need to click Connect again."""


class RateLimited(Exception):
    """Strava asked us to slow down. resume_at is a UTC datetime."""

    def __init__(self, resume_at, daily):
        super().__init__("rate limited")
        self.resume_at = resume_at
        self.daily = daily


class Offline(Exception):
    """Couldn't reach Strava at all."""


def credentials():
    cid = (os.environ.get("STRAVA_CLIENT_ID") or "").strip()
    secret = (os.environ.get("STRAVA_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise NotConfigured()
    return cid, secret


def is_configured():
    try:
        credentials()
        return True
    except NotConfigured:
        return False


def authorize_url(redirect_uri, state):
    cid, _ = credentials()
    req = requests.Request("GET", AUTHORIZE_URL, params={
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": state,
    }).prepare()
    return req.url


def _token_request(data):
    cid, secret = credentials()
    try:
        r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, **data},
                          timeout=20)
    except requests.RequestException:
        raise Offline()
    if r.status_code in (400, 401, 403):
        raise AuthError()
    r.raise_for_status()
    return r.json()


def exchange_code(code, granted_scope):
    """Turn the one-time code from Strava's login page into saved tokens."""
    tok = _token_request({"code": code, "grant_type": "authorization_code"})
    athlete = tok.get("athlete") or {}
    name = " ".join(p for p in (athlete.get("firstname"), athlete.get("lastname")) if p)
    db.save_auth(tok["access_token"], tok["refresh_token"], tok["expires_at"],
                 scope=granted_scope, athlete_id=athlete.get("id"), athlete_name=name or None)


def _valid_access_token():
    auth = db.get_auth()
    if not auth or not auth.get("refresh_token"):
        raise AuthError()
    # Refresh if the token expires within the next 5 minutes
    if auth["expires_at"] - 300 < time.time():
        try:
            tok = _token_request({"grant_type": "refresh_token",
                                  "refresh_token": auth["refresh_token"]})
        except AuthError:
            db.clear_auth()
            raise
        db.save_auth(tok["access_token"], tok["refresh_token"], tok["expires_at"])
        return tok["access_token"]
    return auth["access_token"]


# ---------- rate limits ----------

def next_quarter_hour(now=None):
    """Strava's 15-minute window resets at :00, :15, :30 and :45."""
    now = now or datetime.now(timezone.utc)
    minutes = (now.minute // 15 + 1) * 15
    base = now.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=minutes, seconds=5)


def next_utc_midnight(now=None):
    """The daily limit resets at midnight UTC."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)


class Usage:
    """Remembers what Strava's rate-limit headers said on the last response."""

    def __init__(self):
        self.short_limit, self.daily_limit = 100, 1000
        self.short_used, self.daily_used = 0, 0
        self.window_start = None

    def update(self, headers):
        # Strava sends read-specific headers; fall back to the overall ones
        limit = headers.get("X-ReadRateLimit-Limit") or headers.get("X-RateLimit-Limit")
        usage = headers.get("X-ReadRateLimit-Usage") or headers.get("X-RateLimit-Usage")
        try:
            if limit:
                self.short_limit, self.daily_limit = (int(x) for x in limit.split(","))
            if usage:
                self.short_used, self.daily_used = (int(x) for x in usage.split(","))
                self.window_start = next_quarter_hour() - timedelta(minutes=15)
        except ValueError:
            pass

    def check(self):
        """Raise RateLimited before a request if we're about to hit a limit."""
        now = datetime.now(timezone.utc)
        # Forget the 15-minute count once its window has passed
        if self.window_start and now >= self.window_start + timedelta(minutes=15):
            self.short_used = 0
        if self.daily_used >= self.daily_limit - DAILY_MARGIN:
            raise RateLimited(next_utc_midnight(now), daily=True)
        if self.short_used >= self.short_limit - SHORT_MARGIN:
            raise RateLimited(next_quarter_hour(now), daily=False)

    def as_dict(self):
        return {"short_used": self.short_used, "short_limit": self.short_limit,
                "daily_used": self.daily_used, "daily_limit": self.daily_limit}


usage = Usage()


def get(path, params=None):
    """GET from the Strava API. Returns (status_code, json_or_None)."""
    usage.check()
    for attempt in (1, 2):
        token = _valid_access_token()
        try:
            r = requests.get(API + path, params=params,
                             headers={"Authorization": "Bearer " + token}, timeout=30)
        except requests.RequestException:
            raise Offline()
        usage.update(r.headers)

        if r.status_code == 429:
            daily = usage.daily_used >= usage.daily_limit - DAILY_MARGIN
            raise RateLimited(next_utc_midnight() if daily else next_quarter_hour(), daily)
        if r.status_code == 401:
            if attempt == 1:
                # Token may have been invalidated early – force one refresh and retry
                auth = db.get_auth()
                if auth:
                    db.save_auth(auth["access_token"], auth["refresh_token"], 0)
                continue
            db.clear_auth()
            raise AuthError()
        if r.status_code == 404:
            return 404, None
        r.raise_for_status()
        return r.status_code, r.json()
    raise AuthError()
