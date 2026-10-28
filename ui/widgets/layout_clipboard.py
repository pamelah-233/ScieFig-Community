"""Versioned, data-only clipboard transport. Assets remain source references."""
import json
from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import QApplication

LAYOUT_MIME = 'application/x-sciefig-layout-objects+json'


def write_layout_clipboard(payload):
    mime = QMimeData()
    mime.setData(LAYOUT_MIME, json.dumps({'version': 1, **payload}, ensure_ascii=False).encode('utf-8'))
    labels = [entry.get('object', {}).get('text', '') for entry in payload.get('entries', [])]
    mime.setText('\n'.join(text for text in labels if text) or 'ScieFig layout objects')
    QApplication.clipboard().setMimeData(mime)


def read_layout_clipboard():
    mime = QApplication.clipboard().mimeData()
    if mime is None:
        return None
    if not mime.hasFormat(LAYOUT_MIME):
        return {'kind':'text','text':mime.text()} if mime.hasText() and mime.text().strip() else None
    try:
        payload = json.loads(bytes(mime.data(LAYOUT_MIME)))
        if not isinstance(payload, dict) or payload.get('version') != 1:
            return None
        entries = payload.get('entries')
        if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
            return None
        return payload
    except (ValueError, TypeError, UnicodeError):
        return None
