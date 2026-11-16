# ui/modais.py
"""Janelas auxiliares: notas, zoom de imagem, confirmacao, menu e configuracoes.

Todas passam por `C.preparar_modal`, que centraliza sobre a janela principal,
aplica o tema, prende a janela acima da dona com `transient()` e fecha com Esc.
"""
import io
import threading

import customtkinter as ctk
from PIL import Image

import ui.componentes as C
from configuracoes import (APP_NOME, APP_VERSAO, ESP, FONTE_UI,
                           PALETAS_PRONTAS, RAIO, T, montar_tema)
from core.paths import user_path


class Modais:

    # =====================================================================
    # CONFIRMACAO / AVISO
    # =====================================================================
    @staticmethod
    def confirmar(pai, titulo, mensagem, texto_ok="Confirmar", texto_cancelar="Cancelar",
                  perigo=False, ao_confirmar=None):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, titulo, 470, 260, pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["xl"], pady=ESP["xl"])

        C.rotulo(corpo, titulo, 16, "bold").pack(fill="x")
        C.rotulo(corpo, mensagem, 12, "normal", "texto_suave", wraplength=400,
                 justify="left").pack(fill="x", pady=ESP["md"])

        acoes = C.quadro(corpo, "transparent")
        acoes.pack(side="bottom", fill="x")

        def confirmar():
            C.fechar_modal(modal)
            if ao_confirmar:
                ao_confirmar()

        C.botao(acoes, texto_ok, confirmar, largura=140,
                variante="perigo" if perigo else "primario").pack(side="right")
        C.botao(acoes, texto_cancelar, lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(side="right", padx=ESP["sm"])
        modal.bind("<Return>", lambda e: confirmar())
        return modal

    @staticmethod
    def aviso(pai, titulo, mensagem):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, titulo, 470, 250, pai.winfo_toplevel())
        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["xl"], pady=ESP["xl"])
        C.rotulo(corpo, titulo, 16, "bold").pack(fill="x")
        C.rotulo(corpo, mensagem, 12, "normal", "texto_suave", wraplength=400,
                 justify="left").pack(fill="x", pady=ESP["md"])
        C.botao(corpo, "Entendi", lambda: C.fechar_modal(modal),
                largura=120).pack(side="bottom", anchor="e")
        return modal

    # =====================================================================
    # ENTRADA DE TEXTO
    # =====================================================================
    @staticmethod
    def pedir_texto(pai, titulo, descricao, valor_inicial, ao_confirmar,
                    texto_ok="Confirmar", dica="Nome"):
        """Prompt de uma linha, usado para criar e renomear pastas."""
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, titulo, 440, 230, pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["xl"], pady=ESP["xl"])

        C.rotulo(corpo, titulo, 16, "bold").pack(fill="x")
        C.rotulo(corpo, descricao, 11, "normal", "texto_suave").pack(
            fill="x", pady=(2, ESP["md"]))

        campo = C.campo(corpo, dica, largura=380, altura=36)
        campo.pack(fill="x")
        campo.insert(0, valor_inicial or "")
        modal.after(320, lambda: (campo.focus_set(), campo.select_range(0, "end")))

        aviso = C.rotulo(corpo, "", 10, "normal", "perigo")
        aviso.pack(fill="x", pady=(ESP["xs"], 0))

        def confirmar(_=None):
            nome = campo.get().strip()
            if not nome:
                aviso.configure(text="Digite um nome.")
                campo.focus_set()
                return
            C.fechar_modal(modal)
            ao_confirmar(nome)

        acoes = C.quadro(corpo, "transparent")
        acoes.pack(side="bottom", fill="x")
        C.botao(acoes, texto_ok, confirmar, largura=130).pack(side="right")
        C.botao(acoes, "Cancelar", lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(side="right", padx=ESP["sm"])
        modal.bind("<Return>", confirmar)
        return modal

    @staticmethod
    def escolher_da_lista(pai, titulo, descricao, opcoes, atual, ao_escolher):
        """Lista simples de escolha unica, com o item atual em destaque."""
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, titulo, 460, 520, pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])
        C.rotulo(corpo, titulo, 15, "bold").pack(fill="x")
        C.rotulo(corpo, descricao, 10, "normal", "texto_suave", wraplength=400,
                 justify="left").pack(fill="x", pady=(2, ESP["md"]))

        lista = C.rolavel(corpo, "superficie")
        lista.pack(fill="both", expand=True)

        def escolher(valor):
            C.fechar_modal(modal)
            ao_escolher(valor)

        for opcao in opcoes:
            marcado = opcao == atual
            botao = ctk.CTkButton(
                lista, text=opcao, anchor="w", height=32, corner_radius=RAIO["sm"],
                fg_color=T.superficie_alt if marcado else "transparent",
                hover_color=T.borda,
                text_color=T.destaque if marcado else T.texto,
                font=ctk.CTkFont(FONTE_UI, 12, "bold" if marcado else "normal"),
                command=lambda o=opcao: escolher(o))
            C.registrar_estilo(botao, hover_color="borda")
            botao.pack(fill="x", pady=1)

        C.botao(corpo, "Cancelar", lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(anchor="e", pady=(ESP["md"], 0))
        return modal

    # =====================================================================
    # ESCOLHA DE PASTA
    # =====================================================================
    @staticmethod
    def escolher_pasta(pai, pastas, ao_escolher, titulo="Mover para",
                       excluir_ids=None, atual=None, permitir_raiz=True):
        """Arvore de pastas achatada com recuo, para escolher um destino."""
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, titulo, 460, 520, pai.winfo_toplevel())
        excluir_ids = excluir_ids or set()

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])
        C.rotulo(corpo, titulo, 15, "bold", wraplength=400, justify="left").pack(fill="x")
        C.rotulo(corpo, "Escolha o destino ou crie uma pasta nova.", 11, "normal",
                 "texto_suave").pack(fill="x", pady=(2, ESP["md"]))

        lista = C.rolavel(corpo, "superficie")
        lista.pack(fill="both", expand=True)

        def escolher(destino):
            C.fechar_modal(modal)
            ao_escolher(destino)

        def linha(rotulo, destino, profundidade=0, selecionado=False):
            botao = ctk.CTkButton(
                lista, text=("    " * profundidade) + rotulo, anchor="w", height=32,
                corner_radius=RAIO["sm"],
                fg_color=T.superficie_alt if selecionado else "transparent",
                hover_color=T.borda,
                text_color=T.destaque if selecionado else T.texto,
                font=ctk.CTkFont(FONTE_UI, 12, "bold" if selecionado else "normal"),
                command=lambda: escolher(destino))
            C.registrar_estilo(botao, hover_color="borda")
            botao.pack(fill="x", pady=1)

        if permitir_raiz:
            linha("Sem pasta (raiz)", None, selecionado=atual is None)

        arvore = [(p, n) for p, n in pastas.arvore() if p["id_pasta"] not in excluir_ids]
        for pasta, profundidade in arvore:
            linha(pasta["nome"], pasta["id_pasta"], profundidade,
                  selecionado=atual == pasta["id_pasta"])

        if not arvore and not permitir_raiz:
            C.rotulo(lista, "Nenhuma pasta disponivel como destino.", 11, "normal",
                     "texto_suave").pack(padx=ESP["md"], pady=ESP["lg"])

        def nova():
            def criar(nome):
                escolher(pastas.criar(nome, None))
            Modais.pedir_texto(pai, "Nova pasta", "Na raiz da biblioteca", "",
                               criar, texto_ok="Criar e mover")
            C.fechar_modal(modal)

        acoes = C.quadro(corpo, "transparent")
        acoes.pack(fill="x", pady=(ESP["md"], 0))
        C.botao(acoes, "Cancelar", lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(side="right")
        C.botao(acoes, "Nova pasta", nova, largura=130,
                variante="secundario").pack(side="right", padx=ESP["sm"])
        return modal

    # =====================================================================
    # TEMA PERSONALIZADO
    # =====================================================================
    @staticmethod
    def abrir_tema_personalizado(pai, cfg, ao_aplicar, base=None):
        """Editor de tema: parte das cores em uso e deriva o resto.

        `base` chega como (papel, texto, destaque) do tema ativo, entao
        qualquer tema embutido serve de ponto de partida -- e o que torna
        todos eles editaveis, em vez de existir um unico "personalizado".

        Quem passa o dia no editor quer o livro na mesma cor: escolhendo papel
        e texto, as barras, bordas, grifo, busca e o realce de codigo saem
        combinando, em claro ou escuro conforme o brilho do papel.
        """
        from tkinter import colorchooser

        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, "Personalizar tema", 560, 650, pai.winfo_toplevel())

        papel, texto, destaque = base or (cfg.obter("cor_papel", "#1E1E1E"),
                                          cfg.obter("cor_texto", "#D4D4D4"),
                                          cfg.obter("cor_destaque", "#569CD6"))
        estado = {"papel": papel, "texto": texto, "destaque": destaque}

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])

        C.rotulo(corpo, "Personalizar tema", 16, "bold").pack(fill="x")
        C.rotulo(corpo, "Parte do tema atual. Ajuste as cores ou copie a paleta do seu editor.",
                 11, "normal", "texto_suave", wraplength=500,
                 justify="left").pack(fill="x", pady=(2, ESP["md"]))

        # ------------------------------------------------------------ previa
        previa = ctk.CTkFrame(corpo, corner_radius=RAIO["sm"], height=124,
                              border_width=1)
        previa.pack(fill="x")
        previa.pack_propagate(False)
        titulo_previa = ctk.CTkLabel(previa, text="Capitulo 1", anchor="w",
                                     font=ctk.CTkFont("Georgia", 16, "bold"))
        titulo_previa.pack(fill="x", padx=ESP["md"], pady=(ESP["sm"], 0))
        texto_previa = ctk.CTkLabel(
            previa, anchor="w", justify="left", font=ctk.CTkFont("Georgia", 12),
            text="O texto do livro aparece assim, com o grifo em destaque.")
        texto_previa.pack(fill="x", padx=ESP["md"])
        linha_previa = ctk.CTkFrame(previa, fg_color="transparent")
        linha_previa.pack(anchor="w", fill="x", padx=ESP["md"], pady=(ESP["xs"], ESP["sm"]))
        grifo_previa = ctk.CTkLabel(linha_previa, text="  trecho grifado  ", anchor="w",
                                    corner_radius=3, font=ctk.CTkFont("Georgia", 12))
        grifo_previa.pack(side="left")
        codigo_previa = ctk.CTkLabel(linha_previa, text="  def somar(a, b):  ", anchor="w",
                                     corner_radius=3, font=ctk.CTkFont("Consolas", 11))
        codigo_previa.pack(side="left", padx=(ESP["sm"], 0))

        def atualizar_previa():
            paleta = montar_tema(estado["papel"], estado["texto"], estado["destaque"])
            previa.configure(fg_color=paleta["papel"], border_color=paleta["borda"])
            titulo_previa.configure(text_color=paleta["papel_titulo"])
            texto_previa.configure(text_color=paleta["papel_texto"])
            grifo_previa.configure(fg_color=paleta["grifo"], text_color=paleta["grifo_texto"])
            codigo_previa.configure(fg_color=paleta["superficie_alt"],
                                    text_color=paleta["cod_palavra"])
            for chave, botao in amostras.items():
                botao.configure(fg_color=estado[chave])

        # --------------------------------------------------- cores manuais
        C.rotulo(corpo, "Cores", 11, "bold", "texto_suave").pack(
            fill="x", pady=(ESP["md"], ESP["xs"]))
        linha = C.quadro(corpo, "transparent")
        linha.pack(fill="x")
        amostras = {}

        def escolher(chave, titulo):
            escolha = colorchooser.askcolor(color=estado[chave], title=titulo,
                                            parent=modal)
            if escolha and escolha[1]:
                estado[chave] = escolha[1].upper()
                atualizar_previa()

        for chave, rotulo_txt in (("papel", "Pagina"), ("texto", "Texto"),
                                  ("destaque", "Destaque")):
            bloco = C.quadro(linha, "transparent")
            bloco.pack(side="left", expand=True, fill="x", padx=(0, ESP["sm"]))
            C.rotulo(bloco, rotulo_txt, 10, "normal", "texto_suave").pack(fill="x")
            amostra = ctk.CTkButton(
                bloco, text="", height=34, corner_radius=RAIO["sm"],
                border_width=1, border_color=T.borda, fg_color=estado[chave],
                hover=False, command=lambda c=chave, r=rotulo_txt: escolher(c, r))
            amostra.pack(fill="x")
            amostras[chave] = amostra

        # ------------------------------------------------------- paletas
        C.rotulo(corpo, "Paletas prontas", 11, "bold", "texto_suave").pack(
            fill="x", pady=(ESP["md"], ESP["xs"]))
        grade = C.rolavel(corpo, "superficie")
        grade.pack(fill="both", expand=True)

        def aplicar_paleta(nome):
            papel, texto, destaque = PALETAS_PRONTAS[nome]
            estado.update(papel=papel, texto=texto, destaque=destaque)
            atualizar_previa()

        for indice, (nome, (papel, texto, destaque)) in enumerate(PALETAS_PRONTAS.items()):
            item = ctk.CTkFrame(grade, fg_color=papel, corner_radius=RAIO["sm"],
                                border_width=1, border_color=T.borda, height=38)
            item.grid(row=indice // 2, column=indice % 2, sticky="ew", padx=3, pady=3)
            item.grid_propagate(False)
            rotulo_paleta = ctk.CTkLabel(item, text=nome, text_color=texto, anchor="w",
                                         font=ctk.CTkFont(FONTE_UI, 11, "bold"))
            rotulo_paleta.pack(side="left", padx=ESP["sm"])
            ponto = ctk.CTkLabel(item, text="  ", fg_color=destaque, corner_radius=6,
                                 width=14, height=14)
            ponto.pack(side="right", padx=ESP["sm"])
            for widget in (item, rotulo_paleta, ponto):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda e, n=nome: aplicar_paleta(n))
        grade.grid_columnconfigure((0, 1), weight=1)

        # --------------------------------------------------------- acoes
        acoes = C.quadro(corpo, "transparent")
        acoes.pack(fill="x", pady=(ESP["md"], 0))

        def aplicar():
            cfg.definir("cor_papel", estado["papel"])
            cfg.definir("cor_texto", estado["texto"])
            cfg.definir("cor_destaque", estado["destaque"])
            C.fechar_modal(modal)
            ao_aplicar()

        C.botao(acoes, "Aplicar", aplicar, largura=130).pack(side="right")
        C.botao(acoes, "Cancelar", lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(side="right", padx=ESP["sm"])

        atualizar_previa()
        return modal

    # =====================================================================
    # MENU DA PASTA
    # =====================================================================
    @staticmethod
    def menu_pasta(pai, pasta, ao_abrir, ao_nova_sub, ao_renomear, ao_mover, ao_excluir):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, pasta["nome"], 400, 370, pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])

        C.rotulo(corpo, pasta["nome"], 15, "bold", wraplength=340,
                 justify="left").pack(fill="x")
        C.rotulo(corpo, f"{pasta['n_livros']} livro(s) diretos  ·  "
                        f"{pasta['n_filhas']} subpasta(s)", 10, "normal",
                 "texto_suave").pack(fill="x", pady=(ESP["xs"], 0))

        C.separador(corpo)

        def envolver(funcao):
            def executar():
                C.fechar_modal(modal)
                funcao()
            return executar

        for texto, icone, comando, variante in (
            ("Abrir pasta", None, ao_abrir, "primario"),
            ("Nova subpasta", None, ao_nova_sub, "secundario"),
            ("Renomear", None, ao_renomear, "secundario"),
            ("Mover para outra pasta", None, ao_mover, "secundario"),
            ("Excluir pasta", None, ao_excluir, "perigo"),
        ):
            C.botao(corpo, texto, envolver(comando), largura=360, altura=36,
                    variante=variante, icone=icone, ancora="w").pack(fill="x", pady=3)
        return modal

    # =====================================================================
    # MENU DO LIVRO
    # =====================================================================
    @staticmethod
    def menu_livro(pai, livro, nome_pasta, ao_abrir, ao_renomear, ao_categorizar,
                   ao_mover, ao_fixar, ao_abrir_pasta, ao_recriar_capa, ao_remover):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, livro.get("titulo") or "Livro", 420, 530,
                         pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])

        C.rotulo(corpo, livro.get("titulo") or "Sem titulo", 14, "bold",
                 wraplength=360, justify="left").pack(fill="x")
        if livro.get("autor"):
            C.rotulo(corpo, livro["autor"], 11, "normal", "texto_suave").pack(fill="x")

        segundos = livro.get("segundos_lidos") or 0
        detalhes = [
            f"{livro.get('total_paginas') or '?'} paginas",
            f"lendo a pagina {(livro.get('pagina_atual') or 0) + 1}",
            f"{segundos // 3600}h {(segundos % 3600) // 60}min de leitura",
        ]
        C.rotulo(corpo, "  ·  ".join(detalhes), 10, "normal", "texto_suave").pack(
            fill="x", pady=(ESP["xs"], 0))
        C.rotulo(corpo, f"em {nome_pasta or 'Sem pasta'}", 10, "normal", "destaque").pack(
            fill="x", pady=(ESP["xs"], 0))
        C.rotulo(corpo, livro.get("caminho_pdf") or "", 9, "normal", "texto_suave",
                 wraplength=360, justify="left").pack(fill="x", pady=(ESP["sm"], 0))

        C.separador(corpo)

        def envolver(funcao):
            def executar():
                C.fechar_modal(modal)
                funcao()
            return executar

        fixado = bool(livro.get("fixado"))
        opcoes = [
            ("Abrir livro", None, ao_abrir, "primario"),
            ("Renomear livro", None, ao_renomear, "secundario"),
            ("Definir categoria", None, ao_categorizar, "secundario"),
            ("Mover para pasta", None, ao_mover, "secundario"),
            ("Desafixar do topo" if fixado else "Fixar no topo", None, ao_fixar, "secundario"),
            ("Abrir pasta do arquivo", None, ao_abrir_pasta, "secundario"),
            ("Recriar capa", None, ao_recriar_capa, "secundario"),
            ("Remover da biblioteca", None, ao_remover, "perigo"),
        ]
        for texto, icone, comando, variante in opcoes:
            C.botao(corpo, texto, envolver(comando), largura=380, altura=36,
                    variante=variante, icone=icone, ancora="w").pack(fill="x", pady=3)
        return modal

    # =====================================================================
    # NOTAS
    # =====================================================================
    @staticmethod
    def abrir_notas(pai, banco, id_livro, pagina, ao_salvar=None):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, f"Anotacoes — pagina {pagina + 1}", 560, 460,
                         pai.winfo_toplevel())

        corpo = C.quadro(modal, "transparent")
        corpo.pack(fill="both", expand=True, padx=ESP["lg"], pady=ESP["lg"])

        cabecalho = C.quadro(corpo, "transparent")
        cabecalho.pack(fill="x")
        C.rotulo(cabecalho, f"Anotacoes da pagina {pagina + 1}", 14, "bold").pack(side="left")
        C.rotulo(cabecalho, "Ctrl+S salva", 10, "normal", "texto_suave",
                 ancora="e").pack(side="right")

        editor = ctk.CTkTextbox(corpo, wrap="word", corner_radius=RAIO["sm"], border_width=0,
                                fg_color=T.superficie, text_color=T.texto,
                                font=ctk.CTkFont(FONTE_UI, 13))
        C.registrar_estilo(editor, fg_color="superficie", text_color="texto")
        editor.pack(fill="both", expand=True, pady=ESP["md"])
        original = banco.obter_nota(id_livro, pagina)
        editor.insert("1.0", original)
        editor.focus_set()

        def salvar(_=None):
            banco.salvar_nota(id_livro, pagina, editor.get("1.0", "end-1c"))
            C.fechar_modal(modal)
            if ao_salvar:
                ao_salvar()

        def fechar_salvando(_=None):
            # Esc e o X salvam sozinhos: apertar Esc por habito apagava uma
            # anotacao longa. So o botao Cancelar descarta de proposito.
            try:
                alterado = editor.get("1.0", "end-1c") != original
            except Exception:
                alterado = False
            if alterado:
                salvar()
                try:
                    C.Toast.mostrar(pai, "Anotacao salva.", "ok", 1200)
                except Exception:
                    pass
            else:
                C.fechar_modal(modal)

        modal.bind("<Escape>", fechar_salvando)
        modal.protocol("WM_DELETE_WINDOW", fechar_salvando)

        acoes = C.quadro(corpo, "transparent")
        acoes.pack(fill="x")
        C.botao(acoes, "Salvar", salvar, largura=130).pack(side="right")
        C.botao(acoes, "Cancelar", lambda: C.fechar_modal(modal), largura=110,
                variante="secundario").pack(side="right", padx=ESP["sm"])
        modal.bind("<Control-s>", salvar)
        return modal

    # =====================================================================
    # ZOOM DE IMAGEM
    # =====================================================================
    @staticmethod
    def abrir_zoom_imagem(pai, dados_imagem):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, "Visualizar imagem", 900, 700, pai.winfo_toplevel())

        try:
            original = Image.open(io.BytesIO(dados_imagem))
            if original.mode not in ("RGB", "RGBA", "L"):
                original = original.convert("RGB")
        except Exception:
            C.rotulo(modal, "Nao foi possivel abrir esta imagem.", 12, "normal",
                     "texto_suave", ancora="center").pack(expand=True)
            return modal

        area = C.rolavel(modal, "fundo")
        area.pack(fill="both", expand=True, padx=ESP["sm"], pady=ESP["sm"])
        visor = ctk.CTkLabel(area, text="")
        visor.pack(expand=True)

        controles = C.quadro(modal, "superficie", height=56)
        controles.pack(side="bottom", fill="x")

        lbl_fator = C.rotulo(controles, "100%", 11, "bold", ancora="center")
        lbl_fator.pack(side="left", padx=ESP["lg"])

        estado = {"fator": 1.0}

        def redesenhar(valor=None):
            if valor is not None:
                estado["fator"] = float(valor)
            fator = estado["fator"]
            largura = max(24, int(original.width * fator))
            altura = max(24, int(original.height * fator))
            imagem = ctk.CTkImage(light_image=original, dark_image=original,
                                  size=(largura, altura))
            visor.configure(image=imagem)
            visor.image = imagem
            lbl_fator.configure(text=f"{int(fator * 100)}%")

        def ajustar_a_janela():
            modal.update_idletasks()
            disponivel_l = max(120, area.winfo_width() - 30)
            disponivel_a = max(120, area.winfo_height() - 30)
            fator = min(disponivel_l / original.width, disponivel_a / original.height, 3.0)
            controle.set(fator)
            redesenhar(fator)

        controle = ctk.CTkSlider(controles, from_=0.15, to=4.0, command=redesenhar,
                                 button_color=T.destaque, button_hover_color=T.destaque_hover,
                                 progress_color=T.destaque, fg_color=T.superficie_alt)
        controle.set(1.0)
        controle.pack(side="left", expand=True, fill="x", padx=ESP["md"], pady=ESP["md"])

        C.botao(controles, "Ajustar", ajustar_a_janela, largura=90, altura=30,
                variante="secundario").pack(side="left", padx=(0, ESP["sm"]))

        def salvar():
            from tkinter import filedialog
            destino = filedialog.asksaveasfilename(
                defaultextension=".png", initialdir=user_path(),
                filetypes=[("Imagem PNG", "*.png")])
            if destino:
                try:
                    original.save(destino)
                    C.Toast.mostrar(pai, "Imagem salva.", "ok")
                except Exception as erro:
                    C.Toast.mostrar(pai, f"Falha ao salvar: {erro}", "erro")

        C.botao(controles, "Salvar", salvar, largura=90, altura=30,
                variante="secundario").pack(side="left", padx=(0, ESP["lg"]))

        redesenhar(1.0)
        modal.after(200, ajustar_a_janela)
        return modal

    # =====================================================================
    # CONFIGURACOES
    # =====================================================================
    @staticmethod
    def abrir_configuracoes(pai, cfg, ao_mudar_tema=None):
        modal = ctk.CTkToplevel(pai)
        C.preparar_modal(modal, "Configuracoes", 660, 640, pai.winfo_toplevel())

        abas = ctk.CTkTabview(modal, fg_color=T.superficie, segmented_button_fg_color=T.superficie_alt,
                              segmented_button_selected_color=T.destaque,
                              segmented_button_selected_hover_color=T.destaque_hover,
                              segmented_button_unselected_color=T.superficie_alt,
                              text_color=T.texto, corner_radius=RAIO["md"])
        abas.pack(fill="both", expand=True, padx=ESP["md"], pady=ESP["md"])
        aba_ia = abas.add("Assistente de IA")
        aba_voz = abas.add("Voz")
        aba_ctx = abas.add("Contexto")
        aba_atalhos = abas.add("Atalhos")
        aba_sobre = abas.add("Sobre")

        Modais._aba_ia(aba_ia, cfg)
        Modais._aba_voz(aba_voz, cfg)
        Modais._aba_contexto(aba_ctx, cfg)
        Modais._aba_atalhos(aba_atalhos)
        Modais._aba_sobre(aba_sobre, cfg)
        return modal

    @staticmethod
    def _aba_ia(pai, cfg):
        area = C.rolavel(pai, "superficie")
        area.pack(fill="both", expand=True)

        C.rotulo(area, "Como o assistente escolhe o provedor", 13, "bold").pack(
            fill="x", pady=(ESP["sm"], ESP["xs"]))
        C.rotulo(area,
                 "Ele tenta na ordem: chaves de API preenchidas → IA local → modo "
                 "offline. Se um falhar, cai automaticamente para o proximo, entao o "
                 "botao Processar nunca fica sem resposta.",
                 11, "normal", "texto_suave", wraplength=520, justify="left").pack(
            fill="x", pady=(0, ESP["md"]))

        Modais._bloco_ia_local(area, cfg)

        # Sem este seletor, quem tinha uma chave salva nunca mais via a IA
        # local responder: chave configurada sempre ia para a frente da fila,
        # e nao havia como dizer "prefiro a local". Privacidade e custo sao
        # motivos legitimos para preferir, e a escolha e de quem le.
        C.rotulo(area, "Quem responde primeiro", 13, "bold").pack(
            fill="x", pady=(ESP["lg"], ESP["xs"]))
        preferencias = ["IA local (nao envia nada para fora)",
                        "Claude", "OpenAI", "Gemini"]
        chaves = {"IA local (nao envia nada para fora)": "Local",
                  "Claude": "Claude", "OpenAI": "OpenAI", "Gemini": "Gemini"}
        inverso = {v: k for k, v in chaves.items()}
        atual = cfg.obter_provedor()
        C.seletor(area, preferencias,
                  lambda v: cfg.definir_provedor(chaves[v]), largura=330,
                  valor=inverso.get(atual, preferencias[0])).pack(fill="x")
        C.rotulo(area,
                 "Os outros continuam na fila como reserva: se o primeiro falhar, "
                 "o proximo assume sozinho.",
                 9, "normal", "texto_suave", wraplength=520, justify="left").pack(
            fill="x", pady=(2, 0))

        C.rotulo(area, "Chaves de API (opcional)", 13, "bold").pack(
            fill="x", pady=(ESP["lg"], ESP["xs"]))
        C.rotulo(area,
                 "So preencha se quiser respostas de um modelo grande, na nuvem. "
                 "A IA local acima ja responde sem chave e sem enviar nada.",
                 10, "normal", "texto_suave", wraplength=520, justify="left").pack(
            fill="x", pady=(0, ESP["sm"]))

        entradas = {}
        modelos_escolhidos = {}
        provedores = [
            ("Claude", "Anthropic — cole a chave que comeca com sk-ant-"),
            ("OpenAI", "Chave da OpenAI, comeca com sk-"),
            ("Gemini", "Chave do Google AI Studio"),
        ]
        for nome, dica in provedores:
            bloco = C.quadro(area, "superficie_alt", raio=RAIO["sm"])
            bloco.pack(fill="x", pady=ESP["xs"])
            linha = C.quadro(bloco, "transparent")
            linha.pack(fill="x", padx=ESP["md"], pady=ESP["sm"])
            C.rotulo(linha, nome, 12, "bold", ancora="w", width=70).pack(side="left")
            entrada = C.campo(linha, "Nao configurado", largura=330, show="*")
            entrada.insert(0, cfg.obter_chave(nome))
            entrada.pack(side="left", padx=ESP["sm"])

            def alternar_visibilidade(campo=entrada):
                campo.configure(show="" if campo.cget("show") == "*" else "*")

            C.botao_icone(linha, "visivel", alternar_visibilidade, "Mostrar/ocultar",
                          tamanho=30).pack(side="left")
            C.rotulo(bloco, dica, 9, "normal", "texto_suave").pack(
                fill="x", padx=ESP["md"], pady=(0, ESP["xs"]))
            entradas[nome] = entrada
            Modais._seletor_de_modelo(bloco, cfg, nome, modelos_escolhidos)

        status = C.rotulo(area, "", 10, "normal", "texto_suave")
        status.pack(fill="x", pady=ESP["sm"])

        def salvar():
            for nome, entrada in entradas.items():
                cfg.definir_chave(nome, entrada.get().strip())
            for nome, obter in modelos_escolhidos.items():
                cfg.definir(f"modelo_{nome}", obter())
            status.configure(text="Preferencias salvas.", text_color=T.sucesso)

        def testar():
            from ia.manager import AIManager
            status.configure(text="Testando… (a IA local pode levar alguns segundos "
                                  "para ligar)", text_color=T.texto_suave)
            salvar()
            gerente = AIManager(cfg)

            # Em segundo plano: descrever a fila completa sonda portas e pode
            # ligar a IA local -- na linha da janela, ela congelava.
            def trabalhador():
                try:
                    texto = f"Ordem ativa: {gerente.descrever_fila()}"
                except Exception as erro:
                    texto = f"Falha ao testar: {erro}"

                def mostrar():
                    try:
                        if status.winfo_exists():
                            status.configure(text=texto, text_color=T.texto)
                    except Exception:
                        pass
                try:
                    pai.after(0, mostrar)
                except Exception:
                    pass
            threading.Thread(target=trabalhador, daemon=True).start()

        acoes = C.quadro(area, "transparent")
        acoes.pack(fill="x", pady=ESP["md"])
        C.botao(acoes, "Salvar", salvar, largura=130).pack(side="right")
        C.botao(acoes, "Testar provedores", testar, largura=160,
                variante="secundario").pack(side="right", padx=ESP["sm"])

    @staticmethod
    def _seletor_de_modelo(bloco, cfg, provedor, modelos_escolhidos):
        """Linha "Modelo: [Haiku | Sonnet | Opus | Outro…]" de um provedor.

        O modelo leve custa uma fracao e responde bem resumo, flashcard e
        pergunta sobre o livro; o maior so compensa em raciocinio longo. A
        escolha e de quem paga a conta. "Outro…" aceita qualquer nome que o
        provedor publicar depois desta versao.
        """
        from ia.providers import MODELOS
        opcoes = MODELOS.get(provedor, [])
        rotulos = [r for _m, r in opcoes] + ["Outro (digitar o nome)…"]
        por_rotulo = {r: m for m, r in opcoes}
        atual = (cfg.obter(f"modelo_{provedor}", "") or "").strip()
        padrao = {"Claude": "claude-opus-5-5", "OpenAI": "gpt-4o-mini",
                  "Gemini": "gemini-flash-latest"}.get(provedor, "")
        conhecido = next((r for m, r in opcoes if m == (atual or padrao)), None)

        linha = C.quadro(bloco, "transparent")
        linha.pack(fill="x", padx=ESP["md"], pady=(0, ESP["sm"]))
        C.rotulo(linha, "Modelo", 11, "normal", "texto_suave", width=70).pack(side="left")
        livre = C.campo(linha, "ex.: claude-haiku-4-5", largura=180)
        if atual and not conhecido:
            livre.insert(0, atual)

        def trocar(valor):
            if valor in por_rotulo:
                livre.pack_forget()
            else:
                livre.pack(side="left", padx=ESP["sm"])

        seletor = C.seletor(linha, rotulos, trocar, largura=250,
                            valor=conhecido or rotulos[-1])
        seletor.pack(side="left", padx=ESP["sm"])
        trocar(seletor.get())

        def escolhido():
            valor = seletor.get()
            return por_rotulo.get(valor) or livre.get().strip()
        modelos_escolhidos[provedor] = escolhido

    # ------------------------------------------------------------- ia local
    @staticmethod
    def _bloco_ia_local(area, cfg):
        """Estado da IA local e, se faltar, o unico clique que ela exige.

        Nao ha campo de URL nem de nome de modelo aqui de proposito. Se o
        usuario ja tem Ollama ou LM Studio aberto, o leitor acha sozinho e usa
        o menor modelo que encontrar; se nao tem, o que ele precisa e de um
        botao, nao de um formulario que exige saber o que e uma porta TCP.
        """
        from ia import local

        caixa = C.quadro(area, "superficie_alt", raio=RAIO["sm"])
        caixa.pack(fill="x", pady=(0, ESP["sm"]))

        C.rotulo(caixa, "IA local — sem chave, sem enviar nada para fora",
                 12, "bold").pack(fill="x", padx=ESP["md"], pady=(ESP["sm"], 2))
        estado = C.rotulo(caixa, "Procurando…", 10, "normal", "texto_suave",
                          wraplength=500, justify="left")
        estado.pack(fill="x", padx=ESP["md"])

        barra = ctk.CTkProgressBar(caixa, height=6, progress_color=T.destaque,
                                   fg_color=T.superficie)
        C.registrar_estilo(barra, progress_color="destaque", fg_color="superficie")
        barra.set(0)

        # Escolha do tamanho do modelo. Existe porque a resposta certa depende
        # da maquina, e so quem esta nela sabe quanta memoria quer gastar. O
        # aviso de RAM e medido na hora, para o download de 940 MB nao virar
        # descoberta tardia de que o modelo nao cabe.
        local.definir_modelo(cfg.obter("ia_modelo", "0.5b"))
        rotulos = {dados["rotulo"]: chave for chave, dados in local.MODELOS.items()}
        atual = local.modelo_atual()["rotulo"]

        aviso_ram = C.rotulo(caixa, "", 9, "normal", "texto_suave",
                             wraplength=470, justify="left")

        def ao_trocar(rotulo):
            chave = rotulos.get(rotulo, "0.5b")
            cfg.definir("ia_modelo", chave)
            local.definir_modelo(chave)
            pintar_ram()
            mostrar_estado()

        def pintar_ram():
            livre = local.ram_livre_mb()
            preciso = local.modelo_atual()["ram_mb"]
            if not livre:
                aviso_ram.configure(text="")
                return
            if livre < preciso:
                aviso_ram.configure(
                    text=f"Atencao: este modelo precisa de ~{preciso} MB e ha "
                         f"{livre} MB livres agora. Ele vai usar disco como "
                         "memoria e responder bem mais devagar.",
                    text_color=T.perigo)
            else:
                aviso_ram.configure(
                    text=f"Cabe: precisa de ~{preciso} MB e ha {livre} MB livres.",
                    text_color=T.texto_suave)

        C.seletor(caixa, list(rotulos), ao_trocar, largura=380,
                  valor=atual).pack(fill="x", padx=ESP["md"], pady=(ESP["xs"], 2))
        aviso_ram.pack(fill="x", padx=ESP["md"])
        C.rotulo(caixa,
                 "Como a resposta e escrita a partir dos trechos que o indice "
                 "ja encontrou, o modelo maior melhora principalmente a redacao "
                 "e o modo de conhecimento geral — nao a busca no livro.",
                 9, "normal", "texto_suave", wraplength=470,
                 justify="left").pack(fill="x", padx=ESP["md"], pady=(2, 0))

        linha = C.quadro(caixa, "transparent")
        linha.pack(fill="x", padx=ESP["md"], pady=(ESP["sm"], ESP["sm"]))
        botao = C.botao(linha, "Instalar IA local", lambda: None, largura=180)
        botao.pack(side="left")
        botao_remover = C.botao(linha, "Remover", lambda: None, largura=110,
                                variante="secundario")

        def vivo():
            try:
                return bool(area.winfo_exists())
            except Exception:
                return False

        def mostrar_estado():
            """Consulta em thread: sondar portas e subir servidor demoram."""
            def trabalhador():
                achado = None
                try:
                    # Sem acordar o motor proprio: aqui so queremos SABER o que
                    # existe, e ligar 600 MB de modelo para escrever uma linha
                    # de status seria caro demais.
                    achado = local.descobrir(incluir_proprio=False)
                except Exception:
                    achado = None
                if vivo():
                    area.after(0, lambda: pintar(achado))

            threading.Thread(target=trabalhador, daemon=True).start()

        def pintar(achado):
            if not vivo():
                return
            if achado:
                rotulo, _url, _dialeto, modelo = achado
                estado.configure(
                    text=f"Usando o {rotulo} que ja esta aberto nesta maquina, "
                         f"com o modelo mais leve que ele tem ({modelo}). "
                         f"Nada a instalar.",
                    text_color=T.sucesso)
                botao.pack_forget()
            elif local.instalado():
                estado.configure(
                    text=f"Instalada e pronta ({local.modelo_atual()['nome']}). "
                         "Ela sobe sozinha na primeira pergunta e se desliga "
                         "depois de dez minutos parada.",
                    text_color=T.sucesso)
                botao.pack_forget()
                botao_remover.pack(side="left")
            else:
                estado.configure(
                    text="Nenhum modelo local nesta maquina. Um clique baixa o "
                         f"motor e um modelo (~{local.modelo_atual()['mb'] + 18} MB, "
                         "uma vez). Depois disso o assistente funciona offline, "
                         "sem chave e sem mandar seu livro para lugar nenhum.",
                    text_color=T.texto_suave)
                botao.configure(command=instalar, state="normal",
                                text="Instalar IA local")
                botao.pack(side="left")

        def instalar():
            botao.configure(state="disabled", text="Instalando…")
            barra.pack(fill="x", padx=ESP["md"], pady=(ESP["xs"], 0))
            barra.set(0)

            def progresso(fracao, mensagem):
                if not vivo():
                    return
                area.after(0, lambda: (barra.set(fracao),
                                       estado.configure(text=mensagem,
                                                        text_color=T.texto_suave)))

            def terminou(ok, mensagem):
                if not vivo():
                    return

                def aplicar():
                    barra.pack_forget()
                    estado.configure(text=mensagem,
                                     text_color=T.sucesso if ok else T.perigo)
                    if ok:
                        botao.pack_forget()
                        botao_remover.pack(side="left")
                    else:
                        botao.configure(state="normal", text="Tentar de novo")

                area.after(0, aplicar)

            local.instalar(ao_progredir=progresso, ao_terminar=terminou)

        def remover():
            local.desinstalar()
            botao_remover.pack_forget()
            mostrar_estado()

        botao_remover.configure(command=remover)
        pintar_ram()
        mostrar_estado()

    @staticmethod
    def _aba_contexto(pai, cfg):
        """Motor de contexto: adapta a leitura ao programa que voce usa ao lado.

        A tela e explicita sobre o que e lido e sobre o que NAO sai da
        maquina. Recurso desse tipo so ganha confianca se for transparente e
        facil de desligar.
        """
        from services import contexto as ctx

        area = C.rolavel(pai, "superficie")
        area.pack(fill="both", expand=True)

        C.rotulo(area, "Adaptar ao que voce usa ao lado", 13, "bold").pack(
            fill="x", pady=(ESP["sm"], ESP["xs"]))
        C.rotulo(area,
                 "Com isto ligado, o Lumina observa qual programa esta em primeiro "
                 "plano e sugere o modo de leitura combinando. Abriu o VS Code com "
                 "um arquivo .py? Ele sugere modo Programacao. Abriu o Word? Sugere "
                 "Prosa. A sugestao aparece numa faixa: nada muda sem voce aceitar.",
                 11, "normal", "texto_suave", wraplength=500,
                 justify="left").pack(fill="x")

        variavel = ctk.StringVar(value="1" if cfg.obter_bool("contexto_ativo", False) else "0")
        chave = ctk.CTkSwitch(
            area, text="Ativar deteccao de contexto", variable=variavel,
            onvalue="1", offvalue="0",
            command=lambda: cfg.definir("contexto_ativo", variavel.get()),
            font=ctk.CTkFont(FONTE_UI, 12, "bold"), text_color=T.texto,
            progress_color=T.destaque, button_color=T.superficie,
            fg_color=T.superficie_alt)
        C.registrar_estilo(chave, text_color="texto", progress_color="destaque",
                           button_color="superficie", fg_color="superficie_alt")
        chave.pack(fill="x", pady=ESP["md"])

        # Interruptor separado, e nunca ligado junto com o de cima: ler o
        # CONTEUDO de janelas alheias e outra categoria de permissao. Quem
        # aceita "olhe qual programa esta aberto" nao aceitou, com isso,
        # "leia o que esta escrito nele".
        C.rotulo(area, "Ler o conteudo da janela ao lado", 12, "bold").pack(
            fill="x", pady=(ESP["md"], 2))
        C.rotulo(area,
                 "O nome do programa diz pouco: o navegador serve tanto para um "
                 "video de calculo quanto para um tutorial de Python. Com isto "
                 "ligado, o Lumina le o TEXTO da janela ao lado e acerta o "
                 "assunto — 5 de 6 nos testes, contra 2 de 6 adivinhando pelo "
                 "nome do programa.",
                 10, "normal", "texto_suave", wraplength=500,
                 justify="left").pack(fill="x")

        var_tela = ctk.StringVar(value="1" if cfg.obter_bool("ler_tela", False) else "0")
        chave_tela = ctk.CTkSwitch(
            area, text="Ler o texto das janelas ao lado", variable=var_tela,
            onvalue="1", offvalue="0",
            command=lambda: cfg.definir("ler_tela", var_tela.get()),
            font=ctk.CTkFont(FONTE_UI, 12, "bold"), text_color=T.texto,
            progress_color=T.destaque, button_color=T.superficie,
            fg_color=T.superficie_alt)
        C.registrar_estilo(chave_tela, text_color="texto", progress_color="destaque",
                           button_color="superficie", fg_color="superficie_alt")
        chave_tela.pack(fill="x", pady=(ESP["sm"], ESP["md"]))

        caixa = C.quadro(area, "superficie_alt", raio=RAIO["sm"])
        caixa.pack(fill="x")
        C.rotulo(caixa, "O que e lido, e o que nao acontece", 11, "bold").pack(
            fill="x", padx=ESP["md"], pady=(ESP["sm"], ESP["xs"]))
        for linha in (
            "Lido: nome do executavel ao lado e o titulo da janela.",
            "Com a segunda opcao ligada, tambem o texto visivel daquela janela.",
            "O texto vem da arvore de acessibilidade do Windows — a mesma dos "
            "leitores de tela. Nenhuma imagem da tela e capturada, nao ha OCR.",
            "Nada e enviado para servidor nenhum: a analise e local e feita por "
            "regras, em menos de um milissegundo.",
            "O texto lido NAO e gravado em disco — vive em memoria por 45 "
            "segundos e e descartado.",
            "O unico registro guardado e um contador de quais modos voce confirmou.",
            "Desligando, a leitura para na hora e nada mais e observado.",
        ):
            C.rotulo(caixa, "•  " + linha, 10, "normal", "texto_suave",
                     wraplength=470, justify="left").pack(fill="x", padx=ESP["md"])
        C.rotulo(caixa, "", 4).pack()

        estado = C.rotulo(area, "", 10, "normal", "texto_suave", wraplength=500,
                          justify="left")
        estado.pack(fill="x", pady=ESP["md"])

        def ver_agora():
            janela = ctx.janela_em_primeiro_plano()
            if not janela:
                estado.configure(text="Nao consegui ler a janela ativa neste sistema.")
                return
            sugestao = ctx.analisar(janela, memoria=ctx.Memoria(cfg))
            estado.configure(
                text=f"Janela ativa: {janela.processo or '?'}\n"
                     f"Titulo: {janela.titulo[:70]}\n"
                     f"Sugeriria: {sugestao.render or 'nada'} "
                     f"({int(sugestao.confianca * 100)}%) — {sugestao.motivo or 'sem pistas'}")

        def esquecer():
            ctx.Memoria(cfg).esquecer_tudo()
            estado.configure(text="Preferencias aprendidas apagadas.")

        acoes = C.quadro(area, "transparent")
        acoes.pack(fill="x")
        C.botao(acoes, "Esquecer o aprendido", esquecer, largura=180,
                variante="secundario").pack(side="right")
        C.botao(acoes, "Ver o que seria detectado", ver_agora, largura=200).pack(
            side="right", padx=ESP["sm"])

    @staticmethod
    def _aba_voz(pai, cfg):
        """Controles da leitura em voz alta.

        O volume vai ate 200% porque o mecanismo do Windows para em 100 e
        ainda assim sai baixo -- acima disso o audio e sintetizado em arquivo,
        normalizado pelo pico e amplificado com limitador.
        """
        from services.voz import Narrador

        area = C.rolavel(pai, "superficie")
        area.pack(fill="both", expand=True)
        narrador = Narrador()

        if not narrador.disponivel():
            C.rotulo(area, "Este sistema nao tem sintese de voz instalada.",
                     12, "bold", "perigo").pack(fill="x", pady=ESP["lg"])
            return

        C.rotulo(area, "Voz", 13, "bold").pack(fill="x", pady=(ESP["sm"], ESP["xs"]))
        vozes = narrador.vozes() or []
        opcoes = ["Automatica (prefere portugues)"] + vozes
        atual = cfg.obter("voz_nome", "")
        C.seletor(area, opcoes,
                  lambda v: cfg.definir("voz_nome", "" if v.startswith("Automatica") else v),
                  largura=380,
                  valor=atual if atual in vozes else opcoes[0]).pack(fill="x")

        def controle(titulo, chave, minimo, maximo, padrao, sufixo="", dica=""):
            linha = C.quadro(area, "transparent")
            linha.pack(fill="x", pady=(ESP["md"], 0))
            C.rotulo(linha, titulo, 11, "bold").pack(side="left")
            valor = C.rotulo(linha, f"{cfg.obter_int(chave, padrao)}{sufixo}",
                             11, "bold", "destaque", ancora="e")
            valor.pack(side="right")
            if dica:
                C.rotulo(area, dica, 9, "normal", "texto_suave",
                         wraplength=470, justify="left").pack(fill="x")

            def mudou(v):
                cfg.definir(chave, int(v))
                valor.configure(text=f"{int(v)}{sufixo}")

            controle_slider = ctk.CTkSlider(
                area, from_=minimo, to=maximo, command=mudou, height=16,
                number_of_steps=int(maximo - minimo),
                button_color=T.destaque, button_hover_color=T.destaque_hover,
                progress_color=T.destaque, fg_color=T.superficie_alt)
            C.registrar_estilo(controle_slider, button_color="destaque",
                               button_hover_color="destaque_hover",
                               progress_color="destaque", fg_color="superficie_alt")
            controle_slider.set(cfg.obter_int(chave, padrao))
            controle_slider.pack(fill="x")

        controle("Volume", "voz_volume", 20, 200, 160, "%",
                 "Acima de 100% o audio e normalizado e amplificado. Custa alguns "
                 "segundos de preparo antes de comecar a falar.")
        controle("Velocidade", "voz_velocidade", -10, 10, 0)
        controle("Tom", "voz_tom", -10, 10, 0, "", "Em semitons. Mexer no tom "
                 "tambem usa o modo amplificado.")

        estado = C.rotulo(area, "", 10, "normal", "texto_suave")
        estado.pack(fill="x", pady=(ESP["md"], 0))

        def testar():
            frase = "Instalação prática de aplicação não é óbvia. Este é um teste de voz."
            volume = cfg.obter_int("voz_volume", 160)
            tom = cfg.obter_int("voz_tom", 0)
            velocidade = cfg.obter_int("voz_velocidade", 0)
            nome = cfg.obter("voz_nome", "") or None
            if volume > 100 or tom:
                # A sintese roda em outra thread; a janela continua respondendo.
                estado.configure(text="Preparando o audio…")
                narrador.falar_alto(frase, voz=nome, velocidade=velocidade,
                                    ganho=volume / 100.0, tom=tom)
            else:
                estado.configure(text="Tocando o teste…")
                narrador.falar(frase, voz=nome, velocidade=velocidade, volume=volume)

        acoes = C.quadro(area, "transparent")
        acoes.pack(fill="x", pady=ESP["md"])
        C.botao(acoes, "Parar", narrador.parar, largura=100,
                variante="secundario").pack(side="right")
        C.botao(acoes, "Testar voz", testar, largura=130).pack(side="right",
                                                               padx=ESP["sm"])

    @staticmethod
    def _aba_atalhos(pai):
        area = C.rolavel(pai, "superficie")
        area.pack(fill="both", expand=True)

        grupos = [
            ("Leitura", [
                ("→ / Espaco", "Avancar (rola e vira a pagina no fim)"),
                ("← / Shift+Espaco", "Voltar"),
                ("Home / End", "Primeira / ultima pagina"),
                ("Ctrl+G", "Ir para uma pagina pelo numero"),
                ("Alt+← / Alt+→", "Voltar / refazer o ultimo salto (sumario, busca…)"),
                ("Roda do mouse", "Rola; no modo Livre vira no fim da pagina"),
                ("Ctrl+Espaco", "Iniciar/pausar a rolagem automatica"),
                ("Ctrl + / Ctrl -", "Aumentar / diminuir a fonte"),
                ("Ctrl+P", "Escolher COMO virar a pagina (4 modos)"),
            ]),
            ("Janela e tela dividida", [
                ("Ctrl+T", "Manter sobre as outras janelas"),
                ("Ctrl+Shift+←/→", "Encaixar em metade da tela"),
                ("Ctrl+Shift+↑", "Maximizar  ·  Ctrl+Shift+↓ restaurar"),
                ("Alt+C", "Modo coluna (faixa estreita a direita)"),
                ("Ctrl+[ / Ctrl+]", "Menos / mais opacidade"),
                ("F11", "Modo foco  ·  Esc sai"),
            ]),
            ("Estudo", [
                ("Ctrl+F", "Buscar no livro inteiro  ·  F3 proximo"),
                ("Ctrl+B", "Sumario, marcadores e notas"),
                ("Ctrl+H", "Grifar a selecao"),
                ("Ctrl+N", "Anotar a pagina"),
                ("Ctrl+D", "Marcar a pagina"),
                ("Ctrl+E", "Exportar grifos, notas e marcadores (Markdown)"),
                ("Ctrl+L", "Ler a pagina em voz alta"),
                ("Ctrl+I", "Assistente de estudo"),
                ("Ctrl+P", "Aparencia e tipografia"),
            ]),
        ]

        for titulo, itens in grupos:
            C.rotulo(area, titulo, 12, "bold").pack(fill="x", pady=(ESP["md"], ESP["xs"]))
            for atalho, descricao in itens:
                linha = C.quadro(area, "superficie_alt", raio=RAIO["sm"])
                linha.pack(fill="x", pady=1)
                C.rotulo(linha, atalho, 11, "bold", "destaque", width=150).pack(
                    side="left", padx=ESP["md"], pady=6)
                C.rotulo(linha, descricao, 11, "normal", "texto_suave").pack(
                    side="left", padx=(0, ESP["md"]))

    @staticmethod
    def _aba_sobre(pai, cfg=None):
        from core.branding import carregar_arte, carregar_logo

        # Rolavel: com o bloco de atualizacao, o conteudo passa da altura do
        # modal em telas com escala de 125-150%.
        area = C.rolavel(pai, "superficie")
        area.pack(fill="both", expand=True)

        # A arte completa ja traz o nome e o "PDF READER"; sem ela, cai no
        # simbolo + nome escrito.
        arte = carregar_arte(420)
        if arte is not None:
            ctk_img = ctk.CTkImage(light_image=arte, dark_image=arte,
                                   size=(arte.width, arte.height))
            rotulo_arte = ctk.CTkLabel(area, text="", image=ctk_img)
            rotulo_arte.image = ctk_img
            rotulo_arte.pack(pady=(ESP["xl"], ESP["sm"]))
        else:
            imagem = carregar_logo(96)
            if imagem is not None:
                ctk_img = ctk.CTkImage(light_image=imagem, dark_image=imagem, size=(96, 96))
                rotulo_logo = ctk.CTkLabel(area, text="", image=ctk_img)
                rotulo_logo.image = ctk_img
                rotulo_logo.pack(pady=(ESP["xxl"], ESP["md"]))
            C.rotulo(area, APP_NOME, 20, "bold", ancora="center").pack()
        C.rotulo(area, f"Versao {APP_VERSAO}", 11, "normal", "texto_suave",
                 ancora="center").pack(pady=(2, ESP["lg"]))
        C.rotulo(area,
                 "Converte PDF e EPUB em livro refluido para leitura confortavel em tela\n"
                 "dividida, ao lado das suas outras janelas de trabalho.",
                 12, "normal", "texto_suave", ancora="center", justify="center").pack()

        C.rotulo(area, f"Seus dados ficam em:\n{user_path()}", 9, "normal", "texto_suave",
                 ancora="center", justify="center").pack(pady=ESP["xl"])
        Modais._bloco_atualizacao(area, cfg)

    # =====================================================================
    # ATUALIZACAO
    # =====================================================================
    @staticmethod
    def _bloco_atualizacao(area, cfg):
        """Interruptor da verificacao diaria + botao "Procurar atualizacoes"."""
        caixa = C.quadro(area, "superficie_alt", raio=RAIO["sm"])
        caixa.pack(fill="x", padx=ESP["lg"], pady=(0, ESP["md"]))

        if cfg is not None:
            from services import atualizacao as at
            variavel = ctk.StringVar(value="1" if cfg.obter_bool(at.CHAVE_ATIVA, True) else "0")
            chave = ctk.CTkSwitch(
                caixa, text="Verificar atualizacoes automaticamente", variable=variavel,
                onvalue="1", offvalue="0",
                command=lambda: cfg.definir(at.CHAVE_ATIVA, variavel.get()),
                font=ctk.CTkFont(FONTE_UI, 12, "bold"), text_color=T.texto,
                progress_color=T.destaque, button_color=T.superficie,
                fg_color=T.superficie)
            C.registrar_estilo(chave, text_color="texto", progress_color="destaque",
                               button_color="superficie", fg_color="superficie")
            chave.pack(fill="x", padx=ESP["md"], pady=(ESP["sm"], 2))
            C.rotulo(caixa, "Uma consulta anonima por dia a pagina de Releases do GitHub. "
                            "Nada e instalado sem voce clicar em Atualizar.",
                     10, "normal", "texto_suave", wraplength=480,
                     justify="left").pack(fill="x", padx=ESP["md"])

        linha = C.quadro(caixa, "transparent")
        linha.pack(fill="x", padx=ESP["md"], pady=ESP["sm"])
        estado = C.rotulo(caixa, "", 10, "normal", "texto_suave", wraplength=480,
                          justify="left")
        barra = C.BarraProgresso(caixa, fundo="superficie_alt", trilho="superficie")
        achada = {"novidade": None}

        def vivo():
            try:
                return bool(caixa.winfo_exists())
            except Exception:
                return False

        def mostrar(texto, cor="texto_suave"):
            if vivo():
                estado.configure(text=texto, text_color=getattr(T, cor))
                estado.pack(fill="x", padx=ESP["md"], pady=(0, ESP["sm"]))

        def procurar():
            from services import atualizacao as at
            botao.configure(state="disabled")
            mostrar("Consultando o GitHub…")

            def terminou(novidade, erro):
                def aplicar():
                    if not vivo():
                        return
                    botao.configure(state="normal")
                    if erro is not None:
                        mostrar(f"Nao foi possivel consultar: {erro}", "perigo")
                    elif novidade is None:
                        mostrar(f"Voce ja esta na versao mais recente ({APP_VERSAO}).", "sucesso")
                    else:
                        achada["novidade"] = novidade
                        if novidade.pode_instalar:
                            mostrar(f"Versao {novidade.versao} disponivel.", "destaque")
                            botao.configure(text="Atualizar agora", command=atualizar)
                        else:
                            mostrar(f"Versao {novidade.versao} disponivel. Rodando do "
                                    "codigo-fonte: atualize com git pull.", "destaque")
                            botao.configure(text="Abrir pagina da versao", command=atualizar)

                try:
                    caixa.after(0, aplicar)
                except Exception:
                    pass

            if cfg is not None:
                at.registrar_verificacao(cfg)
            at.verificar_em_segundo_plano(terminou)

        def atualizar():
            botao.configure(state="disabled")
            barra.pack(fill="x", padx=ESP["md"], pady=(0, ESP["sm"]))

            def progresso(fracao, texto):
                if vivo():
                    barra.set(fracao)
                    mostrar(texto)

            def falhou(mensagem):
                if vivo():
                    barra.pack_forget()
                    botao.configure(state="normal", text="Tentar de novo")
                    mostrar(mensagem, "perigo")

            Modais.iniciar_atualizacao(caixa, achada["novidade"], progresso, falhou)

        botao = C.botao(linha, "Procurar atualizacoes", procurar, largura=190,
                        variante="secundario")
        botao.pack(side="left")

    @staticmethod
    def iniciar_atualizacao(widget, novidade, ao_progredir, ao_falhar):
        """Baixa e aplica a versao nova; dando certo, fecha o Lumina (o .cmd
        de troca reabre). Os callbacks rodam na thread do Tk.

        Do codigo-fonte nao ha o que trocar: abre a pagina da versao.
        """
        from services import atualizacao as at
        if not novidade.pode_instalar:
            import webbrowser
            webbrowser.open(novidade.pagina)
            return
        raiz = widget._root()

        def na_interface(funcao):
            try:
                raiz.after(0, funcao)
            except Exception:
                pass

        def progresso(fracao, texto):
            na_interface(lambda: ao_progredir(fracao, texto))

        def terminou(ok, mensagem):
            def aplicar():
                if not ok:
                    ao_falhar(mensagem)
                    return
                ao_progredir(1.0, mensagem)
                # _ao_fechar grava progresso e geometria antes de sair; o .cmd
                # espera este processo terminar para trocar o executavel.
                fechar = getattr(raiz, "_ao_fechar", None) or raiz.destroy
                raiz.after(600, fechar)
            na_interface(aplicar)

        ao_progredir(0.0, "Baixando…")
        at.baixar_e_aplicar_em_segundo_plano(novidade, progresso, terminou)

    @staticmethod
    def faixa_atualizacao(raiz, novidade):
        """Aviso discreto no topo da janela: "Lumina X disponivel [Atualizar]".

        Nao e modal nem rouba foco: quem esta no meio de um capitulo fecha com
        "Depois" e so ve de novo no dia seguinte.
        """
        faixa = C.quadro(raiz, "superficie_alt", raio=RAIO["pill"],
                         border_width=1, border_color=T.borda)
        texto = C.rotulo(faixa, f"{APP_NOME} {novidade.versao} disponivel", 12, "bold")
        texto.pack(side="left", padx=(ESP["lg"], ESP["md"]), pady=ESP["sm"])

        def fechar():
            try:
                faixa.destroy()
            except Exception:
                pass

        def progresso(fracao, mensagem):
            try:
                texto.configure(text=mensagem)
            except Exception:
                pass

        def falhou(mensagem):
            try:
                texto.configure(text=mensagem, text_color=T.perigo)
                botao.configure(state="normal", text="Tentar de novo")
            except Exception:
                pass

        def atualizar():
            if novidade.pode_instalar:
                botao.configure(state="disabled")
            Modais.iniciar_atualizacao(faixa, novidade, progresso, falhou)
            if not novidade.pode_instalar:
                fechar()

        rotulo_botao = "Atualizar" if novidade.pode_instalar else "Ver novidades"
        botao = C.botao(faixa, rotulo_botao, atualizar, largura=100, altura=28)
        botao.pack(side="left", pady=ESP["xs"])
        C.botao(faixa, "Depois", fechar, largura=70, altura=28,
                variante="fantasma").pack(side="left", padx=(ESP["xs"], ESP["sm"]),
                                          pady=ESP["xs"])
        faixa.place(relx=0.5, y=ESP["md"], anchor="n")
        faixa.lift()
        return faixa
