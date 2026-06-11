# PyInstaller spec for the Merc Wizard 2 sidecar.
#
# Build with: pyinstaller mercwizard_core.spec --clean
# Output: dist/mercwizard_core.exe (single-file build).
#
# Mode: --onefile. The EXE() block below embeds binaries + datas inline and
# sets runtime_tmpdir=None — both are --onefile signatures. The bootloader
# unpacks dependencies to %TEMP%\_MEIxxxxx\ on each launch. Rationale:
#   - Single file is easier for Tauri externalBin to bundle and ship
#   - The cost (~3-5s cold start unpack) is acceptable for this app
#   - kill_sidecar uses `taskkill /F /T` to walk the bootloader + child
#     Python interpreter pair so neither is orphaned on shutdown
#
# The Tauri shell expects the binary at `binaries/mercwizard_core-x86_64-pc-windows-msvc.exe`
# (per tauri.conf.json's externalBin entry). The build script copies
# dist/mercwizard_core.exe → shell/binaries/ with the platform-suffixed name.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('mercwizard_core/data', 'mercwizard_core/data'),
        ('mercwizard_core/presets', 'mercwizard_core/presets'),
        # Distilled generator corpus — without these the FROZEN build's
        # corpus.available() is False and the building stamp (the one
        # generator that needs the corpus by default) fails on every
        # run. Dev runs never hit this (the JSONs sit next to the .py).
        ('mercwizard_core/mapforge/corpus/generator_corpus.json',
         'mercwizard_core/mapforge/corpus'),
        ('mercwizard_core/mapforge/corpus/coverage.json',
         'mercwizard_core/mapforge/corpus'),
        ('ja2py', 'ja2py'),
    ],
    hiddenimports=[
        'lxml.etree', 'lxml._elementpath',
        'PIL._imagingtk', 'PIL._tkinter_finder',
        'fastapi', 'uvicorn', 'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'multipart',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # AI deps moved to companion app — strip from main sidecar
        'rembg', 'onnxruntime', 'pymatting', 'pooch',
        # Common bloat we don't use
        'matplotlib', 'tkinter', 'tk', 'tcl',
        'scipy.special', 'scipy.signal', 'scipy.sparse',
        'torch', 'tensorflow',
        'IPython', 'jupyter',
        # Pillow plugins for image formats we never decode (we only handle PNG
        # uploads + STI output). JPEG/WebP stay in case players bring those.
        'PIL.AvifImagePlugin', 'PIL._avif',     # 7.5 MB AVIF codec
        # uvicorn's hot-reload watcher — dev-only, never used in production
        'watchfiles', 'watchfiles._rust_notify',
        # lxml extras we never touch (we use lxml.etree only)
        'lxml.objectify',                       # 1.6 MB
        'lxml.html', 'lxml.html.clean', 'lxml.html.diff',
        'lxml.html._diffcommand', 'lxml.html._html5builder',
        'lxml.isoschematron',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='mercwizard_core',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=False so no console window flash appears when Tauri spawns the
    # sidecar. main.py defensively patches sys.stdout/sys.stderr to a logfile
    # at %APPDATA%/MercWizard/logs/sidecar.log if they're None (which they are
    # in windowed PyInstaller mode), so print()/logger calls still work.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

