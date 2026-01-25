#!/usr/bin/env python3
"""Build script to create a PCM (Plugin and Content Manager) package for KiCad.

Usage:
    python build_pcm.py <version>

Example:
    python build_pcm.py 2.0.0
    python build_pcm.py v2.1.0

Output: advanced-zone-helper-ipc-vX.X.X-pcm.zip in the dist/ folder.
"""

import json
import sys
import zipfile
from pathlib import Path

# Files to include in plugins/ subdirectory
PLUGIN_FILES = [
    "plugin.json",
    "create_zones.py",
    "setup_dependencies.py",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "src/",
    # Note: icons/ is handled separately - copied from resources/
]

# Patterns to exclude
EXCLUDE_PATTERNS = [
    "*.pyc",
    "__pycache__",
    "*.log",
    ".git",
    ".gitignore",
    "*.egg-info",
    ".pytest_cache",
    ".mypy_cache",
]


def should_exclude(path: Path) -> bool:
    """Check if a path should be excluded from the package."""
    name = path.name
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*") and name.endswith(pattern[1:]):
            return True
        if name == pattern:
            return True
    return False


def inject_version(content: str, version: str) -> str:
    """Replace ${VERSION} placeholder with actual version."""
    return content.replace("${VERSION}", version)


def add_file_to_zip(zipf: zipfile.ZipFile, source: Path, arcname: str, version: str = None):
    """Add a single file to the zip, optionally injecting version."""
    if should_exclude(source):
        return

    if version and source.suffix == ".json":
        # Inject version into JSON files
        content = source.read_text(encoding="utf-8")
        content = inject_version(content, version)
        zipf.writestr(arcname, content)
    else:
        zipf.write(source, arcname)
    print(f"  Added: {arcname}")


def add_directory_to_zip(zipf: zipfile.ZipFile, source: Path, arc_prefix: str):
    """Add a directory recursively to the zip."""
    for item in source.rglob("*"):
        if item.is_file() and not should_exclude(item):
            rel_path = item.relative_to(source.parent)
            arcname = f"{arc_prefix}/{rel_path}".replace("\\", "/")
            zipf.write(item, arcname)
            print(f"  Added: {arcname}")


def build_package(version: str):
    """Build the PCM package."""
    plugin_dir = Path(__file__).parent.resolve()

    # Clean version (remove 'v' prefix for internal use)
    version_clean = version.lstrip("v")
    version_display = f"v{version_clean}"

    # Create dist directory
    dist_dir = plugin_dir / "dist"
    dist_dir.mkdir(exist_ok=True)

    # Output filename
    output_name = f"advanced-zone-helper-ipc-{version_display}-pcm.zip"
    output_path = dist_dir / output_name

    # Remove existing file if present
    if output_path.exists():
        output_path.unlink()

    print(f"Building PCM package: {output_name}")
    print(f"Version: {version_clean}")
    print(f"Source: {plugin_dir}")
    print(f"Output: {output_path}")
    print()

    # Create zip file with PCM structure
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Add metadata.json at root (with version injected)
        metadata_path = plugin_dir / "metadata.json"
        if metadata_path.exists():
            content = metadata_path.read_text(encoding="utf-8")
            content = inject_version(content, version_clean)
            zipf.writestr("metadata.json", content)
            print("  Added: metadata.json")

        # Add resources/ at root (for PCM icon)
        resources_dir = plugin_dir / "resources"
        icon_path = resources_dir / "icon.png"
        if resources_dir.exists():
            for item in resources_dir.rglob("*"):
                if item.is_file() and not should_exclude(item):
                    arcname = f"resources/{item.relative_to(resources_dir)}".replace("\\", "/")
                    zipf.write(item, arcname)
                    print(f"  Added: {arcname}")

        # Also copy icon to plugins/icons/ for toolbar button
        if icon_path.exists():
            zipf.write(icon_path, "plugins/icons/icon.png")
            print("  Added: plugins/icons/icon.png (toolbar icon)")

        # Add plugin files under plugins/ directory
        for item in PLUGIN_FILES:
            source = plugin_dir / item
            if not source.exists():
                print(f"  WARNING: {item} not found, skipping")
                continue

            if source.is_file():
                arcname = f"plugins/{item}"
                add_file_to_zip(zipf, source, arcname, version_clean)
            elif source.is_dir():
                add_directory_to_zip(zipf, source, "plugins")

    # Show result
    size_kb = output_path.stat().st_size / 1024
    print()
    print(f"Package created: {output_path}")
    print(f"Size: {size_kb:.1f} KB")

    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python build_pcm.py <version>", file=sys.stderr)
        print("Example: python build_pcm.py 2.0.0", file=sys.stderr)
        return 1

    version = sys.argv[1]

    try:
        output_path = build_package(version)
        print()
        print("To install in KiCad:")
        print("  1. Open KiCad > Plugin and Content Manager")
        print("  2. Click 'Install from File...'")
        print(f"  3. Select: {output_path}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
