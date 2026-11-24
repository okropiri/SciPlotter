import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import uproot
from uproot.writing.identify import to_TAxis, to_TH2x

from sciplotter_backend import create_app, importer


class RootHistogramOverflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'timing.root'
        self.client = create_app().test_client()

    def write_histogram(self, *, delta, stored_variances=True):
        native = np.zeros((5, 6), dtype=np.int32)
        native[1:4, 1:5] = np.arange(1, 13).reshape(3, 4)
        native[0, 1] = 13
        variances = native.astype(np.float64) * 2 if stored_variances else None
        with uproot.recreate(self.path, compression=None) as file:
            file['timing'] = to_TH2x(
                None, 'Timing', native.ravel(), 91, 78, 156, 0, 0, 0, 0, 0,
                None if variances is None else variances.ravel(),
                to_TAxis('xaxis', 'Time', 4, 0, 4), to_TAxis('yaxis', 'TOT', 3, 0, 3),
            )
        with uproot.open(self.path) as file:
            key = file.key('timing')
            offset, size = key.data_cursor.index, key.fObjlen
        # Model a wrapped object header in a small fixture. The key keeps the actual byte count.
        with self.path.open('r+b') as file:
            file.seek(offset)
            file.write(struct.pack('>I', (size - 4 - delta) | uproot.const.kByteCountMask))

    def request(self, action):
        return self.client.post(f'/api/data-importer/{action}', json={
            'kind': 'root', 'path': str(self.path), 'histogram_path': 'timing',
        })

    def test_recovery_merges_whole_bins_and_preserves_totals(self):
        self.write_histogram(delta=128)
        with patch.object(importer, 'ROOT_BYTE_COUNT_BIT', 128), patch.object(importer, 'ROOT_RECOVERED_BINS_PER_AXIS', 2):
            preview = self.request('preview')
            response = self.request('load')
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertEqual(response.status_code, 200, response.get_json())
        loaded = response.get_json()
        meta = loaded['meta']
        self.assertEqual(preview.get_json()['selected_histogram']['import_rebin'], meta['import_rebin'])
        self.assertEqual(meta['import_rebin']['native_bin_counts'], [4, 3])
        self.assertEqual((meta['integral'], meta['flow_integral']), (78, 13))
        self.assertEqual(meta['variance_source'], 'stored_sumw2')
        self.assertEqual(loaded['histogram']['x_edges'], [0, 2, 4])
        self.assertEqual(loaded['histogram']['y_edges'], [0, 1, 3])
        self.assertEqual(loaded['histogram']['counts'], [[3, 7], [30, 38]])
        self.assertEqual(loaded['histogram']['variances'], [[6, 14], [60, 76]])
        self.assertAlmostEqual(loaded['stats']['x']['mean'], (15 * .5 + 18 * 1.5 + 21 * 2.5 + 24 * 3.5) / 78)

    def test_different_declared_size_is_still_rejected(self):
        self.write_histogram(delta=132)
        with patch.object(importer, 'ROOT_BYTE_COUNT_BIT', 128):
            for action in ('preview', 'load'):
                with self.subTest(action=action):
                    response = self.request(action)
                    self.assertEqual(response.status_code, 400, response.get_json())
                    self.assertIn('Cannot recover the oversized ROOT TH2I safely', response.get_json()['error'])
