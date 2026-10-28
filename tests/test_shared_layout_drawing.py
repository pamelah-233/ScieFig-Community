from PyQt6.QtWidgets import QApplication,QGraphicsView,QGraphicsScene
from PyQt6.QtCore import Qt,QPoint
from PyQt6.QtTest import QTest
from ui.widgets.shared_layout_drawing import LayoutDrawTool

_APP=QApplication.instance() or QApplication([])


def test_drag_creation_emits_annotation_and_cleans_preview():
    scene=QGraphicsScene();scene.setSceneRect(0,0,500,400)
    view=QGraphicsView(scene);view.resize(600,500);view.show();_APP.processEvents()
    tool=LayoutDrawTool(view);events=[];tool.created.connect(lambda *args:events.append(args))
    tool.arm('arrow')
    QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(100,100))
    QTest.mouseMove(view.viewport(),QPoint(220,180))
    QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(220,180))
    assert len(events)==1 and events[0][0]=='arrow'
    assert events[0][1].width()>100 and events[0][2]['x2']>100
    assert tool.kind is None and not scene.items()
    view.close()
