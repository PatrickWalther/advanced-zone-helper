"""Zone selection dialog with preview for IPC-based plugin."""

import logging
import wx
from typing import List, Optional
from src.geometry import SimpleZone, RingZone, MultiHoleZone, LineSegment, Arc, Circle, Bezier
from src.geometry.arc_approximator import ArcApproximator
from src.geometry.zone_builder_ipc import ZoneSettings, ZoneBuilderIPC

logger = logging.getLogger(__name__)


class ZonePreviewPanel(wx.Panel):
    """Panel that displays a preview of detected zones."""

    def __init__(self, parent, zones, arc_approximator):
        super().__init__(parent, size=(300, 300))
        self.zones = zones
        self.arc_approximator = arc_approximator
        self.selected_indices = set()

        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def set_selected(self, indices: set):
        """Set which zones are selected."""
        self.selected_indices = indices
        self.Refresh()

    # Color palette for zones (distinct colors with good visibility)
    ZONE_COLORS = [
        (66, 133, 244),   # Blue
        (234, 67, 53),    # Red
        (251, 188, 5),    # Yellow
        (52, 168, 83),    # Green
        (255, 112, 67),   # Orange
        (156, 39, 176),   # Purple
        (0, 188, 212),    # Cyan
        (255, 193, 7),    # Amber
    ]

    def on_paint(self, event):
        """Paint the preview."""
        dc = wx.AutoBufferedPaintDC(self)
        gc = wx.GraphicsContext.Create(dc)

        gc.SetBrush(wx.Brush(wx.Colour(40, 40, 40)))
        width, height = self.GetSize()
        gc.DrawRectangle(0, 0, width, height)

        if not self.zones:
            return

        # Calculate bounding box
        all_points = []
        for zone in self.zones:
            points = self._get_zone_points(zone)
            all_points.extend(points)

        if not all_points:
            return

        min_x = min(p[0] for p in all_points)
        max_x = max(p[0] for p in all_points)
        min_y = min(p[1] for p in all_points)
        max_y = max(p[1] for p in all_points)

        # Calculate scale
        margin = 20
        data_width = max_x - min_x
        data_height = max_y - min_y

        if data_width == 0 or data_height == 0:
            return

        scale_x = (width - 2 * margin) / data_width
        scale_y = (height - 2 * margin) / data_height
        scale = min(scale_x, scale_y)

        # Transform function
        def transform(point):
            x = margin + (point[0] - min_x) * scale
            y = margin + (point[1] - min_y) * scale
            return (x, y)

        # Sort zones by area (largest first) so smaller zones draw on top
        zone_indices = list(range(len(self.zones)))
        zone_areas = []
        for zone in self.zones:
            pts = self._get_zone_points(zone)
            area = self._calculate_area(pts) if len(pts) >= 3 else 0
            zone_areas.append(area)
        zone_indices.sort(key=lambda i: zone_areas[i], reverse=True)

        # Draw zones (largest first)
        for i in zone_indices:
            zone = self.zones[i]
            points = self._get_zone_points(zone)
            if len(points) < 3:
                continue

            screen_points = [transform(p) for p in points]

            # Get color for this zone
            base_color = self.ZONE_COLORS[i % len(self.ZONE_COLORS)]

            # Set colors based on selection
            if i in self.selected_indices:
                fill_alpha = 180
                stroke_width = 3
            else:
                fill_alpha = 80
                stroke_width = 2

            fill_color = wx.Colour(base_color[0], base_color[1], base_color[2], fill_alpha)
            stroke_color = wx.Colour(base_color[0], base_color[1], base_color[2], 255)

            gc.SetPen(gc.CreatePen(wx.Pen(stroke_color, stroke_width)))
            gc.SetBrush(gc.CreateBrush(wx.Brush(fill_color)))

            # Create path for polygon
            path = gc.CreatePath()
            path.MoveToPoint(screen_points[0][0], screen_points[0][1])
            for pt in screen_points[1:]:
                path.AddLineToPoint(pt[0], pt[1])
            path.CloseSubpath()
            gc.DrawPath(path)

            # For ring zones, draw inner hole (cut out)
            if isinstance(zone, RingZone):
                inner_points = self._loop_to_points(zone.inner_loop)
                if len(inner_points) >= 3:
                    inner_screen = [transform(p) for p in inner_points]
                    gc.SetBrush(gc.CreateBrush(wx.Brush(wx.Colour(40, 40, 40, 255))))
                    gc.SetPen(gc.CreatePen(wx.Pen(stroke_color, stroke_width)))

                    hole_path = gc.CreatePath()
                    hole_path.MoveToPoint(inner_screen[0][0], inner_screen[0][1])
                    for pt in inner_screen[1:]:
                        hole_path.AddLineToPoint(pt[0], pt[1])
                    hole_path.CloseSubpath()
                    gc.DrawPath(hole_path)

            # For multi-hole zones, draw all inner holes (cut out)
            elif isinstance(zone, MultiHoleZone):
                for inner_loop in zone.inner_loops:
                    inner_points = self._loop_to_points(inner_loop)
                    if len(inner_points) >= 3:
                        inner_screen = [transform(p) for p in inner_points]
                        gc.SetBrush(gc.CreateBrush(wx.Brush(wx.Colour(40, 40, 40, 255))))
                        gc.SetPen(gc.CreatePen(wx.Pen(stroke_color, stroke_width)))

                        hole_path = gc.CreatePath()
                        hole_path.MoveToPoint(inner_screen[0][0], inner_screen[0][1])
                        for pt in inner_screen[1:]:
                            hole_path.AddLineToPoint(pt[0], pt[1])
                        hole_path.CloseSubpath()
                        gc.DrawPath(hole_path)

    def _calculate_area(self, points: List[tuple]) -> float:
        """Calculate polygon area using shoelace formula."""
        n = len(points)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += points[i][0] * points[j][1]
            area -= points[j][0] * points[i][1]
        return abs(area) / 2.0

    def _get_zone_points(self, zone) -> List[tuple]:
        """Get polygon points for a zone."""
        if isinstance(zone, SimpleZone):
            return self._loop_to_points(zone.loop)
        elif isinstance(zone, RingZone):
            return self._loop_to_points(zone.outer_loop)
        elif isinstance(zone, MultiHoleZone):
            return self._loop_to_points(zone.outer_loop)
        return []

    def _loop_to_points(self, loop) -> List[tuple]:
        """Convert loop to list of points."""
        points = []
        for primitive in loop.primitives:
            if isinstance(primitive, LineSegment):
                points.append(primitive.start)
            elif isinstance(primitive, Arc):
                arc_pts = self.arc_approximator.approximate_arc(primitive)
                points.extend(arc_pts[:-1])
            elif isinstance(primitive, Circle):
                circle_pts = self.arc_approximator.approximate_circle(primitive)
                points.extend(circle_pts)
            elif isinstance(primitive, Bezier):
                bezier_pts = self.arc_approximator.approximate_bezier(primitive)
                points.extend(bezier_pts[:-1])
        return points


class ZoneDialogIPC(wx.Dialog):
    """Dialog for selecting zones to create and configuring settings."""

    ID_OK = wx.ID_OK
    ID_CANCEL = wx.ID_CANCEL

    def __init__(self, simple_zones: List[SimpleZone], ring_zones: List[RingZone],
                 multi_hole_zones: List[MultiHoleZone],
                 arc_approximator: ArcApproximator, board):
        super().__init__(None, title="Advanced Zone Helper",
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.simple_zones = simple_zones
        self.ring_zones = ring_zones
        self.multi_hole_zones = multi_hole_zones
        self.arc_approximator = arc_approximator
        self.board = board
        self.all_zones = simple_zones + ring_zones + multi_hole_zones

        self._create_ui()
        self.SetSize(800, 600)
        self.Centre()

    def _create_ui(self):
        """Create the dialog UI."""
        main_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Left side: zone list and settings
        left_panel = wx.Panel(self)
        left_sizer = wx.BoxSizer(wx.VERTICAL)

        # Zone list
        list_label = wx.StaticText(left_panel, label="Detected Zones (check to create):")
        left_sizer.Add(list_label, 0, wx.ALL, 5)

        self.zone_list = wx.CheckListBox(left_panel)
        for i, zone in enumerate(self.all_zones):
            if isinstance(zone, SimpleZone):
                label = f"Simple Zone {i+1}: {len(zone.loop.primitives)} primitives"
            elif isinstance(zone, RingZone):
                label = f"Ring Zone {i+1}: outer={len(zone.outer_loop.primitives)}, inner={len(zone.inner_loop.primitives)}"
            elif isinstance(zone, MultiHoleZone):
                label = f"Multi-Hole Zone {i+1}: outer={len(zone.outer_loop.primitives)}, {len(zone.inner_loops)} holes"
            else:
                label = f"Zone {i+1}"
            self.zone_list.Append(label)

        self.zone_list.Bind(wx.EVT_CHECKLISTBOX, self._on_zone_check)
        left_sizer.Add(self.zone_list, 1, wx.EXPAND | wx.ALL, 5)

        # Select all / none buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        select_all_btn = wx.Button(left_panel, label="Select All")
        select_none_btn = wx.Button(left_panel, label="Select None")
        select_all_btn.Bind(wx.EVT_BUTTON, self._on_select_all)
        select_none_btn.Bind(wx.EVT_BUTTON, self._on_select_none)
        btn_sizer.Add(select_all_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(select_none_btn, 0)
        left_sizer.Add(btn_sizer, 0, wx.ALL, 5)

        # Settings
        settings_box = wx.StaticBox(left_panel, label="Zone Settings")
        settings_sizer = wx.StaticBoxSizer(settings_box, wx.VERTICAL)

        grid = wx.FlexGridSizer(rows=5, cols=2, hgap=10, vgap=5)
        grid.AddGrowableCol(1, 1)

        # Layer
        grid.Add(wx.StaticText(left_panel, label="Layer:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.layer_choice = wx.Choice(left_panel)
        self._populate_layers()
        grid.Add(self.layer_choice, 0, wx.EXPAND)

        # Net
        grid.Add(wx.StaticText(left_panel, label="Net:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.net_choice = wx.Choice(left_panel)
        self._populate_nets()
        grid.Add(self.net_choice, 0, wx.EXPAND)

        # Priority
        grid.Add(wx.StaticText(left_panel, label="Priority:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.priority_spin = wx.SpinCtrl(left_panel, min=0, max=100, initial=0)
        grid.Add(self.priority_spin, 0, wx.EXPAND)

        # Clearance
        grid.Add(wx.StaticText(left_panel, label="Clearance (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.clearance_spin = wx.SpinCtrlDouble(left_panel, min=0.0, max=10.0, initial=0.2, inc=0.05)
        self.clearance_spin.SetDigits(2)
        grid.Add(self.clearance_spin, 0, wx.EXPAND)

        # Min thickness
        grid.Add(wx.StaticText(left_panel, label="Min Thickness (mm):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.thickness_spin = wx.SpinCtrlDouble(left_panel, min=0.0, max=10.0, initial=0.1, inc=0.05)
        self.thickness_spin.SetDigits(2)
        grid.Add(self.thickness_spin, 0, wx.EXPAND)

        settings_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 5)
        left_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Dialog buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(left_panel, wx.ID_OK, "Create Zones")
        cancel_btn = wx.Button(left_panel, wx.ID_CANCEL, "Cancel")
        button_sizer.Add(ok_btn, 0, wx.RIGHT, 5)
        button_sizer.Add(cancel_btn, 0)
        left_sizer.Add(button_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        left_panel.SetSizer(left_sizer)
        main_sizer.Add(left_panel, 1, wx.EXPAND)

        # Right side: preview
        preview_box = wx.StaticBox(self, label="Preview")
        preview_sizer = wx.StaticBoxSizer(preview_box, wx.VERTICAL)
        self.preview = ZonePreviewPanel(self, self.all_zones, self.arc_approximator)
        preview_sizer.Add(self.preview, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(preview_sizer, 1, wx.EXPAND | wx.ALL, 5)

        self.SetSizer(main_sizer)

    def _populate_layers(self):
        """Populate layer choice with all ZoneBuilder-supported layers."""
        def sort_key(layer: str):
            if layer == "F.Cu":
                return (0, 0, layer)
            if layer == "B.Cu":
                return (0, 10_000, layer)
            if layer.startswith("In") and layer.endswith(".Cu"):
                try:
                    num = int(layer[2:-3])
                    return (0, num, layer)
                except ValueError:
                    pass
            return (1, 0, layer)

        for layer in sorted(ZoneBuilderIPC.LAYER_MAP.keys(), key=sort_key):
            self.layer_choice.Append(layer)

        self.layer_choice.SetSelection(0)

    def _populate_nets(self):
        """Populate net choice using IPC API."""
        self.net_choice.Append("(none)")
        try:
            # Try to get nets from IPC board
            if hasattr(self.board, 'get_nets'):
                nets = self.board.get_nets()
                for net in nets:
                    net_name = net.name if hasattr(net, 'name') else str(net)
                    if net_name:
                        self.net_choice.Append(net_name)
            elif hasattr(self.board, 'nets'):
                for net in self.board.nets:
                    net_name = net.name if hasattr(net, 'name') else str(net)
                    if net_name:
                        self.net_choice.Append(net_name)
        except Exception as e:
            logger.error(f"Error populating nets: {e}")
        self.net_choice.SetSelection(0)

    def _on_zone_check(self, event):
        """Handle zone checkbox changes."""
        selected = set()
        for i in range(self.zone_list.GetCount()):
            if self.zone_list.IsChecked(i):
                selected.add(i)
        self.preview.set_selected(selected)

    def _on_select_all(self, event):
        """Select all zones."""
        for i in range(self.zone_list.GetCount()):
            self.zone_list.Check(i, True)
        self.preview.set_selected(set(range(len(self.all_zones))))

    def _on_select_none(self, event):
        """Deselect all zones."""
        for i in range(self.zone_list.GetCount()):
            self.zone_list.Check(i, False)
        self.preview.set_selected(set())

    def get_selected_zones(self) -> List[SimpleZone | RingZone | MultiHoleZone]:
        """Get list of selected zones."""
        selected = []
        for i in range(self.zone_list.GetCount()):
            if self.zone_list.IsChecked(i):
                selected.append(self.all_zones[i])
        return selected

    def get_settings(self) -> ZoneSettings:
        """Get zone settings from dialog."""
        layer = self.layer_choice.GetStringSelection()
        # Handle separator line - default to F.Cu
        if layer.startswith("─") or not layer:
            layer = "F.Cu"
        net_selection = self.net_choice.GetStringSelection()
        net_name = None if net_selection == "(none)" else net_selection

        return ZoneSettings(
            layer=layer,
            net_name=net_name,
            priority=self.priority_spin.GetValue(),
            clearance_mm=self.clearance_spin.GetValue(),
            min_thickness_mm=self.thickness_spin.GetValue()
        )
