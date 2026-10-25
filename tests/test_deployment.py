"""Exercise a fresh, relocated checkout in a real isolated server process."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


class FreshDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / 'tests' / '.tmp'
        parent.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='先觉 fresh copy ', dir=parent)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        for name in ('server.py', 'update.py', 'scoring.py'):
            shutil.copy2(ROOT / name, cls.folder / name)
        shutil.copytree(ROOT / 'public', cls.folder / 'public')
        (cls.folder / 'data').mkdir()
        for name in ('indices.json', 'calendar.json'):
            shutil.copy2(ROOT / 'data' / name, cls.folder / 'data' / name)
        shutil.copytree(ROOT / 'data' / 'reviews', cls.folder / 'data' / 'reviews')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            cls.port = probe.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.process = subprocess.Popen(
            [sys.executable, str(cls.folder / 'server.py'), '--port', str(cls.port)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise AssertionError('Fresh server exited before becoming ready')
            try:
                with urllib.request.urlopen(cls.base + '/api/health', timeout=1):
                    return
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        raise AssertionError('Fresh server did not become ready')

    @classmethod
    def stop_server(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            cls.process.wait(timeout=10)

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return json.load(response)

    def test_new_port_and_checkout_identity(self):
        health = self.get_json('/api/health')
        self.assertEqual(health['app'], 'position-guide')
        self.assertEqual(health['version'], '1.0.0')
        self.assertEqual(health['port'], self.port)
        self.assertEqual(Path(health['project_root']), self.folder.resolve())

    def test_missing_dashboard_is_built_without_local_configuration(self):
        data = self.get_json('/api/data')
        self.assertTrue((self.folder / 'data' / 'dashboard.json').is_file())
        self.assertFalse(data['schedule']['enabled'])
        self.assertNotIn('automation_id', data['schedule'])
        self.assertEqual(data['update']['state'], 'never')
        self.assertIn('records', data)
        self.assertIn('latest_date', data)

    def test_page_and_assets_are_served(self):
        with urllib.request.urlopen(self.base, timeout=5) as response:
            self.assertIn('先觉1.0', response.read().decode('utf-8'))
        for path in ('/app.js', '/style.css', '/favicon.svg'):
            with self.subTest(path=path), urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())

    def test_relocated_server_keeps_host_and_origin_protection(self):
        invalid_host = urllib.request.Request(self.base + '/api/health', headers={'Host': 'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(invalid_host)
        self.assertEqual(error.exception.code, 403)
        for origin, expected in ((self.base, 400), ('https://evil.example', 403)):
            request = urllib.request.Request(self.base + '/api/update', data=b'{"force":"bad"}',
                headers={'Origin': origin, 'X-Local-Request': 'position-guide', 'Content-Type': 'application/json'})
            with self.subTest(origin=origin), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, expected)

    def test_internal_files_are_not_exposed(self):
        for path in ('/server.py', '/data/indices.json', '/.git/config'):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
