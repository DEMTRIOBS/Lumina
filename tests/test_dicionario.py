"""Testes do services/dicionario.py, sem rede.

As respostas simuladas copiam o formato real das APIs (conferido com uma
chamada de verdade a cada uma em 2026-11).

Rodar:  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from services import dicionario  # noqa: E402

EN_CLOSURE = [{
    "word": "closure",
    "meanings": [
        {"partOfSpeech": "noun", "definitions": [
            {"definition": "An event or occurrence that signifies an ending."},
            {"definition": "A feeling of completeness."},
            {"definition": "Terceira, cortada."}]},
        {"partOfSpeech": "verb", "definitions": [{"definition": "To close."}]},
    ],
}]
EN_404 = {"title": "No Definitions Found", "message": "Sorry pal"}

WIKI_ALGORITMO = (
    "={{-pt-}}=\n==Substantivo==\n{{flex.pt|ms=algoritmo}}\n"
    "# {{escopo|pt|Matemática}} [[conjunto]] de [[regra]]s para [[resolução]]\n"
    "#: ''exemplo que nao e definicao''\n"
    "# [[processo]] [[computacional|computacional]] '''bem''' definido<ref>fonte</ref>\n"
    "=={{etimologia|pt}}==\n"
    "={{-es-}}=\n==Substantivo==\n# [[algoritmo#Português|algoritmo]]\n"
)
WIKI_EFEMERO = "={{-pt-}}=\n==Adjetivo==\n# ver [[efémero]]\n"
WIKI_EFEMERO_PT = "={{-pt-}}=\n==Adjetivo==\n# que é de [[curto|curta]] [[duração]]\n"


class Resposta:
    def __init__(self, dados, status=200):
        self._dados, self.status_code = dados, status

    def json(self):
        return self._dados

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def _mymemory(texto, status=200, cota=False):
    return Resposta({"responseData": {"translatedText": texto, "match": 0.85},
                     "quotaFinished": cota, "responseDetails": "", "responseStatus": status})


class Servidor:
    """Roteia as URLs como os tres servicos reais."""

    def __init__(self, en=None, pt=None, traducoes=None, falha=None):
        self.en = en or {}
        self.pt = pt or {}
        self.traducoes = traducoes or {}
        self.falha = falha or set()
        self.chamadas = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.chamadas.append((url, params, headers, timeout))
        if "dictionaryapi" in url:
            if "en" in self.falha:
                raise requests.ConnectionError("sem rede")
            palavra = url.rsplit("/", 1)[1]
            return Resposta(self.en[palavra]) if palavra in self.en else Resposta(EN_404, 404)
        if "wiktionary" in url:
            if "pt" in self.falha:
                raise requests.Timeout("lento")
            pagina = params["page"]
            if pagina in self.pt:
                return Resposta({"parse": {"title": pagina, "wikitext": self.pt[pagina]}})
            return Resposta({"error": {"code": "missingtitle"}})
        if "mymemory" in url:
            if "traducao" in self.falha:
                raise requests.Timeout("lento")
            return self.traducoes.get(params["q"], _mymemory(params["q"]))
        raise AssertionError(url)


class BaseDicionario(unittest.TestCase):
    def setUp(self):
        dicionario.limpar_cache()

    def usar(self, servidor):
        patcher = mock.patch.object(dicionario.requests, "get", side_effect=servidor)
        patcher.start()
        self.addCleanup(patcher.stop)
        return servidor


class TestIdioma(unittest.TestCase):
    def test_deteccao(self):
        casos = {
            "closure": "en", "computer": "en", "running": "en", "the function": "en",
            "efêmero": "pt", "ação": "pt", "casa": "pt", "algoritmo": "pt",
            "a função retorna um valor": "pt", "isso é uma casa": "pt",
            "returns a value from the stack": "en", "": "en",
        }
        for texto, esperado in casos.items():
            self.assertEqual(dicionario.detectar_idioma(texto), esperado, texto)


class TestConsulta(BaseDicionario):
    def test_ingles(self):
        srv = self.usar(Servidor(en={"closure": EN_CLOSURE},
                                 traducoes={"Closure": _mymemory("FECHAMENTO")}))
        r = dicionario.consultar("Closure,")
        self.assertEqual(r.termo, "Closure")
        self.assertEqual(r.definicoes, [
            "(noun) An event or occurrence that signifies an ending.",
            "(noun) A feeling of completeness.", "(verb) To close."])
        self.assertEqual(r.traducao, "Fechamento")   # caixa segue a selecao
        self.assertEqual((r.idioma_origem, r.idioma_destino), ("en", "pt"))
        self.assertEqual(r.fonte, "Free Dictionary API + MyMemory")
        self.assertIsNone(r.erro)
        # So o texto selecionado sai: confere User-Agent, timeout e o par.
        traducao = [c for c in srv.chamadas if "mymemory" in c[0]][0]
        self.assertEqual(traducao[1], {"q": "Closure", "langpair": "en|pt-BR"})
        self.assertEqual(traducao[2], {"User-Agent": "Lumina-leitor"})
        self.assertEqual(traducao[3], dicionario.TEMPO_LIMITE)

    def test_portugues_wikcionario(self):
        self.usar(Servidor(pt={"algoritmo": WIKI_ALGORITMO},
                           traducoes={"algoritmo": _mymemory("algorithm")}))
        r = dicionario.consultar("algoritmo")
        self.assertEqual(r.definicoes, [
            "(Matemática) conjunto de regras para resolução",
            "processo computacional bem definido"])
        self.assertEqual(r.traducao, "algorithm")
        self.assertEqual((r.idioma_origem, r.idioma_destino), ("pt", "en"))
        self.assertEqual(r.fonte, "Wikcionario + MyMemory")

    def test_remissao_seguida(self):
        self.usar(Servidor(pt={"efêmero": WIKI_EFEMERO, "efémero": WIKI_EFEMERO_PT}))
        r = dicionario.consultar("efêmero")
        self.assertEqual(r.definicoes, ["que é de curta duração"])

    def test_chute_errado_tenta_outro_idioma(self):
        # "data" parece portugues pela terminacao, mas so o dicionario EN conhece.
        self.usar(Servidor(en={"data": [{"meanings": [{"partOfSpeech": "noun",
                                                        "definitions": [{"definition": "Info."}]}]}]},
                           traducoes={"data": _mymemory("dados")}))
        r = dicionario.consultar("data")
        self.assertEqual(r.definicoes, ["(noun) Info."])
        self.assertEqual((r.idioma_origem, r.traducao), ("en", "dados"))

    def test_idioma_forcado_nao_troca(self):
        self.usar(Servidor(en={"data": EN_CLOSURE}))
        r = dicionario.consultar("data", idioma="pt")
        self.assertEqual(r.definicoes, [])
        self.assertEqual(r.idioma_origem, "pt")

    def test_frase_longa_so_traduz(self):
        srv = self.usar(Servidor(traducoes={
            "the function returns a value": _mymemory("a função retorna um valor")}))
        r = dicionario.consultar("the function returns a value")
        self.assertEqual(r.definicoes, [])
        self.assertEqual(r.traducao, "a função retorna um valor")
        self.assertFalse(any("dictionaryapi" in c[0] for c in srv.chamadas))

    def test_desconhecida(self):
        self.usar(Servidor())
        r = dicionario.consultar("xqzzvw")
        self.assertEqual(r.definicoes, [])
        self.assertIsNone(r.traducao)          # eco do termo nao e traducao
        self.assertEqual(r.erro, "Nenhuma definicao ou traducao encontrada.")

    def test_cota_esgotada(self):
        self.usar(Servidor(en={"closure": EN_CLOSURE}, traducoes={"closure": _mymemory(
            "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY", 429)}))
        r = dicionario.consultar("closure")
        self.assertEqual(len(r.definicoes), 3)
        self.assertIsNone(r.traducao)
        self.assertIn("limite diario", r.erro)

    def test_html_escapado(self):
        self.usar(Servidor(traducoes={"it's done now": _mymemory("est&#225; feito &amp; pronto")}))
        r = dicionario.consultar("it's done now", definir=False)
        self.assertEqual(r.traducao, "está feito & pronto")

    def test_sem_rede_nao_levanta_e_nao_cacheia(self):
        srv = self.usar(Servidor(falha={"en", "pt", "traducao"}))
        r = dicionario.consultar("closure")
        self.assertIsNotNone(r.erro)
        self.assertIn("tempo esgotado", r.erro)
        self.assertEqual(r.definicoes, [])
        antes = len(srv.chamadas)
        dicionario.consultar("closure")
        self.assertGreater(len(srv.chamadas), antes)   # tentou de novo

    def test_cache(self):
        srv = self.usar(Servidor(en={"closure": EN_CLOSURE}))
        a = dicionario.consultar("closure")
        n = len(srv.chamadas)
        b = dicionario.consultar("  closure. ")
        self.assertIs(a, b)
        self.assertEqual(len(srv.chamadas), n)

    def test_vazio(self):
        srv = self.usar(Servidor())
        r = dicionario.consultar("  ...  ")
        self.assertEqual(r.erro, "Nada selecionado.")
        self.assertEqual(srv.chamadas, [])

    def test_limite_de_tamanho(self):
        srv = self.usar(Servidor())
        dicionario.consultar("palavra " * 200, definir=False)
        q = [c for c in srv.chamadas if "mymemory" in c[0]][0][1]["q"]
        self.assertLessEqual(len(q), dicionario.MAX_CARACTERES)


class TestCacheLRU(unittest.TestCase):
    def test_despeja_o_mais_antigo(self):
        c = dicionario._CacheLRU(tamanho=2)
        c.guardar("a", 1)
        c.guardar("b", 2)
        c.obter("a")                 # "a" fica recente
        c.guardar("c", 3)
        self.assertIsNone(c.obter("b"))
        self.assertEqual((c.obter("a"), c.obter("c")), (1, 3))


if __name__ == "__main__":
    unittest.main()
