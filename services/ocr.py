# services/ocr.py
"""OCR de paginas digitalizadas com o reconhecedor embutido no Windows.

POR QUE O OCR DO WINDOWS (Windows.Media.Ocr) E NAO O TESSERACT:
o Tesseract exigiria instalar um programa a parte e baixar dezenas de MB de
modelos por idioma. O Windows 10/11 ja traz um reconhecedor pronto, com os
idiomas que o usuario tem instalados, e a ponte para o Python sao pacotes
"winrt-*" de poucas centenas de KB. Se nada disso existir (Windows antigo,
pacote ausente, idioma sem OCR), `disponivel()` responde False e o leitor
continua mostrando a imagem da pagina como sempre fez.

O que sai daqui NAO sao paragrafos prontos: sao blocos no MESMO formato de
`page.get_text("dict")` (blocos -> linhas -> spans com tamanho de fonte). Assim
o motor de reflow trata o texto reconhecido com as mesmas regras do texto
nativo -- recuo de paragrafo, desfazer hifenizacao, titulo pelo tamanho,
numero de pagina no rodape descartado -- em vez de uma segunda copia dessas
regras que logo divergiria da primeira.
"""
import asyncio
import statistics
import threading
import time
from collections import OrderedDict

# Resolucao do reconhecimento. Abaixo de ~200 dpi o reconhecedor comeca a
# confundir acentos e pontuacao miuda; acima de 300 o ganho some e o tempo
# cresce com a area.
_DPI = 300
# Teto do lado maior da imagem: uma pagina A3 ou um poster a 300 dpi passaria
# de 5000 px, e o tempo de reconhecimento cresce com a area sem ganho real.
_LADO_MAX_PX = 4200
# Paginas reconhecidas guardadas em memoria. O reconhecimento custa centenas
# de ms; refazer a pagina so porque o leitor trocou a fonte seria desperdicio.
_CACHE_MAX = 64

_trava = threading.Lock()
_estado = {"verificado": False, "motor": None, "idiomas": [], "erro": ""}
_cache = OrderedDict()
# Tempo do ultimo reconhecimento (ms), para medir o custo por pagina.
ultimo_ms = 0.0


# ------------------------------------------------------------------ MOTOR

def _criar_motor():
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    _estado["idiomas"] = [l.language_tag for l in OcrEngine.available_recognizer_languages]
    # Primeiro o idioma do perfil do usuario: e o que ele le na maior parte
    # do tempo. Os fixos cobrem um perfil em idioma sem OCR instalado.
    motor = OcrEngine.try_create_from_user_profile_languages()
    if motor is None:
        for tag in ["pt-BR", "en-US"] + _estado["idiomas"]:
            try:
                motor = OcrEngine.try_create_from_language(Language(tag))
            except Exception:
                motor = None
            if motor is not None:
                break
    return motor


def disponivel() -> bool:
    """Se o OCR do Windows pode ser usado. Verifica uma vez so; nunca levanta."""
    if _estado["verificado"]:
        return _estado["motor"] is not None
    with _trava:
        if not _estado["verificado"]:
            try:
                _estado["motor"] = _criar_motor()
                if _estado["motor"] is None:
                    _estado["erro"] = "nenhum idioma com OCR instalado no Windows"
            except Exception as erro:          # pacote ausente, Windows antigo...
                _estado["motor"] = None
                _estado["erro"] = f"{type(erro).__name__}: {erro}"
            _estado["verificado"] = True
    return _estado["motor"] is not None


def idiomas() -> list:
    """Idiomas com OCR instalados no Windows (ex.: ['en-US', 'pt-BR'])."""
    disponivel()
    return list(_estado["idiomas"])


def idioma_em_uso() -> str:
    if not disponivel():
        return ""
    try:
        return _estado["motor"].recognizer_language.language_tag
    except Exception:
        return ""


def motivo_indisponivel() -> str:
    disponivel()
    return _estado["erro"]


def _aguardar(operacao):
    """Espera uma operacao assincrona do WinRT.

    Um laco de eventos proprio, e nao `asyncio.run`, porque o reflow roda em
    threads de trabalho; se ja houver um laco rodando nesta thread, a espera
    bloqueante do proprio WinRT resolve sem conflitar com ele.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        async def _esperar():
            return await operacao
        laco = asyncio.new_event_loop()
        try:
            return laco.run_until_complete(_esperar())
        finally:
            laco.close()
    return operacao.get()


# ------------------------------------------------------------ RECONHECIMENTO

def _reconhecer(pagina) -> list:
    """Linhas reconhecidas da pagina, em coordenadas da pagina (pontos).

    Cada linha: {"palavras": [(texto, (x0, y0, x1, y1)), ...]}.
    """
    global ultimo_ms
    import fitz
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    inicio = time.perf_counter()
    caixa = pagina.rect
    lado = max(caixa.width, caixa.height, 1.0)
    escala = min(_DPI / 72.0, _LADO_MAX_PX / lado)
    # Cinza de 8 bits: e o formato que o reconhecedor aceita direto, sem
    # conversao de cor, e ocupa um quarto da memoria do BGRA.
    pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala),
                            colorspace=fitz.csGRAY, alpha=False)
    largura, altura = pix.width, pix.height
    amostras = pix.samples
    if pix.stride != largura:
        # Linhas com preenchimento no fim: o SoftwareBitmap espera as linhas
        # coladas umas nas outras.
        amostras = b"".join(amostras[i * pix.stride:i * pix.stride + largura]
                            for i in range(altura))
    del pix

    escritor = DataWriter()
    escritor.write_bytes(amostras)
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        escritor.detach_buffer(), BitmapPixelFormat.GRAY8, largura, altura)
    # Um reconhecimento por vez: o motor e compartilhado e paralelizar paginas
    # so disputaria CPU com a interface.
    with _trava:
        resultado = _aguardar(_estado["motor"].recognize_async(bitmap))

    fator = 1.0 / escala
    x_base, y_base = caixa.x0, caixa.y0
    linhas = []
    for linha in resultado.lines:
        palavras = []
        for palavra in linha.words:
            texto = (palavra.text or "").strip()
            if not texto:
                continue
            r = palavra.bounding_rect
            palavras.append((texto, (x_base + r.x * fator, y_base + r.y * fator,
                                     x_base + (r.x + r.width) * fator,
                                     y_base + (r.y + r.height) * fator)))
        if palavras:
            linhas.append({"palavras": palavras})
    try:
        bitmap.close()
    except Exception:
        pass
    ultimo_ms = (time.perf_counter() - inicio) * 1000
    return linhas


def _linhas_em_cache(pagina) -> list:
    doc = pagina.parent
    chave = (id(doc), getattr(doc, "name", ""), len(doc), pagina.number)
    if chave in _cache:
        _cache.move_to_end(chave)
        return _cache[chave]
    linhas = _reconhecer(pagina)
    _cache[chave] = linhas
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return linhas


def limpar_cache():
    _cache.clear()


# ---------------------------------------------------------- LINHAS -> BLOCOS

# Proporcao da altura da caixa de uma palavra em relacao ao corpo da fonte. A
# caixa do reconhecedor envolve so a tinta: "uso" mede a altura do x, "Uma" a
# das maiusculas, "pagina" desce abaixo da linha. Sem compensar, uma linha
# curta sem hastes ("e o mar.") pareceria fonte miuda e viraria nota de rodape.
_ALTURA_X = 0.52
_HASTE = 0.21
_DESCENDENTE = 0.21
_COM_HASTE = set("bdfhkltij0123456789ÁÉÍÓÚÀÂÊÔÃÕÇáéíóúàâêôãõ!?/()[]{}|'\"%&#@$")
_COM_DESCENDENTE = set("gjpqyçÇ,;()[]{}|")


def _corpo_da_palavra(texto, altura):
    """Tamanho de fonte aproximado (na unidade da altura) de uma palavra."""
    haste = any(c.isupper() or c in _COM_HASTE for c in texto)
    desce = any(c in _COM_DESCENDENTE for c in texto)
    em = _ALTURA_X + (_HASTE if haste else 0.0) + (_DESCENDENTE if desce else 0.0)
    return altura / em


def _segmentos(linhas) -> list:
    """Quebra cada linha reconhecida onde houver um vao horizontal grande.

    O reconhecedor as vezes emenda numa linha so as duas colunas de uma
    pagina diagramada; o vao da medianiz entre as palavras denuncia o corte.
    """
    saida = []
    for linha in linhas:
        palavras = sorted(linha["palavras"], key=lambda p: p[1][0])
        corpos = [_corpo_da_palavra(t, c[3] - c[1]) for t, c in palavras if c[3] > c[1]]
        corpo = statistics.median(corpos) if corpos else 10.0
        atual = [palavras[0]]
        for anterior, palavra in zip(palavras, palavras[1:]):
            if palavra[1][0] - anterior[1][2] > corpo * 2.2:
                saida.append(_segmento(atual))
                atual = [palavra]
            else:
                atual.append(palavra)
        saida.append(_segmento(atual))
    # Linha sem nenhuma letra ou numero e sujeira do papel (manchas, grampos,
    # filetes) que o reconhecedor tentou ler.
    return [s for s in saida if any(c.isalnum() for c in s["texto"])]


def _segmento(palavras):
    caixas = [c for _t, c in palavras]
    corpos = [_corpo_da_palavra(t, c[3] - c[1]) for t, c in palavras if c[3] > c[1]]
    corpo = statistics.median(corpos) if corpos else 0.0
    # Caixa TIPOGRAFICA, nao a da tinta: a linha "ficava aberta ate tarde"
    # (sem descendentes) termina na linha de base, e a seguinte comeca no
    # topo das maiusculas. O vao entre elas parecia respiro de paragrafo e o
    # motor partia a frase no meio. Reconstruida a partir da linha de base,
    # a caixa fica igual a do texto nativo (ascendente + descendente).
    sem_descer = [c[3] for t, c in palavras
                  if not any(ch in _COM_DESCENDENTE for ch in t)]
    if sem_descer:
        base = statistics.median(sem_descer)
    else:
        base = max(c[3] for c in caixas) - corpo * _DESCENDENTE
    return {
        "texto": " ".join(t for t, _c in palavras),
        "bbox": (min(c[0] for c in caixas), base - corpo * 0.9,
                 max(c[2] for c in caixas), base + corpo * 0.22),
        "corpo": corpo,
    }


def _medianiz(segmentos, largura_pagina):
    """Posicao x da separacao entre duas colunas, ou None se nao houver.

    So conta como coluna quando ha linhas suficientes dos DOIS lados dividindo
    a mesma faixa vertical -- um titulo curto a esquerda e uma nota a direita
    nao fazem uma pagina diagramada.
    """
    estreitos = [s for s in segmentos
                 if s["bbox"][2] - s["bbox"][0] < largura_pagina * 0.55]
    if len(estreitos) < 6:
        return None
    faixas = 120
    passo = largura_pagina / faixas
    cobertura = [0] * faixas
    for s in estreitos:
        for i in range(max(0, int(s["bbox"][0] / passo)),
                       min(faixas, int(s["bbox"][2] / passo) + 1)):
            cobertura[i] += 1
    candidatos = [i for i in range(int(faixas * 0.3), int(faixas * 0.7))
                  if cobertura[i] == 0]
    if not candidatos:
        return None
    meio = faixas / 2
    x = (min(candidatos, key=lambda i: abs(i - meio)) + 0.5) * passo
    esquerda = [s for s in estreitos if s["bbox"][2] <= x]
    direita = [s for s in estreitos if s["bbox"][0] >= x]
    if len(esquerda) < 3 or len(direita) < 3:
        return None
    topo = max(min(s["bbox"][1] for s in esquerda), min(s["bbox"][1] for s in direita))
    base = min(max(s["bbox"][3] for s in esquerda), max(s["bbox"][3] for s in direita))
    return x if base > topo else None


def _em_ordem_de_leitura(segmentos, largura_pagina) -> list:
    """Grupos de segmentos (cada grupo de uma coluna so) na ordem de leitura.

    Numa pagina em duas colunas, o que atravessa a medianiz (titulo, figura
    larga com legenda) corta a pagina em faixas; dentro de cada faixa le-se a
    coluna da esquerda inteira e depois a da direita.
    """
    por_altura = sorted(segmentos, key=lambda s: (s["bbox"][1], s["bbox"][0]))
    x = _medianiz(segmentos, largura_pagina)
    if x is None:
        return [por_altura]
    grupos, esquerda, direita = [], [], []

    def fechar_faixa():
        if esquerda:
            grupos.append(list(esquerda))
        if direita:
            grupos.append(list(direita))
        esquerda.clear()
        direita.clear()

    for s in por_altura:
        if s["bbox"][0] < x < s["bbox"][2]:
            fechar_faixa()
            grupos.append([s])
        elif s["bbox"][2] <= x:
            esquerda.append(s)
        else:
            direita.append(s)
    fechar_faixa()
    return grupos


def _blocos_do_grupo(segmentos, alt_linha, corpo_pagina, corpo_alvo) -> list:
    """Corta uma coluna em blocos onde houver respiro vertical de paragrafo.

    O motor de reflow ainda separa paragrafos DENTRO de cada bloco (recuo,
    fim de frase); cortar aqui pelos vaos garante que o numero de pagina do
    rodape e um titulo afastado virem blocos proprios, como no texto nativo.
    """
    blocos, atual = [], []
    for s in segmentos:
        if atual:
            anterior = atual[-1]
            vao = s["bbox"][1] - anterior["bbox"][3]
            sem_sobrepor = (s["bbox"][0] > anterior["bbox"][2]
                            or s["bbox"][2] < anterior["bbox"][0])
            if vao > alt_linha * 1.0 or sem_sobrepor:
                blocos.append(atual)
                atual = []
        atual.append(s)
    if atual:
        blocos.append(atual)

    saida = []
    for grupo in blocos:
        linhas = []
        for s in grupo:
            razao = s["corpo"] / corpo_pagina if corpo_pagina else 1.0
            # A medida pela tinta oscila alguns por cento de linha para linha;
            # dentro da faixa de oscilacao, a linha e corpo de texto mesmo --
            # senao o motor quebraria o paragrafo a cada "mudanca de fonte".
            if 0.86 <= razao <= 1.18:
                razao = 1.0
            linhas.append({
                "bbox": s["bbox"],
                "spans": [{"text": s["texto"], "size": round(corpo_alvo * razao, 2),
                           "flags": 0, "font": "OCR"}],
            })
        caixas = [s["bbox"] for s in grupo]
        saida.append({
            "type": 0,
            "bbox": (min(c[0] for c in caixas), min(c[1] for c in caixas),
                     max(c[2] for c in caixas), max(c[3] for c in caixas)),
            "lines": linhas,
        })
    return saida


def blocos_da_pagina(pagina, corpo: float = 11.0) -> list:
    """Reconhece a pagina e devolve blocos no formato de get_text("dict").

    `corpo` e o tamanho do texto corrido do livro, ja medido pelo motor: o
    corpo estimado da pagina digitalizada e reescalado para ele, entao um
    titulo maior que o corpo continua maior e vira titulo no reflow.
    Levanta excecao se o OCR falhar; quem chama decide o que exibir.
    """
    if not disponivel():
        return []
    segmentos = _segmentos(_linhas_em_cache(pagina))
    if not segmentos:
        return []
    corpo_pagina = statistics.median(s["corpo"] for s in segmentos) or 1.0
    alt_linha = statistics.median(s["bbox"][3] - s["bbox"][1] for s in segmentos) or 1.0
    saida = []
    for grupo in _em_ordem_de_leitura(segmentos, pagina.rect.width or 1.0):
        saida.extend(_blocos_do_grupo(grupo, alt_linha, corpo_pagina, corpo))
    return saida


def parece_texto(texto: str) -> bool:
    """Se o reconhecido parece prosa e nao letras soltas lidas numa foto.

    Uma ilustracao ou um quadrinho produzem fragmentos curtos e muito
    simbolo; ali e melhor continuar mostrando a imagem original.
    """
    nao_brancos = [c for c in texto if not c.isspace()]
    if not nao_brancos:
        return False
    letras = sum(c.isalpha() for c in nao_brancos) / len(nao_brancos)
    palavras = texto.split()
    longas = sum(1 for p in palavras if sum(c.isalpha() for c in p) >= 3)
    return letras >= 0.6 and longas >= 4
