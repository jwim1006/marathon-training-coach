#!/usr/bin/env python3
"""
Garmin Connect data provider.

Owns Garmin auth (cached OAuth tokens) and the translation from Garmin's payloads
into the canonical Strava-shaped dicts the rest of the repo consumes. Caching and
merging come from ActivityProvider.

Account safety — the reason this class is careful:
  * Tokens cache under CONFIG_DIR/garmin/. The library loads them and refreshes the
    DI token transparently, so the SSO password endpoint is only hit on the very
    first run. Repeated password logins get the IP rate-limited (HTTP 429).
  * The client is built once per instance and reused for list and detail calls.
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

from activity_provider import ActivityProvider, CONFIG_DIR

TOKEN_DIR = os.path.join(CONFIG_DIR, 'garmin')

# Garmin splits running into subtypes; all are runs and must map to Strava 'Run'
# (consumers detect runs purely via type == 'Run').
RUNNING_TYPE_KEYS = {
    'running', 'track_running', 'trail_running', 'indoor_running',
    'virtual_run', 'street_running', 'treadmill_running',
}

# Garmin typeKey -> Strava sport. Unmapped types become 'Workout' (generic).
SPORT_MAP = {
    **{k: 'Run' for k in RUNNING_TYPE_KEYS},
    'cycling': 'Ride',
    'mountain_biking': 'Ride',
    'swimming': 'Swim',
    'hiking': 'Hike',
    'walking': 'Walk',
    'strength_training': 'WeightTraining',
    'crossfit': 'Crossfit',
}


class GarminProvider(ActivityProvider):
    """Garmin Connect provider via the python-garminconnect library."""

    name = 'garmin'

    def __init__(self, logger: logging.Logger):
        super().__init__(logger)
        self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Usable if tokens are cached or credentials are set."""
        return os.path.isdir(TOKEN_DIR) or bool(
            os.environ.get('GARMIN_EMAIL') and os.environ.get('GARMIN_PASSWORD'))

    def authenticate(self) -> bool:
        try:
            self._connect()
            return True
        except Exception as e:
            self.logger.error("Garmin authentication failed: %s", e)
            return False

    @property
    def client(self):
        """Authenticated Garmin client, created once per instance."""
        if self._client is None:
            self._connect()
        return self._client

    def _persist_tokens(self, client) -> None:
        """Save OAuth tokens so the next run skips the SSO password endpoint."""
        try:
            os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
            client.client.dump(TOKEN_DIR)
        except Exception as e:
            self.logger.debug("Could not persist Garmin tokens: %s", e)

    def _mfa_prompt(self) -> str:
        """Supply an MFA code without assuming a terminal is attached.

        Prefers GARMIN_MFA_CODE (settable on a server for a one-off re-auth) and
        only falls back to an interactive prompt when stdin is really a TTY.
        On a headless box this raises instead of blocking a scheduled job forever.
        """
        code = os.environ.get('GARMIN_MFA_CODE', '').strip()
        if code:
            return code
        if sys.stdin is not None and sys.stdin.isatty():
            return input("Garmin MFA code: ")
        raise RuntimeError(
            "Garmin requires an MFA code but no TTY is available. Re-authenticate "
            "interactively and copy the refreshed token file to this host, or set "
            "GARMIN_MFA_CODE for a one-off login.")

    def _connect(self) -> None:
        """Log in, preferring cached tokens over the password endpoint."""
        try:
            from garminconnect import Garmin
        except ImportError:
            raise RuntimeError(
                "garminconnect is not installed. Install it with: pip install garminconnect")

        email = os.environ.get('GARMIN_EMAIL')
        password = os.environ.get('GARMIN_PASSWORD')
        has_creds = bool(email and password)
        client = Garmin(
            email, password,
            prompt_mfa=self._mfa_prompt if has_creds else None,
        )

        # Tokenstore login: loads cached tokens and refreshes the DI token; only
        # falls through to the password endpoint when no usable tokens exist.
        try:
            client.login(TOKEN_DIR)
        except Exception as e:
            if not has_creds:
                raise
            self.logger.info("Cached Garmin tokens unavailable (%s); fresh login.", e)
            client.login()

        # Always persist: login() may have refreshed the DI token in the
        # background, and an unsaved refresh means the next run re-authenticates
        # against the SSO endpoint (which gets the IP rate limited).
        self._persist_tokens(client)
        self._client = client
        self.logger.debug("Garmin client ready (tokenstore=%s)", TOKEN_DIR)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def fetch_since(self, start: datetime) -> List[Dict[str, Any]]:
        start_date = start.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        self.logger.debug("Garmin fetch %s -> %s", start_date, end_date)
        # No server-side type filter, so running subtypes are not dropped;
        # classification happens in _convert_activity.
        raw = self.client.get_activities_by_date(start_date, end_date)
        if not isinstance(raw, list):
            return []
        return [self._convert_activity(a) for a in raw
                if isinstance(a, dict) and a.get('activityId')]

    def fetch_detail(self, activity_id: Any) -> Optional[Dict[str, Any]]:
        aid = str(activity_id)
        summary = self.client.get_activity(aid) or {}
        if not summary:
            return None

        try:
            splits = self.client.get_activity_splits(aid) or {}
        except Exception as e:
            self.logger.debug("Garmin splits fetch failed for %s: %s", aid, e)
            splits = {}

        lap_list = splits.get('lapDTOs') or splits.get('laps') or []
        detail = self._convert_activity(summary)
        detail['laps'] = [self._convert_lap(l) for l in lap_list if isinstance(l, dict)]
        detail['name'] = summary.get('activityName') or detail.get('name') or ''
        return detail

    # ------------------------------------------------------------------
    # Garmin -> Strava shape
    # ------------------------------------------------------------------

    @staticmethod
    def _field(a: Dict[str, Any], key: str, default: Any = None) -> Any:
        """Read a field across both Garmin payload shapes.

        List payloads (get_activities*) keep metrics flat; the detail payload
        (get_activity) nests them under 'summaryDTO'.
        """
        if a.get(key) is not None:
            return a[key]
        nested = a.get('summaryDTO')
        if isinstance(nested, dict) and nested.get(key) is not None:
            return nested[key]
        return default

    @staticmethod
    def _type_key(a: Dict[str, Any]) -> str:
        return (a.get('activityType') or a.get('activityTypeDTO') or {}).get('typeKey') or ''

    @classmethod
    def _to_iso(cls, start_time_local: str) -> str:
        """'2026-07-29 09:52:33' -> '2026-07-29T09:52:33Z'."""
        try:
            return datetime.fromisoformat(start_time_local).replace(microsecond=0).isoformat() + 'Z'
        except (ValueError, TypeError):
            return start_time_local or ''

    @classmethod
    def _convert_activity(cls, a: Dict[str, Any]) -> Dict[str, Any]:
        """Garmin activity (either shape) -> canonical Strava-shaped dict."""
        sport = SPORT_MAP.get(cls._type_key(a), 'Workout')
        avg_hr = cls._field(a, 'averageHR')
        # Prefer true moving time; fall back to elapsed duration.
        moving = cls._field(a, 'movingDuration') or cls._field(a, 'duration') or 0
        return {
            'id': a.get('activityId'),
            'name': a.get('activityName') or '',
            'type': sport,
            'sport_type': sport,
            'start_date': cls._to_iso(cls._field(a, 'startTimeLocal', '')),
            'distance': float(cls._field(a, 'distance', 0) or 0),   # meters
            'moving_time': float(moving),                           # seconds
            'average_heartrate': int(round(avg_hr)) if avg_hr else 0,
        }

    @staticmethod
    def _convert_lap(lap: Dict[str, Any]) -> Dict[str, Any]:
        """Garmin lap -> Strava lap (workout_analysis contract)."""
        avg_hr = lap.get('averageHR')
        max_hr = lap.get('maxHR')
        return {
            'distance': float(lap.get('distance') or 0),                               # meters
            'moving_time': float(lap.get('movingDuration') or lap.get('duration') or 0),  # seconds
            'average_heartrate': int(round(avg_hr)) if avg_hr else 0,
            'max_heartrate': int(round(max_hr)) if max_hr else 0,
            'average_speed': float(lap.get('avgSpeed') or lap.get('averageSpeed') or 0),  # m/s
        }
