# ia/offline.py
"""Assistente offline: analise puramente local, sem rede nem chave de API.

E o ultimo da fila de fallback, entao precisa sempre responder alguma coisa util.
A versao anterior apenas pegava as frases mais compridas; aqui ha pontuacao por
frequencia de termos, peso de posicao e deteccao de definicoes.
"""
import re
import unicodedata
from collections import Counter

from ia.provider import AIProvider

_STOPWORDS = {
    "a", "ao", "aos", "aquela", "aquelas", "aquele", "aqueles", "aquilo", "as", "ate",
    "com", "como", "da", "das", "de", "dela", "delas", "dele", "deles", "depois", "do",
    "dos", "e", "ela", "elas", "ele", "eles", "em", "entre", "era", "eram", "essa",
    "essas", "esse", "esses", "esta", "estas", "este", "estes", "eu", "foi", "fomos",
    "for", "foram", "isso", "isto", "ja", "la", "lhe", "lhes", "mais", "mas", "me",
    "mesmo", "meu", "minha", "muito", "na", "nao", "nas", "nem", "no", "nos", "nossa",
    "nosso", "num", "numa", "o", "os", "ou", "para", "pela", "pelas", "pelo", "pelos",
    "por", "porque", "qual", "quando", "que", "quem", "sao", "se", "seja", "sem", "ser",
    "seu", "seus", "so", "sob", "sobre", "sua", "suas", "tambem", "tem", "tinha", "toda",
    "todas", "todo", "todos", "um", "uma", "umas", "uns", "voce", "voces", "the", "of",
    "and", "to", "in", "is", "for", "that", "with", "as", "on", "by", "an", "be", "this",
    "are", "from", "or", "it", "at", "was", "were", "which", "can", "has", "have",
}

_RE_PALAVRA = re.compile(r"\b[\wáàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ]+\b", re.UNICODE)
_RE_FIM_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ0-9])")
_RE_ABREV = re.compile(r"\b(sr|sra|dr|dra|prof|etc|ex|fig|pag|vol|ed|art)\.\s*$", re.I)


def _normalizar(palavra):
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", palavra)
                         if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


def _frases(texto):
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if not texto:
        return []
    bruto = _RE_FIM_FRASE.split(texto)

    # Recola quebras causadas por abreviacoes ("Dr. Silva" nao sao duas frases).
    frases = []
    for parte in bruto:
        if frases and _RE_ABREV.search(frases[-1]):
            frases[-1] = frases[-1] + " " + parte
        else:
            frases.append(parte)
    return [f.strip() for f in frases if len(f.strip()) > 25]


def _termos(texto):
    return [_normalizar(p) for p in _RE_PALAVRA.findall(texto or "")
            if len(p) > 3 and _normalizar(p) not in _STOPWORDS]


class OfflineAssistant(AIProvider):

    nome = "Offline"

    # ------------------------------------------------------------------ resumo
    def summarize(self, text):
        frases = _frases(text)
        if len(frases) < 2:
            return ("Texto curto demais para resumir.\n\n"
                    "Paginas de abertura de capitulo e paginas digitalizadas costumam "
                    "cair nesse caso.")

        frequencia = Counter(_termos(text))
        if not frequencia:
            return frases[0]
        maior = max(frequencia.values())

        pontuados = []
        total = len(frases)
        for indice, frase in enumerate(frases):
            termos = _termos(frase)
            if not termos:
                continue
            # Media normalizada evita que frases longas vencam so por tamanho.
            base = sum(frequencia[t] / maior for t in termos) / len(termos)
            # Comeco e fim de pagina concentram tese e conclusao.
            posicao = 1.18 if indice < total * 0.2 else (1.06 if indice > total * 0.8 else 1.0)
            tamanho = 0.75 if len(termos) < 5 else 1.0
            pontuados.append((base * posicao * tamanho, indice, frase))

        if not pontuados:
            return frases[0]

        quantidade = max(2, min(5, total // 3))
        melhores = sorted(pontuados, key=lambda item: item[0], reverse=True)[:quantidade]
        melhores.sort(key=lambda item: item[1])      # devolve na ordem do texto

        linhas = ["Resumo extraido da pagina:", ""]
        linhas += [f"• {frase.rstrip('.')}." for _, _, frase in melhores]
        return "\n".join(linhas)

    # -------------------------------------------------------------- conceitos
    def keywords(self, text):
        termos = _termos(text)
        if not termos:
            return ["Sem destaques"]

        contagem = Counter(termos)
        # Bigramas capturam termos compostos ("energia potencial", "guerra fria").
        bigramas = Counter()
        for anterior, atual in zip(termos, termos[1:]):
            bigramas[f"{anterior} {atual}"] += 1

        resultado = []
        for expressao, vezes in bigramas.most_common(4):
            if vezes >= 2:
                resultado.append(expressao.title())

        for palavra, _ in contagem.most_common(12):
            if len(resultado) >= 7:
                break
            if not any(palavra in item.lower() for item in resultado):
                resultado.append(palavra.capitalize())

        return resultado or ["Sem destaques"]

    # ------------------------------------------------------------- flashcards
    def flashcards(self, text):
        frases = _frases(text)
        frequencia = Counter(_termos(text))
        cards = []

        # 1. Frases de definicao viram perguntas diretas -- as mais uteis.
        padrao = re.compile(
            r"^(.{4,70}?)\s+(?:e|sao|era|eram|significa|consiste em|define-se como|"
            r"refere-se a|corresponde a)\s+(.{15,220})$", re.IGNORECASE)
        for frase in frases:
            achado = padrao.match(frase.strip())
            if achado:
                conceito = achado.group(1).strip(" ,;")
                definicao = achado.group(2).strip(" .")
                cards.append(f"P: O que e {conceito}?\nR: {definicao}.")
            if len(cards) >= 3:
                break

        # 2. Completar lacuna, escondendo o termo mais relevante da frase.
        if len(cards) < 4:
            for frase in frases:
                if len(cards) >= 4:
                    break
                termos = [t for t in _termos(frase) if frequencia[t] >= 2]
                if not termos or len(frase) > 220:
                    continue
                alvo = max(termos, key=lambda t: frequencia[t])
                lacuna = re.sub(rf"\b{re.escape(alvo)}\w*\b", "______", frase,
                                count=1, flags=re.IGNORECASE)
                if "______" in lacuna:
                    cards.append(f"P: {lacuna}\nR: {alvo}")

        if not cards:
            return ["Nao consegui montar flashcards com esta pagina.\n\n"
                    "Paginas com pouco texto corrido, tabelas ou formulas costumam "
                    "render pouco no modo offline."]
        return cards

    # -------------------------------------------------------------- explicacao
    def explain(self, text):
        resumo = self.summarize(text)
        conceitos = ", ".join(self.keywords(text)[:5])
        return (f"{resumo}\n\n"
                f"Conceitos centrais: {conceitos}.\n\n"
                "Observacao: o modo offline analisa o texto estatisticamente, sem "
                "reescreve-lo. Para explicacoes em linguagem propria, configure uma "
                "chave de API ou o Ollama em Configuracoes.")

    # ------------------------------------------------------------------- Q&A
    def ask_question(self, text, question):
        frases = _frases(text)
        if not frases:
            return "Nao ha texto suficiente nesta pagina para procurar a resposta."

        termos_pergunta = set(_termos(question))
        if not termos_pergunta:
            return "Reformule a pergunta com palavras mais especificas."

        frequencia = Counter(_termos(text))
        total_frases = len(frases)
        # Termo raro no texto identifica melhor a frase certa que um termo comum.
        pesos = {t: 1.0 / (1 + frequencia.get(t, 0)) ** 0.5 for t in termos_pergunta}

        melhores = []
        for indice, frase in enumerate(frases):
            termos = set(_termos(frase))
            comuns = termos_pergunta & termos
            if not comuns:
                continue
            melhores.append((sum(pesos[t] for t in comuns), indice, frase))

        if not melhores:
            return ("Nao encontrei nada relacionado a essa pergunta nesta pagina.\n\n"
                    "Dica: use Ctrl+F para buscar o termo no livro inteiro.")

        melhores.sort(reverse=True)
        partes = ["Trechos desta pagina que respondem a sua pergunta:", ""]
        for _, indice, frase in melhores[:3]:
            contexto = ""
            if indice + 1 < total_frases and len(frase) < 140:
                contexto = " " + frases[indice + 1]
            partes.append(f"• …{frase}{contexto}")
        partes.append("")
        partes.append("(Modo offline: os trechos sao localizados no texto, nao reescritos.)")
        return "\n".join(partes)
