"""Testes do atualizador (services/atualizacao.py), sem rede.

A API do GitHub e o download sao simulados por um `abrir` falso; nenhum
teste sai da maquina nem dispara o .cmd de troca.

Rodar:  python -m unittest tests.test_atualizacao -v
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest

# Isola a pasta de dados: os testes nunca tocam a biblioteca real.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import atualizacao as at  # noqa: E402


class CfgFalsa:
    """Mesma interface usada do BancoConfig, em memoria."""

    def __init__(self, **valores):
        self.valores = dict(valores)

    def obter(self, chave, padrao=None):
        return self.valores.get(chave, padrao)

    def obter_bool(self, chave, padrao=False):
        if chave not in self.valores:
            return padrao
        return str(self.valores[chave]).strip().lower() in ("1", "true", "sim")

    def definir(self, chave, valor):
        self.valores[chave] = str(valor)


class RespostaFalsa(io.BytesIO):
    def __init__(self, dados, headers=None):
        super().__init__(dados)
        self.headers = headers or {}


def abrir_com(dados, registro=None):
    def abrir(pedido, timeout=None):
        if registro is not None:
            registro.append((pedido, timeout))
        return RespostaFalsa(dados)
    return abrir


ASSETS = [
    {"name": "Lumina.exe", "browser_download_url": "https://x/Lumina.exe", "size": 10},
    {"name": "LuminaSetup-2.3.exe", "browser_download_url": "https://x/LuminaSetup-2.3.exe",
     "size": 20},
]


class TestVersao(unittest.TestCase):

    def test_tupla(self):
        self.assertEqual(at.versao_tupla("v2.3.1"), (2, 3, 1))
        self.assertEqual(at.versao_tupla("2.3-beta"), (2, 3))
        self.assertEqual(at.versao_tupla(""), ())
        self.assertEqual(at.versao_tupla("nada"), ())

    def test_comparacao(self):
        self.assertTrue(at.eh_mais_nova("v2.3", "2.2"))
        self.assertTrue(at.eh_mais_nova("2.10", "2.9"))     # string daria errado
        self.assertTrue(at.eh_mais_nova("2.2.1", "2.2"))
        self.assertFalse(at.eh_mais_nova("2.2", "2.2"))
        self.assertFalse(at.eh_mais_nova("v2.2.0", "2.2"))
        self.assertFalse(at.eh_mais_nova("2.1", "2.2"))
        self.assertFalse(at.eh_mais_nova("", "2.2"))
        self.assertFalse(at.eh_mais_nova("latest", "2.2"))


class TestUmaVezPorDia(unittest.TestCase):

    def test_nunca_verificou(self):
        self.assertTrue(at.deve_verificar(CfgFalsa(), agora=1_000_000))

    def test_desligado(self):
        cfg = CfgFalsa(**{at.CHAVE_ATIVA: "0"})
        self.assertFalse(at.deve_verificar(cfg, agora=1_000_000))

    def test_intervalo(self):
        cfg = CfgFalsa()
        at.registrar_verificacao(cfg, agora=1_000_000)
        self.assertFalse(at.deve_verificar(cfg, agora=1_000_000 + 3600))
        self.assertTrue(at.deve_verificar(cfg, agora=1_000_000 + at.INTERVALO))

    def test_relogio_voltou_e_lixo(self):
        cfg = CfgFalsa(**{at.CHAVE_ULTIMA: "2000000"})
        self.assertTrue(at.deve_verificar(cfg, agora=1_000_000))
        cfg = CfgFalsa(**{at.CHAVE_ULTIMA: "abc"})
        self.assertTrue(at.deve_verificar(cfg, agora=1_000_000))

    def test_automatica_respeita_o_dia(self):
        cfg = CfgFalsa()
        at.registrar_verificacao(cfg)
        self.assertFalse(at.verificacao_automatica(cfg, lambda n: None))


class TestModoEAsset(unittest.TestCase):

    def test_modo(self):
        base = tempfile.mkdtemp()
        instalado = os.path.join(base, "Programs", "Lumina", "Lumina.exe")
        self.assertEqual(at.modo_execucao(instalado, congelado=False, localappdata=base), "fonte")
        self.assertEqual(at.modo_execucao(instalado, congelado=True, localappdata=base),
                         "instalado")
        solto = os.path.join(base, "Downloads", "Lumina.exe")
        self.assertEqual(at.modo_execucao(solto, congelado=True, localappdata=base), "portatil")
        # Pasta escolhida a mao na instalacao: reconhecida pelo desinstalador.
        outra = os.path.join(base, "Apps", "Lumina")
        os.makedirs(outra)
        open(os.path.join(outra, "unins000.exe"), "w").close()
        self.assertEqual(at.modo_execucao(os.path.join(outra, "Lumina.exe"), congelado=True,
                                          localappdata=base), "instalado")

    def test_asset(self):
        self.assertEqual(at.escolher_asset(ASSETS, "portatil")["name"], "Lumina.exe")
        self.assertEqual(at.escolher_asset(ASSETS, "instalado")["name"], "LuminaSetup-2.3.exe")
        self.assertIsNone(at.escolher_asset(ASSETS, "fonte"))
        # Release sem instalador: a versao instalada troca o .exe direto.
        self.assertEqual(at.escolher_asset(ASSETS[:1], "instalado")["name"], "Lumina.exe")
        self.assertIsNone(at.escolher_asset([], "portatil"))
        self.assertIsNone(at.escolher_asset([{"name": "Lumina.exe"}], "portatil"))


class TestConsulta(unittest.TestCase):

    def _release(self, **extra):
        dados = {"tag_name": "v2.3", "html_url": "https://pagina", "body": "notas",
                 "assets": ASSETS}
        dados.update(extra)
        return json.dumps(dados).encode()

    def test_nova(self):
        registro = []
        novidade = at.consultar(abrir=abrir_com(self._release(), registro), local="2.2",
                                modo="portatil")
        self.assertEqual(novidade.versao, "2.3")
        self.assertEqual(novidade.asset["name"], "Lumina.exe")
        self.assertTrue(novidade.pode_instalar)
        pedido, timeout = registro[0]
        self.assertEqual(pedido.full_url, at.URL_API)
        self.assertEqual(pedido.get_header("User-agent"), "Lumina-atualizador")
        self.assertLessEqual(timeout, 10)

    def test_igual_ou_prerelease(self):
        self.assertIsNone(at.consultar(abrir=abrir_com(self._release()), local="2.3"))
        self.assertIsNone(at.consultar(abrir=abrir_com(self._release(prerelease=True)),
                                       local="2.2"))

    def test_fonte_so_avisa(self):
        novidade = at.consultar(abrir=abrir_com(self._release()), local="2.2", modo="fonte")
        self.assertIsNotNone(novidade)
        self.assertFalse(novidade.pode_instalar)


class TestDownload(unittest.TestCase):

    def test_baixa_confere_e_renomeia(self):
        dados = b"x" * 1000
        asset = {"name": "Lumina.exe", "browser_download_url": "https://x", "size": len(dados),
                 "digest": "sha256:" + hashlib.sha256(dados).hexdigest()}
        progresso = []
        pasta = tempfile.mkdtemp()
        caminho = at.baixar(asset, lambda f, t: progresso.append(f), pasta=pasta,
                            abrir=abrir_com(dados))
        with open(caminho, "rb") as arquivo:
            self.assertEqual(arquivo.read(), dados)
        self.assertEqual(progresso[-1], 1.0)
        self.assertFalse(os.path.exists(caminho + ".parcial"))

    def test_incompleto_ou_digest_errado(self):
        pasta = tempfile.mkdtemp()
        curto = {"name": "Lumina.exe", "browser_download_url": "https://x", "size": 99}
        with self.assertRaises(IOError):
            at.baixar(curto, pasta=pasta, abrir=abrir_com(b"abc"))
        errado = {"name": "Lumina.exe", "browser_download_url": "https://x", "size": 3,
                  "digest": "sha256:" + "0" * 64}
        with self.assertRaises(IOError):
            at.baixar(errado, pasta=pasta, abrir=abrir_com(b"abc"))
        self.assertEqual(os.listdir(pasta), [])


class TestScripts(unittest.TestCase):

    def test_troca_portatil(self):
        texto = at.gerar_script_troca(r"C:\Livros 100%\Lumina.exe",
                                      r"C:\Temp\Lumina.exe", 4321)
        self.assertIn('set "PID=4321"', texto)
        self.assertIn(r'set "ALVO=C:\Livros 100%%\Lumina.exe"', texto)   # % escapado
        self.assertIn('move /Y "%NOVO%" "%ALVO%"', texto)
        self.assertIn('start "" "%ALVO%"', texto)
        self.assertIn('tasklist.exe" /NH /FI "PID eq %PID%"', texto)
        # find/ping do System32: o PATH pode ter o `find` do Git na frente.
        self.assertIn(r'"%SystemRoot%\System32\find.exe"', texto)
        self.assertNotIn("timeout /t", texto)        # falha sem console
        self.assertTrue(texto.startswith("@echo off\r\n"))
        self.assertNotIn("\r\r", texto)
        self.assertNotIn("\n", texto.replace("\r\n", ""))
        # Todo goto aponta para um rotulo que existe.
        rotulos = {linha[1:] for linha in texto.split("\r\n") if linha.startswith(":")}
        for linha in texto.split("\r\n"):
            if " goto " in f" {linha}" and "(goto)" not in linha:
                self.assertIn(linha.rsplit("goto ", 1)[1].strip(), rotulos)

    def test_instalador(self):
        texto = at.gerar_script_instalador(r"C:\Temp\LuminaSetup-2.3.exe",
                                           r"C:\Users\a\AppData\Local\Programs\Lumina\Lumina.exe",
                                           99)
        self.assertIn('start "" /wait "%SETUP%" /VERYSILENT /SUPPRESSMSGBOXES', texto)
        self.assertIn('start "" "%ALVO%"', texto)
        self.assertLess(texto.index(":livre"), texto.index("/VERYSILENT"))

    def test_aplicar_recusa_fora_do_executavel(self):
        if getattr(sys, "frozen", False):
            self.skipTest("so faz sentido rodando do codigo-fonte")
        with self.assertRaises(RuntimeError):
            at.aplicar(os.path.join(tempfile.mkdtemp(), "Lumina.exe"), "portatil")


if __name__ == "__main__":
    unittest.main()
