"""Testes da estante: senha de PDF (DPAPI + abertura transparente) e acoes em lote.

Rodar:  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

# Isola a pasta de dados: os testes nunca tocam a biblioteca real.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from database import cofre  # noqa: E402
from database.biblioteca import BancoBiblioteca  # noqa: E402
from database.sqlite_db import conexao, inicializar_banco  # noqa: E402
from services import pdf_service  # noqa: E402
from services.pdf_service import PDFService, abrir, bloqueado  # noqa: E402

SENHA = "c0rreta-ç"


def _pdf_protegido(pasta, nome="secreto.pdf", senha=SENHA, paginas=3):
    doc = fitz.open()
    for i in range(paginas):
        doc.new_page().insert_text((72, 72), f"Pagina secreta {i + 1}")
    doc.set_metadata({"title": "Manual Confidencial", "author": "Fulana de Tal"})
    caminho = os.path.join(pasta, nome)
    doc.save(caminho, encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw=senha, owner_pw=senha + "-dono")
    doc.close()
    return caminho


@unittest.skipUnless(cofre.disponivel(), "DPAPI so existe no Windows")
class TestCofre(unittest.TestCase):

    def test_ida_e_volta(self):
        for texto in ("abc", SENHA, "", "x" * 500):
            cifrado = cofre.cifrar(texto)
            self.assertIsNotNone(cifrado)
            self.assertNotIn(texto or "\0", cifrado)
            self.assertEqual(cofre.decifrar(cifrado), texto)

    def test_blob_invalido_vira_none(self):
        self.assertIsNone(cofre.decifrar("isto-nao-e-base64-de-dpapi"))
        self.assertIsNone(cofre.decifrar(""))
        cifrado = cofre.cifrar("abc")
        adulterado = cifrado[:-6] + ("AAAA==" if not cifrado.endswith("AAAA==") else "BBBB==")
        self.assertIsNone(cofre.decifrar(adulterado))


class TestPdfProtegido(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        inicializar_banco()
        cls.pasta = tempfile.mkdtemp(prefix="lumina_pdf_")
        cls.banco = BancoBiblioteca()

    def setUp(self):
        pdf_service._SENHAS.clear()

    def test_aes256_trancado_sem_senha(self):
        caminho = _pdf_protegido(self.pasta, "trancado.pdf")
        with abrir(caminho) as doc:
            self.assertTrue(bloqueado(doc))
        self.assertTrue(PDFService.esta_protegido(caminho))
        self.assertTrue(PDFService.precisa_senha(caminho))
        self.assertIsNone(PDFService.abrir_documento(caminho))

    def test_testar_senha(self):
        caminho = _pdf_protegido(self.pasta, "teste_senha.pdf")
        self.assertFalse(PDFService.testar_senha(caminho, "errada"))
        self.assertFalse(PDFService.testar_senha(caminho, ""))
        self.assertTrue(PDFService.testar_senha(caminho, SENHA))
        # Testar nao lembra nada: continua pedindo senha.
        self.assertTrue(PDFService.precisa_senha(caminho))

    def test_senha_explicita(self):
        caminho = _pdf_protegido(self.pasta, "explicita.pdf")
        with abrir(caminho, senha=SENHA) as doc:
            self.assertFalse(bloqueado(doc))
            self.assertIn("Pagina secreta 1", doc[0].get_text())

    def test_memoria_da_sessao(self):
        caminho = _pdf_protegido(self.pasta, "memoria.pdf")
        PDFService.lembrar_senha(caminho, SENHA)
        doc = PDFService.abrir_documento(caminho)
        self.assertIsNotNone(doc)
        self.assertEqual(len(doc), 3)
        doc.close()
        self.assertFalse(PDFService.precisa_senha(caminho))
        # A deteccao "tem senha" independe da senha lembrada.
        self.assertTrue(PDFService.esta_protegido(caminho))

    @unittest.skipUnless(cofre.disponivel(), "DPAPI so existe no Windows")
    def test_senha_do_banco_abre_sem_perguntar(self):
        caminho = _pdf_protegido(self.pasta, "banco.pdf")
        id_livro = PDFService.gerar_id_unico(caminho)
        self.assertTrue(self.banco.definir_senha(id_livro, SENHA))

        # Nunca em texto puro no banco.
        with conexao() as conn:
            linha = conn.execute("SELECT senha_cifrada FROM senhas_livro WHERE id_livro = ?",
                                 (id_livro,)).fetchone()
        self.assertIsNotNone(linha)
        self.assertNotIn(SENHA, linha["senha_cifrada"])
        self.assertEqual(self.banco.obter_senha(id_livro), SENHA)

        # Como o leitor chama: so com o caminho.
        doc = PDFService.abrir_documento(caminho)
        self.assertIsNotNone(doc)
        self.assertIn("Pagina secreta 2", doc[1].get_text())
        doc.close()

        meta = PDFService.extrair_metadados(caminho)
        self.assertEqual(meta["total_paginas"], 3)
        self.assertEqual(meta["titulo"], "Manual Confidencial")
        self.assertEqual(meta["autor"], "Fulana de Tal")
        capa = PDFService.extrair_capa(caminho, id_livro, forcar=True)
        self.assertTrue(capa and os.path.exists(capa))

    @unittest.skipUnless(cofre.disponivel(), "DPAPI so existe no Windows")
    def test_arquivo_movido_continua_abrindo(self):
        caminho = _pdf_protegido(self.pasta, "original.pdf")
        id_livro = PDFService.gerar_id_unico(caminho)
        self.banco.definir_senha(id_livro, SENHA)
        outra = tempfile.mkdtemp(prefix="lumina_movido_")
        novo = os.path.join(outra, "renomeado.pdf")
        os.replace(caminho, novo)
        doc = PDFService.abrir_documento(novo)
        self.assertIsNotNone(doc)
        doc.close()

    @unittest.skipUnless(cofre.disponivel(), "DPAPI so existe no Windows")
    def test_remover_livro_apaga_senha(self):
        caminho = _pdf_protegido(self.pasta, "remover.pdf")
        id_livro = PDFService.gerar_id_unico(caminho)
        self.banco.definir_senha(id_livro, SENHA)
        self.banco.adicionar_livro(id_livro, caminho, "", "T", "", 3)
        self.banco.remover_livro(id_livro, apagar_capa=False)
        self.assertIsNone(self.banco.obter_senha(id_livro))

    def test_pdf_comum_nao_pede_senha(self):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "aberto")
        caminho = os.path.join(self.pasta, "comum.pdf")
        doc.save(caminho)
        doc.close()
        self.assertFalse(PDFService.esta_protegido(caminho))
        self.assertFalse(PDFService.precisa_senha(caminho))
        self.assertIsNotNone(PDFService.abrir_documento(caminho))


class TestAcoesEmLote(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        inicializar_banco()
        cls.banco = BancoBiblioteca()

    def test_marcar_como_lido(self):
        self.banco.adicionar_livro("lote-a", "a.pdf", "", "A", "", 120)
        self.banco.adicionar_livro("lote-b", "b.pdf", "", "B", "", 0)
        self.banco.definir_pagina("lote-a", 10)
        self.banco.definir_rolagem("lote-a", 300)
        mudados = self.banco.marcar_como_lido(["lote-a", "lote-b"])
        self.assertEqual(mudados, 1)          # "b" nao tem contagem de paginas
        livro = self.banco.obter_livro("lote-a")
        self.assertEqual(livro["pagina_atual"], 119)
        self.assertEqual(livro["rolagem"], 0)
        self.assertEqual(self.banco.marcar_como_lido([]), 0)


class TestArrastarSoltar(unittest.TestCase):

    def test_expandir_pastas_soltas(self):
        from ui.estante import TelaEstante
        raiz = tempfile.mkdtemp(prefix="lumina_soltos_")
        sub = os.path.join(raiz, "sub")
        os.makedirs(sub)
        for nome in ("a.pdf", "b.EPUB", "c.txt"):
            open(os.path.join(sub, nome), "wb").close()
        solto = os.path.join(raiz, "solto.docx")
        saida = TelaEstante._expandir_soltos([sub, solto])
        nomes = sorted(os.path.basename(c) for c in saida)
        # Pasta: so livros. Arquivo solto passa adiante (a importacao conta
        # como "ignorado" o que nao e livro, para avisar o usuario).
        self.assertEqual(nomes, ["a.pdf", "b.EPUB", "solto.docx"])


if __name__ == "__main__":
    unittest.main()
