#!/usr/bin/env python3
"""
Activity provider abstraction.

Defines the contract every data source (Strava, Garmin, ...) implements, plus the
caching algorithm they all share. Subclasses supply only what genuinely differs:
authentication and the raw fetch. Everything else — incremental cache windows,
merge-by-id, permanent detail caching, validation, sorting — lives here once.

All providers emit the same Strava-shaped activity dict, which is the contract the
consumer scripts read:

    {'id', 'type' ('Run' for runs), 'sport_type', 'start_date' (ISO+Z),
     'distance' (m), 'moving_time' (s), 'average_heartrate' (bpm)}

Detail objects add 'name' and 'laps': [{'distance', 'moving_time',
'average_heartrate', 'max_heartrate', 'average_speed' (m/s)}].
"""

import os
import re
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any


def get_config_dir() -> str:
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        return os.path.join(xdg_config, 'marathon-training-coach')
    return os.path.expanduser('~/.config/marathon-training-coach')


CONFIG_DIR = get_config_dir()
os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

_START_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T')


class ProviderUnavailable(Exception):
    """Raised when a provider cannot authenticate (missing creds, dead API)."""


class ActivityProvider(ABC):
    """Base class for activity data sources.

    Subclass contract: `name`, `is_available`, `authenticate`, `fetch_since`,
    `fetch_detail`. The caching/merging behavior is inherited and should not be
    overridden.
    """

    #: Short identifier, also used to derive cache filenames.
    name: str = 'base'

    #: Days of overlap re-fetched on each incremental sync, so edits to recent
    #: activities are picked up rather than frozen in cache.
    overlap_days: int = 3

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    # ------------------------------------------------------------------
    # Cache locations (overridable — Strava keeps its legacy filenames)
    # ------------------------------------------------------------------

    @property
    def activities_cache_path(self) -> str:
        return os.path.join(CONFIG_DIR, f'activities_cache_{self.name}.json')

    @property
    def details_cache_path(self) -> str:
        return os.path.join(CONFIG_DIR, f'activity_details_cache_{self.name}.json')

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """True if this provider is configured enough to attempt authentication."""

    @abstractmethod
    def authenticate(self) -> bool:
        """Establish credentials. Return True on success. Must not raise."""

    @abstractmethod
    def fetch_since(self, start: datetime) -> List[Dict[str, Any]]:
        """Fetch activities on/after `start` (tz-aware UTC), in Strava shape."""

    @abstractmethod
    def fetch_detail(self, activity_id: Any) -> Optional[Dict[str, Any]]:
        """Fetch one activity with laps, in Strava shape. None on failure."""

    # ------------------------------------------------------------------
    # Shared behavior
    # ------------------------------------------------------------------

    def fetch_activities(self, days: int = 28) -> List[Dict[str, Any]]:
        """Return activities from the last `days`, cache-first.

        Loads the on-disk cache, asks the provider only for activities since the
        newest cached entry (minus an overlap window), merges by id, persists, and
        returns the requested window newest-first. Repeat runs therefore make
        near-zero API calls.
        """
        cached = self._load_activities_cache()
        now = datetime.now(timezone.utc)

        start = self._incremental_start(cached, now, days)
        new_activities = self._safe_fetch_since(start)

        merged: Dict[Any, Dict[str, Any]] = {
            a['id']: a for a in cached if a.get('id')
        }
        for a in new_activities:
            if a.get('id'):
                merged[a['id']] = a  # fresh data wins (activity may have been edited)

        all_activities = list(merged.values())
        self._save_activities_cache(all_activities)

        cutoff = now - timedelta(days=days)
        window = [a for a in all_activities if self._start_dt(a) and self._start_dt(a) > cutoff]
        window.sort(key=lambda a: a.get('start_date', ''), reverse=True)
        self.logger.debug(
            "%s: returning %d activities (cache holds %d)",
            self.name, len(window), len(all_activities))
        return window

    def fetch_activity_detail(self, activity_id: Any) -> Optional[Dict[str, Any]]:
        """Return one activity with laps, cached permanently.

        Recorded activities are immutable, so a hit is served forever and each
        activity costs exactly one API call over its lifetime.
        """
        cache = self._load_details_cache()
        key = str(activity_id)
        if key in cache:
            self.logger.debug("Detail cache hit for %s", activity_id)
            return cache[key]

        try:
            detail = self.fetch_detail(activity_id)
        except Exception as e:
            self.logger.error("%s: detail fetch failed for %s: %s", self.name, activity_id, e)
            return None
        if not detail:
            return None

        cache[key] = detail
        self._write_json(self.details_cache_path, cache)
        return detail

    def _incremental_start(self, cached: List[Dict], now: datetime, days: int) -> datetime:
        """Earliest date to request: just before the newest cached activity."""
        full_window = now - timedelta(days=days)
        if not cached:
            self.logger.debug("%s: no cache, fetching last %d days", self.name, days)
            return full_window
        latest = max((a.get('start_date', '') for a in cached), default='')
        latest_dt = self._parse_iso(latest)
        if not latest_dt:
            return full_window
        self.logger.debug("%s: cache has %d activities, syncing since %s",
                          self.name, len(cached), latest[:10])
        return latest_dt - timedelta(days=self.overlap_days)

    def _safe_fetch_since(self, start: datetime) -> List[Dict[str, Any]]:
        """Call the provider, validate results, never let a failure nuke the cache."""
        try:
            raw = self.fetch_since(start)
        except Exception as e:
            self.logger.error("%s: fetch failed (%s); serving cache only", self.name, e)
            return []
        if not isinstance(raw, list):
            self.logger.error("%s: invalid fetch response type", self.name)
            return []
        valid = [a for a in raw
                 if isinstance(a, dict) and _START_DATE_RE.match(a.get('start_date', ''))]
        dropped = len(raw) - len(valid)
        if dropped:
            self.logger.debug("%s: dropped %d activities with bad start_date", self.name, dropped)
        return valid

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------

    def _load_activities_cache(self) -> List[Dict[str, Any]]:
        data = self._read_json(self.activities_cache_path)
        return data if isinstance(data, list) else []

    def _save_activities_cache(self, activities: List[Dict[str, Any]]) -> None:
        activities.sort(key=lambda a: a.get('start_date', ''), reverse=True)
        self._write_json(self.activities_cache_path, activities)

    def _load_details_cache(self) -> Dict[str, Any]:
        data = self._read_json(self.details_cache_path)
        return data if isinstance(data, dict) else {}

    def _read_json(self, path: str) -> Any:
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, IOError, OSError) as e:
            self.logger.debug("Cache read error for %s (will rebuild): %s", path, e)
            return None

    def _write_json(self, path: str, payload: Any) -> None:
        try:
            with open(path, 'w') as f:
                json.dump(payload, f, separators=(',', ':'))
            os.chmod(path, 0o600)
        except (IOError, OSError) as e:
            self.logger.debug("Cache write error for %s: %s", path, e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso(value: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            return None

    @classmethod
    def _start_dt(cls, activity: Dict[str, Any]) -> Optional[datetime]:
        return cls._parse_iso(activity.get('start_date', ''))
