import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from index import ingest, load_snapshot, validate


def fixture():
    days = [f'2022-06-{n:02}' for n in range(7, 17)]
    row = {'source_id': 'synthetic-photo', 'provider_dedup_key': 'synthetic-dedup',
           'photo_timestamp_ms': 1655336400000, 'recorded_offset_ms': 3600000,
           'item_creation_timestamp_ms': 1767268800000, 'utc_date': '2022-06-15',
           'recorded_offset_date': '2022-06-16', 'matching_days': ['2022-06-15'],
           'query_days': ['2022-06-16'], 'media_type': 'photo', 'timing_unresolved': False}
    audit = {'source_id': row['source_id'], 'filename': 'IMG-20220616-WA0001.jpg',
             'sha256': 'a'*64, 'bytes': 1000, 'provider_size_matches': True,
             'whatsapp_filename_hint': True, 'capture_date_present': False,
             'creation_or_digitization_date_present': False, 'timezone_present': False, 'gps_present': False, 'camera_present': False,
             'embedded_tags': {'IFD0:Orientation': 1}, 'originals_archived': False}
    return {'schema_version': 1, 'provider': 'google_photos', 'run_id': 'synthetic-run',
            'collected_at': '2026-11-04T12:00:00Z',
            'window': {'start': '2022-06-08', 'end': '2022-06-15', 'query_start': days[0], 'query_end': days[-1], 'policy': 'utc-or-recorded-offset-v1'},
            'records': [row], 'unresolved': [],
            'queries': [{'day': d, 'pages': 1, 'excluded_known_outside': 0, 'complete': True} for d in days], 'audits': [audit]}


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'private'/'photos.sqlite'
        self.data = fixture()

    def tearDown(self):
        self.temp.cleanup()

    def query(self, sql):
        with sqlite3.connect(self.db) as db:
            return db.execute(sql).fetchall()

    def test_private_index_preserves_sources_without_event_inference(self):
        result = ingest(self.db, self.data)
        self.assertEqual(result['event_links_created'], 0)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
        saved = json.loads(self.query('SELECT data_json FROM observations')[0][0])
        self.assertEqual(saved, self.data['records'][0])
        self.assertEqual(self.query('SELECT count(*) FROM event_links')[0][0], 0)
        self.assertEqual(self.query('SELECT records, originals_audited, audited_without_capture_time FROM review_groups'), [(1, 1, 1)])

    def test_replay_idempotent_but_conflicting_run_rejected(self):
        ingest(self.db, self.data)
        self.assertTrue(ingest(self.db, self.data)['already_present'])
        changed = copy.deepcopy(self.data)
        changed['collected_at'] = '2026-11-04T13:00:00Z'
        with self.assertRaises(ValueError):
            ingest(self.db, changed)
        self.assertEqual(self.query('SELECT count(*) FROM observations')[0][0], 1)

    def test_new_run_appends_not_overwrites(self):
        ingest(self.db, self.data)
        changed = copy.deepcopy(self.data)
        changed['run_id'] = 'second-run'
        changed['records'][0]['photo_timestamp_ms'] += 1000
        ingest(self.db, changed)
        self.assertEqual(self.query('SELECT count(*) FROM assets')[0][0], 1)
        self.assertEqual(self.query('SELECT count(*) FROM observations')[0][0], 2)
        original = json.loads(self.query("SELECT data_json FROM observations WHERE run_id='synthetic-run'")[0][0])
        self.assertEqual(original['photo_timestamp_ms'], 1655336400000)

    def test_rejects_wrong_date_math_creation_substitution_and_secret_fields(self):
        for mutate in [lambda d: d['records'][0].update(recorded_offset_date='2022-06-15'),
                       lambda d: d['records'][0].update(matching_days=['2026-01-01']),
                       lambda d: d['records'][0].update(thumb='https://example.test/private'),
                       lambda d: d['records'][0].update(provider_dedup_key={'thumb': 'https://example.test/private'}),
                       lambda d: d['records'][0].update(provider_dedup_key='https://example.test/private'),
                       lambda d: d['records'][0].update(source_id='https://example.test/private'),
                       lambda d: d['records'][0].update(photo_timestamp_ms=[]),
                       lambda d: d['records'][0].update(recorded_offset_ms=False),
                       lambda d: d.update(cookies='not-allowed'),
                       lambda d: d['queries'][0].update(complete=False),
                       lambda d: d['window'].update(start='20220608')]:
            d = copy.deepcopy(self.data)
            mutate(d)
            with self.assertRaises(ValueError):
                ingest(self.db, d)
            self.assertFalse(self.db.exists())

    def test_unresolved_evidence_is_retained_not_filled(self):
        row = copy.deepcopy(self.data['records'][0])
        row.update(source_id='unknown', photo_timestamp_ms=None, recorded_offset_ms=None, utc_date=None, recorded_offset_date=None, matching_days=[], timing_unresolved=True)
        self.data['unresolved'] = [row]
        ingest(self.db, self.data)
        saved = json.loads(self.query("SELECT data_json FROM observations WHERE source_id='unknown'")[0][0])
        self.assertIsNone(saved['photo_timestamp_ms'])
        self.assertEqual(saved['item_creation_timestamp_ms'], row['item_creation_timestamp_ms'])

    def test_creation_only_is_not_capture_evidence(self):
        audit = self.data['audits'][0]
        audit['embedded_tags'] = {'ExifIFD:CreateDate': '2024:01:01 12:00:00'}
        audit['creation_or_digitization_date_present'] = True
        ingest(self.db, self.data)
        self.assertEqual(self.query('SELECT audited_without_capture_time FROM review_groups'), [(1,)])
        bad = copy.deepcopy(self.data)
        bad['audits'][0]['capture_date_present'] = True
        with self.assertRaises(ValueError):
            validate(bad)

    def test_rejects_invalid_creation_times_and_nonstandard_json(self):
        for value in [float('nan'), float('inf'), float('-inf'), 10**1000, 253402300800000, 1.5, True]:
            data = copy.deepcopy(self.data)
            data['records'][0]['item_creation_timestamp_ms'] = value
            with self.assertRaises(ValueError):
                ingest(self.db, data)
            self.assertFalse(self.db.exists())
        for token in ['NaN', 'Infinity', '-Infinity']:
            with self.assertRaises(ValueError):
                load_snapshot('{"value":'+token+'}')

    def test_refuses_another_app_database(self):
        self.db.parent.mkdir(mode=0o700)
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE unrelated (x)')
        with self.assertRaises(ValueError):
            ingest(self.db, self.data)
        self.assertEqual(self.query('PRAGMA application_id')[0][0], 0)
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE type='table'"), [('unrelated',)])

    def test_transaction_rolls_back_failed_audit(self):
        ingest(self.db, self.data)
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON file_audits BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        changed = copy.deepcopy(self.data)
        changed['run_id'] = 'failed-run'
        with self.assertRaises(sqlite3.IntegrityError):
            ingest(self.db, changed)
        self.assertEqual(self.query('SELECT count(*) FROM runs')[0][0], 1)
        self.assertEqual(self.query('SELECT count(*) FROM observations')[0][0], 1)

    def test_refuses_symlink(self):
        target = self.root/'target'
        self.db.parent.mkdir(mode=0o700)
        self.db.symlink_to(target)
        with self.assertRaises(ValueError):
            ingest(self.db, self.data)
        self.assertFalse(target.exists())

    def test_duplicate_id_and_large_audit_rejected(self):
        self.data['records'] *= 2
        with self.assertRaises(ValueError):
            validate(self.data)
        data = fixture()
        data['audits'] *= 11
        with self.assertRaises(ValueError):
            validate(data)


if __name__ == '__main__':
    unittest.main()
