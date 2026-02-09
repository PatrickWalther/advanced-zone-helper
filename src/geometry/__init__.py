"""Geometry processing module for Advanced Zone Helper."""

from dataclasses import dataclass
from typing import Tuple, List, NamedTuple
import math


class Point(NamedTuple):
    """A 2D point."""
    x: float
    y: float


@dataclass
class LineSegment:
    """Represents a line segment."""
    start: Point
    end: Point

    def endpoints(self) -> List[Point]:
        """Return list of endpoints."""
        return [self.start, self.end]

    def length(self) -> float:
        """Calculate length of line segment."""
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        return math.sqrt(dx * dx + dy * dy)


@dataclass
class Arc:
    """Represents an arc defined by three points."""
    start: Point
    mid: Point
    end: Point

    def endpoints(self) -> List[Point]:
        """Return list of endpoints."""
        return [self.start, self.end]

    def center_radius_angles(self) -> Tuple[Point, float, float, float]:
        """Calculate arc center, radius, start angle, and end angle.

        Returns:
            (center, radius, start_angle_rad, end_angle_rad)
        """
        # Calculate circumcenter from three points with a determinant form.
        # This avoids slope-based divide-by-zero for horizontal/vertical chords.
        x1, y1 = self.start
        x2, y2 = self.mid
        x3, y3 = self.end

        d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-12:
            # Collinear or nearly collinear points cannot define a stable circle.
            raise ValueError("Degenerate arc: points are collinear")

        x1_sq_y1_sq = x1 * x1 + y1 * y1
        x2_sq_y2_sq = x2 * x2 + y2 * y2
        x3_sq_y3_sq = x3 * x3 + y3 * y3

        cx = (
            x1_sq_y1_sq * (y2 - y3)
            + x2_sq_y2_sq * (y3 - y1)
            + x3_sq_y3_sq * (y1 - y2)
        ) / d
        cy = (
            x1_sq_y1_sq * (x3 - x2)
            + x2_sq_y2_sq * (x1 - x3)
            + x3_sq_y3_sq * (x2 - x1)
        ) / d

        # Calculate radius
        radius = math.sqrt((x1 - cx) ** 2 + (y1 - cy) ** 2)

        # Calculate angles
        start_angle = math.atan2(y1 - cy, x1 - cx)
        mid_angle = math.atan2(y2 - cy, x2 - cx)
        end_angle = math.atan2(y3 - cy, x3 - cx)

        # Normalize angles to determine arc direction
        # Check if mid_angle is between start and end (accounting for wraparound)
        def normalize_angle(angle, reference):
            while angle - reference > math.pi:
                angle -= 2 * math.pi
            while angle - reference < -math.pi:
                angle += 2 * math.pi
            return angle

        _ = normalize_angle(mid_angle, start_angle)
        end_angle = normalize_angle(end_angle, start_angle)

        return ((cx, cy), radius, start_angle, end_angle)


@dataclass
class Circle:
    """Represents a complete circle."""
    center: Point
    radius: float

    def endpoints(self) -> List[Point]:
        """Circles have no endpoints (they are closed)."""
        return []

    def is_closed(self) -> bool:
        """Circles are always closed."""
        return True


@dataclass
class Bezier:
    """Represents a cubic bezier curve defined by 4 points."""
    start: Point
    control1: Point
    control2: Point
    end: Point

    def endpoints(self) -> List[Point]:
        """Return list of endpoints."""
        return [self.start, self.end]


@dataclass
class Loop:
    """Represents a closed loop of primitives."""
    primitives: List[LineSegment | Arc | Circle | Bezier]
    is_closed: bool = True

    def __post_init__(self):
        """Validate loop closure."""
        if len(self.primitives) == 1 and isinstance(self.primitives[0], Circle):
            self.is_closed = True
            return

        if len(self.primitives) < 2:
            self.is_closed = False
            return

        # Check if endpoints connect
        # Use 0.01mm tolerance for DXF imports which may have precision issues
        TOLERANCE = 0.01  # mm
        for i in range(len(self.primitives)):
            curr = self.primitives[i]
            next_prim = self.primitives[(i + 1) % len(self.primitives)]

            curr_end = curr.endpoints()[-1] if curr.endpoints() else None
            next_start = next_prim.endpoints()[0] if next_prim.endpoints() else None

            if curr_end and next_start:
                dist = math.sqrt((curr_end[0] - next_start[0]) ** 2 +
                               (curr_end[1] - next_start[1]) ** 2)
                if dist > TOLERANCE:
                    self.is_closed = False
                    return


@dataclass
class RingZone:
    """Represents a ring zone (area between two loops)."""
    outer_loop: Loop
    inner_loop: Loop

    def __str__(self):
        return f"Ring Zone (outer: {len(self.outer_loop.primitives)} primitives, inner: {len(self.inner_loop.primitives)} primitives)"


@dataclass
class MultiHoleZone:
    """Represents a zone with multiple holes (1 outer + N inner loops)."""
    outer_loop: Loop
    inner_loops: List[Loop]

    def __str__(self):
        return f"Multi-Hole Zone (outer: {len(self.outer_loop.primitives)} primitives, {len(self.inner_loops)} holes)"


@dataclass
class SimpleZone:
    """Represents a simple zone (single loop)."""
    loop: Loop

    def __str__(self):
        return f"Simple Zone ({len(self.loop.primitives)} primitives)"
