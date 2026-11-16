# services/exportar.py
"""Exporta grifos, anotacoes e marcadores de um livro para Markdown.

Estudar e grifar so vale metade se o que foi grifado fica preso dentro do
leitor. O Markdown abre em qualquer editor, cola no Notion/Obsidian e
imprime limpo.
"""
import time


def _capitulo_por_pagina(sumario):
    """Funcao pagina(0-based) -> titulo do capitulo em que ela esta."""
    entradas = sorted(
        (max(0, int(item[2]) - 1), str(item[1]).strip())
        for item in (sumario or [])
        if len(item) >= 3 and item[1] and item[2])

    def capitulo(pagina):
        atual = ""
        for inicio, titulo in entradas:
            if inicio > pagina:
                break
            atual = titulo
        return atual

    return capitulo


def _citar(texto):
    linhas = " ".join((texto or "").split())
    return "> " + linhas


def gerar_markdown(livro, grifos, notas, marcadores, sumario=None):
    """Monta o documento.

    livro: dict com titulo/autor/total_paginas
    grifos: [(pagina, trecho, criado_em)]
    notas: [(pagina, texto)]
    marcadores: [(pagina, rotulo)]
    sumario: get_toc() do PyMuPDF, para agrupar por capitulo
    """
    titulo = (livro.get("titulo") or "Sem titulo").strip()
    autor = (livro.get("autor") or "").strip()
    capitulo = _capitulo_por_pagina(sumario)

    saida = [f"# {titulo}"]
    if autor:
        saida.append(f"*{autor}*")
    saida.append("")
    saida.append(f"Exportado pelo Lumina em {time.strftime('%d/%m/%Y %H:%M')} · "
                 f"{len(grifos)} grifo(s) · {len(notas)} anotacao(oes) · "
                 f"{len(marcadores)} marcador(es)")
    saida.append("")

    # Grifos e notas lado a lado, pagina por pagina: a nota da pagina
    # costuma comentar justamente o que foi grifado nela.
    por_pagina = {}
    for pagina, trecho, _ in grifos:
        por_pagina.setdefault(int(pagina), {"grifos": [], "nota": ""})["grifos"].append(trecho)
    for pagina, texto in notas:
        por_pagina.setdefault(int(pagina), {"grifos": [], "nota": ""})["nota"] = texto

    if por_pagina:
        saida.append("## Grifos e anotacoes")
        saida.append("")
        capitulo_atual = None
        for pagina in sorted(por_pagina):
            cap = capitulo(pagina)
            if cap and cap != capitulo_atual:
                saida.append(f"### {cap}")
                saida.append("")
                capitulo_atual = cap
            saida.append(f"**Pagina {pagina + 1}**")
            saida.append("")
            for trecho in por_pagina[pagina]["grifos"]:
                saida.append(_citar(trecho))
                saida.append("")
            nota = (por_pagina[pagina]["nota"] or "").strip()
            if nota:
                saida.append("📝 " + nota.replace("\n", "  \n"))
                saida.append("")

    if marcadores:
        saida.append("## Marcadores")
        saida.append("")
        for pagina, rotulo in marcadores:
            cap = capitulo(int(pagina))
            detalhe = rotulo or cap
            saida.append(f"- Pagina {int(pagina) + 1}" + (f" — {detalhe}" if detalhe else ""))
        saida.append("")

    if not por_pagina and not marcadores:
        saida.append("_Nenhum grifo, anotacao ou marcador neste livro ainda._")
        saida.append("")

    return "\n".join(saida)
