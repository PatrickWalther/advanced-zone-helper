"""Extract geometric shapes from KiCad board selections using IPC API."""

import math
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
        selected_items = self.board.get_selection()

        if not selected_items:
            return primitives

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
