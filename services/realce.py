# services/realce.py
"""Realce de sintaxe com deteccao de linguagem.

Um livro tecnico nao diz em que linguagem cada trecho esta, e trechos curtos
("pip install django", "SELECT * FROM users") nao trazem sinal suficiente
sozinhos. A saida e votar no nivel do LIVRO: cada bloco contribui com o que
reconhece, e blocos curtos herdam a linguagem dominante do documento.

Devolve intervalos de caracteres para a interface pintar; nao altera o texto.
"""
import re
from collections import Counter

# ---------------------------------------------------------------- LINGUAGENS
# Cada linguagem traz as marcas que praticamente so aparecem nela. O peso
# separa sinal forte (">>> " so existe em Python) de sinal fraco ("return").
_ASSINATURAS = {
    "python": [
        (r"^\s*def\s+\w+\s*\(", 5), (r"^\s*class\s+\w+.*:\s*$", 5),
        (r">>>\s", 6), (r"\bself\b", 4), (r"^\s*(from|import)\s+[\w.]+", 4),
        (r"\belif\b", 5), (r"__\w+__", 4), (r'^\s*@\w+', 2),
        (r"\bprint\s*\(", 2), (r":\s*$", 1), (r"\bNone\b|\bTrue\b|\bFalse\b", 3),
    ],
    "javascript": [
        (r"\bfunction\s*\w*\s*\(", 4), (r"\b(const|let|var)\s+\w+\s*=", 4),
        (r"=>", 4), (r"console\.log", 6), (r"\bdocument\.", 4),
        (r"\brequire\s*\(", 3), (r"\bexport\s+(default|const)", 4), (r";\s*$", 1),
    ],
    "typescript": [
        (r":\s*(string|number|boolean|any)\b", 6), (r"\binterface\s+\w+\s*\{", 5),
        (r"\bexport\s+(class|interface|type)", 4),
    ],
    "java": [
        (r"\bpublic\s+(static\s+)?(class|void|int|String)", 6),
        (r"System\.out\.print", 7), (r"\bimport\s+java\.", 7),
        (r"\bnew\s+[A-Z]\w*\s*\(", 2), (r"@Override", 5),
    ],
    "csharp": [
        (r"\busing\s+System", 7), (r"\bnamespace\s+\w+", 5),
        (r"Console\.Write", 7), (r"\bpublic\s+\w+\s+\w+\s*\{\s*get", 5),
    ],
    "php": [(r"<\?php", 9), (r"\$\w+\s*=", 4), (r"\becho\b", 3), (r"->\w+\(", 2)],
    "ruby": [(r"\bdef\s+\w+.*\n.*\bend\b", 5), (r"\bputs\b", 5),
             (r"\bdo\s*\|\w+\|", 6), (r"@\w+\s*=", 2)],
    "go": [(r"\bfunc\s+\w*\s*\(", 5), (r"\bpackage\s+main", 8),
           (r":=", 5), (r"\bfmt\.", 7)],
    "rust": [(r"\bfn\s+\w+", 5), (r"\blet\s+mut\b", 7),
             (r"\bimpl\b", 5), (r"println!", 8), (r"\bmatch\b.*\{", 2)],
    "sql": [
        (r"\bSELECT\b.*\bFROM\b", 8), (r"\bINSERT\s+INTO\b", 8),
        (r"\bCREATE\s+TABLE\b", 8), (r"\bUPDATE\b.*\bSET\b", 7),
        (r"\bWHERE\b", 3), (r"\bJOIN\b", 4),
    ],
    "html": [(r"</\w+>", 6), (r"<(div|span|html|body|head|p|a|table)\b", 6),
             (r"<!DOCTYPE", 8), (r'\w+="[^"]*"', 1)],
    "css": [(r"^\s*[.#]?[\w-]+\s*\{\s*$", 4), (r"^\s*[\w-]+\s*:\s*[^;]+;", 3),
            (r"@media\b", 6), (r"\b\d+(px|rem|em|%)\b", 3)],
    "json": [(r'^\s*[\{\[]', 2), (r'"\w+"\s*:', 5), (r'^\s*"\w+"\s*:\s*[\{\[""\d]', 4)],
    "yaml": [(r"^\s*[\w-]+:\s*$", 3), (r"^\s*-\s+\w+:", 4), (r"^---\s*$", 6)],
    "shell": [
        (r"^\s*\$\s+\w", 7), (r"\b(sudo|apt-get|apt|yum|brew)\b", 7),
        (r"\b(pip|npm|yarn|git|docker)\s+\w+", 6), (r"^\s*#!/bin/", 9),
        (r"\bcd\s+[\w./~-]+", 3), (r"\b(mkdir|chmod|ls|rm|cp|mv)\b", 4),
    ],
}

# Palavras reservadas por linguagem. "comum" cobre o que aparece em quase todas
# e serve de rede de seguranca quando a linguagem nao foi identificada.
_RESERVADAS = {
    "comum": {"if", "else", "for", "while", "return", "break", "continue",
              "class", "function", "true", "false", "null", "new", "import",
              "try", "catch", "throw", "this", "int", "string", "void", "and",
              "or", "not", "in", "do", "switch", "case", "const", "var", "let"},
    "python": {"def", "class", "return", "if", "elif", "else", "for", "while",
               "import", "from", "as", "with", "try", "except", "finally",
               "raise", "lambda", "yield", "pass", "break", "continue", "global",
               "nonlocal", "assert", "del", "and", "or", "not", "in", "is",
               "None", "True", "False", "self", "async", "await", "match", "case"},
    "javascript": {"function", "const", "let", "var", "return", "if", "else",
                   "for", "while", "class", "extends", "new", "this", "import",
                   "export", "default", "async", "await", "try", "catch",
                   "finally", "throw", "typeof", "instanceof", "null",
                   "undefined", "true", "false", "of", "in", "switch", "case"},
    "java": {"public", "private", "protected", "static", "final", "class",
             "interface", "extends", "implements", "void", "int", "long",
             "double", "float", "boolean", "char", "String", "new", "return",
             "if", "else", "for", "while", "try", "catch", "finally", "throw",
             "throws", "import", "package", "this", "super", "null", "true",
             "false", "abstract", "synchronized"},
    "sql": {"select", "from", "where", "insert", "into", "values", "update",
            "set", "delete", "create", "table", "alter", "drop", "index",
            "join", "inner", "left", "right", "outer", "on", "group", "by",
            "order", "having", "limit", "distinct", "as", "and", "or", "not",
            "null", "primary", "key", "foreign", "references", "constraint"},
    "shell": {"cd", "ls", "mkdir", "rm", "cp", "mv", "sudo", "apt", "apt-get",
              "pip", "npm", "git", "docker", "echo", "export", "source",
              "chmod", "chown", "grep", "cat", "python", "python3", "node"},
    "html": {"div", "span", "html", "body", "head", "title", "script", "style",
             "link", "meta", "form", "input", "button", "table", "tr", "td",
             "th", "ul", "li", "ol", "img", "a", "p", "h1", "h2", "h3"},
    "css": {"color", "background", "margin", "padding", "border", "display",
            "flex", "grid", "position", "width", "height", "font", "align"},
}
_RESERVADAS["typescript"] = _RESERVADAS["javascript"] | {"interface", "type", "enum"}
_RESERVADAS["csharp"] = _RESERVADAS["java"] | {"using", "namespace", "var"}
_RESERVADAS["php"] = _RESERVADAS["javascript"] | {"echo", "elseif", "foreach"}
_RESERVADAS["ruby"] = {"def", "end", "do", "module", "require", "puts", "attr_accessor",
                       "if", "elsif", "else", "unless", "while", "class", "nil"}
_RESERVADAS["go"] = {"func", "package", "import", "var", "const", "type", "struct",
                     "interface", "range", "defer", "go", "chan", "if", "else",
                     "for", "return", "nil", "map"}
_RESERVADAS["rust"] = {"fn", "let", "mut", "impl", "struct", "enum", "trait",
                       "match", "use", "pub", "mod", "if", "else", "loop",
                       "while", "for", "return", "Some", "None", "Ok", "Err"}
_RESERVADAS["json"] = {"true", "false", "null"}
_RESERVADAS["yaml"] = {"true", "false", "null"}

# Sintaxe de comentario por familia de linguagem.
_COMENTARIOS = {
    "python": (r"#[^\n]*",), "ruby": (r"#[^\n]*",), "shell": (r"#[^\n]*",),
    "yaml": (r"#[^\n]*",),
    "sql": (r"--[^\n]*", r"/\*.*?\*/"),
    "html": (r"<!--.*?-->",),
    "css": (r"/\*.*?\*/",),
    "json": (),
}
_COMENTARIO_PADRAO = (r"//[^\n]*", r"/\*.*?\*/")

_RE_TEXTO = (
    re.compile(r"('''|\"\"\")(?:.*?)\1", re.DOTALL),
    re.compile(r"(['\"`])(?:\\.|(?!\1).)*\1"),
)
_RE_NUMERO = re.compile(r"\b\d+(?:\.\d+)?\b")
_RE_FUNCAO = re.compile(r"\b([A-Za-z_]\w*)\s*(?=\()")
_RE_DECORADOR = re.compile(r"(?:^|\s)([@#]!?\w[\w.]*)")
_RE_PALAVRA = re.compile(r"\b[A-Za-z_][\w-]*\b")
_RE_TAG_HTML = re.compile(r"</?([A-Za-z][\w-]*)")


# ------------------------------------------------------------------ DETECCAO
def pontuar(codigo: str) -> Counter:
    """Pontos por linguagem para um trecho de codigo."""
    pontos = Counter()
    if not codigo:
        return pontos
    for lingua, marcas in _ASSINATURAS.items():
        for padrao, peso in marcas:
            achados = len(re.findall(padrao, codigo, re.MULTILINE | re.IGNORECASE
                                     if lingua == "sql" else re.MULTILINE))
            if achados:
                # Raiz quadrada: repetir a mesma marca 50 vezes nao vale 50x.
                pontos[lingua] += peso * (achados ** 0.5)
    return pontos


# Marca minima que um trecho precisa ter para HERDAR a linguagem dominante do
# livro. Um livro de Django tem tantos templates que a dominante vira "html" --
# e todo trecho Python curto saia rotulado e colorido como HTML.
_COMPATIVEL = {
    "html": re.compile(r"<"), "xml": re.compile(r"<"),
    "css": re.compile(r"[{:]"), "json": re.compile(r"[{\[]"),
}

NOMES = {
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
    "java": "Java", "csharp": "C#", "php": "PHP", "ruby": "Ruby", "go": "Go",
    "rust": "Rust", "sql": "SQL", "html": "HTML", "css": "CSS", "json": "JSON",
    "yaml": "YAML", "shell": "Terminal", "comum": "Codigo", "arvore": "Arquivos",
}


def nome_exibicao(lingua: str) -> str:
    return NOMES.get(lingua or "comum", (lingua or "codigo").capitalize())


def _herdar(codigo, dominante):
    if not dominante:
        return None
    exigencia = _COMPATIVEL.get(dominante)
    if exigencia is not None and not exigencia.search(codigo):
        return None
    return dominante


_RE_LINHA_ARVORE = re.compile(
    r"^\s*(?:[-*+|├└│─`]+\s*)*/?[\w.\-]+(?:/[\w.\-]*)*/?\s*(?:#.*)?$")


def _eh_arvore(codigo: str) -> bool:
    """Arvore de diretorios ("/projeto  - settings.py  - urls.py").

    Nao e linguagem nenhuma; o "__init__.py" fazia ela passar por Python.
    """
    linhas = [l for l in codigo.split("\n") if l.strip()]
    if len(linhas) < 3:
        return False
    com_arquivo = sum(1 for l in linhas if re.search(r"\.\w{1,5}\s*$|/\s*$", l))
    return (all(_RE_LINHA_ARVORE.match(l) for l in linhas)
            and com_arquivo >= len(linhas) * 0.6)


def detectar(codigo: str, dominante: str = None, minimo: float = 6.0) -> str:
    """Linguagem do trecho, caindo para a dominante do livro quando fraco."""
    if _eh_arvore(codigo):
        return "arvore"
    pontos = pontuar(codigo)
    if not pontos:
        return _herdar(codigo, dominante) or "comum"
    lingua, melhor = pontos.most_common(1)[0]
    if melhor < minimo:
        return _herdar(codigo, dominante) or lingua
    return lingua


# ------------------------------------------------------------------ TOKENS
def tokenizar(codigo: str, lingua: str = None, dominante: str = None):
    """[(inicio, fim, classe), ...] em deslocamentos de caractere.

    Os intervalos nunca se sobrepoem: o primeiro padrao que reivindica uma
    faixa fica com ela, e comentario/texto tem prioridade sobre o resto.
    """
    if not codigo:
        return []
    lingua = lingua or detectar(codigo, dominante)
    reservadas = _RESERVADAS.get(lingua) or _RESERVADAS["comum"]

    ocupado = bytearray(len(codigo))
    achados = []

    def marcar(inicio, fim, classe):
        if fim <= inicio or any(ocupado[inicio:fim]):
            return
        ocupado[inicio:fim] = b"\x01" * (fim - inicio)
        achados.append((inicio, fim, classe))

    for padrao in _COMENTARIOS.get(lingua, _COMENTARIO_PADRAO):
        for encontro in re.finditer(padrao, codigo, re.MULTILINE | re.DOTALL):
            marcar(*encontro.span(), "comentario")

    for regex in _RE_TEXTO:
        for encontro in regex.finditer(codigo):
            marcar(*encontro.span(), "texto")

    if lingua == "html":
        for encontro in _RE_TAG_HTML.finditer(codigo):
            marcar(*encontro.span(1), "palavra")

    for encontro in _RE_DECORADOR.finditer(codigo):
        marcar(*encontro.span(1), "decorador")

    for encontro in _RE_NUMERO.finditer(codigo):
        marcar(*encontro.span(), "numero")

    for encontro in _RE_FUNCAO.finditer(codigo):
        alvo = encontro.group(1)
        marcar(*encontro.span(1), "palavra" if alvo.lower() in reservadas else "funcao")

    for encontro in _RE_PALAVRA.finditer(codigo):
        alvo = encontro.group()
        if alvo in reservadas or alvo.lower() in reservadas:
            marcar(*encontro.span(), "palavra")

    achados.sort()
    return achados
