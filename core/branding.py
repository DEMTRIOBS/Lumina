# core/branding.py
"""Identidade visual: simbolo, icone e arte de abertura.

Fontes, em ordem de preferencia:
  1. arte/simbolo.png  -- so o simbolo, fundo transparente (ideal p/ icone)
  2. arte/lumina.png   -- a arte completa (simbolo + "Lumina PDF READER").
                          O simbolo e RECORTADO dela automaticamente e posto
                          num quadrado arredondado azul-marinho, como icone
                          de aplicativo moderno.
  3. minha_logo.svg / minha_logo.png -- logo da versao anterior.

Trocar a arte e so substituir o arquivo: icone e splash se regeneram sozinhos.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFilter

from core.paths import resource_path, user_path

CAMINHO_SIMBOLO = resource_path("arte", "simbolo.png")
CAMINHO_ARTE = resource_path("arte", "lumina.png")
CAMINHO_SVG = resource_path("minha_logo.svg")
CAMINHO_PNG = resource_path("minha_logo.png")
CAMINHO_ICO = user_path("app.ico")

# Azul-marinho do fundo da arte.
COR_FUNDO_MARCA = (11, 17, 32)

_cache = {}


def _origens():
    return (CAMINHO_SIMBOLO, CAMINHO_ARTE, CAMINHO_SVG, CAMINHO_PNG)


def carregar_logo(tamanho: int) -> Image.Image | None:
    """Devolve o simbolo como imagem RGBA quadrada de `tamanho` px."""
    if tamanho in _cache:
        return _cache[tamanho]

    imagem = (_do_simbolo(tamanho) or _da_arte(tamanho) or _do_svg(tamanho)
              or _do_png(tamanho))
    if imagem is not None:
        _cache[tamanho] = imagem
    return imagem


def carregar_arte(largura: int) -> Image.Image | None:
    """A arte completa (para splash e "Sobre"), na largura pedida."""
    chave = ("arte", largura)
    if chave in _cache:
        return _cache[chave]
    if not os.path.exists(CAMINHO_ARTE):
        return None
    try:
        arte = Image.open(CAMINHO_ARTE).convert("RGBA")
        altura = max(1, int(arte.height * largura / arte.width))
        arte = arte.resize((largura, altura), Image.Resampling.LANCZOS)
        _cache[chave] = arte
        return arte
    except Exception as erro:
        print(f"[Branding] Arte indisponivel: {erro}")
        return None


# ------------------------------------------------------------------ fontes
def _conter(imagem, tamanho):
    """Encaixa a imagem num quadrado transparente, sem distorcer."""
    imagem = imagem.copy()
    imagem.thumbnail((tamanho, tamanho), Image.Resampling.LANCZOS)
    quadro = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    quadro.alpha_composite(imagem, ((tamanho - imagem.width) // 2,
                                    (tamanho - imagem.height) // 2))
    return quadro


def _do_simbolo(tamanho):
    if not os.path.exists(CAMINHO_SIMBOLO):
        return None
    try:
        imagem = Image.open(CAMINHO_SIMBOLO).convert("RGBA")
        caixa = imagem.getbbox()
        if caixa:
            imagem = imagem.crop(caixa)
        return _conter(imagem, tamanho)
    except Exception:
        return None


def caixa_do_simbolo(arte: Image.Image):
    """Localiza o simbolo colorido na metade esquerda da arte.

    Procura pixels saturados e claros (o gradiente ciano/violeta/laranja); o
    texto "Lumina" e branco (pouco saturado) e fica de fora por isso.
    """
    rgb = arte.convert("RGB")
    largura, altura = rgb.size
    regiao = rgb.crop((0, 0, int(largura * 0.45), altura))
    hsv = regiao.convert("HSV")
    _h, s, v = hsv.split()
    mascara = Image.eval(s, lambda x: 255 if x > 90 else 0)
    claro = Image.eval(v, lambda x: 255 if x > 110 else 0)
    mascara = Image.composite(mascara, Image.new("L", mascara.size, 0), claro)
    # Abre a mascara para descartar as faiscas/estrelas soltas do fundo.
    mascara = mascara.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    caixa = mascara.getbbox()
    if not caixa:
        return None
    x0, y0, x1, y1 = caixa
    lado = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lado = int(lado * 1.12)
    return (int(cx - lado / 2), int(cy - lado / 2),
            int(cx + lado / 2), int(cy + lado / 2))


def _da_arte(tamanho):
    """Icone a partir da arte completa: simbolo num quadrado arredondado."""
    if not os.path.exists(CAMINHO_ARTE):
        return None
    try:
        arte = Image.open(CAMINHO_ARTE).convert("RGBA")
        caixa = caixa_do_simbolo(arte)
        if not caixa:
            return None
        simbolo = arte.crop(caixa)
        grande = 512
        simbolo = simbolo.resize((int(grande * 0.86), int(grande * 0.86)),
                                 Image.Resampling.LANCZOS)
        icone = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
        mascara = Image.new("L", (grande, grande), 0)
        ImageDraw.Draw(mascara).rounded_rectangle([0, 0, grande - 1, grande - 1],
                                                  radius=int(grande * 0.22), fill=255)
        fundo = Image.new("RGBA", (grande, grande), COR_FUNDO_MARCA + (255,))
        fundo.alpha_composite(simbolo, ((grande - simbolo.width) // 2,
                                        (grande - simbolo.height) // 2))
        icone.paste(fundo, (0, 0), mascara)
        return icone.resize((tamanho, tamanho), Image.Resampling.LANCZOS)
    except Exception as erro:
        print(f"[Branding] Falha ao recortar o simbolo: {erro}")
        return None


def _do_svg(tamanho: int):
    if not os.path.exists(CAMINHO_SVG):
        return None
    try:
        import fitz
        with fitz.open(CAMINHO_SVG) as doc:
            pagina = doc.load_page(0)
            lado = max(pagina.rect.width, pagina.rect.height) or 1
            # Rasteriza 2x e reduz: bordas do traco ficam bem mais suaves.
            escala = (tamanho * 2) / lado
            pix = pagina.get_pixmap(matrix=fitz.Matrix(escala, escala), alpha=True)
            imagem = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
        return imagem.resize((tamanho, tamanho), Image.Resampling.LANCZOS)
    except Exception as erro:
        print(f"[Branding] SVG indisponivel: {erro}")
        return None


def _do_png(tamanho: int):
    if not os.path.exists(CAMINHO_PNG):
        return None
    try:
        imagem = Image.open(CAMINHO_PNG).convert("RGBA")
        return imagem.resize((tamanho, tamanho), Image.Resampling.LANCZOS)
    except Exception:
        return None


# ------------------------------------------------------------------- icone
def _fonte_mais_nova(gerado: str) -> bool:
    """Alguma arte de origem mudou depois que este arquivo foi gerado?"""
    try:
        marca = os.path.getmtime(gerado)
        for origem in _origens():
            if os.path.exists(origem) and os.path.getmtime(origem) > marca:
                return True
    except OSError:
        return True
    return False


def salvar_icone(destino: str) -> bool:
    base = carregar_logo(256)
    if base is None:
        return False
    base.save(destino, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                     (128, 128), (256, 256)])
    return True


def garantir_icone() -> str | None:
    """Gera o .ico multi-resolucao usado pela janela e pelo executavel.

    Regenera sozinho quando a arte e mais recente que o icone em cache --
    trocar o logo nao deve exigir apagar arquivo nenhum na mao.
    """
    empacotado = resource_path("app.ico")
    if os.path.exists(empacotado) and not _fonte_mais_nova(empacotado):
        return empacotado
    if os.path.exists(CAMINHO_ICO) and not _fonte_mais_nova(CAMINHO_ICO):
        return CAMINHO_ICO
    try:
        return CAMINHO_ICO if salvar_icone(CAMINHO_ICO) else None
    except Exception as erro:
        print(f"[Branding] Falha ao gerar icone: {erro}")
        return None
