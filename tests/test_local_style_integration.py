from pathlib import Path
import pytest

from core.style_preset_registry import StylePresetRegistry


def test_local_draft_saved_with_evidence_without_promoting_unknown_tokens(tmp_path):
    from ui.local_style_extraction.integration import save_extracted_style

    draft = {
        "name": "显微镜论文配色", "source": {"path": "article.pdf", "kind": "pdf", "page_index": 0},
        "tokens": {
            "series_colors": {"status": "detected", "value": ["#123456", "#ABCDEF"]},
            "font_family": {"status": "unknown", "value": None},
            "font_size_pt": {"status": "user_edited", "value": 9.0},
        },
        "resolved_style": {"font_family": "Invented font"}, "warnings": [],
    }
    registry = StylePresetRegistry(tmp_path / "styles.json")
    saved = save_extracted_style(draft, registry)
    restored = registry.get_preset(saved.preset_id)
    assert restored.display_name == draft["name"]
    assert restored.plot_style["series_colors"] == ["#123456", "#ABCDEF"]
    assert restored.plot_style["font_family"] != "Invented font"
    assert restored.plot_style["font_size_pt"] == 9.0
    assert restored.extraction_profile["local_extraction"]["tokens"]["font_family"]["status"] == "unknown"
    from core.style_preset_resolver import set_runtime_active_style_preset, clear_runtime_active_style_preset
    from core.plotting.statistics_plot import build_group_statistics_figure
    from matplotlib.colors import to_hex
    from matplotlib import pyplot as plt
    set_runtime_active_style_preset(saved.preset_id, tmp_path / "styles.json")
    try:
        figure = build_group_statistics_figure({"plot_data": [{"group": "A", "value": 1}, {"group": "B", "value": 2}]}, chart_kind="bar")
        assert [to_hex(patch.get_facecolor()).upper() for patch in figure.axes[0].patches[:2]] == ["#123456", "#ABCDEF"]
        plt.close(figure)
    finally:
        clear_runtime_active_style_preset()


def test_extraction_workspace_is_embedded_lazy_and_stops_worker(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from ui.widgets.style_lab_workspace import StyleLabWorkspace

    app = QApplication.instance() or QApplication([])
    workspace = StyleLabWorkspace(preset_registry_path=tmp_path / "styles.json", render_initial_preview=False)
    assert workspace.local_extraction is None
    workspace.open_local_extraction()
    assert workspace.local_extraction is not None
    assert workspace.local_extraction.api.can_extract
    assert workspace.local_extraction.parent() is not None
    from ui.local_style_extraction.app import _as_abs
    assert _as_abs("preview.png", tmp_path) == str(tmp_path / "preview.png")
    workspace.shutdown_local_extraction()
    assert not workspace.local_extraction.worker.isRunning()
    workspace.close()


def test_style_workspace_uses_stage_width_and_restores_other_module_sidebar():
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args: True
    window.v9_shell.restore_right_panel()
    window.set_module("stylelab")
    assert window.v9_shell.is_right_panel_collapsed()
    window.set_module("qpcr")
    assert not window.v9_shell.is_right_panel_collapsed()
    window.composer_canvas._plot_payload_bytes_cache[("old_style",)] = b"old"
    window.apply_style_lab_runtime_saved_preset()
    assert ("old_style",) not in window.composer_canvas._plot_payload_bytes_cache
    window.close()


@pytest.mark.parametrize("suffix", ["pdf", "png"])
def test_integrated_engine_retains_ten_color_palette(tmp_path, suffix):
    from ui.local_style_extraction.engine import extract_style
    source = Path(__file__).parent / "fixtures" / "local_style_extraction" / f"legend10.{suffix}"
    draft = extract_style(str(source), 0, [0, 0, 1, 1], str(tmp_path / suffix))
    colors = draft["tokens"]["series_colors"]["value"]
    assert len(colors) == len(set(colors)) == 10
    assert "#4C4C4C" not in colors
    if suffix == "pdf":
        assert draft["tokens"]["font_family"]["status"] == "detected"


def test_saved_pdf_font_matches_preview_substitution_without_losing_evidence(tmp_path):
    from ui.local_style_extraction.integration import save_extracted_style
    from ui.local_style_extraction.preview import _FontResolver
    draft = {"name": "PDF font", "source": {}, "tokens": {"font_family": {"status": "detected", "value": "Helvetica"}}}
    saved = save_extracted_style(draft, StylePresetRegistry(tmp_path / "fonts.json"))
    assert saved.plot_style["font_family"] == _FontResolver().actual("Helvetica")
    assert saved.extraction_profile["local_extraction"]["tokens"]["font_family"]["value"] == "Helvetica"
