# Changelog

## 2.3 — 2026-11-16

### Leitura
- **O texto flui entre as paginas.** Nos modos Rolagem continua, Folhear e
  Automatica a tela monta um trecho de paginas seguidas: sem buraco no fim
  de cada pagina, e o paragrafo cortado pela quebra de pagina e costurado
  ("...ou in" | "teligencia" vira "ou inteligencia", sem recuo falso). Grifos,
  notas e progresso continuam por pagina; a pagina atual e a do topo da tela.
- **Palavras partidas no EPUB** ("Co mo", "te clado") corrigidas: o hifen
  invisivel (U+00AD) agora e tratado na juncao das linhas.
- **Duas folhas lado a lado** no Folhear em janela larga, como um livro aberto.
- **Deslize** de ~120 ms ao virar a folha (desligavel no Ctrl+P).
- Codigo de EPUB nao e mais cortado na margem direita; linha longa quebra com
  recuo, como no word wrap dos editores.
- Titulos de capitulo de EPUB nao somem mais como "cabecalho repetido".
- Justificacao aceita um pouco mais de folga: menos linhas irregulares em
  coluna estreita.

### Estudo
- **Relacionar com meu codigo (VS Code):** acao do assistente que le o
  arquivo aberto no VS Code (do disco, pelo titulo da janela) e explica a
  pagina do livro aplicada a ele. Pede confirmacao no primeiro uso.
- **Enviar ao VS Code:** botao em cada bloco de codigo copia o trecho limpo e
  traz o VS Code para a frente.
- **Dicionario e traducao** da selecao (Free Dictionary, Wiktionary, MyMemory
  — gratuitos, sem chave).
- **Revisao espacada** (SM-2, como no Anki): cada grifo vira um cartao que
  volta no dia certo (`Ctrl+R`), e **estatisticas**: dias seguidos, minutos
  por dia, ritmo e tempo por livro.

### Arquivos
- **PDF com senha:** pede a senha uma vez e guarda cifrada pelo Windows
  (DPAPI, so a sua conta decifra).
- **Arrastar e soltar** PDF/EPUB (ou pastas) na estante.
- **Selecao multipla** na estante (Ctrl/Shift+clique): mover, remover e
  marcar como lido de uma vez.
- **OCR** de paginas digitalizadas com o reconhecedor do proprio Windows
  (gratuito, sem download; ~150-300 ms por pagina).

### IA
- **Escolha do modelo** de cada provedor em Configuracoes > IA: Claude Haiku
  5.5 / Sonnet 5.5 / Opus 5.5, GPT-4o mini / GPT-6 Luna, Gemini Flash-Lite /
  Flash / Pro, ou qualquer nome no campo "Outro".

### Distribuicao
- **Instalador** (`LuminaSetup-x.y.exe`, Inno Setup): instala por usuário e
  sem UAC em `%LOCALAPPDATA%\Programs\Lumina`, atalho no menu Iniciar
  (área de trabalho opcional) e desinstalador. O executável portátil
  continua publicado.
- **Abrir com**: PDF e EPUB passam a listar o Lumina no *Abrir com* e em
  *Aplicativos padrão*, sem trocar o leitor de PDF padrão; opção na
  instalação para tornar o Lumina o leitor padrão de EPUB.
- **Atualização automática**: uma consulta anônima por dia às Releases do
  GitHub; havendo versão nova, um aviso com **Atualizar** baixa o arquivo
  certo (instalador para quem instalou, `.exe` para o portátil), confere o
  sha256, fecha, troca e reabre. Interruptor e botão "Procurar atualizações"
  em *Configurações → Sobre*.
- Release do GitHub agora anexa `Lumina.exe` e `LuminaSetup-x.y.exe`;
  `python build.py --instalador` gera o instalador localmente.

## 2.2 — 2026-11-16

### Leitura
- **Modo Folhear (cabe na tela):** sem barra de rolagem; o texto vira por
  folhas com a roda, as setas, o espaço ou um clique na margem. A folha
  seguinte começa exatamente na linha que não coube, nenhuma linha aparece
  cortada no pé, e títulos/cabeçalhos de código descem junto com o texto
  (sem órfãos). Rodapé com `folha 2/4`; voltar abre a última folha da página
  anterior; a grade é refeita ao redimensionar, mantendo a frase do topo.
- **Rolagem contínua** (antigo "Livre"): vira com um toque ao encostar no fim
  (antes eram dois), ignora a inércia do gesto e, voltando, abre a página
  anterior pelo fim. Limites medidos na tela, não estimados pelo Tk.
- Setas ↑/↓ rolam (ou folheiam) o texto; atalhos não disparam mais enquanto
  se digita na resposta da IA ou nas notas.

### Formatos
- **EPUB**: abrir, capa, título/autor, sumário, busca, grifos e progresso.
  Paginação fixa (A5, letra 11) em todo o app, para os grifos não mudarem de
  lugar.

### Correções
- **Vazamento de memória na leitura da janela ao lado:** objetos COM e textos
  (BSTR) nunca eram liberados, e cada análise criava uma instância nova de UI
  Automation numa thread descartável. Medido em 80 leituras do VS Code: de
  +57 MB para +1 MB.
- Avisos de acessibilidade de editores ("O editor não está acessível…") não
  contam mais como assunto da janela.

## 2.1 — 2026-11-16

### Leitura e aparência
- **Texto justificado** nas duas margens (o Tk não oferece isso nativamente:
  a quebra é calculada com as métricas reais da fonte). Pode ser desligado em
  `Ctrl+P`.
- Entrelinha calculada pela métrica real da fonte (antes ficava ~20% mais
  aberta que o pedido) e respiro visível entre parágrafos.
- Linhas que o PDF entrega soltas (Word, geradores web) voltam a formar
  parágrafos; listas com marcador do Word (`` → •``) e recuo pendente.
- Parágrafos sobre fundo branco não viram mais "recorte" claro no tema escuro.
- Figuras do mesmo tamanho e posição não somem mais como se fossem marca
  d'água (a assinatura agora inclui o conteúdo da imagem).
- Código: coluna de numeração do PDF removida, linhas em branco preservadas,
  sem espaçamento duplo, rótulo de linguagem correto (Python não vira mais
  "HTML"), árvores de diretório identificadas.
- Nova identidade visual: azul-marinho e violeta da marca, barra de progresso
  com o gradiente do símbolo, ícones corretos no tema claro.

### Recursos
- Exportar grifos, notas e marcadores para Markdown (`Ctrl+E`).
- Histórico de saltos (`Alt+←` / `Alt+→`) e ir para página (`Ctrl+G`).
- Retomar a leitura na linha exata em que parou.
- Abrir PDF pelo "Abrir com" do Windows / argumento de linha de comando.
- Suporte a vários monitores no encaixe de janela.

### Correções
- "Responder pergunta" falhava com qualquer chave de nuvem (Claude/OpenAI/Gemini).
- Leitura quadro a quadro de HQ quebrava (`NameError`).
- Grifos salvos deslocados em páginas com imagem.
- Vazamento de memória em livros ilustrados.
- Expressão regular de citações podia congelar o app por segundos.
- Esc no editor de notas descartava a anotação (agora salva).
- Estado da busca, quadros e IA vazava de um livro para outro.
- Servidor de IA local podia subir duas vezes e ficar órfão.
- Exclusão de pasta-mãe, seletor de pastas com nomes repetidos, capa
  recriada sem salvar, notas órfãs ao remover livro, entre outros.
- Atualização de modelos de IA (Claude Opus 5.5, Gemini Flash atual).
