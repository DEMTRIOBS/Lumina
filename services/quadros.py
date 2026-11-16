# services/quadros.py
"""Deteccao de quadros (paineis) em paginas de HQ e manga.

POR QUE NAO OPENCV/NUMPY: o aplicativo vira um .exe de 39 MB e numpy sozinho
passa de 15 MB, OpenCV de 60 MB. A deteccao aqui e feita com PIL pura, por
projecao de brancos -- que e exatamente o que separa quadro de quadro numa
HQ: a calha (gutter) e uma faixa clara continua atravessando a pagina.

O metodo e um corte recursivo: procura uma calha horizontal, corta, procura
uma calha vertical em cada pedaco, corta de novo, e assim por diante. E o
mesmo raciocinio de quem le a pagina -- primeiro as tiras, depois os quadros
dentro de cada tira.
"""
import fitz
from PIL import Image

# Largura de trabalho. Precisao de quadro nao precisa de resolucao cheia, e
# reduzir antes de projetar e o que mantem a deteccao abaixo de meio segundo.
_LARGURA_ANALISE = 400

# Um pixel e "calha" acima deste brilho (0-255).
_LIMIAR_CLARO = 235
# Fracao da linha/coluna que precisa estar clara para valer como calha.
_FRACAO_CALHA = 0.97
# Calha mais fina que isso e entrelinha de balao, nao separacao de quadro.
_ESPESSURA_MIN = 6
# Quadro menor que esta fatia da pagina e recorte de arte, nao painel.
_AREA_MIN = 0.03
# Profundidade do corte recursivo: tira -> quadro -> subquadro basta.
_PROFUNDIDADE_MAX = 4


# Fracao minima de pixels nao-brancos para o retangulo ser arte, e nao texto.
# Uma pagina de livro fica perto de 5-10%; um quadro desenhado passa de 25%.
_TINTA_MIN = 0.22
# Acima disto a pagina tem texto demais para ser HQ.
_MAX_TEXTO_HQ = 400
# Os quadros juntos precisam ocupar boa parte da pagina.
_COBERTURA_MIN = 0.45


def _cobertura_de_tinta(imagem, caixa):
    """Fracao de pixels escuros dentro do retangulo."""
    recorte = imagem.crop(caixa)
    total = recorte.width * recorte.height
    if not total:
        return 0.0
    histograma = recorte.histogram()
    escuros = sum(histograma[:_LIMIAR_CLARO])
    return escuros / total


def _pagina_em_cinza(documento, numero, escala):
    pagina = documento.load_page(numero)
    pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala), colorspace=fitz.csGRAY)
    imagem = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    proporcao = _LARGURA_ANALISE / max(1, imagem.width)
    if proporcao < 1:
        imagem = imagem.resize(
            (_LARGURA_ANALISE, max(1, int(imagem.height * proporcao))),
            Image.Resampling.BILINEAR)
    return imagem, pagina.rect


def _linhas_claras(imagem, horizontal=True):
    """Indices das linhas (ou colunas) majoritariamente claras."""
    largura, altura = imagem.size
    dados = imagem.load()
    claras = []
    if horizontal:
        minimo = int(largura * _FRACAO_CALHA)
        for y in range(altura):
            contagem = 0
            for x in range(largura):
                if dados[x, y] >= _LIMIAR_CLARO:
                    contagem += 1
            if contagem >= minimo:
                claras.append(y)
    else:
        minimo = int(altura * _FRACAO_CALHA)
        for x in range(largura):
            contagem = 0
            for y in range(altura):
                if dados[x, y] >= _LIMIAR_CLARO:
                    contagem += 1
            if contagem >= minimo:
                claras.append(x)
    return claras


def _faixas(indices, espessura_min=_ESPESSURA_MIN):
    """Agrupa indices consecutivos em faixas (inicio, fim)."""
    if not indices:
        return []
    faixas = []
    inicio = anterior = indices[0]
    for atual in indices[1:]:
        if atual == anterior + 1:
            anterior = atual
            continue
        if anterior - inicio + 1 >= espessura_min:
            faixas.append((inicio, anterior))
        inicio = anterior = atual
    if anterior - inicio + 1 >= espessura_min:
        faixas.append((inicio, anterior))
    return faixas


def _cortar(imagem, caixa, profundidade, area_pagina):
    """Divide um retangulo pelas calhas encontradas dentro dele."""
    x0, y0, x1, y1 = caixa
    if profundidade >= _PROFUNDIDADE_MAX or (x1 - x0) < 20 or (y1 - y0) < 20:
        return [caixa]

    recorte = imagem.crop(caixa)

    # Tenta primeiro na horizontal (separar tiras), depois na vertical.
    for horizontal in (True, False):
        claras = _linhas_claras(recorte, horizontal)
        # Calhas coladas na borda sao margem da pagina, nao separacao.
        limite = (y1 - y0) if horizontal else (x1 - x0)
        internas = [i for i in claras if 3 < i < limite - 3]
        faixas = _faixas(internas)
        if not faixas:
            continue

        pedacos, anterior = [], 0
        for inicio, fim in faixas:
            if inicio - anterior > 15:
                pedacos.append((anterior, inicio))
            anterior = fim + 1
        if limite - anterior > 15:
            pedacos.append((anterior, limite))
        if len(pedacos) < 2:
            continue

        saida = []
        for inicio, fim in pedacos:
            if horizontal:
                sub = (x0, y0 + inicio, x1, y0 + fim)
            else:
                sub = (x0 + inicio, y0, x0 + fim, y1)
            saida.extend(_cortar(imagem, sub, profundidade + 1, area_pagina))
        return saida

    return [caixa]


def detectar_quadros(documento, numero_pagina: int, escala: float = 1.5) -> list:
    """Retangulos dos quadros, em coordenadas da PAGINA (pontos PDF).

    Devolve [] quando a pagina nao parece HQ -- quem chama trata como pagina
    inteira. E o comportamento certo: inventar quadros numa pagina de texto
    seria pior que nao detectar nada.
    """
    try:
        pagina = documento.load_page(numero_pagina)
        # Criterio decisivo e barato: HQ quase nao tem texto extraivel -- o
        # que existe esta desenhado dentro do balao. Uma pagina de livro tem
        # milhares de caracteres, e suas figuras densas passariam por qualquer
        # medida de tinta.
        if len(pagina.get_text("text").strip()) > _MAX_TEXTO_HQ:
            return []
        imagem, retangulo = _pagina_em_cinza(documento, numero_pagina, escala)
    except Exception as erro:
        print(f"[Quadros] Falha ao renderizar a pagina: {erro}")
        return []

    largura, altura = imagem.size
    area_pagina = largura * altura
    caixas = _cortar(imagem, (0, 0, largura, altura), 0, area_pagina)

    # Filtra o que nao e painel: fragmento pequeno demais, ou o retangulo que
    # simplesmente cobre a pagina toda (pagina sem calha nenhuma).
    uteis = []
    for caixa in caixas:
        x0, y0, x1, y1 = caixa
        fatia = ((x1 - x0) * (y1 - y0)) / max(1, area_pagina)
        if fatia < _AREA_MIN or fatia > 0.95:
            continue
        # Quadro de HQ e PREENCHIDO de arte; bloco de texto e quase todo
        # branco com traco fino. Sem esta medida, o espaco entre paragrafos
        # vira calha e cada paragrafo virava um "quadro".
        if _cobertura_de_tinta(imagem, caixa) < _TINTA_MIN:
            continue
        uteis.append(caixa)

    if len(uteis) < 2:
        return []

    # Numa HQ os quadros cobrem quase a pagina inteira. Poucos
    # retangulos densos soltos sao figuras, nao paineis.
    cobertura = sum((c[2]-c[0]) * (c[3]-c[1]) for c in uteis) / max(1, area_pagina)
    if cobertura < _COBERTURA_MIN:
        return []

    # De volta para pontos da pagina.
    fator_x = retangulo.width / largura
    fator_y = retangulo.height / altura
    quadros = [(x0 * fator_x, y0 * fator_y, x1 * fator_x, y1 * fator_y)
               for x0, y0, x1, y1 in uteis]
    return ordem_leitura(quadros)


def ordem_leitura(quadros: list, direita_para_esquerda: bool = False) -> list:
    """Ordena os quadros como se le.

    Agrupa por faixa horizontal antes de ordenar dentro dela: ordenar so por
    y jogaria o segundo quadro de uma tira para depois da tira inteira.
    """
    if not quadros:
        return []
    altura_media = sum(q[3] - q[1] for q in quadros) / len(quadros)
    tolerancia = altura_media * 0.4

    restantes = sorted(quadros, key=lambda q: q[1])
    saida = []
    while restantes:
        topo = restantes[0][1]
        faixa = [q for q in restantes if abs(q[1] - topo) <= tolerancia]
        restantes = [q for q in restantes if q not in faixa]
        faixa.sort(key=lambda q: q[0], reverse=direita_para_esquerda)
        saida.extend(faixa)
    return saida


def recortar_quadro(documento, numero_pagina: int, quadro: tuple,
                    escala: float = 2.5) -> bytes:
    """PNG do quadro, para a leitura quadro a quadro."""
    pagina = documento.load_page(numero_pagina)
    pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala),
                            clip=fitz.Rect(*quadro))
    return pix.tobytes("png")
