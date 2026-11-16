"""Testes do services/vscode.py com um %APPDATA% falso.

Monta storage.json, workspaceStorage/*/workspace.json e state.vscdb como o
VS Code grava, numa pasta temporaria -- o estado real do editor nunca e lido.

Rodar:  python -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="lumina_testes_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import vscode  # noqa: E402
from services.vscode import interpretar_titulo  # noqa: E402


def _uri(caminho):
    return Path(caminho).resolve().as_uri()


class TestTitulo(unittest.TestCase):
    def test_padrao(self):
        t = interpretar_titulo("views.py - meu_projeto - Visual Studio Code")
        self.assertEqual((t.arquivo, t.raiz, t.app, t.sujo, t.remoto),
                         ("views.py", "meu_projeto", "Code", False, False))

    def test_sujo(self):
        t = interpretar_titulo("● views.py - meu_projeto - Visual Studio Code")
        self.assertTrue(t.sujo)
        self.assertEqual(t.arquivo, "views.py")

    def test_remoto(self):
        t = interpretar_titulo("x.php - www [SSH: host.com] - Visual Studio Code")
        self.assertTrue(t.remoto)
        self.assertEqual((t.arquivo, t.raiz), ("x.php", "www"))
        t = interpretar_titulo("a.py - proj [WSL: Ubuntu] - Visual Studio Code")
        self.assertTrue(t.remoto)

    def test_insiders_e_cursor(self):
        t = interpretar_titulo("a.ts - web - Visual Studio Code - Insiders")
        self.assertEqual((t.arquivo, t.raiz, t.app), ("a.ts", "web", "Code - Insiders"))
        t = interpretar_titulo("a.ts - web - Cursor")
        self.assertEqual(t.app, "Cursor")

    def test_perfil(self):
        t = interpretar_titulo("a.py - proj - Python - Visual Studio Code")
        self.assertEqual((t.arquivo, t.raiz), ("a.py", "proj"))

    def test_nome_com_separador_usa_raiz_conhecida(self):
        t = interpretar_titulo("notas - v2.md - proj - Visual Studio Code", ["proj"])
        self.assertEqual((t.arquivo, t.raiz), ("notas - v2.md", "proj"))

    def test_workspace(self):
        t = interpretar_titulo("a.py - estudos (Workspace) - Visual Studio Code")
        self.assertEqual(t.raiz, "estudos")
        self.assertTrue(t.workspace)

    def test_sem_editor(self):
        t = interpretar_titulo("meu_projeto - Visual Studio Code")
        self.assertIsNone(t.arquivo)
        self.assertEqual(t.raiz, "meu_projeto")
        self.assertIsNone(interpretar_titulo("Welcome - proj - Visual Studio Code").arquivo)
        self.assertIsNone(interpretar_titulo("Untitled-1 - proj - Visual Studio Code").arquivo)

    def test_arquivo_solto(self):
        t = interpretar_titulo("script.py - Visual Studio Code")
        self.assertEqual((t.arquivo, t.raiz), ("script.py", None))

    def test_administrador(self):
        t = interpretar_titulo("a.py - proj - Visual Studio Code [Administrator]")
        self.assertEqual(t.arquivo, "a.py")

    def test_nao_e_vscode(self):
        self.assertIsNone(interpretar_titulo("Documento - Bloco de Notas"))
        self.assertIsNone(interpretar_titulo(""))
        self.assertIsNone(interpretar_titulo(None))


class TestArquivoAtivo(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="lumina_vsc_")).resolve()
        self.appdata = self.raiz / "AppData"
        self.user = self.appdata / "Code" / "User"
        (self.user / "globalStorage").mkdir(parents=True)
        (self.user / "workspaceStorage").mkdir()

        self.proj = self.raiz / "meu_projeto"
        (self.proj / "app").mkdir(parents=True)
        (self.proj / "node_modules" / "lib").mkdir(parents=True)
        (self.proj / "app" / "views.py").write_text("def index():\n    return 1\n", "utf-8")
        (self.proj / "node_modules" / "lib" / "util.js").write_text("x", "utf-8")
        (self.proj / "util.js").write_text("y", "utf-8")
        self.antigo = self.raiz / "antigo" / "meu_projeto"
        self.antigo.mkdir(parents=True)

        self._storage({"windowsState": {
            "lastActiveWindow": {"folder": _uri(self.proj)},
            "openedWindows": [
                {"folder": "vscode-remote://ssh-remote%2Bhost/var/www",
                 "remoteAuthority": "ssh-remote+host"},
                {"folder": _uri(self.proj)}],
        }})
        self._ws("h_antigo", {"folder": _uri(self.antigo)})
        self.patch = mock.patch.dict(os.environ, {"APPDATA": str(self.appdata)})
        self.patch.start()
        vscode.limpar_cache()

    def tearDown(self):
        self.patch.stop()
        vscode.limpar_cache()

    def _storage(self, dados):
        (self.user / "globalStorage" / "storage.json").write_text(json.dumps(dados), "utf-8")

    def _ws(self, nome, dados, uris_editor=()):
        pasta = self.user / "workspaceStorage" / nome
        pasta.mkdir()
        (pasta / "workspace.json").write_text(json.dumps(dados), "utf-8")
        conn = sqlite3.connect(pasta / "state.vscdb")
        conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        # Como o VS Code grava: JSON com JSON escapado dentro.
        editores = [{"id": "workbench.editors.files.fileEditorInput",
                     "value": json.dumps({"resourceJSON": {"external": u, "scheme": "file"}})}
                    for u in uris_editor]
        conn.execute("INSERT INTO ItemTable VALUES (?, ?)", (
            "memento/workbench.parts.editor",
            json.dumps({"editorpart.state": {"serializedGrid": {"root": {
                "data": [{"data": {"editors": editores}}]}}}})))
        conn.commit()
        conn.close()
        return pasta

    def test_acha_por_busca(self):
        r = vscode.arquivo_ativo("views.py - meu_projeto - Visual Studio Code")
        self.assertEqual(Path(r.caminho), self.proj / "app" / "views.py")
        self.assertEqual(Path(r.pasta_projeto), self.proj)
        self.assertEqual(r.linguagem, "python")
        self.assertTrue(r.legivel)

    def test_pula_node_modules(self):
        r = vscode.arquivo_ativo("util.js - meu_projeto - Visual Studio Code")
        self.assertEqual(Path(r.caminho), self.proj / "util.js")

    def test_ambiguo_prefere_mais_recente(self):
        (self.proj / "a").mkdir()
        (self.proj / "b").mkdir()
        velho, novo = self.proj / "a" / "models.py", self.proj / "b" / "models.py"
        velho.write_text("1", "utf-8")
        novo.write_text("2", "utf-8")
        agora = time.time()
        os.utime(velho, (agora - 1000, agora - 1000))
        os.utime(novo, (agora, agora))
        r = vscode.arquivo_ativo("models.py - meu_projeto - Visual Studio Code")
        self.assertEqual(Path(r.caminho), novo)

    def test_estado_do_editor_acha_fora_da_pasta(self):
        fora = self.raiz / "outro_lugar" / "notas.md"
        fora.parent.mkdir()
        fora.write_text("# oi", "utf-8")
        self._ws("h_proj", {"folder": _uri(self.proj)}, [_uri(fora)])
        r = vscode.arquivo_ativo("notas.md - meu_projeto - Visual Studio Code")
        self.assertEqual(Path(r.caminho), fora)
        self.assertEqual(r.linguagem, "markdown")

    def test_remoto(self):
        r = vscode.arquivo_ativo("index.php - www [SSH: host] - Visual Studio Code")
        self.assertTrue(r.remoto)
        self.assertIsNone(r.caminho)
        self.assertFalse(r.legivel)

    def test_nao_encontrado(self):
        r = vscode.arquivo_ativo("fantasma.py - meu_projeto - Visual Studio Code")
        self.assertIsNone(r.caminho)
        self.assertEqual(Path(r.pasta_projeto), self.proj)

    def test_projeto_desconhecido(self):
        r = vscode.arquivo_ativo("a.py - nunca_aberto - Visual Studio Code")
        self.assertIsNone(r.caminho)
        self.assertIsNone(r.pasta_projeto)

    def test_sem_arquivo_ou_outro_programa(self):
        self.assertIsNone(vscode.arquivo_ativo("meu_projeto - Visual Studio Code"))
        self.assertIsNone(vscode.arquivo_ativo("Caixa de entrada - Outlook"))

    def test_code_workspace(self):
        pasta_a = self.raiz / "ws_a"
        pasta_a.mkdir()
        (pasta_a / "main.go").write_text("package main", "utf-8")
        arquivo_ws = self.raiz / "estudos.code-workspace"
        arquivo_ws.write_text('{\n // comentario\n "folders": [{"path": "ws_a"},],\n}', "utf-8")
        self._ws("h_ws", {"workspace": _uri(arquivo_ws)})
        r = vscode.arquivo_ativo("main.go - estudos (Workspace) - Visual Studio Code")
        self.assertEqual(Path(r.caminho), pasta_a / "main.go")
        self.assertEqual(r.linguagem, "go")

    def test_projeto_aberto_depois_do_cache(self):
        vscode.arquivo_ativo("views.py - meu_projeto - Visual Studio Code")   # monta o cache
        novo = self.raiz / "recente"
        novo.mkdir()
        (novo / "x.rs").write_text("fn main(){}", "utf-8")
        self._ws("h_novo", {"folder": _uri(novo)})
        r = vscode.arquivo_ativo("x.rs - recente - Visual Studio Code")
        self.assertEqual(Path(r.caminho), novo / "x.rs")

    def test_sem_vscode_instalado(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.raiz / "vazio")}):
            vscode.limpar_cache()
            r = vscode.arquivo_ativo("a.py - p - Visual Studio Code")
            self.assertIsNone(r.caminho)


class TestLerTrecho(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="lumina_trecho_"))

    def test_utf8(self):
        p = self.pasta / "a.py"
        # write_bytes: write_text no Windows trocaria \n por \r\n de novo.
        p.write_bytes("# ação\r\nprint('olá')\n".encode("utf-8"))
        self.assertEqual(vscode.ler_trecho(str(p)), "# ação\nprint('olá')\n")

    def test_cp1252(self):
        p = self.pasta / "b.txt"
        p.write_bytes("ação".encode("cp1252"))
        self.assertEqual(vscode.ler_trecho(str(p)), "ação")

    def test_truncado(self):
        p = self.pasta / "c.py"
        p.write_text("é" * 5000, "utf-8")
        texto = vscode.ler_trecho(str(p), max_chars=100)
        self.assertTrue(texto.startswith("é" * 100))
        self.assertIn("truncado", texto)
        self.assertNotIn("�", texto)

    def test_binario(self):
        p = self.pasta / "d.dat"
        p.write_bytes(b"abc\x00\x01\x02" * 10)
        with self.assertRaises(vscode.ArquivoIlegivel):
            vscode.ler_trecho(str(p))
        p2 = self.pasta / "e.png"
        p2.write_bytes(b"texto")
        with self.assertRaises(vscode.ArquivoIlegivel):
            vscode.ler_trecho(str(p2))

    def test_utf16_com_bom(self):
        p = self.pasta / "f.txt"
        p.write_bytes("olá".encode("utf-16"))
        self.assertEqual(vscode.ler_trecho(str(p)), "olá")

    def test_grande_demais(self):
        p = self.pasta / "g.log"
        p.write_bytes(b"a")
        with mock.patch.object(vscode, "MAX_BYTES", 0):
            with self.assertRaises(vscode.ArquivoIlegivel):
                vscode.ler_trecho(str(p))

    def test_inexistente(self):
        with self.assertRaises(vscode.ArquivoIlegivel):
            vscode.ler_trecho(str(self.pasta / "nada.py"))
        with self.assertRaises(ValueError):          # e subclasse de ValueError
            vscode.ler_trecho("")


if __name__ == "__main__":
    unittest.main()
