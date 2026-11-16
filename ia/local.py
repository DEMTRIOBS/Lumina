# ia/local.py
"""IA local automatica: acha, escolhe e, se preciso, instala sozinha.

O objetivo e que o assistente funcione sem chave de API, sem cadastro e sem o
usuario configurar nada. Isso acontece em tres degraus, do mais barato para o
mais caro, e o primeiro que responder ganha:

  1. SERVIDOR JA ABERTO NA MAQUINA  (0 byte, 0 clique)
     Quem usa Ollama, LM Studio ou Jan ja tem um modelo carregado. Basta
     bater nas portas conhecidas e usar o que estiver la.

  2. MOTOR PROPRIO DO LUMINA        (~397 MB, 1 clique, uma vez)
     Nao havendo nada, o proprio leitor baixa o `llama-server` de CPU
     (17,5 MB) e um modelo de 0,5B (379 MB) para %APPDATA%. Dali em diante
     roda offline, sem depender de mais nada.

  3. ASSISTENTE EXTRATIVO           (0 byte, ja embutido)
     Sem modelo nenhum, o modo offline por frequencia de termos continua
     respondendo. Nunca sobra tela vazia.

POR QUE UM PROCESSO SEPARADO, E NAO UMA BIBLIOTECA PYTHON:
`llama-cpp-python` traria binarios nativos para dentro do PyInstaller, que e
justamente onde empacotamento quebra. O `llama-server` e um executavel que fala
HTTP -- o mesmo padrao ja usado para a sintese de voz, que roda o PowerShell em
outro processo. O leitor so precisa de `requests`, que ele ja tem, e matar o
processo e o suficiente para liberar a memoria toda.

POR QUE O MENOR MODELO SEMPRE:
as tarefas daqui sao resumir, listar conceitos e classificar -- nenhuma delas
precisa de raciocinio longo. Um modelo de 0,5B responde em cerca de um segundo
em CPU e ocupa ~600 MB. Um de 3B daria respostas pouco melhores, levaria quase
um minuto e entraria em swap em qualquer maquina de 8 GB. Entre esperar meio
minuto e ler o proprio livro, a pessoa le o livro.
"""
import ctypes
import os
import re
import subprocess
import threading
import time
import zipfile

import requests

from core.paths import user_path

# ------------------------------------------------------------------ DESCOBERTA
# Portas padrao dos servidores locais mais comuns. A ordem nao importa: todos
# sao testados e o mais leve vence.
SERVIDORES_CONHECIDOS = (
    # (rotulo, url_base, dialeto)
    ("Ollama", "http://127.0.0.1:11434", "ollama"),
    ("LM Studio", "http://127.0.0.1:1234", "openai"),
    ("Jan", "http://127.0.0.1:1337", "openai"),
    ("llama.cpp", "http://127.0.0.1:8080", "openai"),
    ("LocalAI", "http://127.0.0.1:8081", "openai"),
)

# Teste de porta curto de proposito: isto roda antes de responder ao usuario e
# cinco portas mortas a um segundo cada seriam cinco segundos de espera.
TEMPO_SONDAGEM = 0.6

# ------------------------------------------------------------------- INSTALACAO
# Binario de CPU: 17,5 MB contra 239 MB da versao CUDA. Placa de video ajudaria
# na velocidade, mas o modelo e tao pequeno que a CPU da conta -- e o download
# de 240 MB extras nao se pagaria.
URL_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
PADRAO_BINARIO = "bin-win-cpu-x64"

# Qwen2.5 0.5B Instruct: multilingue de verdade (responde bem em portugues),
# treinado para seguir instrucao e com 379 MB no disco. E o menor modelo que
# ainda escreve um resumo apresentavel.
MODELOS = {
    "0.5b": {
        "nome": "Qwen2.5-0.5B-Instruct",
        "rotulo": "Leve — 0,5B (380 MB, ~0,6 GB de RAM)",
        "url": "https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF/"
               "resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        "arquivo": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        "mb": 380, "ram_mb": 620,
    },
    "1b": {
        "nome": "Llama-3.2-1B-Instruct",
        "rotulo": "Medio — 1B (770 MB, ~1,2 GB de RAM)",
        "url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/"
               "resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "arquivo": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "mb": 770, "ram_mb": 1230,
    },
    "1.5b": {
        "nome": "Qwen2.5-1.5B-Instruct",
        "rotulo": "Forte — 1,5B (940 MB, ~1,5 GB de RAM)",
        "url": "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/"
               "resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "arquivo": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "mb": 940, "ram_mb": 1505,
    },
}
_PADRAO = "0.5b"
# Mantido para quem ja importava o nome antigo.
MODELO_PADRAO = MODELOS[_PADRAO]

# Escolha viva do usuario. Guardada aqui e nao no banco porque `ia.local` nao
# conhece o banco; quem sabe da preferencia a injeta com `definir_modelo`.
_escolhido = _PADRAO


def definir_modelo(chave):
    global _escolhido
    if chave in MODELOS:
        _escolhido = chave


def modelo_atual():
    return MODELOS.get(_escolhido, MODELOS[_PADRAO])


def ram_livre_mb():
    """RAM disponivel agora, para avisar antes de baixar 940 MB a toa."""
    if os.name != "nt":
        return 0
    try:
        class _Estado(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        estado = _Estado()
        estado.dwLength = ctypes.sizeof(_Estado)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))
        return int(estado.ullAvailPhys / (1024 * 1024))
    except Exception:
        return 0

# Janela de contexto. Uma pagina de livro cabe folgada em 4k tokens, e cada
# duplicacao aqui custa memoria que a maquina do usuario pode nao ter.
CONTEXTO = 4096
# Desliga o servidor depois deste tempo sem uso: 600 MB parados na memoria de
# quem abriu o assistente uma vez e nunca mais.
OCIOSO_MAX = 600


def pasta_ia() -> str:
    caminho = user_path("ia")
    os.makedirs(caminho, exist_ok=True)
    return caminho


def caminho_modelo() -> str:
    return os.path.join(pasta_ia(), modelo_atual()["arquivo"])


def caminho_servidor() -> str:
    return os.path.join(pasta_ia(), "llama", "llama-server.exe")


def instalado() -> bool:
    return os.path.exists(caminho_modelo()) and os.path.exists(caminho_servidor())


# ===================================================================== SONDAGEM
def _modelos_ollama(url_base):
    resposta = requests.get(f"{url_base}/api/tags", timeout=TEMPO_SONDAGEM)
    resposta.raise_for_status()
    saida = []
    for item in resposta.json().get("models", []):
        saida.append({"id": item.get("name") or item.get("model") or "",
                      "bytes": int(item.get("size") or 0)})
    return [m for m in saida if m["id"]]


def _modelos_openai(url_base):
    resposta = requests.get(f"{url_base}/v1/models", timeout=TEMPO_SONDAGEM)
    resposta.raise_for_status()
    dados = resposta.json().get("data") or []
    return [{"id": item.get("id") or "", "bytes": 0} for item in dados if item.get("id")]


def sondar(url_base, dialeto):
    """Modelos disponiveis num servidor, ou None se ele nao estiver no ar."""
    try:
        return (_modelos_ollama(url_base) if dialeto == "ollama"
                else _modelos_openai(url_base))
    except Exception:
        return None


# Quanto o nome do modelo sugere de parametros. Serve so para ORDENAR, entao
# aproximacao basta -- o que importa e nunca escolher o de 70B quando ha um de
# 1B ao lado.
_RE_PARAMETROS = re.compile(r"(\d+(?:[.,]\d+)?)\s*([bm])\b", re.IGNORECASE)


def peso_do_modelo(modelo) -> float:
    """Tamanho estimado em bilhoes de parametros. Menor = preferido."""
    tamanho = modelo.get("bytes") or 0
    if tamanho:
        # Q4 gasta cerca de meio byte por parametro; a conta nao precisa ser
        # exata, so monotonica.
        return tamanho / 1e9 * 2.0

    achados = _RE_PARAMETROS.findall(modelo.get("id") or "")
    for valor, unidade in achados:
        try:
            numero = float(valor.replace(",", "."))
        except ValueError:
            continue
        bilhoes = numero / 1000.0 if unidade.lower() == "m" else numero
        # "Qwen2.5" e "llama3.2" tem numero de VERSAO, nao de tamanho.
        if 0.05 <= bilhoes <= 500:
            return bilhoes
    return 7.0          # desconhecido: assume medio e deixa passar na frente
                        # qualquer modelo que tenha se identificado


def escolher_modelo(modelos):
    """O mais leve da lista -- a 'IA mais fraca' que ainda resolve a tarefa."""
    if not modelos:
        return None
    # Modelo de embedding nao conversa; entra na lista do servidor e sairia
    # como o mais leve de todos.
    uteis = [m for m in modelos
             if not re.search(r"embed|rerank|whisper|clip|bge|e5-",
                              m.get("id", ""), re.IGNORECASE)]
    return min(uteis or modelos, key=peso_do_modelo)


def descobrir(incluir_proprio=True):
    """Primeiro servidor local utilizavel: (rotulo, url_base, dialeto, modelo).

    Devolve None quando nao ha nenhum. O motor proprio do Lumina entra por
    ultimo porque so vale acorda-lo se nada mais estiver de pe -- quem ja tem
    o LM Studio aberto nao precisa de um segundo servidor consumindo memoria.
    """
    for rotulo, url_base, dialeto in SERVIDORES_CONHECIDOS:
        modelos = sondar(url_base, dialeto)
        if not modelos:
            continue
        escolhido = escolher_modelo(modelos)
        if escolhido:
            return rotulo, url_base, dialeto, escolhido["id"]

    if incluir_proprio and instalado():
        servidor = ServidorProprio.instancia()
        url_base = servidor.garantir_no_ar()
        if url_base:
            return "IA local", url_base, "openai", modelo_atual()["nome"]
    return None


# ============================================================ MOTOR DO PROPRIO
class ServidorProprio:
    """O `llama-server` que o Lumina baixou e administra sozinho.

    Sobe na primeira pergunta, nao na abertura do programa: manter 600 MB
    ocupados por causa de um assistente que talvez nao seja usado hoje seria
    caro numa maquina de 8 GB. Pelo mesmo motivo ele se desliga sozinho depois
    de um tempo parado.
    """

    _instancia = None
    _trava_classe = threading.Lock()

    @classmethod
    def instancia(cls):
        with cls._trava_classe:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def __init__(self):
        self._trava = threading.RLock()
        # Serializa a PARTIDA inteira (checar -> Popen -> esperar /health).
        # Sem ela, o monitor de contexto e uma pergunta podiam subir dois
        # servidores ao mesmo tempo e o primeiro (~600 MB) ficava orfao.
        self._trava_partida = threading.Lock()
        self._processo = None
        self._porta = 0
        self._ultimo_uso = 0.0
        self._vigia = None

    # ------------------------------------------------------------- ciclo
    def _porta_livre(self):
        import socket
        with socket.socket() as sonda:
            sonda.bind(("127.0.0.1", 0))
            return sonda.getsockname()[1]

    @property
    def no_ar(self):
        with self._trava:
            return self._processo is not None and self._processo.poll() is None

    def garantir_no_ar(self, espera=40.0):
        """Sobe o servidor se preciso e devolve a URL, ou "" se nao subiu."""
        with self._trava_partida:
            return self._garantir_no_ar(espera)

    def _garantir_no_ar(self, espera):
        with self._trava:
            if self.no_ar:
                self._ultimo_uso = time.time()
                return f"http://127.0.0.1:{self._porta}"
        if not instalado():
            return ""

        try:
            porta = self._porta_livre()
            comando = [
                caminho_servidor(),
                "-m", caminho_modelo(),
                "--host", "127.0.0.1", "--port", str(porta),
                "-c", str(CONTEXTO),
                # Deixa um nucleo livre para a interface continuar desenhando
                # enquanto o modelo pensa.
                "-t", str(max(2, (os.cpu_count() or 4) - 1)),
                "--no-webui",
            ]
            processo = subprocess.Popen(
                comando, cwd=os.path.dirname(caminho_servidor()),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception as erro:
            print(f"[IA local] Nao consegui iniciar o servidor: {erro}")
            return ""

        with self._trava:
            self._processo = processo
            self._porta = porta
            self._ultimo_uso = time.time()

        url_base = f"http://127.0.0.1:{porta}"
        # Carregar o modelo leva alguns segundos; sem esperar pelo /health a
        # primeira pergunta bateria numa porta que ainda nao aceita conexao.
        limite = time.time() + espera
        while time.time() < limite:
            if processo.poll() is not None:
                print("[IA local] O servidor encerrou durante a carga.")
                return ""
            try:
                if requests.get(f"{url_base}/health", timeout=1.0).ok:
                    self._agendar_desligamento()
                    return url_base
            except Exception:
                pass
            # Fora do except: enquanto carrega o modelo o servidor responde
            # 503 sem excecao, e o laco disparava requisicoes sem pausa.
            time.sleep(0.4)
        self.parar()
        return ""

    def marcar_uso(self):
        with self._trava:
            self._ultimo_uso = time.time()

    def _agendar_desligamento(self):
        with self._trava:
            if self._vigia is not None:
                return
            self._vigia = threading.Thread(target=self._vigiar_ocioso, daemon=True)
            self._vigia.start()

    def _vigiar_ocioso(self):
        while True:
            time.sleep(30)
            with self._trava:
                if not self.no_ar:
                    self._vigia = None
                    return
                parado = time.time() - self._ultimo_uso
            if parado > OCIOSO_MAX:
                self.parar()
                with self._trava:
                    self._vigia = None
                return

    def parar(self):
        with self._trava:
            processo = self._processo
            self._processo = None
            self._porta = 0
        if processo is None:
            return
        try:
            if processo.poll() is None:
                processo.terminate()
                try:
                    processo.wait(timeout=4)
                except Exception:
                    processo.kill()
        except Exception:
            pass


# ================================================================= INSTALACAO
def _baixar(url, destino, ao_progredir=None, base=0.0, fatia=1.0):
    """Baixa com retomada: um download de 380 MB nao pode recomecar do zero.

    `base` e `fatia` situam este arquivo dentro do progresso total, para os
    dois downloads virarem uma barra so na tela.
    """
    parcial = destino + ".part"
    baixado = os.path.getsize(parcial) if os.path.exists(parcial) else 0
    cabecalhos = {"Range": f"bytes={baixado}-"} if baixado else {}

    resposta = requests.get(url, headers=cabecalhos, stream=True, timeout=60)
    if resposta.status_code == 416:          # ja estava completo
        os.replace(parcial, destino)
        return
    if baixado and resposta.status_code != 206:
        baixado = 0                          # servidor ignorou a retomada
        cabecalhos = {}
    resposta.raise_for_status()

    total = int(resposta.headers.get("content-length", 0)) + baixado
    modo = "ab" if baixado else "wb"
    with open(parcial, modo) as arquivo:
        for pedaco in resposta.iter_content(chunk_size=1 << 18):
            if not pedaco:
                continue
            arquivo.write(pedaco)
            baixado += len(pedaco)
            if ao_progredir and total:
                ao_progredir(base + fatia * baixado / total)
    os.replace(parcial, destino)


def _url_do_binario():
    dados = requests.get(URL_RELEASES, timeout=30).json()
    for recurso in dados.get("assets", []):
        if PADRAO_BINARIO in recurso.get("name", ""):
            return recurso["browser_download_url"]
    raise RuntimeError("nao achei o binario de CPU no ultimo lancamento")


def instalar(ao_progredir=None, ao_terminar=None):
    """Baixa motor e modelo em segundo plano.

    `ao_progredir(fracao, mensagem)` e `ao_terminar(ok, mensagem)` sao chamados
    de OUTRA thread -- quem desenha precisa reagendar com `after`.
    """
    def trabalhador():
        try:
            destino_zip = os.path.join(pasta_ia(), "llama.zip")
            pasta_llama = os.path.join(pasta_ia(), "llama")

            if not os.path.exists(caminho_servidor()):
                _aviso(ao_progredir, 0.0, "Baixando o motor (17 MB)…")
                _baixar(_url_do_binario(), destino_zip,
                        lambda f: _aviso(ao_progredir, f * 0.05,
                                         "Baixando o motor (17 MB)…"))
                _aviso(ao_progredir, 0.05, "Descompactando o motor…")
                os.makedirs(pasta_llama, exist_ok=True)
                with zipfile.ZipFile(destino_zip) as pacote:
                    pacote.extractall(pasta_llama)
                _achatar(pasta_llama)
                try:
                    os.remove(destino_zip)
                except OSError:
                    pass

            if not os.path.exists(caminho_modelo()):
                rotulo = f"Baixando o modelo ({modelo_atual()['mb']} MB)…"
                _aviso(ao_progredir, 0.06, rotulo)
                _baixar(modelo_atual()["url"], caminho_modelo(),
                        lambda f: _aviso(ao_progredir, 0.06 + f * 0.94, rotulo))

            if not instalado():
                raise RuntimeError("arquivos faltando depois do download")
            _aviso(ao_progredir, 1.0, "Pronto.")
            _fim(ao_terminar, True, "IA local instalada. Nada mais a configurar.")
        except Exception as erro:
            _fim(ao_terminar, False, f"Nao deu para instalar: {erro}")

    threading.Thread(target=trabalhador, daemon=True).start()


def _achatar(pasta):
    """O zip do llama.cpp as vezes traz tudo dentro de uma subpasta."""
    if os.path.exists(os.path.join(pasta, "llama-server.exe")):
        return
    for raiz, _pastas, arquivos in os.walk(pasta):
        if "llama-server.exe" not in arquivos:
            continue
        import shutil
        for nome in os.listdir(raiz):
            try:
                shutil.move(os.path.join(raiz, nome), os.path.join(pasta, nome))
            except Exception:
                pass
        return


def _aviso(retorno, fracao, mensagem):
    if retorno:
        try:
            retorno(max(0.0, min(1.0, fracao)), mensagem)
        except Exception:
            pass


def _fim(retorno, ok, mensagem):
    if retorno:
        try:
            retorno(ok, mensagem)
        except Exception:
            pass


def desinstalar():
    """Apaga motor e modelo -- ~400 MB de volta para o disco."""
    import shutil
    ServidorProprio.instancia().parar()
    for alvo in (caminho_modelo(), os.path.join(pasta_ia(), "llama")):
        try:
            if os.path.isdir(alvo):
                shutil.rmtree(alvo, ignore_errors=True)
            elif os.path.exists(alvo):
                os.remove(alvo)
        except Exception:
            pass


def encerrar():
    ServidorProprio.instancia().parar()
