# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for printer_agent.
#
# Build a single .exe:
#   pip install pyinstaller
#   cd printer_agent
#   pyinstaller pyinstaller.spec
#
# Output: dist/printer_agent (single .exe)
#
# Copy dist/printer_agent + config.json to the restaurant's POS computer.
# Add a shortcut to the Windows Startup folder to launch on boot.

import sys
from pathlib import Path

# The agent directory
_agent_dir = Path(SPECPATH)  # SPECPATH = directory containing this .spec file

# printer.py lives in the parent repo directory
_printer_py = str(_agent_dir.parent / "printer.py")

a = Analysis(
    ['agent.py'],
    pathex=[str(_agent_dir.parent)],
    binaries=[],
    datas=[
        ('config.json', '.'),
        (_printer_py, '.'),
    ],
    hiddenimports=['requests'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='printer_agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
