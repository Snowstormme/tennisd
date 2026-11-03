import unittest
import re
from unittest.mock import patch
from flask import abort
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from tennisd import db
from tennisd.security import limit_action
import test_app as fixture


class ErrorResponseTests(unittest.TestCase):
    tearDown=fixture.TennisdFlows.tearDown

    def setUp(self):
        fixture.TennisdFlows.setUp(self)
        self.app.config['PROPAGATE_EXCEPTIONS']=False
        @self.app.get('/_errors/<int:code>')
        @self.app.get('/api/_errors/<int:code>')
        def sample(code):abort(code,description='SECRET_INTERNAL_DETAIL')
        @self.app.get('/_limited')
        def limited():
            limit_action('fixture-limit','fixture',2,60)
            return 'ok'

    def test_html_and_api_errors_have_safe_body_ids_and_no_cache(self):
        for prefix in ('/_errors/','/api/_errors/'):
            for code in (400,403,404,405,413,429,500,503):
                with self.assertLogs(self.app.logger,level='ERROR') if code>=500 else self.subTest(code=code):
                    r=self.client.get(prefix+str(code))
                self.assertEqual(r.status_code,code)
                self.assertNotIn(b'SECRET_INTERNAL_DETAIL',r.data)
                self.assertIn('no-store',r.headers['Cache-Control'])
                self.assertEqual(r.headers['Vercel-CDN-Cache-Control'],'no-store')
                self.assertEqual(r.headers['X-Robots-Tag'],'noindex, nofollow')
                self.assertRegex(r.headers['X-Request-ID'],r'^[a-f0-9]{24}$')
                if prefix.startswith('/api'):
                    self.assertEqual(r.json['request_id'],r.headers['X-Request-ID'])
                    self.assertEqual(r.json['status'],code)
                else:self.assertIn(r.headers['X-Request-ID'].encode(),r.data)

    def test_method_and_upload_limits_preserve_protocol_headers(self):
        r=self.client.get('/logout')
        self.assertEqual(r.status_code,405);self.assertIn('POST',r.headers['Allow'])
        self.assertIn(b'different request method',r.data)
        self.app.config['MAX_CONTENT_LENGTH']=100
        r=self.client.post('/register',data={'username':'x'*200})
        self.assertEqual(r.status_code,413);self.assertIn(b'too large',r.data)

    def test_sliding_limit_reports_real_wait_without_extending_it(self):
        with patch('tennisd.security.time.time',return_value=1000):
            self.assertEqual(self.client.get('/_limited').status_code,200)
        with patch('tennisd.security.time.time',return_value=1010):
            self.assertEqual(self.client.get('/_limited').status_code,200)
        with patch('tennisd.security.time.time',return_value=1020):
            r=self.client.get('/_limited')
            self.assertEqual(r.status_code,429);self.assertEqual(r.headers['Retry-After'],'40')
            self.assertIn(b'40 seconds',r.data)
        with patch('tennisd.security.time.time',return_value=1059):
            self.assertEqual(self.client.get('/_limited').headers['Retry-After'],'1')
        with patch('tennisd.security.time.time',return_value=1060):
            self.assertEqual(self.client.get('/_limited').status_code,200)

    def test_database_outage_does_not_requery_for_error_navigation(self):
        with self.client.session_transaction() as session:session['_user_id']='999:0'
        with self.app.app_context():engine=db.engine
        queries=[]
        def fail(conn,cursor,statement,params,context,many):
            queries.append(statement)
            raise OperationalError('SECRET_SQL',{},Exception('SECRET_DATABASE_PASSWORD'))
        event.listen(engine,'before_cursor_execute',fail)
        try:
            with self.assertLogs(self.app.logger,level='ERROR'):
                r=self.client.get('/readyz')
            self.assertEqual(r.status_code,500)
            self.assertTrue(r.is_json);self.assertEqual(len(queries),1)
            self.assertNotIn(b'SECRET',r.data)
            # A normal page attempts auth SQL while the database is down; its
            # fallback HTML must not try to render database-dependent nav again.
            queries.clear()
            with self.assertLogs(self.app.logger,level='ERROR'):
                r=self.client.get('/settings')
            self.assertEqual(r.status_code,500);self.assertEqual(len(queries),1)
            self.assertIn(b'Reference:',r.data);self.assertNotIn(b'SECRET',r.data)
            stylesheet=re.search(r'href="([^"]+)"',r.get_data(as_text=True)).group(1)
            with self.client.get(stylesheet) as css:
                self.assertEqual(css.status_code,200)
            self.assertEqual(len(queries),1)
        finally:event.remove(engine,'before_cursor_execute',fail)
        self.assertEqual(self.client.get('/readyz').status_code,200)
