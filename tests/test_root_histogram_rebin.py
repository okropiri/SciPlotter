import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


class RootHistogramRebinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which('node')
        if node:
            cls.command = [node, '-e']
            cls.node = True
        elif sys.platform == 'darwin' and Path('/usr/bin/osascript').is_file():
            cls.command = ['/usr/bin/osascript', '-l', 'JavaScript', '-e']
            cls.node = False
        else:
            raise unittest.SkipTest('Frontend rebin tests require Node.js or macOS JavaScriptCore')
        html = (Path(__file__).resolve().parents[1] / 'static' / 'histogram.html').read_text(encoding='utf-8')
        helpers = []
        for name in (
            'buildRootHistogramRebinView', 'buildRootHistogramAxisView',
            'validateRootHistogramDisplay', 'getRootHistogramLayoutRange',
            'normalizeHistogramBinCounts', 'applyCumulativeHistogramCounts',
            'buildPreComputed1DTrace', 'buildExactProjectionGrid',
            'buildSelectionProjectionPanelStateFromExactHistogram',
            'collectTraceBounds', 'mergeBounds', 'axisTypeIsUsable', 'getHistogramAutofitBounds',
            'captureCurrentRanges',
        ):
            start = html.index(f'function {name}(')
            end = html.index('\n        function ', start + 1)
            helpers.append(html[start:end])
        cls.helper = '\n'.join(helpers)

    def evaluate(self, expression):
        code = self.helper + '\nconst result = JSON.stringify(' + expression + ');'
        code += '\nconsole.log(result);' if self.node else '\nresult;'
        result = subprocess.run(self.command + [code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def one(self, counts=None):
        return {
            'rows': [], 'notes': [],
            'meta': {
                'dimension': 1, 'integral': 8, 'entry_count': 12, 'flow_integral': 5, 'has_negative_bins': False,
                'axes': [{'title': 'Energy', 'bin_count': 3, 'edges': [0, 1, 3, 6], 'min': 0, 'max': 6, 'labels': ['A', 'B', 'C']}],
            },
            'histogram': {'counts': counts or [1.5, 2.5, 4], 'variances': [0.25, 1.25, 4]},
        }

    def test_weighted_variable_bins_variances_and_labels_are_merged_without_mutation(self):
        source = json.dumps(self.one())
        result = self.evaluate(f"""(() => {{
            const source = {source};
            const before = JSON.stringify(source);
            const view = buildRootHistogramRebinView(source, {{binsX: 2}});
            return {{view, unchanged: JSON.stringify(source) === before}};
        }})()""")
        view = result['view']
        self.assertTrue(result['unchanged'])
        self.assertEqual(view['histogram']['counts'], [1.5, 6.5])
        self.assertEqual(view['histogram']['variances'], [0.25, 5.25])
        self.assertEqual(view['histogram']['edges'], [0, 1, 6])
        self.assertEqual(view['meta']['axes'][0]['labels'], ['A', 'B + C'])
        self.assertEqual(view['meta']['integral'], 8)
        self.assertEqual(view['meta']['flow_integral'], 5)
        self.assertTrue(any('flow-bin sum: 5 (not plotted)' in note for note in view['notes']))
        self.assertAlmostEqual(view['stats']['mean'], 23.75 / 8)

    def test_th2_orientation_uneven_groups_and_variance_sums_are_preserved(self):
        source = {
            'rows': [], 'notes': [],
            'meta': {
                'dimension': 2, 'integral': 21, 'entry_count': 21, 'has_negative_bins': False,
                'axes': [
                    {'bin_count': 3, 'edges': [0, 1, 3, 6], 'min': 0, 'max': 6},
                    {'bin_count': 2, 'edges': [0, 2, 5], 'min': 0, 'max': 5},
                ],
            },
            'histogram': {'counts': [[1, 2, 3], [4, 5, 6]], 'variances': [[1, 2, 3], [4, 5, 6]]},
        }
        view = self.evaluate(f'buildRootHistogramRebinView({json.dumps(source)}, {{binsX:2, binsY:1}})')
        self.assertEqual(view['histogram']['counts'], [[5, 16]])
        self.assertEqual(view['histogram']['variances'], [[5, 16]])
        self.assertEqual(view['histogram']['x_edges'], [0, 1, 6])
        self.assertEqual(view['histogram']['y_edges'], [0, 5])
        self.assertEqual(view['meta']['integral'], 21)
        self.assertEqual(view['rows'], [])

    def test_ranges_snap_outward_and_available_bins_are_reported(self):
        view = self.evaluate(f'buildRootHistogramRebinView({json.dumps(self.one())}, {{binsX:3, xRange:[2.9,1.1]}})')
        self.assertEqual(view['histogram']['edges'], [1, 3])
        self.assertEqual(view['histogram']['counts'], [2.5])
        self.assertEqual(view['histogram']['variances'], [1.25])
        self.assertTrue(any('limited to available native bins' in note for note in view['notes']))

    def test_empty_signed_and_missing_variances_remain_valid(self):
        source = self.one([0, 0, 0])
        source['histogram']['variances'] = None
        empty = self.evaluate(f'buildRootHistogramRebinView({json.dumps(source)}, {{binsX:1}})')
        self.assertEqual(empty['histogram']['counts'], [0])
        self.assertIsNone(empty['histogram']['variances'])
        self.assertIsNone(empty['stats'])
        source = self.one([2, -4, 1])
        source['meta']['has_negative_bins'] = True
        signed = self.evaluate(f'buildRootHistogramRebinView({json.dumps(source)}, {{binsX:2}})')
        self.assertEqual(signed['histogram']['counts'], [2, -3])
        self.assertEqual(signed['meta']['integral'], -1)
        self.assertTrue(signed['meta']['has_negative_bins'])
        self.assertIsNone(signed['stats'])

    def test_invalid_bin_counts_and_nonoverlapping_ranges_raise_errors(self):
        result = self.evaluate(f"""(() => {{
            const source = {json.dumps(self.one())};
            return [{{binsX:0}}, {{binsX:4}}, {{binsX:1.5}}, {{binsX:NaN}},
                {{binsX:1,xRange:[9,10]}}, {{binsX:1,xRange:[2,2]}}].map(options => {{
                    try {{ buildRootHistogramRebinView(source, options); return false; }}
                    catch (error) {{ return !!error.message; }}
                }});
        }})()""")
        self.assertEqual(result, [True] * 6)

    def test_probability_percent_density_and_cumulative_preserve_raw_bins(self):
        result = self.evaluate("""(() => {
            const counts = [1.5, 2.5, 4], edges = [0, 1, 3, 6];
            const before = JSON.stringify({counts, edges});
            const traces = ['', 'probability', 'percent', 'density'].map(histnorm =>
                buildPreComputed1DTrace(counts, edges, {nativeHistogram:true, histnorm}));
            const cdf = buildPreComputed1DTrace(counts, edges,
                {nativeHistogram:true, histnorm:'density', cumulative:true});
            const horizontal = buildPreComputed1DTrace(counts, edges,
                {nativeHistogram:true, histnorm:'density', orientation:'h'});
            return {traces, cdf, horizontal, unchanged:JSON.stringify({counts, edges}) === before};
        })()""")
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['traces'][0]['y'], [1.5, 2.5, 4])
        self.assertEqual(result['traces'][1]['y'], [0.1875, 0.3125, 0.5])
        self.assertEqual(sum(result['traces'][2]['y']), 100)
        density = result['traces'][3]
        self.assertAlmostEqual(sum(value * width for value, width in zip(density['y'], density['width'])), 1)
        self.assertEqual(result['cdf']['y'], [0.1875, 0.5, 1])
        self.assertEqual(result['horizontal']['x'], density['y'])
        self.assertEqual(density['customdata'][1], [1, 3, 2.5])

    def test_invalid_probability_is_rejected_not_relabelled_as_counts(self):
        result = self.evaluate("""(() => {
            return [[0,0], [2,-2], [2,-1]].map(counts => {
                try {
                    buildPreComputed1DTrace(counts, [0,1,2],
                        {nativeHistogram:true, histnorm:'probability', cumulative:true});
                    return false;
                } catch (error) { return error instanceof RangeError; }
            });
        })()""")
        self.assertEqual(result, [True, True, True])
        signed = self.evaluate("buildPreComputed1DTrace([2,-2], [0,1,2], {nativeHistogram:true, cumulative:true})")
        self.assertEqual(signed['y'], [2, 0])

    def test_density_numeric_overflow_is_reported(self):
        result = self.evaluate("""(() => {
            try {
                buildPreComputed1DTrace([1], [0, 1e-320], {nativeHistogram:true, histnorm:'density'});
                return false;
            } catch (error) { return error instanceof RangeError; }
        })()""")
        self.assertTrue(result)

    def test_statistics_do_not_shift_when_only_rebin_resolution_changes(self):
        source = self.one()
        result = self.evaluate(f"""(() => {{
            const source = {json.dumps(source)};
            return [1,2,3].map(binsX => buildRootHistogramRebinView(source, {{binsX}}).stats);
        }})()""")
        self.assertEqual(result[0], result[1])
        self.assertEqual(result[1], result[2])
        self.assertAlmostEqual(result[0]['mean'], 23.75 / 8)
        cropped = self.evaluate(f'buildRootHistogramRebinView({json.dumps(source)}, {{binsX:1,xRange:[1,6]}}).stats')
        self.assertAlmostEqual(cropped['mean'], (2.5 * 2 + 4 * 4.5) / 6.5)

    def test_nonfinite_native_contents_are_rejected(self):
        self.assertTrue(self.evaluate("""(() => {
            try {
                buildPreComputed1DTrace([NaN,1], [0,1,2], {nativeHistogram:true});
                return false;
            } catch (error) { return error instanceof RangeError; }
        })()"""))

    def test_swap_transposes_counts_variances_edges_and_statistics_without_mutation(self):
        source = {
            'meta': {'dimension': 2, 'axes': [{'title': 'Energy'}, {'title': 'Time'}]},
            'stats': {'x': {'mean': 2}, 'y': {'mean': 3}},
            'histogram': {
                'counts': [[1, 2, 3], [4, 5, 6]], 'variances': [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                'x_edges': [0, 1, 3, 6], 'y_edges': [0, 2, 5],
                'x_centers': [0.5, 2, 4.5], 'y_centers': [1, 3.5],
            },
        }
        result = self.evaluate(f"""(() => {{
            const source = {json.dumps(source)}, before = JSON.stringify(source);
            const view = buildRootHistogramAxisView(source, true);
            return {{view, unchanged:JSON.stringify(source) === before}};
        }})()""")
        view = result['view']
        self.assertTrue(result['unchanged'])
        self.assertEqual(view['histogram']['counts'], [[1, 4], [2, 5], [3, 6]])
        self.assertEqual(view['histogram']['variances'], [[0.1, 0.4], [0.2, 0.5], [0.3, 0.6]])
        self.assertEqual(view['histogram']['x_edges'], [0, 2, 5])
        self.assertEqual(view['meta']['axes'][0]['title'], 'Time')
        self.assertEqual(view['stats']['x']['mean'], 3)

    def test_log_layout_ranges_convert_units_without_altering_input(self):
        ranges = self.evaluate("""[
            getRootHistogramLayoutRange([1,100], 'linear', true),
            getRootHistogramLayoutRange([0,2], 'log', false),
            getRootHistogramLayoutRange([2,0], 'log', true),
            getRootHistogramLayoutRange([0,100], 'linear', true)
        ]""")
        self.assertEqual(ranges, [[0, 2], [1, 100], [2, 0], None])

    def test_invalid_display_bounds_are_explicitly_rejected(self):
        result = self.evaluate("""[
            {xMin:1}, {xMin:2,xMax:2}, {xMin:0,xMax:2,logX:true},
            {yMin:-2,yMax:4,logY:true}, {zMin:2,zMax:1}, {zMin:-1,logZ:true}
        ].map(options => {
            try { validateRootHistogramDisplay(options); return false; }
            catch (error) { return error instanceof RangeError; }
        })""")
        self.assertEqual(result, [True] * 6)
        self.assertTrue(self.evaluate("""(() => {
            validateRootHistogramDisplay({xMin:100,xMax:1,logX:true});
            return true;
        })()"""))

    def test_signed_projections_preserve_sums_and_stored_variances(self):
        histogram = {
            'counts': [[2, -1], [-3, 2]], 'variances': [[4, 1], [9, 4]],
            'x_edges': [0, 1, 3], 'y_edges': [-2, 0, 5],
            'x_centers': [0.5, 2], 'y_centers': [-1, 2.5],
        }
        result = self.evaluate(f"""(() => {{
            const source = {json.dumps(histogram)}, before = JSON.stringify(source);
            const state = buildSelectionProjectionPanelStateFromExactHistogram(source, 'Energy', 'Time', 0);
            return {{state, unchanged:JSON.stringify(source) === before}};
        }})()""")
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['state']['selectedCount'], 0)
        self.assertEqual(result['state']['items'][0]['counts'], [-1, 1])
        self.assertEqual(result['state']['items'][1]['counts'], [1, -1])
        self.assertEqual(result['state']['items'][0]['variances'], [13, 5])
        self.assertEqual(result['state']['items'][1]['variances'], [5, 13])

    def test_native_autofit_includes_whole_bin_edges_and_bar_baseline(self):
        result = self.evaluate("""(() => {
            const graph = {
                data:[{type:'bar',orientation:'v',x:[0.5,3.5],y:[1.5,6.5]}],
                _fullLayout:{xaxis:{type:'linear'},yaxis:{type:'linear'}},
                __rootHistogramContext:{dimension:1,axes:[{edges:[0,1,6]}]}
            };
            const vertical = getHistogramAutofitBounds(graph);
            graph.data = [{type:'bar',orientation:'h',x:[1.5,6.5],y:[0.5,3.5]}];
            return {vertical, horizontal:getHistogramAutofitBounds(graph)};
        })()""")
        self.assertEqual(result['vertical']['x'], [0, 6])
        self.assertEqual(result['vertical']['y'], [0, 6.5])
        self.assertEqual(result['horizontal']['x'], [0, 6.5])
        self.assertEqual(result['horizontal']['y'], [0, 6])

    def test_clear_placeholder_ranges_are_not_used_to_crop_rebinning(self):
        result = self.evaluate("""(() => {
            const graph = {
                data:[{_markerTrace:true}],
                _fullLayout:{xaxis:{range:[0,1]},yaxis:{range:[0,1]}}
            };
            globalThis.document = {getElementById:() => graph};
            globalThis.LAST_X_RANGE = null;
            globalThis.LAST_Y_RANGE = null;
            captureCurrentRanges();
            const placeholder = [LAST_X_RANGE, LAST_Y_RANGE];
            graph.data = [{type:'heatmap',z:[[2]]}];
            graph._fullLayout = {xaxis:{range:[0,3]},yaxis:{range:[-2,5]}};
            captureCurrentRanges();
            return {placeholder, data:[LAST_X_RANGE, LAST_Y_RANGE]};
        })()""")
        self.assertEqual(result['placeholder'], [None, None])
        self.assertEqual(result['data'], [[0, 3], [-2, 5]])


if __name__ == '__main__':
    unittest.main()
