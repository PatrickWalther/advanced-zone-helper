#!/usr/bin/env python3
"""Setup script to install dependencies for Advanced Zone Helper IPC plugin.

Run this script if KiCad's automatic dependency installation didn't work.
This will install the required packages into the plugin's virtual environment.
"""

import os
import sys
import subprocess
import platform
from pathlib import Path

PLUGIN_IDENTIFIER = "com.github.advanced-zone-helper-ipc"
REQUIREMENTS = ["kicad-python>=0.1.0"]  # wxPython is provided by KiCad on Windows/macOS


def get_kicad_cache_home():
    """Get KiCad cache home directory based on platform."""
    system = platform.system()

    if system == "Windows":
        # Windows: %LOCALAPPDATA%\KiCad\9.0
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "KiCad" / "9.0"
        return Path.home() / "AppData" / "Local" / "KiCad" / "9.0"

    elif system == "Darwin":
        # macOS: ~/Library/Caches/KiCad/9.0
        return Path.home() / "Library" / "Caches" / "KiCad" / "9.0"

    else:
        # Linux: ~/.cache/kicad/9.0 or $XDG_CACHE_HOME/kicad/9.0
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            return Path(xdg_cache) / "kicad" / "9.0"
        return Path.home() / ".cache" / "kicad" / "9.0"


def get_venv_path():
    """Get path to plugin's virtual environment."""
    cache_home = get_kicad_cache_home()
    return cache_home / "python-environments" / PLUGIN_IDENTIFIER


def get_pip_executable(venv_path):
    """Get pip executable path for the virtual environment."""
    system = platform.system()
    if system == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    else:
        return venv_path / "bin" / "pip"


def get_python_executable(venv_path):
    """Get Python executable path for the virtual environment."""
    system = platform.system()
    if system == "Windows":
        return venv_path / "Scripts" / "python.exe"
    else:
        return venv_path / "bin" / "python"


def create_venv_if_missing(venv_path):
    """Create virtual environment if it doesn't exist."""
    if not venv_path.exists():
        print(f"Creating virtual environment at: {venv_path}")
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        print("Virtual environment created.")
    else:
        print(f"Virtual environment exists at: {venv_path}")


def install_packages(venv_path, packages):
    """Install packages into the virtual environment."""
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


def verify_installation(venv_path):
    """Verify that required packages are installed correctly."""
    python_exe = get_python_executable(venv_path)

    print("\nVerifying installation...")

    try:
        result = subprocess.run(
            [str(python_exe), "-c", "import kipy; print(f'kipy version: {kipy.__version__}')"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            print("SUCCESS: kicad-python is installed correctly!")
            return True
        else:
            print(f"WARNING: Could not verify kipy installation: {result.stderr}")
            return False
    except Exception as e:
        print(f"WARNING: Verification failed: {e}")
        return False


def main():
    print("=" * 60)
    print("Advanced Zone Helper IPC - Dependency Setup")
    print("=" * 60)

    venv_path = get_venv_path()
    print(f"\nPlugin identifier: {PLUGIN_IDENTIFIER}")
    print(f"Virtual environment path: {venv_path}")

    # Check if venv exists, create if needed
    create_venv_if_missing(venv_path)

    # Install packages
    success = install_packages(venv_path, REQUIREMENTS)

    if success:
        verify_installation(venv_path)
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
