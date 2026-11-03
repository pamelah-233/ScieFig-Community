import unittest
import numpy as np

from core.spatial_demo import DemoResult, synthetic_image, analyze, spatial_metrics


class SpatialDemoTests(unittest.TestCase):
    def test_native_rectangular_image_and_roi_bounds(self):
        image = np.zeros((4, 180, 320), dtype=np.uint16)
        image[0, 20:30, 50:60] = 40000
        image[1, 20:30, 50:60] = 30000
        result = analyze(image, pixel_um=1, expansion_um=0)
        self.assertEqual(result.nuclei.shape, (180, 320))
        self.assertEqual(result.roi, (0, 0, 320, 180))
        self.assertEqual(result.area_um2, 180*320)
        np.testing.assert_allclose(result.xy, [[54.5, 24.5]])
        self.assertEqual(result.phenotype.tolist(), [1])
        for roi in ((0, 0, 321, 180), (0, 0, 0, 180)):
            with self.assertRaises(ValueError):
                analyze(image, roi=roi)

    def test_generated_image_is_reproducible_and_analyzed_from_pixels(self):
        image = synthetic_image()
        self.assertEqual(image.shape, (4, 512, 512))
        self.assertEqual(image.dtype, np.uint16)
        np.testing.assert_array_equal(image, synthetic_image())
        result = analyze(image)
        self.assertGreater(len(result.ids), 100)
        self.assertEqual(result.area_um2, 256**2)
        self.assertGreater(len(np.unique(result.phenotype)), 2)
        self.assertTrue(np.all(result.cells[result.nuclei > 0] == result.nuclei[result.nuclei > 0]))
        nuclei_only = analyze(image, expansion_um=0)
        np.testing.assert_array_equal(nuclei_only.cells, nuclei_only.nuclei)
        negative = analyze(image, thresholds=(1, 1, 1))
        self.assertTrue(np.all(negative.phenotype == 0))
        empty = analyze(np.zeros_like(image))
        self.assertEqual(len(empty.ids), 0)
        self.assertIsNone(spatial_metrics(empty, 3, 4)['p'])

    def test_directed_distances_self_exclusion_roi_and_radius(self):
        r = DemoResult(np.zeros((1, 1)), np.zeros((1, 1)), np.arange(1, 5),
                       np.array([[0., 0.], [3., 4.], [9., 0.], [40., 0.]]),
                       np.zeros((4, 3)), np.array([1, 2, 2, 1]),
                       100., np.ones(4, dtype=bool), (0, 0, 10, 10))
        metrics = spatial_metrics(r, 1, 2, radius_um=5)
        np.testing.assert_allclose(metrics['nearest'], [5., 31.])
        np.testing.assert_array_equal(metrics['neighbors'], [1, 0])
        self.assertTrue(0 < metrics['p'] <= 1)
        again = spatial_metrics(r, 1, 2, radius_um=5)
        self.assertEqual(metrics['p'], again['p'])
        same = spatial_metrics(r, 2, 2, radius_um=10)
        np.testing.assert_allclose(same['nearest'], [np.sqrt(52)]*2)
        np.testing.assert_array_equal(same['neighbors'], [1, 1])
        r.selected = np.array([True, True, False, False])
        np.testing.assert_allclose(spatial_metrics(r, 1, 2)['nearest'], [5.])
        self.assertIsNone(spatial_metrics(r, 1, 7)['p'])

    def test_roi_assigns_cells_by_centroid(self):
        image = synthetic_image()
        whole = analyze(image)
        left = analyze(image, roi=(0, 0, 256, 512))
        right = analyze(image, roi=(256, 0, 512, 512))
        self.assertEqual(int(left.selected.sum()+right.selected.sum()), len(whole.ids))
        self.assertEqual(left.area_um2+right.area_um2, whole.area_um2)

    def test_same_type_coincident_centroids_exclude_only_self(self):
        r = DemoResult(np.zeros((1, 1)), np.zeros((1, 1)), np.arange(1, 4),
                       np.array([[0., 0.], [0., 0.], [10., 0.]]), np.zeros((3, 3)),
                       np.ones(3, dtype=int), 100., np.ones(3, dtype=bool), (0, 0, 10, 10))
        metrics = spatial_metrics(r, 1, 1, radius_um=1, permutations=0)
        np.testing.assert_allclose(metrics['nearest'], [0., 0., 10.])
        np.testing.assert_array_equal(metrics['neighbors'], [1, 1, 0])
        r.selected = np.array([True, False, False])
        single = spatial_metrics(r, 1, 1, permutations=0)
        self.assertTrue(np.isinf(single['nearest'][0]))
        self.assertEqual(single['neighbors'].tolist(), [0])


if __name__ == '__main__':
    unittest.main()
