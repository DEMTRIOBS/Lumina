# ui/estante.py
"""Biblioteca: pastas hierarquicas, cartoes de livro com capa e progresso.

Organizacao no modelo do explorador de arquivos: cada livro mora em UMA pasta,
as pastas aninham sem limite de profundidade ("Faculdade > Calculo I") e livros
sem pasta ficam em "Soltos" em vez de sumirem.

A barra lateral com a arvore some abaixo de `LIMIAR_COMPACTO` e da lugar a um
seletor de pasta -- em meia tela ela consumiria quase toda a largura util.
"""
import os
import subprocess
import sys
import unicodedata
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

import ui.componentes as C
import ui.icones as icones
from configuracoes import (APP_NOME, APP_VERSAO, CATEGORIAS, ESP, FONTE_UI,
                           LIMIAR_COMPACTO, RAIO, T,
                           adivinhar_categoria)
from core.branding import carregar_logo
from database.pastas import SOLTOS, TODOS
from ui.modais import Modais

LARGURA_CARTAO = 178
ALTURA_CAPA = 208
LARGURA_BARRA = 214
ORDENACOES = ["Recentes", "Titulo", "Progresso", "Adicionados"]

# Miniatura da capa no modo lista. Em faixa estreita o cartao de grade deixa
# UM livro por tela; deitado, a mesma altura mostra oito.
LARGURA_MINIATURA = 40
ALTURA_MINIATURA = 54

# Espaco reservado para a barra de rolagem da area. Sem descontar, a conta de
# colunas achava que cabia mais uma e o ultimo cartao saia cortado.
LARGURA_ROLAGEM = 18


def _sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn").lower()


class TelaEstante(ctk.CTkFrame):

    def __init__(self, master, controlador, banco, pdf_service, banco_config, pastas):
        super().__init__(master, fg_color=T.fundo, corner_radius=0)
        C.registrar_estilo(self, fg_color="fundo")

        self.controlador = controlador
        self.banco = banco
        self.pdf_service = pdf_service
        self.cfg = banco_config
        self.pastas = pastas

        self.logo = None
        self.capas = {}          # id_livro -> CTkImage (evita recarregar do disco)
        self.miniaturas = {}     # o mesmo, no tamanho da lista compacta
        self.livros = []
        self.filtro = ""
        self.pasta_atual = self._pasta_salva()
        self.expandidas = self._expandidas_salvas()

        self._colunas_anteriores = 0
        self._compacto = None
        self._agendado_layout = None

        self._carregar_logo()
        self._construir()

    # ------------------------------------------------------------------ estado
    def _pasta_salva(self):
        valor = self.cfg.obter("pasta_atual", TODOS)
        if valor in (TODOS, SOLTOS):
            return valor
        try:
            id_pasta = int(valor)
            return id_pasta if self.pastas.obter(id_pasta) else TODOS
        except (TypeError, ValueError):
            return TODOS

    def _expandidas_salvas(self):
        bruto = self.cfg.obter("pastas_expandidas", "")
        ids = set()
        for parte in str(bruto).split(","):
            if parte.strip().isdigit():
                ids.add(int(parte))
        return ids

    def _salvar_estado(self):
        self.cfg.definir("pasta_atual", self.pasta_atual)
        self.cfg.definir("pastas_expandidas", ",".join(str(i) for i in sorted(self.expandidas)))

    def _carregar_logo(self):
        # Vem do SVG, entao fica nitido em qualquer escala de tela.
        try:
            imagem = carregar_logo(32)
            if imagem is not None:
                self.logo = ctk.CTkImage(light_image=imagem, dark_image=imagem, size=(32, 32))
        except Exception:
            self.logo = None

    # =====================================================================
    # CONSTRUCAO
    # =====================================================================
    def _construir(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._construir_cabecalho()

        corpo = C.quadro(self, "fundo")
        corpo.grid(row=1, column=0, sticky="nsew")
        corpo.grid_rowconfigure(0, weight=1)
        corpo.grid_columnconfigure(1, weight=1)
        self.corpo = corpo

        self._construir_barra_pastas(corpo)
        self._construir_conteudo(corpo)

        self.rodape = C.rotulo(self, "", 10, "normal", "texto_suave", ancora="center")
        self.rodape.grid(row=2, column=0, sticky="ew", pady=(0, ESP["sm"]))

        self.bind("<Configure>", self._ao_redimensionar)

    def _construir_cabecalho(self):
        cabecalho = C.quadro(self, "superficie", height=62)
        cabecalho.grid(row=0, column=0, sticky="ew")
        cabecalho.grid_propagate(False)
        # A coluna do filtro e a elastica: encolhe antes de empurrar as acoes
        # para fora da tela quando a janela vira meia tela.
        cabecalho.grid_columnconfigure(1, weight=1)

        self.marca = C.quadro(cabecalho, "transparent")
        self.marca.grid(row=0, column=0, sticky="w", padx=(ESP["md"], 0), pady=ESP["md"])
        if self.logo:
            ctk.CTkLabel(self.marca, text="", image=self.logo).pack(side="left",
                                                                   padx=(0, ESP["sm"]))
        self.lbl_marca = C.rotulo(self.marca, APP_NOME, 16, "bold")
        self.lbl_marca.pack(side="left")

        self.entry_filtro = C.campo(cabecalho, "Filtrar por titulo ou autor…", largura=160)
        self.entry_filtro.grid(row=0, column=1, sticky="ew", padx=ESP["sm"])
        self.entry_filtro.bind("<KeyRelease>", self._ao_filtrar)

        # `grid` aqui (e nao `pack`) para que esconder o seletor com
        # grid_remove() nao bagunce a ordem dos botoes ao restaurar.
        acoes = C.quadro(cabecalho, "transparent")
        acoes.grid(row=0, column=2, sticky="e", padx=(0, ESP["md"]))

        self.seletor_ordem = C.seletor(acoes, ORDENACOES, self._ao_ordenar, largura=126,
                                       valor=self.cfg.obter("ordenacao_estante", "Recentes"))
        self.seletor_ordem.grid(row=0, column=0, padx=(0, ESP["sm"]))

        C.botao_icone(acoes, "config", self._abrir_configuracoes,
                      "Configuracoes e assistente de IA").grid(row=0, column=1, padx=2)
        self.btn_adicionar = C.botao(acoes, "Adicionar PDF", self.adicionar_livros,
                                     largura=140, icone="＋")
        self.btn_adicionar.grid(row=0, column=2, padx=(ESP["sm"], 0))
        C.Tooltip(self.btn_adicionar, "Adicionar um ou mais PDFs a pasta aberta")

    # ---------------------------------------------------------- barra lateral
    def _construir_barra_pastas(self, pai):
        self.barra = C.quadro(pai, "superficie", width=LARGURA_BARRA)
        self.barra.grid(row=0, column=0, sticky="nsew")
        self.barra.grid_propagate(False)
        self.barra.grid_rowconfigure(1, weight=1)
        self.barra.grid_columnconfigure(0, weight=1)

        topo = C.quadro(self.barra, "transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=ESP["sm"], pady=(ESP["sm"], 0))
        C.botao_icone(topo, "anterior", self.alternar_barra_pastas,
                      "Recolher o menu de pastas", tamanho=22).pack(side="left")
        C.rotulo(topo, "PASTAS", 10, "bold", "texto_suave").pack(side="left", padx=ESP["xs"])
        C.botao_icone(topo, "adicionar", lambda: self._nova_pasta(None),
                      "Nova pasta na raiz", tamanho=26).pack(side="right")

        self.arvore = C.rolavel(self.barra, "superficie")
        self.arvore.grid(row=1, column=0, sticky="nsew", padx=ESP["xs"], pady=ESP["xs"])

    # ------------------------------------------------------ recolher / abrir
    @property
    def barra_recolhida(self):
        return self.cfg.obter_bool("barra_pastas_recolhida", False)

    def alternar_barra_pastas(self, _=None):
        """Esconde a arvore para a estante ocupar a largura inteira.

        Quem organizou os livros uma vez passa a maior parte do tempo so
        procurando -- e ai a arvore vira 214 px de largura parada. Recolhida,
        a navegacao continua possivel pelo seletor de pastas, que e o mesmo
        que a janela estreita ja usava.
        """
        self.cfg.definir_bool("barra_pastas_recolhida", not self.barra_recolhida)
        self._aplicar_barra_pastas()
        # A largura util mudou em 214 px: cabe outra coluna de livros, ou uma
        # a menos. So da para recontar depois que o Tk refizer o layout.
        self.after(80, self._talvez_redesenhar)

    def _aplicar_barra_pastas(self):
        """Mostra a arvore, o botao de reabrir ou nada, conforme o caso."""
        # Em janela estreita quem manda e o modo compacto: a arvore nao cabe,
        # recolhida ou nao.
        if self._compacto:
            self.barra.grid_remove()
            self.btn_abrir_pastas.grid_remove()
            return
        if self.barra_recolhida:
            self.barra.grid_remove()
            self.btn_abrir_pastas.grid()
            self.seletor_pasta.grid()
            self._atualizar_seletor_compacto()
        else:
            self.btn_abrir_pastas.grid_remove()
            self.barra.grid()
            self.seletor_pasta.grid_remove()

    # -------------------------------------------------------------- conteudo
    def _construir_conteudo(self, pai):
        conteudo = C.quadro(pai, "fundo")
        conteudo.grid(row=0, column=1, sticky="nsew")
        conteudo.grid_rowconfigure(1, weight=1)
        conteudo.grid_columnconfigure(0, weight=1)

        trilha = C.quadro(conteudo, "fundo", height=40)
        trilha.grid(row=0, column=0, sticky="ew", padx=ESP["lg"], pady=(ESP["md"], 0))
        trilha.grid_columnconfigure(1, weight=1)
        trilha.grid_propagate(False)

        # So aparece com a arvore recolhida: e o caminho de volta para ela.
        self.btn_abrir_pastas = C.botao_icone(
            trilha, "menu", self.alternar_barra_pastas, "Mostrar o menu de pastas",
            tamanho=26)
        self.btn_abrir_pastas.grid(row=0, column=0, padx=(0, ESP["sm"]))
        self.btn_abrir_pastas.grid_remove()

        self.faixa_trilha = C.quadro(trilha, "transparent")
        self.faixa_trilha.grid(row=0, column=1, sticky="w")

        direita = C.quadro(trilha, "transparent")
        direita.grid(row=0, column=2, sticky="e")

        # Seletor de pasta que substitui a arvore quando a janela fica estreita.
        self.seletor_pasta = C.seletor(direita, ["Todos os livros"],
                                       self._ao_escolher_pasta_compacta, largura=170)
        self.seletor_pasta.grid(row=0, column=0, padx=(0, ESP["sm"]))
        self.seletor_pasta.grid_remove()

        self.switch_sub = ctk.CTkSwitch(
            direita, text="Incluir subpastas", command=self._alternar_subpastas,
            font=ctk.CTkFont(FONTE_UI, 11), text_color=T.texto_suave,
            progress_color=T.destaque, button_color=T.superficie,
            fg_color=T.superficie_alt, switch_width=34, switch_height=16)
        C.registrar_estilo(self.switch_sub, text_color="texto_suave",
                           progress_color="destaque", button_color="superficie",
                           fg_color="superficie_alt")
        if self.cfg.obter_bool("incluir_subpastas", True):
            self.switch_sub.select()
        self.switch_sub.grid(row=0, column=1)

        self.area = C.rolavel(conteudo, "fundo")
        self.area.grid(row=1, column=0, sticky="nsew", padx=ESP["lg"], pady=ESP["md"])

    # =====================================================================
    # DESENHO
    # =====================================================================
    def construir_interface(self):
        """Recarrega do banco e redesenha (chamado ao entrar na tela)."""
        self.controlador.title(f"{APP_NOME} {APP_VERSAO}")
        self.recarregar()

    def recarregar(self):
        self.livros = self._carregar_livros()
        self._desenhar_arvore()
        self._desenhar_trilha()
        if self._compacto:
            self._atualizar_seletor_compacto()
        self._desenhar()

    def _carregar_livros(self):
        # Com filtro digitado a busca vale para a biblioteca inteira: procurar
        # um livro e mais urgente que respeitar a pasta aberta.
        if self.filtro.strip():
            return self.banco.obter_estante()
        if self.pasta_atual == TODOS:
            return self.banco.obter_estante()
        if self.pasta_atual == SOLTOS:
            return self.banco.obter_estante(soltos=True)

        ids = {int(self.pasta_atual)}
        if self.cfg.obter_bool("incluir_subpastas", True):
            ids |= self.pastas.descendentes(self.pasta_atual)
        return self.banco.obter_estante(ids_pasta=ids)

    # ------------------------------------------------------------- arvore
    def _desenhar_arvore(self):
        for widget in self.arvore.winfo_children():
            widget.destroy()
        C.limpar_registro_mortos()

        total = len(self.banco.obter_estante())
        self._linha_arvore(TODOS, "Todos os livros", "biblioteca", 0, total, False, None)

        soltos = self.pastas.contar_soltos()
        if soltos or self.pasta_atual == SOLTOS:
            self._linha_arvore(SOLTOS, "Sem pasta", "documento", 0, soltos, False, None)

        for pasta, profundidade in self.pastas.arvore():
            id_pasta = pasta["id_pasta"]
            # Nao desenha filhas de pastas fechadas.
            if not self._visivel_na_arvore(pasta):
                continue
            self._linha_arvore(
                id_pasta, pasta["nome"], "pasta", profundidade,
                self.pastas.contar_livros(id_pasta, recursivo=True),
                bool(pasta["n_filhas"]), pasta)

        if not self.pastas.listar_todas():
            C.rotulo(self.arvore,
                     "Crie pastas para separar seus livros\npor assunto, curso ou semestre.",
                     10, "normal", "texto_suave", wraplength=170,
                     justify="left").pack(padx=ESP["sm"], pady=ESP["md"])

    def _visivel_na_arvore(self, pasta):
        """Uma pasta so aparece se todos os ancestrais estiverem expandidos."""
        pai = pasta["id_pai"]
        while pai is not None:
            if pai not in self.expandidas:
                return False
            acima = self.pastas.obter(pai)
            pai = acima["id_pai"] if acima else None
        return True

    def _linha_arvore(self, chave, nome, icone, profundidade, contagem, tem_filhas, pasta):
        selecionada = self.pasta_atual == chave
        linha = C.quadro(self.arvore, "superficie_alt" if selecionada else "transparent",
                         raio=RAIO["sm"])
        linha.pack(fill="x", pady=1)
        if selecionada:
            C.registrar_estilo(linha, fg_color="superficie_alt")

        recuo = ESP["xs"] + profundidade * 12

        if tem_filhas:
            aberta = chave in self.expandidas
            seta = C.botao_icone(linha, "baixo" if aberta else "proximo",
                                 lambda c=chave: self._alternar_expansao(c), "", tamanho=18)
            seta.pack(side="left", padx=(recuo, 0))
        else:
            C.quadro(linha, "transparent", width=18, height=18).pack(
                side="left", padx=(recuo, 0))

        # O icone vai num rotulo proprio: glifo e texto usam fontes diferentes
        # e nao cabem no mesmo widget.
        simbolo = ctk.CTkLabel(linha, text=icones.icone(icone), width=18,
                               text_color=T.destaque if selecionada else T.texto_suave,
                               font=ctk.CTkFont(icones.familia(), 13))
        C.registrar_estilo(simbolo, text_color="destaque" if selecionada else "texto_suave")
        simbolo.pack(side="left", padx=(0, 4))

        # Quanto mais fundo na arvore, menos espaco sobra para o nome antes de
        # ele passar por baixo da contagem.
        limite = max(10, 22 - profundidade * 3)
        botao = ctk.CTkButton(
            linha, text=self._encurtar(nome, limite), anchor="w", height=28,
            corner_radius=RAIO["sm"], fg_color="transparent",
            hover_color=T.borda, font=ctk.CTkFont(FONTE_UI, 11,
                                                  "bold" if selecionada else "normal"),
            text_color=T.destaque if selecionada else T.texto,
            command=lambda c=chave: self.selecionar_pasta(c))
        C.registrar_estilo(botao, hover_color="borda")
        botao.pack(side="left", fill="x", expand=True)

        C.rotulo(linha, str(contagem) if contagem else "", 9, "normal",
                 "texto_suave", ancora="e", width=22).pack(side="right", padx=(0, ESP["xs"]))

        if pasta is not None:
            for widget in (linha, botao):
                widget.bind("<Button-3>", lambda e, p=pasta: self._menu_pasta(p))

    def _alternar_expansao(self, chave):
        if chave in self.expandidas:
            self.expandidas.discard(chave)
        else:
            self.expandidas.add(chave)
        self._salvar_estado()
        self._desenhar_arvore()

    # ------------------------------------------------------------- trilha
    def _desenhar_trilha(self):
        for widget in self.faixa_trilha.winfo_children():
            widget.destroy()
        C.limpar_registro_mortos()

        if self.filtro.strip():
            C.rotulo(self.faixa_trilha, f"Resultados para “{self.filtro}” em toda a biblioteca",
                     12, "bold").pack(side="left")
            return

        if self.pasta_atual == TODOS:
            C.rotulo(self.faixa_trilha, "Todos os livros", 14, "bold").pack(side="left")
            return
        if self.pasta_atual == SOLTOS:
            C.rotulo(self.faixa_trilha, "Livros sem pasta", 14, "bold").pack(side="left")
            return

        trilha = self.pastas.caminho(self.pasta_atual)
        self._elo_trilha("Todos", TODOS)
        for indice, (id_pasta, nome) in enumerate(trilha):
            C.rotulo(self.faixa_trilha, "›", 12, "normal", "texto_suave").pack(
                side="left", padx=ESP["xs"])
            ultimo = indice == len(trilha) - 1
            self._elo_trilha(nome, id_pasta, ativo=ultimo)

    def _elo_trilha(self, nome, chave, ativo=False):
        if ativo:
            C.rotulo(self.faixa_trilha, nome, 14, "bold").pack(side="left")
            return
        botao = ctk.CTkButton(
            self.faixa_trilha, text=nome, height=24, corner_radius=RAIO["sm"],
            fg_color="transparent", hover_color=T.superficie_alt,
            text_color=T.texto_suave, font=ctk.CTkFont(FONTE_UI, 12),
            width=len(nome) * 7 + 12, command=lambda c=chave: self.selecionar_pasta(c))
        C.registrar_estilo(botao, hover_color="superficie_alt", text_color="texto_suave")
        botao.pack(side="left")

    # --------------------------------------------------------- grade central
    def _desenhar(self):
        for widget in self.area.winfo_children():
            widget.destroy()
        C.limpar_registro_mortos()

        itens = self._livros_visiveis()
        subpastas = ([] if self.filtro.strip() or self.pasta_atual in (TODOS, SOLTOS)
                     else self.pastas.filhas(self.pasta_atual))

        if not itens and not subpastas:
            self._estado_vazio_apropriado()
            return

        colunas = self._colunas()
        self._colunas_anteriores = colunas

        # Em meia tela a grade vira lista: o cartao em pe gasta a largura toda
        # com uma capa e mostra um livro de cada vez. Deitado, o mesmo espaco
        # mostra a estante inteira -- que e o ponto de ler em meia tela.
        if colunas <= 1 or self._compacto:
            self._desenhar_lista(subpastas, itens)
            self._atualizar_rodape(itens, subpastas)
            return

        # Colunas de conteudo com largura natural e uma coluna-sobra elastica no
        # fim: assim um unico livro fica encostado a esquerda em vez de flutuar
        # no meio da tela.
        for indice in range(colunas + 1):
            self.area.grid_columnconfigure(indice, weight=1 if indice == colunas else 0)

        posicao = 0
        for pasta in subpastas:
            self._cartao_pasta(pasta).grid(row=posicao // colunas, column=posicao % colunas,
                                           padx=ESP["sm"], pady=ESP["sm"], sticky="nw")
            posicao += 1

        # As pastas terminam a linha antes de comecarem os livros.
        if subpastas and posicao % colunas:
            posicao += colunas - (posicao % colunas)

        for livro in itens:
            self._cartao(livro).grid(row=posicao // colunas, column=posicao % colunas,
                                     padx=ESP["sm"], pady=ESP["sm"], sticky="nw")
            posicao += 1

        self._atualizar_rodape(itens, subpastas)

    # ------------------------------------------------------------ modo lista
    def _desenhar_lista(self, subpastas, itens):
        """Um item por linha, ocupando a largura toda."""
        self.area.grid_columnconfigure(0, weight=1)
        for indice in range(1, self._colunas_anteriores + 2):
            self.area.grid_columnconfigure(indice, weight=0)

        linha = 0
        for pasta in subpastas:
            self._linha_pasta(pasta).grid(row=linha, column=0, sticky="ew", pady=2)
            linha += 1
        for livro in itens:
            self._linha_livro(livro).grid(row=linha, column=0, sticky="ew", pady=2)
            linha += 1

    def _linha_pasta(self, pasta):
        id_pasta = pasta["id_pasta"]
        caixa = C.quadro(self.area, "superficie", raio=RAIO["sm"])
        caixa.grid_columnconfigure(1, weight=1)

        simbolo = ctk.CTkLabel(caixa, text=icones.icone("pasta"), width=32,
                               text_color=T.texto_suave, cursor="hand2",
                               font=ctk.CTkFont(icones.familia(), 18))
        C.registrar_estilo(simbolo, text_color="texto_suave")
        simbolo.grid(row=0, column=0, padx=(ESP["sm"], ESP["xs"]), pady=ESP["sm"])

        nome = C.rotulo(caixa, self._encurtar(pasta["nome"], 44), 12, "bold", ancora="w")
        nome.grid(row=0, column=1, sticky="ew")

        quantos = self.pastas.contar_livros(id_pasta, recursivo=True)
        C.rotulo(caixa, f"{quantos} livro(s)" if quantos else "vazia", 9, "normal",
                 "texto_suave", ancora="e").grid(row=0, column=2, padx=ESP["xs"])
        C.botao_icone(caixa, "mais_opcoes", lambda p=pasta: self._menu_pasta(p),
                      "Mais acoes", tamanho=26).grid(row=0, column=3, padx=(0, ESP["xs"]))

        abrir = lambda _=None, c=id_pasta: self.selecionar_pasta(c)
        for widget in (caixa, simbolo, nome):
            widget.bind("<Button-1>", abrir)
            widget.bind("<Button-3>", lambda e, p=pasta: self._menu_pasta(p))
        self._realce_lista(caixa, (caixa, simbolo, nome))
        return caixa

    def _linha_livro(self, livro):
        existe = os.path.exists(livro.get("caminho_pdf") or "")
        caixa = C.quadro(self.area, "superficie", raio=RAIO["sm"])
        caixa.grid_columnconfigure(1, weight=1)

        miniatura = self._miniatura(livro)
        if miniatura is not None:
            capa = ctk.CTkLabel(caixa, text="", image=miniatura, cursor="hand2")
        else:
            capa = ctk.CTkLabel(caixa, text="PDF", width=LARGURA_MINIATURA,
                                height=ALTURA_MINIATURA, fg_color=T.superficie_alt,
                                corner_radius=RAIO["sm"], cursor="hand2",
                                text_color=T.texto_suave,
                                font=ctk.CTkFont(FONTE_UI, 10, "bold"))
            C.registrar_estilo(capa, fg_color="superficie_alt", text_color="texto_suave")
        capa.grid(row=0, column=0, rowspan=3, padx=(ESP["sm"], ESP["sm"]), pady=ESP["sm"])

        titulo = C.rotulo(caixa, self._encurtar(livro.get("titulo") or "Sem titulo", 60),
                          12, "bold", ancora="w")
        titulo.grid(row=0, column=1, sticky="ew", pady=(ESP["sm"], 0))

        legenda = self._legenda_do_livro(livro, existe)
        C.rotulo(caixa, self._encurtar(legenda, 60), 9, "normal",
                 "perigo" if not existe else "texto_suave", ancora="w").grid(
            row=1, column=1, sticky="ew")

        fracao = self._fracao_lida(livro)
        barra = C.BarraProgresso(caixa, altura=4, fundo="superficie")
        barra.set(fracao)
        barra.grid(row=2, column=1, sticky="ew", pady=(ESP["xs"], ESP["sm"]))

        total = livro.get("total_paginas") or 0
        info = (f"{int(fracao * 100)}%  ·  pag. {(livro.get('pagina_atual') or 0) + 1}"
                f"{'/' + str(total) if total else ''}")
        C.rotulo(caixa, info, 9, "normal", "texto_suave", ancora="e").grid(
            row=0, column=2, rowspan=3, padx=ESP["xs"])
        C.botao_icone(caixa, "mais_opcoes", lambda: self._menu_livro(livro),
                      "Mais acoes", tamanho=26).grid(row=0, column=3, rowspan=3,
                                                     padx=(0, ESP["xs"]))

        abrir = lambda _=None: self.abrir_livro(livro)
        for widget in (caixa, capa, titulo):
            widget.bind("<Button-1>", abrir)
            widget.bind("<Button-3>", lambda e, l=livro: self._menu_livro(l))
        self._realce_lista(caixa, (caixa, capa, titulo))
        return caixa

    def _legenda_do_livro(self, livro, existe):
        if not existe:
            return "Arquivo ausente"
        if self.filtro.strip() or self.pasta_atual == TODOS:
            local = self._nome_da_pasta(livro.get("id_pasta"))
            if local:
                return f"em {local}"
        legenda = livro.get("autor") or ""
        categoria = (livro.get("categoria") or "").strip()
        if categoria:
            return f"{categoria}  ·  {legenda}" if legenda else categoria
        return legenda

    @staticmethod
    def _realce_lista(caixa, widgets):
        def entrar(_=None):
            caixa.configure(fg_color=T.superficie_alt)

        def sair(_=None):
            caixa.configure(fg_color=T.superficie)

        for widget in widgets:
            widget.bind("<Enter>", entrar, add=True)
            widget.bind("<Leave>", sair, add=True)

    def _miniatura(self, livro):
        """Capa pequena da linha, em cache separado do cartao da grade."""
        id_livro = livro["id_livro"]
        if id_livro in self.miniaturas:
            return self.miniaturas[id_livro]
        caminho = livro.get("caminho_capa")
        if not caminho or not os.path.exists(caminho):
            return None
        try:
            imagem = Image.open(caminho)
            ctk_img = ctk.CTkImage(light_image=imagem, dark_image=imagem,
                                   size=(LARGURA_MINIATURA, ALTURA_MINIATURA))
            self.miniaturas[id_livro] = ctk_img
            return ctk_img
        except Exception:
            return None

    def _atualizar_rodape(self, itens, subpastas):
        partes = []
        if subpastas:
            partes.append(f"{len(subpastas)} pasta(s)")
        partes.append(f"{len(itens)} livro(s)")
        faltando = sum(1 for l in itens if not os.path.exists(l.get("caminho_pdf") or ""))
        if faltando:
            partes.append(f"{faltando} com arquivo ausente")
        self.rodape.configure(text="  ·  ".join(partes))

    def _estado_vazio_apropriado(self):
        if self.filtro.strip():
            self._estado_vazio("busca", "Nenhum resultado",
                               f"Nada corresponde a “{self.filtro}”.",
                               "Limpar filtro", self._limpar_filtro)
        elif isinstance(self.pasta_atual, int):
            nome = (self.pastas.obter(self.pasta_atual) or {}).get("nome", "esta pasta")
            self._estado_vazio(
                "pasta", f"“{nome}” esta vazia",
                "Adicione PDFs direto aqui ou crie subpastas\npara separar por materia.",
                "Adicionar PDF nesta pasta", self.adicionar_livros)
        elif self.pasta_atual == SOLTOS:
            self._estado_vazio("confirmado", "Nenhum livro solto",
                               "Todos os seus livros ja estao organizados em pastas.",
                               "Ver todos os livros", lambda: self.selecionar_pasta(TODOS))
        else:
            self._estado_vazio(
                "biblioteca", "Sua biblioteca esta vazia",
                "Adicione um PDF e ele vira um livro refluido, pronto para ler\n"
                "em meia tela ao lado das suas outras janelas.",
                "Adicionar primeiro PDF", self.adicionar_livros)
        self.rodape.configure(text="")

    def _estado_vazio(self, icone, titulo, descricao, texto_botao, comando):
        caixa = C.quadro(self.area, "transparent")
        caixa.grid(row=0, column=0, pady=80)
        self.area.grid_columnconfigure(0, weight=1)
        simbolo = ctk.CTkLabel(caixa, text=icones.icone(icone), text_color=T.texto_suave,
                               font=ctk.CTkFont(icones.familia(), 44))
        C.registrar_estilo(simbolo, text_color="texto_suave")
        simbolo.pack()
        C.rotulo(caixa, titulo, 17, "bold", ancora="center").pack(pady=(ESP["md"], ESP["xs"]))
        C.rotulo(caixa, descricao, 12, "normal", "texto_suave", ancora="center",
                 justify="center").pack()
        C.botao(caixa, texto_botao, comando, largura=230, altura=38).pack(pady=ESP["xl"])

    # =====================================================================
    # LISTAGEM E ORDENACAO
    # =====================================================================
    def _livros_visiveis(self):
        termo = _sem_acento(self.filtro).strip()
        itens = self.livros
        if termo:
            itens = [l for l in itens
                     if termo in _sem_acento(l.get("titulo"))
                     or termo in _sem_acento(l.get("autor"))
                     or termo in _sem_acento(l.get("categoria"))
                     or termo in _sem_acento(os.path.basename(l.get("caminho_pdf") or ""))]

        ordem = self.cfg.obter("ordenacao_estante", "Recentes")
        if ordem == "Titulo":
            itens = sorted(itens, key=lambda l: _sem_acento(l.get("titulo")))
        elif ordem == "Progresso":
            itens = sorted(itens, key=self._fracao_lida, reverse=True)
        elif ordem == "Adicionados":
            itens = sorted(itens, key=lambda l: l.get("data_adicao") or "", reverse=True)
        else:
            itens = sorted(itens, key=lambda l: (l.get("fixado") or 0,
                                                 l.get("data_ultimo_acesso") or ""),
                           reverse=True)
        return itens

    @staticmethod
    def _fracao_lida(livro):
        total = livro.get("total_paginas") or 0
        if total <= 1:
            return 0.0
        return min(1.0, ((livro.get("pagina_atual") or 0) + 1) / total)

    def _colunas(self):
        largura = 0
        for widget in (self.area, self):
            try:
                largura = widget.winfo_width()
            except Exception:
                largura = 0
            if largura > 50:
                break
        if largura <= 50:                    # ainda nao medido no primeiro desenho
            largura = 980
        # `winfo_width` devolve PIXEL DE TELA; `LARGURA_CARTAO` esta em unidades
        # do CustomTkinter, que ele multiplica pelo fator de escala ao desenhar.
        # Comparar os dois direto fazia caber uma coluna a mais do que cabe --
        # e o ultimo livro de cada linha aparecia cortado na borda da janela.
        try:
            escala = ctk.ScalingTracker.get_widget_scaling(self) or 1.0
        except Exception:
            escala = 1.0
        util = largura / escala - 2 * ESP["lg"] - LARGURA_ROLAGEM
        return max(1, int(util // (LARGURA_CARTAO + 2 * ESP["sm"])))

    # =====================================================================
    # CARTOES
    # =====================================================================
    def _cartao_pasta(self, pasta):
        id_pasta = pasta["id_pasta"]
        cartao = C.quadro(self.area, "superficie", raio=RAIO["md"], width=LARGURA_CARTAO)
        cartao.configure(border_width=1, border_color=T.superficie)
        C.registrar_estilo(cartao, fg_color="superficie")

        icone = ctk.CTkLabel(cartao, text=icones.icone("pasta"),
                             width=LARGURA_CARTAO - 24, height=76,
                             fg_color=T.superficie_alt, corner_radius=RAIO["sm"],
                             cursor="hand2", text_color=T.texto_suave,
                             font=ctk.CTkFont(icones.familia(), 34))
        C.registrar_estilo(icone, fg_color="superficie_alt")
        icone.pack(padx=ESP["sm"], pady=(ESP["sm"], ESP["xs"]))

        nome = C.rotulo(cartao, self._encurtar(pasta["nome"], 36), 11, "bold",
                        wraplength=LARGURA_CARTAO - 24, justify="left")
        nome.pack(fill="x", padx=ESP["sm"])

        total = self.pastas.contar_livros(id_pasta, recursivo=True)
        detalhe = f"{total} livro(s)"
        if pasta["n_filhas"]:
            detalhe += f"  ·  {pasta['n_filhas']} subpasta(s)"
        C.rotulo(cartao, detalhe, 9, "normal", "texto_suave").pack(fill="x", padx=ESP["sm"])

        rodape = C.quadro(cartao, "transparent")
        rodape.pack(fill="x", padx=ESP["xs"], pady=(ESP["xs"], ESP["xs"]))
        C.botao_icone(rodape, "mais_opcoes", lambda p=pasta: self._menu_pasta(p),
                      "Renomear, mover ou excluir", tamanho=26).pack(side="right")

        abrir = lambda _=None: self.selecionar_pasta(id_pasta)
        for widget in (icone, nome):
            widget.bind("<Button-1>", abrir)
        for widget in (cartao, icone, nome):
            widget.bind("<Button-3>", lambda e, p=pasta: self._menu_pasta(p))
        self._realce(cartao, (cartao, icone, nome))
        return cartao

    def _cartao(self, livro):
        id_livro = livro["id_livro"]
        existe = os.path.exists(livro.get("caminho_pdf") or "")

        cartao = C.quadro(self.area, "superficie", raio=RAIO["md"], width=LARGURA_CARTAO)
        cartao.configure(border_width=1, border_color=T.superficie)
        C.registrar_estilo(cartao, fg_color="superficie")

        capa = self._capa(livro, id_livro)
        if capa is not None:
            widget_capa = ctk.CTkLabel(cartao, text="", image=capa, cursor="hand2")
        else:
            widget_capa = ctk.CTkLabel(
                cartao, text="PDF", width=LARGURA_CARTAO - 24, height=ALTURA_CAPA,
                fg_color=T.superficie_alt, corner_radius=RAIO["sm"], cursor="hand2",
                text_color=T.texto_suave, font=ctk.CTkFont(FONTE_UI, 22, "bold"))
            C.registrar_estilo(widget_capa, fg_color="superficie_alt", text_color="texto_suave")
        widget_capa.pack(padx=ESP["sm"], pady=(ESP["sm"], ESP["xs"]))

        titulo = livro.get("titulo") or "Sem titulo"
        lbl_titulo = C.rotulo(cartao, self._encurtar(titulo, 40), 11, "bold",
                              ancora="w", wraplength=LARGURA_CARTAO - 24, justify="left")
        lbl_titulo.pack(fill="x", padx=ESP["sm"])

        # Quando a busca ignora as pastas, mostrar onde o livro mora evita
        # aquela pergunta de "mas em que pasta esse estava?".
        legenda = livro.get("autor") or ""
        categoria = (livro.get("categoria") or "").strip()
        if categoria:
            legenda = f"{categoria}  ·  {legenda}" if legenda else categoria
        if self.filtro.strip() or self.pasta_atual == TODOS:
            local = self._nome_da_pasta(livro.get("id_pasta"))
            if local:
                legenda = f"em {local}"
        if not existe:
            legenda = "Arquivo ausente"
        C.rotulo(cartao, self._encurtar(legenda, 30), 9, "normal",
                 "perigo" if not existe else "texto_suave",
                 ancora="w").pack(fill="x", padx=ESP["sm"])

        fracao = self._fracao_lida(livro)
        barra = C.BarraProgresso(cartao, altura=4, fundo="superficie")
        barra.set(fracao)
        barra.pack(fill="x", padx=ESP["sm"], pady=(ESP["sm"], ESP["xs"]))

        total = livro.get("total_paginas") or 0
        info = (f"{int(fracao * 100)}%  ·  pag. {(livro.get('pagina_atual') or 0) + 1}"
                f"{'/' + str(total) if total else ''}")
        C.rotulo(cartao, info, 9, "normal", "texto_suave", ancora="w").pack(
            fill="x", padx=ESP["sm"])

        # Texto puro em vez de emoji: some a poluicao de cor no cartao.
        selos = []
        if livro.get("n_grifos"):
            selos.append(f"{livro['n_grifos']} grifos")
        if livro.get("n_notas"):
            selos.append(f"{livro['n_notas']} notas")
        if livro.get("n_favoritos"):
            selos.append(f"{livro['n_favoritos']} marcas")

        rodape = C.quadro(cartao, "transparent")
        rodape.pack(fill="x", padx=ESP["xs"], pady=(ESP["xs"], ESP["xs"]))
        if selos:
            C.rotulo(rodape, " · ".join(selos), 9, "normal", "texto_suave").pack(
                side="left", padx=ESP["xs"])
        C.botao_icone(rodape, "mais_opcoes", lambda: self._menu_livro(livro), "Mais acoes",
                      tamanho=26).pack(side="right")

        abrir = lambda _=None: self.abrir_livro(livro)
        for widget in (widget_capa, lbl_titulo):
            widget.bind("<Button-1>", abrir)
        cartao.bind("<Double-Button-1>", abrir)
        for widget in (cartao, widget_capa, lbl_titulo):
            widget.bind("<Button-3>", lambda e, l=livro: self._menu_livro(l))
        self._realce(cartao, (cartao, widget_capa, lbl_titulo))
        return cartao

    @staticmethod
    def _realce(cartao, widgets):
        def entrar(_=None):
            cartao.configure(border_color=T.destaque)

        def sair(_=None):
            cartao.configure(border_color=T.superficie)

        for widget in widgets:
            widget.bind("<Enter>", entrar, add=True)
            widget.bind("<Leave>", sair, add=True)

    def _nome_da_pasta(self, id_pasta):
        if not id_pasta:
            return ""
        pasta = self.pastas.obter(id_pasta)
        return pasta["nome"] if pasta else ""

    def _capa(self, livro, id_livro):
        if id_livro in self.capas:
            return self.capas[id_livro]
        caminho = livro.get("caminho_capa")
        if not caminho or not os.path.exists(caminho):
            return None
        try:
            imagem = Image.open(caminho)
            # Largura fixa e altura pela proporcao: os cartoes ficam alinhados
            # numa grade regular mesmo com PDFs de formatos diferentes.
            largura = LARGURA_CARTAO - 24
            altura = int(largura * imagem.height / max(1, imagem.width))
            altura = max(120, min(ALTURA_CAPA + 30, altura))
            ctk_img = ctk.CTkImage(light_image=imagem, dark_image=imagem,
                                   size=(largura, altura))
            self.capas[id_livro] = ctk_img
            return ctk_img
        except Exception:
            return None

    @staticmethod
    def _encurtar(texto, limite):
        texto = (texto or "").strip()
        return texto if len(texto) <= limite else texto[:limite - 1] + "…"

    # =====================================================================
    # PASTAS — ACOES
    # =====================================================================
    def selecionar_pasta(self, chave):
        self.pasta_atual = chave
        if isinstance(chave, int):
            # Abre a trilha ate a pasta escolhida para ela ficar visivel.
            for id_pasta, _ in self.pastas.caminho(chave)[:-1]:
                self.expandidas.add(id_pasta)
        self._salvar_estado()
        self.recarregar()

    def _nova_pasta(self, id_pai):
        def criar(nome):
            novo = self.pastas.criar(nome, id_pai)
            if id_pai is not None:
                self.expandidas.add(id_pai)
            self.selecionar_pasta(novo)
            C.Toast.mostrar(self, f"Pasta “{nome}” criada.", "ok", 1500)

        destino = self._nome_da_pasta(id_pai)
        Modais.pedir_texto(
            self, "Nova pasta",
            f"Dentro de “{destino}”" if destino else "Na raiz da biblioteca",
            "", criar, texto_ok="Criar")

    def _menu_pasta(self, pasta):
        Modais.menu_pasta(
            self, pasta,
            ao_abrir=lambda: self.selecionar_pasta(pasta["id_pasta"]),
            ao_nova_sub=lambda: self._nova_pasta(pasta["id_pasta"]),
            ao_renomear=lambda: self._renomear_pasta(pasta),
            ao_mover=lambda: self._mover_pasta(pasta),
            ao_excluir=lambda: self._excluir_pasta(pasta),
        )

    def _renomear_pasta(self, pasta):
        def aplicar(nome):
            self.pastas.renomear(pasta["id_pasta"], nome)
            self.recarregar()
            C.Toast.mostrar(self, "Pasta renomeada.", "ok", 1400)

        Modais.pedir_texto(self, "Renomear pasta", "Novo nome",
                           pasta["nome"], aplicar, texto_ok="Renomear")

    def _mover_pasta(self, pasta):
        def aplicar(destino):
            if self.pastas.mover(pasta["id_pasta"], destino):
                if destino is not None:
                    self.expandidas.add(destino)
                self.recarregar()
                C.Toast.mostrar(self, "Pasta movida.", "ok", 1400)
            else:
                C.Toast.mostrar(self, "Nao da para mover uma pasta para dentro dela mesma.",
                                "erro", 2600)

        proibidos = {pasta["id_pasta"]} | self.pastas.descendentes(pasta["id_pasta"])
        Modais.escolher_pasta(self, self.pastas, aplicar,
                              titulo=f"Mover “{pasta['nome']}” para",
                              excluir_ids=proibidos, atual=pasta["id_pai"])

    def _excluir_pasta(self, pasta):
        total = self.pastas.contar_livros(pasta["id_pasta"], recursivo=True)
        subpastas = len(self.pastas.descendentes(pasta["id_pasta"]))

        detalhes = []
        if subpastas:
            detalhes.append(f"{subpastas} subpasta(s) tambem serao excluidas")
        if total:
            detalhes.append(f"{total} livro(s) voltam para “Sem pasta”")
        extra = ("\n\n" + "\n".join("• " + d for d in detalhes)) if detalhes else ""

        def confirmar():
            # As subpastas somem junto: se a aberta era uma delas, a estante
            # ficaria apontando para uma pasta inexistente (e adicionar PDF
            # ali falhava por chave estrangeira).
            alvos = {pasta["id_pasta"]} | set(self.pastas.descendentes(pasta["id_pasta"]))
            self.pastas.excluir(pasta["id_pasta"])
            if self.pasta_atual in alvos:
                self.pasta_atual = TODOS
            self.recarregar()
            C.Toast.mostrar(self, "Pasta excluida. Nenhum livro foi apagado.", "ok", 2200)

        Modais.confirmar(
            self, "Excluir pasta",
            f"“{pasta['nome']}” sera removida da organizacao.{extra}\n\n"
            "Nenhum livro e apagado da biblioteca nem do disco.",
            texto_ok="Excluir pasta", perigo=True, ao_confirmar=confirmar)

    def _renomear_livro(self, livro):
        """Titulo escolhido a mao: PDFs sem metadado caem no nome do arquivo,
        que as vezes e so um numero."""
        def aplicar(nome):
            self.banco.definir_titulo(livro["id_livro"], nome)
            self.recarregar()
            C.Toast.mostrar(self, "Titulo atualizado.", "ok", 1400)

        Modais.pedir_texto(self, "Renomear livro", "Como ele aparece na estante",
                           livro.get("titulo") or "", aplicar,
                           texto_ok="Renomear", dica="Titulo do livro")

    def _definir_categoria(self, livro):
        """Camada 2: organiza a estante, nao muda a leitura."""
        def aplicar(valor):
            self.banco.definir_preferencia(livro["id_livro"], "categoria",
                                           "" if valor == "Sem categoria" else valor)
            self.recarregar()
            C.Toast.mostrar(self, "Categoria atualizada.", "ok", 1400)

        atual = (livro.get("categoria") or "").strip()
        if not atual:
            atual = adivinhar_categoria(livro.get("titulo"), livro.get("autor"))
        Modais.escolher_da_lista(
            self, "Categoria do livro",
            "Serve para organizar e filtrar a estante. Nao altera a leitura: "
            "quem manda na exibicao e o tipo de renderizacao (Ctrl+P no leitor).",
            ["Sem categoria"] + [c for c in CATEGORIAS if c],
            atual if atual else "Sem categoria", aplicar)

    def _mover_livro(self, livro):
        def aplicar(destino):
            self.pastas.definir_pasta_do_livro(livro["id_livro"], destino)
            self.recarregar()
            nome = self._nome_da_pasta(destino) or "Sem pasta"
            C.Toast.mostrar(self, f"Movido para “{nome}”.", "ok", 1600)

        Modais.escolher_pasta(self, self.pastas, aplicar,
                              titulo=f"Mover “{self._encurtar(livro.get('titulo'), 34)}” para",
                              atual=livro.get("id_pasta"), permitir_raiz=True)

    def _alternar_subpastas(self):
        self.cfg.definir_bool("incluir_subpastas", bool(self.switch_sub.get()))
        self.recarregar()

    def _ao_escolher_pasta_compacta(self, rotulo):
        chave = self._mapa_compacto.get(rotulo)
        if chave is not None:
            self.selecionar_pasta(chave)

    def _atualizar_seletor_compacto(self):
        rotulos = ["Todos os livros"]
        self._mapa_compacto = {"Todos os livros": TODOS}
        if self.pastas.contar_soltos():
            rotulos.append("Sem pasta")
            self._mapa_compacto["Sem pasta"] = SOLTOS
        for pasta, profundidade in self.pastas.arvore():
            rotulo = f"{'   ' * profundidade}{pasta['nome']}"
            # Duas subpastas "Provas" em pais diferentes teriam o mesmo
            # rotulo e o dicionario abriria a errada: desempata com espacos
            # invisiveis no fim, que nao mudam o que se ve.
            while rotulo in self._mapa_compacto:
                rotulo += " "
            rotulos.append(rotulo)
            self._mapa_compacto[rotulo] = pasta["id_pasta"]

        self.seletor_pasta.configure(values=rotulos)
        atual = next((r for r, c in self._mapa_compacto.items() if c == self.pasta_atual),
                     "Todos os livros")
        self.seletor_pasta.set(atual)

    # =====================================================================
    # LIVROS — ACOES
    # =====================================================================
    def abrir_livro(self, livro):
        caminho = livro.get("caminho_pdf") or ""
        if not os.path.exists(caminho):
            self._arquivo_ausente(livro)
            return
        self.controlador.mostrar_tela("leitor", livro["id_livro"])

    def _arquivo_ausente(self, livro):
        def relocalizar():
            novo = filedialog.askopenfilename(
                parent=self.winfo_toplevel(),
                title=f"Localizar “{livro.get('titulo')}”",
                filetypes=[("Arquivos PDF", "*.pdf")])
            if not novo:
                return

            def aplicar():
                self.banco.definir_caminho(livro["id_livro"], novo)
                try:
                    meta = self.pdf_service.extrair_metadados(novo)
                    self.banco.definir_total_paginas(livro["id_livro"],
                                                     meta["total_paginas"])
                except Exception:
                    pass
                self.recarregar()
                C.Toast.mostrar(self, "Caminho atualizado.", "ok")

            # Grifos sao offsets de caractere: apontar para outra edicao
            # espalharia todos no lugar errado sem ninguem perceber.
            if self.pdf_service.gerar_id_unico(novo) == livro["id_livro"]:
                aplicar()
            else:
                Modais.confirmar(
                    self, "Arquivo diferente",
                    "Este PDF nao e identico ao original (outra edicao ou "
                    "arquivo modificado).\n\nGrifos e marcacoes podem cair em "
                    "lugares errados. Usar mesmo assim?",
                    texto_ok="Usar este arquivo", ao_confirmar=aplicar)

        Modais.confirmar(
            self, "Arquivo nao encontrado",
            f"O PDF de “{livro.get('titulo')}” nao esta mais em:\n\n"
            f"{livro.get('caminho_pdf')}\n\n"
            "Voce pode apontar o novo local do arquivo sem perder grifos e notas.",
            texto_ok="Localizar arquivo", ao_confirmar=relocalizar)

    def adicionar_livros(self):
        caminhos = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(), title="Escolher PDFs", filetypes=[("Arquivos PDF", "*.pdf")])
        if not caminhos:
            return

        # Os novos livros caem na pasta que esta aberta -- e o comportamento
        # esperado de qualquer gerenciador de arquivos.
        destino = self.pasta_atual if isinstance(self.pasta_atual, int) else None

        novos, repetidos, falhas = 0, 0, 0
        for caminho in caminhos:
            if not os.path.exists(caminho):
                falhas += 1
                continue
            try:
                id_livro = self.pdf_service.gerar_id_unico(caminho)
                if self.banco.existe(id_livro):
                    self.banco.definir_caminho(id_livro, caminho)
                    repetidos += 1
                    continue
                if self.pdf_service.esta_protegido(caminho):
                    falhas += 1
                    continue
                meta = self.pdf_service.extrair_metadados(caminho)
                capa = self.pdf_service.extrair_capa(caminho, id_livro)
                self.banco.adicionar_livro(id_livro, caminho, capa, meta["titulo"],
                                           meta["autor"], meta["total_paginas"],
                                           id_pasta=destino)
                novos += 1
            except Exception as erro:
                print(f"[Estante] Falha ao adicionar {caminho}: {erro}")
                falhas += 1

        self.recarregar()
        partes = []
        if novos:
            nome = self._nome_da_pasta(destino)
            partes.append(f"{novos} adicionado(s)" + (f" em “{nome}”" if nome else ""))
        if repetidos:
            partes.append(f"{repetidos} ja na estante")
        if falhas:
            partes.append(f"{falhas} com erro ou protegido(s)")
        C.Toast.mostrar(self, "  ·  ".join(partes) or "Nada a fazer",
                        "ok" if novos else "info")

    def _menu_livro(self, livro):
        Modais.menu_livro(
            self, livro, self._nome_da_pasta(livro.get("id_pasta")),
            ao_abrir=lambda: self.abrir_livro(livro),
            ao_renomear=lambda: self._renomear_livro(livro),
            ao_categorizar=lambda: self._definir_categoria(livro),
            ao_mover=lambda: self._mover_livro(livro),
            ao_fixar=lambda: self._fixar(livro),
            ao_abrir_pasta=lambda: self._abrir_pasta_arquivo(livro),
            ao_recriar_capa=lambda: self._recriar_capa(livro),
            ao_remover=lambda: self._remover(livro),
        )

    def _fixar(self, livro):
        fixado = self.banco.alternar_fixado(livro["id_livro"])
        self.recarregar()
        C.Toast.mostrar(self, "Fixado no topo." if fixado else "Desafixado.", "ok", 1400)

    def _abrir_pasta_arquivo(self, livro):
        caminho = livro.get("caminho_pdf") or ""
        pasta = os.path.dirname(caminho)
        if not os.path.isdir(pasta):
            C.Toast.mostrar(self, "Pasta indisponivel.", "erro")
            return
        try:
            if sys.platform == "win32":
                if os.path.exists(caminho):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])
                else:
                    os.startfile(pasta)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", pasta])
            else:
                subprocess.Popen(["xdg-open", pasta])
        except Exception as erro:
            C.Toast.mostrar(self, f"Nao foi possivel abrir: {erro}", "erro")

    def _recriar_capa(self, livro):
        caminho = livro.get("caminho_pdf") or ""
        if not os.path.exists(caminho):
            self._arquivo_ausente(livro)
            return
        capa = self.pdf_service.extrair_capa(caminho, livro["id_livro"], forcar=True)
        if capa:
            # Sem gravar o caminho, um livro cuja capa apontava para um
            # arquivo morto continuava mostrando o marcador "PDF".
            self.banco.atualizar_metadados(
                livro["id_livro"], livro.get("titulo") or "", livro.get("autor") or "",
                livro.get("total_paginas") or 0, capa)
        self.capas.pop(livro["id_livro"], None)
        self.recarregar()
        C.Toast.mostrar(self, "Capa atualizada.", "ok", 1400)

    def _remover(self, livro):
        def confirmar():
            self.banco.remover_livro(livro["id_livro"])
            self.capas.pop(livro["id_livro"], None)
            self.recarregar()
            C.Toast.mostrar(self, "Livro removido da biblioteca.", "ok")

        Modais.confirmar(
            self, "Remover da biblioteca",
            f"“{livro.get('titulo')}” sai da estante junto com seus grifos, "
            "marcadores e anotacoes.\n\nO arquivo PDF original nao e apagado do disco.",
            texto_ok="Remover", perigo=True, ao_confirmar=confirmar)

    def _abrir_configuracoes(self):
        Modais.abrir_configuracoes(self, self.cfg, ao_mudar_tema=self.controlador.aplicar_tema)

    # =====================================================================
    # EVENTOS
    # =====================================================================
    def _ao_filtrar(self, _=None):
        anterior = bool(self.filtro.strip())
        self.filtro = self.entry_filtro.get()
        # Entrar ou sair do modo de busca muda o conjunto carregado do banco.
        if bool(self.filtro.strip()) != anterior:
            self.recarregar()
        else:
            self._desenhar_trilha()
            self._desenhar()

    def _limpar_filtro(self):
        self.entry_filtro.delete(0, "end")
        self.filtro = ""
        self.recarregar()

    def _ao_ordenar(self, valor):
        self.cfg.definir("ordenacao_estante", valor)
        self._desenhar()

    def _ao_redimensionar(self, evento=None):
        # `CTkFrame.bind` liga o evento ao canvas interno, nunca a este frame.
        if self._agendado_layout:
            try:
                self.after_cancel(self._agendado_layout)
            except Exception:
                pass
        self._agendado_layout = self.after(160, self._talvez_redesenhar)

    def _talvez_redesenhar(self):
        self._agendado_layout = None
        mudou_modo = self._ajustar_compacto()
        if mudou_modo or self._colunas() != self._colunas_anteriores:
            self._desenhar()

    def _ajustar_compacto(self):
        """Em meia tela o cabecalho perde o supérfluo e a arvore vira seletor."""
        largura = self.winfo_width()
        compacto = largura < LIMIAR_COMPACTO
        if compacto == self._compacto:
            return False
        self._compacto = compacto

        if compacto:
            self.lbl_marca.pack_forget()
            self.seletor_ordem.grid_remove()
            self.btn_adicionar.configure(text="＋", width=40)
            self.entry_filtro.configure(placeholder_text="Filtrar…")
            # A trilha sairia por baixo do seletor; o proprio seletor ja diz
            # em que pasta voce esta.
            self.faixa_trilha.grid_remove()
            self._atualizar_seletor_compacto()
            self.seletor_pasta.grid()
        else:
            self.lbl_marca.pack(side="left")
            self.seletor_ordem.grid()
            self.btn_adicionar.configure(text="＋  Adicionar PDF", width=140)
            self.entry_filtro.configure(placeholder_text="Filtrar por titulo ou autor…")
            self.faixa_trilha.grid()
        # Quem decide entre arvore, botao de reabrir e nada e um lugar so --
        # senao o modo compacto e o recolhimento manual se desfariam um ao
        # outro conforme a ordem em que acontecessem.
        self._aplicar_barra_pastas()
        return True

    def aplicar_tema(self):
        self.recarregar()
