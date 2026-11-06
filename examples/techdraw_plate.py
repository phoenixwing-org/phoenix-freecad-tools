# SPDX-License-Identifier: Apache-2.0
"""Run explicitly through execute_script after creating PhoenixParametricDemo.

Only the unsaved public demo is edited. All files use a new temporary directory.
Official API references (API usage, no upstream implementation copied):
https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/TechDrawGui_API.md
https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/TechDraw/App/DrawViewPartPyImp.cpp
https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/TechDraw/App/DrawViewDimensionPyImp.cpp
https://github.com/FreeCAD/FreeCAD/blob/main/src/App/DocumentPyImp.cpp
"""

import hashlib
import math
from pathlib import Path
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
import TechDrawGui
from PySide import QtCore, QtGui, QtSvg, QtWidgets


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def value(number):
    return float(number.Value if hasattr(number, "Value") else number)


def quantity_signature(shape):
    box = shape.BoundBox
    return [float(shape.Volume), float(shape.Area)] + [float(getattr(box, name)) for name in
        ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")]


def close_numbers(left, right):
    return len(left) == len(right) and all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6) for a, b in zip(left, right))


def projected_edges(view):
    """Enumerate actual indices; do not infer indices from a filtered edge list."""
    found, misses = [], 0
    for index in range(2048):
        try:
            edge = view.getEdgeByIndex(index)
            if edge is None or edge.isNull():
                raise ValueError("missing")
            found.append((index, edge))
            misses = 0
        except (ValueError, RuntimeError, IndexError):
            misses += 1
            if found and misses >= 8:
                break
            if not found and misses >= 32:
                break
    return found


def settle(document, views, dimensions=()):
    document.recompute()
    deadline, previous, stable = time.monotonic() + 12.0, None, 0
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        Gui.updateGui()
        snapshot = []
        ready = True
        for view in views:
            edges = list(view.getVisibleEdges())
            ready = ready and bool(edges)
            snapshot.append(tuple(round(float(edge.Length), 8) for edge in edges))
            require(not any("invalid" in str(state).lower() or "error" in str(state).lower() for state in view.State), "Projected view is invalid: " + view.Name)
        for dimension in dimensions:
            measured = float(dimension.getRawValue())
            ready = ready and math.isfinite(measured) and measured > 0
            snapshot.append(round(measured, 8))
        stable = stable + 1 if ready and snapshot == previous else 0
        previous = snapshot
        if stable >= 3:
            return
        time.sleep(0.06)
    raise RuntimeError("TechDraw did not settle; the demo transaction will be aborted.")


def curve_radius(edge):
    curve = edge.Curve
    radius = getattr(curve, "Radius", None)
    if radius is None or not edge.isClosed():
        return None
    radius = float(radius)
    if radius > 0 and math.isclose(float(edge.Length), 2 * math.pi * radius, rel_tol=1e-6, abs_tol=1e-6):
        return radius
    return None


def find_dimension(view, source):
    source_radii = [radius for edge in source.Shape.Edges for radius in [curve_radius(edge)] if radius is not None]
    circles, lines = [], []
    for index, edge in projected_edges(view):
        radius = curve_radius(edge)
        if radius is not None and any(math.isclose(radius, candidate, rel_tol=1e-7, abs_tol=1e-6) for candidate in source_radii):
            center = edge.Curve.Center
            circles.append((radius, float(center.x), float(center.y), index))
        if len(edge.Vertexes) == 2:
            span = (edge.Vertexes[0].Point - edge.Vertexes[1].Point).Length
            if span > 0 and math.isclose(span, float(edge.Length), rel_tol=1e-8, abs_tol=1e-6):
                source_box = source.Shape.BoundBox
                if any(math.isclose(span, length, rel_tol=1e-7, abs_tol=1e-6) for length in (source_box.XLength, source_box.YLength)):
                    lines.append((float(span), index))
    if circles:
        radius, unused_x, unused_y, index = sorted(circles)[0]
        return "Diameter", index, radius * 2, {"classification": "closed projected circle matching source circle radius", "radius_mm": radius}
    require(bool(lines), "No unambiguous circle or full outer straight edge was found; no dimension was guessed.")
    length, index = sorted(lines, key=lambda item: (-item[0], item[1]))[0]
    return "Distance", index, length, {"classification": "straight projected edge matching full source X/Y span", "length_mm": length}


def verify_dimension(dimension, view, expected):
    require(bool(dimension.References2D), "Dimension has no actual geometry reference.")
    require(not any("invalid" in str(state).lower() or "error" in str(state).lower() for state in dimension.State), "Dimension state is invalid.")
    measured = float(dimension.getRawValue())
    require(math.isclose(measured, expected, rel_tol=1e-7, abs_tol=1e-5), "Actual dimension does not match classified geometry.")
    for owner, names in dimension.References2D:
        require(owner is view, "Dimension references an unexpected view.")
        for name in ([names] if isinstance(names, str) else names):
            require(re.fullmatch(r"Edge[0-9]+", name) is not None, "Unexpected dimension subelement.")
            edge = view.getEdgeBySelection(name)
            require(edge is not None and not edge.isNull(), "Stored dimension reference is not a real projected edge.")
    return measured


def file_report(filename):
    data = filename.read_bytes()
    return {"path": str(filename), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def run_demo():
    document = App.getDocument("PhoenixParametricDemo")
    require(document is not None, "Create PhoenixParametricDemo before running this script.")
    require(not document.FileName, "This example only edits the newly created, unsaved public demo.")
    source = document.getObject("Pocket")
    bodies = [obj for obj in document.Objects if obj.TypeId == "PartDesign::Body" and obj.Tip is source]
    require(source is not None and len(bodies) == 1, "Expected one Body with Pocket as its final tip.")
    body = bodies[0]
    require(not source.Shape.isNull() and source.Shape.isValid() and len(source.Shape.Solids) == 1, "Demo source must be one valid solid.")
    require(document.getObject("PhoenixDemoPage") is None, "Demo drawing already exists; nothing was replaced.")
    source_before = quantity_signature(source.Shape)
    existing_documents = set(App.listDocuments())
    template_root = Path(App.getResourceDir()) / "Mod" / "TechDraw" / "Templates"
    choices = [template_root / "ISO" / "A4_Landscape_ISO5457_advanced.svg",
               template_root / "A4_Landscape_ISO5457_advanced.svg",
               template_root / "Default_Template_A4_Landscape.svg"]
    selected = next((filename for filename in choices if filename.is_file()), None)
    require(selected is not None, "No supported built-in A4 landscape template exists in this FreeCAD installation.")
    base = Path(App.getTempPath()) / "PhoenixFreeCADDemo"
    base.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="techdraw-", dir=str(base)))
    reopened = None
    transaction_open = False
    try:
        document.openTransaction("Phoenix public TechDraw demonstration")
        transaction_open = True
        page = document.addObject("TechDraw::DrawPage", "PhoenixDemoPage")
        page.Label = "Phoenix public demo - A4"
        template = document.addObject("TechDraw::DrawSVGTemplate", "PhoenixDemoTemplate")
        template.Template = str(selected)
        page.Template = template
        document.recompute()
        require(math.isclose(value(template.Width), 297, abs_tol=0.1) and math.isclose(value(template.Height), 210, abs_tol=0.1), "Selected template is not A4 landscape.")
        texts = {name: "" for name in template.EditableTexts}
        truthful = {"title": "Phoenix parametric plate demo", "drawing_number": document.Name,
                    "document_type": "Parametric demonstration", "document_status": "DEMO - NOT FOR PRODUCTION",
                    "sheet_number": "1 / 1", "language_code": "EN"}
        for name, text in truthful.items():
            if name in texts:
                texts[name] = text
        template.EditableTexts = texts
        specs = [("Front", (0, -1, 0), (1, 0, 0), 85, 95, 120, 55),
                 ("Top", (0, 0, 1), (1, 0, 0), 85, 165, 120, 55),
                 ("Right", (1, 0, 0), (0, 1, 0), 220, 95, 110, 55),
                 ("Iso", (1, -1, 1), (1, 1, 0), 220, 165, 110, 60)]
        views = []
        for title, direction, x_direction, x, y, cell_w, cell_h in specs:
            view = document.addObject("TechDraw::DrawViewPart", "PhoenixDemo" + title)
            view.Label = title
            view.Source = [source]
            view.Direction = App.Vector(*direction)
            if "XDirection" in view.PropertiesList:
                view.XDirection = App.Vector(*x_direction)
            view.ScaleType = "Custom"
            view.Scale = 1.0
            page.addView(view)
            view.X, view.Y = x, y
            views.append(view)
        page.ViewObject.show()
        settle(document, views)
        # addView and first page activation may initialize positions to the centre.
        # Apply the layout only after those native initialization steps.
        for view, spec in zip(views, specs):
            view.X, view.Y = spec[3], spec[4]
        settle(document, views)
        spans = []
        for view in views:
            edges = [edge for unused_index, edge in projected_edges(view)]
            require(bool(edges), "Projection has no native indexed edges.")
            spans.append((max(edge.BoundBox.XMax for edge in edges) - min(edge.BoundBox.XMin for edge in edges),
                          max(edge.BoundBox.YMax for edge in edges) - min(edge.BoundBox.YMin for edge in edges)))
        scale = next((candidate for candidate in (1.0, 0.5, 0.2, 0.1, 0.05)
                      if all(width * candidate <= spec[5] and height * candidate <= spec[6]
                             for (width, height), spec in zip(spans, specs))), None)
        require(scale is not None, "The actual projections cannot fit the reserved A4 cells.")
        page.Scale = scale
        for view in views:
            view.Scale = scale
        if "scale" in texts:
            texts["scale"] = "1 : " + str(int(round(1 / scale)))
            template.EditableTexts = texts
        settle(document, views)
        top = views[1]
        kind, edge_index, expected, classification = find_dimension(top, source)
        edge_name = "Edge" + str(edge_index)
        require(not top.getEdgeBySelection(edge_name).isNull(), "Selected projection reference cannot be resolved by its real name.")
        dimension = document.addObject("TechDraw::DrawViewDimension", "PhoenixDemoDimension")
        dimension.Type = kind
        dimension.MeasureType = "Projected"
        dimension.References2D = [(top, edge_name)]
        page.addView(dimension)
        # Dimension offsets are relative to their source view (millimetres).
        dimension.X, dimension.Y = -(spans[1][0] * scale / 2 + 12), -25
        for property_name in ("Fontsize", "FontSize"):
            if property_name in dimension.ViewObject.PropertiesList:
                setattr(dimension.ViewObject, property_name, 3.5)
        settle(document, views, [dimension])
        measured = verify_dimension(dimension, top, expected)
        for view, spec in zip(views, specs):
            require(math.isclose(value(view.X), spec[3], abs_tol=1e-6) and math.isclose(value(view.Y), spec[4], abs_tol=1e-6), "Native view initialization reset the drawing layout.")
        require(close_numbers(source_before, quantity_signature(source.Shape)), "Source geometry changed while creating the drawing.")
        pdf, svg, fcstd = output / "PhoenixDemo.pdf", output / "PhoenixDemo.svg", output / "PhoenixDemo.FCStd"
        TechDrawGui.exportPageAsPdf(page, str(pdf))
        TechDrawGui.exportPageAsSvg(page, str(svg))
        pdf_data = pdf.read_bytes()
        require(pdf_data.startswith(b"%PDF-") and len(pdf_data) > 500, "GUI PDF export did not produce a PDF.")
        pdf_pages = len(re.findall(rb"/Type\s*/Page(?!s)\b", pdf_data))
        require(pdf_pages == 1, "Exported PDF is not a single-page drawing.")
        svg_root = ET.fromstring(svg.read_bytes())
        require(svg_root.tag.endswith("svg") and any(node.tag.endswith(("path", "circle", "polyline")) for node in svg_root.iter()), "GUI SVG export lacks drawing geometry.")
        svg_text = " ".join(svg_root.itertext())
        pdf_text = {"checked": False, "reason": "No optional PDF text reader installed in FreeCAD Python"}
        try:
            from pypdf import PdfReader
        except ImportError:
            pass
        else:
            reader = PdfReader(str(pdf))
            require(len(reader.pages) == 1, "Independent PDF reader found an unexpected page count.")
            pdf_text = {"checked": True, "contains_demo_title": "Phoenix" in (reader.pages[0].extract_text() or "")}
        preview = output / "PhoenixDemo.png"
        renderer = QtSvg.QSvgRenderer(str(svg))
        require(renderer.isValid(), "The exported SVG cannot be rendered by Qt.")
        image = QtGui.QImage(1188, 840, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(image)
        try:
            renderer.render(painter)
        finally:
            painter.end()
        require(image.save(str(preview), "PNG"), "Page preview could not be saved.")
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Document")
        thumbnail_setting = preferences.GetBool("SaveThumbnail", True)
        try:
            if not thumbnail_setting:
                preferences.SetBool("SaveThumbnail", True)
            document.saveCopy(str(fcstd))
        finally:
            if not thumbnail_setting:
                preferences.SetBool("SaveThumbnail", False)
        require(not document.FileName, "Saving the demo copy unexpectedly changed the source document destination.")
        with zipfile.ZipFile(fcstd) as archive:
            require("Document.xml" in archive.namelist() and "GuiDocument.xml" in archive.namelist(), "Saved FCStd lacks native document records.")
            thumbnails = [name for name in archive.namelist() if name.lower().endswith("thumbnail.png")]
            require(len(thumbnails) == 1, "Saved FCStd has no unique native thumbnail.")
            thumbnail_bytes = archive.read(thumbnails[0])
            thumbnail = QtGui.QImage.fromData(thumbnail_bytes)
            require(not thumbnail.isNull(), "Saved native thumbnail cannot be decoded.")
            colors = {thumbnail.pixel(x, y) for x in range(0, thumbnail.width(), max(1, thumbnail.width() // 20))
                      for y in range(0, thumbnail.height(), max(1, thumbnail.height() // 20))}
            require(len(colors) > 1, "Saved native thumbnail is monochrome; visual verification is required.")
        reopened = App.openDocument(str(fcstd))
        require(reopened is not None and reopened.Name not in existing_documents, "Verification did not open an independent new document.")
        reopened_views = [reopened.getObject(view.Name) for view in views]
        reopened_dimension = reopened.getObject(dimension.Name)
        require(all(view is not None for view in reopened_views) and reopened_dimension is not None, "Reopened drawing lost views or dimension.")
        settle(reopened, reopened_views, [reopened_dimension])
        require(reopened.getObject(page.Name).Template is not None, "Reopened page lost its template.")
        require(reopened.getObject(template.Name).EditableTexts == texts, "Reopened titleblock values changed.")
        reopened_measured = verify_dimension(reopened_dimension, reopened_views[1], expected)
        for view, spec in zip(reopened_views, specs):
            require(math.isclose(value(view.X), spec[3], abs_tol=1e-6) and math.isclose(value(view.Y), spec[4], abs_tol=1e-6), "Reopened view position differs from the tested layout.")
        require(close_numbers(source_before, quantity_signature(reopened.getObject(source.Name).Shape)), "Reopened demo source geometry differs.")
        verification_name = reopened.Name
        App.closeDocument(reopened.Name)
        reopened = None
        document.commitTransaction()
        transaction_open = False
        App.setActiveDocument(document.Name)
        page.ViewObject.show()
        Gui.updateGui()
        return {"document": document.Name, "page": page.Name, "views": [view.Name for view in views],
                "layout": [{"view": view.Name, "x_mm": value(view.X), "y_mm": value(view.Y)} for view in views],
                "scale": scale, "template": selected.name, "dimension": {"name": dimension.Name, "type": kind,
                    "reference": edge_name, "classification": classification, "actual_mm": measured, "reopened_mm": reopened_measured},
                "exports": {"pdf": file_report(pdf), "svg": file_report(svg), "preview": file_report(preview), "fcstd": file_report(fcstd)},
                "checks": {"pdf_pages": pdf_pages, "svg_demo_text_found": "Phoenix" in svg_text, "pdf_text": pdf_text,
                    "reopened_document": verification_name, "reopened_document_closed": True, "native_thumbnail_bytes": len(thumbnail_bytes),
                    "native_thumbnail_non_monochrome": True, "source_geometry_preserved": True},
                "manual_review_required": True, "scope": "Public demonstration only; geometry-linked example, not a production drawing approval"}
    except BaseException:
        if reopened is not None and reopened.Name not in existing_documents:
            App.closeDocument(reopened.Name)
        if transaction_open:
            document.abortTransaction()
        shutil.rmtree(output, ignore_errors=True)
        raise


result = run_demo()
