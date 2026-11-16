# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller para o Lumina.

Gere o executavel com:  python build.py
(o build.py prepara icone e splash antes de chamar o PyInstaller)
"""
import os

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH (definido pelo PyInstaller) e a pasta deste arquivo: o build
# funciona de qualquer diretorio, nao so de dentro do projeto.
RAIZ = os.path.abspath(globals().get("SPECPATH") or os.getcwd())

# O CustomTkinter carrega temas e fontes de arquivos em disco; sem eles o
# executavel abre e morre com FileNotFoundError no primeiro widget.
dados = collect_data_files("customtkinter")

for nome in ("minha_logo.svg", "minha_logo.png", "app.ico"):
    caminho = os.path.join(RAIZ, nome)
    if os.path.exists(caminho):
        dados.append((caminho, "."))

# Arte da marca (arte/lumina.png, arte/simbolo.png): splash, icone e "Sobre".
for nome in ("lumina.png", "simbolo.png"):
    caminho = os.path.join(RAIZ, "arte", nome)
    if os.path.exists(caminho):
        dados.append((caminho, "arte"))

a = Analysis(
    ["main.py"],
    pathex=[RAIZ],
    binaries=[],
    datas=dados,
    hiddenimports=[
        "PIL._tkinter_finder",     # necessario para ImageTk dentro do bundle
        "fitz",
        "requests",
        # OCR do Windows (services/ocr.py). Importados so dentro de funcoes,
        # entao ficam explicitos aqui. Os "_table.pywinrt" de cada namespace
        # (sem eles o import falha no exe) vem do hook que o proprio
        # winrt-runtime registra no PyInstaller -- nada a coletar a mao.
        "services.ocr",
        "winrt.runtime",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.globalization",
        "winrt.windows.graphics.imaging",
        "winrt.windows.media.ocr",
        "winrt.windows.storage.streams",
        # Arrastar e soltar (main.py importa dentro de try). A DLL e os .tcl
        # do tkdnd -- so a pasta da plataforma do build, ex. tkdnd/win-x64 --
        # vem do hook que o proprio tkinterdnd2 registra no PyInstaller
        # (entry point "pyinstaller40"); nada a coletar a mao.
        "tkinterdnd2",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Modulos pesados que o app nunca usa: cortam ~40 MB do executavel.
    excludes=[
        "numpy", "scipy", "pandas", "matplotlib", "PyQt5", "PyQt6",
        "PySide2", "PySide6", "IPython", "jupyter", "notebook",
        "pytest", "setuptools", "pip", "test", "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

splash = Splash(
    os.path.join(RAIZ, "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="Lumina",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # sem janela preta de console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(RAIZ, "app.ico"),
)
