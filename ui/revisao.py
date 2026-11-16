# ui/revisao.py
"""Revisao espacada dos grifos e estatisticas de leitura.

Grifar e o primeiro passo; lembrar e o que importa. Cada grifo vira um cartao
(frente: o trecho grifado; verso: a nota da pagina, se houver) e volta no dia
certo pelo algoritmo SM-2 -- o mesmo do Anki. A aba de estatisticas mostra o
habito: dias seguidos, minutos por dia e o ritmo de leitura.

A logica mora em `services/revisao.py`; aqui so ha a interface.
"""
import threading
import tkinter as tk

import customtkinter as ctk

import ui.componentes as C
from configuracoes import ESP, FONTE_UI, RAIO, T
import services.revisao as revisao

# Notas do SM-2 (0..5) por botao. "Errei" reinicia o cartao; os outros
# espacam cada vez mais.
_BOTOES_NOTA = (("Errei", 1, "#E5534B"), ("Dificil", 3, "#D4A72C"),
                ("Bom", 4, "#3FB950"), ("Facil", 5, "#3FD3F2"))


def _formatar_tempo(segundos):
    minutos = int(segundos // 60)
    if minutos >= 60:
        return f"{minutos // 60}h {minutos % 60:02d}min"
    return f"{minutos} min"


class JanelaRevisao(ctk.CTkToplevel):
    """Janela de revisao + estatisticas. `ao_ir_para(id_livro, pagina)` abre
    o trecho original de um cartao no leitor."""

    def __init__(self, master, ao_ir_para=None, id_livro=None):
        super().__init__(master)
        self.title("Revisao e estatisticas")
        self.geometry("560x640")
        self.minsize(420, 480)
        self.configure(fg_color=T.fundo)
        self.transient(master.winfo_toplevel())
        self.ao_ir_para = ao_ir_para
        self.id_livro = id_livro
        self.cartoes = []
        self.atual = None

        self.abas = ctk.CTkTabview(self, fg_color=T.superficie,
                                   segmented_button_selected_color=T.destaque,
                                   segmented_button_selected_hover_color=T.destaque,
                                   text_color=T.texto)
        self.abas.pack(fill="both", expand=True, padx=ESP["md"], pady=ESP["md"])
        self._aba_rev = self.abas.add("Revisar")
        self._aba_est = self.abas.add("Estatisticas")
        self._montar_revisao()
        self._montar_estatisticas_vazio()
        self.bind("<Escape>", lambda e: self.destroy())
        for tecla, nota in (("1", 1), ("2", 3), ("3", 4), ("4", 5)):
            self.bind(tecla, lambda e, n=nota: self._responder(n))
        self.bind("<space>", lambda e: self._mostrar_verso())
        self.after(50, self._carregar)
        self.after(200, self._para_frente)

    def _para_frente(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------ revisao
    def _montar_revisao(self):
        aba = self._aba_rev
        self.lbl_fila = C.rotulo(aba, "Carregando…", 12, "bold")
        self.lbl_fila.pack(fill="x", padx=ESP["sm"], pady=(ESP["sm"], 2))
        self.lbl_origem = C.rotulo(aba, "", 10, "normal", "texto_suave")
        self.lbl_origem.pack(fill="x", padx=ESP["sm"])

        self.caixa_frente = ctk.CTkTextbox(aba, wrap="word", height=170,
                                           fg_color=T.superficie_alt, text_color=T.texto,
                                           font=ctk.CTkFont("Georgia", 15),
                                           corner_radius=RAIO["sm"], border_width=0)
        self.caixa_frente.pack(fill="both", expand=True, padx=ESP["sm"], pady=ESP["sm"])
        self.caixa_verso = ctk.CTkTextbox(aba, wrap="word", height=110,
                                          fg_color=T.superficie_alt, text_color=T.texto_suave,
                                          font=ctk.CTkFont(FONTE_UI, 12),
                                          corner_radius=RAIO["sm"], border_width=0)

        self.linha_acoes = C.quadro(aba, "transparent")
        self.linha_acoes.pack(fill="x", padx=ESP["sm"], pady=(0, ESP["sm"]))
        self.btn_mostrar = C.botao(self.linha_acoes, "Mostrar resposta  (espaco)",
                                   self._mostrar_verso, largura=220)
        self.btn_mostrar.pack(side="left")
        self.btn_ir = C.botao(self.linha_acoes, "Abrir no livro", self._ir_para_origem,
                              largura=130, variante="secundario")
        self.btn_ir.pack(side="right")

        self.linha_notas = C.quadro(aba, "transparent")
        for indice, (rotulo, nota, cor) in enumerate(_BOTOES_NOTA):
            botao = ctk.CTkButton(self.linha_notas, text=f"{rotulo}  ({indice + 1})",
                                  width=110, height=34, corner_radius=RAIO["sm"],
                                  fg_color=cor, hover_color=cor, text_color="#0A0F1C",
                                  font=ctk.CTkFont(FONTE_UI, 12, "bold"),
                                  command=lambda n=nota: self._responder(n))
            botao.pack(side="left", expand=True, fill="x", padx=2)

    def _carregar(self):
        def trabalhador():
            try:
                revisao.sincronizar_grifos()
                cartoes = revisao.cartoes_para_hoje(limite=50)
                estat = revisao.estatisticas(30)
            except Exception as erro:
                cartoes, estat = [], None
                print(f"[Revisao] {erro}")
            self.after(0, lambda: self._carregado(cartoes, estat))
        threading.Thread(target=trabalhador, daemon=True).start()

    def _carregado(self, cartoes, estat):
        if not self.winfo_exists():
            return
        self.cartoes = list(cartoes)
        self._proximo()
        if estat is not None:
            self._montar_estatisticas(estat)

    def _proximo(self):
        self.caixa_verso.pack_forget()
        self.linha_notas.pack_forget()
        self.btn_mostrar.configure(state="normal")
        if not self.cartoes:
            self.atual = None
            self.lbl_fila.configure(text="Tudo revisado por hoje.")
            self.lbl_origem.configure(text="Novos grifos viram cartoes sozinhos e voltam "
                                           "no dia certo.")
            self._escrever(self.caixa_frente, "Nenhum cartao pendente. ✓")
            self.btn_mostrar.configure(state="disabled")
            self.btn_ir.configure(state="disabled")
            return
        self.atual = self.cartoes.pop(0)
        restantes = len(self.cartoes) + 1
        self.lbl_fila.configure(text=f"{restantes} cartao(oes) para hoje"
                                     + ("  ·  novo" if self.atual.novo else ""))
        origem = self.atual.titulo_livro or ""
        if self.atual.pagina is not None and origem:
            origem += f"  ·  pagina {self.atual.pagina + 1}"
        self.lbl_origem.configure(text=origem)
        self.btn_ir.configure(state="normal" if self.atual.id_livro else "disabled")
        self._escrever(self.caixa_frente, self.atual.frente or "(sem texto)")

    def _mostrar_verso(self):
        if self.atual is None or self.linha_notas.winfo_ismapped():
            return
        verso = self.atual.verso or "Sem nota nesta pagina. Lembrou do contexto?"
        self._escrever(self.caixa_verso, verso)
        self.caixa_verso.pack(fill="x", padx=ESP["sm"], pady=(0, ESP["sm"]),
                              before=self.linha_acoes)
        self.linha_notas.pack(fill="x", padx=ESP["sm"], pady=(0, ESP["md"]))
        self.btn_mostrar.configure(state="disabled")

    def _responder(self, nota):
        if self.atual is None or not self.linha_notas.winfo_ismapped():
            return
        try:
            revisao.registrar_resposta(self.atual.id_cartao, nota)
        except Exception as erro:
            print(f"[Revisao] {erro}")
        if nota < 3:
            # Errou: volta ainda nesta sessao, no fim da fila.
            self.cartoes.append(self.atual)
        self._proximo()

    def _ir_para_origem(self):
        if self.atual and self.atual.id_livro and self.ao_ir_para:
            self.ao_ir_para(self.atual.id_livro, self.atual.pagina or 0)

    @staticmethod
    def _escrever(caixa, texto):
        caixa.configure(state="normal")
        caixa.delete("1.0", "end")
        caixa.insert("1.0", texto)
        caixa.configure(state="disabled")

    # ------------------------------------------------------- estatisticas
    def _montar_estatisticas_vazio(self):
        C.rotulo(self._aba_est, "Calculando…", 12, "normal", "texto_suave").pack(
            pady=ESP["lg"])

    def _montar_estatisticas(self, est):
        for filho in self._aba_est.winfo_children():
            filho.destroy()
        aba = C.rolavel(self._aba_est, "superficie")
        aba.pack(fill="both", expand=True)

        cartoes = C.quadro(aba, "transparent")
        cartoes.pack(fill="x", padx=ESP["sm"], pady=ESP["sm"])
        ritmo = (f"{est.paginas_por_hora:.0f} pag/h" if est.paginas_por_hora
                 else "—")
        for i, (titulo, valor) in enumerate((
                ("Dias seguidos", f"{est.sequencia_atual}"),
                ("Recorde", f"{est.maior_sequencia}"),
                ("30 dias", _formatar_tempo(est.total_segundos)),
                ("Ritmo", ritmo))):
            caixa = C.quadro(cartoes, "superficie_alt", raio=RAIO["sm"])
            caixa.grid(row=0, column=i, sticky="nsew", padx=3)
            cartoes.grid_columnconfigure(i, weight=1)
            C.rotulo(caixa, valor, 18, "bold", ancora="center").pack(pady=(ESP["sm"], 0))
            C.rotulo(caixa, titulo, 9, "normal", "texto_suave", ancora="center").pack(
                pady=(0, ESP["sm"]))

        C.rotulo(aba, "Minutos por dia (30 dias)", 11, "bold").pack(
            fill="x", padx=ESP["sm"], pady=(ESP["md"], 2))
        grafico = tk.Canvas(aba, height=130, highlightthickness=0,
                            bg=T.superficie)
        grafico.pack(fill="x", padx=ESP["sm"])
        grafico.bind("<Configure>", lambda e, d=est.dias: self._desenhar_barras(grafico, d))

        if est.cartoes_pendentes:
            C.rotulo(aba, f"{est.cartoes_pendentes} cartao(oes) esperando revisao.",
                     10, "normal", "texto_suave").pack(fill="x", padx=ESP["sm"],
                                                         pady=(ESP["sm"], 0))
        if est.por_livro:
            C.rotulo(aba, "Por livro", 11, "bold").pack(fill="x", padx=ESP["sm"],
                                                        pady=(ESP["md"], 2))
            maior = max(1, est.por_livro[0].segundos)
            for item in est.por_livro[:8]:
                linha = C.quadro(aba, "transparent")
                linha.pack(fill="x", padx=ESP["sm"], pady=1)
                C.rotulo(linha, (item.titulo or "Livro")[:42], 10).pack(side="left")
                C.rotulo(linha, f"{_formatar_tempo(item.segundos)} · {item.paginas} pag",
                         10, "normal", "texto_suave").pack(side="right")
                barra = C.BarraProgresso(aba, altura=4)
                barra.pack(fill="x", padx=ESP["sm"], pady=(0, 4))
                barra.set(item.segundos / maior)
        elif not est.total_segundos:
            C.rotulo(aba, "As estatisticas comecam a contar a partir de agora.",
                     10, "normal", "texto_suave").pack(fill="x", padx=ESP["sm"],
                                                         pady=ESP["md"])

    @staticmethod
    def _desenhar_barras(canvas, dias):
        canvas.delete("all")
        largura = max(1, canvas.winfo_width())
        altura = max(1, canvas.winfo_height())
        if not dias:
            return
        maximo = max(60, max(d.segundos for d in dias))
        passo = largura / len(dias)
        cor = T.destaque
        apagada = T.borda
        for i, dia in enumerate(dias):
            h = (dia.segundos / maximo) * (altura - 18)
            x0 = i * passo + passo * 0.18
            x1 = (i + 1) * passo - passo * 0.18
            canvas.create_rectangle(x0, altura - 14 - max(2, h), x1, altura - 14,
                                    fill=cor if dia.segundos else apagada, width=0)
        canvas.create_text(2, altura - 2, anchor="sw", text=f"{dias[0].data[8:10]}/{dias[0].data[5:7]}",
                           fill=apagada, font=(FONTE_UI, 8))
        canvas.create_text(largura - 2, altura - 2, anchor="se", text="hoje",
                           fill=apagada, font=(FONTE_UI, 8))
