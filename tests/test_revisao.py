"""Testes da revisao espacada (SM-2), do diario de leitura e da migracao.

Cada teste usa um banco novo numa pasta temporaria: nunca toca a biblioteca
real do usuario.

Rodar:  python -m unittest discover -s tests -v
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import sqlite_db  # noqa: E402
from database.biblioteca import BancoBiblioteca  # noqa: E402
from services import revisao  # noqa: E402

HOJE = dt.date(2026, 11, 16)


def _novo_banco():
    """Aponta a conexao da thread para um arquivo novo."""
    pasta = tempfile.mkdtemp(prefix="lumina_rev_")
    sqlite_db.fechar_conexao()
    sqlite_db.ARQUIVO_BANCO = os.path.join(pasta, "teste.db")
    # A migracao do JSON da v1 procura arquivos relativos ao diretorio atual.
    sqlite_db.ARQUIVO_BIBLIOTECA = os.path.join(pasta, "nao_existe.json")
    sqlite_db.ARQUIVO_CONFIG = os.path.join(pasta, "nao_existe_cfg.json")
    return sqlite_db.ARQUIVO_BANCO


def _tabelas(caminho):
    conn = sqlite3.connect(caminho)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


class BaseBanco(unittest.TestCase):
    def setUp(self):
        self.caminho = _novo_banco()
        sqlite_db.inicializar_banco()
        self.banco = BancoBiblioteca()
        self.banco.adicionar_livro("L1", "c:/x.pdf", "", "Livro Um", "Autor", 100)

    def tearDown(self):
        sqlite_db.fechar_conexao()

    def _grifo(self, trecho="texto grifado", pagina=3, inicio=0):
        self.banco.adicionar_grifo("L1", pagina, inicio, inicio + len(trecho), trecho)
        with sqlite_db.conexao() as conn:
            return conn.execute("SELECT MAX(id_grifo) AS m FROM grifos").fetchone()["m"]


class TestSM2(unittest.TestCase):
    def test_sequencia_perfeita(self):
        ef, i, r = revisao.calcular_sm2(2.5, 0, 0, 5)
        self.assertEqual((i, r), (1, 1))
        self.assertAlmostEqual(ef, 2.6)
        ef, i, r = revisao.calcular_sm2(ef, i, r, 5)
        self.assertEqual((i, r), (6, 2))
        self.assertAlmostEqual(ef, 2.7)
        ef, i, r = revisao.calcular_sm2(ef, i, r, 5)
        self.assertEqual(i, round(6 * 2.7))      # usa o EF anterior: 16
        self.assertEqual(r, 3)

    def test_nota_4_mantem_facilidade(self):
        ef, _, _ = revisao.calcular_sm2(2.5, 0, 0, 4)
        self.assertAlmostEqual(ef, 2.5)

    def test_nota_3_reduz_facilidade(self):
        ef, i, r = revisao.calcular_sm2(2.5, 6, 2, 3)
        self.assertAlmostEqual(ef, 2.36)
        self.assertEqual((i, r), (15, 3))

    def test_falha_recomeca_sem_mudar_facilidade(self):
        ef, i, r = revisao.calcular_sm2(2.2, 30, 5, 2)
        self.assertEqual((ef, i, r), (2.2, 1, 0))

    def test_piso_da_facilidade(self):
        ef = 1.35
        for _ in range(5):
            ef, _, _ = revisao.calcular_sm2(ef, 6, 2, 3)
        self.assertEqual(ef, revisao.FACILIDADE_MINIMA)

    def test_nota_invalida(self):
        with self.assertRaises(ValueError):
            revisao.calcular_sm2(2.5, 0, 0, 6)
        with self.assertRaises(ValueError):
            revisao.calcular_sm2(2.5, 0, 0, -1)


class TestSequencias(unittest.TestCase):
    def d(self, *dias):
        return [HOJE - dt.timedelta(days=n) for n in dias]

    def test_sem_leitura(self):
        self.assertEqual(revisao.calcular_sequencias([], HOJE), (0, 0))

    def test_ate_hoje(self):
        self.assertEqual(revisao.calcular_sequencias(self.d(0, 1, 2), HOJE), (3, 3))

    def test_hoje_ainda_nao_leu_mantem(self):
        self.assertEqual(revisao.calcular_sequencias(self.d(1, 2), HOJE), (2, 2))

    def test_buraco_quebra(self):
        # leu hoje e ontem; falhou anteontem; antes disso 4 dias seguidos
        self.assertEqual(revisao.calcular_sequencias(self.d(0, 1, 3, 4, 5, 6), HOJE), (2, 4))

    def test_dois_dias_sem_ler_zera(self):
        self.assertEqual(revisao.calcular_sequencias(self.d(2, 3), HOJE), (0, 2))

    def test_virada_de_mes(self):
        hoje = dt.date(2026, 3, 1)
        datas = ["2026-02-27", "2026-02-28", "2026-03-01"]
        self.assertEqual(revisao.calcular_sequencias(datas, hoje), (3, 3))


class TestMigracao(unittest.TestCase):
    def tearDown(self):
        sqlite_db.fechar_conexao()

    def test_banco_vazio(self):
        caminho = _novo_banco()
        sqlite_db.inicializar_banco()
        tabelas = _tabelas(caminho)
        self.assertIn("cartoes_revisao", tabelas)
        self.assertIn("leitura_diaria", tabelas)
        sqlite_db.inicializar_banco()           # idempotente

    def test_banco_do_esquema_atual_sem_tabelas_novas(self):
        caminho = _novo_banco()
        conn = sqlite3.connect(caminho)
        for comando in sqlite_db._ESQUEMA:
            if "cartoes_revisao" in comando or "leitura_diaria" in comando:
                continue
            conn.execute(comando)
        conn.execute("INSERT INTO livros (id_livro, titulo, segundos_lidos) VALUES ('A', 'T', 50)")
        conn.execute("INSERT INTO grifos (id_livro, pagina, inicio_char, fim_char, trecho) "
                     "VALUES ('A', 1, 0, 5, 'ola')")
        conn.commit()
        conn.close()
        self.assertNotIn("cartoes_revisao", _tabelas(caminho))

        sqlite_db.inicializar_banco()
        self.assertIn("cartoes_revisao", _tabelas(caminho))
        self.assertEqual(BancoBiblioteca().obter_livro("A")["segundos_lidos"], 50)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 1)

    def test_banco_da_v1(self):
        """Esquema da primeira versao (grifos sem trecho, livros sem titulo)."""
        caminho = _novo_banco()
        conn = sqlite3.connect(caminho)
        conn.executescript("""
            CREATE TABLE configuracoes (chave TEXT PRIMARY KEY, valor TEXT);
            CREATE TABLE livros (id_livro TEXT PRIMARY KEY, caminho_pdf TEXT,
                caminho_capa TEXT, pagina_atual INTEGER DEFAULT 0,
                zoom INTEGER DEFAULT 16, tema TEXT DEFAULT 'Dark',
                tempo_lido_segundos INTEGER DEFAULT 0,
                data_ultimo_acesso TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE grifos (id_grifo INTEGER PRIMARY KEY AUTOINCREMENT,
                id_livro TEXT, pagina INTEGER, inicio TEXT, fim TEXT,
                FOREIGN KEY (id_livro) REFERENCES livros (id_livro) ON DELETE CASCADE);
            INSERT INTO livros (id_livro, caminho_pdf) VALUES ('V', 'c:/v.pdf');
            INSERT INTO grifos (id_livro, pagina, inicio, fim) VALUES ('V', 2, '1.0', '1.5');
        """)
        conn.commit()
        conn.close()
        sqlite_db.inicializar_banco()
        # Grifo da v1 nao tem trecho: nao vira cartao, mas nada quebra.
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 0)
        banco = BancoBiblioteca()
        banco.somar_tempo_leitura("V", 120)
        banco.definir_pagina("V", 1)
        est = revisao.estatisticas(7)
        self.assertEqual(est.total_segundos, 120)
        self.assertEqual(est.total_paginas, 1)


class TestCartoes(BaseBanco):
    def test_criar_de_grifo_e_idempotente(self):
        g = self._grifo()
        c1 = revisao.criar_cartao_de_grifo(g, hoje=HOJE)
        c2 = revisao.criar_cartao_de_grifo(g, hoje=HOJE)
        self.assertEqual(c1, c2)
        cartao = revisao.obter_cartao(c1)
        self.assertEqual(cartao.frente, "texto grifado")
        self.assertEqual(cartao.titulo_livro, "Livro Um")
        self.assertEqual(cartao.pagina, 3)
        self.assertTrue(cartao.novo)

    def test_verso_usa_nota_da_pagina(self):
        g = self._grifo()
        self.banco.salvar_nota("L1", 3, "minha explicacao")
        cartao = revisao.obter_cartao(revisao.criar_cartao_de_grifo(g, hoje=HOJE))
        self.assertEqual(cartao.verso, "minha explicacao")
        revisao.editar_cartao(cartao.id_cartao, verso="outro verso")
        self.assertEqual(revisao.obter_cartao(cartao.id_cartao).verso, "outro verso")

    def test_grifo_inexistente(self):
        self.assertIsNone(revisao.criar_cartao_de_grifo(9999))

    def test_apagar_grifo_apaga_cartao(self):
        g = self._grifo()
        c = revisao.criar_cartao_de_grifo(g, hoje=HOJE)
        self.banco.remover_grifos_no_intervalo("L1", 3, 0, 100)
        self.assertIsNone(revisao.obter_cartao(c))

    def test_cartao_livre(self):
        c = revisao.criar_cartao_livre("O que e SM-2?", "Algoritmo de repeticao", "L1", hoje=HOJE)
        cartao = revisao.obter_cartao(c)
        self.assertEqual((cartao.frente, cartao.verso), ("O que e SM-2?", "Algoritmo de repeticao"))
        self.assertIsNone(cartao.id_grifo)
        with self.assertRaises(ValueError):
            revisao.criar_cartao_livre("   ")

    def test_fila_de_hoje_e_reagendamento(self):
        a = revisao.criar_cartao_livre("A", hoje=HOJE)
        b = revisao.criar_cartao_livre("B", hoje=HOJE)
        self.assertEqual([c.id_cartao for c in revisao.cartoes_para_hoje(hoje=HOJE)], [a, b])
        self.assertEqual(revisao.contar_pendentes(hoje=HOJE), 2)

        cartao = revisao.registrar_resposta(a, 5, hoje=HOJE)
        self.assertEqual(cartao.proxima_revisao, "2026-11-17")
        self.assertEqual(cartao.ultima_revisao, "2026-11-16")
        self.assertFalse(cartao.novo)
        self.assertEqual([c.id_cartao for c in revisao.cartoes_para_hoje(hoje=HOJE)], [b])

        amanha = HOJE + dt.timedelta(days=1)
        self.assertIn(a, [c.id_cartao for c in revisao.cartoes_para_hoje(hoje=amanha)])
        cartao = revisao.registrar_resposta(a, 4, hoje=amanha)
        self.assertEqual(cartao.intervalo_dias, 6)
        self.assertEqual(cartao.proxima_revisao, "2026-11-23")

    def test_atrasados_primeiro_e_limite(self):
        ids = [revisao.criar_cartao_livre(f"C{i}", hoje=HOJE) for i in range(5)]
        revisao.registrar_resposta(ids[4], 5, hoje=HOJE - dt.timedelta(days=10))  # venceu ha 9 dias
        fila = revisao.cartoes_para_hoje(limite=2, hoje=HOJE)
        self.assertEqual(len(fila), 2)
        self.assertEqual(fila[0].id_cartao, ids[4])

    def test_filtro_por_livro(self):
        self.banco.adicionar_livro("L2", "c:/y.pdf", "", "Livro Dois", "", 10)
        revisao.criar_cartao_livre("de L1", id_livro="L1", hoje=HOJE)
        revisao.criar_cartao_livre("de L2", id_livro="L2", hoje=HOJE)
        self.assertEqual([c.frente for c in revisao.cartoes_para_hoje(id_livro="L2", hoje=HOJE)],
                         ["de L2"])

    def test_resposta_invalida(self):
        with self.assertRaises(ValueError):
            revisao.registrar_resposta(12345, 3)
        c = revisao.criar_cartao_livre("x", hoje=HOJE)
        with self.assertRaises(ValueError):
            revisao.registrar_resposta(c, 7)


class TestSincronizacao(BaseBanco):
    def test_cria_para_grifos_novos_uma_vez(self):
        self._grifo("um")
        self._grifo("dois", inicio=10)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 2)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 0)
        self._grifo("tres", inicio=20)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 1)

    def test_excluido_nao_volta(self):
        self._grifo("um")
        revisao.sincronizar_grifos(hoje=HOJE)
        cartao = revisao.cartoes_para_hoje(hoje=HOJE)[0]
        revisao.excluir_cartao(cartao.id_cartao)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 0)
        self.assertEqual(revisao.cartoes_para_hoje(hoje=HOJE), [])

    def test_desligado(self):
        self.assertTrue(revisao.auto_grifos_ativo())
        revisao.definir_auto_grifos(False)
        self.assertFalse(revisao.auto_grifos_ativo())
        self._grifo("um")
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 0)
        revisao.definir_auto_grifos(True)
        # O periodo desligado nao e despejado de uma vez ao religar.
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 0)
        self._grifo("dois", inicio=10)
        self.assertEqual(revisao.sincronizar_grifos(hoje=HOJE), 1)

    def test_forcar(self):
        revisao.definir_auto_grifos(False)
        self._grifo("um")
        self.assertEqual(revisao.sincronizar_grifos(forcar=True, hoje=HOJE), 1)


class TestEstatisticas(BaseBanco):
    def test_tempo_e_paginas_pelo_fluxo_normal(self):
        self.banco.definir_pagina("L1", 5)      # pulo do sumario: nao conta
        self.banco.definir_pagina("L1", 6)      # +1
        self.banco.definir_pagina("L1", 8)      # +2
        self.banco.definir_pagina("L1", 4)      # voltou: nao conta
        self.banco.somar_tempo_leitura("L1", 600)
        est = revisao.estatisticas(30)
        self.assertEqual(est.total_paginas, 3)
        self.assertEqual(est.total_segundos, 600)
        self.assertEqual(est.dias[-1].data, dt.date.today().isoformat())
        self.assertEqual(est.dias[-1].segundos, 600)
        self.assertEqual(est.sequencia_atual, 1)
        self.assertEqual(est.paginas_por_hora, 18.0)
        self.assertEqual(self.banco.obter_livro("L1")["segundos_lidos"], 600)

    def test_janela_sequencia_e_por_livro(self):
        self.banco.adicionar_livro("L2", "c:/y.pdf", "", "Livro Dois", "", 10)
        reg = self.banco.registrar_leitura_diaria
        dia = lambda n: (HOJE - dt.timedelta(days=n)).isoformat()  # noqa: E731
        reg("L1", 1800, 20, dia(0))
        reg("L2", 600, 5, dia(1))
        reg("L1", 30, 0, dia(2))        # 30 s e nenhuma pagina: nao conta
        reg("L1", 900, 10, dia(3))
        reg("L1", 900, 10, dia(4))
        reg("L1", 900, 10, dia(5))
        reg("L1", 3600, 50, dia(40))    # fora da janela
        reg("REMOVIDO", 300, 3, dia(1))  # livro que saiu da estante
        est = revisao.estatisticas(7, hoje=HOJE)
        self.assertEqual(len(est.dias), 7)
        self.assertEqual(est.dias[0].data, dia(6))
        self.assertEqual(est.total_segundos, 1800 + 600 + 30 + 2700 + 300)
        self.assertEqual(est.total_paginas, 20 + 5 + 30 + 3)
        self.assertEqual(est.dias_lidos, 5)
        self.assertEqual(est.sequencia_atual, 2)
        self.assertEqual(est.maior_sequencia, 3)
        self.assertEqual([l.titulo for l in est.por_livro], ["Livro Um", "Livro Dois", "REMOVIDO"])
        self.assertEqual(est.por_livro[0].segundos, 1800 + 30 + 2700)

    def test_sem_dados(self):
        est = revisao.estatisticas(10, hoje=HOJE)
        self.assertEqual(est.total_segundos, 0)
        self.assertIsNone(est.paginas_por_hora)
        self.assertEqual(est.sequencia_atual, 0)
        self.assertEqual(len(est.dias), 10)

    def test_pendentes_no_resumo(self):
        revisao.criar_cartao_livre("x", hoje=HOJE)
        self.assertEqual(revisao.estatisticas(7, hoje=HOJE).cartoes_pendentes, 1)

    def test_remover_livro_mantem_historico(self):
        self.banco.somar_tempo_leitura("L1", 120)
        revisao.criar_cartao_livre("x", id_livro="L1", hoje=HOJE)
        self.banco.remover_livro("L1", apagar_capa=False)
        self.assertEqual(revisao.estatisticas(3).total_segundos, 120)
        self.assertEqual(revisao.contar_pendentes(hoje=HOJE), 0)


if __name__ == "__main__":
    unittest.main()
