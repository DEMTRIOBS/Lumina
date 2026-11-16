# ia/roteador.py
"""Quem responde: o livro, a IA, ou os dois -- e com que direito.

A regra da casa: O LUMINA RESPONDE, A IA ESCREVE. A verdade sai do indice do
livro, que e deterministico e auditavel; o modelo entra depois, e so para
redigir o que o indice ja encontrou. Ele nunca pesquisa, nunca completa
lacuna, e nunca e a fonte -- exceto no modo geral, onde isso e dito na cara
do usuario.

Isso existe por uma medicao, nao por gosto. Perguntado sobre algo FORA do
contexto que recebeu, e mandado explicitamente a dizer "nao sei", o modelo de
0,5B levou 50 s para inventar uma classe que nao existe (`AbstractAuthentication
Token`) e entrou em loop repetindo o mesmo paragrafo. Dentro do contexto, o
mesmo modelo respondeu certo em 12 s. A diferenca entre util e perigoso e
inteiramente sobre TER ou NAO TER o material em maos.

O caminho de uma pergunta:

    Pergunta
       |
       v
    Roteador de intencao  (regras, sem IA)
       |
       +--> LIVRO ----> Indice --> Guardiao --> [IA redige] --> Verificador
       |
       +--> GERAL ----> IA responde, rotulada como conhecimento geral
       |
       +--> HIBRIDO --> Indice (o que o livro diz)
                        + IA (o que o livro nao cobre), separados na resposta

O GUARDIAO, sobre a nota do indice (0 a 1, absoluta):

    >= 0,80   o livro cobre a pergunta -> a IA pode redigir
    0,45-0,80 o livro toca no assunto  -> mostra os TRECHOS, sem IA
    <  0,45   o livro nao fala disso   -> nao inventa; oferece o modo geral

O VERIFICADOR fecha a porta que sobra: depois de o modelo escrever, todo
identificador tecnico da resposta (CamelCase, snake_case, ALL_CAPS) e
procurado nos trechos de origem. Se o modelo trouxe nomes que nao estavam la,
a resposta e descartada e os trechos aparecem crus. E o que teria barrado a
`AbstractAuthenticationToken`.
"""
import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ LIMIARES
# Onde o Guardiao corta. Medidos no livro de Django da estante: perguntas
# cobertas ficaram entre 0,60 e 1,00, e as de FORA do livro entre 0,00 e 0,31.
# O vao entre 0,31 e 0,60 e o que da seguranca ao limiar de redacao: mesmo em
# 0,70 sobra o dobro de margem ate a pergunta de fora melhor pontuada.
#
# 0,80 (o primeiro valor testado) era conservador demais na pratica: "para que
# serve o admin do Django", coberto pelo livro, ficou em 0,60 e nunca chegava
# a ser redigido. Com o verificador de saida funcionando, vale ser um pouco
# mais permissivo aqui -- ele e a segunda barreira.
LIMIAR_REDIGIR = 0.70
LIMIAR_TRECHOS = 0.45

# Quantos blocos vao para o modelo. Poucos demais e ele nao tem o que dizer;
# muitos e ele se perde e a resposta demora -- em CPU cada bloco extra custa.
BLOCOS_PARA_REDIGIR = 3
BLOCOS_PARA_MOSTRAR = 4

FRASE_SEM_RESPOSTA = "Nao encontrei essa informacao neste livro."

# --------------------------------------------------------------- ROTEADOR
# Marcas de problema tecnico concreto: erro, comando, stack trace. Nenhuma
# delas pertence ao texto de um livro -- sao do MUNDO do leitor, e o livro
# nao tem como saber o que aconteceu no terminal dele.
_RE_GERAL = re.compile(
    # Error/Exception so como NOME DE CLASSE (maiuscula): "terror jacobino" ou
    # "o que e exception handling?" sao perguntas sobre o livro.
    r"(traceback|(?-i:\b[A-Z]\w*(?:Error|Exception)\b)|(?-i:\bERROR\b)|"
    r"\bfailed\b|\bstack trace\b|"
    r"\bexit code\b|\bcommand not found\b|\bnao (?:funciona|inicia|roda|abre)\b|"
    r"\bnão (?:funciona|inicia|roda|abre)\b|\bdeu (?:erro|pau|ruim)\b|"
    r"\bmeu (?:codigo|código|projeto|terminal|servidor|ambiente)\b|"
    # Ferramenta so conta como LINHA DE COMANDO ("pip install x", "$ git
    # push", "rodei manage.py migrate"): a mera mencao ("como funciona o
    # manage.py migrate?", "o que e um node?") e assunto do livro.
    r"(?:^|\n)\s*\$?\s*(?:python\s+)?(?:pip|npm|yarn|cargo|rustc|docker|git|"
    r"manage\.py|django-admin|composer|gradle|maven)\s+\S|"
    r"\b(?:rodei|executei|rodando|executando|digitei)\b|"
    r"\bcomo (?:resolv|corrig|conserta|arrum))", re.IGNORECASE)

# Marcas de que a pergunta e SOBRE o livro aberto.
_RE_LIVRO = re.compile(
    r"(\bcap[ií]tulo\b|\bp[aá]gina\b|\bno livro\b|\bdo livro\b|\beste livro\b|"
    r"\bsegundo o (?:livro|autor)\b|\bo autor\b|"
    # "o livro fala de X, mas o meu codigo deu erro" e o caso hibrido tipico:
    # sem esta alternativa ele caia todo em GERAL e a metade do livro sumia.
    r"\bo livro (?:fala|diz|explica|mostra|trata|ensina|cita)\b|"
    r"\bonde (?:ele |o livro )?(?:explica|fala|mostra|comenta|trata)\b|"
    r"\bem que parte\b|\bonde (?:est[aá]|aparece)\b|"
    r"\bresum[ae]\b|\bsum[aá]rio\b)",
    re.IGNORECASE)

LIVRO, GERAL, HIBRIDO = "livro", "geral", "hibrido"


@dataclass
class Resposta:
    """O que a interface precisa para mostrar E para rotular a origem."""
    texto: str = ""
    origem: str = LIVRO             # livro | geral | hibrido | nenhum
    confianca: float = 0.0
    paginas: list = field(default_factory=list)
    trechos: list = field(default_factory=list)   # [(pagina, texto)]
    modelo: str = ""
    aviso: str = ""
    # A IA nao foi usada, mas poderia ser. A interface oferece o botao.
    oferecer_geral: bool = False
    # Pergunta reescrita com o assunto herdado da troca anterior ("e como ele
    # funciona?" -> "QuerySet e como ele funciona?"). Fica visivel na resposta
    # para o leitor conferir se o Lumina entendeu do que ele estava falando.
    assunto_herdado: str = ""

    @property
    def rotulo(self):
        return {
            LIVRO: "Resposta do livro",
            GERAL: "Conhecimento geral da IA — nao veio do livro",
            HIBRIDO: "Livro + conhecimento geral da IA",
        }.get(self.origem, "Sem resposta")


def rotear(pergunta: str) -> str:
    """Decide o destino da pergunta SEM consultar modelo nenhum.

    Regra e nao IA porque isto precisa ser instantaneo (roda a cada pergunta),
    previsivel (a pessoa aprende como o programa pensa) e porque as marcas sao
    literais: quem cola um `ModuleNotFoundError` nao esta perguntando sobre o
    capitulo 4.
    """
    texto = pergunta or ""
    geral = bool(_RE_GERAL.search(texto))
    livro = bool(_RE_LIVRO.search(texto))
    if geral and livro:
        return HIBRIDO
    if geral:
        return GERAL
    return LIVRO


# ------------------------------------------------------------- VERIFICADOR
# Identificador tecnico: e o que um modelo pequeno inventa quando nao sabe.
# Palavra comum nao entra aqui de proposito -- o modelo PRECISA parafrasear, e
# exigir que toda palavra venha dos trechos rejeitaria toda resposta boa.
# O `(?![a-z])` no fim do trecho de maiusculas evita casar o prefixo de um
# CamelCase: sem ele, "JWTAuthentication" gerava tambem um falso "JWTA".
_RE_IDENTIFICADOR = re.compile(
    r"\b([A-Z][a-z]+[A-Z]\w*|[a-z]+_[a-z_]{2,}|[A-Z]{3,}(?![a-z])|\w+\(\))")
# Zero tolerancia, e nao "um passa". O caso que motivou o verificador foi UMA
# classe inventada (`AbstractAuthenticationToken`) numa resposta que, no
# resto, soava perfeita -- e e justamente essa que engana. Se o modelo cita um
# nome tecnico que nao esta nos trechos, ou ele inventou ou esta falando de
# outra coisa; nos dois casos a resposta nao serve.
_MAX_INVENTADOS = 0

# Vocabulario tecnico do mundo, nao simbolo do livro. Sao nomes proprios que o
# modelo tem todo direito de usar ao explicar ("os templates misturam HTML com
# JavaScript") mesmo que a palavra exata nao esteja no trecho recuperado.
#
# Sem esta lista o verificador reprovava resposta boa: uma explicacao correta
# sobre templates foi descartada porque citava "JavaScript". O verificador
# existe para pegar CLASSE INVENTADA, nao para proibir o modelo de nomear a
# linguagem de que o proprio livro trata.
_VOCABULARIO_COMUM = {
    "javascript", "typescript", "coffeescript", "postgresql", "mysql",
    "sqlite", "mongodb", "mariadb", "oracle", "github", "gitlab", "bitbucket",
    "powershell", "bash", "linux", "windows", "macos", "ubuntu", "debian",
    "docker", "kubernetes", "nginx", "apache", "django", "flask", "fastapi",
    "python", "java", "kotlin", "swift", "rust", "golang", "csharp",
    "html", "css", "json", "yaml", "xml", "http", "https", "rest", "api",
    "sql", "orm", "url", "uri", "utf", "ascii", "css3", "html5", "ajax",
    "jquery", "react", "vue", "angular", "nodejs", "npm", "pip", "venv",
    "ide", "cpu", "gpu", "ram", "pdf", "csv", "crud", "mvc", "mvt", "id",
}


def verificar(resposta: str, fontes: str):
    """(ok, inventados). Confere se a resposta so cita nomes que estavam nas fontes.

    Fecha a porta que o Guardiao deixa aberta: mesmo com bons trechos em maos,
    um modelo pequeno acrescenta nomes plausiveis que nunca existiram. Aqui
    todo identificador tecnico da resposta e procurado nos trechos de origem.
    """
    if not resposta:
        return False, []
    fonte_normal = _normalizar_para_busca(fontes)
    inventados = []
    vistos = set()
    for achado in _RE_IDENTIFICADOR.findall(resposta):
        nome = achado.strip("()")
        chave = nome.lower()
        if len(nome) < 4 or chave in vistos or chave in _VOCABULARIO_COMUM:
            continue
        vistos.add(chave)
        if chave not in fonte_normal:
            inventados.append(nome)
    return len(inventados) <= _MAX_INVENTADOS, inventados


def _normalizar_para_busca(texto):
    return re.sub(r"[^\w]", " ", (texto or "").lower())


# ------------------------------------------------------------------ MONTAGEM
def _prompt_do_livro(pergunta, blocos):
    partes = [
        "Responda a pergunta usando SOMENTE os trechos abaixo, retirados do "
        "livro que o leitor esta lendo.",
        f'Se a resposta nao estiver nos trechos, responda exatamente: "{FRASE_SEM_RESPOSTA}"',
        "Nao acrescente informacao que nao esteja nos trechos.",
        "",
        "TRECHOS:",
    ]
    for bloco, _score in blocos:
        partes.append(f"[pagina {bloco.pagina + 1}] {bloco.texto}")
    partes += ["", f"Pergunta: {pergunta}", "Resposta:"]
    return "\n".join(partes)


def _texto_dos_trechos(blocos):
    return "\n".join(b.texto for b, _s in blocos)


def _mostrar_trechos(blocos, confianca, aviso=""):
    """Resposta sem IA: os proprios trechos, que sao a fonte de qualquer jeito."""
    linhas = ["O livro toca neste assunto nestes trechos:", ""]
    for bloco, _score in blocos[:BLOCOS_PARA_MOSTRAR]:
        rotulo = f"pagina {bloco.pagina + 1}"
        if bloco.capitulo:
            rotulo += f" · {bloco.capitulo}"
        linhas.append(f"[{rotulo}]")
        linhas.append(bloco.texto.strip())
        linhas.append("")
    return Resposta(
        texto="\n".join(linhas).strip(),
        origem=LIVRO, confianca=confianca,
        paginas=[b.pagina for b, _s in blocos[:BLOCOS_PARA_MOSTRAR]],
        trechos=[(b.pagina, b.texto) for b, _s in blocos[:BLOCOS_PARA_MOSTRAR]],
        aviso=aviso, oferecer_geral=True)


# =================================================================== PRINCIPAL
# ------------------------------------------------------------------ CONVERSA
# Quantas trocas anteriores entram no contexto. Curto de proposito: o que
# resolve "e como ELE funciona?" e a pergunta imediatamente anterior, e cada
# troca extra e mais texto para o modelo processar em CPU.
_MEMORIA_TROCAS = 3

# Pergunta que nao se sustenta sozinha: comeca por conectivo ou tem pronome
# solto e nenhum substantivo proprio. E o caso em que herdar o assunto da
# pergunta anterior e a unica leitura possivel.
_RE_CONTINUACAO = re.compile(
    r"^\s*(e\b|mas\b|ent[aã]o\b|ai\b|a[ií]\b|tamb[eé]m\b|por qu[eê]|"
    r"como assim|tem exemplo|d[aá] um exemplo|explica melhor|e o\b|e a\b)",
    re.IGNORECASE)
_RE_PRONOME = re.compile(
    r"\b(ele|ela|isso|isto|aquilo|esse|essa|este|esta|dele|dela|"
    r"o mesmo|a mesma)\b", re.IGNORECASE)

# Palavras que compoem a PERGUNTA e nao o assunto dela. Herdar "exemplo" ou
# "funciona" para a proxima busca so adiciona ruido.
_PALAVRAS_DE_PERGUNTA = {
    "como", "quando", "onde", "quem", "qual", "quais", "porque", "porquê",
    "para", "sobre", "isso", "isto", "esse", "essa", "este", "esta", "aquilo",
    "existe", "funciona", "funcionam", "exemplo", "exemplos", "explica",
    "explicar", "melhor", "tambem", "também", "entao", "então", "mesmo",
    "usar", "serve", "diferenca", "diferença", "entre", "pode", "posso",
    "fazer", "faco", "faço", "tem", "tenho", "dele", "dela", "mais",
}


class Conversa:
    """Historico curto de uma conversa com UM livro.

    Existe para o caso mais comum de uma conversa de verdade: a segunda
    pergunta quase nunca repete o assunto. "O que e QuerySet?" seguido de "e
    como ele funciona?" -- sem historico, a segunda vira uma busca por "como
    funciona", que nao acha nada. Com historico, ela busca por
    "QuerySet como funciona" e acha.

    Guarda o ASSUNTO, nao a conversa inteira: o que o indice precisa e de
    termos para buscar, e arrastar respostas antigas para dentro da busca so
    traria ruido.
    """

    def __init__(self):
        self.trocas = []             # [(pergunta, assunto)]

    def limpar(self):
        self.trocas.clear()

    def registrar(self, pergunta, assunto):
        self.trocas.append((pergunta, assunto))
        if len(self.trocas) > _MEMORIA_TROCAS:
            self.trocas.pop(0)

    @staticmethod
    def assunto_de(texto):
        """So as palavras de CONTEUDO -- o assunto, nao a pergunta inteira.

        Guardar a pergunta completa e arrasta-la para a proxima parecia obvio
        e degrada a busca a cada troca: "O que e ForeignKey? E como ele
        funciona? Tem exemplo?" acumula "que", "como", "exemplo", que diluem a
        cobertura e derrubam a confianca de 1,00 para 0,55 em duas perguntas.
        O que a busca precisa herdar e o SUBSTANTIVO, nao a frase.
        """
        conteudo = []
        for palavra in re.findall(r"\b[\w\-]{4,}\b", texto or ""):
            chave = palavra.lower()
            if chave in _PALAVRAS_DE_PERGUNTA or chave in conteudo:
                continue
            conteudo.append(palavra)
        return " ".join(conteudo[:3])

    def precisa_de_contexto(self, pergunta) -> bool:
        texto = (pergunta or "").strip()
        if not texto or not self.trocas:
            return False
        # Pergunta curta que comeca por conectivo, ou que usa pronome sem
        # dizer de quem: os dois casos so fazem sentido com o que veio antes.
        if _RE_CONTINUACAO.match(texto):
            return True
        return bool(_RE_PRONOME.search(texto)) and len(texto.split()) <= 8

    def completar(self, pergunta):
        """Devolve a pergunta com o assunto herdado, quando fizer falta."""
        if not self.precisa_de_contexto(pergunta):
            return pergunta
        anterior = self.trocas[-1][1]
        return f"{anterior} {pergunta}".strip() if anterior else pergunta

    def assunto_atual(self):
        return self.trocas[-1][1] if self.trocas else ""


def responder(pergunta, indice, gerente_ia, modo="auto", pagina_atual=None,
              conversa=None):
    """Responde uma pergunta e diz DE ONDE veio a resposta.

    `modo` aceita "auto" (o roteador decide), "livro" ou "geral" -- a interface
    expoe os tres, e o padrao e automatico porque ninguem deveria precisar
    pensar nisso antes de perguntar.
    """
    pergunta = (pergunta or "").strip()
    if len(pergunta) < 3:
        return Resposta(texto="Escreva a pergunta.", origem="nenhum")

    # "E como ele funciona?" so quer dizer alguma coisa depois da pergunta
    # anterior. A busca usa a versao completa; a interface mostra o que a
    # pessoa escreveu.
    consulta = conversa.completar(pergunta) if conversa is not None else pergunta
    herdou = consulta != pergunta

    destino = rotear(consulta) if modo == "auto" else modo
    if destino == GERAL:
        resposta = _responder_geral(consulta, gerente_ia)
    elif destino == HIBRIDO:
        resposta = _responder_hibrido(consulta, indice, gerente_ia)
    else:
        resposta = _responder_pelo_livro(consulta, indice, gerente_ia)

    if herdou:
        resposta.assunto_herdado = consulta
    if conversa is not None:
        # Guarda o ASSUNTO da consulta efetiva, nao a frase. Assim uma cadeia
        # de tres perguntas mantem o fio sem acumular ruido de pergunta.
        assunto = conversa.assunto_de(consulta) or conversa.assunto_atual()
        conversa.registrar(pergunta, assunto)
    return resposta


def _responder_pelo_livro(pergunta, indice, gerente_ia):
    if indice is None or not indice.pronto:
        return Resposta(
            texto="O indice deste livro ainda esta sendo montado. "
                  "Tente de novo em alguns segundos.",
            origem="nenhum", oferecer_geral=True)

    achados = indice.buscar(pergunta, BLOCOS_PARA_REDIGIR)
    confianca = achados[0][1] if achados else 0.0

    # --- Guardiao, faixa baixa: o livro nao fala disso.
    if confianca < LIMIAR_TRECHOS:
        aviso = ""
        if not indice.concluido:
            aviso = (f"O indice esta em {int(indice.progresso() * 100)}% — "
                     "pode ser que a resposta esteja numa parte ainda nao lida.")
        return Resposta(
            texto=FRASE_SEM_RESPOSTA, origem="nenhum", confianca=confianca,
            aviso=aviso, oferecer_geral=True)

    # --- Guardiao, faixa do meio: mostra o que achou, sem deixar a IA redigir.
    if confianca < LIMIAR_REDIGIR:
        return _mostrar_trechos(
            achados, confianca,
            aviso="Trechos mostrados como estao no livro: a busca nao teve "
                  "certeza suficiente para deixar a IA reescrever.")

    # --- Faixa alta: ha material. A IA REDIGE, nao pesquisa.
    provedor = _provedor_local(gerente_ia)
    if provedor is None:
        return _mostrar_trechos(achados, confianca,
                                aviso="Sem IA local disponivel para redigir.")

    nome, cliente = provedor
    try:
        bruto = cliente.ask_question(_texto_dos_trechos(achados), pergunta)
    except Exception as erro:
        return _mostrar_trechos(achados, confianca,
                                aviso=f"A IA falhou ao redigir ({erro}).")

    if not bruto or FRASE_SEM_RESPOSTA.lower()[:24] in bruto.lower():
        return _mostrar_trechos(
            achados, confianca,
            aviso="A IA nao encontrou a resposta nos trechos; eles vao crus.")

    # --- Verificador: a resposta so pode citar o que estava nos trechos.
    ok, inventados = verificar(bruto, _texto_dos_trechos(achados))
    if not ok:
        return _mostrar_trechos(
            achados, confianca,
            aviso="Resposta da IA descartada: ela citou "
                  f"{', '.join(inventados[:3])}, que nao aparece nos trechos. "
                  "Mostrando o texto do livro.")

    paginas = sorted({b.pagina for b, _s in achados})
    return Resposta(texto=bruto.strip(), origem=LIVRO, confianca=confianca,
                    paginas=paginas,
                    trechos=[(b.pagina, b.texto) for b, _s in achados],
                    modelo=nome)


def _responder_geral(pergunta, gerente_ia):
    """Conhecimento geral: aqui a IA E a fonte, e isso vai dito no rotulo.

    Usa a fila inteira, e nao so o modelo local: quando nao ha livro por tras,
    o modelo E a fonte da verdade, e um modelo de nuvem configurado responde
    muito melhor que um de 0,5B. No modo livro isso nao importava, porque la
    ele so redige.
    """
    if gerente_ia is None:
        return Resposta(texto="Nenhum provedor de IA disponivel.", origem="nenhum")
    try:
        texto = gerente_ia.executar_acao("Pergunta geral", "", pergunta)
    except Exception as erro:
        return Resposta(texto=f"A IA nao conseguiu responder: {erro}",
                        origem="nenhum")
    return Resposta(
        texto=texto, origem=GERAL,
        aviso="Isto NAO veio do livro aberto. E conhecimento geral do modelo, "
              "e modelos pequenos erram detalhes com confianca.")


def _responder_hibrido(pergunta, indice, gerente_ia):
    """O caso real: "o livro fala de X, mas meu comando deu erro Y".

    As duas metades ficam separadas e rotuladas. Misturar as duas num texto so
    seria o pior dos mundos: o leitor deixaria de saber o que o autor escreveu
    e o que o modelo imaginou.
    """
    do_livro = _responder_pelo_livro(pergunta, indice, gerente_ia)
    geral = _responder_geral(pergunta, gerente_ia)

    partes = []
    if do_livro.origem == LIVRO and do_livro.texto:
        paginas = ", ".join(str(p + 1) for p in do_livro.paginas[:4])
        partes.append(f"NO LIVRO (paginas {paginas}):\n{do_livro.texto}")
    else:
        partes.append("NO LIVRO:\nO livro aberto nao trata desta parte.")
    partes.append(f"\nCONHECIMENTO GERAL DA IA (nao veio do livro):\n{geral.texto}")

    return Resposta(
        texto="\n".join(partes), origem=HIBRIDO,
        confianca=do_livro.confianca, paginas=do_livro.paginas,
        trechos=do_livro.trechos,
        aviso="A segunda metade nao saiu do livro; confira antes de aplicar.")


def _provedor_local(gerente_ia):
    if gerente_ia is None:
        return None
    try:
        return gerente_ia._provedor_local()
    except Exception:
        return None
