# services/indice.py
"""Indice de blocos do livro: a base para responder COM BASE NO LIVRO.

A regra que organiza este modulo inteiro: o LUMINA responde, a IA escreve.
Quem decide o que e verdade e a recuperacao daqui -- deterministica, auditavel
e sem modelo nenhum. O modelo entra depois, e so para redigir o que este
modulo ja encontrou.

POR QUE BLOCOS E NAO PAGINAS:
uma pagina de livro tecnico mistura o fim de um assunto, um bloco de codigo e
o comeco de outro assunto. Recuperando a pagina inteira, o modelo recebe tres
temas e escolhe mal; recuperando o bloco de 300-700 caracteres, ele recebe o
paragrafo que responde a pergunta. A granularidade menor tambem faz a
pontuacao significar alguma coisa: um termo que aparece 2 vezes num bloco de
500 caracteres e o assunto do bloco, enquanto 2 vezes numa pagina de 3000 e
menção de passagem.

POR QUE SEM EMBEDDINGS:
embeddings exigiriam um segundo modelo (mais 100-400 MB) e uma passada de
inferencia por bloco -- minutos num livro de mil paginas. TF-IDF com reranker
por cobertura e proximidade resolve as perguntas que um leitor faz sobre um
livro tecnico ("onde ele explica X", "o que e Y"), porque essas perguntas
usam as MESMAS palavras que o livro usa. Custo medido: cerca de 3 s para
indexar 1101 paginas, e a busca fica na casa dos milissegundos.

O QUE VAI PARA O DISCO:
o indice inteiro vira um arquivo `.lumina` comprimido em %APPDATA%. Da segunda
abertura em diante o livro ja abre com tudo pronto.
"""
import json
import os
import re
import time
import zlib
from dataclasses import dataclass, field, asdict

from core.paths import user_path
from ia.offline import _STOPWORDS, _normalizar
from services.pdf_service import texto_da_pagina

# ------------------------------------------------------------------ CONSTANTES
# Faixa de tamanho de bloco. Abaixo do minimo o trecho nao tem contexto para
# responder nada; acima do maximo volta o problema da pagina inteira.
_MIN_BLOCO = 300
_MAX_BLOCO = 700
# Um paragrafo maior que isto e quebrado em frases ate caber.
_CORTE_DURO = 900

_VERSAO_INDICE = 5

_RE_ESPACOS = re.compile(r"\s+")
_RE_PARAGRAFO = re.compile(r"\n\s*\n")
_RE_FIM_FRASE = re.compile(r"(?<=[.!?;:])\s+")
_RE_PALAVRA = re.compile(r"\b[\wáàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ]{3,}\b", re.UNICODE)

# Marcas de codigo. Reaproveitadas do motor de contexto pela mesma razao de la:
# reconhecer `def` ou `SELECT` e trabalho de expressao regular.
_RE_CODIGO = re.compile(
    r"(\b(def|class|function|return|import|from|const|let|var|public|private|"
    r"void|null|None|True|False|elif|SELECT|INSERT|UPDATE|WHERE|FROM)\b|"
    r"[{}]|;\s*$|=>|::|\(\)|\[\]|</\w+>|\w+\.\w+\()", re.MULTILINE)

# Identificador tecnico. A primeira versao pegava so CamelCase, snake_case e
# ALL_CAPS, e deixava de fora justamente tres especies que a classificacao por
# tipo precisa VER para poder separar:
#
#   settings.py    nome de arquivo -- some se o ponto nao entrar no padrao
#   medicSearch    lowerCamelCase -- o nome do projeto de exemplo
#   Django         nome proprio simples -- o framework do livro inteiro
#
# Sem o ultimo, o termo mais importante do livro nao participava da expansao
# por assunto: procurar "consulta no banco" perdia a ligacao com Django.
_RE_IDENTIFICADOR = re.compile(
    r"\b("
    r"\w+\.(?:py|js|jsx|ts|tsx|html|css|json|ya?ml|xml|sql|md|cfg|ini|toml|"
    r"env|sh|bat|ps1)"                       # arquivo
    r"|[A-Z][a-z]+[A-Z]\w*"                  # UpperCamelCase
    r"|[a-z]+[A-Z]\w*"                       # lowerCamelCase
    r"|[a-z]+_[a-z_]{2,}"                    # snake_case
    r"|[A-Z]{3,}"                            # ALL_CAPS
    r")\b")

# Nome proprio de tecnologia: capitalizado NO MEIO da frase. A exigencia de
# nao estar no comeco e o que separa "Django" de qualquer palavra que apenas
# abre um periodo.
_RE_NOME_PROPRIO = re.compile(r"(?<=[a-z,;]\s)([A-Z][a-z]{3,15})\b")
# Palavra unica capitalizada, sem marca tecnica nenhuma. So estas passam pelo
# teste de "esta palavra tambem vive em minuscula no livro?".
_RE_PALAVRA_SIMPLES = re.compile(r"[A-Z][a-z]+")


# ============================================================ ENTIDADES
# "Conceito = qualquer identificador importante" foi a premissa errada da
# primeira versao, e o sintoma apareceu na expansao de busca: procurar
# "consulta no banco" trazia `tiago_luiz` e `livro_django` -- o usuario e o
# diretorio do projeto de exemplo do livro. Sao tokens legitimos e devem
# continuar achaveis na busca literal; o que eles nao podem e participar da
# expansao por assunto, porque nao SAO assunto.
#
# Os tipos abaixo saem de medidas do proprio livro, nao de lista fixa. O sinal
# que separa tudo, medido na estante: quantas vezes o token aparece colado a
# uma marca de caminho ou de prompt de shell.
#
#   ForeignKey, QuerySet, ModelForm ....  0% de contexto de caminho
#   livro_django, medicSearch .........  88% e 79%
#   tiago_luiz ........................ 100%
#
# Peso na recuperacao. Uma definicao vale o dobro de uma citacao, e o nome do
# projeto de exemplo quase nao vale nada -- e o que evita que uma busca por
# assunto seja sequestrada pelo diretorio do autor.
PESO_TIPO = {
    "conceito": 1.00,
    "framework": 0.95,
    "simbolo": 0.60,
    "arquivo": 0.30,
    "projeto": 0.10,
    "pessoa": 0.05,
}
# So estes participam da expansao por assunto. Os demais seguem indexados e
# achaveis literalmente -- quem procura `settings.py` tem que achar.
TIPOS_EXPANSIVEIS = ("conceito", "framework")

# Acima desta fracao de aparicoes em contexto de caminho/prompt, o token e do
# PROJETO de exemplo, nao do assunto do livro.
_FRACAO_CAMINHO = 0.40
# Abaixo desta fracao de aparicoes em prosa, e simbolo de codigo: existe nas
# listagens, nao no texto que explica.
_FRACAO_PROSA_CONCEITO = 0.45
# Espalhamento minimo para um termo puxar outros numa busca por assunto.
# Medido: ForeignKey e QuerySet atravessam capitulos; `password` e `port`
# vivem num bloco de configuracao so. Os dois passam pela classificacao de
# especie -- e a densidade que os separa.
#
# Medido no livro de Django: ForeignKey 0,38 e QuerySet 0,48 de um lado;
# `port` 0,28 e `password` 0,19 do outro. O corte fica no vao entre eles.
#
# Nota para quem mexer: `PORT`, `PASSWORD` e `HOST` chegam ate aqui porque sao
# ALL_CAPS num dicionario de configuracao, e a classificacao por especie os
# aceita como conceito. Separa-los de `API`, `ORM` e `TDD` -- que sao ALL_CAPS
# legitimos -- exige um PAPEL ("configuracao") alem do tipo. Ate la, e a
# densidade que segura.
_DENSIDADE_MINIMA_EXPANSAO = 0.32

_RE_ARQUIVO = re.compile(
    r"\.(py|js|jsx|ts|tsx|html|htm|css|scss|json|ya?ml|xml|sql|md|txt|cfg|ini|"
    r"toml|env|lock|sh|bat|ps1|java|cs|cpp|rb|go|rs|php)$", re.IGNORECASE)

# Frameworks e bibliotecas conhecidos. Lista curta de proposito: serve para
# garantir que o nome da tecnologia do livro participe da expansao mesmo
# quando aparece pouco em frase de definicao.
_FRAMEWORKS = {
    "django", "flask", "fastapi", "react", "vue", "angular", "svelte",
    "spring", "laravel", "rails", "express", "nextjs", "nuxt", "celery",
    "pandas", "numpy", "pytorch", "tensorflow", "keras", "sklearn",
    "bootstrap", "tailwind", "jquery", "pytest", "unittest", "docker",
    "kubernetes", "redis", "postgresql", "mysql", "sqlite", "mongodb",
}


@dataclass
class Conceito:
    """O que o livro sabe sobre um termo -- uma verbete do proprio livro.

    Reune, num lugar so, onde o termo e DEFINIDO, onde e exemplificado e onde
    e apenas citado. E o que permite, depois, responder "mostre so os
    exemplos" sem refazer busca nenhuma.
    """
    nome: str = ""
    tipo: str = "conceito"
    definicao: str = ""
    pagina_definicao: int = -1
    paginas: list = field(default_factory=list)
    exemplos: list = field(default_factory=list)     # paginas com codigo
    citacoes: list = field(default_factory=list)     # paginas com mencao
    relacionados: list = field(default_factory=list)
    capitulos: list = field(default_factory=list)
    vezes: int = 0
    # Espalhamento pelo livro, de 0 a 1. Um termo que atravessa o livro inteiro
    # e assunto; um que existe em duas paginas seguidas e detalhe de uma
    # secao. E o que separa `ForeignKey` de `password`: os dois passam por
    # "conceito" na classificacao por especie, mas so o primeiro E do que o
    # livro trata.
    densidade: float = 0.0
    # Quanto este termo importa NESTE livro: densidade + ter definicao + peso
    # da especie. Ordena o mapa de conceitos e barra a expansao por assunto.
    importancia: float = 0.0

    @property
    def peso(self):
        return PESO_TIPO.get(self.tipo, 0.5)

    @property
    def explicado(self) -> bool:
        return bool(self.definicao)

    @property
    def primeira_aparicao(self):
        return self.paginas[0] if self.paginas else -1

    @property
    def ultima_aparicao(self):
        return self.paginas[-1] if self.paginas else -1


@dataclass
class BlocoIndice:
    """Um trecho recuperavel, com o que se sabe sobre ele."""
    id: int = 0
    texto: str = ""
    pagina: int = 0
    capitulo: str = ""
    tipo: str = "prosa"            # prosa | codigo
    conceitos: list = field(default_factory=list)
    # 1 a 5. Sai da densidade de termo tecnico, tamanho de frase e presenca de
    # formula -- nao de IA. Serve para avisar "este trecho e denso" e, mais
    # tarde, para o tutor saber onde o leitor tende a travar.
    dificuldade: int = 1
    # Segundos de leitura estimados, no ritmo de ~200 palavras por minuto.
    segundos: int = 0
    # Este bloco DEFINE um conceito ("QuerySet e uma colecao de..."), em vez de
    # apenas menciona-lo. E o sinal que faz a definicao formal ganhar do bloco
    # que repete a palavra dezoito vezes.
    define: list = field(default_factory=list)


# ------------------------------------------------------------------ SEGMENTACAO
def _partir_em_blocos(texto):
    """Quebra o texto de uma pagina em trechos de tamanho util.

    Respeita paragrafo primeiro e frase depois: cortar no meio de uma frase
    produziria trechos que nao respondem nada sozinhos.
    """
    texto = (texto or "").strip()
    if not texto:
        return []

    brutos = []
    for paragrafo in _RE_PARAGRAFO.split(texto):
        paragrafo = _RE_ESPACOS.sub(" ", paragrafo).strip()
        if not paragrafo:
            continue
        if len(paragrafo) <= _CORTE_DURO:
            brutos.append(paragrafo)
            continue
        # Paragrafo gigante (tabela, listagem corrida): parte por frase.
        atual = ""
        for frase in _RE_FIM_FRASE.split(paragrafo):
            if len(atual) + len(frase) + 1 > _MAX_BLOCO and atual:
                brutos.append(atual.strip())
                atual = frase
            else:
                atual = f"{atual} {frase}".strip()
        if atual.strip():
            brutos.append(atual.strip())

    # Junta os curtos com o vizinho ate chegarem ao minimo util.
    blocos = []
    for parte in brutos:
        if blocos and len(blocos[-1]) < _MIN_BLOCO:
            juntado = f"{blocos[-1]} {parte}".strip()
            if len(juntado) <= _CORTE_DURO:
                blocos[-1] = juntado
                continue
        blocos.append(parte)
    return [b for b in blocos if len(b) >= 40]


def _tipo_do_bloco(texto):
    marcas = len(_RE_CODIGO.findall(texto))
    # Proporcional ao tamanho: 3 marcas em 300 caracteres e codigo, em 700 e
    # um paragrafo que cita nomes de funcao.
    return "codigo" if marcas >= max(3, len(texto) // 120) else "prosa"


def _conceitos_do_bloco(texto, limite=8):
    """Identificadores e nomes proprios que nomeiam o bloco."""
    vistos = []
    for achado in _RE_IDENTIFICADOR.findall(texto):
        if achado and achado not in vistos:
            vistos.append(achado)
        if len(vistos) >= limite:
            return vistos
    # Nome proprio entra depois: e o candidato mais fraco, e nao pode ocupar
    # as vagas de um identificador tecnico de verdade.
    for achado in _RE_NOME_PROPRIO.findall(texto):
        if achado not in vistos and _normalizar(achado) not in _STOPWORDS:
            vistos.append(achado)
        if len(vistos) >= limite:
            break
    return vistos


def _termos(texto):
    """Palavras indexaveis, sem acento e sem palavra de ligacao."""
    saida = []
    for palavra in _RE_PALAVRA.findall(texto or ""):
        normal = _normalizar(palavra)
        if normal not in _STOPWORDS:
            saida.append(normal)
    return saida


# Frase que DEFINE alguma coisa. E o padrao que separa a pagina onde o autor
# explica o conceito daquela onde ele so usa o nome de passagem -- e o caso
# que o reranker por frequencia sozinho erra: um bloco de codigo pode repetir
# "QuerySet" dezoito vezes sem dizer o que ele e.
_RE_DEFINICAO = re.compile(
    r"\b([A-Za-z_][\w\.]{2,40})\s+"
    r"(?:e|é|sao|são|e um|é um|e uma|é uma|significa|representa|consiste|"
    r"define-se|refere-se|corresponde|serve para|permite)\b",
    re.IGNORECASE)

# Marcas de trecho denso: formula, simbolo matematico, termo muito longo.
_RE_DENSO = re.compile(r"[∫∑√±≤≥≠∞π=<>]|\b\w{14,}\b")

_PALAVRAS_POR_MINUTO = 200.0

# Token colado a marca de caminho ou de prompt de shell, de qualquer um dos
# dois lados. O lado direito importa tanto quanto o esquerdo: `tiago_luiz`
# so se revela projeto pelo `@` que vem DEPOIS (`tiago_luiz@ubuntu:~/...$`),
# e olhando so a esquerda ele passava por conceito com 0% de contexto.
_MARCAS_CAMINHO = r"[~/\\$@:]"


def _em_contexto_de_caminho(texto_baixo, chave):
    alvo = re.escape(chave)
    return bool(re.search(
        rf"(?:{_MARCAS_CAMINHO}[\w\-\.]*{alvo})|(?:{alvo}[\w\-\.]*{_MARCAS_CAMINHO})",
        texto_baixo))


def _classificar_entidade(nome, sinais):
    """De que ESPECIE e este token, pelas medidas do proprio livro.

    Devolve "" quando o token nao e entidade nenhuma -- palavra comum que so
    foi capturada por comecar uma oracao.
    """
    total = max(1, sinais["vezes"])
    chave = nome.lower()

    if chave in _FRAMEWORKS:
        return "framework"
    # Palavra simples capitalizada que tambem vive em minuscula no livro nao
    # e nome proprio: e o "Vamos" de "Vamos criar o model". Nome de
    # tecnologia praticamente nunca aparece minusculo.
    #
    # As duas contagens sao de OCORRENCIAS, nao de blocos. Comparar ocorrencias
    # minusculas com numero de blocos -- o erro da primeira versao -- misturava
    # unidades e nunca descartava nada.
    if (_RE_PALAVRA_SIMPLES.fullmatch(nome)
            and sinais.get("minusculo_global", 0) > sinais.get("maiusculo", 0)):
        return ""
    if _RE_ARQUIVO.search(nome):
        return "arquivo"
    # Nome do projeto de exemplo, do diretorio ou do usuario do autor.
    if sinais["caminho"] / total >= _FRACAO_CAMINHO:
        # Usuario de shell aparece SEMPRE em prompt; diretorio aparece as
        # vezes no texto. A distincao nao muda o peso, mas muda o rotulo.
        return "pessoa" if sinais["caminho"] == sinais["vezes"] else "projeto"
    # Vive nas listagens e quase nunca no texto que explica: e simbolo de
    # codigo (parametro, metodo), nao um assunto do livro.
    if sinais["prosa"] / total < _FRACAO_PROSA_CONCEITO:
        return "simbolo"
    # snake_case em prosa ainda e mais simbolo que assunto; CamelCase em prosa
    # e o padrao de nome de classe, que num livro tecnico E o assunto.
    if "_" in nome and not sinais["definido"]:
        return "simbolo"
    return "conceito"


def _define_o_que(texto, conceitos):
    """Conceitos que ESTE bloco define, e nao apenas menciona."""
    definidos = []
    conhecidos = {c.lower() for c in conceitos}
    for achado in _RE_DEFINICAO.findall(texto or ""):
        alvo = achado.strip(" .,;:")
        if len(alvo) < 3:
            continue
        # Vale se for um conceito do proprio bloco ou um identificador tecnico.
        if alvo.lower() in conhecidos or _RE_IDENTIFICADOR.match(alvo):
            if alvo not in definidos:
                definidos.append(alvo)
    return definidos[:4]


def _dificuldade(texto):
    """1 (leve) a 5 (denso). Regras, nao modelo -- e roda por bloco."""
    palavras = texto.split()
    if not palavras:
        return 1
    marcas = len(_RE_DENSO.findall(texto))
    codigo = len(_RE_CODIGO.findall(texto))
    media = sum(len(p) for p in palavras) / len(palavras)
    pontos = 1.0
    pontos += min(2.0, marcas / 6.0)          # formula e termo comprido
    pontos += min(1.0, codigo / 10.0)         # sintaxe
    if media > 6.5:                            # vocabulario longo
        pontos += 1.0
    return max(1, min(5, int(round(pontos))))


def _segundos_de_leitura(texto):
    return int(len(texto.split()) / _PALAVRAS_POR_MINUTO * 60) or 1


# ================================================================== O INDICE
class IndiceLivro:
    """Blocos + indice invertido de um livro, com busca e persistencia."""

    def __init__(self, id_livro):
        self.id_livro = id_livro
        self.blocos = []
        self.invertido = {}          # termo -> {id_bloco: repeticoes}
        self.paginas_lidas = set()
        self.capitulos = {}          # numero da primeira pagina -> titulo
        # conceito -> {conceito vizinho: quantas vezes apareceram juntos}.
        # Nao chega a ser um grafo de conhecimento; e uma tabela de
        # co-ocorrencia, que ja basta para expandir busca e sugerir assunto
        # relacionado sem custo de modelo.
        self.relacoes = {}
        self.rotulos = {}            # chave normalizada -> como aparece no livro
        # palavra comum -> {conceito: vezes que apareceram no mesmo bloco}.
        # Sem esta ponte a expansao so funcionava para quem ja escrevia o nome
        # tecnico: "consulta no banco" nao puxava QuerySet, porque "consulta"
        # nunca e um conceito. E justamente quem NAO sabe o termo que precisa
        # da expansao.
        self.termo_conceito = {}
        # Medidas cruas por token, usadas para classificar a especie no fim.
        self.sinais = {}
        # Quantas vezes cada palavra aparece MINUSCULA no livro inteiro. E a
        # visao global que falta ao contador por bloco: "Vamos" so se revela
        # palavra comum quando se ve que "vamos" minusculo aparece cem vezes
        # em blocos onde a versao capitalizada nunca apareceu.
        self.freq_minuscula = {}
        # chave normalizada -> Conceito. A "wikipedia do livro".
        self.entidades = {}
        self.total_paginas = 0
        self.concluido = False

    # ------------------------------------------------------------- construcao
    def definir_capitulos(self, sumario):
        """Recebe o get_toc() do PyMuPDF: [[nivel, titulo, pagina], ...]."""
        for entrada in sumario or []:
            try:
                titulo, pagina = entrada[1], entrada[2]
            except (IndexError, ValueError):
                continue
            if titulo and pagina:
                self.capitulos[int(pagina) - 1] = str(titulo)[:80]

    def _capitulo_da_pagina(self, numero):
        if not self.capitulos:
            return ""
        candidatas = [p for p in self.capitulos if p <= numero]
        return self.capitulos[max(candidatas)] if candidatas else ""

    def indexar_pagina(self, numero, texto):
        """Acrescenta uma pagina ao indice. Repetir a mesma pagina nao duplica."""
        if numero in self.paginas_lidas:
            return 0
        self.paginas_lidas.add(numero)

        capitulo = self._capitulo_da_pagina(numero)
        novos = 0
        for corpo in _partir_em_blocos(texto):
            conceitos = _conceitos_do_bloco(corpo)
            bloco = BlocoIndice(
                id=len(self.blocos), texto=corpo, pagina=numero,
                capitulo=capitulo, tipo=_tipo_do_bloco(corpo),
                conceitos=conceitos,
                dificuldade=_dificuldade(corpo),
                segundos=_segundos_de_leitura(corpo),
                define=_define_o_que(corpo, conceitos))
            self.blocos.append(bloco)

            # Sinais por token, para classificar a especie depois. Tem que ser
            # aqui: a especie so aparece olhando o livro INTEIRO, e o texto de
            # cada bloco so passa por aqui uma vez.
            baixo = corpo.lower()
            for palavra in _RE_PALAVRA.findall(corpo):
                if palavra.islower():
                    chave_p = _normalizar(palavra)
                    self.freq_minuscula[chave_p] = self.freq_minuscula.get(chave_p, 0) + 1
            for nome in conceitos:
                sinais = self.sinais.setdefault(
                    nome, {"vezes": 0, "prosa": 0, "caminho": 0, "definido": 0,
                           "paginas": set(), "codigo": set()})
                sinais["vezes"] += 1
                sinais["paginas"].add(numero)
                if bloco.tipo == "codigo":
                    sinais["codigo"].add(numero)
                else:
                    sinais["prosa"] += 1
                if _em_contexto_de_caminho(baixo, nome.lower()):
                    sinais["caminho"] += 1
                # Quantas vezes o token aparece em MINUSCULA. E o que separa
                # um nome proprio de uma palavra comum que calhou de comecar
                # uma oracao: "Django" quase nunca aparece minusculo, "vamos"
                # aparece o tempo todo. Sinal automatico, sem lista de
                # palavras a manter.
                exatas = corpo.count(nome)
                todas = baixo.count(nome.lower())
                sinais["maiusculo"] = sinais.get("maiusculo", 0) + exatas
                sinais["minusculo"] = sinais.get("minusculo", 0) + max(0, todas - exatas)
                if any(nome.lower() in d.lower() for d in bloco.define):
                    sinais["definido"] += 1
                    if not sinais.get("definicao"):
                        sinais["definicao"] = corpo[:280]
                        sinais["pagina_definicao"] = numero

            # Conceitos que aparecem JUNTOS num bloco estao relacionados. E
            # daqui que sai a expansao da busca: quem procura "ForeignKey"
            # tambem quer os trechos de Model e ManyToMany, mesmo sem ter
            # escrito essas palavras.
            for a in conceitos:
                chave_a = _normalizar(a)
                if not chave_a:
                    continue
                vizinhos = self.relacoes.setdefault(chave_a, {})
                for b in conceitos:
                    if a is b:
                        continue
                    chave_b = _normalizar(b)
                    if chave_b and chave_b != chave_a:
                        vizinhos[chave_b] = vizinhos.get(chave_b, 0) + 1
                self.rotulos[chave_a] = a

            # Ponte palavra-comum -> conceito, so para as palavras mais
            # informativas do bloco. Guardar todas incharia o indice sem
            # melhorar nada: as frequentes ("django", "que") ligam tudo a tudo.
            if conceitos:
                comuns = [t for t in dict.fromkeys(_termos(corpo))
                          if len(t) > 4][:8]
                for palavra in comuns:
                    ponte = self.termo_conceito.setdefault(palavra, {})
                    for conceito in conceitos:
                        chave = _normalizar(conceito)
                        if chave and chave != palavra:
                            ponte[chave] = ponte.get(chave, 0) + 1

            contagem = {}
            for termo in _termos(corpo):
                contagem[termo] = contagem.get(termo, 0) + 1
            # Conceito conta em dobro: e o nome do assunto, nao uma palavra
            # qualquer que apareceu no meio da frase.
            for conceito in bloco.conceitos:
                chave = _normalizar(conceito)
                if chave and chave not in _STOPWORDS:
                    contagem[chave] = contagem.get(chave, 0) + 2

            for termo, vezes in contagem.items():
                self.invertido.setdefault(termo, {})[bloco.id] = vezes
            novos += 1
        return novos

    def consolidar(self):
        """Transforma os sinais crus em entidades tipadas.

        Roda uma vez, no fim da indexacao: a especie de um token so aparece
        olhando o livro inteiro. `ForeignKey` visto num bloco isolado e
        indistinguivel de `medicSearch`; visto nos 607, um nunca aparece em
        caminho de arquivo e o outro aparece em 79% das vezes.
        """
        self.entidades = {}
        for nome, sinais in self.sinais.items():
            chave = _normalizar(nome)
            if not chave:
                continue
            sinais["minusculo_global"] = self.freq_minuscula.get(chave, 0)
            tipo = _classificar_entidade(nome, sinais)
            if not tipo:
                continue
            paginas = sorted(sinais["paginas"])
            codigo = sinais["codigo"]
            capitulos = sorted({self._capitulo_da_pagina(p) for p in paginas} - {""})

            # Espalhamento. Manda o NUMERO de paginas distintas, e nao a
            # distancia entre a primeira e a ultima: a versao que pesava a
            # distancia dava densidade 0,66 para "Deus", que aparece duas
            # vezes -- nos agradecimentos e na ultima pagina -- e atravessava
            # o livro inteiro sem ser assunto de nada.
            #
            # O alcance entra so como multiplicador leve: entre dois termos
            # com o mesmo numero de paginas, o que reaparece ao longo do livro
            # e mais estrutural que o que se esgota numa secao.
            alcance = (paginas[-1] - paginas[0]) if len(paginas) > 1 else 0
            fatia_do_livro = alcance / max(1, self.total_paginas)
            presenca = min(1.0, len(paginas) / 8.0)
            densidade = presenca * (0.75 + 0.25 * fatia_do_livro)
            importancia = round(
                densidade * 0.55
                + (0.25 if sinais.get("definicao") else 0.0)
                + PESO_TIPO.get(tipo, 0.5) * 0.20, 4)

            self.entidades[chave] = Conceito(
                nome=nome, tipo=tipo,
                definicao=sinais.get("definicao", ""),
                pagina_definicao=sinais.get("pagina_definicao", -1),
                paginas=paginas[:40],
                exemplos=sorted(codigo)[:20],
                citacoes=[p for p in paginas if p not in codigo][:20],
                relacionados=[r for r, _v in self.relacionados(nome, 6)],
                capitulos=capitulos[:8],
                vezes=sinais["vezes"],
                densidade=round(densidade, 4),
                importancia=importancia)

        # A ponte palavra -> conceito so pode apontar para o que E assunto.
        # Sem esta poda, "consulta no banco" expandia para o diretorio do
        # projeto de exemplo do autor.
        for palavra, destinos in list(self.termo_conceito.items()):
            limpos = {c: v for c, v in destinos.items()
                      if (self.entidades.get(c) and
                          self.entidades[c].tipo in TIPOS_EXPANSIVEIS)}
            if limpos:
                self.termo_conceito[palavra] = limpos
            else:
                del self.termo_conceito[palavra]
        self.sinais = {}          # ja consolidado; nao vai para o disco

    def _pode_expandir(self, chave) -> bool:
        """Se este termo pode puxar outros trechos numa busca por assunto.

        Duas barreiras. A especie ja tira arquivo, projeto e pessoa; a
        DENSIDADE tira o que sobrou -- termos que passam por conceito mas
        vivem num canto so do livro. `password` e `port` sao o caso: pela
        especie sao conceito (aparecem em prosa, sem contexto de caminho),
        mas existem apenas no bloco que configura o banco. Nao sao assunto do
        livro, sao detalhe de uma secao.
        """
        entidade = self.entidades.get(chave)
        if entidade is None:
            return True                    # ainda nao consolidado
        if entidade.tipo not in TIPOS_EXPANSIVEIS:
            return False
        return entidade.densidade >= _DENSIDADE_MINIMA_EXPANSAO

    def conceito(self, nome):
        return self.entidades.get(_normalizar(nome))

    def mapa(self, quantos=40):
        """Os conceitos que sustentam o livro, do mais importante ao menos."""
        uteis = [c for c in self.entidades.values()
                 if c.tipo in TIPOS_EXPANSIVEIS
                 and c.densidade >= _DENSIDADE_MINIMA_EXPANSAO]
        uteis.sort(key=lambda c: c.importancia, reverse=True)
        return uteis[:quantos]

    def principais(self, quantos=25):
        """Os conceitos que mais importam neste livro."""
        so_assunto = [c for c in self.entidades.values()
                      if c.tipo in TIPOS_EXPANSIVEIS]
        so_assunto.sort(key=lambda c: (bool(c.definicao), c.vezes), reverse=True)
        return so_assunto[:quantos]

    @property
    def pronto(self) -> bool:
        return bool(self.blocos)

    def progresso(self) -> float:
        if not self.total_paginas:
            return 0.0
        return min(1.0, len(self.paginas_lidas) / self.total_paginas)

    # ------------------------------------------------------------------ busca
    def buscar(self, pergunta, quantos=10):
        """Blocos mais relevantes: [(bloco, confianca 0..1), ...].

        A confianca aqui e ABSOLUTA, e essa e a decisao mais importante do
        modulo. A versao obvia -- dividir pelo maior score -- daria 1,00 ao
        primeiro colocado sempre, inclusive quando o livro nao fala nada sobre
        a pergunta. O Guardiao que decide se o modelo pode escrever leria esse
        1,00 e liberaria a redacao em cima de trecho irrelevante, que e
        exatamente a alucinacao que se quer evitar.

        Entao a nota mede quanto da PERGUNTA foi encontrado, e nao quanto este
        bloco se destaca dos outros:

          cobertura_idf  quanto do peso informativo da pergunta o bloco cobre.
                         Termo ausente do livro inteiro entra no denominador
                         com peso maximo -- e o sinal mais forte de que a
                         resposta nao esta ali.
          densidade      o quanto o bloco fala DISSO, e nao so menciona.

        Duas etapas, ambas sem IA e auditaveis: TF-IDF acha os candidatos, e o
        reranker por cobertura separa o trecho que explica o assunto do trecho
        que cita a palavra de passagem.
        """
        distintos = list(dict.fromkeys(_termos(pergunta)))
        if not distintos or not self.blocos:
            return []

        total_blocos = len(self.blocos)
        idf_maximo = _log(total_blocos / 1.0) or 1.0

        # Peso informativo de cada termo da pergunta. Ausente do livro = peso
        # cheio, para pesar contra a confianca em vez de ser ignorado.
        pesos = {}
        for termo in distintos:
            ocorrencias = self.invertido.get(termo)
            if not ocorrencias:
                pesos[termo] = idf_maximo
                continue
            # Piso pequeno e positivo: com log(N/(1+df)) puro, um termo presente
            # em (quase) todos os blocos pesava ZERO -- num livro curto, ou
            # quando o assunto aparece em todo trecho, a pergunta caia em
            # "nao encontrei" mesmo com a resposta ali.
            pesos[termo] = max(0.1, _log(total_blocos / (1 + len(ocorrencias))))
        peso_total = sum(pesos.values()) or 1.0

        pontos = {}
        cobertos = {}
        for termo in distintos:
            ocorrencias = self.invertido.get(termo)
            if not ocorrencias or pesos[termo] <= 0:
                continue
            for id_bloco, vezes in ocorrencias.items():
                tamanho = max(1, len(self.blocos[id_bloco].texto))
                # Frequencia normalizada pelo tamanho: um bloco curto que cita
                # o termo duas vezes fala mais dele que um longo com duas.
                tf = vezes / (tamanho / 500.0)
                pontos[id_bloco] = pontos.get(id_bloco, 0.0) + tf * pesos[termo]
                cobertos.setdefault(id_bloco, set()).add(termo)

        if not pontos:
            return []

        resultados = []
        for id_bloco, bruto in pontos.items():
            bloco = self.blocos[id_bloco]
            cobertura = sum(pesos[t] for t in cobertos[id_bloco]) / peso_total
            # 3.0 e a densidade de um bloco que trata mesmo do assunto, medida
            # nos livros da estante. Acima disso nao aumenta a certeza.
            densidade = min(1.0, bruto / (3.0 * peso_total)) if peso_total else 0.0
            confianca = cobertura * 0.75 + densidade * 0.25

            # Reranker 1: o bloco que DEFINE o termo ganha do que so o repete.
            # Sem isto, "como funciona QuerySet" trazia primeiro a listagem de
            # codigo que usa a palavra dezoito vezes, e nao o paragrafo em que
            # o autor explica o que ela e.
            if _define_algum(bloco, distintos):
                confianca += 0.15
            # Reranker 2: peso da ESPECIE dos conceitos do bloco. Um trecho
            # cheio de nomes do projeto de exemplo do autor pontua menos que
            # um sobre o assunto, mesmo casando os mesmos termos.
            confianca *= self._peso_dos_conceitos(bloco)
            resultados.append((bloco, round(min(1.0, confianca), 4)))

        resultados.sort(key=lambda item: item[1], reverse=True)
        return resultados[:quantos]

    def _peso_dos_conceitos(self, bloco):
        """Multiplicador de 0,6 a 1,0 pela especie do que o bloco nomeia."""
        if not bloco.conceitos or not self.entidades:
            return 1.0
        pesos = [PESO_TIPO.get(e.tipo, 0.5)
                 for e in (self.entidades.get(_normalizar(c))
                           for c in bloco.conceitos) if e is not None]
        if not pesos:
            return 1.0
        # Media suavizada: um bloco com um nome de projeto no meio de tres
        # conceitos continua valendo quase tudo.
        return 0.6 + 0.4 * (sum(pesos) / len(pesos))

    # ---------------------------------------------------------- relacionados
    def relacionados(self, conceito, quantos=5):
        """Conceitos que aparecem junto com este, do mais frequente ao menos."""
        vizinhos = self.relacoes.get(_normalizar(conceito)) or {}
        ordenados = sorted(vizinhos.items(), key=lambda kv: kv[1], reverse=True)
        return [(self.rotulos.get(chave, chave), vezes)
                for chave, vezes in ordenados[:quantos]]

    def expandir(self, pergunta, limite=3):
        """Acrescenta a pergunta os conceitos vizinhos dos termos que ela cita.

        E o que faz "consulta no banco" achar QuerySet mesmo que o livro nunca
        use aquela frase: os termos da pergunta puxam os conceitos com que
        convivem no proprio livro. Sem embeddings, sem modelo -- so a tabela
        de co-ocorrencia montada durante a indexacao.
        """
        candidatos = {}
        for termo in dict.fromkeys(_termos(pergunta)):
            # Caminho 1: o termo JA e um conceito -> pega os vizinhos dele.
            for rotulo, vezes in self.relacionados(termo, limite * 2):
                chave = _normalizar(rotulo)
                # So assunto entra na expansao. Nome de arquivo, de projeto e
                # de pessoa continuam achaveis pela busca literal, mas nao
                # podem sequestrar uma pergunta sobre o conteudo do livro.
                if not self._pode_expandir(chave):
                    continue
                if chave:
                    candidatos[chave] = candidatos.get(chave, 0) + vezes
            # Caminho 2: o termo e palavra comum -> pega os conceitos com que
            # ela convive. E o que traduz "consulta" em QuerySet.
            for chave, vezes in (self.termo_conceito.get(termo) or {}).items():
                # A mesma barreira de densidade do caminho 1. Sem ela, `port`
                # e `password` voltavam por aqui: a poda do `consolidar` olha
                # so a especie, e os dois passam por conceito.
                if not self._pode_expandir(chave):
                    continue
                candidatos[chave] = candidatos.get(chave, 0) + vezes

        ordenados = sorted(candidatos.items(), key=lambda kv: kv[1], reverse=True)
        return [chave for chave, _v in ordenados[:limite * 2]]

    # ------------------------------------------------------- busca unificada
    def buscar_unificado(self, consulta, quantos=60):
        """A busca do Ctrl+F: literal, por conceito e expandida, num resultado.

        Havia duas buscas no programa -- a literal, que so achava o que estava
        escrito exatamente assim, e a do indice, que so o assistente usava.
        Para quem procura sao a mesma coisa, e ter duas significava que metade
        do que o livro sabe ficava inacessivel pelo caminho obvio.

        A ordem importa: quem digita uma palavra exata quer VER aquela palavra.
        O literal vem primeiro, sempre; o conceitual entra em seguida,
        rotulado, para quem digitou uma ideia em vez de um termo.
        """
        consulta = (consulta or "").strip()
        if len(consulta) < 2 or not self.blocos:
            return []

        alvo = _normalizar(consulta)
        vistos = set()
        saida = []

        # Relevancia por bloco, para ordenar TAMBEM os achados literais. Sem
        # isto eles saiam na ordem das paginas: procurando "ForeignKey", o
        # primeiro resultado era um bloco sobre ManyToManyField que citava a
        # palavra de passagem, e a definicao vinha depois.
        relevancia = {b.id: s for b, s in self.buscar(consulta, quantos * 3)}

        literais = [b for b in self.blocos if alvo in _normalizar(b.texto)]
        literais.sort(key=lambda b: (relevancia.get(b.id, 0.0),
                                     _define_algum(b, [alvo])), reverse=True)
        for bloco in literais[:quantos]:
            vistos.add(bloco.id)
            saida.append((bloco, 1.0, "literal"))
        if len(saida) >= quantos:
            return saida

        # 2. Conceitual: os mesmos termos, com peso e cobertura.
        for bloco, score in self.buscar(consulta, quantos):
            if bloco.id not in vistos and score >= 0.35:
                vistos.add(bloco.id)
                saida.append((bloco, score, "conceito"))

        # 3. Expandida: conceitos que convivem com o que foi pedido.
        extras = self.expandir(consulta)
        if extras and len(saida) < quantos:
            for bloco, score in self.buscar(" ".join(extras), quantos):
                if bloco.id not in vistos and score >= 0.45:
                    vistos.add(bloco.id)
                    saida.append((bloco, score * 0.8, "relacionado"))

        return saida[:quantos]

    # ------------------------------------------------------------ persistencia
    def caminho(self):
        pasta = user_path("indices")
        os.makedirs(pasta, exist_ok=True)
        return os.path.join(pasta, f"{self.id_livro}.lumina")

    def salvar(self):
        """Grava comprimido. Um livro de mil paginas cabe em poucas centenas
        de KB, e a proxima abertura nao paga a indexacao de novo."""
        try:
            dados = {
                "versao": _VERSAO_INDICE,
                "id_livro": self.id_livro,
                "total_paginas": self.total_paginas,
                "concluido": self.concluido,
                "capitulos": {str(k): v for k, v in self.capitulos.items()},
                "relacoes": self.relacoes,
                "rotulos": self.rotulos,
                "termo_conceito": self.termo_conceito,
                "entidades": {k: asdict(v) for k, v in self.entidades.items()},
                "paginas": sorted(self.paginas_lidas),
                "blocos": [asdict(b) for b in self.blocos],
                # As chaves de bloco viram texto no JSON; a volta desfaz isso.
                "invertido": {t: {str(i): n for i, n in ocor.items()}
                              for t, ocor in self.invertido.items()},
            }
            bruto = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            # Grava ao lado e troca de uma vez: fechar o app no meio da
            # gravacao nao deixa um cache truncado.
            temporario = self.caminho() + ".tmp"
            with open(temporario, "wb") as arquivo:
                arquivo.write(zlib.compress(bruto, 6))
            os.replace(temporario, self.caminho())
            return True
        except Exception as erro:
            print(f"[Indice] Falha ao salvar: {erro}")
            return False

    @classmethod
    def carregar(cls, id_livro):
        indice = cls(id_livro)
        caminho = indice.caminho()
        if not os.path.exists(caminho):
            return None
        try:
            with open(caminho, "rb") as arquivo:
                dados = json.loads(zlib.decompress(arquivo.read()).decode("utf-8"))
            if dados.get("versao") != _VERSAO_INDICE:
                return None
            indice.total_paginas = dados.get("total_paginas", 0)
            indice.concluido = dados.get("concluido", False)
            indice.capitulos = {int(k): v for k, v in (dados.get("capitulos") or {}).items()}
            indice.relacoes = dados.get("relacoes") or {}
            indice.rotulos = dados.get("rotulos") or {}
            indice.termo_conceito = dados.get("termo_conceito") or {}
            indice.entidades = {k: Conceito(**v)
                                for k, v in (dados.get("entidades") or {}).items()}
            indice.paginas_lidas = set(dados.get("paginas") or [])
            indice.blocos = [BlocoIndice(**b) for b in dados.get("blocos") or []]
            indice.invertido = {t: {int(i): n for i, n in ocor.items()}
                                for t, ocor in (dados.get("invertido") or {}).items()}
            return indice if indice.blocos else None
        except Exception as erro:
            print(f"[Indice] Falha ao carregar: {erro}")
            return None

    def apagar(self):
        try:
            caminho = self.caminho()
            if os.path.exists(caminho):
                os.remove(caminho)
        except OSError:
            pass


def _define_algum(bloco, termos):
    """Se o bloco define algum dos termos procurados."""
    if not bloco.define:
        return False
    definidos = " ".join(bloco.define).lower()
    return any(t in definidos for t in termos)


def _log(valor):
    import math
    try:
        return math.log(valor) if valor > 0 else 0.0
    except ValueError:
        return 0.0


# ============================================================ CONSTRUCAO EM 2o PLANO
def construir(documento, id_livro, ao_progredir=None, cancelar=None,
              a_partir_de=0):
    """Indexa o livro inteiro. Devolve o indice pronto.

    Comeca pela pagina onde a pessoa esta lendo e so depois volta ao inicio:
    as perguntas dos proximos minutos sao sobre o que esta na tela agora, e
    esperar mil paginas para poder perguntar sobre a pagina aberta seria
    esperar por nada.
    """
    indice = IndiceLivro(id_livro)
    total = len(documento)
    indice.total_paginas = total
    try:
        indice.definir_capitulos(documento.get_toc())
    except Exception:
        pass

    ordem = list(range(a_partir_de, total)) + list(range(0, a_partir_de))
    inicio = time.time()
    for feitas, numero in enumerate(ordem, 1):
        if cancelar is not None and cancelar.is_set():
            return indice
        try:
            indice.indexar_pagina(numero, texto_da_pagina(documento, numero))
        except Exception:
            continue
        if ao_progredir and (feitas % 40 == 0 or feitas == len(ordem)):
            try:
                ao_progredir(feitas / max(1, len(ordem)))
            except Exception:
                pass

    indice.consolidar()
    indice.concluido = True
    print(f"[Indice] {id_livro}: {len(indice.blocos)} blocos de {total} paginas "
          f"em {time.time() - inicio:.1f}s")
    return indice
