"""Advanced Zone Helper IPC - Main entrypoint."""
import sys
import logging
from pathlib import Path

# Add plugin root to path for imports when run as script
_plugin_root = Path(__file__).parent.parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

# Configure logging
LOG_FILE = _plugin_root / "zone_helper_ipc.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='a'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)


def run():
    """IPC plugin entrypoint - called when user clicks the toolbar button."""
    logger.info("Advanced Zone Helper IPC starting...")

    try:
        # Import kicad-python
        try:
            from kipy import Board
        except ImportError as e:
            logger.error(f"Failed to import kipy: {e}")
            try:
                from kicad import Board
            except ImportError:
                logger.error("Failed to import from both 'kipy' and 'kicad' modules")
                raise

        # Connect to board
        board = Board.open(auto_connect=True)
        logger.info(f"Connected to board: {board.name if hasattr(board, 'name') else 'Unknown'}")

        # Import geometry modules
        from src.geometry.shape_extractor_ipc import ShapeExtractorIPC
        from src.geometry.loop_detector import LoopDetector
        from src.geometry.ring_finder import RingFinder
        from src.geometry.arc_approximator import ArcApproximator
        from src.geometry.zone_builder_ipc import ZoneBuilderIPC, ZoneSettings
        from src.config import DEFAULT_ARC_SEGMENTS, DEFAULT_LAYER, DEFAULT_CLEARANCE, DEFAULT_MIN_THICKNESS

        arc_approximator = ArcApproximator(segments_per_360=DEFAULT_ARC_SEGMENTS)

        # Phase 1: Extract shapes from selection
        extractor = ShapeExtractorIPC(board)
        primitives = extractor.extract_from_selection()
        logger.info(f"Extracted {len(primitives)} primitives")

        if not primitives:
            logger.warning("No primitives extracted - please select some shapes first")
            return

        # Phase 2: Detect loops
        detector = LoopDetector(primitives)
        loops = detector.detect_loops()
        logger.info(f"Detected {len(loops)} loops")

        if not loops:
            logger.warning("No closed loops detected from primitives")
            return

        # Phase 2b: Find zone types (simple, ring, multi-hole)
        finder = RingFinder(loops, arc_approximator)
        simple_zones, ring_zones, multi_hole_zones = finder.find_zones()
        logger.info(f"Found {len(simple_zones)} simple, {len(ring_zones)} ring, {len(multi_hole_zones)} multi-hole zones")

        total_zones = len(simple_zones) + len(ring_zones) + len(multi_hole_zones)
        if total_zones == 0:
            logger.warning("No zones detected from loops")
            return

        # Phase 3: Show dialog and create zones
        import wx
        from src.ui.zone_dialog_ipc import ZoneDialogIPC

        app = wx.App() if not wx.GetApp() else None

        dialog = ZoneDialogIPC(simple_zones, ring_zones, multi_hole_zones, arc_approximator, board)
        result = dialog.ShowModal()

        if result == wx.ID_OK:
            selected_zones = dialog.get_selected_zones()
            settings = dialog.get_settings()

            if selected_zones:
                builder = ZoneBuilderIPC(board, arc_approximator)
                success_count = builder.create_zones(selected_zones, settings)

                if success_count > 0:
                    logger.info(f"Successfully created {success_count}/{len(selected_zones)} zones")
                    wx.MessageBox(f"Created {success_count} zone(s)", "Advanced Zone Helper", wx.OK | wx.ICON_INFORMATION)
                else:
                    logger.warning("Zone creation returned 0 successes")
                    wx.MessageBox("Zone creation via IPC API did not succeed.",
                                  "Advanced Zone Helper", wx.OK | wx.ICON_WARNING)
        else:
            logger.info("Dialog cancelled by user")

        dialog.Destroy()
        if app:
            app.Destroy()

        logger.info("Plugin execution complete")

    except Exception as e:
        logger.exception("Plugin error")
        raise


if __name__ == "__main__":
    run()
