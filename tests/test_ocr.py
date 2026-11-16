"""Testes do OCR de paginas digitalizadas (Windows.Media.Ocr).

Monta uma pagina que e SO imagem -- texto desenhado com o Pillow, como sai de
um scanner -- e confere que o reflow entrega texto de verdade no lugar da
figura. Pulado quando o OCR do Windows nao esta disponivel na maquina.

Rodar:  python -m unittest tests.test_ocr -v
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
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from services import ocr  # noqa: E402
from services.reflow import MotorReflow  # noqa: E402

_FONTE = r"C:\Windows\Fonts\arial.ttf"

# 200 dpi numa pagina Carta: o que um scanner domestico entrega.
_LARGURA_PX, _ALTURA_PX = 1700, 2200
_MARGEM = 170

_TITULO = "Capitulo Primeiro"
_LINHAS = [
    "A biblioteca da cidade ficava aberta ate tarde durante o",
    "inverno, e os leitores mais assiduos ocupavam sempre as mes-",
    "mas cadeiras perto da janela grande do segundo andar.",
    None,
    "Naquela noite chovia muito e o bibliotecario decidiu fechar",
    "as portas mais cedo para voltar caminhando para casa.",
]


def _pdf_digitalizado() -> bytes:
    imagem = Image.new("L", (_LARGURA_PX, _ALTURA_PX), 255)
    desenho = ImageDraw.Draw(imagem)
    titulo = ImageFont.truetype(_FONTE, 64)
    corpo = ImageFont.truetype(_FONTE, 38)
    desenho.text((_MARGEM, 220), _TITULO, font=titulo, fill=0)
    y = 380
    for linha in _LINHAS:
        if linha is None:              # respiro entre paragrafos
            y += 40
            continue
        desenho.text((_MARGEM, y), linha, font=corpo, fill=0)
        y += 56
    desenho.text((_LARGURA_PX // 2 - 20, _ALTURA_PX - 150), "17", font=corpo, fill=0)
    buf = io.BytesIO()
    imagem.save(buf, "PNG")

    doc = fitz.open()
    pagina = doc.new_page(width=612, height=792)
    pagina.insert_image(pagina.rect, stream=buf.getvalue())
    saida = doc.tobytes()
    doc.close()
    return saida


@unittest.skipUnless(os.path.exists(_FONTE) and ocr.disponivel(),
                     "OCR do Windows indisponivel nesta maquina")
class TesteOcr(unittest.TestCase):

    def setUp(self):
        ocr.limpar_cache()
        self.doc = fitz.open(stream=_pdf_digitalizado(), filetype="pdf")
        self.addCleanup(self.doc.close)
        self.ativo_original = MotorReflow.ocr_ativo
        self.addCleanup(setattr, MotorReflow, "ocr_ativo", self.ativo_original)

    def test_pagina_digitalizada_vira_texto(self):
        MotorReflow.ocr_ativo = True
        inicio = time.perf_counter()
        pagina = MotorReflow(self.doc).pagina(0)
        decorrido = (time.perf_counter() - inicio) * 1000
        print(f"\n  OCR: {ocr.ultimo_ms:.0f} ms reconhecimento, {decorrido:.0f} ms "
              f"pagina inteira, idioma {ocr.idioma_em_uso()}, "
              f"instalados {ocr.idiomas()}")

        self.assertTrue(pagina.digitalizada)
        tipos = [b.tipo for b in pagina.blocos]
        self.assertNotIn("imagem", tipos, "com OCR a pagina nao deve virar figura")
        self.assertEqual(pagina.blocos[0].tipo, "aviso")
        self.assertIn("OCR", pagina.blocos[0].texto)

        texto = pagina.texto_puro.lower()
        for palavra in ("biblioteca", "cidade", "inverno", "leitores", "janela",
                        "chovia", "bibliotecario", "caminhando", "casa"):
            self.assertIn(palavra, texto)
        # Hifenizacao de fim de linha desfeita: "mes-" + "mas" = "mesmas".
        self.assertIn("mesmas", texto)
        # Numero de pagina solto no rodape e descartado, como no texto nativo.
        self.assertNotIn("17", texto)

        titulos = [b for b in pagina.blocos if b.tipo == "titulo"]
        self.assertTrue(titulos, f"titulo nao detectado: {tipos}")
        self.assertIn("capitulo", titulos[0].texto.lower())
        paragrafos = [b for b in pagina.blocos if b.tipo == "paragrafo"]
        self.assertEqual(len(paragrafos), 2, [b.texto for b in paragrafos])
        self.assertTrue(paragrafos[0].texto.lower().startswith("a biblioteca"))
        self.assertTrue(paragrafos[1].texto.lower().startswith("naquela noite"))

    def test_ocr_desligado_mostra_imagem(self):
        MotorReflow.ocr_ativo = False
        pagina = MotorReflow(self.doc).pagina(0)
        self.assertTrue(pagina.digitalizada)
        self.assertIn("imagem", [b.tipo for b in pagina.blocos])

    def test_pagina_sem_texto_continua_imagem(self):
        # Pagina em branco (so imagem, sem letra nenhuma): nada a reconhecer,
        # entao a imagem original continua sendo exibida.
        doc = fitz.open()
        pagina = doc.new_page(width=612, height=792)
        buf = io.BytesIO()
        Image.new("RGB", (600, 800), (180, 200, 220)).save(buf, "PNG")
        pagina.insert_image(pagina.rect, stream=buf.getvalue())
        self.addCleanup(doc.close)
        MotorReflow.ocr_ativo = True
        resultado = MotorReflow(doc).pagina(0)
        self.assertIn("imagem", [b.tipo for b in resultado.blocos])

    def test_cache_evita_segundo_reconhecimento(self):
        MotorReflow.ocr_ativo = True
        motor = MotorReflow(self.doc)
        motor.pagina(0)
        motor.invalidar_cache()
        inicio = time.perf_counter()
        motor.pagina(0)
        self.assertLess((time.perf_counter() - inicio) * 1000, max(50.0, ocr.ultimo_ms / 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
