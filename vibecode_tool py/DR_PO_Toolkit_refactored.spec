# PyInstaller spec for the refactored GUI.
# Build from project root:
#   pyinstaller DR_PO_Toolkit_refactored.spec

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('dr_po_toolkit')
datas = [('rules', 'rules')]

try:
    datas.append(('examples/icon.ico', '.'))
    icon = 'examples/icon.ico'
except Exception:
    icon = None

block_cipher = None

a = Analysis(
    ['run_toolkit.py'],
    pathex=['.', 'src'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DR_PO_Toolkit_Refactored',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)
