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
    paginado = epub_pronto(caminho)
    if paginado:
        try:
            return fitz.open(paginado)
        except Exception:
            pass                       # cache corrompido: cai no EPUB
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


# ------------------------------------------------------- EPUB JA PAGINADO
# Abrir um EPUB obriga o MuPDF a paginar o livro INTEIRO (674 paginas: 1 a 2 s
# de CPU) -- e o leitor abre o arquivo varias vezes (tela, indice, analise,
# busca). Como o MuPDF segura o Python enquanto pagina, cada abertura, mesmo
# em segundo plano, congelava a janela. A saida: paginar UMA vez, ao importar,
# e guardar o resultado como PDF (abre em milissegundos, mesmas paginas).
#
# A paginacao roda num PROCESSO separado: numa thread ela seguraria o Python
# os ~8 s inteiros e a janela pararia do mesmo jeito.
_VERSAO_PAGINACAO = "a5v1"            # muda se _LAYOUT_REFLUIVEL mudar
_PRODUTOR_EPUB = "Lumina EPUB paginado"
_em_preparo = set()
_trava_preparo = threading.Lock()


def _pasta_epub():
    from configuracoes import user_path
    pasta = user_path("epub_paginado")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def caminho_cache_epub(caminho):
    ident = PDFService.gerar_id_unico(caminho)
    return os.path.join(_pasta_epub(), f"{ident}_{_VERSAO_PAGINACAO}.pdf")


def epub_pronto(caminho):
    if not str(caminho).lower().endswith(".epub"):
        return None
    try:
        destino = caminho_cache_epub(caminho)
        return destino if os.path.exists(destino) else None
    except Exception:
        return None


def veio_de_epub(doc) -> bool:
    """O documento e um EPUB (direto ou ja paginado)? Ai nao ha cabecalho
    nem rodape de verdade, e o texto pode passar da margem da pagina."""
    try:
        if doc.is_reflowable:
            return True
        return (doc.metadata or {}).get("producer", "").startswith(_PRODUTOR_EPUB)
    except Exception:
        return False


# Ligaduras: ao converter, o MuPDF grava "fi", "fl", "ff" como um caractere
# so (U+FB01...) e TODO hifen como inseparavel (U+2011). Pior: com algumas
# fontes a ligadura "ft" sai com um caractere qualquer ("soƤware"). As padrao
# vao numa tabela fixa; as da fonte sao descobertas na conversao (comparando
# com o texto do EPUB) e gravadas DENTRO do PDF, no campo `keywords`.
_LIGADURAS_PADRAO = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
                     "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "‑": "-"}
_MARCA_LIGADURAS = "lumina-ligaduras:"


def _descobrir_ligaduras(epub, pdf):
    """{caractere errado: texto certo} comparando paginas de amostra."""
    import difflib
    from collections import Counter
    contagem = Counter()
    padrao = str.maketrans(_LIGADURAS_PADRAO)
    # Todas as paginas, mas o diff (caro) so roda onde aparece um caractere
    # que nao existe no EPUB -- as trocas raras (a ligadura do negrito, por
    # exemplo) escapavam de uma amostra.
    for numero in range(len(epub)):
        try:
            a = epub.load_page(numero).get_text().replace("­", "").replace("\xa0", " ")
            b = pdf.load_page(numero).get_text().translate(padrao).replace("\xa0", " ")
        except Exception:
            continue
        if not any(not ch.isascii() for ch in set(b) - set(a)):
            continue
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, a, b, autojunk=False).get_opcodes():
            if (op == "replace" and j2 - j1 == 1 and 2 <= i2 - i1 <= 3
                    and a[i1:i2].isalpha() and not b[j1:j2].isascii()):
                contagem[(b[j1:j2], a[i1:i2])] += 1
    tabela = {}
    for (errado, certo), vezes in contagem.most_common():
        if errado not in tabela:
            tabela[errado] = certo
    return tabela


def tabela_de_texto(doc):
    """Tabela de `str.translate` que conserta o texto de um EPUB paginado.

    None para qualquer outro documento (nada a consertar). Fica guardada no
    proprio objeto: e consultada a cada pagina extraida.
    """
    tabela = getattr(doc, "_lumina_tabela", False)
    if tabela is not False:
        return tabela
    tabela = None
    try:
        meta = doc.metadata or {}
        if meta.get("producer", "").startswith(_PRODUTOR_EPUB):
            mapa = dict(_LIGADURAS_PADRAO)
            chaves = meta.get("keywords") or ""
            if _MARCA_LIGADURAS in chaves:
                import json
                mapa.update(json.loads(chaves.split(_MARCA_LIGADURAS, 1)[1]))
            tabela = str.maketrans(mapa)
    except Exception:
        tabela = None
    try:
        doc._lumina_tabela = tabela
    except Exception:
        pass
    return tabela


def texto_da_pagina(doc, numero):
    """Texto puro de uma pagina, ja consertado (use no lugar de get_text)."""
    texto = doc.load_page(numero).get_text("text")
    tabela = tabela_de_texto(doc)
    return texto.translate(tabela) if tabela else texto


def converter_epub(entrada, saida):
    """Pagina o EPUB e grava como PDF (sumario e metadados preservados).

    Chamada pelo processo auxiliar (`Lumina.exe --paginar-epub ...`).
    """
    import json
    doc = fitz.open(entrada)
    doc.layout(**_LAYOUT_REFLUIVEL)
    pdf = fitz.open("pdf", doc.convert_to_pdf())
    try:
        pdf.set_toc(doc.get_toc())
    except Exception:
        pass
    meta = {k: v for k, v in (doc.metadata or {}).items()
            if k in ("title", "author", "subject") and v}
    meta["producer"] = _PRODUTOR_EPUB
    meta["keywords"] = _MARCA_LIGADURAS + json.dumps(_descobrir_ligaduras(doc, pdf))
    pdf.set_metadata(meta)
    temporario = saida + ".parcial"
    pdf.save(temporario, garbage=3, deflate=True)
    pdf.close()
    doc.close()
    os.replace(temporario, saida)


def preparar_epub(caminho, ao_terminar=None):
    """Garante o EPUB paginado em segundo plano. Nao bloqueia.

    `ao_terminar(ok)` e chamado numa thread de trabalho (quem chama leva para
    a interface). Se ja estiver pronto, chama na hora com True.
    """
    if not str(caminho).lower().endswith(".epub"):
        return
    if epub_pronto(caminho):
        if ao_terminar:
            ao_terminar(True)
        return
    with _trava_preparo:
        if caminho in _em_preparo:
            return
        _em_preparo.add(caminho)

    def trabalhador():
        import subprocess
        import sys
        ok = False
        try:
            destino = caminho_cache_epub(caminho)
            if getattr(sys, "frozen", False):
                comando = [sys.executable, "--paginar-epub", caminho, destino]
            else:
                raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                comando = [sys.executable, os.path.join(raiz, "main.py"),
                           "--paginar-epub", caminho, destino]
            # subprocess.run espera SEM segurar o Python: a janela segue viva.
            # Sem a splash do executavel: o processo auxiliar nao tem janela.
            ambiente = dict(os.environ, PYINSTALLER_SUPPRESS_SPLASH_SCREEN="1")
            subprocess.run(comando, timeout=600, env=ambiente,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok = os.path.exists(destino)
        except Exception as erro:
            print(f"[PDFService] Paginacao do EPUB falhou: {erro}")
        finally:
            with _trava_preparo:
                _em_preparo.discard(caminho)
        if ao_terminar:
            ao_terminar(ok)

    threading.Thread(target=trabalhador, daemon=True, name="paginar-epub").start()


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
                texto = texto_da_pagina(documento, numero)
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
                soma += len(texto_da_pagina(documento, i).split())
                lidas += 1
            except Exception:
                continue
        return soma / lidas if lidas else 0.0
