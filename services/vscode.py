# services/vscode.py
"""O arquivo aberto no VS Code ao lado do leitor.

Quem le um livro de programacao com o VS Code na outra metade da tela quer
perguntar "o que o meu codigo tem a ver com esta pagina?". O VS Code nao
publica o texto do editor na arvore de acessibilidade (o Monaco desenha num
canvas), entao `services/tela.py` nao alcanca o codigo. Mas o TITULO da
janela diz qual arquivo esta ativo e em qual projeto -- e o arquivo esta no
disco. Este modulo junta as duas pontas:

    titulo "● views.py - meu_projeto - Visual Studio Code"
        -> arquivo "views.py", raiz "meu_projeto"
        -> pasta do projeto (estado salvo do proprio VS Code)
        -> caminho completo do arquivo (estado do editor ou busca no disco)

ONDE O VS CODE GUARDA O QUE PRECISAMOS (investigado nesta maquina):
  %APPDATA%/Code/User/globalStorage/storage.json
      windowsState.lastActiveWindow / openedWindows -> URIs das pastas abertas
      backupWorkspaces.folders / profileAssociations.workspaces -> historico
  %APPDATA%/Code/User/workspaceStorage/<hash>/workspace.json
      {"folder": "file:///r%3A/..."} ou {"workspace": ".../x.code-workspace"}
  %APPDATA%/Code/User/workspaceStorage/<hash>/state.vscdb   (SQLite)
      chave 'memento/workbench.parts.editor' -> abas abertas, com fsPath
      chave 'history.entries'                -> arquivos recentes do projeto
  O state.vscdb e gravado pelo VS Code de tempos em tempos, nao a cada clique;
  por isso ele serve de PISTA (desempata nomes repetidos, acha arquivos fora
  da pasta) e a busca no disco continua sendo a rede de seguranca.

SOMENTE LEITURA: o estado do VS Code e aberto com `?mode=ro` e nunca gravado.
Corromper o estado do editor do usuario seria muito pior que nao achar o
arquivo.

PRIVACIDADE: nada aqui roda sozinho. A interface so chama `arquivo_ativo` e
`ler_trecho` quando a pessoa pede explicitamente (botao/atalho "usar o arquivo
do VS Code"). O conteudo lido nao e gravado em disco; para onde ele vai
(modelo local ou provedor na nuvem) e decisao de quem chama.

Projetos remotos (SSH, WSL, Dev Container, Codespaces, Tunnel) moram em outra
maquina: o titulo e reconhecido e marcado como `remoto`, mas o arquivo nao e
lido.
"""
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import unquote, urlparse

# ------------------------------------------------------------------ titulo
# Nomes de aplicativo que aparecem no fim do titulo. O mais longo primeiro:
# "Visual Studio Code - Insiders" contem o separador " - " no meio.
_APPS = [
    ("Visual Studio Code - Insiders", "Code - Insiders"),
    ("Visual Studio Code", "Code"),
    ("VSCodium", "VSCodium"),
    ("Cursor", "Cursor"),
    ("Windsurf", "Windsurf"),
]
_SEPARADOR = " - "

# Prefixos/sufixos que o VS Code acrescenta e que nao sao nome de nada.
_SUJO = ("● ", "\u25cf", "*")
_SUFIXOS_TITULO = re.compile(
    r"\s*\[(Administrator|Administrador|Unsupported|Sem suporte|"
    r"Restricted Mode|Modo Restrito|Extension Development Host)\]\s*$", re.I)
_REMOTO = re.compile(
    r"\s*\[(SSH|WSL|Dev Container|Container|Codespaces|Tunnel|Remote)[^\]]*\]", re.I)
_WORKSPACE = re.compile(r"\s*\((Workspace|Espaco de Trabalho|Espaço de Trabalho)\)\s*$", re.I)

# Abas que nao sao arquivo: nao adianta procurar no disco.
_NAO_ARQUIVO = re.compile(
    r"^(Untitled-\d+|Sem T[ií]tulo-\d+|Welcome|Bem-vindo|Settings|Configura[cç][oõ]es|"
    r"Keyboard Shortcuts|Extension: .*|Extens[aã]o: .*|Release Notes.*|"
    r"Get Started|Search|Pesquisar|Output|Sa[ií]da|Terminal)$", re.I)

# ------------------------------------------------------------------- busca
_PASTAS_IGNORADAS = {
    "node_modules", ".git", "venv", ".venv", "env", "__pycache__", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".tox", ".idea", ".next",
    "site-packages", ".gradle", "target", "bower_components",
}
TEMPO_BUSCA = 0.3          # segundos; a pessoa clicou e esta esperando
MAX_BYTES = 5 * 1024 * 1024  # acima disso nao e codigo-fonte de estudo

_LINGUAGENS = {
    ".py": "python", ".pyw": "python", ".ipynb": "jupyter",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".r": "r", ".lua": "lua", ".dart": "dart", ".pl": "perl",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".bat": "batch", ".cmd": "batch",
    ".sql": "sql", ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
    ".md": "markdown", ".rst": "restructuredtext", ".txt": "texto", ".ini": "ini",
    ".cfg": "ini", ".vue": "vue", ".svelte": "svelte", ".tex": "latex",
    ".hs": "haskell", ".ex": "elixir", ".exs": "elixir", ".clj": "clojure",
    ".m": "objective-c", ".f90": "fortran", ".asm": "assembly", ".s": "assembly",
}
_EXT_BINARIAS = {
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyd", ".o", ".obj", ".class", ".jar",
    ".zip", ".7z", ".rar", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".ico", ".webp", ".pdf", ".epub", ".db", ".sqlite", ".vscdb", ".mp3", ".mp4",
    ".wav", ".avi", ".mkv", ".woff", ".woff2", ".ttf", ".otf", ".bin", ".pkl",
}


@dataclass
class TituloVSCode:
    """O que da para tirar do titulo, sem tocar no disco."""
    arquivo: Optional[str]      # "views.py"; None se nao ha editor de arquivo ativo
    raiz: Optional[str]         # "meu_projeto" (sem o sufixo [SSH: ...])
    app: str                    # "Code", "Code - Insiders", "Cursor"...
    remoto: bool = False
    sujo: bool = False          # arquivo com alteracoes nao salvas
    workspace: bool = False     # raiz e um .code-workspace ("x (Workspace)")


@dataclass
class ArquivoAtivo:
    """Arquivo ativo no editor.

    `caminho` e None quando o projeto e remoto ou o arquivo nao foi achado no
    disco -- a interface ainda pode mostrar `nome`/`pasta_projeto` e explicar.
    """
    caminho: Optional[str]
    nome: str
    pasta_projeto: Optional[str]
    linguagem: str
    remoto: bool = False
    sujo: bool = False          # o disco pode estar atras do que esta na tela

    @property
    def legivel(self) -> bool:
        return bool(self.caminho) and not self.remoto


class ArquivoIlegivel(ValueError):
    """Arquivo que nao deve ser enviado como texto (binario, enorme, sumiu)."""


# ================================================================== titulo
def interpretar_titulo(titulo: str, raizes_conhecidas: Iterable[str] = ()) -> Optional[TituloVSCode]:
    """Decompoe o titulo da janela. None se nao for janela de um editor da familia.

    Formato padrao do `window.title`:
        ${dirty}${activeEditorShort} - ${rootName} - ${profileName} - ${appName}
    O perfil so aparece quando nao e o padrao. Nome de arquivo pode conter
    " - " ("notas - v2.md"), entao, quando conhecemos as raizes do VS Code,
    a divisao certa e a que cai numa raiz conhecida.
    """
    titulo = _SUFIXOS_TITULO.sub("", (titulo or "").strip())
    app = None
    for nome_app, curto in _APPS:
        if titulo == nome_app:
            return TituloVSCode(arquivo=None, raiz=None, app=curto)
        if titulo.endswith(_SEPARADOR + nome_app):
            titulo = titulo[: -len(_SEPARADOR + nome_app)]
            app = curto
            break
    if app is None:
        return None

    sujo = False
    for prefixo in _SUJO:
        if titulo.startswith(prefixo):
            titulo, sujo = titulo[len(prefixo):].lstrip(), True
            break

    partes = [p for p in titulo.split(_SEPARADOR)]
    conhecidas = {r.lower() for r in raizes_conhecidas if r}

    def _limpa_raiz(texto):
        remoto = bool(_REMOTO.search(texto))
        texto = _REMOTO.sub("", texto).strip()
        ws = bool(_WORKSPACE.search(texto))
        return _WORKSPACE.sub("", texto).strip(), remoto, ws

    indice = None
    if len(partes) >= 2 and conhecidas:
        for i in range(1, len(partes)):
            if _limpa_raiz(partes[i])[0].lower() in conhecidas:
                indice = i
                break

    if indice is None:
        if len(partes) == 1:
            # Uma parte so: ou e a pasta sem editor aberto, ou um arquivo solto
            # sem pasta. Nome com extensao (ou marca remota) decide.
            unico = partes[0].strip()
            if _REMOTO.search(unico) or _WORKSPACE.search(unico):
                raiz, remoto, ws = _limpa_raiz(unico)
                return TituloVSCode(None, raiz, app, remoto, sujo, ws)
            if unico.lower() in conhecidas or "." not in unico:
                return TituloVSCode(None, unico or None, app, False, sujo)
            return TituloVSCode(_arquivo_ou_none(unico), None, app, False, sujo)
        indice = 1

    arquivo = _SEPARADOR.join(partes[:indice]).strip()
    raiz, remoto, ws = _limpa_raiz(partes[indice])
    return TituloVSCode(_arquivo_ou_none(arquivo), raiz or None, app, remoto, sujo, ws)


def _arquivo_ou_none(nome):
    nome = (nome or "").strip()
    if not nome or _NAO_ARQUIVO.match(nome):
        return None
    return nome


# ============================================================ estado do VS Code
def _pastas_dados_usuario() -> List[Path]:
    """Pastas 'User' das variantes instaladas, VS Code estavel primeiro."""
    base = os.environ.get("APPDATA")
    if not base:
        return []
    nomes = ["Code", "Code - Insiders", "Cursor", "VSCodium", "Windsurf"]
    return [Path(base, n, "User") for n in nomes if Path(base, n, "User").is_dir()]


def _uri_para_caminho(uri: str) -> Optional[str]:
    """'file:///r%3A/x/y' -> 'r:\\x\\y'. URIs remotas devolvem None."""
    if not uri or not uri.startswith("file://"):
        return None
    analise = urlparse(uri)
    caminho = unquote(analise.path)
    if analise.netloc:                       # compartilhamento UNC: file://srv/c
        return "\\\\" + analise.netloc + caminho.replace("/", "\\")
    if re.match(r"^/[A-Za-z]:", caminho):
        caminho = caminho[1:]
    return os.path.normpath(caminho)


def _ler_json(caminho: Path):
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _ler_jsonc(caminho: Path):
    """.code-workspace aceita comentarios e virgula sobrando (JSONC)."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except OSError:
        return None
    # Tira // e /* */ fora de strings; simples mas suficiente para esse arquivo.
    texto = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/',
                   lambda m: m.group(1) or "", texto, flags=re.S)
    texto = re.sub(r",(\s*[}\]])", r"\1", texto)
    try:
        return json.loads(texto)
    except ValueError:
        return None


def _pastas_do_workspace(arquivo_ws: str) -> List[str]:
    dados = _ler_jsonc(Path(arquivo_ws)) or {}
    base = os.path.dirname(arquivo_ws)
    pastas = []
    for item in dados.get("folders", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("uri"):
            caminho = _uri_para_caminho(item["uri"])
        else:
            caminho = item.get("path")
            if caminho and not os.path.isabs(caminho):
                caminho = os.path.normpath(os.path.join(base, caminho))
        if caminho:
            pastas.append(caminho)
    return pastas


@dataclass
class _Projeto:
    raiz: str                       # nome exibido no titulo (basename / nome do .code-workspace)
    pastas: List[str]               # pastas locais do projeto
    estado: Optional[Path] = None   # state.vscdb deste projeto, se conhecido


def _projetos_conhecidos() -> List[_Projeto]:
    """Projetos que o VS Code conhece, do mais recente ao mais antigo.

    Ordem: janelas abertas agora, ultima janela ativa, depois o historico por
    data de uso do state.vscdb. Com duas pastas de mesmo nome, a mais recente
    ganha -- e quase sempre a que esta na tela.
    """
    vistos, projetos = set(), []

    def _add(uri_pasta=None, uri_ws=None, estado=None):
        if uri_pasta:
            caminho = _uri_para_caminho(uri_pasta)
            if not caminho or caminho.lower() in vistos:
                return
            vistos.add(caminho.lower())
            projetos.append(_Projeto(os.path.basename(caminho.rstrip("\\/")) or caminho,
                                     [caminho], estado))
        elif uri_ws:
            caminho = _uri_para_caminho(uri_ws)
            if not caminho or caminho.lower() in vistos:
                return
            vistos.add(caminho.lower())
            nome = os.path.splitext(os.path.basename(caminho))[0]
            projetos.append(_Projeto(nome, _pastas_do_workspace(caminho), estado))

    for usuario in _pastas_dados_usuario():
        storage = _ler_json(usuario / "globalStorage" / "storage.json") or {}
        janelas = storage.get("windowsState") or {}
        lista = list(janelas.get("openedWindows") or [])
        if janelas.get("lastActiveWindow"):
            lista.insert(0, janelas["lastActiveWindow"])
        for janela in lista:
            ws = janela.get("workspace") or {}
            _add(janela.get("folder"), ws.get("configPath") if isinstance(ws, dict) else None)

        # Historico: workspaceStorage, do mais usado recentemente ao mais antigo.
        pasta_ws = usuario / "workspaceStorage"
        entradas = []
        try:
            for item in os.scandir(pasta_ws):
                if not item.is_dir():
                    continue
                estado = Path(item.path, "state.vscdb")
                try:
                    quando = estado.stat().st_mtime
                except OSError:
                    quando = 0
                entradas.append((quando, Path(item.path)))
        except OSError:
            pass
        mapa_estado = {}
        for _, pasta in sorted(entradas, reverse=True):
            dados = _ler_json(pasta / "workspace.json") or {}
            uri = dados.get("folder") or dados.get("workspace")
            caminho = _uri_para_caminho(uri) if uri else None
            if caminho:
                mapa_estado.setdefault(caminho.lower(), pasta / "state.vscdb")
            _add(dados.get("folder"), dados.get("workspace"), pasta / "state.vscdb")

        for uri in (storage.get("profileAssociations") or {}).get("workspaces", {}) or {}:
            if uri.endswith(".code-workspace"):
                _add(None, uri)
            else:
                _add(uri)
        backups = storage.get("backupWorkspaces") or {}
        for item in backups.get("folders", []) or []:
            _add(item.get("folderUri"))

        # As janelas abertas foram adicionadas antes de conhecermos o estado.
        for projeto in projetos:
            if projeto.estado is None and projeto.pastas:
                projeto.estado = mapa_estado.get(projeto.pastas[0].lower())
    return projetos


# Varrer as ~200 pastas de workspaceStorage custa 100-200 ms (medido aqui);
# a pessoa pode clicar varias vezes seguidas, entao o indice vale 30 s.
_VALIDADE_CACHE = 30.0
_cache_projetos = {"quando": 0.0, "lista": None}


def _projetos_em_cache() -> List[_Projeto]:
    agora = time.monotonic()
    if _cache_projetos["lista"] is None or agora - _cache_projetos["quando"] > _VALIDADE_CACHE:
        _cache_projetos["lista"] = _projetos_conhecidos()
        _cache_projetos["quando"] = agora
    return _cache_projetos["lista"]


def limpar_cache():
    """Esquece o indice de projetos (testes, ou apos abrir outro projeto)."""
    _cache_projetos["lista"] = None


def _uris_no_estado(estado: Optional[Path]) -> List[str]:
    """Arquivos citados no estado do editor (abas abertas + historico)."""
    if not estado or not estado.is_file():
        return []
    try:
        conn = sqlite3.connect(estado.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)
    except sqlite3.Error:
        return []
    try:
        linhas = conn.execute(
            "SELECT value FROM ItemTable WHERE key IN "
            "('memento/workbench.parts.editor', 'history.entries')").fetchall()
    except sqlite3.Error:
        linhas = []
    finally:
        conn.close()
    uris = []
    for (valor,) in linhas:
        if isinstance(valor, bytes):
            valor = valor.decode("utf-8", "replace")
        # O valor e JSON com JSON dentro (aspas escapadas); a URI termina na
        # primeira aspa ou barra invertida, o que serve para os dois niveis.
        uris.extend(re.findall(r'file:///[^"\\\s]+', valor or ""))
    return uris


# ================================================================== busca
def _buscar_no_disco(pasta: str, nome: str, prazo: float) -> List[str]:
    """Busca em largura por arquivos com esse nome, ate estourar o prazo."""
    alvo = nome.lower()
    achados, fila = [], [pasta]
    while fila and time.monotonic() < prazo:
        atual = fila.pop(0)
        try:
            with os.scandir(atual) as itens:
                for item in itens:
                    try:
                        if item.is_dir(follow_symlinks=False):
                            if item.name.lower() not in _PASTAS_IGNORADAS:
                                fila.append(item.path)
                        elif item.name.lower() == alvo:
                            achados.append(item.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return achados


def _mais_recente(caminhos: Iterable[str]) -> Optional[str]:
    melhor, quando = None, -1.0
    for caminho in caminhos:
        try:
            m = os.path.getmtime(caminho)
        except OSError:
            continue
        if m > quando:
            melhor, quando = caminho, m
    return melhor


def _dentro(caminho: str, pasta: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(caminho), os.path.normcase(pasta)]) \
            == os.path.normcase(pasta)
    except ValueError:
        return False


def linguagem_de(nome: str) -> str:
    ext = os.path.splitext(nome or "")[1].lower()
    if not ext and (nome or "").lower() in ("makefile", "dockerfile"):
        return nome.lower()
    return _LINGUAGENS.get(ext, "texto")


# ==================================================================== API
def arquivo_ativo(titulo_janela: str, tempo_busca: float = TEMPO_BUSCA) -> Optional[ArquivoAtivo]:
    """Arquivo ativo a partir do titulo de uma janela do VS Code (ou Cursor...).

    Devolve None se o titulo nao e de um editor da familia ou se nao ha
    arquivo ativo (aba de boas-vindas, configuracoes, "Untitled-1").
    Caso contrario devolve ArquivoAtivo; `caminho` fica None quando o projeto
    e remoto ou o arquivo nao foi encontrado no disco (ver `legivel`).

    Custo: le storage.json + workspace.json (alguns ms) e, se o estado do
    editor nao resolver, busca no disco por no maximo `tempo_busca` segundos.
    """
    # Titulo que nem e do VS Code sai antes de qualquer acesso a disco.
    if interpretar_titulo(titulo_janela) is None:
        return None
    projetos = _projetos_em_cache()
    info = interpretar_titulo(titulo_janela, (p.raiz for p in projetos))
    if info and info.raiz and not info.remoto and \
            not any(p.raiz.lower() == info.raiz.lower() for p in projetos):
        # Projeto aberto depois do indice montado: vale uma releitura.
        limpar_cache()
        projetos = _projetos_em_cache()
        info = interpretar_titulo(titulo_janela, (p.raiz for p in projetos))
    if info is None or not info.arquivo:
        return None

    nome = info.arquivo
    linguagem = linguagem_de(nome)
    if info.remoto:
        return ArquivoAtivo(None, nome, None, linguagem, remoto=True, sujo=info.sujo)

    # Projetos com o nome da raiz, mais recente primeiro.
    alvo_raiz = (info.raiz or "").lower()
    candidatos = [p for p in projetos if alvo_raiz and p.raiz.lower() == alvo_raiz]
    pastas_locais = [pasta for p in candidatos for pasta in p.pastas if os.path.isdir(pasta)]
    pasta_projeto = pastas_locais[0] if pastas_locais else None

    # 1) Pista do estado do editor: caminho exato, inclusive fora da pasta.
    via_estado = []
    estados = [p.estado for p in candidatos] if candidatos else []
    for estado in estados:
        for uri in _uris_no_estado(estado):
            caminho = _uri_para_caminho(uri)
            if caminho and os.path.basename(caminho).lower() == nome.lower() \
                    and os.path.isfile(caminho):
                via_estado.append(caminho)
    if via_estado:
        caminho = _mais_recente(set(via_estado))
        pasta = next((p for p in pastas_locais if _dentro(caminho, p)), pasta_projeto)
        return ArquivoAtivo(caminho, nome, pasta, linguagem, sujo=info.sujo)

    # 2) Busca no disco, dividindo o prazo entre as pastas candidatas.
    prazo = time.monotonic() + max(0.0, tempo_busca)
    for pasta in pastas_locais:
        achados = _buscar_no_disco(pasta, nome, prazo)
        if achados:
            return ArquivoAtivo(_mais_recente(achados), nome, pasta, linguagem, sujo=info.sujo)
        if time.monotonic() >= prazo:
            break

    return ArquivoAtivo(None, nome, pasta_projeto, linguagem, sujo=info.sujo)


def _decodificar(bruto: bytes, cortado: bool) -> str:
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError as erro:
        # Leitura cortada no meio de um caractere multibyte nao e erro de
        # verdade: o arquivo e UTF-8, so faltou o resto do ultimo caractere.
        if cortado and erro.start >= len(bruto) - 3:
            try:
                return bruto[:erro.start].decode("utf-8-sig")
            except UnicodeDecodeError:
                pass
    try:
        return bruto.decode("cp1252")
    except UnicodeDecodeError:
        return bruto.decode("latin-1")


def ler_trecho(caminho: str, max_chars: int = 12000) -> str:
    """Texto do arquivo, cortado em `max_chars` (com aviso de corte).

    Levanta ArquivoIlegivel (subclasse de ValueError, mensagem pronta para a
    interface) para arquivo inexistente, binario ou maior que MAX_BYTES.
    Decodifica em UTF-8 e cai para cp1252/latin-1 -- codigo antigo de Windows
    ainda e salvo em ANSI.
    """
    if not caminho or not os.path.isfile(caminho):
        raise ArquivoIlegivel("Arquivo nao encontrado no disco.")
    if os.path.splitext(caminho)[1].lower() in _EXT_BINARIAS:
        raise ArquivoIlegivel("Arquivo binario: nao ha texto para ler.")
    try:
        tamanho = os.path.getsize(caminho)
    except OSError as erro:
        raise ArquivoIlegivel(f"Nao foi possivel ler o arquivo: {erro}") from erro
    if tamanho > MAX_BYTES:
        raise ArquivoIlegivel("Arquivo grande demais para enviar como contexto.")

    max_chars = max(1, int(max_chars))
    # 4 bytes por caractere cobre o pior caso de UTF-8; +1 detecta o corte.
    try:
        with open(caminho, "rb") as f:
            bruto = f.read(max_chars * 4 + 4)
    except OSError as erro:
        raise ArquivoIlegivel(f"Nao foi possivel ler o arquivo: {erro}") from erro
    if b"\x00" in bruto[:8192]:
        # UTF-16 tambem tem NUL, mas comeca com BOM; o resto e binario mesmo.
        if bruto[:2] in (b"\xff\xfe", b"\xfe\xff"):
            texto = bruto.decode("utf-16", "replace")
        else:
            raise ArquivoIlegivel("Arquivo binario: nao ha texto para ler.")
    else:
        texto = _decodificar(bruto, cortado=tamanho > len(bruto))

    texto = texto.replace("\r\n", "\n")
    truncado = tamanho > len(bruto) or len(texto) > max_chars
    texto = texto[:max_chars]
    if truncado:
        texto += "\n\n[...arquivo truncado...]"
    return texto
