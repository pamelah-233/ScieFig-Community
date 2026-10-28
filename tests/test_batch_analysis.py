from copy import deepcopy
import cv2
import numpy as np
import pytest

from core.models import BrightfieldPanel, FluorescencePanel, IHCPanel


def fluorescence_panel():
    panel = FluorescencePanel(panel_id="batch-fluorescence")
    for index in range(3):
        uid = f"image-{index}"
        pixels = np.zeros((1, 80, 100), np.uint16)
        cv2.circle(pixels[0], (25, 35), 7, 900, -1)
        cv2.circle(pixels[0], (65, 35), 5, 600, -1)
        panel.data.setdefault("fluorescence_images", []).append({
            "uid": uid, "source_name": f"sample-{index}.tif", "source_path": f"C:/images/sample-{index}.tif",
            "source_hash": f"stored-source-{index}",
            "metadata": {"width_px": 100, "height_px": 80, "pixel_size_x_um": 0.4,
                         "pixel_size_y_um": 0.6, "exposure": 150},
            "channels": [{"index": 0, "name": "GFP", "visible": True}], "measurements": [],
        })
        panel.cache_image_data(uid, pixels)
    panel.data["active_image_uid"] = "image-2"
    return panel


def test_batch_runs_selected_images_and_appends_without_replacing_old_state():
    from core.batch_analysis import BatchAnalysisJob, apply_batch_image_result

    panel = fluorescence_panel()
    old = panel.count_channel_objects("image-0", [0], method="manual", manual_value=100)[0]
    before = deepcopy(panel.to_dict())
    options = {"channel_indices": [0], "method": "manual", "manual_value": 500, "min_area_px": 5}
    preset = {"id": "preset-20x", "name": "20× camera", "um_per_px_x": 0.5, "um_per_px_y": 0.75}
    job = BatchAnalysisJob(panel, ["image-0", "image-1"], "count_channel_objects", options,
                           calibration_preset=preset)
    options["manual_value"] = 99999
    results = job.run()

    assert panel.to_dict() == before
    assert [item.status for item in results] == ["success", "success"]
    assert [item.measurements[0]["count"] for item in results] == [2, 2]
    for item in results:
        apply_batch_image_result(panel, item)
    assert panel.data["active_image_uid"] == "image-2"
    assert panel.images()[2] == before["data"]["fluorescence_images"][2]
    assert panel.analysis["object_counts"][0] == old
    assert len(panel.analysis["object_counts"]) == 3
    for index in (0, 1):
        image = panel.images()[index]
        assert image["uid"] == before["data"]["fluorescence_images"][index]["uid"]
        assert image["metadata"] == before["data"]["fluorescence_images"][index]["metadata"]
        assert panel.measurement_scale_bar(image["uid"])["um_per_px_y"] == 0.75
        record = image["object_counts"][-1]
        assert record["batch_provenance"]["settings"]["manual_value"] == 500
        assert record["batch_provenance"]["calibration_preset"]["id"] == "preset-20x"
        assert record["batch_provenance"]["source_hash"] == image["source_hash"]
    panel.dispose()


def test_one_image_failure_keeps_prior_success_and_continues():
    from core.batch_analysis import BatchAnalysisJob, apply_batch_image_result

    panel = fluorescence_panel()
    before = deepcopy(panel.to_dict())

    def runner(snapshot, uid, method, options):
        if uid == "image-1":
            snapshot.data["fluorescence_images"][0]["metadata"]["exposure"] = 999
            raise ValueError("Invalid image channel")
        return getattr(snapshot, method)(uid, **options)

    progress = []
    results = BatchAnalysisJob(panel, ["image-0", "image-1", "image-2"], "count_channel_objects",
        {"channel_indices": [0]}, runner=runner).run(on_progress=lambda done, total, uid: progress.append((done, total, uid)))
    assert [item.status for item in results] == ["success", "failed", "success"]
    assert results[1].error == "Invalid image channel"
    assert progress[-1][:2] == (3, 3)
    for result in results:
        apply_batch_image_result(panel, result)
    assert panel.images()[1] == before["data"]["fluorescence_images"][1]
    assert len(panel.analysis["object_counts"]) == 2
    panel.dispose()


def test_cooperative_cancel_keeps_completed_image_and_skips_remaining():
    from core.batch_analysis import BatchAnalysisJob

    panel = fluorescence_panel()
    called = []
    job = BatchAnalysisJob(panel, ["image-0", "image-1", "image-2"], "count_channel_objects", {"channel_indices": [0]})

    def completed(result):
        called.append(result.image_uid)
        job.cancel()

    results = job.run(on_result=completed)
    assert called == ["image-0", "image-1", "image-2"]
    assert [item.status for item in results] == ["success", "cancelled", "cancelled"]
    assert results[1].measurements == []
    assert results[2].measurements == []
    panel.dispose()


@pytest.mark.parametrize("kind,method", [("brightfield", "measure_bright_round_cells"), ("ihc", "measure_dab_positive_area")])
def test_batch_uses_real_public_brightfield_and_ihc_methods(tmp_path, kind, method):
    from core.batch_analysis import BatchAnalysisJob, apply_batch_image_result

    pixels = np.full((100, 150, 3), 80, np.uint8)
    cv2.circle(pixels, (55, 45), 12, (235, 235, 235), -1)
    if kind == "ihc":
        pixels[3:15, 3:20] = (110, 140, 170)
        pixels[70:85, 90:120] = (170, 140, 110)
        pixels[30:38, 90:108] = (40, 55, 80)
    path = tmp_path / "cells.png"
    assert cv2.imwrite(str(path), pixels)
    panel = BrightfieldPanel(panel_id="cells") if kind == "brightfield" else IHCPanel(panel_id="dab")
    image = panel.import_image(path)
    preset = {"id": "cal", "name": "Known pixels", "um_per_px_x": 0.5, "um_per_px_y": 0.5}
    result = BatchAnalysisJob(panel, [image["uid"]], method, {"settings": {}}, calibration_preset=preset).run()[0]
    assert result.status == "success", result.error
    apply_batch_image_result(panel, result)
    key = "bright_round_cell_diameter_measurements" if kind == "brightfield" else "ihc_measurements"
    assert len(panel.analysis[key]) == 1
    assert panel.analysis[key][0]["image_uid"] == image["uid"]
    assert panel.analysis[key][0]["batch_provenance"]["method"] == method
    if kind == "brightfield":
        assert panel.analysis[key][0]["summary"]["count"] == 1
    else:
        assert image["measurements"][0]["measurement_uuid"] == panel.analysis[key][0]["measurement_uuid"]


def test_invalid_batch_inputs_do_not_modify_images():
    from core.batch_analysis import BatchAnalysisJob

    panel = fluorescence_panel()
    before = deepcopy(panel.to_dict())
    for uids, method, options in [([], "count_channel_objects", {}), (["missing"], "count_channel_objects", {}),
                                  (["image-0"], "delete_image", {}), (["image-0"], "count_channel_objects", {"image_uid": "image-1"})]:
        with pytest.raises((ValueError, KeyError)):
            BatchAnalysisJob(panel, uids, method, options)
    assert panel.to_dict() == before
    panel.dispose()


def test_failed_image_retains_attempted_calibration_in_provenance():
    from core.batch_analysis import BatchAnalysisJob

    panel = fluorescence_panel()
    preset = {"id": "known", "name": "Known pixels", "um_per_px_x": .2, "um_per_px_y": .3}

    def unavailable(snapshot, uid, method, options):
        raise RuntimeError("Backend unavailable")

    result = BatchAnalysisJob(panel, ["image-0"], "count_channel_objects", {"channel_indices": [0]},
                              calibration_preset=preset, runner=unavailable).run()[0]
    assert result.status == "failed"
    assert result.calibration["um_per_px_y"] == .3
    assert result.provenance["calibration"]["preset_id"] == "known"
    assert panel.measurement_scale_bar("image-0")["um_per_px_y"] == .6
    panel.dispose()


def test_zero_detections_remain_success_and_missing_backend_is_not_zero():
    from core.batch_analysis import BatchAnalysisJob
    from core.image_analysis.fluorescence_ai import FluorescenceAIModelBoundary

    panel = fluorescence_panel()
    zero = BatchAnalysisJob(panel, ["image-0"], "count_channel_objects",
                            {"channel_indices": [0], "method": "manual", "manual_value": 999999}).run()[0]
    assert zero.status == "success"
    assert zero.measurements[0]["count"] == 0

    def unavailable(snapshot, uid, method, options):
        return snapshot.run_ai_assisted_analysis(uid, **options, model_boundary=FluorescenceAIModelBoundary())

    missing = BatchAnalysisJob(panel, ["image-0"], "run_ai_assisted_analysis", {"channel_indices": [0]}, runner=unavailable).run()[0]
    assert missing.status == "failed"
    assert not missing.measurements
    assert "unavailable" in missing.error
    panel.dispose()


@pytest.mark.parametrize("method,key", [
    ("run_ai_assisted_analysis", "fluorescence_ai_assisted_measurements"),
    ("run_smart_assisted_analysis", "fluorescence_smart_assisted_measurements"),
])
def test_fluorescence_ai_and_smart_keep_native_records_and_original_image_ids(method, key):
    from core.batch_analysis import BatchAnalysisJob, apply_batch_image_result

    panel = fluorescence_panel()
    result = BatchAnalysisJob(panel, ["image-0"], method,
        {"channel_indices": [0], "settings": {"min_object_area_px": 5}}).run()[0]
    assert result.status == "success", result.error
    assert result.measurements[0]["image_uid"] == "image-0"
    assert result.measurements[0]["object_table"]
    assert apply_batch_image_result(panel, result)
    assert not apply_batch_image_result(panel, result)
    assert len(panel.analysis[key]) == len(panel.images()[0][key]) == 1
    assert not panel.images()[1].get(key)
    panel.dispose()


@pytest.mark.parametrize("method,options", [
    ("auto_analyze_he", {"settings": {}, "guided_mode": "foolproof"}),
    ("create_profile_preset_measurement", {"stain_type": "Masson", "guided_mode": "foolproof"}),
    ("auto_analyze_dab", {"settings": {}, "guided_mode": "foolproof"}),
])
def test_batch_keeps_existing_guided_analysis_methods(tmp_path, method, options):
    from core.batch_analysis import BatchAnalysisJob, apply_batch_image_result

    yy, xx = np.indices((80, 100))
    image = np.stack([80 + (xx * 2) % 150, 70 + (yy * 3) % 160, 50 + (xx + yy) % 180], axis=-1).astype(np.uint8)
    path = tmp_path / "stain.png"
    assert cv2.imwrite(str(path), image)
    panel = IHCPanel(panel_id="ihc") if method == "auto_analyze_dab" else BrightfieldPanel(panel_id="bf")
    imported = panel.import_image(path)
    options = {**options, "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None}}
    result = BatchAnalysisJob(panel, [imported["uid"]], method, options).run()[0]
    assert result.status == "success", result.error
    assert result.measurements[0]["guided_mode"] == "foolproof"
    assert result.measurements[0]["batch_provenance"]["method"] == method
    assert apply_batch_image_result(panel, result)
    key = "ihc_measurements" if method == "auto_analyze_dab" else "brightfield_measurements"
    assert len(panel.analysis[key]) == 1


def test_batch_spots_save_historical_scale_for_later_object_review():
    from core.batch_analysis import BatchAnalysisJob
    from core.measurement_review import ObjectReviewSession

    panel = fluorescence_panel()
    result = BatchAnalysisJob(panel, ["image-0"], "count_channel_objects", {"channel_indices": [0]}).run()[0]
    record = result.measurements[0]
    review = ObjectReviewSession(record, (80, 100))
    assert review.calibration_snapshot["um_per_px_x"] == .4
    assert review.calibration_snapshot["um_per_px_y"] == .6
    panel.dispose()
