"""Synthetic data only. Run: python3 -m unittest -v"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import subprocess
import unittest
from unittest import mock

import history


def gpx(points):
    return ('<gpx xmlns="http://www.topografix.com/GPX/1/0"><trk><trkseg>' + ''.join(
        f'<trkpt lat="{lat}" lon="{lon}"><ele>12</ele><time>{timestamp}</time></trkpt>'
        for timestamp, lat, lon in points) + '</trkseg></trk></gpx>').encode()


T = '2020-01-01T12:00:00Z'
P = (T, '10.1', '20.2')


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.incoming = self.root / 'incoming'
        self.incoming.mkdir()
        self.google = self.root / 'Timeline.json'
        self.google.write_text(json.dumps({'semanticSegments': [], 'rawSignals': []}))

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, age=0):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return history.run(self.state, self.google, self.incoming, age)

    def query(self, sql):
        with sqlite3.connect(self.state / 'history.sqlite') as db:
            return db.execute(sql).fetchall()

    def test_google_all_types_and_utc_and_provenance(self):
        common = {'startTime': '2020-01-01T13:00:00+01:00', 'endTime': '2020-01-01T14:00:00+01:00'}
        segments = [
            {**common, 'visit': {'topCandidate': {'placeId': 'synthetic', 'probability': .8, 'placeLocation': {'latLng': '10.1°, 20.2°'}}}},
            {**common, 'activity': {'start': {'latLng': '10.1,20.2'}, 'end': {'latLng': '11,21'}, 'topCandidate': {'type': 'WALKING', 'probability': .6}}},
            {**common, 'timelinePath': [{'point': '10.1, 20.2', 'time': T}]},
            {**common, 'timelineMemory': {'trip': {'distanceFromOriginKms': 1}}},
        ]
        raw = {'semanticSegments': segments, 'rawSignals': [{'position': {'LatLng': '12,22', 'timestamp': T, 'accuracyMeters': 4}}]}
        self.google.write_text(json.dumps(raw))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM events')[0][0], 4)
        self.assertEqual(self.query('SELECT count(*) FROM observations')[0][0], 4)
        self.assertEqual(self.query('SELECT min(start_utc) FROM events')[0][0], '2020-01-01T12:00:00.000000Z')
        self.assertEqual(self.query("SELECT activity,confidence FROM events WHERE kind='activity'")[0], ('WALKING', .6))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM events')[0][0], 4)
        self.assertTrue(self.query('SELECT * FROM chronology LIMIT 1'))

    def test_google_scientific_notation_coordinates(self):
        self.assertEqual(history.coordinate('10.123°, -1.234E-4°'), (10.123, -0.0001234))
        self.google.write_text(json.dumps({'semanticSegments': [{'startTime': T, 'endTime': T, 'timelinePath': [{'point': '10.123°, -1.234E-4°', 'time': T}]}]}))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 1)

    def test_repeat_growth_and_overwrite_preserve_old_points(self):
        path = self.incoming / 'day.gpx'
        path.write_bytes(gpx([P]))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 1)
        path.write_bytes(gpx([P, ('2020-01-01T12:01:00Z', '10.2', '20.3')]))
        self.assertEqual(self.run_import(), 0)
        path.write_bytes(gpx([('2020-01-01T12:02:00Z', '10.3', '20.4')]))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 3)
        self.assertEqual(self.query('SELECT count(*) FROM assets WHERE source="gpslogger"')[0][0], 3)
        for (p,) in self.query('SELECT archived_path FROM assets'):
            self.assertTrue(Path(p).exists())

    def test_gpx_csv_dedup_and_enrichment_but_not_cross_source(self):
        (self.incoming / 'a.gpx').write_bytes(gpx([P]))
        (self.incoming / 'a.csv').write_text('time,lat,lon,elevation,accuracy,speed,timestamp_ms\n2020-01-01T12:00:00Z,10.1,20.2,12,3,1.2,1577880000000\n')
        self.google.write_text(json.dumps({'semanticSegments': [{'startTime': T, 'endTime': T, 'timelinePath': [{'point': '10.1,20.2', 'time': T}]}]}))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points WHERE source="gpslogger"')[0][0], 1)
        self.assertEqual(self.query('SELECT count(*) FROM observations WHERE role IN ("gpx","csv")')[0][0], 2)
        self.assertEqual(self.query('SELECT accuracy_m,speed_mps FROM points WHERE source="gpslogger"')[0], (3., 1.2))
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 2)

    def test_csv_semicolon_decimal_comma(self):
        (self.incoming / 'a.csv').write_text('time;lat;lon;accuracy\n2020-01-01T12:00:00Z;10,1;20,2;3,5\n')
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT accuracy_m FROM points')[0][0], 3.5)

    def test_partial_upload_rolls_back_and_retries(self):
        path = self.incoming / 'day.gpx'
        path.write_bytes(gpx([P])[:-15])
        self.assertEqual(self.run_import(), 1)
        self.assertEqual(self.query('SELECT count(*) FROM assets WHERE source="gpslogger"')[0][0], 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 0)
        path.write_bytes(gpx([P]))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM import_failures')[0][0], 0)

    def test_malformed_file_does_not_block_independent_files(self):
        (self.incoming / 'bad.csv').write_text('time,lat,lon\n2020-01-01T12:00:00Z,10,20')
        (self.incoming / 'good.gpx').write_bytes(gpx([P]))
        self.assertEqual(self.run_import(), 1)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 1)
        self.assertEqual(self.query('SELECT count(*) FROM import_failures')[0][0], 1)

    def test_invalid_coordinate_atomicity(self):
        (self.incoming / 'bad.gpx').write_bytes(gpx([P, (T, '999', '20')]))
        self.assertEqual(self.run_import(), 1)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 0)
        self.assertEqual(self.query('SELECT count(*) FROM observations')[0][0], 0)

    def test_recent_upload_deferred(self):
        (self.incoming / 'day.gpx').write_bytes(gpx([P]))
        self.assertEqual(self.run_import(age=300), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 0)
        self.assertEqual(json.loads((self.state / 'summary.json').read_text())['deferred_uploads'], 2)

    def test_symlink_input_rejected(self):
        secret = self.root / 'not-an-upload'
        secret.write_bytes(gpx([P]))
        (self.incoming / 'link.gpx').symlink_to(secret)
        self.assertEqual(self.run_import(), 1)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 0)

    def test_duplicate_filename_different_arrival_no_duplicate_points(self):
        for name in ['one.gpx', 'two.gpx']:
            (self.incoming / name).write_bytes(gpx([P]))
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM points')[0][0], 1)
        self.assertEqual(self.query('SELECT count(*) FROM arrivals WHERE filename LIKE "%.gpx"')[0][0], 2)

    def test_raw_snapshots_and_database_private(self):
        self.assertEqual(self.run_import(), 0)
        self.assertEqual((self.state.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.state / 'history.sqlite').stat().st_mode & 0o777), 0o600)
        p = next((self.state / 'raw-snapshots').iterdir())
        self.assertEqual((p.stat().st_mode & 0o777), 0o600)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), p.stem.split('-', 1)[1])

    def test_interrupted_snapshot_write_can_retry(self):
        with mock.patch('history.os.fsync', side_effect=OSError('simulated interruption')):
            self.assertEqual(self.run_import(), 1)
        self.assertEqual(list((self.state / 'raw-snapshots').iterdir()), [])
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(self.query('SELECT count(*) FROM assets')[0][0], 1)

    def test_incomplete_existing_snapshot_recovers(self):
        self.state.mkdir()
        directory = self.state / 'raw-snapshots'
        directory.mkdir()
        digest = hashlib.sha256(self.google.read_bytes()).hexdigest()
        archived = directory / f'google_timeline-{digest}.json'
        archived.write_bytes(b'truncated')
        self.assertEqual(self.run_import(), 0)
        self.assertEqual(archived.read_bytes(), self.google.read_bytes())
        self.assertEqual(len(list(directory.glob('*.corrupt-*'))), 1)

    def test_activation_keeps_timer_enabled_when_first_import_fails(self):
        # Execute the actual activation script with harmless stand-in commands.
        # Models a partial-upload ingestion failure without stopping retries.
        commands = self.root / 'commands'
        commands.mkdir()
        log = self.root / 'activation.log'
        systemctl = commands / 'systemctl'
        systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$ACTIVATION_LOG"\nif [ "$1" = start ]; then exit 1; fi\n')
        systemctl.chmod(0o700)
        journal = commands / 'journalctl'
        journal.write_text('#!/bin/sh\nexit 0\n')
        journal.chmod(0o700)
        env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH'], 'ACTIVATION_LOG': str(log)}
        result = subprocess.run(['sh', str(Path(__file__).parent / 'activate.sh')], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        entries = log.read_text().splitlines()
        self.assertLess(entries.index('enable --now life-atlas-history.timer'), entries.index('start life-atlas-history.service'))
        self.assertFalse(any('disable' in line or 'stop' in line for line in entries))

    def test_reject_dtd(self):
        (self.incoming / 'bad.gpx').write_bytes(b'<!DOCTYPE gpx [<!ENTITY x "hidden">]><gpx/>')
        self.assertEqual(self.run_import(), 1)


if __name__ == '__main__':
    unittest.main()
