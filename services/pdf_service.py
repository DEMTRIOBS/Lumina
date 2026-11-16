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


def abrir(caminho: str, senha: str = None, usar_guardada: bool = True):
    """Abre PDF ou EPUB. Use SEMPRE isto no lugar de fitz.open.

    PDF protegido e destrancado aqui, com a senha informada ou com a que o
    usuario ja digitou uma vez (memoria da sessao ou banco, cifrada). Assim
    leitor, capa, metadados e buscas abrem o livro sem saber que ele tem
    senha. Se nenhuma servir, o documento volta trancado: use `bloqueado()`.
    """
    doc = fitz.open(caminho)
    if bloqueado(doc):
        candidatas = [senha] if senha else []
        if usar_guardada and not candidatas:
            candidatas.append(_senha_guardada(caminho))
        for tentativa in candidatas:
            if tentativa and doc.authenticate(tentativa):
                break
    if doc.is_reflowable:
        doc.layout(**_LAYOUT_REFLUIVEL)
    return doc


def bloqueado(doc) -> bool:
    """Documento ainda pede senha para ser lido.

    `needs_pass` continua verdadeiro mesmo DEPOIS de `authenticate()` dar
    certo; quem diz se o conteudo ja esta acessivel e `is_encrypted`. E PDF
    so com senha de dono (restricao de impressao, copia) abre direto.

    A ORDEM importa: no PyMuPDF 1.28, ler `needs_pass` depois de autenticar
    refaz a checagem com senha vazia e estraga a chave de decifragem -- as
    paginas passam a sair vazias ("aes padding out of range"). Com
    `is_encrypted` na frente, o `and` nunca chega a ele num documento ja
    destrancado.
    """
    return bool(doc.is_encrypted) and bool(doc.needs_pass)


# ----------------------------------------------------------- SENHAS LEMBRADAS
# caminho normalizado -> senha. Evita ir ao banco (e ao DPAPI) a cada abertura:
# o leitor reabre o arquivo em threads de busca, indice e exportacao.
_SENHAS = {}
_TRAVA_SENHAS = threading.Lock()


def _chave_senha(caminho: str) -> str:
    return os.path.normcase(os.path.abspath(str(caminho)))


def lembrar_senha(caminho: str, senha: str):
    """Guarda a senha so na memoria desta sessao (o banco e com o chamador)."""
    with _TRAVA_SENHAS:
        _SENHAS[_chave_senha(caminho)] = senha


def _senha_guardada(caminho: str):
    chave = _chave_senha(caminho)
    with _TRAVA_SENHAS:
        if chave in _SENHAS:
            return _SENHAS[chave]
    # Busca pela identidade do CONTEUDO, nao pelo caminho: o livro movido de
    # pasta continua abrindo, e a senha pode ser gravada antes de a linha do
    # livro existir na estante. Import tardio: o servico nao depende do banco
    # para nada alem disso (os testes do motor o usam sem banco nenhum).
    try:
        from database.biblioteca import BancoBiblioteca
        senha = BancoBiblioteca().obter_senha(PDFService.gerar_id_unico(caminho))
    except Exception as erro:
        print(f"[PDFService] Senha guardada indisponivel: {erro}")
        senha = None
    if senha:
        lembrar_senha(caminho, senha)
    return senha

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
        """Documento pronto para ler, ou None. Protegido abre com a senha lembrada."""
        try:
            doc = abrir(caminho_pdf)
            if bloqueado(doc):
                doc.close()
                return None
            return doc
        except Exception as erro:
            print(f"[PDFService] Erro ao abrir PDF: {erro}")
            return None

    @staticmethod
    def esta_protegido(caminho_pdf: str) -> bool:
        """O arquivo tem senha de abertura (lembrada ou nao)."""
        # EPUB nao tem senha no MuPDF; abrir um so para perguntar custaria a
        # paginacao inteira do livro.
        if not str(caminho_pdf).lower().endswith(".pdf"):
            return False
        try:
            with abrir(caminho_pdf, usar_guardada=False) as doc:
                return bloqueado(doc)
        except Exception:
            return False

    @staticmethod
    def precisa_senha(caminho_pdf: str) -> bool:
        """Protegido E sem senha lembrada que funcione: hora de perguntar."""
        if not str(caminho_pdf).lower().endswith(".pdf"):
            return False
        try:
            with abrir(caminho_pdf) as doc:
                return bloqueado(doc)
        except Exception:
            return False

    @staticmethod
    def testar_senha(caminho_pdf: str, senha: str) -> bool:
        """A senha destranca o arquivo? Ignora qualquer senha ja lembrada."""
        if not senha:
            return False
        try:
            with abrir(caminho_pdf, senha=senha, usar_guardada=False) as doc:
                return not bloqueado(doc)
        except Exception:
            return False

    @staticmethod
    def lembrar_senha(caminho_pdf: str, senha: str):
        lembrar_senha(caminho_pdf, senha)

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
