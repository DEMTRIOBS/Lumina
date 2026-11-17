# configuracoes.py
"""Design tokens, temas e caminhos do aplicativo.

Toda a interface le as cores daqui atraves do objeto `Tema`, entao trocar de
tema repinta o aplicativo inteiro -- nao apenas a area de texto.
"""
import os

from core.paths import migrar_dados_locais, user_path

APP_NOME = "Lumina"
APP_VERSAO = "2.3.4"

# ---------------------------------------------------------------- ARMAZENAMENTO
migrar_dados_locais(
    nomes_arquivos=["meu_leitor.db"],
    nomes_pastas=["capas_cache"],
)

ARQUIVO_BANCO = user_path("meu_leitor.db")
PASTA_CAPAS = user_path("capas_cache")

# Arquivos legados (usados apenas pela migracao automatica)
ARQUIVO_BIBLIOTECA = "biblioteca.json"
ARQUIVO_CONFIG = "config.json"

# ---------------------------------------------------------------------- ESPACO
# Escala de espacamento de 4px: usar sempre estes valores mantem o ritmo visual.
ESP = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

RAIO = {"sm": 6, "md": 10, "lg": 14, "pill": 999}

# ---------------------------------------------------------------------- TEMAS
TEMAS = {
    # Identidade da arte do Lumina: azul-marinho profundo, violeta do simbolo
    # como destaque e o branco quente do logotipo nos titulos.
    "Escuro": {
        "modo": "dark",
        "fundo": "#0A0F1C",
        "superficie": "#111827",
        "superficie_alt": "#1A2338",
        "borda": "#273149",
        "texto": "#ECEFF6",
        "texto_suave": "#8F9BB8",
        "destaque": "#8B7CF6",
        "destaque_hover": "#7766EE",
        "texto_destaque": "#FFFFFF",
        "papel": "#0D1322",
        "papel_texto": "#D9DFEB",
        "papel_titulo": "#F6F1E7",
        "grifo": "#5B3E68",
        "grifo_texto": "#FCEFFF",
        "busca": "#0E6E8C",
        "busca_texto": "#E9FBFF",
        "perigo": "#EF4444",
        "sucesso": "#22C55E",
        "cod_comentario": "#6B7C93",
        "cod_texto": "#A3D9A5",
        "cod_numero": "#E8A87C",
        "cod_palavra": "#7AA2F7",
        "cod_funcao": "#C4A7E7",
    },
    "Claro": {
        "modo": "light",
        "fundo": "#EEF1F6",
        "superficie": "#FFFFFF",
        "superficie_alt": "#E4E9F2",
        "borda": "#D3DAE6",
        "texto": "#141C2B",
        "texto_suave": "#5A6880",
        "destaque": "#6C5CE7",
        "destaque_hover": "#5A4BD6",
        "texto_destaque": "#FFFFFF",
        "papel": "#FFFFFF",
        "papel_texto": "#1B2432",
        "papel_titulo": "#0A101C",
        "grifo": "#FDE68A",
        "grifo_texto": "#3B2F0B",
        "busca": "#A5F3FC",
        "busca_texto": "#0C3B44",
        "perigo": "#DC2626",
        "sucesso": "#16A34A",
        "cod_comentario": "#7A8290",
        "cod_texto": "#0A7D3F",
        "cod_numero": "#B5651D",
        "cod_palavra": "#1D4ED8",
        "cod_funcao": "#7C3AED",
    },
    "Sepia": {
        "modo": "light",
        "fundo": "#E8DCC4",
        "superficie": "#F4EBD8",
        "superficie_alt": "#DED2B8",
        "borda": "#CDBE9E",
        "texto": "#42382A",
        "texto_suave": "#7A6C56",
        "destaque": "#9A6B3F",
        "destaque_hover": "#805634",
        "texto_destaque": "#FFF8EC",
        "papel": "#FBF3E2",
        "papel_texto": "#453A2B",
        "papel_titulo": "#2E251A",
        "grifo": "#EBD08A",
        "grifo_texto": "#3B2F14",
        "busca": "#B5D8C4",
        "busca_texto": "#1F3A2C",
        "perigo": "#B4432E",
        "sucesso": "#4F7A3C",
        "cod_comentario": "#9A8C72",
        "cod_texto": "#4F7A3C",
        "cod_numero": "#A9601F",
        "cod_palavra": "#2F6F8F",
        "cod_funcao": "#7A5296",
    },
    "Noturno": {
        # Preto real: reduz brilho ao maximo para leitura noturna / telas OLED.
        "modo": "dark",
        "fundo": "#000000",
        "superficie": "#0A0A0A",
        "superficie_alt": "#161616",
        "borda": "#232323",
        "texto": "#C9C9C9",
        "texto_suave": "#6E6E6E",
        "destaque": "#B45309",
        "destaque_hover": "#92400E",
        "texto_destaque": "#FFF5E6",
        "papel": "#000000",
        "papel_texto": "#B4B4B4",
        "papel_titulo": "#DEDEDE",
        "grifo": "#4A3A12",
        "grifo_texto": "#E8D8B0",
        "busca": "#0B4F5E",
        "busca_texto": "#CFF6FB",
        "perigo": "#B91C1C",
        "sucesso": "#15803D",
        "cod_comentario": "#5A5A5A",
        "cod_texto": "#7FA07F",
        "cod_numero": "#A8804F",
        "cod_palavra": "#6E8FB8",
        "cod_funcao": "#9A7FB0",
    },
    "Alto Contraste": {
        "modo": "dark",
        "fundo": "#000000",
        "superficie": "#0D0D0D",
        "superficie_alt": "#1A1A1A",
        "borda": "#FFFFFF",
        "texto": "#FFFFFF",
        "texto_suave": "#D0D0D0",
        "destaque": "#FFD400",
        "destaque_hover": "#E0BA00",
        "texto_destaque": "#000000",
        "papel": "#000000",
        "papel_texto": "#FFFFFF",
        "papel_titulo": "#FFD400",
        "grifo": "#FFD400",
        "grifo_texto": "#000000",
        "busca": "#00E5FF",
        "busca_texto": "#000000",
        "perigo": "#FF4D4D",
        "sucesso": "#4DFF88",
        "cod_comentario": "#B0B0B0",
        "cod_texto": "#4DFF88",
        "cod_numero": "#FFB84D",
        "cod_palavra": "#00E5FF",
        "cod_funcao": "#FF7AF5",
    },
}

TEMA_PADRAO = "Escuro"
TEMA_PERSONALIZADO = "Personalizado"

# ============================================================ DUAS CAMADAS
# Separar COMO exibir de SOBRE O QUE E evita criar um perfil de renderizacao
# para cada assunto. Um livro de engenharia e um de medicina se leem igual
# (titulos, listas, tabelas); o que muda entre eles e so a organizacao da
# estante. Por isso: renderizacao muda o motor, categoria nao.

# ---- CAMADA 1: tipo de renderizacao (muda o motor de exibicao)
RENDERIZACOES = {
    "prosa": {
        "nome": "Prosa",
        "descricao": "Romance, contos, ficcao. Texto corrido e leitura longa.",
        "recuo_paragrafo": True,
        "altura_linha": 1.75,
        "largura_coluna": 66,
        "fonte": "Georgia (serifada)",
        "realce_codigo": False,
        "mono_inline": False,
        "escala_titulo": 1.0,
        "pagina_como_imagem": False,
        "destacar_avisos": False,
    },
    "tecnico": {
        "nome": "Tecnico",
        "descricao": "Engenharia, medicina, direito, administracao. Muitos titulos e listas.",
        "recuo_paragrafo": False,
        "altura_linha": 1.6,
        "largura_coluna": 74,
        "fonte": "Segoe UI (sem serifa)",
        "realce_codigo": False,
        "mono_inline": False,
        # Hierarquia de titulo mais marcada: e o que orienta a leitura aqui.
        "escala_titulo": 1.15,
        "pagina_como_imagem": False,
        "destacar_avisos": True,
    },
    "programacao": {
        "nome": "Programacao",
        "descricao": "Python, Java, JavaScript. Blocos de codigo e terminal.",
        "recuo_paragrafo": False,
        "altura_linha": 1.55,
        "largura_coluna": 78,
        "fonte": "Segoe UI (sem serifa)",
        "realce_codigo": True,
        "mono_inline": True,
        "escala_titulo": 1.1,
        "pagina_como_imagem": False,
        "destacar_avisos": True,
    },
    "matematica": {
        "nome": "Matematica",
        "descricao": "Formulas, equacoes e simbolos. Preserva alinhamento.",
        "recuo_paragrafo": False,
        "altura_linha": 1.85,          # formulas precisam de ar entre linhas
        "largura_coluna": 72,
        "fonte": "Cambria (serifada)",
        "realce_codigo": False,        # Courier aqui e formula, nao codigo
        "mono_inline": False,
        "escala_titulo": 1.05,
        "pagina_como_imagem": False,
        "destacar_avisos": False,
        "formulas_maiores": True,
    },
    "hq": {
        "nome": "HQ / Mangá / Digitalizado",
        "descricao": "Quase sem texto (quadrinho ou pagina escaneada). A pagina e a imagem.",
        "recuo_paragrafo": False,
        "altura_linha": 1.4,
        "largura_coluna": 120,
        "fonte": "Segoe UI (sem serifa)",
        "realce_codigo": False,
        "mono_inline": False,
        "escala_titulo": 1.0,
        "pagina_como_imagem": True,
        "destacar_avisos": False,
    },
    "revista": {
        "nome": "Revista",
        "descricao": "Diagramacao complexa em colunas. Mantem o layout original.",
        "recuo_paragrafo": False,
        "altura_linha": 1.5,
        "largura_coluna": 120,
        "fonte": "Segoe UI (sem serifa)",
        "realce_codigo": False,
        "mono_inline": False,
        "escala_titulo": 1.0,
        "pagina_como_imagem": True,
        "destacar_avisos": False,
    },
    "manual": {
        "nome": "Manual",
        "descricao": "Passo a passo, avisos e procedimentos.",
        "recuo_paragrafo": False,
        "altura_linha": 1.65,
        "largura_coluna": 72,
        "fonte": "Segoe UI (sem serifa)",
        "realce_codigo": False,
        "mono_inline": True,
        "escala_titulo": 1.12,
        "pagina_como_imagem": False,
        "destacar_avisos": True,
    },
}
RENDER_PADRAO = "prosa"


def preset_render(nome):
    return RENDERIZACOES.get(nome) or RENDERIZACOES[RENDER_PADRAO]


# ---- CAMADA 2: categoria (so organiza a estante; nao muda a leitura)
CATEGORIAS = [
    "", "Computacao", "Engenharia", "Matematica", "Medicina", "Direito",
    "Historia", "Geografia", "Economia", "Filosofia", "Psicologia",
    "Literatura", "Infantil", "Idiomas", "Negocios", "Ciencias", "Religiao",
    "Academico", "Outros",
]

# Palpite de categoria pelo titulo. E so um chute inicial: a categoria nao
# altera a leitura, entao errar aqui nao estraga nada, e o usuario ajusta.
_PISTAS_CATEGORIA = {
    "Computacao": ("python", "java", "javascript", "django", "programa",
                   "codigo", "software", "algoritmo", "dados", "web", "sql",
                   "linux", "redes neurais", "machine learning"),
    "Engenharia": ("engenharia", "mecanica", "eletrica", "civil", "estrutur"),
    "Matematica": ("matematica", "calculo", "algebra", "geometria", "estatistica"),
    "Medicina": ("medicina", "anatomia", "farmac", "clinic", "enfermagem"),
    "Direito": ("direito", "juridic", "constituc", "penal", "civil comentado"),
    "Historia": ("historia", "guerra", "imperio", "civilizac"),
    "Geografia": ("geografia", "atlas", "mapa", "geopolit"),
    "Economia": ("economia", "financ", "investi", "contabil"),
    "Filosofia": ("filosofia", "etica", "metafisic"),
    "Psicologia": ("psicolog", "comportament", "mente"),
    "Literatura": ("romance", "contos", "poesia", "cronicas", "novela"),
    "Infantil": ("infantil", "crianca"),
    "Idiomas": ("ingles", "espanhol", "frances", "gramatica", "idioma"),
    "Negocios": ("negocio", "gestao", "marketing", "lideranca", "empreend"),
    "Religiao": ("biblia", "religi", "teolog", "espiritual"),
}


def adivinhar_categoria(titulo, autor=""):
    """Palpite pelo titulo, so para pre-preencher o campo na estante."""
    alvo = f"{titulo or ''} {autor or ''}".lower()
    for categoria, pistas in _PISTAS_CATEGORIA.items():
        if any(pista in alvo for pista in pistas):
            return categoria
    return ""

# Paletas de editores conhecidos: quem le codigo o dia inteiro quer o livro na
# mesma cor da IDE. (nome -> papel, texto, destaque)
PALETAS_PRONTAS = {
    "VS Code Dark+":   ("#1E1E1E", "#D4D4D4", "#569CD6"),
    "VS Code Light":   ("#FFFFFF", "#3B3B3B", "#0066B8"),
    "One Dark":        ("#282C34", "#ABB2BF", "#61AFEF"),
    "Dracula":         ("#282A36", "#F8F8F2", "#BD93F9"),
    "Monokai":         ("#272822", "#F8F8F2", "#A6E22E"),
    "Nord":            ("#2E3440", "#D8DEE9", "#88C0D0"),
    "Solarized Dark":  ("#002B36", "#93A1A1", "#B58900"),
    "Solarized Light": ("#FDF6E3", "#586E75", "#B58900"),
    "GitHub Dark":     ("#0D1117", "#C9D1D9", "#58A6FF"),
    "Gruvbox":         ("#282828", "#EBDBB2", "#FE8019"),
    "Papel":           ("#F7F3EA", "#2E2A24", "#8A5A2B"),
}


# ------------------------------------------------------- MISTURA DE CORES
def _para_rgb(cor):
    cor = (cor or "#000000").lstrip("#")
    if len(cor) == 3:
        cor = "".join(c * 2 for c in cor)
    return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))


def _para_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def misturar(cor_a, cor_b, peso):
    """Interpola duas cores. peso=0 devolve cor_a, peso=1 devolve cor_b."""
    a, b = _para_rgb(cor_a), _para_rgb(cor_b)
    return _para_hex(a[i] + (b[i] - a[i]) * peso for i in range(3))


def luminancia(cor):
    """Brilho percebido, de 0 (preto) a 1 (branco)."""
    r, g, b = (v / 255 for v in _para_rgb(cor))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def eh_escura(cor):
    return luminancia(cor) < 0.45


def montar_tema(papel, texto, destaque):
    """Deriva a paleta inteira do aplicativo a partir de tres cores.

    Assim o usuario escolhe so a cor da pagina e o resto (barras, bordas,
    grifo, busca) sai coerente, claro ou escuro conforme o caso.
    """
    escuro = eh_escura(papel)
    contraste = "#FFFFFF" if escuro else "#000000"
    oposto = "#000000" if escuro else "#FFFFFF"

    # O fundo das barras precisa se distinguir do papel. Escurecer sempre nao
    # serve: num papel quase preto nao sobra para onde escurecer, entao ali a
    # separacao vem de clarear um pouco.
    if luminancia(papel) < 0.12:
        fundo = misturar(papel, "#FFFFFF", 0.07)
    else:
        fundo = misturar(papel, "#000000", 0.20 if escuro else 0.07)

    return {
        "modo": "dark" if escuro else "light",
        "fundo": fundo,
        "superficie": misturar(papel, contraste, 0.06),
        "superficie_alt": misturar(papel, contraste, 0.13),
        "borda": misturar(papel, contraste, 0.22),
        "texto": misturar(texto, contraste, 0.12),
        "texto_suave": misturar(texto, papel, 0.42),
        "destaque": destaque,
        "destaque_hover": misturar(destaque, oposto, 0.22),
        "texto_destaque": "#FFFFFF" if eh_escura(destaque) else "#101010",
        "papel": papel,
        "papel_texto": texto,
        "papel_titulo": misturar(texto, contraste, 0.30),
        "grifo": misturar(papel, destaque, 0.42 if escuro else 0.55),
        "grifo_texto": misturar(texto, contraste, 0.25),
        "busca": misturar(papel, "#00B4D8", 0.55),
        "busca_texto": "#FFFFFF" if escuro else "#062B33",
        "perigo": "#EF4444" if escuro else "#DC2626",
        "sucesso": "#22C55E" if escuro else "#16A34A",
        # Realce de codigo derivado do papel: matizes fixos, luminosidade
        # ajustada para manter contraste tanto no claro quanto no escuro.
        "cod_comentario": misturar(texto, papel, 0.45),
        "cod_texto": misturar("#3FA45B" if not escuro else "#98D8A0", papel, 0.12),
        "cod_numero": misturar("#B5651D" if not escuro else "#E8A87C", papel, 0.12),
        "cod_palavra": misturar(destaque, contraste, 0.10),
        "cod_funcao": misturar("#7C3AED" if not escuro else "#C4A7E7", papel, 0.12),
    }


class _Tema:
    """Tema ativo. Acesso por atributo (`T.fundo`) para deixar a UI legivel."""

    def __init__(self):
        self._nome = TEMA_PADRAO
        self._cores = dict(TEMAS[TEMA_PADRAO])
        self._observadores = []

    @property
    def nome(self):
        return self._nome

    def definir(self, nome, cores_personalizadas=None):
        if nome == TEMA_PERSONALIZADO and cores_personalizadas:
            self._nome = nome
            self._cores = dict(cores_personalizadas)
        else:
            if nome not in TEMAS:
                nome = TEMA_PADRAO
            self._nome = nome
            self._cores = dict(TEMAS[nome])
        for callback in list(self._observadores):
            try:
                callback()
            except Exception:
                pass

    def ao_mudar(self, callback):
        self._observadores.append(callback)

    def __getattr__(self, item):
        try:
            return self.__dict__["_cores"][item]
        except KeyError:
            raise AttributeError(f"Cor '{item}' nao existe no tema") from None


T = _Tema()

# ------------------------------------------------------------------- TIPOGRAFIA
# Fontes de leitura: nome exibido -> familia real instalada no Windows.
FONTES_LEITURA = {
    "Georgia (serifada)": "Georgia",
    "Cambria (serifada)": "Cambria",
    "Segoe UI (sem serifa)": "Segoe UI",
    "Verdana (legivel)": "Verdana",
    "Tahoma (compacta)": "Tahoma",
    "Consolas (mono)": "Consolas",
}
FONTE_LEITURA_PADRAO = "Georgia (serifada)"

FONTE_UI = "Segoe UI"

# Largura maxima da coluna de texto, em caracteres.
# Tipografia classica aponta 55-75 caracteres como faixa ideal de leitura.
LARGURA_COLUNA_PADRAO = 68
LARGURA_COLUNA_MIN = 40
LARGURA_COLUNA_MAX = 120

TAMANHO_FONTE_PADRAO = 17
ALTURA_LINHA_PADRAO = 1.6

# ------------------------------------------------------- VIRADA DE PAGINA
# Como o leitor avanca de pagina. Cada modo troca o gesto, nunca o conteudo.
MODO_LIVRE = "Rolagem continua"
MODO_FOLHA = "Folhear (cabe na tela)"
MODO_BOTOES = "Botoes flutuantes"
MODO_MARGEM = "Clique na margem"
MODO_AUTO = "Rolagem automatica"

MODOS_VIRADA = {
    MODO_LIVRE: "Texto corrido: a roda rola e emenda uma pagina na outra.",
    MODO_FOLHA: "Sem rolagem: o texto cabe na tela, como um livro. Roda, "
                "setas, espaco ou clique na margem viram a folha.",
    MODO_BOTOES: "Setas surgem nas laterais ao chegar ao fim. So elas viram.",
    MODO_MARGEM: "Clicar na margem esquerda ou direita vira a pagina.",
    MODO_AUTO: "A pagina rola sozinha e avanca ao chegar ao fim.",
}
MODO_VIRADA_PADRAO = MODO_LIVRE

# Nomes antigos gravados na configuracao de quem ja usava o app.
_MODOS_ANTIGOS = {"Livre": MODO_LIVRE}


def normalizar_modo_virada(valor):
    valor = _MODOS_ANTIGOS.get(valor, valor)
    return valor if valor in MODOS_VIRADA else MODO_VIRADA_PADRAO

# Velocidade da rolagem automatica: 1 (bem lenta) a 10 (rapida).
VELOCIDADE_ROLAGEM_PADRAO = 4
INTERVALO_ROLAGEM_MS = 40          # ~25 quadros por segundo
PAUSA_FIM_PAGINA_MS = 1400         # respiro antes de virar sozinho

# ------------------------------------------------------------------ JANELA
LARGURA_MINIMA = 340
ALTURA_MINIMA = 420
# Abaixo desta largura (px fisicos) o titulo sai da barra para dar lugar as acoes.
LIMIAR_COMPACTO = 720

# Estimativa de ritmo de leitura para o tempo restante.
PALAVRAS_POR_MINUTO = 220

if not os.path.exists(PASTA_CAPAS):
    os.makedirs(PASTA_CAPAS, exist_ok=True)
