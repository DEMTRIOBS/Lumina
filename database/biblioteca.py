# database/biblioteca.py
"""Operacoes da biblioteca.

A versao anterior usava um unico `atualizar_livro(chave, valor)` que montava SQL
por interpolacao de string e reescrevia TODOS os grifos do livro a cada clique.
Aqui cada operacao e granular, parametrizada e toca so a linha necessaria.
"""
import os
import time

from database.sqlite_db import conexao


class BancoBiblioteca:

    # ------------------------------------------------------------------ leitura
    def obter_estante(self, ids_pasta=None, soltos=False):
        """Lista para a tela inicial, com contadores agregados em uma consulta.

        ids_pasta: None mostra tudo; uma colecao de ids restringe as pastas.
        soltos:    True devolve apenas os livros que nao estao em pasta nenhuma.
        """
        onde, parametros = "", ()
        if soltos:
            onde = "WHERE l.id_pasta IS NULL"
        elif ids_pasta is not None:
            ids = tuple(int(i) for i in ids_pasta)
            if not ids:
                return []
            onde = f"WHERE l.id_pasta IN ({','.join('?' * len(ids))})"
            parametros = ids

        with conexao() as conn:
            linhas = conn.execute(f"""
                SELECT l.*,
                       (SELECT COUNT(*) FROM favoritos f WHERE f.id_livro = l.id_livro) AS n_favoritos,
                       (SELECT COUNT(*) FROM anotacoes a WHERE a.id_livro = l.id_livro) AS n_notas,
                       (SELECT COUNT(*) FROM grifos g
                         WHERE g.id_livro = l.id_livro AND g.inicio_char IS NOT NULL) AS n_grifos
                  FROM livros l
                  {onde}
              ORDER BY l.fixado DESC, l.data_ultimo_acesso DESC
            """, parametros).fetchall()
        return [dict(linha) for linha in linhas]

    def obter_livro(self, id_livro):
        with conexao() as conn:
            linha = conn.execute(
                "SELECT * FROM livros WHERE id_livro = ?", (id_livro,)).fetchone()
        return dict(linha) if linha else None

    def existe(self, id_livro) -> bool:
        return self.obter_livro(id_livro) is not None

    # ------------------------------------------------------------------ escrita
    def adicionar_livro(self, id_livro, caminho, capa, titulo, autor, total_paginas,
                        id_pasta=None):
        agora = time.strftime("%Y-%m-%d %H:%M:%S")
        with conexao() as conn:
            conn.execute("""
                INSERT INTO livros (id_livro, caminho_pdf, caminho_capa, titulo, autor,
                                    total_paginas, pagina_atual, id_pasta,
                                    data_adicao, data_ultimo_acesso)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(id_livro) DO UPDATE SET
                    caminho_pdf  = excluded.caminho_pdf,
                    caminho_capa = COALESCE(NULLIF(excluded.caminho_capa, ''), livros.caminho_capa),
                    total_paginas = excluded.total_paginas
            """, (id_livro, caminho, capa or "", titulo, autor, total_paginas,
                  id_pasta, agora, agora))

    def remover_livro(self, id_livro, apagar_capa=True):
        livro = self.obter_livro(id_livro)
        with conexao() as conn:
            # Explicito: bancos vindos da v1 nao tem ON DELETE CASCADE nessas
            # tabelas, e re-adicionar o mesmo PDF ressuscitava notas antigas.
            for tabela in ("grifos", "anotacoes", "favoritos"):
                conn.execute(f"DELETE FROM {tabela} WHERE id_livro = ?", (id_livro,))
            conn.execute("DELETE FROM livros WHERE id_livro = ?", (id_livro,))
        if apagar_capa and livro and livro.get("caminho_capa"):
            try:
                os.remove(livro["caminho_capa"])
            except OSError:
                pass

    def definir_pagina(self, id_livro, pagina):
        with conexao() as conn:
            conn.execute(
                # rolagem zera: o offset salvo era da pagina anterior.
                "UPDATE livros SET pagina_atual = ?, rolagem = 0, "
                "data_ultimo_acesso = ? WHERE id_livro = ?",
                (int(pagina), time.strftime("%Y-%m-%d %H:%M:%S"), id_livro))

    def definir_rolagem(self, id_livro, offset):
        """Offset (em caracteres) do topo visivel da pagina atual."""
        with conexao() as conn:
            conn.execute("UPDATE livros SET rolagem = ? WHERE id_livro = ?",
                         (max(0, int(offset or 0)), id_livro))

    def listar_grifos_para_exportar(self, id_livro):
        """(pagina, trecho, criado_em) de todos os grifos, em ordem de leitura."""
        with conexao() as conn:
            linhas = conn.execute("""
                SELECT pagina, trecho, criado_em FROM grifos
                 WHERE id_livro = ? AND inicio_char IS NOT NULL AND trecho <> ''
              ORDER BY pagina, inicio_char
            """, (id_livro,)).fetchall()
        return [(l["pagina"], l["trecho"], l["criado_em"] or "") for l in linhas]

    def atualizar_metadados(self, id_livro, titulo, autor, total_paginas, capa=None):
        with conexao() as conn:
            conn.execute("""
                UPDATE livros
                   SET titulo = ?, autor = ?, total_paginas = ?,
                       caminho_capa = COALESCE(NULLIF(?, ''), caminho_capa)
                 WHERE id_livro = ?
            """, (titulo, autor, int(total_paginas or 0), capa or "", id_livro))

    def definir_titulo(self, id_livro, titulo, autor=None):
        """Titulo escolhido pelo usuario.

        Necessario porque muito PDF nao traz metadado utilizavel e o nome do
        arquivo pode ser algo como "00403.pdf".
        """
        titulo = (titulo or "").strip()[:120]
        if not titulo:
            return
        with conexao() as conn:
            if autor is None:
                conn.execute("UPDATE livros SET titulo = ? WHERE id_livro = ?",
                             (titulo, id_livro))
            else:
                conn.execute("UPDATE livros SET titulo = ?, autor = ? WHERE id_livro = ?",
                             (titulo, autor.strip()[:80], id_livro))

    # ------------------------------------------------- propriedades do livro
    # Um leitor de PDF generico recebe livros muito diferentes -- programacao,
    # matematica, literatura, contos. Cada um pode guardar as suas
    # preferencias; nulo significa "usar o padrao global".
    CHAVES_PREF = {"pref_fonte", "pref_tamanho", "pref_altura", "pref_largura",
                   "perfil", "render", "categoria"}

    def definir_preferencia(self, id_livro, chave, valor):
        if chave not in self.CHAVES_PREF:
            raise ValueError(f"preferencia desconhecida: {chave}")
        with conexao() as conn:
            conn.execute(f"UPDATE livros SET {chave} = ? WHERE id_livro = ?",
                         (valor, id_livro))

    def limpar_preferencias(self, id_livro):
        """Volta o livro a seguir a tipografia global."""
        with conexao() as conn:
            conn.execute("""UPDATE livros
                               SET pref_fonte = NULL, pref_tamanho = NULL,
                                   pref_altura = NULL, pref_largura = NULL
                             WHERE id_livro = ?""", (id_livro,))

    def obter_preferencias(self, id_livro):
        with conexao() as conn:
            linha = conn.execute(
                """SELECT perfil, render, categoria, pref_fonte, pref_tamanho,
                          pref_altura, pref_largura
                     FROM livros WHERE id_livro = ?""", (id_livro,)).fetchone()
        return dict(linha) if linha else {}

    def definir_caminho(self, id_livro, caminho):
        with conexao() as conn:
            conn.execute("UPDATE livros SET caminho_pdf = ? WHERE id_livro = ?",
                         (caminho, id_livro))

    def definir_total_paginas(self, id_livro, total):
        with conexao() as conn:
            conn.execute("UPDATE livros SET total_paginas = ? WHERE id_livro = ?",
                         (int(total), id_livro))

    def alternar_fixado(self, id_livro) -> bool:
        with conexao() as conn:
            atual = conn.execute("SELECT fixado FROM livros WHERE id_livro = ?",
                                 (id_livro,)).fetchone()
            novo = 0 if (atual and atual["fixado"]) else 1
            conn.execute("UPDATE livros SET fixado = ? WHERE id_livro = ?", (novo, id_livro))
        return bool(novo)

    def somar_tempo_leitura(self, id_livro, segundos):
        if segundos <= 0:
            return
        with conexao() as conn:
            conn.execute(
                "UPDATE livros SET segundos_lidos = COALESCE(segundos_lidos, 0) + ? "
                "WHERE id_livro = ?", (int(segundos), id_livro))

    # ---------------------------------------------------------------- favoritos
    def categorias_em_uso(self):
        """Categorias que realmente aparecem na estante, com a contagem."""
        with conexao() as conn:
            linhas = conn.execute("""
                SELECT COALESCE(NULLIF(TRIM(categoria), ''), '') AS cat,
                       COUNT(*) AS n
                  FROM livros GROUP BY cat ORDER BY n DESC
            """).fetchall()
        return [(linha["cat"], linha["n"]) for linha in linhas]

    def listar_favoritos(self, id_livro):
        with conexao() as conn:
            linhas = conn.execute(
                "SELECT pagina, rotulo FROM favoritos WHERE id_livro = ? ORDER BY pagina",
                (id_livro,)).fetchall()
        return [(linha["pagina"], linha["rotulo"] or "") for linha in linhas]

    def estado_da_pagina(self, id_livro, pagina):
        """(tem_marcador, tem_nota) numa consulta so.

        A barra superior precisa dos dois a cada virada de pagina; separados,
        eram duas idas ao banco por pagina.
        """
        with conexao() as conn:
            linha = conn.execute("""
                SELECT EXISTS(SELECT 1 FROM favoritos
                               WHERE id_livro = ? AND pagina = ?) AS marcado,
                       EXISTS(SELECT 1 FROM anotacoes
                               WHERE id_livro = ? AND pagina = ?
                                 AND TRIM(COALESCE(nota, '')) <> '') AS anotado
            """, (id_livro, int(pagina), id_livro, int(pagina))).fetchone()
        return bool(linha["marcado"]), bool(linha["anotado"])

    def eh_favorito(self, id_livro, pagina) -> bool:
        with conexao() as conn:
            linha = conn.execute(
                "SELECT 1 FROM favoritos WHERE id_livro = ? AND pagina = ?",
                (id_livro, int(pagina))).fetchone()
        return linha is not None

    def alternar_favorito(self, id_livro, pagina, rotulo="") -> bool:
        pagina = int(pagina)
        with conexao() as conn:
            existe = conn.execute(
                "SELECT 1 FROM favoritos WHERE id_livro = ? AND pagina = ?",
                (id_livro, pagina)).fetchone()
            if existe:
                conn.execute("DELETE FROM favoritos WHERE id_livro = ? AND pagina = ?",
                             (id_livro, pagina))
                return False
            conn.execute(
                "INSERT INTO favoritos (id_livro, pagina, rotulo, criado_em) VALUES (?, ?, ?, ?)",
                (id_livro, pagina, rotulo, time.strftime("%Y-%m-%d %H:%M:%S")))
            return True

    def remover_favorito(self, id_livro, pagina):
        with conexao() as conn:
            conn.execute("DELETE FROM favoritos WHERE id_livro = ? AND pagina = ?",
                         (id_livro, int(pagina)))

    # ---------------------------------------------------------------- anotacoes
    def obter_nota(self, id_livro, pagina) -> str:
        with conexao() as conn:
            linha = conn.execute(
                "SELECT nota FROM anotacoes WHERE id_livro = ? AND pagina = ?",
                (id_livro, int(pagina))).fetchone()
        return linha["nota"] if linha else ""

    def salvar_nota(self, id_livro, pagina, texto):
        pagina = int(pagina)
        agora = time.strftime("%Y-%m-%d %H:%M:%S")
        with conexao() as conn:
            if texto and texto.strip():
                conn.execute("""
                    INSERT INTO anotacoes (id_livro, pagina, nota, atualizado_em)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id_livro, pagina) DO UPDATE SET
                        nota = excluded.nota, atualizado_em = excluded.atualizado_em
                """, (id_livro, pagina, texto.strip(), agora))
            else:
                conn.execute("DELETE FROM anotacoes WHERE id_livro = ? AND pagina = ?",
                             (id_livro, pagina))

    def listar_notas(self, id_livro):
        with conexao() as conn:
            linhas = conn.execute(
                "SELECT pagina, nota FROM anotacoes WHERE id_livro = ? ORDER BY pagina",
                (id_livro,)).fetchall()
        return [(linha["pagina"], linha["nota"]) for linha in linhas]

    def paginas_com_nota(self, id_livro):
        return {pagina for pagina, _ in self.listar_notas(id_livro)}

    # ------------------------------------------------------------------- grifos
    def listar_grifos(self, id_livro, pagina):
        """Offsets de caracteres dentro do texto refluido da pagina.

        Guardar indices do widget Tk (como na v1) quebrava o grifo assim que a
        fonte ou a largura da janela mudava; offset de caractere e estavel.
        """
        with conexao() as conn:
            linhas = conn.execute("""
                SELECT id_grifo, inicio_char, fim_char, trecho FROM grifos
                 WHERE id_livro = ? AND pagina = ? AND inicio_char IS NOT NULL
              ORDER BY inicio_char
            """, (id_livro, int(pagina))).fetchall()
        return [dict(linha) for linha in linhas]

    def adicionar_grifo(self, id_livro, pagina, inicio_char, fim_char, trecho=""):
        with conexao() as conn:
            conn.execute("""
                INSERT INTO grifos (id_livro, pagina, inicio_char, fim_char, trecho, criado_em)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (id_livro, int(pagina), int(inicio_char), int(fim_char),
                  trecho[:400], time.strftime("%Y-%m-%d %H:%M:%S")))

    def remover_grifos_no_intervalo(self, id_livro, pagina, inicio_char, fim_char) -> int:
        """Remove todo grifo que se sobreponha ao intervalo selecionado."""
        with conexao() as conn:
            cursor = conn.execute("""
                DELETE FROM grifos
                 WHERE id_livro = ? AND pagina = ?
                   AND inicio_char IS NOT NULL
                   AND inicio_char < ? AND fim_char > ?
            """, (id_livro, int(pagina), int(fim_char), int(inicio_char)))
            return cursor.rowcount

    def listar_todos_grifos(self, id_livro):
        with conexao() as conn:
            linhas = conn.execute("""
                SELECT pagina, trecho FROM grifos
                 WHERE id_livro = ? AND inicio_char IS NOT NULL AND trecho <> ''
              ORDER BY pagina, inicio_char
            """, (id_livro,)).fetchall()
        return [(linha["pagina"], linha["trecho"]) for linha in linhas]

    def paginas_com_grifo(self, id_livro):
        with conexao() as conn:
            linhas = conn.execute(
                "SELECT DISTINCT pagina FROM grifos WHERE id_livro = ? "
                "AND inicio_char IS NOT NULL", (id_livro,)).fetchall()
        return {linha["pagina"] for linha in linhas}
