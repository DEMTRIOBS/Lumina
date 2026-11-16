# Changelog

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
