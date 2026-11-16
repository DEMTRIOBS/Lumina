# services/reflow.py
"""Motor de reflow: converte a diagramacao rigida do PDF em texto de livro.

O PDF guarda posicoes absolutas, nao paragrafos. Ler isso cru (que era o que a
versao anterior fazia) produz linhas quebradas, palavras hifenizadas partidas,
numeros de pagina no meio do texto e colunas embaralhadas.

Este modulo reconstroi a estrutura logica:
  1. mede o corpo tipografico do documento inteiro;
  2. descobre cabecalhos/rodapes recorrentes e os descarta;
  3. detecta layout em duas colunas e reordena a leitura;
  4. junta linhas em paragrafos e desfaz hifenizacao de fim de linha;
  5. classifica cada paragrafo (titulo, lista, citacao, nota, corpo);
  6. coloca CADA FIGURA no ponto do texto onde ela aparece na pagina.

O resultado e uma lista de blocos semanticos que a interface renderiza com a
tipografia escolhida pelo usuario -- reflow real, adaptado a largura da janela.

SOBRE AS FIGURAS (o passo 6):
a versao anterior empilhava todas as imagens no fim da pagina. Era simples de
escrever e errado de ler: num livro tecnico a frase "a figura abaixo mostra..."
ficava a tres paragrafos de distancia da figura. Agora imagem e texto sao
ordenados JUNTOS pela geometria, entao a ilustracao cai exatamente onde o autor
a colocou.

Tres problemas aparecem junto com essa mudanca, e os tres sao tratados aqui:

  * o PDF fatia UMA figura em varios XObjects (mapas e capturas de tela vem
    quase sempre em tiras). Caixas que se tocam sao fundidas antes de virar
    figura, senao a mesma imagem apareceria em quatro pedacos empilhados;

  * logotipo e marca d'agua sao imagens legitimas do arquivo e lixo no meio da
    leitura. O que reaparece no mesmo lugar em boa parte das paginas e
    descartado;

  * diagramas VETORIAIS (fluxogramas, graficos, esquemas) nao sao imagens: sao
    centenas de tracos soltos. O extrator ignorava os tracos e cuspia os
    rotulos internos no meio da prosa -- e por isso que um livro tecnico
    aparecia com palavras soltas sem sentido entre os paragrafos. Agrupando os
    tracos e rasterizando a regiao, o diagrama volta inteiro e os rotulos param
    de poluir o texto.
"""
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

import fitz

import services.realce as realce

# ------------------------------------------------------------------ CONSTANTES

# Fracao da altura da pagina considerada zona de cabecalho / rodape.
_ZONA_MARGEM = 0.075
# Um texto so e descartado como cabecalho se reaparecer em pelo menos esta
# fracao das paginas amostradas.
_LIMIAR_RECORRENCIA = 0.30
# Imagens menores que isso sao icones, filetes e artefatos de diagramacao.
_IMAGEM_MIN_PX = 55
# Abaixo disto a pagina e considerada digitalizada (imagem pura).
_MIN_CHARS_PAGINA = 25

# ------------------------------------------------------------------- FIGURAS
# Medida em PONTOS da pagina, e nao em pixels da imagem: um logotipo de 2000 px
# pode estar desenhado num quadrado de 10 pontos, e o criterio em pixels
# sozinho deixaria ele passar.
_FIGURA_MIN_PT = 42.0
# Acima desta fracao da pagina a "imagem" e fundo, moldura ou papel de parede.
_FIGURA_MAX_FRACAO = 0.92
# Folga para considerar duas caixas parte da MESMA figura. Um pouco de folga e
# necessario porque as tiras de um mapa fatiado costumam encostar sem se
# sobrepor -- e as vezes ficam a um ponto de distancia.
_FOLGA_UNIAO = 7.0
# Largura desejada, em pixels, da figura rasterizada. Rasterizar a regiao da
# pagina (em vez de usar os bytes embutidos) resolve de uma vez transparencia,
# fatiamento e texto desenhado dentro do grafico.
_FIGURA_PX_ALVO = 1000
_FIGURA_ESCALA_MAX = 4.0
# Uma imagem so e considerada marca d'agua se reaparecer, no mesmo lugar e no
# mesmo tamanho, em pelo menos esta fracao das paginas amostradas.
_LIMIAR_IMAGEM_RECORRENTE = 0.35
# Grade de quantizacao da posicao, em pontos, ao montar a assinatura.
_GRADE_IMAGEM = 8.0

# ---- diagramas vetoriais
# Traco mais fino que isto e filete, sublinhado ou regua de tabela.
_TRACO_MIN_PT = 3.0
# Pagina com mais tracos que isso e mapa vetorial ou grafico gigante; agrupar
# tudo custaria caro e o resultado seria a pagina inteira virando uma figura.
_MAX_TRACOS = 1500
# Um diagrama e maior que um icone e menor que a pagina.
_FIGURA_VETOR_MIN_PT = 70.0
# Quando o agrupamento engole texto, ele precisa parecer MESMO um diagrama:
# muitos tracos e poucos caracteres. Uma caixa de fundo atras de um paragrafo
# tem um traco so; um fluxograma tem uma dezena.
_MIN_TRACOS_COM_TEXTO = 5
_MAX_CHARS_FIGURA = 600
# Texto majoritariamente monoespacado dentro da area = fundo de bloco de
# codigo, nao diagrama. Virar imagem ali tiraria o realce e o botao de copiar.
_MAX_MONO_FIGURA = 0.40

_RE_MARCADOR = re.compile(r"^\s*([•‣◦⁃∙•·▪●◦\-\*–—]|"
                          r"\(?\d{1,2}[\.\)]|[a-zA-Z][\.\)])\s+")

# Faixas Unicode de escritas que nao aparecem num livro latino. Quando um PDF
# usa fonte embutida sem tabela ToUnicode completa, o extrator devolve o
# identificador cru do glifo, que costuma cair justamente nestes blocos --
# foi assim que "Capitulo" virou uma sequencia de letras cingalesas.
_FAIXAS_IMPOSSIVEIS = (
    (0x0900, 0x0DFF),   # devanagari, bengali, gurmukhi, oriya, tamil, cingales
    (0x0E00, 0x0FFF),   # tai, laosiano, tibetano
    (0x1000, 0x109F),   # birmanes
    (0x1200, 0x139F),   # etiope
    (0x1780, 0x17FF),   # khmer
    (0xA000, 0xA4CF),   # yi
)
# Acima desta fracao de letras impossiveis a PALAVRA e considerada ilegivel.
_LIMIAR_ILEGIVEL = 0.30
# Ate este tamanho o bloco corrompido vira imagem; acima disso so limpamos
# as palavras quebradas, para nao congelar um paragrafo inteiro numa figura.
_MAX_CHARS_RECORTE = 300


# Mesma faixa em forma de regex: uma varredura em C em vez de uma chamada de
# funcao por caractere. Sem isto, verificar cada bloco custava ~6 ms por pagina.
_RE_IMPOSSIVEL = re.compile("[" + "".join(
    chr(inicio) + "-" + chr(fim) for inicio, fim in _FAIXAS_IMPOSSIVEIS) + "]")


def _impossivel(caractere: str) -> bool:
    ponto = ord(caractere)
    return any(inicio <= ponto <= fim for inicio, fim in _FAIXAS_IMPOSSIVEIS)
_RE_SO_NUMERO = re.compile(r"^[\svixlcdmIVXLCDM\d\.\-–—|]+$")
_RE_DIGITOS = re.compile(r"\d+")
# Legenda de figura. Exige o NUMERO logo depois da palavra: "Figura 4.3" e
# legenda, "Figuras ajudam a entender" e corpo de texto.
_RE_LEGENDA = re.compile(
    r"^\s*(figuras?|fig\.?|ilustra[cç][aã]o|esquemas?|diagramas?|mapas?|"
    r"tabelas?|quadros?|gr[aá]ficos?|imagens?|fotos?|listagens?|exemplos?)"
    r"\s*\.?\s*\d", re.IGNORECASE)
_RE_ESPACOS = re.compile(r"[ \t ]+")


_RE_LINHA_SO_NUMERO = re.compile(r"^\s*\d{1,4}\s*$")
_RE_PREFIXO_NUMERO = re.compile(r"^\s*(\d{1,4})(?:\s+|$)")


def _limpar_numeracao_de_codigo(blocos):
    """Remove a numeracao de linha que o PDF imprime ao lado das listagens.

    Livros de programacao trazem "1 2 3 4" numa coluna propria: o extrator a
    entrega como um bloco de codigo separado, e a leitura mostrava uma caixa
    de codigo VAZIA so com numeros antes do codigo de verdade. Quando os
    numeros vem grudados no comeco da linha ("1 django-admin.py ..."), eles
    sujam a copia. A propria interface numera as linhas quando pedido.
    """
    saida = []
    for bloco in blocos:
        if bloco.tipo != "codigo" or not bloco.texto:
            saida.append(bloco)
            continue
        linhas = bloco.texto.split("\n")
        if all(_RE_LINHA_SO_NUMERO.match(l) for l in linhas if l.strip()):
            continue                      # coluna de numeros solta
        numeros = [_RE_PREFIXO_NUMERO.match(l) for l in linhas]
        validas = [m for m in numeros if m]
        if (validas and len(validas) == len([l for l in linhas if l.strip()])
                and [int(m.group(1)) for m in validas]
                == list(range(int(validas[0].group(1)),
                              int(validas[0].group(1)) + len(validas)))):
            bloco.texto = "\n".join(
                l[m.end():] if m else l for l, m in zip(linhas, numeros))
        saida.append(bloco)
    return saida


def _chars_do_bloco(bloco) -> int:
    return sum(len(_texto_da_linha(l).strip()) for l in bloco.get("lines", []))


def _layout_em_colunas(blocos, largura) -> bool:
    """A pagina esta mesmo em duas colunas?

    Mede o VOLUME de texto de cada lado do meio da pagina. Contar "blocos
    estreitos" (o criterio anterior) confundia titulo, item de lista e linha
    de codigo -- todos curtos -- com coluna, e deixava passar a revista de
    verdade, em que cada coluna e um bloco unico e alto.
    """
    meio = largura / 2
    folga = largura * 0.04
    esquerda = direita = cruza = total = 0
    for bloco in blocos:
        caixa = bloco.get("bbox")
        if not caixa:
            continue
        n = _chars_do_bloco(bloco)
        if not n:
            continue
        total += n
        if caixa[2] <= meio + folga:
            esquerda += n
        elif caixa[0] >= meio - folga:
            direita += n
        else:
            cruza += n
    return (total >= 200 and esquerda >= total * 0.2 and direita >= total * 0.2
            and cruza <= total * 0.25)


_SIMBOLOS_MATEMATICOS = set("∫∬∮∑∏√∂∇∞≤≥≠≈≡∝∈∉∋⊂⊃⊆⊇∪∩∀∃∄∅→⇒⇔↦±∓×÷∘′″‰"
                            "ℝℕℤℚℂ∧∨¬⊥∥∠∡⟨⟩⌊⌋⌈⌉")
_RE_PASSO = re.compile(r"^\s*(?:\d{1,2}[.)]|passo\s+\d+|etapa\s+\d+)\s", re.IGNORECASE)
_RE_AVISO = re.compile(
    r"^\s*(?:aten[cç][aã]o|cuidado|importante|aviso|advert[eê]ncia|perigo|nota|"
    r"observa[cç][aã]o|dica|warning|caution|note|tip)\s*[:!\u2014\-]", re.IGNORECASE)


def _eh_simbolo_matematico(caractere) -> bool:
    if caractere in _SIMBOLOS_MATEMATICOS:
        return True
    ponto = ord(caractere)
    return (0x0391 <= ponto <= 0x03C9          # letras gregas
            or 0x1D400 <= ponto <= 0x1D7FF     # alfanumericos matematicos
            or 0x2200 <= ponto <= 0x22FF)      # operadores matematicos


def _eh_fundo_de_texto(desenho, largura_pt, altura_pt, largura_pag, corpo):
    """Forma que so pinta o FUNDO de um trecho de texto, nao um desenho.

    PDFs exportados do Word/Google Docs poem um retangulo branco atras de cada
    paragrafo. Agrupados, eles passavam por "diagrama" e o paragrafo inteiro
    virava um recorte de pagina -- um retangulo branco no meio do tema escuro,
    sem busca nem grifo.
    """
    preenchimento = desenho.get("fill")
    contorno = desenho.get("color")
    if preenchimento is None or contorno is not None:
        return False
    # Preenchimento invisivel sobre papel branco.
    if all(c >= 0.94 for c in preenchimento[:3]):
        return True
    # Faixa larga e baixa (marca-texto, fundo de linha/paragrafo) feita de
    # um unico retangulo.
    itens = desenho.get("items") or []
    so_retangulo = len(itens) == 1 and itens[0] and itens[0][0] in ("re", "qu")
    return (so_retangulo and largura_pt > largura_pag * 0.5
            and altura_pt < corpo * 4.5)


# ------------------------------------------------------------------ GEOMETRIA

def _uniao(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _area(caixa):
    return max(0.0, caixa[2] - caixa[0]) * max(0.0, caixa[3] - caixa[1])


def _area_comum(a, b):
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def _fracao_dentro(interna, externa):
    """Quanto da caixa `interna` cai dentro de `externa` (0.0 a 1.0)."""
    total = _area(interna)
    if total <= 0:
        return 0.0
    return _area_comum(interna, externa) / total


def _proximas(a, b, folga):
    """Se as duas caixas se tocam, se sobrepoem ou quase -- a menos de `folga`."""
    return (a[0] - folga < b[2] and b[0] - folga < a[2]
            and a[1] - folga < b[3] and b[1] - folga < a[3])


def _agrupar_caixas(itens, folga):
    """Funde caixas vizinhas em grupos. Cada item e um par (caixa, carga).

    Repete ate estabilizar de proposito: numa passada so, A e C ficariam
    separados mesmo quando B -- que cresceu no meio do caminho -- encosta nos
    dois. E exatamente o caso do mapa fatiado em tiras.
    """
    grupos = [{"bbox": caixa, "partes": 1, "carga": [carga]}
              for caixa, carga in itens]
    for _ in range(6):
        fundiu = False
        saida = []
        for grupo in grupos:
            alvo = None
            for existente in saida:
                if _proximas(existente["bbox"], grupo["bbox"], folga):
                    alvo = existente
                    break
            if alvo is None:
                saida.append(grupo)
                continue
            alvo["bbox"] = _uniao(alvo["bbox"], grupo["bbox"])
            alvo["partes"] += grupo["partes"]
            alvo["carga"].extend(grupo["carga"])
            fundiu = True
        grupos = saida
        if not fundiu:
            break
    return grupos


# Sem esta bandeira o extrator DECODIFICA cada imagem da pagina para entregar
# os bytes junto com o texto -- e como toda figura daqui e rasterizada da
# propria pagina, esses bytes seriam decodificados para nada.
#
# Atencao: sem `TEXT_PRESERVE_IMAGES` o dicionario nao traz apenas os pixels a
# menos; ele omite o BLOCO DE IMAGEM inteiro. A geometria das figuras tem que
# vir de `get_image_info`, que e justamente a consulta barata (so metadados).
_FLAGS_SEM_IMAGEM = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES


def _imagens_da_pagina(pagina) -> list:
    """Geometria das imagens da pagina + impressao digital do conteudo.

    O `digest` (md5 dos pixels decodificados) separa a marca d'agua -- a MESMA
    imagem em toda pagina, ainda que recodificada -- das figuras de um livro
    que so tem o mesmo tamanho e posicao (capturas de tela de um tutorial).
    """
    try:
        informacoes = pagina.get_image_info(hashes=True)
    except Exception:
        try:
            informacoes = pagina.get_image_info()
        except Exception:
            return []
    saida = []
    for info in informacoes:
        caixa = info.get("bbox")
        if caixa:
            digest = info.get("digest")
            saida.append({"bbox": tuple(float(v) for v in caixa),
                          "width": int(info.get("width") or 0),
                          "height": int(info.get("height") or 0),
                          "digest": digest.hex() if isinstance(digest, bytes) else ""})
    return saida


def _marcar_legendas(blocos) -> None:
    """Promove a legenda encostada numa figura ao seu proprio tipo.

    Legenda nao e paragrafo: e um rotulo curto que pertence a imagem. Sem
    marca-la, "Figura 4.3 - Arquitetura do Django" era lida como se fosse a
    continuacao do texto, no mesmo corpo e com a mesma entrelinha, e o leitor
    tropecava nela a cada figura.

    So vale para o bloco COLADO na figura (antes ou depois): mais longe que
    isso ja e o corpo do capitulo, mesmo comecando com a palavra "Figura".
    """
    for indice, bloco in enumerate(blocos):
        if bloco.tipo not in ("paragrafo", "nota"):
            continue
        if len(bloco.texto) > 300 or not _RE_LEGENDA.match(bloco.texto):
            continue
        vizinhos = []
        if indice > 0:
            vizinhos.append(blocos[indice - 1])
        if indice + 1 < len(blocos):
            vizinhos.append(blocos[indice + 1])
        if any(v.tipo == "imagem" for v in vizinhos):
            bloco.tipo = "legenda"


def _assinatura_imagem(caixa, bloco):
    """Identidade de uma imagem para efeito de recorrencia.

    Junta TAMANHO, POSICAO e CONTEUDO. So tamanho e posicao nao bastavam: um
    livro com tres capturas de tela do mesmo tamanho, no mesmo lugar da
    pagina, perdia as tres como se fossem marca d'agua. O digest e dos pixels
    decodificados, entao a marca d'agua recodificada a cada pagina continua
    com a mesma assinatura.
    """
    return (bloco.get("digest") or "",
            int(bloco.get("width") or 0), int(bloco.get("height") or 0),
            int(caixa[0] / _GRADE_IMAGEM), int(caixa[1] / _GRADE_IMAGEM),
            int((caixa[2] - caixa[0]) / _GRADE_IMAGEM),
            int((caixa[3] - caixa[1]) / _GRADE_IMAGEM))


@dataclass
class Bloco:
    """Unidade semantica renderizavel."""
    tipo: str                      # titulo | paragrafo | lista | citacao | nota | imagem | aviso
    texto: str = ""
    nivel: int = 0                 # 1..3 para titulos
    imagem: Optional[bytes] = None
    largura_img: int = 0
    altura_img: int = 0
    # Recorte de texto que nao pode ser extraido: a interface pinta a imagem
    # com a cor do tema em vez de exibir o retangulo branco original.
    recolorir: bool = False
    # Altura do recorte medida em multiplos do corpo do texto, para a imagem
    # acompanhar o tamanho de fonte escolhido pelo leitor.
    altura_em_corpos: float = 0.0
    # Trechos monoespacados DENTRO de um paragrafo comum -- as mencoes a
    # codigo no meio da prosa ("use o metodo list_display"). Deslocamentos de
    # caractere relativos ao texto do bloco.
    mono: List[tuple] = field(default_factory=list)
    # A ultima palavra do bloco foi partida por hifenizacao ("in-" ... na
    # pagina seguinte "teligencia"). O leitor usa isso para costurar o
    # paragrafo que atravessa a quebra de pagina sem espaco no meio.
    hifen_final: bool = False


@dataclass
class PaginaReflow:
    numero: int
    blocos: List[Bloco] = field(default_factory=list)
    texto_puro: str = ""
    digitalizada: bool = False
    palavras: int = 0


# ------------------------------------------------------------------ AUXILIARES

def _normalizar_assinatura(texto: str) -> str:
    """'Pagina 12' e 'Pagina 87' viram a mesma assinatura."""
    return _RE_DIGITOS.sub("#", texto.strip().lower())[:70]


def _recorte(pagina):
    """Area de extracao. No EPUB, sem recorte: o MuPDF nao quebra blocos
    <pre>, e a linha de codigo mais larga que a pagina A5 era cortada pelo
    extrator ("on_delete=models.CAS"). No PDF, a pagina e o limite -- o que
    fica fora dela e lixo de diagramacao, nao conteudo."""
    try:
        from services.pdf_service import veio_de_epub
        if veio_de_epub(pagina.parent):
            return fitz.INFINITE_RECT()
    except Exception:
        pass
    return None


def _consertar(dados, pagina):
    """Aplica a tabela de ligaduras do EPUB paginado (ver pdf_service)."""
    try:
        from services.pdf_service import tabela_de_texto
        tabela = tabela_de_texto(pagina.parent)
    except Exception:
        tabela = None
    if tabela:
        for bloco in dados.get("blocks", []):
            for linha in bloco.get("lines", []):
                for span in linha.get("spans", []):
                    span["text"] = span["text"].translate(tabela)
    return dados


def _limpar(texto: str) -> str:
    texto = texto.replace("ﬁ", "fi").replace("ﬂ", "fl")
    texto = texto.replace("­", "")            # hifen condicional
    texto = texto.replace("’", "'").replace("‘", "'")
    texto = texto.replace("“", '"').replace("”", '"')
    texto = _RE_ESPACOS.sub(" ", texto)
    return texto.strip()


# Marcadores desenhados com as fontes Symbol/Wingdings do Word: o extrator os
# devolve na area de uso privado do Unicode, que nenhuma fonte de leitura tem
# -- na tela viravam um quadrado vazio e a lista nao era reconhecida.
_MARCADORES_PRIVADOS = str.maketrans({
    "\uf0b7": "\u2022", "\uf0a7": "\u25aa", "\uf076": "\u2022", "\uf0d8": "\u2022",
    "\uf0a8": "\u2022", "\uf06e": "\u25aa", "\uf0fc": "\u2713", "\uf0e0": "\u2192",
    "\uf0ad": "\u2022", "\uf02d": "\u2013",
    # O MuPDF grava TODO hifen como inseparavel (U+2011) ao converter EPUB
    # para PDF; de volta ao hifen comum, a busca acha "Self-Play" e a quebra
    # de silaba no fim da linha e desfeita pela regra de sempre.
    "\u2011": "-",
})


def _texto_da_linha(linha) -> str:
    texto = "".join(span.get("text", "") for span in linha.get("spans", []))
    return texto.translate(_MARCADORES_PRIVADOS)


_FONTES_MONO = ("mono", "courier", "consol", "menlo", "inconsolata", "source code",
                "fira code", "dejavu sans mono", "andale")

# Fontes de formula e simbolo que o PDF costuma marcar como monoespacadas. Num
# livro de matematica elas cobrem boa parte da pagina; sem esta excecao, o
# livro inteiro seria classificado como tecnico e sairia pintado como codigo.
# So fontes de FORMULA. "cmr"/"cmbx" (Computer Modern Roman) ficaram de fora:
# sao o corpo de texto de todo PDF feito em LaTeX, e com elas qualquer tese
# ou artigo em prosa era classificado como livro de matematica.
_FONTES_MATEMATICA = ("cmmi", "cmsy", "cmex", "msam", "msbm", "mathematical",
                      "euclid", "mtsy", "mtmi", "wasy", "stixmath", "stixtwomath",
                      "mathjax", "xits", "asana", "latinmodernmath",
                      "latinmodern math", "cambriamath", "cambria math", "math")
_FONTES_SIMBOLO = ("symbol", "dingbat", "wingding", "marlett")
_FONTES_NAO_MONO = _FONTES_MATEMATICA + _FONTES_SIMBOLO


def _eh_matematica(span) -> bool:
    nome = span.get("font", "").lower()
    return any(x in nome for x in _FONTES_MATEMATICA)


def _eh_mono(span) -> bool:
    nome = span.get("font", "").lower()
    if any(x in nome for x in _FONTES_NAO_MONO):
        return False
    return bool(span.get("flags", 0) & 2 ** 3) or any(m in nome for m in _FONTES_MONO)


def _spans_mono(linha):
    """Textos dos spans monoespacados de uma linha (mencoes a codigo)."""
    saida = []
    for span in linha.get("spans", []):
        texto = span.get("text", "")
        if not texto.strip():
            continue
        if _eh_mono(span):
            saida.append(texto)
    return saida


def _juntar_linhas_visuais(linhas, largura_bloco):
    """Junta os pedacos que o extrator devolveu para uma MESMA linha da pagina.

    Texto justificado -- o caso de todo PDF gerado a partir de EPUB -- chega
    com cada palavra numa "linha" propria, porque o gerador posiciona palavra
    por palavra para esticar a linha ate a margem. Sem juntar antes de
    analisar, a regra de recuo de primeira linha dispara em cada palavra e o
    capitulo inteiro sai picado em paragrafos de uma palavra so:

        declarar
        uma
        variavel
        numero1

    Era o defeito mais visivel do leitor, e nao tinha nada a ver com o reflow
    em si: o texto ja chegava quebrado antes de qualquer decisao nossa.

    A folga horizontal limite existe para nao colar as duas colunas de uma
    pagina diagramada, que compartilham a faixa vertical mas nao a leitura.
    """
    if len(linhas) < 2:
        return linhas

    limite_folga = max(24.0, largura_bloco * 0.25)
    grupos = []
    for linha in sorted(linhas, key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0])):
        caixa = linha["bbox"]
        atual = grupos[-1] if grupos else None
        if atual is not None and _mesma_faixa(atual["bbox"], caixa, limite_folga):
            atual["linhas"].append(linha)
            atual["bbox"] = _uniao(atual["bbox"], caixa)
        else:
            grupos.append({"bbox": caixa, "linhas": [linha]})

    saida = []
    for grupo in grupos:
        pedacos = grupo["linhas"]
        if len(pedacos) == 1:
            saida.append(pedacos[0])
            continue
        pedacos.sort(key=lambda l: l["bbox"][0])
        spans, fim_anterior = [], None
        for pedaco in pedacos:
            atuais = [dict(s) for s in pedaco.get("spans", []) if s.get("text")]
            if not atuais:
                continue
            # O espaco entre as palavras foi embora junto com a quebra: sem
            # recoloca-lo, "declarar uma variavel" viraria "declararumavariavel".
            if spans and fim_anterior is not None:
                corpo = atuais[0].get("size") or 10.0
                if (pedaco["bbox"][0] - fim_anterior > corpo * 0.18
                        and not spans[-1]["text"].endswith(" ")
                        and not atuais[0]["text"].startswith(" ")):
                    spans[-1]["text"] += " "
            spans.extend(atuais)
            fim_anterior = pedaco["bbox"][2]
        nova = dict(pedacos[0])
        nova["spans"] = spans
        nova["bbox"] = grupo["bbox"]
        saida.append(nova)
    return saida


def _mesma_faixa(caixa_grupo, caixa, limite_folga) -> bool:
    """Se a caixa pertence a mesma linha visual do grupo."""
    altura = min(caixa_grupo[3] - caixa_grupo[1], caixa[3] - caixa[1])
    if altura <= 0:
        return False
    comum = min(caixa_grupo[3], caixa[3]) - max(caixa_grupo[1], caixa[1])
    if comum / altura < 0.6:
        return False
    # Distancia ate o fim do que ja foi juntado. Negativa quando se sobrepoem.
    return (caixa[0] - caixa_grupo[2]) <= limite_folga


def _metricas_da_linha(linha):
    """Retorna (tamanho_max, fracao_negrito, fracao_monoespacada) da linha."""
    maior = 0.0
    chars_negrito = 0
    chars_mono = 0
    chars_total = 0
    for span in linha.get("spans", []):
        n = len(span.get("text", ""))
        if n == 0:
            continue
        maior = max(maior, span.get("size", 0.0))
        chars_total += n
        flags = span.get("flags", 0)
        nome = span.get("font", "").lower()
        if flags & 2 ** 4 or "bold" in nome or "black" in nome or "heavy" in nome:
            chars_negrito += n
        if _eh_mono(span):
            chars_mono += n
    if not chars_total:
        return maior, 0.0, 0.0
    return maior, chars_negrito / chars_total, chars_mono / chars_total


# --------------------------------------------------------------- MOTOR PRINCIPAL

class MotorReflow:
    """Mantem as estatisticas de um documento para reusar entre paginas."""

    # Pagina digitalizada vira texto pelo OCR do Windows quando disponivel.
    # Atributo de classe para a interface poder desligar (e religar) para
    # todos os documentos de uma vez.
    ocr_ativo = True

    def __init__(self, documento: fitz.Document):
        self.doc = documento
        self.tamanho_corpo = 11.0
        self.recorrentes = set()
        # Logotipos e marcas d'agua: imagem que reaparece no mesmo lugar e no
        # mesmo tamanho pagina apos pagina nunca e ilustracao.
        self.imagens_recorrentes = set()
        self.predominante_latino = True
        # Perfil do livro: manda no realce de codigo e nas mencoes inline.
        # Sem isso, um romance com uma fonte mal marcada como monoespacada
        # aparecia todo pintado como se fosse manual de programacao.
        self.fracao_mono = 0.0
        self.render_detectado = "prosa"
        self.pontuacao_codigo = 0.0
        self.fracao_matematica = 0.0
        self.fracao_titulos = 0.0
        self.fracao_imagem = 0.0
        self.fracao_pag_ilustradas = 0.0
        self.fracao_duas_colunas = 0.0
        self.titulos_por_pagina = 0.0
        self.fracao_linhas_lista = 0.0
        self.fracao_passos = 0.0
        self.avisos_por_pagina = 0.0
        self.fracao_simbolos_mat = 0.0
        self.chars_por_pagina = 0.0
        self._votos_lingua = Counter()
        self._cache = {}
        self._analisar()

    @property
    def linguagem_dominante(self):
        """Linguagem mais votada pelos blocos ja lidos do livro.

        Trechos curtos ("manage.py runserver") nao se identificam sozinhos;
        herdam daqui."""
        if not self._votos_lingua:
            return None
        return self._votos_lingua.most_common(1)[0][0]

    def _classificar_renderizacao(self):
        """Escolhe COMO o livro deve ser exibido, nao sobre o que ele fala.

        A ordem importa: os tipos mais especificos sao testados primeiro, e o
        ultimo caso e sempre prosa. Cada teste usa medidas do proprio arquivo,
        nunca o titulo -- "Calculo I" pode ser uma apostila digitalizada e
        "Django" pode nao ter uma linha de codigo em texto.
        """
        # Quase sem texto e quase tudo imagem: quadrinho, album, scan.
        if self.fracao_pag_ilustradas >= 0.6 and self.chars_por_pagina < 700:
            return "hq"
        # Muita figura + duas colunas + texto medio: revista/diagramacao rica,
        # que o reflow destruiria.
        if (self.fracao_imagem >= 0.25 and self.fracao_duas_colunas >= 0.4
                and self.chars_por_pagina < 3000):
            return "revista"
        # Formula: fonte de formula (LaTeX) OU densidade de simbolos
        # matematicos (equacoes do Word/Docs, que usam Unicode comum).
        if self.fracao_matematica >= 0.06 or self.fracao_simbolos_mat >= 0.008:
            return "matematica"
        # Codigo de verdade: monoespacado E pontuando como linguagem.
        if 0.004 <= self.fracao_mono <= 0.60 and self.pontuacao_codigo >= 8.0:
            return "programacao"
        # Manual: avisos ("Atencao:", "Cuidado:") e passos numerados.
        if (self.avisos_por_pagina >= 0.8
                or (self.fracao_passos >= 0.08 and self.avisos_por_pagina >= 0.25)):
            return "manual"
        # Livro tecnico/didatico: secoes frequentes, muitas vezes com listas.
        if (self.titulos_por_pagina >= 1.5
                or (self.titulos_por_pagina >= 0.7 and self.fracao_linhas_lista >= 0.06)
                or self.fracao_titulos >= 0.16):
            return "tecnico"
        return "prosa"

    # ------------------------------------------------------------- diagnostico
    def palavras_quebradas(self, texto: str) -> list:
        """Palavras que vieram como identificador de glifo em vez de texto.

        A conta e por PALAVRA, nao pelo trecho inteiro: em "Capitulo 2
        Primeiros passos com Django" so uma palavra estava corrompida, o que
        diluia para 21% num limiar de bloco -- e o defeito passava batido.

        So vale para livros predominantemente latinos: num livro em cingales
        de verdade essas letras sao o conteudo, nao um defeito.
        """
        if not self.predominante_latino or not texto:
            return []
        # Saida barata: a esmagadora maioria dos blocos nao tem nada suspeito.
        if not _RE_IMPOSSIVEL.search(texto):
            return []
        achadas = []
        for palavra in re.split(r"\s+", texto or ""):
            letras = [c for c in palavra if c.isalpha()]
            if len(letras) < 2:
                continue
            quebradas = sum(1 for c in letras if _impossivel(c))
            if quebradas and quebradas / len(letras) >= _LIMIAR_ILEGIVEL:
                achadas.append(palavra)
        return achadas

    def texto_ilegivel(self, texto: str) -> bool:
        return bool(self.palavras_quebradas(texto))

    def limpar_quebradas(self, texto: str) -> str:
        """Remove as palavras corrompidas de um trecho longo demais para virar
        imagem. Melhor uma lacuna honesta que uma sequencia de simbolos."""
        quebradas = set(self.palavras_quebradas(texto))
        if not quebradas:
            return texto
        saida = [("…" if p in quebradas else p) for p in re.split(r"(\s+)", texto)]
        return _RE_ESPACOS.sub(" ", "".join(saida)).strip()

    # ---------------------------------------------------------------- analise
    def _analisar(self, amostra_max: int = 24):
        total = len(self.doc)
        if total == 0:
            return

        # Amostra distribuida: inicio, meio e fim tem diagramacoes diferentes.
        passo = max(1, total // amostra_max)
        indices = list(range(0, total, passo))[:amostra_max]

        pesos_tamanho = Counter()
        contagem_margem = Counter()
        contagem_imagem = Counter()
        paginas_validas = 0
        latinas = impossiveis = 0
        chars_mono = chars_todos = 0
        chars_matematica = chars_titulo = 0
        amostra_mono = []
        area_imagem = area_pagina = 0.0
        paginas_com_muita_imagem = paginas_duas_colunas = 0
        # (tamanho, negrito, texto) de cada linha: titulos so podem ser
        # contados depois que o corpo real do livro for conhecido.
        linhas_info = []
        simbolos_mat = 0

        for i in indices:
            try:
                pagina = self.doc.load_page(i)
                # Sem os BYTES das imagens: aqui so interessam geometria e
                # texto, e decodificar cada figura de 24 paginas era o passo
                # mais caro da abertura do livro.
                dados = _consertar(pagina.get_text("dict", flags=_FLAGS_SEM_IMAGEM, clip=_recorte(pagina)), pagina)
            except Exception:
                continue

            altura = dados.get("height") or pagina.rect.height or 1
            largura_pag = dados.get("width") or pagina.rect.width or 1
            paginas_validas += 1
            vistos_nesta = set()
            imagens_nesta = set()

            area_pagina += largura_pag * altura
            area_desta = 0.0
            for imagem in _imagens_da_pagina(pagina):
                caixa = imagem["bbox"]
                area_desta += _area(caixa)
                imagens_nesta.add(_assinatura_imagem(caixa, imagem))
            area_imagem += area_desta
            if area_desta > largura_pag * altura * 0.55:
                paginas_com_muita_imagem += 1
            blocos_txt = [b for b in dados.get("blocks", []) if b.get("type") == 0]
            if _layout_em_colunas(blocos_txt, largura_pag):
                paginas_duas_colunas += 1

            for bloco in dados.get("blocks", []):
                if bloco.get("type") != 0:
                    continue
                y0, y1 = bloco["bbox"][1], bloco["bbox"][3]
                na_margem = (y1 < altura * _ZONA_MARGEM or
                             y0 > altura * (1 - _ZONA_MARGEM))

                texto_bloco = []
                for linha in bloco.get("lines", []):
                    texto_linha = _texto_da_linha(linha)
                    texto_bloco.append(texto_linha)
                    if texto_linha.strip() and not na_margem and len(linhas_info) < 6000:
                        tam, negr, _m = _metricas_da_linha(linha)
                        linhas_info.append((tam or 0, negr, texto_linha.strip()))
                    for span in linha.get("spans", []):
                        conteudo = span.get("text", "")
                        n = len(conteudo.strip())
                        if n:
                            pesos_tamanho[round(span.get("size", 0), 1)] += n
                            chars_todos += n
                            if _eh_mono(span):
                                chars_mono += n
                                if len(amostra_mono) < 4000:
                                    amostra_mono.append(conteudo)
                            if _eh_matematica(span):
                                chars_matematica += n
                        for caractere in conteudo:
                            if _eh_simbolo_matematico(caractere):
                                simbolos_mat += 1
                            if not caractere.isalpha():
                                continue
                            if _impossivel(caractere):
                                impossiveis += 1
                            elif ord(caractere) < 0x0250:
                                latinas += 1

                inteiro = _limpar(" ".join(texto_bloco))
                if na_margem and 0 < len(inteiro) <= 120:
                    vistos_nesta.add(_normalizar_assinatura(inteiro))

            for assinatura in vistos_nesta:
                contagem_margem[assinatura] += 1
            for assinatura in imagens_nesta:
                contagem_imagem[assinatura] += 1

        if pesos_tamanho:
            # O tamanho que cobre mais caracteres e, por definicao, o corpo.
            self.tamanho_corpo = max(pesos_tamanho.items(), key=lambda kv: kv[1])[0]
            # Titulo = maior que o corpo REAL. Medido durante o laco, o corpo
            # ainda era o padrao de 11 pt: um livro em 14 pt virava "tecnico"
            # (tudo parecia titulo) e um em 9 pt perdia os titulos de verdade.
            chars_titulo = sum(n for tamanho, n in pesos_tamanho.items()
                               if tamanho >= self.tamanho_corpo * 1.2)

        if paginas_validas >= 3:
            minimo = max(2, int(paginas_validas * _LIMIAR_RECORRENCIA))
            self.recorrentes = {a for a, c in contagem_margem.items() if c >= minimo}
            # EPUB nao tem cabecalho nem rodape: quem pagina e o MuPDF. O que
            # se "repete na margem" ali sao titulos de capitulo ("Capitulo 1",
            # "Capitulo 2" -> mesma assinatura) -- e eles sumiam do livro.
            from services.pdf_service import veio_de_epub
            if veio_de_epub(self.doc):
                self.recorrentes = set()
            # O limiar da imagem e mais alto que o do texto: cabecalho se repete
            # em toda pagina, mas uma ilustracao legitima pode reaparecer em
            # varias (o mesmo diagrama retomado a cada secao de um capitulo).
            minimo_img = max(3, int(paginas_validas * _LIMIAR_IMAGEM_RECORRENTE))
            self.imagens_recorrentes = {a for a, c in contagem_imagem.items()
                                        if c >= minimo_img}

        total_letras = latinas + impossiveis
        self.predominante_latino = (not total_letras) or (latinas / total_letras) > 0.7

        # Dois sinais precisam concordar para o livro ser "tecnico":
        #   1. existe texto monoespacado em quantidade plausivel;
        #   2. esse texto PARECE codigo de alguma linguagem.
        # So o primeiro sinal classificaria errado livros de matematica,
        # dicionarios e qualquer obra com tabelas em fonte de maquina.
        if chars_todos:
            self.fracao_mono = chars_mono / chars_todos
        texto_mono = " ".join(amostra_mono)[:4000]
        pontos = realce.pontuar(texto_mono)
        self.pontuacao_codigo = max(pontos.values()) if pontos else 0.0
        if pontos:
            self._votos_lingua.update(pontos)
        # Sinais que separam COMO o livro deve ser exibido.
        if chars_todos:
            self.fracao_matematica = chars_matematica / chars_todos
            self.fracao_titulos = chars_titulo / chars_todos
        if area_pagina:
            self.fracao_imagem = area_imagem / area_pagina
        # Estrutura do texto: quantos titulos por pagina, quanto e lista,
        # passo numerado ou aviso ("Atencao:"). E o que separa prosa, livro
        # tecnico e manual -- a fracao de CARACTERES em titulo nao serve,
        # porque titulo e curto por natureza.
        if paginas_validas and linhas_info:
            corpo = self.tamanho_corpo
            titulos = sum(1 for tam, negr, txt in linhas_info
                          if len(txt) <= 120 and (tam >= corpo * 1.2
                                                  or (negr > 0.8 and len(txt) <= 80
                                                      and not txt.endswith(".")
                                                      and not _RE_AVISO.match(txt))))
            self.titulos_por_pagina = titulos / paginas_validas
            total_linhas = len(linhas_info)
            self.fracao_linhas_lista = sum(
                1 for _t, _n, txt in linhas_info if _RE_MARCADOR.match(txt)) / total_linhas
            self.fracao_passos = sum(
                1 for _t, _n, txt in linhas_info if _RE_PASSO.match(txt)) / total_linhas
            self.avisos_por_pagina = sum(
                1 for _t, _n, txt in linhas_info if _RE_AVISO.match(txt)) / paginas_validas
        if chars_todos and self.predominante_latino:
            self.fracao_simbolos_mat = simbolos_mat / chars_todos
        if paginas_validas:
            self.fracao_pag_ilustradas = paginas_com_muita_imagem / paginas_validas
            self.fracao_duas_colunas = paginas_duas_colunas / paginas_validas
            self.chars_por_pagina = chars_todos / paginas_validas
        self.render_detectado = self._classificar_renderizacao()

    # ---------------------------------------------------------------- extracao
    def pagina(self, numero: int) -> PaginaReflow:
        if numero in self._cache:
            return self._cache[numero]
        resultado = self._extrair(numero)
        # Cache curto: paginas vizinhas sao as unicas com chance de reuso.
        if len(self._cache) > 12:
            self._cache.clear()
        self._cache[numero] = resultado
        return resultado

    def invalidar_cache(self):
        self._cache.clear()

    def _extrair(self, numero: int) -> PaginaReflow:
        resultado = PaginaReflow(numero=numero)
        try:
            pagina = self.doc.load_page(numero)
            # Toda figura e rasterizada da propria pagina (veja `_rasterizar`),
            # entao os bytes embutidos nao servem para nada aqui -- e pedi-los
            # obrigaria a decodificar cada imagem duas vezes.
            dados = _consertar(pagina.get_text("dict", flags=_FLAGS_SEM_IMAGEM, clip=_recorte(pagina)), pagina)
        except Exception as erro:
            resultado.blocos.append(Bloco("aviso", f"Nao foi possivel ler esta pagina.\n{erro}"))
            return resultado

        largura = dados.get("width") or pagina.rect.width or 1
        altura = dados.get("height") or pagina.rect.height or 1

        blocos_texto = [b for b in dados.get("blocks", [])
                        if b.get("type") == 0 and b.get("lines")]
        blocos_imagem = _imagens_da_pagina(pagina)

        chars = sum(len(_texto_da_linha(l)) for b in blocos_texto for l in b.get("lines", []))

        # Pagina digitalizada: nao ha texto para refluir, entao mostramos a
        # imagem renderizada em vez de uma pagina em branco silenciosa.
        if chars < _MIN_CHARS_PAGINA:
            resultado.digitalizada = True
            # Com OCR, a digitalizacao vira texto de verdade: reflui, justifica,
            # busca, grifa e le em voz alta. Os blocos reconhecidos passam pelo
            # MESMO `_blocos_do_bloco` do texto nativo (paragrafos, titulos,
            # hifenizacao, numero de pagina). Import tardio: o WinRT so e
            # carregado quando aparece a primeira pagina digitalizada.
            if self.ocr_ativo:
                reconhecidos = []
                try:
                    from services import ocr
                    if ocr.disponivel():
                        for bloco in ocr.blocos_da_pagina(pagina, self.tamanho_corpo):
                            reconhecidos.extend(self._blocos_do_bloco(bloco, altura))
                        texto = "\n\n".join(b.texto for b in reconhecidos if b.texto)
                        # Pouco texto ou letras soltas: foto, ilustracao ou
                        # quadrinho -- a imagem original e mais fiel.
                        if len(texto) < _MIN_CHARS_PAGINA or not ocr.parece_texto(texto):
                            reconhecidos = []
                except Exception:
                    reconhecidos = []
                if reconhecidos:
                    resultado.blocos.append(Bloco(
                        "aviso", "Texto reconhecido por OCR — pode conter erros."))
                    resultado.blocos.extend(reconhecidos)
                    resultado.texto_puro = texto
                    resultado.palavras = len(texto.split())
                    return resultado
            resultado.blocos.append(Bloco(
                "aviso",
                "Pagina sem texto selecionavel (imagem ou digitalizacao). Exibindo o original."
            ))
            imagem = self._renderizar_pagina(pagina)
            if imagem:
                resultado.blocos.append(imagem)
            return resultado

        # As figuras entram na fila JUNTO com o texto e sao ordenadas pela
        # mesma geometria -- e o que faz a ilustracao cair onde o autor a
        # colocou, em vez de num amontoado no rodape da pagina.
        figuras = self._figuras_da_pagina(pagina, blocos_imagem, blocos_texto,
                                          largura, altura)
        engolidos = set()
        for figura in figuras:
            engolidos.update(id(b) for b in figura["consumidos"])

        fila = [(b["bbox"], ("texto", b)) for b in blocos_texto
                if id(b) not in engolidos]
        fila += [(f["bbox"], ("figura", f)) for f in figuras]

        duas_colunas = self._duas_colunas(blocos_texto, largura)
        paragrafos = []
        ordenados = self._unir_linhas_soltas(
            list(self._ordenar_leitura(fila, largura, duas_colunas)))
        for _caixa, (especie, item) in ordenados:
            if especie == "figura":
                figura = self._rasterizar(pagina, item["bbox"])
                if figura is not None:
                    paragrafos.append(figura)
                continue

            bloco = item
            # Texto que veio corrompido da fonte nao tem conserto confiavel:
            # em vez de exibir simbolos aleatorios, recortamos a area original
            # da pagina e mostramos como o livro realmente e.
            bruto = " ".join(_texto_da_linha(l) for l in bloco.get("lines", []))
            # Bloco curto (tipicamente um titulo) vira imagem inteira. Num
            # bloco longo, virar imagem destruiria o refluxo do paragrafo --
            # ali as palavras corrompidas apenas somem.
            if self.texto_ilegivel(bruto) and len(bruto) <= _MAX_CHARS_RECORTE:
                recorte = self._recortar(pagina, bloco["bbox"])
                if recorte is not None:
                    paragrafos.append(recorte)
                    continue
            paragrafos.extend(self._blocos_do_bloco(bloco, altura))

        _marcar_legendas(paragrafos)
        paragrafos = _limpar_numeracao_de_codigo(paragrafos)
        resultado.blocos = paragrafos
        resultado.texto_puro = "\n\n".join(b.texto for b in paragrafos if b.texto)
        resultado.palavras = len(resultado.texto_puro.split())
        return resultado

    # ------------------------------------------------------------ ordem leitura
    def _duas_colunas(self, blocos_texto, largura_pagina) -> bool:
        """Se a pagina esta diagramada em duas colunas.

        Decidido SO pelo texto, de proposito: uma figura larga atravessando as
        duas colunas e comum, e conta-la como bloco derrubaria a deteccao
        justamente nas paginas mais ilustradas.
        """
        return _layout_em_colunas(blocos_texto, largura_pagina)

    def _unir_linhas_soltas(self, ordenados):
        """Junta blocos que sao so a CONTINUACAO do bloco anterior.

        PDFs exportados do Word e de alguns geradores web entregam cada linha
        como um bloco proprio. Cada bloco virava um "paragrafo": a frase
        quebrava no meio da tela e uma linha curta ("linha 6.") chegava a ser
        tomada por titulo. Unidas aqui, as linhas passam pelas mesmas regras
        de quebra de paragrafo de qualquer bloco (recuo, fim de frase, salto
        vertical, marcador de lista).
        """
        saida = []
        for caixa, (especie, item) in ordenados:
            anterior = saida[-1] if saida else None
            if (especie == "texto" and anterior is not None
                    and anterior[1][0] == "texto"
                    and self._continua(anterior[1][1], item)):
                bloco = anterior[1][1]
                unido = dict(bloco)
                unido["lines"] = list(bloco.get("lines", [])) + list(item.get("lines", []))
                unido["bbox"] = _uniao(bloco["bbox"], item["bbox"])
                saida[-1] = (unido["bbox"], ("texto", unido))
                continue
            saida.append((caixa, (especie, item)))
        return saida

    def _continua(self, bloco_a, bloco_b) -> bool:
        linhas_a = bloco_a.get("lines") or []
        linhas_b = bloco_b.get("lines") or []
        if not linhas_a or not linhas_b:
            return False
        ultima, primeira = linhas_a[-1], linhas_b[0]
        ta, _na, ma = _metricas_da_linha(ultima)
        tb, _nb, mb = _metricas_da_linha(primeira)
        # Codigo fica de fora: ali a quebra de linha e conteudo. Excecao: uma
        # palavra de codigo sozinha continuando a frase ("...com a execucao
        # do comando" + "makemigrations.") -- virava uma caixa de codigo.
        if ma > 0.65 and mb > 0.65:
            # Duas metades da mesma listagem separadas por uma linha em
            # branco: viravam duas caixas, cada uma com seu rotulo.
            altura = max(1.0, ultima["bbox"][3] - ultima["bbox"][1])
            vao = primeira["bbox"][1] - ultima["bbox"][3]
            margem_a = min(l["bbox"][0] for l in linhas_a)
            return (-altura * 0.5 <= vao <= altura * 2.6
                    and primeira["bbox"][0] >= margem_a - 3)
        if ma > 0.65:
            return False
        if mb > 0.65:
            texto_b = _texto_da_linha(primeira).strip()
            texto_a = _texto_da_linha(ultima).rstrip()
            if not (len(linhas_b) == 1 and len(texto_b) <= 60
                    and texto_b.endswith((".", ",", ";", ")"))
                    and not texto_a.endswith((":", "."))):
                return False
        ta, tb = ta or self.tamanho_corpo, tb or self.tamanho_corpo
        if abs(ta - tb) > max(0.8, ta * 0.12):
            return False
        ya1 = ultima["bbox"][3]
        yb0 = primeira["bbox"][1]
        altura = max(1.0, ultima["bbox"][3] - ultima["bbox"][1])
        # Proxima linha logo abaixo (nem sobreposta, nem depois de um respiro
        # de paragrafo grande).
        if not (-altura * 0.5 <= yb0 - ya1 <= altura * 0.9):
            return False
        # Mesma coluna: as margens esquerdas se encontram dentro de um recuo
        # de primeira linha / de item de lista.
        xa0 = min(l["bbox"][0] for l in linhas_a)
        xa1 = max(l["bbox"][2] for l in linhas_a)
        largura = max(1.0, xa1 - xa0)
        xb0 = primeira["bbox"][0]
        # Recuo de primeira linha: o bloco A e so a primeira linha, recuada;
        # a margem do paragrafo fica alguns corpos a esquerda dela.
        recuo_permitido = largura * 0.06
        if len(linhas_a) == 1:
            recuo_permitido = max(recuo_permitido, self.tamanho_corpo * 4.5)
        if xb0 < xa0 - recuo_permitido or xb0 > xa0 + largura * 0.25:
            return False
        if primeira["bbox"][2] > xa1 + largura * 0.25:
            return False
        return True

    def _ordenar_leitura(self, itens, largura_pagina, duas_colunas):
        """Ordena pares (caixa, carga) na ordem em que se le a pagina."""
        meio = largura_pagina / 2

        def chave(par):
            caixa = par[0]
            if not duas_colunas:
                return (0, round(caixa[1], 1), caixa[0])
            centro = (caixa[0] + caixa[2]) / 2
            coluna = 0 if centro < meio else 1
            # O que atravessa as duas colunas (titulo de secao, figura larga)
            # se le antes -- e vai para o topo da coluna da esquerda.
            if caixa[0] < meio * 0.92 and caixa[2] > meio * 1.08:
                coluna = 0
            return (coluna, round(caixa[1], 1), caixa[0])

        return sorted(itens, key=chave)

    # ----------------------------------------------------- linhas -> paragrafos
    def _blocos_do_bloco(self, bloco, altura_pagina) -> List[Bloco]:
        linhas = []
        brutas = [l for l in bloco.get("lines", [])
                  if l.get("bbox") and _texto_da_linha(l).strip()]
        largura_bruta = max(1.0, bloco["bbox"][2] - bloco["bbox"][0])
        for linha in _juntar_linhas_visuais(brutas, largura_bruta):
            texto = _texto_da_linha(linha)
            if not texto.strip():
                continue
            tamanho, negrito, mono = _metricas_da_linha(linha)
            bbox = linha.get("bbox", bloco["bbox"])
            linhas.append({
                "spans_mono": _spans_mono(linha),
                "texto": texto,
                "tamanho": tamanho or self.tamanho_corpo,
                "negrito": negrito,
                "mono": mono,
                "x0": bbox[0],
                "x1": bbox[2],
                "y0": bbox[1],
                "y1": bbox[3],
            })

        if not linhas:
            return []

        # Bloco de codigo: quebras de linha e indentacao SAO o conteudo, entao
        # ele escapa do reflow. Sem isso, listagens de codigo e arvores de
        # diretorio viravam uma papa ilegivel de tokens.
        chars = sum(len(l["texto"]) for l in linhas) or 1
        if sum(l["mono"] * len(l["texto"]) for l in linhas) / chars > 0.65:
            # Linha em branco da listagem original volta como linha vazia: o
            # extrator descarta a linha vazia, mas o vao vertical denuncia.
            alturas_cod = [l["y1"] - l["y0"] for l in linhas if l["y1"] > l["y0"]]
            passo = statistics.median(alturas_cod) if alturas_cod else self.tamanho_corpo
            saida_linhas = [linhas[0]["texto"].rstrip()]
            for anterior, linha in zip(linhas, linhas[1:]):
                if linha["y0"] - anterior["y1"] > passo * 0.9:
                    saida_linhas.append("")
                saida_linhas.append(linha["texto"].rstrip())
            corpo = "\n".join(saida_linhas)
            return [Bloco("codigo", corpo)] if corpo.strip() else []

        # Descarte de cabecalho / rodape recorrente.
        y0, y1 = bloco["bbox"][1], bloco["bbox"][3]
        na_margem = (y1 < altura_pagina * _ZONA_MARGEM or
                     y0 > altura_pagina * (1 - _ZONA_MARGEM))
        if na_margem:
            inteiro = _limpar(" ".join(l["texto"] for l in linhas))
            if len(inteiro) <= 120:
                if _normalizar_assinatura(inteiro) in self.recorrentes:
                    return []
                if _RE_SO_NUMERO.match(inteiro):    # numero de pagina solto
                    return []

        alturas = [l["y1"] - l["y0"] for l in linhas if l["y1"] > l["y0"]]
        altura_media = statistics.median(alturas) if alturas else self.tamanho_corpo
        x0_bloco = min(l["x0"] for l in linhas)
        x1_bloco = max(l["x1"] for l in linhas)
        largura_bloco = max(1.0, x1_bloco - x0_bloco)

        grupos = []
        atual = [linhas[0]]

        for anterior, linha in zip(linhas, linhas[1:]):
            quebra = False

            # 1. Salto vertical maior que o entrelinhas normal.
            if linha["y0"] - anterior["y1"] > altura_media * 0.75:
                quebra = True
            # A linha anterior chegou a margem direita sem fechar a frase: esta
            # e a continuacao dela, mesmo que o autor tenha posto a palavra em
            # negrito ("na **linha 6**.") -- antes isso partia a frase e a
            # sobra curta virava titulo.
            continua_frase = (
                anterior["x1"] >= x1_bloco - largura_bloco * 0.12
                and not anterior["texto"].rstrip().endswith((".", "!", "?", ":")))
            # 2. Mudanca real de corpo tipografico (titulo comecando).
            if (not continua_frase and abs(linha["tamanho"] - anterior["tamanho"])
                    > max(0.8, anterior["tamanho"] * 0.12)):
                quebra = True
            # 3. Peso muda de normal para negrito e vice-versa.
            if not continua_frase and (linha["negrito"] > 0.6) != (anterior["negrito"] > 0.6):
                quebra = True
            # 4. Recuo de primeira linha marcando novo paragrafo -- exceto o
            #    recuo pendente de um item de lista: as linhas seguintes do
            #    item alinham com o texto depois do marcador, mais a direita.
            item_de_lista = (_RE_MARCADOR.match(atual[0]["texto"])
                             and linha["x0"] > atual[0]["x0"] + 2)
            if (not item_de_lista and len(atual) > 0
                    and linha["x0"] - x0_bloco > largura_bloco * 0.045
                    and linha["x0"] - anterior["x0"] > largura_bloco * 0.02):
                quebra = True
            # 5. Linha anterior terminou a frase sem preencher a largura.
            if (anterior["texto"].rstrip().endswith((".", "!", "?", ":", '"'))
                    and anterior["x1"] < x1_bloco - largura_bloco * 0.12):
                quebra = True
            # 6. Nova linha comeca com marcador de lista.
            if _RE_MARCADOR.match(linha["texto"]):
                quebra = True

            if quebra:
                grupos.append(atual)
                atual = [linha]
            else:
                atual.append(linha)

        grupos.append(atual)

        saida = []
        for grupo in grupos:
            item = self._montar_bloco(grupo, x0_bloco, largura_bloco)
            if item:
                saida.append(item)
        return saida

    def _montar_bloco(self, linhas, x0_bloco, largura_bloco) -> Optional[Bloco]:
        # Juncao com desfazimento de hifenizacao de fim de linha.
        partes = []
        hifen_final = False
        for indice, linha in enumerate(linhas):
            texto = linha["texto"].rstrip()
            ultima = indice == len(linhas) - 1
            # Hifen condicional (U+00AD): o EPUB marca as silabas com ele e o
            # MuPDF o deixa no fim da linha quando parte a palavra ("Co\xad" +
            # "mo"). E invisivel -- antes so era apagado DEPOIS da juncao, e a
            # palavra virava "Co mo".
            # U+2011 (hifen inseparavel) no FIM da linha e o mesmo caso: e como
            # o MuPDF desenha essa quebra ao converter o EPUB para PDF -- um
            # hifen que nao deixa quebrar nunca termina linha por vontade do autor.
            if texto.endswith(("­", "‑")):
                if ultima:
                    hifen_final = True
                partes.append(texto[:-1])
                continue
            # Qualquer letra antes do hifen -- inclusive ligaduras ("enﬁ-"
            # + "ou"): so a-z deixava a palavra partida em dois.
            if re.search(r"[^\W\d_]-$", texto):
                if ultima:
                    hifen_final = True
                    partes.append(texto + " ")
                    continue
                proxima = linhas[indice + 1]["texto"].lstrip()
                if proxima[:1].islower():
                    partes.append(texto[:-1])     # cola sem hifen nem espaco
                    continue
            partes.append(texto + " ")

        texto = _limpar("".join(partes))
        if not texto:
            return None
        texto = self.limpar_quebradas(texto)
        if not texto:
            return None

        # Onde cada trecho monoespacado foi parar depois do reflow. Procurar o
        # texto do span (em vez de rastrear indices durante a juncao) sobrevive
        # a desidifenizacao e a normalizacao de espacos.
        faixas_mono = []
        cursor = 0
        for pedaco in (p for l in linhas for p in l.get("spans_mono", [])):
            alvo = _limpar(pedaco)
            if len(alvo) < 2:
                continue
            posicao = texto.find(alvo, cursor)
            if posicao < 0:
                continue
            faixas_mono.append((posicao, posicao + len(alvo)))
            cursor = posicao + len(alvo)

        tamanho = max(l["tamanho"] for l in linhas)
        negrito = max(l["negrito"] for l in linhas)
        corpo = self.tamanho_corpo
        recuo = min(l["x0"] for l in linhas) - x0_bloco

        # ---- classificacao
        if _RE_MARCADOR.match(texto):
            return Bloco("lista", texto, mono=faixas_mono, hifen_final=hifen_final)

        if tamanho >= corpo * 1.55:
            return Bloco("titulo", texto, nivel=1)
        if tamanho >= corpo * 1.22:
            return Bloco("titulo", texto, nivel=2)
        if negrito > 0.75 and len(texto) < 110 and tamanho >= corpo * 0.98:
            return Bloco("titulo", texto, nivel=3)
        letras = [c for c in texto if c.isalpha()]
        if letras and len(texto) < 80 and sum(c.isupper() for c in letras) / len(letras) > 0.85:
            return Bloco("titulo", texto, nivel=3)

        if tamanho <= corpo * 0.82:
            return Bloco("nota", texto)

        # Bloco inteiro recuado = citacao.
        if recuo > largura_bloco * 0.08 and len(linhas) > 1:
            return Bloco("citacao", texto, mono=faixas_mono, hifen_final=hifen_final)

        return Bloco("paragrafo", texto, mono=faixas_mono, hifen_final=hifen_final)

    # ------------------------------------------------------------------ figuras
    def _figuras_da_pagina(self, pagina, blocos_imagem, blocos_texto,
                           largura_pag, altura_pag) -> List[dict]:
        """Regioes da pagina que valem como ilustracao, na forma
        {"bbox": ..., "consumidos": [blocos de texto que ficam dentro dela]}.

        Junta as duas origens possiveis -- imagem embutida e desenho vetorial
        -- porque para quem le nao ha diferenca nenhuma entre as duas.
        """
        figuras = self._figuras_embutidas(blocos_imagem, largura_pag, altura_pag)
        ocupadas = [f["bbox"] for f in figuras]
        figuras.extend(self._figuras_vetoriais(pagina, blocos_texto, ocupadas,
                                               largura_pag, altura_pag))
        return figuras

    def _figuras_embutidas(self, blocos_imagem, largura_pag, altura_pag) -> List[dict]:
        """Imagens do arquivo, ja sem icone nem marca d'agua e ja fundidas."""
        candidatas = []
        for bloco in blocos_imagem:
            caixa = tuple(float(v) for v in bloco["bbox"])
            largura_pt = caixa[2] - caixa[0]
            altura_pt = caixa[3] - caixa[1]
            if largura_pt < _FIGURA_MIN_PT or altura_pt < _FIGURA_MIN_PT:
                continue
            px_l = int(bloco.get("width") or 0)
            px_a = int(bloco.get("height") or 0)
            if px_l and px_a and (px_l < _IMAGEM_MIN_PX or px_a < _IMAGEM_MIN_PX):
                continue
            # Papel de parede e moldura de pagina: nunca sao a ilustracao.
            if largura_pt * altura_pt > largura_pag * altura_pag * _FIGURA_MAX_FRACAO:
                continue
            if _assinatura_imagem(caixa, bloco) in self.imagens_recorrentes:
                continue
            candidatas.append((caixa, None))

        return [{"bbox": grupo["bbox"], "consumidos": []}
                for grupo in _agrupar_caixas(candidatas, _FOLGA_UNIAO)]

    def _figuras_vetoriais(self, pagina, blocos_texto, ocupadas,
                           largura_pag, altura_pag) -> List[dict]:
        """Diagramas desenhados com tracos, e nao com uma imagem.

        Fluxograma, grafico de barras, esquema de arquitetura, arvore de
        diretorios com setas: o extrator de texto so ve os ROTULOS, e ate aqui
        eles caiam soltos no meio da prosa -- palavras sem frase entre dois
        paragrafos. Rasterizando a regiao inteira o desenho volta completo e
        os rotulos saem do fluxo de texto.

        Os limiares sao conservadores porque o erro caro e o oposto: virar
        imagem um paragrafo que tinha uma caixa de fundo atras dele custaria o
        reflow, a busca e o grifo daquele trecho.
        """
        try:
            desenhos = pagina.get_drawings()
        except Exception:
            return []
        if not desenhos or len(desenhos) > _MAX_TRACOS:
            return []

        tracos = []
        for desenho in desenhos:
            retangulo = desenho.get("rect")
            if retangulo is None:
                continue
            largura_pt = float(retangulo.width)
            altura_pt = float(retangulo.height)
            # Filete, sublinhado, regua de tabela e borda de celula.
            if largura_pt < _TRACO_MIN_PT or altura_pt < _TRACO_MIN_PT:
                continue
            # Moldura da pagina inteira.
            if (largura_pt > largura_pag * 0.95 and altura_pt > altura_pag * 0.95):
                continue
            if _eh_fundo_de_texto(desenho, largura_pt, altura_pt, largura_pag,
                                  self.tamanho_corpo):
                continue
            tracos.append(((float(retangulo.x0), float(retangulo.y0),
                            float(retangulo.x1), float(retangulo.y1)), None))

        if len(tracos) < 2:
            return []

        figuras = []
        for grupo in _agrupar_caixas(tracos, _FOLGA_UNIAO):
            caixa = grupo["bbox"]
            largura_pt = caixa[2] - caixa[0]
            altura_pt = caixa[3] - caixa[1]
            if largura_pt < _FIGURA_VETOR_MIN_PT or altura_pt < _FIGURA_VETOR_MIN_PT:
                continue
            if largura_pt * altura_pt > largura_pag * altura_pag * _FIGURA_MAX_FRACAO:
                continue
            # Ja coberto por uma imagem embutida: o desenho ali por cima e
            # seta ou moldura da propria figura.
            if any(_fracao_dentro(caixa, area) > 0.7 for area in ocupadas):
                continue

            dentro, chars, mono = self._texto_na_area(blocos_texto, caixa)
            if dentro:
                # Engolir texto exige prova de que aquilo e mesmo um desenho.
                if grupo["partes"] < _MIN_TRACOS_COM_TEXTO:
                    continue
                if chars > _MAX_CHARS_FIGURA or mono > _MAX_MONO_FIGURA:
                    continue
            figuras.append({"bbox": caixa, "consumidos": dentro})
        return figuras

    def _texto_na_area(self, blocos_texto, caixa):
        """(blocos contidos, caracteres, fracao monoespacada) dentro da caixa."""
        dentro = []
        chars = mono = 0
        for bloco in blocos_texto:
            if _fracao_dentro(bloco["bbox"], caixa) < 0.8:
                continue
            dentro.append(bloco)
            for linha in bloco.get("lines", []):
                for span in linha.get("spans", []):
                    n = len(span.get("text", ""))
                    chars += n
                    if _eh_mono(span):
                        mono += n
        return dentro, chars, (mono / chars if chars else 0.0)

    def _rasterizar(self, pagina, caixa, folga: float = 3.0) -> Optional[Bloco]:
        """Desenha a regiao da pagina como PNG.

        Rasterizar em vez de reaproveitar os bytes embutidos resolve de uma so
        vez quatro problemas: transparencia com mascara (que sairia como
        retangulo preto), figura fatiada em varios XObjects, rotulo de eixo
        escrito em texto por cima do grafico, e desenho puramente vetorial --
        que nem bytes tem.
        """
        try:
            alvo = fitz.Rect(caixa)
            alvo = fitz.Rect(alvo.x0 - folga, alvo.y0 - folga,
                             alvo.x1 + folga, alvo.y1 + folga) & pagina.rect
            if alvo.is_empty or alvo.width < 4 or alvo.height < 4:
                return None
            # Resolucao proporcional ao tamanho de exibicao: uma figura
            # estreita precisa de mais aumento que uma de pagina inteira para
            # chegar legivel a tela.
            escala = min(_FIGURA_ESCALA_MAX,
                         max(1.0, _FIGURA_PX_ALVO / max(1.0, alvo.width)))
            pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala), clip=alvo)
            return Bloco("imagem", imagem=pix.tobytes("png"),
                         largura_img=pix.width, altura_img=pix.height)
        except Exception as erro:
            print(f"[Reflow] Falha ao rasterizar figura: {erro}")
            return None

    def pagina_inteira(self, numero: int, escala: float = 2.2):
        """A pagina como imagem, sem refluir.

        Serve a HQ e revista: ali a diagramacao E o conteudo, e remontar o
        texto em coluna destruiria a obra em vez de melhorar a leitura.
        """
        try:
            pagina = self.doc.load_page(numero)
            pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala))
            return Bloco("imagem", imagem=pix.tobytes("png"),
                         largura_img=pix.width, altura_img=pix.height)
        except Exception as erro:
            print(f"[Reflow] Falha ao renderizar pagina inteira: {erro}")
            return None

    def _recortar(self, pagina, bbox, escala: float = 3.0) -> Optional[Bloco]:
        """Recorta um pedaco da pagina original como imagem em tons de cinza.

        Sai em cinza de proposito: a interface usa o desenho como mascara e o
        pinta com a cor do tema, senao um titulo preto sobre papel branco
        apareceria como um retangulo claro no meio da leitura escura.
        """
        try:
            retangulo = fitz.Rect(bbox)
            folga = 2
            retangulo = fitz.Rect(retangulo.x0 - folga, retangulo.y0 - folga,
                                  retangulo.x1 + folga, retangulo.y1 + folga)
            pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala),
                                    clip=retangulo, colorspace=fitz.csGRAY)
            if pix.width < 4 or pix.height < 4:
                return None
            altura_pt = max(1.0, retangulo.height)
            return Bloco("imagem", imagem=pix.tobytes("png"),
                         largura_img=pix.width, altura_img=pix.height,
                         recolorir=True,
                         altura_em_corpos=altura_pt / max(1.0, self.tamanho_corpo))
        except Exception as erro:
            print(f"[Reflow] Falha ao recortar bloco ilegivel: {erro}")
            return None

    def _renderizar_pagina(self, pagina, escala: float = 2.0) -> Optional[Bloco]:
        try:
            pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala))
            return Bloco("imagem", imagem=pix.tobytes("png"),
                         largura_img=pix.width, altura_img=pix.height)
        except Exception:
            return None
