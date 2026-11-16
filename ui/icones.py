# ui/icones.py
"""Conjunto de icones monocromaticos da interface.

A versao anterior misturava emoji colorido (estrela, paleta, alfinete) com
glifos de texto na mesma barra: cada botao vinha com cor, peso e tamanho
proprios, e o resultado parecia remendado. Aqui todos os icones saem da
"Segoe MDL2 Assets", a fonte de icones que acompanha o Windows 10/11 -- traco
uniforme, uma cor so, e essa cor passa a ser a do tema.

Fora do Windows (ou se a fonte sumir) cada icone tem um substituto em texto
simples, entao a interface continua legivel em vez de exibir quadradinhos.
Os codigos foram conferidos um a um renderizando a fonte.
"""
import tkinter.font as tkfont

FAMILIA = "Segoe MDL2 Assets"

# chave semantica -> (codigo MDL2, substituto em texto)
_MAPA = {
    "voltar":        ("E72B", "‹"),
    "menu":          ("E700", "☰"),
    "busca":         ("E721", "⌕"),
    "estrela":       ("E734", "☆"),
    "estrela_cheia": ("E735", "★"),
    "nota":          ("E70F", "✎"),
    "ia":            ("E82F", "◎"),
    "tema":          ("E790", "◐"),
    "fixar":         ("E718", "⇧"),
    "fixado":        ("E840", "⬆"),
    "foco":          ("E740", "⛶"),
    "anterior":      ("E76B", "‹"),
    "proximo":       ("E76C", "›"),
    "pagina_ant":    ("E892", "◀"),
    "pagina_prox":   ("E893", "▶"),
    "cima":          ("E70E", "▲"),
    "baixo":         ("E70D", "▼"),
    "fechar":        ("E711", "✕"),
    "mais_opcoes":   ("E712", "⋯"),
    "adicionar":     ("E710", "＋"),
    "config":        ("E713", "⚙"),
    "grifar":        ("EC24", "▰"),
    "apagar_grifo":  ("E75C", "▱"),
    "copiar":        ("E8C8", "⧉"),
    "lixeira":       ("E74D", "⌦"),
    "pasta":         ("ED25", "▤"),
    "documento":     ("E7C3", "▢"),
    "biblioteca":    ("E8F1", "▤"),
    "mover":         ("E8DE", "➦"),
    "play":          ("E768", "▶"),
    "pause":         ("E769", "⏸"),
    "processar":     ("E945", "⚡"),
    "info":          ("E946", "ⓘ"),
    "imagem":        ("EB9F", "▣"),
    "recentes":      ("E81C", "◷"),
    "visivel":       ("E7B3", "◉"),
    "confirmado":    ("E73E", "✓"),
}

_disponivel = None


def disponivel() -> bool:
    """A fonte de icones existe neste sistema?"""
    global _disponivel
    if _disponivel is None:
        try:
            _disponivel = FAMILIA in set(tkfont.families())
        except Exception:
            _disponivel = False
    return _disponivel


def icone(chave: str) -> str:
    par = _MAPA.get(chave)
    if par is None:
        return chave
    codigo, reserva = par
    return chr(int(codigo, 16)) if disponivel() else reserva


def eh_icone(chave: str) -> bool:
    return chave in _MAPA


def familia() -> str:
    """Familia a usar num rotulo que contenha apenas icones."""
    from configuracoes import FONTE_UI
    return FAMILIA if disponivel() else FONTE_UI


class _Atalho:
    """Acesso por atributo: `ICO.busca` em vez de `icone("busca")`."""

    def __getattr__(self, chave):
        if chave not in _MAPA:
            raise AttributeError(f"icone '{chave}' nao existe")
        return icone(chave)


ICO = _Atalho()
