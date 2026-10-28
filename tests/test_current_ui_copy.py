import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from core.utils.i18n import get_language, set_language, tr


def test_live_empty_states_do_not_claim_implemented_features_are_missing():
    previous = get_language()
    try:
        for language in ('en','zh'):
            set_language(language)
            for key in ('module_qpcr_detail','module_wb_detail','module_fluoro_detail','preview_initial_body'):
                text=tr(key).lower()
                assert not any(old in text for old in ('placeholder','phase 2','phase 0/1','not implemented','占位','暂未实现','后续提供'))
    finally:
        set_language(previous)


def test_composer_language_change_retains_compact_zoom_symbols():
    from PyQt6.QtWidgets import QApplication
    from ui.widgets.composer_canvas import ComposerCanvas
    app=QApplication.instance() or QApplication([])
    canvas=ComposerCanvas()
    previous=get_language()
    try:
        set_language('en')
        canvas.retranslate()
        from ui.widgets.canvas_zoom import CanvasZoomControls
        zoom=canvas.findChild(CanvasZoomControls)
        assert zoom.zoom_in_button.text() == '+'
        assert zoom.zoom_out_button.text() == '-'
    finally:
        set_language(previous)
        canvas.close()
