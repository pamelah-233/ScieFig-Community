import unittest
import numpy as np

from core.image_analysis.io_engine import normalize_to_cyx


class NativeImageDimensionsTests(unittest.TestCase):
    def test_singleton_spatial_axes_and_sample_axes_keep_dimensions(self):
        for shape, axes, expected in (
            ((1, 17), 'YX', (1, 1, 17)),
            ((31, 1), 'YX', (1, 31, 1)),
            ((1, 17, 3), 'YXS', (3, 1, 17)),
            ((3, 31, 1), 'CYX', (3, 31, 1)),
            ((1, 3, 1, 17, 1), 'TCZYX', (3, 17, 1)),
            ((1, 1, 17, 3), 'TYXS', (3, 1, 17)),
        ):
            with self.subTest(shape=shape, axes=axes):
                raw = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape)
                decoded = normalize_to_cyx(raw, axes)
                self.assertEqual(decoded.shape, expected)
                self.assertEqual(decoded.dtype, raw.dtype)
                np.testing.assert_array_equal(np.sort(decoded.ravel()), np.sort(raw.ravel()))


if __name__ == '__main__':
    unittest.main()
