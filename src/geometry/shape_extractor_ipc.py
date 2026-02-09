"""Extract geometric shapes from KiCad board selections using IPC API."""

import math
import re
import logging
from typing import List
from . import LineSegment, Arc, Circle, Bezier, Point

logger = logging.getLogger(__name__)


class ShapeExtractorIPC:
    """Extract geometric primitives from board items using kicad-python IPC API."""

    def __init__(self, board):
        """Initialize extractor with board reference."""
        self.board = board
        # True when selection payload had entries that could not be decoded as shapes.
        self._selection_had_unparsed = False

    def extract_from_selection(self) -> List[LineSegment | Arc | Circle | Bezier]:
        """Extract all geometric primitives from selected board items."""
        primitives = []
        selected_items = self._get_selected_shapes_safe()
        logger.info(
            "Selection extraction start: selected_items=%d, had_unparsed=%s",
            len(selected_items),
            self._selection_had_unparsed,
        )

        # Fallback for grouped selections where IPC Any payloads cannot be unwrapped:
        # parse the textual selection dump and recover gr_* primitives.
        if not selected_items:
            logger.info("No selected items from IPC; using full selection-string fallback")
            return self._extract_primitives_from_selection_string()

        type_counts = {}
        for item in selected_items:
            item_type = type(item).__name__
            type_counts[item_type] = type_counts.get(item_type, 0) + 1

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
                else:
                    logger.debug("Unknown/unsupported selected item type: %s", item_type)

        # Always merge textual selection recovery. In mixed selections, some grouped members
        # can be missing from IPC selection payloads even when no explicit decode error occurs.
        fallback_primitives = self._extract_primitives_from_selection_string()
        if fallback_primitives:
            logger.info("Selection-string fallback produced %d primitives", len(fallback_primitives))
        primitives = self._merge_primitives_without_duplicates(primitives, fallback_primitives)
        logger.info("Selection extraction summary: item_types=%s, merged_primitives=%d", type_counts, len(primitives))

        # Dedupe in case selection APIs return overlapping members.
        return self._merge_primitives_without_duplicates([], primitives)

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
        self._selection_had_unparsed = False
        stats = {
            "raw_messages": 0,
            "typed_messages": 0,
            "wrapper_items": 0,
            "decode_failures": 0,
            "geometry_missing": 0,
            "resolved_shapes": 0,
        }

        def append_shapes_from_messages(messages):
            for message in messages:
                stats["raw_messages"] += 1
                type_url = getattr(message, 'type_url', '')

                if type_url:
                    try:
                        concrete = unpack_any(message)
                    except Exception:
                        self._selection_had_unparsed = True
                        stats["decode_failures"] += 1
                        continue

                    # Accept any proto payload that can be converted to a concrete board shape.
                    try:
                        shape = to_concrete_board_shape(BoardShape(proto=concrete))
                    except Exception:
                        shape = None

                    if shape is not None:
                        selected_items.append(shape)
                        stats["resolved_shapes"] += 1
                    else:
                        self._selection_had_unparsed = True
                        stats["decode_failures"] += 1
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
                    self._selection_had_unparsed = True
                    stats["decode_failures"] += 1
                    continue

                if maybe_shape.shape.WhichOneof("geometry") is None:
                    self._selection_had_unparsed = True
                    stats["geometry_missing"] += 1
                    continue

                shape = to_concrete_board_shape(BoardShape(proto=maybe_shape))
                if shape is not None:
                    selected_items.append(shape)
                    stats["resolved_shapes"] += 1
                else:
                    self._selection_had_unparsed = True
                    stats["decode_failures"] += 1

        append_shapes_from_messages(response.items)

        # Also query explicit PCB-shape filtering and merge results. Some KiCad versions
        # expose grouped members only via typed queries.
        try:
            cmd = editor_commands_pb2.GetSelection()
            cmd.header.document.CopyFrom(self.board._doc)
            cmd.types.append(KiCadObjectType.KOT_PCB_SHAPE)
            typed_response = self.board._kicad.send(cmd, editor_commands_pb2.SelectionResponse)
            stats["typed_messages"] = len(getattr(typed_response, "items", []) or [])
            append_shapes_from_messages(typed_response.items)
        except Exception:
            pass

        # Also query through wrapper API and flatten potential group containers.
        # Some KiCad/kipy variants expose grouped members here when raw IPC misses them.
        try:
            wrapper_items = self.board.get_selection(types=[KiCadObjectType.KOT_PCB_SHAPE])
        except Exception:
            try:
                wrapper_items = self.board.get_selection()
            except Exception:
                wrapper_items = []
        stats["wrapper_items"] = len(wrapper_items or [])

        for item in wrapper_items or []:
            for shape in self._flatten_group_shapes(item):
                if shape is not None:
                    selected_items.append(shape)
                    stats["resolved_shapes"] += 1

        logger.info(
            "IPC selection summary: selected_items=%d, had_unparsed=%s, stats=%s",
            len(selected_items),
            self._selection_had_unparsed,
            stats,
        )

        return selected_items

    def _flatten_group_shapes(self, item, _depth: int = 0, _seen=None):
        """Yield shape-like items, recursively expanding group/container members.

        Handles KiCad variants that expose grouped members as direct objects or as IDs.
        """
        if item is None or _depth > 5:
            return []

        if _seen is None:
            _seen = set()
        marker = id(item)
        if marker in _seen:
            return []
        _seen.add(marker)

        out = [item]
        group_children = []

        # Common container field names first.
        candidate_attrs = {"members", "items", "children", "group_items", "objects", "shapes"}
        type_name = type(item).__name__.lower()
        if "group" in type_name or "selection" in type_name:
            # Aggressive scan for container-like attrs on group wrappers.
            for attr in dir(item):
                if attr.startswith("_"):
                    continue
                lower = attr.lower()
                if lower.endswith(("members", "items", "children", "objects", "shapes")):
                    candidate_attrs.add(attr)

        for attr in candidate_attrs:
            try:
                value = getattr(item, attr, None)
            except Exception:
                value = None

            if value is None:
                continue

            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue

            # Single object child
            if self._looks_shape_like(value):
                group_children.append(value)
                continue

            # Iterable children
            try:
                if isinstance(value, (str, bytes, bytearray)):
                    continue
                for child in value:
                    resolved = self._resolve_member_reference(child)
                    group_children.append(resolved if resolved is not None else child)
            except Exception:
                continue

        for child in group_children:
            out.extend(self._flatten_group_shapes(child, _depth + 1, _seen))

        return out

    def _looks_shape_like(self, value) -> bool:
        """Best-effort check for board graphic shape-like objects."""
        if value is None:
            return False
        t = type(value).__name__
        if t in {
            "BoardRectangle", "BoardCircle", "BoardArc", "BoardLine", "BoardSegment",
            "BoardPolygon", "GraphicPolygon", "Polygon", "DrawPolygon", "BoardBezier"
        }:
            return True

        for attr in ("start", "end", "center", "top_left", "bottom_right", "outline", "points", "vertices", "polygons"):
            if hasattr(value, attr):
                return True
        return False

    def _resolve_member_reference(self, ref):
        """Resolve group-member references (uuid/id) to concrete board items when possible."""
        if ref is None:
            return None

        if self._looks_shape_like(ref):
            return ref

        board = getattr(self, "board", None)
        if board is None:
            return None

        # Candidate identifiers to try.
        candidates = [ref]
        for attr in ("uuid", "id", "value"):
            try:
                value = getattr(ref, attr, None)
            except Exception:
                value = None
            if value is not None:
                candidates.append(value)

        methods = ("find_item", "get_item", "item_by_id", "find_by_uuid")
        for candidate in candidates:
            for method_name in methods:
                method = getattr(board, method_name, None)
                if not callable(method):
                    continue
                try:
                    resolved = method(candidate)
                except Exception:
                    continue
                if resolved is not None:
                    return resolved
        return None

    def _extract_primitives_from_selection_string(self, only_gr_poly: bool = False) -> List[LineSegment | Arc | Circle | Bezier]:
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
            logger.info("Selection-string fallback: empty contents")
            return []

        expressions = self._split_gr_expressions(contents)
        primitives: List[LineSegment | Arc | Circle | Bezier] = []

        for expr in expressions:
            if only_gr_poly and not expr.startswith('(gr_poly'):
                continue
            prims = self._parse_gr_expression(expr)
            if prims:
                primitives.extend(prims)

        logger.info(
            "Selection-string fallback summary: expressions=%d, only_gr_poly=%s, primitives=%d",
            len(expressions),
            only_gr_poly,
            len(primitives),
        )

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
        coord = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'

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
            # Parse each (pts ... ) contour separately. Grouped selections may include
            # multi-contour polygons in one gr_poly expression.
            segs: List[LineSegment] = []
            pts_blocks = re.findall(r'\(pts\s+((?:\([^()]*\)\s*)+)\)', expr)
            if pts_blocks:
                for block in pts_blocks:
                    pts = [
                        Point(float(x), float(y))
                        for x, y in re.findall(rf'\(xy\s+{coord}\s+{coord}\)', block)
                    ]
                    if len(pts) >= 3:
                        segs.extend(self._contour_to_segments(pts))
                return segs

            # Fallback for alternative formatting without explicit (pts ...) grouping.
            pts = [
                Point(float(x), float(y))
                for x, y in re.findall(rf'\(xy\s+{coord}\s+{coord}\)', expr)
            ]
            if len(pts) < 3:
                return []
            return self._contour_to_segments(pts)

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
        contours: List[List[Point]] = []

        # Try different property names for accessing polygon data
        if hasattr(item, 'polygons') and item.polygons:
            # PolygonWithHoles style - process all contours.
            try:
                for poly in item.polygons:
                    if hasattr(poly, 'outline') and poly.outline:
                        contour = self._outline_to_points(poly.outline)
                        if contour:
                            contours.append(contour)

                    # Some representations expose holes on each polygon entry.
                    if hasattr(poly, 'holes') and poly.holes:
                        for hole in poly.holes:
                            hole_contour = self._outline_to_points(hole)
                            if hole_contour:
                                contours.append(hole_contour)
            except Exception:
                pass

        if not contours and hasattr(item, 'outline'):
            # Direct outline property
            try:
                outline = item.outline
                if hasattr(outline, 'outline'):
                    # PolygonWithHoles
                    outer = self._outline_to_points(outline.outline)
                    if outer:
                        contours.append(outer)
                    if hasattr(outline, 'holes') and outline.holes:
                        for hole in outline.holes:
                            hole_contour = self._outline_to_points(hole)
                            if hole_contour:
                                contours.append(hole_contour)
                else:
                    contour = self._outline_to_points(outline)
                    if contour:
                        contours.append(contour)
            except Exception:
                pass

        if not contours and hasattr(item, 'points'):
            # Simple points list
            try:
                contour = [self._vector_to_point(p) for p in item.points]
                if contour:
                    contours.append(contour)
            except Exception:
                pass

        if not contours and hasattr(item, 'vertices'):
            # Vertices property
            try:
                contour = [self._vector_to_point(v) for v in item.vertices]
                if contour:
                    contours.append(contour)
            except Exception:
                pass

        # Convert all contours to line segments
        for contour in contours:
            if len(contour) < 3:
                continue
            segments.extend(self._contour_to_segments(contour))
        if segments:
            logger.debug(
                "Polygon extraction: item_type=%s contours=%d segments=%d",
                type(item).__name__,
                len(contours),
                len(segments),
            )
        else:
            logger.debug("Polygon extraction yielded no segments for item_type=%s", type(item).__name__)

        return segments

    def _outline_to_points(self, outline) -> List[Point]:
        """Convert an IPC polygon outline object into points."""
        points: List[Point] = []
        if not hasattr(outline, '__iter__'):
            return points

        for node in outline:
            if hasattr(node, 'point'):
                points.append(self._vector_to_point(node.point))
            elif hasattr(node, 'x') and hasattr(node, 'y'):
                points.append(Point(self._nm_to_mm(node.x), self._nm_to_mm(node.y)))
        return points

    def _contour_to_segments(self, points: List[Point]) -> List[LineSegment]:
        """Convert one polygon contour to line segments."""
        segments: List[LineSegment] = []
        for i in range(len(points)):
            start = points[i]
            end = points[(i + 1) % len(points)]
            # Skip zero-length segments
            if abs(start.x - end.x) > 1e-6 or abs(start.y - end.y) > 1e-6:
                segments.append(LineSegment(start, end))
        return segments

    def _primitive_key(self, primitive):
        """Return a stable key for deduplicating primitives."""
        if isinstance(primitive, LineSegment):
            a = (round(primitive.start.x, 9), round(primitive.start.y, 9))
            b = (round(primitive.end.x, 9), round(primitive.end.y, 9))
            # Lines are undirected for dedupe purposes.
            return ("line", tuple(sorted([a, b])))

        if isinstance(primitive, Arc):
            return (
                "arc",
                (round(primitive.start.x, 9), round(primitive.start.y, 9)),
                (round(primitive.mid.x, 9), round(primitive.mid.y, 9)),
                (round(primitive.end.x, 9), round(primitive.end.y, 9)),
            )

        if isinstance(primitive, Circle):
            return (
                "circle",
                (round(primitive.center.x, 9), round(primitive.center.y, 9)),
                round(primitive.radius, 9),
            )

        if isinstance(primitive, Bezier):
            return (
                "bezier",
                (round(primitive.start.x, 9), round(primitive.start.y, 9)),
                (round(primitive.control1.x, 9), round(primitive.control1.y, 9)),
                (round(primitive.control2.x, 9), round(primitive.control2.y, 9)),
                (round(primitive.end.x, 9), round(primitive.end.y, 9)),
            )

        return ("unknown", repr(primitive))

    def _merge_primitives_without_duplicates(self, base, extra):
        """Merge primitive lists while avoiding duplicates."""
        merged = list(base)
        seen = {self._primitive_key(p) for p in merged}
        for primitive in extra:
            key = self._primitive_key(primitive)
            if key in seen:
                continue
            seen.add(key)
            merged.append(primitive)
        return merged

    def _extract_bezier(self, item) -> Bezier | None:
        """Extract bezier from BoardBezier."""
        if hasattr(item, 'start') and hasattr(item, 'control1') and hasattr(item, 'control2') and hasattr(item, 'end'):
            start = self._vector_to_point(item.start)
            ctrl1 = self._vector_to_point(item.control1)
            ctrl2 = self._vector_to_point(item.control2)
            end = self._vector_to_point(item.end)
            return Bezier(start, ctrl1, ctrl2, end)
        return None
