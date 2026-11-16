# services/deteccao_academico.py
"""Estrutura de textos academicos e religiosos.

Os dois no mesmo modulo porque o problema e o mesmo: referencia numerada que
precisa virar navegacao. Num paper e "(SILVA, 2020)" apontando para a lista
de referencias; numa Biblia e "Joao 3:16" apontando para outro versiculo.

REGRA QUE GUIA TODO O ARQUIVO: falso positivo e pior que falso negativo.
Marcar "Python3" como nota de rodape ou "lista[1]" como citacao numerica
sujaria a leitura de um livro tecnico inteiro. Por isso todo padrao aqui exige
contexto, nao so o formato.
"""
import re

# ============================================================== ACADEMICO

# ABNT com sobrenome em maiusculas: (SILVA, 2020) / (SILVA; SOUZA, 2020, p. 45)
# O nome NAO pode terminar em espaco: a versao antiga aceitava espaco dentro
# da classe repetida e, encostado num separador, cada item da lista podia ser
# casado de dois jeitos -- 25 itens levavam 15 s com a interface congelada.
_NOME_ABNT = (r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}"
              r"(?:\s+(?:D[AEO]S?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}){0,4}")
_RE_ABNT_PARENTESES = re.compile(
    r"\(\s*(" + _NOME_ABNT +
    r"(?:\s*(?:;|,|\be\b|&|et al\.?)\s*" + _NOME_ABNT + r"){0,12}"
    r"(?:\s+et\s+al\.?)?)"
    r"\s*,\s*(\d{4})([^)]{0,24})\)")

# ABNT no corpo da frase: SILVA (2020) / Silva (2020, p. 45)
_RE_ABNT_CORPO = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zá-ÿA-ZÁ-Ý]+(?:\s+(?:e|&|et al\.?)\s+"
    r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zá-ÿ]+)?)\s*\(\s*(\d{4})([^)]{0,24})\)")

# APA: (Silva, 2020) / (Silva & Souza, 2020)
_RE_APA = re.compile(
    r"\(\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zá-ÿ]+(?:\s*(?:&|;|,|and|e)\s*"
    r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zá-ÿ]+)*)\s*,\s*(\d{4})([^)]{0,24})\)")

# Numerica [1] [1,2] [1-3]. Perigosa: colide com indice de lista em codigo.
_RE_NUMERICA = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")

# Entrada de referencia ABNT: SOBRENOME, Prenome.
_RE_ENTRADA_REF = re.compile(
    r"^\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{2,}),\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-zá-ÿ]",
    re.MULTILINE)

# Palavras que aparecem grudadas a numero em texto tecnico e NAO sao nota.
_TERMOS_TECNICOS = {
    "python", "html", "css", "java", "sql", "php", "covid", "http", "https",
    "utf", "iso", "mp", "gb", "mb", "kb", "px", "ipv", "sha", "md", "rgb",
    "win", "x", "v", "vol", "cap", "fig", "n", "no", "art", "p", "pag",
    "windows", "office", "excel", "word", "android", "ios", "usb", "wifi",
}
# Aceita inicial maiuscula: o caso tipico e um SOBRENOME com a nota colada
# ("conforme Silva1 afirma"), que uma classe so de minusculas deixava passar.
_RE_NOTA_RODAPE = re.compile(r"\b([A-Za-zÀ-ÿ][a-zá-ÿ]{2,})(\d{1,2})(?=[\s.,;:!?)]|$)")


def citacoes(texto: str) -> list:
    """Citacoes no corpo do texto, sem sobreposicao."""
    achadas = []
    ocupado = []

    def livre(inicio, fim):
        return not any(i < fim and inicio < f for i, f in ocupado)

    def registrar(inicio, fim, autores, ano, estilo, bruto):
        if not livre(inicio, fim):
            return
        ocupado.append((inicio, fim))
        achadas.append({
            "texto": bruto, "inicio": inicio, "fim": fim,
            "autores": autores, "ano": int(ano) if ano else None,
            "estilo": estilo,
        })

    for encontro in _RE_ABNT_PARENTESES.finditer(texto):
        autores = [a.strip() for a in re.split(r"[;,]|\be\b|&", encontro.group(1))
                   if a.strip() and "et al" not in a.lower()]
        registrar(*encontro.span(), autores, encontro.group(2), "ABNT",
                  encontro.group(0))

    for encontro in _RE_APA.finditer(texto):
        autores = [a.strip() for a in re.split(r"[;,&]|\band\b|\be\b", encontro.group(1))
                   if a.strip()]
        registrar(*encontro.span(), autores, encontro.group(2), "APA",
                  encontro.group(0))

    for encontro in _RE_ABNT_CORPO.finditer(texto):
        registrar(*encontro.span(), [encontro.group(1).strip()], encontro.group(2),
                  "ABNT-corpo", encontro.group(0))

    # A numerica so entra se o texto tiver cara de trabalho academico. Num
    # livro de programacao "lista[1]" apareceria como citacao em toda pagina.
    if _parece_academico(texto):
        for encontro in _RE_NUMERICA.finditer(texto):
            anterior = texto[max(0, encontro.start() - 1):encontro.start()]
            # Colado num identificador e indexacao de codigo, nao citacao.
            if anterior and (anterior.isalnum() or anterior in "_)]\"'"):
                continue
            registrar(*encontro.span(), [], None, "numerica", encontro.group(0))

    achadas.sort(key=lambda c: c["inicio"])
    return achadas


def _parece_academico(texto: str) -> bool:
    """Ha sinais de trabalho academico suficientes para confiar na numerica?"""
    marcas = ("referências", "referencias", "bibliografia", "resumo", "abstract",
              "palavras-chave", "metodologia", "conclusão", "conclusao",
              "apud", "et al", "ibid", "op. cit.")
    baixo = texto.lower()
    return sum(1 for m in marcas if m in baixo) >= 2


def referencias_bibliograficas(texto: str) -> list:
    """Entradas da lista de referencias. O fim de uma e o inicio da seguinte."""
    inicios = [(m.start(), m.group(1).strip()) for m in _RE_ENTRADA_REF.finditer(texto)]
    if not inicios:
        return []
    saida = []
    for indice, (inicio, autor) in enumerate(inicios):
        fim = inicios[indice + 1][0] if indice + 1 < len(inicios) else len(texto)
        bruto = texto[inicio:fim].strip()
        ano = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", bruto)
        saida.append({"texto": bruto[:300], "inicio": inicio, "fim": fim,
                      "autor": autor, "ano": int(ano.group(1)) if ano else None})
    return saida


def notas_de_rodape(texto: str) -> list:
    """Marcadores de nota grudados numa palavra.

    O PDF entrega o sobrescrito como digito comum, entao "Silva1" pode ser
    nota -- mas "Python3" e "HTML5" tambem casam com o padrao. A lista de
    termos tecnicos e o que separa um do outro.
    """
    saida = []
    for encontro in _RE_NOTA_RODAPE.finditer(texto):
        palavra, numero = encontro.group(1), encontro.group(2)
        if palavra.lower() in _TERMOS_TECNICOS:
            continue
        # Numero alto demais para nota de rodape de uma pagina.
        if int(numero) > 60:
            continue
        saida.append({"numero": int(numero),
                      "inicio": encontro.start(2), "fim": encontro.end(2)})
    return saida


# ============================================================== RELIGIOSO
_LIVROS_BIBLIA = {
    "genesis": "Gn", "gênesis": "Gn", "gn": "Gn", "exodo": "Ex", "êxodo": "Ex",
    "ex": "Ex", "levitico": "Lv", "levítico": "Lv", "lv": "Lv",
    "numeros": "Nm", "números": "Nm", "nm": "Nm", "deuteronomio": "Dt",
    "deuteronômio": "Dt", "dt": "Dt", "josue": "Js", "josué": "Js", "js": "Js",
    "juizes": "Jz", "juízes": "Jz", "jz": "Jz", "rute": "Rt", "rt": "Rt",
    "samuel": "Sm", "sm": "Sm", "reis": "Rs", "rs": "Rs",
    "cronicas": "Cr", "crônicas": "Cr", "cr": "Cr", "esdras": "Ed", "ed": "Ed",
    "neemias": "Ne", "ne": "Ne", "ester": "Et", "et": "Et", "jo": "Jó",
    "job": "Jó", "salmos": "Sl", "salmo": "Sl", "sl": "Sl",
    "proverbios": "Pv", "provérbios": "Pv", "pv": "Pv",
    "eclesiastes": "Ec", "ec": "Ec", "canticos": "Ct", "cânticos": "Ct",
    "ct": "Ct", "isaias": "Is", "isaías": "Is", "is": "Is",
    "jeremias": "Jr", "jr": "Jr", "lamentacoes": "Lm", "lamentações": "Lm",
    "lm": "Lm", "ezequiel": "Ez", "ez": "Ez", "daniel": "Dn", "dn": "Dn",
    "oseias": "Os", "oséias": "Os", "os": "Os", "joel": "Jl", "jl": "Jl",
    "amos": "Am", "amós": "Am", "am": "Am", "obadias": "Ob", "ob": "Ob",
    "jonas": "Jn", "jn": "Jn", "miqueias": "Mq", "miquéias": "Mq", "mq": "Mq",
    "naum": "Na", "na": "Na", "habacuque": "Hc", "hc": "Hc",
    "sofonias": "Sf", "sf": "Sf", "ageu": "Ag", "ag": "Ag",
    "zacarias": "Zc", "zc": "Zc", "malaquias": "Ml", "ml": "Ml",
    "mateus": "Mt", "mt": "Mt", "marcos": "Mc", "mc": "Mc",
    "lucas": "Lc", "lc": "Lc", "joao": "Jo", "joão": "Jo",
    "atos": "At", "at": "At", "romanos": "Rm", "rm": "Rm",
    "corintios": "Co", "coríntios": "Co", "co": "Co",
    "galatas": "Gl", "gálatas": "Gl", "gl": "Gl", "efesios": "Ef",
    "efésios": "Ef", "ef": "Ef", "filipenses": "Fp", "fp": "Fp",
    "colossenses": "Cl", "cl": "Cl", "tessalonicenses": "Ts", "ts": "Ts",
    "timoteo": "Tm", "timóteo": "Tm", "tm": "Tm", "tito": "Tt", "tt": "Tt",
    "filemom": "Fm", "fm": "Fm", "hebreus": "Hb", "hb": "Hb",
    "tiago": "Tg", "tg": "Tg", "pedro": "Pe", "pe": "Pe",
    "judas": "Jd", "jd": "Jd", "apocalipse": "Ap", "ap": "Ap",
}

_RE_REF_BIBLICA = re.compile(
    r"\b((?:[1-3]\s*)?[A-Za-zÀ-ÿ]{2,15})\.?\s+(\d{1,3})\s*:\s*(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)")
_RE_REF_CAPITULO = re.compile(r"\b((?:[1-3]\s*)?[A-Za-zÀ-ÿ]{4,15})\s+(\d{1,3})\b")

# Versiculo: numero no comeco da frase, seguido de maiuscula.
_RE_VERSICULO = re.compile(r"(?:^|(?<=[.\s]))(\d{1,3})\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")


def referencias_biblicas(texto: str) -> list:
    """Citacoes cruzadas do tipo "Joao 3:16" ou "1 Corintios 13:4-7"."""
    saida = []
    for encontro in _RE_REF_BIBLICA.finditer(texto):
        bruto = encontro.group(1).strip()
        nome = re.sub(r"^[1-3]\s*", "", bruto).lower()
        if nome not in _LIVROS_BIBLIA:
            continue
        saida.append({
            "texto": encontro.group(0).strip(), "inicio": encontro.start(),
            "fim": encontro.end(), "livro": bruto,
            "capitulo": int(encontro.group(2)),
            "versiculos": encontro.group(3).replace(" ", ""),
        })
    return saida


def versiculos(texto: str) -> list:
    """Numeros de versiculo no inicio de frase.

    So devolve quando ha varios em sequencia: um numero solto seguido de
    maiuscula acontece em qualquer texto ("Em 1994 Silva provou...").
    """
    achados = [{"numero": int(m.group(1)), "inicio": m.start(1), "fim": m.end(1)}
               for m in _RE_VERSICULO.finditer(texto)]
    if len(achados) < 3:
        return []
    # Versiculo numera em sequencia. Se os numeros nao crescem, nao e Biblia.
    numeros = [a["numero"] for a in achados]
    crescentes = sum(1 for a, b in zip(numeros, numeros[1:]) if b == a + 1)
    if crescentes < max(2, len(numeros) * 0.5):
        return []
    return achados
