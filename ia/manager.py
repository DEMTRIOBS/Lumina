# ia/manager.py
"""Orquestra os provedores de IA com fallback em cascata.

Ordem: chaves de API configuradas -> IA local -> assistente offline.
Se um provedor falhar (rede caiu, chave invalida, cota estourada), o proximo
assume, entao o botao Processar nunca devolve tela vazia.

A IA LOCAL NAO PRECISA DE CONFIGURACAO. O modulo `ia.local` procura sozinho um
servidor ja aberto na maquina (Ollama, LM Studio, Jan), e escolhe entre os
modelos dele o MENOR -- as tarefas daqui sao resumir e classificar, e um modelo
de 0,5B faz isso em cerca de um segundo enquanto um de 7B levaria quase um
minuto em CPU. Nao havendo servidor nenhum, entra o motor que o proprio leitor
baixou, se o usuario tiver aceitado instala-lo.

A descoberta e feita UMA vez e guardada por alguns minutos: sondar cinco portas
antes de cada pergunta somaria segundos de espera a cada clique.
"""
import time

from ia.offline import OfflineAssistant
from ia.providers import (ClaudeProvider, GeminiProvider, LocalProvider,
                          OllamaProvider, OpenAIProvider)

# Quanto tempo confiar na ultima descoberta. Curto o bastante para o usuario
# abrir o LM Studio no meio da sessao e o leitor perceber sem reiniciar.
VALIDADE_DESCOBERTA = 120


def _curto(modelo):
    """Nome do modelo sem o caminho do repositorio nem a extensao."""
    nome = (modelo or "").split("/")[-1]
    for sufixo in (".gguf", ":latest"):
        if nome.lower().endswith(sufixo):
            nome = nome[:-len(sufixo)]
    return nome[:34]

# Rotulo da acao na interface -> metodo do provedor.
ACOES = {
    "Resumo da pagina": "summarize",
    "Palavras-chave": "keywords",
    "Gerar flashcards": "flashcards",
    "Explicar de forma simples": "explain",
    "Responder pergunta": "ask_question",
    # Nao aparece no seletor da interface: quem usa e o roteador, no modo em
    # que a pergunta nao pertence ao livro aberto.
    "Pergunta geral": "general_question",
}


class AIManager:

    def __init__(self, banco_config):
        self.config = banco_config
        self.offline = OfflineAssistant()
        self._descoberta = None
        self._descoberto_em = 0.0

    # ------------------------------------------------------------- ia local
    def descobrir_local(self, forcar=False):
        """(rotulo, url, dialeto, modelo) do servidor local, ou None.

        O resultado fica em cache: subir o proprio motor custa segundos e
        sondar as portas custa fracoes de segundo -- nenhum dos dois pode
        acontecer a cada clique em Processar.
        """
        from ia import local

        agora = time.time()
        if not forcar and agora - self._descoberto_em < VALIDADE_DESCOBERTA:
            return self._descoberta
        try:
            self._descoberta = local.descobrir()
        except Exception as erro:
            print(f"[IA] Falha ao procurar servidor local: {erro}")
            self._descoberta = None
        self._descoberto_em = agora
        return self._descoberta

    def _provedor_local(self):
        achado = self.descobrir_local()
        if not achado:
            return None
        from ia import local

        rotulo, url_base, dialeto, modelo = achado
        if dialeto == "ollama":
            provedor = OllamaProvider(url_base, modelo)
        else:
            provedor = LocalProvider(
                url_base, modelo, rotulo=rotulo,
                ao_usar=local.ServidorProprio.instancia().marcar_uso)
        # O nome do modelo entra no rotulo porque e a unica pista visivel de
        # QUEM respondeu -- e o usuario nunca escolheu isso.
        return (f"{rotulo} · {_curto(modelo)}", provedor)

    def _modelo(self, provedor):
        try:
            return (self.config.obter(f"modelo_{provedor}", "") or "").strip() or None
        except Exception:
            return None

    # ------------------------------------------------------------------ fila
    def _montar_fila(self):
        fila = []
        preferido = self.config.obter_provedor()

        construtores = {
            # Modelo ajustavel sem recompilar: chave "modelo_<provedor>" no
            # banco de configuracao; vazio = padrao do provedor.
            "Claude": lambda k: ClaudeProvider(k, self._modelo("Claude")),
            "OpenAI": lambda k: OpenAIProvider(k, self._modelo("OpenAI")),
            "Gemini": lambda k: GeminiProvider(k, self._modelo("Gemini")),
        }

        disponiveis = []
        for nome, construir in construtores.items():
            chave = (self.config.obter_chave(nome) or "").strip()
            if chave:
                disponiveis.append((nome, construir(chave)))

        # O provedor escolhido em Configuracoes vai para a frente da fila.
        disponiveis.sort(key=lambda item: item[0] != preferido)
        fila.extend(disponiveis)

        local = self._provedor_local()
        if local is not None:
            # A IA local vai para a frente quando foi escolhida, e tambem
            # quando nao ha chave nenhuma -- ali ela E o assistente, e ficar
            # atras de provedores que nem existem so atrasaria a resposta.
            if preferido in ("Local", "Ollama", "Offline") or not disponiveis:
                fila.insert(0, local)
            else:
                fila.append(local)

        fila.append(("Modo offline", self.offline))
        return fila

    def descrever_fila(self):
        nomes = []
        for nome, provedor in self._montar_fila():
            modelo = getattr(provedor, "modelo", None)
            # Provedores de nuvem: mostra o modelo escolhido (Haiku, Sonnet...).
            if modelo and nome in ("Claude", "OpenAI", "Gemini"):
                nome = f"{nome} · {_curto(modelo)}"
            nomes.append(nome)
        return " → ".join(nomes)

    # --------------------------------------------------------------- execucao
    def executar_acao(self, acao, texto, pergunta=None):
        metodo = ACOES.get(acao)
        if metodo is None:
            return f"Acao desconhecida: {acao}"
        if metodo in ("ask_question", "general_question") and not (pergunta or "").strip():
            return "Digite sua pergunta no campo acima."

        erros = []
        for nome, provedor in self._montar_fila():
            # Ollama so entra na fila real se estiver mesmo no ar: sem isso a
            # espera pelo timeout travava o assistente por dezenas de segundos.
            if isinstance(provedor, OllamaProvider) and not provedor.disponivel():
                erros.append(f"{nome}: servidor nao respondeu")
                continue
            try:
                if metodo == "ask_question":
                    bruto = provedor.ask_question(texto, pergunta)
                elif metodo == "general_question":
                    bruto = provedor.general_question(pergunta)
                else:
                    bruto = getattr(provedor, metodo)(texto)

                resultado = self._formatar(acao, bruto)
                if not resultado.strip():
                    raise RuntimeError("resposta vazia")

                rodape = ""
                if erros:
                    rodape = "\n\n— tentativas anteriores: " + "; ".join(erros)
                return f"[{nome}]\n\n{resultado}{rodape}"

            except Exception as erro:
                erros.append(f"{nome}: {self._resumir_erro(erro)}")
                continue

        return ("Nenhum provedor conseguiu responder.\n\n" + "\n".join(erros))

    @staticmethod
    def _formatar(acao, bruto):
        if acao == "Palavras-chave":
            itens = bruto if isinstance(bruto, list) else [str(bruto)]
            return "Conceitos centrais desta pagina:\n\n" + "\n".join(f"• {i}" for i in itens)
        if acao == "Gerar flashcards":
            cards = bruto if isinstance(bruto, list) else [str(bruto)]
            return "\n\n".join(f"[{i}]  {c}" for i, c in enumerate(cards, 1))
        return bruto if isinstance(bruto, str) else str(bruto)

    @staticmethod
    def _resumir_erro(erro):
        texto = str(erro)
        if "401" in texto or "403" in texto:
            return "chave de API invalida ou sem permissao"
        if "429" in texto:
            return "limite de uso atingido"
        if "timeout" in texto.lower() or "timed out" in texto.lower():
            return "tempo esgotado"
        if "ConnectionError" in type(erro).__name__ or "Connection" in texto:
            return "sem conexao"
        return texto[:90]
