import unittest

from flask import Flask, session

from .. import rabbit_plugins as plugin


class AdultRatingTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('adult-rating-test')
        self.app.secret_key = 'test-only'

        class Gateway:
            def __init__(self):
                self.settings = {}
                self.allowed = True
                self.book = {
                    'id': 12, 'library_id': 3, 'series_name': '테스트 작품',
                    'books_lv': 'r18', 'genre': '', 'tags': '',
                }

            def fetch_one(self, query, params=()):
                if 'FROM books' in query:
                    return self.book if params == (12,) else None
                if 'FROM user_category_permissions' in query:
                    return {'allowed': 1} if self.allowed else None
                return None

            def fetch_all(self, query, params=()):
                self_query = query.lower()
                if 'from settings' not in self_query:
                    return []
                return [{'key': key, 'value': value} for key, value in self.settings.items()]

            def set_setting(self, key, value):
                self.settings[key] = value

            def execute(self, query, params=()):
                self.settings.pop(params[0], None)

        self.gateway = Gateway()
        self.provider = object.__new__(plugin.RabbitPluginsMetadataProvider)
        self.provider.get_db_gateway = lambda db_type: self.gateway
        self.context = {'book_id': 12, 'library_id': 3, 'series_name': '테스트 작품'}

    def _session(self, user_id=1, role='admin', adult_access=1, rating_max=20):
        session.update(user_id=user_id, role=role, has_adult_access=adult_access,
                       content_rating_max=rating_max)

    def test_ratings_are_saved_per_user_and_aggregated(self):
        with self.app.test_request_context('/'):
            self._session()
            self.assertEqual(self.provider.get_rating_widget_data('adult', self.context)['count'], 0)
            result = self.provider.submit_rating('adult', self.context, 4.5)
            self.assertEqual((result['count'], result['average'], result['my_rating']), (1, 4.5, 4.5))
            self._session(user_id=2, role='user')
            result = self.provider.submit_rating('adult', self.context, 3)
            self.assertEqual((result['count'], result['average'], result['my_rating']), (2, 3.8, 3.0))
            self._session()
            self.assertEqual(self.provider.get_rating_widget_data('adult', self.context)['my_rating'], 4.5)
            self.assertEqual(len(self.gateway.settings), 2)

    def test_sort_export_uses_current_user_not_average_or_another_user(self):
        with self.app.test_request_context('/'):
            self._session()
            self.provider.submit_rating('adult', self.context, 4.5)
            self._session(user_id=2)
            self.provider.submit_rating('adult', self.context, 3)
            self.assertEqual(list(self.provider.get_sort_ratings('adult')['ratings'].values()), [3])
            self._session()
            self.assertEqual(list(self.provider.get_sort_ratings('adult')['ratings'].values()), [4.5])

    def test_denied_access_and_invalid_rating_never_write(self):
        with self.app.test_request_context('/'):
            self._session(adult_access=0)
            self.assertFalse(self.provider.submit_rating('adult', self.context, 5)['success'])
            self._session(rating_max=0)
            self.assertFalse(self.provider.submit_rating('adult', self.context, 5)['success'])
            self._session()
            self.assertFalse(self.provider.submit_rating('adult', {**self.context, 'library_id': 4}, 5)['success'])
            self.assertFalse(self.provider.submit_rating('adult', self.context, 4.25)['success'])
            self._session(role='user')
            self.gateway.allowed = False
            self.assertFalse(self.provider.submit_rating('adult', self.context, 5)['success'])
            self.assertEqual(self.gateway.settings, {})

    def test_cancel_removes_only_current_user_and_is_idempotent(self):
        with self.app.test_request_context('/'):
            self._session()
            self.provider.submit_rating('adult', self.context, 4.5)
            self._session(user_id=2)
            self.provider.submit_rating('adult', self.context, 3)
            self._session()
            result = self.provider.cancel_rating('adult', self.context)
            self.assertEqual((result['count'], result['average'], result['my_rating']), (1, 3, None))
            self.assertEqual(len(self.gateway.settings), 1)
            self.assertTrue(self.provider.cancel_rating('adult', self.context)['success'])
            self._session(adult_access=0)
            self.assertFalse(self.provider.cancel_rating('adult', self.context)['success'])
            self.assertEqual(len(self.gateway.settings), 1)


if __name__ == '__main__':
    unittest.main()
