# -*- mode: python ; coding: utf-8 -*-
import glob
import os
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('tzdata')

# futu's generated *_pb2.py files bare-import each other (e.g. "import Common_pb2")
# via a sys.path.append(os.path.dirname(__file__)) trick in futu/common/pb/__init__.py.
# That only works if the sibling files are real files on disk, so force them to be
# extracted as data instead of bundled purely inside the PYZ archive.
import futu
futu_dir = os.path.dirname(futu.__file__)
pb_dir = os.path.join(futu_dir, 'common', 'pb')
for py_file in glob.glob(os.path.join(pb_dir, '*.py')):
    datas.append((py_file, 'futu/common/pb'))
# futu/__init__.py reads its own VERSION.txt at import time.
datas.append((os.path.join(futu_dir, 'VERSION.txt'), 'futu'))


a = Analysis(
    ['DailyTrade_app.py'],
    # ios16_ui_kit (shared iOS16-style Tkinter theme module) lives under the
    # sibling UIDesign folder, not on sys.path by default at build time.
    pathex=[os.path.join(os.path.dirname(os.path.abspath(SPECPATH)), 'UIDesign', 'ios16_ui_kit')],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Not used by this app; pandas' PyInstaller hook pulls these in transitively
    # (charting/Excel/image support) and they roughly triple the bundle size.
    excludes=['matplotlib', 'scipy', 'PIL', 'openpyxl', 'PyQt5', 'PySide2', 'PySide6', 'PyQt6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DailyTrade',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DailyTrade',
)
