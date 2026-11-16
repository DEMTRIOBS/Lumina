# services/figuras.py
"""Catalogo de ilustracoes do livro: figuras, mapas, tabelas e graficos.

Livros de ciencias, geografia e historia sao lidos tanto pelo texto quanto
pelas ilustracoes -- o leitor quer "voltar naquele mapa do capitulo 4" sem
folhear 200 paginas. Este modulo varre o documento uma unica vez e monta o
indice que alimenta o painel de figuras.

Duas decisoes sustentam o modulo inteiro:

  1. A varredura NAO renderiza nada. Renderizar um pixmap por imagem num livro
     de 1000 paginas levaria minutos e estouraria a memoria. A varredura le so
     geometria e texto; o desenho acontece na hora de exibir a miniatura.

  2. O que o PDF chama de "imagem" nao e o que o leitor chama de "figura".
     Icones, filetes de diagramacao, logotipos e marcas d'agua sao imagens
     legitimas do arquivo e lixo absoluto no painel. Sem os dois filtros daqui
     (tamanho minimo e recorrencia entre paginas) o painel de um livro
     didatico vira uma lista com 300 copias do logotipo da editora.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

import fitz

# ------------------------------------------------------------------ CONSTANTES

# Abaixo disto nao e ilustracao: e icone, filete, marcador ou artefato de
# diagramacao. Medido em pixels da imagem original, nao em pontos da pagina.
_IMAGEM_MIN_PX = 60
# Guarda extra: uma imagem de 2000px pode estar DESENHADA num quadrado de 8
# pontos (bullet vetorizado, selo de rodape). O criterio em pixels sozinho
# deixaria esses passarem.
_IMAGEM_MIN_PT = 20

# Recorrencia = logotipo / marca d'agua. Os dois limites andam juntos: a
# fracao pega o livro longo, o minimo absoluto evita descartar uma figura
# legitima de um documento de 8 paginas.
_FRACAO_RECORRENTE = 0.25
_MIN_PAGINAS_RECORRENTE = 4
# Quantizacao da posicao ao montar a assinatura, em pontos. Marca d'agua
# raramente cai no mesmo ponto flutuante em todas as paginas.
_GRADE_POSICAO = 6.0

# Distancia maxima entre a imagem e o bloco de texto que pode ser sua legenda.
_DIST_LEGENDA = 40.0
# Sem a palavra-rotulo ("Figura", "Mapa"...) so aceitamos texto praticamente
# colado na imagem e curto -- senao o primeiro paragrafo do corpo vira legenda.
_DIST_LEGENDA_SEM_ROTULO = 15.0
_MAX_CHARS_SEM_ROTULO = 200
# Fracao minima de sobreposicao horizontal entre imagem e legenda. Em pagina
# de duas colunas isso impede pegar a legenda da coluna vizinha.
_SOBREPOSICAO_MIN = 0.25
# Legenda mais longa que isto quase certamente e corpo de texto que o extrator
# grudou no mesmo bloco; cortamos para nao poluir a lista do painel.
_MAX_CHARS_LEGENDA = 400

# Folga ao recortar a figura da pagina, em pontos: bordas de screenshot e
# molduras finas ficam cortadas ao meio sem isso.
_FOLGA_RECORTE = 3.0

_RE_ESPACOS = re.compile(r"[ \t ]+")
# Numero de pagina, numeracao romana, filete: nunca e legenda.
_RE_SO_NUMERO = re.compile(r"^[\svixlcdmIVXLCDM\d\.\-–—|]+$")

# Palavra-rotulo -> tipo do catalogo. As variantes de plural existem porque
# livros escrevem "Figuras 3.1 e 3.2" numa legenda so.
_TIPO_POR_PALAVRA = {
    "figura": "figura", "figuras": "figura", "fig": "figura",
    "ilustracao": "figura", "ilustracoes": "figura",
    "esquema": "figura", "esquemas": "figura",
    "diagrama": "figura", "diagramas": "figura",
    "mapa": "mapa", "mapas": "mapa",
    "tabela": "tabela", "tabelas": "tabela",
    "quadro": "tabela", "quadros": "tabela",
    "grafico": "grafico", "graficos": "grafico",
    "imagem": "imagem", "imagens": "imagem",
    "foto": "imagem", "fotos": "imagem",
    "fotografia": "imagem", "fotografias": "imagem",
}

# Alternativas em ordem decrescente de tamanho: senao "fig" casaria antes de
# "figura" e o resto da palavra viraria o numero.
_RE_PALAVRA_ROTULO = re.compile(
    r"^(" + "|".join(sorted(_TIPO_POR_PALAVRA, key=len, reverse=True)) + r")\b\.?",
    re.IGNORECASE,
)
# "4.3", "12", "2-1", "3.4a" -- o formato de numeracao varia por editora.
_RE_NUMERO_ROTULO = re.compile(r"^(\d+(?:[.\-–—]\d+)*[A-Za-z]?)")
_SEPARADORES = ":-–—.)"


@dataclass
class Figura:
    """Uma ilustracao catalogada, pronta para virar linha do painel."""
    pagina: int                 # indice 0, como o resto do projeto
    numero: str                 # "4.3" de "Figura 4.3", ou "" se sem rotulo
    tipo: str                   # figura | mapa | tabela | grafico | imagem
    legenda: str                # texto da legenda, ja limpo
    bbox: tuple                 # (x0, y0, x1, y1) da IMAGEM na pagina
    largura: int                # pixels da imagem original
    altura: int

    @property
    def rotulo(self) -> str:
        """Como o painel deve nomear a figura na lista."""
        nome = self.tipo.capitalize()
        if self.numero:
            return f"{nome} {self.numero}"
        return f"{nome} (pag. {self.pagina + 1})"


# ------------------------------------------------------------------ AUXILIARES

def _limpar(texto: str) -> str:
    """Normaliza o texto extraido do PDF para leitura humana."""
    texto = texto.replace("ﬁ", "fi").replace("ﬂ", "fl")
    texto = texto.replace("­", "")                      # hifen condicional
    texto = texto.replace("’", "'").replace("‘", "'")
    texto = texto.replace("“", '"').replace("”", '"')
    texto = texto.replace("\n", " ")
    return _RE_ESPACOS.sub(" ", texto).strip()


def _sem_acento(texto: str) -> str:
    """'Gráfico' e 'Grafico' precisam bater com a mesma chave do dicionario."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _ler_rotulo(legenda: str):
    """Devolve (tipo, numero) se a legenda comeca com rotulo, senao None.

    O rotulo vale como confirmacao de que aquele texto E uma legenda -- por
    isso ele exige numero OU separador logo depois. "Figura 4.3:" e legenda;
    "Figuras servem para ilustrar" e corpo de texto.
    """
    if not legenda:
        return None
    normalizado = _sem_acento(legenda).lstrip()
    achado = _RE_PALAVRA_ROTULO.match(normalizado)
    if not achado:
        return None

    palavra = achado.group(1).lower().rstrip(".")
    tipo = _TIPO_POR_PALAVRA.get(palavra)
    if not tipo:
        return None

    resto = normalizado[achado.end():].lstrip()
    numero = _RE_NUMERO_ROTULO.match(resto)
    if numero:
        return tipo, numero.group(1)
    # Sem numero, so aceitamos se vier pontuacao de legenda logo em seguida
    # ("Mapa - Bacias hidrograficas").
    if resto[:1] in _SEPARADORES:
        return tipo, ""
    return None


def _cortar_legenda(texto: str) -> str:
    """Impede que o corpo do texto entre no painel disfarcado de legenda.

    Alguns PDFs colocam legenda e paragrafo seguinte no mesmo bloco. Cortamos
    na primeira fronteira de frase depois de um trecho ja informativo.
    """
    if len(texto) <= _MAX_CHARS_LEGENDA:
        return texto
    corte = texto.find(". ", 60)
    if 0 < corte <= _MAX_CHARS_LEGENDA:
        return texto[:corte + 1]
    return texto[:_MAX_CHARS_LEGENDA].rstrip() + "..."


def _sobreposicao_horizontal(caixa_img, caixa_txt) -> float:
    """Fracao da largura do texto que fica sob a imagem (0.0 a 1.0)."""
    largura = caixa_txt[2] - caixa_txt[0]
    if largura <= 0:
        return 0.0
    comum = min(caixa_img[2], caixa_txt[2]) - max(caixa_img[0], caixa_txt[0])
    return max(0.0, comum) / largura


def _texto_dos_blocos(dados) -> list:
    """[(bbox, texto_limpo), ...] dos blocos de texto de uma pagina."""
    saida = []
    for bloco in dados.get("blocks", []):
        if bloco.get("type") != 0:
            continue
        partes = []
        for linha in bloco.get("lines", []):
            partes.append("".join(s.get("text", "") for s in linha.get("spans", [])))
        texto = _limpar(" ".join(partes))
        if not texto or _RE_SO_NUMERO.match(texto):
            continue
        saida.append((tuple(bloco.get("bbox") or (0, 0, 0, 0)), texto))
    return saida


# --------------------------------------------------------------- CANDIDATOS

@dataclass
class _Candidata:
    """Imagem que sobreviveu ao filtro de tamanho, antes da deduplicacao."""
    pagina: int
    bbox: tuple
    largura: int
    altura: int
    assinatura_conteudo: tuple
    assinatura_posicao: tuple
    legenda: str = ""
    tipo: str = "imagem"
    numero: str = ""


def _quantizar(valor: float) -> int:
    return int(round(valor / _GRADE_POSICAO))


def _imagens_relevantes(pagina) -> list:
    """Geometria das imagens da pagina, ja sem icones e filetes.

    Usa get_image_info em vez dos blocos de imagem de get_text("dict"): o
    primeiro devolve so os metadados, o segundo carrega os BYTES de cada
    imagem. No livro de teste isso foi a diferenca entre 0,5 s e 2,4 s de
    varredura -- e a varredura nao precisa de um unico byte de pixel.
    """
    try:
        informacoes = pagina.get_image_info()
    except Exception:
        return []

    brutas = []
    for info in informacoes:
        caixa = info.get("bbox")
        if not caixa:
            continue
        largura = int(info.get("width") or 0)
        altura = int(info.get("height") or 0)
        if largura < _IMAGEM_MIN_PX or altura < _IMAGEM_MIN_PX:
            continue
        largura_pt = caixa[2] - caixa[0]
        altura_pt = caixa[3] - caixa[1]
        if largura_pt < _IMAGEM_MIN_PT or altura_pt < _IMAGEM_MIN_PT:
            continue
        brutas.append({
            "bbox": tuple(round(float(v), 2) for v in caixa),
            "largura": largura,
            "altura": altura,
            "bytes": int(info.get("size") or 0),
        })
    return _agrupar_sobrepostas(brutas)


def _agrupar_sobrepostas(brutas: list) -> list:
    """Junta imagens que se sobrepoem numa unica figura.

    Mapas e infograficos costumam vir fatiados em varios XObjects. Sem isto o
    painel mostraria o mesmo mapa em quatro pedacos, nenhum deles legivel. O
    criterio e sobreposicao real (nao proximidade) para nao colar duas
    capturas de tela empilhadas.
    """
    if len(brutas) < 2:
        return brutas

    grupos = []
    for item in brutas:
        alvo = None
        for grupo in grupos:
            if _intersecta(grupo["bbox"], item["bbox"]):
                alvo = grupo
                break
        if alvo is None:
            grupos.append(dict(item))
            continue
        caixa_a, caixa_b = alvo["bbox"], item["bbox"]
        alvo["bbox"] = (min(caixa_a[0], caixa_b[0]), min(caixa_a[1], caixa_b[1]),
                        max(caixa_a[2], caixa_b[2]), max(caixa_a[3], caixa_b[3]))
        alvo["largura"] = max(alvo["largura"], item["largura"])
        alvo["altura"] = max(alvo["altura"], item["altura"])
        alvo["bytes"] += item["bytes"]
    return grupos


def _intersecta(a, b, folga: float = 1.0) -> bool:
    return (a[0] < b[2] - folga and b[0] < a[2] - folga
            and a[1] < b[3] - folga and b[1] < a[3] - folga)


def _achar_legenda(caixa_img, blocos):
    """Legenda da imagem: (texto, tipo, numero).

    Procura primeiro ABAIXO da imagem, que e onde 90% dos livros colocam a
    legenda, e so depois acima. Em cada lado percorre do mais perto para o
    mais longe e para no primeiro bloco com palavra-rotulo. Se nenhum bloco
    tiver rotulo, aceita o mais proximo apenas se estiver colado e for curto.
    """
    abaixo = []
    acima = []
    for caixa_txt, texto in blocos:
        if _sobreposicao_horizontal(caixa_img, caixa_txt) < _SOBREPOSICAO_MIN:
            continue
        distancia = caixa_txt[1] - caixa_img[3]
        if -2.0 <= distancia <= _DIST_LEGENDA:
            abaixo.append((max(0.0, distancia), texto))
            continue
        distancia = caixa_img[1] - caixa_txt[3]
        if -2.0 <= distancia <= _DIST_LEGENDA:
            acima.append((max(0.0, distancia), texto))

    for lado in (abaixo, acima):
        if not lado:
            continue
        lado.sort(key=lambda item: item[0])
        for distancia, texto in lado:
            rotulo = _ler_rotulo(texto)
            if rotulo:
                return _cortar_legenda(texto), rotulo[0], rotulo[1]
        # Nenhum rotulo deste lado: o vizinho mais proximo so vale como
        # legenda se estiver praticamente encostado e for curto.
        distancia, texto = lado[0]
        if distancia <= _DIST_LEGENDA_SEM_ROTULO and len(texto) < _MAX_CHARS_SEM_ROTULO:
            return texto, "imagem", ""

    return "", "imagem", ""


def _recorrentes(candidatas: list, total_paginas: int) -> set:
    """Assinaturas de logotipo / marca d'agua, para descartar.

    Duas assinaturas, porque ha dois jeitos de a mesma marca se repetir:
      - o MESMO objeto de imagem reusado em todas as paginas (conteudo igual);
      - a marca re-codificada por pagina, com bytes diferentes, mas desenhada
        sempre no mesmo lugar e no mesmo tamanho (posicao igual).
    Contamos PAGINAS distintas, nao ocorrencias: uma pagina com a mesma figura
    repetida quatro vezes nao pode disparar o filtro sozinha.
    """
    limiar = max(_MIN_PAGINAS_RECORRENTE, int(total_paginas * _FRACAO_RECORRENTE))

    por_conteudo = {}
    por_posicao = {}
    for candidata in candidatas:
        if candidata.assinatura_conteudo[2]:      # so vale com bytes conhecidos
            por_conteudo.setdefault(candidata.assinatura_conteudo, set()).add(candidata.pagina)
        por_posicao.setdefault(candidata.assinatura_posicao, set()).add(candidata.pagina)

    suspeitas = set()
    for assinatura, paginas in por_conteudo.items():
        if len(paginas) >= limiar:
            suspeitas.add(assinatura)
    for assinatura, paginas in por_posicao.items():
        if len(paginas) >= limiar:
            suspeitas.add(assinatura)
    return suspeitas


# ------------------------------------------------------------------ VARREDURA

def listar_figuras(documento, ao_progredir=None, cancelar=None) -> List[Figura]:
    """Varre o documento inteiro e cataloga as ilustracoes.

    `documento` e um fitz.Document ja aberto. `ao_progredir(feito, total)` e
    chamado a cada pagina para a barra de progresso. `cancelar` e um
    threading.Event -- respeitado a cada pagina, porque isto roda em segundo
    plano e um livro de 1000 paginas nao pode segurar o fechamento do leitor.

    Cancelar devolve o catalogo parcial do que ja foi lido, e nao uma lista
    vazia: o painel prefere mostrar as figuras dos primeiros capitulos a
    mostrar nada.
    """
    if documento is None:
        return []
    try:
        total = len(documento)
    except Exception:
        return []
    if total == 0:
        return []

    # Sem os bytes das imagens: get_text so precisa entregar o texto e suas
    # caixas. Preservar imagens aqui multiplicaria por cinco o custo da
    # varredura sem acrescentar nada, ja que a geometria vem de outro lugar.
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES

    candidatas = []
    lidas = 0
    for numero in range(total):
        if cancelar is not None and cancelar.is_set():
            break
        try:
            pagina = documento.load_page(numero)
            imagens = _imagens_relevantes(pagina)
            if imagens:
                blocos = _texto_dos_blocos(pagina.get_text("dict", flags=flags))
                for imagem in imagens:
                    candidatas.append(_montar_candidata(numero, imagem, blocos))
        except Exception as erro:
            print(f"[Figuras] Pagina {numero} ignorada: {erro}")
        lidas = numero + 1
        if ao_progredir is not None:
            try:
                ao_progredir(lidas, total)
            except Exception:
                pass

    if not candidatas:
        return []

    # A base do limiar e o que foi realmente lido: cancelar no meio nao pode
    # transformar figuras normais em "recorrentes".
    descartar = _recorrentes(candidatas, max(1, lidas))

    figuras = []
    for candidata in candidatas:
        if (candidata.assinatura_conteudo in descartar
                or candidata.assinatura_posicao in descartar):
            continue
        figuras.append(Figura(
            pagina=candidata.pagina,
            numero=candidata.numero,
            tipo=candidata.tipo,
            legenda=candidata.legenda,
            bbox=candidata.bbox,
            largura=candidata.largura,
            altura=candidata.altura,
        ))
    return figuras


def _montar_candidata(numero: int, imagem: dict, blocos: list) -> _Candidata:
    caixa = imagem["bbox"]
    legenda, tipo, numero_rotulo = _achar_legenda(caixa, blocos)
    return _Candidata(
        pagina=numero,
        bbox=caixa,
        largura=imagem["largura"],
        altura=imagem["altura"],
        assinatura_conteudo=(imagem["largura"], imagem["altura"], imagem["bytes"]),
        assinatura_posicao=(imagem["largura"], imagem["altura"],
                            _quantizar(caixa[0]), _quantizar(caixa[1]),
                            _quantizar(caixa[2]), _quantizar(caixa[3])),
        legenda=legenda,
        tipo=tipo,
        numero=numero_rotulo,
    )


# ---------------------------------------------------------------- RENDERIZACAO

def _retangulo(pagina, figura: Figura, folga: float) -> Optional[fitz.Rect]:
    """Area a recortar, ja com folga e limitada a pagina."""
    try:
        alvo = fitz.Rect(figura.bbox)
    except Exception:
        return None
    alvo = fitz.Rect(alvo.x0 - folga, alvo.y0 - folga,
                     alvo.x1 + folga, alvo.y1 + folga)
    alvo = alvo & pagina.rect
    if alvo.is_empty or alvo.width < 2 or alvo.height < 2:
        return None
    return alvo


def _recortar(documento, figura: Figura, escala: float,
              folga: float = _FOLGA_RECORTE) -> Optional[bytes]:
    try:
        pagina = documento.load_page(figura.pagina)
    except Exception as erro:
        print(f"[Figuras] Pagina {figura.pagina} indisponivel: {erro}")
        return None
    alvo = _retangulo(pagina, figura, folga)
    if alvo is None:
        return None
    try:
        pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala), clip=alvo)
        return pix.tobytes("png")
    except Exception as erro:
        print(f"[Figuras] Falha ao recortar figura: {erro}")
        return None


def renderizar_miniatura(documento, figura: Figura,
                         largura_alvo: int = 220) -> Optional[bytes]:
    """PNG da figura recortada da pagina, para a lista do painel.

    A escala sai da largura pedida e nao da resolucao da imagem original:
    o painel quer todas as miniaturas com a mesma largura, venham elas de um
    scan de 4000px ou de um grafico vetorial de 200px.
    """
    largura_pt = max(1.0, figura.bbox[2] - figura.bbox[0])
    escala = largura_alvo / largura_pt
    # Piso: miniatura ilegivel nao serve de nada. Teto: recortar um mapa de
    # pagina inteira a 8x geraria dezenas de MB para um item de lista.
    escala = min(6.0, max(0.25, escala))
    return _recortar(documento, figura, escala)


def renderizar_ampliada(documento, figura: Figura, escala: float = 3.0) -> Optional[bytes]:
    """PNG em alta resolucao, para abrir em tela cheia (zoom de mapa).

    A folga e maior que a da miniatura de proposito: em tela cheia o leitor
    quer ver a escala, a legenda de cores e a fonte impressas na borda do
    mapa, que costumam ficar rente ao limite da imagem.
    """
    escala = min(8.0, max(0.5, float(escala)))
    return _recortar(documento, figura, escala, folga=_FOLGA_RECORTE * 2)
