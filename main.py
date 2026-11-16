# main.py
"""Lumina — PDF refluido para leitura em tela dividida.

O foco do aplicativo e ocupar meia tela ao lado de outra janela de trabalho.
Por isso a janela sabe se encaixar sozinha, ficar sempre visivel e virar uma
coluna estreita, tudo por teclado.
"""
import sys
import tkinter as tk

import customtkinter as ctk

import ui.componentes as C
from configuracoes import (ALTURA_MINIMA, APP_NOME, APP_VERSAO, LARGURA_MINIMA,
                           TEMA_PERSONALIZADO, T, montar_tema)
from core.branding import carregar_arte, carregar_logo, garantir_icone
from database.biblioteca import BancoBiblioteca
from database.config import BancoConfig
from database.pastas import BancoPastas
from database.sqlite_db import fechar_conexao, inicializar_banco
from services.pdf_service import PDFService
from ui.estante import TelaEstante
from ui.leitor import TelaLeitor

# Largura do "modo coluna", em pixels fisicos de tela.
LARGURA_COLUNA_DOCK = 500


def _fechar_splash_do_executavel():
    """Fecha a tela de abertura do bootloader do PyInstaller.

    Ela cobre o app ate ser fechada explicitamente; fora do .exe o modulo
    simplesmente nao existe.
    """
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.withdraw()                     # so aparece depois de tudo pronto

        inicializar_banco()
        self.banco_config = BancoConfig()

        self._definir_tema_ativo()
        ctk.set_appearance_mode(T.modo)

        splash = Splash(self)

        self.title(f"{APP_NOME} {APP_VERSAO}")
        self.minsize(LARGURA_MINIMA, ALTURA_MINIMA)
        self.configure(fg_color=T.fundo)
        self._aplicar_icone()

        self.banco = BancoBiblioteca()
        self.pastas = BancoPastas()
        self.pdf_service = PDFService()

        self._deco = None
        self._sempre_no_topo = self.banco_config.obter_bool("sempre_no_topo", False)

        self.container = ctk.CTkFrame(self, fg_color=T.fundo, corner_radius=0)
        C.registrar_estilo(self.container, fg_color="fundo")
        self.container.pack(fill="both", expand=True)

        splash.avancar("Lendo a biblioteca…")
        self._completar_metadados(splash)

        splash.avancar("Montando a biblioteca…")
        self.telas = {
            "estante": TelaEstante(self.container, self, self.banco,
                                   self.pdf_service, self.banco_config,
                                   self.pastas),
            "leitor": TelaLeitor(self.container, self, self.banco,
                                 self.pdf_service, self.banco_config),
        }
        self.tela_ativa = None

        splash.avancar("Quase la…")
        self._ligar_atalhos_globais()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.mostrar_tela("estante")

        if self._sempre_no_topo:
            self.attributes("-topmost", True)
        self._aplicar_opacidade(self.banco_config.obter_float("opacidade", 1.0))

        # O CustomTkinter faz seu proprio withdraw/deiconify para pintar a barra
        # de titulo escura. Uma janela em estado `withdrawn` ignora pedidos de
        # redimensionamento, entao a geometria so pode ser aplicada depois que
        # essas chamadas pendentes terminarem -- caso contrario o encaixe em
        # tela dividida simplesmente nao acontece.
        self.after(30, lambda: self._exibir(splash))

    def _exibir(self, splash):
        _fechar_splash_do_executavel()
        self.deiconify()
        self.update_idletasks()
        self._restaurar_janela()
        self.lift()
        self.focus_force()
        splash.fechar()
        if self.banco_config.obter_bool("primeira_execucao", True):
            self.after(700, self._boas_vindas)
        # A lista de vozes vem de um PowerShell (ate segundos). Aquecida aqui,
        # em segundo plano, abrir Configuracoes nao congela a janela.
        try:
            from services import voz
            self.after(1500, voz.preaquecer_vozes)
        except Exception:
            pass
        # "Abrir com > Lumina" ou arrastar um PDF sobre o .exe.
        pdfs = [a for a in sys.argv[1:] if a.lower().endswith(".pdf")]
        if pdfs:
            self.after(200, lambda: self.abrir_arquivo(pdfs[0]))

    def abrir_arquivo(self, caminho):
        """Adiciona o PDF a estante (se ainda nao estiver) e abre na leitura."""
        import os
        caminho = os.path.abspath(caminho)
        if not os.path.exists(caminho):
            C.Toast.mostrar(self, "Arquivo nao encontrado.", "erro")
            return
        try:
            id_livro = self.pdf_service.gerar_id_unico(caminho)
            if self.banco.existe(id_livro):
                self.banco.definir_caminho(id_livro, caminho)
            else:
                if self.pdf_service.esta_protegido(caminho):
                    C.Toast.mostrar(self, "PDF protegido por senha.", "erro")
                    return
                meta = self.pdf_service.extrair_metadados(caminho)
                capa = self.pdf_service.extrair_capa(caminho, id_livro)
                self.banco.adicionar_livro(id_livro, caminho, capa, meta["titulo"],
                                           meta["autor"], meta["total_paginas"])
        except Exception as erro:
            C.Toast.mostrar(self, f"Nao foi possivel abrir: {erro}", "erro")
            return
        self.mostrar_tela("leitor", id_livro)

    def _completar_metadados(self, splash):
        """Preenche titulo, autor e capa de livros vindos da versao antiga.

        A base v1 guardava so o caminho do arquivo, entao esses livros
        apareceriam sem nome nenhum na estante nova.
        """
        import os

        # Sem titulo, sem capa valida ou sem contagem de paginas: livros
        # vindos da v1 tem titulo (o nome do arquivo) mas nada mais.
        pendentes = [l for l in self.banco.obter_estante()
                     if os.path.exists(l.get("caminho_pdf") or "")
                     and (not (l.get("titulo") or "").strip()
                          or not (l.get("total_paginas") or 0)
                          or not os.path.exists(l.get("caminho_capa") or ""))]
        for indice, livro in enumerate(pendentes, 1):
            splash.avancar(f"Lendo metadados… ({indice}/{len(pendentes)})")
            try:
                meta = self.pdf_service.extrair_metadados(livro["caminho_pdf"])
                capa = livro.get("caminho_capa")
                if not capa or not os.path.exists(capa):
                    capa = self.pdf_service.extrair_capa(livro["caminho_pdf"],
                                                         livro["id_livro"])
                # Titulo/autor ja escolhidos (ou renomeados a mao) sao mantidos.
                self.banco.atualizar_metadados(
                    livro["id_livro"],
                    (livro.get("titulo") or "").strip() or meta["titulo"],
                    (livro.get("autor") or "").strip() or meta["autor"],
                    meta["total_paginas"], capa)
            except Exception as erro:
                print(f"[App] Metadados pendentes falharam: {erro}")

    # =====================================================================
    # JANELA
    # =====================================================================
    def _aplicar_icone(self):
        caminho = garantir_icone()
        if caminho and sys.platform == "win32":
            try:
                self.iconbitmap(caminho)
            except Exception:
                pass
        else:
            imagem = carregar_logo(64)
            if imagem is not None:
                try:
                    from PIL import ImageTk
                    self._icone = ImageTk.PhotoImage(imagem)
                    self.iconphoto(True, self._icone)
                except Exception:
                    pass

    # ------------------------------------------------------ escala e medidas
    # Armadilha do CustomTkinter: `geometry()` multiplica LARGURA e ALTURA pelo
    # fator de DPI, mas deixa X e Y como estao. Misturar as duas unidades fazia
    # a janela nascer maior que a tela e o encaixe cair no lugar errado -- logo,
    # todo posicionamento aqui trabalha em pixels fisicos e converte no fim.
    def _escala(self):
        try:
            from customtkinter.windows.widgets.scaling.scaling_tracker import \
                ScalingTracker
            return ScalingTracker.get_window_scaling(self) or 1.0
        except Exception:
            return 1.0

    def _decoracao(self):
        """(borda_lateral, altura_do_titulo) em pixels fisicos.

        `geometry(+x+y)` posiciona a moldura externa, mas largura/altura valem
        para a area de cliente. Sem descontar a moldura, a janela encaixada
        ultrapassa a area util e some atras da barra de tarefas.
        """
        if self._deco is not None:
            return self._deco
        padrao = (8, 31)
        try:
            self.update_idletasks()
            bruto = self.wm_geometry()
            _, _, resto = bruto.partition("+")
            x, _, y = resto.partition("+")
            dx = self.winfo_rootx() - int(x)
            dy = self.winfo_rooty() - int(y)
            self._deco = (dx, dy) if 0 <= dx <= 40 and 0 <= dy <= 110 else padrao
        except Exception:
            self._deco = padrao
        return self._deco

    def _posicionar(self, x, y, largura, altura):
        """Encaixa a MOLDURA da janela no retangulo (pixels fisicos) informado."""
        borda, titulo = self._decoracao()
        escala = self._escala() or 1.0
        cliente_l = max(240, largura - 2 * borda)
        cliente_a = max(240, altura - titulo - borda)
        self.geometry(f"{int(cliente_l / escala)}x{int(cliente_a / escala)}"
                      f"+{int(x)}+{int(y)}")

    def _area_util(self):
        """Area de trabalho em pixels fisicos, sem a barra de tarefas.

        E a area do MONITOR ONDE A JANELA ESTA -- com dois monitores, encaixar
        a esquerda no secundario nao pode jogar a janela para o principal.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

                user32 = ctypes.windll.user32
                user32.MonitorFromWindow.restype = ctypes.c_void_p
                user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
                user32.GetParent.restype = ctypes.c_void_p
                hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
                monitor = user32.MonitorFromWindow(hwnd, 2)   # DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if monitor and user32.GetMonitorInfoW(ctypes.c_void_p(monitor),
                                                      ctypes.byref(info)):
                    r = info.rcWork
                    return (r.left, r.top, r.right - r.left, r.bottom - r.top)
            except Exception:
                pass
            try:
                import ctypes
                from ctypes import wintypes
                retangulo = wintypes.RECT()
                # SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                              ctypes.byref(retangulo), 0):
                    return (retangulo.left, retangulo.top,
                            retangulo.right - retangulo.left,
                            retangulo.bottom - retangulo.top)
            except Exception:
                pass
        escala = self._escala() or 1.0
        return (0, 0, int(self.winfo_screenwidth() * escala),
                int(self.winfo_screenheight() * escala))

    def _restaurar_janela(self):
        salva = self.banco_config.obter("geometria", "")
        if salva and self._geometria_visivel(salva):
            self.geometry(salva)
        else:
            self._centralizar()

    def _geometria_visivel(self, geometria):
        """Descarta posicoes de um monitor que nao existe mais."""
        try:
            _, _, resto = geometria.partition("+")
            if not resto:
                return False
            x, _, y = resto.partition("+")
            x, y = int(x), int(y)
        except Exception:
            return False
        if sys.platform == "win32":
            # Vale qualquer monitor conectado, nao so o principal: quem le no
            # segundo monitor quer reabrir la.
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                user32.MonitorFromPoint.restype = ctypes.c_void_p
                user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
                # Um ponto da barra de titulo precisa estar num monitor.
                return bool(user32.MonitorFromPoint(wintypes.POINT(x + 80, y + 15), 0))
            except Exception:
                pass
        base_x, base_y, largura, altura = self._area_util()
        return (base_x - 60 <= x <= base_x + largura - 150
                and base_y - 20 <= y <= base_y + altura - 120)

    def _centralizar(self):
        """Tamanho inicial proporcional a tela, nunca maior que a area util."""
        x, y, largura, altura = self._area_util()
        alvo_l = min(1180, int(largura * 0.72))
        alvo_a = min(880, int(altura * 0.92))
        self._posicionar(x + (largura - alvo_l) // 2,
                         y + max(0, (altura - alvo_a) // 2 - 10), alvo_l, alvo_a)

    # ------------------------------------------------------- tela dividida
    def encaixar(self, lado):
        """Ocupa metade da tela — o gesto central do uso em tela dividida."""
        self.state("normal")
        x, y, largura, altura = self._area_util()
        metade = largura // 2
        self._posicionar(x if lado == "esquerda" else x + metade, y, metade, altura)
        C.Toast.mostrar(self, f"Encaixado a {lado}", "info", 1200)

    def modo_coluna(self):
        """Faixa estreita na borda direita, sempre visivel: leitura de apoio."""
        self.state("normal")
        x, y, largura, altura = self._area_util()
        # Nunca mais estreita que o minsize (que o CustomTkinter multiplica
        # pela escala): a 150%+ a janela recusava encolher e vazava da tela.
        minimo = int(LARGURA_MINIMA * self._escala()) + 2 * self._decoracao()[0]
        coluna = max(minimo, min(LARGURA_COLUNA_DOCK, largura // 3))
        self._posicionar(x + largura - coluna, y, coluna, altura)
        if not self._sempre_no_topo:
            self.alternar_sempre_no_topo()
        C.Toast.mostrar(self, "Modo coluna — Ctrl+Shift+↓ volta ao normal", "info", 2000)

    def maximizar(self):
        if sys.platform == "win32":
            self.state("zoomed")
        else:
            self._posicionar(*self._area_util())

    def restaurar(self):
        self.state("normal")
        self._centralizar()

    def alternar_sempre_no_topo(self):
        self._sempre_no_topo = not self._sempre_no_topo
        self.attributes("-topmost", self._sempre_no_topo)
        self.banco_config.definir_bool("sempre_no_topo", self._sempre_no_topo)
        return self._sempre_no_topo

    def ajustar_opacidade(self, delta):
        atual = self.banco_config.obter_float("opacidade", 1.0)
        novo = max(0.45, min(1.0, round(atual + delta, 2)))
        self._aplicar_opacidade(novo)
        self.banco_config.definir("opacidade", novo)
        C.Toast.mostrar(self, f"Opacidade: {int(novo * 100)}%", "info", 1100)

    def _aplicar_opacidade(self, valor):
        try:
            self.attributes("-alpha", max(0.45, min(1.0, float(valor))))
        except Exception:
            pass

    # =====================================================================
    # TEMA
    # =====================================================================
    def _definir_tema_ativo(self):
        """Tema nomeado ou paleta derivada das cores escolhidas pelo usuario."""
        nome = self.banco_config.obter("tema", "Escuro")
        if nome == TEMA_PERSONALIZADO:
            T.definir(nome, montar_tema(
                self.banco_config.obter("cor_papel", "#1E1E1E"),
                self.banco_config.obter("cor_texto", "#D4D4D4"),
                self.banco_config.obter("cor_destaque", "#569CD6")))
        else:
            T.definir(nome)

    def aplicar_tema(self, nome=None):
        if nome:
            self.banco_config.definir("tema", nome)
        self._definir_tema_ativo()
        ctk.set_appearance_mode(T.modo)
        self.configure(fg_color=T.fundo)
        C.repintar_tudo()
        for tela in self.telas.values():
            if hasattr(tela, "aplicar_tema"):
                try:
                    tela.aplicar_tema()
                except Exception as erro:
                    print(f"[App] Falha ao repintar tela: {erro}")

    # =====================================================================
    # NAVEGACAO ENTRE TELAS
    # =====================================================================
    def mostrar_tela(self, nome, id_livro=None):
        for tela in self.telas.values():
            tela.pack_forget()

        self.tela_ativa = nome
        tela = self.telas[nome]
        tela.pack(fill="both", expand=True)

        if nome == "estante":
            self.title(f"{APP_NOME} {APP_VERSAO}")
            tela.construir_interface()
        elif nome == "leitor" and id_livro:
            tela.carregar_livro(id_livro)

    # =====================================================================
    # ATALHOS GLOBAIS
    # =====================================================================
    def _ligar_atalhos_globais(self):
        atalhos = {
            "<F11>": lambda e: self._no_leitor(lambda t: t.alternar_modo_foco()),
            "<Escape>": self._ao_escape,
            "<Control-t>": lambda e: self._alternar_topo_com_aviso(),
            # Num campo de texto, Ctrl+Shift+setas estende a selecao por
            # palavra -- nao pode arremessar a janela.
            "<Control-Shift-Left>": lambda e: None if self._em_campo(e)
            else self.encaixar("esquerda"),
            "<Control-Shift-Right>": lambda e: None if self._em_campo(e)
            else self.encaixar("direita"),
            "<Control-Shift-Up>": lambda e: self.maximizar(),
            "<Control-Shift-Down>": lambda e: self.restaurar(),
            "<Alt-c>": lambda e: self.modo_coluna(),
            "<Control-bracketleft>": lambda e: self.ajustar_opacidade(-0.05),
            "<Control-bracketright>": lambda e: self.ajustar_opacidade(0.05),
            "<Control-q>": lambda e: self._ao_fechar(),
        }
        for sequencia, funcao in atalhos.items():
            self.bind(sequencia, funcao)

    @staticmethod
    def _em_campo(evento):
        return isinstance(evento.widget, tk.Entry)

    def _no_leitor(self, acao):
        if self.tela_ativa == "leitor":
            acao(self.telas["leitor"])

    def _ao_escape(self, _=None):
        if self.tela_ativa == "leitor":
            leitor = self.telas["leitor"]
            if leitor.sair_modo_foco():
                return
            if leitor.barra_busca.winfo_ismapped():
                leitor.alternar_busca()
                return
            leitor._fechar_popover()

    def _alternar_topo_com_aviso(self):
        if self.tela_ativa == "leitor":
            self.telas["leitor"].alternar_sempre_no_topo()
        else:
            ativo = self.alternar_sempre_no_topo()
            C.Toast.mostrar(self, "Sempre visivel: ligado" if ativo
                            else "Sempre visivel: desligado", "ok" if ativo else "info", 1400)

    # =====================================================================
    # BOAS-VINDAS E ENCERRAMENTO
    # =====================================================================
    def _boas_vindas(self):
        from ui.modais import Modais
        self.banco_config.definir_bool("primeira_execucao", False)
        Modais.aviso(
            self.telas["estante"], f"Bem-vindo ao {APP_NOME}",
            "Adicione um PDF e ele vira um livro refluido: paragrafos remontados, "
            "cabecalhos removidos e tipografia que voce controla.\n\n"
            "Feito para tela dividida:\n"
            "  Ctrl+Shift+←/→   encaixa em metade da tela\n"
            "  Alt+C            vira uma coluna estreita a direita\n"
            "  Ctrl+T           mantem sobre as outras janelas\n"
            "  F11              modo foco, so o texto\n\n"
            "A lista completa de atalhos esta em Configuracoes.")

    def _ao_fechar(self, _=None):
        # Cada etapa isolada: uma falha ao salvar a geometria nao pode impedir
        # de gravar o progresso, nem deixar o servidor de IA (600 MB) vivo.
        try:
            if self.state() not in ("zoomed", "iconic"):
                self.banco_config.definir("geometria", self.geometry())
        except Exception as erro:
            print(f"[App] Erro ao salvar janela: {erro}")
        try:
            leitor = self.telas.get("leitor")
            if leitor is not None:
                leitor.descarregar()
        except Exception as erro:
            print(f"[App] Erro ao fechar o livro: {erro}")
        try:
            from ia import local
            local.encerrar()
        except Exception as erro:
            print(f"[App] Erro ao encerrar IA local: {erro}")
        try:
            fechar_conexao()
        finally:
            self.destroy()


# =========================================================================
# SPLASH
# =========================================================================
class Splash:
    """Tela de abertura com o logo vetorial, exibida enquanto o banco carrega."""

    def __init__(self, mestre):
        self.janela = None
        try:
            self.janela = tk.Toplevel(mestre)
            self.janela.overrideredirect(True)
            self.janela.configure(background=T.fundo)
            self.janela.attributes("-topmost", True)

            arte = carregar_arte(452)
            # Com a arte nova, o splash e a propria arte (fundo azul-marinho
            # da marca) com o status embaixo; sem ela, simbolo + nome.
            fundo = "#0B1120" if arte is not None else T.fundo
            largura = 454 if arte is not None else 340
            altura = (arte.height + 46) if arte is not None else 300
            x = (self.janela.winfo_screenwidth() - largura) // 2
            y = (self.janela.winfo_screenheight() - altura) // 2
            self.janela.geometry(f"{largura}x{altura}+{x}+{y}")
            self.janela.configure(background=fundo)

            moldura = tk.Frame(self.janela, background="#273149")
            moldura.pack(fill="both", expand=True)
            interior = tk.Frame(moldura, background=fundo)
            interior.pack(fill="both", expand=True, padx=1, pady=1)

            from PIL import ImageTk
            if arte is not None:
                self._foto = ImageTk.PhotoImage(arte)
                tk.Label(interior, image=self._foto, background=fundo,
                         borderwidth=0).pack()
            else:
                imagem = carregar_logo(128)
                if imagem is not None:
                    self._foto = ImageTk.PhotoImage(imagem)
                    tk.Label(interior, image=self._foto, background=fundo).pack(pady=(46, 14))
                tk.Label(interior, text=APP_NOME, background=fundo, foreground=T.texto,
                         font=("Segoe UI", 16, "bold")).pack()
                tk.Label(interior, text=f"versao {APP_VERSAO}", background=fundo,
                         foreground=T.texto_suave, font=("Segoe UI", 9)).pack(pady=(2, 18))

            self.status = tk.Label(interior, text="Iniciando…", background=fundo,
                                   foreground="#8F9BB8", font=("Segoe UI", 9))
            self.status.pack(pady=(4 if arte is not None else 0, 10))
            self.janela.update()
        except Exception:
            self.janela = None

    def avancar(self, texto):
        if self.janela is None:
            return
        try:
            self.status.configure(text=texto)
            self.janela.update()
        except Exception:
            pass

    def fechar(self):
        if self.janela is None:
            return
        try:
            self.janela.destroy()
        except Exception:
            pass
        self.janela = None


def main():
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("dark-blue")
    App().mainloop()


if __name__ == "__main__":
    main()
