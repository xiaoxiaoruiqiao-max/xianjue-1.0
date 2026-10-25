"""Failure cases found in the 2026-10-25 audit; fixtures never publish live data."""
import copy
from datetime import datetime, timedelta
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import scoring
import server
import update
from test_market import history, review


class EvidenceRegressions(unittest.TestCase):
    def test_conflicting_seal_statistics_are_quarantined(self):
        r = review()
        r['metrics'].update(limit_up=53, broken=18, seal_rate=64)
        r['conditions'][0]['status'] = 'fail'
        result = scoring.assess(r, r['date'], 'close')
        self.assertIsNone(result['score'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['missing'][0]['key'], 'sentiment')
        self.assertEqual(result['conditions'][0]['status'], 'unknown')
        self.assertTrue(any('来源统计冲突' in reason for reason in result['reasons']))
        with self.assertRaises(ValueError):
            scoring.validate_review(r, r['date'], 'close', strict=True)

    def test_whole_percent_rounding_and_truncation_are_tolerated(self):
        r = review()
        r['metrics'].update(limit_up=58, broken=14, seal_rate=80)
        self.assertTrue(scoring.assess(r, r['date'], 'close')['complete'])

    def test_pass_without_source_cannot_enable_add(self):
        r = review()
        r['conditions'][2].pop('source_ids', None)
        self.assertEqual(scoring.assess(r, r['date'], 'close')['actions']['add'], '等待确认')

    def test_new_review_requires_dated_sources_and_condition_evidence(self):
        r = review()
        for c in r['conditions']:
            c['source_ids'] = ['a']
        scoring.validate_review(r, r['date'], 'close', strict=True)
        r['conditions'][2]['source_ids'] = []
        with self.assertRaises(ValueError):
            scoring.validate_review(r, r['date'], 'close', strict=True)
        r['conditions'][2]['source_ids'] = ['a']
        r['sources'][0].pop('published_at')
        with self.assertRaises(ValueError):
            scoring.validate_review(r, r['date'], 'close', strict=True)

    def test_close_review_before_close_is_rejected(self):
        r = review()
        r['asof'] = '2026-09-09T10:00:00+08:00'
        r['sources'][0]['published_at'] = '2026-09-08T10:00:00+08:00'
        with self.assertRaises(ValueError):
            scoring.validate_review(r, r['date'], 'close')

    def test_midday_cutoff_uses_beijing_time(self):
        r = review()
        r['phase'] = 'midday'
        r['asof'] = '2026-09-09T06:00:00+00:00'  # 14:00 Beijing
        r['sources'][0]['published_at'] = '2026-09-08T10:00:00+08:00'
        with self.assertRaises(ValueError):
            scoring.validate_review(r, r['date'], 'midday')

    def test_risk_statistics_missing_is_explicit(self):
        r = review()
        result = scoring.assess(r, r['date'], 'close')
        self.assertEqual(result['risk_status'], 'unknown')
        r['metrics'].update(decline_ratio=.3, limit_down=2)
        self.assertEqual(scoring.assess(r, r['date'], 'close')['risk_status'], 'clear')
        r['metrics']['decline_ratio'] = .85
        self.assertEqual(scoring.assess(r, r['date'], 'close')['risk_status'], 'triggered')


class UpdateRegressions(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.folder = Path(self.temp.name)
        self.data_patch = patch.object(update, 'DATA', self.folder)
        self.data_patch.start()

    def tearDown(self):
        self.data_patch.stop()
        self.temp.cleanup()

    def test_invalid_ohlc_and_nonfinite_values_rejected(self):
        start = datetime(2025, 1, 1)
        rows = [f'{(start+timedelta(days=i)).date()},100,101,102,99,100,1000,3,1'
                for i in range(220)]
        for bad in ('NaN', 'Infinity', '-2', '0', '103'):
            changed = rows.copy()
            parts = changed[-1].split(',')
            parts[2] = bad
            changed[-1] = ','.join(parts)
            with self.subTest(close=bad), self.assertRaises(ValueError):
                update.parse_kline(json.dumps({'data': {'klines': changed}}))

    def test_zero_turnover_in_backup_is_a_mismatch(self):
        a = {k: {'close': 100, 'turnover': 1000} for k in update.INDICES}
        b = copy.deepcopy(a)
        b['sh']['turnover'] = 0
        self.assertFalse(update.backup_quotes_agree(a, b))

    def test_failed_noon_crosscheck_never_saves_snapshot(self):
        data = history()
        update.write_json(self.folder / 'indices.json', {'indices': {k: v[:-1] for k, v in data.items()}})
        quotes = {k: {**v[-1], 'asof': '2026-09-09T11:30:00+08:00'} for k, v in data.items()}
        backup = {k: {**q, 'close': q['close'] + 5} for k, q in quotes.items()}
        with patch.object(update, 'fetch_index', side_effect=OSError('primary down')), \
             patch.object(update, 'fetch', return_value=b'test'), \
             patch.object(update, 'parse_quotes', return_value=quotes), \
             patch.object(update, 'parse_sina', return_value=backup):
            result = update.update(datetime.fromisoformat('2026-09-09T12:30:00+08:00'))
        self.assertFalse(result['current_quotes_verified'])
        self.assertFalse((self.folder / 'snapshots' / '2026-09-09-midday.json').exists())

    def test_stale_primary_on_weekend_is_not_success(self):
        data = history()
        update.write_json(self.folder / 'indices.json', {'indices': data})
        update.write_json(self.folder / 'calendar.json', {
            'start': '2026-09-01', 'end': '2026-09-30',
            'trade_days': ['2026-09-09', '2026-09-10', '2026-09-11']})
        def fetched(key, end):
            return key, data[key], {'url': 'https://example.com'}, b'{}'
        stale = {k: {**v[-1], 'asof': '2026-09-09T15:00:00+08:00'} for k, v in data.items()}
        with patch.object(update, 'fetch_index', side_effect=fetched), \
             patch.object(update, 'fetch', return_value=b'test'), \
             patch.object(update, 'parse_quotes', return_value=stale):
            result = update.update(datetime.fromisoformat('2026-09-12T16:30:00+08:00'))
        self.assertNotEqual(result['state'], 'success')
        self.assertTrue(any('2026-09-11' in e for e in result['errors']))

    def test_weekend_backup_verifies_last_session_without_fabricating_sunday(self):
        data = history()
        update.write_json(self.folder / 'indices.json', {'indices': data})
        update.write_json(self.folder / 'calendar.json', {'end':'2026-09-30','trade_days':['2026-09-09']})
        quotes = {k: {**v[-1], 'asof':'2026-09-09T15:00:00+08:00'} for k,v in data.items()}
        before = (self.folder / 'indices.json').read_bytes()
        for q in quotes.values():
            q['close'] += .01
        with patch.object(update, 'fetch_index', side_effect=OSError('primary down')), \
             patch.object(update, 'fetch', return_value=b'test'), \
             patch.object(update, 'parse_quotes', return_value=quotes), \
             patch.object(update, 'parse_sina', return_value=quotes):
            result = update.update(datetime.fromisoformat('2026-09-12T16:30:00+08:00'))
        self.assertEqual(result['state'], 'success')
        self.assertEqual(result['verified_quote_date'], '2026-09-09')
        self.assertEqual(result['latest_date'], '2026-09-09')
        saved = update.read_json(self.folder / 'indices.json')
        self.assertEqual((self.folder / 'indices.json').read_bytes(), before)
        self.assertTrue(all(rows[-1]['date']=='2026-09-09' for rows in saved['indices'].values()))

    def test_mismatched_primary_dates_preserve_cache(self):
        data = history()
        update.write_json(self.folder / 'indices.json', {'indices': data})
        original = (self.folder / 'indices.json').read_bytes()
        def fetched(key, end):
            rows = data[key][:-1] if key == 'cyb' else data[key]
            return key, rows, {'url': 'https://example.com'}, b'{}'
        with patch.object(update, 'fetch_index', side_effect=fetched), \
             patch.object(update, 'fetch', return_value=b'test'), \
             patch.object(update, 'parse_quotes', return_value={}):
            result = update.update(datetime.fromisoformat('2026-09-12T16:30:00+08:00'))
        self.assertNotEqual(result['state'], 'success')
        self.assertEqual((self.folder / 'indices.json').read_bytes(), original)

    def test_rolling_window_keeps_saved_reviews(self):
        update.write_json(self.folder / 'indices.json', {'indices': history()})
        r = review()
        path = self.folder / 'reviews' / '2026-09-09-close.json'
        update.write_json(path, r)
        before = path.read_bytes()
        dash = update.build_dashboard(datetime.fromisoformat('2027-09-10T16:30:00+08:00'))
        self.assertEqual(dash['records'], [])
        self.assertEqual(path.read_bytes(), before)


class RequestRegressions(unittest.TestCase):
    def request(self, body, length=None):
        handler = object.__new__(server.Handler)
        handler.headers = {'Host': '127.0.0.1:18789', 'Origin': 'http://127.0.0.1:18789',
                           'X-Local-Request': 'position-guide',
                           'Content-Length': str(len(body)) if length is None else length}
        handler.path = '/api/update'
        handler.rfile = io.BytesIO(body)
        result = []
        handler.json = lambda status, value: result.append(status)
        with patch.object(server, 'start_update', return_value=False) as start:
            handler.do_POST()
            return result[0], start.call_count

    def test_invalid_json_types_return_400_without_starting_worker(self):
        for body in (b'[]', b'null', b'true', b'1', b'"bad"', b'{bad'):
            with self.subTest(body=body):
                self.assertEqual(self.request(body), (400, 0))

    def test_bad_content_lengths_return_400(self):
        for length in ('abc', '-1'):
            with self.subTest(length=length):
                self.assertEqual(self.request(b'{}', length), (400, 0))

    def test_oversized_request_returns_413(self):
        self.assertEqual(self.request(b'', '1025'), (413, 0))

    def test_holiday_with_missing_previous_close_still_catches_up(self):
        now = datetime.fromisoformat('2026-09-12T16:30:00+08:00')
        dashboard = {'calendar_end': '2026-12-31', 'trade_days': ['2026-09-10', '2026-09-11'],
                     'latest_date': '2026-09-10', 'records': []}
        with patch.object(server, 'now_cn', return_value=now), \
             patch.object(server, 'read_json', side_effect=[{}, dashboard]), \
             patch.object(server, 'worker', None), patch.object(server.subprocess, 'Popen') as spawn:
            self.assertTrue(server.start_update())
            spawn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
