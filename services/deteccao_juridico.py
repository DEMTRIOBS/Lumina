# services/deteccao_juridico.py
"""Estrutura de textos juridicos brasileiros e datas historicas.

Os dois assuntos moram no mesmo modulo porque o problema e o mesmo: varrer
texto corrido e devolver FAIXAS de caractere (inicio/fim) para a interface
pintar, dobrar ou indexar. Nada aqui altera o texto.

A regra que guia todas as decisoes: falso positivo e pior que falso negativo.
Um livro de historia cheio de "1500" que na verdade e numero de pagina, ou um
"R$ 1.822,00" virando o ano da Independencia, destroi a confianca do leitor na
linha do tempo. Por isso cada padrao carrega guardas de contexto e os padroes
mais especificos consomem a faixa antes dos mais genericos.
"""
import re

# ---------------------------------------------------------------- NUMERO ROMANO
# Incisos e seculos usam romanos, e "I" / "V" / "C" tambem sao letras comuns.
# Validar a grafia do romano corta boa parte dos enganos.
_RE_ROMANO = re.compile(
    r"^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$"
)
_VALOR_ROMANO = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _romano_valido(texto: str) -> bool:
    """True quando o texto e um romano bem formado e nao vazio."""
    return bool(texto) and bool(_RE_ROMANO.match(texto.upper()))


def _romano_para_inteiro(texto: str) -> int:
    """Converte um romano ja validado em inteiro."""
    total = 0
    anterior = 0
    for letra in reversed(texto.upper()):
        valor = _VALOR_ROMANO[letra]
        total += -valor if valor < anterior else valor
        anterior = max(anterior, valor)
    return total


def _limpo(texto: str) -> str:
    """Colapsa espacos e quebras de linha -- rotulos vem de PDF, vem sujos."""
    return " ".join(texto.split())


class _Reserva:
    """Marca faixas ja reivindicadas para os padroes nao se atropelarem.

    Mesma ideia usada no realce de sintaxe: quem chega primeiro (o padrao mais
    especifico) fica com a faixa, e o generico so pega o que sobrou.
    """

    def __init__(self, tamanho: int):
        self._ocupado = bytearray(tamanho)
        self.achados = []

    def livre(self, inicio: int, fim: int) -> bool:
        return fim > inicio and not any(self._ocupado[inicio:fim])

    def marcar(self, inicio: int, fim: int, item: dict) -> bool:
        if not self.livre(inicio, fim):
            return False
        self._ocupado[inicio:fim] = b"\x01" * (fim - inicio)
        self.achados.append(item)
        return True

    def ordenados(self) -> list:
        return sorted(self.achados, key=lambda d: (d["inicio"], -d["fim"]))


# ==============================================================================
# JURIDICO
# ==============================================================================

# "Art. 5o", "Art. 5º", "Artigo 5o", "Art 5", "Art. 1.234", "Art. 5o-A".
# O ordinal vem colado no numero; exigir isso evita engolir o artigo "o" da
# frase seguinte em construcoes como "Art. 5 o texto diz".
_RE_ARTIGO = re.compile(
    r"\b(?:artigos?|arts?)\.?[ \t]*"
    r"(\d{1,3}(?:\.\d{3})*(?:[ \t]*-[ \t]*[A-Za-z]\b)?)"
    r"(?:[oº°ªº](?![\wÀ-ÿ]))?",
    re.IGNORECASE,
)

# "§ 1o", "§ 1º", "§§ 2o" e o caso por extenso "Paragrafo unico".
_RE_PARAGRAFO = re.compile(
    r"§{1,2}[ \t]*(\d{1,3})[ \t]*(?:[oº°ªº])?(?![\wÀ-ÿ])"
    r"|\bpar[aá]grafo[ \t]+[uú]nico\b",
    re.IGNORECASE,
)

# Duas formas reais de inciso: nomeado ("inciso III") ou aberto em linha
# propria com travessao ("III - garantir..."). Fora dessas duas, um romano
# solto no meio da frase quase sempre e outra coisa.
_RE_INCISO_NOMEADO = re.compile(r"(?i:incisos?)[ \t]+([IVXLCDM]{1,7})\b")
_RE_INCISO_LINHA = re.compile(r"(?m)^[ \t]*([IVXLCDM]{1,7})[ \t]*[-–—][ \t]")

# Alineas: "a)" abrindo linha, ou citadas como 'alinea "a"'.
_RE_ALINEA_LINHA = re.compile(r"(?m)^[ \t]*([a-z])\)(?=[ \t]|$)")
_RE_ALINEA_NOMEADA = re.compile(r"(?i:al[ií]neas?)[ \t]+[\"“']?([a-z])[\"”']?\b")


def artigos(texto: str) -> list[dict]:
    """Artigos, incisos e paragrafos encontrados no texto.

    Cada item: {"tipo": "artigo"|"paragrafo"|"inciso"|"alinea",
                "rotulo": "Art. 5o", "inicio": int, "fim": int, "numero": str}
    inicio/fim sao deslocamentos de caractere do ROTULO (nao do conteudo).

    O "numero" vem normalizado (sem ponto de milhar, romano em maiuscula,
    "unico" para o paragrafo unico) porque quem consome quer comparar e
    ordenar; a grafia original continua no "rotulo".
    """
    if not texto:
        return []

    reserva = _Reserva(len(texto))

    for encontro in _RE_ARTIGO.finditer(texto):
        numero = encontro.group(1)
        reserva.marcar(encontro.start(), encontro.end(), {
            "tipo": "artigo",
            "rotulo": _limpo(encontro.group(0)),
            "inicio": encontro.start(),
            "fim": encontro.end(),
            "numero": _limpo(numero).replace(".", "").replace(" ", ""),
        })

    for encontro in _RE_PARAGRAFO.finditer(texto):
        numero = encontro.group(1) or "unico"
        reserva.marcar(encontro.start(), encontro.end(), {
            "tipo": "paragrafo",
            "rotulo": _limpo(encontro.group(0)),
            "inicio": encontro.start(),
            "fim": encontro.end(),
            "numero": numero,
        })

    for regex in (_RE_INCISO_NOMEADO, _RE_INCISO_LINHA):
        for encontro in regex.finditer(texto):
            romano = encontro.group(1)
            if not _romano_valido(romano):
                continue
            # A faixa do rotulo termina no romano: o travessao e separador,
            # nao faz parte do rotulo que a interface vai pintar.
            fim = encontro.end(1)
            reserva.marcar(encontro.start(), fim, {
                "tipo": "inciso",
                "rotulo": _limpo(texto[encontro.start():fim]),
                "inicio": encontro.start(),
                "fim": fim,
                "numero": romano.upper(),
            })

    for regex in (_RE_ALINEA_NOMEADA, _RE_ALINEA_LINHA):
        for encontro in regex.finditer(texto):
            reserva.marcar(encontro.start(), encontro.end(), {
                "tipo": "alinea",
                "rotulo": _limpo(encontro.group(0)),
                "inicio": encontro.start(),
                "fim": encontro.end(),
                "numero": encontro.group(1).lower(),
            })

    return reserva.ordenados()


# ------------------------------------------------------------- REFERENCIAS
# Siglas de codigos e da Constituicao. A ordem importa dentro da alternancia:
# a mais longa precisa ser tentada antes ("CRFB" antes de "CF", "CCB" antes
# de "CC"), senao o regex casa o prefixo e para.
_SIGLAS = r"(?:CRFB|LINDB|CCB|CDC|CLT|CPC|CPP|CTN|CTB|ECA|LEP|CF|CC|CP)"
_SIGLA_COM_ANO = _SIGLAS + r"(?:[ \t]*/[ \t]*\d{2,4})?\b"

# Norma nomeada com numero. "n", "no", "nº" e "n." sao a mesma abreviacao de
# "numero" e aparecem misturadas no mesmo documento.
_NORMA = (
    r"(?i:lei[ \t]+complementar|lei[ \t]+ordin[aá]ria|lei[ \t]+federal|lei|"
    r"decreto[-\s]lei|decreto|medida[ \t]+provis[oó]ria|"
    r"emenda[ \t]+constitucional|portaria|resolu[cç][aã]o|"
    r"instru[cç][aã]o[ \t]+normativa|provimento)"
    r"[ \t]*(?:n[.oº°º]{0,2}[ \t]*)?"
    r"\d{1,3}(?:\.\d{3})*(?:[ \t]*/[ \t]*\d{2,4})?"
)

# Estruturas que costumam vir penduradas na citacao de um artigo.
_ESTRUTURA = (
    r"(?:[ \t]*,[ \t]*(?:§{1,2}[ \t]*\d{1,3}[ \t]*[oº°ªº]?"
    r"|(?i:par[aá]grafo[ \t]+[uú]nico)"
    r"|(?i:incisos?)[ \t]+[IVXLCDM]{1,7}"
    r"|(?i:al[ií]neas?)[ \t]+[\"“']?[a-z][\"”']?))*"
)
_ART_CITADO = r"(?i:arts?\.?|artigos?)[ \t]*\d{1,3}(?:\.\d{3})*[ \t]*[oº°ªº]?"

_REGEX_REFERENCIAS = (
    # 1. Artigo + norma de destino: "art. 5o da CF", "art. 927 do CC".
    re.compile(_ART_CITADO + _ESTRUTURA +
               r"[ \t]*,?[ \t]+d[aeo]s?[ \t]+(?:" + _NORMA + r"|" +
               _SIGLA_COM_ANO + r")"),
    # 2. Sumulas, com ou sem o tribunal que a editou.
    re.compile(r"(?i:s[uú]mulas?)(?:[ \t]+(?i:vinculante))?"
               r"[ \t]*(?:n[.oº°º]{0,2}[ \t]*)?\d{1,4}"
               r"(?:[ \t]+d[aeo][ \t]+[A-Z]{2,5}\b)?"),
    # 3. Norma nomeada e numerada.
    re.compile(_NORMA),
    # 4. Constituicao por extenso.
    re.compile(r"(?i:constitui[cç][aã]o)"
               r"(?:[ \t]+(?i:federal|da[ \t]+rep[uú]blica))?"
               r"(?:[ \t]+de[ \t]+\d{4}|[ \t]*/[ \t]*\d{2,4})?"),
    # 5. Sigla solta -- so o que sobrou das faixas acima.
    re.compile(r"\b" + _SIGLA_COM_ANO),
)


def referencias_legais(texto: str) -> list[dict]:
    """Citacoes a outras normas: {"texto": str, "inicio": int, "fim": int}.

    Ex.: "Lei no 8.078/90", "Lei 13.105/2015", "CF/88", "art. 5o da CF",
         "Decreto-Lei 4.657", "Sumula 331 do TST", "CDC", "CLT", "CPC".

    Os padroes correm do mais especifico para o mais generico e reservam a
    faixa que consomem, para "art. 5o da CF" sair como UMA referencia em vez
    de virar tambem um "CF" solto grudado nela.
    """
    if not texto:
        return []

    reserva = _Reserva(len(texto))
    for regex in _REGEX_REFERENCIAS:
        for encontro in regex.finditer(texto):
            reserva.marcar(encontro.start(), encontro.end(), {
                "texto": _limpo(encontro.group(0)),
                "inicio": encontro.start(),
                "fim": encontro.end(),
            })
    return reserva.ordenados()


# --------------------------------------------------------- BLOCOS DOBRAVEIS
# Um bloco so abre artigo se o rotulo estiver no COMECO dele; "conforme o
# art. 5o" no meio de um paragrafo e citacao, nao inicio de dispositivo.
_RE_ARTIGO_ABERTURA = re.compile(
    r"^[\s\"“'(\[]*(?:artigos?|arts?)\.?[ \t]*"
    r"\d{1,3}(?:\.\d{3})*(?:[ \t]*-[ \t]*[A-Za-z]\b)?"
    r"(?:[oº°ªº](?![\wÀ-ÿ]))?",
    re.IGNORECASE,
)


def blocos_dobraveis(blocos_texto: list[str]) -> list[dict]:
    """Dado o texto dos blocos de uma pagina, agrupa em artigos recolhiveis:
    {"indice_inicio": int, "indice_fim": int, "rotulo": "Art. 5o"}.
    Serve para "recolher artigos" na interface.

    "indice_fim" e INCLUSIVO: e o ultimo bloco que pertence ao artigo, ou
    seja, o bloco anterior ao proximo rotulo (ou o fim da pagina). Blocos que
    aparecem antes do primeiro artigo ficam de fora -- sao ementa, titulo do
    capitulo ou texto corrido, e recolher isso esconderia contexto.
    """
    if not blocos_texto:
        return []

    grupos = []
    for indice, bloco in enumerate(blocos_texto):
        if not isinstance(bloco, str):
            continue
        encontro = _RE_ARTIGO_ABERTURA.match(bloco)
        if not encontro:
            continue
        if grupos:
            grupos[-1]["indice_fim"] = indice - 1
        rotulo = _limpo(encontro.group(0)).lstrip("\"“'([ ")
        grupos.append({
            "indice_inicio": indice,
            "indice_fim": len(blocos_texto) - 1,
            "rotulo": rotulo,
        })

    # Um artigo de bloco unico ainda e valido (o caput cabe num paragrafo so),
    # entao nao filtramos por tamanho; so garantimos coerencia dos indices.
    return [g for g in grupos if g["indice_fim"] >= g["indice_inicio"]]


# ==============================================================================
# DATAS E LINHA DO TEMPO
# ==============================================================================

_ANO_MINIMO = 1000
_ANO_MAXIMO = 2100

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

# Palavras que, imediatamente antes de um numero, provam que aquele numero NAO
# e um ano. Esta e a defesa principal contra o falso positivo: "pagina 1500",
# "art. 1234", "R$ 1822", "n. 1988", "versiculo 1500".
_ANTES_PROIBIDO = re.compile(
    r"(?<![A-Za-zÀ-ÿ])"
    r"(?:pp?\.|p[aá]gs?\.?|p[aá]ginas?|f[l]s?\.|folhas?|"
    r"artigos?|arts?\.?|incisos?|al[ií]neas?|§{1,2}|"
    r"cap\.|cap[ií]tulos?|se[cç][aã]o|vers[ií]culos?|vv?\.|"
    r"itens|item|tabelas?|figuras?|quadros?|gr[aá]ficos?|linhas?|notas?|"
    r"n[.oº°º]{1,2}|n[uú]meros?|num\.|"
    r"r\$|us\$|u\$|\$|€|£|"
    r"lei|leis|decreto(?:-lei)?|s[uú]mulas?|portarias?|resolu[cç][aã]o|"
    r"medida[ \t]+provis[oó]ria|emenda[ \t]+constitucional|processo|"
    r"isbn|issn|cep|cnpj|cpf|tel\.|telefone|ramal|quarto|sala|apto\.?"
    r")"
    r"[ \t]*[:nNoOº°º.\-–]*[ \t]*$",
    re.IGNORECASE,
)

# "a.C." / "d.C." exigem o ponto: sem ele, "em 1889 a Camara" seria lido como
# "1889 a C" e viraria antes de Cristo.
_RE_ERA = re.compile(
    r"^[ \t]*(?:([adAD])[ \t]*\.[ \t]*C[ \t]*\.?|([adAD])[ \t]*C[ \t]*\.)"
    r"(?![\wÀ-ÿ])"
)

_RE_DATA_EXTENSO = re.compile(
    r"\b(\d{1,2})\s+de\s+([A-Za-zÀ-ÿ]+)\s+de\s+(\d{3,4})\b", re.IGNORECASE
)
_RE_DATA_NUMERICA = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})\b")
_RE_PERIODO = re.compile(r"\b(\d{3,4})\s*[-–—]\s*(\d{3,4})\b")
_RE_SECULO = re.compile(
    r"(?i:s[eé]culos?|s[eé]c\.)[ \t]+([IVXLCDM]{1,7})\b"
)
_RE_ANO_COM_ERA = re.compile(
    r"\b(\d{1,4})[ \t]*(?=(?:[adAD][ \t]*\.[ \t]*C|[adAD][ \t]*C[ \t]*\.))"
)
_RE_ANO_SOLTO = re.compile(r"\b(\d{4})\b")


def _era_depois(texto: str, fim: int):
    """(sinal, novo_fim) olhando se logo apos a faixa vem a.C. ou d.C.

    Devolve sinal -1 para a.C. e 1 para d.C./ausencia, e o fim ja esticado
    para dentro da faixa marcada, porque "1500 a.C." e um so achado.
    """
    encontro = _RE_ERA.match(texto, fim)
    if not encontro:
        return 1, fim
    letra = (encontro.group(1) or encontro.group(2)).lower()
    return (-1 if letra == "a" else 1), encontro.end()


def _contexto_bloqueia(texto: str, inicio: int) -> bool:
    """True quando o que vem antes do numero prova que ele nao e um ano."""
    return bool(_ANTES_PROIBIDO.search(texto[max(0, inicio - 40):inicio]))


def _ano_plausivel(valor: int) -> bool:
    return _ANO_MINIMO <= valor <= _ANO_MAXIMO


def datas(texto: str) -> list[dict]:
    """Datas e periodos historicos.

    Cada item: {"texto": str, "inicio": int, "fim": int, "ano": int|None,
                "tipo": "ano"|"data"|"periodo"|"seculo"}
    Cobrir: 1500, 1822, "15 de novembro de 1889", "1939-1945",
            "seculo XIX", "século XIX", "d.C.", "a.C.", "12/10/1492".

    Anos antes de Cristo vem com "ano" negativo, o que mantem a ordenacao da
    linha do tempo correta sem precisar de um campo extra.

    CUIDADO: nao marcar numeros que sao paginas, versiculos, valores ou
    numeros de artigo. Exija contexto plausivel de ano (1000-2100) e evite
    numeros grudados em "p.", "pag.", "R$", "art.", "n.".
    """
    if not texto:
        return []

    reserva = _Reserva(len(texto))

    def registrar(inicio, fim, ano, tipo):
        return reserva.marcar(inicio, fim, {
            "texto": _limpo(texto[inicio:fim]),
            "inicio": inicio,
            "fim": fim,
            "ano": ano,
            "tipo": tipo,
        })

    # 1. Data por extenso: "15 de novembro de 1889". Exigir nome de mes real
    #    impede que "3 de 4 de 1889" ou "capitulo de X de 1889" passem.
    for encontro in _RE_DATA_EXTENSO.finditer(texto):
        mes = encontro.group(2).lower()
        if mes not in _MESES:
            continue
        dia = int(encontro.group(1))
        ano = int(encontro.group(3))
        if not (1 <= dia <= 31) or not _ano_plausivel(ano):
            continue
        sinal, fim = _era_depois(texto, encontro.end())
        registrar(encontro.start(), fim, sinal * ano, "data")

    # 2. Data numerica com ano de 4 digitos. Aceitar ano de 2 digitos abriria
    #    a porta para "8.078/90" e para fracoes.
    for encontro in _RE_DATA_NUMERICA.finditer(texto):
        dia, mes, ano = (int(g) for g in encontro.groups())
        if not (1 <= dia <= 31 and 1 <= mes <= 12 and _ano_plausivel(ano)):
            continue
        if _contexto_bloqueia(texto, encontro.start()):
            continue
        sinal, fim = _era_depois(texto, encontro.end())
        registrar(encontro.start(), fim, sinal * ano, "data")

    # 3. Periodo "1939-1945". O intervalo tem de crescer, senao e numeracao
    #    de paginas invertida, telefone ou faixa de itens.
    for encontro in _RE_PERIODO.finditer(texto):
        inicial, final = int(encontro.group(1)), int(encontro.group(2))
        if not (_ano_plausivel(inicial) and _ano_plausivel(final)):
            continue
        if final <= inicial:
            continue
        if _contexto_bloqueia(texto, encontro.start()):
            continue
        sinal, fim = _era_depois(texto, encontro.end())
        registrar(encontro.start(), fim, sinal * inicial, "periodo")

    # 4. Seculos. O ano guardado e o primeiro do seculo, o que basta para
    #    ordenar a linha do tempo.
    for encontro in _RE_SECULO.finditer(texto):
        romano = encontro.group(1)
        if not _romano_valido(romano):
            continue
        numero = _romano_para_inteiro(romano)
        if not 1 <= numero <= 21:
            continue
        sinal, fim = _era_depois(texto, encontro.end())
        registrar(encontro.start(), fim, sinal * ((numero - 1) * 100 + 1),
                  "seculo")

    # 5. Ano com era explicita: "44 a.C.", "476 d.C.". Aqui o limite de
    #    1000-2100 nao vale, pois a era ja garante que e ano.
    for encontro in _RE_ANO_COM_ERA.finditer(texto):
        if _contexto_bloqueia(texto, encontro.start()):
            continue
        sinal, fim = _era_depois(texto, encontro.end())
        if fim == encontro.end():
            continue
        registrar(encontro.start(), fim, sinal * int(encontro.group(1)), "ano")

    # 6. Ano solto -- o padrao mais perigoso, e por isso o ultimo e o mais
    #    vigiado: faixa plausivel, contexto limpo e sem vizinhos numericos
    #    que revelem valor monetario, numeracao ou fracao.
    for encontro in _RE_ANO_SOLTO.finditer(texto):
        inicio, fim = encontro.span()
        if not reserva.livre(inicio, fim):
            continue
        valor = int(encontro.group(1))
        if not _ano_plausivel(valor):
            continue
        anterior = texto[inicio - 1] if inicio else ""
        if anterior in "/.,%\\":
            # "13.105/2015" e "0.1988" nao sao anos: sao numero de norma ou
            # parte de um numero maior quebrado por pontuacao.
            continue
        seguinte = texto[fim:fim + 2]
        if len(seguinte) >= 2 and seguinte[0] in ".," and seguinte[1].isdigit():
            # "1822,00" e valor monetario; "1500.75" e decimal.
            continue
        if seguinte[:1] == "%":
            continue
        if _contexto_bloqueia(texto, inicio):
            continue
        sinal, fim_era = _era_depois(texto, fim)
        registrar(inicio, fim_era, sinal * valor, "ano")

    return reserva.ordenados()


# ------------------------------------------------------------ LINHA DO TEMPO
_FIM_DE_FRASE = ".!?;\n"


def _frase_em_volta(texto: str, inicio: int, fim: int, limite: int = 90) -> str:
    """A frase onde a data aparece, encurtada mantendo a data visivel.

    Cortar os primeiros 90 caracteres da frase costuma esconder justamente a
    data, entao a janela e centrada nela quando a frase e longa.
    """
    esquerda = inicio
    while esquerda > 0 and texto[esquerda - 1] not in _FIM_DE_FRASE:
        esquerda -= 1
    direita = fim
    while direita < len(texto) and texto[direita] not in _FIM_DE_FRASE:
        direita += 1
    if direita < len(texto) and texto[direita] in ".!?":
        direita += 1

    frase = _limpo(texto[esquerda:direita])
    if len(frase) <= limite:
        return frase

    alvo = _limpo(texto[inicio:fim])
    posicao = frase.find(alvo)
    if posicao < 0:
        return frase[:limite].rstrip() + "..."
    comeco = max(0, posicao - (limite - len(alvo)) // 2)
    recorte = frase[comeco:comeco + limite].strip()
    if comeco > 0:
        recorte = "..." + recorte
    if comeco + limite < len(frase):
        recorte = recorte + "..."
    return recorte


def linha_do_tempo(paginas: dict[int, str]) -> list[dict]:
    """Recebe {numero_pagina: texto} e devolve eventos ordenados por ano:
    [{"ano": int, "pagina": int, "trecho": str}] -- o trecho e a frase onde a
    data aparece, cortada em ~90 caracteres. Deduplique por (ano, pagina).

    A deduplicacao por (ano, pagina) existe porque livros repetem a mesma data
    varias vezes na mesma pagina; a linha do tempo quer o evento, nao cada
    mencao.
    """
    if not paginas:
        return []

    vistos = set()
    eventos = []
    for numero in sorted(paginas):
        texto = paginas[numero] or ""
        for achado in datas(texto):
            ano = achado.get("ano")
            if ano is None:
                continue
            chave = (ano, numero)
            if chave in vistos:
                continue
            vistos.add(chave)
            eventos.append({
                "ano": ano,
                "pagina": numero,
                "trecho": _frase_em_volta(texto, achado["inicio"],
                                          achado["fim"]),
            })

    eventos.sort(key=lambda e: (e["ano"], e["pagina"]))
    return eventos
