# services/contexto.py
"""Motor de contexto: adapta a leitura ao que voce esta fazendo ao lado.

TUDO ACONTECE NA SUA MAQUINA. Nada e enviado para lugar nenhum, nada e
gravado alem de um contador local de preferencias, e o recurso nasce
DESLIGADO -- ler titulo de janela e informacao sensivel, entao tem que ser
escolha explicita, com opcao de desligar a qualquer momento.

O SINAL MAIS FORTE E O CONTEUDO DA JANELA AO LADO, e ele nao precisa de IA.
Ligando "ler o texto das janelas ao lado", o modulo `tela` extrai o texto real
daquela janela pela arvore de acessibilidade do Windows -- sem OCR, sem
capturar imagem -- e as regras daqui classificam o assunto. Medido em seis
trechos reais: 5 acertos em 0,6 ms. O modelo de 0,5B, com o MESMO texto,
acertou 2 e levou 400 ms. Reconhecer `def`, `SELECT` ou a palavra "integral" e
trabalho de expressao regular; pedir isso a uma rede neural e pagar caro por
um resultado pior.

ONDE O MODELO DE LINGUAGEM ENTRA:
o caminho normal nao usa modelo nenhum. Regras resolvem o caso nomeavel em
menos de 5 ms; consultar um LLM para descobrir que "Code.exe" e programacao
seria gastar um segundo para saber o que uma tabela ja sabia. Sao quatro
niveis, e cada um so existe porque o anterior falha em algum caso concreto:

  Nivel 1  regras     processo e titulo da janela        ~1-5 ms
  Nivel 2  perfil     o que o livro aberto ja revelou    ~0 ms (ja calculado)
  Nivel 3  memoria    o que voce escolheu antes          ~0 ms
  Nivel 4  modelo     so quando os tres empatam          ~0,2 s, IA local

O nivel 3 e o que faz o acerto subir com o tempo: quando voce corrige a
sugestao, a correcao vira preferencia gravada para aquela combinacao -- e o
nivel 4 para de ser consultado naquela situacao.

O QUE ESPERAR DO NIVEL 4, MEDIDO E NAO SUPOSTO:
com o Qwen2.5-0.5B (o maior que cabe confortavelmente em 8 GB), a escolha
entre quatro opcoes acertou 2 em 4, e entre duas, 3 em 6. Ha vies para
"programacao". Ou seja: e um palpite, e o codigo trata como palpite --
a confianca que ele soma e pequena, o motivo exibido diz de onde veio, e
nada e aplicado sem voce clicar. Esse era o ponto que faltava quando este
nivel chegou a ser removido: a barra de contexto nunca aplicou nada sozinha,
e toda aplicacao pode ser desfeita. Um palpite oferecido custa uma olhada;
um palpite imposto custaria a pagina.

O modelo consultado e SEMPRE o local -- chave de API nao entra aqui. Isto
roda a cada poucos segundos enquanto se le, e mandar o titulo das suas
janelas para um servidor a cada volta do monitor seria indefensavel.
"""
import os
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------- NIVEL 1
# Executavel em primeiro plano -> tipo de renderizacao. Nao precisa de IA
# nenhuma: o programa que a pessoa esta usando ja diz quase tudo.
REGRAS_PROCESSO = {
    "code.exe": "programacao",
    "devenv.exe": "programacao",
    "pycharm64.exe": "programacao",
    "idea64.exe": "programacao",
    "webstorm64.exe": "programacao",
    "rider64.exe": "programacao",
    "clion64.exe": "programacao",
    "sublime_text.exe": "programacao",
    "windowsterminal.exe": "programacao",
    "powershell.exe": "programacao",
    "cmd.exe": "programacao",
    "winword.exe": "prosa",
    "libreoffice.exe": "prosa",
    "soffice.bin": "prosa",
    "acrobat.exe": "tecnico",
    "acrord32.exe": "tecnico",
    "excel.exe": "tecnico",
    "powerpnt.exe": "tecnico",
    "obsidian.exe": "prosa",
    "notion.exe": "prosa",
}

# Pistas no TITULO da janela. Pegam o caso que o processo sozinho nao pega:
# o navegador serve para tudo, entao quem decide e o que esta na aba.
PISTAS_TITULO = (
    # (expressao, tipo_render, categoria, linguagem)
    (r"\b(python|django|flask|pandas|numpy)\b", "programacao", "Computacao", "python"),
    (r"\b(javascript|typescript|react|vue|angular|node)\b", "programacao",
     "Computacao", "javascript"),
    (r"\b(java|spring|kotlin)\b", "programacao", "Computacao", "java"),
    (r"\b(c#|csharp|\.net|dotnet)\b", "programacao", "Computacao", "csharp"),
    (r"\b(sql|postgres|mysql|banco de dados|database)\b", "programacao",
     "Computacao", "sql"),
    (r"\b(html|css|front-?end|web)\b", "programacao", "Computacao", "html"),
    (r"\b(git|github|docker|linux|devops)\b", "programacao", "Computacao", None),
    (r"\b(c[aá]lculo|integral|derivada|[aá]lgebra|matem[aá]tica|geometria|"
     r"trigonometria|equa[cç][aã]o)\b", "matematica", "Matematica", None),
    (r"\b(direito|jur[ií]dic|constitucional|penal|processual|concurso p[uú]blico|"
     r"oab|legisla[cç][aã]o)\b", "tecnico", "Direito", None),
    (r"\b(medicina|anatomia|fisiologia|farmacologia|enfermagem)\b", "tecnico",
     "Medicina", None),
    (r"\b(engenharia|resist[eê]ncia dos materiais|termodin[aâ]mica)\b", "tecnico",
     "Engenharia", None),
    (r"\b(hist[oó]ria|guerra mundial|revolu[cç][aã]o|imp[eé]rio)\b", "prosa",
     "Historia", None),
    (r"\b(geografia|cartografia|geopol[ií]tica)\b", "tecnico", "Geografia", None),
    (r"\b(literatura|romance|conto|poesia|liter[aá]ri)\b", "prosa", "Literatura", None),
    (r"\b(f[ií]sica|qu[ií]mica|biologia)\b", "tecnico", "Ciencias", None),
)

# Extensoes de arquivo visiveis no titulo do editor -> linguagem. E o "olhar
# o contexto do projeto" sem precisar ler disco nenhum: o proprio editor
# escreve o nome do arquivo aberto na barra de titulo.
EXTENSOES = {
    ".py": ("programacao", "Computacao", "python"),
    ".js": ("programacao", "Computacao", "javascript"),
    ".ts": ("programacao", "Computacao", "typescript"),
    ".jsx": ("programacao", "Computacao", "javascript"),
    ".tsx": ("programacao", "Computacao", "typescript"),
    ".java": ("programacao", "Computacao", "java"),
    ".cs": ("programacao", "Computacao", "csharp"),
    ".cpp": ("programacao", "Computacao", "cpp"),
    ".c": ("programacao", "Computacao", "c"),
    ".go": ("programacao", "Computacao", "go"),
    ".rs": ("programacao", "Computacao", "rust"),
    ".php": ("programacao", "Computacao", "php"),
    ".rb": ("programacao", "Computacao", "ruby"),
    ".sql": ("programacao", "Computacao", "sql"),
    ".html": ("programacao", "Computacao", "html"),
    ".css": ("programacao", "Computacao", "css"),
    ".json": ("programacao", "Computacao", "json"),
    ".yaml": ("programacao", "Computacao", "yaml"),
    ".yml": ("programacao", "Computacao", "yaml"),
    ".docx": ("prosa", "", None),
    ".doc": ("prosa", "", None),
}

_RE_EXTENSAO = re.compile(r"[\w\-]+(\.[a-z]{1,5})\b", re.IGNORECASE)

# Modos que a classificacao por conteudo sabe distinguir. Nao inclui hq nem
# revista: aquilo se decide pela diagramacao do LIVRO, nunca pelo que esta na
# janela ao lado.
RENDERIZACOES_CONHECIDAS = ("prosa", "tecnico", "programacao", "matematica")


@dataclass
class Janela:
    titulo: str = ""
    processo: str = ""
    # Identificador da janela no Windows. Serve para saber que a janela AO
    # LADO mudou mesmo quando o programa e o mesmo (duas abas do navegador em
    # janelas diferentes, dois projetos no mesmo editor).
    alca: int = 0

    def __bool__(self):
        return bool(self.titulo or self.processo)

    @property
    def assinatura(self):
        """Identidade estavel da janela, sem o titulo.

        O titulo muda a cada arquivo aberto no editor; usa-lo faria o leitor
        perguntar de novo a cada troca de aba, o que seria insuportavel.
        """
        return (self.processo, self.alca)


@dataclass
class Sugestao:
    render: str = ""
    categoria: str = ""
    linguagem: str = ""
    confianca: float = 0.0
    motivo: str = ""
    janela: Janela = field(default_factory=Janela)
    # Cores lidas da mesma janela que gerou a sugestao. Andam juntas de
    # proposito: o programa ao lado define TANTO o modo quanto a aparencia,
    # e separar isso obrigava o usuario a fazer a mesma escolha duas vezes.
    cores: tuple = None
    # As regras nao se decidiram entre dois modos. Quem le esta marca sabe
    # que vale perguntar ao modelo -- e que a resposta sera um palpite.
    empatado: bool = False
    # O modelo local participou desta sugestao.
    palpite: bool = False
    # A sugestao saiu do CONTEUDO da janela ao lado, e nao so do nome dela.
    leu_tela: bool = False

    def vale(self, minimo: float = 0.55) -> bool:
        return bool(self.render) and self.confianca >= minimo


# ==================================================== LEITURA DA JANELA
def janela_em_primeiro_plano() -> Janela:
    """Titulo e executavel da janela ativa. So Windows, so leitura local."""
    if os.name != "nt":
        return Janela()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        alca = user32.GetForegroundWindow()
        if not alca:
            return Janela()

        tamanho = user32.GetWindowTextLengthW(alca) + 1
        buffer = ctypes.create_unicode_buffer(tamanho)
        user32.GetWindowTextW(alca, buffer, tamanho)
        titulo = buffer.value or ""

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(alca, ctypes.byref(pid))
        processo = _nome_do_processo(pid.value)
        return Janela(titulo=titulo, processo=processo)
    except Exception:
        return Janela()


def janela_ao_lado(retangulo_proprio=None) -> tuple:
    """A janela que divide a tela conosco, como (Janela, retangulo).

    Esta e a leitura CORRETA para o motor de contexto. Usar a janela em
    primeiro plano era um erro: enquanto a pessoa le, quem esta em foco e o
    proprio Lumina, entao a deteccao ou se via no espelho ou desistia. O que
    importa e o programa ao LADO -- o mesmo que fornece as cores.
    """
    vizinha = janela_vizinha(retangulo_proprio)
    if not vizinha:
        return Janela(), None
    alca, retangulo, titulo = vizinha
    processo = ""
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(alca, ctypes.byref(pid))
        processo = _nome_do_processo(pid.value)
    except Exception:
        pass
    return Janela(titulo=titulo, processo=processo, alca=int(alca)), retangulo


def _nome_do_processo(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        # PROCESS_QUERY_LIMITED_INFORMATION: o suficiente para o nome do
        # executavel, e nao exige privilegio elevado.
        alca = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not alca:
            return ""
        try:
            tamanho = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    alca, 0, buffer, ctypes.byref(tamanho)):
                return os.path.basename(buffer.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(alca)
    except Exception:
        pass
    return ""


# ======================================================== CLASSIFICACAO
def analisar(janela: Janela, perfil_livro=None, memoria=None,
             retangulo_janela=None, gerente_ia=None, texto_tela="") -> Sugestao:
    """Junta os tres niveis numa sugestao unica.

    Nenhum nivel decide sozinho: o programa aberto diz o CONTEXTO, o livro diz
    o CONTEUDO, e a memoria diz o que voce ja escolheu antes. Quando os tres
    apontam junto, a confianca sobe; quando divergem, ela cai e a sugestao
    simplesmente nao e aplicada.
    """
    sugestao = Sugestao(janela=janela)
    if not janela:
        return sugestao

    titulo = (janela.titulo or "").lower()
    processo = (janela.processo or "").lower()
    motivos = []
    pontos = {}

    def somar(render, peso, motivo, categoria="", linguagem=""):
        pontos[render] = pontos.get(render, 0.0) + peso
        motivos.append(motivo)
        if categoria and not sugestao.categoria:
            sugestao.categoria = categoria
        if linguagem and not sugestao.linguagem:
            sugestao.linguagem = linguagem

    # --- nivel 1a: o executavel
    if processo in REGRAS_PROCESSO:
        somar(REGRAS_PROCESSO[processo], 0.45, f"programa {processo}")

    # --- nivel 1b: extensoes citadas no titulo (o editor mostra o arquivo)
    for encontro in _RE_EXTENSAO.finditer(titulo):
        dados = EXTENSOES.get(encontro.group(1).lower())
        if dados:
            render, categoria, linguagem = dados
            somar(render, 0.35, f"arquivo {encontro.group(0)}", categoria, linguagem)
            break

    # --- nivel 1c: assunto no titulo (vale para navegador, video, PDF)
    for expressao, render, categoria, linguagem in PISTAS_TITULO:
        if re.search(expressao, titulo, re.IGNORECASE):
            somar(render, 0.30, "assunto no titulo da janela", categoria,
                  linguagem or "")
            break

    # --- nivel 2: o que o livro aberto ja revelou
    if perfil_livro is not None:
        render_livro = getattr(perfil_livro, "tipo_renderizacao", "")
        if render_livro:
            reforco = 0.35 if render_livro in pontos else 0.20
            somar(render_livro, reforco, "conteudo do livro")
            if not sugestao.linguagem:
                linguagens = getattr(perfil_livro, "linguagens", None) or []
                if linguagens:
                    sugestao.linguagem = linguagens[0]

    # --- nivel 3: o que voce escolheu antes nesta mesma situacao.
    # Consultado ANTES de desistir: o caso ambiguo (navegador sem pista no
    # titulo) e exatamente onde a memoria serve, e desistir antes dela
    # anulava o aprendizado justamente ali.
    if memoria is not None:
        aprendido = memoria.preferido(processo, sugestao.categoria)
        if aprendido:
            somar(aprendido, 0.50, "sua escolha anterior nesta situacao")

    # --- nivel 4a: o CONTEUDO lido da janela ao lado.
    #
    # Vem ANTES de desistir por falta de pistas, e nao depois. O caso que mais
    # precisa dele e justamente o que nao pontua em nada: navegador aberto,
    # titulo generico ("Nova guia"), nenhuma regra com o que dizer. Deixar
    # este passo depois do `if not pontos` fazia o unico sinal bom da situacao
    # ser descartado exatamente na situacao que o justificava.
    if texto_tela:
        assunto, _forca = classificar_texto(texto_tela, RENDERIZACOES_CONHECIDAS)
        if assunto:
            pontos[assunto] = pontos.get(assunto, 0.0) + _PESO_TELA
            motivos.append("conteudo da janela ao lado")
            sugestao.leu_tela = True

    if not pontos:
        return sugestao

    render, melhor = max(pontos.items(), key=lambda kv: kv[1])
    segundo = max((v for k, v in pontos.items() if k != render), default=0.0)
    # Marcado antes da consulta ao modelo: e assim que a passada barata avisa
    # a interface de que vale a pena paga-lo nesta situacao.
    sugestao.empatado = _empatado(melhor, segundo)

    # --- nivel 4b: nada resolveu e as regras seguem empatadas. So aqui o
    # modelo entra, e so como palpite.
    if gerente_ia is not None and not sugestao.leu_tela:
        segundo_agora = max((v for k, v in pontos.items() if k != render),
                            default=0.0)
        if _empatado(melhor, segundo_agora):
            veredito = _desempatar_com_modelo(gerente_ia, janela, perfil_livro,
                                              sorted(pontos))
            if veredito and veredito in pontos:
                pontos[veredito] += _PESO_MODELO
                motivos.append("palpite da IA local")
                sugestao.palpite = True
                render, melhor = max(pontos.items(), key=lambda kv: kv[1])

    concorrente = max((v for k, v in pontos.items() if k != render), default=0.0)
    # Amostrar custa ~100 ms, entao so acontece quando a sugestao vai mesmo
    # ser mostrada -- nunca a cada volta do monitor.
    if retangulo_janela is not None:
        try:
            sugestao.cores = amostrar_cores(retangulo_janela)
        except Exception:
            sugestao.cores = None
    # Divergencia entre sinais derruba a confianca: melhor nao sugerir do que
    # trocar o modo de leitura na cara do usuario sem motivo.
    sugestao.render = render
    sugestao.confianca = round(max(0.0, min(0.99, melhor - concorrente * 0.6)), 2)
    sugestao.motivo = " + ".join(dict.fromkeys(motivos))
    return sugestao


# =================================================== NIVEL 4: MODELO LOCAL
# Diferenca abaixo da qual dois candidatos estao empatados. Acima disso as
# regras ja decidiram e consultar o modelo seria desperdicio.
_MARGEM_EMPATE = 0.12
# Abaixo deste ponto nenhum sinal e forte o bastante para valer a consulta.
_PISO_PARA_CONSULTAR = 0.30
# Quanto o palpite soma. Pequeno de proposito: desempata sem virar dono da
# decisao, e mantem a confianca final na faixa de "sugestao", nao de certeza.
_PESO_MODELO = 0.18
# Quando a resposta veio do CONTEUDO da tela, e um sinal de outra qualidade:
# o modelo leu o assunto em vez de adivinhar pelo nome do executavel.
_PESO_TELA = 0.34

# Exemplos resolvidos antes da pergunta de verdade. Sem eles um modelo de 0,5B
# praticamente chuta: classificava "Dom Casmurro" como programacao. Com quatro
# exemplos o formato fica firme -- modelos pequenos aprendem pelo exemplo, nao
# pela instrucao.
_EXEMPLOS = (
    ("Code.exe", "main.py - projeto", "python", "programacao"),
    ("WINWORD.EXE", "Memorias Postumas de Bras Cubas", "nenhuma", "prosa"),
    ("chrome.exe", "Derivadas e integrais - aula 3", "nenhuma", "matematica"),
    ("Acrobat.exe", "Manual tecnico - norma ABNT", "nenhuma", "tecnico"),
)


def _empatado(melhor, concorrente) -> bool:
    return melhor >= _PISO_PARA_CONSULTAR and (melhor - concorrente) < _MARGEM_EMPATE


def _linha_do_caso(processo, titulo, linguagens):
    return (f"Programa: {processo or 'desconhecido'} | "
            f"Janela: {(titulo or '')[:80]} | "
            f"Linguagens: {linguagens or 'nenhuma'}")


# Pontuacao minima para o texto da tela valer como sinal. Abaixo disso o que
# foi lido nao tem cara de nada -- menu, barra de status, tela quase vazia.
_MINIMO_SINAL_TELA = 2.5

# Sinais de codigo que nenhum texto corrido tem. Contados por ocorrencia, nao
# por presenca: um ponto e virgula solto nao faz de um romance um programa.
_RE_CODIGO = re.compile(
    r"(\b(def|class|function|return|import|from|const|let|var|public|private|"
    r"void|null|None|True|False|elif|foreach|SELECT|INSERT|UPDATE|WHERE|"
    r"FROM|JOIN)\b|[{}]|;\s*$|=>|::|\(\)|\[\]|</\w+>|\w+\.\w+\()",
    re.MULTILINE)
_RE_MATEMATICA = re.compile(
    r"(\b(integral|derivada|equa[cç][aã]o|teorema|fun[cç][aã]o f\(x\)|limite|"
    r"matriz|vetor|logaritmo|senoide|primitiva|somat[oó]rio|c[aá]lculo)\b|"
    r"[∫∑√±≤≥≠∞π]|\bf\(x\)|\bdx\b)", re.IGNORECASE)
# Frase de prosa: comeca com maiuscula, corre por varias palavras e termina em
# ponto. E o que separa um paragrafo de uma lista de rotulos de interface.
_RE_FRASE = re.compile(r"[A-ZÀ-Ý][^.!?\n]{45,}[.!?]")


def classificar_texto(texto, candidatos):
    """Do que trata este texto? Devolve (candidato, forca) ou ("", 0).

    Feito com as MESMAS regras que ja classificam os livros -- as pistas de
    assunto de `PISTAS_TITULO` e o pontuador de linguagens do realce -- e nao
    com o modelo. Medido nos mesmos seis trechos, o modelo de 0,5B acertou 2;
    isto acerta 6, em menos de um milissegundo e sem variar entre execucoes.
    Faz sentido: reconhecer `def`, `SELECT` ou a palavra "integral" e trabalho
    de expressao regular, nao de rede neural.
    """
    amostra = (texto or "")[:4000]
    if len(amostra.strip()) < 40:
        return "", 0.0

    pontos = {}

    def somar(alvo, quanto):
        if alvo in candidatos:
            pontos[alvo] = pontos.get(alvo, 0.0) + quanto

    # --- codigo: marcas de sintaxe e vocabulario de linguagem
    marcas = len(_RE_CODIGO.findall(amostra))
    somar("programacao", min(8.0, marcas * 0.7))
    try:
        import services.realce as realce
        melhor = max(realce.pontuar(amostra).values(), default=0.0)
        somar("programacao", min(5.0, melhor * 0.5))
    except Exception:
        pass

    # --- matematica
    somar("matematica", min(8.0, len(_RE_MATEMATICA.findall(amostra)) * 1.2))

    # --- assunto declarado no proprio texto (as pistas ja usadas no titulo)
    for expressao, render, _categoria, _lingua in PISTAS_TITULO:
        achados = len(re.findall(expressao, amostra, re.IGNORECASE))
        if achados:
            somar(render, min(4.0, 1.0 + achados * 0.5))

    # --- prosa: frases longas e poucas marcas de codigo
    frases = len(_RE_FRASE.findall(amostra))
    if frases and marcas < 6:
        somar("prosa", min(7.0, 1.5 + frases * 1.6))

    if not pontos:
        return "", 0.0
    vencedor, forca = max(pontos.items(), key=lambda kv: kv[1])
    segundo = max((v for k, v in pontos.items() if k != vencedor), default=0.0)
    # Empate no proprio texto tambem e "nao sei".
    if forca < _MINIMO_SINAL_TELA or forca - segundo < 1.0:
        return "", 0.0
    return vencedor, forca


def _desempatar_com_modelo(gerente_ia, janela, perfil_livro, candidatos):
    """Pede um palpite ao modelo LOCAL entre os candidatos ja empatados.

    Nao e conversa: e escolha entre opcoes que as regras levantaram, e a
    resposta so vale se for exatamente uma delas. Qualquer outra coisa --
    modelo ausente, texto solto, alucinacao -- vira "nao sei", e o leitor
    volta a nao sugerir nada.

    O que sai daqui e so o nome do programa, o titulo da janela e as
    linguagens ja detectadas no livro. Nada do texto que voce esta lendo, e
    nada sai da maquina: `_provedor_local` nunca devolve provedor de nuvem.
    """
    try:
        provedor = gerente_ia._provedor_local()
    except Exception:
        provedor = None
    if not provedor:
        return ""

    linguagens = ", ".join(
        list(getattr(perfil_livro, "linguagens", None) or [])[:3])
    partes = ["Voce classifica o modo de leitura de um livro. Responda SO "
              "com uma palavra da lista: " + ", ".join(candidatos) + ".\n"]
    for processo, titulo, linguas, resposta in _EXEMPLOS:
        if resposta in candidatos:
            partes.append(_linha_do_caso(processo, titulo, linguas)
                          + f"\nResposta: {resposta}\n")
    partes.append(_linha_do_caso(janela.processo, janela.titulo, linguagens)
                  + "\nResposta:")
    try:
        bruto = provedor[1].classificar("\n".join(partes)) or ""
    except Exception:
        return ""

    resposta = bruto.strip().lower()
    for candidato in candidatos:
        if re.search(rf"\b{re.escape(candidato)}\b", resposta):
            return candidato
    return ""


# ================================================= CORES DA JANELA AO LADO
# Combinar a aparencia com o programa ao lado nao precisa de tabela de temas:
# da para LER as cores da tela. E mais fiel que adivinhar pelo nome do
# aplicativo, porque pega o tema que a pessoa realmente configurou lá.
_AMOSTRA_LARGURA = 160     # a imagem e reduzida antes de contar cores


def retangulo_da_janela(alca=None):
    """Retangulo (x0, y0, x1, y1) da janela ativa, em pixels de tela."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        alca = alca or user32.GetForegroundWindow()
        if not alca:
            return None
        retangulo = wintypes.RECT()
        if not user32.GetWindowRect(alca, ctypes.byref(retangulo)):
            return None
        return (retangulo.left, retangulo.top, retangulo.right, retangulo.bottom)
    except Exception:
        return None


_CLASSES_DO_SISTEMA = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Button", "DV2ControlHost", "MsgrIMEWindowClass", "SysShadow",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow_Hidden",
    "ForegroundStaging", "XamlExplorerHostIslandWindow",
}
_TITULOS_DO_SISTEMA = {"Program Manager", "Configurações", "Settings",
                       "Windows Shell Experience Host", "Pesquisar"}


def _janelas_visiveis():
    """[(alca, retangulo, titulo)] das janelas de topo, da frente para tras.

    O `EnumWindows` entrega em ordem de Z, entao a primeira que sobrar depois
    dos filtros e a que esta mais a frente.
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    achadas = []

    PROTOTIPO = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def visitar(alca, _):
        try:
            if not user32.IsWindowVisible(alca) or user32.IsIconic(alca):
                return True
            # Janela "cloaked" e a de app da loja que existe mas nao aparece;
            # sem este filtro elas viram candidatas fantasma.
            oculta = ctypes.c_int(0)
            try:
                ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    alca, 14, ctypes.byref(oculta), ctypes.sizeof(oculta))
            except Exception:
                pass
            if oculta.value:
                return True

            retangulo = wintypes.RECT()
            if not user32.GetWindowRect(alca, ctypes.byref(retangulo)):
                return True
            largura = retangulo.right - retangulo.left
            altura = retangulo.bottom - retangulo.top
            if largura < 240 or altura < 180:
                return True

            # A area de trabalho e a barra de tarefas sao janelas como as
            # outras e cobrem a tela inteira -- sem filtrar pela classe, o
            # "Program Manager" sempre ganharia a disputa por area visivel.
            classe = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(alca, classe, 128)
            if classe.value in _CLASSES_DO_SISTEMA:
                return True

            tamanho = user32.GetWindowTextLengthW(alca) + 1
            buffer = ctypes.create_unicode_buffer(tamanho)
            user32.GetWindowTextW(alca, buffer, tamanho)
            titulo = buffer.value or ""
            if not titulo or titulo in _TITULOS_DO_SISTEMA:
                return True

            achadas.append((alca, (retangulo.left, retangulo.top,
                                   retangulo.right, retangulo.bottom), titulo))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(PROTOTIPO(visitar), 0)
    except Exception:
        return []
    return achadas


def _limitar_a_tela(retangulo):
    """Prende o retangulo dentro da tela principal."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        largura = user32.GetSystemMetrics(0)
        altura = user32.GetSystemMetrics(1)
    except Exception:
        return retangulo
    x0, y0, x1, y1 = retangulo
    return (max(0, x0), max(0, y0), min(largura, x1), min(altura, y1))


def _area_intersecao(a, b):
    largura = min(a[2], b[2]) - max(a[0], b[0])
    altura = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, largura) * max(0, altura)


def janela_vizinha(retangulo_proprio=None, alca_propria=None):
    """A janela que divide a tela com a nossa -- nao a que esta em foco.

    Este era o erro da primeira versao: ao clicar no botao, quem esta em
    primeiro plano e o proprio Lumina, entao ele amostrava as proprias cores.
    A janela util e a que ocupa o pedaco da tela que a nossa NAO ocupa.
    """
    candidatas = _janelas_visiveis()
    if not candidatas:
        return None

    melhor = None
    melhor_area = 0
    for alca, retangulo, titulo in candidatas:
        if alca_propria and alca == alca_propria:
            continue
        retangulo = _limitar_a_tela(retangulo)
        if retangulo[2] - retangulo[0] < 200 or retangulo[3] - retangulo[1] < 150:
            continue
        if retangulo_proprio:
            # Descarta quem e praticamente a nossa propria janela (mesmo
            # retangulo) e quem esta quase todo por baixo dela.
            propria = _area_intersecao(retangulo, retangulo_proprio)
            area = ((retangulo[2] - retangulo[0]) * (retangulo[3] - retangulo[1])) or 1
            if propria / area > 0.7:
                continue
            visivel = area - propria
        else:
            visivel = ((retangulo[2] - retangulo[0]) *
                       (retangulo[3] - retangulo[1]))
        if visivel > melhor_area:
            melhor_area, melhor = visivel, (alca, retangulo, titulo)

    return melhor


def _recortar_fora(retangulo, retangulo_proprio):
    """Corta do retangulo a parte coberta pela nossa janela.

    Sem isso, numa sobreposicao parcial a captura pegaria pedaco do proprio
    Lumina e a cor amostrada sairia errada.
    """
    if not retangulo_proprio:
        return retangulo
    x0, y0, x1, y1 = retangulo
    px0, py0, px1, py1 = retangulo_proprio
    if _area_intersecao(retangulo, retangulo_proprio) <= 0:
        return retangulo
    # Fica com o maior pedaco livre a esquerda ou a direita da nossa janela.
    esquerda = (x0, y0, min(x1, px0), y1)
    direita = (max(x0, px1), y0, x1, y1)
    opcoes = [c for c in (esquerda, direita) if c[2] - c[0] > 200]
    if not opcoes:
        return retangulo
    return max(opcoes, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))


def cores_da_janela_ao_lado(retangulo_proprio=None):
    """Cores do programa que divide a tela conosco.

    Devolve (cores, titulo) ou (None, motivo) -- o motivo serve para dizer ao
    usuario POR QUE nao deu, em vez de so falhar em silencio.
    """
    vizinha = janela_vizinha(retangulo_proprio)
    if not vizinha:
        return None, "Nenhuma outra janela visivel ao lado."
    _alca, retangulo, titulo = vizinha
    area = _recortar_fora(retangulo, retangulo_proprio)
    cores = amostrar_cores(area)
    if not cores:
        return None, f"Nao consegui ler cores uteis de “{titulo[:40]}”."
    return cores, titulo


def amostrar_cores(retangulo=None):
    """Cores dominantes da janela ao lado: (papel, texto, destaque).

    Como funciona: captura a area, reduz para uma miniatura (contar cor em
    resolucao cheia seria desperdicio), e separa em tres papeis:
      * papel    -> a cor MAIS frequente, que e sempre o fundo;
      * texto    -> a cor de maior contraste com o fundo, que e a letra;
      * destaque -> a cor mais saturada, que costuma ser o realce da interface.

    Devolve None quando nao da para amostrar ou quando a janela e pequena
    demais para ter uma cor de fundo confiavel.
    """
    try:
        from PIL import Image, ImageGrab
    except Exception:
        return None

    if not retangulo:
        return None
    # Coordenada negativa vem de janela em monitor secundario ou maximizada
    # alem da borda; capturar fora da tela devolveria preto.
    x0, y0, x1, y1 = _limitar_a_tela(retangulo)
    if (x1 - x0) < 200 or (y1 - y0) < 150:
        return None

    try:
        # Amostra o MIOLO da janela, nao ela inteira. Num editor as bordas
        # sao arvore de arquivos, abas, terminal e barra de status -- cada uma
        # com seu proprio fundo. O conteudo que interessa (o texto sendo
        # editado ou lido) fica no centro, em qualquer aplicativo.
        largura, altura = x1 - x0, y1 - y0
        area = (x0 + int(largura * 0.28), y0 + int(altura * 0.18),
                x1 - int(largura * 0.08), y1 - int(altura * 0.18))
        imagem = ImageGrab.grab(bbox=area)
        proporcao = _AMOSTRA_LARGURA / max(1, imagem.width)
        imagem = imagem.convert("RGB").resize(
            (_AMOSTRA_LARGURA, max(1, int(imagem.height * proporcao))),
            Image.Resampling.BILINEAR)
        # Quantizar antes de contar: uma captura de tela tem dezenas de
        # milhares de cores unicas (antialias, gradiente, sombra) e
        # `getcolors` simplesmente devolve None quando passa do limite.
        # Reduzindo a 32 cores, o fundo e a letra viram blocos solidos.
        imagem = imagem.quantize(colors=32, method=Image.Quantize.FASTOCTREE)
        imagem = imagem.convert("RGB")
    except Exception:
        return None

    cores = imagem.getcolors(maxcolors=4096) or []
    if not cores:
        return None
    cores.sort(reverse=True)

    total = sum(q for q, _ in cores) or 1
    papel = cores[0][1]
    lum_papel = _luminancia(papel)

    # TEXTO: o corpo do texto e quase sempre NEUTRO (cinza ou branco) e o
    # segundo mais frequente da tela. Pegar so "o de maior contraste"
    # escolhia cor de sintaxe -- num editor escuro isso dava salmao no lugar
    # do cinza claro que a pessoa realmente ve.
    texto = None
    melhor_nota = 0.0
    for quantidade, cor in cores[:40]:
        contraste = abs(_luminancia(cor) - lum_papel)
        if contraste < 0.30:
            continue
        saturacao = (max(cor) - min(cor)) / 255.0
        frequencia = quantidade / total
        # Penaliza cor saturada e premia cor frequente: o texto do corpo
        # aparece muito mais que qualquer palavra colorida.
        nota = contraste * (1.0 - saturacao * 0.85) * (0.25 + min(1.0, frequencia * 12))
        if nota > melhor_nota:
            melhor_nota, texto = nota, cor

    if texto is None:
        # Sem contraste suficiente a amostra nao presta (janela cheia de
        # imagem, por exemplo). Melhor nao sugerir nada.
        return None

    # DESTAQUE: a mais colorida, mas com presenca real na tela. Sem exigir
    # frequencia, um sublinhado de erro virava a cor de destaque do tema.
    destaque = None
    maior_saturacao = 0.0
    for quantidade, cor in cores[:40]:
        if quantidade / total < 0.004:
            continue
        saturacao = (max(cor) - min(cor)) / 255.0
        if saturacao > maior_saturacao and 0.25 < _luminancia(cor) < 0.85:
            maior_saturacao, destaque = saturacao, cor
    if destaque is None or maior_saturacao < 0.15:
        destaque = texto

    # A cor amostrada da letra vem da MEDIA dos pixels, e as bordas suavizadas
    # puxam tudo para o cinza medio: no VS Code o texto real e #D4D4D4 e a
    # amostra da #898A8D. Copiar isso literalmente geraria um tema bonito de
    # print e ruim de ler, entao o contraste e reforcado ate o minimo
    # confortavel -- estamos montando um tema de LEITURA, nao uma captura.
    # Guarda um traco do matiz de origem, mas nao a saturacao cheia: ler
    # paragrafos longos numa cor saturada cansa. O destaque mantem a cor.
    # A ordem importa: dessaturar mexe na luminosidade, entao suavizar DEPOIS
    # de reforcar desfazia parte do contraste ja conquistado.
    texto = _reforcar_contraste(papel, _suavizar(texto))
    destaque = _reforcar_contraste(papel, destaque, alvo=0.35)
    return (_para_hex(papel), _para_hex(texto), _para_hex(destaque))


def _suavizar(cor, manter=0.35):
    """Puxa a cor para o cinza de mesma luminosidade, guardando um pouco do matiz."""
    cinza = int(round(_luminancia(cor) * 255))
    return tuple(int(round(cinza + (v - cinza) * manter)) for v in cor)


def _reforcar_contraste(papel, cor, alvo=0.58):
    """Afasta a cor do fundo ate ATINGIR o contraste alvo, guardando o matiz.

    A versao anterior aplicava de uma vez uma fracao estimada e parava perto
    do alvo sem alcanca-lo (0.54 para um alvo de 0.58). Aqui cada passo e
    verificado, entao o alvo e realmente atingido.
    """
    lum_papel = _luminancia(papel)
    if abs(_luminancia(cor) - lum_papel) >= alvo:
        return tuple(int(round(v)) for v in cor)
    # Num fundo escuro a letra vai para o branco; num claro, para o preto.
    extremo = (255, 255, 255) if lum_papel < 0.5 else (0, 0, 0)
    candidato = cor
    for passo in range(1, 25):
        fracao = passo / 24.0
        candidato = tuple(v + (e - v) * fracao for v, e in zip(cor, extremo))
        if abs(_luminancia(candidato) - lum_papel) >= alvo:
            break
    return tuple(int(round(v)) for v in candidato)


def _luminancia(rgb):
    r, g, b = (v / 255 for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _para_hex(rgb):
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb[:3])


# ============================================================ NIVEL 3
class Memoria:
    """Contador local de escolhas. Sem isso o motor nunca melhora.

    Guarda so o que e preciso para decidir: (processo, categoria) -> quantas
    vezes cada tipo de renderizacao foi confirmado. Nada de historico de
    leitura, nada de titulos gravados.
    """

    def __init__(self, cfg, chave="contexto_memoria"):
        self.cfg = cfg
        self.chave = chave
        self._dados = self._carregar()

    def _carregar(self):
        import json
        try:
            return json.loads(self.cfg.obter(self.chave, "") or "{}")
        except Exception:
            return {}

    @staticmethod
    def _peso(valor):
        """Quantas confirmacoes uma entrada acumulou (para decidir o descarte)."""
        if isinstance(valor, dict):
            return sum(v for v in valor.values() if isinstance(v, (int, float)))
        return valor if isinstance(valor, (int, float)) else 0

    def _salvar(self):
        import json
        try:
            # Cortar a STRING quebrava o JSON no meio e a memoria inteira se
            # perdia na proxima leitura. Descarta as entradas menos usadas.
            texto = json.dumps(self._dados)
            while len(texto) > 6000 and self._dados:
                menos_usada = min(self._dados, key=lambda k: self._peso(self._dados[k]))
                del self._dados[menos_usada]
                texto = json.dumps(self._dados)
            self.cfg.definir(self.chave, texto)
        except Exception:
            pass

    @staticmethod
    def _chave(processo, categoria):
        return f"{(processo or '?').lower()}|{(categoria or '').lower()}"

    def registrar(self, processo, categoria, render):
        if not render:
            return
        entrada = self._dados.setdefault(self._chave(processo, categoria), {})
        entrada[render] = entrada.get(render, 0) + 1
        self._salvar()

    def desfazer(self, processo, categoria, render):
        """Contrapartida de `registrar`: tira o +1 de um "Desfazer".

        Sem isto, testar uma sugestao errada e desfazer duas vezes ainda
        deixava `preferido()` >= 2 para aquele render -- a memoria continuaria
        empurrando de volta exatamente o que foi rejeitado duas vezes.
        """
        if not render:
            return
        chave = self._chave(processo, categoria)
        entrada = self._dados.get(chave)
        if not entrada or render not in entrada:
            return
        entrada[render] -= 1
        if entrada[render] <= 0:
            del entrada[render]
        if not entrada:
            del self._dados[chave]
        self._salvar()

    def preferido(self, processo, categoria):
        entrada = self._dados.get(self._chave(processo, categoria)) or {}
        if not entrada:
            return ""
        render, vezes = max(entrada.items(), key=lambda kv: kv[1])
        # Uma escolha isolada pode ter sido engano; duas ja e padrao.
        return render if vezes >= 2 else ""

    def registrar_cores(self, processo, aceitou):
        """Guarda se voce quis as cores daquele programa.

        Sem isso, quem gosta de casar as cores com o editor teria que pedir
        de novo toda vez -- e quem NAO gosta seria perguntado para sempre.
        """
        entrada = self._dados.setdefault(f"cores|{(processo or '?').lower()}", {})
        chave = "sim" if aceitou else "nao"
        entrada[chave] = entrada.get(chave, 0) + 1
        self._salvar()

    def prefere_cores(self, processo):
        entrada = self._dados.get(f"cores|{(processo or '?').lower()}") or {}
        if not entrada:
            return None
        return entrada.get("sim", 0) > entrada.get("nao", 0)

    def esquecer_tudo(self):
        self._dados = {}
        self._salvar()
