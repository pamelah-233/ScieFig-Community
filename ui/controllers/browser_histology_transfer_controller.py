"""Browser destinations share the normal isolated microscopy import worker."""
from copy import deepcopy
from PyQt6.QtCore import QObject


class BrowserHistologyTransferController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.image_browser_workspace.send_to_histology_transfer_requested.connect(self.transfer)

    def transfer(self, module, payloads):
        if module not in ('ihc', 'brightfield'):
            return
        items = [deepcopy(p) for p in payloads if isinstance(p, dict) and str(p.get('path', '')).strip()]
        if not items:
            return
        panel = getattr(self.window, '_active_'+module+'_panel')()
        self.window._start_microscopy_import(module, [p['path'] for p in items], panel,
                                            transfer_by_path={p['path']: p for p in items})
