# services/tela.py
"""Le o TEXTO da janela ao lado -- sem OCR, sem capturar pixel.

O Windows publica o conteudo das janelas numa arvore de acessibilidade (UI
Automation), a mesma que os leitores de tela usam. Dali sai texto de verdade,
ja em caracteres: o corpo da pagina no navegador, os nomes dos arquivos no
Explorador, o titulo do documento no editor. Nada e lido da imagem da tela.

POR QUE NAO OCR: reconhecer letra em pixel exigiria uma dependencia de dezenas
de megabytes num aplicativo de 39 MB, custaria segundos por leitura e erraria
justamente onde a fonte e pequena. A arvore de acessibilidade entrega o texto
exato, de graca, em milissegundos.

POR QUE ctypes CRU E NAO comtypes/pywinauto: sao mais duas dependencias no
executavel, e `comtypes` gera codigo em tempo de execucao -- exatamente o tipo
de coisa que o PyInstaller quebra. Aqui sao meia duzia de chamadas COM, feitas
na mao pela tabela de metodos.

O CUSTO, MEDIDO NESTA MAQUINA:
  filtrando por tipo de elemento (Text/Edit/Document)   20 a 240 ms
  varrendo a arvore inteira                            900 a 2000 ms

Por isso o filtro por tipo: a varredura completa traz um pouco mais (o corpo
do editor de codigo, por exemplo), mas dois segundos e caro demais para algo
que roda enquanto a pessoa le. O titulo da janela ja carrega o nome do arquivo
aberto, que era o principal ganho daquele fundo de arvore.

PRIVACIDADE: isto le o conteudo de janelas de OUTROS programas. E informacao
sensivel, entao nasce DESLIGADO, tem interruptor proprio -- separado do resto
do motor de contexto -- e o texto lido nunca sai da maquina: vai so para o
modelo local, e nem chega a ser gravado em disco.
"""
import ctypes
import threading
import time

try:
    import ctypes.wintypes as wintypes
except Exception:                                    # pragma: no cover
    wintypes = None

# ------------------------------------------------------------------ CONSTANTES
_CLSID_CUIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_IUIAUTOMATION = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"

# Indices na tabela de metodos de cada interface COM. Os tres primeiros de
# qualquer interface sao sempre QueryInterface/AddRef/Release.
_IUIAUTOMATION_ELEMENT_FROM_HANDLE = 6
_IUIAUTOMATION_CREATE_PROPERTY_CONDITION = 23
_ELEMENT_FIND_ALL = 6
_ELEMENT_GET_CURRENT_NAME = 23
_ARRAY_GET_LENGTH = 3
_ARRAY_GET_ELEMENT = 4

_UIA_CONTROL_TYPE_PROPERTY = 30003
_TREESCOPE_DESCENDANTS = 0x04
_VT_I4 = 3

# Tipos que carregam texto para ler. Text pega paragrafo e rotulo, Edit pega
# campo (inclusive a barra de endereco), Document pega o corpo de uma pagina.
_TIPOS_COM_TEXTO = (50020, 50004, 50030)

# Tetos. Existem para o custo nao explodir numa janela gigante -- e porque
# depois de mil caracteres o modelo ja sabe do que a tela trata.
_MAX_ELEMENTOS = 40
_MAX_CHARS = 1400
_MIN_CHARS_TRECHO = 3

# Rotulos de moldura de janela. Aparecem em toda janela do Windows e nao dizem
# nada sobre o assunto; sem filtrar, metade da amostra viraria "Fechar".
_RUIDO = {
    "minimizar", "maximizar", "restaurar", "fechar", "sistema", "voltar",
    "avancar", "avançar", "recarregar", "mais", "menu", "pesquisar", "nome",
    "tipo", "tamanho", "data de modificacao", "data de modificação",
    "barra de status", "modos de exibicao", "modos de exibição", "detalhes",
    "painel de navegacao", "painel de navegação", "painel de detalhes",
    "nivel de zoom", "nível de zoom", "numero da pagina", "numero da página",
    "barra de enderecos", "barra de endereços",
    "barra de endereco e de pesquisa", "barra de endereço e de pesquisa",
}

_local = threading.local()


class _GUID(ctypes.Structure):
    _fields_ = [("D1", ctypes.c_ulong), ("D2", ctypes.c_ushort),
                ("D3", ctypes.c_ushort), ("D4", ctypes.c_ubyte * 8)]


class _VARIANT(ctypes.Structure):
    """VARIANT reduzido ao que usamos: um inteiro de 32 bits.

    O VARIANT real e uma uniao grande; declarar so o cabecalho e um campo do
    tamanho certo basta, porque nunca lemos de volta o que escrevemos aqui.
    """
    _fields_ = [("vt", ctypes.c_ushort), ("reservado1", ctypes.c_ushort),
                ("reservado2", ctypes.c_ushort), ("reservado3", ctypes.c_ushort),
                ("valor", ctypes.c_longlong), ("enchimento", ctypes.c_longlong)]


def _guid(texto):
    identificador = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(texto),
                                        ctypes.byref(identificador))
    return identificador


def _metodo(ponteiro, indice, assinatura):
    """Chama o metodo `indice` da tabela de metodos de um ponteiro COM."""
    tabela = ctypes.cast(ponteiro, ctypes.POINTER(ctypes.c_void_p))[0]
    endereco = ctypes.cast(tabela, ctypes.POINTER(ctypes.c_void_p))[indice]
    return ctypes.WINFUNCTYPE(*assinatura)(endereco)


def _automacao():
    """A instancia de UI Automation DESTA thread.

    COM e por thread: o objeto criado na thread da interface nao pode ser
    usado na thread de analise. Como toda leitura acontece em segundo plano,
    guardar por thread e o que mantem isso valido.
    """
    pronta = getattr(_local, "automacao", None)
    if pronta is not None:
        return pronta
    try:
        ole32 = ctypes.windll.ole32
        # APARTMENTTHREADED: e o modelo que a UI Automation espera.
        ole32.CoInitializeEx(None, 0x2)
        ponteiro = ctypes.c_void_p()
        resultado = ole32.CoCreateInstance(
            ctypes.byref(_guid(_CLSID_CUIAUTOMATION)), None, 1,
            ctypes.byref(_guid(_IID_IUIAUTOMATION)), ctypes.byref(ponteiro))
        if resultado != 0 or not ponteiro:
            _local.automacao = False
            return False
        _local.automacao = ponteiro
        return ponteiro
    except Exception:
        _local.automacao = False
        return False


def disponivel() -> bool:
    return bool(_automacao())


def _condicao_de_tipo(automacao, tipo):
    variante = _VARIANT()
    variante.vt = _VT_I4
    variante.valor = tipo
    condicao = ctypes.c_void_p()
    resultado = _metodo(
        automacao, _IUIAUTOMATION_CREATE_PROPERTY_CONDITION,
        (ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int, _VARIANT,
         ctypes.POINTER(ctypes.c_void_p)))(
        automacao, _UIA_CONTROL_TYPE_PROPERTY, variante, ctypes.byref(condicao))
    return condicao if resultado == 0 and condicao else None


def _nomes_do_tipo(automacao, alca, tipo, teto):
    elemento = ctypes.c_void_p()
    resultado = _metodo(
        automacao, _IUIAUTOMATION_ELEMENT_FROM_HANDLE,
        (ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND,
         ctypes.POINTER(ctypes.c_void_p)))(
        automacao, alca, ctypes.byref(elemento))
    if resultado != 0 or not elemento:
        return []

    condicao = _condicao_de_tipo(automacao, tipo)
    if condicao is None:
        return []

    achados = ctypes.c_void_p()
    resultado = _metodo(
        elemento, _ELEMENT_FIND_ALL,
        (ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
         ctypes.POINTER(ctypes.c_void_p)))(
        elemento, _TREESCOPE_DESCENDANTS, condicao, ctypes.byref(achados))
    if resultado != 0 or not achados:
        return []

    quantos = ctypes.c_int()
    _metodo(achados, _ARRAY_GET_LENGTH,
            (ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)))(
        achados, ctypes.byref(quantos))

    saida = []
    for indice in range(min(quantos.value, teto)):
        filho = ctypes.c_void_p()
        if _metodo(achados, _ARRAY_GET_ELEMENT,
                   (ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p)))(
                achados, indice, ctypes.byref(filho)) != 0 or not filho:
            continue
        nome = ctypes.c_wchar_p()
        if _metodo(filho, _ELEMENT_GET_CURRENT_NAME,
                   (ctypes.HRESULT, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_wchar_p)))(
                filho, ctypes.byref(nome)) == 0 and nome.value:
            saida.append(nome.value)
    return saida


def _limpar(trechos):
    """Tira moldura de janela, repeticao e espaco sobrando."""
    vistos = set()
    saida = []
    total = 0
    for bruto in trechos:
        texto = " ".join((bruto or "").split())
        if len(texto) < _MIN_CHARS_TRECHO:
            continue
        chave = texto.lower()
        if chave in _RUIDO or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(texto)
        total += len(texto)
        if total >= _MAX_CHARS:
            break
    return saida


def texto_da_janela(alca) -> str:
    """Texto legivel da janela, ja limpo e limitado. "" quando nao da.

    Nao levanta excecao: qualquer falha de COM vira string vazia, e quem
    chamou segue sem o sinal em vez de quebrar a leitura.
    """
    if not alca or wintypes is None:
        return ""
    automacao = _automacao()
    if not automacao:
        return ""

    trechos = []
    for tipo in _TIPOS_COM_TEXTO:
        try:
            trechos.extend(_nomes_do_tipo(automacao, alca, tipo, _MAX_ELEMENTOS))
        except Exception:
            continue
        if sum(len(t) for t in trechos) >= _MAX_CHARS:
            break
    return "\n".join(_limpar(trechos))[:_MAX_CHARS]


# ------------------------------------------------------------------- CACHE
# Ler a mesma janela a cada volta do monitor seria desperdicio: o conteudo de
# uma janela parada nao muda, e a leitura custa dezenas de milissegundos.
_VALIDADE_CACHE = 45.0
_cache = {}


def texto_em_cache(alca, assinatura) -> str:
    """Como `texto_da_janela`, mas so relê quando a janela muda de identidade."""
    agora = time.time()
    guardado = _cache.get(assinatura)
    if guardado and agora - guardado[0] < _VALIDADE_CACHE:
        return guardado[1]
    texto = texto_da_janela(alca)
    if len(_cache) > 12:
        _cache.clear()
    _cache[assinatura] = (agora, texto)
    return texto


def esquecer():
    _cache.clear()
