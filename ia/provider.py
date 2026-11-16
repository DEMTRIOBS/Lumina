# ia/provider.py
from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Contrato comum a todos os provedores, online ou offline."""

    nome = "Provedor"

    @abstractmethod
    def summarize(self, text: str) -> str:
        """Resumo em topicos do texto."""

    @abstractmethod
    def keywords(self, text: str) -> list:
        """Conceitos-chave, do mais relevante ao menos."""

    @abstractmethod
    def flashcards(self, text: str) -> list:
        """Cartoes de estudo no formato 'P: ...\\nR: ...'."""

    @abstractmethod
    def explain(self, text: str) -> str:
        """Explicacao em linguagem simples."""

    @abstractmethod
    def ask_question(self, text: str, question: str) -> str:
        """Resposta baseada exclusivamente no texto fornecido."""

    def general_question(self, question: str) -> str:
        """Resposta de conhecimento geral, SEM texto de apoio.

        Separada de `ask_question` porque a pergunta ao modelo e outra: ali e
        "responda a partir deste texto", aqui e "responda do que voce sabe".
        Passar texto vazio para a primeira fazia o modelo responder que nao
        conseguia entender o texto fornecido -- o que estava correto, ja que
        nao havia texto nenhum.

        O padrao recusa: quem nao tem conhecimento proprio (o modo offline, que
        so sabe recortar o texto que recebe) precisa dizer isso em vez de
        inventar.
        """
        return ("Este provedor so responde a partir de um texto; nao tem "
                "conhecimento proprio para esta pergunta.")
