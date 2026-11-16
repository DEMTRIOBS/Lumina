# services/dicionario.py
"""Definir / traduzir a palavra (ou frase curta) selecionada no livro.

Tudo gratuito e sem chave de API -- o leitor nao pode exigir cadastro so para
explicar uma palavra.

FONTES (respostas conferidas com chamadas reais em 2026-11):
  Definicao EN   https://api.dictionaryapi.dev/api/v2/entries/en/<palavra>
                 200 -> [{"word", "meanings": [{"partOfSpeech",
                 "definitions": [{"definition", ...}]}]}]; 404 -> {"title":
                 "No Definitions Found", ...}
  Definicao PT   pt.wiktionary.org, MediaWiki API (action=parse, wikitext).
                 O endpoint REST /page/definition/ do Wikcionario em portugues
                 responde 501 (so existe no en.wiktionary), entao a definicao
                 sai do wikitext: linhas "# ..." dentro da secao ={{-pt-}}=.
                 Verbete que so remete ("# ver [[efémero]]") e seguido uma vez.
  Traducao       https://api.mymemory.translated.net/get?q=..&langpair=en|pt-BR
                 {"responseData": {"translatedText"}, "responseStatus": 200,
                 "quotaFinished": false}. Anonimo: ~5000 caracteres/dia por IP
                 e 500 bytes por consulta; ao estourar a cota responde
                 responseStatus 403/429 ou um texto "MYMEMORY WARNING ...".

PRIVACIDADE: so o texto selecionado sai da maquina -- nada de titulo do
livro, pagina ou contexto. User-Agent generico ("Lumina-leitor"), sem e-mail
(o MyMemory daria cota maior com e-mail, mas isso identificaria a pessoa).

USO: as funcoes fazem rede e podem levar ate ~5 s cada; a interface deve
chamar `consultar` fora da thread do Tk.
"""
import html
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests

TEMPO_LIMITE = 5
CABECALHOS = {"User-Agent": "Lumina-leitor"}
MAX_CARACTERES = 450          # MyMemory aceita 500 bytes por consulta
MAX_PALAVRAS_DEFINICAO = 3    # frase longa nao tem verbete: so traduz
MAX_DEFINICOES = 6

URL_DICIONARIO_EN = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"
URL_WIKCIONARIO_PT = "https://pt.wiktionary.org/w/api.php"
URL_TRADUCAO = "https://api.mymemory.translated.net/get"


@dataclass
class Consulta:
    """Resultado pronto para um popup."""
    termo: str
    definicoes: List[str] = field(default_factory=list)
    traducao: Optional[str] = None
    fonte: str = ""                 # ex.: "Free Dictionary API + MyMemory"
    erro: Optional[str] = None      # mensagem para o usuario; None = tudo certo
    idioma_origem: str = "en"       # "en" | "pt"
    idioma_destino: str = "pt"


# ======================================================== deteccao de idioma
_STOP_PT = {
    "a", "o", "os", "as", "um", "uma", "de", "do", "da", "dos", "das", "em", "no",
    "na", "nos", "nas", "que", "e", "para", "por", "com", "não", "nao", "se", "mais",
    "como", "mas", "ao", "aos", "ou", "seu", "sua", "isso", "este", "esta", "pelo",
    "pela", "foi", "ser", "são", "sao", "é", "tem", "também", "quando", "muito",
}
_STOP_EN = {
    "the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was", "were",
    "be", "for", "on", "with", "as", "by", "at", "it", "this", "that", "from",
    "not", "but", "you", "we", "they", "he", "she", "which", "have", "has", "will",
    "can", "if", "into", "its", "their", "there", "what", "when", "how",
}
_ACENTOS_PT = set("ãõçáéíóúâêôàü")
# Sem "-er" nem "-e": "computer" e "closure" sao ingles. Errar aqui custa pouco
# -- se o dicionario do idioma chutado nao conhece a palavra, tenta o outro.
_FINAIS_PT = ("ção", "ções", "mente", "dade", "agem", "ência", "ância", "eiro",
              "eira", "inho", "inha", "ões", "ãos", "ar", "ir", "oso", "osa")
_FINAIS_EN = ("tion", "ing", "ness", "ship", "ed", "ly", "able", "ible", "ment",
              "ful", "less", "ous", "ize", "ise", "th", "ck", "w", "y", "k")


def detectar_idioma(texto: str) -> str:
    """'pt' ou 'en'. Heuristica barata, sem rede.

    Ordem: acentos tipicos do portugues; depois palavras-funcao (stopwords);
    para uma palavra solta, terminacoes. Empate fica em ingles -- a maior
    parte dos livros tecnicos lidos aqui e em ingles.
    """
    minusculo = (texto or "").lower()
    if any(c in _ACENTOS_PT for c in minusculo):
        return "pt"
    palavras = re.findall(r"[a-zà-ÿ']+", minusculo)
    if not palavras:
        return "en"
    pt = sum(1 for p in palavras if p in _STOP_PT)
    en = sum(1 for p in palavras if p in _STOP_EN)
    if pt != en and (pt or en):
        return "pt" if pt > en else "en"
    if len(palavras) == 1:
        p = palavras[0]
        if p.endswith(_FINAIS_EN):
            return "en"
        # Palavra portuguesa costuma terminar em -a/-o ("casa", "objeto").
        if p.endswith(_FINAIS_PT) or (p[-1] in "ao" and not p.endswith("oo")):
            return "pt"
    return "en"


# ===================================================================== cache
class _CacheLRU:
    """LRU em memoria, seguro entre threads. So guarda consultas SEM erro --
    uma falha de rede nao pode ficar 'grudada' ate fechar o programa."""

    def __init__(self, tamanho=256):
        self.tamanho = tamanho
        self._dados = OrderedDict()
        self._trava = threading.Lock()

    def obter(self, chave):
        with self._trava:
            if chave in self._dados:
                self._dados.move_to_end(chave)
                return self._dados[chave]
        return None

    def guardar(self, chave, valor):
        with self._trava:
            self._dados[chave] = valor
            self._dados.move_to_end(chave)
            while len(self._dados) > self.tamanho:
                self._dados.popitem(last=False)

    def limpar(self):
        with self._trava:
            self._dados.clear()


_cache = _CacheLRU()


def limpar_cache():
    _cache.limpar()


# ================================================================ definicoes
class _SemResultado(Exception):
    """A fonte respondeu, mas nao conhece o termo (diferente de falha de rede)."""


def _definir_en(termo: str) -> List[str]:
    resposta = requests.get(URL_DICIONARIO_EN.format(requests.utils.quote(termo.lower())),
                            headers=CABECALHOS, timeout=TEMPO_LIMITE)
    if resposta.status_code == 404:
        raise _SemResultado()
    resposta.raise_for_status()
    dados = resposta.json()
    definicoes = []
    for entrada in dados if isinstance(dados, list) else []:
        for sentido in entrada.get("meanings", []) or []:
            classe = sentido.get("partOfSpeech") or ""
            # Duas por classe gramatical: o popup e pequeno, e a terceira
            # acepcao de "run" raramente e a que o livro usa.
            for item in (sentido.get("definitions") or [])[:2]:
                texto = (item.get("definition") or "").strip()
                if texto:
                    definicoes.append(f"({classe}) {texto}" if classe else texto)
    if not definicoes:
        raise _SemResultado()
    return definicoes[:MAX_DEFINICOES]


_RE_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_RE_ESCOPO = re.compile(r"\{\{(?:escopo2?|escopoCat|escopoLing)\|[^|}]*\|([^}]*)\}\}", re.I)
_RE_MODELO = re.compile(r"\{\{[^{}]*\}\}")
_RE_REMISSAO = re.compile(r"^\s*(?:ver|o mesmo que|mesmo que|forma de)\s+\[\[([^\]|#]+)", re.I)


def _limpar_wikitexto(linha: str) -> str:
    linha = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", linha, flags=re.S)
    linha = re.sub(r"<[^>]+>", "", linha)
    # {{escopo|pt|Informática}} vira "(Informática)": diz o campo da acepcao.
    linha = _RE_ESCOPO.sub(lambda m: "(" + m.group(1).replace("|", ", ") + ")", linha)
    for _ in range(3):                       # modelos aninhados
        linha = _RE_MODELO.sub("", linha)
    linha = _RE_LINK.sub(r"\1", linha)
    linha = linha.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", linha).strip(" .;:")


def _secao_portugues(wikitexto: str) -> List[str]:
    linhas, dentro = [], False
    for linha in wikitexto.splitlines():
        cabecalho = re.match(r"^=\s*\{\{-(\w+)-\}\}\s*=\s*$", linha.strip())
        if cabecalho:
            dentro = cabecalho.group(1) == "pt"
            continue
        if dentro:
            linhas.append(linha)
    return linhas


def _wikitexto_pt(titulo: str) -> Optional[str]:
    resposta = requests.get(URL_WIKCIONARIO_PT, params={
        "action": "parse", "page": titulo, "prop": "wikitext", "format": "json",
        "formatversion": 2, "redirects": 1,
    }, headers=CABECALHOS, timeout=TEMPO_LIMITE)
    resposta.raise_for_status()
    dados = resposta.json()
    if "error" in dados:                     # missingtitle
        return None
    return (dados.get("parse") or {}).get("wikitext")


def _definir_pt(termo: str, _profundidade=0) -> List[str]:
    wikitexto = _wikitexto_pt(termo.lower()) or (
        _wikitexto_pt(termo) if termo != termo.lower() else None)
    if not wikitexto:
        raise _SemResultado()
    definicoes, remissao = [], None
    for linha in _secao_portugues(wikitexto):
        # "# " e acepcao; "#:" e "#*" sao exemplos e citacoes.
        if not linha.startswith("#") or linha[1:2] in (":", "*", "#"):
            continue
        corpo = linha[1:].strip()
        alvo = _RE_REMISSAO.match(corpo)
        if alvo and remissao is None:
            remissao = alvo.group(1).strip()
        texto = _limpar_wikitexto(corpo)
        if texto and not alvo:
            definicoes.append(texto)
    if not definicoes and remissao and _profundidade == 0:
        # "efêmero" so diz "ver efémero" (grafia europeia): segue uma vez.
        return _definir_pt(remissao, 1)
    if not definicoes:
        raise _SemResultado()
    return definicoes[:MAX_DEFINICOES]


# ================================================================== traducao
def traduzir(texto: str, origem: str, destino: str) -> str:
    """Traducao pelo MyMemory. Levanta RuntimeError com mensagem amigavel."""
    pares = {"en": "en", "pt": "pt-BR"}
    resposta = requests.get(URL_TRADUCAO, params={
        "q": texto, "langpair": f"{pares.get(origem, origem)}|{pares.get(destino, destino)}",
    }, headers=CABECALHOS, timeout=TEMPO_LIMITE)
    if resposta.status_code == 429:
        raise RuntimeError("limite diario de traducoes gratuitas atingido")
    resposta.raise_for_status()
    dados = resposta.json()
    traducao = html.unescape(((dados.get("responseData") or {}).get("translatedText") or "")).strip()
    status = dados.get("responseStatus")
    if dados.get("quotaFinished") or "MYMEMORY WARNING" in traducao.upper():
        raise RuntimeError("limite diario de traducoes gratuitas atingido")
    if str(status) != "200" or not traducao:
        detalhe = dados.get("responseDetails") or f"status {status}"
        raise RuntimeError(f"servico de traducao recusou: {detalhe}")
    return traducao


# ======================================================================= API
def _normalizar(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()
    # Pontuacao arrastada junto na selecao ("closure," / "(closure)").
    return texto.strip(" \t.,;:!?()[]{}\"'“”‘’«»—–-")


def consultar(texto: str, idioma: Optional[str] = None, definir: bool = True,
              traduzir_texto: bool = True) -> Consulta:
    """Define e/ou traduz a selecao. Nunca levanta: problemas vao em `erro`.

    idioma: 'en' | 'pt' | None (detecta). EN->PT por padrao; PT->EN caso
    contrario. Definicao so para ate MAX_PALAVRAS_DEFINICAO palavras; frase
    mais longa e so traduzida. Se o dicionario do idioma detectado nao conhece
    a palavra, tenta o outro (deteccao de palavra solta e chute) e, se achar,
    corrige o idioma e o sentido da traducao.
    """
    termo = _normalizar(texto)[:MAX_CARACTERES]
    if not termo:
        return Consulta(termo="", erro="Nada selecionado.")
    origem = idioma if idioma in ("en", "pt") else detectar_idioma(termo)

    chave = (termo.lower(), idioma, definir, traduzir_texto)
    guardado = _cache.obter(chave)
    if guardado is not None:
        return guardado

    resultado = Consulta(termo=termo, idioma_origem=origem,
                         idioma_destino="en" if origem == "pt" else "pt")
    fontes, erros = [], []

    if definir and len(termo.split()) <= MAX_PALAVRAS_DEFINICAO:
        ordem = [origem] if idioma else [origem, "pt" if origem == "en" else "en"]
        for lingua in ordem:
            try:
                definicoes, fonte = _definir(termo, lingua)
            except _SemResultado:
                continue
            except (requests.RequestException, ValueError) as erro:
                erros.append(_mensagem_rede("dicionario", erro))
                continue
            resultado.definicoes = definicoes
            resultado.idioma_origem = lingua
            resultado.idioma_destino = "en" if lingua == "pt" else "pt"
            fontes.append(fonte)
            break

    if traduzir_texto:
        try:
            traducao = _ajustar_caixa(termo, traduzir(termo, resultado.idioma_origem,
                                                      resultado.idioma_destino))
            # Palavra que o servico nao conhece volta igual: nao e traducao.
            if traducao.lower() != termo.lower():
                resultado.traducao = traducao
                fontes.append("MyMemory")
        except RuntimeError as erro:
            erros.append(f"Traducao indisponivel: {erro}.")
        except (requests.RequestException, ValueError) as erro:
            erros.append(_mensagem_rede("traducao", erro))

    resultado.fonte = " + ".join(fontes)
    if erros:
        resultado.erro = " ".join(erros)
    elif not resultado.definicoes and resultado.traducao is None:
        resultado.erro = "Nenhuma definicao ou traducao encontrada."

    # Erro de rede nao vai para o cache; "palavra desconhecida" vai.
    if not erros:
        _cache.guardar(chave, resultado)
    return resultado


def _ajustar_caixa(original: str, traducao: str) -> str:
    """A memoria de traducao as vezes devolve 'FECHAMENTO' para 'closure':
    a caixa da resposta segue a da selecao."""
    if traducao.isupper() and not original.isupper():
        traducao = traducao.lower()
        if original[:1].isupper():
            traducao = traducao[:1].upper() + traducao[1:]
    elif original.islower() and len(original.split()) == 1 and traducao[:1].isupper():
        traducao = traducao[:1].lower() + traducao[1:]
    return traducao


def _definir(termo: str, lingua: str) -> Tuple[List[str], str]:
    if lingua == "pt":
        return _definir_pt(termo), "Wikcionario"
    return _definir_en(termo), "Free Dictionary API"


def _mensagem_rede(o_que: str, erro: Exception) -> str:
    if isinstance(erro, requests.Timeout):
        return f"Sem resposta do servico de {o_que} (tempo esgotado)."
    if isinstance(erro, requests.ConnectionError):
        return f"Sem conexao com o servico de {o_que}."
    return f"Falha no servico de {o_que}."
