"""Advanced Zone Helper - Create zones from selected shapes."""
import sys
import platform
import os
import re
import logging
from pathlib import Path

plugin_dir = Path(__file__).parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

PLUGIN_ID = "com.github.advanced-zone-helper-ipc"
MODULE_PACKAGE_MAP = {
    "kipy": "kicad-python",
    "wx": "wxPython",
}

LOG_FILE = plugin_dir / "zone_helper_ipc.log"


def configure_logging():
    """Configure file logging for runtime diagnostics."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler_exists = False
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if Path(handler.baseFilename) == LOG_FILE:
                    file_handler_exists = True
                    break
            except Exception:
                continue

    if not file_handler_exists:
        fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    has_stream = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    if not has_stream:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    logging.getLogger(__name__).info(
        "Logging configured",
    )


def _kicad_cache_root() -> Path:
    """Get KiCad cache root path (without version directory)."""
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "KiCad"
        return Path.home() / "AppData" / "Local" / "KiCad"
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "KiCad"
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
    if xdg_cache:
        return Path(xdg_cache) / "kicad"
    return Path.home() / ".cache" / "kicad"


def _version_key(version: str) -> tuple:
    """Sort key for KiCad version directory names."""
    parts = []
    for token in version.split("."):
        try:
            parts.append(int(token))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _list_kicad_versions(cache_root: Path) -> list[str]:
    """Return sorted KiCad version directory names (highest first)."""
    if not cache_root.exists():
        return []
    versions = []
    for child in cache_root.iterdir():
        if child.is_dir() and re.match(r"^\d+(?:\.\d+)*$", child.name):
            versions.append(child.name)
    return sorted(versions, key=_version_key, reverse=True)


def get_venv_path() -> Path:
    """Get best candidate path to this plugin's virtual environment."""
    cache_root = _kicad_cache_root()
    versions = _list_kicad_versions(cache_root)

    # Prefer versions where the plugin venv already exists.
    for version in versions:
        venv = cache_root / version / "python-environments" / PLUGIN_ID
        if venv.exists():
            return venv

    if versions:
        return cache_root / versions[0] / "python-environments" / PLUGIN_ID

    fallback_version = os.environ.get("KICAD_VERSION", "9.0")
    return cache_root / fallback_version / "python-environments" / PLUGIN_ID


def show_dependency_error(missing_module: str, import_error, wx_module=None):
    """Show helpful error message when dependencies are missing."""
    venv_path = get_venv_path()
    system = platform.system()
    package = MODULE_PACKAGE_MAP.get(missing_module, missing_module)
    script_path = plugin_dir / "setup_dependencies.py"

    if system == "Windows":
        pip_path = venv_path / "Scripts" / "pip.exe"
        activate_cmd = f'"{venv_path}\\Scripts\\activate.bat"'
    else:
        pip_path = venv_path / "bin" / "pip"
        activate_cmd = f'source "{venv_path}/bin/activate"'

    message = (
        f"Missing required module: {missing_module}\n"
        f"Import error: {import_error}\n\n"
        f"KiCad's automatic dependency installation may have failed.\n\n"
        f"Automatic repair command:\n"
        f'python "{script_path}" {missing_module}\n\n'
        f"Manual install fallback:\n"
        f'"{pip_path}" install {package}\n\n'
        f"Or activate the venv first:\n"
        f"{activate_cmd}\n"
        f"pip install {package}\n\n"
        f"Virtual environment location:\n{venv_path}\n\n"
        f"The setup script can repair kicad-python, wxPython, or other missing packages."
    )

    if wx_module is None:
        print(message)
        return

    app = wx_module.App() if not wx_module.GetApp() else None
    wx_module.MessageBox(message, "Advanced Zone Helper - Missing Dependencies", wx_module.OK | wx_module.ICON_ERROR)
    if app:
        app.Destroy()


def show_message(wx_module, title, message, icon=None):
    icon = icon if icon is not None else wx_module.ICON_INFORMATION
    app = wx_module.App() if not wx_module.GetApp() else None
    wx_module.MessageBox(message, title, wx_module.OK | icon)
    if app:
        app.Destroy()


def main():
    wx_module = None
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Advanced Zone Helper starting (create_zones.py)")
    logger.info(f"Plugin directory: {plugin_dir}")
    logger.info(f"Log file: {LOG_FILE}")

    try:
        try:
            import wx as wx_module
        except ImportError as e:
            logger.exception("Failed to import wx")
            show_dependency_error("wx", e, wx_module=None)
            return 1

        try:
            from kipy import KiCad
        except ImportError as e:
            logger.exception("Failed to import kipy")
            show_dependency_error("kipy", e, wx_module=wx_module)
            return 1

        from src.geometry.shape_extractor_ipc import ShapeExtractorIPC
        from src.geometry.loop_detector import LoopDetector
        from src.geometry.ring_finder import RingFinder
        from src.geometry.arc_approximator import ArcApproximator
        from src.geometry.zone_builder_ipc import ZoneBuilderIPC
        from src.config import DEFAULT_ARC_SEGMENTS

        kicad = KiCad()
        board = kicad.get_board()
        logger.info("Connected to KiCad board")

        # Extract shapes from selection
        extractor = ShapeExtractorIPC(board)
        primitives = extractor.extract_from_selection()
        logger.info(f"Extracted primitives: {len(primitives)}")

        if not primitives:
            show_message(
                wx_module,
                "Advanced Zone Helper",
                "No shapes found in selection.\n\n"
                "Please select graphic shapes (rectangles, circles, lines, arcs, beziers) and try again.",
                wx_module.ICON_WARNING
            )
            return 0

        # Detect closed loops
        detector = LoopDetector(primitives)
        loops = detector.detect_loops()
        logger.info(f"Detected loops: {len(loops)}")

        if not loops:
            show_message(
                wx_module,
                "Advanced Zone Helper",
                f"Found {len(primitives)} primitives but no closed loops.\n\n"
                "Make sure your shapes form closed boundaries.",
                wx_module.ICON_WARNING
            )
            return 0

        # Classify zones
        arc_approx = ArcApproximator(segments_per_360=DEFAULT_ARC_SEGMENTS)
        finder = RingFinder(loops, arc_approx)
        simple_zones, ring_zones, multi_hole_zones = finder.find_zones()
        logger.info(
            f"Detected zones: simple={len(simple_zones)}, ring={len(ring_zones)}, multi={len(multi_hole_zones)}"
        )

        total_zones = len(simple_zones) + len(ring_zones) + len(multi_hole_zones)
        if total_zones == 0:
            show_message(
                wx_module,
                "Advanced Zone Helper",
                "No zones detected from the loops.",
                wx_module.ICON_WARNING
            )
            return 0

        # Show zone selection dialog
        from src.ui.zone_dialog_ipc import ZoneDialogIPC

        # Ensure wx.App exists
        app = wx_module.App() if not wx_module.GetApp() else None

        dialog = ZoneDialogIPC(simple_zones, ring_zones, multi_hole_zones, arc_approx, board)
        result = dialog.ShowModal()

        if result == wx_module.ID_OK:
            selected_zones = dialog.get_selected_zones()
            settings = dialog.get_settings()

            if selected_zones:
                # Create zones
                builder = ZoneBuilderIPC(board, arc_approx)
                success_count = builder.create_zones(selected_zones, settings)
                logger.info(f"Zone creation: selected={len(selected_zones)}, success={success_count}")

                if success_count > 0:
                    show_message(wx_module, "Advanced Zone Helper", f"Successfully created {success_count} zone(s)!")
                else:
                    show_message(
                        wx_module,
                        "Advanced Zone Helper",
                        "Zone creation via IPC API did not succeed.\n"
                        "The kicad-python library may not yet support zone creation.",
                        wx_module.ICON_WARNING
                    )

        dialog.Destroy()
        if app:
            app.Destroy()
        return 0

    except Exception as e:
        logger.exception("Unhandled plugin exception")
        import traceback
        if wx_module is None:
            print(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        else:
            show_message(
                wx_module,
                "Advanced Zone Helper - Error",
                f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}",
                wx_module.ICON_ERROR
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
