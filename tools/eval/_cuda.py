"""Make the pip-installed NVIDIA CUDA runtime DLLs loadable on Windows.

CTranslate2 needs cuBLAS and cuDNN at runtime. The `nvidia-cublas-cu12` /
`nvidia-cudnn-cu12` wheels ship those DLLs inside site-packages but do not put them on
the DLL search path, so CUDA init fails with "cublas64_12.dll is not found" even though
the GPU is detected and the files are present.

Import this before faster_whisper to register those directories.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def enable() -> list[str]:
    """Register NVIDIA wheel DLL directories. Returns the paths added.

    Both mechanisms are needed. os.add_dll_directory only affects LoadLibraryEx calls
    made with LOAD_LIBRARY_SEARCH_* flags; CTranslate2's native extension resolves
    cuBLAS through the plain search order, which consults PATH. Registering only the
    DLL directory succeeds silently and still fails at encode time.
    """
    if sys.platform != "win32":
        return []
    added = []
    for site in sys.path:
        nvidia = Path(site) / "nvidia"
        if not nvidia.is_dir():
            continue
        for binder in sorted(nvidia.glob("*/bin")):
            try:
                os.add_dll_directory(str(binder))
            except OSError:
                pass
            added.append(str(binder))
        break
    if added:
        os.environ["PATH"] = os.pathsep.join(added) + os.pathsep + os.environ.get("PATH", "")
    return added


enable()
