# ui/leitor.py
"""Tela de leitura: PDF refluido como livro nativo.

Decisoes centrais desta versao:
  * Layout em `grid` com `grid_remove()`. A v1 usava pack + `before=`, o que
    deixava paineis reaparecendo na ordem errada depois do modo foco.
  * Coluna de texto com largura maxima em caracteres, centralizada. E o que
    torna a leitura confortavel numa janela estreita de tela dividida.
  * Grifos gravados como deslocamento de caractere, nao indice do widget.
  * Barra de acoes flutuante que surge ao selecionar texto, liberando espaco
    na barra superior -- decisivo em janelas de 400px.
"""
import io
import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageTk

import ui.componentes as C
import ui.icones as icones
from configuracoes import (ALTURA_LINHA_PADRAO, APP_NOME, ESP, FONTE_UI,
                           FONTES_LEITURA, INTERVALO_ROLAGEM_MS,
                           LARGURA_COLUNA_MAX, LARGURA_COLUNA_MIN,
                           LIMIAR_COMPACTO, MODO_AUTO, MODO_BOTOES, MODO_FOLHA,
                           MODO_LIVRE, normalizar_modo_virada,
                           MODO_MARGEM, MODO_VIRADA_PADRAO, MODOS_VIRADA,
                           PALAVRAS_POR_MINUTO, PAUSA_FIM_PAGINA_MS, RAIO, T,
                           CATEGORIAS, RENDERIZACOES, RENDER_PADRAO,
                           TAMANHO_FONTE_PADRAO,
                           TEMA_PERSONALIZADO, TEMAS, preset_render,
                           VELOCIDADE_ROLAGEM_PADRAO)
from ia import roteador
from ia.manager import AIManager
import services.codigo_util as codigo_util
import services.contexto as contexto
import services.deteccao_juridico as juridico
import services.perfil as perfil
import services.quadros as quadros
import services.indice as indice_livro
import services.exportar as exportar
import services.realce as realce
import services.tela as tela
import services.dicionario as dicionario
import services.vscode as vscode
import services.voz as voz
from services.pdf_service import abrir as abrir_livro, preparar_epub
from services.pdf_service import epub_pronto as pdf_cache_pronto
from services.reflow import Bloco, MotorReflow
from ui.modais import Modais

# Caractere-objeto: reserva exatamente 1 posicao de indice para cada imagem,
# mantendo o alinhamento entre deslocamento salvo e indice do widget.
MARCA_IMAGEM = "￼"

# Tempo que a oferta de desfazer fica na tela depois de aplicar uma sugestao.
# Longo o bastante para ler a pagina e perceber que nao gostou; curto o
# bastante para a barra nao virar parte permanente do rodape.
SEGUNDOS_DESFAZER = 20

# Confianca minima para exibir uma sugestao que veio de PALPITE do modelo. Mais
# baixa que a das regras porque um desempate, por definicao, sai de dois
# candidatos proximos -- exigir os mesmos 55% faria o nivel 4 rodar e nunca
# aparecer. A barra deixa claro de onde veio, e desfazer e um clique.
MINIMO_PALPITE = 0.28

# Acao que usa o indice do livro em vez de mandar a pagina inteira ao modelo.
ACAO_CONVERSAR = "Conversar com o livro"
ACAO_CODIGO = "Relacionar com meu codigo (VS Code)"

# Como cada origem de resultado se apresenta na lista da busca. O rotulo
# importa: quem digitou uma palavra exata precisa saber quando um resultado
# NAO contem aquela palavra, e apareceu por ser sobre o mesmo assunto.
_MARCA_ORIGEM = {"literal": "", "conceito": "~ ", "relacionado": "≈ "}


def _resumo_do_bloco(bloco, origem):
    """Linha de resultado da busca, com a marca de como ela foi achada."""
    corpo = " ".join(bloco.texto.split())
    return f"{_MARCA_ORIGEM.get(origem, '')}{corpo[:120]}"
# Rotulo na interface -> modo do roteador.
ORIGENS_PERGUNTA = {
    "Automatico (livro ou IA)": "auto",
    "So o livro": "livro",
    "So a IA (conhecimento geral)": "geral",
}

# Altura maxima de uma figura, em fracao da area de leitura. Uma figura de
# pagina inteira exibida em tamanho cheio empurra todo o texto para fora da
# tela; limitada, ela convive com o paragrafo que a explica -- e continua
# abrindo em tamanho real com um clique.
FRACAO_ALTURA_FIGURA = 0.78

# Comeco de paragrafo que sinaliza alerta num manual ou livro tecnico.
_RE_AVISO = re.compile(
    r"^\s*(aten[cç][aã]o|importante|cuidado|aviso|nota|observa[cç][aã]o|dica|"
    r"lembre-se|advert[eê]ncia|perigo|warning|note|tip)\s*[:!\-–]", re.IGNORECASE)

# Abas do painel lateral: rotulo interno -> icone do seletor.
# Abas fixas do painel lateral. As extras entram conforme o que o livro
# realmente contem (figuras, datas, artigos, versiculos, referencias) -- uma
# aba vazia so ocupa espaco e confunde.
ABAS_FIXAS = {"Sumario": "Sumario", "Busca": "Busca",
              "Marcadores": "Marcas", "Notas": "Notas"}
ABAS_EXTRAS = {"Figuras": "Figuras", "Linha do tempo": "Datas",
               "Artigos": "Artigos", "Capitulos": "Caps",
               "Exercicios": "Exerc", "Versiculos": "Versos",
               "Referencias": "Refs", "Quadros": "Quadros"}
ICONES_PAINEL = {**ABAS_FIXAS, **ABAS_EXTRAS}


class TelaLeitor(ctk.CTkFrame):

    def __init__(self, master, controlador, banco, pdf_service, banco_config):
        super().__init__(master, fg_color=T.fundo, corner_radius=0)
        C.registrar_estilo(self, fg_color="fundo")

        self.controlador = controlador
        self.banco = banco
        self.pdf_service = pdf_service
        self.cfg = banco_config
        self.ia_manager = AIManager(banco_config)

        # ---- estado do documento
        self.id_livro = None
        self.livro = None
        self.documento = None
        self.motor = None
        self.pagina_atual = 0
        self.total_paginas = 0
        self.media_palavras = 0.0
        self.prefs_livro = {}
        self.extras = {}          # aba -> itens descobertos no livro
        self._abas_atuais = None
        self.narrador = voz.Narrador()
        self._cancelar_varredura = None
        self._trava_varredura = threading.Lock()
        self._resultado_varredura = None
        self.perfil_detectado = None
        self._faixas_codigo = []
        self._botoes_codigo = []
        self._codigo_dobrado = set()
        self._artigo_dobrado = set()
        self._faixas_artigo = []
        # Bloco de codigo sob o ponteiro agora, como (inicio, fim).
        self._bloco_apontado = None
        self._saida_do_bloco = None
        # ---- indice do livro (conversar com o livro)
        self._indice = None
        self._cancelar_indice = None
        self._ultima_resposta = None
        self._pagina_da_resposta = None
        self._conversa = roteador.Conversa()
        # ---- leitura quadro a quadro (HQ / manga)
        self._quadros = []            # retangulos da pagina atual, em pontos
        self._quadros_de = None       # pagina a que os retangulos pertencem
        self._quadro_atual = -1       # -1 = pagina inteira
        self._entrada_quadro = None   # por qual lado entrar na proxima pagina
        self._geracao_quadros = 0
        self._memoria_contexto = contexto.Memoria(banco_config)
        self._sugestao_atual = None
        self._contexto_recusado = None
        self._id_contexto = None
        self._sugestao_so_cores = False
        self._cores_vindas_de = None
        # Como estava antes da ultima sugestao aplicada, para o "Desfazer".
        self._antes_da_sugestao = None
        self._id_desfazer = None
        self._assinatura_vizinha = None
        self._analise_em_curso = False

        # ---- estado da interface
        self.modo_foco = False
        self.painel_aberto = None
        self.imagens_render = []
        self._dados_img = {}
        self.blocos_render = []
        self.grifos_pagina = []
        self.resultados_busca = []
        self.indice_resultado = -1
        self._cancelar_busca = None
        self._thread_busca = None
        self._compacto = False
        self._largura_anterior = 0
        self._agendado_layout = None
        self._agendado_selecao = None
        self._acumulado_wheel = 0
        self._chegou_ao_limite = 0.0
        self._ultima_folha = 0.0
        self._limite_visto = False
        self._grade_folhas = []
        self._regioes = []
        self._trecho = []
        self._agendado_recentrar = None
        self._agendado_topo = None
        self._altura_da_grade = None
        self._montando_folhas = False
        self._folga_folha = 0
        self._folha_atual = 0
        self._simulando = False
        self._agendado_folha = None
        self._mascara = None
        self._animacao = None
        self._par = None
        self._mascara_par = None
        self._entrar_pelo_fim = False
        self._estado_setas = None
        self._rolagem_ativa = False
        self._id_rolagem = None
        self._resto_rolagem = 0.0
        self._cache_char = {}
        self._assinatura_tags = None
        self._chave_margem = None
        self._margem_memo = ESP['md']
        self._inicio_pagina = None
        self._segundos_pendentes = 0.0
        self._popover = None
        # Fila thread-safe: threads de trabalho nunca tocam no Tk. Elas
        # depositam a funcao aqui e a thread da interface executa.
        self._fila_ui = queue.Queue()
        self._ia_ocupada = False
        # Historico de saltos (sumario, busca, Ctrl+G, resposta da IA): o
        # Alt+<- volta ao ponto de onde a pessoa pulou, como num navegador.
        self._historico = []
        self._futuro = []

        self._construir()
        self.after(50, self._drenar_fila_ui)

    # ------------------------------------------------------ ponte de threads
    def _na_ui(self, funcao):
        """Agenda `funcao` na thread da interface. Seguro de qualquer thread."""
        self._fila_ui.put(funcao)

    def _drenar_fila_ui(self):
        try:
            while True:
                funcao = self._fila_ui.get_nowait()
                try:
                    funcao()
                except Exception as erro:
                    print(f"[Leitor] Falha em retorno de thread: {erro}")
        except queue.Empty:
            pass
        try:
            self.after(50, self._drenar_fila_ui)
        except Exception:
            pass        # janela destruida: fim do ciclo

    # =====================================================================
    # CONSTRUCAO DA INTERFACE
    # =====================================================================
    def _construir(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._construir_barra_topo()
        self._construir_barra_busca()
        self._construir_corpo()
        self._construir_barra_baixo()
        self._construir_barra_selecao()
        self._construir_barra_contexto()

        self.bind("<Configure>", self._ao_redimensionar)

    # ------------------------------------------------------------ barra topo
    def _construir_barra_topo(self):
        self.barra_topo = C.quadro(self, "superficie", height=48)
        self.barra_topo.grid(row=0, column=0, sticky="ew")
        self.barra_topo.grid_propagate(False)
        self.barra_topo.grid_columnconfigure(1, weight=1)

        esquerda = C.quadro(self.barra_topo, "transparent")
        esquerda.grid(row=0, column=0, sticky="w", padx=(ESP["sm"], 0), pady=ESP["xs"])

        self.btn_voltar = C.botao_icone(esquerda, "voltar", self.voltar, "Voltar a biblioteca (Alt+←)")
        self.btn_voltar.pack(side="left")

        self.lbl_titulo = C.rotulo(self.barra_topo, "", 13, "bold", "texto")
        self.lbl_titulo.grid(row=0, column=1, sticky="w", padx=ESP["sm"])

        direita = C.quadro(self.barra_topo, "transparent")
        direita.grid(row=0, column=2, sticky="e", padx=(0, ESP["sm"]), pady=ESP["xs"])
        self.acoes_direita = direita

        self.btn_painel = C.botao_icone(direita, "menu", lambda: self.alternar_painel("Sumario"),
                                        "Sumario, busca, marcadores e notas (Ctrl+B)")
        self.btn_busca = C.botao_icone(direita, "busca", self.alternar_busca,
                                       "Buscar no livro inteiro (Ctrl+F)")
        self.btn_favorito = C.botao_icone(direita, "estrela", self.alternar_favorito,
                                          "Marcar esta pagina (Ctrl+D)")
        self.btn_nota = C.botao_icone(direita, "nota", self.abrir_notas,
                                      "Anotar esta pagina (Ctrl+N)")
        self.btn_voz = C.botao_icone(direita, "play", self.alternar_narracao,
                                     "Ler esta pagina em voz alta (Ctrl+L)")
        self.btn_ia = C.botao_icone(direita, "ia", self.alternar_assistente,
                                    "Assistente de estudo (Ctrl+I)")
        self.btn_tipografia = C.botao_icone(direita, "Aa", self.alternar_tipografia,
                                            "Aparencia e tipografia (Ctrl+P)")
        self.btn_revisao = C.botao_icone(direita, "recentes", self.abrir_revisao,
                                         "Revisar grifos e ver estatisticas (Ctrl+R)")
        self.btn_topo = C.botao_icone(direita, "fixar", self.alternar_sempre_no_topo,
                                      "Manter sobre outras janelas (Ctrl+T)")
        self.btn_foco = C.botao_icone(direita, "foco", self.alternar_modo_foco,
                                      "Modo foco (F11)")

        for widget in (self.btn_painel, self.btn_busca, self.btn_favorito, self.btn_nota,
                       self.btn_voz, self.btn_ia, self.btn_revisao, self.btn_tipografia,
                       self.btn_topo, self.btn_foco):
            widget.pack(side="left", padx=1)

    # ----------------------------------------------------------- barra busca
    def _construir_barra_busca(self):
        self.barra_busca = C.quadro(self, "superficie_alt", height=46)
        self.barra_busca.grid(row=1, column=0, sticky="ew")
        self.barra_busca.grid_columnconfigure(0, weight=1)
        self.barra_busca.grid_remove()

        self.entry_busca = C.campo(self.barra_busca, "Buscar em todo o livro...", largura=200)
        self.entry_busca.grid(row=0, column=0, sticky="ew", padx=ESP["md"], pady=ESP["sm"])
        self.entry_busca.bind("<Return>", lambda e: self.executar_busca())
        self.entry_busca.bind("<Escape>", lambda e: self.alternar_busca())

        self.lbl_busca_status = C.rotulo(self.barra_busca, "", 11, "normal", "texto_suave",
                                         ancora="e")
        self.lbl_busca_status.grid(row=0, column=1, padx=ESP["sm"])

        C.botao_icone(self.barra_busca, "cima", lambda: self.navegar_resultado(-1),
                      "Resultado anterior (Shift+F3)").grid(row=0, column=2)
        C.botao_icone(self.barra_busca, "baixo", lambda: self.navegar_resultado(1),
                      "Proximo resultado (F3)").grid(row=0, column=3)
        C.botao_icone(self.barra_busca, "fechar", self.alternar_busca,
                      "Fechar busca (Esc)").grid(row=0, column=4, padx=(0, ESP["sm"]))

    # ---------------------------------------------------------------- corpo
    def _construir_corpo(self):
        self.corpo = C.quadro(self, "fundo")
        self.corpo.grid(row=2, column=0, sticky="nsew")
        self.corpo.grid_rowconfigure(0, weight=1)
        self.corpo.grid_columnconfigure(1, weight=1)

        self._construir_painel_lateral()
        self._construir_leitura()
        self._construir_painel_ia()

    def _construir_painel_lateral(self):
        self.painel = C.quadro(self.corpo, "superficie", width=252)
        self.painel.grid(row=0, column=0, sticky="nsew")
        self.painel.grid_propagate(False)
        self.painel.grid_rowconfigure(1, weight=1)
        self.painel.grid_columnconfigure(0, weight=1)
        self.painel.grid_remove()

        topo = C.quadro(self.painel, "transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=ESP["sm"], pady=(ESP["sm"], 0))

        self.seletor_painel = ctk.CTkSegmentedButton(
            topo, values=list(ICONES_PAINEL.values()), width=200, height=30,
            command=self._trocar_painel,
            font=ctk.CTkFont(FONTE_UI, 10),
            fg_color=T.superficie_alt, selected_color=T.destaque,
            selected_hover_color=T.destaque_hover, unselected_color=T.superficie_alt,
            unselected_hover_color=T.borda, text_color=T.texto)
        C.registrar_estilo(self.seletor_painel, fg_color="superficie_alt",
                           selected_color="destaque", selected_hover_color="destaque_hover",
                           unselected_color="superficie_alt", unselected_hover_color="borda",
                           text_color="texto")
        self.seletor_painel.pack(fill="x")
        self.seletor_painel.set(ICONES_PAINEL["Sumario"])

        self.conteudo_painel = C.rolavel(self.painel, "superficie")
        self.conteudo_painel.grid(row=1, column=0, sticky="nsew", padx=ESP["xs"], pady=ESP["xs"])

        rodape = C.quadro(self.painel, "transparent")
        rodape.grid(row=2, column=0, sticky="ew", padx=ESP["sm"], pady=ESP["sm"])
        self.entry_ir = C.campo(rodape, "Pagina", largura=70, altura=30)
        self.entry_ir.pack(side="left")
        self.entry_ir.bind("<Return>", lambda e: self._ir_pelo_campo())
        C.botao(rodape, "Ir", self._ir_pelo_campo, largura=52, altura=30,
                variante="secundario").pack(side="left", padx=ESP["xs"])

    def _construir_leitura(self):
        self.palco = C.quadro(self.corpo, "papel")
        self.palco.grid(row=0, column=1, sticky="nsew")
        self.palco.grid_rowconfigure(0, weight=1)
        self.palco.grid_columnconfigure(0, weight=1)

        self.area = ctk.CTkTextbox(
            self.palco, wrap="word", corner_radius=0, border_width=0,
            fg_color=T.papel, text_color=T.papel_texto,
            scrollbar_button_color=T.borda, scrollbar_button_hover_color=T.texto_suave)
        C.registrar_estilo(self.area, fg_color="papel", text_color="papel_texto",
                           scrollbar_button_color="borda",
                           scrollbar_button_hover_color="texto_suave")
        self.area.grid(row=0, column=0, sticky="nsew")

        self.txt = self.area._textbox
        self.txt.configure(cursor="arrow", spacing1=0, spacing2=0, spacing3=0,
                           insertwidth=0, takefocus=0)
        self._largura_justificada = None
        self._agendado_justificar = None
        self.txt.bind("<Configure>", self._texto_redimensionado, add="+")
        self.area.configure(state="disabled")

        self.txt.bind("<<Selection>>", self._ao_mudar_selecao, add=True)
        self.txt.bind("<MouseWheel>", self._ao_rolar, add=True)
        self.txt.bind("<Button-1>", self._ao_clicar_texto, add=True)
        self.txt.bind("<Motion>", self._ao_mover_no_texto, add=True)
        self.txt.bind("<Leave>", self._ao_sair_do_texto, add=True)

        # Encadeia o aviso de rolagem: o widget continua alimentando a barra de
        # rolagem do CustomTkinter e, de quebra, avisa quando o fim da pagina
        # entra em cena. E a deteccao exata de "cheguei ao fim", sem ficar
        # perguntando a posicao num temporizador.
        self.txt.configure(yscrollcommand=self._ao_mover_visao)

        self._construir_setas()

    # ------------------------------------------------------- setas flutuantes
    def _construir_setas(self):
        """Botoes redondos nas laterais, usados pelo modo 'Botoes flutuantes'."""
        # Indicador de pagina usado apenas no modo foco. Precisa de fundo
        # proprio: sem ele o numero se mistura ao texto do livro.
        self.lbl_flutuante = ctk.CTkLabel(
            self.palco, text="", font=ctk.CTkFont(FONTE_UI, 11, "bold"),
            text_color=T.texto_suave, fg_color=T.superficie,
            corner_radius=RAIO["pill"], padx=ESP["md"], pady=3)
        C.registrar_estilo(self.lbl_flutuante, text_color="texto_suave",
                           fg_color="superficie")

        self.btn_seta_ant = self._seta("anterior", self.pagina_anterior,
                                       "Pagina anterior (voce esta no topo)")
        self.btn_seta_prox = self._seta("proximo", self.proxima_pagina,
                                        "Proxima pagina (fim desta)")

        # Controle da rolagem automatica.
        self.painel_auto = C.quadro(self.palco, "superficie", raio=RAIO["pill"])
        self.painel_auto.configure(border_width=1, border_color=T.borda)
        C.registrar_estilo(self.painel_auto, fg_color="superficie", border_color="borda")
        self.btn_auto = C.botao_icone(self.painel_auto, "pause", self.alternar_rolagem,
                                      "Pausar/retomar (Ctrl+Espaco)", tamanho=30)
        self.btn_auto.pack(side="left", padx=(4, 0), pady=3)
        C.botao_icone(self.painel_auto, "−", lambda: self.ajustar_velocidade(-1),
                      "Mais devagar", tamanho=26).pack(side="left")
        self.lbl_velocidade = C.rotulo(self.painel_auto, "", 10, "bold", ancora="center",
                                       width=18)
        self.lbl_velocidade.pack(side="left")
        C.botao_icone(self.painel_auto, "+", lambda: self.ajustar_velocidade(1),
                      "Mais rapido", tamanho=26).pack(side="left", padx=(0, 4))

    def _seta(self, chave, comando, dica):
        # Discreta em repouso e so ganha o destaque sob o cursor: uma bolha
        # laranja permanente sobre o texto competia com a leitura.
        botao = ctk.CTkButton(
            self.palco, text=icones.icone(chave), command=comando,
            width=46, height=46, corner_radius=23,
            fg_color=T.superficie, hover_color=T.destaque,
            text_color=T.texto_suave, border_width=1, border_color=T.borda,
            font=ctk.CTkFont(icones.familia(), 20))
        C.registrar_estilo(botao, fg_color="superficie", hover_color="destaque",
                           text_color="texto_suave", border_color="borda")
        botao.bind("<Enter>", lambda e, b=botao: b.configure(text_color=T.texto_destaque))
        botao.bind("<Leave>", lambda e, b=botao: b.configure(text_color=T.texto_suave))
        botao._dica = C.Tooltip(botao, dica)
        return botao

    def _construir_painel_ia(self):
        self.painel_ia = C.quadro(self.corpo, "superficie", width=286)
        self.painel_ia.grid(row=0, column=2, sticky="nsew")
        self.painel_ia.grid_propagate(False)
        self.painel_ia.grid_rowconfigure(6, weight=1)
        self.painel_ia.grid_columnconfigure(0, weight=1)
        self.painel_ia.grid_remove()

        cabecalho = C.quadro(self.painel_ia, "transparent")
        cabecalho.grid(row=0, column=0, sticky="ew", padx=ESP["md"], pady=(ESP["md"], ESP["xs"]))
        C.rotulo(cabecalho, "Assistente de estudo", 13, "bold").pack(side="left")
        C.botao_icone(cabecalho, "fechar", self.alternar_assistente, "Fechar", tamanho=26).pack(side="right")

        self.lbl_ia_status = C.rotulo(self.painel_ia, "Provedor: —", 10, "normal", "texto_suave")
        self.lbl_ia_status.grid(row=1, column=0, sticky="ew", padx=ESP["md"])

        self.combo_acao = C.seletor(
            self.painel_ia,
            [ACAO_CONVERSAR, ACAO_CODIGO, "Resumo da pagina", "Palavras-chave",
             "Gerar flashcards", "Explicar de forma simples",
             "Responder pergunta"],
            comando=self._ao_trocar_acao_ia, largura=250,
            valor=ACAO_CONVERSAR)
        self.combo_acao.grid(row=2, column=0, sticky="ew", padx=ESP["md"], pady=ESP["sm"])

        # Onde a resposta pode ser buscada. "Automatico" e o padrao porque
        # ninguem deveria ter que decidir isso ANTES de escrever a pergunta --
        # o roteador ja sabe distinguir "o que e ForeignKey" de um traceback.
        self.combo_origem = C.seletor(
            self.painel_ia, list(ORIGENS_PERGUNTA), comando=lambda _v: None,
            largura=250, valor=next(iter(ORIGENS_PERGUNTA)))
        self.combo_origem.grid(row=3, column=0, sticky="ew", padx=ESP["md"])

        self.entry_pergunta = C.campo(self.painel_ia, "Pergunte sobre o livro…",
                                      largura=250)
        self.entry_pergunta.grid(row=4, column=0, sticky="ew", padx=ESP["md"],
                                 pady=(ESP["xs"], 0))
        self.entry_pergunta.bind("<Return>", lambda e: self.executar_ia())

        self.lbl_indice = C.rotulo(self.painel_ia, "", 9, "normal", "texto_suave")
        self.lbl_indice.grid(row=5, column=0, sticky="ew", padx=ESP["md"])

        self.txt_ia = ctk.CTkTextbox(
            self.painel_ia, wrap="word", corner_radius=RAIO["sm"], border_width=0,
            fg_color=T.superficie_alt, text_color=T.texto,
            font=ctk.CTkFont(FONTE_UI, 12))
        C.registrar_estilo(self.txt_ia, fg_color="superficie_alt", text_color="texto")
        self.txt_ia.grid(row=6, column=0, sticky="nsew", padx=ESP["md"], pady=ESP["sm"])
        self._escrever_ia(
            "Pergunte sobre o livro aberto.\n\n"
            "A resposta sai do texto do proprio livro, com as paginas de "
            "origem. Se o livro nao falar do assunto, o Lumina diz isso em vez "
            "de inventar — e oferece perguntar a IA como conhecimento geral.")

        acoes = C.quadro(self.painel_ia, "transparent")
        acoes.grid(row=7, column=0, sticky="ew", padx=ESP["md"], pady=(0, ESP["md"]))
        self.btn_ia_processar = C.botao(acoes, "Processar", self.executar_ia, largura=110)
        self.btn_ia_processar.pack(side="left")
        self.btn_ir_resposta = C.botao(acoes, "Ir a pagina", self._ir_para_resposta,
                                       largura=110, variante="secundario")
        C.botao_icone(acoes, "copiar", self._copiar_ia, "Copiar resultado").pack(side="right")

    # ---------------------------------------------------------- barra baixo
    def _construir_barra_baixo(self):
        self.barra_baixo = C.quadro(self, "superficie", height=56)
        self.barra_baixo.grid(row=3, column=0, sticky="ew")
        self.barra_baixo.grid_propagate(False)
        self.barra_baixo.grid_columnconfigure(1, weight=1)

        self.btn_anterior = C.botao_icone(self.barra_baixo, "pagina_ant", self.pagina_anterior,
                                          "Pagina anterior (←)", tamanho=40)
        self.btn_anterior.grid(row=0, column=0, padx=(ESP["md"], 0), pady=ESP["sm"])

        centro = C.quadro(self.barra_baixo, "transparent")
        centro.grid(row=0, column=1, sticky="ew", padx=ESP["md"])

        linha = C.quadro(centro, "transparent")
        linha.pack(fill="x")
        self.lbl_pagina = C.rotulo(linha, "— / —", 12, "bold", ancora="w")
        self.lbl_pagina.pack(side="left")
        self.lbl_tempo = C.rotulo(linha, "", 10, "normal", "texto_suave", ancora="e")
        self.lbl_tempo.pack(side="right")

        self.progresso = C.BarraProgresso(centro, altura=6, fundo="superficie")
        self.progresso.configure(cursor="hand2")
        self.progresso.pack(fill="x", pady=(6, 0))
        self.progresso.set(0)
        self.progresso.bind("<Button-1>", self._ao_clicar_progresso)

        self.btn_proxima = C.botao_icone(self.barra_baixo, "pagina_prox", self.proxima_pagina,
                                         "Proxima pagina (→ ou Espaco)", tamanho=40)
        self.btn_proxima.grid(row=0, column=2, padx=(0, ESP["md"]), pady=ESP["sm"])

    # -------------------------------------------------------- barra selecao
    def _construir_barra_selecao(self):
        """Aparece so quando ha texto selecionado, ancorada no rodape da leitura."""
        self.barra_selecao = C.quadro(self.palco, "superficie_alt", raio=RAIO["pill"])
        self.barra_selecao.configure(border_width=1, border_color=T.borda)
        C.registrar_estilo(self.barra_selecao, fg_color="superficie_alt", border_color="borda")

        for icone, comando, dica in (
            ("grifar", self.grifar_selecao, "Grifar selecao (Ctrl+H)"),
            ("apagar_grifo", self.remover_grifo, "Remover grifo da selecao"),
            ("copiar", self.copiar_selecao, "Copiar (Ctrl+C)"),
            ("nota", self.abrir_notas, "Anotar esta pagina (Ctrl+N)"),
            ("ia", self.perguntar_sobre_selecao, "Enviar selecao ao assistente"),
            ("info", self.consultar_dicionario, "Definir e traduzir a selecao"),
        ):
            C.botao_icone(self.barra_selecao, icone, comando, dica, tamanho=32).pack(
                side="left", padx=2, pady=3)

    # =====================================================================
    # CICLO DE VIDA DO LIVRO
    # =====================================================================
    def carregar_livro(self, id_livro):
        if id_livro == self.id_livro and self.documento:
            self._retomar_cronometro()
            return

        self.descarregar()
        livro = self.banco.obter_livro(id_livro)
        if not livro:
            C.Toast.mostrar(self, "Livro nao encontrado na biblioteca.", "erro")
            self.controlador.mostrar_tela("estante")
            return

        documento = self.pdf_service.abrir_documento(livro["caminho_pdf"])
        if documento is None:
            Modais.aviso(self, "Nao foi possivel abrir",
                         "O arquivo foi movido, renomeado ou esta protegido por senha.\n\n"
                         f"Caminho esperado:\n{livro['caminho_pdf']}")
            self.controlador.mostrar_tela("estante")
            return

        self.id_livro = id_livro
        self.livro = livro
        self.documento = documento
        MotorReflow.ocr_ativo = self.cfg.obter_bool("ocr_ativo", True)
        self.motor = MotorReflow(documento)
        self.prefs_livro = self.banco.obter_preferencias(id_livro)
        self.extras = {}
        self._abas_atuais = None
        self.perfil_detectado = None
        self._codigo_dobrado = set()
        self._artigo_dobrado = set()
        self._quadros = []
        self._quadros_de = None
        self._quadro_atual = -1
        self._entrada_quadro = None
        if self._cancelar_indice is not None:
            self._cancelar_indice.set()
        self._indice = None
        self._ultima_resposta = None
        self._pagina_da_resposta = None
        # Conversa e POR LIVRO: herdar o assunto de outro livro seria pior
        # que nao herdar nada.
        self._conversa.limpar()
        self.total_paginas = len(documento)
        self.pagina_atual = max(0, min(livro.get("pagina_atual") or 0, self.total_paginas - 1))

        if livro.get("total_paginas") != self.total_paginas:
            self.banco.definir_total_paginas(id_livro, self.total_paginas)

        titulo = livro.get("titulo") or "Sem titulo"
        curto = titulo if len(titulo) <= 52 else titulo[:50] + "…"
        self.lbl_titulo.configure(text=curto)
        self.controlador.title(f"{titulo} — {APP_NOME}")

        self.media_palavras = self.pdf_service.contar_palavras_amostra(documento)

        self._fechar_painel()
        self.painel_ia.grid_remove()
        self.barra_busca.grid_remove()
        self.modo_foco = False
        self._aplicar_visibilidade_foco()

        self.ativar_atalhos()
        self._atualizar_status_ia()
        self.renderizar()
        # Livro de texto escaneado: o resto do livro e reconhecido em fundo, a
        # partir da pagina atual, para as proximas paginas ja virem prontas.
        if getattr(self.motor, "escaneado_com_texto", False):
            # Sem texto nativo a amostra conta zero palavras e o "tempo
            # restante" dizia 3 min para 900 paginas: usa as do OCR.
            amostra = getattr(self.motor, "_amostra_ocr", "")
            if self.media_palavras < 50:
                self.media_palavras = max(250, len(amostra.split()) // 3)
                self._atualizar_rodape()
            try:
                from services import ocr
                ocr.reconhecer_em_segundo_plano(livro["caminho_pdf"], self.pagina_atual)
                C.Toast.mostrar(self, "Livro escaneado: o texto esta sendo reconhecido "
                                      "em segundo plano (OCR).", "info", 3200)
            except Exception as erro:
                print(f"[Leitor] OCR em fundo: {erro}")
        # EPUB ainda sem paginacao guardada (importado antes desta versao):
        # prepara agora, em outro processo. A partir da proxima abertura ele
        # abre na hora e as tarefas de fundo deixam de travar a leitura.
        if (livro["caminho_pdf"].lower().endswith(".epub")
                and not pdf_cache_pronto(livro["caminho_pdf"])):
            def pronto(ok):
                if ok:
                    self._na_ui(lambda: C.Toast.mostrar(
                        self, "Livro preparado: da proxima vez ele abre na hora.",
                        "ok", 2600))
            preparar_epub(livro["caminho_pdf"], ao_terminar=pronto)
        # Volta ao paragrafo exato em que a leitura parou, nao so a pagina.
        rolagem = int(livro.get("rolagem") or 0)
        if rolagem > 0:
            self.after(60, lambda: self._restaurar_rolagem(id_livro, rolagem))
        self.aplicar_modo_virada()
        self._retomar_cronometro()
        # Depois da primeira pagina na tela: catalogar nao pode atrasar a leitura.
        self.after(400, self._iniciar_varredura)
        # Descobrir as vozes abre um PowerShell; feito agora, em segundo plano,
        # o primeiro Ctrl+L nao paga essa espera com a janela congelada.
        self.after(600, voz.preaquecer_vozes)
        # O indice do livro sobe junto, em 2o plano: e o que permite perguntar
        # sobre o livro inteiro em vez de so sobre a pagina aberta.
        self.after(800, self._iniciar_indice)
        self._contexto_recusado = None
        self._cores_vindas_de = None
        self._assinatura_vizinha = None
        if self._id_contexto is None:
            # Guardar o id: sem isso o descarregar nao conseguia cancelar e
            # abrir/fechar livros rapido deixava dois monitores rodando.
            self._id_contexto = self.after(2000, self._monitorar_contexto)

    def descarregar(self):
        """Fecha o documento e grava o que estiver pendente."""
        self.parar_rolagem()
        self.narrador.parar()
        if self._cancelar_varredura is not None:
            self._cancelar_varredura.set()
        if self._cancelar_indice is not None:
            self._cancelar_indice.set()
        if self._id_contexto is not None:
            try:
                self.after_cancel(self._id_contexto)
            except Exception:
                pass
            self._id_contexto = None
        try:
            self.barra_contexto.grid_remove()
        except Exception:
            pass
        self._pausar_cronometro(gravar=True)
        self._gravar_rolagem()
        self.desativar_atalhos()
        self._cancelar_thread_busca()
        self._historico = []
        self._futuro = []
        if self.documento is not None:
            try:
                self.documento.close()
            except Exception:
                pass
        self.documento = None
        self.motor = None
        self.id_livro = None
        self.livro = None
        self.imagens_render.clear()
        self._dados_img.clear()
        # Busca e POR LIVRO: sem isso o F3 do livro B pulava para paginas do
        # livro A e todo render de B grifava o termo antigo.
        self.resultados_busca = []
        self.termo_busca = ""
        self.indice_resultado = -1
        self._quadros = []
        self._quadros_de = None

    def voltar(self, _=None):
        self.descarregar()
        self.controlador.mostrar_tela("estante")

    # ------------------------------------------------------------ cronometro
    def _retomar_cronometro(self):
        self._inicio_pagina = time.time()

    def _pausar_cronometro(self, gravar=False):
        if self._inicio_pagina is not None:
            self._segundos_pendentes += time.time() - self._inicio_pagina
            self._inicio_pagina = None
        if gravar and self.id_livro and self._segundos_pendentes >= 1:
            self.banco.somar_tempo_leitura(self.id_livro, int(self._segundos_pendentes))
            self._segundos_pendentes = 0.0

    # ================================================ PREFERENCIAS DO LIVRO
    # Cada livro pode ter tipografia propria. Coluna nula = seguir o global.
    _MAPA_PREF = {"fonte_leitura": "pref_fonte", "tamanho_fonte": "pref_tamanho",
                  "altura_linha": "pref_altura", "largura_coluna": "pref_largura"}

    def pref(self, chave, padrao=None):
        """Ordem: ajuste do livro > preset do tipo de renderizacao > global."""
        coluna = self._MAPA_PREF.get(chave)
        if coluna and self.prefs_livro.get(coluna) not in (None, ""):
            return self.prefs_livro[coluna]
        if self.motor is not None:
            preset = self.preset
            if chave == "fonte_leitura" and preset.get("fonte"):
                return preset["fonte"]
            if chave in ("altura_linha", "largura_coluna") and chave in preset:
                return preset[chave]
        return self.cfg.obter(chave, padrao)

    def pref_int(self, chave, padrao=0):
        try:
            return int(float(self.pref(chave)))
        except (TypeError, ValueError):
            return padrao

    def pref_float(self, chave, padrao=0.0):
        try:
            return float(self.pref(chave))
        except (TypeError, ValueError):
            return padrao

    @property
    def tem_pref_propria(self):
        return any(self.prefs_livro.get(c) not in (None, "")
                   for c in self._MAPA_PREF.values())

    def definir_pref(self, chave, valor):
        """Grava no livro quando ele tem tipografia propria; senao no global."""
        coluna = self._MAPA_PREF.get(chave)
        if coluna and self.tem_pref_propria and self.id_livro:
            self.banco.definir_preferencia(self.id_livro, coluna, valor)
            self.prefs_livro[coluna] = valor
        else:
            self.cfg.definir(chave, valor)

    def alternar_pref_propria(self, ativar):
        if not self.id_livro:
            return
        if ativar:
            # Comeca copiando o que esta valendo, para nada mudar de aparencia.
            for chave, coluna in self._MAPA_PREF.items():
                self.banco.definir_preferencia(self.id_livro, coluna,
                                               self.cfg.obter(chave))
        else:
            self.banco.limpar_preferencias(self.id_livro)
        self.prefs_livro = self.banco.obter_preferencias(self.id_livro)
        self.renderizar()

    @property
    def render_livro(self):
        """Tipo de RENDERIZACAO do livro -- como ele deve ser exibido.

        Independente da categoria (o assunto), que so organiza a estante:
        engenharia e medicina se leem igual, mas ficam em prateleiras
        diferentes.
        """
        escolhido = (self.prefs_livro.get("render") or "auto").lower()
        if escolhido in RENDERIZACOES:
            return escolhido
        return self.motor.render_detectado if self.motor else RENDER_PADRAO

    @property
    def preset(self):
        return preset_render(self.render_livro)

    def definir_render(self, valor):
        if not self.id_livro:
            return
        self.banco.definir_preferencia(self.id_livro, "render", valor)
        self.prefs_livro["render"] = valor
        # O preset novo manda na tipografia, entao a memoria de layout cai.
        self._chave_margem = None
        self._assinatura_tags = None
        self.renderizar()

    def definir_categoria(self, valor):
        """Camada 2: nao altera nada da leitura, so a organizacao."""
        if self.id_livro:
            self.banco.definir_preferencia(self.id_livro, "categoria", valor)
            self.prefs_livro["categoria"] = valor

    # =====================================================================
    # RENDERIZACAO
    # =====================================================================
    def renderizar(self, manter_rolagem=False):
        """Redesenha a pagina atual.

        manter_rolagem=True preserva a posicao de leitura: redimensionar a
        janela, trocar tema ou fonte nao deve jogar o leitor de volta ao topo.
        """
        if not self.documento or self.motor is None:
            return
        # Posicao a preservar: (pagina, caractere no topo, pixels de recuo da
        # linha). Fracao do yview nao serve -- o trecho muda de tamanho.
        ancora = self._posicao_no_topo() if manter_rolagem and self._regioes else None
        if self._agendado_recentrar is not None:
            try:
                self.after_cancel(self._agendado_recentrar)
            except Exception:
                pass
            self._agendado_recentrar = None

        preset_atual = self.preset
        if preset_atual.get("pagina_como_imagem"):
            self._regioes = []
            self._renderizar_pagina_inteira()
            return

        paginas = self._paginas_do_trecho()
        self.blocos_render = self.motor.pagina(self.pagina_atual).blocos

        familia = FONTES_LEITURA.get(self.pref("fonte_leitura"), "Georgia")
        tamanho = max(9, self.pref_int("tamanho_fonte", TAMANHO_FONTE_PADRAO))
        altura_linha = self.pref_float("altura_linha", ALTURA_LINHA_PADRAO)
        preset = self.preset
        recuo = preset.get("recuo_paragrafo", False)
        mostrar_imagens = self.cfg.obter_bool("mostrar_imagens", True)
        realca_codigo = preset.get("realce_codigo", False)
        numerar_codigo = realca_codigo and self.cfg.obter_bool("numerar_codigo", False)
        usa_mono = preset.get("mono_inline", False)

        self._configurar_tags(familia, tamanho, altura_linha, recuo)

        # Monta o conteudo inteiro como uma string unica; as imagens ocupam
        # uma marca de 1 caractere que e trocada pela imagem depois da insercao.
        partes = []
        marcacoes = []          # (inicio, fim, tag)
        imagens = []            # (offset, bytes)
        faixas_codigo = []      # blocos de codigo com seus botoes
        faixas_artigo = []      # artigos juridicos recolhiveis
        artigo_atual = None
        # So agrupa artigos em livro que tenha artigos de verdade: o
        # pipeline ja contou, e um 'Art.' solto nao justifica o recurso.
        agrupa_artigos = bool(self.perfil_detectado and
                              (self.perfil_detectado.estrutura.get('artigos') or 0) >= 12)
        deslocamento = 0
        regioes = []
        ultimo_texto = None     # (bloco, inicio no texto) do ultimo bloco de texto

        for numero in paginas:
            pagina = self.motor.pagina(numero)
            inicio_pagina = deslocamento
            primeiro_da_pagina = True
            for bloco in pagina.blocos:
              # ---- costura: paragrafo que atravessa a quebra de pagina
              # O PDF (e o EPUB paginado) corta o paragrafo onde a folha acaba:
              # "...personalidade ou in" | "teligencia, e todo mundo...". Sem
              # costura, a continuacao virava paragrafo novo, com recuo -- e a
              # palavra partida ficava em dois pedacos.
              if (primeiro_da_pagina and numero != paginas[0] and ultimo_texto
                      and bloco.texto and self._continua_paragrafo(ultimo_texto[0], bloco)):
                  fim_ant = partes[-1]
                  if ultimo_texto[0].hifen_final:
                      corte = 2 if fim_ant.endswith("-\n") else 1
                      partes[-1] = fim_ant[:-corte]
                      deslocamento -= corte
                  else:
                      partes[-1] = fim_ant[:-1] + " "
                  if deslocamento < inicio_pagina:
                      inicio_pagina = deslocamento
              if bloco.tipo == "imagem" or bloco.texto:
                  primeiro_da_pagina = False
              if bloco.tipo == "imagem":
                  if not mostrar_imagens:
                      continue
                  partes.append("\n" + MARCA_IMAGEM + "\n\n")
                  imagens.append((deslocamento + 1, bloco))
                  marcacoes.append((deslocamento, deslocamento + 4, "centro"))
                  deslocamento += 4
                  ultimo_texto = None
                  continue

              texto = bloco.texto
              if not texto:
                  continue
              if (preset.get("destacar_avisos") and bloco.tipo == "paragrafo"
                      and _RE_AVISO.match(texto)):
                  bloco_tipo = "aviso_caixa"
              else:
                  bloco_tipo = bloco.tipo

              tag = {
                  "titulo": f"titulo{max(1, min(3, bloco.nivel))}",
                  "aviso_caixa": "aviso_caixa",
                  "lista": "lista",
                  "citacao": "citacao",
                  "nota": "nota",
                  "codigo": "codigo",
                  "legenda": "legenda",
                  "aviso": "aviso",
              }.get(bloco_tipo, "paragrafo")

              # ---- artigo juridico: cabeca recolhivel com o corpo dentro
              if agrupa_artigos:
                  cabeca = juridico.artigos(texto[:60])
                  eh_cabeca = bool(cabeca) and cabeca[0]["tipo"] == "artigo" \
                      and cabeca[0]["inicio"] <= 2
                  if eh_cabeca:
                      # Indice POR PAGINA: e a chave do "recolhido", e precisa
                      # sobreviver ao trecho ser remontado a partir de outra pagina.
                      chave = (numero, sum(1 for a in faixas_artigo if a["pagina"] == numero))
                      artigo_atual = len(faixas_artigo)
                      faixas_artigo.append({
                          "rotulo": cabeca[0]["rotulo"], "offset": deslocamento,
                          "indice": chave, "pagina": numero,
                          "dobrado": chave in self._artigo_dobrado,
                      })
                  elif artigo_atual is not None and \
                          faixas_artigo[artigo_atual]["dobrado"]:
                      # Corpo de um artigo recolhido: some da pagina.
                      continue

              if bloco.tipo == "codigo":
                  indice_bloco = sum(1 for f in faixas_codigo if f["pagina"] == numero)
                  lingua = realce.detectar(texto, self.motor.linguagem_dominante)
                  dobrado = (numero, indice_bloco) in self._codigo_dobrado

                  # Cabecalho do bloco, como num editor: nome da linguagem a
                  # esquerda; os botoes de copiar e dobrar sao ancorados nele.
                  cabecalho = f"  {realce.nome_exibicao(lingua).upper()}\n"
                  partes.append(cabecalho)
                  marcacoes.append((deslocamento, deslocamento + len(cabecalho),
                                    "cod_cabecalho"))
                  inicio_cabecalho = deslocamento
                  deslocamento += len(cabecalho)

                  corpo = texto
                  if dobrado:
                      linhas_totais = corpo.count("\n") + 1
                      corpo = (corpo.split("\n")[0].rstrip()
                               + f"   ⋯ ({linhas_totais} linhas)")
                  elif numerar_codigo:
                      corpo, faixas_num = codigo_util.numerar(texto)

                  trecho = corpo + "\n"
                  partes.append(trecho)
                  marcacoes.append((deslocamento, deslocamento + len(trecho), tag))
                  ultima_linha = corpo.rfind("\n") + 1
                  marcacoes.append((deslocamento + ultima_linha,
                                    deslocamento + len(trecho), "cod_fim"))

                  if not dobrado:
                      if numerar_codigo:
                          for ini, fim in faixas_num:
                              marcacoes.append((deslocamento + ini, deslocamento + fim,
                                                "cod_numero_linha"))
                          mapa = codigo_util.mapear(texto)
                      else:
                          mapa = None
                      # A linguagem dominante do livro socorre trechos curtos
                      # demais para se identificarem sozinhos.
                      for ini, fim, classe in realce.tokenizar(
                              texto, lingua=lingua,
                              dominante=self.motor.linguagem_dominante):
                          if mapa is not None:
                              ini, fim = mapa[ini], mapa[min(fim, len(mapa) - 1)]
                          marcacoes.append((deslocamento + ini, deslocamento + fim,
                                            f"cod_{classe}"))

                  faixas_codigo.append({
                      "cabecalho": inicio_cabecalho, "texto": texto,
                      "lingua": lingua, "indice": (numero, indice_bloco),
                      "pagina": numero, "dobrado": dobrado,
                      "dobravel": bool(codigo_util.regioes_dobraveis(texto))
                                   or texto.count("\n") >= 4,
                      # Faixa do bloco inteiro, cabecalho incluso: e o que
                      # permite saber se o ponteiro esta em cima DESTE trecho.
                      "inicio": inicio_cabecalho,
                      "fim": deslocamento + len(trecho),
                  })
                  deslocamento += len(trecho)
                  ultimo_texto = None
                  continue

              trecho = texto + "\n"
              partes.append(trecho)
              marcacoes.append((deslocamento, deslocamento + len(trecho), tag))

              if usa_mono:
                  # Mencoes a codigo no meio da prosa ("use o list_display").
                  for inicio, fim in bloco.mono:
                      marcacoes.append((deslocamento + inicio, deslocamento + fim,
                                        "mono_inline"))

              deslocamento += len(trecho)
              ultimo_texto = (bloco,)
            regioes.append((numero, inicio_pagina, deslocamento))

        conteudo = "".join(partes)
        if not conteudo.strip():
            conteudo = "\nEsta pagina nao contem texto extraivel.\n"
            marcacoes = [(0, len(conteudo), "aviso")]
            regioes = [(self.pagina_atual, 0, len(conteudo))]
        self._regioes = regioes
        self._trecho = paginas

        self.imagens_render.clear()
        self._dados_img.clear()
        self.area.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", conteudo)
        self._texto_render = conteudo

        # `tag add` aceita varios pares de indice de uma vez. Uma chamada por
        # intervalo somava ~570 idas ao Tcl por pagina (o maior custo da
        # virada); agrupando por tag sobram poucas dezenas.
        por_tag = {}
        for inicio, fim, tag in marcacoes:
            faixa = por_tag.setdefault(tag, [])
            faixa.append(f"1.0+{inicio}c")
            faixa.append(f"1.0+{fim}c")
        for tag, indices in por_tag.items():
            self.txt.tag_add(tag, *indices)

        largura_img, altura_img = self._limites_da_figura()
        for offset, bloco_img in imagens:
            self._inserir_imagem(offset, bloco_img, largura_img, tamanho,
                                 altura_img)

        self._aplicar_grifos()
        self._destacar_ocorrencias_busca()
        if self.cfg.obter_bool("justificar", True):
            try:
                self._justificar()
            except Exception as erro:
                print(f"[Leitor] Justificacao falhou: {erro}")
        self.area.configure(state="disabled")
        self._acumulado_wheel = 0
        self._grade_folhas = []
        pelo_fim = self._entrar_pelo_fim
        self._entrar_pelo_fim = False
        if ancora and self._regiao(ancora[0]):
            self._posicionar(self._regiao(ancora[0])[1] + ancora[1], ancora[2])
        elif pelo_fim:
            self._mostrar_fim_da_pagina(self.pagina_atual)
        else:
            self._posicionar(self._regiao(self.pagina_atual)[1])

        self._atualizar_rodape()
        self._atualizar_estado_botoes()
        self._ajustar_coluna()
        self._esconder_barra_selecao()
        self._estado_setas = None
        self._faixas_codigo = faixas_codigo
        self._faixas_artigo = faixas_artigo
        # Pagina nova: a faixa apontada era de um bloco que nao existe mais.
        self._bloco_apontado = None
        self._cancelar_saida_do_bloco()
        self.after_idle(self._atualizar_setas)
        self.after_idle(self._ancorar_botoes_de_codigo)

        if self.painel_aberto in ("Sumario", "Marcadores", "Notas"):
            self._preencher_painel(self.painel_aberto)

        # Pre-aquece a pagina seguinte fora do caminho critico do desenho.
        self.after(90, self._pre_carregar_vizinha)

    def _renderizar_pagina_inteira(self, bloco=None, preparar=True):
        """Modo HQ/revista: uma imagem ocupando a largura util.

        Recebe `bloco` pronto quando quem chama e a leitura quadro a quadro --
        ali a imagem e um painel recortado, nao a pagina inteira. Nesse caso
        `preparar` vem falso: os quadros ja estao em maos, e pedi-los de novo
        faria a deteccao chamar a exibicao que a chamou.
        """
        if bloco is None:
            bloco = self.motor.pagina_inteira(self.pagina_atual)
        self.blocos_render = [bloco] if bloco else []
        self.imagens_render.clear()
        self._dados_img.clear()
        self._faixas_codigo = []
        self._faixas_artigo = []
        self._ancorar_botoes_de_codigo()

        self.area.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(padx=ESP["sm"], pady=ESP["sm"])
        if bloco is None:
            self.txt.insert("1.0", "\nNao foi possivel renderizar esta pagina.\n")
        else:
            self.txt.insert("1.0", MARCA_IMAGEM)
            try:
                largura = max(160, self.area.winfo_width() - 2 * ESP["sm"] - 20)
            except Exception:
                largura = 700
            self._inserir_imagem(0, bloco, largura, 0)
        self.area.configure(state="disabled")
        self.txt.yview_moveto(0.0)

        self._atualizar_rodape()
        self._atualizar_estado_botoes()
        self._esconder_barra_selecao()
        self._estado_setas = None
        self.after_idle(self._atualizar_setas)
        if preparar:
            self._preparar_quadros()

    # ================================================ LEITURA QUADRO A QUADRO
    @property
    def quadro_a_quadro(self):
        """Se a HQ deve ser lida painel por painel, e nao pagina por pagina.

        Numa tela dividida a pagina inteira de um manga fica com os baloes
        pequenos demais para ler. Um quadro por vez ocupa a coluna toda.
        """
        return (self.render_livro == "hq"
                and self.cfg.obter_bool("quadro_a_quadro", False))

    def alternar_quadro_a_quadro(self, _=None):
        if self.render_livro != "hq":
            C.Toast.mostrar(self, "So vale para livros em modo HQ / Manga.",
                            "info", 1800)
            return
        ativo = not self.cfg.obter_bool("quadro_a_quadro", False)
        self.cfg.definir_bool("quadro_a_quadro", ativo)
        self._quadro_atual = -1
        self.renderizar()
        C.Toast.mostrar(self, "Leitura quadro a quadro ligada." if ativo
                        else "Voltando a pagina inteira.", "ok", 1600)

    def _preparar_quadros(self):
        """Descobre os quadros da pagina SEM travar a leitura.

        A deteccao renderiza a pagina e projeta as faixas claras: algumas
        centenas de milissegundos. Feita na thread da interface, cada virada de
        pagina daria um engasgo visivel -- entao a pagina inteira aparece na
        hora e os quadros substituem a imagem quando ficam prontos.
        """
        if self.render_livro != "hq" or not self.documento:
            return
        if self._quadros_de == self.pagina_atual:
            self._aplicar_quadro()
            return

        self._geracao_quadros += 1
        geracao = self._geracao_quadros
        numero = self.pagina_atual
        caminho = (self.livro or {}).get("caminho_pdf")
        if not caminho:
            return

        def trabalhador():
            achados = []
            try:
                # Documento proprio: o fitz.Document nao e seguro para uso
                # simultaneo em duas threads, e a interface segue lendo o dela.
                with abrir_livro(caminho) as copia:
                    achados = quadros.detectar_quadros(copia, numero)
            except Exception as erro:
                print(f"[Quadros] Falha na deteccao: {erro}")
            self._na_ui(lambda: self._quadros_prontos(geracao, numero, achados))

        threading.Thread(target=trabalhador, daemon=True).start()

    def _quadros_prontos(self, geracao, numero, achados):
        # A pessoa pode ter virado a pagina enquanto a deteccao rodava.
        if geracao != self._geracao_quadros or numero != self.pagina_atual:
            return
        self._quadros = achados
        self._quadros_de = numero
        if achados:
            itens = [{"rotulo": f"Quadro {i + 1}", "detalhe": "",
                      "pagina": numero, "quadro": i}
                     for i in range(len(achados))]
            self.registrar_extra("Quadros", itens)
        elif self.extras.get("Quadros"):
            self.registrar_extra("Quadros", [])
        self._aplicar_quadro()

    def _aplicar_quadro(self):
        """Troca a pagina inteira pelo quadro corrente, se for o caso."""
        if not self.quadro_a_quadro or not self._quadros:
            return
        if self._quadro_atual == -2:
            # Chegamos aqui voltando: a leitura continua do fim da pagina.
            self._quadro_atual = len(self._quadros) - 1
        elif self._quadro_atual < 0:
            self._quadro_atual = 0
        self.mostrar_quadro(self._quadro_atual)

    def mostrar_quadro(self, indice):
        if not self._quadros:
            return
        indice = max(0, min(int(indice), len(self._quadros) - 1))
        self._quadro_atual = indice
        try:
            dados = quadros.recortar_quadro(self.documento, self.pagina_atual,
                                            self._quadros[indice])
        except Exception as erro:
            print(f"[Quadros] Falha ao recortar: {erro}")
            return
        self._renderizar_pagina_inteira(Bloco("imagem", imagem=dados),
                                        preparar=False)
        self._atualizar_rodape()

    def _andar_quadro(self, direcao) -> bool:
        """Move um quadro. Devolve False quando a pagina precisa virar."""
        if not self.quadro_a_quadro or not self._quadros:
            return False
        destino = self._quadro_atual + direcao
        if destino < 0 or destino >= len(self._quadros):
            # Ao virar, a leitura entra pelo lado certo: indo para frente,
            # comeca no primeiro quadro; voltando, no ultimo.
            self._entrada_quadro = "inicio" if direcao > 0 else "fim"
            return False
        self.mostrar_quadro(destino)
        return True

    def _configurar_tags(self, familia, tamanho, altura_linha, recuo):
        # Sao ~25 chamadas ao Tcl, e nada disso muda entre duas paginas do
        # mesmo livro com a mesma tipografia. Refazer so quando algo mudou.
        assinatura = (familia, tamanho, altura_linha, recuo, T.nome,
                      id(T._cores), self.render_livro)
        if assinatura == self._assinatura_tags:
            return
        self._assinatura_tags = assinatura

        # O Tk SOMA o spacing2 a altura natural da fonte (~1,25 em). Calcular
        # "tamanho * (altura - 1)" dava uma entrelinha efetiva de ~1,9 quando
        # se pedia 1,6 -- o texto ficava espalhado e os paragrafos sumiam.
        try:
            natural = tkfont.Font(family=familia, size=tamanho).metrics("linespace")
        except Exception:
            natural = int(tamanho * 1.6)
        em = natural / 1.25
        entre_linhas = max(0, int(round(em * altura_linha - natural)))
        # Respiro entre paragrafos MAIOR que a entrelinha: e o que faz o olho
        # ver onde um paragrafo termina. Com recuo de primeira linha (prosa
        # de livro), o recuo ja marca a divisao e o respiro pode ser menor.
        respiro = max(6, int(em * (0.35 if recuo else 0.85)))
        margem_lista = max(18, int(tamanho * 1.4))
        recuo_primeira = max(16, int(em * 1.6))

        self.txt.tag_configure("paragrafo", font=(familia, tamanho),
                               spacing1=respiro, spacing2=entre_linhas,
                               spacing3=0, lmargin1=recuo_primeira if recuo else 0,
                               lmargin2=0, foreground=T.papel_texto)
        escala = self.preset.get("escala_titulo", 1.0)
        self.txt.tag_configure("titulo1", font=(familia, int(tamanho * 1.65 * escala), "bold"),
                               spacing1=int(tamanho * 2.0), spacing2=entre_linhas,
                               spacing3=int(tamanho * 0.5), foreground=T.papel_titulo)
        self.txt.tag_configure("titulo2", font=(familia, int(tamanho * 1.3 * escala), "bold"),
                               spacing1=int(tamanho * 1.5), spacing2=entre_linhas,
                               spacing3=int(tamanho * 0.4), foreground=T.papel_titulo)
        self.txt.tag_configure("titulo3", font=(familia, int(tamanho * 1.08 * escala), "bold"),
                               spacing1=int(tamanho * 1.2), spacing2=entre_linhas,
                               spacing3=int(tamanho * 0.3), foreground=T.papel_titulo)
        # Linhas 2..n de um paragrafo justificado: viram linhas logicas
        # proprias, entao precisam desfazer o recuo e o respiro de paragrafo
        # (que o Tk aplicaria a cada uma) e usar a entrelinha no lugar.
        self.txt.tag_configure("par_cont", spacing1=entre_linhas, lmargin1=0,
                               spacing3=0)
        self.txt.tag_configure("lista_cont", spacing1=entre_linhas,
                               lmargin1=margem_lista + 12, spacing3=0)
        self._margem_lista = margem_lista
        self._entre_linhas = entre_linhas
        self.txt.tag_configure("lista", font=(familia, tamanho),
                               spacing1=max(4, int(em * 0.45)), spacing2=entre_linhas,
                               lmargin1=margem_lista, lmargin2=margem_lista + 12,
                               foreground=T.papel_texto)
        self.txt.tag_configure("citacao", font=(familia, tamanho, "italic"),
                               spacing1=respiro, spacing2=entre_linhas,
                               lmargin1=margem_lista + 8, lmargin2=margem_lista + 8,
                               rmargin=margem_lista, foreground=T.texto_suave)
        # Codigo mantem as quebras originais e ganha fundo proprio para se
        # separar da prosa; a fonte menor reduz a quebra de linhas longas.
        # spacing1/3 do Tk valem para CADA linha: no codigo, cada linha tem
        # "\n" proprio, entao o respiro do bloco ficava entre todas as linhas
        # (codigo em espaco duplo). O respiro vai no cabecalho e na ultima
        # linha (tag cod_fim); as linhas do meio ficam coladas, como num editor.
        respiro_bloco = max(8, int(em * 0.9))
        self.txt.tag_configure("codigo", font=("Consolas", max(8, int(tamanho * 0.82))),
                               spacing1=1, spacing2=1, spacing3=1,
                               # Linha longa quebra com recuo de ~4 colunas: le-se
                               # como continuacao, como no word wrap dos editores.
                               lmargin1=margem_lista,
                               lmargin2=margem_lista + max(14, int(tamanho * 1.9)),
                               rmargin=ESP["sm"], background=T.superficie_alt,
                               foreground=T.texto)
        self.txt.tag_configure("cod_fim", spacing3=max(8, int(em * 0.6)))
        self.txt.tag_configure("cod_cabecalho",
                               font=(FONTE_UI, max(7, int(tamanho * 0.58)), "bold"),
                               background=T.superficie_alt, foreground=T.texto_suave,
                               lmargin1=margem_lista, lmargin2=margem_lista,
                               rmargin=ESP["sm"], spacing1=respiro_bloco, spacing3=4)
        self.txt.tag_configure("cod_numero_linha",
                               font=("Consolas", max(7, int(tamanho * 0.74))),
                               foreground=T.texto_suave)
        self.txt.tag_configure("nota", font=(familia, max(8, int(tamanho * 0.85))),
                               spacing1=int(respiro * 0.6), spacing2=max(1, entre_linhas - 2),
                               foreground=T.texto_suave)
        # Legenda pertence a figura, nao ao texto: colada nela por cima,
        # centralizada e menor, para o olho nao a confundir com o corpo.
        self.txt.tag_configure("legenda", font=(familia, max(8, int(tamanho * 0.86)), "italic"),
                               spacing1=max(2, int(respiro * 0.3)),
                               spacing2=max(1, entre_linhas - 2), spacing3=respiro,
                               justify="center", lmargin1=margem_lista,
                               lmargin2=margem_lista, rmargin=margem_lista,
                               foreground=T.texto_suave)
        self.txt.tag_configure("aviso", font=(FONTE_UI, max(9, tamanho - 4), "italic"),
                               justify="center", spacing1=respiro, spacing3=respiro,
                               foreground=T.texto_suave)
        self.txt.tag_configure("centro", justify="center")

        # ---- realce de sintaxe dentro dos blocos de codigo
        corpo_cod = max(8, int(tamanho * 0.82))
        for classe, cor in (("comentario", T.cod_comentario), ("texto", T.cod_texto),
                            ("numero", T.cod_numero), ("palavra", T.cod_palavra),
                            ("funcao", T.cod_funcao), ("decorador", T.cod_funcao)):
            self.txt.tag_configure(f"cod_{classe}", foreground=cor)
        self.txt.tag_configure("cod_comentario", foreground=T.cod_comentario,
                               font=("Consolas", corpo_cod, "italic"))
        self.txt.tag_configure("cod_palavra", foreground=T.cod_palavra,
                               font=("Consolas", corpo_cod, "bold"))

        # ---- avisos de manual ("Atencao:", "Importante:", "Cuidado:")
        self.txt.tag_configure("aviso_caixa", font=(familia, tamanho),
                               spacing1=respiro, spacing2=entre_linhas,
                               spacing3=respiro,
                               lmargin1=margem_lista, lmargin2=margem_lista,
                               rmargin=ESP["sm"],
                               background=T.superficie_alt, foreground=T.papel_titulo)

        # ---- mencao a codigo no meio de um paragrafo
        # Sem fundo: o Tk pinta o fundo da tag tambem sobre o espaco ENTRE as
        # linhas, e cada palavra de codigo virava um bloco alto e torto no
        # meio do paragrafo. Fonte mono + cor ja separam o codigo da prosa.
        self.txt.tag_configure("mono_inline", font=("Consolas", max(8, int(tamanho * 0.88))),
                               foreground=T.cod_palavra)

        # Continuacao de linha justificada precisa vencer as margens/respiros
        # de "paragrafo" e "lista" (o Tk resolve conflito pela prioridade).
        self.txt.tag_raise("par_cont")
        self.txt.tag_raise("lista_cont")
        self.txt.tag_configure("grifo", background=T.grifo, foreground=T.grifo_texto)
        self.txt.tag_configure("achado", background=T.busca, foreground=T.busca_texto)
        for classe in ("comentario", "texto", "numero", "palavra",
                       "funcao", "decorador"):
            self.txt.tag_raise(f"cod_{classe}")
        self.txt.tag_raise("cod_numero_linha")
        self.txt.tag_raise("mono_inline")
        self.txt.tag_raise("grifo")
        self.txt.tag_raise("achado")
        self.txt.tag_raise("sel")

    def _tingir(self, imagem, cor_hex):
        """Usa o desenho em cinza como mascara e o pinta com a cor do tema.

        Um recorte cru do PDF viria como tinta escura sobre papel branco --
        um retangulo claro gritando no meio de uma pagina escura. Aqui o
        branco vira transparente e o traco assume a cor do texto do tema.
        """
        cinza = imagem.convert("L")
        alfa = cinza.point(lambda v: 255 - v)          # tinta = opaco
        cor = tuple(int(cor_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        tingida = Image.new("RGBA", imagem.size, cor + (0,))
        tingida.putalpha(alfa)
        return tingida

    def _inserir_imagem(self, offset, bloco_img, largura_max, tamanho_fonte,
                        altura_max=0):
        dados = bloco_img.imagem
        try:
            imagem = Image.open(io.BytesIO(dados))

            if bloco_img.recolorir:
                imagem = self._tingir(imagem, T.papel_titulo)
                # Acompanha a tipografia escolhida: a altura original em pontos
                # e reexpressa em multiplos do corpo atual.
                fator = 1.35 if self.preset.get("formulas_maiores") else 1.05
                alvo_altura = max(12, int(bloco_img.altura_em_corpos * tamanho_fonte * fator))
                proporcao = alvo_altura / max(1, imagem.height)
                nova_largura = max(1, int(imagem.width * proporcao))
                if nova_largura > largura_max:
                    proporcao = largura_max / float(imagem.width)
                    nova_largura, alvo_altura = int(largura_max), max(1, int(imagem.height * proporcao))
                imagem = imagem.resize((nova_largura, alvo_altura), Image.Resampling.LANCZOS)
            elif imagem.mode not in ("RGB", "RGBA", "L"):
                imagem = imagem.convert("RGB")

            if imagem.width > largura_max:
                proporcao = largura_max / float(imagem.width)
                imagem = imagem.resize(
                    (int(largura_max), max(1, int(imagem.height * proporcao))),
                    Image.Resampling.LANCZOS)
            # Teto de altura: uma figura de pagina inteira ocupava tres telas
            # e o leitor rolava as cegas ate reencontrar a frase que estava
            # lendo. Encolhida, ela cabe na tela junto com o texto ao redor --
            # e continua abrindo em tamanho real com um clique.
            if altura_max and imagem.height > altura_max:
                proporcao = altura_max / float(imagem.height)
                imagem = imagem.resize(
                    (max(1, int(imagem.width * proporcao)), int(altura_max)),
                    Image.Resampling.LANCZOS)
            foto = ImageTk.PhotoImage(imagem)
            self.imagens_render.append(foto)      # evita coleta pelo garbage collector

            indice = f"1.0+{offset}c"
            self.txt.delete(indice, f"1.0+{offset + 1}c")
            self.txt.image_create(indice, image=foto)
            nome = f"img{offset}"
            self.txt.tag_add(nome, indice, f"1.0+{offset + 1}c")
            self.txt.tag_add("imagem", indice, f"1.0+{offset + 1}c")
            # Os bytes ficam num dicionario limpo a cada pagina. Antes cada
            # render fazia tag_bind com um lambda segurando os bytes: o Tcl
            # nunca libera o comando antigo e um livro ilustrado acumulava
            # centenas de MB.
            self._dados_img[nome] = dados
            self._ligar_clique_imagem()
        except Exception as erro:
            print(f"[Leitor] Falha ao renderizar imagem: {erro}")

    # ------------------------------------------------------- justificacao
    def _texto_redimensionado(self, evento=None):
        """A area de texto mudou de largura depois da justificacao.

        Acontece logo apos o render, quando a barra de rolagem surge e rouba
        alguns pixels: a ultima palavra de cada linha justificada nao cabia
        mais e descia sozinha para uma linha propria.
        """
        if self._em_folha() and evento is not None:
            altura = self.txt.winfo_height()
            if altura != getattr(self, "_altura_folhas", None):
                self._altura_folhas = altura
                self._agendar_realinhar_folha()
        if (self._largura_justificada is None or not self.documento
                or not self.cfg.obter_bool("justificar", True)):
            return
        if abs(self.txt.winfo_width() - self._largura_justificada) < 2:
            return
        if self._agendado_justificar:
            try:
                self.after_cancel(self._agendado_justificar)
            except Exception:
                pass
        self._agendado_justificar = self.after(
            60, lambda: (setattr(self, "_agendado_justificar", None),
                         self.renderizar(manter_rolagem=True)))

    def _justificar(self):
        """Alinha os paragrafos nas DUAS margens, como num livro.

        O Tk 8.6 so alinha a esquerda, ao centro ou a direita. Aqui a quebra
        de linha e calculada com as metricas reais da fonte (inclusive dos
        trechos de codigo no meio da frase); cada linha vira uma linha logica
        e os espacos dela viram TABs, com paradas de tabulacao exatas para a
        linha encostar na margem direita. A troca e um caractere por outro:
        offsets de grifo, busca e posicao salva continuam valendo. A ultima
        linha do paragrafo fica alinhada a esquerda, como manda a tipografia.
        """
        txt = self.txt
        for nome in txt.tag_names():
            if nome.startswith("jl"):
                txt.tag_delete(nome)
        try:
            largura = (txt.winfo_width() - 2 * int(float(txt.cget("padx")))
                       - 2 * int(float(txt.cget("borderwidth")))
                       - 2 * int(float(txt.cget("highlightthickness"))) - 4)
        except Exception:
            return
        # Largura do widget usada nesta justificacao: se ela mudar depois
        # (a barra de rolagem aparece so quando o texto transborda), a
        # justificacao e refeita em _texto_redimensionado.
        self._largura_justificada = txt.winfo_width()
        if largura < 120:
            return

        fontes = {}

        def fonte_da_tag(tag):
            if tag not in fontes:
                try:
                    fontes[tag] = tkfont.Font(font=txt.tag_cget(tag, "font"))
                except Exception:
                    fontes[tag] = None
            return fontes[tag]

        contador = [0]
        alvos = []
        for tag, tag_cont in (("paragrafo", "par_cont"), ("lista", "lista_cont")):
            faixas = txt.tag_ranges(tag)
            for i in range(0, len(faixas), 2):
                inicio, fim = str(faixas[i]), str(faixas[i + 1])
                # A faixa pode cobrir varios paragrafos vizinhos: um por linha.
                linha_ini = int(inicio.split(".")[0])
                linha_fim = int(txt.index(f"{fim} -1c").split(".")[0])
                alvos += [(numero, tag, tag_cont)
                          for numero in range(linha_ini, linha_fim + 1)]
        # De BAIXO para cima: justificar um paragrafo acrescenta linhas e
        # empurraria a numeracao de todos os que vem depois dele.
        mono = fonte_da_tag("mono_inline")
        for numero, tag, tag_cont in sorted(set(alvos), reverse=True):
            corpo = fonte_da_tag(tag)
            if corpo is None:
                continue
            self._justificar_linha(numero, tag, tag_cont, corpo, mono or corpo,
                                   largura, contador)

    def _justificar_linha(self, numero, tag, tag_cont, corpo, mono, largura, contador):
        txt = self.txt
        texto = txt.get(f"{numero}.0", f"{numero}.end")
        if " " not in texto.strip() or len(texto) < 20:
            return
        # Quais caracteres estao em fonte de codigo (mencoes inline).
        em_mono = bytearray(len(texto))
        faixa = txt.tag_nextrange("mono_inline", f"{numero}.0", f"{numero}.end")
        while faixa:
            a = int(str(faixa[0]).split(".")[1])
            b = int(str(faixa[1]).split(".")[1]) if str(faixa[1]).split(".")[0] == str(numero) else len(texto)
            for k in range(a, min(b, len(texto))):
                em_mono[k] = 1
            faixa = txt.tag_nextrange("mono_inline", faixa[1], f"{numero}.end")

        def medir(a, b):
            total, k = 0, a
            while k < b:
                j = k
                while j < b and em_mono[j] == em_mono[k]:
                    j += 1
                total += (mono if em_mono[k] else corpo).measure(texto[k:j])
                k = j
            return total

        # Palavras (posicao, largura); o texto do paragrafo ja vem com
        # espacos simples.
        palavras = []
        k = 0
        while k < len(texto):
            if texto[k] == " ":
                k += 1
                continue
            j = texto.find(" ", k)
            j = len(texto) if j < 0 else j
            palavras.append((k, j, medir(k, j)))
            k = j
        if len(palavras) < 2:
            return
        espaco = corpo.measure(" ")

        try:
            margem1 = int(float(txt.tag_cget(tag, "lmargin1") or 0))
        except Exception:
            margem1 = 0
        if tag == "lista":
            # Linhas seguintes do item alinham com o texto depois do marcador.
            marcador = texto.find(" ") + 1
            recuo_cont = margem1 + medir(0, marcador) if 0 < marcador < 6 else margem1 + 12
            txt.tag_configure("lista_cont", lmargin1=recuo_cont)
        else:
            recuo_cont = 0

        # Quebra gulosa, igual a do Tk (palavra que nao cabe desce inteira).
        linhas, atual, x = [], [], 0
        for palavra in palavras:
            disponivel = largura - (margem1 if not linhas else recuo_cont)
            w = palavra[2]
            if atual and x + espaco + w > disponivel:
                linhas.append(atual)
                atual, x = [palavra], w
            else:
                x = w if not atual else x + espaco + w
                atual.append(palavra)
        linhas.append(atual)
        if len(linhas) < 2:
            return

        # Fim de cada linha: o espaco depois da ultima palavra vira quebra.
        # Da DIREITA para a esquerda: cada quebra parte a linha logica, e as
        # colunas a esquerda dela continuam validas.
        for linha in reversed(linhas[:-1]):
            self._trocar_caractere(numero, linha[-1][1], "\n")
        # As trocas acima partiram a linha logica em varias: agora cada
        # linha visual tem numero proprio, de `numero` em diante.
        for indice_linha, linha in enumerate(linhas):
            linha_tk = numero + indice_linha
            if indice_linha > 0:
                txt.tag_add(tag_cont, f"{linha_tk}.0", f"{linha_tk}.end +1c")
            if indice_linha == len(linhas) - 1 or len(linha) < 2:
                continue
            disponivel = largura - (margem1 if indice_linha == 0 else recuo_cont)
            natural = sum(p[2] for p in linha)
            sobra = disponivel - natural
            vaos = len(linha) - 1
            # Linha frouxa demais (palavra longa empurrada para baixo): melhor
            # deixar a esquerda que abrir buracos no meio da frase.
            # Ate ~5 espacos por vao ainda le bem; acima disso o buraco
            # incomoda mais que a margem irregular. Em coluna estreita (meia
            # tela) o limite antigo, 4, deixava linha demais sem justificar.
            if sobra / vaos > espaco * 5.5:
                continue
            base = 0 if indice_linha == 0 else linha[0][0]
            # As paradas de tab do Tk contam da borda da coluna, nao do recuo
            # da linha: o recuo entra na conta (sem isso a primeira linha do
            # paragrafo saia espremida).
            recuo = margem1 if indice_linha == 0 else recuo_cont
            paradas, x = [], recuo + linha[0][2]
            for k in range(1, len(linha)):
                x += sobra / vaos
                paradas.append(int(round(x)))
                x += linha[k][2]
            nome = f"jl{contador[0]}"
            contador[0] += 1
            txt.tag_configure(nome, tabs=tuple(paradas))
            # Os espacos internos desta linha viram TAB.
            for k in range(1, len(linha)):
                self._trocar_caractere(linha_tk, linha[k][0] - 1 - base, "\t")
            txt.tag_add(nome, f"{linha_tk}.0", f"{linha_tk}.end")
            txt.tag_raise(nome)

    def _trocar_caractere(self, numero, coluna, novo):
        """Troca 1 caractere por outro preservando as tags dele."""
        indice = f"{numero}.{coluna}"
        tags = tuple(t for t in self.txt.tag_names(indice) if t != "sel")
        self.txt.delete(indice)
        self.txt.insert(indice, novo, tags)

    def _ligar_clique_imagem(self):
        if getattr(self, "_clique_img_ligado", False):
            return
        self._clique_img_ligado = True

        def clicar(evento):
            nomes = self.txt.tag_names(f"@{evento.x},{evento.y}")
            nome = next((t for t in nomes if t.startswith("img")), None)
            dados = self._dados_img.get(nome) if nome else None
            if dados:
                Modais.abrir_zoom_imagem(self, dados)

        self.txt.tag_bind("imagem", "<Button-1>", clicar)
        self.txt.tag_bind("imagem", "<Enter>", lambda e: self.txt.configure(cursor="hand2"))
        self.txt.tag_bind("imagem", "<Leave>", lambda e: self.txt.configure(cursor="arrow"))

    def _limites_da_figura(self):
        """(largura, altura) maximas de uma figura dentro da coluna de leitura.

        A largura vem da coluna, e nao da janela: figura mais larga que o texto
        quebra o eixo de leitura e obriga o olho a voltar duas vezes por linha.
        """
        largura = altura = 0
        try:
            largura = self.area.winfo_width()
            altura = self.area.winfo_height()
        except Exception:
            pass
        if largura <= 50:
            # Primeira pintura: o widget ainda nao foi disposto e mede 1 px.
            # Sem forcar o layout aqui, TODA figura da primeira pagina aberta
            # ficava congelada no tamanho minimo ate a proxima renderizacao.
            try:
                self.update_idletasks()
                largura = self.area.winfo_width()
                altura = self.area.winfo_height()
            except Exception:
                pass
        if largura <= 50:
            largura, altura = 760, 620
        return (max(160, largura - 2 * self._margem_atual() - 30),
                max(180, int((altura or 620) * FRACAO_ALTURA_FIGURA)))

    # ------------------------------------------------------------ tipografia
    def _largura_do_caractere(self, familia, tamanho):
        """Largura media de caractere, memorizada.

        Criar um objeto de fonte e medi-lo custa alguns milissegundos; isso era
        refeito duas ou tres vezes por virada de pagina.
        """
        chave = (familia, tamanho)
        if chave not in self._cache_char:
            try:
                fonte = tkfont.Font(family=familia, size=tamanho)
                self._cache_char[chave] = fonte.measure("abcdefghijklmnopqrstuvwxyz ") / 27.0
            except Exception:
                self._cache_char[chave] = tamanho * 0.55
        return self._cache_char[chave]

    def _margem_atual(self):
        """Margem lateral que limita a coluna de texto a N caracteres.

        Nao chama `update_idletasks` aqui: forcar um passe de layout a cada
        consulta custava ~22 ms e era o maior peso da virada de pagina. Quem
        redimensiona a janela sincroniza uma vez, em `_reagir_largura`.
        """
        try:
            largura = self.area.winfo_width()
        except Exception:
            largura = 0
        if largura <= 50:
            largura = 800

        familia = FONTES_LEITURA.get(self.pref("fonte_leitura"), "Georgia")
        tamanho = max(9, self.pref_int("tamanho_fonte", TAMANHO_FONTE_PADRAO))
        alvo_chars = max(LARGURA_COLUNA_MIN,
                         min(LARGURA_COLUNA_MAX, self.pref_int("largura_coluna", 68)))

        chave = (largura, familia, tamanho, alvo_chars)
        if chave == self._chave_margem:
            return self._margem_memo

        largura_char = self._largura_do_caractere(familia, tamanho)
        margem = (largura - alvo_chars * largura_char) / 2
        # Teto de seguranca: a coluna nunca pode ficar menor que ~28 caracteres,
        # aconteca o que acontecer com a medicao.
        teto = max(0, (largura - 28 * largura_char) / 2)
        resultado = int(max(ESP["md"], min(margem, teto)))

        self._chave_margem, self._margem_memo = chave, resultado
        return resultado

    def _ajustar_coluna(self):
        margem = self._margem_atual()
        try:
            self.txt.configure(padx=margem, pady=ESP["xl"])
        except Exception:
            pass

    # =====================================================================
    # NAVEGACAO
    # =====================================================================
    def ir_para_pagina(self, numero, salvar=True, registrar=True):
        if not self.documento:
            return
        numero = max(0, min(int(numero), self.total_paginas - 1))
        # Salto (nao virada de pagina): entra no historico para o Alt+<-.
        if registrar and abs(numero - self.pagina_atual) > 1:
            self._historico.append(self.pagina_atual)
            del self._historico[:-50]
            self._futuro = []
        if numero == self.pagina_atual:
            # Primeira/ultima pagina: a virada nao acontece, e a "entrada pelo
            # fim" pedida pelo _andar_quadro nao pode sobrar para o proximo salto.
            self._entrada_quadro = None
            return
        self._pausar_cronometro()
        self.pagina_atual = numero
        # Os quadros detectados sao da pagina anterior; ate a deteccao da nova
        # terminar, usar a lista velha recortaria a pagina nova no lugar errado.
        self._quadros = []
        self._quadros_de = None
        # Pagina nova, quadro novo: sem isto o indice do painel anterior
        # sobreviveria e a HQ abriria no meio da pagina seguinte.
        self._quadro_atual = -2 if self._entrada_quadro == "fim" else -1
        self._entrada_quadro = None
        if salvar:
            self.banco.definir_pagina(self.id_livro, numero)
        if self._usa_trecho() and self._regiao(numero) and self._trecho:
            # Ja esta montada no trecho: basta rolar ate ela.
            pelo_fim = self._entrar_pelo_fim
            self._entrar_pelo_fim = False
            if pelo_fim:
                self._mostrar_fim_da_pagina(numero)
            else:
                self._posicionar(self._regiao(numero)[1])
            self.blocos_render = self.motor.pagina(numero).blocos
            self._atualizar_rodape()
            self._atualizar_estado_botoes()
            if self.painel_aberto in ("Sumario", "Marcadores", "Notas"):
                self._preencher_painel(self.painel_aberto)
            self.after(120, self._verificar_pagina_no_topo)
        else:
            self.renderizar()
        self._retomar_cronometro()

    def proxima_pagina(self, _=None):
        # Numa HQ lida quadro a quadro, avancar so vira a pagina depois do
        # ultimo quadro -- e o mesmo gesto para as duas coisas.
        if self._andar_quadro(1):
            return
        self.ir_para_pagina(self.pagina_atual + 1)

    def pagina_anterior(self, _=None):
        if self._andar_quadro(-1):
            return
        self.ir_para_pagina(self.pagina_atual - 1)

    def _avancar(self, direcao):
        """Setas do teclado: folha no modo Folhear, pagina nos demais."""
        if self._em_folha() and not self.quadro_a_quadro:
            self._virar_folha(direcao)
        elif direcao > 0:
            self.proxima_pagina()
        else:
            self.pagina_anterior()

    def _seta_vertical(self, direcao):
        if self._em_folha():
            self._virar_folha(direcao)
        else:
            self.txt.yview_scroll(direcao * 3, "units")

    def _ir_pelo_campo(self):
        valor = self.entry_ir.get().strip()
        if valor.isdigit():
            self.ir_para_pagina(int(valor) - 1)
            self.entry_ir.delete(0, "end")

    def _pre_carregar_vizinha(self):
        if self.motor and self.pagina_atual + 1 < self.total_paginas:
            try:
                self.motor.pagina(self.pagina_atual + 1)
            except Exception:
                pass

    # ===================================================== MODOS DE VIRADA
    @property
    def modo_virada(self):
        return normalizar_modo_virada(
            self.cfg.obter("modo_virada", MODO_VIRADA_PADRAO))

    def aplicar_modo_virada(self):
        """Reconfigura os controles quando o modo muda."""
        modo = self.modo_virada
        self._mostrar_barra_rolagem(modo != MODO_FOLHA)
        if self._atualizar_duas_folhas() and self.documento:
            self.after_idle(lambda: self.renderizar(manter_rolagem=True))
        if modo == MODO_FOLHA:
            self.after_idle(self._folhear_daqui)
        else:
            self._esconder_mascara()
            self._remover_folga_final()
            self._grade_folhas = []
        if modo != MODO_AUTO:
            self.parar_rolagem()
            self.painel_auto.place_forget()
        else:
            self.painel_auto.place(relx=0.5, rely=0.985, anchor="s")
            self.painel_auto.lift()
            self._atualizar_rotulo_velocidade()
            self.iniciar_rolagem()
        self._estado_setas = None
        self._atualizar_setas()
        self.txt.configure(cursor="arrow")

    # ============================================================ TRECHO
    # Nos modos de leitura corrida (Rolagem continua, Folhear, Automatica) a
    # tela mostra um TRECHO de paginas seguidas, nao uma pagina so: o texto
    # flui de uma para a outra sem buraco no fim de cada pagina e sem a
    # palavra partida entre elas. Cada pagina continua com a sua REGIAO no
    # texto -- grifos, notas e progresso seguem amarrados a pagina, com os
    # mesmos offsets de antes. A "pagina atual" passa a ser a que esta no
    # topo da tela; perto da borda do trecho, ele e remontado em volta dela.
    _PAGINAS_ANTES = 1
    _PAGINAS_DEPOIS = 3

    def _folha_na_pagina(self):
        """(folha atual, total de folhas) DENTRO da pagina atual.

        A grade cobre o trecho inteiro; o leitor quer saber da pagina dele.
        Uma folha que comeca no fim da pagina anterior e termina nesta conta
        como a primeira desta.
        """
        grade = self._grade_folhas
        regiao = self._regiao(self.pagina_atual)
        if not grade or not regiao:
            return None
        _n, inicio, fim = regiao
        primeira = max((i for i, s in enumerate(grade) if s <= inicio), default=0)
        ultima = max((i for i, s in enumerate(grade) if s < fim), default=primeira)
        total = max(1, ultima - primeira + 1)
        atual = min(total, max(1, self._folha_atual - primeira + 1))
        return atual, total

    def _usa_trecho(self):
        return (self.modo_virada in (MODO_LIVRE, MODO_FOLHA, MODO_AUTO)
                and not self.preset.get("pagina_como_imagem")
                and not self.quadro_a_quadro)

    def _paginas_do_trecho(self):
        p = self.pagina_atual
        if not self._usa_trecho():
            return [p]
        inicio = max(0, p - self._PAGINAS_ANTES)
        fim = min(self.total_paginas - 1, p + self._PAGINAS_DEPOIS)
        return list(range(inicio, fim + 1))

    @staticmethod
    def _continua_paragrafo(anterior, bloco):
        """O primeiro bloco da pagina continua o ultimo da pagina anterior?"""
        if anterior.tipo not in ("paragrafo", "citacao") or bloco.tipo != anterior.tipo:
            return False
        texto = bloco.texto.lstrip()
        if not texto:
            return False
        if anterior.hifen_final:
            return texto[:1].islower()
        final = anterior.texto.rstrip()[-1:]
        return texto[:1].islower() and final not in ".!?:;\"'”»)"

    def _regiao(self, numero):
        for regiao in self._regioes:
            if regiao[0] == numero:
                return regiao
        return None

    def _pagina_do_offset(self, offset):
        """(pagina, inicio da regiao) que contem o offset global."""
        escolhida = self._regioes[0] if self._regioes else (self.pagina_atual, 0, 0)
        for regiao in self._regioes:
            if regiao[1] <= offset:
                escolhida = regiao
            else:
                break
        return escolhida[0], escolhida[1]

    def _posicao_no_topo(self):
        """(pagina, offset local, recuo em pixels) da linha no topo da tela."""
        topo, _base = self._limites_visiveis()
        indice = self.txt.index(f"@0,{topo + 1}")
        offset = self._para_offset(self.txt.index(f"{indice} display linestart"))
        info = self.txt.dlineinfo(indice)
        recuo = max(0, topo - info[1]) if info else 0
        pagina, inicio = self._pagina_do_offset(offset)
        return pagina, offset - inicio, recuo

    def _posicionar(self, offset, recuo=0):
        """Poe o caractere `offset` no topo da tela (folha comecando nele)."""
        if self._em_folha():
            self._montar_folhas(ancora=offset)
            return
        self.txt.yview(f"1.0+{int(offset)}c")
        if recuo:
            self.txt.yview_scroll(int(recuo), "pixels")

    def _mostrar_fim_da_pagina(self, numero):
        regiao = self._regiao(numero)
        if not regiao:
            return
        ultimo = f"1.0+{max(regiao[1], regiao[2] - 2)}c"
        if self._em_folha():
            self._montar_folhas(manter_indice=ultimo)
            return
        # `see` mede de verdade; yview_moveto(1.0) usa a ALTURA ESTIMADA e as
        # vezes parava a dois tercos da pagina.
        self.txt.see(ultimo)

    def _pode_estender(self, direcao):
        if not self._usa_trecho() or len(self._trecho) < 1:
            return False
        if direcao > 0:
            return self._trecho[-1] < self.total_paginas - 1
        return self._trecho[0] > 0

    def _recentrar(self):
        """Remonta o trecho em volta da pagina do topo, sem mexer na leitura."""
        self._agendado_recentrar = None
        if not self.documento or not self._usa_trecho():
            return
        self._verificar_pagina_no_topo(recentrar=False)
        self.renderizar(manter_rolagem=True)

    def _agendar_recentrar(self):
        # Nao reinicia o relogio a cada evento: na rolagem automatica a tela
        # nunca para, e um temporizador sempre adiado nunca dispararia.
        if self._agendado_recentrar is None:
            self._agendado_recentrar = self.after(450, self._recentrar)

    def _agendar_verificar_topo(self):
        if self._agendado_topo is None:
            self._agendado_topo = self.after(90, self._verificar_pagina_no_topo)

    def _verificar_pagina_no_topo(self, recentrar=True):
        """A pagina do topo da tela mudou? Ela vira a pagina atual."""
        self._agendado_topo = None
        if (not self.documento or len(self._trecho) < 2 or self._simulando
                or not self._regioes):
            return
        try:
            # Pelo FIM da primeira linha visivel: quando a pagina comeca no
            # meio de uma linha costurada ("...ou in|teligencia"), o inicio
            # dessa linha ainda e da pagina anterior.
            topo, _base = self._limites_visiveis()
            fim_linha = self.txt.index(f"@0,{topo + 1} display lineend")
            numero = self._pagina_do_offset(self._para_offset(fim_linha))[0]
        except Exception:
            return
        if numero != self.pagina_atual:
            self._pausar_cronometro()
            self.pagina_atual = numero
            self.blocos_render = self.motor.pagina(numero).blocos
            self.banco.definir_pagina(self.id_livro, numero)
            self._retomar_cronometro()
            self._atualizar_rodape()
            self._atualizar_estado_botoes()
            if self.painel_aberto in ("Sumario", "Marcadores", "Notas"):
                self._preencher_painel(self.painel_aberto)
        if not recentrar:
            return
        posicao = self._trecho.index(numero) if numero in self._trecho else -1
        perto_do_fim = posicao >= len(self._trecho) - 2 and self._pode_estender(1)
        perto_do_inicio = posicao == 0 and self._pode_estender(-1)
        if perto_do_fim or perto_do_inicio:
            self._agendar_recentrar()

    # ========================================================== FOLHEAR
    # O texto cabe na tela e vira por folhas, como num livro. A pagina do PDF
    # continua sendo a unidade de progresso; dentro dela, a "grade" guarda em
    # que CARACTERE cada folha comeca. A folha seguinte comeca exatamente na
    # primeira linha que nao coube inteira -- nada se perde entre folhas, e
    # nada aparece pela metade: a mascara da cor do papel cobre a linha
    # cortada no pe da folha.
    #
    # Por que caractere e nao pixel: a justificacao troca espacos por quebras
    # e refaz a pagina sempre que a largura muda (a barra de rolagem some, a
    # janela encolhe). Pixels medidos antes disso ficavam velhos e a folha 2
    # repetia o fim da folha 1; o caractere aponta sempre para a mesma frase.
    def _em_folha(self):
        return self.modo_virada == MODO_FOLHA and self.documento is not None

    def _mostrar_barra_rolagem(self, mostrar):
        area = self.area
        try:
            if mostrar and not area._scrollbars_activated:
                area._scrollbars_activated = True
                area._check_if_scrollbars_needed()
            elif not mostrar:
                area._scrollbars_activated = False
                area._y_scrollbar.grid_forget()
        except Exception:
            pass

    def _limites_visiveis(self):
        t = self.txt
        try:
            borda = int(float(t.cget("borderwidth"))) + int(float(t.cget("highlightthickness")))
            pady = int(float(str(t.cget("pady"))))
        except Exception:
            borda, pady = 0, 0
        topo = borda + pady
        return topo, max(topo + 20, t.winfo_height() - borda - pady)

    def _fim_visivel(self):
        """O fim da pagina esta inteiro na tela? Medido, nao estimado.

        As fracoes do yview sao ESTIMATIVAS enquanto o Tk mede as linhas em
        segundo plano: 0.97 com o fim ja visivel, ou 1.0 sem ele estar.
        """
        _topo, base = self._limites_visiveis()
        ultimo = self._ultimo_conteudo()
        info = self.txt.dlineinfo(ultimo)
        return bool(info) and info[1] + self._altura_da_linha(ultimo) <= base + 1

    def _ypx(self, a, b):
        valor = self.txt.count(a, b, "update", "ypixels")
        if isinstance(valor, tuple):
            valor = valor[-1]
        return int(valor or 0)

    def _altura_da_linha(self, indice):
        """Altura INTEIRA da linha de exibicao que contem `indice`.

        O dlineinfo do Tk devolve so a parte VISIVEL de uma linha cortada no
        pe da tela (22 px de uma linha de 38) -- com ele, a linha cortada
        parecia caber inteira e a mascara nunca aparecia.
        """
        inicio = self.txt.index(f"{indice} display linestart")
        seguinte = self.txt.index(f"{inicio} +1 display lines display linestart")
        altura = self._ypx(inicio, seguinte if self.txt.compare(seguinte, ">", inicio)
                           else "end")
        # A folga do Folhear mora na ultima linha; ela nao e conteudo.
        if (self._em_folha() and self._folga_folha
                and self.txt.compare(f"{inicio} display lineend", ">=", "end-1c")):
            altura -= self._folga_folha
        return altura

    def _ultimo_conteudo(self):
        """Ultimo caractere que nao e quebra de linha (figura conta)."""
        indice = self.txt.index("end-1c")
        for _ in range(8):
            if self.txt.get(indice) != "\n" or not self.txt.compare(indice, ">", "1.0"):
                break
            indice = self.txt.index(f"{indice} -1c")
        return indice

    def _linha_cortada_no_pe(self):
        """(indice, y) da linha que nao cabe inteira no pe da tela, ou None."""
        topo, base = self._limites_visiveis()
        ultima = self.txt.index(f"@0,{base - 1}")
        info = self.txt.dlineinfo(ultima)
        if (info and info[1] > topo + 1
                and info[1] + self._altura_da_linha(ultima) > base + 1):
            return self.txt.index(f"{ultima} display linestart"), info[1]
        return None

    def _inicio_visivel(self):
        topo, _base = self._limites_visiveis()
        info = self.txt.dlineinfo("1.0")
        return bool(info) and info[1] >= topo - 1

    def _offset_no_topo(self):
        topo, _base = self._limites_visiveis()
        return self._para_offset(self.txt.index(f"@0,{topo + 1}"))

    def _inicio_da_proxima_folha(self):
        """Indice da primeira linha que nao coube inteira na tela."""
        cortada = self._linha_cortada_no_pe()
        if cortada:
            inicio = cortada[0]
        else:
            # Coube inteira (ou e uma linha/figura mais alta que a tela): a
            # proxima folha comeca na linha de exibicao seguinte.
            _topo, base = self._limites_visiveis()
            ultima = self.txt.index(f"@0,{base - 1}")
            inicio = self.txt.index(f"{ultima} +1 display lines display linestart")
        return self._sem_orfaos(inicio)

    _TAGS_PRESAS_A_SEGUINTE = ("titulo1", "titulo2", "titulo3", "cod_cabecalho")

    def _sem_orfaos(self, inicio):
        """Titulo e cabecalho de codigo nunca ficam sozinhos no pe da folha.

        Regra de diagramacao de livro: se a ultima linha da folha e um titulo
        (ou a faixa "PYTHON" de um bloco de codigo), ela desce junto com o que
        vem depois. So recua ate 3 linhas e nunca esvazia a folha.
        """
        topo, _base = self._limites_visiveis()
        primeira = self.txt.index(f"@0,{topo + 1} display linestart")
        for _ in range(3):
            anterior = self.txt.index(f"{inicio} -1 display lines display linestart")
            if (not self.txt.compare(anterior, ">", primeira)
                    or not self.txt.compare(anterior, "<", inicio)):
                break
            tags = self.txt.tag_names(anterior)
            if not any(t in tags for t in self._TAGS_PRESAS_A_SEGUINTE):
                break
            inicio = anterior
        return inicio

    def _montar_folhas(self, manter_indice=None, ancora=None):
        """Calcula a grade de folhas do trecho e mostra a folha certa.

        `manter_indice`: indice de texto que deve continuar visivel (busca,
        selecao arrastada) ou "fim" -- mostra a folha que o CONTEM.
        `ancora`: offset que vira o INICIO exato de uma folha. E o que mantem
        a leitura parada no lugar quando o trecho e remontado (recentrar,
        redimensionar, trocar fonte): a grade se reorganiza em volta da frase
        do topo, em vez de a frase pular para o meio de outra folha.
        """
        if self._montando_folhas:
            # Reentrada: algum callback disparado no meio da montagem pediu
            # outra. A de fora ja vai deixar a grade certa.
            return
        self._grade_folhas = []
        if not self._em_folha():
            return
        # Janela ainda sem altura de verdade (acabou de abrir/maximizar): com
        # 1-20 px cada "folha" tinha UMA linha, e a grade de 5 paginas levava
        # 200 passos (9 s parado). Espera o layout e monta uma vez so.
        if self.txt.winfo_height() < 120:
            if ancora is not None:
                self.txt.yview(f"1.0+{int(ancora)}c")
            self.after(120, lambda: self._montar_folhas(manter_indice, ancora))
            return
        self._montando_folhas = True
        try:
            self._montar_folhas_agora(manter_indice, ancora)
        finally:
            self._montando_folhas = False

    def _montar_folhas_agora(self, manter_indice, ancora):
        # Sem update_idletasks aqui nem na simulacao: o dlineinfo e os
        # indices "@x,y" do Tk ja atualizam o layout sozinhos. O
        # update_idletasks redesenhava a tela inteira a cada passo E rodava
        # callbacks pendentes (renderizar, montar de novo...) por dentro --
        # numa sessao real, uma montagem chegou a 12 s com a janela parada.
        self._aplicar_folga_final()
        manter = None
        if manter_indice and manter_indice != "fim":
            try:
                manter = self._para_offset(self.txt.index(manter_indice))
            except Exception:
                manter = None
        self._simulando = True
        try:
            if ancora is not None:
                ancora = self._para_offset(
                    self.txt.index(f"1.0+{int(ancora)}c display linestart"))
                antes = [s for s in self._simular_folhas(0, parar_em=ancora) if s < ancora]
                grade = antes + self._simular_folhas(ancora)
                alvo = len(antes)
            else:
                grade = self._simular_folhas(0)
                alvo = 0
                if manter_indice == "fim":
                    alvo = len(grade) - 1
                elif manter is not None:
                    alvo = max(i for i, inicio in enumerate(grade) if inicio <= manter)
            self._grade_folhas = grade
            self._altura_da_grade = self.txt.winfo_height()
        finally:
            self._simulando = False
        self._mostrar_folha(alvo)

    def _folhear_daqui(self):
        """Monta as folhas a partir da linha que esta no topo agora."""
        if not self._em_folha():
            return
        try:
            self._montar_folhas(ancora=self._offset_no_topo())
        except Exception:
            self._montar_folhas()

    def _simular_folhas(self, inicio, parar_em=None):
        """Inicios de folha a partir de `inicio` ate o fim (ou ate `parar_em`)."""
        self.txt.yview(f"1.0+{inicio}c")
        grade = [inicio]
        for _ in range(600):
            if parar_em is not None and grade[-1] >= parar_em:
                break
            if self._fim_visivel():
                break
            proxima = self._inicio_da_proxima_folha()
            offset = self._para_offset(proxima)
            if offset <= grade[-1]:
                break
            # So sobrou espaco em branco (a margem do ultimo paragrafo):
            # uma folha vazia no fim seria uma virada inutil.
            if (not self.txt.get(proxima, "end").strip()
                    and not self.txt.dump(proxima, "end", image=True)):
                break
            grade.append(offset)
            self.txt.yview(proxima)
        return grade

    def _aplicar_folga_final(self):
        """Espaco em branco depois do fim da pagina, do tamanho da tela.

        O Tk nao deixa rolar alem do fim do texto: a ultima folha parava
        "encostada" no fundo e repetia o final da folha anterior. Com a folga,
        ela comeca no topo como todas as outras -- e o resto fica em branco,
        como a ultima pagina de um capitulo.
        """
        topo, base = self._limites_visiveis()
        self._folga_folha = base - topo
        self.txt.tag_configure("folga_folha", spacing3=self._folga_folha)
        self.txt.tag_remove("folga_folha", "1.0", "end")
        self.txt.tag_add("folga_folha", "end-1c linestart", "end")
        self.txt.tag_raise("folga_folha")

    def _remover_folga_final(self):
        self._folga_folha = 0
        try:
            self.txt.tag_remove("folga_folha", "1.0", "end")
        except Exception:
            pass

    def _mostrar_folha(self, indice, animar=False):
        grade = self._grade_folhas or [0]
        indice = max(0, min(indice, len(grade) - 1))
        self._concluir_animacao()
        anterior = self._folha_atual
        self._folha_atual = indice
        if (animar and abs(indice - anterior) == 1 and 0 <= anterior < len(grade)
                and not self._duas_folhas()
                and self.cfg.obter_bool("animar_folha", True)):
            if self._animar_ate(grade[anterior], grade[indice]):
                return
        self._fixar_folha()

    def _fixar_folha(self):
        """Encaixa a folha atual exatamente no topo (fim de qualquer animacao)."""
        grade = self._grade_folhas or [0]
        indice = max(0, min(self._folha_atual, len(grade) - 1))
        self._simulando = True
        try:
            self.txt.yview(f"1.0+{grade[indice]}c")
        finally:
            self._simulando = False
        self._atualizar_mascara()
        # O aviso de rolagem do Tk chega durante o encaixe (e e ignorado ali):
        # a pagina do topo e conferida aqui, ao fim de cada virada.
        if len(self._trecho) > 1:
            self._verificar_pagina_no_topo()
        self._atualizar_rodape()
        self._sincronizar_par()

    # ---- duas folhas lado a lado
    # Em janela larga o Folhear mostra duas folhas, como um livro aberto. A
    # folha da direita e um PEER do Tk: outro visor do MESMO texto, com os
    # mesmos grifos, busca e figuras -- nada e montado duas vezes. Ela so liga
    # quando cabem duas colunas de leitura inteiras; em meia tela, uma folha.
    _OPCOES_COPIADAS_DO_PAR = ("background", "foreground", "padx", "pady",
                               "spacing1", "spacing2", "spacing3", "wrap", "font",
                               "selectbackground", "selectforeground",
                               "insertwidth", "cursor", "tabs")

    def _quer_duas_folhas(self):
        if not self._em_folha() or not self.cfg.obter_bool("duas_folhas", True):
            return False
        try:
            largura = self.palco.winfo_width()
        except Exception:
            return False
        familia = FONTES_LEITURA.get(self.pref("fonte_leitura"), "Georgia")
        tamanho = max(9, self.pref_int("tamanho_fonte", TAMANHO_FONTE_PADRAO))
        alvo = max(LARGURA_COLUNA_MIN,
                   min(LARGURA_COLUNA_MAX, self.pref_int("largura_coluna", 68)))
        coluna = alvo * self._largura_do_caractere(familia, tamanho)
        return largura >= 2 * coluna + 4 * ESP["xl"]

    def _duas_folhas(self):
        return self._par is not None and self._par.winfo_ismapped()

    def _atualizar_duas_folhas(self):
        """Liga/desliga a segunda folha conforme o modo e a largura."""
        quer = self._quer_duas_folhas()
        if quer == self._duas_folhas():
            return False
        if quer:
            if self._par is None:
                # O tkinter nao embrulha um peer ja existente: registra-se o
                # objeto Python e o Tk cria o widget com aquele nome.
                par = tk.Text.__new__(tk.Text)
                tk.BaseWidget._setup(par, self.palco, {})
                par.widgetName = "text"
                self.txt.tk.call(self.txt._w, "peer", "create", par._w)
                # Sem a borda afundada padrao do Text: virava um risco claro
                # entre as duas folhas.
                par.configure(relief="flat", borderwidth=0, highlightthickness=0)
                self._par = par
                self._par.bind("<MouseWheel>", self._ao_rolar)
                self._par.bind("<Button-1>", self._ao_clicar_par, add=True)
                self._par.bind("<<Selection>>", self._ao_mudar_selecao, add=True)
            self.palco.grid_columnconfigure(0, weight=1, uniform="folhas")
            self.palco.grid_columnconfigure(1, weight=1, uniform="folhas")
            self._par.grid(row=0, column=1, sticky="nsew")
        else:
            if self._par is not None:
                self._par.grid_remove()
            if self._mascara_par is not None:
                self._mascara_par.place_forget()
            self.palco.grid_columnconfigure(0, weight=1, uniform="")
            self.palco.grid_columnconfigure(1, weight=0, uniform="")
        return True

    def _copiar_opcoes_para_par(self):
        if self._par is None:
            return
        opcoes = {}
        for nome in self._OPCOES_COPIADAS_DO_PAR:
            try:
                opcoes[nome] = self.txt.cget(nome)
            except Exception:
                pass
        try:
            self._par.configure(state="normal", **opcoes)
            self._par.configure(state="disabled")
        except Exception:
            pass

    def _sincronizar_par(self):
        """A folha da direita mostra a folha seguinte a da esquerda."""
        if not self._duas_folhas():
            return
        self._copiar_opcoes_para_par()
        grade = self._grade_folhas or [0]
        seguinte = self._folha_atual + 1
        if self._mascara_par is None:
            self._mascara_par = tk.Frame(self.palco, bd=0, highlightthickness=0)
            self._mascara_par.bind("<MouseWheel>", self._ao_rolar)
            self._mascara_par.bind("<Button-1>", lambda e: self._virar_folha(1))
        self._mascara_par.configure(bg=self.txt.cget("bg"))
        dx = self._par.winfo_rootx() - self.palco.winfo_rootx()
        dy = self._par.winfo_rooty() - self.palco.winfo_rooty()
        if seguinte >= len(grade):
            # Nao ha folha seguinte no trecho: a direita fica em branco.
            self._mascara_par.place(x=dx, y=dy, width=self._par.winfo_width(),
                                    height=self._par.winfo_height())
            self._mascara_par.lift()
            return
        self._par.yview(f"1.0+{grade[seguinte]}c")
        corte = None
        if seguinte + 1 < len(grade):
            info = self._par.dlineinfo(f"1.0+{grade[seguinte + 1]}c")
            if info:
                corte = info[1]
        if corte is None:
            self._mascara_par.place_forget()
        else:
            self._mascara_par.place(x=dx, y=dy + corte, width=self._par.winfo_width(),
                                    height=self._par.winfo_height() - corte)
            self._mascara_par.lift()

    def _ao_clicar_par(self, evento):
        largura = max(1, self._par.winfo_width())
        if evento.x >= largura * 0.75:
            self._virar_folha(1)
            return "break"

    # ---- deslize da virada
    # A folha desliza em ~120 ms, desacelerando no fim (ease-out cubico):
    # rapido o bastante para nunca atrapalhar quem vira varias seguidas, e o
    # suficiente para o olho acompanhar para onde o texto foi. Uma nova virada
    # no meio do deslize termina o anterior na hora.
    _QUADROS_DESLIZE = 7

    def _animar_ate(self, origem, destino):
        try:
            if destino >= origem:
                distancia = self._ypx(f"1.0+{origem}c", f"1.0+{destino}c")
            else:
                distancia = -self._ypx(f"1.0+{destino}c", f"1.0+{origem}c")
        except Exception:
            return False
        if not distancia or abs(distancia) > 4000:
            return False
        self._esconder_mascara()
        self._simulando = True
        passos = [round(distancia * (1 - (1 - k / self._QUADROS_DESLIZE) ** 3))
                  for k in range(1, self._QUADROS_DESLIZE + 1)]
        self._animacao = {"passos": passos, "feito": 0, "id": None}
        self._quadro_deslize()
        return True

    def _quadro_deslize(self):
        anim = self._animacao
        if anim is None:
            return
        anim["id"] = None
        if not anim["passos"]:
            self._concluir_animacao()
            return
        alvo = anim["passos"].pop(0)
        delta = alvo - anim["feito"]
        anim["feito"] = alvo
        if delta:
            self.txt.yview_scroll(delta, "pixels")
        if anim["passos"]:
            anim["id"] = self.after(16, self._quadro_deslize)
        else:
            self._concluir_animacao()

    def _concluir_animacao(self):
        anim = getattr(self, "_animacao", None)
        if anim is None:
            return
        self._animacao = None
        if anim.get("id") is not None:
            try:
                self.after_cancel(anim["id"])
            except Exception:
                pass
        self._simulando = False
        self._fixar_folha()

    def _virar_folha(self, direcao):
        if not self._grade_folhas:
            self._montar_folhas(manter_indice=self.txt.index("@0,4"))
        if self._duas_folhas():
            self._virar_par_de_folhas(direcao)
            return
        if direcao > 0:
            if self._folha_atual + 1 < len(self._grade_folhas):
                self._mostrar_folha(self._folha_atual + 1, animar=True)
            elif self._pode_estender(1):
                # Fim do trecho montado: remonta em volta da folha atual e
                # segue -- a costura continua, sem saltar para o topo da
                # pagina seguinte.
                self._recentrar()
                if self._folha_atual + 1 < len(self._grade_folhas):
                    self._mostrar_folha(self._folha_atual + 1)
            elif self.pagina_atual < self.total_paginas - 1:
                self.proxima_pagina()
        else:
            if self._folha_atual > 0:
                self._mostrar_folha(self._folha_atual - 1, animar=True)
            elif self._pode_estender(-1):
                self._recentrar()
                if self._folha_atual > 0:
                    self._mostrar_folha(self._folha_atual - 1)
            elif self.pagina_atual > 0:
                # Voltar de pagina abre na ULTIMA folha da anterior: e a
                # continuacao natural de quem esta folheando para tras.
                self._entrar_pelo_fim = True
                self.pagina_anterior()

    def _virar_par_de_folhas(self, direcao):
        """Com duas folhas na tela, cada virada avanca as duas."""
        alvo = self._folha_atual + 2 * direcao
        if direcao > 0 and alvo >= len(self._grade_folhas) and self._pode_estender(1):
            self._recentrar()
            alvo = self._folha_atual + 2
        if direcao < 0 and alvo < 0 and self._pode_estender(-1):
            self._recentrar()
            alvo = self._folha_atual - 2
        if 0 <= alvo < len(self._grade_folhas):
            self._mostrar_folha(alvo)
        elif direcao > 0 and self.pagina_atual < self.total_paginas - 1 \
                and not self._pode_estender(1):
            self._mostrar_folha(len(self._grade_folhas) - 1)
        elif direcao < 0 and alvo < 0:
            self._mostrar_folha(0)

    def _atualizar_mascara(self):
        if not self._em_folha():
            self._esconder_mascara()
            return
        if self._mascara is None:
            # Irma do texto, no palco -- nao filha dele. No Windows o Text
            # redesenha por cima das proprias janelas-filhas ao rolar, e a
            # mascara ficava invisivel mesmo "posicionada".
            self._mascara = tk.Frame(self.palco, bd=0, highlightthickness=0)
            self._mascara.bind("<MouseWheel>", self._ao_rolar)
            self._mascara.bind("<Button-1>", self._clique_na_mascara)
        # Tudo a partir do inicio da PROXIMA folha e dela: a linha cortada e,
        # com a regra dos orfaos, um titulo que desceu junto com o texto.
        topo, base = self._limites_visiveis()
        info = None
        if not self._fim_visivel():
            info = self.txt.dlineinfo(self._inicio_da_proxima_folha())
        if not info or not (topo + 1 < info[1] < base):
            self._esconder_mascara()
            return
        y = info[1]
        dx = self.txt.winfo_rootx() - self.palco.winfo_rootx()
        dy = self.txt.winfo_rooty() - self.palco.winfo_rooty()
        self._mascara.configure(bg=self.txt.cget("bg"))
        self._mascara.place(x=dx, y=dy + y, width=self.txt.winfo_width(),
                            height=self.txt.winfo_height() - y)
        self._mascara.lift()
        # O numero da pagina do modo foco tambem vive no palco: fica por cima.
        if self.lbl_flutuante.winfo_ismapped():
            self.lbl_flutuante.lift()

    def _esconder_mascara(self):
        if getattr(self, "_mascara", None) is not None:
            self._mascara.place_forget()

    def _clique_na_mascara(self, evento):
        if evento.x <= max(1, self.txt.winfo_width()) * 0.3:
            self._virar_folha(-1)
        else:
            self._virar_folha(1)

    def _folha_apos_mudanca(self):
        """A visao ou a geometria mudou por fora (busca, selecao arrastada,
        janela redimensionada): refaz a grade mantendo a frase do topo."""
        self._agendado_folha = None
        if not self._em_folha() or self._simulando:
            return
        try:
            grade = self._grade_folhas
            no_topo = self._offset_no_topo()
            alinhado = (grade and 0 <= self._folha_atual < len(grade)
                        and grade[self._folha_atual] == no_topo
                        and self.txt.winfo_height() == self._altura_da_grade)
            if not alinhado:
                self._montar_folhas(ancora=no_topo)
            else:
                self._atualizar_mascara()
        except Exception:
            pass

    def _agendar_realinhar_folha(self):
        if self._agendado_folha is None and not self._simulando:
            self._agendado_folha = self.after(120, self._folha_apos_mudanca)

    def _ao_mover_visao(self, primeiro, ultimo):
        if self._simulando:
            # Durante a montagem das folhas a visao pula dezenas de vezes;
            # reagir a cada pulo custava ~1 s por montagem.
            return
        # Mantem a barra de rolagem do CustomTkinter funcionando...
        try:
            self.area._y_scrollbar.set(primeiro, ultimo)
        except Exception:
            pass
        # Rolagem continua: anota QUANDO o texto encostou no limite. A virada
        # so acontece num toque de roda depois disso -- a inercia do proprio
        # gesto que trouxe ate aqui nao vira a pagina sem querer.
        if self.modo_virada == MODO_LIVRE and self.documento:
            try:
                no_limite = self._fim_visivel() or self._inicio_visivel()
            except Exception:
                no_limite = False
            if no_limite and not self._limite_visto:
                self._chegou_ao_limite = time.time()
            self._limite_visto = no_limite
        if self._em_folha() and not self._simulando:
            self._agendar_realinhar_folha()
        if len(self._trecho) > 1 and not self._simulando:
            self._agendar_verificar_topo()
        # ...e reage a chegada ao fim/topo da pagina.
        try:
            self._atualizar_setas(float(primeiro), float(ultimo))
            self._reposicionar_botoes_codigo()
        except Exception:
            pass

    def _atualizar_setas(self, inicio=None, fim=None):
        if self.modo_virada != MODO_BOTOES or not self.documento:
            if self._estado_setas != (False, False):
                self.btn_seta_ant.place_forget()
                self.btn_seta_prox.place_forget()
                self._estado_setas = (False, False)
            return

        if inicio is None:
            inicio, fim = self.txt.yview()

        # Pagina curta (sem barra de rolagem) mostra as duas setas de uma vez.
        sem_rolagem = inicio <= 0.001 and fim >= 0.999
        mostrar_ant = (sem_rolagem or inicio <= 0.001) and self.pagina_atual > 0
        mostrar_prox = ((sem_rolagem or fim >= 0.999)
                        and self.pagina_atual < self.total_paginas - 1)

        if (mostrar_ant, mostrar_prox) == self._estado_setas:
            return
        self._estado_setas = (mostrar_ant, mostrar_prox)

        # Em janela estreita a coluna de texto ocupa quase tudo, entao as setas
        # encolhem e colam na borda para atrapalhar o minimo possivel.
        lado = 38 if self._compacto else 46
        margem = 6 if self._compacto else 14

        if mostrar_ant:
            self.btn_seta_ant.configure(width=lado, height=lado, corner_radius=lado // 2)
            self.btn_seta_ant.place(relx=0.0, rely=0.5, anchor="w", x=margem)
            self.btn_seta_ant.lift()
        else:
            self.btn_seta_ant.place_forget()

        if mostrar_prox:
            # Deslocado da borda para nao cobrir a barra de rolagem.
            self.btn_seta_prox.configure(width=lado, height=lado, corner_radius=lado // 2)
            self.btn_seta_prox.place(relx=1.0, rely=0.5, anchor="e", x=-(margem + 16))
            self.btn_seta_prox.lift()
        else:
            self.btn_seta_prox.place_forget()

    def _ao_clicar_texto(self, evento):
        self._fechar_popover()
        if self._rolagem_ativa:
            self.parar_rolagem(avisar=True)
            return
        if self.modo_virada not in (MODO_MARGEM, MODO_FOLHA) or not self.documento:
            return
        if self.modo_virada == MODO_FOLHA:
            largura = max(1, self.txt.winfo_width())
            zona = max(40, min(self._margem_atual(), largura * 0.25))
            if evento.x <= zona:
                self._virar_folha(-1)
                return "break"
            if evento.x >= largura - zona:
                self._virar_folha(1)
                return "break"
            return

        # A zona de clique e exatamente a margem que cerca a coluna de texto,
        # entao selecionar palavras continua funcionando normalmente.
        largura = max(1, self.txt.winfo_width())
        zona = max(40, min(self._margem_atual(), largura * 0.25))
        if evento.x <= zona:
            self.pagina_anterior()
            return "break"
        if evento.x >= largura - zona:
            self.proxima_pagina()
            return "break"

    def _ao_rolar(self, evento):
        """No modo Livre, virar a pagina ao passar do fim -- leitura continua."""
        if not self.documento:
            return
        if self._rolagem_ativa:
            self.parar_rolagem(avisar=True)

        descendo = evento.delta < 0
        if self.modo_virada == MODO_FOLHA:
            # Um gesto, uma folha. Touchpads mandam dezenas de eventos por
            # gesto; o intervalo minimo impede que um deslize vire cinco.
            agora = time.time()
            if agora - self._ultima_folha >= 0.28:
                self._ultima_folha = agora
                self._virar_folha(1 if descendo else -1)
            return "break"

        if self.modo_virada != MODO_LIVRE:
            # Nos outros modos a roda so rola; virar e sempre um ato explicito.
            self._acumulado_wheel = 0
            return

        no_limite = ((descendo and self._fim_visivel())
                     or (not descendo and self._inicio_visivel()))
        if not no_limite:
            self._acumulado_wheel = 0
            return

        # Texto corrido: encostou no limite e a roda continuou -- emenda na
        # pagina vizinha. O respiro de 0,3 s depois de encostar absorve a
        # inercia do gesto que trouxe ate aqui.
        if time.time() - self._chegou_ao_limite < 0.3:
            return "break"
        self._acumulado_wheel = 0
        if self._pode_estender(1 if descendo else -1):
            self._recentrar()
            return "break"
        if descendo:
            if self.pagina_atual < self.total_paginas - 1:
                self.proxima_pagina()
        elif self.pagina_atual > 0:
            # Voltando, a pagina anterior abre pelo FIM: a leitura emenda.
            self._entrar_pelo_fim = True
            self.pagina_anterior()
        # "break" e obrigatorio: o Tk roda ESTE handler antes da rolagem
        # padrao do widget. Sem interromper, a pagina virava e logo em
        # seguida levava o deslocamento da roda.
        return "break"

    def _rolar_ou_virar(self, direcao):
        """Espaco/PageDown: rola a pagina; no fim dela, avanca."""
        if self._rolagem_ativa:
            self.parar_rolagem(avisar=True)
        if self._em_folha():
            self._virar_folha(direcao)
            return
        salto = int(self.txt.winfo_height() * 0.85)

        if direcao > 0 and self._fim_visivel() and self._pode_estender(1):
            self._recentrar()
        elif direcao < 0 and self._inicio_visivel() and self._pode_estender(-1):
            self._recentrar()
        if direcao > 0:
            if self._fim_visivel():
                self.proxima_pagina()
            else:
                self.txt.yview_scroll(salto, "pixels")
        else:
            if self._inicio_visivel():
                # Volta lendo o fim da pagina anterior na rolagem continua;
                # nos demais a regra e sempre comecar do topo.
                self._entrar_pelo_fim = self.modo_virada == MODO_LIVRE
                self.pagina_anterior()
            else:
                self.txt.yview_scroll(-salto, "pixels")

    # ================================================== ROLAGEM AUTOMATICA
    def alternar_rolagem(self, _=None):
        if self.modo_virada != MODO_AUTO:
            C.Toast.mostrar(self, "Ative “Rolagem automatica” em Aparencia (Ctrl+P).",
                            "info", 2400)
            return
        if self._rolagem_ativa:
            self.parar_rolagem()
        else:
            self.iniciar_rolagem()

    def iniciar_rolagem(self):
        if self._rolagem_ativa or not self.documento or self.modo_virada != MODO_AUTO:
            return
        self._rolagem_ativa = True
        self._resto_rolagem = 0.0
        C.trocar_icone(self.btn_auto, "pause")
        self._tique_rolagem()

    def parar_rolagem(self, avisar=False):
        estava = self._rolagem_ativa
        self._rolagem_ativa = False
        if self._id_rolagem is not None:
            try:
                self.after_cancel(self._id_rolagem)
            except Exception:
                pass
            self._id_rolagem = None
        try:
            C.trocar_icone(self.btn_auto, "play")
        except Exception:
            pass
        if estava and avisar:
            C.Toast.mostrar(self, "Rolagem pausada.", "info", 1200)

    def _tique_rolagem(self):
        self._id_rolagem = None
        if not self._rolagem_ativa or not self.documento:
            return

        velocidade = max(1, min(10, self.cfg.obter_int("velocidade_rolagem",
                                                       VELOCIDADE_ROLAGEM_PADRAO)))
        # Acumula a fracao de pixel entre quadros; sem isso as velocidades
        # baixas simplesmente nao saem do lugar.
        self._resto_rolagem += velocidade * 0.55
        passo = int(self._resto_rolagem)
        if passo:
            self._resto_rolagem -= passo
            self.txt.yview_scroll(passo, "pixels")

        _, fim = self.txt.yview()
        if fim >= 0.999:
            self._id_rolagem = self.after(PAUSA_FIM_PAGINA_MS, self._avancar_rolando)
        else:
            self._id_rolagem = self.after(INTERVALO_ROLAGEM_MS, self._tique_rolagem)

    def _avancar_rolando(self):
        self._id_rolagem = None
        if not self._rolagem_ativa:
            return
        if self._pode_estender(1):
            self._recentrar()
            self._id_rolagem = self.after(INTERVALO_ROLAGEM_MS, self._tique_rolagem)
            return
        if self.pagina_atual >= self.total_paginas - 1:
            self.parar_rolagem()
            C.Toast.mostrar(self, "Fim do livro.", "ok", 2000)
            return
        self.proxima_pagina()
        self._id_rolagem = self.after(600, self._tique_rolagem)

    def ajustar_velocidade(self, delta):
        atual = self.cfg.obter_int("velocidade_rolagem", VELOCIDADE_ROLAGEM_PADRAO)
        novo = max(1, min(10, atual + delta))
        self.cfg.definir("velocidade_rolagem", novo)
        self._atualizar_rotulo_velocidade()

    def _atualizar_rotulo_velocidade(self):
        try:
            self.lbl_velocidade.configure(
                text=str(self.cfg.obter_int("velocidade_rolagem",
                                            VELOCIDADE_ROLAGEM_PADRAO)))
        except Exception:
            pass

    def _ao_clicar_progresso(self, evento):
        largura = self.progresso.winfo_width() or 1
        fracao = max(0.0, min(1.0, evento.x / largura))
        self.ir_para_pagina(int(fracao * (self.total_paginas - 1)))

    # ------------------------------------------------- historico e posicao
    def voltar_no_historico(self, _=None):
        """Volta ao ponto anterior ao ultimo salto; sem historico, a estante."""
        if not self._historico:
            self.voltar()
            return
        destino = self._historico.pop()
        self._futuro.append(self.pagina_atual)
        self.ir_para_pagina(destino, registrar=False)
        C.Toast.mostrar(self, f"De volta a pagina {destino + 1}  ·  Alt+→ refaz",
                        "info", 1300)

    def avancar_no_historico(self, _=None):
        if not self._futuro:
            return
        destino = self._futuro.pop()
        self._historico.append(self.pagina_atual)
        self.ir_para_pagina(destino, registrar=False)

    def pedir_pagina(self, _=None):
        """Ctrl+G: ir direto a uma pagina pelo numero."""
        if not self.documento:
            return

        def ir(texto):
            try:
                numero = int("".join(ch for ch in texto if ch.isdigit()))
            except ValueError:
                C.Toast.mostrar(self, "Digite o numero da pagina.", "erro")
                return
            self.ir_para_pagina(numero - 1)

        Modais.pedir_texto(self, "Ir para a pagina",
                           f"De 1 a {self.total_paginas}",
                           str(self.pagina_atual + 1), ir, texto_ok="Ir",
                           dica="Numero da pagina")

    def _gravar_rolagem(self):
        if not self.id_livro or not self.documento:
            return
        # Guarda o CARACTERE no topo da tela, nao a fracao rolada: a fracao do
        # Tk e estimada ate o layout terminar (0.32 na tela voltava 0.18) e
        # muda com a fonte; o offset aponta sempre para a mesma frase.
        try:
            # Offset LOCAL da pagina do topo: num trecho de varias paginas o
            # global nao significaria nada na proxima abertura.
            self._verificar_pagina_no_topo(recentrar=False)
            pagina, local, _recuo = self._posicao_no_topo()
            if pagina == self.pagina_atual:
                self.banco.definir_rolagem(self.id_livro, local)
        except Exception:
            pass

    def _restaurar_rolagem(self, id_livro, offset):
        if id_livro != self.id_livro:
            return
        try:
            regiao = self._regiao(self.pagina_atual)
            self._posicionar((regiao[1] if regiao else 0) + int(offset))
        except Exception:
            pass

    def exportar_anotacoes(self, _=None):
        """Ctrl+E: grifos, notas e marcadores do livro num arquivo Markdown."""
        if not self.id_livro:
            return
        grifos = self.banco.listar_grifos_para_exportar(self.id_livro)
        notas = self.banco.listar_notas(self.id_livro)
        marcadores = self.banco.listar_favoritos(self.id_livro)
        if not (grifos or notas or marcadores):
            C.Toast.mostrar(self, "Nada para exportar: grife (Ctrl+H), anote (Ctrl+N) "
                                  "ou marque (Ctrl+D) primeiro.", "info", 2600)
            return
        titulo = (self.livro or {}).get("titulo") or "anotacoes"
        nome = "".join(ch for ch in titulo if ch not in r'<>:"/\|?*').strip()[:80]
        caminho = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Exportar anotacoes",
            defaultextension=".md", initialfile=f"{nome or 'anotacoes'} - anotacoes.md",
            filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")])
        if not caminho:
            return
        try:
            sumario = self.pdf_service.obter_sumario(self.documento)
        except Exception:
            sumario = []
        texto = exportar.gerar_markdown(self.livro or {}, grifos, notas,
                                        marcadores, sumario)
        try:
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(texto)
        except OSError as erro:
            Modais.aviso(self, "Nao foi possivel salvar", str(erro))
            return
        C.Toast.mostrar(self, f"Exportado: {len(grifos)} grifo(s), {len(notas)} "
                              f"nota(s), {len(marcadores)} marcador(es).", "ok", 2400)

    # =====================================================================
    # ATALHOS
    # =====================================================================
    def ativar_atalhos(self):
        raiz = self.controlador
        self._ligacoes = {
            "<Right>": lambda e: self._se_livre(e, lambda: self._avancar(1)),
            "<Left>": lambda e: self._se_livre(e, lambda: self._avancar(-1)),
            "<Down>": lambda e: self._se_livre(e, lambda: self._seta_vertical(1)),
            "<Up>": lambda e: self._se_livre(e, lambda: self._seta_vertical(-1)),
            "<Next>": lambda e: self._se_livre(e, lambda: self._rolar_ou_virar(1)),
            "<Prior>": lambda e: self._se_livre(e, lambda: self._rolar_ou_virar(-1)),
            "<space>": lambda e: self._se_livre(e, lambda: self._rolar_ou_virar(1)),
            "<Shift-space>": lambda e: self._se_livre(e, lambda: self._rolar_ou_virar(-1)),
            "<Home>": lambda e: self._se_livre(e, lambda: self.ir_para_pagina(0)),
            "<End>": lambda e: self._se_livre(e, lambda: self.ir_para_pagina(self.total_paginas - 1)),
            "<Control-f>": lambda e: self.alternar_busca(),
            "<Control-b>": lambda e: self.alternar_painel("Sumario"),
            "<Control-i>": lambda e: self.alternar_assistente(),
            "<Control-p>": lambda e: self.alternar_tipografia(),
            "<Control-h>": lambda e: self.grifar_selecao(),
            "<Control-n>": lambda e: self.abrir_notas(),
            "<Control-d>": lambda e: self.alternar_favorito(),
            "<Control-g>": lambda e: self.pedir_pagina(),
            "<Control-r>": lambda e: self.abrir_revisao(),
            "<Control-e>": lambda e: self.exportar_anotacoes(),
            "<Control-space>": lambda e: self.alternar_rolagem(),
            "<Control-l>": lambda e: self.alternar_narracao(),
            "<Control-Shift-C>": lambda e: self.copiar_codigo_da_pagina(),
            "<Control-Shift-L>": lambda e: self.alternar_numeracao_codigo(),
            "<Control-Shift-A>": lambda e: self.recolher_todos_artigos(),
            "<Control-Shift-Q>": lambda e: self.alternar_quadro_a_quadro(),
            "<Control-plus>": lambda e: self.ajustar_fonte(1),
            "<Control-minus>": lambda e: self.ajustar_fonte(-1),
            "<Control-equal>": lambda e: self.ajustar_fonte(1),
            "<F3>": lambda e: self.navegar_resultado(1),
            "<Shift-F3>": lambda e: self.navegar_resultado(-1),
            "<Alt-Left>": lambda e: self.voltar_no_historico(),
            "<Alt-Right>": lambda e: self.avancar_no_historico(),
        }
        for sequencia, funcao in self._ligacoes.items():
            raiz.bind(sequencia, funcao)

    def desativar_atalhos(self):
        for sequencia in getattr(self, "_ligacoes", {}):
            try:
                self.controlador.unbind(sequencia)
            except Exception:
                pass
        self._ligacoes = {}

    def _se_livre(self, evento, funcao):
        """Ignora o atalho quando o foco esta num campo de digitacao."""
        foco = self.controlador.focus_get()
        if isinstance(foco, (tk.Entry, ctk.CTkEntry)):
            return
        # Outra caixa de texto (a resposta da IA, as notas): as setas e o
        # espaco sao dela, nao da virada de pagina.
        if isinstance(foco, tk.Text) and foco is not self.txt:
            return
        funcao()
        return "break"

    # =====================================================================
    # PAINEL LATERAL
    # =====================================================================
    def alternar_painel(self, aba="Sumario"):
        if self.painel_aberto == aba:
            self._fechar_painel()
        else:
            self._abrir_painel(aba)

    def abas_disponiveis(self):
        """Abas fixas mais as que este livro tem conteudo para preencher."""
        return list(ABAS_FIXAS) + [a for a in ABAS_EXTRAS if self.extras.get(a)]

    def _sincronizar_abas(self):
        """Reconstroi o seletor quando o conjunto de abas muda de livro."""
        abas = self.abas_disponiveis()
        if abas == self._abas_atuais:
            return
        self._abas_atuais = abas
        try:
            self.seletor_painel.configure(values=[ICONES_PAINEL[a] for a in abas])
        except Exception:
            pass

    def registrar_extra(self, aba, itens):
        """Publica o resultado de uma varredura em 2o plano (figuras, datas...)."""
        self.extras[aba] = itens
        self._sincronizar_abas()
        if self.painel_aberto == aba:
            self._preencher_painel(aba)

    def _abrir_painel(self, aba):
        if aba not in self.abas_disponiveis():
            aba = "Sumario"
        self.painel_aberto = aba
        self.painel.grid()
        self._sincronizar_abas()
        self.seletor_painel.set(ICONES_PAINEL[aba])
        self._preencher_painel(aba)
        C.marcar_ativo(self.btn_painel, True)
        self._ajustar_coluna()

    def _fechar_painel(self):
        self.painel_aberto = None
        self.painel.grid_remove()
        C.marcar_ativo(self.btn_painel, False)
        self._ajustar_coluna()

    def _trocar_painel(self, valor):
        aba = {v: k for k, v in ICONES_PAINEL.items()}[valor]
        self._abrir_painel(aba)

    def _preencher_painel(self, aba):
        for widget in self.conteudo_painel.winfo_children():
            widget.destroy()
        C.limpar_registro_mortos()

        if aba == "Sumario":
            self._preencher_sumario()
        elif aba == "Busca":
            self._preencher_resultados_busca()
        elif aba == "Marcadores":
            self._preencher_marcadores()
        elif aba == "Notas":
            self._preencher_notas()
        elif aba in ABAS_EXTRAS:
            self._preencher_extra(aba)

    def _preencher_extra(self, aba):
        """Lista generica das abas descobertas no livro.

        Todas seguem a mesma forma -- rotulo, detalhe e um destino de pagina --
        entao uma implementacao so atende figuras, datas, artigos, versiculos
        e referencias.
        """
        itens = self.extras.get(aba) or []
        if not itens:
            C.rotulo(self.conteudo_painel, "Nada encontrado neste livro.",
                     11, "normal", "texto_suave", wraplength=188,
                     justify="left").pack(padx=ESP["md"], pady=ESP["lg"])
            return
        for item in itens[:400]:
            # Quadro de HQ e o unico destino que nao e so uma pagina: ele
            # aponta para um painel dentro dela.
            quadro = item.get("quadro")
            if quadro is None:
                destino = lambda p=item.get("pagina", 0): self.ir_para_pagina(p)
            else:
                destino = lambda i=quadro: self.mostrar_quadro(i)
            self._item_painel(item.get("rotulo", "?"), destino,
                              subtexto=item.get("detalhe", ""))

    def _item_painel(self, texto, comando, subtexto="", icone=""):
        item = C.quadro(self.conteudo_painel, "transparent")
        item.pack(fill="x", pady=1)
        botao = ctk.CTkButton(
            item, text=f"{icone} {texto}".strip(), command=comando, anchor="w",
            height=30 if not subtexto else 44, corner_radius=RAIO["sm"],
            fg_color="transparent", hover_color=T.superficie_alt, text_color=T.texto,
            font=ctk.CTkFont(FONTE_UI, 11))
        C.registrar_estilo(botao, hover_color="superficie_alt", text_color="texto")
        botao.pack(fill="x")
        if subtexto:
            C.rotulo(item, subtexto, 9, "normal", "texto_suave").pack(fill="x", padx=ESP["md"])
        return item

    def _preencher_sumario(self):
        toc = self.pdf_service.obter_sumario(self.documento) if self.documento else []
        if not toc:
            C.rotulo(self.conteudo_painel,
                     "Este livro nao traz indice embutido.\n\nUse o campo de pagina abaixo "
                     "ou a busca para navegar.", 11, "normal", "texto_suave",
                     wraplength=188, justify="left").pack(padx=ESP["md"], pady=ESP["lg"])
            return

        # Destaca o capitulo em que a leitura ESTA -- a ultima entrada que
        # comeca antes ou na pagina atual -- e nao so a entrada que comeca
        # exatamente nela (no meio do capitulo nada ficava marcado).
        atual = None
        for item in toc:
            nivel, titulo, pagina = item[0], item[1], max(0, item[2] - 1)
            recuo = "   " * max(0, nivel - 1)
            texto = titulo if len(titulo) <= 34 else titulo[:32] + "…"
            widget = self._item_painel(f"{recuo}{texto}", lambda p=pagina: self.ir_para_pagina(p))
            if pagina <= self.pagina_atual:
                atual = widget
        if atual is not None:
            try:
                atual.winfo_children()[0].configure(text_color=T.destaque)
            except Exception:
                pass

    def _preencher_marcadores(self):
        favoritos = self.banco.listar_favoritos(self.id_livro)
        if not favoritos:
            C.rotulo(self.conteudo_painel,
                     "Nenhuma pagina marcada.\n\nUse Ctrl+D para marcar a pagina atual.",
                     11, "normal", "texto_suave", wraplength=188,
                     justify="left").pack(padx=ESP["md"], pady=ESP["lg"])
            return
        for pagina, rotulo_txt in favoritos:
            linha = C.quadro(self.conteudo_painel, "transparent")
            linha.pack(fill="x", pady=1)
            botao = ctk.CTkButton(
                linha, text=f"Pagina {pagina + 1}", anchor="w", height=30,
                corner_radius=RAIO["sm"], fg_color="transparent",
                hover_color=T.superficie_alt, text_color=T.texto,
                font=ctk.CTkFont(FONTE_UI, 11),
                command=lambda p=pagina: self.ir_para_pagina(p))
            C.registrar_estilo(botao, hover_color="superficie_alt", text_color="texto")
            botao.pack(side="left", fill="x", expand=True)
            C.botao_icone(linha, "fechar", lambda p=pagina: self._remover_marcador(p),
                          "Remover marcador", tamanho=26).pack(side="right")

    def _remover_marcador(self, pagina):
        self.banco.remover_favorito(self.id_livro, pagina)
        self._preencher_painel("Marcadores")
        self._atualizar_estado_botoes()

    def _preencher_notas(self):
        notas = self.banco.listar_notas(self.id_livro)
        if not notas:
            C.rotulo(self.conteudo_painel,
                     "Nenhuma anotacao ainda.\n\nCtrl+N abre o bloco de notas da pagina.",
                     11, "normal", "texto_suave", wraplength=188,
                     justify="left").pack(padx=ESP["md"], pady=ESP["lg"])
            return
        for pagina, nota in notas:
            resumo = nota.strip().replace("\n", " ")
            resumo = resumo if len(resumo) <= 46 else resumo[:44] + "…"
            self._item_painel(f"Pagina {pagina + 1}",
                              lambda p=pagina: self.ir_para_pagina(p),
                              subtexto=resumo, icone="")

    # =====================================================================
    # BUSCA
    # =====================================================================
    def alternar_busca(self, _=None):
        if self.barra_busca.winfo_ismapped():
            self.barra_busca.grid_remove()
            self._limpar_destaques_busca()
            self.resultados_busca = []
            self.indice_resultado = -1
            C.marcar_ativo(self.btn_busca, False)
        else:
            self.barra_busca.grid()
            self.entry_busca.focus_set()
            C.marcar_ativo(self.btn_busca, True)

    def executar_busca(self):
        termo = self.entry_busca.get().strip()
        if len(termo) < 2:
            self.lbl_busca_status.configure(text="Digite ao menos 2 letras")
            return
        if not self.livro:
            return

        self._cancelar_thread_busca()
        self.lbl_busca_status.configure(text="Buscando…")
        caminho = self.livro["caminho_pdf"]
        cancelar = threading.Event()
        self._cancelar_busca = cancelar

        indice = self._indice

        def trabalhador():
            # Com o indice montado a busca e uma so: literal, por conceito e
            # expandida no mesmo resultado. Ter duas buscas separadas fazia
            # metade do que o livro sabe ficar inacessivel pelo Ctrl+F, que e
            # o caminho que todo mundo usa.
            if indice is not None and indice.pronto:
                try:
                    achados = [(b.pagina, _resumo_do_bloco(b, origem))
                               for b, _s, origem in indice.buscar_unificado(termo)]
                    if not cancelar.is_set():
                        self._na_ui(lambda: self._concluir_busca(termo, achados, cancelar))
                    return
                except Exception as erro:
                    print(f"[Leitor] Busca pelo indice falhou: {erro}")

            # Indice ainda montando: cai na varredura literal do documento.
            # Handle proprio: o PyMuPDF nao e seguro para uso simultaneo do
            # mesmo documento em duas threads.
            try:
                with abrir_livro(caminho) as doc:
                    achados = self.pdf_service.buscar_no_documento(doc, termo, cancelar)
            except Exception as erro:
                achados = []
                print(f"[Leitor] Busca falhou: {erro}")
            if not cancelar.is_set():
                self._na_ui(lambda: self._concluir_busca(termo, achados, cancelar))

        self._thread_busca = threading.Thread(target=trabalhador, daemon=True)
        self._thread_busca.start()

    def _cancelar_thread_busca(self):
        if self._cancelar_busca is not None:
            self._cancelar_busca.set()
            self._cancelar_busca = None

    def _concluir_busca(self, termo, achados, cancelar=None):
        # Uma busca antiga que terminou depois de outra comecar (ou de trocar
        # de livro) nao pode sobrescrever o resultado atual.
        if cancelar is not None and (cancelar.is_set()
                                     or cancelar is not self._cancelar_busca):
            return
        self.resultados_busca = achados
        self.indice_resultado = -1
        self.termo_busca = termo

        if not achados:
            self.lbl_busca_status.configure(text="Nenhum resultado")
            C.Toast.mostrar(self, f"'{termo}' nao encontrado no livro.", "info")
            return

        self.lbl_busca_status.configure(text=f"{len(achados)} resultados")
        self._abrir_painel("Busca")
        self.navegar_resultado(1)

    def _preencher_resultados_busca(self):
        if not self.resultados_busca:
            C.rotulo(self.conteudo_painel,
                     "Use Ctrl+F e digite o termo.\n\nA busca varre o livro inteiro, "
                     "nao apenas a pagina aberta.", 11, "normal", "texto_suave",
                     wraplength=188, justify="left").pack(padx=ESP["md"], pady=ESP["lg"])
            return

        agrupado = {}
        for pagina, trecho in self.resultados_busca:
            agrupado.setdefault(pagina, []).append(trecho)

        for pagina, trechos in list(agrupado.items())[:120]:
            resumo = trechos[0].replace("\n", " ")
            resumo = resumo if len(resumo) <= 64 else resumo[:62] + "…"
            marcador = f" ({len(trechos)}×)" if len(trechos) > 1 else ""
            self._item_painel(f"Pagina {pagina + 1}{marcador}",
                              lambda p=pagina: self.ir_para_pagina(p),
                              subtexto=resumo)

    def navegar_resultado(self, passo):
        if not self.resultados_busca:
            return
        self.indice_resultado = (self.indice_resultado + passo) % len(self.resultados_busca)
        pagina, _ = self.resultados_busca[self.indice_resultado]
        self.lbl_busca_status.configure(
            text=f"{self.indice_resultado + 1} de {len(self.resultados_busca)}")
        if pagina != self.pagina_atual:
            self.ir_para_pagina(pagina)
        self._destacar_ocorrencias_busca(rolar=True)

    def _limpar_destaques_busca(self):
        try:
            self.txt.tag_remove("achado", "1.0", "end")
        except Exception:
            pass

    def _destacar_ocorrencias_busca(self, rolar=False):
        termo = getattr(self, "termo_busca", "")
        self._limpar_destaques_busca()
        if not termo or not self.resultados_busca:
            return
        indice = "1.0"
        primeiro = None
        regiao = self._regiao(self.pagina_atual)
        inicio_pagina = regiao[1] if regiao else 0
        # Entre as palavras pode haver espaco, tab ou quebra (texto
        # justificado): a busca aceita qualquer um deles.
        palavras = [re.sub(r"([\\.^$*+?(){}\[\]|])", r"\\\1", p)
                    for p in termo.split()]
        padrao = r"[ \t\n]+".join(palavras)
        tamanho = tk.IntVar()
        while padrao:
            indice = self.txt.search(padrao, indice, stopindex="end", nocase=True,
                                     regexp=True, count=tamanho)
            if not indice or not tamanho.get():
                break
            fim = f"{indice}+{tamanho.get()}c"
            self.txt.tag_add("achado", indice, fim)
            if primeiro is None and self._para_offset(indice) >= inicio_pagina:
                primeiro = indice
            indice = fim
        if rolar and primeiro:
            self.txt.see(primeiro)

    # =====================================================================
    # GRIFOS, NOTAS E MARCADORES
    # =====================================================================
    def _intervalo_selecionado(self):
        """Devolve (inicio_char, fim_char, texto) da selecao atual."""
        try:
            faixas = self.txt.tag_ranges("sel")
            if not faixas and self._par is not None:
                faixas = self._par.tag_ranges("sel")
            if not faixas:
                return None
            inicio = self._para_offset(faixas[0])
            fim = self._para_offset(faixas[1])
            if fim <= inicio:
                return None
            # O texto vem da string renderizada, e nao do widget: na leitura
            # justificada os espacos viraram tabs e quebras de linha.
            original = getattr(self, "_texto_render", None)
            if original and fim <= len(original):
                trecho = original[inicio:fim].replace(MARCA_IMAGEM, "")
            else:
                trecho = self.txt.get(faixas[0], faixas[1])
            return inicio, fim, trecho
        except Exception:
            return None

    def _para_offset(self, indice):
        # "indices" conta as imagens embutidas (cada uma ocupa uma posicao),
        # como os offsets "1.0+Nc" usados no resto do arquivo. "chars" nao
        # conta -- e o grifo abaixo de uma figura caia deslocado.
        valor = self.txt.count("1.0", indice, "indices")
        if isinstance(valor, (list, tuple)):
            valor = valor[0]
        return int(valor or 0)

    def grifar_selecao(self, _=None):
        selecao = self._intervalo_selecionado()
        if not selecao:
            C.Toast.mostrar(self, "Selecione um trecho primeiro.", "info")
            return
        inicio, fim, _texto = selecao
        # A selecao pode atravessar paginas: um grifo por pagina.
        for numero, ini_local, fim_local, trecho in self._fatiar_por_pagina(inicio, fim):
            self.banco.adicionar_grifo(self.id_livro, numero, ini_local, fim_local, trecho)
        self.area.configure(state="normal")
        self.txt.tag_add("grifo", f"1.0+{inicio}c", f"1.0+{fim}c")
        self.area.configure(state="disabled")
        self._esconder_barra_selecao()
        C.Toast.mostrar(self, "Trecho grifado.", "ok", 1400)

    def remover_grifo(self, _=None):
        selecao = self._intervalo_selecionado()
        if not selecao:
            C.Toast.mostrar(self, "Selecione o trecho grifado.", "info")
            return
        inicio, fim, _texto = selecao
        removidos = sum(
            self.banco.remover_grifos_no_intervalo(self.id_livro, numero, a, b) or 0
            for numero, a, b, _t in self._fatiar_por_pagina(inicio, fim))
        self.area.configure(state="normal")
        self.txt.tag_remove("grifo", "1.0", "end")
        self._aplicar_grifos()
        self.area.configure(state="disabled")
        self._esconder_barra_selecao()
        C.Toast.mostrar(self, f"{removidos} grifo(s) removido(s)." if removidos
                        else "Nenhum grifo nessa selecao.", "ok" if removidos else "info", 1400)

    def _aplicar_grifos(self):
        self.grifos_pagina = self.banco.listar_grifos(self.id_livro, self.pagina_atual)
        regioes = self._regioes or [(self.pagina_atual, 0, self._para_offset("end-1c"))]
        for numero, inicio_reg, fim_reg in regioes:
            for grifo in self.banco.listar_grifos(self.id_livro, numero):
                # Offsets do banco sao LOCAIS da pagina; a regiao os desloca.
                inicio = inicio_reg + max(0, grifo["inicio_char"])
                fim = min(fim_reg, inicio_reg + max(0, grifo["fim_char"]))
                if fim > inicio:
                    try:
                        self.txt.tag_add("grifo", f"1.0+{inicio}c", f"1.0+{fim}c")
                    except Exception:
                        pass

    def _fatiar_por_pagina(self, inicio, fim):
        """[(pagina, inicio_local, fim_local, texto)] de um intervalo global."""
        original = getattr(self, "_texto_render", "") or ""
        regioes = self._regioes or [(self.pagina_atual, 0, len(original))]
        fatias = []
        for numero, inicio_reg, fim_reg in regioes:
            a, b = max(inicio, inicio_reg), min(fim, fim_reg)
            if b > a:
                texto = original[a:b].replace(MARCA_IMAGEM, "")
                fatias.append((numero, a - inicio_reg, b - inicio_reg, texto))
        return fatias

    def copiar_selecao(self, _=None):
        selecao = self._intervalo_selecionado()
        if not selecao:
            return
        self.clipboard_clear()
        self.clipboard_append(selecao[2])
        self._esconder_barra_selecao()
        C.Toast.mostrar(self, "Copiado.", "ok", 1200)

    def abrir_notas(self, _=None):
        if not self.id_livro:
            return
        Modais.abrir_notas(self, self.banco, self.id_livro, self.pagina_atual,
                           ao_salvar=self._apos_salvar_nota)

    def _apos_salvar_nota(self):
        self._atualizar_estado_botoes()
        if self.painel_aberto == "Notas":
            self._preencher_painel("Notas")
        C.Toast.mostrar(self, "Anotacao salva.", "ok", 1400)

    def alternar_favorito(self, _=None):
        if not self.id_livro:
            return
        marcado = self.banco.alternar_favorito(self.id_livro, self.pagina_atual)
        self._atualizar_estado_botoes()
        if self.painel_aberto == "Marcadores":
            self._preencher_painel("Marcadores")
        C.Toast.mostrar(self, "Pagina marcada." if marcado else "Marcador removido.",
                        "ok", 1300)

    # ------------------------------------------------------- barra de selecao
    def _ao_mudar_selecao(self, _=None):
        if self._agendado_selecao:
            try:
                self.after_cancel(self._agendado_selecao)
            except Exception:
                pass
        self._agendado_selecao = self.after(120, self._avaliar_selecao)

    def _avaliar_selecao(self):
        self._agendado_selecao = None
        if self.modo_foco:
            self._esconder_barra_selecao()
            return
        selecao = self._intervalo_selecionado()
        if selecao and len(selecao[2].strip()) >= 2:
            self.barra_selecao.place(relx=0.5, rely=0.97, anchor="s")
            self.barra_selecao.lift()
        else:
            self._esconder_barra_selecao()

    def _esconder_barra_selecao(self):
        try:
            self.barra_selecao.place_forget()
        except Exception:
            pass

    # ================================================== MOTOR DE CONTEXTO
    def _construir_barra_contexto(self):
        """Faixa discreta que sugere um modo com base no que voce usa ao lado.

        Sugere, nao impoe: trocar a aparencia da leitura sozinho seria
        invasivo, e uma sugestao errada aplicada sem aviso e pior que
        nenhuma sugestao.
        """
        self.barra_contexto = C.quadro(self, "superficie_alt", height=38)
        self.barra_contexto.grid(row=4, column=0, sticky="ew")
        self.barra_contexto.grid_columnconfigure(0, weight=1)
        self.barra_contexto.grid_remove()

        self.lbl_contexto = C.rotulo(self.barra_contexto, "", 11, "normal", "texto")
        self.lbl_contexto.grid(row=0, column=0, sticky="w", padx=ESP["md"], pady=ESP["sm"])
        self.btn_ctx_cores = C.botao(
            self.barra_contexto, "Modo + cores",
            lambda: self._aplicar_sugestao(com_cores=True), largura=120, altura=26)
        self.btn_ctx_cores.grid(row=0, column=1, padx=ESP["xs"])
        self.btn_ctx_cores.grid_remove()

        self.btn_ctx_modo = C.botao(
            self.barra_contexto, "So o modo",
            lambda: self._aplicar_sugestao(com_cores=False), largura=90,
            altura=26, variante="secundario")
        self.btn_ctx_modo.grid(row=0, column=2, padx=ESP["xs"])
        self.btn_ctx_recusar = C.botao(
            self.barra_contexto, "Agora nao", self._recusar_sugestao, largura=90,
            altura=26, variante="secundario")
        self.btn_ctx_recusar.grid(row=0, column=3, padx=(0, ESP["md"]))

        # Ocupa a mesma celula: depois de aplicar, "Agora nao" nao faz mais
        # sentido e o que a pessoa quer e o caminho de volta.
        self.btn_ctx_desfazer = C.botao(
            self.barra_contexto, "Desfazer", self._desfazer_sugestao, largura=90,
            altura=26)
        self.btn_ctx_desfazer.grid(row=0, column=3, padx=(0, ESP["md"]))
        self.btn_ctx_desfazer.grid_remove()

    def _monitorar_contexto(self):
        """Verifica a janela ao lado de tempos em tempos, se voce autorizou.

        O laco NUNCA morre enquanto houver livro aberto. Antes ele saia fora
        quando o recurso estava desligado e nao reagendava -- entao ligar a
        opcao nas Configuracoes depois nao adiantava nada, porque nao havia
        mais quem verificasse. A checagem em si e uma consulta a um dicionario
        em memoria, custo zero.
        """
        self._id_contexto = None
        if not self.documento:
            return
        if not self.cfg.obter_bool("contexto_ativo", False):
            self._id_contexto = self.after(2500, self._monitorar_contexto)
            return
        try:
            # A janela AO LADO, nao a em foco: enquanto se le, quem esta em
            # foco e o proprio Lumina, e olhar para ele nao diz nada.
            janela, retangulo = contexto.janela_ao_lado(self._retangulo_proprio())
            if janela.processo and "lumina" not in janela.processo:
                # Trocou a janela do lado? Entao as recusas anteriores eram
                # sobre OUTRA coisa e nao valem mais -- perguntar de novo e o
                # comportamento certo. Sem isto, um "agora nao" calava o
                # leitor para sempre, mesmo depois de mudar tudo ao lado.
                if janela.assinatura != self._assinatura_vizinha:
                    self._assinatura_vizinha = janela.assinatura
                    self._contexto_recusado = None
                    self._cores_vindas_de = None

                previa = contexto.analisar(janela, self.perfil_detectado,
                                           self._memoria_contexto)

                muda_modo = (previa.vale() and previa.render != self.render_livro
                             and previa.render != self._contexto_recusado)
                # As regras nao se decidiram. E exatamente aqui que o palpite
                # do modelo local tem o que acrescentar -- e so aqui, porque
                # em todo o resto ele custaria tempo para repetir a tabela.
                consultar = (previa.empatado and not muda_modo
                             and self._contexto_recusado is None)
                # As cores valem por si. O caso mais comum e justamente este:
                # o livro JA esta no modo certo, mas a aparencia nao combina
                # com o programa ao lado -- e antes a barra ficava calada.
                cores_novas = (self._cores_vindas_de != janela.processo
                               and janela.processo != self._contexto_recusado)

                if muda_modo or cores_novas or consultar:
                    self._analisar_a_fundo(janela, retangulo, muda_modo,
                                           consultar)
        except Exception as erro:
            print(f"[Contexto] {erro}")
        self._id_contexto = self.after(2500, self._monitorar_contexto)

    def _analisar_a_fundo(self, janela, retangulo, muda_modo, consultar=False):
        """A parte cara da analise, fora da thread que desenha.

        Aqui moram os ~100 ms de leitura de cores da tela e, no caso ambiguo,
        cerca de um segundo de modelo local. Somados, sao mais de um segundo a
        cada 2,5 s de monitoramento -- com a janela congelada em todos eles se
        isto rodasse onde estava antes.
        """
        if self._analise_em_curso:
            return
        self._analise_em_curso = True

        def trabalhador():
            try:
                # O texto da janela ao lado, quando autorizado. Vem da arvore
                # de acessibilidade do Windows -- sem OCR, sem capturar tela --
                # e e o sinal mais forte que existe aqui: diz o ASSUNTO, nao
                # so o nome do programa.
                conteudo = ""
                if self.cfg.obter_bool("ler_tela", False):
                    conteudo = tela.texto_em_cache(janela.alca, janela.assinatura)
                # O modelo so e passado quando ha empate para desfazer. Nos
                # outros casos a mesma funcao roda sem tocar em IA nenhuma.
                sugestao = contexto.analisar(
                    janela, self.perfil_detectado, self._memoria_contexto,
                    retangulo_janela=retangulo, texto_tela=conteudo,
                    gerente_ia=self.ia_manager if consultar else None)
            except Exception as erro:
                print(f"[Contexto] analise em segundo plano: {erro}")
                sugestao = None
            try:
                self._na_ui(lambda: self._concluir_analise(sugestao, muda_modo))
            except Exception:
                self._analise_em_curso = False

        threading.Thread(target=trabalhador, daemon=True).start()

    def _concluir_analise(self, sugestao, muda_modo):
        self._analise_em_curso = False
        if sugestao is None or not self.documento:
            return

        # A passada barata nao viu o conteudo da tela nem consultou o modelo,
        # entao a decisao sobre trocar de modo tem que ser refeita aqui. O
        # limiar e mais baixo de proposito: tanto o palpite quanto a leitura
        # da tela entram num empate, e empate nunca produz confianca alta --
        # exigir os mesmos 55% das regras faria isto rodar e nunca aparecer.
        if (not muda_modo and (sugestao.palpite or sugestao.leu_tela)
                and sugestao.vale(MINIMO_PALPITE)
                and sugestao.render != self.render_livro
                and sugestao.render != self._contexto_recusado):
            muda_modo = True

        if not muda_modo:
            # Sem troca de modo, a sugestao e so de aparencia.
            sugestao.render = self.render_livro
        if muda_modo or sugestao.cores:
            self._mostrar_sugestao(sugestao, so_cores=not muda_modo)

    def _retangulo_proprio(self):
        try:
            raiz = self.winfo_toplevel()
            return (raiz.winfo_rootx(), raiz.winfo_rooty(),
                    raiz.winfo_rootx() + raiz.winfo_width(),
                    raiz.winfo_rooty() + raiz.winfo_height())
        except Exception:
            return None

    def _mostrar_sugestao(self, sugestao, so_cores=False):
        # Uma sugestao nova cancela a oferta de desfazer a anterior: a barra
        # nao pode mostrar "Desfazer" ao lado de um texto que ja e outro.
        self._cancelar_desfazer()
        self._antes_da_sugestao = None
        self.btn_ctx_desfazer.grid_remove()
        self.btn_ctx_recusar.grid()

        self._sugestao_atual = sugestao
        self._sugestao_so_cores = so_cores
        nome = RENDERIZACOES.get(sugestao.render, {}).get("nome", sugestao.render)
        origem = sugestao.janela.processo or "janela ao lado"

        if so_cores:
            texto = f"Detectei {origem} ao lado · usar as cores dele na leitura?"
        elif sugestao.leu_tela:
            extra = "  ·  com as cores dele" if sugestao.cores else ""
            texto = (f"Li o que esta em {origem} ao lado: parece assunto de "
                     f"{nome.lower()}. Usar esse modo{extra}?")
        elif sugestao.palpite:
            # Palpite tem que se apresentar como palpite. Dizer "85% de
            # certeza" sobre um desempate de modelo pequeno seria mentir para
            # quem confia na barra.
            extra = "  ·  com as cores dele" if sugestao.cores else ""
            texto = (f"{origem} ao lado, mas as pistas se dividiram. "
                     f"A IA local apostaria em modo {nome}{extra} — testar?")
        else:
            extra = "  ·  com as cores dele" if sugestao.cores else ""
            texto = (f"Detectei {origem} · usar modo {nome}{extra}?  "
                     f"({int(sugestao.confianca * 100)}% — {sugestao.motivo})")
        self.lbl_contexto.configure(text=texto)

        # O botao de cores so aparece quando ha cores para aplicar.
        if sugestao.cores:
            self.btn_ctx_cores.grid()
            # Se voce ja quis as cores desse programa antes, ela vem como a
            # opcao principal -- nao faz sentido perguntar de novo do zero.
            prefere = self._memoria_contexto.prefere_cores(sugestao.janela.processo)
            self.btn_ctx_cores.configure(
                fg_color=T.destaque if prefere is not False else T.superficie_alt)
        else:
            self.btn_ctx_cores.grid_remove()
        # Numa sugestao so de aparencia nao ha "modo" a aplicar sozinho.
        if so_cores:
            self.btn_ctx_cores.configure(text="Usar as cores")
            self.btn_ctx_modo.grid_remove()
        else:
            self.btn_ctx_cores.configure(text="Modo + cores")
            self.btn_ctx_modo.grid()
        self.barra_contexto.grid()

    def _aplicar_sugestao(self, com_cores=False):
        sugestao = self._sugestao_atual
        if not sugestao:
            self.barra_contexto.grid_remove()
            return

        # Fotografia do que valia ANTES. Toda aplicacao e um teste: aceitar
        # uma sugestao nao pode ser uma decisao sem volta, ainda mais quando
        # quem sugeriu foi um palpite de modelo pequeno.
        self._antes_da_sugestao = {
            "render": (self.prefs_livro.get("render") or "auto"),
            "categoria": (self.prefs_livro.get("categoria") or ""),
            "tema": self.cfg.obter("tema"),
            "cor_papel": self.cfg.obter("cor_papel"),
            "cor_texto": self.cfg.obter("cor_texto"),
            "cor_destaque": self.cfg.obter("cor_destaque"),
        }

        self.definir_render(sugestao.render)
        if sugestao.categoria:
            self.definir_categoria(sugestao.categoria)

        aviso = "Modo aplicado e memorizado."
        if com_cores and sugestao.cores:
            papel, texto, destaque = sugestao.cores
            self.cfg.definir("cor_papel", papel)
            self.cfg.definir("cor_texto", texto)
            self.cfg.definir("cor_destaque", destaque)
            self.cfg.definir("tema", TEMA_PERSONALIZADO)
            self.controlador.aplicar_tema(TEMA_PERSONALIZADO)
            self._cores_vindas_de = sugestao.janela.processo
            aviso = (f"Cores de {sugestao.janela.processo} aplicadas."
                     if self._sugestao_so_cores
                     else f"Modo e cores de {sugestao.janela.processo} aplicados.")

        # O aprendizado vive DESTA confirmacao: e ela que faz o acerto subir.
        self._memoria_contexto.registrar(sugestao.janela.processo,
                                         sugestao.categoria, sugestao.render)
        if sugestao.cores:
            self._memoria_contexto.registrar_cores(sugestao.janela.processo,
                                                   com_cores)
            if not com_cores:
                # Nao insiste com as cores deste programa nesta sessao.
                self._cores_vindas_de = sugestao.janela.processo
        self._oferecer_desfazer(aviso)

    # ------------------------------------------------------------ desfazer
    def _oferecer_desfazer(self, aviso):
        """A mesma barra vira o caminho de volta, sem virar mais um botao.

        Um botao "aplicar como teste" ao lado de "aplicar" obrigaria a pessoa
        a decidir, antes de ver, se aquilo era um teste. Aqui toda aplicacao
        ja e reversivel e a volta fica onde o olho acabou de estar.
        """
        self.lbl_contexto.configure(text=f"{aviso}  Nao gostou?")
        self.btn_ctx_cores.grid_remove()
        self.btn_ctx_modo.grid_remove()
        self.btn_ctx_recusar.grid_remove()
        self.btn_ctx_desfazer.grid()
        self.barra_contexto.grid()

        self._cancelar_desfazer()
        # Some sozinha: uma barra parada para sempre no rodape vira ruido.
        self._id_desfazer = self.after(SEGUNDOS_DESFAZER * 1000,
                                       self._encerrar_desfazer)

    def _cancelar_desfazer(self):
        if self._id_desfazer is not None:
            try:
                self.after_cancel(self._id_desfazer)
            except Exception:
                pass
            self._id_desfazer = None

    def _encerrar_desfazer(self):
        self._id_desfazer = None
        self._antes_da_sugestao = None
        self.btn_ctx_desfazer.grid_remove()
        self.btn_ctx_recusar.grid()
        self.barra_contexto.grid_remove()

    def _desfazer_sugestao(self):
        """Devolve modo, categoria e cores ao que eram antes do teste."""
        antes = self._antes_da_sugestao
        self._cancelar_desfazer()
        if not antes:
            self._encerrar_desfazer()
            return

        sugestao = self._sugestao_atual
        if sugestao:
            # Desfazer e uma correcao: a memoria precisa saber, senao o leitor
            # sugeriria a mesma coisa na proxima volta do monitor -- ou pior,
            # a proxima aplicacao ja teria memoria >= 2 empurrando de volta
            # exatamente o que acabou de ser rejeitado.
            self._contexto_recusado = sugestao.render
            self._cores_vindas_de = sugestao.janela.processo
            self._memoria_contexto.desfazer(sugestao.janela.processo,
                                            sugestao.categoria, sugestao.render)
            self._memoria_contexto.registrar_cores(sugestao.janela.processo, False)

        if self.id_livro:
            self.banco.definir_preferencia(self.id_livro, "render", antes["render"])
            self.prefs_livro["render"] = antes["render"]
            self.banco.definir_preferencia(self.id_livro, "categoria",
                                           antes["categoria"])
            self.prefs_livro["categoria"] = antes["categoria"]

        for chave in ("cor_papel", "cor_texto", "cor_destaque"):
            if antes.get(chave):
                self.cfg.definir(chave, antes[chave])
        if antes.get("tema"):
            self.cfg.definir("tema", antes["tema"])
            self.controlador.aplicar_tema(antes["tema"])

        self._chave_margem = None
        self._assinatura_tags = None
        self._encerrar_desfazer()
        self.renderizar()
        C.Toast.mostrar(self, "Voltou como estava.", "ok", 1800)

    def _recusar_sugestao(self):
        """"Agora nao" cala TUDO ate a janela ao lado mudar.

        Silenciar so o modo fazia a barra voltar no mesmo instante oferecendo
        as cores -- o usuario dizia nao e era perguntado de novo em seguida.
        """
        sugestao = self._sugestao_atual
        if sugestao:
            self._contexto_recusado = sugestao.render
            self._cores_vindas_de = sugestao.janela.processo
        self.barra_contexto.grid_remove()

    # ================================================== LEITURA EM VOZ ALTA
    def alternar_narracao(self, _=None):
        if self.narrador.falando:
            self.narrador.parar()
            self._narracao_terminou()
            return
        if not self.narrador.disponivel():
            C.Toast.mostrar(self, "Este sistema nao tem sintese de voz.", "erro")
            return
        texto = self._texto_da_pagina()
        if len(texto.strip()) < 10:
            C.Toast.mostrar(self, "Nada para ler nesta pagina.", "info")
            return

        volume = self.cfg.obter_int("voz_volume", 160)
        velocidade = self.cfg.obter_int("voz_velocidade", 0)
        tom = self.cfg.obter_int("voz_tom", 0)
        nome_voz = self.cfg.obter("voz_nome", "") or None

        # A thread da vigia avisa o fim; volta pela fila da interface antes de
        # tocar em qualquer widget.
        def terminou():
            try:
                self._na_ui(self._narracao_terminou)
            except Exception:
                pass

        if volume > 100 or tom:
            # Acima de 100% (ou com mudanca de tom) e preciso sintetizar em
            # arquivo para tratar a onda. A sintese roda em outra thread: a
            # leitura continua rolando enquanto o audio e preparado.
            def caiu_para_comum():
                """O caminho amplificado nao deu; le no volume normal."""
                def retomar():
                    self.narrador.falar(texto, voz=nome_voz,
                                        velocidade=velocidade,
                                        ao_terminar=terminou, volume=100)
                    C.Toast.mostrar(self, "Narrando no volume normal.", "info", 1600)
                try:
                    self._na_ui(retomar)
                except Exception:
                    pass

            C.Toast.mostrar(self, "Preparando a narracao…", "info", 1800)
            self.narrador.falar_alto(
                texto, voz=nome_voz, velocidade=velocidade,
                ganho=volume / 100.0, tom=tom, ao_terminar=terminou,
                ao_falhar=caiu_para_comum)
        else:
            self.narrador.falar(texto, voz=nome_voz, velocidade=velocidade,
                                ao_terminar=terminou, volume=volume)
        C.trocar_icone(self.btn_voz, "pause")
        C.marcar_ativo(self.btn_voz, True)
        C.Toast.mostrar(self, "Lendo em voz alta — Ctrl+L para parar.", "ok", 1800)

    def _narracao_terminou(self):
        try:
            C.trocar_icone(self.btn_voz, "play")
            C.marcar_ativo(self.btn_voz, False)
        except Exception:
            pass

    # ============================================ VARREDURA EM SEGUNDO PLANO
    def _iniciar_varredura(self):
        """Cataloga figuras, datas e artigos sem travar a leitura.

        Thread com handle proprio do PyMuPDF: o documento aberto na interface
        nao pode ser lido por duas threads ao mesmo tempo.
        """
        if not self.livro:
            return
        caminho = self.livro["caminho_pdf"]
        cancelar = threading.Event()
        self._cancelar_varredura = cancelar
        livro_alvo = self.id_livro

        def trabalhador():
            try:
                # Handle proprio: o documento da interface nao aguenta duas
                # threads. O motor tambem e refeito para nao competir com o
                # que a leitura esta usando.
                with abrir_livro(caminho) as doc:
                    motor = MotorReflow(doc)
                    resultado = perfil.montar_perfil(doc, motor, cancelar=cancelar)
            except Exception as erro:
                print(f"[Leitor] Pipeline de deteccao falhou: {erro}")
                return
            if not cancelar.is_set():
                # Nao chamar `after` daqui: agendar do lado de fora da thread
                # principal levanta "main thread is not in main loop". A
                # thread so DEPOSITA o resultado; quem agenda e a interface.
                with self._trava_varredura:
                    self._resultado_varredura = (livro_alvo, resultado)

        threading.Thread(target=trabalhador, daemon=True).start()
        self.after(300, self._checar_varredura)

    def _checar_varredura(self):
        """Sondagem na thread principal pelo resultado do pipeline."""
        with self._trava_varredura:
            pendente = self._resultado_varredura
            self._resultado_varredura = None
        if pendente is not None:
            self._varredura_pronta(*pendente)
            return
        if self._cancelar_varredura is not None and not self._cancelar_varredura.is_set():
            self.after(300, self._checar_varredura)

    def _varredura_pronta(self, livro_alvo, resultado):
        # O usuario pode ter trocado de livro enquanto a varredura rodava.
        if livro_alvo != self.id_livro:
            return
        self.perfil_detectado = resultado
        for aba, itens in resultado.paineis.items():
            if itens:
                self.registrar_extra(aba, itens)

    # ======================================================= COPIAR CODIGO
    def _ancorar_botoes_de_codigo(self):
        """Botoes no cabecalho de cada bloco, como num editor de codigo.

        O atalho da pagina inteira nao resolvia o caso real: numa pagina com
        tres trechos, a pessoa quer UM deles. Os botoes acompanham a rolagem
        porque sao reposicionados a cada movimento da visao.
        """
        for botao in self._botoes_codigo:
            try:
                botao.destroy()
            except Exception:
                pass
        self._botoes_codigo = []

        for dados in self._faixas_codigo:
            faixa = (dados["inicio"], dados["fim"])
            copiar = C.botao_icone(
                self.palco, "copiar", lambda: None,
                "Copiar este codigo (sem os prompts)", tamanho=24)
            # O comando so pode ser montado depois que o botao existe: ele
            # precisa de uma referencia a si mesmo para trocar de icone.
            copiar.configure(command=lambda t=dados["texto"], b=copiar:
                             self._copiar_bloco(t, b))
            copiar._offset_codigo = dados["cabecalho"]
            copiar._coluna = 0
            copiar._faixa = faixa
            self._botoes_codigo.append(copiar)

            enviar = C.botao_icone(
                self.palco, "mover", lambda t=dados["texto"]: self._enviar_ao_editor(t),
                "Copiar e ir para o VS Code (cole com Ctrl+V)", tamanho=24)
            enviar._offset_codigo = dados["cabecalho"]
            enviar._coluna = 2 if dados["dobravel"] else 1
            enviar._faixa = faixa
            self._botoes_codigo.append(enviar)

            if dados["dobravel"]:
                dobrar = C.botao_icone(
                    self.palco, "cima" if not dados["dobrado"] else "baixo",
                    lambda i=dados["indice"]: self._alternar_dobra(i),
                    "Recolher/expandir este bloco", tamanho=24)
                dobrar._offset_codigo = dados["cabecalho"]
                dobrar._coluna = 1
                dobrar._faixa = faixa
                self._botoes_codigo.append(dobrar)
        for artigo in self._faixas_artigo:
            botao = C.botao_icone(
                self.palco, "cima" if not artigo["dobrado"] else "baixo",
                lambda i=artigo["indice"]: self._alternar_dobra_artigo(i),
                f"Recolher/expandir {artigo['rotulo']}", tamanho=24)
            botao._offset_codigo = artigo["offset"]
            botao._coluna = 0
            # Sem faixa: o botao do artigo nao depende do ponteiro. Recolher
            # a lei inteira e uma acao de leitura, nao de edicao -- some-lo
            # atras do mouse esconderia o recurso de quem nunca soube dele.
            botao._faixa = None
            self._botoes_codigo.append(botao)

        # Entrar no botao dispara o <Leave> do texto; sem segurar aqui, ele
        # sumiria no exato movimento de ir clicar nele.
        for botao in self._botoes_codigo:
            botao.bind("<Enter>", lambda e: self._cancelar_saida_do_bloco(), add=True)
            botao.bind("<Leave>", self._ao_sair_do_texto, add=True)

        self._reposicionar_botoes_codigo()
        # De novo com o layout ja assentado. A primeira passada acontece com a
        # largura que o widget tinha ANTES de o Tk aplicar a coluna nova, e os
        # botoes acabavam ancorados na margem vazia, longe do bloco.
        self.after(80, self._reposicionar_botoes_codigo)

    def _alternar_dobra_artigo(self, chave):
        if chave in self._artigo_dobrado:
            self._artigo_dobrado.discard(chave)
        else:
            self._artigo_dobrado.add(chave)
        self.renderizar(manter_rolagem=True)

    def recolher_todos_artigos(self, _=None):
        """Deixa so os cabecalhos: e como se le codigo de lei, saltando de
        artigo em artigo."""
        if not self._faixas_artigo:
            C.Toast.mostrar(self, "Nenhum artigo nesta pagina.", "info")
            return
        todos = {a["indice"] for a in self._faixas_artigo
                 if a.get("pagina") == self.pagina_atual}
        if todos <= self._artigo_dobrado:
            self._artigo_dobrado -= todos
            aviso = "Artigos expandidos."
        else:
            self._artigo_dobrado |= todos
            aviso = f"{len(todos)} artigo(s) recolhido(s)."
        self.renderizar(manter_rolagem=True)
        C.Toast.mostrar(self, aviso, "ok", 1500)

    def _alternar_dobra(self, chave):
        if chave in self._codigo_dobrado:
            self._codigo_dobrado.discard(chave)
        else:
            self._codigo_dobrado.add(chave)
        self.renderizar(manter_rolagem=True)

    def alternar_numeracao_codigo(self, _=None):
        ativo = not self.cfg.obter_bool("numerar_codigo", False)
        self.cfg.definir_bool("numerar_codigo", ativo)
        self.renderizar(manter_rolagem=True)
        C.Toast.mostrar(self, "Numeracao de linhas ligada." if ativo
                        else "Numeracao de linhas desligada.", "ok", 1400)

    def _reposicionar_botoes_codigo(self):
        """Mostra cada botao so quando o bloco dele esta visivel na tela."""
        if not self._botoes_codigo:
            return
        try:
            altura = self.txt.winfo_height()
            largura = self.txt.winfo_width()
        except Exception:
            return

        # Alinhados a borda direita da COLUNA de texto, e nao da janela. Com a
        # coluna estreita e centralizada numa tela larga, ancorar na janela
        # deixava o botao boiando no vazio, a centenas de pixels do bloco a
        # que pertence.
        #
        # A margem sai do PROPRIO widget, e nao de `_margem_atual()`: aquele
        # valor e memorizado e pode estar um passo atrasado logo depois de
        # redimensionar a janela -- que e exatamente quando os botoes sao
        # reposicionados.
        try:
            margem = int(self.txt.cget("padx"))
        except Exception:
            margem = self._margem_atual()
        borda = largura - margem - ESP["sm"]

        # O `place` do CustomTkinter MULTIPLICA x e y pelo fator de escala de
        # tela, enquanto `winfo_width` e `bbox` ja vem em pixels reais. Sem
        # desfazer a conta, numa tela a 125% os botoes iam parar 25% mais a
        # direita e mais abaixo do bloco a que pertencem -- que era justamente
        # o efeito de eles flutuarem na margem vazia.
        try:
            escala = ctk.ScalingTracker.get_widget_scaling(self) or 1.0
        except Exception:
            escala = 1.0

        for botao in self._botoes_codigo:
            # Botao de bloco de codigo so aparece com o ponteiro sobre o bloco.
            # Sempre visiveis, eles poluiam a pagina: numa pagina com cinco
            # trechos eram dez icones flutuando sobre o texto o tempo todo.
            faixa = getattr(botao, "_faixa", None)
            if faixa is not None and faixa != self._bloco_apontado:
                botao.place_forget()
                continue
            try:
                caixa = self.txt.bbox(f"1.0+{botao._offset_codigo}c")
            except Exception:
                caixa = None
            if not caixa or not (0 <= caixa[1] <= altura - 20):
                botao.place_forget()
                continue
            recuo = 30 + getattr(botao, "_coluna", 0) * 28
            botao.place(x=max(0, int((borda - recuo) / escala)),
                        y=max(2, int((caixa[1] + 1) / escala)))
            botao.lift()

    # ------------------------------------------------- ponteiro sobre o bloco
    def _ao_mover_no_texto(self, evento):
        """Descobre sobre qual bloco de codigo o ponteiro esta."""
        if not self._faixas_codigo:
            return
        try:
            posicao = self._para_offset(self.txt.index(f"@{evento.x},{evento.y}"))
        except Exception:
            return
        alvo = None
        for dados in self._faixas_codigo:
            if dados["inicio"] <= posicao < dados["fim"]:
                alvo = (dados["inicio"], dados["fim"])
                break
        self._apontar_bloco(alvo)

    def _apontar_bloco(self, faixa):
        if faixa == self._bloco_apontado:
            return
        self._bloco_apontado = faixa
        self._cancelar_saida_do_bloco()
        self._reposicionar_botoes_codigo()

    def _ao_sair_do_texto(self, _=None):
        """Some com os botoes, mas nao na hora.

        O ponteiro sai do widget de texto justamente ao entrar no botao, que
        e filho do palco. Escondendo na hora, o botao fugia do clique.
        """
        self._cancelar_saida_do_bloco()
        self._saida_do_bloco = self.after(260, lambda: self._apontar_bloco(None))

    def _cancelar_saida_do_bloco(self):
        if self._saida_do_bloco is not None:
            try:
                self.after_cancel(self._saida_do_bloco)
            except Exception:
                pass
            self._saida_do_bloco = None

    def _copiar_bloco(self, texto, botao=None):
        limpo = codigo_util.para_area_transferencia(texto)
        self.clipboard_clear()
        self.clipboard_append(limpo)
        C.Toast.mostrar(self, "Codigo copiado, sem os prompts.", "ok", 1500)
        # O aviso aparece no rodape; a confirmacao tambem no proprio botao,
        # que e para onde o olho estava olhando quando clicou.
        if botao is not None:
            self._confirmar_no_botao(botao)

    def _confirmar_no_botao(self, botao):
        try:
            C.trocar_icone(botao, "confirmado")
        except Exception:
            return

        def restaurar():
            # O botao pode ter sido destruido por uma virada de pagina.
            try:
                if botao.winfo_exists():
                    C.trocar_icone(botao, "copiar")
            except Exception:
                pass

        self.after(1200, restaurar)

    def copiar_codigo_da_pagina(self, _=None):
        """Junta os blocos de codigo da pagina, limpos para colar num editor."""
        blocos = [d["texto"] for d in self._faixas_codigo
                  if d.get("pagina", self.pagina_atual) == self.pagina_atual]
        if not blocos:
            C.Toast.mostrar(self, "Nenhum bloco de codigo nesta pagina.", "info")
            return
        limpo = "\n\n".join(codigo_util.para_area_transferencia(b) for b in blocos)
        self.clipboard_clear()
        self.clipboard_append(limpo)
        C.Toast.mostrar(self, f"{len(blocos)} bloco(s) copiado(s) sem os prompts.",
                        "ok", 1800)

    # =====================================================================
    # ASSISTENTE DE IA
    # =====================================================================
    def alternar_assistente(self, _=None):
        if self.painel_ia.winfo_ismapped():
            self.painel_ia.grid_remove()
            C.marcar_ativo(self.btn_ia, False)
        else:
            self.painel_ia.grid()
            C.marcar_ativo(self.btn_ia, True)
            self._atualizar_status_ia()
            # O rotulo do indice so era preenchido pelo callback do seletor de
            # acao -- que nunca dispara quando "Conversar com o livro" ja e o
            # valor inicial. Resultado: no caso mais comum, o painel abria sem
            # dizer se havia indice.
            self._atualizar_rotulo_indice()
        self._ajustar_coluna()

    def _atualizar_status_ia(self):
        try:
            # Versao rapida: a completa pode ligar a IA local e congelar a
            # janela ate o modelo carregar.
            self.lbl_ia_status.configure(
                text=f"Provedor: {self.ia_manager.descrever_fila_rapido()}")
        except Exception:
            pass

    def _ao_trocar_acao_ia(self, valor):
        precisa_pergunta = valor in ("Responder pergunta", ACAO_CONVERSAR)
        if precisa_pergunta:
            self.entry_pergunta.grid()
        else:
            self.entry_pergunta.grid_remove()
        # O seletor de origem so faz sentido conversando com o livro: as
        # outras acoes sao sempre sobre a pagina aberta.
        if valor == ACAO_CONVERSAR:
            self.combo_origem.grid()
            self.lbl_indice.grid()
            self._atualizar_rotulo_indice()
        else:
            self.combo_origem.grid_remove()
            self.lbl_indice.grid_remove()

    # ------------------------------------------------------- indice do livro
    def _atualizar_rotulo_indice(self):
        if self._indice is None:
            texto = "Indice: montando…"
        elif self._indice.concluido:
            texto = (f"Indice: {len(self._indice.blocos)} trechos de "
                     f"{self._indice.total_paginas} paginas")
        else:
            texto = f"Indice: {int(self._indice.progresso() * 100)}% do livro"
        try:
            self.lbl_indice.configure(text=texto)
        except Exception:
            pass

    def _iniciar_indice(self):
        """Monta (ou carrega) o indice do livro sem travar a leitura.

        Le do cache quando existe: um livro ja indexado abre pronto. Quando
        nao existe, indexa a partir da PAGINA ATUAL -- as perguntas dos
        proximos minutos sao sobre o que esta na tela, e esperar mil paginas
        para poder perguntar sobre a pagina aberta seria esperar por nada.
        """
        if not self.id_livro or not self.documento:
            return
        id_livro = self.id_livro
        caminho = (self.livro or {}).get("caminho_pdf")
        pagina = self.pagina_atual
        cancelar = threading.Event()
        self._cancelar_indice = cancelar

        def trabalhador():
            pronto = indice_livro.IndiceLivro.carregar(id_livro)
            if pronto is not None and pronto.concluido:
                self._na_ui(lambda: self._indice_pronto(id_livro, pronto))
                return
            try:
                # Documento proprio: o fitz.Document nao e seguro para uso
                # simultaneo, e a interface segue lendo o dela.
                with abrir_livro(caminho) as copia:
                    novo = indice_livro.construir(
                        copia, id_livro, cancelar=cancelar, a_partir_de=pagina,
                        ao_progredir=lambda f: self._na_ui(
                            lambda: self._rotulo_progresso_indice(id_livro, f)))
            except Exception as erro:
                print(f"[Indice] Falha ao construir: {erro}")
                return
            if not cancelar.is_set():
                novo.salvar()
                self._na_ui(lambda: self._indice_pronto(id_livro, novo))

        threading.Thread(target=trabalhador, daemon=True).start()

    def _rotulo_progresso_indice(self, id_livro, fracao):
        # O self._indice so existe no fim; o progresso vem pela fracao.
        if id_livro != self.id_livro or self._indice is not None:
            return
        try:
            self.lbl_indice.configure(text=f"Indice: {int(fracao * 100)}% do livro")
        except Exception:
            pass

    def _indice_pronto(self, id_livro, indice):
        # A pessoa pode ter trocado de livro enquanto o indice era montado.
        if id_livro != self.id_livro:
            return
        self._indice = indice
        self._atualizar_rotulo_indice()

    def perguntar_sobre_selecao(self, _=None):
        selecao = self._intervalo_selecionado()
        if not self.painel_ia.winfo_ismapped():
            self.alternar_assistente()
        if selecao:
            self._texto_ia_forcado = selecao[2]
            self.combo_acao.set("Explicar de forma simples")
            self._ao_trocar_acao_ia("Explicar de forma simples")
            self.executar_ia()
        self._esconder_barra_selecao()

    def executar_ia(self, _=None):
        acao = self.combo_acao.get()
        pergunta = self.entry_pergunta.get().strip()

        # Conversar com o livro nao manda a pagina aberta ao modelo: quem
        # decide o que e relevante e o indice, e ele pode achar a resposta a
        # duzentas paginas daqui.
        if acao == ACAO_CONVERSAR:
            if not pergunta:
                self._escrever_ia("Escreva a pergunta sobre o livro.")
                self.entry_pergunta.focus_set()
                return
            self._conversar_com_livro(pergunta)
            return
        if acao == ACAO_CODIGO:
            self._relacionar_com_codigo(pergunta)
            return

        texto = getattr(self, "_texto_ia_forcado", None) or self._texto_da_pagina()
        self._texto_ia_forcado = None

        if len(texto.strip()) < 40:
            self._escrever_ia("Esta pagina nao tem texto suficiente para analise.\n\n"
                              "Se for uma pagina digitalizada, o conteudo e uma imagem "
                              "e nao ha camada de texto para processar.")
            return
        if acao == "Responder pergunta" and not pergunta:
            self._escrever_ia("Digite sua pergunta no campo acima.")
            self.entry_pergunta.focus_set()
            return

        if self._ia_ocupada:
            return
        self._ia_ocupada = True
        self.btn_ia_processar.configure(state="disabled", text="Processando…")
        self._escrever_ia("Processando em segundo plano…")
        id_livro = self.id_livro

        def trabalhador():
            # Sempre devolve algo: sem o try, uma excecao deixava o botao
            # preso em "Processando…" para sempre.
            try:
                resultado = self.ia_manager.executar_acao(acao, texto, pergunta)
            except Exception as erro:
                resultado = f"Falha ao processar: {erro}"
            self._na_ui(lambda: self._concluir_ia(resultado, id_livro))

        threading.Thread(target=trabalhador, daemon=True).start()

    def _conversar_com_livro(self, pergunta):
        if self._ia_ocupada:
            return
        self._ia_ocupada = True
        id_livro = self.id_livro
        modo = ORIGENS_PERGUNTA.get(self.combo_origem.get(), "auto")
        self.btn_ia_processar.configure(state="disabled", text="Procurando…")
        self._escrever_ia("Procurando no livro…")
        indice = self._indice

        def trabalhador():
            try:
                resposta = roteador.responder(pergunta, indice, self.ia_manager,
                                              modo=modo,
                                              pagina_atual=self.pagina_atual,
                                              conversa=self._conversa)
            except Exception as erro:
                resposta = roteador.Resposta(
                    texto=f"Falha ao responder: {erro}", origem="nenhum")
            self._na_ui(lambda: self._concluir_conversa(resposta, id_livro))

        threading.Thread(target=trabalhador, daemon=True).start()

    def _concluir_conversa(self, resposta, id_livro=None):
        self._ia_ocupada = False
        self.btn_ia_processar.configure(state="normal", text="Processar")
        # Resposta de outro livro (trocou durante a espera): descartar, ou o
        # "Ir a pagina N" levaria a uma pagina do livro errado.
        if id_livro is not None and id_livro != self.id_livro:
            return
        self._ultima_resposta = resposta

        partes = [f"[{resposta.rotulo}]"]
        if resposta.assunto_herdado:
            partes.append(f"Entendi como: “{resposta.assunto_herdado}”")
        if resposta.paginas:
            paginas = ", ".join(str(p + 1) for p in resposta.paginas[:6])
            partes.append(f"Paginas: {paginas}  ·  confianca {int(resposta.confianca * 100)}%")
        partes += ["", resposta.texto]
        if resposta.aviso:
            partes += ["", f"— {resposta.aviso}"]
        if resposta.oferecer_geral:
            partes += ["", "Para perguntar a mesma coisa a IA como conhecimento "
                           "geral, escolha \"So a IA\" acima e processe de novo."]
        self._escrever_ia("\n".join(partes))

        # Ir direto ao trecho: a resposta perde metade do valor se voce ainda
        # tiver que caçar a pagina.
        if resposta.paginas:
            self._pagina_da_resposta = resposta.paginas[0]
            self.btn_ir_resposta.configure(
                text=f"Ir a pagina {resposta.paginas[0] + 1}")
            self.btn_ir_resposta.pack(side="left", padx=ESP["xs"])
        else:
            self.btn_ir_resposta.pack_forget()

    def _ir_para_resposta(self):
        if self._pagina_da_resposta is not None:
            self.ir_para_pagina(self._pagina_da_resposta)

    def _concluir_ia(self, resultado, id_livro=None):
        self._ia_ocupada = False
        self.btn_ia_processar.configure(state="normal", text="Processar")
        if id_livro is not None and id_livro != self.id_livro:
            return
        self._escrever_ia(resultado)

    def _escrever_ia(self, texto):
        self.txt_ia.configure(state="normal")
        self.txt_ia.delete("1.0", "end")
        self.txt_ia.insert("1.0", texto)
        self.txt_ia.configure(state="disabled")

    def _copiar_ia(self):
        conteudo = self.txt_ia.get("1.0", "end-1c")
        if conteudo.strip():
            self.clipboard_clear()
            self.clipboard_append(conteudo)
            C.Toast.mostrar(self, "Resultado copiado.", "ok", 1200)

    # ======================================================= VS CODE
    def _janela_do_editor(self):
        """(alca, titulo) do VS Code (ou Cursor/VSCodium) mais a vista.

        Prefere a janela que divide a tela com o leitor; se ela nao for um
        editor, a primeira janela de editor na ordem de empilhamento.
        """
        candidatas = []
        try:
            vizinha = contexto.janela_vizinha(self._retangulo_proprio())
            if vizinha:
                candidatas.append((vizinha[0], vizinha[2]))
        except Exception:
            pass
        try:
            candidatas += [(a, t) for a, _r, t in contexto._janelas_visiveis()]
        except Exception:
            pass
        for alca, titulo in candidatas:
            if titulo and vscode.interpretar_titulo(titulo):
                return alca, titulo
        return None, None

    def _relacionar_com_codigo(self, pergunta):
        """Explica a pagina do livro aplicada ao arquivo aberto no VS Code.

        Le o ARQUIVO do disco (o VS Code nao publica o texto do editor pela
        acessibilidade) e manda pagina + codigo ao assistente. So acontece
        quando a pessoa pede, e na primeira vez o Lumina avisa que o codigo
        vai para o provedor de IA configurado.
        """
        if self._ia_ocupada:
            return
        if not self.cfg.obter_bool("vscode_consentiu", False):
            def consentiu():
                self.cfg.definir("vscode_consentiu", "1")
                self._relacionar_com_codigo(pergunta)
            Modais.confirmar(
                self, "Enviar seu codigo ao assistente?",
                "O Lumina vai ler o arquivo aberto no VS Code e enviar o "
                "conteudo, junto com esta pagina, ao provedor de IA "
                "configurado (na IA local, nada sai do computador).\n\n"
                "So acontece quando voce escolher esta acao.",
                texto_ok="Entendi, enviar", ao_confirmar=consentiu)
            return
        self._ia_ocupada = True
        self.btn_ia_processar.configure(state="disabled", text="Lendo o VS Code…")
        self._escrever_ia("Procurando o arquivo aberto no VS Code…")
        alca, titulo = self._janela_do_editor()
        pagina = self._texto_da_pagina()[:5000]
        numero = self.pagina_atual + 1
        id_livro = self.id_livro

        def trabalhador():
            try:
                if not titulo:
                    raise ValueError("Nenhuma janela do VS Code aberta.")
                arquivo = vscode.arquivo_ativo(titulo)
                if arquivo is None:
                    raise ValueError("O VS Code nao esta com um arquivo aberto no editor "
                                     "(abra um arquivo e tente de novo).")
                if arquivo.remoto:
                    raise ValueError(f"“{arquivo.nome}” esta num projeto remoto "
                                     "(SSH/WSL): o arquivo nao esta neste computador.")
                if not arquivo.caminho and "." not in arquivo.nome:
                    raise ValueError(f"A aba ativa do VS Code (“{arquivo.nome}”) nao e um "
                                     "arquivo. Clique num arquivo de codigo e tente de novo.")
                if not arquivo.caminho:
                    raise ValueError(f"Nao encontrei “{arquivo.nome}” no disco.")
                codigo = vscode.ler_trecho(arquivo.caminho, max_chars=9000)
                aviso = ("\n(Arquivo com alteracoes nao salvas: usei a versao do disco.)"
                         if arquivo.sujo else "")
                texto = (f"TRECHO DO LIVRO (pagina {numero}):\n{pagina}\n\n"
                         f"ARQUIVO DO LEITOR: {arquivo.nome} ({arquivo.linguagem})\n"
                         f"```{arquivo.linguagem}\n{codigo}\n```")
                q = pergunta or ("Explique como o conteudo desta pagina do livro se aplica "
                                 "a este arquivo. Aponte onde o codigo ja segue o livro, "
                                 "onde diverge e o que poderia melhorar, citando linhas.")
                resposta = self.ia_manager.executar_acao("Responder pergunta", texto, q)
                resultado = f"📎 {arquivo.nome}{aviso}\n\n{resposta}"
            except Exception as erro:
                resultado = str(erro)
            self._na_ui(lambda: self._concluir_ia(resultado, id_livro))

        threading.Thread(target=trabalhador, daemon=True).start()

    def _enviar_ao_editor(self, codigo):
        """Copia o bloco limpo e traz o VS Code para a frente: e so colar."""
        limpo = codigo_util.para_area_transferencia(codigo)
        self.clipboard_clear()
        self.clipboard_append(limpo)
        alca, _titulo = self._janela_do_editor()
        if alca:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                if user32.IsIconic(alca):
                    user32.ShowWindow(alca, 9)          # SW_RESTORE
                user32.SetForegroundWindow(alca)
                C.Toast.mostrar(self, "Codigo copiado — cole no VS Code com Ctrl+V.",
                                "ok", 2200)
                return
            except Exception:
                pass
        C.Toast.mostrar(self, "Codigo copiado. (VS Code nao encontrado.)", "info", 2200)

    # ==================================================== DICIONARIO
    def consultar_dicionario(self, _=None):
        selecao = self._intervalo_selecionado()
        termo = (selecao[2] if selecao else "").strip()
        if not termo:
            C.Toast.mostrar(self, "Selecione uma palavra ou frase.", "info")
            return
        janela = ctk.CTkToplevel(self)
        janela.title("Dicionario")
        janela.geometry(f"380x320+{self.winfo_pointerx() - 190}+{self.winfo_pointery() + 18}")
        janela.configure(fg_color=T.superficie)
        janela.transient(self.winfo_toplevel())
        janela.bind("<Escape>", lambda e: janela.destroy())
        C.rotulo(janela, termo[:60], 15, "bold").pack(fill="x", padx=ESP["md"],
                                                      pady=(ESP["md"], 2))
        rodape = C.rotulo(janela, "Consultando…", 9, "normal", "texto_suave")
        corpo = ctk.CTkTextbox(janela, wrap="word", fg_color=T.superficie_alt,
                               text_color=T.texto, font=ctk.CTkFont(FONTE_UI, 12),
                               corner_radius=RAIO["sm"], border_width=0)
        corpo.pack(fill="both", expand=True, padx=ESP["md"], pady=ESP["sm"])
        rodape.pack(fill="x", padx=ESP["md"], pady=(0, ESP["sm"]))
        janela.after(150, janela.lift)

        def mostrar(consulta):
            if not janela.winfo_exists():
                return
            partes = []
            if consulta.traducao:
                partes.append(f"Traducao ({consulta.idioma_origem} → "
                              f"{consulta.idioma_destino}):\n{consulta.traducao}")
            if consulta.definicoes:
                partes.append("Definicoes:\n" + "\n".join(f"• {d}" for d in consulta.definicoes))
            if consulta.erro and not partes:
                partes.append(consulta.erro)
            corpo.insert("1.0", "\n\n".join(partes) or "Nada encontrado.")
            corpo.configure(state="disabled")
            rodape.configure(text=f"Fonte: {consulta.fonte}" if consulta.fonte else "")

        def trabalhador():
            consulta = dicionario.consultar(termo)
            self._na_ui(lambda: mostrar(consulta))

        threading.Thread(target=trabalhador, daemon=True).start()

    # ======================================================= REVISAO
    def abrir_revisao(self, _=None):
        from ui.revisao import JanelaRevisao

        def ir_para(id_livro, pagina):
            if id_livro == self.id_livro:
                self.ir_para_pagina(pagina)
            else:
                self.banco.definir_pagina(id_livro, pagina)
                self.controlador.mostrar_tela("leitor", id_livro)

        janela = getattr(self, "_janela_revisao", None)
        if janela is not None and janela.winfo_exists():
            janela.lift()
            return
        self._janela_revisao = JanelaRevisao(self, ao_ir_para=ir_para, id_livro=self.id_livro)

    def _texto_da_pagina(self):
        return "\n\n".join(b.texto for b in self.blocos_render
                           if b.texto and b.tipo != "aviso")

    # =====================================================================
    # APARENCIA / MODO FOCO / TELA DIVIDIDA
    # =====================================================================
    def alternar_tipografia(self, _=None):
        if self._popover is not None:
            self._fechar_popover()
            return
        self._popover = PopoverTipografia(self, self.cfg, self._aplicar_preferencias,
                                          self._fechar_popover, leitor=self)
        self._popover.place(relx=1.0, rely=0.0, anchor="ne", x=-6, y=52)
        self._popover.lift()

    def _fechar_popover(self):
        if self._popover is not None:
            try:
                self._popover.destroy()
            except Exception:
                pass
            self._popover = None

    def _aplicar_preferencias(self, mudou_tema=False, mudou_modo=False):
        if mudou_tema:
            self.controlador.aplicar_tema(self.cfg.obter("tema"))
        self._atualizar_duas_folhas()
        if self.documento:
            self.renderizar(manter_rolagem=True)
        else:
            self._ajustar_coluna()
        if mudou_modo:
            self.aplicar_modo_virada()

    def ajustar_fonte(self, delta):
        atual = self.pref_int("tamanho_fonte", TAMANHO_FONTE_PADRAO)
        novo = max(9, min(40, atual + delta))
        if novo != atual:
            self.definir_pref("tamanho_fonte", novo)
            self.renderizar(manter_rolagem=True)
            C.Toast.mostrar(self, f"Fonte: {novo}px", "info", 900)

    def alternar_sempre_no_topo(self, _=None):
        ativo = self.controlador.alternar_sempre_no_topo()
        C.marcar_ativo(self.btn_topo, ativo)
        C.Toast.mostrar(self, "Sempre visivel: ligado" if ativo else "Sempre visivel: desligado",
                        "ok" if ativo else "info", 1400)

    def alternar_modo_foco(self, _=None):
        self.modo_foco = not self.modo_foco
        self._aplicar_visibilidade_foco()

    def sair_modo_foco(self, _=None):
        if self.modo_foco:
            self.modo_foco = False
            self._aplicar_visibilidade_foco()
            return True
        return False

    def _aplicar_visibilidade_foco(self):
        if self.modo_foco:
            self.barra_topo.grid_remove()
            self.barra_baixo.grid_remove()
            self.barra_busca.grid_remove()
            self.painel.grid_remove()
            self.painel_ia.grid_remove()
            self._fechar_popover()
            self._esconder_barra_selecao()
            self.barra_contexto.grid_remove()
            self.lbl_flutuante.place(relx=0.5, rely=0.985, anchor="s")
            self._atualizar_rodape()
            C.Toast.mostrar(self, "Modo foco — F11 ou Esc para sair", "info", 1800)
        else:
            self.barra_topo.grid()
            self.barra_baixo.grid()
            self.lbl_flutuante.place_forget()
            if self.painel_aberto:
                self.painel.grid()
        C.marcar_ativo(self.btn_foco, self.modo_foco)
        self._ajustar_coluna()

    # ---------------------------------------------------------- responsivo
    def _ao_redimensionar(self, evento=None):
        # Atencao: `CTkFrame.bind` registra o evento no canvas interno, entao
        # `evento.widget` nunca e este frame -- comparar os dois desligaria o
        # handler por completo. O canvas acompanha o frame, entao basta medir.
        largura = self.winfo_width()
        if abs(largura - self._largura_anterior) < 6:
            return
        self._largura_anterior = largura

        if self._agendado_layout:
            try:
                self.after_cancel(self._agendado_layout)
            except Exception:
                pass
        self._agendado_layout = self.after(140, self._reagir_largura)

    def _reagir_largura(self):
        self._agendado_layout = None
        # Unico ponto que precisa do layout ja resolvido antes de medir.
        self.update_idletasks()
        largura = self.winfo_width()
        compacto = largura < LIMIAR_COMPACTO

        if compacto != self._compacto:
            self._compacto = compacto
            # Em janela estreita o titulo some para dar lugar as acoes.
            if compacto:
                self.lbl_titulo.grid_remove()
            else:
                self.lbl_titulo.grid()

            # Paineis laterais roubam quase toda a largura util no modo estreito.
            if compacto and largura < 560:
                if self.painel_aberto:
                    self._fechar_painel()
                if self.painel_ia.winfo_ismapped():
                    self.painel_ia.grid_remove()
                    C.marcar_ativo(self.btn_ia, False)

        self._atualizar_duas_folhas()
        self._ajustar_coluna()
        self._estado_setas = None
        self._atualizar_setas()
        # A coluna mudou de largura: os botoes de codigo acompanham a borda
        # dela, entao precisam ser reancorados mesmo sem redesenhar a pagina.
        self._reposicionar_botoes_codigo()
        if self.documento and (self.imagens_render
                              or self.cfg.obter_bool("justificar", True)):
            self.renderizar(manter_rolagem=True)

    # ------------------------------------------------------------- rodape
    def _atualizar_rodape(self):
        atual = self.pagina_atual + 1
        total = max(1, self.total_paginas)
        folha = self._folha_na_pagina() if self._em_folha() else None
        if folha and folha[1] > 1:
            # Em janela estreita o rotulo longo invadia o tempo restante.
            texto = (f"{atual}/{total} · {folha[0]}/{folha[1]}" if self._compacto
                     else f"{atual} / {total}  ·  folha {folha[0]}/{folha[1]}")
            self.lbl_pagina.configure(text=texto)
        else:
            self.lbl_pagina.configure(text=f"{atual} / {total}")
        self.progresso.set(atual / total)

        restantes = total - atual
        minutos = int((restantes * max(1.0, self.media_palavras)) / PALAVRAS_POR_MINUTO)
        if minutos >= 60:
            tempo = f"~{minutos // 60}h {minutos % 60}min restantes"
        elif minutos > 0:
            tempo = f"~{minutos} min restantes"
        else:
            tempo = "Ultima pagina"
        self.lbl_tempo.configure(text=f"{int(atual / total * 100)}%  ·  {tempo}")
        self.lbl_flutuante.configure(text=f"{atual} / {total}")

    def _atualizar_estado_botoes(self):
        if not self.id_livro:
            return
        try:
            self.btn_anterior.configure(state="normal" if self.pagina_atual > 0 else "disabled")
            self.btn_proxima.configure(
                state="normal" if self.pagina_atual < self.total_paginas - 1 else "disabled")
            favorito, tem_nota = self.banco.estado_da_pagina(self.id_livro,
                                                             self.pagina_atual)
            C.marcar_ativo(self.btn_favorito, favorito)
            C.trocar_icone(self.btn_favorito, "estrela_cheia" if favorito else "estrela")
            self.btn_nota.configure(text_color=T.destaque if tem_nota else T.texto)
        except Exception:
            pass

    # ------------------------------------------------------------- tema
    def aplicar_tema(self):
        """Chamado pelo controlador depois de repintar os widgets registrados."""
        try:
            self.barra_selecao.configure(border_color=T.borda)
            if self.documento:
                self.renderizar(manter_rolagem=True)
        except Exception:
            pass


# =========================================================================
# POPOVER DE TIPOGRAFIA
# =========================================================================
class PopoverTipografia(ctk.CTkFrame):
    """Painel flutuante de aparencia. Fica dentro da janela, nao numa Toplevel,
    porque em tela dividida uma janela extra atrapalha mais do que ajuda."""

    def __init__(self, master, cfg, ao_mudar, ao_fechar, leitor=None):
        super().__init__(master, fg_color=T.superficie, corner_radius=RAIO["md"],
                         border_width=1, border_color=T.borda, width=290)
        self.leitor = leitor
        C.registrar_estilo(self, fg_color="superficie", border_color="borda")
        self.cfg = cfg
        self.ao_mudar = ao_mudar
        self.ao_fechar = ao_fechar

        cabecalho = C.quadro(self, "transparent")
        cabecalho.pack(fill="x", padx=ESP["md"], pady=(ESP["md"], ESP["xs"]))
        C.rotulo(cabecalho, "Aparencia e leitura", 12, "bold").pack(side="left")
        C.botao_icone(cabecalho, "fechar", self.ao_fechar, "Fechar", tamanho=24).pack(side="right")

        # Corpo rolavel com altura limitada a janela: sem isso, em tela dividida
        # (ou depois de crescer com a secao de virada) os ultimos controles
        # ficavam abaixo da borda inferior, sem jeito de alcançar.
        try:
            disponivel = master.winfo_height() - 120
        except Exception:
            disponivel = 520
        self.corpo = C.rolavel(self, "superficie", height=max(240, min(560, disponivel)))
        self.corpo.pack(fill="both", expand=True, padx=(0, ESP["xs"]),
                        pady=(0, ESP["sm"]))

        if self.leitor is not None and self.leitor.id_livro:
            self._secao_do_livro()

        self._secao("Virar pagina")
        C.seletor(self.corpo, list(MODOS_VIRADA.keys()), self._definir_modo, largura=250,
                  valor=normalizar_modo_virada(cfg.obter("modo_virada", MODO_VIRADA_PADRAO))).pack(
            fill="x", padx=ESP["md"])
        self.lbl_modo = C.rotulo(
            self.corpo, MODOS_VIRADA.get(normalizar_modo_virada(cfg.obter("modo_virada")), ""), 9, "normal",
            "texto_suave", wraplength=250, justify="left")
        self.lbl_modo.pack(fill="x", padx=ESP["md"], pady=(3, 0))

        self.linha_velocidade = C.quadro(self.corpo, "transparent")
        C.rotulo(self.linha_velocidade, "Velocidade", 10, "normal",
                 "texto_suave").pack(side="left")
        self.lbl_vel = C.rotulo(self.linha_velocidade, "", 10, "bold", ancora="e")
        self.lbl_vel.pack(side="right")
        self.controle_velocidade = ctk.CTkSlider(
            self.corpo, from_=1, to=10, number_of_steps=9, height=16,
            command=self._definir_velocidade,
            button_color=T.destaque, button_hover_color=T.destaque_hover,
            progress_color=T.destaque, fg_color=T.superficie_alt)
        C.registrar_estilo(self.controle_velocidade, button_color="destaque",
                           button_hover_color="destaque_hover",
                           progress_color="destaque", fg_color="superficie_alt")
        self.controle_velocidade.set(cfg.obter_int("velocidade_rolagem",
                                                   VELOCIDADE_ROLAGEM_PADRAO))
        self._atualizar_linha_velocidade()

        self._secao("Tema")
        grade = C.quadro(self.corpo, "transparent")
        grade.pack(fill="x", padx=ESP["md"])
        atual = cfg.obter("tema")
        for indice, nome in enumerate(TEMAS):
            botao = ctk.CTkButton(
                grade, text=nome, height=28, corner_radius=RAIO["sm"],
                font=ctk.CTkFont(FONTE_UI, 10),
                fg_color=T.destaque if nome == atual else T.superficie_alt,
                hover_color=T.borda,
                text_color=T.texto_destaque if nome == atual else T.texto,
                command=lambda n=nome: self._definir_tema(n))
            botao.grid(row=indice // 2, column=indice % 2, sticky="ew", padx=2, pady=2)
        grade.grid_columnconfigure((0, 1), weight=1)

        # Cor livre: casar o livro com a cor do editor que a pessoa usa.
        personalizado = atual == TEMA_PERSONALIZADO
        C.botao(self.corpo, "Cor da pagina…", self._abrir_cores, largura=250, altura=30,
                variante="primario" if personalizado else "secundario").pack(
            fill="x", padx=ESP["md"], pady=(ESP["sm"], 0))
        C.rotulo(self.corpo,
                 f"Usando {cfg.obter('cor_papel')}" if personalizado
                 else "Escolha a cor exata, ou copie a paleta do seu editor.",
                 9, "normal", "texto_suave").pack(fill="x", padx=ESP["md"], pady=(2, 0))

        C.botao(self.corpo, "Copiar cores da janela ao lado",
                self._copiar_cores_da_janela, largura=250, altura=30,
                variante="secundario").pack(fill="x", padx=ESP["md"],
                                            pady=(ESP["xs"], 0))
        C.rotulo(self.corpo,
                 "Le as cores do programa que estiver na frente e monta o tema "
                 "igual. A leitura e local e so acontece ao clicar.",
                 9, "normal", "texto_suave", wraplength=250,
                 justify="left").pack(fill="x", padx=ESP["md"], pady=(2, 0))

        self._secao("Fonte")
        C.seletor(self.corpo, list(FONTES_LEITURA.keys()), self._definir_fonte, largura=250,
                  valor=self._valor("fonte_leitura")).pack(fill="x", padx=ESP["md"])

        self.lbl_tamanho = self._secao(f"Tamanho  ·  {self._valor_int('tamanho_fonte', 17)}px")
        self._slider(9, 34, self._valor_int("tamanho_fonte", 17), self._definir_tamanho)

        self.lbl_altura = self._secao(f"Espaco entre linhas  ·  {self._valor_float('altura_linha', 1.6):.1f}")
        self._slider(1.0, 2.4, self._valor_float("altura_linha", 1.6), self._definir_altura,
                     passos=14)

        self.lbl_largura = self._secao(f"Largura da coluna  ·  {self._valor_int('largura_coluna', 68)} letras")
        self._slider(LARGURA_COLUNA_MIN, LARGURA_COLUNA_MAX,
                     self._valor_int("largura_coluna", 68), self._definir_largura)

        opcoes = C.quadro(self.corpo, "transparent")
        opcoes.pack(fill="x", padx=ESP["md"], pady=(ESP["md"], ESP["md"]))
        self._switch(opcoes, "Recuo de paragrafo", "recuo_paragrafo", False)
        self._switch(opcoes, "Mostrar imagens", "mostrar_imagens", True)
        self._switch(opcoes, "Texto justificado (alinhado nas duas margens)",
                     "justificar", True)
        self._switch(opcoes, "Folhear: duas folhas lado a lado em janela larga",
                     "duas_folhas", True)
        self._switch(opcoes, "Folhear: animar a virada da folha", "animar_folha", True)
        self._switch(opcoes, "Reconhecer texto (OCR) em paginas digitalizadas "
                             "(vale ao reabrir o livro)", "ocr_ativo", True)

    # ----------------------------------------------------- este livro
    def _secao_do_livro(self):
        """As duas camadas do livro: como exibir e sobre o que e.

        Separadas de proposito. A renderizacao muda o motor de exibicao; a
        categoria so organiza a estante. Engenharia e medicina se leem igual
        (titulos, listas, tabelas) mas ficam em prateleiras diferentes -- criar
        um perfil de renderizacao por assunto multiplicaria perfis sem
        nenhuma diferenca real de leitura.
        """
        leitor = self.leitor
        self._secao("Como exibir este livro")

        detectado = leitor.motor.render_detectado if leitor.motor else RENDER_PADRAO
        rotulos = {"auto": f"Automatico ({RENDERIZACOES[detectado]['nome']})"}
        rotulos.update({chave: dados["nome"] for chave, dados in RENDERIZACOES.items()})
        inverso = {v: k for k, v in rotulos.items()}
        escolhido = (leitor.prefs_livro.get("render") or "auto").lower()

        C.seletor(self.corpo, list(rotulos.values()),
                  lambda v: self._definir_render(inverso[v]), largura=250,
                  valor=rotulos.get(escolhido, rotulos["auto"])).pack(
            fill="x", padx=ESP["md"])

        self.lbl_render = C.rotulo(
            self.corpo, RENDERIZACOES[leitor.render_livro]["descricao"], 9,
            "normal", "texto_suave", wraplength=250, justify="left")
        self.lbl_render.pack(fill="x", padx=ESP["md"], pady=(3, 0))

        lingua = leitor.motor.linguagem_dominante if leitor.motor else None
        if lingua and lingua != "comum" and leitor.preset.get("realce_codigo"):
            C.rotulo(self.corpo, f"Linguagem reconhecida: {lingua}", 9, "normal",
                     "destaque").pack(fill="x", padx=ESP["md"], pady=(2, 0))

        # A pagina inteira de um manga fica ilegivel numa coluna estreita, que
        # e justamente onde este leitor vive. Quadro a quadro, cada painel
        # ocupa a largura toda.
        if leitor.render_livro == "hq":
            caixa_hq = C.quadro(self.corpo, "transparent")
            caixa_hq.pack(fill="x", padx=ESP["md"], pady=(ESP["sm"], 0))
            estado = ctk.StringVar(
                value="1" if self.cfg.obter_bool("quadro_a_quadro", False) else "0")
            interruptor = ctk.CTkSwitch(
                caixa_hq, text="Ler quadro a quadro", variable=estado,
                onvalue="1", offvalue="0", command=leitor.alternar_quadro_a_quadro,
                font=ctk.CTkFont(FONTE_UI, 11), progress_color=T.destaque)
            C.registrar_estilo(interruptor, progress_color="destaque")
            interruptor.pack(fill="x")
            C.rotulo(self.corpo,
                     "Um painel por vez, ocupando a coluna inteira. Avancar passa "
                     "ao proximo quadro e so vira a pagina no fim dela.",
                     9, "normal", "texto_suave", wraplength=250,
                     justify="left").pack(fill="x", padx=ESP["md"], pady=(2, 0))

        self._secao("Categoria (so organiza a estante)")
        atual_cat = (leitor.prefs_livro.get("categoria") or "").strip()
        visiveis = ["Sem categoria"] + [c for c in CATEGORIAS if c]
        C.seletor(self.corpo, visiveis,
                  lambda v: leitor.definir_categoria("" if v == "Sem categoria" else v),
                  largura=250,
                  valor=atual_cat if atual_cat else "Sem categoria").pack(
            fill="x", padx=ESP["md"])

        variavel = ctk.StringVar(value="1" if leitor.tem_pref_propria else "0")

        def alternar():
            leitor.alternar_pref_propria(variavel.get() == "1")

        controle = ctk.CTkSwitch(
            self.corpo, text="Tipografia so deste livro", variable=variavel,
            onvalue="1", offvalue="0", command=alternar,
            font=ctk.CTkFont(FONTE_UI, 11), text_color=T.texto,
            progress_color=T.destaque, button_color=T.superficie,
            fg_color=T.superficie_alt)
        C.registrar_estilo(controle, text_color="texto", progress_color="destaque",
                           button_color="superficie", fg_color="superficie_alt")
        controle.pack(fill="x", padx=ESP["md"], pady=(ESP["md"], 0))

    def _definir_render(self, valor):
        self.leitor.definir_render(valor)
        try:
            self.lbl_render.configure(
                text=RENDERIZACOES[self.leitor.render_livro]["descricao"])
        except Exception:
            pass

    # --------------------------------------------------------------- helpers
    def _secao(self, texto):
        rotulo = C.rotulo(self.corpo, texto, 10, "bold", "texto_suave")
        rotulo.pack(fill="x", padx=ESP["md"], pady=(ESP["md"], ESP["xs"]))
        return rotulo

    def _slider(self, minimo, maximo, valor, comando, passos=None):
        controle = ctk.CTkSlider(
            self.corpo, from_=minimo, to=maximo, command=comando, height=16,
            number_of_steps=passos or int(maximo - minimo),
            button_color=T.destaque, button_hover_color=T.destaque_hover,
            progress_color=T.destaque, fg_color=T.superficie_alt)
        C.registrar_estilo(controle, button_color="destaque", button_hover_color="destaque_hover",
                           progress_color="destaque", fg_color="superficie_alt")
        controle.set(valor)
        controle.pack(fill="x", padx=ESP["md"])
        return controle

    def _switch(self, pai, texto, chave, padrao):
        variavel = ctk.StringVar(value="1" if self.cfg.obter_bool(chave, padrao) else "0")

        def alternar():
            self.cfg.definir(chave, variavel.get())
            self.ao_mudar()

        controle = ctk.CTkSwitch(pai, text=texto, variable=variavel, onvalue="1", offvalue="0",
                                 command=alternar, font=ctk.CTkFont(FONTE_UI, 11),
                                 text_color=T.texto, progress_color=T.destaque,
                                 button_color=T.superficie, fg_color=T.superficie_alt)
        C.registrar_estilo(controle, text_color="texto", progress_color="destaque",
                           button_color="superficie", fg_color="superficie_alt")
        controle.pack(fill="x", pady=2)
        return controle

    # -------------------------------------------------------------- acoes
    def _definir_modo(self, valor):
        self.cfg.definir("modo_virada", valor)
        self.lbl_modo.configure(text=MODOS_VIRADA.get(valor, ""))
        self._atualizar_linha_velocidade()
        self.ao_mudar(mudou_modo=True)

    def _atualizar_linha_velocidade(self):
        """O controle de velocidade so faz sentido na rolagem automatica."""
        if normalizar_modo_virada(self.cfg.obter("modo_virada")) == MODO_AUTO:
            self.linha_velocidade.pack(fill="x", padx=ESP["md"], pady=(ESP["md"], 2))
            self.controle_velocidade.pack(fill="x", padx=ESP["md"])
            self.lbl_vel.configure(
                text=str(self.cfg.obter_int("velocidade_rolagem",
                                            VELOCIDADE_ROLAGEM_PADRAO)))
        else:
            self.linha_velocidade.pack_forget()
            self.controle_velocidade.pack_forget()

    def _definir_velocidade(self, valor):
        self.cfg.definir("velocidade_rolagem", int(valor))
        self.lbl_vel.configure(text=str(int(valor)))

    def _valor(self, chave, padrao=None):
        if self.leitor is not None and self.leitor.id_livro:
            return self.leitor.pref(chave, padrao)
        return self.cfg.obter(chave, padrao)

    def _valor_int(self, chave, padrao=0):
        try:
            return int(float(self._valor(chave, padrao)))
        except (TypeError, ValueError):
            return padrao

    def _valor_float(self, chave, padrao=0.0):
        try:
            return float(self._valor(chave, padrao))
        except (TypeError, ValueError):
            return padrao

    def _gravar(self, chave, valor):
        """Grava no livro ou no global, conforme a opcao "so deste livro"."""
        if self.leitor is not None and self.leitor.id_livro:
            self.leitor.definir_pref(chave, valor)
        else:
            self.cfg.definir(chave, valor)

    def _definir_tema(self, nome):
        self.cfg.definir("tema", nome)
        self.ao_mudar(mudou_tema=True)
        self.ao_fechar()

    def _copiar_cores_da_janela(self):
        """Le as cores da janela ao lado e monta o tema a partir delas.

        Mais fiel que casar pelo nome do programa: pega o tema que a pessoa
        realmente configurou lá, seja ele qual for.
        """
        # Passa o retangulo da PROPRIA janela: sem isso a busca escolheria a
        # janela em foco, que ao clicar no botao e o proprio Lumina.
        raiz = self.winfo_toplevel()
        try:
            proprio = (raiz.winfo_rootx(), raiz.winfo_rooty(),
                       raiz.winfo_rootx() + raiz.winfo_width(),
                       raiz.winfo_rooty() + raiz.winfo_height())
        except Exception:
            proprio = None

        cores, informacao = contexto.cores_da_janela_ao_lado(proprio)
        if not cores:
            C.Toast.mostrar(self.master, informacao, "erro", 3200)
            return
        papel, texto, destaque = cores
        self.cfg.definir("cor_papel", papel)
        self.cfg.definir("cor_texto", texto)
        self.cfg.definir("cor_destaque", destaque)
        self.cfg.definir("tema", TEMA_PERSONALIZADO)
        self.ao_mudar(mudou_tema=True)
        self.ao_fechar()
        C.Toast.mostrar(self.master, f"Cores copiadas de “{informacao[:40]}”.",
                        "ok", 2200)

    def _abrir_cores(self):
        """Abre o editor ja carregado com as cores do tema em uso.

        E o que torna TODO tema editavel: escolha "Sepia", abra aqui e ajuste
        o que quiser -- o ponto de partida e sempre o que esta na tela.
        """
        def aplicar():
            self.cfg.definir("tema", TEMA_PERSONALIZADO)
            self.ao_mudar(mudou_tema=True)

        base = (T.papel, T.papel_texto, T.destaque)
        self.ao_fechar()
        Modais.abrir_tema_personalizado(self.master, self.cfg, aplicar, base=base)

    def _definir_fonte(self, valor):
        self._gravar("fonte_leitura", valor)
        self.ao_mudar()

    def _definir_tamanho(self, valor):
        self._gravar("tamanho_fonte", int(valor))
        self.lbl_tamanho.configure(text=f"Tamanho  ·  {int(valor)}px")
        self.ao_mudar()

    def _definir_altura(self, valor):
        self._gravar("altura_linha", round(float(valor), 2))
        self.lbl_altura.configure(text=f"Espaco entre linhas  ·  {float(valor):.1f}")
        self.ao_mudar()

    def _definir_largura(self, valor):
        self._gravar("largura_coluna", int(valor))
        self.lbl_largura.configure(text=f"Largura da coluna  ·  {int(valor)} letras")
        self.ao_mudar()
