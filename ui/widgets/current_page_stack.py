"""Content-sized stacks for scrolling workflow controls, not canvas pages."""
from PyQt6.QtCore import QEvent,QSize,QTimer
from PyQt6.QtWidgets import QSizePolicy,QStackedWidget,QBoxLayout


class CurrentPageStackedWidget(QStackedWidget):
    def __init__(self,parent=None,*,compact=False):
        super().__init__(parent)
        self._compact=bool(compact)
        self._syncing_geometry=False
        self._geometry_key=None
        self._parent_refresh=QTimer(self)
        self._parent_refresh.setSingleShot(True)
        self._parent_refresh.timeout.connect(self._refresh_parent_item)
        if self._compact:
            self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
            self.currentChanged.connect(self._sync_current_page_geometry)

    def sizeHint(self)->QSize:
        page=self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self)->QSize:
        page=self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def hasHeightForWidth(self):
        if not self._compact:return super().hasHeightForWidth()
        page=self.currentWidget()
        return bool(page is not None and page.hasHeightForWidth())

    def heightForWidth(self,width):
        if not self._compact:return super().heightForWidth(width)
        page=self.currentWidget()
        if page is None:return super().heightForWidth(width)
        return page.heightForWidth(width) if page.hasHeightForWidth() else page.sizeHint().height()

    def _sync_current_page_geometry(self,*_args):
        if not self._compact:return
        for index in range(self.count()):
            page=self.widget(index)
            policy=page.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
            policy.setVerticalPolicy(QSizePolicy.Policy.Preferred if index==self.currentIndex() else QSizePolicy.Policy.Ignored)
            page.setSizePolicy(policy)
        height=max(self.minimumSizeHint().height(),self.heightForWidth(self.contentsRect().width()))
        if height>0 and (self.minimumHeight()!=height or self.maximumHeight()!=height):self.setFixedHeight(height)
        key=(self.currentIndex(),self.width(),height)
        if key!=self._geometry_key:
            self._geometry_key=key
            self._parent_refresh.start(0)
        self.updateGeometry()

    def _refresh_parent_item(self):
        # A nested QWidgetItemV2 can retain the previous page's width/height
        # cache even after layout.invalidate(). Rebuild only this layout item,
        # outside the layout pass; the real widget, state and signals stay put.
        parent=self.parentWidget()
        layout=parent.layout() if parent is not None else None
        if isinstance(layout,QBoxLayout):
            index=layout.indexOf(self)
            if index>=0:
                stretch=layout.stretch(index)
                item=layout.takeAt(index)
                layout.insertWidget(index,self,stretch,item.alignment())
                parent.updateGeometry()

    def event(self,event):
        result=super().event(event)
        if (self._compact and not self._syncing_geometry and event.type() in {
            QEvent.Type.LayoutRequest,QEvent.Type.Resize,QEvent.Type.Show,
            QEvent.Type.FontChange,QEvent.Type.LanguageChange,
        }):
            self._syncing_geometry=True
            try:self._sync_current_page_geometry()
            finally:self._syncing_geometry=False
        return result
