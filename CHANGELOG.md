# Changelog

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
