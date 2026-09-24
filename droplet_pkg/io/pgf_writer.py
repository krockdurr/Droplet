"""PGF output for anything Qt can paint.

PgfPaintDevice is a QPaintDevice: point a QPainter at it (for example
QWidget.render(painter)) and every line, path, fill, clip, text and image is
recorded as PGF drawing commands, then written with save(). The result is
the same picture as on screen, but with native LaTeX text and vector paths:

    \\usepackage{pgf}
    ...
    \\input{plot.pgf}                                   % natural size
    \\resizebox{\\linewidth}{!}{\\input{plot.pgf}}         % fit the text width

Nothing is compiled while exporting, so no LaTeX installation is needed.
Text is typeset by LaTeX in the document's font, anchored at the centre of
where Qt drew it, so slightly different font widths stay balanced.
Bitmaps (e.g. scatter symbols) are written next to the .pgf file as
<name>-img<N>.png; like matplotlib's PGF files, LaTeX looks for them relative
to the main document, so keep them in the same folder as the .tex file or
use \\import from the import package.
"""

import math
import os

try:
    from PyQt6 import QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtCore, QtGui

_DPI = 96.0                   # screen logical DPI; 1 px = 72/96 bp
# Units are PostScript points (TeX "bp", 1/72 in) like Qt's, not TeX "pt".
_PT = 72.0 / _DPI
_E = QtGui.QPainterPath.ElementType
_Dirty = QtGui.QPaintEngine.DirtyFlag

# Characters that are special in LaTeX, and common symbols pdfLaTeX cannot
# typeset directly (XeLaTeX / LuaLaTeX would, but these work everywhere).
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "#": r"\#", "%": r"\%", "_": r"\_", "^": r"\^{}", "~": r"\~{}",
    "-": "-{}",                       # keep "---" as hyphens, not a dash ligature
    "×": r"$\times$", "Δ": r"$\Delta$", "δ": r"$\delta$", "µ": r"$\mu$",
    "μ": r"$\mu$", "σ": r"$\sigma$", "λ": r"$\lambda$", "±": r"$\pm$",
    "−": r"$-$", "→": r"$\rightarrow$", "←": r"$\leftarrow$", "·": r"$\cdot$",
    "°": r"$^{\circ}$", "≈": r"$\approx$", "≥": r"$\geq$", "≤": r"$\leq$",
    "…": r"\ldots{}", "–": "--", "—": "---", "\u00a0": "~", "●": r"$\bullet$",
    "•": r"$\bullet$", "′": r"$'$", "Å": r"\AA{}",
}

# Unicode super- / subscripts. Consecutive ones become one group, so that
# (NaOH)₁₂ gives (NaOH)$_{12}$ and OH²⁻ gives OH$^{2-}$.
_SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
_SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₙₐₑₒₓₕₖₗₘₚₛₜ", "0123456789+-=()naeoxhklmpst"))


def _latex_char(ch: str) -> str:
    if ch in _LATEX_ESCAPES:
        return _LATEX_ESCAPES[ch]
    if ord(ch) < 0x100:                  # ASCII / Latin-1: fine for every engine
        return ch
    # Anything else (emoji, rare symbols): printed by XeLaTeX / LuaLaTeX,
    # silently left out by pdfLaTeX (which has no glyph and would stop).
    return r"\ifdefined\Umathcode " + ch + r"\fi{}"


def latex_escape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        for table, op in ((_SUPERSCRIPTS, "^"), (_SUBSCRIPTS, "_")):
            if text[i] in table:
                j = i
                while j < len(text) and text[j] in table:
                    j += 1
                out.append("$" + op + "{" + "".join(table[c] for c in text[i:j]) + "}$")
                i = j
                break
        else:
            out.append(_latex_char(text[i]))
            i += 1
    return "".join(out)


# How text is anchored to where Qt drew it: "center" (default; balances a
# LaTeX font of a slightly different width) or "left" (text that must start
# exactly where Qt started it, e.g. legend labels next to their symbol).
# Set it around drawText() calls with text_anchor("left").
_text_anchor = ["center"]


class text_anchor:
    """Context manager: with text_anchor("left"): painter.drawText(...)"""
    def __init__(self, mode):
        self._mode = mode

    def __enter__(self):
        self._prev = _text_anchor[0]
        _text_anchor[0] = self._mode

    def __exit__(self, *exc):
        _text_anchor[0] = self._prev


def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


class _PgfEngine(QtGui.QPaintEngine):
    def __init__(self, device):
        super().__init__(QtGui.QPaintEngine.PaintEngineFeature.AllFeatures)
        self._dev = device
        self._tf = QtGui.QTransform()
        self._pen = QtGui.QPen()
        self._brush = QtGui.QBrush()
        self._opacity = 1.0
        self._clip = None                # QPainterPath in device pixels, or None
        self._clip_enabled = True
        self._run_key = None             # pending stroke-only lines with one style
        self._run: list[list[tuple[float, float]]] = []
        self._run_state = None           # the pen/clip/... those lines were drawn with
        self._text_run = None            # text pieces being joined (drawTextItem)

    # ── QPaintEngine interface ──

    def begin(self, device):
        return True

    def end(self):
        self._flush_text()
        self._flush_run()
        return True

    def type(self):
        return QtGui.QPaintEngine.Type.User

    def updateState(self, st):
        f = st.state()
        if f & _Dirty.DirtyTransform:
            self._tf = st.transform()
        if f & _Dirty.DirtyPen:
            self._pen = QtGui.QPen(st.pen())
        if f & _Dirty.DirtyBrush:
            self._brush = QtGui.QBrush(st.brush())
        if f & _Dirty.DirtyOpacity:
            self._opacity = st.opacity()
        if f & _Dirty.DirtyClipEnabled:
            self._clip_enabled = st.isClipEnabled()
        if f & _Dirty.DirtyClipPath:
            self._set_clip(st.clipPath(), st.clipOperation())
        if f & _Dirty.DirtyClipRegion:
            path = QtGui.QPainterPath()
            path.addRegion(st.clipRegion())
            self._set_clip(path, st.clipOperation())

    def drawPath(self, path):
        stroke = self._pen.style() != QtCore.Qt.PenStyle.NoPen
        fill = self._brush.style() != QtCore.Qt.BrushStyle.NoBrush
        if stroke or fill:
            self._emit_path(self._tf.map(path), stroke, fill, path.fillRule())

    def drawPolygon(self, points, mode):
        if len(points) < 2:
            return
        path = QtGui.QPainterPath(QtCore.QPointF(points[0]))
        for p in points[1:]:
            path.lineTo(QtCore.QPointF(p))
        M = QtGui.QPaintEngine.PolygonDrawMode
        if mode == M.PolylineMode:
            if self._pen.style() != QtCore.Qt.PenStyle.NoPen:
                self._emit_path(self._tf.map(path), True, False, QtCore.Qt.FillRule.OddEvenFill)
            return
        path.closeSubpath()
        rule = (QtCore.Qt.FillRule.WindingFill if mode == M.WindingMode
                else QtCore.Qt.FillRule.OddEvenFill)
        path.setFillRule(rule)
        self.drawPath(path)

    # ── long stroked lines ──
    # pyqtgraph draws thick curves as thousands of two-point segments and thin
    # ones as a single path over every data point, including those outside the
    # visible area. Stroke-only lines of the same style are therefore merged,
    # points outside the clip are dropped, and within each half-pixel column
    # only the first, lowest, highest and last point are kept. The window
    # shows at most one pixel's worth, so the picture does not change.

    _COLUMN = 0.5          # device pixels
    _MIN_POINTS = 200      # shorter lines (grid, ticks, shapes) are left alone

    def _subpaths(self, path):
        """Point lists of a path made only of move/line elements, else None."""
        subs = []
        for i in range(path.elementCount()):
            e = path.elementAt(i)
            if e.type == _E.MoveToElement:
                subs.append([(e.x, e.y)])
            elif e.type == _E.LineToElement and subs:
                subs[-1].append((e.x, e.y))
            else:
                return None
        return subs

    def _simplify(self, pts):
        if len(pts) < self._MIN_POINTS:
            return [pts]
        clip = (self._clip.boundingRect()
                if self._clip_enabled and self._clip is not None else None)
        pieces = [pts]
        if clip is not None:
            lo, hi = clip.left() - 2, clip.right() + 2
            inside = [lo <= x <= hi for x, _ in pts]
            pieces, cur = [], []
            for i, p in enumerate(pts):
                if (inside[i] or (i > 0 and inside[i - 1])
                        or (i + 1 < len(pts) and inside[i + 1])):
                    cur.append(p)
                elif cur:
                    pieces.append(cur); cur = []
            if cur:
                pieces.append(cur)
        out = []
        for piece in pieces:
            kept, group, col = [], [], None
            for p in piece:
                c = int(p[0] // self._COLUMN)
                if c != col and group:
                    kept.extend(self._column_points(group))
                    group = []
                col = c
                group.append(p)
            kept.extend(self._column_points(group))
            out.append(kept)
        return out

    @staticmethod
    def _column_points(group):
        if len(group) <= 4:
            return group
        i_lo = min(range(len(group)), key=lambda i: group[i][1])
        i_hi = max(range(len(group)), key=lambda i: group[i][1])
        idx = sorted({0, i_lo, i_hi, len(group) - 1})
        return [group[i] for i in idx]

    def _stroke_key(self):
        pen = self._pen
        clip = self._clip if (self._clip_enabled and self._clip is not None) else None
        return (pen.color().rgba(), pen.widthF(), pen.isCosmetic(), pen.style(),
                pen.capStyle(), pen.joinStyle(), tuple(pen.dashPattern()),
                round(self._opacity, 4), round(math.sqrt(abs(self._tf.determinant())), 6),
                None if clip is None else tuple(
                    (clip.elementAt(i).x, clip.elementAt(i).y)
                    for i in range(clip.elementCount())))

    def _snapshot(self):
        return (QtGui.QPen(self._pen), self._opacity, QtGui.QTransform(self._tf),
                self._clip, self._clip_enabled)

    def _flush_run(self):
        if not self._run:
            return
        path = QtGui.QPainterPath()
        for sub in self._run:
            path.moveTo(*sub[0])
            for x, y in sub[1:]:
                path.lineTo(x, y)
        run_state, self._run, self._run_key = self._run_state, [], None
        # Qt has usually moved on to the next pen by now: draw with the old one.
        current = self._snapshot()
        self._pen, self._opacity, self._tf, self._clip, self._clip_enabled = run_state
        try:
            self._write_path(path, True, False, QtCore.Qt.FillRule.OddEvenFill)
        finally:
            self._pen, self._opacity, self._tf, self._clip, self._clip_enabled = current

    def drawPixmap(self, rect, pixmap, source):
        self._emit_image(rect, pixmap.copy(source.toRect()).toImage())

    def drawImage(self, rect, image, source, flags=None):
        self._emit_image(rect, image.copy(source.toRect()))

    def drawTiledPixmap(self, rect, pixmap, offset):
        self._emit_image(rect, pixmap.toImage())

    def drawTextItem(self, pos, item):
        """Qt draws one string in several pieces when some characters come from
        a fallback font (super- / subscripts, symbols). Pieces that follow each
        other on the same line are joined back into one LaTeX text."""
        text = item.text()
        if not text:
            return
        font = item.font()
        width = QtGui.QFontMetricsF(font).horizontalAdvance(text)
        run = self._text_run
        if (run is not None and run["tf"] == self._tf and abs(run["y"] - pos.y()) < 0.5
                and abs(run["end"] - pos.x()) < 1.5
                and run["color"] == self._pen.color().rgba()):
            run["text"] += text
            run["end"] = pos.x() + width
            return
        self._flush_text()
        self._run_flushed_paths()
        self._text_run = {
            "text": text, "start": pos.x(), "end": pos.x() + width, "y": pos.y(),
            "tf": QtGui.QTransform(self._tf), "font": QtGui.QFont(font),
            "color": self._pen.color().rgba(), "qcolor": QtGui.QColor(self._pen.color()),
            "opacity": self._opacity, "anchor": _text_anchor[0],
            "clip": self._clip, "clip_enabled": self._clip_enabled,
        }

    def _run_flushed_paths(self):
        self._flush_run()

    def _flush_text(self):
        run, self._text_run = self._text_run, None
        if run is None or not run["text"].strip():
            return
        tf, font, color = run["tf"], run["font"], run["qcolor"]
        left = run["anchor"] == "left"
        x_anchor = run["start"] if left else (run["start"] + run["end"]) / 2
        anchor_pt = tf.map(QtCore.QPointF(x_anchor, run["y"]))
        angle = -math.degrees(math.atan2(tf.m12(), tf.m11()))
        scale = math.hypot(tf.m11(), tf.m12()) or 1.0
        if font.pointSizeF() > 0:
            size_pt = font.pointSizeF() * scale
        else:
            size_pt = font.pixelSize() * _PT * scale
        style = (r"\bfseries" if font.bold() else "") + (r"\itshape" if font.italic() else "")
        x, y = self._pt(anchor_pt)
        rot = f",rotate={_num(angle)}" if abs(angle) > 0.01 else ""
        saved = (self._clip, self._clip_enabled)
        self._clip, self._clip_enabled = run["clip"], run["clip_enabled"]
        self._begin_scope()
        self._clip, self._clip_enabled = saved
        self._set_color("currenttext", color)
        opacity = run["opacity"] * color.alphaF()
        self._dev._out.append(
            (rf"\pgfsetfillopacity{{{_num(opacity)}}}" if opacity < 1 else "")
            + rf"\pgftext[{'left,' if left else ''}base,at={{\pgfqpoint{{{_num(x)}bp}}{{{_num(y)}bp}}}}{rot}]"
            rf"{{\color{{currenttext}}\fontsize{{{_num(size_pt)}bp}}{{{_num(size_pt * 1.2)}bp}}"
            rf"\selectfont{style}{{}}{latex_escape(run['text'])}}}%")
        self._end_scope()

    # ── helpers ──

    def _set_clip(self, path, op):
        Op = QtCore.Qt.ClipOperation
        mapped = self._tf.map(path)
        if op == Op.NoClip:
            self._clip = None
        elif op == Op.IntersectClip and self._clip is not None:
            self._clip = self._clip.intersected(mapped)
        else:
            self._clip = mapped

    def _pt(self, p):
        return p.x() * _PT, (self._dev.height_px - p.y()) * _PT

    def _path_commands(self, path):
        out, i, n = [], 0, path.elementCount()
        while i < n:
            e = path.elementAt(i)
            x, y = self._pt(QtCore.QPointF(e.x, e.y))
            if e.type == _E.MoveToElement:
                out.append(rf"\pgfpathmoveto{{\pgfqpoint{{{_num(x)}bp}}{{{_num(y)}bp}}}}%")
                i += 1
            elif e.type == _E.LineToElement:
                out.append(rf"\pgfpathlineto{{\pgfqpoint{{{_num(x)}bp}}{{{_num(y)}bp}}}}%")
                i += 1
            elif e.type == _E.CurveToElement and i + 2 < n:
                c2 = path.elementAt(i + 1); end = path.elementAt(i + 2)
                x2, y2 = self._pt(QtCore.QPointF(c2.x, c2.y))
                x3, y3 = self._pt(QtCore.QPointF(end.x, end.y))
                out.append(rf"\pgfpathcurveto{{\pgfqpoint{{{_num(x)}bp}}{{{_num(y)}bp}}}}"
                           rf"{{\pgfqpoint{{{_num(x2)}bp}}{{{_num(y2)}bp}}}}"
                           rf"{{\pgfqpoint{{{_num(x3)}bp}}{{{_num(y3)}bp}}}}%")
                i += 3
            else:
                i += 1
        return out

    def _begin_scope(self):
        out = self._dev._out
        out.append(r"\begin{pgfscope}%")
        if self._clip_enabled and self._clip is not None:
            out.extend(self._path_commands(self._clip))
            out.append(r"\pgfusepath{clip}%")

    def _end_scope(self):
        self._dev._out.append(r"\end{pgfscope}%")

    def _set_color(self, name, color):
        self._dev._out.append(
            rf"\definecolor{{{name}}}{{rgb}}{{{_num(color.redF())},{_num(color.greenF())},"
            rf"{_num(color.blueF())}}}%")

    def _emit_path(self, path, stroke, fill, fill_rule):
        self._flush_text()
        if path.elementCount() == 0:
            return
        subs = self._subpaths(path) if stroke and not fill else None
        if subs is None:
            self._flush_run()
            self._write_path(path, stroke, fill, fill_rule)
            return
        key = self._stroke_key()
        if key != self._run_key:
            self._flush_run()
            self._run_key = key
            self._run_state = self._snapshot()
        for sub in subs:
            for piece in self._simplify(sub):
                if len(piece) < 2:
                    continue
                if self._run and self._run[-1][-1] == piece[0]:
                    self._run[-1].extend(piece[1:])       # continue the same line
                else:
                    self._run.append(list(piece))

    def _write_path(self, path, stroke, fill, fill_rule):
        out = self._dev._out
        self._begin_scope()
        actions = []
        if fill:
            c = QtGui.QColor(self._brush.color())
            if self._brush.gradient() is not None:
                stops = self._brush.gradient().stops()
                if stops:
                    c = QtGui.QColor(stops[0][1])
            self._set_color("currentfill", c)
            out.append(r"\pgfsetfillcolor{currentfill}%")
            a = self._opacity * c.alphaF()
            if a < 1:
                out.append(rf"\pgfsetfillopacity{{{_num(a)}}}%")
            out.append(r"\pgfseteorule%" if fill_rule == QtCore.Qt.FillRule.OddEvenFill
                       else r"\pgfsetnonzerorule%")
            actions.append("fill")
        if stroke:
            pen = self._pen
            c = QtGui.QColor(pen.color())
            self._set_color("currentstroke", c)
            out.append(r"\pgfsetstrokecolor{currentstroke}%")
            a = self._opacity * c.alphaF()
            if a < 1:
                out.append(rf"\pgfsetstrokeopacity{{{_num(a)}}}%")
            width_px = pen.widthF() or 1.0            # width 0 = 1-pixel hairline
            if not pen.isCosmetic() and pen.widthF() > 0:
                width_px *= math.sqrt(abs(self._tf.determinant())) or 1.0
            out.append(rf"\pgfsetlinewidth{{{_num(width_px * _PT)}bp}}%")
            cap = {QtCore.Qt.PenCapStyle.FlatCap: r"\pgfsetbuttcap",
                   QtCore.Qt.PenCapStyle.RoundCap: r"\pgfsetroundcap",
                   QtCore.Qt.PenCapStyle.SquareCap: r"\pgfsetrectcap"}.get(pen.capStyle(), r"\pgfsetbuttcap")
            join = {QtCore.Qt.PenJoinStyle.RoundJoin: r"\pgfsetroundjoin",
                    QtCore.Qt.PenJoinStyle.BevelJoin: r"\pgfsetbeveljoin"}.get(pen.joinStyle(), r"\pgfsetmiterjoin")
            out.append(cap + join + "%")
            if pen.style() not in (QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenStyle.NoPen):
                dashes = pen.dashPattern() or [4.0, 2.0]
                unit = width_px * _PT
                spec = "".join(f"{{{_num(max(d * unit, 0.01))}bp}}" for d in dashes)
                out.append(rf"\pgfsetdash{{{spec}}}{{0pt}}%")
            actions.append("stroke")
        out.extend(self._path_commands(path))
        out.append(rf"\pgfusepath{{{','.join(actions)}}}%")
        self._end_scope()

    def _emit_image(self, rect, image):
        self._flush_text()
        if image.isNull():
            return
        self._flush_run()
        dev = self._dev
        name = f"{dev.image_prefix}-img{len(dev.images)}.png"
        dev.images.append((name, QtGui.QImage(image)))
        box = self._tf.mapRect(QtCore.QRectF(rect))
        x, y = self._pt(box.bottomLeft())
        self._begin_scope()
        if self._opacity < 1:
            dev._out.append(rf"\pgfsetfillopacity{{{_num(self._opacity)}}}%")
        dev._out.append(
            rf"\pgftext[left,bottom,at={{\pgfqpoint{{{_num(x)}bp}}{{{_num(y)}bp}}}}]"
            rf"{{\pgfimage[interpolate=true,width={_num(box.width() * _PT)}bp,"
            rf"height={_num(box.height() * _PT)}bp]{{{name}}}}}%")
        self._end_scope()


class PgfPaintDevice(QtGui.QPaintDevice):
    """Paint on this with a QPainter, then call save(path)."""

    def __init__(self, width_px: int, height_px: int, image_prefix: str = "figure"):
        super().__init__()
        self.width_px, self.height_px = int(width_px), int(height_px)
        self.image_prefix = image_prefix
        self.images: list[tuple[str, QtGui.QImage]] = []
        self._out: list[str] = []
        self._engine = _PgfEngine(self)

    def paintEngine(self):
        return self._engine

    def metric(self, m):
        M = QtGui.QPaintDevice.PaintDeviceMetric
        return {
            M.PdmWidth: self.width_px, M.PdmHeight: self.height_px,
            M.PdmWidthMM: round(self.width_px * 25.4 / _DPI),
            M.PdmHeightMM: round(self.height_px * 25.4 / _DPI),
            M.PdmDpiX: int(_DPI), M.PdmDpiY: int(_DPI),
            M.PdmPhysicalDpiX: int(_DPI), M.PdmPhysicalDpiY: int(_DPI),
            M.PdmDepth: 32, M.PdmNumColors: 2**31 - 1,
            M.PdmDevicePixelRatio: 1, M.PdmDevicePixelRatioScaled: 65536,
        }.get(m, 0)

    def save(self, path: str, comment: str = ""):
        """Write the .pgf file, plus <name>-img<N>.png for any bitmaps."""
        self._engine._flush_text()
        self._engine._flush_run()
        folder = os.path.dirname(os.path.abspath(path))
        for name, image in self.images:
            if not image.save(os.path.join(folder, name), "PNG"):
                raise OSError(f"Could not write {name}")
        w, h = self.width_px * _PT, self.height_px * _PT
        header = [
            f"%% {comment}" if comment else "%% PGF picture",
            "%% Include in LaTeX with \\usepackage{pgf} and \\input{" + os.path.basename(path) + "}",
            "%% Scale it with \\resizebox{\\linewidth}{!}{\\input{...}}.",
        ]
        if self.images:
            header.append("%% Needs these images next to the main .tex file: "
                          + ", ".join(n for n, _ in self.images))
        body = [r"\begingroup%", r"\makeatletter%", r"\begin{pgfpicture}%",
                rf"\pgfpathrectangle{{\pgfpointorigin}}{{\pgfqpoint{{{_num(w)}bp}}{{{_num(h)}bp}}}}%",
                r"\pgfusepath{use as bounding box, clip}%"]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(header + body + self._out
                               + [r"\end{pgfpicture}%", r"\makeatother%", r"\endgroup%"]) + "\n")
