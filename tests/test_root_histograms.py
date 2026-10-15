from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import uproot
from uproot.writing import identify

from sciplotter_backend import aggregation, create_app


class RootHistogramImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / 'histograms.root'
        cls.mixed_path = Path(cls.directory.name) / 'mixed.root'
        cls.unsupported_path = Path(cls.directory.name) / 'unsupported.root'
        cls.x_edges = np.array([0.0, 1.0, 3.0])
        cls.y_edges = np.array([-2.0, 0.0, 4.0, 5.0])
        cls.matrix = np.array([[1.0, 2.0, 3.0], [4.0, 0.0, 6.0]])
        with uproot.recreate(cls.path) as root:
            root['nested/two'] = (cls.matrix, cls.x_edges, cls.y_edges)
            root['one'] = (np.array([1.0, 2.0, 3.0]), np.array([0.0, 1.0, 3.0, 6.0]))
            root['empty'] = (np.zeros(2), np.array([0.0, 1.0, 2.0]))
            root['signed'] = (np.array([2.0, -1.0]), np.array([0.0, 1.0, 2.0]))
            root['invalid'] = (np.array([np.nan, 1.0]), np.array([0.0, 1.0, 2.0]))
            root['cycle'] = (np.array([1.0]), np.array([0.0, 1.0]))
            root['cycle'] = (np.array([7.0]), np.array([0.0, 1.0]))
            root['weighted'] = identify.to_TH1x(
                fName=None, fTitle='Weighted energy',
                data=np.array([2.0, 1.5, 2.5, 4.0, 3.0]),
                fEntries=12.0, fTsumw=8.0, fTsumw2=5.5,
                fTsumwx=23.75, fTsumwx2=91.375,
                fSumw2=np.array([0.5, 0.25, 1.25, 4.0, 9.0]),
                fXaxis=identify.to_TAxis(
                    fName='xaxis', fTitle='Energy (keV)',
                    fNbins=3, fXmin=0.0, fXmax=6.0,
                    fXbins=np.array([0.0, 1.0, 3.0, 6.0]),
                ),
            )
            for name, sumw2 in (
                ('signed_stored', [0.0, 4.0, 1.0, 0.0]),
                ('invalid_sumw2', [4.0, 1.0]),
            ):
                root[name] = identify.to_TH1x(
                    fName=None, fTitle='Signed weights',
                    data=np.array([0.0, 2.0, -1.0, 0.0]),
                    fEntries=3.0, fTsumw=1.0, fTsumw2=5.0,
                    fTsumwx=-0.5, fTsumwx2=-1.75, fSumw2=np.array(sumw2),
                    fXaxis=identify.to_TAxis(
                        fName='xaxis', fTitle='Energy', fNbins=2, fXmin=0.0, fXmax=2.0,
                    ),
                )
            root['invalid_flow'] = identify.to_TH1x(
                fName=None, fTitle='Invalid flow bin',
                data=np.array([np.nan, 1.0, 0.0]),
                fEntries=1.0, fTsumw=1.0, fTsumw2=1.0,
                fTsumwx=0.5, fTsumwx2=0.25, fSumw2=None,
                fXaxis=identify.to_TAxis(fName='xaxis', fTitle='', fNbins=1, fXmin=0.0, fXmax=1.0),
            )
        with uproot.recreate(cls.mixed_path) as root:
            root['image'] = (cls.matrix, cls.x_edges, cls.y_edges)
            root.mktree('events', {'x': 'float64', 'channel': 'int32'})
            root['events'].extend({'x': np.array([1.0, 2.0]), 'channel': np.array([0, 1], dtype=np.int32)})
        with uproot.recreate(cls.unsupported_path) as root:
            root['volume'] = (np.ones((2, 2, 2)), np.arange(3.0), np.arange(3.0), np.arange(3.0))
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.client = self.app.test_client()

    def request(self, action, path=None, **selection):
        return self.client.post(f'/api/data-importer/{action}', json={
            'kind': 'root', 'path': str(path or self.path), **selection,
        })

    def test_histogram_only_file_is_detected_and_lists_latest_objects_once(self):
        response = self.request('preview')
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload['supported'])
        self.assertEqual(payload['tree_options'], [])
        names = [item['path'] for item in payload['histogram_options']]
        self.assertEqual(names.count('cycle'), 1)
        self.assertIn('nested/two', names)
        self.assertEqual(payload['selected_histogram']['path'], 'cycle')

    def test_th2_bin_orientation_and_variable_edges_are_preserved(self):
        response = self.request('load', histogram_path='nested/two')
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        np.testing.assert_array_equal(payload['histogram']['counts'], self.matrix.T)
        np.testing.assert_array_equal(payload['histogram']['x_edges'], self.x_edges)
        np.testing.assert_array_equal(payload['histogram']['y_edges'], self.y_edges)
        self.assertEqual(payload['meta']['dimension'], 2)
        self.assertEqual(payload['meta']['integral'], 16.0)
        self.assertEqual(payload['rows'], [])
        self.assertNotIn('dataset_session', payload['meta'])
        self.assertAlmostEqual(payload['stats']['x']['mean'], (0.5 * 6 + 2.0 * 10) / 16)

    def test_weighted_contents_variances_titles_entries_and_flow_are_preserved(self):
        response = self.request('load', histogram_path='weighted')
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        histogram = payload['histogram']
        self.assertEqual(histogram['counts'], [1.5, 2.5, 4.0])
        self.assertEqual(histogram['variances'], [0.25, 1.25, 4.0])
        self.assertEqual(payload['meta']['variance_source'], 'stored_sumw2')
        self.assertEqual(histogram['edges'], [0.0, 1.0, 3.0, 6.0])
        self.assertEqual(payload['meta']['entry_count'], 12.0)
        self.assertEqual(payload['meta']['integral'], 8.0)
        self.assertEqual(payload['meta']['flow_integral'], 5.0)
        self.assertEqual(payload['meta']['title'], 'Weighted energy')
        self.assertEqual(payload['meta']['axes'][0]['title'], 'Energy (keV)')
        self.assertAlmostEqual(payload['stats']['mean'], 23.75 / 8)

    def test_preview_samples_bins_not_events(self):
        response = self.request('preview', histogram_path='nested/two')
        self.assertEqual(response.status_code, 200, response.get_json())
        selected = response.get_json()['selected_histogram']
        self.assertEqual(selected['bin_count'], 6)
        self.assertEqual(selected['nonzero_bin_count'], 5)
        self.assertEqual(len(selected['sample_bins']), 5)
        self.assertEqual(selected['sample_bins'][0], {'x_center': 0.5, 'y_center': -1.0, 'content': 1.0})
        self.assertNotIn('sample_rows', selected)

    def test_empty_histogram_can_be_previewed_and_loaded(self):
        preview = self.request('preview', histogram_path='empty').get_json()
        self.assertTrue(preview['supported'])
        self.assertEqual(preview['selected_histogram']['sample_bins'], [])
        loaded = self.request('load', histogram_path='empty').get_json()
        self.assertEqual(loaded['histogram']['counts'], [0.0, 0.0])
        self.assertIsNone(loaded['stats'])

    def test_signed_bins_are_not_converted_to_event_counts(self):
        loaded = self.request('load', histogram_path='signed').get_json()
        self.assertEqual(loaded['histogram']['counts'], [2.0, -1.0])
        self.assertTrue(loaded['meta']['has_negative_bins'])
        self.assertEqual(loaded['meta']['min_bin_content'], -1)
        self.assertIsNone(loaded['stats'])
        self.assertTrue(any('negative weights' in note for note in loaded['notes']))
        self.assertIsNone(loaded['histogram']['variances'])
        self.assertEqual(loaded['meta']['variance_source'], 'unavailable')

    def test_default_poisson_variances_are_explicit_not_claimed_to_be_stored(self):
        loaded = self.request('load', histogram_path='one').get_json()
        self.assertEqual(loaded['histogram']['variances'], [1, 2, 3])
        self.assertEqual(loaded['meta']['variance_source'], 'poisson_default')
        self.assertTrue(any('Poisson assumption' in note for note in loaded['notes']))

    def test_signed_stored_variances_are_not_replaced_by_poisson_errors(self):
        loaded = self.request('load', histogram_path='signed_stored').get_json()
        self.assertEqual(loaded['histogram']['counts'], [2, -1])
        self.assertEqual(loaded['histogram']['variances'], [4, 1])
        self.assertEqual(loaded['meta']['variance_source'], 'stored_sumw2')

    def test_malformed_sumw2_is_rejected_instead_of_defaulting_to_counts(self):
        response = self.request('load', histogram_path='invalid_sumw2')
        self.assertEqual(response.status_code, 400)
        self.assertIn('variances', response.get_json()['error'])

    def test_reloading_another_histogram_does_not_change_the_file(self):
        for name, integral in [('one', 6), ('nested/two', 16), ('cycle', 7)]:
            with self.subTest(name=name):
                response = self.request('load', histogram_path=name)
                self.assertEqual(response.status_code, 200, response.get_json())
                payload = response.get_json()
                self.assertEqual(payload['source_path'], str(self.path.resolve()))
                self.assertEqual(payload['meta']['histogram_path'], name)
                self.assertEqual(payload['meta']['integral'], integral)

    def test_mixed_file_keeps_tree_default_and_allows_explicit_histogram_selection(self):
        preview = self.request('preview', path=self.mixed_path).get_json()
        self.assertEqual(preview['selected_tree']['path'], 'events')
        self.assertEqual(preview['histogram_options'][0]['path'], 'image')
        image = self.request('preview', path=self.mixed_path, histogram_path='image').get_json()
        self.assertEqual(image['selected_histogram']['path'], 'image')
        response = self.request('load', path=self.mixed_path, tree_path='events', channel_field='channel')
        self.assertEqual(response.status_code, 200, response.get_json())
        tree = response.get_json()
        self.assertEqual(len(tree['rows']), 2)
        self.assertTrue(tree['exact_aggregation']['available'])
        with aggregation._SESSION_LOCK:
            aggregation._DATASET_SESSIONS.pop(tree['exact_aggregation']['session_id'], None)

    def test_unsupported_classes_are_visible_but_not_loadable(self):
        preview = self.request('preview', path=self.unsupported_path).get_json()
        self.assertFalse(preview['supported'])
        self.assertEqual(preview['histogram_options'][0]['class_name'], 'TH3D')
        self.assertFalse(preview['histogram_options'][0]['supported'])
        response = self.request('load', path=self.unsupported_path, histogram_path='volume')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Unsupported ROOT histogram class', response.get_json()['error'])

    def test_invalid_missing_and_ambiguous_selections_report_errors(self):
        for action, selection in [
            ('preview', {'histogram_path': 'missing'}),
            ('load', {'histogram_path': 'missing'}),
            ('preview', {'histogram_path': 'invalid'}),
            ('load', {'histogram_path': 'invalid'}),
            ('preview', {'histogram_path': 'one', 'tree_path': 'events'}),
            ('load', {'histogram_path': 'one', 'tree_path': 'events'}),
        ]:
            with self.subTest(action=action, selection=selection):
                response = self.request(action, **selection)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn('error', response.get_json())

    def test_nonfinite_flow_bins_are_rejected(self):
        for action in ['preview', 'load']:
            with self.subTest(action=action):
                response = self.request(action, histogram_path='invalid_flow')
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn('finite', response.get_json()['error'])

    def test_histogram_selection_requires_a_string(self):
        for action in ['preview', 'load']:
            for path in [123, False, [], {'path': 'one'}]:
                with self.subTest(action=action, path=path):
                    response = self.request(action, histogram_path=path)
                    self.assertEqual(response.status_code, 400, response.get_json())
                    self.assertEqual(response.get_json()['error'], 'histogram_path must be a string')


if __name__ == '__main__':
    unittest.main()
