# services/codigo_util.py
"""Utilitarios para os blocos de codigo que o motor de reflow ja separou.

Um bloco de codigo num livro tecnico nao e um paragrafo: o leitor quer contar
linhas para acompanhar a explicacao do autor, quer recolher a funcao gigante
que nao esta lendo agora, e quer copiar o trecho sem levar junto o ">>> " que
o livro imprimiu.

Sao tres servicos independentes, todos sobre texto puro:

  numerar / mapear      -- numeros de linha a esquerda. Como isso desloca todo
                           o texto, `mapear` traduz os deslocamentos do codigo
                           original (onde o realce de sintaxe foi calculado)
                           para o texto numerado.
  regioes_dobraveis /
  dobrar                -- recolhimento de blocos, por indentacao, chaves ou
                           tags.
  para_area_transferencia -- devolve o codigo colavel num editor.

Nada aqui altera o codigo original: as funcoes recebem e devolvem strings, e a
interface decide o que fazer com elas. Numeros de linha sao sempre 1-based,
como o leitor le na tela.
"""
import re

import services.realce as realce

# Separador entre o numero de linha e o codigo. Duas colunas de espaco dao
# respiro sem empurrar o codigo para longe da margem.
_SEPARADOR = "  "

# Reticencia horizontal (U+22EF). Marca a linha recolhida sem parecer que o
# proprio livro escreveu tres pontos ali.
_MARCA_DOBRA = "\u22ef"

# Largura assumida para o tab ao MEDIR indentacao. Serve so para comparar
# niveis entre linhas; o texto exibido continua com o tab original.
_TAB = 4

# Abaixo disto recolher nao compensa: o leitor gasta um clique para esconder
# duas linhas que ja estavam na tela.
_MIN_LINHAS_DOBRA = 3

# Linhas que so fecham o que a anterior abriu ("}", "});", ")", "end").
# Entram na regiao dobrada porque o fecho sozinho na tela nao diz nada.
_RE_SO_FECHAMENTO = re.compile(r"^[\)\]\}>;,\s]*$|^(?:end|fi|done|esac)[;,]?$")

# Tags HTML sem fechamento -- nunca abrem regiao.
_TAGS_VAZIAS = {"br", "img", "input", "meta", "link", "hr", "area", "base",
                "col", "embed", "source", "track", "wbr", "param", "!doctype"}
_RE_TAG = re.compile(r"<(/?)([a-zA-Z!][\w!-]*)([^>]*?)(/?)>", re.DOTALL)
_RE_TAG_FECHA = re.compile(r"</[a-zA-Z][\w-]*>")

# Prompts que os livros imprimem antes do comando. Sao a maior fonte de
# codigo colado que nao roda.
_RE_PROMPT_PYTHON = re.compile(r"^[ \t]*>>>[ \t]?")
_RE_PROMPT_CONTINUA = re.compile(r"^[ \t]*\.\.\.[ \t]?")
_RE_PROMPT_UNIX = re.compile(
    r"^[ \t]*(?:\([\w .\-]+\)[ \t]*)?"          # (venv)
    r"(?:[\w.\-]+@[\w.\-]+(?::[^\s$#]*)?)?"     # usuario@maquina:~/projeto
    r"[ \t]*[$%][ \t]")
_RE_PROMPT_WINDOWS = re.compile(
    r"^[ \t]*(?:\([\w .\-]+\)[ \t]*)?(?:PS[ \t]+)?[A-Za-z]:\\[^>\n]*>[ \t]?")

# Numero de linha impresso a esquerda, com os separadores mais comuns.
_RE_NUM_LINHA = re.compile(r"^[ \t]*(\d{1,5})(?:[ \t]{1,4}|[.):|][ \t]?)")

# Caracteres que o PDF entrega no lugar dos originais e que quebram o codigo
# assim que ele e colado num editor.
_TRADUCAO_COLAGEM = {
    "\u00a0": " ",   # espaco inquebravel
    "\u2007": " ",
    "\u202f": " ",
    "\u200b": "",    # espaco de largura zero
    "\ufeff": "",
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
    "\u2212": "-",   # sinal de menos tipografico
}


# ------------------------------------------------------------------ NUMERACAO

def _prefixos(codigo: str):
    """(linhas, prefixos) -- o prefixo e a coluna de numeros de cada linha."""
    linhas = codigo.split("\n")
    largura = len(str(len(linhas)))
    prefixos = [str(n).rjust(largura) + _SEPARADOR
                for n in range(1, len(linhas) + 1)]
    return linhas, prefixos


def numerar(codigo: str, largura_minima: int = 2) -> tuple:
    """Devolve (texto_com_numeros, faixas_dos_numeros).

    faixas_dos_numeros sao (inicio, fim) em deslocamento de caractere do texto
    devolvido, cobrindo APENAS os digitos -- a interface pinta essas faixas
    numa cor apagada. Os deslocamentos do CODIGO em si tambem mudam; use
    `mapear` para converte-los.
    """
    if not codigo:
        return "", []

    linhas, prefixos = _prefixos(codigo)
    largura = max(largura_minima, len(str(len(linhas))))

    partes, faixas, posicao = [], [], 0
    for numero, linha in enumerate(linhas, start=1):
        rotulo = str(numero).rjust(largura)
        # A faixa comeca depois do enchimento: espaco pintado nao aparece,
        # mas a interface pode querer medir a coluna pelo que e digito.
        inicio = posicao + len(rotulo) - len(str(numero))
        faixas.append((inicio, posicao + len(rotulo)))
        partes.append(rotulo + _SEPARADOR + linha)
        posicao += len(rotulo) + len(_SEPARADOR) + len(linha) + 1  # +1 = "\n"

    return "\n".join(partes), faixas


def mapear(codigo: str, largura_minima: int = 2) -> list:
    """Para cada indice de caractere do codigo original, o indice
    correspondente no texto numerado.

    Necessario porque o realce de sintaxe e calculado sobre o codigo original.
    A lista tem len(codigo) + 1 posicoes: a ultima e o fim do texto, para que
    uma faixa (inicio, fim) possa ser convertida inteira.
    """
    if not codigo:
        return [0]

    linhas = codigo.split("\n")
    largura = max(largura_minima, len(str(len(linhas))))
    salto = largura + len(_SEPARADOR)

    mapa = [0] * (len(codigo) + 1)
    origem = destino = 0
    for indice, linha in enumerate(linhas):
        destino += salto
        for deslocamento in range(len(linha)):
            mapa[origem + deslocamento] = destino + deslocamento
        origem += len(linha)
        destino += len(linha)
        if indice < len(linhas) - 1:
            mapa[origem] = destino          # o proprio "\n"
            origem += 1
            destino += 1
    mapa[len(codigo)] = destino
    return mapa


# -------------------------------------------------------------------- DOBRA

def _indentacoes(linhas: list) -> list:
    """Indentacao de cada linha em colunas; None para linha em branco."""
    medidas = []
    for linha in linhas:
        if not linha.strip():
            medidas.append(None)
            continue
        expandida = linha.expandtabs(_TAB)
        medidas.append(len(expandida) - len(expandida.lstrip()))
    return medidas


def _proxima_util(indentacoes: list, inicio: int) -> int:
    """Indice da proxima linha nao vazia a partir de `inicio`, ou -1."""
    for indice in range(inicio, len(indentacoes)):
        if indentacoes[indice] is not None:
            return indice
    return -1


def _regioes_por_indentacao(linhas: list, indentacoes: list) -> list:
    """Uma linha abre bloco quando a proxima linha util esta mais a direita.

    Vale para Python (dois pontos), mas tambem para chaves e para HTML, desde
    que o texto esteja indentado -- que e como todo livro imprime codigo.
    """
    achados = []
    for topo in range(len(linhas)):
        if indentacoes[topo] is None:
            continue
        seguinte = _proxima_util(indentacoes, topo + 1)
        if seguinte < 0 or indentacoes[seguinte] <= indentacoes[topo]:
            continue

        # O bloco vai ate a ultima linha util que continua mais a direita.
        fim = seguinte
        for indice in range(seguinte, len(linhas)):
            if indentacoes[indice] is None:
                continue
            if indentacoes[indice] <= indentacoes[topo]:
                break
            fim = indice

        # O fecho ("}", ")", "end") volta ao nivel do cabecalho, entao ficaria
        # de fora; sem ele a linha dobrada deixa um fecho orfao na tela.
        posterior = _proxima_util(indentacoes, fim + 1)
        if (posterior >= 0 and indentacoes[posterior] == indentacoes[topo]
                and _RE_SO_FECHAMENTO.match(linhas[posterior].strip())):
            fim = posterior

        achados.append((topo, fim))
    return achados


def _regioes_por_tags(linhas: list) -> list:
    """Casamento de tags, para o HTML que o livro imprimiu sem indentacao."""
    pilha, achados = [], []
    for numero, linha in enumerate(linhas):
        for encontro in _RE_TAG.finditer(linha):
            barra, nome, resto, sozinha = encontro.groups()
            nome = nome.lower()
            if nome in _TAGS_VAZIAS or nome.startswith("!"):
                continue
            if barra:
                # Fecha a tag correspondente e descarta o que ficou aberto no
                # meio: HTML de livro raramente esta bem balanceado.
                for profundidade in range(len(pilha) - 1, -1, -1):
                    if pilha[profundidade][0] == nome:
                        achados.append((pilha[profundidade][1], numero))
                        del pilha[profundidade:]
                        break
            elif not sozinha and not resto.rstrip().endswith("/"):
                pilha.append((nome, numero))
    return achados


def _parece_html(codigo: str) -> bool:
    if len(_RE_TAG_FECHA.findall(codigo)) >= 2:
        return True
    return realce.detectar(codigo) == "html"


def regioes_dobraveis(codigo: str) -> list:
    """Blocos que podem ser recolhidos, por indentacao/chaves.

    Cada item: {"linha_inicio": int, "linha_fim": int, "titulo": str,
                "nivel": int}, com linhas 1-based e ambas inclusivas.
    `titulo` e a primeira linha (o cabecalho que fica visivel quando dobrado).
    `nivel` e quantas regioes envolvem esta.

    So devolve regioes com 3+ linhas -- dobrar duas linhas nao ajuda ninguem.
    """
    if not codigo or "\n" not in codigo:
        return []

    linhas = codigo.split("\n")
    indentacoes = _indentacoes(linhas)
    brutas = _regioes_por_indentacao(linhas, indentacoes)
    if _parece_html(codigo):
        brutas += _regioes_por_tags(linhas)

    # Duas estrategias podem apontar o mesmo cabecalho; fica a mais abrangente,
    # que e a que o leitor espera ver desaparecer ao clicar.
    melhores = {}
    for topo, fim in brutas:
        if fim - topo + 1 < _MIN_LINHAS_DOBRA:
            continue
        if topo not in melhores or fim > melhores[topo]:
            melhores[topo] = fim

    ordenadas = sorted(melhores.items(), key=lambda item: (item[0], -item[1]))
    regioes = []
    for topo, fim in ordenadas:
        nivel = sum(1 for outro, extremo in ordenadas
                    if outro < topo and extremo >= fim)
        regioes.append({
            "linha_inicio": topo + 1,
            "linha_fim": fim + 1,
            "titulo": linhas[topo].rstrip(),
            "nivel": nivel,
        })
    return regioes


def dobrar(codigo: str, dobradas: set) -> tuple:
    """Aplica o recolhimento. `dobradas` sao numeros de linha_inicio.

    Devolve (texto_resultante, linhas_visiveis_originais) -- a segunda lista
    tem um numero de linha original por linha do texto devolvido, para a
    interface saber a que linha do codigo cada linha da tela corresponde.

    Linha dobrada aparece como: "    def foo():  ... (12 linhas)", onde a
    contagem e das linhas escondidas. Numeros que nao abrem regiao dobravel
    sao ignorados, e uma regiao dentro de outra ja dobrada nao aparece duas
    vezes.
    """
    if not codigo:
        return "", []
    if not dobradas:
        linhas = codigo.split("\n")
        return codigo, list(range(1, len(linhas) + 1))

    linhas = codigo.split("\n")
    regioes = {regiao["linha_inicio"]: regiao
               for regiao in regioes_dobraveis(codigo)}

    saida, visiveis = [], []
    numero = 1
    while numero <= len(linhas):
        regiao = regioes.get(numero)
        if regiao and numero in dobradas:
            escondidas = regiao["linha_fim"] - numero
            saida.append("%s%s%s (%d linhas)" % (linhas[numero - 1].rstrip(),
                                                 _SEPARADOR, _MARCA_DOBRA,
                                                 escondidas))
            visiveis.append(numero)
            numero = regiao["linha_fim"] + 1
        else:
            saida.append(linhas[numero - 1])
            visiveis.append(numero)
            numero += 1
    return "\n".join(saida), visiveis


# --------------------------------------------------------------- COPIA LIMPA

def _tem_numeracao(linhas: list) -> bool:
    """So considera numeracao quando ela e regular e crescente.

    Uma linha solta comecando com numero ("2020 foi o ano...") nao pode fazer
    a limpeza comer o inicio do codigo.
    """
    numeros, uteis = [], 0
    for linha in linhas:
        if not linha.strip():
            continue
        uteis += 1
        encontro = _RE_NUM_LINHA.match(linha)
        if encontro:
            numeros.append(int(encontro.group(1)))

    if uteis < _MIN_LINHAS_DOBRA or len(numeros) < uteis * 0.8:
        return False
    return all(depois > antes for antes, depois in zip(numeros, numeros[1:]))


def _sem_prompt(linha: str, repl: bool) -> str:
    """Remove um unico prompt do inicio da linha."""
    if repl:
        # "..." so e continuacao quando o bloco e mesmo uma sessao do REPL;
        # fora disso e o Ellipsis do Python ou um corte do autor.
        for regex in (_RE_PROMPT_PYTHON, _RE_PROMPT_CONTINUA):
            nova = regex.sub("", linha, count=1)
            if nova != linha:
                return nova
    for regex in (_RE_PROMPT_WINDOWS, _RE_PROMPT_UNIX):
        nova = regex.sub("", linha, count=1)
        if nova != linha:
            return nova
    return linha


def para_area_transferencia(codigo: str) -> str:
    """Limpa o codigo para colar num editor: remove numeros de linha se
    houver, remove prompts de terminal ('>>> ', '$ ', 'C:\\> '), normaliza
    espacos finais e tabulacoes.

    As linhas de SAIDA do interpretador ficam -- distinguir resultado de
    codigo exige adivinhar, e apagar linha do usuario e pior que deixar uma
    linha a mais para ele remover.
    """
    if not codigo:
        return ""

    texto = codigo.replace("\r\n", "\n").replace("\r", "\n")
    for origem, destino in _TRADUCAO_COLAGEM.items():
        texto = texto.replace(origem, destino)

    linhas = [linha.expandtabs(_TAB) for linha in texto.split("\n")]

    if _tem_numeracao(linhas):
        linhas = [_RE_NUM_LINHA.sub("", linha, count=1) if linha.strip()
                  else linha for linha in linhas]

    repl = any(_RE_PROMPT_PYTHON.match(linha) for linha in linhas)
    linhas = [_sem_prompt(linha, repl).rstrip() for linha in linhas]

    # A margem do livro vira indentacao falsa; colada num editor ela quebra o
    # primeiro nivel do arquivo inteiro.
    uteis = [linha for linha in linhas if linha.strip()]
    if uteis:
        margem = min(len(linha) - len(linha.lstrip()) for linha in uteis)
        if margem:
            linhas = [linha[margem:] if linha.strip() else linha
                      for linha in linhas]

    while linhas and not linhas[0].strip():
        linhas.pop(0)
    while linhas and not linhas[-1].strip():
        linhas.pop()
    return "\n".join(linhas)
