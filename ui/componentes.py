# ui/componentes.py
"""Widgets reutilizaveis e sistema de repintura por tema.

Cada widget criado aqui se registra com o mapa "propriedade -> token de cor".
Quando o tema muda, `repintar_tudo()` percorre o registro e reconfigura tudo --
por isso trocar de tema repinta o aplicativo inteiro sem reconstruir telas.
"""
import tkinter as tk

import customtkinter as ctk

import ui.icones as icones
from configuracoes import ESP, FONTE_UI, RAIO, T

# [(widget, {propriedade: token}), ...]
_REGISTRO = []

# Cores do simbolo do Lumina (ciano -> violeta -> laranja). A barra de
# progresso as usa: e a assinatura visual da marca dentro da leitura.
GRADIENTE_MARCA = ("#3FD3F2", "#8B6CF6", "#F6A86B")


def _hex_rgb(cor):
    cor = cor.lstrip("#")
    return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))


def _cor_no_gradiente(fracao, paradas=GRADIENTE_MARCA):
    paradas = [_hex_rgb(c) for c in paradas]
    fracao = max(0.0, min(1.0, fracao))
    trecho = fracao * (len(paradas) - 1)
    i = min(int(trecho), len(paradas) - 2)
    t = trecho - i
    a, b = paradas[i], paradas[i + 1]
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


class BarraProgresso(tk.Canvas):
    """Barra de progresso com o gradiente da marca e pontas arredondadas.

    Mesma interface usada do CTkProgressBar (`set`, `bind`), entao entra no
    lugar dele sem mudar quem a usa. Desenhada com PIL em 3x e reduzida: o
    Tk nao suaviza bordas, e uma barra de 5 px ficava serrilhada.
    """

    def __init__(self, pai, altura=5, fundo="superficie", trilho="superficie_alt"):
        # width=1: o Canvas pede ~380 px por padrao e alargava o cartao da
        # estante; quem decide a largura e o fill/sticky de quem o posiciona.
        super().__init__(pai, height=altura, width=1, highlightthickness=0, bd=0,
                         background=_resolver(fundo))
        self._altura = altura
        self._fundo = fundo
        self._trilho = trilho
        self._fracao = 0.0
        self._foto = None
        self._largura_desenhada = None
        self.bind("<Configure>", lambda e: self._desenhar(), add="+")
        registrar_estilo(self, background=fundo)
        _BARRAS.append(self)

    def set(self, fracao):
        fracao = max(0.0, min(1.0, float(fracao or 0)))
        if abs(fracao - self._fracao) < 1e-4 and self._foto is not None:
            return
        self._fracao = fracao
        self._desenhar()

    def get(self):
        return self._fracao

    def _desenhar(self):
        try:
            from PIL import Image, ImageDraw, ImageTk
            largura = max(4, self.winfo_width())
            altura = self._altura
            f = 3
            w, h = largura * f, altura * f
            imagem = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            desenho = ImageDraw.Draw(imagem)
            desenho.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2,
                                      fill=_hex_rgb(_resolver(self._trilho)) + (255,))
            cheio = int(w * self._fracao)
            if cheio > 0:
                faixa = Image.new("RGBA", (w, h))
                linha = ImageDraw.Draw(faixa)
                for x in range(0, w, 2):
                    linha.line([(x, 0), (x, h)], fill=_cor_no_gradiente(x / max(1, w - 1)) + (255,),
                               width=2)
                mascara = Image.new("L", (w, h), 0)
                ImageDraw.Draw(mascara).rounded_rectangle(
                    [0, 0, max(h, cheio) - 1, h - 1], radius=h // 2, fill=255)
                imagem.paste(faixa, (0, 0), mascara)
            imagem = imagem.resize((largura, altura), Image.Resampling.LANCZOS)
            self._foto = ImageTk.PhotoImage(imagem)
            self.delete("all")
            self.create_image(0, 0, image=self._foto, anchor="nw")
        except Exception:
            pass

    def repintar(self):
        try:
            self.configure(background=_resolver(self._fundo))
        except Exception:
            pass
        self._desenhar()


_BARRAS = []


def registrar_estilo(widget, **mapa):
    """Associa propriedades do widget a tokens do tema."""
    _REGISTRO.append((widget, mapa))
    return widget


def _resolver(token):
    # Token calculado na hora (ex.: cor que depende de o botao estar ligado).
    if callable(token):
        return token()
    if isinstance(token, str) and token.startswith("#"):
        return token
    if isinstance(token, tuple):
        return token
    return getattr(T, token)


def repintar_tudo():
    vivos = []
    for widget, mapa in _REGISTRO:
        try:
            if not widget.winfo_exists():
                continue
        except Exception:
            continue
        vivos.append((widget, mapa))
        try:
            widget.configure(**{prop: _resolver(token) for prop, token in mapa.items()})
        except Exception:
            pass
    _REGISTRO[:] = vivos
    vivas = []
    for barra in _BARRAS:
        if _existe(barra):
            barra.repintar()
            vivas.append(barra)
    _BARRAS[:] = vivas


def limpar_registro_mortos():
    _REGISTRO[:] = [(w, m) for w, m in _REGISTRO if _existe(w)]


def _existe(widget):
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


# --------------------------------------------------------------------- TOOLTIP

class Tooltip:
    """Dica flutuante. Essencial agora que a barra vira so icones em tela estreita."""

    _ativo = None

    def __init__(self, widget, texto, atraso=420):
        self.widget = widget
        self.texto = texto
        self.atraso = atraso
        self._janela = None
        self._agendado = None
        widget.bind("<Enter>", self._agendar)
        widget.bind("<Leave>", self._cancelar)
        widget.bind("<ButtonPress>", self._cancelar)

    def definir_texto(self, texto):
        self.texto = texto

    def _agendar(self, _=None):
        self._cancelar()
        self._agendado = self.widget.after(self.atraso, self._mostrar)

    def _cancelar(self, _=None):
        if self._agendado:
            try:
                self.widget.after_cancel(self._agendado)
            except Exception:
                pass
            self._agendado = None
        self._esconder()

    def _mostrar(self):
        if self._janela or not self.texto:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except Exception:
            return

        if Tooltip._ativo is not None:
            Tooltip._ativo._esconder()
        Tooltip._ativo = self

        self._janela = tk.Toplevel(self.widget)
        self._janela.wm_overrideredirect(True)
        self._janela.attributes("-topmost", True)
        try:
            self._janela.attributes("-alpha", 0.97)
        except Exception:
            pass
        quadro = tk.Frame(self._janela, background=T.borda)
        quadro.pack()
        rotulo = tk.Label(quadro, text=self.texto, background=T.superficie_alt,
                          foreground=T.texto, font=(FONTE_UI, 9), padx=9, pady=5,
                          justify="left")
        rotulo.pack(padx=1, pady=1)
        self._janela.update_idletasks()
        largura = self._janela.winfo_width()
        altura = self._janela.winfo_height()
        # Prende a dica dentro da tela: junto da borda direita (as setas
        # flutuantes ficam la) ela aparecia cortada pela metade.
        limite_x = self._janela.winfo_screenwidth() - largura - 6
        limite_y = self._janela.winfo_screenheight() - altura - 6
        pos_x = max(6, min(x - largura // 2, limite_x))
        pos_y = min(y, limite_y)
        self._janela.wm_geometry(f"+{pos_x}+{pos_y}")

    def _esconder(self):
        if self._janela:
            try:
                self._janela.destroy()
            except Exception:
                pass
            self._janela = None
        if Tooltip._ativo is self:
            Tooltip._ativo = None


# --------------------------------------------------------------------- BOTOES

def botao(pai, texto, comando, largura=130, altura=34, variante="primario",
          icone=None, fonte=None, ancora="center"):
    """variante: primario | secundario | fantasma | perigo"""
    estilos = {
        "primario":   ("destaque", "destaque_hover", "texto_destaque"),
        "secundario": ("superficie_alt", "borda", "texto"),
        "fantasma":   ("transparent", "superficie_alt", "texto"),
        "perigo":     ("perigo", "destaque_hover", "#FFFFFF"),
    }
    fundo, hover, cor_texto = estilos.get(variante, estilos["primario"])
    rotulo = f"{icone}  {texto}" if icone and texto else (icone or texto)

    widget = ctk.CTkButton(
        pai, text=rotulo, command=comando, width=largura, height=altura,
        corner_radius=RAIO["sm"], anchor=ancora,
        fg_color="transparent" if fundo == "transparent" else getattr(T, fundo),
        hover_color=getattr(T, hover),
        text_color=cor_texto if cor_texto.startswith("#") else getattr(T, cor_texto),
        font=fonte or ctk.CTkFont(FONTE_UI, 12, "bold"),
    )
    mapa = {"hover_color": hover,
            "text_color": cor_texto if cor_texto.startswith("#") else cor_texto}
    if fundo != "transparent":
        mapa["fg_color"] = fundo
    registrar_estilo(widget, **mapa)
    return widget


def botao_icone(pai, chave, comando, dica="", tamanho=36, ativo=False, corpo=15):
    """Botao quadrado so com icone -- base da barra compacta.

    `chave` e um nome semantico de `ui.icones` (ex.: "busca"); qualquer outro
    texto e desenhado como esta, na fonte da interface.
    """
    simbolo = icones.icone(chave)
    familia = icones.familia() if icones.eh_icone(chave) else FONTE_UI

    widget = ctk.CTkButton(
        pai, text=simbolo, command=comando, width=tamanho, height=tamanho,
        corner_radius=RAIO["sm"],
        fg_color=T.destaque if ativo else "transparent",
        hover_color=T.superficie_alt,
        text_color=T.texto_destaque if ativo else T.texto,
        font=ctk.CTkFont(familia, corpo),
    )
    widget._ativo = bool(ativo)
    # Cor do icone tambem acompanha o tema: antes so o hover era repintado e,
    # no tema claro, os icones continuavam claros sobre fundo branco.
    registrar_estilo(
        widget, hover_color="superficie_alt",
        text_color=lambda w=widget: T.texto_destaque if getattr(w, "_ativo", False) else T.texto,
        fg_color=lambda w=widget: T.destaque if getattr(w, "_ativo", False) else "transparent")
    if dica:
        widget._dica = Tooltip(widget, dica)
    return widget


def trocar_icone(widget, chave):
    """Troca o glifo de um botao de icone mantendo a fonte correta."""
    try:
        widget.configure(text=icones.icone(chave))
    except Exception:
        pass


def marcar_ativo(widget, ativo):
    """Alterna a aparencia de um botao-icone entre ligado e desligado."""
    try:
        widget._ativo = bool(ativo)
        widget.configure(
            fg_color=T.destaque if ativo else "transparent",
            text_color=T.texto_destaque if ativo else T.texto,
        )
    except Exception:
        pass


# --------------------------------------------------------------------- ROTULOS

def rotulo(pai, texto, tamanho=12, peso="normal", cor="texto", ancora="w", **kwargs):
    widget = ctk.CTkLabel(pai, text=texto, anchor=ancora,
                          font=ctk.CTkFont(FONTE_UI, tamanho, peso),
                          text_color=getattr(T, cor), **kwargs)
    registrar_estilo(widget, text_color=cor)
    return widget


def quadro(pai, cor="superficie", raio=0, **kwargs):
    widget = ctk.CTkFrame(pai, fg_color="transparent" if cor == "transparent" else getattr(T, cor),
                          corner_radius=raio, **kwargs)
    if cor != "transparent":
        registrar_estilo(widget, fg_color=cor)
    return widget


def separador(pai, vertical=False):
    if vertical:
        widget = ctk.CTkFrame(pai, width=1, fg_color=T.borda, corner_radius=0)
        widget.pack(side="left", fill="y", padx=ESP["sm"], pady=ESP["sm"])
    else:
        widget = ctk.CTkFrame(pai, height=1, fg_color=T.borda, corner_radius=0)
        widget.pack(fill="x", pady=ESP["sm"])
    registrar_estilo(widget, fg_color="borda")
    return widget


def campo(pai, placeholder="", largura=200, altura=32, **kwargs):
    widget = ctk.CTkEntry(
        pai, placeholder_text=placeholder, width=largura, height=altura,
        corner_radius=RAIO["sm"], fg_color=T.superficie_alt, border_color=T.borda,
        text_color=T.texto, placeholder_text_color=T.texto_suave,
        font=ctk.CTkFont(FONTE_UI, 12), **kwargs)
    registrar_estilo(widget, fg_color="superficie_alt", border_color="borda",
                     text_color="texto", placeholder_text_color="texto_suave")
    return widget


def seletor(pai, valores, comando=None, largura=180, valor=None):
    widget = ctk.CTkOptionMenu(
        pai, values=list(valores), command=comando, width=largura, height=32,
        corner_radius=RAIO["sm"], fg_color=T.superficie_alt, button_color=T.superficie_alt,
        button_hover_color=T.borda, text_color=T.texto, dropdown_fg_color=T.superficie,
        dropdown_text_color=T.texto, dropdown_hover_color=T.superficie_alt,
        font=ctk.CTkFont(FONTE_UI, 12), dropdown_font=ctk.CTkFont(FONTE_UI, 12))
    if valor:
        widget.set(valor)
    registrar_estilo(widget, fg_color="superficie_alt", button_color="superficie_alt",
                     button_hover_color="borda", text_color="texto",
                     dropdown_fg_color="superficie", dropdown_text_color="texto",
                     dropdown_hover_color="superficie_alt")
    return widget


def rolavel(pai, cor="superficie", **kwargs):
    widget = ctk.CTkScrollableFrame(
        pai, fg_color=getattr(T, cor), corner_radius=0,
        scrollbar_button_color=T.borda, scrollbar_button_hover_color=T.texto_suave,
        **kwargs)
    registrar_estilo(widget, fg_color=cor, scrollbar_button_color="borda",
                     scrollbar_button_hover_color="texto_suave")
    return widget


# ----------------------------------------------------------------------- TOAST

class Toast:
    """Confirmacao discreta no rodape. Substitui o silencio total da v1:
    grifar, salvar nota ou copiar nao davam nenhum retorno visual."""

    _atual = None

    @staticmethod
    def mostrar(mestre, mensagem, tipo="info", duracao=2200):
        try:
            raiz = mestre.winfo_toplevel()
        except Exception:
            return
        Toast.fechar()

        cor = {"info": T.superficie_alt, "ok": T.sucesso, "erro": T.perigo}.get(tipo, T.superficie_alt)
        cor_texto = T.texto if tipo == "info" else "#FFFFFF"

        caixa = ctk.CTkFrame(raiz, fg_color=cor, corner_radius=RAIO["pill"],
                             border_width=1, border_color=T.borda)
        ctk.CTkLabel(caixa, text=mensagem, text_color=cor_texto,
                     font=ctk.CTkFont(FONTE_UI, 12, "bold")).pack(padx=ESP["lg"], pady=ESP["sm"])
        caixa.place(relx=0.5, rely=0.94, anchor="s")
        Toast._atual = caixa
        raiz.after(duracao, lambda: Toast.fechar(caixa))

    @staticmethod
    def fechar(alvo=None):
        caixa = alvo or Toast._atual
        if caixa is None:
            return
        if alvo is not None and Toast._atual is not alvo:
            return
        try:
            caixa.destroy()
        except Exception:
            pass
        if alvo is None or Toast._atual is alvo:
            Toast._atual = None


# ------------------------------------------------------------------ TOPLEVEL

def preparar_modal(modal, titulo, largura, altura, pai=None):
    """Centraliza sobre a janela principal, aplica tema e garante que o modal
    fique VISIVEL na frente.

    Historico: a versao anterior chamava `grab_set()` 120 ms apos criar a
    janela. Nesse instante o CustomTkinter ainda estava fazendo seu proprio
    withdraw/deiconify para pintar a barra de titulo escura, entao o modal
    reaparecia ATRAS da janela principal -- com o grab ativo, todo clique era
    engolido e o aplicativo parecia travado. Agora usamos `transient()`, que
    prende a janela acima da dona pelo gerenciador de janelas, e nao capturamos
    mais o mouse: se algo der errado, o pior caso e um modal atras, nunca um
    aplicativo congelado.
    """
    modal.title(titulo)
    modal.configure(fg_color=T.fundo)
    modal.resizable(True, True)
    modal.minsize(320, 260)

    raiz = None
    if pai is not None:
        try:
            raiz = pai.winfo_toplevel()
            pai_x, pai_y = raiz.winfo_rootx(), raiz.winfo_rooty()
            pai_w, pai_h = raiz.winfo_width(), raiz.winfo_height()
            x = pai_x + (pai_w - largura) // 2
            y = pai_y + (pai_h - altura) // 3
            modal.geometry(f"{largura}x{altura}+{max(0, x)}+{max(0, y)}")
        except Exception:
            modal.geometry(f"{largura}x{altura}")
    else:
        modal.geometry(f"{largura}x{altura}")

    if raiz is not None:
        try:
            modal.transient(raiz)
        except Exception:
            pass

    modal.bind("<Escape>", lambda e: fechar_modal(modal))
    modal.protocol("WM_DELETE_WINDOW", lambda: fechar_modal(modal))
    modal.after(250, lambda: _apresentar_modal(modal, raiz))
    return modal


def _apresentar_modal(modal, raiz, tentativa=0):
    """Traz o modal para a frente, esperando ele existir de fato na tela."""
    try:
        if not modal.winfo_exists():
            return
        # Enquanto o CustomTkinter refaz a janela, ela nao e "viewable" e um
        # lift() aqui nao teria efeito nenhum.
        if not modal.winfo_viewable() and tentativa < 10:
            modal.after(90, lambda: _apresentar_modal(modal, raiz, tentativa + 1))
            return

        modal.lift()
        # Se a janela principal esta fixada acima de todas, o modal precisa
        # acompanhar -- caso contrario nasce escondido atras dela.
        if raiz is not None and bool(raiz.attributes("-topmost")):
            modal.attributes("-topmost", True)
        modal.focus_force()
    except Exception:
        pass


def fechar_modal(modal):
    try:
        modal.grab_release()
    except Exception:
        pass
    try:
        modal.destroy()
    except Exception:
        pass


# ----------------------------------------------------------------- SENHA

def pedir_senha(pai, nome_arquivo, validar):
    """Pergunta a senha de um PDF protegido e ESPERA a resposta.

    `validar(senha) -> bool` e chamado a cada tentativa; senha errada mantem a
    janela aberta com o aviso, para tentar de novo. Devolve a senha aceita ou
    None se o usuario desistir (Cancelar, Esc ou fechar a janela).

    E sincrono (wait_window) de proposito: quem chama esta no meio de um laco
    adicionando varios arquivos, e cada um precisa da resposta antes de seguir.
    """
    modal = ctk.CTkToplevel(pai)
    preparar_modal(modal, "Arquivo protegido", 440, 250, pai.winfo_toplevel())
    resultado = {"senha": None}

    corpo = quadro(modal, "transparent")
    corpo.pack(fill="both", expand=True, padx=ESP["xl"], pady=ESP["xl"])
    rotulo(corpo, "Arquivo protegido por senha", 16, "bold").pack(fill="x")
    nome = nome_arquivo if len(nome_arquivo) <= 52 else nome_arquivo[:51] + "…"
    rotulo(corpo, f"Digite a senha de “{nome}”.\nEla fica guardada cifrada nesta conta "
                  "do Windows.", 11, "normal", "texto_suave", justify="left").pack(
        fill="x", pady=(2, ESP["md"]))

    entrada = campo(corpo, "Senha", largura=380, altura=36, show="•")
    entrada.pack(fill="x")
    modal.after(320, lambda: entrada.focus_set() if _existe(entrada) else None)

    aviso = rotulo(corpo, "", 10, "normal", "perigo")
    aviso.pack(fill="x", pady=(ESP["xs"], 0))

    def confirmar(_=None):
        senha = entrada.get()
        if not senha:
            aviso.configure(text="Digite a senha.")
            return
        if not validar(senha):
            aviso.configure(text="Senha incorreta. Tente de novo.")
            entrada.delete(0, "end")
            entrada.focus_set()
            return
        resultado["senha"] = senha
        fechar_modal(modal)

    acoes = quadro(corpo, "transparent")
    acoes.pack(side="bottom", fill="x")
    botao(acoes, "Abrir", confirmar, largura=130).pack(side="right")
    botao(acoes, "Cancelar", lambda: fechar_modal(modal), largura=110,
          variante="secundario").pack(side="right", padx=ESP["sm"])
    modal.bind("<Return>", confirmar)
    modal.wait_window()
    return resultado["senha"]
