import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication
from core.utils.i18n import get_language, set_language, tr


def test_canvas_empty_hint_translates_without_rerendering_images(monkeypatch):
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    app=QApplication.instance() or QApplication([])
    previous=get_language()
    canvas=FluorescenceCanvas()
    try:
        set_language('zh')
        canvas.refresh()
        assert any(getattr(item,'toPlainText',lambda:'' )()==tr('fluoro_canvas_import_hint') for item in canvas.scene.items())
        monkeypatch.setattr(canvas,'refresh',lambda:(_ for _ in ()).throw(AssertionError('unnecessary image render')))
        set_language('en')
        canvas.retranslate()
        assert any(getattr(item,'toPlainText',lambda:'' )()==tr('fluoro_canvas_import_hint') for item in canvas.scene.items())
    finally:
        set_language(previous)
        canvas.close()
