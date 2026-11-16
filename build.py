# build.py
"""Gera o executavel unico (Lumina.exe) a partir do codigo-fonte.

Uso:
    venv\\Scripts\\activate
    python build.py

O resultado fica em  dist/Lumina.exe  e roda em qualquer Windows 10/11
sem Python instalado. Os dados do usuario (biblioteca, grifos, notas) ficam em
%APPDATA%/Lumina, entao atualizar o .exe nunca apaga a estante.
"""
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
NOME = "Lumina"


def passo(texto):
    print(f"\n=== {texto} ===")


def preparar_icone():
    """Cria app.ico multi-resolucao a partir do logo vetorial."""
    passo("Gerando icone")
    destino = os.path.join(RAIZ, "app.ico")
    try:
        sys.path.insert(0, RAIZ)
        from core.branding import salvar_icone
        if not salvar_icone(destino):
            print("  logo nao encontrado; o executavel usara o icone padrao")
            return None
        print(f"  {destino}")
        return destino
    except Exception as erro:
        print(f"  falhou: {erro}")
        return None


def preparar_splash():
    """Imagem exibida pelo bootloader enquanto o executavel se descompacta.

    Sem ela, o modo arquivo-unico fica 2-4 s sem nenhum sinal de vida e o
    usuario clica duas vezes achando que travou.
    """
    passo("Gerando splash")
    destino = os.path.join(RAIZ, "splash.png")
    try:
        from PIL import Image, ImageDraw, ImageFont

        sys.path.insert(0, RAIZ)
        from core.branding import carregar_arte, carregar_logo

        arte = carregar_arte(452)
        if arte is not None:
            # A propria arte nova e o splash: so ganha a faixa "carregando".
            largura, altura = arte.width, arte.height + 34
            fundo = Image.new("RGBA", (largura, altura), "#0B1120")
            fundo.alpha_composite(arte, (0, 0))
            desenho = ImageDraw.Draw(fundo)
            try:
                fonte = ImageFont.truetype("segoeui.ttf", 12)
            except Exception:
                fonte = ImageFont.load_default()
            texto = "carregando…"
            caixa = desenho.textbbox((0, 0), texto, font=fonte)
            desenho.text(((largura - (caixa[2] - caixa[0])) // 2, arte.height + 8),
                         texto, font=fonte, fill="#8F9BB8")
            fundo.convert("RGB").save(destino)
            print(f"  {destino}")
            return destino

        largura, altura = 380, 300
        fundo = Image.new("RGBA", (largura, altura), "#0B1120")
        desenho = ImageDraw.Draw(fundo)
        desenho.rectangle([0, 0, largura - 1, altura - 1], outline="#26334A", width=1)

        logo = carregar_logo(128)
        if logo is not None:
            fundo.alpha_composite(logo, ((largura - 128) // 2, 46))

        for texto, tamanho, cor, y in (("Lumina", 22, "#E8EDF5", 196),
                                       ("carregando…", 12, "#8A9AB4", 234)):
            try:
                fonte = ImageFont.truetype("segoeui.ttf", tamanho)
            except Exception:
                fonte = ImageFont.load_default()
            caixa = desenho.textbbox((0, 0), texto, font=fonte)
            desenho.text(((largura - (caixa[2] - caixa[0])) // 2, y), texto,
                         font=fonte, fill=cor)

        fundo.convert("RGB").save(destino)
        print(f"  {destino}")
        return destino
    except Exception as erro:
        print(f"  falhou: {erro}")
        return None


def limpar():
    passo("Limpando builds anteriores")
    for pasta in ("build", "dist"):
        caminho = os.path.join(RAIZ, pasta)
        if os.path.isdir(caminho):
            shutil.rmtree(caminho, ignore_errors=True)
            print(f"  removido {pasta}/")


def compilar():
    passo("Compilando com PyInstaller (leva alguns minutos)")
    comando = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "leitor.spec"]
    resultado = subprocess.run(comando, cwd=RAIZ)
    return resultado.returncode == 0


def main():
    os.chdir(RAIZ)
    preparar_icone()
    tem_splash = preparar_splash()

    if not tem_splash:
        print("\nAviso: sem splash.png o leitor.spec falha. Abortando.")
        return 1

    limpar()
    if not compilar():
        print("\nBuild FALHOU. Leia a saida do PyInstaller acima.")
        return 1

    executavel = os.path.join(RAIZ, "dist", f"{NOME}.exe")
    if os.path.exists(executavel):
        tamanho = os.path.getsize(executavel) / (1024 * 1024)
        passo("Pronto")
        print(f"  {executavel}  ({tamanho:.1f} MB)")
        print("\n  Pode copiar esse arquivo sozinho para qualquer PC Windows.")
        return 0

    print("\nBuild terminou mas o .exe nao foi encontrado.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
