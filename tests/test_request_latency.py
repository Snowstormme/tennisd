import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tennisd import create_app, db
import test_app as fixture


class RequestLatencyTests(unittest.TestCase):
    tearDown = fixture.TennisdFlows.tearDown

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'latency-fixture',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(Path(self.temporary.name) / 'test.db'),
            'SQLALCHEMY_ENGINE_OPTIONS': {'pool_size': 1, 'max_overflow': 0, 'pool_timeout': .01},
        })
        self.client = self.app.test_client()

    def test_processing_time_header_has_milliseconds_without_slow_log(self):
        with patch('tennisd.perf_counter', side_effect=[1, 1.125]), patch.object(self.app.logger, 'warning') as log:
            response = self.client.get('/healthz')
        self.assertEqual(response.headers['Server-Timing'], 'app;dur=125.0')
        self.assertEqual(response.status_code, 200)
        log.assert_not_called()

    def test_slow_unmatched_request_log_excludes_path_query_and_headers(self):
        with patch('tennisd.perf_counter', side_effect=[1, 3.5]), self.assertLogs(self.app.logger, level='WARNING') as log:
            response = self.client.get('/private-name?token=secret-query', headers={'Authorization': 'secret-header'})
        self.assertEqual(response.status_code, 404)
        message = '\n'.join(log.output)
        self.assertIn('endpoint=unmatched status=404 duration_ms=2500.0', message)
        self.assertIn(response.headers['X-Request-ID'], message)
        for secret in ('private-name', 'secret-query', 'secret-header'):
            self.assertNotIn(secret, message)

    def test_real_pool_exhaustion_is_retryable_and_recovers_after_release(self):
        for path in ('/readyz', '/'):
            with self.app.app_context():
                with db.engine.connect():
                    with self.assertLogs(self.app.logger, level='ERROR'):
                        response = self.client.get(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.headers['Retry-After'], '10')
                    self.assertIn('no-store', response.headers['Cache-Control'])
                    self.assertIn('Server-Timing', response.headers)
                    self.assertNotIn(b'QueuePool', response.data)
                    if path == '/readyz':
                        self.assertEqual(response.json['status'], 503)
                self.assertEqual(self.client.get('/readyz').status_code, 200)

    def test_render_pool_wait_is_five_seconds_without_growing_connection_limit(self):
        environment = {'APP_ENV': 'production', 'SECRET_KEY': 'x' * 64,
                       'DATABASE_URL': 'postgresql://fixture:unused@example.test/db',
                       'RENDER_EXTERNAL_HOSTNAME': 'tennisd.onrender.com', 'REGISTRATION_ENABLED': 'false'}
        with patch.dict(os.environ, environment, clear=True):
            app = create_app({'TESTING': True})
        with app.app_context():
            self.assertEqual(db.engine.pool.timeout(), 5)
            self.assertEqual(db.engine.pool.size(), 2)
            self.assertEqual(app.config['SQLALCHEMY_ENGINE_OPTIONS']['max_overflow'], 1)
            db.engine.dispose()
