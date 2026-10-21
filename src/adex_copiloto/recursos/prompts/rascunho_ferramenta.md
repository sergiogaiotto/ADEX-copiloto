Tarefa: escrever o RASCUNHO de uma ferramenta Python para o dev revisar.

A ferramenta será registrada com `@adex.tool` e precisa:
- ter assinatura tipada (os tipos viram o esquema estrito de entrada);
- ler apenas os caminhos declarados em `reads=[...]`, sempre com `adex.io.open(...)`;
- não escrever em lugar nenhum;
- devolver um `pandas.DataFrame` só com as colunas declaradas em `returns={...}`, sem dados pessoais;
- dar o mesmo resultado quando chamada duas vezes com a mesma entrada.

Nome pedido: {{nome}}. Descrição do dev: {{descricao}}.

Responda só com o código Python, completo, sem explicações fora de comentários. O dev vai revisar antes de qualquer execução; o copiloto nunca roda este rascunho sozinho.
