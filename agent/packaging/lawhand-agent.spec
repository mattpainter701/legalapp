# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the LawHand file share agent.

Builds the ``lawhand-agent`` supervisor binary. OpenSearch content extraction
requires a separately provisioned, reviewed Python worker runtime. Used by both packaging/windows/build.ps1
(which then wraps the exe in an MSI) and packaging/linux/build.sh.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

AGENT_ROOT = Path(SPECPATH).resolve().parent

hidden = [
    "clarity_agent.service",
    "clarity_agent.search_node",
    "clarity_agent.search_gateway",
    "clarity_agent.search_control",
    "clarity_agent.search_engine",
    "clarity_agent.opensearch_engine",
    "smbclient",
    "smbprotocol",
    "spnego",
    "spnego.auth",
    "pypdf",
    "docx",
    "aiosqlite",
    "tomli_w",
    "truststore",
]

# Supervisor imports must also work in a frozen installation. The parser itself
# runs in the separately configured, contained worker runtime.
hidden += collect_submodules("search_node")
search_node_data = collect_data_files("search_node")

if sys.platform == "win32":
    # pywin32 pieces the Windows service host needs at runtime.
    hidden += [
        "servicemanager",
        "win32serviceutil",
        "win32service",
        "win32event",
        "win32timezone",
        "win32api",
        "win32con",
        "win32file",
        "win32pipe",
        "win32process",
        "win32security",
        "win32ts",
        "ntsecuritycon",
        "pywintypes",
    ]

a = Analysis(
    [str(AGENT_ROOT / "clarity_agent" / "__main__.py")],
    pathex=[str(AGENT_ROOT)],
    binaries=[],
    datas=search_node_data,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="lawhand-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
