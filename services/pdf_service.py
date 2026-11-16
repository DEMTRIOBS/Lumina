# services/pdf_service.py
"""Acesso ao arquivo PDF: identidade, metadados, capa e busca global."""
import hashlib
import os
import re
import threading

import fitz

from configuracoes import PASTA_CAPAS

_RE_ESPACOS = re.compile(r"\s+")

# ------------------------------------------------------------------ FORMATOS
# Tudo o que o Lumina abre. O EPUB nao tem paginas: o MuPDF as monta a partir
# de um tamanho de pagina e de letra. Esse tamanho PRECISA ser sempre o mesmo
# -- se cada parte do app paginasse de um jeito, a "pagina 40" do progresso,
# dos grifos e das notas apontaria para trechos diferentes. Por isso todo
# arquivo e aberto por `abrir()`, nunca por fitz.open direto.
EXTENSOES = (".pdf", ".epub")
TIPOS_ARQUIVO = [("Livros (PDF, EPUB)", "*.pdf *.epub"), ("PDF", "*.pdf"),
                 ("EPUB", "*.epub")]
# Formato A5 com letra de livro: paginas do tamanho das de um PDF comum, o
# que mantem estaveis o motor de leitura e a estimativa de tempo restante.
_LAYOUT_REFLUIVEL = {"width": 420, "height": 595, "fontsize": 11}


def eh_livro(caminho) -> bool:
    return str(caminho).lower().endswith(EXTENSOES)


def abrir(caminho: str):
    """Abre PDF ou EPUB. Use SEMPRE isto no lugar de fitz.open."""
    doc = fitz.open(caminho)
    if doc.is_reflowable:
        doc.layout(**_LAYOUT_REFLUIVEL)
    return doc

# Titulos que geradores de PDF gravam sozinhos e nao dizem nada sobre o livro.
# Sem esta lista a estante exibe coisas como "PDF Content" no lugar do nome.
_TITULOS_LIXO = (
    "microsoft word", "microsoft powerpoint", "microsoft excel",
    "pdf content", "untitled", "sem titulo", "sem título", "no title",
    "document1", "documento1", "novo documento", "new document",
    "print", "impressao", "impressão", "layout 1", "book1", "output",
)


def _titulo_e_lixo(titulo: str) -> bool:
    baixo = titulo.strip().lower()
    if any(baixo.startswith(lixo) for lixo in _TITULOS_LIXO):
        return True
    # "C:\Users\...\arquivo.pdf" ou "arquivo.indd" tambem nao sao titulos.
    if baixo.endswith((".pdf", ".doc", ".docx", ".indd", ".tex", ".qxd")):
        return True
    return bool(re.match(r"^[a-z]:[\\/]", baixo))


class PDFService:

    # ------------------------------------------------------------- identidade
    @staticmethod
    def gerar_id_unico(caminho: str) -> str:
        """Identidade baseada no conteudo, nao no caminho.

        Assim mover ou renomear o arquivo preserva progresso, grifos e notas --
        antes qualquer mudanca de pasta criava um livro duplicado do zero.
        """
        digest = hashlib.md5()
        try:
            tamanho = os.path.getsize(caminho)
            digest.update(str(tamanho).encode())
            with open(caminho, "rb") as arquivo:
                digest.update(arquivo.read(512 * 1024))
        except Exception:
            digest.update(os.path.abspath(caminho).encode("utf-8", "ignore"))
        return digest.hexdigest()

    # -------------------------------------------------------------- metadados
    @staticmethod
    def extrair_metadados(caminho: str) -> dict:
        info = {
            "titulo": os.path.splitext(os.path.basename(caminho))[0],
            "autor": "",
            "total_paginas": 0,
        }
        try:
            with abrir(caminho) as doc:
                info["total_paginas"] = len(doc)
                meta = doc.metadata or {}
                titulo = (meta.get("title") or "").strip()
                autor = (meta.get("author") or "").strip()
                if titulo and len(titulo) > 3 and not _titulo_e_lixo(titulo):
                    info["titulo"] = _RE_ESPACOS.sub(" ", titulo)[:120]
                if autor and len(autor) > 2:
                    info["autor"] = _RE_ESPACOS.sub(" ", autor)[:80]
        except Exception as erro:
            print(f"[PDFService] Metadados indisponiveis: {erro}")
        return info

    # ------------------------------------------------------------------- capa
    @staticmethod
    def extrair_capa(caminho_pdf: str, id_livro: str, forcar: bool = False):
        caminho_capa = os.path.join(PASTA_CAPAS, f"{id_livro}.png")
        if os.path.exists(caminho_capa) and not forcar:
            return caminho_capa
        try:
            with abrir(caminho_pdf) as doc:
                if len(doc) == 0:
                    return None
                pagina = doc.load_page(0)
                # Escala fixa por altura: capas ficam nitidas em qualquer PDF.
                escala = min(2.0, max(0.6, 420 / max(1.0, pagina.rect.height)))
                pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala))
                pix.save(caminho_capa)
            return caminho_capa
        except Exception as erro:
            print(f"[PDFService] Erro ao extrair capa: {erro}")
            return None

    # ------------------------------------------------------------------ abrir
    @staticmethod
    def abrir_documento(caminho_pdf: str):
        try:
            doc = abrir(caminho_pdf)
            if doc.needs_pass:
                doc.close()
                return None
            return doc
        except Exception as erro:
            print(f"[PDFService] Erro ao abrir PDF: {erro}")
            return None

    @staticmethod
    def esta_protegido(caminho_pdf: str) -> bool:
        try:
            with abrir(caminho_pdf) as doc:
                return bool(doc.needs_pass)
        except Exception:
            return False

    # ------------------------------------------------------------ busca global
    @staticmethod
    def buscar_no_documento(documento, termo: str, cancelar: threading.Event = None,
                            limite: int = 400):
        """Percorre o livro inteiro e devolve [(pagina, trecho), ...].

        A busca antiga so enxergava a pagina aberta, o que a tornava inutil em
        livros longos.
        """
        resultados = []
        if not documento or not termo or len(termo.strip()) < 2:
            return resultados

        alvo = termo.strip().lower()
        for numero in range(len(documento)):
            if cancelar is not None and cancelar.is_set():
                break
            try:
                texto = documento.load_page(numero).get_text("text")
            except Exception:
                continue
            if not texto:
                continue

            plano = _RE_ESPACOS.sub(" ", texto)
            minusculo = plano.lower()
            inicio = 0
            while True:
                pos = minusculo.find(alvo, inicio)
                if pos < 0:
                    break
                antes = plano[max(0, pos - 55):pos]
                achado = plano[pos:pos + len(alvo)]
                depois = plano[pos + len(alvo):pos + len(alvo) + 55]
                prefixo = "..." if pos > 55 else ""
                resultados.append((numero, f"{prefixo}{antes}[{achado}]{depois}..."))
                inicio = pos + len(alvo)
                if len(resultados) >= limite:
                    return resultados
        return resultados

    # ---------------------------------------------------------------- sumario
    @staticmethod
    def obter_sumario(documento):
        try:
            return documento.get_toc() or []
        except Exception:
            return []

    @staticmethod
    def contar_palavras_amostra(documento, amostra: int = 12) -> float:
        """Media de palavras por pagina, para estimar tempo restante de leitura."""
        total = len(documento)
        if total == 0:
            return 0.0
        passo = max(1, total // amostra)
        indices = list(range(0, total, passo))[:amostra]
        soma = 0
        lidas = 0
        for i in indices:
            try:
                soma += len(documento.load_page(i).get_text("text").split())
                lidas += 1
            except Exception:
                continue
        return soma / lidas if lidas else 0.0
