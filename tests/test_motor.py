"""Testes do motor de leitura (sem interface grafica).

Cada teste monta um PDF minimo, em memoria, que reproduz um problema real
encontrado em livros -- e confere que o reflow entrega o resultado certo.

Rodar:  python -m unittest discover -s tests -v
"""
import io
import os
import sys
import tempfile
import time
import unittest

# Isola a pasta de dados: os testes nunca tocam a biblioteca real.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from services import realce  # noqa: E402
from services.deteccao_academico import _RE_ABNT_PARENTESES  # noqa: E402
from services.exportar import gerar_markdown  # noqa: E402
from services.reflow import MotorReflow  # noqa: E402
from ia.roteador import rotear  # noqa: E402

FRASE = ("Este e um paragrafo de teste com texto suficiente para ocupar a "
         "largura inteira da coluna e quebrar em varias linhas seguidas. ")


def _png(cor, tamanho=(400, 200)):
    buf = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(buf, "PNG")
    return buf.getvalue()


def _uma_linha_por_bloco(pagina, linhas, x=72, y=100, passo=16, tamanho=11):
    """Como o Word exporta: cada linha e um bloco de texto separado."""
    for i, linha in enumerate(linhas):
        pagina.insert_text((x, y + i * passo), linha, fontsize=tamanho)


def _blocos(doc, numero=0):
    return MotorReflow(doc).pagina(numero).blocos


class TestReflow(unittest.TestCase):

    def test_linhas_soltas_viram_um_paragrafo(self):
        doc = fitz.open()
        pg = doc.new_page()
        _uma_linha_por_bloco(pg, [
            "A leitura de um livro tecnico exige atencao e o texto precisa",
            "fluir de uma linha para a outra sem quebrar a frase no meio de",
            "uma ideia, porque cada quebra obriga o leitor a recomecar."])
        paragrafos = [b for b in _blocos(doc) if b.tipo == "paragrafo"]
        self.assertEqual(len(paragrafos), 1, [b.texto for b in paragrafos])
        self.assertIn("precisa fluir", paragrafos[0].texto)

    def test_fundo_branco_atras_do_texto_nao_vira_imagem(self):
        doc = fitz.open()
        pg = doc.new_page()
        for i in range(6):
            y = 100 + i * 40
            pg.draw_rect(fitz.Rect(60, y - 14, 540, y + 20), color=None,
                         fill=(1, 1, 1))
            pg.insert_text((72, y), "Paragrafo numero %d com texto comum." % i,
                           fontsize=11)
        blocos = _blocos(doc)
        self.assertFalse(any(b.imagem for b in blocos),
                         "retangulos brancos de fundo viraram recorte de imagem")

    def test_coluna_de_numeros_de_linha_e_removida(self):
        doc = fitz.open()
        pg = doc.new_page()
        pg.insert_text((60, 100), "1\n2\n3", fontname="cour", fontsize=9)
        pg.insert_text((80, 100), "x = 1\ny = 2\nprint(x + y)", fontname="cour",
                       fontsize=9)
        codigos = [b for b in _blocos(doc) if b.tipo == "codigo"]
        self.assertTrue(codigos)
        self.assertFalse(any(b.texto.replace("\n", "").strip().isdigit()
                             for b in codigos), [b.texto for b in codigos])

    def test_marcador_do_word_vira_lista(self):
        #  = marcador da fonte Symbol, como o extrator entrega os PDFs
        # do Word. Testado na estrutura que o PyMuPDF devolve por linha.
        from services.reflow import _RE_MARCADOR, _texto_da_linha
        linha = {"spans": [{"text": ""}, {"text": " Primeiro item da lista"}]}
        texto = _texto_da_linha(linha)
        self.assertTrue(texto.startswith("•"), repr(texto))
        self.assertTrue(_RE_MARCADOR.match(texto))

    def test_figuras_de_mesmo_tamanho_nao_somem_como_marca_dagua(self):
        doc = fitz.open()
        cores = ["red", "green", "blue", "orange", "purple", "gray"]
        for cor in cores:
            pg = doc.new_page()
            pg.insert_image(fitz.Rect(100, 300, 500, 500), stream=_png(cor))
            pg.insert_textbox(fitz.Rect(72, 80, 520, 280), FRASE * 4, fontsize=11)
        motor = MotorReflow(doc)
        visiveis = sum(1 for n in range(len(doc))
                       for b in motor.pagina(n).blocos if b.imagem)
        self.assertEqual(visiveis, len(cores))

    def test_logo_repetido_e_removido(self):
        doc = fitz.open()
        logo = _png("red", (200, 80))
        for _ in range(8):
            pg = doc.new_page()
            pg.insert_image(fitz.Rect(450, 20, 560, 64), stream=logo)
            pg.insert_textbox(fitz.Rect(72, 100, 520, 700), FRASE * 10, fontsize=11)
        motor = MotorReflow(doc)
        self.assertEqual(sum(1 for n in range(8)
                             for b in motor.pagina(n).blocos if b.imagem), 0)


def _caixa(pg, y, texto, fonte="tiro", tam=11, x0=72, x1=523, h=400):
    r = fitz.Rect(x0, y, x1, y + h)
    sobra = pg.insert_textbox(r, texto, fontname=fonte, fontsize=tam)
    return (r.y1 - sobra + 6) if sobra >= 0 else y + h


class TestClassificacao(unittest.TestCase):
    """Cada tipo de livro precisa cair no modo de exibicao certo."""

    def _tipo(self, montar, paginas=10):
        doc = fitz.open()
        for n in range(paginas):
            montar(doc.new_page(width=595, height=842), n)
        return MotorReflow(doc).render_detectado

    def test_romance_com_dialogos_e_prosa(self):
        def montar(pg, n):
            y = 80
            for _ in range(8):
                y = _caixa(pg, y, "- Voce vai embora amanha? - perguntou ela.", h=30)
                y = _caixa(pg, y, FRASE * 2, h=70)
        self.assertEqual(self._tipo(montar), "prosa")

    def test_livro_tecnico(self):
        def montar(pg, n):
            y = 70
            for s in range(3):
                y = _caixa(pg, y, "%d.%d Estrutura" % (n, s), "hebo", 15, h=26)
                y = _caixa(pg, y, FRASE * 2, "helv", 10.5, h=90)
        self.assertEqual(self._tipo(montar), "tecnico")

    def test_programacao(self):
        codigo = ("def carregar(caminho):\n    with open(caminho) as arq:\n"
                  "        return [l.strip() for l in arq]\n\nclass Leitor:\n"
                  "    def __init__(self, fonte):\n        self.fonte = fonte\n")

        def montar(pg, n):
            y = _caixa(pg, 70, FRASE * 3, h=120)
            y = _caixa(pg, y, codigo, "cour", 9.5, x0=90, h=130)
            _caixa(pg, y, FRASE * 3, h=140)
        self.assertEqual(self._tipo(montar), "programacao")

    def test_manual(self):
        def montar(pg, n):
            y = 70
            for i in range(1, 5):
                y = _caixa(pg, y, "%d. Conecte o cabo e aguarde o indicador." % i,
                           "helv", 10.5, h=30)
            y = _caixa(pg, y, "Atencao: nao remova a tampa ligada.", "helv", 10.5, h=26)
            _caixa(pg, y, "Cuidado: superficie quente.", "helv", 10.5, h=26)
        self.assertEqual(self._tipo(montar), "manual")

    def test_revista(self):
        def montar(pg, n):
            pg.insert_image(fitz.Rect(40, 40, 555, 330), stream=_png("teal", (900, 500)))
            _caixa(pg, 350, FRASE * 6, "tiro", 9.5, x0=40, x1=290, h=460)
            _caixa(pg, 350, FRASE * 6, "tiro", 9.5, x0=305, x1=555, h=460)
        self.assertEqual(self._tipo(montar), "revista")

    def test_hq(self):
        def montar(pg, n):
            pg.insert_image(pg.rect, stream=_png((200, 60 + n * 10, 90), (600, 850)))
            pg.insert_text((100, 120), "Onde voce estava?!", fontsize=12)
        self.assertEqual(self._tipo(montar), "hq")

    def test_fonte_do_latex_nao_e_formula(self):
        from services.reflow import _eh_matematica
        self.assertFalse(_eh_matematica({"font": "CMR10"}))
        self.assertFalse(_eh_matematica({"font": "CMBX12"}))
        self.assertTrue(_eh_matematica({"font": "CMMI10"}))
        self.assertTrue(_eh_matematica({"font": "CambriaMath"}))


class TestRealce(unittest.TestCase):

    def test_python_nao_herda_html_do_livro(self):
        self.assertNotEqual(realce.detectar("DATABASES = {\n  'a': 1\n}", "html"), "html")

    def test_html_continua_html(self):
        self.assertEqual(realce.detectar('<div class="x">oi</div>', "python"), "html")

    def test_arvore_de_diretorios(self):
        arvore = "/projeto\n  - __init__.py\n  - settings.py\n  - urls.py"
        self.assertEqual(realce.detectar(arvore), "arvore")


class TestRoteador(unittest.TestCase):

    CASOS = {
        "O que e uma ForeignKey?": "livro",
        "o que e o terror jacobino?": "livro",
        "como funciona o manage.py migrate": "livro",
        "Rodei manage.py migrate e deu OperationalError": "geral",
        "O livro fala de QuerySet mas meu codigo deu AttributeError": "hibrido",
        "pip install django falhou": "geral",
    }

    def test_rotas(self):
        for pergunta, esperado in self.CASOS.items():
            with self.subTest(pergunta=pergunta):
                self.assertEqual(rotear(pergunta), esperado)


def _epub_minimo(caminho, capitulos=4):
    import zipfile
    par = "<p>" + FRASE * 3 + "</p>"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:'
                   'names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="c.opf" media-type="application/oebps-package+xml"/>'
                   '</rootfiles></container>')
        itens = espinha = ""
        for n in range(capitulos):
            z.writestr(f"c{n}.xhtml",
                       '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                       f"<h1>Capitulo {n + 1}</h1>{par * 6}</body></html>")
            itens += f'<item id="c{n}" href="c{n}.xhtml" media-type="application/xhtml+xml"/>'
            espinha += f'<itemref idref="c{n}"/>'
        z.writestr("c.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                   'unique-identifier="i"><metadata xmlns:dc="http://purl.org/dc/'
                   'elements/1.1/"><dc:identifier id="i">x</dc:identifier>'
                   '<dc:title>Livro EPUB de Teste</dc:title><dc:creator>Autora</dc:creator>'
                   f'</metadata><manifest>{itens}</manifest><spine>{espinha}</spine></package>')


class TestEpub(unittest.TestCase):

    def setUp(self):
        self.caminho = os.path.join(tempfile.mkdtemp(), "teste.epub")
        _epub_minimo(self.caminho)

    def test_paginacao_e_estavel(self):
        """Grifo e progresso apontam para paginas: abrir duas vezes tem que
        paginar igual, senao a "pagina 3" muda de lugar."""
        from services.pdf_service import abrir
        with abrir(self.caminho) as a, abrir(self.caminho) as b:
            self.assertGreater(len(a), 1)
            self.assertEqual(len(a), len(b))
            ultima = len(a) - 1
            self.assertEqual(a.load_page(ultima).get_text(), b.load_page(ultima).get_text())

    def test_metadados_e_reflow(self):
        from services.pdf_service import PDFService, abrir, eh_livro
        self.assertTrue(eh_livro(self.caminho))
        meta = PDFService.extrair_metadados(self.caminho)
        self.assertEqual(meta["titulo"], "Livro EPUB de Teste")
        with abrir(self.caminho) as doc:
            blocos = MotorReflow(doc).pagina(0).blocos
            self.assertTrue(any(b.tipo == "titulo" for b in blocos))
            self.assertTrue(any(b.tipo == "paragrafo" for b in blocos))


class TestLivroSemEstrutura(unittest.TestCase):
    """PDF gerado pelo Calibre: tudo numa fonte so, cabecalho no meio do texto."""

    def test_linha_de_codigo_pelo_conteudo(self):
        from services.reflow import _parece_linha_de_codigo
        self.assertTrue(_parece_linha_de_codigo("var ano = 2012;"))
        self.assertTrue(_parece_linha_de_codigo('document.write("oi");'))
        self.assertTrue(_parece_linha_de_codigo('");'))
        self.assertFalse(_parece_linha_de_codigo("importante experimentar alem das licoes."))
        self.assertFalse(_parece_linha_de_codigo("Variavel e um nome para um valor."))

    def test_frase_interrompida(self):
        from services.reflow import _frase_interrompida
        self.assertTrue(_frase_interrompida("e de dois cliques nele. O", "navegador deve abrir"))
        self.assertTrue(_frase_interrompida("falando numa", '"lingua" que ele'))
        self.assertFalse(_frase_interrompida("Fim do paragrafo.", "outro comeco"))

    def test_corpo_negrito_e_cabecalho_corrido(self):
        doc = fitz.open()
        for n in range(12):
            pg = doc.new_page()
            y = 80
            for _ in range(5):
                y = _caixa(pg, y, FRASE * 2, "hebo", 14, h=120)
            _caixa(pg, y, "%d" % (n + 10), "hebo", 14, h=20)
            _caixa(pg, y + 24, "E-book gerado especialmente para Fulano de Tal", "hebo", 14, h=20)
        motor = MotorReflow(doc)
        self.assertTrue(motor.corpo_negrito)
        blocos = motor.pagina(5).blocos
        self.assertFalse(any(b.tipo == "titulo" for b in blocos), [b.texto[:40] for b in blocos])
        self.assertFalse(any("E-book gerado" in b.texto for b in blocos))


class TestDiversos(unittest.TestCase):

    def test_modo_livre_antigo_vira_rolagem_continua(self):
        from configuracoes import MODO_LIVRE, MODO_VIRADA_PADRAO, normalizar_modo_virada
        self.assertEqual(normalizar_modo_virada("Livre"), MODO_LIVRE)
        self.assertEqual(normalizar_modo_virada("modo-que-nao-existe"), MODO_VIRADA_PADRAO)

    def test_regex_de_citacao_nao_trava(self):
        inicio = time.time()
        _RE_ABNT_PARENTESES.search("(" + "SILVA ; " * 40)
        self.assertLess(time.time() - inicio, 0.5)
        m = _RE_ABNT_PARENTESES.search("(SILVA et al., 2015)")
        self.assertIsNotNone(m)

    def test_exportar_markdown(self):
        md = gerar_markdown({"titulo": "Livro", "autor": "Autora"},
                            [(4, "trecho grifado", "")], [(4, "minha nota")],
                            [(9, "")], [[1, "Capitulo 1", 1], [1, "Capitulo 2", 5]])
        self.assertIn("### Capitulo 2", md)
        self.assertIn("> trecho grifado", md)
        self.assertIn("minha nota", md)
        self.assertIn("Pagina 10", md)


if __name__ == "__main__":
    unittest.main()
