# ia/providers.py
"""Provedores de IA por HTTP. Nenhum SDK extra -- so `requests`.

Manter o cliente em HTTP puro evita empacotar tres SDKs no executavel, o que
dobraria o tamanho do .exe sem nenhum ganho para o usuario final.
"""
import re

import requests

from ia.provider import AIProvider

# Contexto enviado por requisicao. Uma pagina de livro cabe folgada nisso e o
# limite evita estourar custo/token com PDFs de pagina densa.
LIMITE_CARACTERES = 12000
TEMPO_LIMITE = 45
# Em CPU a primeira resposta carrega o modelo junto; depois disso cai para
# poucos segundos. Curto demais aqui derrubaria justamente a primeira pergunta.
TEMPO_LIMITE_LOCAL = 180


def _cortar(texto: str) -> str:
    texto = (texto or "").strip()
    if len(texto) <= LIMITE_CARACTERES:
        return texto
    return texto[:LIMITE_CARACTERES] + "\n\n[...trecho truncado...]"


def _separar_lista(resposta: str) -> list:
    itens = [i.strip(" -•\t") for i in re.split(r"[,\n;]+", resposta or "") if i.strip()]
    return itens[:8] or ["Sem destaques"]


def _separar_cards(resposta: str) -> list:
    blocos = [b.strip() for b in re.split(r"\n\s*\n", resposta or "") if b.strip()]
    return blocos or ["Nao foi possivel gerar flashcards."]


class _ProvedorHTTP(AIProvider):
    """Prompts compartilhados: so a chamada de rede muda entre provedores."""

    nome = "HTTP"

    def _call(self, prompt: str, limite=None, temperatura=None) -> str:
        raise NotImplementedError

    def summarize(self, text):
        return self._call(
            "Voce e um tutor de estudos. Resuma o texto abaixo em portugues do Brasil, "
            "em 3 a 5 topicos curtos comecando com '•'. Foque nas ideias centrais.\n\n"
            f"{_cortar(text)}")

    def keywords(self, text):
        resposta = self._call(
            "Liste de 5 a 7 conceitos-chave do texto abaixo, separados por virgula. "
            "Responda apenas com a lista, sem numeracao nem explicacao.\n\n"
            f"{_cortar(text)}")
        return _separar_lista(resposta)

    def flashcards(self, text):
        resposta = self._call(
            "Crie 4 flashcards de estudo sobre o texto abaixo, em portugues do Brasil. "
            "Formato exato, separando cada card por uma linha em branco:\n"
            "P: <pergunta>\nR: <resposta>\n\n"
            f"{_cortar(text)}")
        return _separar_cards(resposta)

    def explain(self, text):
        return self._call(
            "Explique o texto abaixo em portugues simples, como se fosse para alguem "
            "que esta vendo o assunto pela primeira vez. Use analogias quando ajudar "
            "e no maximo 2 paragrafos.\n\n"
            f"{_cortar(text)}")

    def ask_question(self, text, question):
        """Igual ao contrato geral, mas com teto REAL de tokens.

        Pedir "responda em 6 linhas" no texto do prompt nao funciona com
        modelo pequeno -- ele ignora e segue escrevendo. Medido: 34 s por uma
        resposta de leitura. O unico teto que ele respeita e o do parametro.
        """
        return self._call(
            "Responda a pergunta usando SOMENTE as informacoes do texto "
            "fornecido. Se a resposta nao estiver no texto, diga isso "
            "claramente.\n\n"
            f"Pergunta: {question}\n\nTexto:\n{_cortar(text)}",
            limite=260)

    def general_question(self, question):
        """Conhecimento geral do modelo. Aqui ele E a fonte, e nao um redator.

        Usado pelo modo "IA" do roteador, quando a pergunta nao pertence ao
        livro aberto (um erro de terminal, por exemplo). A resposta e curta de
        proposito: modelo pequeno divaga e repete quando lhe dao espaco, e a
        medicao mostrou justamente isso -- sem teto, ele escreveu dez itens
        iguais sobre uma classe que nao existe.
        """
        return self._call(
            "Responda de forma direta e pratica, em portugues, em no maximo "
            "8 linhas. Se nao tiver certeza, diga que nao tem certeza.\n\n"
            f"Pergunta: {question}")

    def classificar(self, pergunta):
        """Escolha entre opcoes fechadas -- uma palavra, nao um texto.

        Existe separada das demais porque o custo e outro: as acoes de estudo
        querem paragrafos, esta quer um rotulo. Passar por `explain` fazia o
        modelo escrever dois paragrafos justificando a escolha, e o que era
        para custar um segundo custava vinte.
        """
        return self._call(pergunta)


# Modelos oferecidos em Configuracoes > IA, do mais leve ao mais capaz.
# O leve responde resumo, flashcard e pergunta sobre o livro muito bem e
# custa uma fracao; o maior so compensa em raciocinio longo. Qualquer outro
# nome pode ser digitado no campo "Outro" (fica em "modelo_<provedor>").
MODELOS = {
    "Claude": [
        ("claude-haiku-5-5", "Haiku 5.5 — rapido e barato"),
        ("claude-sonnet-5-5", "Sonnet 5.5 — equilibrio"),
        ("claude-opus-5-5", "Opus 5.5 — o mais capaz"),
    ],
    "OpenAI": [
        ("gpt-4o-mini", "GPT-4o mini — barato (aposenta em jan/2027)"),
        ("gpt-6-luna", "GPT-6 Luna — sucessor indicado pela OpenAI"),
    ],
    "Gemini": [
        ("gemini-flash-lite-latest", "Flash-Lite — o mais leve"),
        ("gemini-flash-latest", "Flash — equilibrio"),
        ("gemini-pro-latest", "Pro — o mais capaz"),
    ],
}


class ClaudeProvider(_ProvedorHTTP):
    nome = "Claude"

    MODELO_PADRAO = "claude-opus-5-5"

    def __init__(self, api_key, modelo=None):
        self.api_key = api_key
        self.modelo = modelo or self.MODELO_PADRAO
        self.url = "https://api.anthropic.com/v1/messages"

    def _call(self, prompt, limite=None, temperatura=None):
        # `limite` e `temperatura` existem para o modelo local. Aqui:
        # - no Opus 5.5 o raciocinio interno e sempre ligado e CONTA no
        #   max_tokens; um teto de 260 terminaria antes de qualquer texto.
        #   O tamanho da resposta fica a cargo do prompt e o esforco "low"
        #   mantem as tarefas curtas rapidas e baratas.
        # - temperatura nao e aceita nos modelos atuais (400).
        corpo = {
            "model": self.modelo,
            "max_tokens": 8000,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": prompt}],
        }
        cabecalhos = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        resposta = requests.post(self.url, headers=cabecalhos, json=corpo,
                                 timeout=TEMPO_LIMITE)
        if resposta.status_code == 400 and "effort" in resposta.text:
            # Modelo antigo digitado a mao (ex.: Haiku 4.5) nao conhece o
            # "effort": tenta de novo sem ele em vez de falhar.
            corpo.pop("output_config", None)
            corpo["max_tokens"] = 4000
            resposta = requests.post(self.url, headers=cabecalhos, json=corpo,
                                     timeout=TEMPO_LIMITE)
        resposta.raise_for_status()
        dados = resposta.json()
        if dados.get("stop_reason") == "refusal":
            # Recusa vem com HTTP 200; levantar faz a fila passar ao proximo.
            raise RuntimeError("o modelo recusou o pedido")
        blocos = dados.get("content", [])
        texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text").strip()
        if not texto:
            raise RuntimeError("resposta vazia")
        return texto


class OpenAIProvider(_ProvedorHTTP):
    nome = "OpenAI"

    MODELO_PADRAO = "gpt-4o-mini"

    def __init__(self, api_key, modelo=None):
        self.api_key = api_key
        self.modelo = modelo or self.MODELO_PADRAO
        self.url = "https://api.openai.com/v1/chat/completions"

    def _call(self, prompt, limite=None, temperatura=None):
        resposta = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            # max_completion_tokens: o campo atual da API (max_tokens e
            # recusado pelos modelos com raciocinio). Nos que raciocinam o
            # teto inclui o raciocinio, entao fica mais folgado.
            json={"model": self.modelo,
                  "max_completion_tokens": (max(400, limite or 1400)
                                            if self.modelo.startswith("gpt-4") else 6000),
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=TEMPO_LIMITE,
        )
        resposta.raise_for_status()
        return resposta.json()["choices"][0]["message"]["content"].strip()


class GeminiProvider(_ProvedorHTTP):
    nome = "Gemini"

    # Alias mantido pelo Google apontando sempre para o Flash vigente:
    # um nome fixo ("gemini-2.0-flash") vira 404 quando o modelo e aposentado.
    MODELO_PADRAO = "gemini-flash-latest"

    def __init__(self, api_key, modelo=None):
        self.api_key = api_key
        modelo = modelo or self.MODELO_PADRAO
        self.modelo = modelo
        self.url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{modelo}:generateContent")

    def _call(self, prompt, limite=None, temperatura=None):
        resposta = requests.post(
            self.url,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=TEMPO_LIMITE,
        )
        resposta.raise_for_status()
        candidatos = resposta.json().get("candidates", [])
        if not candidatos:
            raise RuntimeError("resposta vazia")
        partes = candidatos[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in partes).strip()


class LocalProvider(_ProvedorHTTP):
    """Qualquer servidor local que fale o dialeto da OpenAI.

    Um cliente so atende llama-server, LM Studio, Jan e LocalAI: os quatro
    expoem `/v1/chat/completions` com o mesmo formato. O tempo limite e mais
    largo que o da nuvem porque em CPU a primeira resposta inclui a carga do
    modelo, e mais curto que o infinito porque o usuario esta esperando.
    """

    nome = "IA local"

    def __init__(self, url_base, modelo, rotulo=None, ao_usar=None):
        self.url_base = (url_base or "").rstrip("/")
        self.modelo = modelo or "local"
        self.nome = rotulo or "IA local"
        # Avisa quem administra o servidor que ele acabou de ser usado, para o
        # desligamento por ociosidade nao cortar no meio de uma sessao.
        self._ao_usar = ao_usar

    def _call(self, prompt, limite=900, temperatura=0.3):
        if self._ao_usar:
            try:
                self._ao_usar()
            except Exception:
                pass
        resposta = requests.post(
            f"{self.url_base}/v1/chat/completions",
            json={"model": self.modelo, "max_tokens": limite,
                  "temperature": temperatura,
                  "messages": [{"role": "user", "content": prompt}],
                  "stream": False},
            timeout=TEMPO_LIMITE_LOCAL,
        )
        resposta.raise_for_status()
        escolhas = resposta.json().get("choices") or []
        if not escolhas:
            raise RuntimeError("resposta vazia")
        return (escolhas[0].get("message", {}).get("content") or "").strip()

    def classificar(self, pergunta):
        # Doze tokens cabem "programacao" com folga. O modelo gera palavra por
        # palavra, entao o teto e o que separa uma resposta de um segundo de
        # uma de vinte.
        #
        # Temperatura zero porque isto e uma escolha entre opcoes fixas, nao
        # redacao: com 0,3 a mesma janela era classificada de um jeito numa
        # volta do monitor e de outro na seguinte.
        return self._call(pergunta, limite=12, temperatura=0.0)


class OllamaProvider(_ProvedorHTTP):
    nome = "Ollama"

    def __init__(self, url_base="http://localhost:11434", modelo="llama3"):
        self.url_base = (url_base or "http://localhost:11434").rstrip("/")
        self.modelo = modelo or "llama3"

    def disponivel(self) -> bool:
        try:
            return requests.get(f"{self.url_base}/api/tags", timeout=1.2).ok
        except Exception:
            return False

    def _call(self, prompt, limite=None, temperatura=None):
        corpo = {"model": self.modelo, "prompt": prompt, "stream": False}
        opcoes = {}
        if limite:
            opcoes["num_predict"] = limite
        if temperatura is not None:
            opcoes["temperature"] = temperatura
        if opcoes:
            corpo["options"] = opcoes
        resposta = requests.post(
            f"{self.url_base}/api/generate", json=corpo,
            timeout=TEMPO_LIMITE_LOCAL,
        )
        resposta.raise_for_status()
        return (resposta.json().get("response") or "").strip()

    def classificar(self, pergunta):
        return self._call(pergunta, limite=12, temperatura=0.0)
