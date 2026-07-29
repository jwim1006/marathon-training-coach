#!/usr/bin/env python3
"""
Provider selection and failover.

Chooses the active ActivityProvider and owns the Strava -> Garmin cutover policy:

  * DATA_PROVIDER=strava|garmin  -> that provider, strictly, no fallback.
  * DATA_PROVIDER unset (default) -> try Strava, and if its auth is dead (which is
    what happens when Strava API access expires), fall back to Garmin automatically.

Selection is resolved once per process and cached, so a fallback decision applies
to every later call rather than being re-litigated (and re-logged) each time.
"""

import os
import logging
from typing import Optional, List, Type

from activity_provider import ActivityProvider
from strava_fetcher import StravaProvider
from garmin_fetcher import GarminProvider

#: Preference order used when DATA_PROVIDER is unset.
PROVIDER_CHAIN: List[Type[ActivityProvider]] = [StravaProvider, GarminProvider]

PROVIDERS = {cls.name: cls for cls in PROVIDER_CHAIN}

_active: Optional[ActivityProvider] = None


def configured_provider() -> Optional[str]:
    """The explicitly requested provider name, or None for automatic mode."""
    return (os.environ.get('DATA_PROVIDER') or '').lower() or None


def get_provider(logger: logging.Logger) -> Optional[ActivityProvider]:
    """Return the authenticated provider for this process, or None if none work.

    Caches the result: the first call decides, later calls reuse that decision.
    """
    global _active
    if _active is not None:
        return _active

    requested = configured_provider()
    if requested:
        cls = PROVIDERS.get(requested)
        if cls is None:
            logger.error("Unknown DATA_PROVIDER=%r (expected one of: %s)",
                         requested, ', '.join(PROVIDERS))
            return None
        _active = _try(cls, logger, strict=True)
        return _active

    # Automatic mode: first provider in the chain that authenticates wins.
    for index, cls in enumerate(PROVIDER_CHAIN):
        provider = _try(cls, logger, strict=False)
        if provider:
            if index > 0:
                logger.warning(
                    "%s auth unavailable - automatically switched to %s. "
                    "Set DATA_PROVIDER=%s to make this permanent.",
                    PROVIDER_CHAIN[index - 1].name, provider.name, provider.name)
            _active = provider
            return _active

    logger.error("No usable data provider: configure Strava (auth.py) or Garmin "
                 "(GARMIN_EMAIL / GARMIN_PASSWORD).")
    return None


def _try(cls: Type[ActivityProvider], logger: logging.Logger,
         strict: bool) -> Optional[ActivityProvider]:
    """Instantiate and authenticate one provider. None if unusable."""
    provider = cls(logger)
    if not provider.is_available():
        message = "%s is not configured" % cls.name
        logger.error(message) if strict else logger.debug(message)
        return None
    if not provider.authenticate():
        return None
    logger.debug("Active data provider: %s", provider.name)
    return provider


def reset() -> None:
    """Clear the cached provider. For tests and for forcing re-selection."""
    global _active
    _active = None
