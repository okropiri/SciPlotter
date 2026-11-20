from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import uproot

from sciplotter_backend import aggregation, create_app, importer


class SplitRootImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name).resolve()
        self.client = create_app().test_client()
        self.first = self.write_tree('part2.root', x=[0.0, 1.0], channel=[0, 0])
        # The branch order may differ, and a uniquely matching TTree may have a different name.
        self.second = self.write_tree('part10.root', tree='pulses', channel=[1, 2, 2], x=[2.0, 3.0, 4.0])

    def write_tree(self, filename, *, tree='events', **columns):
        path = self.folder / filename
        arrays = {key: np.asarray(values) for key, values in columns.items()}
        with uproot.recreate(path) as root:
            root.mktree(tree, {key: array.dtype for key, array in arrays.items()}).extend(arrays)
        return str(path)

    def request(self, action, **params):
        return self.client.post(f'/api/data-importer/{action}', json={
            'kind': 'root', 'paths': [self.first, self.second], **params,
        })

    def remove_session(self, session_id):
        with aggregation._SESSION_LOCK:
            aggregation._DATASET_SESSIONS.pop(session_id, None)

    def test_picker_returns_all_parts_in_filename_order(self):
        with patch.object(importer, '_show_native_file_picker', return_value=[self.second, self.first]):
            response = self.request('pick-file')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['selected_paths'], [self.first, self.second])
        self.assertEqual(response.get_json()['selected_path'], self.first)

    def test_preview_and_exact_aggregation_span_every_part(self):
        with patch.object(importer, 'ROOT_LOAD_ENTRY_LIMIT', 3):
            response = self.request('preview')
            self.assertEqual(response.status_code, 200, response.get_json())
            selected = response.get_json()['selected_tree']
            self.assertEqual(selected['entry_count'], 5)
            self.assertEqual([part['tree_path'] for part in selected['parts']], ['events', 'pulses'])
            self.assertEqual([row['x'] for row in selected['sample_rows']], [0, 1, 2, 3, 4])
            self.assertTrue(selected['will_truncate_on_load'])

            response = self.request('load', tree_path='events', channel_field='channel')
        self.assertEqual(response.status_code, 200, response.get_json())
        loaded = response.get_json()
        self.assertEqual(loaded['source_paths'], [self.first, self.second])
        self.assertEqual([row['x'] for row in loaded['rows']], [0, 1, 2])
        self.assertEqual(loaded['meta']['entry_count'], 5)
        session = loaded['meta']['dataset_session']
        self.assertEqual(session['source_paths'], [self.first, self.second])
        session_id = session['session_id']
        self.addCleanup(self.remove_session, session_id)

        response = self.request('aggregate', session_id=session_id, mode='1d', x_feature='x',
                                bins_x=5, x_hist_range=[0, 5], exclude_pileup=False)
        self.assertEqual(response.status_code, 200, response.get_json())
        histogram = response.get_json()
        self.assertEqual(histogram['meta']['selected_count'], 5)
        self.assertEqual(histogram['histogram']['counts'], [1, 1, 1, 1, 1])

        response = self.request('session/channel-values', session_id=session_id, channel_field='channel')
        self.assertEqual(response.get_json()['values'], [0, 1, 2])
        response = self.request('session/feature-bounds', session_id=session_id, feature='x', channel_values=['2'])
        result = response.get_json()
        self.assertEqual((result['count'], result['min'], result['max']), (2, 3, 4))

    def test_mismatches_are_rejected_before_loading_rows_or_creating_a_session(self):
        bad = {
            'missing channel': self.write_tree('missing.root', x=[1.0]),
            'extra timestamp': self.write_tree('extra.root', x=[1.0], channel=[1], timestamp=[0]),
            'different type x': self.write_tree('type.root', x=[1], channel=[1]),
        }
        with patch.object(importer, '_read_root_tree_rows') as reader:
            with patch.object(aggregation, 'create_root_dataset_session') as create_session:
                for message, path in bad.items():
                    for action in ('preview', 'load', 'session'):
                        with self.subTest(action=action, path=path):
                            response = self.request(action, paths=[self.first, path], tree_path='events')
                            self.assertEqual(response.status_code, 400, response.get_json())
                            error = response.get_json()['error']
                            self.assertIn('Cannot load these ROOT files together', error)
                            self.assertIn(Path(path).name, error)
                            self.assertIn(message, error)
        reader.assert_not_called()
        create_session.assert_not_called()

    def test_duplicate_paths_histograms_and_mixed_formats_cannot_form_a_dataset(self):
        saved = self.folder / 'saved.root'
        with uproot.recreate(saved) as root:
            root['histogram'] = np.histogram([1.0, 2.0], bins=2)
        csv_path = self.folder / 'other.csv'
        csv_path.write_text('x\n1\n')
        for path, message in (
            (self.first, 'Select each file only once'),
            (str(saved), 'contains no TTrees'),
            (str(csv_path), 'Unsupported file type'),
        ):
            with self.subTest(path=path):
                response = self.request('preview', paths=[self.first, path])
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertIn(message, response.get_json()['error'])
