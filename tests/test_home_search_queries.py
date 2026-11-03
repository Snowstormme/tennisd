import unittest
from datetime import date
from sqlalchemy import event, select
from tennisd import db
from tennisd.models import Match, Player, ProfileImage, Review, User
import test_app as fixture


class HomeSearchQueryTests(unittest.TestCase):
    tearDown = fixture.TennisdFlows.tearDown

    def setUp(self):
        fixture.TennisdFlows.setUp(self)
        with self.app.app_context():
            for i in range(32):
                user = User(username=f'query_member_{i:02}', display_name=f'Query member {i:02}',
                            email=f'fixture{i}@example.test', password_hash='unused')
                db.session.add(user)
                db.session.flush()
                db.session.add(ProfileImage(user_id=user.id, mime_type='image/png', image_data=b'large-image' * 1000))
                if i < 5:
                    winner = Player(id=f'query-w-{i}', name=f'Query Winner {i}', tour='ATP')
                    loser = Player(id=f'query-l-{i}', name=f'Query Loser {i}', tour='ATP')
                    match = Match(id=f'query-match-{i}', winner=winner, loser=loser, tour='ATP',
                                  tournament='Query Open', surface='Hard', level='A', round='F',
                                  status='finished', week_start=date(2020, 1, 1), score='6-0 6-0')
                    db.session.add_all([winner, loser, match])
                    db.session.add(Review(user=user, match=match, watched_on=date.today(),
                                          is_public=i < 4, body=f'query-review-{i}'))
            db.session.commit()

    def capture(self, path):
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)
        with self.app.app_context():
            engine = db.engine
        event.listen(engine, 'before_cursor_execute', record)
        try:
            response = self.client.get(path)
        finally:
            event.remove(engine, 'before_cursor_execute', record)
        self.assertEqual(response.status_code, 200)
        return response, statements

    def test_home_community_has_bounded_queries_and_excludes_avatar_bytes(self):
        response, statements = self.capture('/')
        self.assertLessEqual(len(statements), 9)
        self.assertFalse(any('image_data' in sql for sql in statements))
        for i in range(4):
            self.assertIn(f'Query Winner {i}'.encode(), response.data)
            self.assertIn(f'Query Loser {i}'.encode(), response.data)
            self.assertIn(f'/u/query_member_{i:02}/avatar'.encode(), response.data)
        self.assertNotIn(b'Query Winner 4', response.data)

    def test_member_search_pages_do_not_add_one_query_per_avatar(self):
        for page, expected in ((1, 30), (2, 2)):
            response, statements = self.capture(f'/search?q=query_member_&category=members&page={page}')
            self.assertEqual(len(statements), 5)
            self.assertFalse(any('image_data' in sql for sql in statements))
            self.assertEqual(response.data.count(b'loading="lazy"'), expected)
