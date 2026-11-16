# database/config.py
"""Preferencias globais, com cache em memoria.

A v1 abria uma conexao SQLite a cada leitura de cor/tema -- e a interface le
dezenas dessas chaves ao montar cada tela. Aqui tudo e carregado uma vez e
mantido em memoria; a escrita persiste na hora.
"""
from database.sqlite_db import conexao

PADROES = {
    "tema": "Escuro",
    "fonte_leitura": "Georgia (serifada)",
    "tamanho_fonte": "17",
    "altura_linha": "1.6",
    "largura_coluna": "68",
    "recuo_paragrafo": "0",
    "mostrar_imagens": "1",
    "modo_virada": "Livre",
    "cor_papel": "#1E1E1E",
    "cor_texto": "#D4D4D4",
    "cor_destaque": "#569CD6",
    "velocidade_rolagem": "4",
    "voz_volume": "160",      # 0-200%: acima de 100 amplifica a onda
    "voz_velocidade": "0",    # -10 a 10
    "voz_tom": "0",           # -10 a 10 semitons
    "voz_nome": "",
    "numerar_codigo": "0",
    # Ler o titulo da janela ativa e informacao sensivel: nasce
    # DESLIGADO e nada sai da maquina.
    "contexto_ativo": "0",
    "contexto_memoria": "",
    "sempre_no_topo": "0",
    "opacidade": "1.0",
    "geometria": "",
    "ordenacao_estante": "Recentes",
    "pasta_atual": "todos",
    "pastas_expandidas": "",
    "incluir_subpastas": "1",
    "provedor_ia": "Offline",
    "ollama_url": "http://localhost:11434",
    "ollama_modelo": "llama3",
    "primeira_execucao": "1",
}


class BancoConfig:

    def __init__(self):
        self._cache = {}
        self.recarregar()

    # ------------------------------------------------------------------ basico
    def recarregar(self):
        self._cache = dict(PADROES)
        try:
            with conexao() as conn:
                for linha in conn.execute("SELECT chave, valor FROM configuracoes"):
                    self._cache[linha["chave"]] = linha["valor"]
        except Exception as erro:
            print(f"[Config] Falha ao carregar preferencias: {erro}")

    def obter(self, chave, padrao=None):
        if chave in self._cache:
            return self._cache[chave]
        return padrao if padrao is not None else PADROES.get(chave, "")

    def definir(self, chave, valor):
        valor = "" if valor is None else str(valor)
        if self._cache.get(chave) == valor:
            return
        self._cache[chave] = valor
        try:
            with conexao() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES (?, ?)",
                    (chave, valor))
        except Exception as erro:
            print(f"[Config] Falha ao salvar '{chave}': {erro}")

    # ---------------------------------------------------------------- tipagem
    def obter_int(self, chave, padrao=0):
        try:
            return int(float(self.obter(chave)))
        except (TypeError, ValueError):
            return padrao

    def obter_float(self, chave, padrao=0.0):
        try:
            return float(self.obter(chave))
        except (TypeError, ValueError):
            return padrao

    def obter_bool(self, chave, padrao=False):
        # Chave nunca gravada e sem padrao na tabela: vale o padrao do chamador.
        # Sem esta guarda, uma preferencia nova nascia sempre desligada, mesmo
        # com `padrao=True` explicito.
        if chave not in self._cache:
            return padrao
        valor = str(self._cache[chave]).strip().lower()
        if valor in ("1", "true", "sim", "on"):
            return True
        if valor in ("0", "false", "nao", "off"):
            return False
        return padrao

    def definir_bool(self, chave, valor):
        self.definir(chave, "1" if valor else "0")

    # -------------------------------------------------------------------- IA
    def obter_provedor(self):
        return self.obter("provedor_ia", "Offline")

    def definir_provedor(self, provedor):
        self.definir("provedor_ia", provedor)

    def obter_chave(self, provedor):
        return self.obter(f"api_key_{provedor}", "")

    def definir_chave(self, provedor, chave):
        self.definir(f"api_key_{provedor}", chave or "")
