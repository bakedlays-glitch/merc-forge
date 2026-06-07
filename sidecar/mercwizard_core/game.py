"""Launch JA2.exe from the wizard."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def launch_ja2(install_root: Path, exe_name: Optional[str] = None) -> int:
    """Spawn the JA2 executable for an install. Returns the PID.

    Doesn't wait for it to exit. The wizard remains responsive.

    Args:
        install_root: The folder containing the executable.
        exe_name: Specific executable name. If None, probes common names.
    """
    install_root = Path(install_root)
    if not install_root.is_dir():
        raise FileNotFoundError(f"Install root not found: {install_root}")

    if exe_name is not None:
        exe_path = install_root / exe_name
    else:
        from .install_detect import find_exe
        found = find_exe(install_root)
        if found is None:
            raise FileNotFoundError(f"No JA2 executable found in {install_root}")
        exe_path = found

    if not exe_path.is_file():
        raise FileNotFoundError(f"Executable not found: {exe_path}")

    proc = subprocess.Popen(
        [str(exe_path)],
        cwd=str(install_root),
        creationflags=subprocess.DETACHED_PROCESS if hasattr(subprocess, "DETACHED_PROCESS") else 0,
    )
    return proc.pid
