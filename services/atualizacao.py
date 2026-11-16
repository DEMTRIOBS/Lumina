# services/atualizacao.py
"""Atualizacao automatica a partir das Releases do GitHub.

Fluxo:
  1. `verificacao_automatica` (no maximo uma vez por dia) ou o botao
     "Procurar atualizacoes" consultam a API publica de Releases.
  2. Havendo versao mais nova, a interface oferece "Atualizar".
  3. `baixar_e_aplicar_em_segundo_plano` baixa o arquivo certo para a pasta
     temporaria e entrega a troca a um pequeno .cmd, que espera o Lumina
     fechar, troca o executavel (ou roda o instalador em modo silencioso) e
     abre o Lumina de novo.

Privacidade: a unica requisicao e um GET anonimo a api.github.com, com um
User-Agent generico. Nenhum identificador, versao do Windows ou dado do
usuario sai da maquina -- o GitHub exige um User-Agent, nao um nome.

Nada aqui toca a interface: os callbacks rodam na thread de trabalho e quem
chama e responsavel por levar o resultado para o Tk (`after(0, ...)`).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from configuracoes import APP_VERSAO

REPOSITORIO = "DEMTRIOBS/Lumina"
URL_API = f"https://api.github.com/repos/{REPOSITORIO}/releases/latest"
URL_RELEASES = f"https://github.com/{REPOSITORIO}/releases/latest"
USER_AGENT = "Lumina-atualizador"
TEMPO_LIMITE = 6

CHAVE_ATIVA = "verificar_atualizacoes"
CHAVE_ULTIMA = "ultima_verificacao_atualizacao"
# Um dia entre consultas automaticas: a API anonima do GitHub permite 60
# pedidos por hora por IP, e uma rede de escritorio inteira divide esse limite.
INTERVALO = 24 * 60 * 60

NOME_PORTATIL = "Lumina.exe"
PREFIXO_INSTALADOR = "LuminaSetup"


# ===================================================================== versao
def versao_tupla(texto) -> tuple:
    """'v2.3.1' -> (2, 3, 1). Sufixos como '-beta' sao ignorados.

    Comparar strings dava '2.10' < '2.9'; comparar tuplas de inteiros nao.
    """
    texto = str(texto or "").strip().lstrip("vV")
    numeros = []
    for parte in texto.split("."):
        achado = re.match(r"\d+", parte)
        if not achado:
            break
        numeros.append(int(achado.group()))
        if achado.end() != len(parte):
            break           # '3-beta': aproveita o 3 e para
    return tuple(numeros)


def eh_mais_nova(remota, local=APP_VERSAO) -> bool:
    a, b = versao_tupla(remota), versao_tupla(local)
    if not a:
        return False
    # '2.3' e '2.3.0' sao a mesma versao: completa com zeros antes de comparar.
    tamanho = max(len(a), len(b))
    a += (0,) * (tamanho - len(a))
    b += (0,) * (tamanho - len(b))
    return a > b


# ============================================================ uma vez por dia
def deve_verificar(cfg, agora=None) -> bool:
    if not cfg.obter_bool(CHAVE_ATIVA, True):
        return False
    agora = time.time() if agora is None else agora
    try:
        ultima = float(cfg.obter(CHAVE_ULTIMA, "") or 0)
    except (TypeError, ValueError):
        ultima = 0.0
    # Relogio que voltou no tempo (ultima no futuro) conta como "nunca":
    # senao um acerto de data travaria a verificacao por dias. A folga de um
    # minuto absorve o arredondamento do registro.
    return ultima <= 0 or ultima > agora + 60 or agora - ultima >= INTERVALO


def registrar_verificacao(cfg, agora=None):
    cfg.definir(CHAVE_ULTIMA, str(int(time.time() if agora is None else agora)))


# ========================================================== modo de execucao
def pasta_instalacao_padrao(localappdata=None) -> str:
    base = localappdata or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", "Lumina")


def modo_execucao(executavel=None, congelado=None, localappdata=None) -> str:
    """'fonte' (python main.py), 'instalado' (via LuminaSetup) ou 'portatil'.

    Instalado = dentro da pasta padrao do instalador OU com o desinstalador
    do Inno Setup ao lado (quem escolheu outra pasta na instalacao).
    """
    if congelado is None:
        congelado = bool(getattr(sys, "frozen", False))
    if not congelado:
        return "fonte"
    executavel = os.path.abspath(executavel or sys.executable)
    pasta = os.path.dirname(executavel)
    padrao = pasta_instalacao_padrao(localappdata)
    if os.path.normcase(os.path.normpath(pasta)) == os.path.normcase(os.path.normpath(padrao)):
        return "instalado"
    if os.path.exists(os.path.join(pasta, "unins000.exe")):
        return "instalado"
    return "portatil"


def escolher_asset(assets, modo):
    """Arquivo da release que serve para este modo, ou None.

    Instalado prefere o LuminaSetup (atualiza atalhos, desinstalador e
    associacoes); sem ele, trocar o Lumina.exe na pasta tambem funciona, pois
    a instalacao e por usuario e a pasta e gravavel.
    """
    if modo == "fonte":
        return None
    por_nome = {}
    for asset in assets or []:
        nome = str(asset.get("name", ""))
        if nome and asset.get("browser_download_url"):
            por_nome[nome.lower()] = asset
    if modo == "instalado":
        for nome, asset in sorted(por_nome.items()):
            if nome.startswith(PREFIXO_INSTALADOR.lower()) and nome.endswith(".exe"):
                return asset
    return por_nome.get(NOME_PORTATIL.lower())


# ================================================================== consulta
@dataclass
class Novidade:
    versao: str
    tag: str
    pagina: str = URL_RELEASES
    notas: str = ""
    modo: str = "fonte"
    asset: dict = field(default=None)

    @property
    def pode_instalar(self) -> bool:
        """Do codigo-fonte so avisamos: trocar arquivos do repositorio nao e
        papel do aplicativo."""
        return self.modo != "fonte" and self.asset is not None


def _pedido(url, aceitar="application/vnd.github+json"):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                "Accept": aceitar})


def consultar(timeout=TEMPO_LIMITE, abrir=None, local=APP_VERSAO, modo=None):
    """Novidade se a release mais recente for mais nova que `local`; senao None.

    Erros de rede sobem como excecao: quem chama decide se mostra ou cala.
    """
    abrir = abrir or urllib.request.urlopen
    with abrir(_pedido(URL_API), timeout=timeout) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))
    tag = str(dados.get("tag_name") or "")
    if dados.get("draft") or dados.get("prerelease") or not eh_mais_nova(tag, local):
        return None
    modo = modo or modo_execucao()
    return Novidade(versao=tag.lstrip("vV"), tag=tag,
                    pagina=dados.get("html_url") or URL_RELEASES,
                    notas=dados.get("body") or "", modo=modo,
                    asset=escolher_asset(dados.get("assets"), modo))


def verificar_em_segundo_plano(ao_terminar, timeout=TEMPO_LIMITE):
    """ao_terminar(novidade_ou_None, erro_ou_None), chamado na thread de trabalho."""
    def trabalhar():
        try:
            novidade = consultar(timeout=timeout)
        except Exception as erro:
            ao_terminar(None, erro)
            return
        ao_terminar(novidade, None)

    tarefa = threading.Thread(target=trabalhar, daemon=True, name="lumina-atualizacao")
    tarefa.start()
    return tarefa


def verificacao_automatica(cfg, ao_encontrar) -> bool:
    """Consulta silenciosa da abertura: so chama `ao_encontrar(novidade)` se
    houver versao nova. Retorna se a consulta foi disparada.

    A data e gravada AQUI, na thread da interface e antes da consulta: o
    banco tem uma conexao por thread, e gravar a tentativa (nao o sucesso)
    evita insistir a cada abertura quando a rede esta fora.
    """
    if not deve_verificar(cfg):
        return False
    registrar_verificacao(cfg)

    def terminou(novidade, erro):
        if erro is not None:
            print(f"[Atualizacao] Verificacao falhou: {erro}")
        elif novidade is not None:
            ao_encontrar(novidade)

    verificar_em_segundo_plano(terminou)
    return True


# ==================================================================== baixar
def baixar(asset, ao_progredir=None, pasta=None, abrir=None, timeout=30):
    """Baixa o asset para a pasta temporaria e devolve o caminho.

    Grava em .parcial e so renomeia no fim: um download interrompido nunca
    e confundido com um executavel valido.
    """
    abrir = abrir or urllib.request.urlopen
    pasta = pasta or os.path.join(tempfile.gettempdir(), "Lumina-atualizacao")
    os.makedirs(pasta, exist_ok=True)
    nome = os.path.basename(str(asset["name"]))
    destino = os.path.join(pasta, nome)
    parcial = destino + ".parcial"
    total = int(asset.get("size") or 0)
    resumo = hashlib.sha256()
    recebido = 0

    with abrir(_pedido(asset["browser_download_url"], "application/octet-stream"),
               timeout=timeout) as resposta, open(parcial, "wb") as saida:
        total = total or int(resposta.headers.get("Content-Length") or 0)
        while True:
            bloco = resposta.read(256 * 1024)
            if not bloco:
                break
            saida.write(bloco)
            resumo.update(bloco)
            recebido += len(bloco)
            if ao_progredir and total:
                ao_progredir(min(recebido / total, 1.0),
                             f"Baixando… {recebido / 1048576:.1f} de {total / 1048576:.1f} MB")

    try:
        if total and recebido != total:
            raise IOError(f"download incompleto ({recebido} de {total} bytes)")
        # A API passou a publicar o sha256 de cada arquivo ('digest'); quando
        # vem, conferimos -- e de graca e pega arquivo corrompido no caminho.
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:") and digest[7:].lower() != resumo.hexdigest():
            raise IOError("o arquivo baixado nao confere com o da release (sha256)")
        os.replace(parcial, destino)
    except Exception:
        try:
            os.remove(parcial)
        except OSError:
            pass
        raise
    return destino


# ================================================================ trocar/.cmd
def _cmd(caminho) -> str:
    """Caminho seguro para `set "X=..."` num .cmd: '%' viraria variavel."""
    return str(caminho).replace("%", "%%")


# Caminhos completos do System32: um PATH com o `find` do Git/MSYS na frente
# fazia o teste do PID sempre falhar, e a troca nao esperava o app fechar.
# `ping` no lugar de `timeout`: lancado sem console, o `timeout` falha na hora
# ("redirecionamento de entrada nao suportado") e o laco viraria espera ativa.
_SYS = r"%SystemRoot%\System32"
_PAUSA = f'"{_SYS}\\PING.EXE" -n 2 127.0.0.1 >nul\n'

_ESPERAR = (
    "set /a ESPERA=0\n"
    ":esperar\n"
    f'"{_SYS}\\tasklist.exe" /NH /FI "PID eq %PID%" 2>nul | "{_SYS}\\find.exe" " %PID% " >nul\n'
    "if errorlevel 1 goto livre\n"
    "set /a ESPERA+=1\n"
    "if %ESPERA% geq 120 goto livre\n"
    + _PAUSA +
    "goto esperar\n"
    ":livre\n"
)

# `(goto) 2>nul & del "%~f0"` apaga o proprio .cmd sem o erro de "arquivo
# em uso".
_FIM = """\
:fim
(goto) 2>nul & del "%~f0"
"""


def gerar_script_troca(exe_atual, exe_novo, pid) -> str:
    """Executavel portatil: espera fechar, troca o arquivo e reabre.

    A troca tenta por ate 30 s: no modo arquivo-unico do PyInstaller o
    processo-pai (bootloader) ainda segura o .exe por um instante depois que
    o nosso processo termina, limpando a pasta temporaria.
    Se nunca conseguir, reabre a versao antiga -- melhor que nenhuma.
    """
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal\r\n"
        f'set "PID={int(pid)}"\r\n'
        f'set "ALVO={_cmd(exe_atual)}"\r\n'
        f'set "NOVO={_cmd(exe_novo)}"\r\n'
        + _ESPERAR.replace("\n", "\r\n") +
        "set /a TENTATIVAS=0\r\n"
        ":trocar\r\n"
        'move /Y "%NOVO%" "%ALVO%" >nul 2>&1\r\n'
        "if not errorlevel 1 goto relancar\r\n"
        "set /a TENTATIVAS+=1\r\n"
        "if %TENTATIVAS% geq 30 goto relancar\r\n"
        + _PAUSA.replace("\n", "\r\n") +
        "goto trocar\r\n"
        ":relancar\r\n"
        'start "" "%ALVO%"\r\n'
        + _FIM.replace("\n", "\r\n")
    )


def gerar_script_instalador(setup, exe_relancar, pid) -> str:
    """Versao instalada: roda o LuminaSetup silencioso e reabre o Lumina.

    `start /wait` porque o cmd nao espera programas de janela; sem ele o
    Lumina reabriria antes de o instalador terminar de copiar.
    O instalador reaproveita a pasta e o modo (usuario/todos) da instalacao
    anterior, pelo AppId.
    """
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal\r\n"
        f'set "PID={int(pid)}"\r\n'
        f'set "SETUP={_cmd(setup)}"\r\n'
        f'set "ALVO={_cmd(exe_relancar)}"\r\n'
        + _ESPERAR.replace("\n", "\r\n") +
        'start "" /wait "%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-\r\n'
        'del "%SETUP%" >nul 2>&1\r\n'
        'start "" "%ALVO%"\r\n'
        + _FIM.replace("\n", "\r\n")
    )


def _pasta_gravavel(pasta) -> bool:
    # os.access mente no Windows (ignora ACLs); criar um arquivo e a prova real.
    try:
        with tempfile.NamedTemporaryFile(dir=pasta, prefix=".lumina-teste-"):
            return True
    except OSError:
        return False


def aplicar(baixado, modo, exe_atual=None, pid=None) -> str:
    """Grava e dispara o .cmd de troca. Depois disso o app PRECISA fechar.

    Retorna o caminho do .cmd. Levanta excecao se nao der para trocar.
    """
    if modo == "fonte" or not getattr(sys, "frozen", False):
        raise RuntimeError("rodando do codigo-fonte: atualize com git pull")
    exe_atual = os.path.abspath(exe_atual or sys.executable)
    pid = os.getpid() if pid is None else pid
    instalador = os.path.basename(baixado).lower().startswith(PREFIXO_INSTALADOR.lower())

    if instalador:
        texto = gerar_script_instalador(baixado, exe_atual, pid)
    else:
        if not _pasta_gravavel(os.path.dirname(exe_atual)):
            raise PermissionError(
                "sem permissao para gravar na pasta do Lumina; baixe a nova "
                "versao pela pagina de Releases")
        texto = gerar_script_troca(exe_atual, baixado, pid)

    script = os.path.join(os.path.dirname(baixado), "lumina-atualizar.cmd")
    with open(script, "w", encoding="utf-8", newline="") as arquivo:
        arquivo.write(texto)

    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    grupo = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", script], cwd=os.path.dirname(script),
                     creationflags=sem_janela | grupo, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return script


def baixar_e_aplicar_em_segundo_plano(novidade, ao_progredir, ao_terminar):
    """ao_progredir(fracao, texto); ao_terminar(ok, mensagem) -- na thread de
    trabalho. Com ok=True o chamador deve fechar o app em seguida."""
    def trabalhar():
        try:
            if not novidade.pode_instalar:
                raise RuntimeError("nenhum arquivo desta release serve para esta copia")
            caminho = baixar(novidade.asset, ao_progredir)
            ao_progredir(1.0, "Instalando… o Lumina vai reabrir sozinho.")
            aplicar(caminho, novidade.modo)
        except Exception as erro:
            ao_terminar(False, f"Nao foi possivel atualizar: {erro}")
            return
        ao_terminar(True, f"Lumina {novidade.versao} baixado. Reiniciando…")

    tarefa = threading.Thread(target=trabalhar, daemon=True, name="lumina-download")
    tarefa.start()
    return tarefa
