from pathlib import Path
import runpy
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
community_version = runpy.run_path(str(root / 'core/constants.py'))['SOFTWARE_VERSION']
datas = [(str(root / 'resources'), 'resources'), (str(root / 'config'), 'config')]
datas += [(str(root / name), '.') for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')]
datas += [(str(root / 'LICENSES'), 'THIRD_PARTY_LICENSES/ScieFig-Models'),
          (str(root / 'docs/MODEL_LICENSE_REVIEW.md'), 'THIRD_PARTY_LICENSES/ScieFig-Models'),
          (str(root / 'docs/DEPENDENCY_SOURCES.json'), '.')]
for source in ('engine.py', 'preview.py'):
    # The existing engine binder checks these files before importing modules.
    datas.append((str(root / 'ui/local_style_extraction' / source), 'ui/local_style_extraction'))
binaries, hidden = [], ['czifile', 'nd2', 'openpyxl', 'tifffile', 'Vision',
                       'matplotlib.backends.backend_svg', 'matplotlib.backends.backend_pdf']
for package in ('nd2', 'openpyxl', 'pymupdf', 'fontTools'):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hidden += package_hidden
for package in ('pymupdf', 'fonttools'):
    datas += copy_metadata(package)
hidden += collect_submodules('core.image_analysis')
hidden += collect_submodules('ui.local_style_extraction')

a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=['pytest', 'tests', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ScieFig',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ScieFig')
app = BUNDLE(coll, name='ScieFig.app',
             icon=str(root / 'build/macos/ScieFig.icns'),
             bundle_identifier='com.sciefig.desktop', version=community_version,
             info_plist={
                 'CFBundleDisplayName': 'ScieFig',
                 'CFBundleShortVersionString': community_version,
                 'NSHighResolutionCapable': True,
                 'NSPrincipalClass': 'NSApplication',
                 'NSHumanReadableCopyright': 'Copyright (C) 2026 pamelah-233 and contributors',
                 'CFBundleDocumentTypes': [{
                     'CFBundleTypeName': 'ScieFig Project',
                     'CFBundleTypeRole': 'Editor',
                     'LSHandlerRank': 'Owner',
                     'CFBundleTypeExtensions': ['sfig'],
                     'LSItemContentTypes': ['com.sciefig.project'],
                 }],
                 'UTExportedTypeDeclarations': [{
                     'UTTypeIdentifier': 'com.sciefig.project',
                     'UTTypeDescription': 'ScieFig Project',
                     'UTTypeConformsTo': ['public.data'],
                     'UTTypeTagSpecification': {'public.filename-extension': ['sfig']},
                 }],
             })
