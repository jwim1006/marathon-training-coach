"""Run restored coach reports using cached data only; never refresh live tokens.

Pipe into the rehearsal container's Python via stdin. Output contains only check
results, not athlete details, credentials or report contents.
"""
import contextlib
import io
import importlib
import json
import logging
from pathlib import Path
import socket
import sys
import traceback


def main():
    home = Path('/home/node')
    state = home / '.openclaw'
    config = json.loads((state / 'openclaw.json').read_text())
    assert config['cron']['enabled'] is False
    assert all(channel.get('enabled') is False for channel in config['channels'].values())
    assert config['agents']['defaults']['heartbeat']['every'] == '0m'
    import garminconnect, curl_cffi, dotenv  # noqa: F401

    def no_network(*args, **kwargs):
        raise RuntimeError('Network disabled for offline coaching checks')

    socket.create_connection = no_network
    socket.socket.connect = no_network
    skill = state / 'workspace/skills/strava-training-coach/scripts'
    sys.path.insert(0, str(skill))
    from strava_fetcher import StravaProvider
    from garmin_fetcher import GarminProvider
    import providers

    # Authenticate and fetch methods are replaced before any report runs. This
    # avoids rotating credentials also in use by the still-running Zeabur coach.
    for provider in (StravaProvider, GarminProvider):
        provider.authenticate = lambda self: True
        provider.fetch_since = lambda self, start: []
        provider.fetch_detail = lambda self, activity_id: None
    logging.disable(logging.CRITICAL)
    results = {'rehearsal_inactive': True, 'python_dependencies': 'passed', 'provider_authentication': 'not attempted'}
    for filename, arguments in [('weekly_report.py', []), ('marathon_status.py', ['--json'])]:
        providers.reset()
        sys.argv = [str(skill / filename), *arguments]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            report_module = importlib.import_module(Path(filename).stem)
            exit_status = report_module.main()
            if exit_status not in (None, 0):
                raise RuntimeError('Report process returned a failure status')
        report = json.loads(captured.getvalue())
        assert isinstance(report, dict) and report
        assert 'error' not in report, 'Report returned an application error'
        results[filename] = 'cached-data JSON generated'
    print(json.dumps(results))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        locations = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                     for frame in traceback.extract_tb(error.__traceback__)]
        print(json.dumps({'offline_smoke': 'failed', 'error_type': type(error).__name__, 'locations': locations}))
        sys.exit(1)
