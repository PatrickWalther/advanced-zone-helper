#!/usr/bin/env python3
"""Setup script to install dependencies for Advanced Zone Helper IPC plugin.

Run this script if KiCad's automatic dependency installation didn't work.
This script can self-repair missing dependencies in the plugin's virtual environment.
"""

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_IDENTIFIER = "com.github.advanced-zone-helper-ipc"
REQUIREMENTS = ["kicad-python>=0.1.0"]
CORE_MODULES = ["kipy", "wx"]
MODULE_PACKAGE_MAP = {
    "kipy": "kicad-python",
    "wx": "wxPython",
}


def get_kicad_cache_root() -> Path:
    """Get KiCad cache root directory (without version)."""
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
    parts = []
    for token in version.split("."):
        try:
            parts.append(int(token))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def list_kicad_versions(cache_root: Path) -> list[str]:
    """List KiCad version directories sorted newest-first."""
    if not cache_root.exists():
        return []

    versions = []
    for child in cache_root.iterdir():
        if child.is_dir() and re.match(r"^\d+(?:\.\d+)*$", child.name):
            versions.append(child.name)
    return sorted(versions, key=_version_key, reverse=True)


def get_venv_path() -> Path:
    """Get best candidate plugin venv path across KiCad versions."""
    cache_root = get_kicad_cache_root()
    versions = list_kicad_versions(cache_root)

    # Prefer existing plugin venv directories.
    for version in versions:
        candidate = cache_root / version / "python-environments" / PLUGIN_IDENTIFIER
        if candidate.exists():
            return candidate

    if versions:
        return cache_root / versions[0] / "python-environments" / PLUGIN_IDENTIFIER

    fallback_version = os.environ.get("KICAD_VERSION", "9.0")
    return cache_root / fallback_version / "python-environments" / PLUGIN_IDENTIFIER


def get_pip_executable(venv_path: Path) -> Path:
    """Get pip executable path for the virtual environment."""
    system = platform.system()
    if system == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    return venv_path / "bin" / "pip"


def get_python_executable(venv_path: Path) -> Path:
    """Get Python executable path for the virtual environment."""
    system = platform.system()
    if system == "Windows":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def create_venv_if_missing(venv_path: Path):
    """Create virtual environment if it doesn't exist."""
    if not venv_path.exists():
        print(f"Creating virtual environment at: {venv_path}")
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        print("Virtual environment created.")
    else:
        print(f"Virtual environment exists at: {venv_path}")


def _module_to_package(module_name: str) -> str:
    return MODULE_PACKAGE_MAP.get(module_name, module_name)


def _parse_package_name(spec: str) -> str:
    name = re.split(r"[<>=!~\[\]]", spec, maxsplit=1)[0].strip()
    return name or spec.strip()


def _looks_like_module_name(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value))


def dedupe_package_specs(specs: list[str]) -> list[str]:
    """Deduplicate package specs by package name while preserving order."""
    selected: dict[str, str] = {}
    order: list[str] = []
    for spec in specs:
        spec = spec.strip()
        if not spec:
            continue
        key = _parse_package_name(spec).lower()
        existing = selected.get(key)
        if existing is None:
            selected[key] = spec
            order.append(key)
            continue
        # Prefer a version-constrained spec over a plain package name.
        existing_has_constraint = bool(re.search(r"[<>=!~]", existing))
        spec_has_constraint = bool(re.search(r"[<>=!~]", spec))
        if spec_has_constraint and not existing_has_constraint:
            selected[key] = spec
    return [selected[k] for k in order]


def detect_missing_modules(venv_path: Path, modules: list[str]) -> list[str]:
    """Return module names that fail import in the target venv."""
    if not modules:
        return []

    python_exe = get_python_executable(venv_path)
    if not python_exe.exists():
        return list(modules)

    check_code = (
        "import importlib.util, json, sys\n"
        "mods = sys.argv[1:]\n"
        "missing = [m for m in mods if importlib.util.find_spec(m) is None]\n"
        "print(json.dumps(missing))\n"
    )
    try:
        result = subprocess.run(
            [str(python_exe), "-c", check_code, *modules],
            capture_output=True,
            text=True,
            check=True,
        )
        import json
        return json.loads(result.stdout.strip() or "[]")
    except Exception:
        return list(modules)


def normalize_extra_specs(values: list[str]) -> list[str]:
    """Normalize CLI extras to pip-installable package specs."""
    specs: list[str] = []
    for value in values:
        if _looks_like_module_name(value):
            specs.append(_module_to_package(value))
        else:
            specs.append(value)
    return specs


def compute_install_packages(venv_path: Path, extras: list[str]) -> list[str]:
    """Build package install list from defaults, missing modules, and extras."""
    specs: list[str] = list(REQUIREMENTS)
    missing_core = detect_missing_modules(venv_path, CORE_MODULES)
    specs.extend(_module_to_package(module) for module in missing_core)
    specs.extend(normalize_extra_specs(extras))
    return dedupe_package_specs(specs)


def install_packages(venv_path: Path, packages: list[str]) -> bool:
    """Install packages into the virtual environment."""
    if not packages:
        print("\nNo packages to install.")
        return True

    pip_exe = get_pip_executable(venv_path)

    if not pip_exe.exists():
        print(f"ERROR: pip not found at {pip_exe}")
        print("The virtual environment may be corrupted. Try deleting it and running KiCad again.")
        return False

    print(f"\nInstalling packages: {', '.join(packages)}")
    print(f"Using pip: {pip_exe}\n")

    try:
        # Upgrade pip first
        subprocess.run([str(pip_exe), "install", "--upgrade", "pip"], check=True)

        # Install packages
        result = subprocess.run(
            [str(pip_exe), "install"] + packages,
            check=True,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to install packages")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected installation failure: {e}")
        return False


def verify_installation(venv_path: Path, modules: list[str]) -> bool:
    """Verify that required modules import correctly."""
    python_exe = get_python_executable(venv_path)

    print("\nVerifying installation...")
    if not python_exe.exists():
        print(f"WARNING: Python executable not found at {python_exe}")
        return False

    try:
        import_list = ", ".join(modules)
        script = (
            "import importlib, sys\n"
            "mods = sys.argv[1:]\n"
            "missing = []\n"
            "for m in mods:\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "    except Exception:\n"
            "        missing.append(m)\n"
            "print(','.join(missing))\n"
        )
        result = subprocess.run(
            [str(python_exe), "-c", script, *modules],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            missing_csv = (result.stdout or "").strip()
            if missing_csv:
                print(f"WARNING: Missing imports after install: {missing_csv}")
                return False
            print(f"SUCCESS: Verified imports for: {import_list}")
            return True

        print(f"WARNING: Could not verify imports: {result.stderr}")
        return False
    except Exception as e:
        print(f"WARNING: Verification failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Repair Advanced Zone Helper plugin dependencies in KiCad's plugin venv."
    )
    parser.add_argument(
        "extras",
        nargs="*",
        help=(
            "Optional extra modules or package specs to install "
            "(examples: wx, numpy, requests==2.32.3)"
        ),
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Advanced Zone Helper IPC - Dependency Setup")
    print("=" * 60)

    venv_path = get_venv_path()
    version_dir = venv_path.parent.parent.name if len(venv_path.parents) >= 2 else "unknown"
    print(f"\nPlugin identifier: {PLUGIN_IDENTIFIER}")
    print(f"Detected KiCad version directory: {version_dir}")
    print(f"Virtual environment path: {venv_path}")

    # Check if venv exists, create if needed
    create_venv_if_missing(venv_path)

    packages = compute_install_packages(venv_path, args.extras)
    print(f"Repair package list: {', '.join(packages) if packages else '(none)'}")

    # Install packages
    success = install_packages(venv_path, packages)

    if success:
        verify_modules = list(dict.fromkeys(CORE_MODULES + [m for m in args.extras if _looks_like_module_name(m)]))
        verify_installation(venv_path, verify_modules)
        print("\n" + "=" * 60)
        print("Setup complete! Please restart KiCad and try the plugin again.")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("Setup failed. Please try the following:")
        print(f"1. Delete the folder: {venv_path}")
        print("2. Restart KiCad (it will recreate the venv)")
        print("3. Run this script again")
        print("=" * 60)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
