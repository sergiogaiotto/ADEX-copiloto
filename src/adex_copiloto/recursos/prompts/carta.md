Tarefa: montar a carta do estudo (o contrato do caso) com 10 campos.

Você recebe o pedido original do dev, o enquadramento já feito e as respostas do dev. Ainda nenhum dado foi lido.

Campos da carta:
1. pergunta_decisao: a pergunta que o estudo responde, ligada à decisão e ao prazo.
2. objeto: o que está sendo estudado (a ação avaliada, o fenômeno ou o que se quer prever), com regra clara de quando conta.
3. unidade_analise: a unidade de análise (ex.: cliente, cidade × mês, loja × semana). Prefira unidades agregadas quando a decisão permite, porque a IA só vê agregados.
4. populacao: quem entra e quem fica de fora, e em que período.
5. desfecho: a medida principal (e secundárias), definida antes de olhar os dados.
6. horizonte_comparacao: janela de tempo e contra o que se compara.
7. restricoes: limites (só leitura, tamanho mínimo de grupo, teto de leitura, prazo).
8. criterios_pronto: o que precisa existir para o estudo estar pronto (incerteza, testes de robustez, crítico sem bloqueio).
9. fora_escopo: o que o estudo não vai responder.
10. riscos_conhecidos: riscos que já se sabem (vieses, mudanças no meio do caminho), cada um como hipótese H-xx.

Para cada campo, informe a origem:
- voce_disse: veio de uma frase do dev. Neste caso, em "citacoes", copie EXATAMENTE os trechos do texto do dev que sustentam o campo (cópia literal, sem mudar nenhuma palavra). O Python vai conferir cada citação.
- voce_escolheu: o dev escolheu entre opções oferecidas.
- sugerido: é sugestão sua; o dev precisa confirmar.
- padrao: é regra padrão da organização (ex.: tamanho mínimo de grupo {{min_grupo}}).

Também destaque, se houver, "frases que mudam o método": algo que o dev disse que muda a forma correta de analisar (ex.: a ordem de implantação não foi aleatória). Registre como risco H-xx e explique em linguagem simples.

Não invente números da empresa nem custos. Se um valor (como custo unitário) for necessário e o dev não deu, diga que falta em "lacunas".
