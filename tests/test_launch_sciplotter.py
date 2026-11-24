import io
import json
import unittest
from unittest.mock import patch

from sciplotter_backend import create_app, runtime
from scripts import launch_sciplotter as launcher


class LauncherVersionTests(unittest.TestCase):
    def test_health_uses_the_version_loaded_when_the_server_started(self):
        with patch.object(runtime, 'app_version', return_value='1.2.2'):
            client = create_app().test_client()
        with patch.object(runtime, 'app_version', return_value='1.2.3'):
            health = client.get('/health').get_json()
        self.assertEqual(health['app'], 'SciPlotter')
        self.assertEqual(health['version'], '1.2.2')

    def test_ready_requires_the_same_app_version(self):
        matching = {'app': 'SciPlotter', 'status': 'ok', 'version': '1.2.2'}
        cases = (
            ({'status': 'ok', 'pid': 100}, False),
            ({**matching, 'app': 'Other'}, False),
            ({**matching, 'version': '1.2.1'}, False),
            (matching, True),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                response = io.BytesIO(json.dumps(data).encode())
                with patch.object(launcher, 'APP_VERSION', '1.2.2'):
                    with patch.object(launcher.urllib.request, 'urlopen', return_value=response):
                        self.assertEqual(launcher.is_server_ready('127.0.0.1', 5001), expected)

    def test_busy_legacy_port_uses_an_alternate_port(self):
        with patch.object(launcher, 'is_server_ready', return_value=False):
            with patch.object(launcher, 'is_port_open', side_effect=lambda _, port: port == 5001):
                self.assertEqual(launcher.select_server_port('127.0.0.1', 5001), (5002, False))

    def test_matching_server_on_alternate_port_is_reused(self):
        with patch.object(launcher, 'is_server_ready', side_effect=lambda _, port: port == 5002):
            with patch.object(launcher, 'is_port_open', return_value=False):
                self.assertEqual(launcher.select_server_port('127.0.0.1', 5001), (5002, True))
