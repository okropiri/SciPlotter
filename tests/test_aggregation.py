from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import uproot

from sciplotter_backend import aggregation, create_app, importer


class RootAggregationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root_path = Path(cls.directory.name) / 'events.root'
        index = np.arange(importer.ROOT_LOAD_ENTRY_LIMIT + 513)
        cls.arrays = {
            'x': ((index % 101) + 0.5) * (10.0 / 101),
            'y': (((index // 101) % 97) + 0.5) * (10.0 / 97),
            'channel': (index % 3).astype(np.int32),
            'pileup': (index % 11 == 0),
        }
        with uproot.recreate(cls.root_path) as root:
            root.mktree('events', {key: value.dtype for key, value in cls.arrays.items()})
            root['events'].extend(cls.arrays)
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.client = self.app.test_client()
        self.session_ids = []
        response = self.client.post('/api/data-importer/session', json={
            'kind': 'root',
            'path': str(self.root_path),
            'tree_path': 'events',
            'channel_field': 'channel',
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.session_id = response.get_json()['session_id']
        self.session_ids.append(self.session_id)
        self.chunk_size = patch.object(aggregation, 'ROOT_AGGREGATION_CHUNK_SIZE', 1024)
        self.chunk_size.start()
        self.addCleanup(self.chunk_size.stop)
        self.addCleanup(self._remove_sessions)

    def _remove_sessions(self):
        with aggregation._SESSION_LOCK:
            for session_id in self.session_ids:
                aggregation._DATASET_SESSIONS.pop(session_id, None)

    def aggregate(self, **overrides):
        query = {
            'session_id': self.session_id,
            'mode': '2d',
            'x_feature': 'x',
            'y_feature': 'y',
            'bins_x': 12,
            'bins_y': 9,
            'exclude_pileup': False,
        }
        query.update(overrides)
        response = self.client.post('/api/data-importer/aggregate', json=query)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def assert_histogram_matches(self, payload, mask):
        histogram = payload['histogram']
        expected, _, _ = np.histogram2d(
            self.arrays['x'][mask],
            self.arrays['y'][mask],
            bins=[histogram['x_edges'], histogram['y_edges']],
        )
        np.testing.assert_array_equal(histogram['counts'], expected.T)
        self.assertEqual(payload['meta']['selected_count'], int(mask.sum()))
        self.assertEqual(int(np.sum(histogram['counts'])), int(mask.sum()))
        self.assertEqual(payload['meta']['entry_count'], len(mask))
        self.assertTrue(payload['meta']['exact'])
        self.assertAlmostEqual(payload['stats']['x']['mean'], self.arrays['x'][mask].mean())
        self.assertAlmostEqual(payload['stats']['y']['mean'], self.arrays['y'][mask].mean())

    @staticmethod
    def geometry(kind='ellipse'):
        axes = {
            'x': {'type': 'linear', 'start': 0, 'end': 10},
            'y': {'type': 'linear', 'start': 0, 'end': 10},
        }
        if kind == 'polygon':
            return {
                'type': kind,
                'axes': axes,
                'points': [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
            }
        return {'type': kind, 'axes': axes, 'center': [0.5, 0.5], 'radius': [0.3, 0.2]}

    def test_root_load_is_sampled_but_session_covers_all_entries(self):
        response = self.client.post('/api/data-importer/preview', json={
            'kind': 'root', 'path': str(self.root_path),
        })
        self.assertEqual(response.status_code, 200)
        preview = response.get_json()['selected_tree']
        self.assertEqual(preview['entry_count'], len(self.arrays['x']))
        self.assertEqual(preview['sample_row_count'], importer.PREVIEW_ROW_LIMIT)
        self.assertTrue(preview['will_truncate_on_load'])
        response = self.client.post('/api/data-importer/load', json={
            'kind': 'root', 'path': str(self.root_path), 'tree_path': 'events',
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        loaded = response.get_json()
        self.session_ids.append(loaded['exact_aggregation']['session_id'])
        self.assertEqual(len(loaded['rows']), importer.ROOT_LOAD_ENTRY_LIMIT)
        self.assertTrue(loaded['exact_aggregation']['available'])
        self.assertEqual(loaded['meta']['dataset_session']['entry_count'], len(self.arrays['x']))

    def test_exact_1d_uses_all_entries_and_excludes_pileup(self):
        payload = self.aggregate(mode='1d', exclude_pileup=True, x_hist_range=[0, 10])
        expected, edges = np.histogram(
            self.arrays['x'][~self.arrays['pileup']], bins=12, range=(0, 10),
        )
        np.testing.assert_array_equal(payload['histogram']['counts'], expected)
        np.testing.assert_allclose(payload['histogram']['edges'], edges)
        self.assertEqual(payload['meta']['selected_count'], int((~self.arrays['pileup']).sum()))
        self.assertEqual(payload['meta']['entry_count'], len(self.arrays['x']))

    def test_exact_2d_without_geometry_preserves_all_entries(self):
        self.assert_histogram_matches(self.aggregate(), np.ones(len(self.arrays['x']), dtype=bool))

    def test_polygon_selection_matches_numpy_with_one_and_two_scan_passes(self):
        mask = (
            (self.arrays['x'] >= 2) & (self.arrays['x'] < 8)
            & (self.arrays['y'] >= 2) & (self.arrays['y'] < 8)
        )
        for fixed_range in (False, True):
            with self.subTest(fixed_range=fixed_range):
                ranges = {'x_hist_range': [0, 10], 'y_hist_range': [0, 10]} if fixed_range else {}
                payload = self.aggregate(selection_geometry=self.geometry('polygon'), **ranges)
                self.assert_histogram_matches(payload, mask)
                self.assertEqual(payload['meta']['scan_passes'], 1 if fixed_range else 2)

    def test_ellipse_selection_combines_channel_range_and_pileup_filters(self):
        mask = (
            (((self.arrays['x'] / 10 - 0.5) / 0.3) ** 2
             + ((self.arrays['y'] / 10 - 0.5) / 0.2) ** 2 <= 1)
            & (self.arrays['channel'] == 1)
            & ~self.arrays['pileup']
            & (self.arrays['x'] >= 4)
        )
        for fixed_range in (False, True):
            with self.subTest(fixed_range=fixed_range):
                ranges = {'x_hist_range': [0, 10], 'y_hist_range': [0, 10]} if fixed_range else {}
                payload = self.aggregate(
                    selection_geometry=self.geometry(),
                    channel_field='channel', channel_values=['1'],
                    exclude_pileup=True, x_filter_range=[4, None], **ranges,
                )
                self.assert_histogram_matches(payload, mask)

    def test_logarithmic_and_reversed_axes_use_normalized_screen_geometry(self):
        geometry = self.geometry()
        geometry['axes'] = {
            'x': {'type': 'log', 'start': 0.01, 'end': 10},
            'y': {'type': 'log', 'start': 10, 'end': 0.01},
        }
        normalized_x = (np.log10(self.arrays['x']) + 2) / 3
        normalized_y = (np.log10(self.arrays['y']) - 1) / -3
        mask = (
            ((normalized_x - 0.5) / 0.3) ** 2
            + ((normalized_y - 0.5) / 0.2) ** 2 <= 1
        )
        self.assert_histogram_matches(self.aggregate(selection_geometry=geometry), mask)

    def test_geometry_changes_do_not_reuse_an_unrelated_cached_histogram(self):
        baseline = self.aggregate()
        selected = self.aggregate(selection_geometry=self.geometry())
        cached = self.aggregate(selection_geometry=self.geometry())
        self.assertFalse(selected['meta']['cache_hit'])
        self.assertTrue(cached['meta']['cache_hit'])
        self.assertLess(selected['meta']['selected_count'], baseline['meta']['selected_count'])
        self.assertEqual(selected['histogram'], cached['histogram'])
        changed = self.geometry()
        changed['radius'] = [0.1, 0.1]
        smaller = self.aggregate(selection_geometry=changed)
        self.assertFalse(smaller['meta']['cache_hit'])
        self.assertLess(smaller['meta']['selected_count'], selected['meta']['selected_count'])

    def test_empty_selection_returns_explicit_empty_metadata(self):
        geometry = self.geometry()
        geometry['center'] = [3, 3]
        payload = self.aggregate(selection_geometry=geometry)
        self.assertEqual(payload['meta']['selected_count'], 0)
        self.assertIsNone(payload['histogram'])
        self.assertEqual(payload['notes'], ['No rows matched the requested filters.'])

    def test_invalid_geometry_is_reported_as_http_400(self):
        invalid_geometries = ['not an object', {'type': 'rectangle'}]
        for change in (
            {'radius': [0, 0.1]},
            {'center': [float('inf'), 0.5]},
            {'axes': {'x': {'type': 'log', 'start': 0, 'end': 10}, 'y': self.geometry()['axes']['y']}},
            {'axes': {'x': {'type': 'linear', 'start': 1, 'end': 1}, 'y': self.geometry()['axes']['y']}},
        ):
            geometry = copy.deepcopy(self.geometry())
            geometry.update(change)
            invalid_geometries.append(geometry)
        polygon = self.geometry('polygon')
        polygon['points'] = [[0, 0], [1, 1]]
        invalid_geometries.append(polygon)
        for geometry in invalid_geometries:
            with self.subTest(geometry=geometry):
                response = self.client.post('/api/data-importer/aggregate', json={
                    'session_id': self.session_id,
                    'mode': '2d', 'x_feature': 'x', 'y_feature': 'y',
                    'bins_x': 12, 'bins_y': 9, 'selection_geometry': geometry,
                })
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn('selection_geometry', response.get_json()['error'])

    def test_missing_session_is_reported_as_http_404(self):
        response = self.client.post('/api/data-importer/aggregate', json={
            'session_id': 'missing', 'mode': '1d', 'x_feature': 'x', 'bins_x': 12,
        })
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.get_json())

    def test_nonpositive_and_nonfinite_log_values_are_not_selected(self):
        geometry = aggregation._parse_selection_geometry({
            'type': 'ellipse',
            'axes': {
                'x': {'type': 'log', 'start': 0.1, 'end': 10},
                'y': {'type': 'linear', 'start': 0, 'end': 1},
            },
            'center': [0.5, 0.5], 'radius': [1, 1],
        })
        mask = aggregation._build_selection_geometry_mask(
            np.array([-1, 0, np.nan, np.inf, 1]),
            np.full(5, 0.5),
            geometry,
        )
        np.testing.assert_array_equal(mask, [False, False, False, False, True])

    def test_csv_preview_and_load_preserve_rows(self):
        path = Path(self.directory.name) / 'events.csv'
        path.write_text('x,y,channel\n1,2,0\n3,4,1\n', encoding='utf-8')
        for action in ('preview', 'load'):
            with self.subTest(action=action):
                response = self.client.post(f'/api/data-importer/{action}', json={
                    'kind': 'csv', 'path': str(path),
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                payload = response.get_json()
                self.assertEqual(payload['headers'], ['x', 'y', 'channel'])
                rows = payload['sample_rows'] if action == 'preview' else payload['rows']
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[1]['y'], '4')


if __name__ == '__main__':
    unittest.main()
