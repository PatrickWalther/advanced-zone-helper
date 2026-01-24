"""Create zones in KiCAD via SWIG API (pcbnew)."""

import logging
import pcbnew
from typing import List, Optional
from dataclasses import dataclass
from src.geometry import SimpleZone, RingZone, MultiHoleZone, Loop, LineSegment, Arc, Circle, Bezier
from src.geometry.arc_approximator import ArcApproximator

logger = logging.getLogger(__name__)

# Conversion factor
IU_PER_MM = pcbnew.IU_PER_MM if hasattr(pcbnew, 'IU_PER_MM') else 1000000


def mm_to_iu(value_mm) -> int:
    """Convert millimeters to KiCAD internal units."""
    return int(value_mm * IU_PER_MM)


@dataclass
class ZoneSettings:
    """Settings for zone creation."""
    layer: str = "F.Cu"
    net_name: Optional[str] = None
    priority: int = 0
    clearance_mm: float = 0.2
    min_thickness_mm: float = 0.1


class ZoneBuilderSWIG:
    """Creates zones in KiCAD board via SWIG API."""

    def __init__(self, board, arc_approximator: ArcApproximator):
        """Initialize zone builder.

        Args:
            board: pcbnew.BOARD object
            arc_approximator: Arc approximator for converting geometry
        """
        self.board = board
        self.arc_approximator = arc_approximator

    def create_zones(self, zones: List[SimpleZone | RingZone | MultiHoleZone], settings: ZoneSettings) -> int:
        """Create multiple zones.

        Args:
            zones: List of zones to create
            settings: Zone configuration settings

        Returns:
            Number of zones created successfully
        """
        success_count = 0

        for i, zone in enumerate(zones):
            logger.info(f"Creating zone {i+1}/{len(zones)}")

            if isinstance(zone, SimpleZone):
                if self.create_simple_zone(zone, settings):
                    success_count += 1
            elif isinstance(zone, RingZone):
                if self.create_ring_zone(zone, settings):
                    success_count += 1
            elif isinstance(zone, MultiHoleZone):
                if self.create_multi_hole_zone(zone, settings):
                    success_count += 1

        logger.info(f"Created {success_count}/{len(zones)} zones")
        return success_count

    def create_simple_zone(self, zone: SimpleZone, settings: ZoneSettings) -> bool:
        """Create a simple zone (single outline)."""
        try:
            logger.info(f"Creating simple zone on layer {settings.layer}")

            points_mm = self._loop_to_points_mm(zone.loop)

            if not points_mm or len(points_mm) < 3:
                logger.error(f"Not enough points: {len(points_mm) if points_mm else 0}")
                return False

            pcb_zone = self._create_zone(points_mm, None, settings)
            if pcb_zone:
                self.board.Add(pcb_zone)
                logger.info("Simple zone created successfully")
                return True

            return False

        except Exception as e:
            logger.error(f"Error creating simple zone: {e}", exc_info=True)
            return False

    def create_ring_zone(self, ring: RingZone, settings: ZoneSettings) -> bool:
        """Create a ring zone (outline with hole)."""
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

            pcb_zone = self._create_zone(outer_points_mm, [inner_points_mm], settings)
            if pcb_zone:
                self.board.Add(pcb_zone)
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

            pcb_zone = self._create_zone(outer_points_mm, holes_mm, settings)
            if pcb_zone:
                self.board.Add(pcb_zone)
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
                    points.extend(arc_points[:-1])

                elif isinstance(primitive, Circle):
                    circle_points = self.arc_approximator.approximate_circle(primitive)
                    points.extend(circle_points)

                elif isinstance(primitive, Bezier):
                    bezier_points = self.arc_approximator.approximate_bezier(primitive)
                    points.extend(bezier_points[:-1])

            # Don't close - we handle that in _create_zone
            
        except Exception as e:
            logger.error(f"Error converting loop to points: {e}", exc_info=True)

        logger.debug(f"Converted loop to {len(points)} points")
        return points

    def _sanitize_points_iu(self, points_mm: List[tuple]) -> List[tuple]:
        """Convert points to IU and remove duplicates.
        
        Args:
            points_mm: Points in millimeters
            
        Returns:
            Sanitized points in internal units (integers)
        """
        if not points_mm:
            return []
            
        points_iu = []
        for x_mm, y_mm in points_mm:
            x_iu = mm_to_iu(x_mm)
            y_iu = mm_to_iu(y_mm)
            
            # Skip if duplicate of last point
            if points_iu and points_iu[-1] == (x_iu, y_iu):
                continue
                
            points_iu.append((x_iu, y_iu))
        
        # Also check if last point duplicates first
        if len(points_iu) > 1 and points_iu[-1] == points_iu[0]:
            points_iu.pop()
            
        logger.debug(f"Sanitized {len(points_mm)} mm points to {len(points_iu)} IU points")
        return points_iu

    def _create_zone(self, outline_mm: List[tuple],
                     holes_mm: Optional[List[List[tuple]]],
                     settings: ZoneSettings):
        """Create a ZONE object."""
        try:
            # Create zone
            zone = pcbnew.ZONE(self.board)

            # Set layer
            layer_id = self._get_layer_id(settings.layer)
            zone.SetLayer(layer_id)
            logger.debug(f"Set layer to {settings.layer} (id={layer_id})")

            # Set net - use "no net" if not specified
            if settings.net_name:
                net = self.board.FindNet(settings.net_name)
                if net:
                    zone.SetNet(net)
                    logger.debug(f"Set net to '{settings.net_name}'")
            else:
                # Set to no net (net code 0)
                zone.SetNetCode(0)

            # Set zone name for identification
            zone.SetZoneName("AdvZoneHelper")

            # Set priority
            zone.SetAssignedPriority(settings.priority)

            # Set clearance and min thickness
            zone.SetLocalClearance(mm_to_iu(settings.clearance_mm))
            zone.SetMinThickness(mm_to_iu(settings.min_thickness_mm))

            # Set fill mode to polygon (solid fill)
            zone.SetFillMode(pcbnew.ZONE_FILL_MODE_POLYGONS)

            # Set island removal mode
            zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)

            # Sanitize points (convert to IU and remove duplicates)
            outline_iu = self._sanitize_points_iu(outline_mm)
            
            if len(outline_iu) < 3:
                logger.error(f"Not enough unique points after sanitization: {len(outline_iu)}")
                return None

            # CRITICAL: Use zone.Outline() to get the zone-owned SHAPE_POLY_SET
            # DO NOT create a new SHAPE_POLY_SET and SetOutline() - that causes
            # SWIG ownership issues where Python GC frees the C++ object while
            # the zone still references it, causing crashes on zone refill.
            poly = zone.Outline()
            poly.RemoveAllContours()
            
            # For ring zones (with holes), KiCAD expects a SINGLE polygon with a
            # zero-width "bridge" connecting outer and inner contours, NOT separate
            # outline + hole contours. The bridge creates a slit that makes the
            # ring topologically a single closed polygon.
            if holes_mm and len(holes_mm) > 0:
                # Build bridged polygon for ring zone
                bridged_points = self._build_bridged_polygon(outline_iu, holes_mm)
                if bridged_points:
                    poly.NewOutline()
                    for x_iu, y_iu in bridged_points:
                        poly.Append(x_iu, y_iu)
                    logger.debug(f"Added bridged ring polygon with {len(bridged_points)} points")
                else:
                    logger.error("Failed to build bridged polygon")
                    return None
            else:
                # Simple zone - just add outline points
                poly.NewOutline()
                for x_iu, y_iu in outline_iu:
                    poly.Append(x_iu, y_iu)
                logger.debug(f"Added {len(outline_iu)} points to outline")

            # Log polygon stats for debugging
            logger.debug(f"Polygon outline count: {poly.OutlineCount()}")
            if poly.OutlineCount() > 0:
                logger.debug(f"Outline 0 point count: {poly.Outline(0).PointCount()}")
                logger.debug(f"Hole count for outline 0: {poly.HoleCount(0)}")

            # Hatch the zone border for visibility
            zone.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_DIAGONAL_EDGE, 
                                        mm_to_iu(0.5), True)

            # Don't fill here - let KiCAD do it when user requests
            # This avoids potential crashes during zone fill
            zone.SetIsFilled(False)
            zone.SetNeedRefill(True)

            return zone

        except Exception as e:
            logger.error(f"Error creating zone: {e}", exc_info=True)
            return None

    def _signed_area(self, pts: List[tuple]) -> float:
        """Calculate signed area of polygon using shoelace formula.
        
        Positive = CCW winding, Negative = CW winding (in standard coords).
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
        return list(reversed(pts)) if s != desired_sign else pts

    def _rotate_list(self, pts: List[tuple], start_idx: int) -> List[tuple]:
        """Rotate list so element at start_idx becomes first."""
        return pts[start_idx:] + pts[:start_idx]

    def _build_bridged_polygon(self, outer_iu: List[tuple], 
                                  holes_mm: List[List[tuple]]) -> List[tuple]:
        """Build a single polygon with bridges connecting outer to all holes.
        
        KiCAD zones with holes use zero-width "slits" connecting the outer
        boundary to each hole, making it topologically a single closed polygon.
        
        This implementation properly handles winding direction:
        - Normalizes outer boundary to a consistent winding
        - Normalizes holes to OPPOSITE winding
        - Bridges from the ORIGINAL outer contour (not previously inserted holes)
        """
        if not holes_mm or len(holes_mm) == 0:
            return outer_iu

        # Normalize OUTER winding to a deterministic sign (+1 = CCW)
        outer = list(outer_iu)
        outer_sign = self._winding_sign(outer)
        if outer_sign == 0:
            logger.warning("Outer polygon has zero area; returning as-is")
            return outer

        if outer_sign < 0:
            outer = list(reversed(outer))
            outer_sign = 1

        desired_hole_sign = -outer_sign  # Holes must be opposite winding

        # Sanitize + normalize HOLES to opposite winding
        holes_iu = []
        for hole_mm in holes_mm:
            hole = self._sanitize_points_iu(hole_mm)
            if len(hole) < 3:
                continue
            if self._winding_sign(hole) == 0:
                logger.warning("Skipping degenerate hole (zero area)")
                continue
            hole = self._ensure_winding(hole, desired_hole_sign)
            holes_iu.append(hole)

        if not holes_iu:
            logger.warning("No valid holes after sanitization")
            return outer

        logger.debug(f"Building bridged polygon with {len(holes_iu)} holes (outer sign={outer_sign})")

        # Build result incrementally, but ALWAYS bridge from the OUTER contour
        result = list(outer)

        # Mapping from outer vertex index -> current index in result list
        outer_to_result_idx = list(range(len(outer)))

        for hole_idx, hole in enumerate(holes_iu):
            # Find closest vertex pair using OUTER only (not result with previous holes)
            min_dist = float("inf")
            outer_bridge_i = 0
            hole_bridge_j = 0

            for i, (ox, oy) in enumerate(outer):
                for j, (hx, hy) in enumerate(hole):
                    d = (ox - hx) ** 2 + (oy - hy) ** 2
                    if d < min_dist:
                        min_dist = d
                        outer_bridge_i = i
                        hole_bridge_j = j

            bridge_outer_pt = outer[outer_bridge_i]
            bridge_hole_pt = hole[hole_bridge_j]

            # Rotate hole so traversal starts at bridge point, then traverse FORWARD
            # (hole is already in opposite winding, so forward traversal is correct)
            hole_rot = self._rotate_list(hole, hole_bridge_j)

            # Build insertion:
            # outer_bridge -> bridge_hole -> (around hole in normalized direction) -> bridge_hole -> outer_bridge
            insertion = []
            insertion.append(bridge_hole_pt)
            insertion.extend(hole_rot[1:])      # rest of hole (already starts at bridge point)
            insertion.append(bridge_hole_pt)    # close around the hole
            insertion.append(bridge_outer_pt)   # bridge back to outer

            # Insert right after the chosen outer vertex in *current* result
            insert_at = outer_to_result_idx[outer_bridge_i] + 1
            result[insert_at:insert_at] = insertion

            # Update mapping for subsequent outer vertices
            delta = len(insertion)
            for k in range(outer_bridge_i + 1, len(outer_to_result_idx)):
                outer_to_result_idx[k] += delta

            logger.debug(f"Hole {hole_idx}: bridged at outer[{outer_bridge_i}] -> hole[{hole_bridge_j}], result now {len(result)} pts")

        logger.debug(f"Built bridged polygon: {len(outer)} outer + {len(holes_iu)} holes = {len(result)} total points")
        return result

    def _get_layer_id(self, layer_name: str) -> int:
        """Get layer ID from name."""
        # Skip separator
        if layer_name.startswith("─"):
            return pcbnew.F_Cu
            
        # Try board method first
        try:
            layer_id = self.board.GetLayerID(layer_name)
            if layer_id >= 0:
                return layer_id
        except:
            pass

        # Fallback to known layer IDs
        layer_map = {
            # Copper layers
            "F.Cu": pcbnew.F_Cu,
            "B.Cu": pcbnew.B_Cu,
            "In1.Cu": pcbnew.In1_Cu,
            "In2.Cu": pcbnew.In2_Cu,
            "In3.Cu": pcbnew.In3_Cu,
            "In4.Cu": pcbnew.In4_Cu,
            "In5.Cu": pcbnew.In5_Cu if hasattr(pcbnew, 'In5_Cu') else pcbnew.F_Cu,
            "In6.Cu": pcbnew.In6_Cu if hasattr(pcbnew, 'In6_Cu') else pcbnew.F_Cu,
            # Technical layers
            "F.Adhes": pcbnew.F_Adhes,
            "B.Adhes": pcbnew.B_Adhes,
            "F.Paste": pcbnew.F_Paste,
            "B.Paste": pcbnew.B_Paste,
            "F.SilkS": pcbnew.F_SilkS,
            "B.SilkS": pcbnew.B_SilkS,
            "F.Mask": pcbnew.F_Mask,
            "B.Mask": pcbnew.B_Mask,
            "Dwgs.User": pcbnew.Dwgs_User,
            "Cmts.User": pcbnew.Cmts_User,
            "Eco1.User": pcbnew.Eco1_User,
            "Eco2.User": pcbnew.Eco2_User,
            "Edge.Cuts": pcbnew.Edge_Cuts,
            "Margin": pcbnew.Margin,
            "F.CrtYd": pcbnew.F_CrtYd,
            "B.CrtYd": pcbnew.B_CrtYd,
            "F.Fab": pcbnew.F_Fab,
            "B.Fab": pcbnew.B_Fab,
        }
        
        # Handle User layers dynamically
        if layer_name.startswith("User."):
            try:
                user_num = int(layer_name.split(".")[1])
                user_layer = getattr(pcbnew, f"User_{user_num}", None)
                if user_layer is not None:
                    return user_layer
            except:
                pass
        
        return layer_map.get(layer_name, pcbnew.F_Cu)

    def get_available_layers(self) -> List[str]:
        """Get list of available copper layer names."""
        layers = []
        try:
            layer_ids = [
                pcbnew.F_Cu, pcbnew.B_Cu,
                pcbnew.In1_Cu, pcbnew.In2_Cu,
                pcbnew.In3_Cu, pcbnew.In4_Cu,
            ]

            for layer_id in layer_ids:
                name = self.board.GetLayerName(layer_id)
                if name:
                    layers.append(str(name))

        except Exception as e:
            logger.error(f"Error getting layers: {e}")
            layers = ["F.Cu", "B.Cu"]

        return layers

    def get_available_nets(self) -> List[str]:
        """Get list of available net names."""
        nets = []
        try:
            netinfo = self.board.GetNetInfo()
            for net in netinfo.NetsByName():
                nets.append(str(net))
        except Exception as e:
            logger.error(f"Error getting nets: {e}")

        return nets
