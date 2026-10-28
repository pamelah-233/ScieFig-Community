"""Width must span the local tube body, not a thin side process or end cap."""
import cv2
import numpy as np
import pytest

from core.image_analysis.cell_width import mask_width_metrics


@pytest.mark.parametrize('angle', [0, 25, 70, 120])
def test_thin_side_process_does_not_become_tube_body_width(angle):
    mask = np.zeros((480, 480), np.uint8)
    cv2.rectangle(mask, (50, 220), (430, 260), 1, -1)
    cv2.rectangle(mask, (210, 185), (215, 220), 1, -1)
    rotation = cv2.getRotationMatrix2D((240, 240), angle, 1)
    mask = cv2.warpAffine(mask, rotation, (480, 480), flags=cv2.INTER_NEAREST)
    result = mask_width_metrics(mask)
    assert result['max_width_px'] == pytest.approx(40, abs=2)
    # A width line also needs support through its centre, not just at the tips.
    midpoint = np.mean([result['width_start_px'], result['width_end_px']], axis=0)
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    assert distance[tuple(np.rint(midpoint[::-1]).astype(int))] >= .43 * result['max_width_px']


def test_width_correction_preserves_real_broad_swelling():
    mask = np.zeros((150, 420), np.uint8)
    cv2.rectangle(mask, (25, 65), (390, 85), 1, -1)
    cv2.ellipse(mask, (200, 75), (60, 26), 0, 0, 360, 1, -1)
    result = mask_width_metrics(mask, .4, .4, offset=(70, 90))
    assert result['max_width_px'] == pytest.approx(52, abs=2)
    assert result['max_width_um'] == pytest.approx(result['max_width_px'] * .4)
    assert np.linalg.norm(np.subtract(result['width_end_px'], result['width_start_px'])) == pytest.approx(result['max_width_px'])


def test_many_thin_processes_do_not_hide_a_measurable_main_shaft():
    mask = np.zeros((260, 480), np.uint8)
    cv2.rectangle(mask, (20, 110), (460, 150), 1, -1)
    rng = np.random.default_rng(12)
    for x in range(30, 450, 12):
        cv2.rectangle(mask, (x, 110-int(rng.integers(20,65))), (x+2,110), 1, -1)
        cv2.rectangle(mask, (x+4,150), (x+6,150+int(rng.integers(20,65))), 1, -1)
    result = mask_width_metrics(mask)
    assert result['max_width_px'] == pytest.approx(40, abs=2)


def test_fiber_length_reaches_end_caps_and_preserves_anisotropic_scale():
    from core.image_analysis.cell_width import contour_width_metrics
    contour = [[25,35],[285,35],[285,55],[25,55]]
    result = contour_width_metrics(contour, .5, .25, include_length=True)
    assert result['centerline_length_px'] == pytest.approx(260, abs=2)
    assert result['centerline_length_um'] == pytest.approx(130, abs=1)
    points = np.asarray(result['centerline_points_px'])
    assert points[:,1].min() >= 35 and points[:,1].max() <= 55
    assert np.linalg.norm(np.diff(points,axis=0)*[.5,.25],axis=1).sum() == pytest.approx(result['centerline_length_um'])


def test_curved_fiber_length_is_path_length_not_feret_or_endpoint_chord():
    mask = np.zeros((240,240),np.uint8)
    angles = np.linspace(0,np.pi,240)
    points = np.rint(np.column_stack([120+80*np.cos(angles),120+80*np.sin(angles)])).astype(np.int32)
    cv2.polylines(mask,[points],False,1,16)
    result = mask_width_metrics(mask,.5,.5,include_length=True)
    assert result['centerline_length_px'] == pytest.approx(np.pi*80+16,abs=8)
    assert result['centerline_length_um'] == pytest.approx(result['centerline_length_px']*.5)
    assert result['centerline_length_px'] > 1.4*np.linalg.norm(np.subtract(result['centerline_points_px'][0],result['centerline_points_px'][-1]))


@pytest.mark.parametrize('angle',[0,25,70,120])
def test_rotated_fiber_length_stays_boundary_to_boundary(angle):
    from core.image_analysis.cell_width import contour_width_metrics
    contour=np.asarray([[80,220],[400,220],[400,250],[80,250]],float)
    matrix=cv2.getRotationMatrix2D((240,240),angle,1)
    contour=contour@matrix[:,:2].T+matrix[:,2]
    result=contour_width_metrics(contour,include_length=True)
    assert result['centerline_length_px']==pytest.approx(320,abs=4)

