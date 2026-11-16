"""Redesenha a arte do Lumina: simbolo (documento + fita em gradiente) e
banner com o logotipo "Lumina / PDF READER" sobre azul-marinho estrelado."""
import math
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SAIDA = sys.argv[1]          # pasta arte/ do projeto
FONTE = sys.argv[2]          # Nunito[wght].ttf

CIANO = (63, 211, 242)
AZUL = (96, 150, 250)
VIOLETA = (139, 108, 246)
ROSA = (214, 120, 220)
LARANJA = (246, 168, 107)
NAVY = (11, 17, 32)
BRANCO_QUENTE = (246, 241, 231)


# ----------------------------------------------------------------- utilitarios
def bezier(p0, p1, p2, p3, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        a = (1 - t) ** 3
        b = 3 * (1 - t) ** 2 * t
        c = 3 * (1 - t) * t ** 2
        d = t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def caminho(segmentos):
    pts = []
    for seg in segmentos:
        pts += bezier(*seg)
    return pts


def gradiente(tamanho, paradas, angulo=90):
    """Gradiente linear multi-paradas (angulo em graus, 90 = cima->baixo)."""
    w, h = tamanho
    pequeno = 256
    base = Image.new("RGB", (pequeno, 1))
    for x in range(pequeno):
        t = x / (pequeno - 1)
        trecho = t * (len(paradas) - 1)
        i = min(int(trecho), len(paradas) - 2)
        f = trecho - i
        a, b = paradas[i], paradas[i + 1]
        base.putpixel((x, 0), tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3)))
    diag = int(math.hypot(w, h)) + 4
    faixa = base.resize((diag, diag), Image.Resampling.BICUBIC)
    faixa = faixa.rotate(-(angulo), resample=Image.Resampling.BICUBIC)
    esq, topo = (diag - w) // 2, (diag - h) // 2
    return faixa.crop((esq, topo, esq + w, topo + h))


def mascara_poligono(tamanho, pts):
    m = Image.new("L", tamanho, 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


# --------------------------------------------------------------------- simbolo
def simbolo(lado=1024, f=4):
    S = lado * f
    k = S / 1024.0

    def P(x, y):
        return (x * k, y * k)

    tela = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # --- documento (pagina com canto dobrado)
    x0, y0, x1, y1 = 385, 175, 770, 745
    dobra = 115
    raio = 34
    doc_m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(doc_m)
    d.rounded_rectangle([*P(x0, y0), *P(x1, y1)], radius=raio * k, fill=255)
    # recorta o canto superior direito
    d.polygon([P(x1 - dobra, y0 - 2), P(x1 + 2, y0 - 2), P(x1 + 2, y0 + dobra)], fill=0)
    papel = gradiente((S, S), [(240, 238, 255), (214, 206, 252), (178, 160, 246)], 100)
    tela.paste(papel, (0, 0), doc_m)
    # dobra do canto
    dobra_m = mascara_poligono((S, S), [P(x1 - dobra, y0), P(x1 - dobra + 8, y0 + dobra - 8),
                                        P(x1, y0 + dobra)])
    tela.paste(gradiente((S, S), [(255, 255, 255), (200, 190, 250)], 135), (0, 0), dobra_m)
    # sombra suave sob a dobra
    sombra = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sombra).polygon([P(x1 - dobra, y0 + 6), P(x1 - 8, y0 + dobra),
                                    P(x1 - dobra + 30, y0 + dobra + 20)], fill=70)
    sombra = sombra.filter(ImageFilter.GaussianBlur(14 * k))
    sombra = ImageChops.multiply(sombra, doc_m)
    tela.paste(Image.new("RGBA", (S, S), (90, 70, 170, 255)), (0, 0), sombra)

    # linhas de texto
    for i, comp in enumerate([0.70, 0.58, 0.78, 0.66, 0.50]):
        ly = 300 + i * 78
        lx0 = 445
        lx1 = lx0 + (x1 - 60 - lx0) * comp
        barra = Image.new("L", (S, S), 0)
        ImageDraw.Draw(barra).rounded_rectangle([*P(lx0, ly), *P(lx1, ly + 30)],
                                                radius=15 * k, fill=255)
        tela.paste(gradiente((S, S), [(70, 200, 245), (110, 150, 250), (150, 120, 245)], 0),
                   (0, 0), barra)

    # --- fita em "L" (frente), do ciano ao laranja
    externo = caminho([
        (P(330, 150), P(250, 150), P(205, 205), P(205, 290)),
        (P(205, 290), P(205, 450), P(200, 620), P(215, 760)),
        (P(215, 760), P(235, 880), P(330, 905), P(470, 900)),
        (P(470, 900), P(620, 895), P(760, 860), P(880, 740)),
    ])
    interno = caminho([
        (P(880, 740), P(800, 800), P(660, 830), P(520, 820)),
        (P(520, 820), P(410, 812), P(345, 770), P(345, 690)),
        (P(345, 690), P(345, 540), P(345, 400), P(350, 300)),
        (P(350, 300), P(352, 230), P(380, 190), P(430, 168)),
        (P(430, 168), P(400, 155), P(365, 150), P(330, 150)),
    ])
    fita_m = mascara_poligono((S, S), externo + interno)
    fita_m = fita_m.filter(ImageFilter.GaussianBlur(1.2 * k))
    cor_fita = gradiente((S, S), [CIANO, CIANO, AZUL, VIOLETA, ROSA, LARANJA, LARANJA, LARANJA], 82)
    # brilho na borda externa esquerda (volume)
    brilho = Image.new("L", (S, S), 0)
    ImageDraw.Draw(brilho).line(caminho([
        (P(330, 165), P(262, 168), P(225, 215), P(225, 300)),
        (P(225, 300), P(225, 460), P(222, 620), P(236, 750)),
    ]), fill=150, width=int(26 * k), joint="curve")
    brilho = brilho.filter(ImageFilter.GaussianBlur(16 * k))
    brilho = ImageChops.multiply(brilho, fita_m)
    cor_fita.paste(Image.new("RGB", (S, S), (255, 255, 255)), (0, 0), brilho)
    # sombra interna perto do documento (profundidade)
    escuro = Image.new("L", (S, S), 0)
    ImageDraw.Draw(escuro).line(interno[: len(interno) // 2 + 20], fill=55,
                                width=int(30 * k), joint="curve")
    escuro = ImageChops.multiply(escuro.filter(ImageFilter.GaussianBlur(26 * k)), fita_m)
    cor_fita.paste(Image.new("RGB", (S, S), (40, 30, 90)), (0, 0), escuro)

    # a fita passa POR CIMA da base do documento
    tela.paste(cor_fita, (0, 0), fita_m)

    # --- brilho geral (glow) atras de tudo
    silhueta = tela.split()[3]
    glow = silhueta.filter(ImageFilter.GaussianBlur(55 * k)).point(lambda v: int(v * 0.85))
    cor_glow = gradiente((S, S), [CIANO, VIOLETA, LARANJA], 70).convert("RGBA")
    cor_glow.putalpha(glow)
    final = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    final.alpha_composite(cor_glow)
    final.alpha_composite(tela)
    return final.resize((lado, lado), Image.Resampling.LANCZOS)


def estrela(tela, cx, cy, r, intensidade=255):
    """Clarao de lente: nucleo + raios horizontal e vertical."""
    camada = Image.new("L", tela.size, 0)
    d = ImageDraw.Draw(camada)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=intensidade)
    d.line([(cx - r * 9, cy), (cx + r * 9, cy)], fill=int(intensidade * 0.8), width=max(1, r // 3))
    d.line([(cx, cy - r * 4), (cx, cy + r * 4)], fill=int(intensidade * 0.6), width=max(1, r // 4))
    camada = camada.filter(ImageFilter.GaussianBlur(r * 0.8))
    halo = Image.new("L", tela.size, 0)
    ImageDraw.Draw(halo).ellipse([cx - r * 5, cy - r * 5, cx + r * 5, cy + r * 5], fill=90)
    halo = halo.filter(ImageFilter.GaussianBlur(r * 3))
    tela.paste(Image.new("RGB", tela.size, (200, 225, 255)), (0, 0), halo)
    tela.paste(Image.new("RGB", tela.size, (255, 255, 255)), (0, 0), camada)


# ---------------------------------------------------------------------- banner
def banner(sim, largura=1808, altura=1020):
    random.seed(7)
    fundo = Image.new("RGB", (largura, altura), NAVY)
    # vinheta radial (mais claro no centro-esquerda)
    luz = Image.new("L", (largura, altura), 0)
    ImageDraw.Draw(luz).ellipse([-200, -300, largura * 0.75, altura + 300], fill=255)
    luz = luz.filter(ImageFilter.GaussianBlur(260))
    fundo.paste(Image.new("RGB", (largura, altura), (22, 30, 56)), (0, 0), luz)
    # estrelas
    poeira = Image.new("L", (largura, altura), 0)
    dp = ImageDraw.Draw(poeira)
    for _ in range(170):
        x, y = random.randint(0, largura), random.randint(0, altura)
        r = random.choice([1, 1, 1, 2, 2, 3])
        dp.ellipse([x - r, y - r, x + r, y + r], fill=random.randint(60, 200))
    fundo.paste(Image.new("RGB", (largura, altura), (190, 210, 255)), (0, 0),
                poeira.filter(ImageFilter.GaussianBlur(0.8)))

    # simbolo a esquerda
    lado = 640
    s = sim.resize((lado, lado), Image.Resampling.LANCZOS)
    fundo = fundo.convert("RGBA")
    fundo.alpha_composite(s, (150, (altura - lado) // 2 - 10))
    base = fundo.convert("RGB")
    estrela(base, 150 + int(lado * 0.36), (altura - lado) // 2 + int(lado * 0.11), 9)

    # logotipo
    d = ImageDraw.Draw(base)
    titulo = ImageFont.truetype(FONTE, 252)
    try:
        titulo.set_variation_by_axes([700])
    except Exception:
        pass
    sub = ImageFont.truetype(FONTE, 64)
    try:
        sub.set_variation_by_axes([500])
    except Exception:
        pass
    x_txt = 760
    d.text((x_txt, 300), "Lumina", font=titulo, fill=BRANCO_QUENTE)
    # "PDF READER" espacado
    x = x_txt + 14
    for ch in "PDF READER":
        d.text((x, 612), ch, font=sub, fill=(232, 228, 220))
        x += d.textlength(ch, font=sub) + (16 if ch != " " else 12)
    return base


if __name__ == "__main__":
    import os
    os.makedirs(SAIDA, exist_ok=True)
    sim = simbolo()
    # recorte justo + margem para o simbolo isolado (fundo transparente)
    caixa = sim.split()[3].point(lambda v: 255 if v > 24 else 0).getbbox()
    isolado = sim.crop(caixa)
    lado = max(isolado.size) + 40
    quadro = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quadro.alpha_composite(isolado, ((lado - isolado.width) // 2, (lado - isolado.height) // 2))
    quadro.resize((512, 512), Image.Resampling.LANCZOS).save(os.path.join(SAIDA, "simbolo.png"))
    b = banner(sim)
    b.resize((904, 510), Image.Resampling.LANCZOS).save(os.path.join(SAIDA, "lumina.png"),
                                                        optimize=True)
    b.save(os.path.join(SAIDA, "lumina@2x.png"), optimize=True)
    print("ok")
