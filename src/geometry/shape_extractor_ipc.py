"""Extract geometric shapes from KiCad board selections using IPC API."""

import math
import re
from typing import List
from . import LineSegment, Arc, Circle, Bezier, Point


class ShapeExtractorIPC:
    """Extract geometric primitives from board items using kicad-python IPC API."""

    def __init__(self, board):
        """Initialize extractor with board reference."""
        self.board = board

    def extract_from_selection(self) -> List[LineSegment | Arc | Circle | Bezier]:
        """Extract all geometric primitives from selected board items."""
        primitives = []
        selected_items = self._get_selected_shapes_safe()

        # Fallback for grouped selections where IPC Any payloads cannot be unwrapped:
        # parse the textual selection dump and recover gr_* primitives.
        if not selected_items:
            return self._extract_primitives_from_selection_string()

        for item in selected_items:
            item_type = type(item).__name__

            if item_type == 'BoardRectangle':
                primitives.extend(self._extract_rectangle(item))
            elif item_type == 'BoardCircle':
                prim = self._extract_circle(item)
                if prim:
                    primitives.append(prim)
            elif item_type == 'BoardArc':
                prim = self._extract_arc(item)
                if prim:
                    primitives.append(prim)
            elif item_type in ('BoardLine', 'BoardSegment'):
                prim = self._extract_line(item)
                if prim:
                    primitives.append(prim)
            elif item_type in ('BoardPolygon', 'GraphicPolygon', 'Polygon', 'DrawPolygon'):
                primitives.extend(self._extract_polygon(item))
            elif item_type == 'BoardBezier':
                prim = self._extract_bezier(item)
                if prim:
                    primitives.append(prim)
            else:
                # Unknown type - try polygon extraction as fallback
                poly_prims = self._extract_polygon(item)
                if poly_prims:
                    primitives.extend(poly_prims)

        return primitives

    def _get_selected_shapes_safe(self):
        """Get selected shape items while tolerating malformed/unsupported selection entries.

        KiCad may include non-shape/group entries in selection responses. Some of those can
        arrive as empty `Any` protobuf messages, which causes kicad-python's default
        `board.get_selection()` path to raise. This helper queries selection directly and
        ignores malformed entries so extraction can continue with valid shapes.
        """
        try:
            from kipy.board_types import BoardShape, to_concrete_board_shape
            from kipy.proto.board import board_types_pb2
            from kipy.proto.common.commands import editor_commands_pb2
            from kipy.proto.common.types import KiCadObjectType
            from kipy.util import unpack_any
        except Exception:
            # If IPC internals are unavailable, keep legacy behavior.
            return self.board.get_selection()

        # Query raw selection (without type filter). Group selections may include malformed
        # entries mixed with valid graphic shapes; filtering by type can drop grouped members.
        try:
            cmd = editor_commands_pb2.GetSelection()
            cmd.header.document.CopyFrom(self.board._doc)
            response = self.board._kicad.send(cmd, editor_commands_pb2.SelectionResponse)
        except Exception:
            # Fall back to the wrapper API (older/newer kicad-python variants).
            try:
                return self.board.get_selection(types=[KiCadObjectType.KOT_PCB_SHAPE])
            except Exception:
                return self.board.get_selection()

        selected_items = []

        def append_shapes_from_messages(messages):
            for message in messages:
                type_url = getattr(message, 'type_url', '')

                if type_url:
                    try:
                        concrete = unpack_any(message)
                    except Exception:
                        continue

                    if isinstance(concrete, board_types_pb2.BoardGraphicShape):
                        shape = to_concrete_board_shape(BoardShape(proto=concrete))
                        if shape is not None:
                            selected_items.append(shape)
                    continue

                # Some KiCad versions return empty Any.type_url for selected group members.
                # Attempt to decode payload bytes directly as BoardGraphicShape.
                raw_value = getattr(message, 'value', b'')
                if not raw_value:
                    continue

                try:
                    maybe_shape = board_types_pb2.BoardGraphicShape()
                    maybe_shape.ParseFromString(raw_value)
                except Exception:
                    continue

                if maybe_shape.shape.WhichOneof("geometry") is None:
                    continue

                shape = to_concrete_board_shape(BoardShape(proto=maybe_shape))
                if shape is not None:
                    selected_items.append(shape)

        append_shapes_from_messages(response.items)

        # If raw query returned no shapes, try explicit shape filtering as a fallback.
        if not selected_items:
            try:
                cmd = editor_commands_pb2.GetSelection()
                cmd.header.document.CopyFrom(self.board._doc)
                cmd.types.append(KiCadObjectType.KOT_PCB_SHAPE)
                typed_response = self.board._kicad.send(cmd, editor_commands_pb2.SelectionResponse)
                append_shapes_from_messages(typed_response.items)
            except Exception:
                pass

        return selected_items

    def _extract_primitives_from_selection_string(self) -> List[LineSegment | Arc | Circle | Bezier]:
        """Parse KiCad's textual selection dump and recover graphic primitives.

        Used only as a last-resort fallback when IPC selection unwrapping yields no shapes.
        """
        try:
            from kipy.proto.common.commands import editor_commands_pb2
        except Exception:
            return []

        try:
            cmd = editor_commands_pb2.SaveSelectionToString()
            response = self.board._kicad.send(cmd, editor_commands_pb2.SavedSelectionResponse)
            contents = getattr(response, 'contents', '') or ''
        except Exception:
            return []

        if not contents:
            return []

        expressions = self._split_gr_expressions(contents)
        primitives: List[LineSegment | Arc | Circle | Bezier] = []

        for expr in expressions:
            prims = self._parse_gr_expression(expr)
            if prims:
                primitives.extend(prims)

        return primitives

    def _split_gr_expressions(self, text: str) -> List[str]:
        """Split `(gr_...)` S-expressions from a KiCad board snippet."""
        exprs: List[str] = []
        i = 0
        n = len(text)

        while i < n:
            start = text.find('(gr_', i)
            if start == -1:
                break

            depth = 0
            in_string = False
            escaped = False
            end = -1

            j = start
            while j < n:
                ch = text[j]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == '\\':
                        escaped = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                        if depth == 0:
                            end = j
                            break
                j += 1

            if end != -1:
                exprs.append(text[start:end + 1])
                i = end + 1
            else:
                break

        return exprs

    def _parse_gr_expression(self, expr: str) -> List[LineSegment | Arc | Circle | Bezier]:
        """Parse one KiCad `(gr_...)` expression into primitives."""
        coord = r'([+-]?\d+(?:\.\d+)?)'

        def pt(label: str):
            m = re.search(rf'\({label}\s+{coord}\s+{coord}\)', expr)
            if not m:
                return None
            return Point(float(m.group(1)), float(m.group(2)))

        if expr.startswith('(gr_line'):
            start = pt('start')
            end = pt('end')
            return [LineSegment(start, end)] if start and end else []

        if expr.startswith('(gr_arc'):
            start = pt('start')
            mid = pt('mid')
            end = pt('end')
            return [Arc(start, mid, end)] if start and mid and end else []

        if expr.startswith('(gr_circle'):
            center = pt('center')
            radius_pt = pt('end')
            if not center or not radius_pt:
                return []
            dx = radius_pt.x - center.x
            dy = radius_pt.y - center.y
            return [Circle(center, math.sqrt(dx * dx + dy * dy))]

        if expr.startswith('(gr_rect'):
            start = pt('start')
            end = pt('end')
            if not start or not end:
                return []
            tl = Point(min(start.x, end.x), min(start.y, end.y))
            br = Point(max(start.x, end.x), max(start.y, end.y))
            tr = Point(br.x, tl.y)
            bl = Point(tl.x, br.y)
            return [
                LineSegment(tl, tr),
                LineSegment(tr, br),
                LineSegment(br, bl),
                LineSegment(bl, tl),
            ]

        if expr.startswith('(gr_poly'):
            pts = [
                Point(float(x), float(y))
                for x, y in re.findall(rf'\(xy\s+{coord}\s+{coord}\)', expr)
            ]
            if len(pts) < 3:
                return []
            segs: List[LineSegment] = []
            for i in range(len(pts)):
                a = pts[i]
                b = pts[(i + 1) % len(pts)]
                if abs(a.x - b.x) > 1e-9 or abs(a.y - b.y) > 1e-9:
                    segs.append(LineSegment(a, b))
            return segs

        if expr.startswith('(gr_curve') or expr.startswith('(gr_bezier'):
            pts = [
                Point(float(x), float(y))
                for x, y in re.findall(rf'\(xy\s+{coord}\s+{coord}\)', expr)
            ]
            if len(pts) < 4:
                return []
            return [Bezier(pts[0], pts[1], pts[2], pts[3])]

        return []

    def _nm_to_mm(self, value_nm: int) -> float:
        """Convert nanometers to millimeters."""
        return float(value_nm) / 1_000_000.0

    def _vector_to_point(self, vec) -> Point:
        """Convert Vector2 to Point (mm)."""
        return Point(self._nm_to_mm(vec.x), self._nm_to_mm(vec.y))

    def _extract_rectangle(self, item) -> List[LineSegment]:
        """Extract rectangle as four line segments."""
        tl = self._vector_to_point(item.top_left)
        br = self._vector_to_point(item.bottom_right)

        tr = Point(br.x, tl.y)
        bl = Point(tl.x, br.y)

        return [
            LineSegment(tl, tr),
            LineSegment(tr, br),
            LineSegment(br, bl),
            LineSegment(bl, tl),
        ]

    def _extract_circle(self, item) -> Circle | None:
        """Extract circle from BoardCircle."""
        center = self._vector_to_point(item.center)
        radius_pt = self._vector_to_point(item.radius_point)

        # Calculate radius from center to radius_point
        dx = radius_pt.x - center.x
        dy = radius_pt.y - center.y
        radius = math.sqrt(dx * dx + dy * dy)

        return Circle(center, radius)

    def _extract_arc(self, item) -> Arc | None:
        """Extract arc from BoardArc."""
        if hasattr(item, 'start') and hasattr(item, 'mid') and hasattr(item, 'end'):
            start = self._vector_to_point(item.start)
            mid = self._vector_to_point(item.mid)
            end = self._vector_to_point(item.end)
            return Arc(start, mid, end)
        return None

    def _extract_line(self, item) -> LineSegment | None:
        """Extract line segment from BoardLine/BoardSegment."""
        if hasattr(item, 'start') and hasattr(item, 'end'):
            start = self._vector_to_point(item.start)
            end = self._vector_to_point(item.end)
            return LineSegment(start, end)
        return None

    def _extract_polygon(self, item) -> List[LineSegment]:
        """Extract polygon as line segments.

        Tries multiple ways to access polygon points from IPC API.
        """
        segments = []
        points = []

        # Try different property names for accessing polygon data
        if hasattr(item, 'polygons') and item.polygons:
            # PolygonWithHoles style - get outline from first polygon
            try:
                for poly in item.polygons:
                    if hasattr(poly, 'outline') and poly.outline:
                        outline = poly.outline
                        # PolyLine style - iterate nodes
                        if hasattr(outline, '__iter__'):
                            for node in outline:
                                if hasattr(node, 'point'):
                                    points.append(self._vector_to_point(node.point))
                                elif hasattr(node, 'x') and hasattr(node, 'y'):
                                    points.append(Point(self._nm_to_mm(node.x), self._nm_to_mm(node.y)))
                    # Only process first polygon for now
                    break
            except Exception:
                pass

        if not points and hasattr(item, 'outline'):
            # Direct outline property
            try:
                outline = item.outline
                if hasattr(outline, 'outline'):
                    # PolygonWithHoles
                    outline = outline.outline
                if hasattr(outline, '__iter__'):
                    for node in outline:
                        if hasattr(node, 'point'):
                            points.append(self._vector_to_point(node.point))
                        elif hasattr(node, 'x') and hasattr(node, 'y'):
                            points.append(Point(self._nm_to_mm(node.x), self._nm_to_mm(node.y)))
            except Exception:
                pass

        if not points and hasattr(item, 'points'):
            # Simple points list
            try:
                points = [self._vector_to_point(p) for p in item.points]
            except Exception:
                pass

        if not points and hasattr(item, 'vertices'):
            # Vertices property
            try:
                points = [self._vector_to_point(v) for v in item.vertices]
            except Exception:
                pass

        # Convert points to line segments
        if len(points) >= 3:
            for i in range(len(points)):
                start = points[i]
                end = points[(i + 1) % len(points)]
                # Skip zero-length segments
                if abs(start.x - end.x) > 1e-6 or abs(start.y - end.y) > 1e-6:
                    segments.append(LineSegment(start, end))

        return segments

    def _extract_bezier(self, item) -> Bezier | None:
        """Extract bezier from BoardBezier."""
        if hasattr(item, 'start') and hasattr(item, 'control1') and hasattr(item, 'control2') and hasattr(item, 'end'):
            start = self._vector_to_point(item.start)
            ctrl1 = self._vector_to_point(item.control1)
            ctrl2 = self._vector_to_point(item.control2)
            end = self._vector_to_point(item.end)
            return Bezier(start, ctrl1, ctrl2, end)
        return None
