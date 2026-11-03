"""Everything PyInstaller must bundle from the installed PyWinRT (``winrt``) packages.

PyInstaller follows imports, which misses parts of PyWinRT:
- 3.x keeps every namespace as a native module in ``winrt/`` (``_winrt.pyd``,
  ``_winrt_windows_devices_bluetooth.pyd``, ...) next to ``msvcp140.dll``, which
  the modules load by directory, not through an import.
- 4.x keeps each namespace as a ``_table.pywinrt`` data file read at import time.

So the list is built from what the installed distributions recorded (their
RECORD files), not from the import graph. It needs no Windows to run, which is
what lets the tests check it against the real Windows wheels.
"""

from __future__ import annotations

from importlib.metadata import distributions

TABLE_NAME = "_table.pywinrt"
_EXTENSION_SUFFIXES = (".pyd", ".so")


def collect_pywinrt(path: list[str] | None = None):
    """Return (hiddenimports, binaries, datas) for every installed ``winrt`` file.

    ``binaries``/``datas`` are PyInstaller (source, destination-dir) tuples.
    """
    hidden: set[str] = set()
    binaries: list[tuple[str, str]] = []
    datas: list[tuple[str, str]] = []
    for dist in distributions(path=path) if path else distributions():
        for file in dist.files or []:
            parts = str(file).replace("\\", "/").split("/")
            if parts[0] != "winrt" or "__pyinstaller" in parts or "__pycache__" in parts:
                continue
            name, package = parts[-1], parts[:-1]
            source = str(dist.locate_file(file))
            if name.endswith(_EXTENSION_SUFFIXES):
                hidden.add(".".join(package + [name.split(".")[0]]))
            elif name == "__init__.py":
                hidden.add(".".join(package))
            elif name.endswith(".py"):
                hidden.add(".".join(package + [name[:-3]]))
            elif name.lower().endswith(".dll"):
                binaries.append((source, "/".join(package)))  # keep it beside the .pyd that loads it
            elif name == TABLE_NAME:
                datas.append((source, "/".join(package)))
    return sorted(hidden), sorted(set(binaries)), sorted(set(datas))


def check_complete(hiddenimports, datas, required) -> list[str]:
    """Names in ``required`` that would be missing from the bundle."""
    table_packages = {dest.replace("/", ".") for _, dest in datas}
    return [m for m in required if m not in hiddenimports and m not in table_packages]
