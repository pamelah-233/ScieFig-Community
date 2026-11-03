import json
import unittest
import numpy as np

from core.image_analysis.multiplex_spatial import measure_multiplex
from core.image_analysis.nuclei_counting import object_rows
from core.image_analysis.cellpose_backend import pack_instance_labels


class MultiplexSpatialTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((6, 50, 80), np.uint16)
        self.labels = np.zeros((50, 80), np.int32)
        for identifier, x in enumerate((10, 20, 40), 1): self.labels[10:13, x:x+3] = identifier
        self.image[0, self.labels > 0] = 500
        self.image[1, (self.labels == 1) | (self.labels == 3)] = 1000
        self.image[2, self.labels == 2] = 2000
        self.source = dict(measurement_uuid='nuclei-1', image_uid='image-1', image_name='test', channel_index=0,
                           nuclei=object_rows(self.labels, self.image[0]), instance_labels=pack_instance_labels(self.labels))
        self.markers = [dict(channel_index=1, name='CD3', threshold=500), dict(channel_index=2, name='CD8', threshold=1000)]

    def test_raw_means_and_anisotropic_distances_are_saved_without_normalization(self):
        result = measure_multiplex(self.image, self.source, markers=self.markers, expansion=0, radius=15, pixel_size_xy=(2., .5))
        self.assertEqual(result['summary']['cells'], 3)
        self.assertEqual(result['summary']['area'], 4000.)
        self.assertEqual(result['summary']['nearest_mean'], 20.)
        self.assertEqual(result['summary']['fraction_with_neighbors'], 0.)
        self.assertEqual([r['marker_means'] for r in result['object_table']], [[1000., 0.], [0., 2000.], [1000., 0.]])
        json.dumps(result, allow_nan=False)

    def test_absent_requested_phenotype_is_not_replaced_by_another_class(self):
        result = measure_multiplex(self.image, self.source, markers=self.markers, expansion=0,
                                   source_phenotype='CD3+ / CD8+', target_phenotype='CD3+ / CD8−')
        self.assertEqual(result['summary']['source_cells'], 0)
        self.assertIsNone(result['summary']['nearest_mean'])
        self.assertEqual(result['parameters']['source_phenotype'], 'CD3+ / CD8+')

    def test_roi_and_source_exclusions_remove_cells_and_self_from_neighbor_counts(self):
        roi = np.zeros((50, 80), bool); roi[:, :30] = True
        result = measure_multiplex(self.image, self.source, markers=self.markers, expansion=0, radius=15, roi_mask=roi)
        self.assertEqual(result['summary']['cells'], 2)
        self.assertEqual(result['summary']['area'], 1500.)
        self.assertEqual(result['summary']['nearest_mean'], 10.)
        self.source['nuclei'][1]['include_in_statistics'] = False
        result = measure_multiplex(self.image, self.source, markers=self.markers, expansion=0, roi_mask=roi)
        self.assertEqual(result['summary']['cells'], 1)
        self.assertIsNone(result['summary']['nearest_mean'])

    def test_empty_mask_preserves_valid_empty_output(self):
        self.source['nuclei'] = []
        self.source['instance_labels'] = pack_instance_labels(np.zeros((50, 80), np.int32))
        result = measure_multiplex(self.image, self.source, markers=self.markers)
        self.assertEqual(result['summary']['cells'], 0)
        self.assertEqual(result['object_table'], [])


if __name__ == '__main__': unittest.main()
