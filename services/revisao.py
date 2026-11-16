# services/revisao.py
"""Revisao espacada dos grifos (SM-2) e estatisticas de leitura.

Grifar e o primeiro passo; lembrar e outro. Cada grifo pode virar um cartao
que volta na hora certa -- a curva do esquecimento diz que rever um pouco
antes de esquecer custa muito menos do que reestudar. O agendamento e o SM-2
classico (SuperMemo 2, Wozniak 1987), o mesmo que o Anki usou por anos:
simples, previsivel e sem parametros para o usuario ajustar.

TABELAS (criadas em database/sqlite_db.py, pelo mecanismo normal de
inicializacao -- `CREATE TABLE IF NOT EXISTS`, nunca altera as existentes):
  cartoes_revisao  um cartao por grifo (id_grifo UNIQUE) ou livre (NULL)
  leitura_diaria   segundos e paginas por (data, livro); alimentada por
                   BancoBiblioteca.somar_tempo_leitura / definir_pagina

DATAS: tudo em data LOCAL 'AAAA-MM-DD'. "Revisar hoje" e "dias seguidos" sao
conceitos do calendario da pessoa, nao de UTC.

Todas as funcoes aceitam `hoje` (datetime.date) para teste; a interface nao
precisa passar.
"""
import datetime as _dt
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from database.sqlite_db import conexao

FACILIDADE_INICIAL = 2.5
FACILIDADE_MINIMA = 1.3
# Um dia so conta para a sequencia com leitura de verdade: abrir o livro por
# engano e fechar em 5 segundos nao deveria manter (nem criar) uma sequencia.
MINIMO_SEGUNDOS_DIA = 60

_CHAVE_AUTO = "revisao_auto_grifos"      # '1' cria cartao para grifo novo
_CHAVE_MARCO = "revisao_ultimo_grifo"    # maior id_grifo ja sincronizado


def _hoje(hoje=None) -> _dt.date:
    return hoje or _dt.date.today()


# ====================================================================== SM-2
def calcular_sm2(facilidade: float, intervalo_dias: int, repeticoes: int,
                 nota: int) -> Tuple[float, int, int]:
    """Um passo do SM-2. Devolve (facilidade, intervalo_dias, repeticoes).

    nota 0..5:  5 perfeito, 4 com hesitacao, 3 com esforco,
                2 errou mas reconheceu, 1 errou, 0 branco total.
    Nota < 3 recomeca a sequencia (intervalo 1 dia) SEM mexer na facilidade,
    como no algoritmo original; nota >= 3 avanca 1 -> 6 -> intervalo*EF e
    ajusta EF = EF + 0.1 - (5-q)*(0.08 + (5-q)*0.02), com piso 1.3.
    """
    nota = int(nota)
    if not 0 <= nota <= 5:
        raise ValueError("nota deve estar entre 0 e 5")
    facilidade = float(facilidade or FACILIDADE_INICIAL)
    intervalo_dias = int(intervalo_dias or 0)
    repeticoes = int(repeticoes or 0)

    if nota < 3:
        return facilidade, 1, 0

    if repeticoes == 0:
        novo_intervalo = 1
    elif repeticoes == 1:
        novo_intervalo = 6
    else:
        novo_intervalo = max(1, round(intervalo_dias * facilidade))
    erro = 5 - nota
    facilidade = max(FACILIDADE_MINIMA, facilidade + 0.1 - erro * (0.08 + erro * 0.02))
    return round(facilidade, 4), novo_intervalo, repeticoes + 1


# ==================================================================== cartoes
@dataclass
class Cartao:
    id_cartao: int
    frente: str                    # trecho do grifo (ou texto livre)
    verso: str                     # nota da pagina do grifo (ou texto livre); pode ser ''
    id_grifo: Optional[int] = None
    id_livro: Optional[str] = None
    titulo_livro: Optional[str] = None
    pagina: Optional[int] = None   # pagina do grifo (0-based, como no banco)
    facilidade: float = FACILIDADE_INICIAL
    intervalo_dias: int = 0
    repeticoes: int = 0
    proxima_revisao: Optional[str] = None   # 'AAAA-MM-DD'; None = nunca revisado
    ultima_revisao: Optional[str] = None

    @property
    def novo(self) -> bool:
        return self.ultima_revisao is None


# frente/verso vazios num cartao de grifo seguem o grifo e a nota da pagina:
# corrigir o grifo ou a nota corrige o cartao, sem copia desatualizada.
_SELECT_CARTAO = """
    SELECT c.id_cartao, c.id_grifo,
           COALESCE(c.id_livro, g.id_livro) AS id_livro,
           COALESCE(NULLIF(c.frente, ''), g.trecho, '') AS frente,
           COALESCE(NULLIF(c.verso, ''), a.nota, '') AS verso,
           l.titulo AS titulo_livro, g.pagina,
           c.facilidade, c.intervalo_dias, c.repeticoes,
           c.proxima_revisao, c.ultima_revisao
      FROM cartoes_revisao c
      LEFT JOIN grifos g    ON g.id_grifo = c.id_grifo
      LEFT JOIN livros l    ON l.id_livro = COALESCE(c.id_livro, g.id_livro)
      LEFT JOIN anotacoes a ON a.id_livro = g.id_livro AND a.pagina = g.pagina
"""


def _cartao(linha) -> Cartao:
    return Cartao(
        id_cartao=linha["id_cartao"], frente=linha["frente"] or "",
        verso=linha["verso"] or "", id_grifo=linha["id_grifo"],
        id_livro=linha["id_livro"], titulo_livro=linha["titulo_livro"],
        pagina=linha["pagina"],
        facilidade=float(linha["facilidade"] or FACILIDADE_INICIAL),
        intervalo_dias=int(linha["intervalo_dias"] or 0),
        repeticoes=int(linha["repeticoes"] or 0),
        proxima_revisao=linha["proxima_revisao"], ultima_revisao=linha["ultima_revisao"])


def obter_cartao(id_cartao: int) -> Optional[Cartao]:
    with conexao() as conn:
        linha = conn.execute(_SELECT_CARTAO + " WHERE c.id_cartao = ?",
                             (int(id_cartao),)).fetchone()
    return _cartao(linha) if linha else None


def criar_cartao_de_grifo(id_grifo: int, hoje=None) -> Optional[int]:
    """Cartao para o grifo; devolve o id (o existente, se ja havia).

    None se o grifo nao existe ou nao tem trecho (grifo da v1, sem texto).
    O cartao novo vence hoje: aparece ja na proxima sessao de revisao.
    """
    with conexao() as conn:
        return _criar_de_grifo(conn, int(id_grifo), _hoje(hoje))


def _criar_de_grifo(conn, id_grifo, hoje):
    existente = conn.execute("SELECT id_cartao FROM cartoes_revisao WHERE id_grifo = ?",
                             (id_grifo,)).fetchone()
    if existente:
        return existente["id_cartao"]
    grifo = conn.execute("SELECT id_livro, trecho FROM grifos WHERE id_grifo = ?",
                         (id_grifo,)).fetchone()
    if not grifo or not (grifo["trecho"] or "").strip():
        return None
    cursor = conn.execute("""
        INSERT INTO cartoes_revisao (id_grifo, id_livro, proxima_revisao, criado_em)
        VALUES (?, ?, ?, ?)
    """, (id_grifo, grifo["id_livro"], hoje.isoformat(),
          _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return cursor.lastrowid


def criar_cartao_livre(frente: str, verso: str = "", id_livro: Optional[str] = None,
                       hoje=None) -> int:
    """Cartao pergunta/resposta escrito pela pessoa (ou vindo dos flashcards da IA)."""
    frente = (frente or "").strip()
    if not frente:
        raise ValueError("a frente do cartao nao pode ficar vazia")
    with conexao() as conn:
        cursor = conn.execute("""
            INSERT INTO cartoes_revisao (id_livro, frente, verso, proxima_revisao, criado_em)
            VALUES (?, ?, ?, ?, ?)
        """, (id_livro, frente, (verso or "").strip(), _hoje(hoje).isoformat(),
              _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return cursor.lastrowid


def editar_cartao(id_cartao: int, frente: Optional[str] = None,
                  verso: Optional[str] = None) -> None:
    """None mantem o campo. '' num cartao de grifo volta a seguir o grifo/nota."""
    campos, valores = [], []
    if frente is not None:
        campos.append("frente = ?")
        valores.append(frente.strip())
    if verso is not None:
        campos.append("verso = ?")
        valores.append(verso.strip())
    if not campos:
        return
    with conexao() as conn:
        conn.execute(f"UPDATE cartoes_revisao SET {', '.join(campos)} WHERE id_cartao = ?",
                     (*valores, int(id_cartao)))


def excluir_cartao(id_cartao: int) -> None:
    """Apaga o cartao (o grifo continua). A sincronizacao automatica nao o
    recria: ela so olha grifos mais novos que o ultimo sincronizado."""
    with conexao() as conn:
        conn.execute("DELETE FROM cartoes_revisao WHERE id_cartao = ?", (int(id_cartao),))


def cartoes_para_hoje(limite: int = 20, id_livro: Optional[str] = None,
                      hoje=None) -> List[Cartao]:
    """Cartoes vencidos (proxima_revisao <= hoje), os mais atrasados primeiro;
    entre os de mesma data, os ja revisados antes dos novos."""
    parametros = [_hoje(hoje).isoformat()]
    filtro = ""
    if id_livro:
        filtro = " AND COALESCE(c.id_livro, g.id_livro) = ?"
        parametros.append(id_livro)
    parametros.append(max(0, int(limite)))
    with conexao() as conn:
        linhas = conn.execute(
            _SELECT_CARTAO +
            " WHERE (c.proxima_revisao IS NULL OR c.proxima_revisao <= ?)" + filtro +
            " ORDER BY COALESCE(c.proxima_revisao, '0000-00-00'),"
            " c.ultima_revisao IS NULL, c.id_cartao LIMIT ?", parametros).fetchall()
    return [_cartao(linha) for linha in linhas]


def contar_pendentes(id_livro: Optional[str] = None, hoje=None) -> int:
    """Quantos cartoes vencem hoje (para um contador/badge na interface)."""
    parametros = [_hoje(hoje).isoformat()]
    filtro = ""
    if id_livro:
        filtro = " AND COALESCE(c.id_livro, g.id_livro) = ?"
        parametros.append(id_livro)
    with conexao() as conn:
        linha = conn.execute(
            "SELECT COUNT(*) AS n FROM cartoes_revisao c "
            "LEFT JOIN grifos g ON g.id_grifo = c.id_grifo "
            "WHERE (c.proxima_revisao IS NULL OR c.proxima_revisao <= ?)" + filtro,
            parametros).fetchone()
    return int(linha["n"])


def registrar_resposta(id_cartao: int, nota: int, hoje=None) -> Cartao:
    """Aplica o SM-2 e reagenda. Devolve o cartao ja atualizado.

    Levanta ValueError para nota fora de 0..5 ou cartao inexistente.
    """
    hoje = _hoje(hoje)
    with conexao() as conn:
        atual = conn.execute(
            "SELECT facilidade, intervalo_dias, repeticoes FROM cartoes_revisao "
            "WHERE id_cartao = ?", (int(id_cartao),)).fetchone()
        if atual is None:
            raise ValueError(f"cartao {id_cartao} nao existe")
        facilidade, intervalo, repeticoes = calcular_sm2(
            atual["facilidade"], atual["intervalo_dias"], atual["repeticoes"], nota)
        conn.execute("""
            UPDATE cartoes_revisao
               SET facilidade = ?, intervalo_dias = ?, repeticoes = ?,
                   proxima_revisao = ?, ultima_revisao = ?
             WHERE id_cartao = ?
        """, (facilidade, intervalo, repeticoes,
              (hoje + _dt.timedelta(days=intervalo)).isoformat(), hoje.isoformat(),
              int(id_cartao)))
    return obter_cartao(id_cartao)


# ============================================================ sincronizacao
def _ler_config(conn, chave, padrao):
    linha = conn.execute("SELECT valor FROM configuracoes WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha and linha["valor"] is not None else padrao


def _gravar_config(conn, chave, valor):
    conn.execute("INSERT INTO configuracoes (chave, valor) VALUES (?, ?) "
                 "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, str(valor)))


def auto_grifos_ativo() -> bool:
    with conexao() as conn:
        return _ler_config(conn, _CHAVE_AUTO, "1") == "1"


def definir_auto_grifos(ativo: bool) -> None:
    """Liga/desliga a criacao automatica de cartoes para grifos novos.

    (Le e grava direto na tabela `configuracoes`; o BancoConfig da interface
    mantem cache proprio, entao use esta funcao em vez dele para esta chave.)
    """
    with conexao() as conn:
        _gravar_config(conn, _CHAVE_AUTO, "1" if ativo else "0")


def sincronizar_grifos(forcar: bool = False, hoje=None) -> int:
    """Cria cartoes para os grifos feitos desde a ultima sincronizacao.

    Barata (uma consulta por indice): pode ser chamada ao abrir a tela de
    revisao ou ao fechar um livro. Respeita `auto_grifos_ativo()` a menos que
    `forcar`. Na primeira vez inclui todos os grifos ja existentes. Grifo cujo
    cartao a pessoa excluiu nao volta, porque o marco so anda para frente.
    Com o automatico desligado o marco tambem anda: religar nao despeja de uma
    vez os grifos feitos no periodo desligado.
    Devolve quantos cartoes foram criados.
    """
    hoje = _hoje(hoje)
    with conexao() as conn:
        ativo = forcar or _ler_config(conn, _CHAVE_AUTO, "1") == "1"
        marco = int(_ler_config(conn, _CHAVE_MARCO, "0") or 0)
        linhas = conn.execute("""
            SELECT g.id_grifo FROM grifos g
             WHERE g.id_grifo > ? AND g.inicio_char IS NOT NULL
               AND TRIM(COALESCE(g.trecho, '')) <> ''
          ORDER BY g.id_grifo
        """, (marco,)).fetchall()
        maior = conn.execute("SELECT COALESCE(MAX(id_grifo), 0) AS m FROM grifos").fetchone()["m"]
        criados = 0
        if ativo:
            for linha in linhas:
                antes = conn.execute("SELECT 1 FROM cartoes_revisao WHERE id_grifo = ?",
                                     (linha["id_grifo"],)).fetchone()
                if not antes and _criar_de_grifo(conn, linha["id_grifo"], hoje):
                    criados += 1
        _gravar_config(conn, _CHAVE_MARCO, max(marco, int(maior)))
    return criados


# ============================================================== estatisticas
def registrar_leitura(id_livro: str, segundos: int = 0, paginas: int = 0,
                      data: Optional[str] = None) -> None:
    """Soma no diario. Normalmente desnecessario: o tempo de sessao
    (somar_tempo_leitura) e as viradas de pagina (definir_pagina) ja gravam."""
    from database.biblioteca import BancoBiblioteca
    BancoBiblioteca().registrar_leitura_diaria(id_livro, segundos, paginas, data)


@dataclass
class DiaLeitura:
    data: str          # 'AAAA-MM-DD'
    segundos: int
    paginas: int


@dataclass
class LeituraLivro:
    id_livro: str
    titulo: str        # titulo atual; livro removido da estante vira o id
    segundos: int
    paginas: int


@dataclass
class Estatisticas:
    dias: List[DiaLeitura] = field(default_factory=list)  # janela completa, dias vazios = 0
    total_segundos: int = 0                 # na janela
    total_paginas: int = 0                  # na janela
    dias_lidos: int = 0                     # dias da janela que contam (MINIMO_SEGUNDOS_DIA ou >=1 pagina)
    sequencia_atual: int = 0                # dias seguidos ate hoje (ou ontem, se hoje ainda nao leu)
    maior_sequencia: int = 0                # de todo o historico
    por_livro: List[LeituraLivro] = field(default_factory=list)   # na janela, mais tempo primeiro
    paginas_por_hora: Optional[float] = None  # na janela; None sem tempo suficiente
    cartoes_pendentes: int = 0


def _conta(segundos, paginas) -> bool:
    return (segundos or 0) >= MINIMO_SEGUNDOS_DIA or (paginas or 0) > 0


def calcular_sequencias(datas_lidas, hoje: _dt.date) -> Tuple[int, int]:
    """(sequencia_atual, maior_sequencia) a partir das datas que contam.

    A sequencia atual sobrevive ao dia de hoje ainda sem leitura: as 8 da
    manha ninguem perdeu a sequencia por nao ter lido ainda.
    """
    dias = sorted({d if isinstance(d, _dt.date) else _dt.date.fromisoformat(d)
                   for d in datas_lidas})
    maior, corrida, anterior = 0, 0, None
    for dia in dias:
        corrida = corrida + 1 if anterior and (dia - anterior).days == 1 else 1
        maior = max(maior, corrida)
        anterior = dia

    conjunto = set(dias)
    cursor = hoje if hoje in conjunto else hoje - _dt.timedelta(days=1)
    atual = 0
    while cursor in conjunto:
        atual += 1
        cursor -= _dt.timedelta(days=1)
    return atual, maior


def estatisticas(dias: int = 30, hoje=None) -> Estatisticas:
    """Resumo dos ultimos `dias` dias (hoje incluido)."""
    hoje = _hoje(hoje)
    dias = max(1, int(dias))
    inicio = hoje - _dt.timedelta(days=dias - 1)
    with conexao() as conn:
        por_dia = conn.execute("""
            SELECT data, SUM(segundos) AS s, SUM(paginas) AS p
              FROM leitura_diaria GROUP BY data
        """).fetchall()
        livros = conn.execute("""
            SELECT d.id_livro, COALESCE(NULLIF(l.titulo, ''), d.id_livro) AS titulo,
                   SUM(d.segundos) AS s, SUM(d.paginas) AS p
              FROM leitura_diaria d
              LEFT JOIN livros l ON l.id_livro = d.id_livro
             WHERE d.data BETWEEN ? AND ?
          GROUP BY d.id_livro
          ORDER BY s DESC, p DESC
        """, (inicio.isoformat(), hoje.isoformat())).fetchall()
    try:
        pendentes = contar_pendentes(hoje=hoje)
    except sqlite3.Error:
        pendentes = 0

    mapa = {linha["data"]: (int(linha["s"] or 0), int(linha["p"] or 0)) for linha in por_dia}
    resultado = Estatisticas(cartoes_pendentes=pendentes)
    for i in range(dias):
        data = (inicio + _dt.timedelta(days=i)).isoformat()
        s, p = mapa.get(data, (0, 0))
        resultado.dias.append(DiaLeitura(data, s, p))
        resultado.total_segundos += s
        resultado.total_paginas += p
        resultado.dias_lidos += 1 if _conta(s, p) else 0

    validas = []
    for data, (s, p) in mapa.items():
        if not _conta(s, p):
            continue
        try:
            validas.append(_dt.date.fromisoformat(data))
        except ValueError:
            continue
    resultado.sequencia_atual, resultado.maior_sequencia = calcular_sequencias(validas, hoje)

    resultado.por_livro = [LeituraLivro(l["id_livro"], l["titulo"], int(l["s"] or 0),
                                        int(l["p"] or 0)) for l in livros]
    # Menos de 5 minutos lidos da uma media sem sentido (20 paginas em 1 min).
    if resultado.total_segundos >= 300 and resultado.total_paginas:
        resultado.paginas_por_hora = round(
            resultado.total_paginas / (resultado.total_segundos / 3600), 1)
    return resultado
