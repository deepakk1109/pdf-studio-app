#!/usr/bin/env python3
"""
PDF Studio - all-in-one PDF editor
View | Create | Edit text | Annotate | Signature | Stamps | Digital sign
Forms | Redact | OCR | Merge/Split | Password | Watermark | Page numbers

Install:
    pip install pymupdf PyQt5 pyhanko cryptography
Run:
    python pdf_studio.py
"""
import sys
import os
import io
import json
import html
import datetime

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QLabel, QScrollArea, QToolBar,
    QAction, QActionGroup, QListWidget, QListWidgetItem, QSplitter,
    QInputDialog, QMessageBox, QColorDialog, QDialog, QVBoxLayout,
    QHBoxLayout, QPushButton, QWidget, QSpinBox, QLineEdit, QMenu
)
from PyQt5.QtGui import QPixmap, QImage, QIcon, QPainter, QPen, QColor
from PyQt5.QtCore import (
    Qt, QSize, QPoint, QRect, pyqtSignal, QBuffer, QByteArray, QIODevice
)

APP_DIR = os.path.join(os.path.expanduser("~"), ".pdf_studio")
SIG_PATH = os.path.join(APP_DIR, "signature.png")
CFG_PATH = os.path.join(APP_DIR, "config.json")

STAMPS = {
    "APPROVED": (0, 0.55, 0), "REJECTED": (0.8, 0, 0),
    "CONFIDENTIAL": (0.8, 0, 0), "DRAFT": (0.9, 0.5, 0),
    "FINAL": (0, 0, 0.7), "PAID": (0, 0.55, 0), "COPY": (0.3, 0.3, 0.3),
    "URGENT": (0.8, 0, 0), "FOR REVIEW": (0, 0.3, 0.7),
}

MODES = [
    ("Select", None), ("Edit Line", "edit_line"),
    ("Edit Paragraph", "edit_para"), ("Add Text", "text"),
    ("Highlight", "highlight"), ("Underline", "underline"),
    ("Strike", "strike"), ("Note", "note"), ("Draw", "draw"),
    ("Rect", "rect"), ("Ellipse", "ellipse"), ("Line", "line"),
    ("Signature", "signature"), ("Stamp", "stamp"), ("Image", "image"),
    ("Redact", "redact"), ("Form", "form"), ("Digital Sign", "digisign"),
]
DRAG_MODES = {"highlight", "underline", "strike", "draw", "rect",
              "ellipse", "line", "redact", "digisign"}
HINTS = {
    None: "Select mode",
    "edit_line": "Click on a text line to edit it",
    "edit_para": "Click on a paragraph to edit it",
    "text": "Click where you want to add text",
    "highlight": "Drag over text to highlight",
    "underline": "Drag over text to underline",
    "strike": "Drag over text to strike out",
    "note": "Click to add a sticky note",
    "draw": "Drag to draw freehand",
    "rect": "Drag to draw a rectangle",
    "ellipse": "Drag to draw an ellipse",
    "line": "Drag to draw a line",
    "signature": "Click (or drag a box) to place your signature",
    "stamp": "Click to place the stamp",
    "image": "Click (or drag a box) to place the image",
    "redact": "Drag over content to permanently remove it",
    "form": "Click a form field to fill it",
    "digisign": "Drag a box where the digital signature should appear",
}

BASE14 = {
    ("he", 0, 0): "helv", ("he", 1, 0): "hebo", ("he", 0, 1): "heit", ("he", 1, 1): "hebi",
    ("ti", 0, 0): "tiro", ("ti", 1, 0): "tibo", ("ti", 0, 1): "tiit", ("ti", 1, 1): "tibi",
    ("co", 0, 0): "cour", ("co", 1, 0): "cobo", ("co", 0, 1): "coit", ("co", 1, 1): "cobi",
}


def is_latin(s):
    return all(ord(c) < 256 for c in s)


def base14_name(font, flags):
    f = (font or "").lower()
    bold = 1 if (flags & 16 or "bold" in f) else 0
    ital = 1 if (flags & 2 or "italic" in f or "oblique" in f) else 0
    if "courier" in f or "mono" in f or (flags & 8):
        fam = "co"
    elif "times" in f or (flags & 4) or ("serif" in f and "sans" not in f):
        fam = "ti"
    else:
        fam = "he"
    return BASE14[(fam, bold, ital)]


def qcolor_to_tuple(c):
    return (c.redF(), c.greenF(), c.blueF())


def parse_range(text, n):
    pages = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            pages.extend(range(int(a) - 1, int(b)))
        else:
            pages.append(int(part) - 1)
    return [p for p in pages if 0 <= p < n]


# ====================================================================
# Widgets
# ====================================================================
class PageLabel(QLabel):
    pressed = pyqtSignal(QPoint)
    moved = pyqtSignal(QPoint)
    released = pyqtSignal(QPoint)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.overlay = None   # (kind, start, cur, pts)
        self.hits = []

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed.emit(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.moved.emit(e.pos())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.released.emit(e.pos())

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        for r in self.hits:
            p.fillRect(r, QColor(255, 200, 0, 110))
        if self.overlay:
            kind, a, b, pts = self.overlay
            p.setPen(QPen(QColor(30, 100, 255), 2, Qt.DashLine))
            if kind in ("rect", "select"):
                p.drawRect(QRect(a, b).normalized())
            elif kind == "ellipse":
                p.drawEllipse(QRect(a, b).normalized())
            elif kind == "line":
                p.drawLine(a, b)
            elif kind == "ink":
                p.setPen(QPen(QColor(30, 100, 255), 2))
                for i in range(1, len(pts)):
                    p.drawLine(pts[i - 1], pts[i])


class Scroll(QScrollArea):
    zoomed = pyqtSignal(int)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoomed.emit(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
        else:
            super().wheelEvent(e)


class SigCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(600, 200)
        self.img = QImage(600, 200, QImage.Format_ARGB32)
        self.img.fill(Qt.transparent)
        self.last = None
        self.bbox = None
        self.pen_color = QColor(10, 20, 140)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QColor(210, 210, 210))
        p.drawLine(20, 150, 580, 150)
        p.drawImage(0, 0, self.img)

    def _track(self, pt):
        if self.bbox is None:
            self.bbox = [pt.x(), pt.y(), pt.x(), pt.y()]
        else:
            b = self.bbox
            b[0] = min(b[0], pt.x()); b[1] = min(b[1], pt.y())
            b[2] = max(b[2], pt.x()); b[3] = max(b[3], pt.y())

    def mousePressEvent(self, e):
        self.last = e.pos()
        self._track(e.pos())

    def mouseMoveEvent(self, e):
        if self.last is None:
            return
        p = QPainter(self.img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.pen_color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(self.last, e.pos())
        p.end()
        self._track(e.pos())
        self.last = e.pos()
        self.update()

    def mouseReleaseEvent(self, e):
        self.last = None

    def clear(self):
        self.img.fill(Qt.transparent)
        self.bbox = None
        self.update()

    def png_bytes(self):
        if self.bbox is None:
            return None
        b = self.bbox
        r = QRect(b[0], b[1], b[2] - b[0], b[3] - b[1]).adjusted(-8, -8, 8, 8)
        r = r.intersected(self.img.rect())
        crop = self.img.copy(r)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        crop.save(buf, "PNG")
        return bytes(ba)


class SignatureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create your signature")
        self.result_png = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Draw your signature with the mouse / touch:"))
        self.canvas = SigCanvas()
        lay.addWidget(self.canvas)
        row = QHBoxLayout()
        for text, fn in [("Clear", self.canvas.clear),
                         ("Pen color", self.pick_color),
                         ("Load image...", self.load_image)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        ok = QPushButton("Use signature")
        ok.clicked.connect(self.accept_draw)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def pick_color(self):
        c = QColorDialog.getColor(self.canvas.pen_color, self)
        if c.isValid():
            self.canvas.pen_color = c

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Signature image", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            with open(path, "rb") as f:
                self.result_png = f.read()
            self.accept()

    def accept_draw(self):
        data = self.canvas.png_bytes()
        if not data:
            QMessageBox.information(self, "Signature", "Please draw something first.")
            return
        self.result_png = data
        self.accept()


# ====================================================================
# Main window
# ====================================================================
class PDFStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1350, 850)
        os.makedirs(APP_DIR, exist_ok=True)

        self.doc = None
        self.path = None
        self.dirty = False
        self.current = 0
        self.zoom = 1.3
        self.mode = None
        self.color = (0.1, 0.1, 0.1)
        self.hl_color = (1, 1, 0)
        self.undo_stack = []
        self.redo_stack = []
        self.hits = []
        self.hit_idx = -1
        self.dragging = False
        self.drag_start = QPoint()
        self.ink = []
        self.stamp_text = "APPROVED"
        self.pending_image = None
        self.signature_png = None
        self.unicode_font = None

        self._load_config()
        self._build_ui()
        self._build_menus()
        self._build_toolbars()
        self.update_title()
        self.statusBar().showMessage("File > Open (Ctrl+O) or File > New")

    # ------------------------------------------------------------ config
    def _load_config(self):
        try:
            if os.path.exists(CFG_PATH):
                with open(CFG_PATH) as f:
                    self.unicode_font = json.load(f).get("unicode_font")
            if os.path.exists(SIG_PATH):
                with open(SIG_PATH, "rb") as f:
                    self.signature_png = f.read()
        except Exception:
            pass

    def _save_config(self):
        try:
            with open(CFG_PATH, "w") as f:
                json.dump({"unicode_font": self.unicode_font}, f)
        except Exception:
            pass

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        self.thumbs = QListWidget()
        self.thumbs.setIconSize(QSize(90, 125))
        self.thumbs.setMaximumWidth(150)
        self.thumbs.currentRowChanged.connect(self.go)

        self.page_label = PageLabel()
        self.page_label.pressed.connect(self.on_press)
        self.page_label.moved.connect(self.on_move)
        self.page_label.released.connect(self.on_release)

        self.scroll = Scroll()
        self.scroll.setWidget(self.page_label)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { background: #6b6f76; }")
        self.scroll.zoomed.connect(lambda d: self.change_zoom(0.15 * d))

        split = QSplitter()
        split.addWidget(self.thumbs)
        split.addWidget(self.scroll)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

    def _act(self, menu, text, fn, shortcut=None):
        a = QAction(text, self)
        a.triggered.connect(lambda _=False: fn())
        if shortcut:
            a.setShortcut(shortcut)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()

        m = mb.addMenu("&File")
        self._act(m, "New blank PDF...", self.new_blank, "Ctrl+N")
        self._act(m, "New PDF from images...", self.new_from_images)
        self._act(m, "New PDF from text file...", self.new_from_text)
        m.addSeparator()
        self._act(m, "Open...", self.open_pdf, "Ctrl+O")
        self._act(m, "Save", self.save_pdf, "Ctrl+S")
        self._act(m, "Save As...", self.save_as, "Ctrl+Shift+S")
        self._act(m, "Save with password (AES-256)...", self.save_protected)
        self._act(m, "Save optimized (smaller size)...", self.save_optimized)
        m.addSeparator()
        self._act(m, "Export pages as images...", self.export_images)
        self._act(m, "Export text to .txt...", self.export_text)
        m.addSeparator()
        self._act(m, "Exit", self.close, "Ctrl+Q")

        m = mb.addMenu("&Edit")
        self._act(m, "Undo", self.undo, "Ctrl+Z")
        self._act(m, "Redo", self.redo, "Ctrl+Y")
        m.addSeparator()
        self._act(m, "Find...", self.find, "Ctrl+F")
        self._act(m, "Find next", lambda: self.find_next(1), "F3")
        self._act(m, "Find previous", lambda: self.find_next(-1), "Shift+F3")
        m.addSeparator()
        self._act(m, "Set Unicode font (Tamil / Hindi etc.)...", self.choose_unicode_font)

        m = mb.addMenu("&Page")
        self._act(m, "Rotate 90", self.rotate_page)
        self._act(m, "Delete page", self.delete_page)
        self._act(m, "Move up", lambda: self.move_page(-1))
        self._act(m, "Move down", lambda: self.move_page(1))
        self._act(m, "Insert blank page after", self.insert_blank)
        self._act(m, "Insert pages from another PDF...", self.insert_from_pdf)
        m.addSeparator()
        self._act(m, "Merge PDFs (append)...", self.merge_pdfs)
        self._act(m, "Extract pages to new PDF...", self.extract_pages)
        self._act(m, "Split into files every N pages...", self.split_every_n)
        m.addSeparator()
        self._act(m, "Add page numbers", self.add_page_numbers)
        self._act(m, "Add watermark...", self.add_watermark)
        self._act(m, "OCR (make scanned PDF searchable)...", self.ocr_doc)

        m = mb.addMenu("&Sign")
        self._act(m, "Create / change my signature...", self.create_signature)
        self._act(m, "Place signature", lambda: self.set_mode("signature"))
        m.addSeparator()
        self._act(m, "Create self-signed certificate (.p12)...", self.make_certificate)
        self._act(m, "Digitally sign (visible box)", lambda: self.set_mode("digisign"))
        self._act(m, "Digitally sign (invisible)", lambda: self.do_digital_sign(None))

        m = mb.addMenu("S&tamp")
        for name in STAMPS:
            self._act(m, name, lambda n=name: self.choose_stamp(n))
        m.addSeparator()
        self._act(m, "Approved + today's date", lambda: self.choose_stamp("DATE"))
        self._act(m, "Custom text...", lambda: self.choose_stamp(None))

        m = mb.addMenu("&View")
        self._act(m, "Zoom in", lambda: self.change_zoom(0.2), "Ctrl++")
        self._act(m, "Zoom out", lambda: self.change_zoom(-0.2), "Ctrl+-")
        self._act(m, "Fit width", self.fit_width, "Ctrl+0")
        self._act(m, "Next page", lambda: self.go(self.current + 1), "PgDown")
        self._act(m, "Previous page", lambda: self.go(self.current - 1), "PgUp")

    def _build_toolbars(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for label, key in MODES:
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addAction(a)
            tb.addAction(a)
            self.mode_actions[key] = a
        self.mode_actions[None].setChecked(True)

        self.addToolBarBreak()
        tb2 = QToolBar("Options")
        tb2.setMovable(False)
        self.addToolBar(tb2)

        tb2.addWidget(QLabel(" Page: "))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.valueChanged.connect(lambda v: self.go(v - 1))
        tb2.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0   ")
        tb2.addWidget(self.page_total)

        tb2.addSeparator()
        tb2.addWidget(QLabel(" Font size: "))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(4, 200)
        self.size_spin.setValue(14)
        tb2.addWidget(self.size_spin)
        tb2.addWidget(QLabel("  Line width: "))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 30)
        self.width_spin.setValue(2)
        tb2.addWidget(self.width_spin)

        tb2.addSeparator()
        a = QAction("Pen/Text color", self)
        a.triggered.connect(self.pick_color)
        tb2.addAction(a)
        a = QAction("Highlight color", self)
        a.triggered.connect(self.pick_hl_color)
        tb2.addAction(a)

    # ------------------------------------------------------------ helpers
    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        star = " *" if self.dirty else ""
        self.setWindowTitle(f"PDF Studio - {name}{star}")

    def msg(self, text):
        self.statusBar().showMessage(text)

    def need_doc(self):
        if not self.doc:
            self.msg("Open or create a PDF first")
            return False
        return True

    def push_undo(self):
        try:
            self.undo_stack.append(self.doc.tobytes())
            if len(self.undo_stack) > 20:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
        except Exception:
            pass

    def changed(self, structural=False):
        self.dirty = True
        self.update_title()
        if structural:
            self.refresh_all()
        else:
            self.render_page()
            self.update_thumb(self.current)

    def load_bytes(self, data, keep_page=True):
        self.doc = fitz.open(stream=data, filetype="pdf")
        if not keep_page:
            self.current = 0
        self.refresh_all()

    def undo(self):
        if not self.doc or not self.undo_stack:
            return
        self.redo_stack.append(self.doc.tobytes())
        self.load_bytes(self.undo_stack.pop())
        self.dirty = True
        self.update_title()

    def redo(self):
        if not self.doc or not self.redo_stack:
            return
        self.undo_stack.append(self.doc.tobytes())
        self.load_bytes(self.redo_stack.pop())
        self.dirty = True
        self.update_title()

    def maybe_save(self):
        if self.doc and self.dirty:
            r = QMessageBox.question(
                self, "Unsaved changes", "Save changes first?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Yes:
                self.save_pdf()
                return not self.dirty
        return True

    def closeEvent(self, e):
        if self.maybe_save():
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------- coordinates
    def to_pdf(self, qpos):
        page = self.doc[self.current]
        pt = fitz.Point(qpos.x() / self.zoom, qpos.y() / self.zoom)
        return pt * page.derotation_matrix

    def vrect(self, p1, p2):
        """Rect from two label points -> unrotated PDF coordinates."""
        page = self.doc[self.current]
        x0, x1 = sorted((p1.x() / self.zoom, p2.x() / self.zoom))
        y0, y1 = sorted((p1.y() / self.zoom, p2.y() / self.zoom))
        return fitz.Rect(x0, y0, x1, y1) * page.derotation_matrix

    def vrect_size(self, p, w, h):
        return self.vrect(p, QPoint(int(p.x() + w * self.zoom), int(p.y() + h * self.zoom)))

    # --------------------------------------------------------- rendering
    def _pix_to_qpixmap(self, pix):
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def render_page(self):
        if not self.doc:
            return
        page = self.doc[self.current]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        pm = self._pix_to_qpixmap(pix)
        self.page_label.setPixmap(pm)
        self.page_label.resize(pm.size())
        rects = []
        for pno, r in self.hits:
            if pno == self.current:
                rr = fitz.Rect(r) * page.rotation_matrix
                rects.append(QRect(int(rr.x0 * self.zoom), int(rr.y0 * self.zoom),
                                   int(rr.width * self.zoom), int(rr.height * self.zoom)))
        self.page_label.hits = rects
        self.page_label.update()
        self.msg(f"Page {self.current + 1}/{len(self.doc)}   Zoom {int(self.zoom * 100)}%   "
                 f"Mode: {HINTS.get(self.mode, '')}")

    def thumb_icon(self, i):
        pix = self.doc[i].get_pixmap(matrix=fitz.Matrix(0.2, 0.2), alpha=False)
        return QIcon(self._pix_to_qpixmap(pix))

    def update_thumb(self, i):
        if self.doc and 0 <= i < self.thumbs.count():
            self.thumbs.item(i).setIcon(self.thumb_icon(i))

    def refresh_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(len(self.doc)):
            self.thumbs.addItem(QListWidgetItem(self.thumb_icon(i), str(i + 1)))
        self.thumbs.setCurrentRow(self.current)
        self.thumbs.blockSignals(False)

    def refresh_all(self):
        if not self.doc:
            return
        self.current = max(0, min(self.current, len(self.doc) - 1))
        self.refresh_thumbs()
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(len(self.doc))
        self.page_spin.setValue(self.current + 1)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {len(self.doc)}   ")
        self.render_page()
        self.update_title()

    # -------------------------------------------------------- navigation
    def go(self, i):
        if not self.doc or not (0 <= i < len(self.doc)):
            return
        self.current = i
        self.thumbs.blockSignals(True)
        self.thumbs.setCurrentRow(i)
        self.thumbs.blockSignals(False)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.render_page()

    def change_zoom(self, d):
        self.zoom = max(0.3, min(5.0, self.zoom + d))
        self.render_page()

    def fit_width(self):
        if not self.doc:
            return
        w = self.doc[self.current].rect.width
        self.zoom = max(0.3, (self.scroll.viewport().width() - 24) / w)
        self.render_page()

    # --------------------------------------------------------- file ops
    def open_pdf(self):
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            doc = fitz.open(stream=data, filetype="pdf")
            if doc.needs_pass:
                while True:
                    pw, ok = QInputDialog.getText(
                        self, "Password", "This PDF is protected. Password:", QLineEdit.Password)
                    if not ok:
                        return
                    if doc.authenticate(pw):
                        break
                    QMessageBox.warning(self, "Password", "Wrong password.")
                data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
                doc = fitz.open(stream=data, filetype="pdf")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.doc = doc
        self.path = path
        self.current = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.hits = []
        self.dirty = False
        self.refresh_all()

    def save_pdf(self):
        if not self.need_doc():
            return
        if not self.path:
            return self.save_as()
        try:
            data = self.doc.tobytes(garbage=3, deflate=True)
            with open(self.path, "wb") as f:
                f.write(data)
            self.dirty = False
            self.update_title()
            self.msg("Saved")
        except Exception as e:
            QMessageBox.critical(self, "Save error", str(e))

    def save_as(self):
        if not self.need_doc():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save As", "", "PDF (*.pdf)")
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.path = path
            self.save_pdf()

    def save_protected(self):
        if not self.need_doc():
            return
        up, ok = QInputDialog.getText(self, "Password", "Password to OPEN the file:", QLineEdit.Password)
        if not ok or not up:
            return
        op, ok = QInputDialog.getText(self, "Owner password", "Owner (edit) password:", QLineEdit.Password)
        if not ok or not op:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save protected PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            data = self.doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                    user_pw=up, owner_pw=op, garbage=3, deflate=True)
            with open(path, "wb") as f:
                f.write(data)
            self.msg("Protected PDF saved")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def save_optimized(self):
        if not self.need_doc():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save optimized PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            before = len(self.doc.tobytes())
            data = self.doc.tobytes(garbage=4, deflate=True, deflate_images=True,
                                    deflate_fonts=True, clean=True)
            with open(path, "wb") as f:
                f.write(data)
            self.msg(f"Optimized: {before // 1024} KB -> {len(data) // 1024} KB")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ----------------------------------------------------------- create
    def new_blank(self):
        if not self.maybe_save():
            return
        size, ok = QInputDialog.getItem(
            self, "New PDF", "Page size:", ["A4", "A3", "A5", "Letter", "Legal"], 0, False)
        if not ok:
            return
        n, ok = QInputDialog.getInt(self, "New PDF", "Number of pages:", 1, 1, 500)
        if not ok:
            return
        w, h = fitz.paper_size(size.lower())
        doc = fitz.open()
        for _ in range(n):
            doc.new_page(width=w, height=h)
        self._adopt_new(doc)

    def _adopt_new(self, doc):
        self.doc = doc
        self.path = None
        self.current = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.hits = []
        self.dirty = True
        self.refresh_all()

    def new_from_images(self):
        if not self.maybe_save():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose images", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if not paths:
            return
        try:
            doc = fitz.open()
            for p in paths:
                img = fitz.open(p)
                pdfb = img.convert_to_pdf()
                doc.insert_pdf(fitz.open("pdf", pdfb))
            self._adopt_new(doc)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def new_from_text(self):
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Text file", "", "Text (*.txt *.md *.py *.csv *.log)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            size = 11
            if is_latin(text):
                font = fitz.Font("helv")
                kw = {"fontname": "helv"}
            elif self.unicode_font:
                font = fitz.Font(fontfile=self.unicode_font)
                kw = {"fontname": "ufont", "fontfile": self.unicode_font}
            else:
                QMessageBox.information(
                    self, "Unicode font needed",
                    "File has non-Latin text. Use Edit > Set Unicode font first.")
                return
            W, H, M = 595, 842, 50
            maxw = W - 2 * M
            lines = []
            for para in text.splitlines():
                words = para.split(" ")
                cur = ""
                for w in words:
                    t = (cur + " " + w).strip() if cur else w
                    if font.text_length(t, fontsize=size) <= maxw:
                        cur = t
                    else:
                        lines.append(cur)
                        cur = w
                lines.append(cur)
            per_page = int((H - 2 * M) // (size * 1.35))
            doc = fitz.open()
            for i in range(0, max(1, len(lines)), per_page):
                page = doc.new_page(width=W, height=H)
                y = M + size
                for ln in lines[i:i + per_page]:
                    if ln:
                        page.insert_text((M, y), ln, fontsize=size, **kw)
                    y += size * 1.35
            self._adopt_new(doc)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ----------------------------------------------------------- export
    def export_images(self):
        if not self.need_doc():
            return
        d = QFileDialog.getExistingDirectory(self, "Save images in folder")
        if not d:
            return
        for i, p in enumerate(self.doc):
            p.get_pixmap(dpi=150).save(os.path.join(d, f"page_{i + 1:03d}.png"))
        self.msg(f"Exported {len(self.doc)} images")

    def export_text(self):
        if not self.need_doc():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save text", "", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                for i, p in enumerate(self.doc):
                    f.write(f"--- Page {i + 1} ---\n{p.get_text()}\n")
            self.msg("Text exported")

    # ---------------------------------------------------------- options
    def pick_color(self):
        c = QColorDialog.getColor(QColor.fromRgbF(*self.color), self)
        if c.isValid():
            self.color = qcolor_to_tuple(c)

    def pick_hl_color(self):
        c = QColorDialog.getColor(QColor.fromRgbF(*self.hl_color), self)
        if c.isValid():
            self.hl_color = qcolor_to_tuple(c)

    def choose_unicode_font(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a .ttf/.otf font that has your script (e.g. NotoSansTamil)",
            "", "Fonts (*.ttf *.otf)")
        if path:
            self.unicode_font = path
            self._save_config()
            self.msg("Unicode font set")

    def set_mode(self, key):
        if key == "signature" and not self.signature_png:
            self.create_signature()
            if not self.signature_png:
                self.mode_actions[None].setChecked(True)
                return
        if key == "image":
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose image", "", "Images (*.png *.jpg *.jpeg)")
            if not path:
                self.mode_actions[None].setChecked(True)
                return
            self.pending_image = path
        self.mode = key
        self.mode_actions[key].setChecked(True)
        self.page_label.setCursor(Qt.ArrowCursor if key is None else Qt.CrossCursor)
        self.msg(HINTS.get(key, ""))

    def choose_stamp(self, name):
        if name is None:
            t, ok = QInputDialog.getText(self, "Custom stamp", "Stamp text (Latin letters):")
            if not ok or not t.strip():
                return
            name = t.strip().upper()
            STAMPS.setdefault(name, (0.1, 0.1, 0.6))
        self.stamp_text = name
        self.set_mode("stamp")

    # ---------------------------------------------------- mouse handling
    def on_press(self, pos):
        if not self.doc:
            return
        self.dragging = True
        self.drag_start = pos
        self.ink = [pos]

    def on_move(self, pos):
        if not self.dragging or self.mode is None:
            return
        m = self.mode
        if m == "draw":
            self.ink.append(pos)
            self.page_label.overlay = ("ink", None, None, list(self.ink))
        elif m in DRAG_MODES or m in ("signature", "image"):
            kind = "ellipse" if m == "ellipse" else "line" if m == "line" else "rect"
            self.page_label.overlay = (kind, self.drag_start, pos, None)
        self.page_label.update()

    def on_release(self, pos):
        if not self.doc or not self.dragging:
            return
        self.dragging = False
        self.page_label.overlay = None
        self.page_label.update()
        if self.mode is None:
            return
        start = self.drag_start
        moved = (pos - start).manhattanLength() > 6
        page = self.doc[self.current]
        try:
            if self.mode in DRAG_MODES:
                if not moved:
                    self.msg("Drag to select an area")
                    return
                self.apply_drag(page, self.mode, start, pos)
            else:
                self.apply_click(page, self.mode, start, pos, moved)
        except Exception as e:
            QMessageBox.warning(self, "Action failed", str(e))

    # ------------------------------------------------------ drag actions
    def apply_drag(self, page, m, start, pos):
        rect = self.vrect(start, pos)

        if m in ("highlight", "underline", "strike"):
            quads = []
            for w in page.get_text("words"):
                r = fitz.Rect(w[:4])
                if r.intersects(rect):
                    quads.append(r.quad)
            if not quads:
                self.msg("No text found in that area")
                return
            self.push_undo()
            fn = {"highlight": page.add_highlight_annot,
                  "underline": page.add_underline_annot,
                  "strike": page.add_strikeout_annot}[m]
            an = fn(quads=quads)
            an.set_colors(stroke=self.hl_color if m == "highlight" else self.color)
            an.update()

        elif m == "draw":
            pts = [tuple(self.to_pdf(p)) for p in self.ink]
            if len(pts) < 2:
                return
            self.push_undo()
            an = page.add_ink_annot([pts])
            an.set_colors(stroke=self.color)
            an.set_border(width=self.width_spin.value())
            an.update()

        elif m in ("rect", "ellipse"):
            self.push_undo()
            an = page.add_rect_annot(rect) if m == "rect" else page.add_circle_annot(rect)
            an.set_colors(stroke=self.color)
            an.set_border(width=self.width_spin.value())
            an.update()

        elif m == "line":
            self.push_undo()
            an = page.add_line_annot(self.to_pdf(start), self.to_pdf(pos))
            an.set_colors(stroke=self.color)
            an.set_border(width=self.width_spin.value())
            an.update()

        elif m == "redact":
            r = QMessageBox.question(
                self, "Redact", "This permanently removes the content under the box. Continue?")
            if r != QMessageBox.Yes:
                return
            self.push_undo()
            page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions()

        elif m == "digisign":
            self.do_digital_sign(rect)
            return

        self.changed()

    # ----------------------------------------------------- click actions
    def apply_click(self, page, m, start, pos, moved):
        pt = self.to_pdf(start)

        if m == "text":
            text, ok = QInputDialog.getMultiLineText(self, "Add text", "Text:")
            if not ok or not text.strip():
                return
            rect = self.vrect_size(start, min(420, page.rect.width - 20), 400)
            self.push_undo()
            if not self.place_text(page, rect, text, self.size_spin.value(),
                                   self.color, "helv", shrink=False):
                return
            self.changed()

        elif m in ("edit_line", "edit_para"):
            self.edit_text_at(page, pt, m == "edit_para")

        elif m == "note":
            text, ok = QInputDialog.getMultiLineText(self, "Note", "Comment:")
            if ok and text.strip():
                self.push_undo()
                an = page.add_text_annot(pt, text)
                an.set_colors(stroke=(1, 0.8, 0))
                an.update()
                self.changed()

        elif m == "signature":
            if not self.signature_png:
                return
            img = QImage.fromData(self.signature_png)
            if moved:
                rect = self.vrect(start, pos)
            else:
                w = 160
                h = w * img.height() / max(1, img.width())
                rect = self.vrect_size(start, w, h)
            self.push_undo()
            page.insert_image(rect, stream=self.signature_png,
                              keep_proportion=True, overlay=True, rotate=page.rotation)
            self.changed()

        elif m == "image":
            if not self.pending_image:
                return
            if moved:
                rect = self.vrect(start, pos)
            else:
                rect = self.vrect_size(start, 150, 150)
            self.push_undo()
            page.insert_image(rect, filename=self.pending_image,
                              keep_proportion=True, overlay=True, rotate=page.rotation)
            self.changed()

        elif m == "stamp":
            self.place_stamp(page, start)

        elif m == "form":
            self.fill_form(page, pt)

    # ------------------------------------------------------ text helpers
    def place_text(self, page, rect, text, size, color, base="helv", shrink=True):
        """Insert text in rect. Handles non-Latin via Unicode font or HTML."""
        rot = page.rotation
        try:
            if is_latin(text) or self.unicode_font:
                if is_latin(text):
                    kw = {"fontname": base}
                else:
                    kw = {"fontname": "ufont", "fontfile": self.unicode_font}
                s = size
                while s >= 4:
                    rc = page.insert_textbox(rect, text, fontsize=s, color=color,
                                             rotate=rot, **kw)
                    if rc >= 0:
                        return True
                    if not shrink:
                        break
                    s -= 1
                # last resort: write at top-left without wrapping
                page.insert_text(fitz.Point(rect.x0, rect.y0 + size), text,
                                 fontsize=size, color=color, **kw)
                return True
            if hasattr(page, "insert_htmlbox"):
                col = "#%02x%02x%02x" % tuple(int(c * 255) for c in color)
                body = "<br>".join(html.escape(t) for t in text.split("\n"))
                css = f"* {{ font-size: {size}pt; color: {col}; }}"
                page.insert_htmlbox(rect, f"<p>{body}</p>", css=css, rotate=rot)
                return True
        except Exception as e:
            QMessageBox.warning(self, "Text", str(e))
            return False
        path, _ = QFileDialog.getOpenFileName(
            self, "Text has non-Latin letters. Choose a font file (.ttf) that supports them",
            "", "Fonts (*.ttf *.otf)")
        if path:
            self.unicode_font = path
            self._save_config()
            return self.place_text(page, rect, text, size, color, base, shrink)
        return False

    def edit_text_at(self, page, pt, paragraph):
        if page.rotation != 0:
            QMessageBox.information(self, "Edit text",
                                    "Text editing works on unrotated pages. Rotate page back to 0 first.")
            return
        d = page.get_text("dict")
        found = None
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                if (fitz.Rect(ln["bbox"]) + (-2, -2, 2, 2)).contains(pt):
                    found = (b, ln)
                    break
            if found:
                break
        if not found:
            self.msg("No editable text here (scanned page? use Page > OCR)")
            return
        b, ln = found
        first = ln["spans"][0] if paragraph is False else b["lines"][0]["spans"][0]
        size = first["size"]
        color = fitz.sRGB_to_pdf(first["color"])
        base = base14_name(first["font"], first["flags"])

        if paragraph:
            old = " ".join("".join(s["text"] for s in l["spans"]).strip() for l in b["lines"])
            area = fitz.Rect(b["bbox"])
        else:
            old = "".join(s["text"] for s in ln["spans"])
            area = fitz.Rect(ln["bbox"])

        new, ok = QInputDialog.getMultiLineText(self, "Edit text", "Change text:", old)
        if not ok or new == old:
            return

        self.push_undo()
        redact_rect = area + (0.5, 0.5, -0.5, -0.5)
        page.add_redact_annot(redact_rect, fill=False)
        try:
            page.apply_redactions(images=0, graphics=0)
        except TypeError:
            page.apply_redactions()

        if paragraph:
            rect = fitz.Rect(area.x0, area.y0, max(area.x1, area.x0 + 40), area.y1 + size * 2)
            self.place_text(page, rect, new, size, color, base, shrink=True)
        else:
            if is_latin(new):
                page.insert_text(fitz.Point(*ln["spans"][0]["origin"]), new,
                                 fontsize=size, fontname=base, color=color)
            else:
                rect = fitz.Rect(area.x0, area.y0, page.rect.x1 - 15, area.y1 + size)
                self.place_text(page, rect, new, size, color, base, shrink=True)
        self.changed()

    # ----------------------------------------------------------- stamps
    def place_stamp(self, page, start):
        text = self.stamp_text
        if text == "DATE":
            text = "APPROVED " + datetime.datetime.now().strftime("%d-%b-%Y")
            color = STAMPS["APPROVED"]
        else:
            color = STAMPS.get(text, (0.1, 0.1, 0.6))
        if not is_latin(text):
            QMessageBox.information(self, "Stamp", "Stamps support Latin letters only.")
            return
        fs = 26
        w = fitz.get_text_length(text, fontname="hebo", fontsize=fs) + 28
        h = 46
        rect = self.vrect_size(QPoint(int(start.x() - w * self.zoom / 2),
                                      int(start.y() - h * self.zoom / 2)), w, h)
        self.push_undo()
        page.draw_rect(rect, color=color, width=3, overlay=True)
        inner = fitz.Rect(rect.x0, rect.y0 + (rect.height - fs * 1.25) / 2, rect.x1, rect.y1)
        page.insert_textbox(inner, text, fontsize=fs, fontname="hebo",
                            color=color, align=1, rotate=page.rotation)
        self.changed()

    # ------------------------------------------------------------ forms
    def fill_form(self, page, pt):
        for w in page.widgets():
            if not w.rect.contains(pt):
                continue
            t = w.field_type_string
            if t == "Text":
                val, ok = QInputDialog.getText(self, w.field_name, "Value:", QLineEdit.Normal,
                                               str(w.field_value or ""))
                if not ok:
                    return
                self.push_undo()
                w.field_value = val
                w.update()
            elif t in ("CheckBox", "RadioButton"):
                cur = w.field_value
                on = cur not in (False, "Off", None, "")
                self.push_undo()
                w.field_value = "Off" if on else w.on_state()
                w.update()
            elif t in ("ComboBox", "ListBox"):
                vals = [str(v if not isinstance(v, (list, tuple)) else v[0])
                        for v in (w.choice_values or [])]
                val, ok = QInputDialog.getItem(self, w.field_name, "Choose:", vals, 0, False)
                if not ok:
                    return
                self.push_undo()
                w.field_value = val
                w.update()
            else:
                self.msg(f"Field type {t} not supported")
                return
            self.changed()
            return
        self.msg("No form field here")

    # -------------------------------------------------------- signature
    def create_signature(self):
        dlg = SignatureDialog(self)
        if dlg.exec_() == QDialog.Accepted and dlg.result_png:
            self.signature_png = dlg.result_png
            try:
                with open(SIG_PATH, "wb") as f:
                    f.write(self.signature_png)
            except Exception:
                pass
            self.msg("Signature saved. Use Sign > Place signature.")

    def make_certificate(self):
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives.serialization import pkcs12
        except ImportError:
            QMessageBox.information(self, "Missing library", "Run: pip install cryptography")
            return
        cn, ok = QInputDialog.getText(self, "Certificate", "Your full name:")
        if not ok or not cn.strip():
            return
        pw, ok = QInputDialog.getText(self, "Certificate", "Choose a password:", QLineEdit.Password)
        if not ok or not pw:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save certificate", "my_id.p12", "PKCS12 (*.p12)")
        if not path:
            return
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn.strip())])
        now = datetime.datetime.utcnow()
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=365 * 5))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        data = pkcs12.serialize_key_and_certificates(
            cn.strip().encode(), key, cert, None,
            serialization.BestAvailableEncryption(pw.encode()))
        with open(path, "wb") as f:
            f.write(data)
        QMessageBox.information(
            self, "Certificate created",
            "Self-signed certificate saved.\nAdobe will show it as 'not trusted' until the "
            "receiver trusts it. For fully trusted signatures, use a certificate from a CA.")

    def do_digital_sign(self, rect):
        if not self.need_doc():
            return
        try:
            from pyhanko.sign import signers
            from pyhanko.sign.fields import SigFieldSpec, append_signature_field
            from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
            from pyhanko.stamp import TextStampStyle
        except ImportError:
            QMessageBox.information(self, "Missing library", "Run: pip install pyhanko")
            return
        p12, _ = QFileDialog.getOpenFileName(self, "Choose certificate", "", "Certificate (*.p12 *.pfx)")
        if not p12:
            return
        pw, ok = QInputDialog.getText(self, "Certificate", "Certificate password:", QLineEdit.Password)
        if not ok:
            return
        reason, _ok = QInputDialog.getText(self, "Signature", "Reason (optional):")
        out, _ = QFileDialog.getSaveFileName(self, "Save signed PDF", "signed.pdf", "PDF (*.pdf)")
        if not out:
            return
        try:
            signer = signers.SimpleSigner.load_pkcs12(pfx_file=p12, passphrase=pw.encode())
            data = self.doc.tobytes(garbage=3, deflate=True)
            w = IncrementalPdfFileWriter(io.BytesIO(data))
            fname = "Sig_" + datetime.datetime.now().strftime("%H%M%S")
            meta = signers.PdfSignatureMetadata(field_name=fname, reason=reason or None)
            style = None
            if rect is not None:
                page = self.doc[self.current]
                H = page.mediabox.height
                append_signature_field(w, SigFieldSpec(
                    fname, on_page=self.current,
                    box=(int(rect.x0), int(H - rect.y1), int(rect.x1), int(H - rect.y0))))
                style = TextStampStyle(
                    stamp_text="Digitally signed by: %(signer)s\nDate: %(ts)s")
            pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style) \
                if style else signers.PdfSigner(meta, signer=signer)
            buf = pdf_signer.sign_pdf(w)
            with open(out, "wb") as f:
                f.write(buf.getbuffer())
            QMessageBox.information(
                self, "Signed",
                f"Signed PDF saved:\n{out}\n\nDo NOT edit this file again - editing breaks the signature.")
        except Exception as e:
            QMessageBox.critical(self, "Signing failed", str(e))

    # ------------------------------------------------------ page actions
    def rotate_page(self):
        if not self.need_doc():
            return
        self.push_undo()
        p = self.doc[self.current]
        p.set_rotation((p.rotation + 90) % 360)
        self.changed()

    def delete_page(self):
        if not self.need_doc() or len(self.doc) <= 1:
            return
        self.push_undo()
        self.doc.delete_page(self.current)
        self.changed(structural=True)

    def move_page(self, d):
        if not self.need_doc():
            return
        n = len(self.doc)
        new = self.current + d
        if not (0 <= new < n):
            return
        self.push_undo()
        if d < 0:
            self.doc.move_page(self.current, new)
        else:
            self.doc.move_page(self.current, -1 if new == n - 1 else new + 1)
        self.current = new
        self.changed(structural=True)

    def insert_blank(self):
        if not self.need_doc():
            return
        self.push_undo()
        r = self.doc[self.current].rect
        self.doc.new_page(pno=self.current + 1, width=r.width, height=r.height)
        self.current += 1
        self.changed(structural=True)

    def insert_from_pdf(self):
        if not self.need_doc():
            return
        path, _ = QFileDialog.getOpenFileName(self, "PDF to insert", "", "PDF (*.pdf)")
        if not path:
            return
        other = fitz.open(path)
        if other.needs_pass:
            QMessageBox.warning(self, "Insert", "That PDF is password protected.")
            return
        self.push_undo()
        self.doc.insert_pdf(other, start_at=self.current + 1)
        self.changed(structural=True)

    def merge_pdfs(self):
        if not self.need_doc():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to append", "", "PDF (*.pdf)")
        if not paths:
            return
        self.push_undo()
        for p in paths:
            other = fitz.open(p)
            if other.needs_pass:
                QMessageBox.warning(self, "Merge", f"Skipped (protected): {p}")
                continue
            self.doc.insert_pdf(other)
        self.changed(structural=True)

    def extract_pages(self):
        if not self.need_doc():
            return
        txt, ok = QInputDialog.getText(self, "Extract", "Pages (example 1-3,5):")
        if not ok or not txt.strip():
            return
        try:
            pages = parse_range(txt, len(self.doc))
        except ValueError:
            QMessageBox.warning(self, "Extract", "Invalid range")
            return
        if not pages:
            return
        out, _ = QFileDialog.getSaveFileName(self, "Save extracted PDF", "", "PDF (*.pdf)")
        if not out:
            return
        new = fitz.open()
        for p in pages:
            new.insert_pdf(self.doc, from_page=p, to_page=p)
        new.save(out)
        self.msg(f"Extracted {len(pages)} pages")

    def split_every_n(self):
        if not self.need_doc():
            return
        n, ok = QInputDialog.getInt(self, "Split", "Pages per file:", 1, 1, len(self.doc))
        if not ok:
            return
        d = QFileDialog.getExistingDirectory(self, "Save split files in folder")
        if not d:
            return
        count = 0
        for i in range(0, len(self.doc), n):
            part = fitz.open()
            part.insert_pdf(self.doc, from_page=i, to_page=min(i + n, len(self.doc)) - 1)
            part.save(os.path.join(d, f"part_{count + 1:03d}.pdf"))
            count += 1
        self.msg(f"Created {count} files")

    def add_page_numbers(self):
        if not self.need_doc():
            return
        self.push_undo()
        n = len(self.doc)
        for i, p in enumerate(self.doc):
            r = p.rect
            p.insert_textbox(fitz.Rect(0, r.height - 32, r.width, r.height - 12),
                             f"{i + 1} / {n}", fontsize=10, fontname="helv", align=1)
        self.changed(structural=True)

    def add_watermark(self):
        if not self.need_doc():
            return
        text, ok = QInputDialog.getText(self, "Watermark", "Text:", QLineEdit.Normal, "CONFIDENTIAL")
        if not ok or not text.strip() or not is_latin(text):
            return
        self.push_undo()
        fs = 60
        tw = fitz.get_text_length(text, fontname="hebo", fontsize=fs)
        for p in self.doc:
            c = p.rect.center
            pt = fitz.Point(c.x - tw / 2, c.y + fs / 3)
            kw = dict(fontsize=fs, fontname="hebo", color=(0.6, 0.6, 0.6),
                      morph=(c, fitz.Matrix(-45)))
            try:
                p.insert_text(pt, text, fill_opacity=0.3, **kw)
            except TypeError:
                p.insert_text(pt, text, **kw)
        self.changed(structural=True)

    def ocr_doc(self):
        if not self.need_doc():
            return
        lang, ok = QInputDialog.getText(
            self, "OCR", "Language code (eng, tam, eng+tam):", QLineEdit.Normal, "eng")
        if not ok or not lang.strip():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out = fitz.open()
            for p in self.doc:
                pix = p.get_pixmap(dpi=200)
                out.insert_pdf(fitz.open("pdf", pix.pdfocr_tobytes(language=lang.strip())))
            data = out.tobytes()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, "OCR failed",
                f"{e}\n\nInstall Tesseract OCR (with language data, e.g. 'tam') and set "
                f"TESSDATA_PREFIX if needed.")
            return
        QApplication.restoreOverrideCursor()
        self.push_undo()
        self.load_bytes(data)
        self.dirty = True
        self.update_title()
        self.msg("OCR done - text is now searchable / editable")

    # ------------------------------------------------------------- find
    def find(self):
        if not self.need_doc():
            return
        term, ok = QInputDialog.getText(self, "Find", "Search for:")
        if not ok or not term:
            return
        self.hits = []
        for i, p in enumerate(self.doc):
            for r in p.search_for(term):
                self.hits.append((i, r))
        self.hit_idx = -1
        if not self.hits:
            self.render_page()
            QMessageBox.information(self, "Find", "Not found")
            return
        self.msg(f"{len(self.hits)} matches")
        self.find_next(1)

    def find_next(self, step):
        if not self.hits:
            return
        self.hit_idx = (self.hit_idx + step) % len(self.hits)
        pno, r = self.hits[self.hit_idx]
        self.go(pno)
        rr = fitz.Rect(r) * self.doc[pno].rotation_matrix
        self.scroll.ensureVisible(int(rr.x0 * self.zoom), int(rr.y0 * self.zoom), 60, 120)
        self.msg(f"Match {self.hit_idx + 1}/{len(self.hits)}")


def main():
    app = QApplication(sys.argv)
    w = PDFStudio()
    w.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        w.open_path(sys.argv[1])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
