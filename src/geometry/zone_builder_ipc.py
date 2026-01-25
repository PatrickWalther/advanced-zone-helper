"""Create zones in KiCad via IPC API."""

import logging
from typing import List, Optional
from dataclasses import dataclass
from . import SimpleZone, RingZone, MultiHoleZone, Loop, LineSegment, Arc, Circle, Bezier
from .arc_approximator import ArcApproximator

logger = logging.getLogger(__name__)


@dataclass
class ZoneSettings:
    """Settings for zone creation."""
    layer: str = "F.Cu"
    net_name: Optional[str] = None
    priority: int = 0
    clearance_mm: float = 0.2
    min_thickness_mm: float = 0.1


class ZoneBuilderIPC:
    """Creates zones in KiCad board via IPC API."""

    # Layer name to BoardLayer enum mapping
    LAYER_MAP = {
        "F.Cu": "BL_F_Cu",
        "B.Cu": "BL_B_Cu",
        "In1.Cu": "BL_In1_Cu",
        "In2.Cu": "BL_In2_Cu",
        "In3.Cu": "BL_In3_Cu",
        "In4.Cu": "BL_In4_Cu",
        "In5.Cu": "BL_In5_Cu",
        "In6.Cu": "BL_In6_Cu",
        "In7.Cu": "BL_In7_Cu",
        "In8.Cu": "BL_In8_Cu",
    }

    def __init__(self, board, arc_approximator: Optional[ArcApproximator] = None):
        """Initialize zone builder.

        Args:
            board: kicad.Board object (IPC API)
            arc_approximator: Arc approximator for converting geometry (optional)
        """
        self.board = board
        self.arc_approximator = arc_approximator or ArcApproximator()
        logger.info("ZoneBuilderIPC initialized")

    def create_zones(self, zones: List[SimpleZone | RingZone | MultiHoleZone], settings: ZoneSettings) -> int:
        """Create multiple zones.

        Args:
            zones: List of zones to create
            settings: Zone configuration settings

        Returns:
            Number of zones created successfully
        """
        success_count = 0

        logger.info(f"Creating {len(zones)} zones on layer {settings.layer}")

        for i, zone in enumerate(zones):
            logger.info(f"Creating zone {i+1}/{len(zones)}: {type(zone).__name__}")

            try:
                if isinstance(zone, SimpleZone):
                    if self.create_simple_zone(zone, settings):
                        success_count += 1
                elif isinstance(zone, RingZone):
                    if self.create_ring_zone(zone, settings):
                        success_count += 1
                elif isinstance(zone, MultiHoleZone):
                    if self.create_multi_hole_zone(zone, settings):
                        success_count += 1
            except Exception as e:
                logger.error(f"Failed to create zone {i+1}: {e}", exc_info=True)

        logger.info(f"Created {success_count}/{len(zones)} zones successfully")
        return success_count

    def create_simple_zone(self, zone: SimpleZone, settings: ZoneSettings) -> bool:
        """Create a simple zone (single outline, no holes)."""
        try:
            logger.info(f"Creating simple zone on layer {settings.layer}")

            points_mm = self._loop_to_points_mm(zone.loop)

            if not points_mm or len(points_mm) < 3:
                logger.error(f"Not enough points: {len(points_mm) if points_mm else 0}")
                return False

            # Create zone via IPC API
            success = self._create_zone_ipc(points_mm, None, settings)

            if success:
                logger.info("Simple zone created successfully")
                return True

            return False

        except Exception as e:
            logger.error(f"Error creating simple zone: {e}", exc_info=True)
            return False

    def create_ring_zone(self, ring: RingZone, settings: ZoneSettings) -> bool:
        """Create a ring zone (outline with single hole)."""
        try:
            logger.info(f"Creating ring zone on layer {settings.layer}")

            outer_points_mm = self._loop_to_points_mm(ring.outer_loop)
            inner_points_mm = self._loop_to_points_mm(ring.inner_loop)

            if not outer_points_mm or len(outer_points_mm) < 3:
                logger.error(f"Not enough outer points")
                return False

            if not inner_points_mm or len(inner_points_mm) < 3:
                logger.error(f"Not enough inner points")
                return False

            # Create zone with hole
            success = self._create_zone_ipc(outer_points_mm, [inner_points_mm], settings)

            if success:
                logger.info("Ring zone created successfully")
                return True

            return False

        except Exception as e:
            logger.error(f"Error creating ring zone: {e}", exc_info=True)
            return False

    def create_multi_hole_zone(self, multi_hole: MultiHoleZone, settings: ZoneSettings) -> bool:
        """Create a zone with multiple holes."""
        try:
            logger.info(f"Creating multi-hole zone on layer {settings.layer} with {len(multi_hole.inner_loops)} holes")

            outer_points_mm = self._loop_to_points_mm(multi_hole.outer_loop)

            if not outer_points_mm or len(outer_points_mm) < 3:
                logger.error(f"Not enough outer points")
                return False

            # Convert all inner loops to points
            holes_mm = []
            for i, inner_loop in enumerate(multi_hole.inner_loops):
                inner_points_mm = self._loop_to_points_mm(inner_loop)
                if not inner_points_mm or len(inner_points_mm) < 3:
                    logger.warning(f"Skipping hole {i} - not enough points")
                    continue
                holes_mm.append(inner_points_mm)

            logger.debug(f"Prepared {len(holes_mm)} valid holes")

            # Create zone with holes
            success = self._create_zone_ipc(outer_points_mm, holes_mm, settings)

            if success:
                logger.info("Multi-hole zone created successfully")
                return True

            return False

        except Exception as e:
            logger.error(f"Error creating multi-hole zone: {e}", exc_info=True)
            return False

    def _loop_to_points_mm(self, loop: Loop) -> List[tuple]:
        """Convert loop to list of points in mm."""
        points = []

        try:
            for primitive in loop.primitives:
                if isinstance(primitive, LineSegment):
                    points.append(primitive.start)

                elif isinstance(primitive, Arc):
                    arc_points = self.arc_approximator.approximate_arc(primitive)
                    points.extend(arc_points[:-1])  # Exclude last point to avoid duplication

                elif isinstance(primitive, Circle):
                    circle_points = self.arc_approximator.approximate_circle(primitive)
                    points.extend(circle_points)

                elif isinstance(primitive, Bezier):
                    bezier_points = self.arc_approximator.approximate_bezier(primitive)
                    points.extend(bezier_points[:-1])  # Exclude last point

        except Exception as e:
            logger.error(f"Error converting loop to points: {e}", exc_info=True)

        logger.debug(f"Converted loop to {len(points)} points")
        return points

    def _sanitize_points(self, points_mm: List[tuple]) -> List[tuple]:
        """Remove duplicate consecutive points."""
        if not points_mm:
            return []

        sanitized = []
        epsilon = 1e-6

        for x, y in points_mm:
            if sanitized:
                last_x, last_y = sanitized[-1]
                if abs(x - last_x) < epsilon and abs(y - last_y) < epsilon:
                    continue
            sanitized.append((x, y))

        # Check if last point duplicates first (don't remove - we need closed polygon)
        logger.debug(f"Sanitized {len(points_mm)} points to {len(sanitized)} unique points")
        return sanitized

    def _signed_area(self, pts: List[tuple]) -> float:
        """Calculate signed area of polygon using shoelace formula.

        Positive = CCW winding, Negative = CW winding.
        """
        a = 0
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            a += x1 * y2 - x2 * y1
        return 0.5 * a

    def _winding_sign(self, pts: List[tuple]) -> int:
        """Get winding direction: +1 for CCW, -1 for CW, 0 for degenerate."""
        a = self._signed_area(pts)
        return 1 if a > 0 else (-1 if a < 0 else 0)

    def _ensure_winding(self, pts: List[tuple], desired_sign: int) -> List[tuple]:
        """Ensure polygon has the desired winding direction."""
        s = self._winding_sign(pts)
        if s == 0:
            return pts
        if s != desired_sign:
            return list(reversed(pts))
        return pts

    def _create_zone_ipc(self, outline_mm: List[tuple],
                         holes_mm: Optional[List[List[tuple]]],
                         settings: ZoneSettings) -> bool:
        """Create a zone using the official KiCad IPC API.

        Uses kipy.board_types.Zone with PolygonWithHoles and PolyLine.
        """
        try:
            logger.debug("=" * 60)
            logger.debug("Creating zone via KiCad IPC API")
            logger.debug(f"Outline: {len(outline_mm)} points")
            logger.debug(f"Holes: {len(holes_mm) if holes_mm else 0}")
            logger.debug("=" * 60)

            # Import IPC API classes
            from kipy.board_types import BoardLayer, Zone
            from kipy.common_types import PolygonWithHoles
            from kipy.geometry import PolyLine, PolyLineNode
            from kipy.util import from_mm

            # Sanitize outline points
            outline = self._sanitize_points(outline_mm)
            if len(outline) < 3:
                logger.error(f"Not enough unique points after sanitization: {len(outline)}")
                return False

            logger.debug(f"Outline winding: {self._winding_sign(outline)}")

            # Create outline PolyLine
            outline_polyline = PolyLine()
            for x, y in outline:
                outline_polyline.append(PolyLineNode.from_xy(from_mm(x), from_mm(y)))
            # Close the polygon
            outline_polyline.append(PolyLineNode.from_xy(from_mm(outline[0][0]), from_mm(outline[0][1])))

            logger.debug(f"Created outline PolyLine with {len(outline) + 1} nodes")

            # Create PolygonWithHoles
            polygon = PolygonWithHoles()
            polygon.outline = outline_polyline

            # Add holes if present
            # Detect outline winding and ensure holes match (some APIs want same, some want opposite)
            outline_winding = self._winding_sign(outline)

            if holes_mm:
                for i, hole_mm in enumerate(holes_mm):
                    hole = self._sanitize_points(hole_mm)
                    if len(hole) < 3:
                        logger.warning(f"Skipping hole {i} - insufficient points")
                        continue

                    hole_winding = self._winding_sign(hole)
                    logger.debug(f"Hole {i}: winding={hole_winding}, outline_winding={outline_winding}, {len(hole)} points")

                    # Ensure hole has SAME winding as outline (KiCad IPC convention)
                    if hole_winding != outline_winding and hole_winding != 0:
                        hole = list(reversed(hole))
                        logger.debug(f"Reversed hole {i} to match outline winding")

                    hole_polyline = PolyLine()
                    for x, y in hole:
                        hole_polyline.append(PolyLineNode.from_xy(from_mm(x), from_mm(y)))
                    # Close the hole
                    hole_polyline.append(PolyLineNode.from_xy(from_mm(hole[0][0]), from_mm(hole[0][1])))

                    # Add hole to polygon using add_hole() method
                    polygon.add_hole(hole_polyline)
                    logger.debug(f"Added hole {i} with {len(hole) + 1} nodes")

            # Create Zone object
            zone = Zone()

            # Set layer(s)
            layer_enum_name = self.LAYER_MAP.get(settings.layer, "BL_F_Cu")
            try:
                layer_enum = getattr(BoardLayer, layer_enum_name)
                zone.layers = [layer_enum]
                logger.debug(f"Set layer to {settings.layer} ({layer_enum_name})")
            except AttributeError:
                logger.warning(f"Layer {layer_enum_name} not found, using F.Cu")
                zone.layers = [BoardLayer.BL_F_Cu]

            # Set outline
            zone.outline = polygon

            # Set optional properties if available
            if hasattr(zone, 'priority'):
                zone.priority = settings.priority

            if hasattr(zone, 'clearance') and settings.clearance_mm:
                zone.clearance = from_mm(settings.clearance_mm)

            if hasattr(zone, 'min_thickness') and settings.min_thickness_mm:
                zone.min_thickness = from_mm(settings.min_thickness_mm)

            if hasattr(zone, 'name'):
                zone.name = "AdvZoneHelper"

            # Set net if specified
            if settings.net_name and hasattr(zone, 'net'):
                # Try to find net by name
                try:
                    if hasattr(self.board, 'get_nets'):
                        nets = self.board.get_nets()
                        for net in nets:
                            net_name = net.name if hasattr(net, 'name') else str(net)
                            if net_name == settings.net_name:
                                zone.net = net
                                logger.debug(f"Set net to {settings.net_name}")
                                break
                except Exception as e:
                    logger.warning(f"Could not set net: {e}")

            # Add zone to board
            logger.debug("Adding zone to board via create_items()")
            result = self.board.create_items(zone)

            if result:
                logger.info("Zone created successfully via IPC API")
                return True
            else:
                logger.warning("create_items() returned empty/None result")
                return False

        except ImportError as e:
            logger.error(f"Failed to import IPC API classes: {e}")
            logger.error("Make sure kicad-python (kipy) is installed correctly")
            return False
        except Exception as e:
            logger.error(f"Error creating zone via IPC: {e}", exc_info=True)
            return False
