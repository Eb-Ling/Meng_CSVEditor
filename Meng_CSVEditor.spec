# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, copy_metadata


a = Analysis(
    ['csv_editor.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['csv_model', 'csv_commands'],
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
    name='Meng_CSVEditor',
    icon='assets/app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# MCP stdio needs a console bootloader with real stdin/stdout handles.
# Keep it separate from the windowed GUI so clients can launch it over pipes.
mcp_analysis = Analysis(
    ['mcp_server.py'],
    pathex=[], binaries=[],
    datas=copy_metadata('mcp') + copy_metadata('mcp-types'),
    hiddenimports=collect_submodules('mcp', filter=lambda name: not name.startswith('mcp.cli'))
                  + ['anyio._backends._asyncio'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'mcp.cli', 'mcp.__main__'], noarchive=False, optimize=0,
)
mcp_pyz = PYZ(mcp_analysis.pure)
mcp_exe = EXE(
    mcp_pyz, mcp_analysis.scripts, mcp_analysis.binaries, mcp_analysis.datas, [],
    name='Meng_CSVEditor_MCP', icon='assets/app.ico',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
)
