# database/sqlite_db.py
"""Esquema, conexao e migracoes do banco SQLite."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from configuracoes import ARQUIVO_BANCO, ARQUIVO_BIBLIOTECA, ARQUIVO_CONFIG


# Uma conexao viva por thread. Abrir e fechar a cada consulta custava caro:
# virar uma pagina disparava 8 conexoes, e cada uma reexecutava
# `PRAGMA journal_mode = WAL`, que mexe em disco. Era ~30 ms so nisso.
_local = threading.local()


def get_conexao():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    conn = sqlite3.connect(ARQUIVO_BANCO, timeout=8.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    # WAL fica gravado no proprio arquivo: basta definir uma vez por conexao.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    _local.conn = conn
    return conn


def fechar_conexao():
    """Fecha a conexao desta thread (usado ao encerrar o aplicativo)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


@contextmanager
def conexao():
    """Commit automatico no sucesso, rollback na falha."""
    conn = get_conexao()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


_ESQUEMA = [
    """
    CREATE TABLE IF NOT EXISTS configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pastas (
        id_pasta  INTEGER PRIMARY KEY AUTOINCREMENT,
        nome      TEXT NOT NULL,
        id_pai    INTEGER,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_pai) REFERENCES pastas (id_pasta) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS livros (
        id_livro            TEXT PRIMARY KEY,
        caminho_pdf         TEXT,
        caminho_capa        TEXT,
        titulo              TEXT,
        autor               TEXT,
        total_paginas       INTEGER DEFAULT 0,
        pagina_atual        INTEGER DEFAULT 0,
        zoom                INTEGER DEFAULT 17,
        tema                TEXT DEFAULT 'Escuro',
        segundos_lidos      INTEGER DEFAULT 0,
        fixado              INTEGER DEFAULT 0,
        id_pasta            INTEGER REFERENCES pastas (id_pasta) ON DELETE SET NULL,
        perfil              TEXT DEFAULT 'auto',
        render              TEXT DEFAULT 'auto',
        categoria           TEXT DEFAULT '',
        pref_fonte          TEXT,
        pref_tamanho        INTEGER,
        pref_altura         REAL,
        pref_largura        INTEGER,
        data_adicao         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        data_ultimo_acesso  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS favoritos (
        id_livro TEXT,
        pagina   INTEGER,
        rotulo   TEXT DEFAULT '',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id_livro, pagina),
        FOREIGN KEY (id_livro) REFERENCES livros (id_livro) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS anotacoes (
        id_livro      TEXT,
        pagina        INTEGER,
        nota          TEXT,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id_livro, pagina),
        FOREIGN KEY (id_livro) REFERENCES livros (id_livro) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grifos (
        id_grifo    INTEGER PRIMARY KEY AUTOINCREMENT,
        id_livro    TEXT,
        pagina      INTEGER,
        inicio      TEXT,
        fim         TEXT,
        inicio_char INTEGER,
        fim_char    INTEGER,
        trecho      TEXT DEFAULT '',
        cor         TEXT DEFAULT 'padrao',
        criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (id_livro) REFERENCES livros (id_livro) ON DELETE CASCADE
    )
    """,
    # Senha de PDF protegido, cifrada com DPAPI (database/cofre.py) -- nunca
    # em texto puro. Tabela a parte, e nao coluna em `livros`, por dois
    # motivos: a senha precisa ser gravada ANTES da linha do livro existir
    # (metadados e capa so saem depois de destrancar o arquivo), e assim o
    # `SELECT l.*` da estante nao arrasta o segredo para cada cartao.
    """
    CREATE TABLE IF NOT EXISTS senhas_livro (
        id_livro      TEXT PRIMARY KEY,
        senha_cifrada TEXT NOT NULL,
        criado_em     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Revisao espacada (services/revisao.py, algoritmo SM-2). Cartao ligado a
    # um grifo (frente vazia = usa o trecho atual do grifo) ou livre
    # (id_grifo NULL). Apagar o grifo apaga o cartao junto (CASCADE).
    """
    CREATE TABLE IF NOT EXISTS cartoes_revisao (
        id_cartao       INTEGER PRIMARY KEY AUTOINCREMENT,
        id_grifo        INTEGER UNIQUE REFERENCES grifos (id_grifo) ON DELETE CASCADE,
        id_livro        TEXT REFERENCES livros (id_livro) ON DELETE CASCADE,
        frente          TEXT DEFAULT '',
        verso           TEXT DEFAULT '',
        facilidade      REAL DEFAULT 2.5,
        intervalo_dias  INTEGER DEFAULT 0,
        repeticoes      INTEGER DEFAULT 0,
        proxima_revisao TEXT,
        ultima_revisao  TEXT,
        criado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Diario de leitura: `livros.segundos_lidos` so guarda o total, e
    # estatistica por dia (sequencia de dias, grafico do mes) precisa da data.
    # Sem FOREIGN KEY de proposito: remover um livro da estante nao deve
    # apagar o historico nem quebrar a sequencia de dias lidos.
    """
    CREATE TABLE IF NOT EXISTS leitura_diaria (
        data     TEXT NOT NULL,
        id_livro TEXT NOT NULL,
        segundos INTEGER DEFAULT 0,
        paginas  INTEGER DEFAULT 0,
        PRIMARY KEY (data, id_livro)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cartoes_proxima ON cartoes_revisao (proxima_revisao)",
    "CREATE INDEX IF NOT EXISTS idx_grifos_pagina ON grifos (id_livro, pagina)",
    "CREATE INDEX IF NOT EXISTS idx_anotacoes_livro ON anotacoes (id_livro)",
    "CREATE INDEX IF NOT EXISTS idx_favoritos_livro ON favoritos (id_livro)",
    "CREATE INDEX IF NOT EXISTS idx_pastas_pai ON pastas (id_pai)",
]

# Colunas adicionadas depois da v1: aplicadas com ALTER em bancos existentes.
_COLUNAS_NOVAS = {
    "livros": [
        ("titulo", "TEXT"),
        ("autor", "TEXT"),
        ("total_paginas", "INTEGER DEFAULT 0"),
        ("segundos_lidos", "INTEGER DEFAULT 0"),
        ("fixado", "INTEGER DEFAULT 0"),
        ("data_adicao", "TIMESTAMP"),
        ("data_ultimo_acesso", "TIMESTAMP"),
        # SQLite so aceita ADD COLUMN com REFERENCES se o padrao for NULL --
        # e o caso: livro sem pasta fica solto na raiz da estante.
        ("id_pasta", "INTEGER REFERENCES pastas (id_pasta) ON DELETE SET NULL"),
        # Propriedades DO LIVRO. 'auto' deixa o motor classificar; as demais
        # colunas, quando nulas, caem na preferencia global.
        # Camada 1: como exibir. Camada 2: sobre o que e (so organiza).
        ("perfil", "TEXT DEFAULT 'auto'"),
        ("render", "TEXT DEFAULT 'auto'"),
        ("categoria", "TEXT DEFAULT ''"),
        ("pref_fonte", "TEXT"),
        ("pref_tamanho", "INTEGER"),
        ("pref_altura", "REAL"),
        ("pref_largura", "INTEGER"),
        # Caractere no topo da tela DENTRO da pagina atual: reabrir o livro volta
        # ao paragrafo exato, nao ao topo da pagina.
        ("rolagem", "INTEGER DEFAULT 0"),
    ],
    "favoritos": [("rotulo", "TEXT DEFAULT ''"), ("criado_em", "TIMESTAMP")],
    "anotacoes": [("atualizado_em", "TIMESTAMP")],
    "grifos": [
        ("inicio_char", "INTEGER"),
        ("fim_char", "INTEGER"),
        ("trecho", "TEXT DEFAULT ''"),
        ("cor", "TEXT DEFAULT 'padrao'"),
        ("criado_em", "TIMESTAMP"),
    ],
}


def inicializar_banco():
    with conexao() as conn:
        cursor = conn.cursor()
        for comando in _ESQUEMA:
            cursor.execute(comando)
        _aplicar_colunas_novas(cursor)
        # A coluna 'perfil' nasceu binaria (tecnico/prosa). Agora quem manda
        # e 'render', com sete tipos; o valor antigo vira o equivalente.
        try:
            cursor.execute("UPDATE livros SET render = 'programacao' "
                           "WHERE render IN ('auto', '') AND perfil = 'tecnico'")
            cursor.execute("UPDATE livros SET render = 'prosa' "
                           "WHERE render IN ('auto', '') AND perfil = 'prosa'")
        except sqlite3.Error:
            pass
    _migrar_json_legado()


def _aplicar_colunas_novas(cursor):
    for tabela, colunas in _COLUNAS_NOVAS.items():
        try:
            existentes = {linha["name"] for linha in cursor.execute(f"PRAGMA table_info({tabela})")}
        except sqlite3.Error:
            continue
        if not existentes:
            continue
        for nome, tipo in colunas:
            if nome not in existentes:
                try:
                    cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
                except sqlite3.Error:
                    pass


def _migrar_json_legado():
    """Importa biblioteca.json / config.json da versao 1.0, se ainda existirem."""
    if not (os.path.exists(ARQUIVO_CONFIG) or os.path.exists(ARQUIVO_BIBLIOTECA)):
        return

    try:
        with conexao() as conn:
            cursor = conn.cursor()

            if os.path.exists(ARQUIVO_CONFIG):
                with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as arquivo:
                    dados = json.load(arquivo)
                cursor.execute(
                    "INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES (?, ?)",
                    ("provedor_ia", dados.get("provedor_ia", "Offline")))
                for provedor, chave in (dados.get("api_keys") or {}).items():
                    cursor.execute(
                        "INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES (?, ?)",
                        (f"api_key_{provedor}", chave))
                os.rename(ARQUIVO_CONFIG, ARQUIVO_CONFIG + ".bkp")

            if os.path.exists(ARQUIVO_BIBLIOTECA):
                with open(ARQUIVO_BIBLIOTECA, "r", encoding="utf-8") as arquivo:
                    biblioteca = json.load(arquivo)

                for id_livro, dados in biblioteca.items():
                    caminho = dados.get("caminho", "")
                    cursor.execute("""
                        INSERT OR IGNORE INTO livros
                            (id_livro, caminho_pdf, caminho_capa, titulo,
                             pagina_atual, zoom, tema)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (id_livro, caminho, dados.get("capa", ""),
                          os.path.splitext(os.path.basename(caminho))[0],
                          dados.get("pagina_atual", 0), dados.get("zoom", 17),
                          dados.get("tema", "Escuro")))

                    for pagina in dados.get("favoritos", []):
                        cursor.execute(
                            "INSERT OR IGNORE INTO favoritos (id_livro, pagina) VALUES (?, ?)",
                            (id_livro, pagina))

                    for pagina, nota in (dados.get("anotacoes") or {}).items():
                        if nota and nota.strip():
                            cursor.execute(
                                "INSERT OR IGNORE INTO anotacoes (id_livro, pagina, nota) "
                                "VALUES (?, ?, ?)", (id_livro, int(pagina), nota))

                os.rename(ARQUIVO_BIBLIOTECA, ARQUIVO_BIBLIOTECA + ".bkp")
    except Exception as erro:
        print(f"[Banco] Falha na migracao do formato antigo: {erro}")
