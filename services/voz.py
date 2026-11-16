# services/voz.py
"""Leitura em voz alta usando a sintese de voz nativa do Windows.

POR QUE POWERSHELL + System.Speech, E NAO pyttsx3/pywin32:
o programa vira um .exe, e cada dependencia nova entra no pacote. O Windows ja
traz o mecanismo de sintese (SAPI5) e o .NET Framework ja expoe ele em
System.Speech, entao da para falar sem instalar NADA. As alternativas eram:

  * pyttsx3  -> dependencia extra, e ainda por cima arrasta comtypes/pywin32;
  * SAPI via ctypes -> exigiria montar as chamadas COM (IDispatch, VARIANT,
    contagem de referencia) na mao; funciona, mas e codigo fragil e dificil de
    manter para um ganho nenhum;
  * PowerShell + System.Speech -> ja esta na maquina, e roda em OUTRO processo.

Esse "outro processo" e justamente a vantagem principal. A sintese e bloqueante
e demorada (uma pagina leva minutos); se rodasse dentro do Python, ou travaria a
interface do CustomTkinter, ou exigiria uma thread que nao da para interromper.
Com um processo separado, `parar()` e simplesmente matar o processo -- corta o
audio na hora, mesmo no meio de uma palavra, que e o que o usuario espera ao
virar a pagina.

COMUNICACAO COM O PROCESSO:
  * texto        -> arquivo temporario em UTF-8 (linha de comando tem limite de
                    ~8 mil caracteres; uma pagina de livro passa disso fácil);
  * pausar/retomar -> arquivo de controle de 1 byte que o script releitura a
                    cada 60 ms (usar stdin travaria: em PowerShell 5.1 nao ha
                    leitura nao-bloqueante de stdin redirecionado);
  * fim da fala  -> o proprio processo terminar.

ARMADILHA DE ACENTUACAO:
o PowerShell 5.1 le arquivos .ps1 na pagina de codigo ANSI quando nao ha BOM, e
a saida do console sai em CP850. Por isso o script abaixo e ASCII PURO e o texto
a falar nunca passa pela linha de comando nem pelo console: ele vai num arquivo
lido explicitamente com `System.Text.UTF8Encoding`. Assim "Instalacao pratica de
aplicacao nao e obvia" chega intacto ao sintetizador.
"""
import array
import os
import subprocess
import wave
try:
    import winsound
except ImportError:
    winsound = None
import tempfile
import threading
import time

# ------------------------------------------------------------------ CONSTANTES

# Faixa aceita pela propriedade Rate do System.Speech. Coincide com a faixa
# pedida pela interface, entao a velocidade vai direto, sem conversao.
_VELOCIDADE_MIN = -10
_VELOCIDADE_MAX = 10

# Intervalo de releitura do arquivo de controle dentro do PowerShell. Curto o
# bastante para o "pausar" parecer instantaneo, longo o bastante para nao
# ocupar um nucleo so lendo arquivo.
_MS_POLLING = 60

# Evita a janela preta do console piscando a cada fala quando empacotado.
_SEM_JANELA = 0x08000000 if os.name == "nt" else 0

# ===================================================== GANHO E NORMALIZACAO
# O SAPI para em Volume=100 e ainda assim sai baixo, porque o processo tem
# sessao propria no mixer do Windows. Para passar disso e preciso mexer na
# onda: normalizar pelo pico e depois aplicar ganho com limitador.
# `audioop` sumiu no Python 3.13, entao a conta e feita na mao -- com tabela
# de consulta, porque um laco sobre 1,3 milhao de amostras em Python puro
# custaria quase um segundo.
_PICO = 32767
_ALVO = int(_PICO * 0.97)


def _tabela_de_ganho(fator: float, joelho: float = 0.70):
    """Mapa de 16 bits: amostra de entrada -> amostra tratada.

    Acima do joelho a curva comprime em vez de cortar reto, senao o ganho
    alto viraria distorcao audivel em vez de volume.
    """
    limite = _ALVO * joelho
    tabela = array.array("h", bytes(2 * 65536))
    for bruto in range(-32768, 32768):
        valor = bruto * fator
        modulo = abs(valor)
        if modulo > limite:
            excesso = modulo - limite
            # Curva assintotica: nunca ultrapassa _ALVO.
            modulo = limite + excesso * (_ALVO - limite) / (excesso + (_ALVO - limite))
            valor = modulo if valor >= 0 else -modulo
        tabela[bruto & 0xFFFF] = max(-_PICO, min(_PICO, int(valor)))
    return tabela


def _amplificar_wav(caminho: str, ganho: float, normalizar: bool = True) -> bool:
    """Reescreve o WAV com ganho aplicado. Devolve False se nao deu para tratar."""
    try:
        with wave.open(caminho, "rb") as onda:
            parametros = onda.getparams()
            if parametros.sampwidth != 2:      # so PCM 16 bits
                return False
            quadros = onda.readframes(parametros.nframes)

        amostras = array.array("h")
        amostras.frombytes(quadros)
        if not amostras:
            return False

        fator = float(ganho)
        if normalizar:
            pico = max(max(amostras), -min(amostras)) or 1
            # Aproveita o espaco livre antes de pedir ganho extra: e o que
            # deixa a fala alta sem forcar o limitador.
            fator *= _ALVO / pico

        tabela = _tabela_de_ganho(fator)
        tratadas = array.array("h", (tabela[v & 0xFFFF] for v in amostras))

        with wave.open(caminho, "wb") as saida:
            saida.setnchannels(parametros.nchannels)
            saida.setsampwidth(parametros.sampwidth)
            saida.setframerate(parametros.framerate)
            saida.writeframes(tratadas.tobytes())
        return True
    except Exception as erro:
        print(f"[Voz] Falha ao amplificar: {erro}")
        return False

_MARCA_PAUSA = "P"
_MARCA_SEGUE = "R"


# Sintetiza para ARQUIVO em vez do alto-falante. E o unico jeito de passar de
# 100% de volume: com a onda em maos da para normalizar e amplificar antes de
# tocar. Usa SSML porque so por ela da para mexer no TOM da voz -- a API
# System.Speech nao expoe pitch como propriedade.
_SCRIPT_ARQUIVO = r"""
param([string]$Texto, [string]$Saida, [string]$Voz, [int]$Velocidade,
      [int]$Tom = 0)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$utf8 = New-Object System.Text.UTF8Encoding($false)
$conteudo = [System.IO.File]::ReadAllText($Texto, $utf8)
if ([string]::IsNullOrWhiteSpace($conteudo)) { exit 0 }
$sintetizador = New-Object System.Speech.Synthesis.SpeechSynthesizer
$sintetizador.SetOutputToWaveFile($Saida)
if ($Voz) {
    try { $sintetizador.SelectVoice($Voz) } catch { }
} else {
    foreach ($v in $sintetizador.GetInstalledVoices()) {
        if ($v.Enabled -and $v.VoiceInfo.Culture.Name.ToLower().StartsWith('pt')) {
            try { $sintetizador.SelectVoice($v.VoiceInfo.Name) } catch { }
            break
        }
    }
}
if ($Velocidade -lt -10) { $Velocidade = -10 }
if ($Velocidade -gt 10) { $Velocidade = 10 }
$sintetizador.Rate = $Velocidade
$sintetizador.Volume = 100
if ($Tom -ne 0) {
    $sinal = '+'
    if ($Tom -lt 0) { $sinal = '' }
    $escapado = [System.Security.SecurityElement]::Escape($conteudo)
    $ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" ' +
            'xml:lang="pt-BR"><prosody pitch="' + $sinal + $Tom + 'st">' +
            $escapado + '</prosody></speak>'
    try { $sintetizador.SpeakSsml($ssml) } catch { $sintetizador.Speak($conteudo) }
} else {
    $sintetizador.Speak($conteudo)
}
$sintetizador.Dispose()
exit 0
"""


# Script que efetivamente fala. Recebe caminhos, nunca texto: assim nada com
# acento trafega pela linha de comando.
_SCRIPT_FALA = r"""
param([string]$Texto, [string]$Controle, [string]$Voz, [int]$Velocidade,
      [int]$Volume = 100)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$utf8 = New-Object System.Text.UTF8Encoding($false)
$conteudo = [System.IO.File]::ReadAllText($Texto, $utf8)
if ([string]::IsNullOrWhiteSpace($conteudo)) { exit 0 }
$sintetizador = New-Object System.Speech.Synthesis.SpeechSynthesizer
$sintetizador.SetOutputToDefaultAudioDevice()
# Sem definir o volume explicitamente, o sintetizador herda um valor que nem
# sempre e o maximo -- e ai a narracao sai baixa mesmo com o volume do
# Windows no talo, porque o processo tem a propria sessao no mixer.
if ($Volume -lt 0) { $Volume = 0 }
if ($Volume -gt 100) { $Volume = 100 }
$sintetizador.Volume = $Volume
if ($Voz) {
    try { $sintetizador.SelectVoice($Voz) } catch { }
} else {
    foreach ($v in $sintetizador.GetInstalledVoices()) {
        if ($v.Enabled -and $v.VoiceInfo.Culture.Name.ToLower().StartsWith('pt')) {
            try { $sintetizador.SelectVoice($v.VoiceInfo.Name) } catch { }
            break
        }
    }
}
if ($Velocidade -lt -10) { $Velocidade = -10 }
if ($Velocidade -gt 10) { $Velocidade = 10 }
$sintetizador.Rate = $Velocidade
# Consulta o estado em vez de assinar o evento SpeakCompleted:
# `Register-ObjectEvent -Action` so roda quando o runspace esta ocioso, e o
# laco abaixo o mantem ocupado o tempo todo -- o evento nunca chegava e o
# processo ficava vivo para sempre depois de terminar de falar.
$sintetizador.SpeakAsync($conteudo) | Out-Null
$pronto = [System.Speech.Synthesis.SynthesizerState]::Ready
Start-Sleep -Milliseconds 150
$ultimo = 'R'
while ($sintetizador.State -ne $pronto) {
    Start-Sleep -Milliseconds __MS__
    try { $comando = ([System.IO.File]::ReadAllText($Controle)).Trim() } catch { $comando = $ultimo }
    if ($comando -ne $ultimo) {
        $ultimo = $comando
        if ($comando -eq 'P') { try { $sintetizador.Pause() } catch { } }
        elseif ($comando -eq 'R') { try { $sintetizador.Resume() } catch { } }
    }
}
$sintetizador.Dispose()
exit 0
""".replace("__MS__", str(_MS_POLLING))


# Listagem de vozes. Grava num arquivo em UTF-8 em vez de imprimir, porque a
# saida do console do PowerShell 5.1 sai na pagina de codigo do sistema e
# estragaria qualquer nome de voz acentuado.
_SCRIPT_VOZES = r"""
param([string]$Saida)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$sintetizador = New-Object System.Speech.Synthesis.SpeechSynthesizer
$nomes = New-Object System.Collections.ArrayList
foreach ($v in $sintetizador.GetInstalledVoices()) {
    if ($v.Enabled) { [void]$nomes.Add($v.VoiceInfo.Name) }
}
$sintetizador.Dispose()
[System.IO.File]::WriteAllLines($Saida, $nomes, (New-Object System.Text.UTF8Encoding($false)))
"""


# Lista de vozes instaladas, compartilhada por TODAS as instancias.
#
# Descobrir as vozes custa a abertura de um PowerShell -- de meio a um segundo
# inteiro. Antes isso era refeito por instancia: a primeira leitura em voz alta
# travava a janela, e abrir as configuracoes travava de novo, porque a aba de
# voz cria o proprio narrador. Como a lista nao muda enquanto o programa esta
# aberto, uma consulta por execucao basta.
_VOZES_CACHE = None
_TRAVA_VOZES = threading.Lock()


def preaquecer_vozes() -> None:
    """Descobre as vozes em segundo plano, antes de alguem precisar delas."""
    if _VOZES_CACHE is not None:
        return

    def trabalhador():
        try:
            Narrador().vozes()
        except Exception:
            pass

    threading.Thread(target=trabalhador, daemon=True).start()


def _caminho_powershell() -> str:
    """Caminho absoluto do PowerShell.

    Chamar so "powershell" depende do PATH, e o PATH de um .exe iniciado por
    duplo clique nem sempre e o mesmo do prompt do usuario.
    """
    raiz = os.environ.get("SystemRoot", r"C:\Windows")
    completo = os.path.join(raiz, "System32", "WindowsPowerShell", "v1.0",
                            "powershell.exe")
    return completo if os.path.exists(completo) else "powershell"


class Narrador:
    """Leitura em voz alta usando a sintese nativa do Windows.

    Roda em processo separado para nao travar a interface, e precisa parar
    NA HORA quando o usuario manda parar ou vira a pagina.
    """

    def __init__(self):
        # Um lock so protege tudo: as chamadas vem da interface (thread do
        # Tk) e do vigia (thread do subprocesso) ao mesmo tempo.
        self._trava = threading.RLock()
        self._processo = None
        self._pasta = None
        self._pausado = False
        self._tocando_arquivo = False
        # Geracao que esta sendo sintetizada em arquivo agora, ou 0. Enquanto
        # dura o preparo ainda nao ha som, mas ja ha uma leitura em andamento.
        self._preparando = 0
        # Cada fala recebe um numero. O vigia so avisa o fim se o numero ainda
        # for o atual -- senao, parar() seguido de falar() dispararia o
        # "terminou" da fala ANTIGA em cima da nova.
        self._geracao = 0

    # ------------------------------------------------------------- infra
    def _pasta_temporaria(self) -> str:
        if self._pasta is None or not os.path.isdir(self._pasta):
            self._pasta = tempfile.mkdtemp(prefix="lumina_voz_")
        return self._pasta

    def _gravar(self, nome: str, conteudo: str) -> str:
        caminho = os.path.join(self._pasta_temporaria(), nome)
        # newline="" para o script chegar ao PowerShell exatamente como escrito.
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(conteudo)
        return caminho

    def _escrever_controle(self, marca: str) -> None:
        """Grava a marca de forma atomica.

        O script do PowerShell le esse arquivo a cada 60 ms; se ele pegasse o
        arquivo no meio da escrita leria vazio e ignoraria o comando.
        """
        if not self._pasta:
            return
        destino = os.path.join(self._pasta, "controle.txt")
        provisorio = destino + ".tmp"
        try:
            with open(provisorio, "w", encoding="ascii") as arquivo:
                arquivo.write(marca)
            os.replace(provisorio, destino)
        except OSError:
            pass

    # ------------------------------------------------------------- consulta
    def disponivel(self) -> bool:
        """Se da para falar nesta maquina.

        Nao basta existir PowerShell: uma instalacao sem nenhuma voz habilitada
        executaria tudo sem emitir som algum, e a interface precisa saber disso
        antes de oferecer o botao de leitura.
        """
        if os.name != "nt":
            return False
        return bool(self.vozes())

    def vozes(self) -> list:
        """Nomes das vozes instaladas, em cache global.

        Abrir um PowerShell custa cerca de meio segundo; a lista nao muda
        enquanto o programa esta aberto, entao so vale consultar uma vez -- e
        uma vez para o programa todo, nao para cada narrador criado.
        """
        global _VOZES_CACHE
        if _VOZES_CACHE is not None:
            return list(_VOZES_CACHE)
        nomes = []
        try:
            script = self._gravar("vozes.ps1", _SCRIPT_VOZES)
            saida = os.path.join(self._pasta_temporaria(), "vozes.txt")
            subprocess.run(
                [_caminho_powershell(), "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", script, "-Saida", saida],
                creationflags=_SEM_JANELA, timeout=30,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if os.path.exists(saida):
                with open(saida, "r", encoding="utf-8") as arquivo:
                    nomes = [l.strip() for l in arquivo if l.strip()]
        except Exception as erro:
            print(f"[Voz] Nao foi possivel listar as vozes: {erro}")
        with _TRAVA_VOZES:
            if _VOZES_CACHE is None:
                _VOZES_CACHE = nomes
            return list(_VOZES_CACHE)

    @property
    def falando(self) -> bool:
        """Se existe uma leitura em andamento -- inclusive pausada.

        A interface usa isto para alternar o botao entre "ler" e "parar", e uma
        leitura pausada continua sendo uma leitura em andamento. Para saber se
        o som esta saindo agora, veja `pausado`.
        """
        with self._trava:
            if self._tocando_arquivo or self._preparando:
                return True
            return self._processo is not None and self._processo.poll() is None

    @property
    def pausado(self) -> bool:
        with self._trava:
            return self._pausado and self.falando

    # ------------------------------------------------------------- comandos
    def falar_alto(self, texto: str, voz: str = None, velocidade: int = 0,
                   ganho: float = 1.6, tom: int = 0, ao_terminar=None,
                   ao_falhar=None) -> bool:
        """Fala com volume acima do que o SAPI entrega sozinho.

        Sintetiza para arquivo, normaliza pelo pico, aplica ganho com
        limitador e so entao toca. E a unica forma de passar de 100% -- e a
        normalizacao sozinha ja levanta bastante, porque a saida do SAPI
        costuma ficar bem abaixo da escala cheia.

        NAO BLOQUEIA. Sintetizar uma pagina inteira leva de alguns segundos a
        mais de um minuto, e a versao anterior fazia essa espera na thread que
        desenha a interface: a janela congelava inteira -- sem rolar, sem
        responder ao botao de parar, com o Windows oferecendo encerrar o
        programa. Agora a espera acontece numa thread e a leitura segue.

        Nao suporta pausar: o audio ja esta pronto e quem toca e o Windows.
        `ao_falhar` avisa que este caminho nao deu certo, para quem chamou
        poder cair na sintese comum -- e chamado de OUTRA thread, como
        `ao_terminar`.
        """
        self.parar()
        if os.name != "nt" or winsound is None or not texto.strip():
            return False
        with self._trava:
            self._geracao += 1
            geracao = self._geracao
            # Ja conta como "falando" durante o preparo: senao o botao voltaria
            # para "ler" e um segundo clique dispararia uma segunda sintese.
            self._preparando = geracao
        threading.Thread(
            target=self._sintetizar_e_tocar,
            args=(texto, voz, velocidade, ganho, tom, geracao, ao_terminar,
                  ao_falhar),
            daemon=True).start()
        return True

    def _limpar_audios_antigos(self, geracao_atual):
        """Apaga WAVs/textos de leituras anteriores (varios MB por pagina).

        Antes so o encerrar() limpava: uma tarde de leitura enchia o %TEMP%.
        Arquivo ainda em uso e simplesmente pulado.
        """
        try:
            pasta = self._pasta_temporaria()
            for nome in os.listdir(pasta):
                if not (nome.startswith("fala_") or nome.startswith("texto_alto_")):
                    continue
                if f"_{geracao_atual}." in nome:
                    continue
                try:
                    os.remove(os.path.join(pasta, nome))
                except OSError:
                    pass
        except Exception:
            pass

    def _sintetizar_e_tocar(self, texto, voz, velocidade, ganho, tom, geracao,
                            ao_terminar, ao_falhar):
        saida = None
        erro = None
        self._limpar_audios_antigos(geracao)
        try:
            script = self._gravar("falar_arquivo.ps1", _SCRIPT_ARQUIVO)
            entrada = self._gravar(f"texto_alto_{geracao}.txt", texto)
            saida = os.path.join(self._pasta_temporaria(), f"fala_{geracao}.wav")
            subprocess.run(
                [_caminho_powershell(), "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", script,
                 "-Texto", entrada, "-Saida", saida, "-Voz", voz or "",
                 "-Velocidade", str(int(velocidade)), "-Tom", str(int(tom))],
                creationflags=_SEM_JANELA, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=300)
            if not os.path.exists(saida) or os.path.getsize(saida) < 128:
                saida = None
            else:
                _amplificar_wav(saida, max(0.1, min(2.5, float(ganho))))
        except Exception as excecao:
            erro = excecao
            saida = None

        with self._trava:
            # Pararam ou pediram outra leitura enquanto isto sintetizava: o
            # audio pronto e de uma pagina que ninguem esta mais lendo.
            if self._geracao != geracao:
                descartar = saida
            else:
                descartar = None
                self._preparando = 0
        if descartar:
            try:
                os.remove(descartar)
            except OSError:
                pass
            return

        if saida is None:
            if erro is not None:
                print(f"[Voz] Falha no modo amplificado: {erro}")
            if ao_falhar:
                try:
                    ao_falhar()
                except Exception:
                    pass
            return

        try:
            winsound.PlaySound(saida, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as excecao:
            print(f"[Voz] Falha ao tocar o audio preparado: {excecao}")
            if ao_falhar:
                try:
                    ao_falhar()
                except Exception:
                    pass
            return

        with self._trava:
            if self._geracao != geracao:
                return
            self._tocando_arquivo = True
        # A vigia roda SEMPRE, mesmo sem callback: e ela que devolve o estado
        # para `falando` quando o audio acaba. Sem isso o narrador ficava
        # marcado como falando para sempre.
        threading.Thread(
            target=self._vigiar_arquivo,
            args=(saida, geracao, ao_terminar), daemon=True).start()

    def _vigiar_arquivo(self, caminho, geracao, ao_terminar):
        try:
            with wave.open(caminho, "rb") as onda:
                duracao = onda.getnframes() / float(onda.getframerate() or 1)
        except Exception:
            duracao = 1.0
        fim = time.time() + duracao + 0.3
        while time.time() < fim:
            with self._trava:
                if self._geracao != geracao or not self._tocando_arquivo:
                    return
            time.sleep(0.15)
        with self._trava:
            if self._geracao != geracao:
                return
            self._tocando_arquivo = False
        if ao_terminar:
            try:
                ao_terminar()
            except Exception:
                pass

    def falar(self, texto: str, voz: str = None, velocidade: int = 0,
              ao_terminar=None, volume: int = 100) -> None:
        """velocidade vai de -10 (lento) a 10 (rapido), 0 e o normal.
        volume vai de 0 a 100 e precisa ser dito explicitamente: o padrao
        herdado pelo sintetizador nem sempre e o maximo."""
        self.parar()
        if not texto or not texto.strip():
            if ao_terminar:
                ao_terminar()
            return
        if os.name != "nt":
            if ao_terminar:
                ao_terminar()
            return

        try:
            velocidade = int(velocidade)
        except (TypeError, ValueError):
            velocidade = 0
        velocidade = max(_VELOCIDADE_MIN, min(_VELOCIDADE_MAX, velocidade))

        try:
            script = self._gravar("falar.ps1", _SCRIPT_FALA)
            # Nome unico por fala: se o processo anterior demorar um instante
            # para morrer, ele nao reescreve nem prende o arquivo do proximo.
            with self._trava:
                self._geracao += 1
                geracao = self._geracao
            arquivo_texto = self._gravar(f"texto_{geracao}.txt", texto)
            self._escrever_controle(_MARCA_SEGUE)
            controle = os.path.join(self._pasta_temporaria(), "controle.txt")

            processo = subprocess.Popen(
                [_caminho_powershell(), "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", script,
                 "-Texto", arquivo_texto, "-Controle", controle,
                 "-Voz", voz or "", "-Velocidade", str(velocidade),
                 "-Volume", str(max(0, min(100, int(volume))))],
                creationflags=_SEM_JANELA,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception as erro:
            print(f"[Voz] Falha ao iniciar a leitura: {erro}")
            if ao_terminar:
                ao_terminar()
            return

        with self._trava:
            self._processo = processo
            self._pausado = False

        vigia = threading.Thread(
            target=self._aguardar_fim,
            args=(processo, geracao, arquivo_texto, ao_terminar),
            daemon=True,
        )
        vigia.start()

    def _aguardar_fim(self, processo, geracao, arquivo_texto, ao_terminar):
        """Espera o processo morrer e avisa quem pediu a leitura.

        O aviso sai desta thread, NAO da thread da interface: quem passar
        `ao_terminar` precisa reagendar com `after(0, ...)` antes de mexer em
        qualquer widget.
        """
        try:
            processo.wait()
        except Exception:
            pass
        try:
            os.remove(arquivo_texto)
        except OSError:
            pass
        with self._trava:
            atual = self._geracao == geracao
            if atual:
                self._processo = None
                self._pausado = False
        # Fala abandonada por parar() ou por outra fala nao dispara o aviso:
        # senao a interface acharia que a leitura chegou ao fim da pagina e
        # viraria para a proxima sozinha.
        if atual and ao_terminar:
            try:
                ao_terminar()
            except Exception as erro:
                print(f"[Voz] Erro no aviso de fim de leitura: {erro}")

    def pausar(self) -> None:
        with self._trava:
            if not self.falando or self._pausado:
                return
            self._pausado = True
        self._escrever_controle(_MARCA_PAUSA)

    def retomar(self) -> None:
        with self._trava:
            if not self.falando or not self._pausado:
                return
            self._pausado = False
        self._escrever_controle(_MARCA_SEGUE)

    def parar(self) -> None:
        """Corta o audio imediatamente, sem terminar a frase.

        Matar o processo e o unico jeito de interromper no meio de uma palavra:
        o SpeakAsyncCancelAll do System.Speech so age no fim do trecho corrente,
        e o usuario que virou a pagina continuaria ouvindo a pagina anterior.
        """
        # Corta tambem o audio ja sintetizado que o Windows esta tocando.
        with self._trava:
            tocando = self._tocando_arquivo
            self._tocando_arquivo = False
            # A sintese em andamento nao da para matar no meio, mas o
            # incremento de geracao logo abaixo faz o resultado dela ser
            # descartado em vez de comecar a tocar depois do "parar".
            self._preparando = 0
        if tocando and winsound is not None:
            try:
                # SND_PURGE nao e suportado no Windows moderno; PlaySound(None)
                # e o jeito documentado de cortar o som em reproducao.
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        with self._trava:
            processo = self._processo
            self._processo = None
            self._pausado = False
            # Invalida o vigia da fala que esta morrendo.
            self._geracao += 1
        if processo is None:
            return
        try:
            if processo.poll() is None:
                processo.kill()
        except Exception as erro:
            print(f"[Voz] Falha ao interromper a leitura: {erro}")

    def encerrar(self) -> None:
        """Fechar o programa nao pode deixar uma voz falando sozinha."""
        self.parar()
        pasta = self._pasta
        self._pasta = None
        if not pasta:
            return
        try:
            import shutil
            shutil.rmtree(pasta, ignore_errors=True)
        except Exception:
            pass

    def __del__(self):
        try:
            self.encerrar()
        except Exception:
            pass
