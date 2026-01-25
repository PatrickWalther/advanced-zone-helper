"""Advanced Zone Helper - Create zones from selected shapes."""
import sys
import platform
import os
from pathlib import Path

plugin_dir = Path(__file__).parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))


def get_venv_path():
    """Get path to plugin's virtual environment for error messages."""
    plugin_id = "com.github.advanced-zone-helper-ipc"
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "KiCad" / "9.0" / "python-environments" / plugin_id
        return Path.home() / "AppData" / "Local" / "KiCad" / "9.0" / "python-environments" / plugin_id
    elif system == "Darwin":
        return Path.home() / "Library" / "Caches" / "KiCad" / "9.0" / "python-environments" / plugin_id
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            return Path(xdg_cache) / "kicad" / "9.0" / "python-environments" / plugin_id
        return Path.home() / ".cache" / "kicad" / "9.0" / "python-environments" / plugin_id


def show_dependency_error(missing_package, import_error):
    """Show helpful error message when dependencies are missing."""
    import wx
    venv_path = get_venv_path()
    system = platform.system()

    if system == "Windows":
        pip_path = venv_path / "Scripts" / "pip.exe"
        activate_cmd = f'"{venv_path}\\Scripts\\activate.bat"'
    else:
        pip_path = venv_path / "bin" / "pip"
        activate_cmd = f'source "{venv_path}/bin/activate"'

    message = (
        f"Missing required package: {missing_package}\n\n"
        f"KiCad's automatic dependency installation may have failed.\n\n"
        f"To fix this, open a terminal and run:\n\n"
        f'"{pip_path}" install kicad-python\n\n'
        f"Or activate the venv first:\n"
        f"{activate_cmd}\n"
        f"pip install kicad-python\n\n"
        f"Virtual environment location:\n{venv_path}\n\n"
        f"Alternatively, run the setup_dependencies.py script from the plugin folder."
    )

    app = wx.App() if not wx.GetApp() else None
    wx.MessageBox(message, "Advanced Zone Helper - Missing Dependencies", wx.OK | wx.ICON_ERROR)
    if app:
        app.Destroy()


# Check for required dependencies before importing
try:
    from kipy import KiCad
except ImportError as e:
    try:
        import wx
        show_dependency_error("kicad-python (kipy)", e)
    except ImportError:
        print(f"ERROR: Missing kicad-python package: {e}")
        print("Please run: pip install kicad-python")
    sys.exit(1)

import wx

from src.geometry.shape_extractor_ipc import ShapeExtractorIPC
from src.geometry.loop_detector import LoopDetector
from src.geometry.ring_finder import RingFinder
from src.geometry.arc_approximator import ArcApproximator
from src.geometry.zone_builder_ipc import ZoneBuilderIPC, ZoneSettings
from src.config import DEFAULT_ARC_SEGMENTS, DEFAULT_LAYER, DEFAULT_CLEARANCE, DEFAULT_MIN_THICKNESS


def show_message(title, message, icon=wx.ICON_INFORMATION):
    app = wx.App() if not wx.GetApp() else None
    wx.MessageBox(message, title, wx.OK | icon)
    if app:
        app.Destroy()


def main():
    try:
        kicad = KiCad()
        board = kicad.get_board()

        # Extract shapes from selection
        extractor = ShapeExtractorIPC(board)
        primitives = extractor.extract_from_selection()

        if not primitives:
            show_message(
                "Advanced Zone Helper",
                "No shapes found in selection.\n\n"
                "Please select graphic shapes (rectangles, circles, lines, arcs, beziers) and try again.",
                wx.ICON_WARNING
            )
            return

        # Detect closed loops
        detector = LoopDetector(primitives)
        loops = detector.detect_loops()

        if not loops:
            show_message(
                "Advanced Zone Helper",
                f"Found {len(primitives)} primitives but no closed loops.\n\n"
                "Make sure your shapes form closed boundaries.",
                wx.ICON_WARNING
            )
            return

        # Classify zones
        arc_approx = ArcApproximator(segments_per_360=DEFAULT_ARC_SEGMENTS)
        finder = RingFinder(loops, arc_approx)
        simple_zones, ring_zones, multi_hole_zones = finder.find_zones()

        total_zones = len(simple_zones) + len(ring_zones) + len(multi_hole_zones)
        if total_zones == 0:
            show_message(
                "Advanced Zone Helper",
                "No zones detected from the loops.",
                wx.ICON_WARNING
            )
            return

        # Show zone selection dialog
        from src.ui.zone_dialog_ipc import ZoneDialogIPC

        # Ensure wx.App exists
        app = wx.App() if not wx.GetApp() else None

        dialog = ZoneDialogIPC(simple_zones, ring_zones, multi_hole_zones, arc_approx, board)
        result = dialog.ShowModal()

        if result == wx.ID_OK:
            selected_zones = dialog.get_selected_zones()
            settings = dialog.get_settings()

            if selected_zones:
                # Create zones
                builder = ZoneBuilderIPC(board, arc_approx)
                success_count = builder.create_zones(selected_zones, settings)

                if success_count > 0:
                    show_message("Advanced Zone Helper", f"Successfully created {success_count} zone(s)!")
                else:
                    show_message(
                        "Advanced Zone Helper",
                        "Zone creation via IPC API did not succeed.\n"
                        "The kicad-python library may not yet support zone creation.",
                        wx.ICON_WARNING
                    )

        dialog.Destroy()
        if app:
            app.Destroy()

    except Exception as e:
        import traceback
        show_message(
            "Advanced Zone Helper - Error",
            f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}",
            wx.ICON_ERROR
        )


main()
