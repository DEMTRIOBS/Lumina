# database/pastas.py
"""Pastas hierarquicas da estante.

Modelo escolhido: pasta EXCLUSIVA, como no explorador de arquivos -- um livro
esta em um lugar so. E o que resolve o "livros soltos" e o que torna
"Faculdade > Calculo I" uma arvore de verdade.

A hierarquia usa `id_pai` apontando para a propria tabela, com consultas
recursivas (CTE) para descendentes e caminho.
"""
import time

from database.sqlite_db import conexao

# Sentinelas de selecao usadas pela interface.
TODOS = "todos"
SOLTOS = "soltos"


class BancoPastas:

    # ------------------------------------------------------------------ escrita
    def criar(self, nome, id_pai=None) -> int:
        nome = (nome or "").strip()[:60] or "Nova pasta"
        with conexao() as conn:
            cursor = conn.execute(
                "INSERT INTO pastas (nome, id_pai, criado_em) VALUES (?, ?, ?)",
                (nome, id_pai, time.strftime("%Y-%m-%d %H:%M:%S")))
            return cursor.lastrowid

    def renomear(self, id_pasta, nome):
        nome = (nome or "").strip()[:60]
        if not nome:
            return
        with conexao() as conn:
            conn.execute("UPDATE pastas SET nome = ? WHERE id_pasta = ?", (nome, id_pasta))

    def mover(self, id_pasta, novo_pai) -> bool:
        """Reparenta uma pasta. Recusa criar ciclo (mover para dentro de si)."""
        if novo_pai is not None and int(novo_pai) == int(id_pasta):
            return False
        if novo_pai is not None and int(novo_pai) in self.descendentes(id_pasta):
            return False
        with conexao() as conn:
            conn.execute("UPDATE pastas SET id_pai = ? WHERE id_pasta = ?",
                         (novo_pai, id_pasta))
        return True

    def excluir(self, id_pasta):
        """Remove a pasta e as subpastas. Os livros voltam a ficar soltos --
        nunca sao apagados junto."""
        alvos = {int(id_pasta)} | self.descendentes(id_pasta)
        marcadores = ",".join("?" * len(alvos))
        with conexao() as conn:
            conn.execute(f"UPDATE livros SET id_pasta = NULL WHERE id_pasta IN ({marcadores})",
                         tuple(alvos))
            conn.execute(f"DELETE FROM pastas WHERE id_pasta IN ({marcadores})", tuple(alvos))

    def definir_pasta_do_livro(self, id_livro, id_pasta):
        with conexao() as conn:
            conn.execute("UPDATE livros SET id_pasta = ? WHERE id_livro = ?",
                         (id_pasta, id_livro))

    # ------------------------------------------------------------------ leitura
    def listar_todas(self):
        with conexao() as conn:
            linhas = conn.execute("""
                SELECT p.id_pasta, p.nome, p.id_pai,
                       (SELECT COUNT(*) FROM livros l WHERE l.id_pasta = p.id_pasta) AS n_livros,
                       (SELECT COUNT(*) FROM pastas f WHERE f.id_pai = p.id_pasta) AS n_filhas
                  FROM pastas p
              ORDER BY p.nome COLLATE NOCASE
            """).fetchall()
        return [dict(linha) for linha in linhas]

    def obter(self, id_pasta):
        with conexao() as conn:
            linha = conn.execute("SELECT * FROM pastas WHERE id_pasta = ?",
                                 (id_pasta,)).fetchone()
        return dict(linha) if linha else None

    def filhas(self, id_pai):
        todas = self.listar_todas()
        return [p for p in todas if p["id_pai"] == id_pai]

    def descendentes(self, id_pasta) -> set:
        """Todos os ids abaixo desta pasta, em qualquer profundidade."""
        with conexao() as conn:
            linhas = conn.execute("""
                WITH RECURSIVE sub(id) AS (
                    SELECT id_pasta FROM pastas WHERE id_pai = ?
                    UNION ALL
                    SELECT p.id_pasta FROM pastas p JOIN sub ON p.id_pai = sub.id
                )
                SELECT id FROM sub
            """, (id_pasta,)).fetchall()
        return {linha["id"] for linha in linhas}

    def caminho(self, id_pasta):
        """Trilha da raiz ate a pasta, para a barra de navegacao."""
        with conexao() as conn:
            linhas = conn.execute("""
                WITH RECURSIVE trilha(id_pasta, nome, id_pai, nivel) AS (
                    SELECT id_pasta, nome, id_pai, 0 FROM pastas WHERE id_pasta = ?
                    UNION ALL
                    SELECT p.id_pasta, p.nome, p.id_pai, t.nivel + 1
                      FROM pastas p JOIN trilha t ON p.id_pasta = t.id_pai
                )
                SELECT id_pasta, nome FROM trilha ORDER BY nivel DESC
            """, (id_pasta,)).fetchall()
        return [(linha["id_pasta"], linha["nome"]) for linha in linhas]

    def contar_livros(self, id_pasta, recursivo=True) -> int:
        alvos = {int(id_pasta)}
        if recursivo:
            alvos |= self.descendentes(id_pasta)
        marcadores = ",".join("?" * len(alvos))
        with conexao() as conn:
            linha = conn.execute(
                f"SELECT COUNT(*) AS n FROM livros WHERE id_pasta IN ({marcadores})",
                tuple(alvos)).fetchone()
        return linha["n"] if linha else 0

    def contar_soltos(self) -> int:
        with conexao() as conn:
            linha = conn.execute(
                "SELECT COUNT(*) AS n FROM livros WHERE id_pasta IS NULL").fetchone()
        return linha["n"] if linha else 0

    # ------------------------------------------------------------------ arvore
    def arvore(self):
        """[(pasta, profundidade), ...] em ordem de exibicao, ja aninhada."""
        todas = self.listar_todas()
        por_pai = {}
        for pasta in todas:
            por_pai.setdefault(pasta["id_pai"], []).append(pasta)

        saida = []

        def descer(id_pai, profundidade):
            for pasta in por_pai.get(id_pai, []):
                saida.append((pasta, profundidade))
                descer(pasta["id_pasta"], profundidade + 1)

        descer(None, 0)
        return saida
