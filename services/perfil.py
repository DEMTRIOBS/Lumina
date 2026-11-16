# services/perfil.py
"""Pipeline de deteccao: muitos detectores, um perfil unico do livro.

O problema que isto resolve: cada recurso novo (figuras, artigos, exercicios,
poesia, receitas...) tentava se encaixar sozinho no leitor, e a interface ia
enchendo de painel que nao servia para o livro aberto.

Aqui cada detector e OPCIONAL e independente. Todos recebem o mesmo contexto,
contribuem com um pedaco do perfil, e a interface le so o perfil:

    PDF -> Contexto -> [detectores] -> PerfilLivro -> interface dinamica

Acrescentar um detector novo e escrever uma classe e chamar `registrar`.
Nada no leitor precisa mudar: se o detector devolver paineis, eles aparecem;
se nao devolver, nada muda para os outros livros.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from services.pdf_service import texto_da_pagina


# Quantas paginas amostrar. Varrer 1000 paginas inteiras em todo detector nao
# se paga; a amostra distribuida representa bem a estrutura do livro.
_AMOSTRA_MAX = 220


# ============================================================== CONTEXTO
@dataclass
class Contexto:
    """Tudo que os detectores precisam, lido do PDF uma unica vez.

    Cada detector receber o documento e ler por conta propria multiplicaria
    o custo pelo numero de detectores.
    """
    documento: object
    motor: object                      # MotorReflow ja analisado
    paginas: dict = field(default_factory=dict)    # numero -> texto
    total_paginas: int = 0
    cancelar: object = None

    @property
    def texto_amostra(self) -> str:
        return "\n".join(self.paginas.values())

    def abortou(self) -> bool:
        return self.cancelar is not None and self.cancelar.is_set()


def montar_contexto(documento, motor, cancelar=None) -> Optional[Contexto]:
    total = len(documento)
    contexto = Contexto(documento=documento, motor=motor,
                        total_paginas=total, cancelar=cancelar)
    passo = max(1, total // _AMOSTRA_MAX)
    for numero in range(0, total, passo):
        if contexto.abortou():
            return None
        try:
            contexto.paginas[numero] = texto_da_pagina(documento, numero)
        except Exception:
            continue
    return contexto


# ================================================================ PERFIL
@dataclass
class PerfilLivro:
    tipo_renderizacao: str = "prosa"
    categoria: str = ""
    idioma: str = ""
    estrutura: dict = field(default_factory=dict)   # capitulos, figuras, ...
    linguagens: List[str] = field(default_factory=list)
    paineis: dict = field(default_factory=dict)     # aba -> itens da lista
    detalhes: dict = field(default_factory=dict)    # bruto por detector

    def resumo(self) -> str:
        partes = [f"{v} {k}" for k, v in self.estrutura.items() if v]
        return "  ·  ".join(partes)


# ============================================================= DETECTORES
class Detector:
    """Contrato de um detector. Herde, implemente `analisar` e registre.

    `analisar` devolve um dicionario com qualquer subconjunto de:
        {"estrutura": {...}, "paineis": {"Aba": [itens]},
         "linguagens": [...], "idioma": "...", "detalhes": {...}}
    """
    nome = "detector"
    # Quando preenchido, o detector so roda nesses tipos de renderizacao.
    tipos = ()

    def aplicavel(self, contexto, tipo_render) -> bool:
        return not self.tipos or tipo_render in self.tipos

    def analisar(self, contexto) -> dict:
        raise NotImplementedError


_REGISTRO: List[Detector] = []


def registrar(detector: Detector) -> None:
    _REGISTRO.append(detector)


def detectores() -> List[Detector]:
    return list(_REGISTRO)


# ------------------------------------------------------------ IDIOMA
# Palavras curtas e frequentes identificam a lingua com pouquissimo texto e
# sem dependencia externa. Serve para escolher a voz do TTS e, mais tarde,
# hifenizacao e dicionario.
_MARCAS_IDIOMA = {
    "Portugues": ("de", "que", "para", "com", "uma", "nao", "por", "mais",
                  "como", "isso", "voce", "ser", "sao", "pode", "quando"),
    "Ingles": ("the", "and", "for", "with", "that", "this", "you", "are",
               "from", "have", "will", "your", "can", "not", "how"),
    "Espanhol": ("que", "para", "con", "una", "por", "los", "las", "del",
                 "como", "pero", "esta", "muy", "sus", "puede"),
    "Frances": ("les", "des", "une", "pour", "dans", "que", "qui", "est",
                "vous", "avec", "sur", "pas", "plus", "cette"),
    "Alemao": ("und", "der", "die", "das", "ist", "nicht", "eine", "sich",
               "mit", "auf", "fur", "auch", "dem", "den"),
}
_RE_PALAVRA = re.compile(r"\b[a-zà-ÿ]{2,}\b")


class DetectorIdioma(Detector):
    nome = "idioma"

    def analisar(self, contexto):
        palavras = _RE_PALAVRA.findall(contexto.texto_amostra.lower()[:120000])
        if len(palavras) < 40:
            return {}
        contagem = Counter(palavras)
        pontos = {}
        for idioma, marcas in _MARCAS_IDIOMA.items():
            pontos[idioma] = sum(contagem.get(m, 0) for m in marcas)
        idioma, melhor = max(pontos.items(), key=lambda kv: kv[1])
        if melhor < 15:
            return {}
        return {"idioma": idioma, "detalhes": {"idioma_pontos": pontos}}


# ------------------------------------------------------------ ESTRUTURA
_RE_CAPITULO = re.compile(
    r"^\s*(cap[ií]tulo|cap\.|unidade|parte|se[cç][aã]o|chapter|li[cç][aã]o)\s+"
    r"([0-9]{1,3}|[ivxlcdm]{1,7})\b", re.IGNORECASE | re.MULTILINE)
_RE_EXERCICIO = re.compile(
    r"^\s*(exerc[ií]cio|quest[aã]o|atividade|pratique|desafio)\s*[:\-–]?\s*\d{0,3}",
    re.IGNORECASE | re.MULTILINE)
_RE_TABELA = re.compile(r"^\s*(tabela|quadro|table)\s+\d", re.IGNORECASE | re.MULTILINE)
_RE_EQUACAO = re.compile(r"^\s*(equa[cç][aã]o|equation|teorema|lema|corol[aá]rio)\s+\d",
                         re.IGNORECASE | re.MULTILINE)
_RE_ALGORITMO = re.compile(r"^\s*(algoritmo|algorithm|pseudoc[oó]digo)\s+\d",
                           re.IGNORECASE | re.MULTILINE)


class DetectorEstrutura(Detector):
    """Contagens que alimentam o resumo do livro e decidem quais paineis
    fazem sentido oferecer."""
    nome = "estrutura"

    def analisar(self, contexto):
        texto = contexto.texto_amostra
        # A amostra cobre 1 de cada `passo` paginas: extrapolar seria chute,
        # entao os numeros sao "encontrados na amostra", nao estimativas.
        estrutura = {
            "capitulos": len(set(m.group(0).strip().lower()
                                 for m in _RE_CAPITULO.finditer(texto))),
            "exercicios": len(_RE_EXERCICIO.findall(texto)),
            "tabelas": len(_RE_TABELA.findall(texto)),
            "equacoes": len(_RE_EQUACAO.findall(texto)),
            "algoritmos": len(_RE_ALGORITMO.findall(texto)),
        }

        paineis = {}
        capitulos = []
        for numero, conteudo in contexto.paginas.items():
            for encontro in _RE_CAPITULO.finditer(conteudo):
                linha = conteudo[encontro.start():encontro.start() + 70]
                capitulos.append({"rotulo": linha.split("\n")[0].strip()[:42],
                                  "detalhe": "", "pagina": numero})
        # Sumario proprio so vale quando o PDF nao traz o embutido.
        if capitulos and not _tem_sumario_embutido(contexto.documento):
            vistos, unicos = set(), []
            for item in capitulos:
                chave = item["rotulo"].lower()
                if chave not in vistos:
                    vistos.add(chave)
                    unicos.append(item)
            paineis["Capitulos"] = unicos

        exercicios = []
        for numero, conteudo in contexto.paginas.items():
            for encontro in _RE_EXERCICIO.finditer(conteudo):
                exercicios.append({
                    "rotulo": encontro.group(0).strip()[:34],
                    "detalhe": conteudo[encontro.end():encontro.end() + 60].strip(),
                    "pagina": numero})
        if len(exercicios) >= 4:
            paineis["Exercicios"] = exercicios

        return {"estrutura": estrutura, "paineis": paineis}


def _tem_sumario_embutido(documento) -> bool:
    try:
        return bool(documento.get_toc())
    except Exception:
        return False


# ------------------------------------------------------------ FIGURAS
class DetectorFiguras(Detector):
    nome = "figuras"

    def analisar(self, contexto):
        from services import figuras as mod
        lista = mod.listar_figuras(contexto.documento, cancelar=contexto.cancelar)
        if not lista:
            return {"estrutura": {"figuras": 0}}
        itens = [{"rotulo": (f"{f.tipo.capitalize()} {f.numero}".strip()
                             if f.numero else f"Pagina {f.pagina + 1}"),
                  "detalhe": (f.legenda or "")[:70], "pagina": f.pagina}
                 for f in lista]
        return {"estrutura": {"figuras": len(lista)},
                "paineis": {"Figuras": itens},
                "detalhes": {"figuras": lista}}


# ------------------------------------------------------------ JURIDICO
class DetectorJuridico(Detector):
    nome = "juridico"

    def analisar(self, contexto):
        from services import deteccao_juridico as mod
        artigos = []
        for numero, texto in contexto.paginas.items():
            for art in mod.artigos(texto):
                if art["tipo"] == "artigo":
                    artigos.append({"rotulo": art["rotulo"], "detalhe": "",
                                    "pagina": numero})
        # Poucos "Art." soltos aparecem em qualquer livro que cite uma lei;
        # painel so faz sentido quando o livro E juridico.
        if len(artigos) < 12:
            return {}
        return {"estrutura": {"artigos": len(artigos)},
                "paineis": {"Artigos": artigos}}


# ------------------------------------------------------------ HISTORIA
class DetectorDatas(Detector):
    nome = "datas"

    def analisar(self, contexto):
        from services import deteccao_juridico as mod
        eventos = mod.linha_do_tempo(contexto.paginas)
        if len(eventos) < 8:
            return {}
        itens = [{"rotulo": str(e["ano"]), "detalhe": e["trecho"][:70],
                  "pagina": e["pagina"]} for e in eventos]
        return {"estrutura": {"datas": len(eventos)},
                "paineis": {"Linha do tempo": itens}}


# ------------------------------------------------------------ ACADEMICO
class DetectorAcademico(Detector):
    nome = "academico"

    def analisar(self, contexto):
        from services import deteccao_academico as mod

        citacoes = referencias = 0
        itens = []
        for numero, texto in contexto.paginas.items():
            achadas = mod.citacoes(texto)
            citacoes += len(achadas)
            for entrada in mod.referencias_bibliograficas(texto):
                referencias += 1
                if len(itens) < 400:
                    itens.append({
                        "rotulo": entrada["autor"][:34],
                        "detalhe": entrada["texto"][:70].replace("\n", " "),
                        "pagina": numero})

        # Livro academico de verdade tem citacao em quase toda pagina. Poucas
        # aparecem em qualquer obra que mencione um autor.
        if citacoes < 15 and referencias < 8:
            return {}
        resultado = {"estrutura": {"citacoes": citacoes, "referencias": referencias}}
        if itens:
            resultado["paineis"] = {"Referencias": itens}
        return resultado


# ------------------------------------------------------------ RELIGIOSO
class DetectorReligioso(Detector):
    nome = "religioso"

    def analisar(self, contexto):
        from services import deteccao_academico as mod

        paginas_com_versiculo = 0
        cruzadas = []
        for numero, texto in contexto.paginas.items():
            if mod.versiculos(texto):
                paginas_com_versiculo += 1
            for referencia in mod.referencias_biblicas(texto):
                if len(cruzadas) < 400:
                    cruzadas.append({"rotulo": referencia["texto"][:30],
                                     "detalhe": "", "pagina": numero})

        # Exige versiculo numerado na maior parte do livro: um trecho biblico
        # citado num livro de historia nao faz dele uma Biblia.
        total = max(1, len(contexto.paginas))
        if paginas_com_versiculo / total < 0.35 and len(cruzadas) < 25:
            return {}
        resultado = {"estrutura": {"versiculos_paginas": paginas_com_versiculo}}
        if cruzadas:
            resultado["paineis"] = {"Versiculos": cruzadas}
        return resultado


# ------------------------------------------------------------ CODIGO
class DetectorCodigo(Detector):
    nome = "codigo"
    tipos = ("programacao",)

    def analisar(self, contexto):
        motor = contexto.motor
        votos = getattr(motor, "_votos_lingua", None)
        linguagens = [nome for nome, _ in votos.most_common(4)] if votos else []
        return {"linguagens": linguagens,
                "estrutura": {"codigos": _contar_blocos_codigo(contexto)}}


def _contar_blocos_codigo(contexto) -> int:
    total = 0
    for numero in list(contexto.paginas)[:60]:
        if contexto.abortou():
            break
        try:
            pagina = contexto.motor.pagina(numero)
        except Exception:
            continue
        total += sum(1 for b in pagina.blocos if b.tipo == "codigo")
    return total


# =============================================================== PIPELINE
def montar_perfil(documento, motor, cancelar=None, ao_progredir=None) -> PerfilLivro:
    """Roda os detectores aplicaveis e funde tudo num perfil so.

    Um detector que falha nao derruba os outros: cada um e isolado, porque
    detector novo e a parte do sistema que mais tende a quebrar.
    """
    perfil = PerfilLivro()
    perfil.tipo_renderizacao = getattr(motor, "render_detectado", "prosa")

    contexto = montar_contexto(documento, motor, cancelar)
    if contexto is None:
        return perfil

    ativos = [d for d in _REGISTRO if d.aplicavel(contexto, perfil.tipo_renderizacao)]
    for indice, detector in enumerate(ativos, 1):
        if contexto.abortou():
            break
        try:
            resultado = detector.analisar(contexto) or {}
        except Exception as erro:
            print(f"[Perfil] Detector '{detector.nome}' falhou: {erro}")
            continue

        perfil.estrutura.update(resultado.get("estrutura") or {})
        perfil.paineis.update(resultado.get("paineis") or {})
        perfil.detalhes[detector.nome] = resultado.get("detalhes")
        if resultado.get("idioma"):
            perfil.idioma = resultado["idioma"]
        for lingua in resultado.get("linguagens") or []:
            if lingua not in perfil.linguagens:
                perfil.linguagens.append(lingua)
        if ao_progredir:
            try:
                ao_progredir(indice, len(ativos))
            except Exception:
                pass

    return perfil


# Ordem de registro = ordem de execucao. Idioma primeiro porque e barato e os
# proximos podem querer usa-lo.
registrar(DetectorIdioma())
registrar(DetectorEstrutura())
registrar(DetectorFiguras())
registrar(DetectorJuridico())
registrar(DetectorDatas())
registrar(DetectorAcademico())
registrar(DetectorReligioso())
registrar(DetectorCodigo())
