#!/usr/bin/env python3
"""
Strava data provider.

Owns everything Strava-specific: OAuth token load/refresh and the v3 REST calls.
Caching, merging and validation come from ActivityProvider.

Strava's API already returns the canonical field shape used across this repo, so
activities pass through unchanged.
"""

import os
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict, List, Any
from urllib.error import HTTPError, URLError

from activity_provider import ActivityProvider, CONFIG_DIR

TOKEN_FILE = os.path.join(CONFIG_DIR, 'strava_tokens.json')

API_BASE = 'https://www.strava.com/api/v3'
TOKEN_URL = 'https://www.strava.com/oauth/token'
USER_AGENT = 'TrainingCoach/2.0'


def validate_token_data(data: Dict) -> bool:
    if not isinstance(data, dict):
        return False
    access_token = data.get('access_token')
    return isinstance(access_token, str) and len(access_token) >= 10


class StravaProvider(ActivityProvider):
    """Strava REST provider. Auth is a bearer access token with refresh."""

    name = 'strava'

    def __init__(self, logger: logging.Logger):
        super().__init__(logger)
        self.access_token: Optional[str] = None

    # Legacy cache filenames — keep existing caches valid across this refactor.
    @property
    def activities_cache_path(self) -> str:
        return os.path.join(CONFIG_DIR, 'activities_cache.json')

    @property
    def details_cache_path(self) -> str:
        return os.path.join(CONFIG_DIR, 'activity_details_cache.json')

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return os.path.exists(TOKEN_FILE)

    def authenticate(self) -> bool:
        """Load the access token, refreshing it if expired.

        Returns False when Strava auth is dead (no token file, invalid data, or a
        rejected refresh) — the signal the registry uses to fall back.
        """
        try:
            with open(TOKEN_FILE, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            self.logger.error("Strava token file not found. Run auth.py first.")
            return False
        except (json.JSONDecodeError, IOError, OSError) as e:
            self.logger.error(f"Cannot read Strava token file: {e}")
            return False

        if not validate_token_data(data):
            self.logger.error("Invalid Strava token data structure")
            return False

        expires_at = data.get('expires_at', 0)
        if expires_at and expires_at < (datetime.now().timestamp() + 300):
            self.logger.debug("Strava token expired, refreshing...")
            refresh_token = data.get('refresh_token')
            if not refresh_token:
                return False
            self.access_token = self._refresh(refresh_token)
        else:
            self.access_token = data.get('access_token')
        return bool(self.access_token)

    def _refresh(self, refresh_token: str) -> Optional[str]:
        client_id = os.environ.get('STRAVA_CLIENT_ID', '').strip()
        client_secret = os.environ.get('STRAVA_CLIENT_SECRET', '').strip()
        if not client_id or not client_secret:
            self.logger.error("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET required")
            return None

        payload = urllib.parse.urlencode({
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }).encode()
        req = urllib.request.Request(
            TOKEN_URL, data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST')

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                new_tokens = json.loads(response.read().decode())
        except HTTPError as e:
            if e.code == 401:
                self.logger.error("Strava authentication failed - credentials may be invalid")
            else:
                self.logger.error(f"Strava token refresh failed: HTTP {e.code}")
            return None
        except (URLError, TimeoutError) as e:
            self.logger.error(f"Network error during Strava token refresh: {e}")
            return None
        except json.JSONDecodeError:
            self.logger.error("Invalid response from Strava token server")
            return None

        if not validate_token_data(new_tokens):
            self.logger.error("Invalid token response from Strava")
            return None
        self._write_json(TOKEN_FILE, new_tokens)
        self.logger.info("Strava token refreshed successfully")
        return new_tokens.get('access_token')

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {'Authorization': f'Bearer {self.access_token}', 'User-Agent': USER_AGENT}

    def fetch_since(self, start: datetime) -> List[Dict[str, Any]]:
        after_ts = int(start.timestamp())
        url = f'{API_BASE}/athlete/activities?after={after_ts}&per_page=200'
        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers=self._headers())
                with urllib.request.urlopen(req, timeout=30) as response:
                    activities = json.loads(response.read().decode())
                if not isinstance(activities, list):
                    self.logger.error("Invalid Strava API response format")
                    return []
                self.logger.debug("Fetched %d activities from Strava", len(activities))
                return activities
            except HTTPError as e:
                if e.code == 401:
                    self.logger.error("Strava authentication expired")
                    return []
                self.logger.warning(f"Strava HTTP error (attempt {attempt + 1}): {e.code}")
                if attempt == max_retries - 1:
                    return []
            except (URLError, TimeoutError) as e:
                self.logger.error(f"Strava network error (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return []
            except json.JSONDecodeError:
                self.logger.error("Invalid JSON response from Strava API")
                return []
        return []

    def fetch_detail(self, activity_id: Any) -> Optional[Dict[str, Any]]:
        url = f'{API_BASE}/activities/{activity_id}'
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as e:
            self.logger.error(f"HTTP {e.code} fetching Strava activity {activity_id}")
        except (URLError, TimeoutError) as e:
            self.logger.error(f"Network error fetching Strava activity {activity_id}: {e}")
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON for Strava activity {activity_id}")
        return None
