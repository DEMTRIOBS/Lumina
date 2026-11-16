# core/paths.py
"""Resolucao de caminhos que funciona tanto em modo script quanto empacotado (.exe).

Regra de ouro para o executavel:
  - Recursos read-only (logo, icone) vivem DENTRO do bundle -> resource_path()
  - Dados do usuario (banco, capas) vivem em %APPDATA% -> user_path()

Sem isso, o .exe grava dentro de uma pasta temporaria que o Windows apaga.
"""
import os
import sys

NOME_APP = "Lumina"
# Pastas de versoes anteriores: se existirem, a de dados e adotada no lugar da
# nova, para que renomear o aplicativo nao apague a biblioteca de ninguem.
NOMES_ANTERIORES = ["LeitorNativo"]


def esta_congelado() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(*partes) -> str:
    """Caminho de um recurso somente-leitura embutido no bundle."""
    if esta_congelado():
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *partes)


def _raiz_dados() -> str:
    if sys.platform == "win32":
        return os.environ.get("APPDATA") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _base_dados_usuario() -> str:
    raiz = _raiz_dados()
    atual = os.path.join(raiz, NOME_APP)
    if os.path.isdir(atual):
        return atual

    # Adota a pasta da versao anterior renomeando-a: o usuario mantem
    # biblioteca, progresso, grifos e notas depois da troca de nome.
    for antigo in NOMES_ANTERIORES:
        anterior = os.path.join(raiz, antigo)
        if os.path.isdir(anterior):
            try:
                os.rename(anterior, atual)
            except OSError:
                return anterior
            break
    return atual


def user_path(*partes) -> str:
    """Caminho gravavel e persistente para dados do usuario."""
    destino = os.path.join(_base_dados_usuario(), *partes)
    os.makedirs(os.path.dirname(destino) if os.path.splitext(destino)[1] else destino,
                exist_ok=True)
    return destino


def migrar_dados_locais(nomes_arquivos, nomes_pastas):
    """Move banco/capas da pasta do projeto para %APPDATA% na primeira execucao.

    Mantem quem ja usava a versao antiga sem perder biblioteca nem progresso.
    """
    if esta_congelado():
        origem_base = os.path.dirname(sys.executable)
    else:
        origem_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for nome in nomes_arquivos:
        origem = os.path.join(origem_base, nome)
        destino = user_path(nome)
        if os.path.exists(origem) and not os.path.exists(destino):
            try:
                if nome.endswith(".db"):
                    # backup() do SQLite le o banco junto com o -wal: copiar so
                    # o .db perdia o que ainda nao tinha sido consolidado.
                    import sqlite3
                    with sqlite3.connect(origem) as fonte, sqlite3.connect(destino) as alvo:
                        fonte.backup(alvo)
                else:
                    import shutil
                    shutil.copy2(origem, destino)
            except Exception:
                pass

    for nome in nomes_pastas:
        origem = os.path.join(origem_base, nome)
        destino = user_path(nome)
        if not os.path.isdir(origem):
            continue
        try:
            import shutil
            for arquivo in os.listdir(origem):
                alvo = os.path.join(destino, arquivo)
                if not os.path.exists(alvo):
                    shutil.copy2(os.path.join(origem, arquivo), alvo)
        except Exception:
            pass
