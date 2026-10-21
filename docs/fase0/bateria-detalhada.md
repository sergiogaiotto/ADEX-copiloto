# Fase 0 · Especificação detalhada da bateria de homologação do GPU Hub

Gerado em 19/10/2026 a partir da pesquisa nas docs oficiais (vLLM, LiteLLM, card do Qwen3.8-27B, OCI Data Science) e de uma revisão adversarial por bloco. **Nada aqui foi medido no hub ainda**: são especificações de teste. O roteiro resumido, com perguntas ao CCOE, decisões e cronograma, está no documento compartilhado "Roteiro da Fase 0 · Homologação do GPU Hub".

Regras para todos os testes: só dados inventados; a chave vem de `ADEX_LLM_KEY` ou do OCI Vault e nunca é impressa; o SDK da OpenAI roda com `max_retries=0` e timeout explícito; cada chamada registra data, URL, modelo devolvido, parâmetros, `usage`, headers `x-litellm-*`, tempo até o primeiro token e tempo total.

## Índice

- **A · Contrato da API e controle do raciocínio** — A1, A2, A3, A4, A5, A6, A7, A8, A9, A10, A11, A12, A13, A14
- **B · Saída estruturada (JSON) e chamadas de ferramenta** — B1, B2, B3, B4, B5, B6, B7, B8, B9, B10, B11, B12
- **Bloco C · Visão, contexto longo, cache de prefixo e desempenho** — C1, C2, C3, C4, C5, C6, C7, C8, C9, C10, C11, C12
- **D · Operação, segurança, governança e troca silenciosa do modelo** — D1, D2, D3, D4, D5, D6, D7, D8, D9, D10, D11, D12, D13, D14
- **E · Mini-avaliação de qualidade nas tarefas do copiloto (pt-BR)** — E0, E1, E2, E3, E4, E5, E6, E7, E8, E9, E10, E11


---

## A · Contrato da API e controle do raciocínio

São 14 testes curtos, todos com dados inventados: os 12 originais, revisados, e 2 novos (A13, raciocínio junto com json_schema; A14, entrada de imagem). Eles mostram como o GPU Hub (LiteLLM na frente de um vLLM) trata de verdade cada parâmetro que o copiloto pretende usar. A leitura das fontes oficiais (código do vLLM e do LiteLLM na branch main de 19/10/2026; card, template e tokenizer do Qwen3.8-27B) define o jeito de testar em cinco pontos.

(1) Uma resposta 200 não prova nada. O vLLM aceita campos desconhecidos e os ignora: o aviso vai só para o log, em nível debug. Ele também descarta, sem avisar, as chaves de chat_template_kwargs que o template não usa. Por isso cada teste mede um efeito observável.

(2) No template oficial do Qwen3.8, reasoning_effort não é teto. É uma frase posta no começo do prompt de sistema: xhigh e low têm frase própria, medium não tem nenhuma, e omitir o campo equivale a xhigh. O efeito aparece de forma exata no usage.prompt_tokens. Calculamos os valores esperados com o template e o tokenizer oficiais: para a mensagem 'Diga OK.', medium dá 14, sem raciocínio 16, low 44, e xhigh ou omitido 56. O vLLM atual aceita no topo none, minimal, low, medium, high, xhigh e max, e 'none' desliga o raciocínio. Os valores que o template não conhece (high, minimal, max) caem numa exceção do template e voltam 400, mas só com o raciocínio ligado. O teto duro de raciocínio é o thinking_token_budget do vLLM. No vLLM atual, ele dá 400 se o servidor não tiver parser de raciocínio configurado.

(3) Teto de saída. No vLLM atual, um max_tokens maior que o max_model_len dá 400. Se o valor cabe no max_model_len mas passa do espaço que sobra no contexto, é reduzido em silêncio. Um teto do servidor (max_new_tokens) também corta em silêncio. Já o LiteLLM, se o modify_params estiver ligado, corta max_tokens pelo max_output_tokens configurado no model_info, sem aviso e sem mexer em max_completion_tokens. Por isso o teto real se mede pelo completion_tokens.

(4) O LiteLLM repassa por extra_body os parâmetros fora do padrão OpenAI (top_k, min_p, repetition_penalty, chat_template_kwargs, thinking_token_budget). Os parâmetros do padrão que ele julga sem suporte dão 400 'litellm.UnsupportedParamsError', ou somem se drop_params estiver ligado; additional_drop_params remove qualquer um. Ele também renomeia reasoning para reasoning_content. No openai SDK, o extra_body vira campos no topo do JSON: é o mesmo pedido do curl, não um segundo caminho.

(5) Segurança. A chave vem de variável de ambiente ou do OCI Vault e nunca aparece em tela, em célula de notebook nem em arquivo. Nada de curl -v (mostra a chave) nem -k (desliga a checagem do certificado). Todo corpo de erro passa por um filtro antes de ser salvo, porque o 401 do LiteLLM pode trazer a chave mascarada ou o hash dela.

Tudo roda com curl ou com o openai SDK (sempre com max_retries=0, para os erros não ficarem escondidos), e vários testes custam quase nada (max_tokens=1, olhando só o prompt_tokens). Os testes pesados (A5, A8 e a rajada do A11) são combinados antes com o CCOE, porque o hub é compartilhado. Ordem sugerida: A1; depois A2 e A3; depois A5 e A6; depois o resto. Ressalva: os comportamentos citados vêm do código atual. O hub pode rodar versões mais antigas, e é isso que a bateria vai revelar.


### A1 · Inventário do endpoint, rede e metadados

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* —

**Pergunta.** Qual endereço funciona, com certificado válido, nos dois ambientes? Quais modelos a chave enxerga? E o que o hub revela sobre si (modelo real, versão, limites)?

**Por que importa.** Fixa a base URL na configuração da lib: a doc diz -lab, mas em 16/10 só a URL sem -lab respondeu. Mostra se o contexto e o teto de saída vêm de metadados ou se viram constantes combinadas com o CCOE. Atenção: o /v1/models do LiteLLM lista os apelidos dele (id, owned_by) e não traz o max_model_len do vLLM; e o que o /model/info mostra é configuração escrita à mão, que sozinha não limita nada. O limite real só o A5 mede. O teste também dá identificadores do deploy para a trilha de auditoria, porque 'qwen-latest' é um apelido e pode passar a apontar para outro modelo sem aviso.

**Como medir.**

1) Mantenha a chave longe do código e da tela. Na máquina local (bash ou zsh): read -rs ADEX_LLM_KEY && export ADEX_LLM_KEY. Assim ela não aparece na tela nem no histórico. Em máquina compartilhada, prefira um arquivo de header com permissão 600 e curl -H @arquivo, porque um -H com a chave aparece na lista de processos. No notebook, leia a chave do OCI Vault com resource principal (oci.auth.signers.get_resource_principals_signer() e oci.secrets.SecretsClient.get_secret_bundle; o conteúdo vem em base64) e grave só em os.environ. Nunca use %env nem cole a chave numa célula, porque o .ipynb guarda células e saídas. Nunca use curl -v (mostra o header Authorization) nem -k (desliga a checagem do certificado). Use -D -, que mostra só os headers da resposta. 2) Chame GET /v1/models nas duas bases (https://hub-gpus-lab.usto.re/v1 e https://hub-gpus.usto.re/v1) e anote o status e os ids. Se o TLS falhar, anote o erro (cadeia de certificado) em vez de desligar a checagem. 3) Com a própria chave, tente GET /v1/models/qwen-latest, /model/info, /v1/model/info, /model_group/info e /key/info, e POST /utils/token_counter e /tokenize (rota do vLLM, que só existe se o CCOE a tiver exposto). Anote 200, 401, 403 ou 404. Guarde só os campos escolhidos: max_input_tokens, max_output_tokens, max_tokens, rpm_limit, tpm_limit, max_parallel_requests, models e expires. Não guarde token nem key, porque o /key/info devolve o hash da chave. No /utils/token_counter, anote o tokenizer_type: se não for o do Qwen, a contagem é só uma estimativa. 4) Faça um chat mínimo sem raciocínio (max_tokens 16). Salve os headers x-litellm-version, x-litellm-model-id, x-litellm-model-group, x-litellm-model-api-base, x-litellm-response-cost, x-litellm-attempted-retries, x-litellm-attempted-fallbacks, x-ratelimit-* e llm_provider-*. Do corpo, salve model, system_fingerprint e o usage completo. 5) Rode as variantes (a), (c) e (f) do A2 (max_tokens=1) e guarde o vetor de prompt_tokens. Ele só muda se o tokenizer ou o template mudarem. 6) Repita tudo dentro de um notebook do OCI Data Science e compare. Antes de salvar qualquer corpo de erro, passe um filtro que troque qualquer trecho 'sk-…' e 'Bearer …' por [REDACTED].

**Exemplo de requisição.**

```bash
curl -s -D - "$ADEX_LLM_BASE/models" -H "Authorization: Bearer $ADEX_LLM_KEY"
curl -s -D - "$ADEX_LLM_BASE/chat/completions" -H "Authorization: Bearer $ADEX_LLM_KEY" -H "Content-Type: application/json" -d '{"model":"qwen-latest","max_tokens":16,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Diga OK."}]}'
# notebook (sem imprimir): os.environ["ADEX_LLM_KEY"] = base64.b64decode(SecretsClient(config={}, signer=signer).get_secret_bundle(SECRET_OCID).data.secret_bundle_content.content).decode()
```

**Métrica.** Status por URL e por endpoint; resultado do TLS; lista de ids; campos de limite encontrados e de onde vieram; conjunto de headers; vetor de prompt_tokens da sonda; igualdade entre local e notebook.

**Critério de aprovação.** Aprovado quando: (a) uma única base URL responde 200 nos dois ambientes, com o certificado verificado (sem -k e sem verify=False); (b) /v1/models lista exatamente qwen-latest e openai/gpt-oss-20b; (c) existem pelo menos dois sinais estáveis do deploy para gravar em cada chamada, o x-litellm-model-id e o vetor de prompt_tokens da sonda, mais o campo model se ele trouxer o nome real (Qwen/Qwen3.8-27B); (d) x-litellm-version, x-litellm-model-id e o vetor da sonda são iguais no local e no notebook; (e) x-litellm-attempted-fallbacks vem 0 ou ausente, ou seja, quem respondeu foi o qwen. Se (d) falhar, a bateria inteira passa a rodar nos dois ambientes.

**Se falhar.** Faltam metadados de contexto, versão ou limite: o CCOE informa (ver perguntas), e a lib guarda os valores como constantes datadas. A -lab é a oficial e continua dando 401: o CCOE corrige a chave ou a documentação. Não há identificador do deploy: a lib grava model, x-litellm-model-id e o vetor da sonda. O vetor pega troca de tokenizer ou de template, mas não pega troca de pesos ou de quantização com o mesmo template; por isso o CCOE ainda precisa se comprometer a avisar antes. O TLS falha só no notebook: o time OCI instala a cadeia de certificados (SSL_CERT_FILE ou REQUESTS_CA_BUNDLE), nunca verify=False. O notebook não alcança o hub: com a rede padrão do Data Science há saída para a internet; com rede própria, a sub-rede precisa de NAT gateway (ou de um endpoint privado), e o CCOE confere se existe lista de IPs liberados.


### A2 · Impressão digital do template (liga/desliga, nível, preservar raciocínio)

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* A1

**Pergunta.** Os controles de raciocínio que mandamos chegam de fato ao modelo, ou somem no caminho sem erro?

**Por que importa.** O vLLM ignora campos desconhecidos (só um log em nível debug) e descarta em silêncio as chaves de chat_template_kwargs que o template não usa. O LiteLLM pode descartar parâmetros por configuração. Por isso uma resposta 200 não prova nada. Este teste usa uma prova determinística: o template oficial muda o texto do prompt conforme enable_thinking, reasoning_effort e preserve_thinking, e isso altera o usage.prompt_tokens. Calculamos os valores esperados com o template e o tokenizer oficiais do Hugging Face (19/10/2026): com a mensagem 'Diga OK.' e sem prompt de sistema, medium dá 14, enable_thinking=false 16, low 44, e xhigh ou omitido 56. O resultado define por qual caminho a lib manda cada controle: reasoning_effort no topo da requisição (como no card do Qwen) ou dentro de chat_template_kwargs (como na receita do vLLM). A posição da frase (começo do prompt de sistema) é um fato do template; o prompt_tokens conta tokens, não mede posição.

**Como medir.**

1) Use sempre a mesma mensagem ('Diga OK.'), sem prompt de sistema, com max_tokens=1. Só interessa o usage.prompt_tokens, então o custo é mínimo. 2) Rode estas variantes: (a) sem nenhum controle; (b) reasoning_effort 'low' no topo; (c) 'medium' no topo; (d) 'xhigh' no topo; (e) chat_template_kwargs.reasoning_effort 'low'; (f) chat_template_kwargs.enable_thinking=false; (g) reasoning_effort 'none' no topo; (h) controle negativo, com o campo inventado 'parametro_inexistente_adex':1; (i) a chave literal "extra_body":{"chat_template_kwargs":{"enable_thinking":false}}, que o LiteLLM desembrulha e o vLLM direto ignoraria. No openai SDK, extra_body não é outro caminho: o SDK junta o conteúdo no topo do JSON, igual ao curl. Basta testar pelo curl. 3) preserve_thinking: use o histórico inventado do A9 (user, assistant com reasoning_content, user), reasoning_effort low, e compare chat_template_kwargs.preserve_thinking true com false. Esperado: 91 e 76. 4) Repita cada variante 2 vezes: o prompt_tokens não pode variar. 5) Se algum caso der 4xx, anote a mensagem. 'litellm.UnsupportedParamsError' aponta para o LiteLLM; 'Unexpected reasoning effort' aponta para o template.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":1,"messages":[{"role":"user","content":"Diga OK."}],"reasoning_effort":"low"}   → esperado 44
Variar: sem o campo (56) | "medium" (14) | "xhigh" (56) | "none" (16) | "chat_template_kwargs":{"reasoning_effort":"low"} (44) | "chat_template_kwargs":{"enable_thinking":false} (16) | "parametro_inexistente_adex":1 (56) | "extra_body":{"chat_template_kwargs":{"enable_thinking":false}} (16). Ler usage.prompt_tokens.
```

**Métrica.** usage.prompt_tokens por variante; diferença de cada uma em relação a (c); status HTTP e mensagem de erro, quando houver.

**Critério de aprovação.** Aprovado quando as diferenças batem com o template: P(f) − P(c) = 2, P(b) − P(c) = 30, P(d) − P(c) = 42 e P(a) = P(d) = P(h). Também P(e) = P(b), ou pelo menos um dos dois caminhos do nível (topo ou chat_template_kwargs) dá a diferença certa. Com preserve_thinking=false, o histórico cai de 91 para 76 (−15). O controle negativo (h) volta 200 com P(h) = P(a). Valores absolutos iguais aos previstos (14, 16, 44 e 56) confirmam que o template e o tokenizer são os oficiais. Se só as diferenças baterem, há texto extra no prompt (por exemplo, um prompt de sistema padrão do servidor): registrar e perguntar ao CCOE. (g) e (i) são só registrados; no vLLM atual, 'none' desliga o raciocínio e deve dar 16.

**Se falhar.** O nível passa só por um dos caminhos: a lib usa só esse, e nunca os dois juntos (no vLLM atual, o valor do topo sobrescreve o de chat_template_kwargs). Nenhum caminho passa: o CCOE confere a configuração do LiteLLM (drop_params, additional_drop_params, allowed_openai_params, prefixo hosted_vllm/ ou openai/) e se o vLLM usa um --chat-template próprio. Enquanto isso, a lib pode escrever no prompt de sistema a frase de low. Ressalva: se o nível não chega ao template, o template continua pondo a frase padrão de xhigh, e a frase da lib entra depois, contraditória; esse efeito precisa ser medido com o método do A8 antes de valer como solução. enable_thinking não passa: testar reasoning_effort 'none' e a variante (i). Se nada desligar o raciocínio, o caso é bloqueante, porque os perfis sem raciocínio (escolha de bloco, extração) dependem disso; a resolução é com o CCOE.


### A3 · Onde vem o raciocínio (campo, streaming, contagem)

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* A1

**Pergunta.** Em que campo da resposta o raciocínio chega, com e sem streaming? E como sabemos quantos tokens ele gastou?

**Por que importa.** O leitor de respostas da lib e a conta de custo por perfil dependem disso. O vLLM atual devolve o raciocínio em reasoning (o nome antigo era reasoning_content), e o LiteLLM o converte em reasoning_content, com e sem streaming. Se o parser de raciocínio estiver desligado, o pensamento vaza para o content: o JSON quebra e o pensamento pode aparecer na narrativa mostrada ao dev. Como o template já põe '<think>' no fim do prompt, o vazamento aparece só como texto seguido de '</think>'. O recorte de reserva do LiteLLM exige as duas tags, então também não resolve esse caso. O vLLM só preenche completion_tokens_details.reasoning_tokens quando o servidor roda com --reasoning-parser.

**Como medir.**

1) Mande a mesma pergunta inventada com raciocínio ligado (reasoning_effort low, max_tokens 2048), 5 vezes sem streaming. Anote as chaves de choices[0].message (reasoning_content, reasoning, provider_specific_fields), procure '</think>' e '<think>' dentro do content, e veja se existe usage.completion_tokens_details.reasoning_tokens. 2) Repita 5 vezes com stream=true e stream_options.include_usage=true. Anote as chaves que aparecem nos deltas, junte os pedaços do raciocínio e confira se o último chunk (que vem com choices vazio) traz o usage. 3) Compare o prompt_tokens do streaming com o da chamada sem streaming, para a mesma pergunta. Tem de ser idêntico. Se diferir, o usage do streaming é uma estimativa do LiteLLM, que recalcula com um tokenizer próprio quando o vLLM não manda o usage. 4) Calibre a contagem com o truque da resposta mínima: peça que a resposta final seja só 'OK'. Assim completion_tokens ≈ reasoning_tokens + 4 a 6 ('</think>', as quebras de linha, 'OK' e o fim de turno somam 4 tokens no tokenizer oficial). 5) No openai SDK, os campos fora do padrão ficam em message.model_extra (ou via getattr) e, no streaming, em delta.model_extra. Registre a forma de leitura.

**Exemplo de requisição.**

```
from openai import OpenAI; import os
client = OpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0, timeout=300)
r = client.chat.completions.create(model="qwen-latest", messages=msgs, max_tokens=2048, reasoning_effort="low")
print(sorted(r.choices[0].message.model_dump(exclude_none=True)), r.usage)
# streaming: stream=True, stream_options={"include_usage": True}
```

**Métrica.** Nome do campo em cada modo; presença de reasoning_tokens; ocorrências de '</think>' no content; usage no último chunk; igualdade do prompt_tokens com e sem streaming.

**Critério de aprovação.** Aprovado quando: o raciocínio vem num campo próprio em 10 de 10 chamadas (5 com e 5 sem streaming), com o nome registrado para cada modo; há zero '</think>' ou '<think>' no content; o número de tokens de raciocínio sai de reasoning_tokens ou, no mínimo, bate com o truque da resposta mínima; o prompt_tokens é igual com e sem streaming; com include_usage, o streaming entrega o usage no fim.

**Se falhar.** O raciocínio vaza no content: é bloqueante. O CCOE liga --reasoning-parser qwen3 (todos os comandos da receita do vLLM para o Qwen3.8 usam esse parser). Até lá, a lib corta o texto no '</think>'. Falta reasoning_tokens: a conta de custo usa completion_tokens menos uma estimativa do content, e o CCOE confere o parser e avalia atualizar o vLLM ou o LiteLLM. O streaming não traz usage, ou traz uma estimativa diferente da chamada sem streaming: o custo vem da chamada sem streaming ou do header x-litellm-response-cost, e o CCOE pode ligar --enable-force-include-usage no vLLM.


### A4 · Repasse dos parâmetros de amostragem

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* A1, A2

**Pergunta.** top_k, min_p, repetition_penalty, presence_penalty e seed chegam ao vLLM ou são ignorados?

**Por que importa.** A amostragem oficial do Qwen pede top_k=20, e a falta dele já foi ligada a loops de raciocínio. presence_penalty=1,5 é a recomendação do modo sem raciocínio, e seed entra na trilha de reprodutibilidade. Esses campos não mexem no prompt, então a prova vem do comportamento: um valor exagerado tem de causar um efeito óbvio. Atenção: com --generation-config auto (o padrão do vLLM), o servidor aplica os valores do generation_config.json oficial (temperature 1.0, top_p 0.95, top_k 20) a todo campo que a requisição omite. Um perfil que mande só temperature 0.7 ganharia top_p 0.95, e não o 0.8 recomendado.

**Como medir.**

Todas as variantes sem raciocínio (enable_thinking=false), com max_tokens 60, 5 repetições e a mesma pergunta criativa inventada. 0) Controle guloso: temperature 0. Anote D0, o número de respostas distintas (esperado 1). 1) Base: temperature 1.0, sem top_k. Anote Db. A base precisa dar pelo menos 3 respostas distintas; se não der, troque a pergunta ou suba a temperatura para 1.5 (o vLLM recusa valores acima de 2). 2) top_k=1, com temperature 1.0. 3) min_p=1.0 (só sobra o token mais provável), com temperature 1.0. 4) repetition_penalty: peça 'escreva a palavra banana 40 vezes' e compare 1.0 com 2.0, contando as ocorrências de 'banana'. 5) presence_penalty: o mesmo pedido, 0 contra 2.0 (o máximo aceito). 6) seed, com temperature 1.0: 5 vezes com seed=1234, 5 vezes sem seed e 1 vez com seed=4321. Mande os campos no topo do JSON. No SDK, o extra_body gera exatamente o mesmo JSON.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","temperature":1.0,"top_k":1,"max_tokens":60,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Invente um nome para uma padaria fictícia."}]}
SDK (mesmo JSON na saída): extra_body={"top_k": 1, "chat_template_kwargs": {"enable_thinking": False}}
```

**Métrica.** Respostas distintas em 5, por variante (D0, Db e as demais); contagem de 'banana'; igualdade das respostas por seed.

**Critério de aprovação.** Aprovado parâmetro a parâmetro, todos com status 200 e com a pré-condição Db ≥ 3. top_k=1 e min_p=1.0 ficam com número de respostas distintas ≤ D0, ou seja, tão estáveis quanto o guloso. Com repetition_penalty=2.0 e com presence_penalty=2.0, 'banana' aparece no máximo metade das vezes da base; esse é um limiar para detectar o efeito, não uma meta de qualidade. seed=1234 dá a mesma resposta em pelo menos 4 de 5, enquanto sem seed saem pelo menos 3 distintas, e seed=4321 difere da resposta de 1234.

**Se falhar.** Parâmetro ignorado: o CCOE confere a configuração do LiteLLM. top_k, min_p e repetition_penalty vão no extra_body e só somem por additional_drop_params ou por valor fixo em litellm_params. presence_penalty e seed estão na lista de parâmetros suportados que o LiteLLM usa para provedores compatíveis com OpenAI, então o drop_params não os remove; se sumirem, a causa é additional_drop_params ou valor fixo. Para top_k: se o CCOE confirmar --generation-config auto pela linha de log da inicialização ('Default vLLM sampling parameters have been overridden by the model's generation_config.json'), um top_k omitido já vale 20, e o problema deixa de ser bloqueante. seed ignorado ou instável: o A12 decide que os testes da lib comparam estrutura, não texto.


### A5 · Teto real de saída e limite de contexto

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* A1, A2

**Pergunta.** Qual o maior tamanho de resposta que o hub entrega numa chamada? E pedir acima disso dá erro ou é cortado em silêncio?

**Por que importa.** É a decisão mais cara do desenho: os perfis de plano (16K de raciocínio) e de caça de riscos (12K) só existem se a saída passar de 8.192 tokens. O 'até 8192' do PDF pode ter quatro origens: (1) um teto no vLLM (max_new_tokens no --override-generation-config), que corta em silêncio; (2) o modify_params do LiteLLM, que corta max_tokens em silêncio para o max_output_tokens do model_info, mas não mexe em max_completion_tokens; (3) um metadado do LiteLLM que não limita nada; (4) confusão com o contexto de 8.192 do gpt-oss-20b. No vLLM atual, max_tokens acima do max_model_len dá 400. Se o valor cabe no max_model_len mas passa do espaço que sobra no contexto, é reduzido sem aviso. O teste também mede se uma chamada longa sobrevive à rede sem streaming: proxies reversos costumam ter um tempo limite padrão de leitura (o do nginx, por exemplo, é de 60 s).

**Como medir.**

Combine o horário com o CCOE, porque as chamadas são longas. 1) Use um pedido sem raciocínio que gere saída longa e fácil de conferir: 'Liste os números de 1 a 9000, um por linha, sem pular nenhum'. Isso dá cerca de 43,9 mil tokens no tokenizer oficial. Se o modelo abreviar, troque a tarefa. 2) Rode com max_tokens 4096, 8192, 8193, 12288, 16384, 20480 e 32768. Repita 8192 e 20480 com max_completion_tokens no lugar de max_tokens: o vLLM dá prioridade a max_completion_tokens, e o corte do LiteLLM (modify_params) só age sobre max_tokens. 3) Em cada caso, anote status, completion_tokens, finish_reason, duração no cliente e o header x-litellm-response-duration-ms. 4) max_tokens omitido: rode com streaming, conte os tokens e corte a conexão no cliente ao chegar em ~21 mil, se ainda estiver gerando. Isso basta para saber se o padrão é de pelo menos 20.480, sem prender a GPU até o fim. 5) Limite de contexto, com 'Diga OK.' e sem raciocínio: (i) max_tokens=300000, que deve dar 400 citando o max_model_len; (ii) max_tokens=196600, em que prompt + pedido passa de 196.608, mas o pedido sozinho não. No vLLM atual, é aceito com corte silencioso e termina em poucos tokens; nas versões antigas, dá 400. 6) Duração: repita o maior valor aceito sem streaming e com streaming, nos dois ambientes. Anote cortes do tipo 504, 524 ou conexão encerrada.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":20480,"temperature":0,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Liste os números de 1 a 9000, um por linha, sem pular nenhum."}]}
Variar: "max_completion_tokens":20480 no lugar de "max_tokens".
```

**Métrica.** Maior completion_tokens alcançado, por campo (max_tokens e max_completion_tokens); finish_reason; status e mensagem; duração; sinal de corte silencioso (completion_tokens parado num mesmo valor abaixo do pedido); max_model_len citado no 400.

**Critério de aprovação.** Meta do desenho: uma chamada com teto de 20.480 (16K de raciocínio + 4K de resposta) devolve completion_tokens acima de 16.384, sem erro, terminando em 'length' exatamente em 20.480 ou em 'stop' antes disso. Aprovado pleno se vale com e sem streaming e com os dois campos. Aprovado parcial se só vale com streaming (a lib usa streaming nos perfis longos) ou só com max_completion_tokens (a lib usa esse campo enquanto o CCOE corrige o LiteLLM). Reprovado se completion_tokens trava em 8.192 (ou em outro valor fixo) nos dois campos, ou se aparece erro acima de 8.192. Em todos os casos, ficam registrados o padrão aplicado quando max_tokens é omitido, o max_model_len real e o comportamento do pedido de 196.600.

**Se falhar.** Pedir ao CCOE a origem do teto (perguntas sobre modify_params, model_info e max_new_tokens) e um teto de pelo menos 20.480 para o qwen-latest. Se não der, replanejar para raciocínio de até ~6K (com thinking_token_budget, A7) mais até 2K de resposta: o plano passa a ser uma chamada por 'jeito de enquadrar', o crítico segue separado e a caça de riscos vira dois passes. Se a rede corta chamadas longas sem streaming: a lib usa streaming por dentro e devolve o resultado inteiro.


### A6 · Raciocínio dentro do max_tokens e corte por length

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* A2, A3

**Pergunta.** O raciocínio gasta o mesmo max_tokens da resposta? E o que volta quando o teto acaba no meio do pensamento?

**Por que importa.** Define a regra de orçamento da lib (max_tokens = teto de raciocínio + teto de resposta + margem) e o detector de 'resposta inválida'. Sem isso, o dev veria uma carta do estudo em branco ou um JSON pela metade sem saber o motivo. No parser qwen3 atual, uma saída sem '</think>' fica inteira no campo de raciocínio. Em versões antigas, ela ia para o content, e aí o pensamento cortado chegaria à lib como se fosse resposta.

**Como medir.**

1) Use um quebra-cabeça lógico inventado (por exemplo, cinco pessoas fictícias, cinco cidades e pistas), com raciocínio xhigh e a amostragem oficial (temperature 1.0, top_p 0.95, top_k 20). 2) Rode com max_tokens 256, 1024 e 4096, 3 vezes cada, sem streaming. Anote o content (null, vazio ou texto), se ele contém '</think>', o tamanho do raciocínio, reasoning_tokens, completion_tokens e finish_reason. 3) Repita o caso de 256 com streaming e leia o finish_reason do último chunk que tem choices. Com include_usage, o último chunk de todos vem com choices vazio e só o usage. 4) Repita com response_format json_schema, usando um max_tokens que corte no meio do JSON. Confira que vem finish_reason='length' e que o JSON é inválido. 5) Confira que completion_tokens ≤ max_tokens e que completion_tokens fica perto de reasoning_tokens + tokens do content.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":256,"reasoning_effort":"xhigh","temperature":1.0,"top_p":0.95,"top_k":20,"messages":[{"role":"user","content":"<quebra-cabeça lógico inventado>"}]}
Esperado: finish_reason "length", content vazio ou null, texto no campo de raciocínio.
```

**Métrica.** finish_reason; completion_tokens contra max_tokens; reasoning_tokens; content vazio ou não; onde ficou o texto cortado; JSON válido ou não.

**Critério de aprovação.** Aprovado quando: em 100% dos casos cortados vem finish_reason='length', com e sem streaming; completion_tokens nunca passa de max_tokens e inclui o raciocínio; um corte no meio do raciocínio deixa o content vazio ou null e o texto no campo de raciocínio, com a forma exata registrada; nenhum caso devolve finish_reason='stop' com content vazio (este teste não usa stop).

**Se falhar.** O raciocínio cortado chega no content: é o comportamento de parser antigo. O CCOE atualiza, e a lib descarta o content sempre que finish_reason for diferente de 'stop'. finish_reason não confiável: a lib trata content vazio ou JSON inválido como falha, seja qual for o finish_reason. Regra fixa em todos os casos (vale também para o A10): a lib só aceita uma resposta quando finish_reason='stop', o content não está vazio, não tem '</think>' e, se for JSON, é válido no esquema. Fora disso, retenta com teto maior ou esforço menor, preferindo o thinking_token_budget (A7) para garantir espaço para a resposta.


### A7 · Teto duro de raciocínio (thinking_token_budget)

*Quem:* nós + CCOE · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* A2, A3, A6

**Pergunta.** Dá para limitar o raciocínio a N tokens por chamada e ainda receber a resposta completa?

**Por que importa.** No template do Qwen3.8, reasoning_effort só pede: é uma frase no prompt, não um limite. O único limite duro documentado é o thinking_token_budget do vLLM: quando o orçamento acaba, o vLLM força o marcador de fim do raciocínio (reasoning_end_str) e o modelo passa para a resposta. No vLLM atual, se o servidor não tiver --reasoning-parser ou --reasoning-config, a chamada falha com 400 ('thinking_token_budget is set but reasoning_config is not configured'); em versões antigas, o campo não existe e é ignorado em silêncio. Os tetos dos perfis (4K no enquadramento, 12K na caça de riscos, 16K no plano) só ficam garantidos se isso funcionar através do LiteLLM.

**Como medir.**

1) Use o mesmo quebra-cabeça do A6, xhigh, sem orçamento, 3 vezes. Anote R0, a mediana de reasoning_tokens. Os orçamentos testados precisam ficar abaixo de R0. 2) Teste orçamentos de 256, 1024 e cerca de R0/2, 3 vezes cada, com max_tokens = orçamento + 2048. 3) Rode 1 vez com orçamento 0, para ver se ele pula o raciocínio. 4) Anote reasoning_tokens (ou use o truque da resposta mínima do A3), se o content veio preenchido, o finish_reason e se a resposta está certa. Leia o fim do raciocínio para ver se apareceu uma frase de transição forçada (só aparece se o reasoning_end_str do servidor tiver uma). 5) Mande o campo no topo do JSON. Não use o parâmetro thinking do LiteLLM (estilo Anthropic): para hosted_vllm, ele vira reasoning_effort 'high' ou 'minimal', que o template do Qwen recusa.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":3072,"reasoning_effort":"xhigh","thinking_token_budget":1024,"temperature":1.0,"top_p":0.95,"top_k":20,"messages":[{"role":"user","content":"<quebra-cabeça inventado>"}]}
```

**Métrica.** R0; reasoning_tokens dividido pelo orçamento; % de chamadas com content preenchido e finish_reason 'stop'; acerto; status e mensagem, se houver erro.

**Critério de aprovação.** Pré-condição: R0 maior que o maior orçamento testado; se não for, o teste não conclui nada e precisa de uma tarefa mais difícil. Aprovado quando, com orçamento, reasoning_tokens fica em no máximo orçamento + M em 100% das execuções, e o content vem preenchido com finish_reason='stop' em 100%. M é o número de tokens do reasoning_end_str configurado mais 5; sem essa informação do CCOE, use M = 32.

**Se falhar.** Vem 400 com 'reasoning_config is not configured': o CCOE liga --reasoning-parser qwen3 (ou --reasoning-config com reasoning_start_str '<think>' e reasoning_end_str '</think>'). Vem 200 e o orçamento é ignorado: o vLLM é antigo, e o CCOE avalia atualizar. O orçamento é respeitado mas o content vem vazio: falta folga no max_tokens, e é preciso aumentá-la. Até resolver, a lib (1) dimensiona o max_tokens com folga, (2) usa uma escada de retentativa xhigh, medium, low e por fim sem raciocínio quando o content vem vazio e (3) reforça a brevidade no prompt de sistema. Sem teto duro, os 12K/16K dos perfis viram só um pedido, e a lib não pode prometer tempo de resposta.


### A8 · Níveis low / medium / xhigh e valores proibidos

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* A2, A3, A5

**Pergunta.** Os três níveis gastam quantidades bem diferentes de raciocínio? E o que acontece com 'high', 'minimal', 'max', 'none' e 'HIGH'?

**Por que importa.** A tabela de perfis usa três níveis. A comunidade relata que xhigh gasta cerca de 8 vezes mais sem ganho claro e que low fica parecido com medium; se os níveis não se separarem, os perfis encolhem. O template só aceita xhigh, medium e low, e omitir o campo equivale a xhigh, o nível mais caro. Os valores proibidos são barrados em lugares diferentes, e a lib precisa saber de onde vem cada erro.

**Como medir.**

Combine o horário com o CCOE (são muitas chamadas xhigh). 1) Escolha três tarefas inventadas de dificuldade crescente: extrair campos de um texto curto, enquadrar um estudo fictício e um quebra-cabeça lógico. 2) Rode cada tarefa nos 4 níveis (low, medium, xhigh e omitido), 3 vezes cada, sem thinking_token_budget, com o max_tokens alto aprovado no A5 e a amostragem oficial. 3) Anote reasoning_tokens, duração, x-litellm-response-cost e acerto (a extração e o quebra-cabeça têm gabarito). 4) Em chamadas avulsas com max_tokens 16, mande reasoning_effort 'high', 'minimal', 'max', 'none' e 'HIGH', e também 'high' com enable_thinking=false. Anote status, error.message e error.type. Esperado no vLLM atual: 'HIGH' dá 400 já na validação do vLLM (fora da lista que ele aceita); 'high', 'minimal' e 'max' passam pelo vLLM e caem na exceção do template, que vira 400 com 'Unexpected reasoning effort ...'; 'none' dá 200 sem raciocínio; 'high' com enable_thinking=false dá 200 com prompt_tokens 16, porque o template nem confere o nível. 5) Teste 'xhigh' no topo e dentro de chat_template_kwargs: vLLMs antigos só aceitavam low, medium e high no topo, e aí 'xhigh' daria 400 na validação.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":16,"reasoning_effort":"high","messages":[{"role":"user","content":"Diga OK."}]}
Esperado: 400 com "Unexpected reasoning effort high".
```

**Métrica.** Mediana de reasoning_tokens por nível e por tarefa; duração; acerto; status, tipo e mensagem de cada valor proibido.

**Critério de aprovação.** Aprovado quando: a mediana de low fica em no máximo metade da de xhigh em pelo menos 2 das 3 tarefas (meta mínima do desenho para justificar perfis separados, não uma previsão); o caso omitido se comporta como xhigh; 'high', 'minimal', 'max' e 'HIGH' com raciocínio ligado devolvem 4xx com mensagem legível, nunca 500 nem 200 silencioso; os comportamentos de 'none' e de 'high' sem raciocínio ficam registrados.

**Se falhar.** Os níveis não se separam: o controle real passa a ser o thinking_token_budget (A7), o nível vira só uma dica e os perfis ficam em 'sem raciocínio' e 'com raciocínio + teto'. Valor proibido dá 500 ou 200: a lib valida antes de enviar, só aceita {low, medium, xhigh} ou o desligamento explícito, e nunca usa o parâmetro thinking do LiteLLM. 'xhigh' no topo dá 400: a lib manda o nível por chat_template_kwargs.


### A9 · Multi-turno: reenviar ou não o raciocínio

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* A2, A3

**Pergunta.** Numa conversa de vários turnos, o raciocínio anterior volta para o modelo? Faz diferença mandá-lo em reasoning_content ou em reasoning? E preserve_thinking=false o descarta?

**Por que importa.** A entrevista e o ciclo de etapa e crítico têm vários turnos. O template do Qwen3.8 lê do histórico só reasoning_content e, com preserve_thinking=true (o padrão), mantém o raciocínio de todos os turnos: o contexto cresce rápido e cada turno fica mais caro. Com preserve ligado, o template põe um bloco de pensamento, mesmo vazio, em todo turno do assistente; com preserve=false, tira o bloco inteiro dos turnos anteriores à última pergunta. O vLLM atual aceita os dois nomes e copia reasoning para reasoning_content antes do template. O LiteLLM (hosted_vllm) mantém reasoning_content quando ele é texto e o descarta nos outros casos. A decisão é se a lib reenvia, resume ou descarta o raciocínio.

**Como medir.**

1) Monte um histórico inventado: user ('Guarde um código interno e responda só pronto.'), depois assistant (content 'pronto', raciocínio 'O código interno é 4721.'), depois user ('Qual era o código interno? Responda só o número.'). 2) Rode 4 variantes, 5 vezes cada, com reasoning_effort low: (a) raciocínio em reasoning_content; (b) raciocínio em reasoning; (c) sem raciocínio; (d) igual a (a), com chat_template_kwargs.preserve_thinking=false. 3) Anote o prompt_tokens (determinístico) e se a resposta traz 4721. Valores previstos pelo template e pelo tokenizer oficiais: (a) 91, (b) 91 no vLLM atual, (c) 80, (d) 76. 4) Faça uma conversa real de 4 turnos sobre um estudo fictício e compare o crescimento do prompt_tokens com e sem reenvio do raciocínio. Anote também se o idioma continua pt-BR.

**Exemplo de requisição.**

```
"messages":[{"role":"user","content":"Guarde um código interno e responda só pronto."},{"role":"assistant","content":"pronto","reasoning_content":"O código interno é 4721."},{"role":"user","content":"Qual era o código interno? Responda só o número."}]
Variar: trocar o nome do campo para "reasoning", removê-lo, ou somar "chat_template_kwargs":{"preserve_thinking":false}.
```

**Métrica.** prompt_tokens por variante e diferença em relação a (c); % de acerto do código; crescimento do prompt_tokens por turno.

**Critério de aprovação.** Aprovado quando: (a) fica em P(c) + 11 (91 no template oficial) e acerta 4721 em pelo menos 4 de 5; (c) não acerta; (b) fica igual a (a), ou, se ficar igual a (c), está provado que o campo reasoning some no caminho; (d) fica em P(c) − 4 (76) e não acerta, o que confirma o descarte; o crescimento por turno fica medido nos dois modos. Se o A2 mostrou deslocamento nos valores absolutos, valem as diferenças.

**Se falhar.** Nenhum nome é lido: o raciocínio antigo não volta. Quando precisar de continuidade, a lib guarda no content um resumo curto das conclusões (por exemplo, 'notas do copiloto'). Só reasoning_content é lido: a lib usa só esse nome. Os nomes são lidos e o crescimento pesa no orçamento: a lib não reenvia por padrão (ou usa preserve_thinking=false) e só preserva o raciocínio dentro de laços de ferramenta, onde o card do Qwen diz que ele ajuda.


### A10 · stop, n e logprobs

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* A2, A3

**Pergunta.** stop corta no lugar certo, mesmo com raciocínio? n devolve várias respostas? logprobs funciona?

**Por que importa.** stop: pelo código atual do vLLM, as strings de parada são conferidas no texto gerado inteiro, raciocínio incluído, porque o detokenizador não separa as partes. O esperado, então, é que uma palavra de parada que apareça no pensamento encerre a chamada com finish_reason 'stop' e content vazio. É o pior caso, porque parece sucesso; o teste confirma se o hub se comporta assim. n: o crítico poderia pedir k respostas e votar. logprobs: daria um grau de confiança barato na escolha de ferramenta ou de bloco (sem raciocínio).

**Como medir.**

1) stop sem raciocínio: peça três frases separadas por linha em branco, com stop=['\n\n'], 5 vezes. Confira o corte e que a string não aparece no content. 2) stop com raciocínio (low): pergunte quantas letras 'r' há em 'carro', com stop=['carro'], palavra que quase certamente aparece no raciocínio. Anote finish_reason, content, raciocínio e choices[0].stop_reason (campo do vLLM que diz qual string parou; o LiteLLM pode não repassar). 3) n=3, temperature 1.0, sem raciocínio: conte as choices e veja se a cobrança (usage, x-litellm-response-cost) conta o prompt uma vez só. Repita com raciocínio. Depois mande n=2 com temperature 0; o esperado é 400 ('n must be 1 when using greedy sampling'). 4) logprobs=true e top_logprobs=5, sem raciocínio, numa classificação A/B/C com max_tokens=1. Leia choices[0].logprobs.content[0].top_logprobs. Repita com raciocínio e compare o tamanho de logprobs.content com o completion_tokens, para ver se os logprobs cobrem o raciocínio. 5) Teste top_logprobs=21 (o padrão do vLLM é --max-logprobs 20) e top_logprobs sem logprobs, e anote os erros.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":1,"logprobs":true,"top_logprobs":5,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"A coluna faturamento_mensal de uma loja fictícia é A) data, B) valor ou C) texto? Responda só a letra."}]}
```

**Métrica.** Posição do corte, finish_reason e stop_reason; número de choices; presença e tamanho de logprobs; mensagens de erro.

**Critério de aprovação.** Aprovado quando: sem raciocínio, stop corta antes da string em 5 de 5; com raciocínio, o comportamento fica registrado sem ambiguidade (se stop age sobre o raciocínio e deixa o content vazio com 'stop', isso vira regra da lib); n=3 devolve 3 choices; n=2 com temperatura 0 devolve 4xx legível; logprobs devolve 5 alternativas para o primeiro token do content; top_logprobs=21 e top_logprobs sem logprobs devolvem 4xx legível.

**Se falhar.** stop corta o raciocínio: a lib proíbe stop com raciocínio ligado, usa json_schema para delimitar a saída e aplica a regra de aceitação do A6. n não funciona ou sai caro: chamadas paralelas, respeitando o limite de concorrência do A11. logprobs indisponível: a confiança vem de autoavaliação estruturada ou de votos, e a decisão final fica com o Python.


### A11 · Catálogo de erros, retentativas, limites e tempos

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* A1

**Pergunta.** Que código e que mensagem o hub devolve em cada tipo de falha? Onde o erro aparece (status HTTP, corpo ou no meio do streaming)? E quem retenta o quê no caminho?

**Por que importa.** A lib precisa transformar cada erro numa ação: retentar com espera (429, 503, timeout), corrigir a requisição (400), parar e avisar (401, 403, 404) ou abrir chamado no CCOE (500, como diz o PDF). E precisa mostrar ao dev uma mensagem clara em português. O LiteLLM tem um mapa próprio de exceções (BadRequestError, ContextWindowExceededError, RateLimitError, UnsupportedParamsError e outras), pode retentar e até desviar para outro modelo (fallback). O vLLM devolve error.message, type, param e code (o code é o número do status), e pode mandar um erro dentro de um streaming que começou com HTTP 200. O openai SDK, por padrão, repete 2 vezes erros de conexão, 408, 409, 429 e 5xx, e espera até 10 minutos. Isso esconde o erro, dobra a carga e falseia os tempos.

**Como medir.**

Use o SDK sempre com max_retries=0 e timeout explícito (ou curl). Provoque só erros baratos e inofensivos, cada um 2 vezes, nos dois ambientes. Para cada caso, anote status, error.message, error.type, error.code, os headers (incluindo x-litellm-attempted-retries e x-litellm-attempted-fallbacks) e o tempo. Antes de salvar qualquer corpo, passe o filtro de redação do A1: o 401 do LiteLLM pode trazer a chave mascarada (4 primeiros e 4 últimos caracteres) ou o hash dela. Casos: 1) chave falsa ('sk-teste-invalida'); 2) base -lab com a chave real (redação obrigatória); 3) model 'qwen-inexistente'; 4) reasoning_effort 'high' (esperado 400 vindo do template); 5) temperature 5 (no vLLM atual, 400 'temperature must be in [0, 2]'); 6) top_logprobs sem logprobs; 7) n=2 com temperature 0; 8) o caso 4 com stream=true, para ver se o erro vem como 4xx antes do stream ou como um chunk 'data: {"error": ...}' dentro de um HTTP 200; 9) se o A2 ou o A4 acharam um UnsupportedParamsError (400 com o prefixo 'litellm.UnsupportedParamsError'), registre a forma; 10) excesso de contexto na entrada (prompt acima de 196.608 tokens), só com aval do CCOE; 11) 429: não provoque por conta própria. Peça os limites (RPM, TPM, paralelismo) e, com aval do CCOE, faça uma rajada combinada de chamadas com max_tokens=1, anotando retry-after e x-ratelimit-*; 12) junte os timeouts observados no A5.

**Exemplo de requisição.**

```bash
curl -s -o erro.json -w "%{http_code}\n" "$ADEX_LLM_BASE/chat/completions" -H "Authorization: Bearer sk-teste-invalida" -H "Content-Type: application/json" -d '{"model":"qwen-latest","max_tokens":1,"messages":[{"role":"user","content":"oi"}]}'
SDK: OpenAI(..., max_retries=0, timeout=60)
```

**Métrica.** Tabela com caso, status, error.type e code, headers, retentativas e fallbacks do LiteLLM e tempo, comparada entre local e notebook.

**Critério de aprovação.** Aprovado quando: todo erro causado pelo cliente devolve 4xx (não 500) com corpo JSON e error.message; nenhum caso sem streaming devolve 200 com o erro escondido; o erro em streaming tem forma detectável e registrada; nenhuma resposta vem de fallback (x-litellm-attempted-fallbacks = 0 ou ausente); há um sinal de espera para o 429 (retry-after, x-ratelimit-reset-* ou janela informada pelo CCOE); o 401 da chave inválida e o 401 da -lab são distinguíveis, ou o CCOE explica a diferença; nenhum corpo salvo contém trecho da chave real.

**Se falhar.** Erros do cliente voltam como 500: a lib valida antes de enviar (nível de raciocínio, faixas de temperature e presence_penalty, logprobs ligado quando há top_logprobs, n=1 quando temperature é 0) e reconhece o erro pelo texto da mensagem. 429 sem sinal de espera: espera exponencial com jitter, poucas tentativas e depois um aviso claro ao dev. Erro dentro do streaming: o leitor da lib procura a chave 'error' em cada chunk. O LiteLLM desvia para outro modelo: a lib trata como falha (resposta de modelo não homologado), e o CCOE desliga o fallback para a nossa chave. Limites e tempos que faltarem entram nas perguntas ao CCOE.


### A12 · Determinismo com seed e temperatura 0

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* A2, A4

**Pergunta.** A mesma pergunta, com temperatura 0 ou com a mesma seed, devolve sempre a mesma resposta?

**Por que importa.** Define como a lib será testada. Se for determinístico, dá para ter testes de regressão por texto (golden files) nas etapas sem raciocínio; se não for, os testes comparam estrutura: esquema JSON, campos e marcadores. Pela doc de reprodutibilidade do vLLM, no modo servidor o resultado só fica imune à composição do lote (as requisições de outros usuários processadas junto) com o modo batch invariance, e a reprodutibilidade só vale no mesmo hardware e na mesma versão. Temperatura 0 vira escolha gulosa, e o vLLM recusa n>1 nesse caso. Os números da narrativa já vêm de marcadores do Python, então o determinismo não é requisito de exatidão, só de testabilidade e de trilha.

**Como medir.**

0) Confirme antes que não há um cache de respostas do LiteLLM no meio. Mande "cache":{"no-cache":true} (se a chave permitir) e confira que não volta o header x-litellm-cache-key, que os ids das respostas mudam e que a duração não cai perto de zero. Um cache ligado daria 10 de 10 respostas idênticas falsamente. 1) Prepare três pedidos inventados: extração para JSON com json_schema, escolha de bloco com resposta curta e um parágrafo de narrativa com o marcador {{n_clientes}}. 2) Rode três modos, 10 repetições cada: (a) temperature 0, sem raciocínio; (b) temperature 1.0 com seed=1234, sem raciocínio; (c) seed=1234 com raciocínio low e a amostragem oficial, só para registro. 3) Rode em dois horários diferentes, com cargas diferentes no hub. 4) Compare o sha256 do content e, no caso do JSON, a igualdade depois do parse.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","temperature":0,"seed":1234,"max_tokens":200,"chat_template_kwargs":{"enable_thinking":false},"cache":{"no-cache":true},"messages":[{"role":"user","content":"Opções: teste_t, regressao, contagem. Qual serve para comparar a média de vendas de duas lojas fictícias? Responda só o nome."}]}
```

**Métrica.** % de saídas idênticas por modo e por pedido (frequência do hash mais comum dividida por 10); % de JSONs iguais depois do parse; presença de x-litellm-cache-key.

**Critério de aprovação.** Pré-condição: nenhum sinal de cache de respostas. Meta do desenho: no modo (a), pelo menos 9 de 10 saídas idênticas em cada pedido, nos dois horários; aí os golden tests textuais valem para as etapas sem raciocínio. Aprovado condicional: 9 de 10 ou mais apenas na igualdade do JSON parseado. O modo (c) não tem meta, só registro.

**Se falhar.** Os testes da lib passam a comparar estrutura (esquema, campos obrigatórios, marcadores presentes, nenhum número fora de marcador) em vez de texto. A trilha grava seed e parâmetros mesmo assim. Perguntar ao CCOE se o modo batch invariance do vLLM (VLLM_BATCH_INVARIANT) é viável; não é pré-requisito. Se houver cache de respostas, pedir ao CCOE que o desligue para a nossa chave durante a homologação.


### A13 · Raciocínio e json_schema na mesma chamada

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* A2, A3, A6

**Pergunta.** Com o raciocínio ligado, o json_schema restringe só a resposta (o que vem depois do '</think>') e deixa o pensamento livre?

**Por que importa.** O perfil de enquadramento pede raciocínio medium e JSON estrito numa só chamada. Pela doc do vLLM, a gramática da saída estruturada fica suspensa enquanto o raciocínio não termina, e isso depende do parser de raciocínio. Sem ele, a gramática vale desde o primeiro token, e o pensamento some ou o JSON quebra. Além disso, os campos antigos guided_json e afins foram removidos do vLLM: ele os ignora (com aviso só no log do servidor) e deixa a saída sem restrição. A lib precisa usar response_format.

**Como medir.**

1) Use um esquema pequeno e uma tarefa inventada de enquadramento (por exemplo, uma rede fictícia de padarias quer entender a queda nas vendas de pão integral). 2) Rode 10 vezes sem streaming e 5 com streaming, com reasoning_effort medium e max_tokens 6144. 3) Rode 5 vezes com enable_thinking=false. 4) Para cada chamada, confira: o campo de raciocínio veio preenchido; o content passa no esquema (validador jsonschema); não há '</think>' nem texto de pensamento no content; finish_reason='stop'. 5) Controle negativo, 1 vez: o mesmo esquema enviado como "guided_json" em vez de response_format. O esperado é uma saída sem restrição.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":6144,"reasoning_effort":"medium","response_format":{"type":"json_schema","json_schema":{"name":"carta","strict":true,"schema":{"type":"object","properties":{"objetivo":{"type":"string"},"riscos":{"type":"array","items":{"type":"string"},"maxItems":5}},"required":["objetivo","riscos"]}}},"messages":[{"role":"user","content":"<estudo fictício de uma rede de padarias inventada>"}]}
```

**Métrica.** % de JSONs válidos no esquema; % de chamadas com raciocínio preenchido; ocorrências de '</think>' no content; finish_reason; resultado do controle negativo.

**Critério de aprovação.** Aprovado quando: com raciocínio, 15 de 15 chamadas (10 sem e 5 com streaming) trazem raciocínio preenchido e content válido no esquema, com finish_reason='stop'; sem raciocínio, 5 de 5 válidas; zero '</think>' no content; o controle negativo confirma que guided_json não restringe nada (essa forma fica proibida na lib).

**Se falhar.** O JSON quebra ou o raciocínio some só quando os dois estão ligados: o enquadramento passa a ter duas chamadas (pensar sem esquema e depois formatar com esquema e sem raciocínio) ou roda sem raciocínio. O CCOE confere o --reasoning-parser e o backend de saídas estruturadas (xgrammar ou guidance). Falha também sem raciocínio: o caso vai para o bloco de saídas estruturadas e é bloqueante para os perfis com JSON estrito.


### A14 · Entrada de imagem (teste de fumaça)

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* A1

**Pergunta.** O qwen-latest aceita imagem pela API (o servidor não está com --language-model-only)? E qual o tamanho de imagem que passa pelo caminho de rede?

**Por que importa.** O perfil 'visão de PNG de gráfico' só existe se o servidor aceitar imagem. Com --language-model-only, a parte de visão fica desligada. O caminho de rede também pode ter um limite de tamanho de corpo (o padrão do nginx, por exemplo, é 1 MB), e o base64 aumenta o arquivo em cerca de um terço. A qualidade da leitura de gráficos fica para o bloco de visão; aqui só interessa saber se a porta está aberta.

**Como medir.**

1) Gere localmente, com Python puro (zlib e struct) ou com qualquer biblioteca de imagem, um PNG inventado de 64×64: um quadrado vermelho sobre fundo branco. Gere também dois PNGs de ruído, de ~0,5 MB e ~2 MB, para testar o tamanho. 2) Mande sempre como data URL base64 no image_url, nunca como link (o servidor baixaria a URL, e a lib nunca deve apontar para recursos internos). 3) Pergunte a cor do quadrado, sem raciocínio, com max_tokens 10, 3 vezes. 4) Compare o prompt_tokens com o da mesma pergunta sem imagem: ele deve subir pelos tokens da imagem. Com --enable-prompt-tokens-details, prompt_tokens_details.multimodal_tokens mostra a parte da imagem. 5) Mande os PNGs de 0,5 MB e 2 MB nos dois ambientes e anote status (413, conexão encerrada) e tempo.

**Exemplo de requisição.**

```json
{"model":"qwen-latest","max_tokens":10,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,<PNG 64x64 gerado localmente>"}},{"type":"text","text":"Qual a cor do quadrado? Responda numa palavra."}]}]}
```

**Métrica.** Status; acerto da cor; prompt_tokens com e sem imagem; maior tamanho de PNG aceito em cada ambiente.

**Critério de aprovação.** Aprovado quando: 3 de 3 respostas corretas com status 200; prompt_tokens maior que o da pergunta sem imagem; o maior PNG que a lib pretende mandar (tamanho a definir no desenho; aqui se testam 0,5 MB e 2 MB) passa sem 413 nem corte nos dois ambientes.

**Se falhar.** Vem 400 dizendo que o modelo não aceita imagem: provavelmente o servidor roda com --language-model-only, e o CCOE decide se liga a visão. Sem visão, o perfil 'visão de PNG' sai, e o Python descreve o gráfico a partir dos dados agregados que ele mesmo gerou. 413 ou conexão encerrada: a lib reduz e comprime a imagem antes de enviar, e o CCOE informa o limite.


### Perguntas ao CCOE · A

- **Qual é a base URL oficial: https://hub-gpus.usto.re/v1 ou https://hub-gpus-lab.usto.re/v1? Por que a -lab deu 401 com a nossa chave? Existe endpoint privado acessível de dentro da VCN dos notebooks do OCI Data Science, ou lista de IPs liberados?** — Fixa a configuração da lib e o caminho de rede real (A1, A11).
- **Quais as versões exatas do vLLM e do LiteLLM em produção, e qual o comando completo do vllm serve do qwen-latest? Interessam em especial: --reasoning-parser, --reasoning-config (com reasoning_start_str e reasoning_end_str), --tool-call-parser, --chat-template, --max-model-len, --generation-config, --override-generation-config, --default-chat-template-kwargs, --max-logprobs, --enable-prompt-tokens-details, --enable-force-include-usage, --structured-outputs-config, --kv-cache-dtype, --speculative-config, --language-model-only, --enable-prefix-caching e --mamba-cache-mode.** — Vários comportamentos mudam com a versão e as flags: aceitar xhigh no topo, suportar thinking_token_budget, cortar max_tokens em silêncio, o nome reasoning ou reasoning_content, a visão ligada ou não. Esses dados explicam os resultados de A2 a A14.
- **O vLLM usa o chat_template.jinja oficial do Qwen3.8-27B ou um template próprio? Podem passar o sha256 do arquivo em uso? Há algum prompt de sistema padrão injetado pelo servidor ou pelo LiteLLM?** — Os valores previstos de prompt_tokens (A2, A9) só valem para o template oficial; um template próprio sem reasoning_effort ou preserve_thinking faria esses controles sumirem em silêncio.
- **De onde vem o 'max_tokens até 8192' do PDF: do model_info do LiteLLM (max_tokens ou max_output_tokens) com modify_params ligado, de um max_new_tokens no vLLM, ou do contexto do gpt-oss-20b? Dá para liberar pelo menos 20.480 tokens de saída no qwen-latest?** — Destrava os perfis de 12K e 16K de raciocínio (A5). O modify_params corta só max_tokens, e não max_completion_tokens, o que muda o contorno.
- **Como o qwen-latest está declarado no LiteLLM: prefixo hosted_vllm/ ou openai/? Quais os valores de drop_params, additional_drop_params, allowed_openai_params e modify_params? Há valores fixos em litellm_params (max_tokens, temperature, top_p) e, se houver, quem ganha quando o cliente manda outro valor?** — É isso que decide o que some ou é alterado em silêncio (A2, A4, A5, A7).
- **O roteador do LiteLLM tem num_retries, fallbacks (por exemplo, para o gpt-oss-20b) ou cache de respostas ligados para o qwen-latest? A nossa chave pode usar "cache":{"no-cache":true}?** — Retentativas escondem erros e falseiam tempos; um fallback responderia com um modelo não homologado; um cache falsearia o teste de determinismo (A1, A11, A12).
- **'qwen-latest' é um apelido que pode mudar de modelo, de quantização ou de flags? Haverá aviso prévio, janela de mudança e um nome fixo (por exemplo, qwen3.8-27b) para uso homologado?** — A homologação vale para um modelo e uma configuração específicos; a lib precisa detectar a troca e reagir. A impressão digital por prompt_tokens não detecta troca de pesos ou de quantização com o mesmo template.
- **Quais os limites da nossa virtual key (RPM, TPM, requisições paralelas, orçamento) e a janela de cada um? O 429 traz retry-after? Podemos fazer uma rajada curta combinada para medir?** — Define a espera entre tentativas e o paralelismo máximo da lib (A10, A11).
- **Que timeouts e limites existem no caminho (request_timeout do LiteLLM, balanceador ou ingress do domínio usto.re), com e sem streaming? Qual o tamanho máximo do corpo da requisição? O LiteLLM cancela a geração no vLLM quando o cliente desconecta?** — Chamadas xhigh longas e imagens grandes podem ser derrubadas pela rede (A5, A14); o cancelamento importa para o caso de max_tokens omitido no A5 não prender a GPU.
- **O vLLM roda com --generation-config auto, ou seja, com os padrões do modelo (temperature 1.0, top_p 0.95, top_k 20)? Podem mostrar a linha de log da inicialização ('Default vLLM sampling parameters have been overridden by ...')?** — É o plano B se top_k não passar pelo LiteLLM, e confirma quais valores o servidor aplica aos campos omitidos (A4).
- **Durante um teste combinado, vocês conseguem olhar o log do vLLM em nível debug (linha 'The following fields were present in the request but ignored') ou o corpo que o LiteLLM repassa ao vLLM?** — Confirma o repasse direto na fonte, sem depender só de efeitos indiretos (A2, A4, A7).
- **Podem expor a rota /tokenize do vLLM pelo hub (rota de passagem do LiteLLM) ou configurar o tokenizer do Qwen no /utils/token_counter?** — A lib precisa contar com exatidão os ~131 mil tokens de metadados antes de enviar; o contador padrão do LiteLLM é uma estimativa com outro tokenizer (A1).
- **A cadeia de certificados de usto.re é pública? Os notebooks do OCI Data Science precisam de uma CA corporativa instalada para verificar o TLS?** — A lib nunca desliga a verificação de certificado; se faltar a CA, ela precisa ser instalada no ambiente (A1).
- **(Time OCI, com o CCOE) Qual dynamic group e qual política deixam a sessão de notebook ler o segredo da chave no OCI Vault (algo como 'read secret-bundles' no compartimento do segredo)? Os notebooks usam a rede padrão ou rede própria com NAT gateway?** — É o caminho seguro da chave e o caminho de rede real dentro do Data Science (A1).
- **Os prompts e as respostas ficam gravados nos logs do LiteLLM ou do vLLM? Por quanto tempo, e quem tem acesso?** — O copiloto só manda metadados e agregados, mas a retenção entra na avaliação de segurança e na mensagem ao dev.
- **Qual o canal e o prazo de suporte para erros 500 e indisponibilidade? Existe janela de manutenção conhecida? Em que horários podemos rodar os testes pesados (A5, A8, rajada do A11)?** — Define a mensagem da lib ao dev, a política de retentativa (A11) e evita carga inesperada no hub compartilhado.

### Resultado → decisão no desenho

| Resultado | Decisão no desenho |
| --- | --- |
| Saída passa de 16.384 tokens com teto de 20.480 (A5). | Mantêm-se os perfis de plano (16K de raciocínio) e de caça de riscos (12K), com teto de saída = teto de raciocínio + teto de resposta + margem. |
| Saída trava em 8.192 nos dois campos (A5). | Raciocínio de até ~6K via thinking_token_budget mais até 2K de resposta. O plano vira uma chamada por 'jeito de enquadrar', o crítico continua separado e a caça de riscos vira dois passes. |
| Saída trava em 8.192 só com max_tokens e passa com max_completion_tokens (A5). | A causa provável é o modify_params do LiteLLM. A lib manda max_completion_tokens enquanto o CCOE corrige o model_info, e a correção vira condição da homologação. |
| max_tokens omitido cai num padrão grande ou variável (A5). | A lib sempre manda o teto de saída explícito, por perfil. |
| Chamadas longas sem streaming são derrubadas, mas com streaming passam (A5, A11). | A lib usa streaming internamente nos perfis longos e devolve ao dev o resultado inteiro. |
| thinking_token_budget é respeitado (A7). | Os tetos de raciocínio dos perfis viram parâmetro duro; reasoning_effort fica como ajuste fino. A lib nunca usa o parâmetro thinking do LiteLLM. |
| thinking_token_budget dá 400 de configuração ou é ignorado (A7). | Escada de retentativa (xhigh, medium, low, sem raciocínio) quando o content vem vazio, max_tokens com folga, e os tetos documentados como 'pedidos', não garantias, até o CCOE corrigir. |
| reasoning_effort só passa por um dos caminhos, ou por nenhum (A2, A8). | A lib manda o nível por um caminho só, o que funcionou, e nunca pelos dois juntos. Se nada passar, a frase própria no prompt de sistema só vira solução depois de medida com o método do A8, porque o template continua inserindo a frase de xhigh. |
| Omitir reasoning_effort equivale a xhigh (A2, A8). | Todo perfil declara o nível (ou o desligamento) de forma explícita, para nunca cair no modo mais caro por omissão. |
| A frase do nível vai no começo do prompt de sistema (fato do template; confirmar o template em uso com o CCOE e o efeito no bloco de cache). | Chamadas que compartilham o prefixo de ~131 mil tokens da descoberta usam o mesmo nível e o mesmo modo de raciocínio, para não perder o cache de prefixo. |
| Os valores absolutos de prompt_tokens das sondas batem com o template e o tokenizer oficiais (A2). | O vetor de prompt_tokens das sondas entra na trilha como impressão digital do deploy, ao lado do x-litellm-model-id; se divergir do homologado, a lib alerta ou bloqueia, conforme a configuração. |
| low e xhigh gastam quase o mesmo (A8). | Os perfis encolhem para 'sem raciocínio' e 'com raciocínio + teto' (A7). |
| Valor proibido ('high', 'minimal', 'max', 'HIGH') devolve 500 ou 200 (A8, A11). | Validação local antes do envio: só {low, medium, xhigh} ou o desligamento explícito, com mensagem clara ao dev. |
| O raciocínio chega num campo próprio, com nome registrado por modo (A3). | O leitor da lib tenta reasoning_content, depois reasoning, depois o corte em '</think>'. O raciocínio vai para a trilha, nunca para a narrativa. |
| '</think>' vaza no content (A3). | A homologação fica bloqueada até o CCOE ligar --reasoning-parser qwen3. |
| O usage do streaming difere do da chamada sem streaming (A3). | A conta de custo usa o usage sem streaming ou o header x-litellm-response-cost; o usage do streaming não entra na trilha como medida oficial. |
| finish_reason='length' é confiável nos cortes (A6). | Regra de aceitação única: a resposta só vale com finish_reason='stop', content não vazio, sem '</think>' e JSON válido no esquema. Fora disso, é falha recuperável: retentar com teto maior ou esforço menor. |
| O hub lê o raciocínio do histórico e o contexto cresce muito por turno (A9). | A entrevista e o crítico não reenviam o raciocínio (ou usam preserve_thinking=false); só os laços de ferramenta o preservam. |
| stop age sobre o raciocínio (A10; é o esperado pelo código atual). | stop fica proibido com raciocínio ligado; a saída é delimitada com json_schema. |
| n e logprobs funcionam (A10). | O crítico pode votar com n=k (custo k vezes maior, e nunca com temperature 0), e a escolha de bloco sem raciocínio usa logprob como confiança. Se não funcionarem, a lib faz chamadas paralelas e autoavaliação estruturada. |
| O servidor aplica os padrões do generation_config.json aos campos omitidos (A4). | Cada perfil manda o conjunto completo de amostragem (temperature, top_p, top_k, min_p, presence_penalty), para não herdar, por exemplo, top_p 0.95 num perfil sem raciocínio que pede 0.8. |
| Parâmetros fora do padrão OpenAI (top_k, min_p, chat_template_kwargs) somem no LiteLLM (A2, A4). | O CCOE ajusta o LiteLLM. Enquanto isso, top_k vem do generation_config do servidor (se confirmado), e os perfis que dependem do parâmetro perdido ficam suspensos. |
| Raciocínio e json_schema convivem na mesma chamada (A13). | O enquadramento roda numa chamada só, com response_format; os campos guided_* ficam proibidos na lib. Se não conviverem, são duas chamadas (pensar e depois formatar) ou enquadramento sem raciocínio. |
| A API aceita imagem e o tamanho previsto passa pela rede (A14). | O perfil de visão segue, com a imagem sempre em data URL e reduzida ao tamanho homologado. Sem visão, o perfil sai e o Python descreve o gráfico a partir dos agregados. |
| Temperatura 0 dá pelo menos 9 de 10 saídas idênticas, sem cache no meio (A12). | Golden tests textuais nas etapas sem raciocínio; nos outros casos, testes por estrutura e marcadores. |
| O 429 traz retry-after ou x-ratelimit-reset-*, e o CCOE informa os limites (A11). | O cliente da lib roda com max_retries=0, e a política de retentativa é da lib: a espera respeita o header, e o paralelismo fica abaixo do limite da chave. Sem header, espera exponencial com jitter e poucas tentativas. |
| O LiteLLM faz fallback para outro modelo (A1, A11). | Resposta com x-litellm-attempted-fallbacks > 0, ou com model diferente do homologado, conta como falha; o CCOE desliga o fallback para a nossa chave. |
| Não há identificador estável do deploy, ou 'qwen-latest' muda sem aviso (A1). | A lib grava model, x-litellm-model-id e o vetor de prompt_tokens das sondas em cada chamada, e alerta ou bloqueia (por configuração) quando algum diverge do homologado. |

### Incertezas

- As versões de vLLM e LiteLLM do hub são desconhecidas. O código citado vem da branch main de 19/10/2026 (vLLM dde9aca, LiteLLM 6762d65), e versões antigas se comportam diferente. Exemplos: vLLMs antigos aceitavam no topo só low, medium e high (aí 'xhigh' daria 400) e podiam aceitar reasoning_effort sem repassá-lo ao template; davam 400 em vez de cortar o max_tokens em silêncio; e o parser qwen3 antigo punha no content o raciocínio cortado. LiteLLMs antigos podiam descartar ou recusar reasoning_effort no prefixo openai/.
- Os prompt_tokens previstos (14, 16, 44, 56; 91, 80, 76) foram calculados por nós, fora do hub, com o template e o tokenizer oficiais (renderização Jinja como a do transformers). Valem só se o hub usar esses mesmos arquivos e não injetar texto extra; o A2 decide.
- As fontes divergem sobre por onde mandar reasoning_effort: o card do Qwen usa o topo da requisição, e a receita do vLLM usa chat_template_kwargs. O vLLM atual aceita os dois (o valor do topo é copiado e sobrescreve o dos kwargs); o A2 decide qual vale no hub.
- A origem do 'até 8192' do PDF é desconhecida. As candidatas são um max_new_tokens no vLLM, o modify_params do LiteLLM com max_output_tokens no model_info, um metadado sem efeito ou confusão com o contexto do gpt-oss-20b.
- Que as strings de stop ajam sobre o raciocínio vem da leitura do código atual (o detokenizador confere o texto inteiro), não de documentação. Versões antigas e o repasse do campo stop_reason pelo LiteLLM ainda precisam do A10.
- Não está documentado se os logprobs cobrem os tokens de raciocínio, nem qual é o --max-logprobs configurado no hub (o padrão do vLLM é 20).
- Não sei se o LiteLLM do hub repassa usage.completion_tokens_details.reasoning_tokens, nem se, no streaming, ele entrega o usage do vLLM ou uma estimativa própria; o A3 detecta pela comparação do prompt_tokens.
- Não está documentado se uma virtual key comum pode chamar /model/info, /v1/model/info, /model_group/info, /key/info, /utils/token_counter e /tokenize no hub.
- A causa do 401 na base -lab é desconhecida: chave de outra instância, documentação desatualizada ou ambiente desligado.
- Não sei se o LiteLLM com prefixo openai/ (em vez de hosted_vllm/) preserva reasoning_content e reasoning nas mensagens do histórico.
- Não sei se o LiteLLM cancela a geração no vLLM quando o cliente desconecta. Se não cancelar, o corte no cliente do A5 não libera a GPU, e esse caso precisa do acompanhamento do CCOE.
- A convivência entre json_schema e raciocínio depende do parser e do backend de saídas estruturadas; a doc do vLLM descreve o mecanismo de forma geral, e o A13 mede no hub.
- Os relatos de que xhigh gasta cerca de 8 vezes mais e de que low fica perto de medium vêm da comunidade e não foram verificados aqui. A meta de 'low ≤ metade de xhigh' é uma necessidade do desenho, não uma previsão.
- O efeito de temperatura 0 com raciocínio ligado não foi avaliado. A bateria usa temperatura 0 só sem raciocínio e a amostragem oficial nos demais casos.
- Não sei se o hub roda com --generation-config auto. Se rodar, omitir top_k já resulta em 20, o que muda a gravidade de uma falha no A4.
- Os timeouts e o limite de tamanho do corpo no caminho (LiteLLM, balanceador, domínio usto.re) não são públicos; os valores do nginx citados são só exemplos de padrão comum. Só o CCOE ou a medição do A5 e do A14 respondem.
- Não sei se o servidor roda com --language-model-only (visão desligada); o A14 responde.

### Fontes consultadas

- [vLLM: Reasoning Outputs (campo reasoning, enable_thinking, thinking_token_budget, --reasoning-config, include_reasoning, saída estruturada com raciocínio)](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)
- [vLLM: OpenAI-Compatible Server (parâmetros extras via extra_body, equivalentes a campos no JSON)](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM: Reproducibility (batch invariance, mesmo hardware e mesma versão)](https://docs.vllm.ai/en/latest/usage/reproducibility/)
- [vLLM: SamplingParams (faixas de temperature, top_p, top_k, min_p e presence_penalty; n=1 com escolha gulosa; thinking_token_budget)](https://github.com/vllm-project/vllm/blob/main/vllm/sampling_params.py)
- [vLLM, código: chat_completion/protocol.py (reasoning_effort aceito: none, minimal, low, medium, high, xhigh, max; cópia para chat_template_kwargs; 'none' liga enable_thinking=false; max_completion_tokens tem prioridade)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/protocol.py)
- [vLLM, código: serve/engine/protocol.py (extra='allow', aviso de campos ignorados em debug, ErrorInfo, UsageInfo com cached_tokens e reasoning_tokens)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/engine/protocol.py)
- [vLLM, código: chat_completion/serving.py (reasoning_tokens só com parser de raciocínio; prompt_tokens_details só com --enable-prompt-tokens-details; override_max_tokens)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/serving.py)
- [vLLM, código: serve/utils/api_utils.py (get_max_tokens: mínimo entre o pedido, o contexto que sobra e o teto do servidor)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/utils/api_utils.py)
- [vLLM, código: renderers/params.py (400 quando max_tokens passa do max_model_len)](https://github.com/vllm-project/vllm/blob/main/vllm/renderers/params.py)
- [vLLM, código: renderers/chat_utils/chat_message.py (reasoning copiado para reasoning_content no histórico)](https://github.com/vllm-project/vllm/blob/main/vllm/renderers/chat_utils/chat_message.py)
- [vLLM, código: renderers/hf.py (chat_template_kwargs filtrados pelas variáveis do template, sem aviso)](https://github.com/vllm-project/vllm/blob/main/vllm/renderers/hf.py)
- [vLLM, código: v1/engine/input_processor.py (400 com thinking_token_budget sem reasoning_config)](https://github.com/vllm-project/vllm/blob/main/vllm/v1/engine/input_processor.py)
- [vLLM, código: v1/engine/detokenizer.py (strings de stop conferidas no texto gerado inteiro)](https://github.com/vllm-project/vllm/blob/main/vllm/v1/engine/detokenizer.py)
- [vLLM, código: parser/qwen3.py (saída começa em estado de raciocínio quando o thinking está ligado)](https://github.com/vllm-project/vllm/blob/main/vllm/parser/qwen3.py)
- [vLLM, código: config/model.py (generation_config 'auto' por padrão; max_new_tokens como teto do servidor; max_logprobs 20)](https://github.com/vllm-project/vllm/blob/main/vllm/config/model.py)
- [vLLM, código: serve/exception_handling/error_response.py (exceção do template vira 400 BadRequestError)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/exception_handling/error_response.py)
- [vLLM, código: serve/tokenize/protocol.py (/tokenize aceita mensagens de chat)](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/serve/tokenize/protocol.py)
- [vLLM Recipes: Qwen3.8-27B (--reasoning-parser qwen3 em todos os comandos; reasoning_effort por chat_template_kwargs)](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)
- [LiteLLM: Drop Unsupported Params (drop_params, additional_drop_params, allowed_openai_params)](https://docs.litellm.ai/docs/completion/drop_params)
- [LiteLLM: provedor vLLM (hosted_vllm/)](https://docs.litellm.ai/docs/providers/vllm)
- [LiteLLM: OpenAI-Compatible Endpoints (prefixo openai/)](https://docs.litellm.ai/docs/providers/openai_compatible)
- [LiteLLM: Provider-specific Params](https://docs.litellm.ai/docs/completion/provider_specific_params)
- [LiteLLM: Reasoning Content](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM: Response Headers (x-litellm-*, x-ratelimit-*, llm_provider-*, attempted-retries e attempted-fallbacks)](https://docs.litellm.ai/docs/proxy/response_headers)
- [LiteLLM: Exception Mapping](https://docs.litellm.ai/docs/exception_mapping)
- [LiteLLM: Caching (controle por requisição, no-cache)](https://docs.litellm.ai/docs/proxy/caching)
- [LiteLLM: Model Management (/model/info)](https://docs.litellm.ai/docs/proxy/model_management)
- [LiteLLM: Virtual Keys (/key/info)](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [LiteLLM: exemplos de cliente com extra_body](https://docs.litellm.ai/docs/proxy/user_keys)
- [LiteLLM, código: hosted_vllm/chat/transformation.py (reasoning_effort suportado; reasoning_content mantido no histórico quando é texto; thinking convertido em nível)](https://github.com/BerriAI/litellm/blob/main/litellm/llms/hosted_vllm/chat/transformation.py)
- [LiteLLM, código: utils.py (parâmetros fora do padrão vão para extra_body; UnsupportedParamsError; lista de suportados)](https://github.com/BerriAI/litellm/blob/main/litellm/utils.py)
- [LiteLLM, código: exceptions.py (UnsupportedParamsError com status 400)](https://github.com/BerriAI/litellm/blob/main/litellm/exceptions.py)
- [LiteLLM, código: litellm_core_utils/token_counter.py (get_modified_max_tokens, usado com modify_params)](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/token_counter.py)
- [LiteLLM, código: openai/chat/gpt_transformation.py (OpenAIUnknownModelConfig; reasoning convertido em reasoning_content no streaming; erro dentro do stream)](https://github.com/BerriAI/litellm/blob/main/litellm/llms/openai/chat/gpt_transformation.py)
- [LiteLLM, código: prompt_templates/common_utils.py (extract_reasoning_content; recorte de <think> exige as duas tags)](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py)
- [LiteLLM, código: streaming_chunk_builder_utils.py (usage recalculado quando o provedor não manda)](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/streaming_chunk_builder_utils.py)
- [LiteLLM, código: proxy/auth/user_api_key_auth.py (mensagem de chave inválida com a chave mascarada)](https://github.com/BerriAI/litellm/blob/main/litellm/proxy/auth/user_api_key_auth.py)
- [LiteLLM, código: litellm_core_utils/reasoning_effort_utils.py (thinking.budget_tokens vira 'high', 'medium', 'low' ou 'minimal')](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/reasoning_effort_utils.py)
- [openai-python: README (retentativas padrão, timeout de 10 minutos, extra_body, model_extra)](https://github.com/openai/openai-python/blob/main/README.md)
- [Hugging Face: card do Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Hugging Face: chat_template.jinja do Qwen3.8-27B (frases de esforço, exceção 'Unexpected reasoning effort', preserve_thinking, bloco de pensamento no histórico)](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja)
- [Hugging Face: tokenizer.json do Qwen3.8-27B (usado para calcular os prompt_tokens previstos)](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/tokenizer.json)
- [Hugging Face: generation_config.json do Qwen3.8-27B (temperature 1.0, top_p 0.95, top_k 20)](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/generation_config.json)
- [OCI Data Science: Creating a Notebook Session (rede padrão contra rede própria)](https://docs.oracle.com/en-us/iaas/Content/data-science/using/create-notebook-sessions.htm)
- [OCI Data Science: Notebook Sessions, troubleshooting (NAT gateway em rede própria)](https://docs.oracle.com/en-us/iaas/Content/data-science/using/tshoot-notebook.htm)
- [Oracle ADS: Configuration (resource principal em notebooks)](https://accelerated-data-science.readthedocs.io/en/latest/user_guide/configuration/configuration.html)

### O que a revisão adversarial corrigiu (25)

- **[alta] Resumo (3) e A5** — A afirmação de que, no vLLM atual, um max_tokens acima do permitido 'é reduzido em silêncio' está incompleta. Pelo código atual (vllm/renderers/params.py), max_tokens ou max_completion_tokens maior que o max_model_len dá 400 ('max_tokens=… cannot be greater than max_model_len=…'). Só o valor que cabe no max_model_len mas passa do espaço que sobra no contexto é reduzido em silêncio (get_max_tokens). *Correção:* Reescrevi o resumo (3). No A5 entraram duas sondas baratas: max_tokens=300000 (espera-se 400 citando o max_model_len) e max_tokens=196600 com prompt de 14 tokens (no vLLM atual é aceito com corte silencioso; nas versões antigas dá 400).
- **[alta] A5** — Faltava uma origem provável do 'até 8192': o modify_params do LiteLLM. Com ele ligado, o LiteLLM corta max_tokens em silêncio para o max_output_tokens do model_info (get_modified_max_tokens) e não mexe em max_completion_tokens. O bloco mandava repetir com max_completion_tokens, mas não explicava por quê, e não perguntava isso ao CCOE. *Correção:* O motivo agora está no por_que_importa e no como_medir do A5. O critério ganhou um 'aprovado parcial' para o caso em que só max_completion_tokens passa. Entraram uma pergunta ao CCOE (modify_params e model_info) e uma decisão de desenho.
- **[alta] A4, A7** — O bloco tratava 'topo do JSON (curl)' e 'extra_body (SDK)' como dois caminhos de repasse. Não são: o openai SDK funde o extra_body no topo do JSON, e a requisição que sai é a mesma. O teste repetia a mesma coisa duas vezes e não testava o caminho que de fato é diferente. *Correção:* Tirei a duplicação do A4 e do A7. O A2 ganhou a variante (i), com a chave literal "extra_body" no corpo. O LiteLLM desembrulha essa chave; o vLLM direto a ignoraria.
- **[alta] A4** — O critério do seed ('seeds diferentes dão respostas diferentes') não separa um seed aplicado de um seed ignorado: com temperature 1.0 as respostas variam de qualquer jeito. *Correção:* Novo critério: seed=1234 repete a mesma resposta em pelo menos 4 de 5, sem seed saem pelo menos 3 respostas distintas, e seed=4321 difere da resposta de 1234.
- **[alta] A1** — O plano B de identificação do deploy ('hash da resposta a uma pergunta-sonda fixa') contradiz o A12, porque as respostas não são garantidamente determinísticas, e daria falso alarme de troca de modelo. *Correção:* O identificador passa a ser o vetor de prompt_tokens das sondas do A2 (determinístico: muda quando o tokenizer ou o template mudam) mais o x-litellm-model-id. A ressalva ficou registrada: nenhum dos dois detecta troca de pesos ou de quantização, então o aviso prévio do CCOE continua necessário.
- **[alta] A6, A10** — A regra 'nunca parsear se finish_reason ≠ stop' e o critério 'nenhum stop com content vazio' não bastam. (1) Pelo código atual do vLLM, as strings de stop são conferidas no texto gerado inteiro, raciocínio incluído (v1/engine/detokenizer.py). Uma string que aparece no pensamento encerra a chamada com finish_reason 'stop' e content vazio. (2) Versões antigas do parser qwen3 punham no content o raciocínio cortado (sem '</think>'). *Correção:* Nova regra de aceitação, comum ao A6 e ao A10: finish_reason='stop', content não vazio, sem '</think>' e JSON válido no esquema. O A6 registra o caso do parser antigo e o A10 passa a esperar o pior caso, além de ler o campo stop_reason.
- **[alta] A7** — 'Ignorado em silêncio' não é a única falha possível. Com thinking_token_budget e sem parser ou reasoning-config no servidor, o vLLM atual devolve 400 ('thinking_token_budget is set but reasoning_config is not configured'). Além disso, o controle 'sem orçamento passa de 1.024' é fraco: com orçamentos de até 4.096, o raciocínio livre precisa passar do maior orçamento testado, senão esse caso não prova nada. *Correção:* O se_falhar agora separa três desfechos (400 de configuração, 200 ignorado, respeitado com content vazio). A pré-condição passou a ser 'mediana sem orçamento acima do maior orçamento', com orçamentos escolhidos abaixo dela. A margem M ficou ligada ao reasoning_end_str do servidor. Entraram o orçamento 0 e o aviso de não usar o parâmetro thinking do LiteLLM.
- **[media] A2** — Critério frouxo ('P(e)=P(b) ou pelo menos um caminho') e sem a ordem de grandeza esperada. O teste também dizia que 'confirma' que a frase vai no começo do prompt de sistema, coisa que o prompt_tokens não mede (ele conta tokens, não posição). *Correção:* Calculei os valores esperados com o template e o tokenizer oficiais: medium 14, sem raciocínio 16, low 44, xhigh ou omitido 56, histórico com preserve 91 contra 76. O critério agora usa as diferenças (+2, +30, +42, −15) e os valores absolutos como prova de template oficial. A posição da frase ficou como fato do template, com pergunta ao CCOE sobre o hash do template em uso.
- **[media] A9** — O critério '(d) ≤ (c)' estava fraco e não refletia o template. Com preserve ligado, o template põe um bloco de pensamento vazio até nos turnos sem raciocínio; com preserve=false, tira o bloco inteiro. Logo (d)=76 < (c)=80 < (a)=91. No vLLM atual, (b) dá 91, porque o vLLM copia reasoning para reasoning_content antes de aplicar o template (renderers/chat_utils/chat_message.py). *Correção:* Critério com os valores previstos e as diferenças (+11 e −4 em relação a c). Um 80 em (b) passa a indicar que o campo reasoning some no caminho.
- **[media] A3** — Procurar '<think>' no content não pega o vazamento: o template já põe '<think>' no fim do prompt, então um vazamento aparece só como texto seguido de '</think>'. O recorte de reserva do LiteLLM também exige as duas tags e não resolve esse caso. Faltava ainda dizer que reasoning_tokens só existe com --reasoning-parser, e que o usage do streaming pode ser uma estimativa do LiteLLM. *Correção:* A busca agora é por '</think>'. Entrou a checagem 'prompt_tokens igual com e sem streaming' (se diferir, o usage do streaming é estimativa). O truque da resposta mínima passou a '+4 a 6 tokens' ('</think>\n\nOK' mais o fim de turno somam 4 tokens no tokenizer oficial).
- **[media] A4** — Estava errado dizer 'presence_penalty e seed são do padrão OpenAI: se sumirem, a causa é drop_params'. Os dois estão na lista de parâmetros suportados que o LiteLLM usa para provedores compatíveis com OpenAI, então o drop_params não os remove. Eles só somem por additional_drop_params ou por valor fixo em litellm_params. *Correção:* O se_falhar e a pergunta ao CCOE foram corrigidos.
- **[media] A4** — O critério 'top_k=1 dá 1 resposta distinta em 5' pode reprovar um top_k que foi aplicado, porque a variação causada pelo lote do servidor também afeta a escolha gulosa (A12). Também faltava avisar que, com --generation-config auto, o servidor aplica top_k=20 e top_p=0.95 quando o campo é omitido. *Correção:* Entrou um controle guloso (temperature 0). Agora o top_k=1 e o min_p=1.0 precisam ser tão estáveis quanto ele, e uma base com pelo menos 3 respostas distintas virou pré-condição. Entrou também a decisão 'a lib manda todos os parâmetros de amostragem de forma explícita'.
- **[media] A11** — O openai SDK repete sozinho, 2 vezes por padrão, erros de conexão, 408, 409, 429 e 5xx, e espera até 10 minutos. Isso esconde o erro, dobra a carga e falseia os tempos medidos. O LiteLLM também pode retentar ou desviar para outro modelo (fallback). *Correção:* Os testes com o SDK usam max_retries=0 e timeout explícito. O A11 registra x-litellm-attempted-retries e x-litellm-attempted-fallbacks, e uma resposta vinda de fallback conta como falha.
- **[media] A1, A11 (segurança)** — Corpos de erro e respostas de metadados podem conter material da chave. O 401 do LiteLLM pode trazer a chave mascarada (4 primeiros e 4 últimos caracteres) ou o hash dela, e o /key/info devolve o hash. O A1 manda a chave real para a base -lab, que dá 401. O bloco também não falava de TLS (risco de alguém usar -k ou verify=False), nem de %env e células de notebook. *Correção:* Entraram um filtro de redação obrigatório antes de salvar qualquer corpo, o salvamento só de campos escolhidos, a proibição de -k, verify=False e %env, o uso de read -rs ou de arquivo de header, e o critério de TLS verificado no A1.
- **[media] A12** — Um cache de respostas do LiteLLM ligado daria 10 de 10 respostas idênticas falsamente, e o teste não tinha como perceber. *Correção:* Entrou o passo 0, com "cache":{"no-cache":true} (se a chave permitir) e a checagem do header x-litellm-cache-key, dos ids e da duração. A descrição da doc de reprodutibilidade do vLLM foi corrigida (batch invariance, mesmo hardware e mesma versão).
- **[media] Lacuna (novo A13)** — Nenhum teste via se o raciocínio e o json_schema convivem na mesma chamada, que é o perfil de enquadramento (medium com JSON estrito). Pela doc do vLLM, a gramática fica suspensa enquanto o raciocínio não termina, mas isso depende do parser. Os campos antigos guided_* são ignorados (só um aviso no log) e deixam a saída sem restrição. *Correção:* Criei o A13, com critério de 10 de 10 JSONs válidos no esquema e raciocínio presente, e um controle negativo com guided_json.
- **[media] Lacuna (novo A14)** — Nenhum teste via se a API aceita imagem (o perfil 'visão de PNG' morre se o servidor rodar com --language-model-only) nem o limite de tamanho do corpo no caminho de rede. *Correção:* Criei o A14, um teste de fumaça com PNG inventado gerado localmente, enviado como data URL (nunca como link), em dois tamanhos e nos dois ambientes.
- **[media] A1** — O /v1/models do LiteLLM lista os apelidos dele (id, owned_by) e não traz o max_model_len do vLLM. O que o /model/info mostra é configuração escrita à mão, que não limita nada sozinha. O bloco esperava achar ali o contexto. Faltavam ainda os detalhes de rede e de Vault do OCI Data Science. *Correção:* Texto corrigido: metadado não é limite, e só o A5 mede o teto real. Entraram a rede padrão contra a rede própria com NAT, o resource principal com SecretsClient, a sonda /tokenize e o tokenizer_type do /utils/token_counter.
- **[media] A5** — O caso 'max_tokens omitido' com uma tarefa de cerca de 43,9 mil tokens (calculado com o tokenizer oficial) pode prender uma GPU compartilhada por muito tempo, ou até o fim do contexto se o modelo entrar em loop. *Correção:* Esse caso agora roda com streaming e o cliente corta a conexão ao chegar em ~21 mil tokens. Os testes pesados ficaram marcados para combinar antes com o CCOE.
- **[media] A2 (plano B)** — O plano B 'a lib escreve no prompt de sistema a frase de xhigh/low' ignora um detalhe: se o reasoning_effort não chega ao template, ele continua inserindo a frase padrão de xhigh. A frase da lib entraria depois, contraditória. *Correção:* Ressalva adicionada. O efeito tem de ser medido com o método do A8 antes de valer como solução.
- **[baixa] A8** — Faltava separar onde cada valor proibido é barrado. 'HIGH' cai na validação do próprio vLLM (fora da lista dele). 'high', 'minimal' e 'max' passam pelo vLLM e caem na exceção do template, que o vLLM converte em 400. E o parâmetro thinking do LiteLLM vira 'high' ou 'minimal' para hosted_vllm. *Correção:* O como_medir agora traz os resultados esperados por valor, e entrou a decisão de nunca usar o parâmetro thinking.
- **[baixa] A10** — Faltavam três pontos: n>1 com temperature 0 dá 400 no vLLM ('n must be 1 when using greedy sampling'); o vLLM devolve o campo stop_reason, que diz qual string parou; e o padrão de --max-logprobs é 20 (confirmado no código). *Correção:* Os três entraram no como_medir e no critério.
- **[baixa] A11** — Os resultados esperados do vLLM e do LiteLLM não estavam registrados: temperature 5 dá 400 'temperature must be in [0, 2]', e o UnsupportedParamsError do LiteLLM vem como 400 com o prefixo 'litellm.UnsupportedParamsError'. *Correção:* Entraram no como_medir, para comparar com o que o hub devolve.
- **[baixa] A6** — Com include_usage, o último chunk do streaming vem com choices vazio e só o usage. Quem lê o finish_reason do último chunk de todos não acha nada. *Correção:* Agora se lê o finish_reason do último chunk que tem choices.
- **[baixa] Resumo (2) e (4)** — Faltavam fatos do código que mudam a leitura dos testes. O vLLM aceita no topo none, minimal, low, medium, high, xhigh e max; 'none' liga enable_thinking=false; o valor do topo sobrescreve o de chat_template_kwargs; e o vLLM descarta em silêncio as chaves de chat_template_kwargs que o template não usa. *Correção:* Resumo atualizado. Entrou a decisão 'mandar o nível por um caminho só'.

---

## B · Saída estruturada (JSON) e chamadas de ferramenta

O Bloco B tem 12 testes (B1 a B12). Eles respondem a uma pergunta só: dá para confiar no JSON e nas chamadas de ferramenta do qwen-latest, passando pelo LiteLLM do GPU Hub, como o copiloto precisa? O copiloto precisa de JSON travado no enquadramento, na carta e no plano, de nomes de colunas exatos na descoberta de dados, de ferramentas em laço nas etapas e de reparo quando o validador em Python recusa uma saída.

O QUE A PESQUISA MUDOU NO PLANO (conferido em 19/10/2026 na doc e no código-fonte oficiais: vLLM tag v0.32.0 e LiteLLM main. Nada disso foi medido no hub):
(1) No vLLM, o JSON é travado no servidor sempre que vem response_format json_schema. O campo strict desse formato é aceito e ignorado: só o schema vai para o motor. Com raciocínio ligado, a trava só começa depois que o raciocínio termina (enable_in_reasoning=false por padrão). ATENÇÃO: a doc avisa que, se o raciocínio não for separado pelo parser (--reasoning-parser), a saída estruturada pode ser desligada sem aviso. Por isso o B1 tem controle negativo também com raciocínio. Se o teto de tokens acabar durante o raciocínio, a resposta vem vazia.
(2) No backend auto, o vLLM tenta o xgrammar. Se o schema tiver recurso que o xgrammar não suporta, passa para o guidance. O outlines só entra quando o guidance não suporta o schema ou o tokenizador. Recursos que o xgrammar recusa na v0.32.0: uniqueItems; contains, minContains e maxContains; multipleOf; pattern ou format junto com minLength/maxLength; allOf com 2 ou mais itens; algumas combinações de propertyNames e patternProperties; format fora da lista suportada. date, date-time, email e uuid são suportados. Se a CCOE fixou o xgrammar, esses schemas dão erro 400. Sem additionalProperties no schema, xgrammar e outlines recusam campos extras e o guidance aceita; o B2 confere isso.
(3) Na v0.32.0, qwen3_coder e qwen3_xml apontam para a mesma classe (Qwen3EngineToolParser, do motor novo do PR #45413, que entrou na v0.24.0). Esse parser usa 'structural tags'. Em tool_choice required ou nomeada, a moldura da chamada (marcação e nome) é sempre travada. Os argumentos só ficam travados quando a ferramenta tem strict:true ou quando o servidor usa --tool-strict-level parameter (o padrão para esse parser é auto). Em auto, a trava só liga se alguma ferramenta tiver strict:true. Se a CCOE ligou VLLM_ENFORCE_STRICT_TOOL_CALLING=false, as structural tags somem: required e nomeada voltam a um caminho de JSON derivado do schema, que trava os argumentos com ou sem strict. Nesse caminho há um bug aberto de erro 500 em required com stream (#49216).
(4) Se o qwen-latest estiver cadastrado no LiteLLM como hosted_vllm/, sempre que houver tools o LiteLLM apaga todas as chaves 'strict', de forma recursiva (inclusive um parâmetro chamado 'strict'), e todo additionalProperties:false. Esse arquivo não mexe no response_format. Ele também apaga thinking_blocks e só repassa reasoning_content se for texto. O prefixo precisa ser confirmado.
(5) O vLLM só lê chamadas de ferramenta no content, nunca no raciocínio. A issue #39056 (aberta, vista com Qwen3.5 no vLLM 0.19) mostra a chamada escrita dentro do raciocínio se perdendo. O parser novo trata <tool_call> como fim implícito do raciocínio, o que pode resolver o sintoma, mas também pode executar uma chamada que o modelo só estava cogitando. O B10 mede.
(6) O vLLM devolve o raciocínio em 'reasoning' (antes era 'reasoning_content') e renomeia reasoning_content para reasoning nas mensagens de entrada. O LiteLLM padroniza para reasoning_content. Se nenhum dos dois vier, ele tenta extrair tags <think> do content, o que pode esconder um vazamento. A biblioteca lê os dois campos.
(7) Uso e custo: no vLLM v0.32.0 existem prompt_tokens_details.cached_tokens (precisa de --enable-prompt-tokens-details) e completion_tokens_details.reasoning_tokens. Este último vale 0 por padrão quando o objeto vem, então 0 não prova que não houve raciocínio. Cabeçalhos úteis do LiteLLM: x-litellm-response-cost, x-litellm-call-id, x-litellm-version, x-litellm-model-id, x-litellm-attempted-retries, x-litellm-response-duration-ms e x-ratelimit-*. Em stream, o custo pode não vir no cabeçalho.
(8) thinking_token_budget é parâmetro por requisição, mas só funciona com --reasoning-parser e --reasoning-config, ou se o vLLM conseguir deduzir as marcas a partir do parser.
(9) Na escolha nomeada, a API da OpenAI devolve finish_reason='stop', e não 'tool_calls'. Não confirmamos o vLLM. A biblioteca decide pela presença de tool_calls.

PREPARAÇÃO COMUM (meio dia, antes do B1):
- Um script único (fase0_bloco_b.py), que roda igual na máquina local e no notebook do OCI Data Science.
- Cliente: OpenAI(base_url=os.environ['ADEX_LLM_BASE_URL'], api_key=os.environ['ADEX_LLM_KEY'], max_retries=0, timeout=600). Base URL: https://hub-gpus.usto.re/v1 (a única que respondeu em 16/10).
- Chave na máquina local: variável de ambiente definida fora do notebook, ou getpass.getpass() no início da sessão. Proibido: '%env ADEX_LLM_KEY=...', a chave literal numa célula, print do cliente ou dos cabeçalhos. Tudo isso fica gravado no .ipynb.
- Chave no notebook OCI, pelo Vault com resource principal: signer = oci.auth.signers.get_resource_principals_signer(); b = oci.secrets.SecretsClient({}, signer=signer).get_secret_bundle(SECRET_OCID).data; os.environ['ADEX_LLM_KEY'] = base64.b64decode(b.secret_bundle_content.content).decode(). O grupo dinâmico do notebook precisa de uma política que permita ler o segredo (quem administra a tenancy concede). A chave nunca é impressa, gravada ou registrada.
- curl: nunca usar -v nem set -x. A chave entra pelo stdin, e não pela linha de comando (onde apareceria na lista de processos): printf 'Authorization: Bearer %s\n' "$ADEX_LLM_KEY" | curl -s -H @- ... (printf é comando interno do bash). No notebook, preferir Python; curl só no terminal.
- Log: nunca gravar cabeçalhos da requisição. Antes de gravar um corpo de erro, trocar qualquer trecho 'sk-...' por [REDACTED], porque o LiteLLM pode ecoar parte mascarada da chave num 401.
- Descoberta passiva (antes do B1, sem custo relevante): 1 chamada curta, lendo x-litellm-version, x-litellm-model-id e x-ratelimit-limit-requests/tokens. Tentar GET $ADEX_LLM_BASE_URL/model/info com a própria chave, para ver litellm_params.model (prefixo hosted_vllm/ ou openai/); se der 401 ou 403, vira pergunta à CCOE. Fazer 20 chamadas idênticas e ver se x-litellm-model-id muda (sinal de mais de uma réplica).
- Retentativa própria: em 429, respeitar Retry-After se vier; senão, backoff de 1, 2, 4, 8 e 16 s (no máximo 5 vezes). Em 500, parar e acionar o suporte, como diz o PDF.
- Cada chamada vira uma linha num arquivo JSONL: teste, braço, repetição, status HTTP, latência, finish_reason, usage completo, x-litellm-response-cost, x-litellm-call-id, x-litellm-model-id, x-litellm-attempted-retries, content, raciocínio e tool_calls.
- Raciocínio: getattr(m,'reasoning_content',None) or getattr(m,'reasoning',None). Em stream, os dois campos do delta. reasoning_tokens só vale se for coerente com o texto: texto não vazio com reasoning_tokens=0 quer dizer campo não confiável, e aí usar completion_tokens.
- Para ler cabeçalhos no Python: c.chat.completions.with_raw_response.create(...), depois .headers e .parse().
- Parâmetros fora do padrão OpenAI vão em extra_body (chat_template_kwargs, top_k, thinking_token_budget). No curl, vão no topo do corpo.
- Não mandar reasoning_effort e chat_template_kwargs.enable_thinking juntos, porque o vLLM deriva enable_thinking do reasoning_effort. Para 'sem raciocínio', usar só enable_thinking=false (o caminho visto funcionando em 16/10).
- O laço e os critérios decidem pela presença de tool_calls, nunca pelo finish_reason.
- Sem stream, salvo nos braços que pedem stream. Nesses, usar stream_options={'include_usage': True}.
- Só dados INVENTADOS: catálogos, transcrições e resultados de ferramenta gerados por script, com nomes fictícios, só metadados e agregados. Não copiar nomes de tabelas, de colunas nem descrições do catálogo real do ADEX: metadado corporativo também é dado corporativo.
- Nenhum parâmetro de ferramenta se chama 'strict' (o LiteLLM hosted_vllm apagaria).
- Amostragem oficial de cada modo: com raciocínio, temp 1,0, top_p 0,95, top_k 20 e presence 0; sem raciocínio, temp 0,7, top_p 0,8, top_k 20 e presence 1,5. Vale salvo quando o braço do teste diz outra coisa.

ORDEM SUGERIDA: descoberta passiva → B1 → B2 → B5. Depois B3, B4, B6, B8 e B9 em paralelo. Depois B7, então B10, e por fim B11 e B12.
Esforço: P = até meio dia; M = 1 a 2 dias; G = 3 dias ou mais.
Volume estimado: cerca de 2.100 chamadas, a maioria curta. Rodar em lotes e combinar a janela com a CCOE por causa do limite de requisições.
Os números dos critérios são metas do desenho do copiloto, não medições. Eles serão recalibrados depois da primeira rodada.


### B1 · JSON travado, com e sem raciocínio

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* Descoberta passiva da preparação, Bloco A: acesso, base URL e teto real de saída (max_tokens) confirmados, Teste de perfis de raciocínio: confirma que reasoning_effort, enable_thinking e thinking_token_budget chegam ao vLLM

**Pergunta.** Quando pedimos JSON com schema, o hub devolve sempre um JSON que obedece ao schema, com o raciocínio ligado ou desligado? A trava é mesmo do servidor nos dois casos? E o raciocínio fica de fora do JSON?

**Por que importa.** Decide se os momentos que pedem JSON (enquadramento, extração da carta, plano) podem usar response_format direto, inclusive com raciocínio. A alternativa é separar em duas chamadas: o modelo pensa em texto livre e depois extrai o JSON sem raciocínio. Também mostra se o teto de saída (8.192 documentado) comporta raciocínio e JSON juntos, o que destrava a contradição dos tetos de 12K e 16K.

**Como medir.**

1) Montar um schema 'enquadramento' inventado, com enum, array com minItems/maxItems, objeto aninhado e additionalProperties:false.
2) Rodar 6 braços, todos com max_tokens=8192 e sem stream:
(a) sem raciocínio (enable_thinking=false), com amostragem sem raciocínio;
(b) reasoning_effort=low;
(c) reasoning_effort=medium com thinking_token_budget=4096;
(d) reasoning_effort=xhigh sem orçamento;
(e) CONTROLE NEGATIVO sem raciocínio: o mesmo pedido do braço (a), mas o prompt manda 'responda em markdown, sem JSON, com tipo exploratório' (valor fora do enum);
(f) CONTROLE NEGATIVO com raciocínio: igual ao (e), mas com reasoning_effort=low.
Nos braços (b), (c), (d) e (f), usar a amostragem com raciocínio.
3) Rodar o braço (e) em duas variantes: com strict:true e sem o campo strict. O vLLM ignora o strict, então as duas devem dar o mesmo resultado.
4) 10 repetições por braço e por variante (cerca de 70 chamadas).
5) Em cada resposta:
- json.loads(content) e validação com jsonschema (Draft 2020-12);
- procurar '<think>' ou '</think>' no content;
- anotar o campo e o tamanho do raciocínio (em caracteres);
- marcar 'content vazio com tool_calls' (sinal de que o caminho converteu o schema em ferramenta);
- marcar 'length com content só de espaços ou quebras' (espaço infinito);
- anotar finish_reason, usage, latência e x-litellm-response-cost.

**Exemplo de requisição.**

```
# braço (b) low; para 'sem raciocínio' troque reasoning_effort por "chat_template_kwargs":{"enable_thinking":false}
# a chave entra pelo stdin (não aparece na lista de processos); nunca use curl -v nem set -x
printf 'Authorization: Bearer %s\n' "$ADEX_LLM_KEY" | curl -s -H @- -H "Content-Type: application/json" \
 "$ADEX_LLM_BASE_URL/chat/completions" \
 -d '{"model":"qwen-latest","max_tokens":8192,"reasoning_effort":"low","temperature":1.0,"top_p":0.95,"top_k":20,
 "messages":[{"role":"user","content":"Enquadre: queda de uso do app da loja ficticia Acme em 2025."}],
 "response_format":{"type":"json_schema","json_schema":{"name":"enquadramento","strict":true,
 "schema":{"type":"object","additionalProperties":false,"required":["tipo","metricas"],
 "properties":{"tipo":{"enum":["descritivo","comparativo","preditivo","causal"]},
 "metricas":{"type":"array","minItems":1,"maxItems":3,"items":{"type":"object",
 "additionalProperties":false,"required":["nome","agregacao"],
 "properties":{"nome":{"type":"string"},"agregacao":{"enum":["soma","media","taxa"]}}}}}}}}}' \
 | python -c "import sys,json; m=json.load(sys.stdin)['choices'][0]['message']; print(json.loads(m['content'])); print(len(m.get('reasoning_content') or m.get('reasoning') or ''))"
```

**Métrica.** Por braço:
- % de respostas cujo content é JSON válido no schema;
- % com raciocínio vazando no content;
- % com finish_reason=length;
- % de espaço infinito;
- % com raciocínio não vazio;
- tamanho do raciocínio (mediana e máximo, em caracteres, e em reasoning_tokens se for coerente);
- latência mediana.

**Critério de aprovação.** Braços (a), (b), (c), (e) e (f): 10/10 JSON válidos no schema, 0 vazamento de raciocínio no content e 0 finish_reason=length.
Os braços (e) e (f) provam que a trava é do servidor, sem e com raciocínio. As variantes do (e) com e sem strict precisam dar o mesmo resultado.
Raciocínio não vazio em 10/10 nos braços (b), (c) e (f), e vazio em 10/10 nos braços (a) e (e). Raciocínio vazio num braço com raciocínio reprova o braço até a CCOE explicar: a trava começou no 1º token, ou o raciocínio não ligou.
Braço (d), só registro: se 1 ou mais em 10 vierem com length ou content vazio, xhigh fica proibido em chamadas JSON enquanto o teto for 8.192.

**Se falhar.** Se o (e) falhar (veio markdown), o response_format não chega ao vLLM ou é descartado (drop_params ou additional_drop_params). Perguntar à CCOE. Enquanto isso, usar 'JSON por ferramenta nomeada' (B5) com validação em Python.
Se o (e) passar e o (f) falhar, a trava se desliga quando há raciocínio (parser de raciocínio ausente ou mal configurado). A CCOE confere --reasoning-parser qwen3. Até lá, JSON só sem raciocínio, ou 2 chamadas.
Se o raciocínio vazar para o content, mesma correção.
Se aparecer espaço infinito, pedir disable_any_whitespace à CCOE. Até lá, usar max_tokens menor nas chamadas JSON e reparo (B12).
Se vier content vazio com tool_calls, o caminho está convertendo o schema em ferramenta: perguntar à CCOE pelo x-litellm-call-id.
Se medium ou xhigh estourarem o teto, separar em 2 chamadas: raciocínio em texto livre com thinking_token_budget, depois extração do JSON sem raciocínio.


### B2 · Recursos de schema aceitos (e o Pydantic)

*Quem:* nós · *Onde:* local · *Esforço:* M · *Depende de:* B1

**Pergunta.** Quais recursos de JSON Schema o hub aceita e respeita de verdade, inclusive nos schemas que o Pydantic gera sozinho? E o que acontece com campos extras quando o schema não os proíbe?

**Por que importa.** Decide se a biblioteca escreve os schemas em Pydantic e manda direto, com $defs/$ref e anyOf para campo opcional, ou se precisa de um 'tradutor' que simplifica o schema antes do envio. Também separa o que o servidor garante do que fica para o validador em Python. Mostra ainda se um schema que cai no guidance muda de regra sem aviso, o que importa porque o hosted_vllm tira additionalProperties:false das tools.

**Como medir.**

1) Montar cerca de 17 mini-schemas, cada um isolando um recurso: enum; const; array com minItems/maxItems; objeto aninhado em 3 níveis; additionalProperties:false; required; tipo anulável ['string','null']; anyOf; $defs/$ref; pattern sozinho (ex.: ^[a-z][a-z0-9_]*$ para nome de coluna); format date; integer com minimum/maximum; maxLength sozinho. Incluir 3 recursos que o xgrammar não suporta: uniqueItems; pattern junto com maxLength; allOf com 2 itens.
2) Para cada schema: 1 chamada normal (anotar HTTP 200 ou 400 e a mensagem) e 5 chamadas de provocação, em que o prompt pede para violar a regra (7 itens quando maxItems=5, valor fora do enum, campo extra, data como 05/03/2025).
3) Campos extras sem proibição: o mesmo objeto SEM additionalProperties, com a provocação 'acrescente o campo observacao', em 2 versões: uma que fica no xgrammar e outra com pattern+maxLength, que força o guidance no modo auto. 5 provocações cada.
4) Compilação: para 3 schemas novos, comparar a latência da 1ª chamada com a mediana das 3 seguintes. Só comparar chamadas com o mesmo x-litellm-model-id, porque cada réplica tem seu cache.
5) Repetir os passos 1 e 2 com o schema que sai de model_json_schema() de 2 modelos Pydantic inventados da biblioteca (carta do estudo e plano), com extra='forbid' e sem edição.
6) Nas datas, conferir em Python se a data existe no calendário: a trava só garante a forma AAAA-MM-DD.

**Exemplo de requisição.**

```
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
class Metrica(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nome: str
    agregacao: Literal['soma','media','taxa']
class Carta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tipo: Literal['descritivo','comparativo','preditivo','causal']
    metricas: list[Metrica] = Field(min_length=1, max_length=5)
    filtro: str | None
r = c.chat.completions.create(model='qwen-latest', max_tokens=2048,
  temperature=0.7, top_p=0.8, presence_penalty=1.5,
  messages=[{'role':'user','content':'Liste 7 metricas e use tipo exploratorio.'}],  # provocação
  response_format={'type':'json_schema','json_schema':{'name':'carta','strict':True,'schema':Carta.model_json_schema()}},
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
```

**Métrica.** Por recurso: aceito (sim, não ou erro, com a mensagem); % de provocações respeitadas (de 5); latência extra da 1ª chamada (na mesma réplica); schema Pydantic aceito sem edição (sim/não); campo extra aceito sem proibição, no xgrammar e no guidance (de 5); datas impossíveis aceitas (contagem).

**Critério de aprovação.** Os recursos que a biblioteca usa (enum, arrays com limite, aninhado, additionalProperties:false, required, anulável, $defs/$ref) são aceitos e respeitados em 5 de 5 provocações cada.
Os 2 schemas Pydantic são aceitos sem edição e respeitados em 5/5.
A latência extra de compilação é de até 2 s (meta).
Recursos fora do xgrammar e teste de campos extras: só registro (erro 400, troca de backend ou campo extra aceito). O resultado vira regra da biblioteca, como diz o plano B.

**Se falhar.** A biblioteca ganha a função achatar_schema: resolve $ref, troca anyOf por tipo anulável e tira as combinações não suportadas. Toda regra que o servidor não trava vira regra do validador Python (ver B12). Evitar uniqueItems e allOf, e conferir duplicatas em Python.
Se um campo extra passar sem proibição, a biblioteca sempre escreve additionalProperties:false e nunca depende do padrão do backend. O validador Python recusa campo extra.
Se a compilação for lenta, guardar os schemas fixos e reaproveitá-los entre chamadas.


### B3 · JSON certo em 50 repetições (extração)

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* B1, B2, Teste de amostragem: confirma que top_k e presence_penalty chegam ao vLLM

**Pergunta.** Em 50 repetições, quantas vezes o JSON sai válido e com os valores certos, em cada perfil?

**Por que importa.** Escolhe o perfil da extração para a carta (no rascunho: low) e do enquadramento (no rascunho: medium). Também verifica se a amostragem oficial do modo sem raciocínio atrapalha o JSON: a presence_penalty de 1,5 pune repetir tokens, e o JSON repete chaves e valores por natureza.

**Como medir.**

1) Escrever 10 transcrições inventadas de entrevista, de estudos variados: abandono de carrinho, tempo de atendimento, inadimplência fictícia, adoção de um recurso do app, previsão de demanda. Cada uma tem gabarito para os campos da carta: tipo de estudo (enum), unidade de análise, janela de datas, lista de métricas e filtros.
2) Rodar 4 braços:
(a) sem raciocínio, amostragem oficial (temp 0,7, top_p 0,8, top_k 20, presence 1,5);
(b) igual ao (a), mas com presence 0;
(c) low, com amostragem de raciocínio (temp 1,0, top_p 0,95, top_k 20, presence 0);
(d) medium com thinking_token_budget=4096, mesma amostragem do (c).
3) 5 repetições por transcrição por braço, ou seja, 50 chamadas por braço.
4) Validar parse e schema. Comparar campo a campo com o gabarito: enum, data e número por igualdade exata; listas por igualdade de conjunto; texto livre fica fora da conta.
5) Consistência: em quantas transcrições as 5 repetições dão o mesmo resultado nos campos exatos.
6) Classificar os finish_reason=length, separando o espaço infinito (content só de espaços ou quebras).

**Exemplo de requisição.**

```
r = c.chat.completions.create(model='qwen-latest', max_tokens=4096,
  temperature=0.7, top_p=0.8, presence_penalty=1.5,   # braço (a)
  messages=[{'role':'system','content':'Extraia a carta do estudo.'},
            {'role':'user','content':transcricao_inventada}],
  response_format={'type':'json_schema','json_schema':{'name':'carta','strict':True,'schema':schema_carta}},
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
acertos = comparar(json.loads(r.choices[0].message.content), gabarito)
```

**Métrica.** Por braço: % de JSON válido; % de campos exatos (por campo e no total); nº de transcrições consistentes (de 10); tokens e custo médios por chamada; distribuição de finish_reason, com espaço infinito à parte.

**Critério de aprovação.** 100% de JSON válido em todos os braços. Zero falha em 50 indica falha real provavelmente abaixo de 6% (regra de 3). O limite é otimista, porque as 5 repetições da mesma transcrição não são independentes, por isso a validação em Python continua obrigatória.
O perfil escolhido precisa de pelo menos 95% de campos exatos e pelo menos 8 de 10 transcrições consistentes.
Entre os braços aprovados, fica o mais barato que estiver a até 2 pontos do melhor.

**Se falhar.** Se nenhum braço chegar a 95%: dividir a extração em 2 chamadas menores (campos estruturais e métricas), e devolver ao dev os campos que divergirem entre 2 repetições ('confirme este campo').
Se a presence 1,5 piorar o resultado, a biblioteca usa presence 0 em todas as chamadas JSON.
Se aparecer espaço infinito, mesma saída do B1 (disable_any_whitespace com a CCOE).


### B4 · Troca silenciosa de nomes de colunas

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* B1, B2

**Pergunta.** Quando o modelo devolve nomes de colunas, ele copia certo, inventa nomes parecidos que não existem, ou pega uma coluna parecida que existe mas é a errada?

**Por que importa.** A descoberta de dados por metadados depende de nomes exatos. Um nome trocado (vl_fatura_m1 virando vl_fatura_mes1) quebra o SQL. Pior: o modelo pode pegar uma coluna parecida, existente e errada (vl_fatura_m01 no lugar de vl_fatura_m1), e o SQL roda com o número errado. O enum impede o primeiro erro, mas não o segundo, e pode até piorá-lo. O teste decide se a biblioteca trava os nomes com enum no schema (lista fechada gerada pelo Python a partir do catálogo) ou se usa texto livre com conferência em Python e reparo.

**Como medir.**

1) Gerar por script um catálogo inventado: 4 tabelas, cerca de 400 colunas, com armadilhas: quase duplicadas (dt_ativacao / dt_ativacao_linha), sufixos (vl_fatura_m1 / m01 / m_1), abreviações em pt e en (qtd_ / qty_), nomes sem acento. Nada copiado do catálogo real do ADEX.
2) Escrever 20 perguntas de estudo inventadas, com gabarito das colunas relevantes.
3) Rodar 3 braços, 2 repetições cada:
(a) colunas como string livre;
(b) colunas com enum igual à lista completa do catálogo;
(c) como (a), mas pedindo 'tabela.coluna' e com raciocínio low.
4) Teste de cópia pura: dar 60 nomes e pedir todos de volta, iguais.
5) Classificar cada nome devolvido, usando distância de edição (ex.: rapidfuzz):
- certo: existe e está no gabarito;
- vizinho errado: existe, não está no gabarito e fica a até 2 letras de um nome do gabarito (a troca silenciosa que o enum não impede);
- quase: não existe, mas fica a até 2 letras de um nome real;
- inventado.
6) No braço (b), medir a latência da 1ª chamada com enum de 400 valores e repetir com 2.000 valores, na mesma réplica (x-litellm-model-id).

**Exemplo de requisição.**

```
cols = sorted(catalogo_inventado)   # gerado pelo Python, nunca pelo modelo
schema = {'type':'object','additionalProperties':False,'required':['colunas'],
  'properties':{'colunas':{'type':'array','maxItems':15,'items':{'enum':cols}}}}   # braço (b)
r = c.chat.completions.create(model='qwen-latest', max_tokens=2048,
  temperature=0.7, top_p=0.8, presence_penalty=1.5,
  messages=[{'role':'user','content':pergunta + '\n\nCatalogo:\n' + metadados_texto}],
  response_format={'type':'json_schema','json_schema':{'name':'colunas','strict':True,'schema':schema}},
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
nomes = json.loads(r.choices[0].message.content)['colunas']
inexistentes = [n for n in nomes if n not in set(cols)]
```

**Métrica.** Por braço: % de nomes inexistentes (quase + inventado); % de vizinho errado; recall e precisão contra o gabarito. Cópia pura: exatos de 60. Latência de compilação do enum com 400 e com 2.000 valores.

**Critério de aprovação.** Obrigatório pelo desenho: depois da conferência em Python, nenhum nome inexistente chega ao plano.
Do modelo:
- braço (a): até 1% de nomes inexistentes e 60/60 na cópia pura;
- braço (b): 0 inexistentes (isso só confirma que a trava funciona) e compilação de até 2 s com 2.000 valores;
- o enum só é adotado se o braço (b) também tiver taxa de vizinho errado não maior que a do (a) e recall de pelo menos 90%;
- no braço escolhido: recall de pelo menos 90% e no máximo 2% de vizinho errado entre os nomes devolvidos (meta).

**Se falhar.** Se o enum for lento ou rejeitado, há três saídas:
- usar enum só com as colunas das tabelas já escolhidas, em 2 etapas (tabela, depois coluna);
- o modelo devolve IDs curtos (ex.: c017) que o Python traduz de volta;
- reparo com lista de nomes parecidos (B12).
Se o enum aumentar o vizinho errado, ele só entra depois do filtro por tabela, e o Python avisa o dev quando uma coluna escolhida tiver uma vizinha parecida no catálogo ('quis dizer vl_fatura_m1 ou vl_fatura_m01?').
Se a cópia pura falhar, nunca pedir ao modelo para 'repetir' listas: o Python monta as listas.


### B5 · Ferramenta simples, modos de tool_choice, strict e stream

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* B1

**Pergunta.** O modelo chama a ferramenta certa, com argumentos válidos, e obedece aos modos auto, required, nomeada e none? O 'strict' das ferramentas chega ao vLLM e trava os argumentos? As chamadas saem iguais com stream?

**Por que importa.** Define a política da biblioteca: com ação obrigatória (escolher um bloco de método), usar required ou nomeada; em conversa, usar auto.
Também verifica dois riscos achados na pesquisa:
- se o modelo estiver cadastrado no LiteLLM como hosted_vllm/, o LiteLLM tira strict e additionalProperties:false das tools, e os argumentos ficam sem trava;
- o caminho com stream usa outro código do parser e tem bugs abertos.
O resultado diz se a biblioteca pode confiar na trava e se pode usar stream com ferramentas.

**Como medir.**

1) Criar 4 ferramentas inventadas: buscar_tabelas(termo, dominio enum); descrever_tabela(tabela); contar_agregado(tabela, metrica enum, agrupar_por array de até 3); aplicar_bloco(bloco enum, parametros objeto).
2) Escrever 20 prompts com gabarito: 14 pedem ferramenta (nome e argumentos-chave) e 6 não pedem (pergunta conceitual, pedido de esclarecimento).
3) Rodar nos 4 modos (auto; required; nomeada {'type':'function','function':{'name':...}}; none), sem raciocínio, sem stream, 3 repetições (240 chamadas).
4) Conferir:
- tool_calls presente;
- arguments passa no json.loads e valida no schema;
- nome e argumentos batem com o gabarito;
- finish_reason, só registro (na escolha nomeada a OpenAI devolve 'stop');
- texto no content junto com a chamada, só registro;
- no modo none, nenhuma marcação vazando no content ('<tool_call>' ou '<function=').
5) Prova do strict, no modo auto, onde só o strict liga a trava dos argumentos:
- ferramenta contar_agregado com metrica enum ['contagem','soma','media'] e additionalProperties:false;
- 10 provocações ('use a metrica mediana e inclua o campo observacao');
- braço S0 sem strict e braço S1 com strict:true;
- repetir os 2 braços em modo nomeado (N0 e N1);
- pré-condição: o S0 precisa ter pelo menos 3 violações em 10; senão, reforçar a provocação, porque sem violação na linha de base o teste não prova nada.
Leitura:
- S1 = 0 e S0 ≥ 3: o strict chega e o vLLM trava;
- S1 ≈ S0: o strict não chega (hosted_vllm remove, ou VLLM_ENFORCE_STRICT_TOOL_CALLING=false);
- N0 = N1 = 0 com S1 ≈ S0: a nomeada está no caminho de JSON derivado do schema (variável desligada).
Pedir à CCOE o corpo repassado ao vLLM pelo x-litellm-call-id de 3 chamadas S1.
6) Stream: repetir auto e required com stream=true nos 20 prompts (1 repetição). Montar tool_calls a partir dos deltas e comparar com o resultado sem stream. Registrar marcação crua em delta.content, finish_reason e erros 500 (#49216).

**Exemplo de requisição.**

```
tools = [{'type':'function','function':{'name':'contar_agregado','strict':True,   # braço S1; S0 = sem 'strict'
  'parameters':{'type':'object','additionalProperties':False,'required':['tabela','metrica'],
    'properties':{'tabela':{'type':'string'},'metrica':{'enum':['contagem','soma','media']}}}}}]
raw = c.chat.completions.with_raw_response.create(model='qwen-latest', tools=tools,
  tool_choice='auto', max_tokens=1024, temperature=0.7, top_p=0.8, presence_penalty=1.5,
  messages=[{'role':'user','content':'Use contar_agregado na tabela ficticia vendas_demo com metrica mediana e inclua o campo observacao.'}],
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
r = raw.parse(); t = (r.choices[0].message.tool_calls or [None])[0]
print(raw.headers.get('x-litellm-call-id'), t and t.function.arguments)   # violou: metrica fora do enum ou campo extra
```

**Métrica.** % de tool_calls parseáveis; % de nome certo; % de argumentos certos e válidos no schema; chamadas indevidas no auto (nos 6 prompts sem ferramenta); obediência por modo; vazamento de marcação no none; violações em S0, S1, N0 e N1 (de 10); no stream, % de chamadas iguais às do modo sem stream, % de marcação crua no content e nº de erros 500.

**Critério de aprovação.** - required e nomeada: 100% com pelo menos 1 chamada, e 100% com o nome pedido na nomeada.
- none: 0 chamadas e 0 vazamentos.
- auto: pelo menos 95% de nome certo nos 14 prompts com ferramenta (42 tentativas, no máximo 2 erros), e no máximo 1 chamada indevida em 18 tentativas (6 prompts × 3).
- Argumentos válidos no schema: pelo menos 98% sem strict. Nos braços com strict, 100%, se a prova mostrar que o strict chega.
- Stream: tool_calls iguais aos do modo sem stream em 100% dos casos e nenhuma marcação crua no content. Senão, a biblioteca não usa stream com ferramentas.

**Se falhar.** Se o auto for fraco: 2 passos (um JSON com enum decide 'precisa de ferramenta?' e depois vem a chamada nomeada).
Se o strict não chegar: pedir à CCOE o cadastro como openai/ ou --tool-strict-level parameter no vLLM. Até lá, validar os argumentos em Python e reparar (B12).
Se a nomeada falhar: usar response_format JSON {ferramenta, argumentos} (B1) e despachar em Python. Nunca misturar response_format e tools na mesma chamada.
Se o stream divergir ou der 500: a biblioteca chama ferramentas sempre sem stream, e o stream fica só para a narrativa em texto. Avisar a CCOE com o x-litellm-call-id.


### B6 · Várias chamadas no mesmo turno

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* B5

**Pergunta.** Quando o pedido tem 3 coisas independentes, o modelo faz as 3 chamadas de uma vez? E o parallel_tool_calls=false é aplicado de verdade?

**Por que importa.** Define o executor da biblioteca. Se o modelo fizer as chamadas em lote, o Python executa as consultas de metadados em paralelo e devolve todos os resultados, cada um com seu tool_call_id: menos voltas, menos tempo e menos custo. Se não fizer, o laço é serial.

**Como medir.**

1) Escrever 10 prompts com 2 a 4 ações independentes (ex.: 'descreva as tabelas fictícias a, b e c').
2) Rodar 2 braços, sem raciocínio e em low, 3 repetições (120 chamadas):
- braço P: sem enviar parallel_tool_calls (o padrão do vLLM é true);
- braço F: parallel_tool_calls=false.
3) Contar tool_calls por turno e conferir se os ids são únicos e os argumentos certos.
4) Devolver todos os resultados (simulados) e conferir se a resposta final usa todos.
Leitura:
- o braço F só prova algo se o P fizer lote em pelo menos 50% dos turnos (pré-condição); senão, o resultado do F é inconclusivo;
- se o F tiver tanto lote quanto o P, o false não chega ou é ignorado (LiteLLM ou vLLM). Perguntar à CCOE pelo x-litellm-call-id.

**Exemplo de requisição.**

```
r = c.chat.completions.create(model='qwen-latest', tools=tools_b5, parallel_tool_calls=False,   # braço F
  temperature=0.7, top_p=0.8, presence_penalty=1.5, max_tokens=2048,
  messages=[{'role':'user','content':'Descreva as tabelas ficticias pedidos_demo, lojas_demo e clientes_demo.'}],
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
calls = r.choices[0].message.tool_calls or []
print(len(calls), len({t.id for t in calls}))
```

**Métrica.** % de turnos com todas as chamadas esperadas de uma vez (braço P); % de ids únicos; % de argumentos certos; no braço F, % de turnos com exatamente 1 chamada; uso de todos os resultados na resposta final.

**Critério de aprovação.** Braço P: pelo menos 90% dos turnos com todas as chamadas esperadas num turno só, e 100% de ids únicos.
Braço F (se a pré-condição valer): 100% dos turnos com 1 chamada só. Se não for assim, a biblioteca garante isso sozinha.

**Se falhar.** Se o modelo não fizer lote: laço serial, com o custo das voltas extras medido no B7.
Se ignorar o false: a biblioteca executa só a 1ª chamada e responde às outras 'não executada, peça de novo', ou passa a aceitar o lote.
Se os ids vierem duplicados: a biblioteca gera ids próprios antes de devolver os resultados.


### B7 · Laço de várias voltas com ferramentas

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* G · *Depende de:* B5, B6

**Pergunta.** Num laço de várias voltas (chama a ferramenta, recebe o resultado, decide o próximo passo), o modelo chega ao fim certo sem repetir chamadas nem se perder? Devolver o raciocínio anterior ajuda, e ele chega mesmo ao modelo?

**Por que importa.** É o coração da descoberta de dados e das etapas. Decide:
- o limite de voltas;
- o formato do histórico: devolver ou não o raciocínio anterior. O preserve_thinking vem ligado por padrão no Qwen3.8; o LiteLLM hosted_vllm só repassa reasoning_content se for texto, e o vLLM renomeia esse campo para reasoning na entrada;
- o que a biblioteca faz quando o modelo repete a mesma chamada.

**Como medir.**

1) Escrever 3 roteiros inventados, com ferramentas simuladas em Python que devolvem respostas fixas (só metadados e agregados inventados). Exemplo: achar a tabela de pedidos, descrever, contar por mês e responder com marcadores {{n_pedidos}}.
2) 10 episódios por roteiro em low e em medium (60 episódios), no formato de histórico (i): mensagens assistant com tool_calls, sem raciocínio.
3) Mais 30 episódios em low, com o raciocínio anterior devolvido: 15 no formato (ii-a), com o campo reasoning_content, e 15 no (ii-b), com o campo reasoning.
4) Prova de que o raciocínio chega: para 5 históricos fixos, mandar o mesmo histórico nos formatos (i), (ii-a) e (ii-b) e comparar usage.prompt_tokens. Se não crescer em (ii), o raciocínio foi descartado no caminho (LiteLLM ou template).
5) Em cada volta, devolver cada resultado com role 'tool' e o tool_call_id certo.
6) Conferir: chegou à resposta final? Com os marcadores certos? Quantas voltas? Repetiu chamada idêntica (mesmo nome e mesmos argumentos)? Houve HTTP 400 por causa do histórico?

**Exemplo de requisição.**

```
for volta in range(8):
    r = c.chat.completions.create(model='qwen-latest', tools=tools_b5, messages=msgs,
            reasoning_effort='low', max_tokens=8192, temperature=1.0, top_p=0.95,
            extra_body={'top_k':20})
    m = r.choices[0].message
    if not m.tool_calls: break                      # resposta final (decide por tool_calls, não por finish_reason)
    msgs.append({'role':'assistant','content':m.content or '',
                 'tool_calls':[t.model_dump(exclude_none=True) for t in m.tool_calls]})   # formato (i)
    for t in m.tool_calls:
        res = STUB[t.function.name](**json.loads(t.function.arguments))  # dados inventados
        msgs.append({'role':'tool','tool_call_id':t.id,'content':json.dumps(res, ensure_ascii=False)})
```

**Métrica.** % de episódios concluídos corretos; voltas (média e máximo); % de episódios com chamada repetida; nº de erros 400; tokens e custo por episódio; diferença entre os formatos (i), (ii-a) e (ii-b); crescimento de prompt_tokens em (ii) contra (i).

**Critério de aprovação.** Pelo menos 90% dos episódios concluídos corretos em até 6 voltas; nenhum erro 400 de histórico; no máximo 5% dos episódios com chamada repetida.
Um formato (ii) só é adotado se o prompt_tokens provar que o raciocínio chegou e se o acerto for pelo menos igual ao do formato (i).

**Se falhar.** A biblioteca detecta repetição (mesmo nome e argumentos) e responde com o resultado já guardado mais um aviso. Terá teto de voltas com handoff ao dev.
Se o formato (ii) der erro, não chegar ou piorar o resultado, a biblioteca não devolve o raciocínio.
Se medium não for melhor que low, o laço fica em low.


### B8 · Idioma depois de resultado em inglês

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* P · *Depende de:* B5

**Pergunta.** Se a ferramenta devolve texto em inglês (mensagem de erro, descrição de coluna), o modelo continua respondendo em português?

**Por que importa.** Metadados e erros de banco costumam vir em inglês, e a narrativa para o dev precisa sair em pt-BR. O cartão oficial do Qwen3.8 avisa que presence_penalty alta pode misturar idiomas, e o modo sem raciocínio recomenda 1,5. O teste decide o prompt de sistema e se a biblioteca 'embrulha' os resultados das ferramentas com um cabeçalho em português.

**Como medir.**

1) Montar históricos fixos em que o último resultado de ferramenta vem em inglês. Exemplos: 'ERROR: column dt_cancel does not exist. Did you mean dt_cancelamento?' e 'Table orders_demo: daily snapshot of orders'.
2) Rodar 3 braços:
(a) sem instrução de idioma;
(b) system 'Responda sempre em português do Brasil';
(c) o braço (b) mais o resultado embrulhado ('Resultado da ferramenta, em inglês: ...').
3) Cada braço em 3 configurações: sem raciocínio com presence 1,5; sem raciocínio com presence 0; low. São 20 chamadas por combinação (180 no total).
4) Classificar a resposta final por frase, com langdetect fixado (DetectorFactory.seed = 0), depois de tirar blocos de código, nomes de coluna e o trecho citado do erro. Uma frase em outro idioma conta como mistura. Somar leitura humana de 10% das respostas.

**Exemplo de requisição.**

```
msgs = [{'role':'system','content':'Responda sempre em portugues do Brasil.'},
 {'role':'user','content':'Quantos pedidos cancelados houve em marco na base ficticia?'},
 {'role':'assistant','content':'','tool_calls':[{'id':'c1','type':'function','function':
   {'name':'contar_agregado','arguments':'{"tabela":"pedidos_demo","metrica":"contagem"}'}}]},
 {'role':'tool','tool_call_id':'c1','content':'ERROR: column dt_cancel does not exist. Did you mean dt_cancelamento?'}]
r = c.chat.completions.create(model='qwen-latest', tools=tools_b5, messages=msgs, max_tokens=2048,
  temperature=0.7, top_p=0.8, presence_penalty=1.5,
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
```

**Métrica.** % de respostas finais inteiramente em pt-BR; % com mistura de idiomas (ao menos 1 frase em outro idioma); % inteiramente em inglês; idioma do raciocínio (só registro).

**Critério de aprovação.** No braço escolhido: 20/20 respostas inteiramente em pt-BR em cada configuração adotada, ou seja, nenhuma mistura e nenhuma resposta inteira em inglês. Com amostras maiores na recalibração, a meta é pelo menos 98%.

**Se falhar.** A biblioteca traduz ou normaliza as mensagens de erro para português antes de devolvê-las ao modelo. Pode também reforçar o idioma no fim do último turno, usar presence 0 e checar o idioma em Python, com 1 reparo (B12).


### B9 · Argumentos grandes: até onde não corta

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* B5, Bloco A: teto real de saída (max_tokens)

**Pergunta.** Qual é o maior argumento de ferramenta (um código, uma lista longa) que o modelo devolve inteiro e sem erro, na escolha nomeada e no auto, com e sem stream?

**Por que importa.** Decide o desenho do rascunho de código e do plano. A dúvida é se um argumento pode carregar um SQL ou Python de 2 a 4 mil tokens, ou se a biblioteca passa só referências (ids de blocos, nomes) e o conteúdo grande vem no content, fora da ferramenta.
O argumento conta no max_tokens (teto documentado de 8.192), junto com o raciocínio. O formato de ferramenta do Qwen é XML, com o valor entre tags, e isso pode quebrar com certos caracteres.
Conforme a configuração do servidor, a escolha nomeada pode nem usar o formato XML, por isso o auto, que é o caminho real do laço, também entra no teste.

**Como medir.**

1) Criar as ferramentas salvar_rascunho(codigo: string) e salvar_lista(colunas: array de string).
2) O prompt entrega um conteúdo inventado e pede para passá-lo idêntico, em tamanhos de cerca de 250, 1K, 2K, 4K, 6K e 7,5K tokens. O código inclui caracteres difíceis: aspas, quebras de linha, tabulação, barra invertida, <, >, & e o texto literal </parameter>.
3) Rodar sem raciocínio, com max_tokens=8192, em 2 modos (nomeada; auto com prompt que pede a ferramenta), com e sem stream=true, 5 repetições por tamanho (cerca de 120 chamadas). No auto, 'não chamou' conta à parte.
4) Comparar o código byte a byte e a lista como conjunto. Medir o tamanho real pelo usage.completion_tokens da própria resposta. Anotar finish_reason e se veio texto no content.

**Exemplo de requisição.**

```
payload = sql_inventado(n_tokens=4000)   # inclui aspas, <, >, & e o texto </parameter>
tools = [{'type':'function','function':{'name':'salvar_rascunho','parameters':{'type':'object',
  'required':['codigo'],'properties':{'codigo':{'type':'string'}}}}}]
r = c.chat.completions.create(model='qwen-latest', max_tokens=8192, tools=tools,
  tool_choice={'type':'function','function':{'name':'salvar_rascunho'}},   # braço auto: tool_choice='auto'
  temperature=0.7, top_p=0.8, presence_penalty=1.5,
  messages=[{'role':'user','content':'Passe EXATAMENTE este codigo para salvar_rascunho:\n' + payload}],
  extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
arg = json.loads(r.choices[0].message.tool_calls[0].function.arguments)['codigo']
print(arg == payload, r.choices[0].finish_reason, r.usage.completion_tokens)
```

**Métrica.** % de cópias exatas por tamanho, por modo de escolha e com ou sem stream; % de argumentos que nem passam no json.loads; % de finish_reason=length; % de 'não chamou' no auto; caractere ou trecho onde a cópia quebrou.

**Critério de aprovação.** O tamanho seguro é o maior com 5/5 cópias exatas no auto e na nomeada, com e sem stream. A meta é que ele seja de pelo menos 2K tokens (rascunho de código e parâmetros de bloco). A biblioteca usa no máximo metade do tamanho seguro.

**Se falhar.** O código grande sai no content, em bloco ```python, e não como argumento. Outras saídas: dividir em várias chamadas (parte 1 de 3...); passar referências em vez de conteúdo; ou carregar o código num response_format JSON, que o vLLM trava. Se </parameter> quebrar a chamada, a biblioteca bloqueia esse texto em argumentos.


### B10 · Turnos vazios: só raciocínio, sem ação

*Quem:* nós + CCOE · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* B7, Teste de perfis de raciocínio: reasoning_effort e thinking_token_budget chegando ao vLLM

**Pergunta.** Com que frequência o modelo pensa, mas não entrega nem resposta nem chamada de ferramenta? E o thinking_token_budget é aplicado de verdade?

**Por que importa.** Um turno vazio com finish_reason=stop faz o laço achar que acabou. O vLLM só lê chamadas no content; se o modelo escreve a chamada dentro do raciocínio, ela pode se perder (issue aberta #39056, vista com Qwen3.5). O parser novo trata <tool_call> como fim implícito do raciocínio, o que pode resolver o sintoma, mas também pode executar uma chamada que o modelo só estava cogitando. O teste decide o detector e a recuperação da biblioteca, e pesa na escolha de low, medium ou xhigh nos momentos com ação.

**Como medir.**

1) Reaproveitar todos os turnos do B7 e acrescentar 30 turnos de ação em xhigh (max_tokens=8192).
2) Classificar cada turno:
- ok;
- vazio por teto (finish_reason=length, content vazio);
- chamada perdida (stop, e '<tool_call>' ou '<function=' dentro do raciocínio);
- marcação crua no content (tool_calls vazio e '<tool_call>' ou '<function=' no texto);
- vazio com stop, sem mais nada.
3) Teste dirigido, que também prova que o orçamento é aplicado: xhigh com max_tokens=2048 sem thinking_token_budget (deve provocar length). Depois o mesmo com thinking_token_budget=1024 (deve fechar o raciocínio e agir). Se os dois derem o mesmo resultado, o orçamento não está chegando ou não está configurado (--reasoning-config).
4) Recuperação: depois de cada turno vazio, 1 nova chamada com tool_choice='required', sem stream.

**Exemplo de requisição.**

```
r = c.chat.completions.create(model='qwen-latest', tools=tools_b5, messages=historico_b7,
  reasoning_effort='xhigh', max_tokens=2048, temperature=1.0, top_p=0.95,
  extra_body={'top_k':20,'thinking_token_budget':1024})
ch = r.choices[0]; m = ch.message
pensou = getattr(m,'reasoning_content',None) or getattr(m,'reasoning',None) or ''
texto = m.content or ''
vazio = not texto.strip() and not m.tool_calls
cru = not m.tool_calls and ('<tool_call>' in texto or '<function=' in texto)
print(ch.finish_reason, vazio, cru, '<tool_call>' in pensou or '<function=' in pensou)
```

**Métrica.** % de turnos vazios por tipo e por perfil; % de chamadas perdidas dentro do raciocínio; % de marcação crua no content; % recuperados com 1 nova chamada; tokens gastos nos turnos vazios; resultado do teste dirigido com e sem orçamento.

**Critério de aprovação.** No máximo 2% de turnos vazios com stop em low e medium.
0 marcação crua no content.
Nenhum vazio por length quando o thinking_token_budget for no máximo max_tokens − 2.048. No teste dirigido, o braço com orçamento precisa agir em pelo menos 9 de 10.
Pelo menos 95% de recuperação em 1 tentativa.

**Se falhar.** O detector de turno vazio fica obrigatório na biblioteca, com 1 recuperação usando required. Evitar xhigh em turnos de ação. Pedir à CCOE uma versão do vLLM com correção e acompanhar a issue.
Se o orçamento não for aplicado: pedir à CCOE o --reasoning-config. Até lá, momentos com ação ficam em low.
Último recurso, arriscado: ler o <tool_call> de dentro do raciocínio com parser próprio e validação forte. O risco é executar algo que o modelo já tinha descartado.


### B11 · Acerto da escolha por perfil de raciocínio

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* B5, B10

**Pergunta.** Desligar o raciocínio na escolha de ferramenta ou bloco custa acerto? Low ou medium compensam o custo?

**Por que importa.** O rascunho prevê 'sem raciocínio' na escolha de ferramenta ou bloco. Este teste confirma ou troca essa escolha com números do próprio hub. Isso afeta a latência e o custo do laço inteiro.

**Como medir.**

1) Escrever 30 cenários inventados (estado do estudo mais o pedido), com gabarito de ferramenta ou bloco e argumentos curtos.
2) Usar as ferramentas do B5 mais 8 blocos de método inventados no enum de aplicar_bloco.
3) Rodar 3 perfis, cada um com a sua amostragem oficial: off (enable_thinking=false); low; medium com thinking_token_budget=2048.
4) 3 repetições, ou seja, 90 chamadas por perfil, com tool_choice='required' e sem stream.
5) Conferir se o raciocínio veio não vazio em low e medium. Se vier vazio, a trava do required começou no 1º token e o braço, na prática, rodou sem raciocínio.

**Exemplo de requisição.**

```
OFF  = dict(temperature=0.7, top_p=0.8, presence_penalty=1.5,
            extra_body={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}})
PERFIS = {'off': OFF,
  'low': dict(reasoning_effort='low', temperature=1.0, top_p=0.95, extra_body={'top_k':20}),
  'medium': dict(reasoning_effort='medium', temperature=1.0, top_p=0.95,
                 extra_body={'top_k':20,'thinking_token_budget':2048})}
raw = c.chat.completions.with_raw_response.create(model='qwen-latest', tools=tools_blocos,
  tool_choice='required', max_tokens=4096, messages=cenario['mensagens'], **PERFIS['low'])
r = raw.parse(); t = r.choices[0].message.tool_calls[0]
acertou = t.function.name == cenario['gabarito']['nome']
custo = float(raw.headers.get('x-litellm-response-cost') or 0)
```

**Métrica.** Por perfil: % de nome certo; % de argumentos certos; latência mediana e p95 (90 amostras); tamanho do raciocínio (texto e reasoning_tokens, se coerente); % com raciocínio não vazio; custo somado (x-litellm-response-cost).

**Critério de aprovação.** O perfil escolhido é o mais barato que tiver pelo menos 95% de nome certo, pelo menos 90% de argumentos certos e ficar a até 2 pontos do melhor perfil nos dois.
Low e medium só valem se vierem com raciocínio não vazio em pelo menos 95% das chamadas.

**Se falhar.** Se nenhum perfil chegar lá: reduzir as opções por chamada (o Python filtra os blocos antes), melhorar as descrições das ferramentas, ou fazer em 2 passos (escolhe o bloco por JSON com enum, depois preenche os argumentos).
Se o raciocínio vier vazio com required: comparar com tool_choice='auto' mais o detector do B10, e levar o caso à CCOE.


### B12 · Custo e eficácia do reparo

*Quem:* nós · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* B3, B4, B5

**Pergunta.** Quando o validador em Python acha um erro, 1 nova tentativa com a mensagem do erro conserta? Quanto isso custa?

**Por que importa.** Define a política de reparo da biblioteca: 0, 1 ou 2 tentativas, e quando devolver ao dev. Define também o orçamento de tokens. Há erros que o schema não pega (coluna fora do catálogo, data_fim antes de data_inicio, agregação que não combina com a métrica, data inexistente), e esses só o Python encontra.

**Como medir.**

1) Juntar as falhas reais de B3, B4 e B5 e completar com 30 casos provocados: uma resposta errada inventada mais a mensagem do validador. Exemplo: 'coluna vl_fatura_mes1 não existe; parecidas: vl_fatura_m1, vl_fatura_m01'.
2) O reparo é a mesma conversa, no mesmo perfil e com o mesmo response_format ou as mesmas tools da chamada original:
- JSON de response_format: a resposta errada entra como assistant e a mensagem do validador como user;
- chamada de ferramenta: o assistant com tool_calls fica como veio, e o erro volta como mensagem role 'tool' com o mesmo tool_call_id (cada tool_call precisa da sua resposta 'tool').
3) Comparar 2 formatos de mensagem: (a) só o erro; (b) o erro mais sugestões do Python.
4) Medir tokens, latência e custo do reparo contra a chamada original. Anotar usage.prompt_tokens_details.cached_tokens, se vier, para ver se o começo repetido da conversa foi reaproveitado. Nos prompts abaixo de ~5K tokens, cache zero é esperado no modelo híbrido e não conta como falha.

**Exemplo de requisição.**

```
# JSON (response_format): erro como user
msgs = conversa_original + [
  {'role':'assistant','content':json.dumps(saida_errada, ensure_ascii=False)},
  {'role':'user','content':'O validador recusou: coluna vl_fatura_mes1 nao existe. Parecidas: vl_fatura_m1, vl_fatura_m01. Devolva o JSON corrigido.'}]
# ferramenta: erro como resultado da propria chamada
# msgs = conversa_original + [msg_assistant_com_tool_calls,
#   {'role':'tool','tool_call_id':t.id,'content':'ERRO de validacao: metrica mediana nao existe. Use: contagem, soma, media.'}]
raw = c.chat.completions.with_raw_response.create(model='qwen-latest', messages=msgs,
  response_format=rf_original, **perfil_original)
r = raw.parse()
print(r.usage.prompt_tokens, getattr(r.usage.prompt_tokens_details,'cached_tokens',None),
      raw.headers.get('x-litellm-response-cost'))
```

**Métrica.** % consertado em 1 tentativa, por formato e por tipo (JSON ou ferramenta); % que conserta mas cria erro novo; custo do reparo dividido pelo custo da chamada original; cached_tokens no reparo.

**Critério de aprovação.** Pelo menos 80% consertados em 1 tentativa no melhor formato; no máximo 2% com erro novo; custo médio do reparo de até 1,5 vez o da chamada original.

**Se falhar.** Se ficar abaixo de 80%, prevenir em vez de reparar: listas fechadas e o enum do B4, schemas mais rígidos. O reparo vira uma pergunta ao dev ('quis dizer vl_fatura_m1?'). Nunca mais de 1 tentativa automática.


### Perguntas ao CCOE · B

- **Qual a versão exata do vLLM e qual o comando 'vllm serve' completo do qwen-latest, com as variáveis de ambiente? Interessam --tool-call-parser, --reasoning-parser, --reasoning-config, --enable-auto-tool-choice, --tool-strict-level, --structured-outputs-config, --chat-template, --default-chat-template-kwargs, --generation-config, --max-model-len, --enable-prompt-tokens-details, --enable-prefix-caching, --exclude-tools-when-tool-choice-none, --speculative-config, --kv-cache-dtype e a variável VLLM_ENFORCE_STRICT_TOOL_CALLING. A versão do LiteLLM nós lemos no cabeçalho x-litellm-version; basta confirmar.** — O comportamento muda com a versão. O motor novo do parser do Qwen3 entrou na v0.24.0. O strict das ferramentas, o padrão do --tool-strict-level, o bug de chamada perdida no raciocínio (#39056) e o erro 500 com required em stream (#49216) dependem da versão e daquela variável de ambiente. O thinking_token_budget depende do --reasoning-config e o cached_tokens do --enable-prompt-tokens-details. Sem isso não dá para interpretar B1, B5, B9, B10 e B12.
- **Como o qwen-latest está cadastrado no LiteLLM: prefixo hosted_vllm/, openai/ ou outro? (Antes, tentaremos ler isso em /model/info com a nossa chave.)** — No prefixo hosted_vllm/, o LiteLLM apaga 'strict' e 'additionalProperties:false' das tools antes de repassar ao vLLM. Aí os argumentos das ferramentas não ficam travados (B5). Só a CCOE pode trocar o prefixo ou ligar --tool-strict-level parameter no vLLM.
- **Podem mostrar o corpo da requisição repassada ao vLLM em 3 chamadas de teste, identificadas pelo x-litellm-call-id? Para isso o LiteLLM precisa guardar o corpo (ex.: store_prompts_in_spend_logs) ou ter log de depuração ligado.** — É a única forma de provar que response_format, strict, parallel_tool_calls, chat_template_kwargs, top_k e thinking_token_budget chegam intactos ao vLLM quando um teste dá 'igual com e sem'. As chamadas só terão dados inventados.
- **O teto de saída de 8.192 tokens é imposto onde (LiteLLM, generation-config do vLLM ou outro lugar)? Pode subir para o qwen-latest?** — Raciocínio e resposta dividem o mesmo max_tokens. Com 8.192, os tetos de raciocínio de 12K e 16K do rascunho são impossíveis, e as chamadas JSON com xhigh correm risco de voltar vazias (B1, B10).
- **Qual a configuração do LiteLLM: drop_params, additional_drop_params, allowed_openai_params, enable_json_schema_validation, num_retries, timeout e tamanho máximo do corpo? Parâmetros fora do padrão OpenAI (top_k, min_p, chat_template_kwargs, thinking_token_budget) são repassados?** — Com drop_params, um parâmetro descartado não dá erro: o teste passa 'por acaso' e o efeito some (B1, B3). A própria doc do LiteLLM usa response_format em vLLM como exemplo de additional_drop_params. O num_retries do LiteLLM distorce a latência. O tamanho do corpo limita enums grandes de colunas (B4) e argumentos grandes (B9).
- **Qual backend de saída estruturada está configurado (auto, xgrammar ou guidance)? disable_any_whitespace, disable_additional_properties e enable_in_reasoning estão ligados?** — Com backend fixo em xgrammar, schemas com recursos fora do xgrammar dão erro 400 em vez de trocar de backend (B2). O guidance aceita campos extras quando o schema não os proíbe (B2). disable_any_whitespace evita o JSON que vira espaço infinito (B1, B3). enable_in_reasoning=true travaria também o raciocínio (B1).
- **O chat template é o original do Qwen3.8 ou um customizado? Há defaults de servidor como preserve_thinking ou enable_thinking?** — O template muda o formato das chamadas de ferramenta, o tratamento do histórico com raciocínio (B7) e a aceitação de valores como reasoning_effort=high.
- **Quantas réplicas do vLLM atendem o qwen-latest e como o LiteLLM balanceia entre elas?** — O cache de gramática compilada e o cache de prefixo são por réplica. Com várias réplicas, a latência de compilação (B2, B4) e o cached_tokens do reparo (B12) só podem ser comparados dentro da mesma réplica (x-litellm-model-id).
- **Os limites da nossa virtual key aparecem em x-ratelimit-*. Eles contam os tokens de raciocínio? Podemos ter uma janela combinada para cerca de 2.100 chamadas do Bloco B?** — As repetições de B3, B5, B7 e B11 dependem disso. Muitos 429 distorcem a latência e alongam a bateria.
- **O que representa o valor do cabeçalho x-litellm-response-cost (tabela interna de preço)? Ele serve para o orçamento por estudo?** — B11 e B12 decidem perfil e política de reparo comparando custo. Se o número não reflete custo real, usaremos tokens como medida.

### Resultado → decisão no desenho

| Resultado | Decisão no desenho |
| --- | --- |
| B1: a trava de JSON se prova no servidor sem e com raciocínio (os controles negativos (e) e (f) continuam JSON), e o raciocínio fica fora do content em off, low e medium. | Todos os momentos com JSON usam response_format direto. O raciocínio é lido de reasoning_content ou reasoning e nunca entra no parser. A validação em Python continua obrigatória. |
| B1: a trava não chega (o controle negativo (e) devolve markdown). | O JSON passa a vir por ferramenta nomeada (B5), com validação em Python, até a CCOE corrigir o repasse. |
| B1: a trava funciona sem raciocínio, mas cai com raciocínio (o (e) passa e o (f) falha). | Chamadas JSON só sem raciocínio. Momentos que precisam pensar viram 2 chamadas (raciocínio em texto livre, depois extração JSON sem raciocínio), até a CCOE acertar o parser de raciocínio. |
| B1/B10: xhigh ou medium estouram o teto de 8.192 (length ou content vazio). | Plano, caça de riscos e crítico viram 2 chamadas: raciocínio em texto livre com thinking_token_budget, depois extração JSON sem raciocínio. Os tetos de 12K/16K saem do desenho até a CCOE subir o limite. |
| B2: os schemas Pydantic (com $defs/$ref e anyOf) são aceitos e respeitados. | Os schemas da biblioteca nascem de modelos Pydantic e vão direto, sempre com additionalProperties:false explícito. Se não forem aceitos, entra a função achatar_schema antes do envio, e as regras não travadas vão para o validador. |
| B3: a presence_penalty 1,5 piora o acerto de campos em JSON sem raciocínio. | Chamadas JSON sem raciocínio usam presence 0, como exceção à amostragem oficial. O perfil da extração para a carta é o braço vencedor do B3. |
| B4: o enum de colunas compila rápido (até 2 s com 2.000 valores), zera os nomes inexistentes e não aumenta o vizinho errado. | A descoberta de dados devolve colunas travadas por enum gerado do catálogo. Se o enum for lento ou aumentar o vizinho errado, a escolha vira 2 etapas (tabela, depois coluna) ou usa IDs curtos traduzidos pelo Python, e o Python avisa o dev sobre colunas vizinhas parecidas. |
| B5: o strict das ferramentas não chega ao vLLM (S1 com tantas violações quanto S0). | Os argumentos são sempre validados em Python, com reparo (B12). Pedido à CCOE: cadastro openai/ ou --tool-strict-level parameter. |
| B5: o modo auto erra a decisão de chamar ou não chamar. | Ação obrigatória vai sempre com tool_choice required ou nomeada. O auto fica só para conversa, ou entra o passo prévio 'precisa de ferramenta?' em JSON com enum. |
| B5: o finish_reason da nomeada vem 'stop' (ou varia). | Independentemente do resultado, o laço decide pela presença de tool_calls, nunca pelo finish_reason. |
| B5: com stream, as chamadas divergem, vêm como texto cru ou dão 500. | Ferramentas e JSON sempre sem stream. O stream fica só para a narrativa em texto. |
| B6: o modelo faz as chamadas independentes em lote, com ids únicos. | O executor da biblioteca roda em paralelo as chamadas do mesmo turno e devolve os resultados com seus tool_call_id. Se não houver lote, laço serial. |
| B7: os episódios fecham em até N voltas; o raciocínio devolvido chega ao modelo (prompt_tokens cresce) e muda o acerto, ou não. | Teto de voltas igual ao máximo observado mais margem, com handoff ao dev. O histórico usa o formato vencedor; se o raciocínio não chegar, ele não é devolvido. Detector de chamada repetida responde com o resultado já guardado. |
| B8: aparece mistura de idioma depois de resultado em inglês. | Prompt de sistema fixo de idioma, resultados das ferramentas embrulhados ou normalizados em português e checagem de idioma depois da resposta, com 1 reparo. |
| B9: o tamanho seguro de argumento é X tokens. | O limite de argumento na biblioteca é X/2. Acima disso, o código sai no content em bloco de código, ou por referência, ou dentro de um response_format JSON. |
| B10: turnos vazios, chamadas perdidas ou marcação crua acima do limite; ou o thinking_token_budget não é aplicado. | Detector obrigatório de turno vazio, com 1 recuperação via tool_choice=required. Os momentos de ação ficam limitados a low, ou a medium com thinking_token_budget comprovado. |
| B11: o perfil mais barato que atende o critério de acerto é off, low ou medium. | O perfil do momento 'escolha de ferramenta/bloco' fica fixado no vencedor, e o rascunho de perfis é atualizado. |
| B12: a taxa de conserto em 1 tentativa e o custo relativo do reparo. | Política de reparo: no máximo 1 tentativa automática, com o formato de mensagem vencedor (erro como 'tool' em ferramentas, como 'user' em JSON). Abaixo de 80% de conserto, prevenir com enum e listas fechadas e devolver a dúvida ao dev. |

### Incertezas

- Não sabemos a versão do vLLM do hub. A do LiteLLM leremos no cabeçalho x-litellm-version. O que está acima sobre parser, strict, troca de backend, campos de uso e campo de raciocínio vem do vLLM tag v0.32.0 e do LiteLLM main, lidos em 19/10/2026. Versões antigas, sobretudo vLLM anterior à v0.24.0, podem se comportar diferente.
- Não está confirmado se o qwen-latest está cadastrado como hosted_vllm/ no LiteLLM. A remoção de strict e de additionalProperties:false das tools só acontece nesse prefixo. O nome 'openai/gpt-oss-20b' não prova nada sobre o cadastro. Não sabemos se /model/info está liberado para a nossa virtual key.
- Não sabemos o valor de VLLM_ENFORCE_STRICT_TOOL_CALLING nem de --tool-strict-level no hub. Eles mudam o caminho de required e nomeada (structural tags com moldura travada, ou JSON derivado do schema com argumentos travados) e mudam a leitura do B5 e do B9. O bug #49216 foi visto só com a variável desligada e com stream.
- Qual campo de raciocínio chega ao cliente: o vLLM atual usa 'reasoning' e o LiteLLM normaliza para 'reasoning_content'. Se nenhum vier, o LiteLLM tenta extrair tags <think> do content, o que pode esconder um vazamento. O teste de 16/10 não registrou qual campo veio; a biblioteca e os testes leem os dois.
- As docs do LiteLLM dizem que parâmetros fora do padrão OpenAI seguem no corpo, mas não citam top_k, min_p, thinking_token_budget nem chat_template_kwargs pelo nome. Só chat_template_kwargs.enable_thinking foi visto funcionando (16/10). drop_params ou additional_drop_params podem remover parâmetros sem erro.
- Como o template do Qwen3.8 usa o raciocínio devolvido no histórico não está documentado. O vLLM renomeia reasoning_content para reasoning na entrada, e o hosted_vllm só repassa reasoning_content se for texto. O B7 mede pelos prompt_tokens.
- O cartão oficial do Qwen3.8 não descreve o formato de chamada nem recomenda parser. A doc do parser qwen3 do vLLM descreve o XML <tool_call><function=...><parameter=...>; supomos o mesmo formato.
- A issue #39056 foi reproduzida com Qwen3.5 no vLLM 0.19 e continua aberta. O parser novo trata <tool_call> como fim implícito do raciocínio, o que pode resolver o sintoma. Mas isso pode também executar uma chamada que o modelo só estava cogitando, e esse caso não tem como ser detectado com segurança pelo cliente.
- Não confirmamos se o vLLM devolve finish_reason='stop' ou 'tool_calls' na escolha nomeada. A API da OpenAI devolve 'stop'. Por isso nada no desenho depende desse campo.
- No vLLM v0.32.0, completion_tokens_details.reasoning_tokens vale 0 por padrão quando o objeto vem. Não sabemos se o hub preenche esse campo nem se o LiteLLM o recalcula com outro tokenizador. Só usamos o campo quando ele for coerente com o texto do raciocínio.
- Combinar response_format e tools na mesma chamada não está documentado. O vLLM recusa combinar structured_outputs (json, regex, choice) com tool_choice nomeada; response_format com tools não é recusado no protocolo, mas o comportamento não é descrito. A biblioteca vai evitar misturar.
- Campos extras sem additionalProperties no schema: pela config do vLLM, xgrammar e outlines recusam e o guidance aceita (disable_additional_properties só existe para o guidance). Não verificamos no hub; o B2 mede.
- O LiteLLM pode retentar sozinho (num_retries) e distorcer a latência; o cabeçalho x-litellm-attempted-retries mostra quando isso acontece. Em stream, o custo pode não vir no cabeçalho.
- Os números dos critérios (95%, 2 s, 80%, 1,5×, 2K tokens, 2% de vizinho errado) são metas do desenho, não medições. Devem ser recalibrados depois da primeira rodada.
- O detector de idioma (langdetect) erra em textos curtos, por isso a detecção é por frase, com seed fixa e com amostra humana de 10% no B8.
- As dependências de outros blocos (A, raciocínio, amostragem) foram citadas por descrição, porque não conhecemos os ids finais desses testes.
- O openai/gpt-oss-20b ficou fora do Bloco B (8.192 de contexto). Se ele virar plano B para chamadas de ferramenta, rodar B5 e B11 nele também.
- O speculative decoding (MTP), se estiver ligado no hub, tem caminho próprio no vLLM para aplicar a gramática depois do raciocínio. Não sabemos se afeta a taxa de JSON válido; a pergunta 1 à CCOE cobre isso.

### Fontes consultadas

- [vLLM — Structured Outputs (doc oficial; aviso sobre saída estruturada desligada quando o raciocínio não é separado)](https://docs.vllm.ai/en/latest/features/structured_outputs.html)
- [vLLM — Tool Calling (doc oficial: structural tags, strict, --tool-strict-level, VLLM_ENFORCE_STRICT_TOOL_CALLING, modos de tool_choice)](https://docs.vllm.ai/en/latest/features/tool_calling.html)
- [vLLM — Tool Calling, fonte da doc na v0.32.0](https://github.com/vllm-project/vllm/blob/v0.32.0/docs/features/tool_calling.md)
- [vLLM — Reasoning Outputs (campo reasoning, thinking_token_budget com --reasoning-config, chamadas só lidas do content)](https://docs.vllm.ai/en/latest/features/reasoning_outputs.html)
- [vLLM — parser Qwen3 (formato XML, <tool_call> como fim implícito do raciocínio)](https://docs.vllm.ai/en/latest/api/vllm/parser/qwen3/)
- [vLLM v0.32.0 — StructuredOutputsConfig (backend, disable_any_whitespace, disable_additional_properties, enable_in_reasoning)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/config/structured_outputs.py)
- [vLLM — Engine Arguments](https://docs.vllm.ai/en/latest/configuration/engine_args.html)
- [vLLM v0.32.0 — registro de tool parsers (qwen3_coder e qwen3_xml → Qwen3EngineToolParser)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/tool_parsers/__init__.py)
- [vLLM v0.32.0 — Qwen3EngineToolParser (structural_tag_model qwen_3_coder)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/tool_parsers/qwen3_engine_tool_parser.py)
- [vLLM v0.32.0 — response_format json_schema (campo strict aceito, sem uso)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/entrypoints/generate/base/protocol.py)
- [vLLM v0.32.0 — protocolo de chat (parallel_tool_calls padrão true, reasoning_effort, thinking_token_budget, campo reasoning)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/entrypoints/openai/chat_completion/protocol.py)
- [vLLM v0.32.0 — UsageInfo (cached_tokens, reasoning_tokens com padrão 0)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/entrypoints/serve/engine/protocol.py)
- [vLLM v0.32.0 — backend auto: xgrammar → guidance (outlines só em casos específicos)](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/sampling_params.py)
- [vLLM v0.32.0 — recursos de JSON Schema não suportados pelo xgrammar](https://github.com/vllm-project/vllm/blob/v0.32.0/vllm/v1/structured_output/backend_xgrammar.py)
- [vLLM v0.24.0 — notas da versão (motor novo de parser, strict em Chat Completions)](https://github.com/vllm-project/vllm/releases/tag/v0.24.0)
- [vLLM PR #45413 — novo motor de parser e novo parser Qwen3](https://github.com/vllm-project/vllm/pull/45413)
- [vLLM issue #39056 — chamada de ferramenta perdida quando escrita dentro do raciocínio (aberta)](https://github.com/vllm-project/vllm/issues/39056)
- [vLLM issue #49216 — erro 500 em required com stream quando VLLM_ENFORCE_STRICT_TOOL_CALLING=false (aberta)](https://github.com/vllm-project/vllm/issues/49216)
- [LiteLLM — provedor vLLM (prefixo hosted_vllm/)](https://docs.litellm.ai/docs/providers/vllm)
- [LiteLLM — JSON mode / Structured Outputs (enable_json_schema_validation)](https://docs.litellm.ai/docs/completion/json_mode)
- [LiteLLM — drop_params, additional_drop_params e allowed_openai_params](https://docs.litellm.ai/docs/completion/drop_params)
- [LiteLLM — parâmetros específicos de provedor (repassados no corpo)](https://docs.litellm.ai/docs/completion/provider_specific_params)
- [LiteLLM — reasoning_content padronizado](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM — cabeçalhos de resposta do proxy (x-litellm-response-cost, x-litellm-call-id, x-litellm-version, x-litellm-model-id, x-ratelimit-*)](https://docs.litellm.ai/docs/proxy/response_headers)
- [LiteLLM — gestão de modelos (/model/info)](https://docs.litellm.ai/docs/proxy/model_management)
- [LiteLLM — transformação hosted_vllm (remove strict e additionalProperties das tools; reasoning_content só se for texto)](https://github.com/BerriAI/litellm/blob/main/litellm/llms/hosted_vllm/chat/transformation.py)
- [LiteLLM — utils.py (remove_strict_from_schema e remove_additional_properties, recursivas)](https://github.com/BerriAI/litellm/blob/main/litellm/utils.py)
- [LiteLLM — extract_reasoning_content (lê reasoning_content, depois reasoning, depois tags <think> no content)](https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/prompt_templates/common_utils.py)
- [Fórum da OpenAI — escolha nomeada devolve finish_reason 'stop'](https://community.openai.com/t/function-call-with-finish-reason-of-stop/437226)
- [Qwen3.8-27B — cartão oficial do modelo (reasoning_effort, preserve_thinking, amostragem, presence_penalty e mistura de idiomas)](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Qwen — implantação com vLLM (doc oficial Qwen)](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)
- [OCI Data Science — sessões de notebook e resource principal](https://docs.oracle.com/en-us/iaas/Content/data-science/using/use-notebook-sessions.htm)
- [OCI ADS — autenticação (resource principal)](https://accelerated-data-science.readthedocs.io/en/latest/user_guide/configuration/authentication.html)
- [Oracle — exemplo de leitura de segredo do Vault com resource principal e base64](https://docs.oracle.com/en/industries/health/lifesciences-ai-data-platform/haiug/authenticate-adw-using-resource-principal-authentication-recommended.html)
- [Template comunitário para Qwen3.8 no vLLM (NÃO oficial; só contexto sobre qwen3_xml, <think> já aberto no prompt e reasoning_effort=high)](https://huggingface.co/nrrso/Qwen3-Chat-Template-vLLM-Fixes)

### O que a revisão adversarial corrigiu (27)

- **[alta] B1** — Só havia controle negativo sem raciocínio. A doc do vLLM avisa que a saída estruturada pode ser desligada sem aviso quando o raciocínio não é separado pelo parser, e o motor usa o fim do raciocínio para decidir quando a trava começa. Com isso, os braços com raciocínio podiam passar porque o modelo obedeceu ao pedido, sem trava nenhuma do servidor. O teste também não conferia se o raciocínio estava mesmo ligado: se a trava começar no 1º token, o raciocínio some e o braço 'low' na prática roda sem raciocínio. *Correção:* Novo braço (f): controle negativo com raciocínio low. Critério novo: raciocínio não vazio em 10/10 nos braços (b), (c) e (f), e vazio em (a) e (e). O braço extra com strict:false virou duas variantes do próprio controle negativo (strict:true e sem strict).
- **[alta] B5** — A prova do strict usava só o modo nomeado. Na v0.32.0, se a CCOE tiver posto VLLM_ENFORCE_STRICT_TOOL_CALLING=false, os modos nomeado e required vão por um caminho de JSON derivado do schema, que trava os argumentos com ou sem strict. Nesse caso os dois braços dão zero violação, ficam 'iguais' e o teste concluiria errado que o strict não chega. Também faltava exigir que a linha de base viole: sem violação sem strict, o teste não prova nada. *Correção:* A prova do strict passou para o modo auto, o único em que só o strict liga a trava dos argumentos. Braços S0/S1 (auto) e N0/N1 (nomeada), com pré-condição de pelo menos 3 violações em 10 no S0 e uma tabela de leitura dos resultados. A provocação passa a incluir campo extra, para pegar a remoção de additionalProperties:false pelo hosted_vllm.
- **[alta] B5** — O critério exigia finish_reason='tool_calls'. Na API da OpenAI, a escolha nomeada devolve finish_reason='stop' de propósito, e o vLLM pode copiar isso. Um laço que decide pelo finish_reason erraria. Não confirmamos o comportamento do vLLM. *Correção:* finish_reason passa a ser só registro. A preparação e a biblioteca decidem pela presença de tool_calls.
- **[alta] B5 / B9 (lacuna)** — Faltava testar a chamada de ferramenta com stream=true no B5. O parser de streaming é outro código e tem bugs abertos: #49216 (erro 500 com required + stream quando o modo estrito está desligado, reproduzido na v0.25.1 com Qwen3.6) e um relato em #39056 de chamada chegando como texto cru em delta.content, com finish_reason=stop. *Correção:* Braço com stream no B5 (auto e required): montar tool_calls a partir dos deltas e comparar com o resultado sem stream. Nova decisão no desenho: JSON e ferramentas sem stream até esse braço passar.
- **[alta] Preparação (segurança)** — No curl, -H "Authorization: Bearer $ADEX_LLM_KEY" coloca a chave na linha de comando do processo, e ela aparece em ps e /proc em máquina compartilhada. No Jupyter, '%env ADEX_LLM_KEY=...' ou uma atribuição literal numa célula ficam gravados no .ipynb. Corpos de erro 401 do LiteLLM podem trazer parte mascarada da chave e o hash dela. O conteúdo do OCI Vault vem em base64, o que não estava dito. *Correção:* A chave passa a ir pelo stdin (printf é comando interno do bash; curl -H @-). Ficam proibidos %env com valor, chave literal em célula, curl -v e set -x. No local, usar getpass. Os corpos de erro passam por redação de 'sk-...' antes de ir ao log. O exemplo do Vault agora decodifica o base64 e cita a política do grupo dinâmico. Também registrado: o catálogo inventado não pode copiar nomes nem descrições do catálogo real do ADEX, porque metadado corporativo também é dado corporativo.
- **[alta] B4** — A troca silenciosa só era medida como nome inexistente. Com o enum, nome inexistente fica impossível, então o critério '(b) 0 inexistentes' só confirma que a trava funciona. O erro perigoso que o próprio teste cita, pegar uma coluna parecida, existente e errada, não era medido em nenhum braço. O enum pode até piorar esse erro, porque força o modelo a escolher um nome real. *Correção:* Nova classe 'vizinho errado': o nome existe, não está no gabarito e fica a distância de edição de até 2 de um nome do gabarito. O enum só é adotado se a taxa de vizinho errado não for maior que a do texto livre e o recall chegar a 90% ou mais. Meta de no máximo 2% de vizinho errado no braço escolhido.
- **[media] Pesquisa (2) e B2** — A ordem de fallback estava errada: no auto, o vLLM v0.32.0 vai do xgrammar para o guidance, e o outlines só entra quando o guidance não suporta o schema ou o tokenizador. A lista de recursos fora do xgrammar estava incompleta (faltavam contains/minContains/maxContains, allOf com 2 ou mais itens, format junto com limite de tamanho, combinações de propertyNames/patternProperties e format fora da lista suportada). Faltava dizer que format date é suportado. *Correção:* Texto corrigido com a lista conferida no backend_xgrammar.py da v0.32.0. O B2 ganha allOf com 2 itens entre os recursos de fallback.
- **[media] B2 (lacuna)** — O comportamento padrão sem additionalProperties não era testado. Pela config da v0.32.0, xgrammar e outlines recusam campos extras e o guidance aceita (disable_additional_properties só existe para o guidance). Isso importa porque o hosted_vllm tira additionalProperties:false das tools e porque um schema que cai no guidance muda de regra sem aviso. *Correção:* Novo passo no B2: o mesmo objeto sem additionalProperties, com provocação de campo extra, numa versão que fica no xgrammar e noutra que força o guidance. Regra nova: a biblioteca sempre escreve additionalProperties:false e o validador Python recusa campo extra.
- **[media] B2 / B4** — A latência de compilação da gramática (1ª chamada contra 2ª) era comparada sem controlar a réplica. Se houver mais de uma réplica do vLLM atrás do LiteLLM, cada uma tem seu próprio cache, e a 2ª chamada pode cair numa réplica fria. *Correção:* Registrar x-litellm-model-id, comparar só chamadas da mesma réplica e usar a mediana de 3 repetições. Nova pergunta à CCOE sobre réplicas e balanceamento.
- **[media] Pesquisa (3) e (5)** — Faltavam nuances que mudam a leitura do B5, do B9 e do B10. O padrão de --tool-strict-level para o parser do Qwen é auto, e no modo auto a trava só liga se alguma ferramenta tiver strict:true. A variável VLLM_ENFORCE_STRICT_TOOL_CALLING muda o caminho das escolhas required e nomeada. O parser novo do Qwen3 trata <tool_call> como fim implícito do raciocínio, o que pode resolver o sintoma do #39056 mas também pode executar uma chamada que o modelo só estava cogitando. *Correção:* Itens (3) e (5) reescritos. Pergunta à CCOE inclui a variável de ambiente. O B10 registra esse risco.
- **[media] B7** — O formato (ii), com o raciocínio anterior devolvido, não provava que o raciocínio chegava ao modelo. O hosted_vllm só repassa reasoning_content se for texto, o vLLM renomeia reasoning_content para reasoning na entrada e o template pode ignorar o campo. Assim o formato (ii) podia ficar 'igual' ao (i) porque o campo foi descartado no caminho. Só uma chave era testada. *Correção:* Formatos (ii-a) reasoning_content e (ii-b) reasoning. Prova de uso: o mesmo histórico em (i) e (ii); se prompt_tokens não crescer, o raciocínio foi descartado. O formato (ii) só é adotado com essa prova.
- **[media] B12** — No reparo de chamada de ferramenta, a mensagem do validador entrava como user logo depois de um assistant com tool_calls. Pela regra de conversa do formato OpenAI, cada tool_call precisa de uma resposta role 'tool'. Sem ela, a chamada pode dar 400 ou o template pode se comportar de forma estranha. *Correção:* Em ferramentas, o erro volta como mensagem role 'tool' com o tool_call_id. Em JSON de response_format, o erro continua como user. Os dois exemplos estão no teste.
- **[media] B6** — O braço parallel_tool_calls=false não separava 'o servidor aplicou' de 'o modelo já não fazia lote'. Se o braço padrão quase não faz lote, 1 chamada por turno no braço false não prova nada. *Correção:* Pré-condição: o braço padrão precisa fazer lote em pelo menos 50% dos turnos, senão o resultado é inconclusivo. Se o false tiver tanto lote quanto o padrão, perguntar à CCOE pelo x-litellm-call-id.
- **[media] B9** — O teste usava só a escolha nomeada. O laço real usa auto. Conforme a configuração do servidor, a nomeada pode ir pelo caminho de JSON derivado do schema e nem passar pelo formato XML, e aí o teste do texto </parameter> não exercitaria o parser. O tamanho em tokens era estimado. *Correção:* Novo braço auto. O tamanho passa a ser medido pelo usage.completion_tokens da própria resposta. 'Não chamou' é contado à parte.
- **[media] Preparação (campos de uso e custo)** — No vLLM v0.32.0, completion_tokens_details.reasoning_tokens tem valor padrão 0 quando o objeto vem, então 0 não prova que não houve raciocínio. cached_tokens exige --enable-prompt-tokens-details. Em stream, o custo pode não vir no cabeçalho, e o usage só vem com stream_options.include_usage. O próprio LiteLLM pode retentar e distorcer a latência. *Correção:* reasoning_tokens só vale se for coerente com o texto do raciocínio. Stream usa stream_options={'include_usage': True}. O log ganha x-litellm-attempted-retries, x-litellm-model-id e x-litellm-response-duration-ms. Em 429, respeitar Retry-After.
- **[media] Perguntas à CCOE** — Algumas perguntas podem ser respondidas por nós mesmos. A versão do LiteLLM vem no cabeçalho x-litellm-version, os limites da chave vêm em x-ratelimit-*, e o prefixo do modelo pode aparecer em /model/info. Faltavam perguntas sobre VLLM_ENFORCE_STRICT_TOOL_CALLING, --reasoning-config, --enable-prompt-tokens-details, additional_drop_params (a própria doc do LiteLLM usa response_format em vLLM como exemplo de parâmetro descartado), réplicas e o log necessário para recuperar o corpo pelo call-id. *Correção:* Nova etapa de 'descoberta passiva' na preparação. Perguntas reescritas: 10 no total, com o que só a CCOE pode responder ou mudar.
- **[media] B1 / B3 (lacuna)** — Faltava detectar o 'JSON que vira espaço infinito'. Com espaço em branco liberado (o padrão, any_whitespace), o modelo pode encher o JSON de espaços ou quebras de linha até bater no teto. Isso aparece como length e seria confundido com falta de teto. *Correção:* Nova classe nas métricas do B1 e do B3: length com content só de espaços ou quebras. No plano B, pedir disable_any_whitespace à CCOE.
- **[media] B8** — A dependência de B7 estava errada: os históricos são fixos e não precisam do laço. O critério de 98% com 20 chamadas na prática é 20/20. langdetect não é determinístico sem seed e não mede mistura de idioma se rodar no texto inteiro. *Correção:* B8 depende só de B5 e entra no lote paralelo. Critério escrito como 20/20. langdetect com DetectorFactory.seed=0, detecção por frase, tirando código, nomes de coluna e o trecho citado do erro.
- **[media] B7 / B8 / B11 (exemplos)** — Os exemplos não fixavam a amostragem de cada perfil e ficavam com o padrão do servidor (top_k 0, ou seja, desligado). A falta de top_k=20 já foi ligada a loops. Braços diferentes rodavam com amostragens diferentes sem querer. *Correção:* A amostragem oficial de cada perfil fica explícita nos exemplos (PERFIS). Regra na preparação: não mandar reasoning_effort junto com enable_thinking, porque o vLLM deriva enable_thinking do reasoning_effort.
- **[media] B10** — Faltava a classe 'marcação crua no content': tool_calls vazio e '<tool_call>' ou '<function=' no texto, o sintoma do parser de streaming relatado em #39056. Faltava também o risco inverso do parser novo, que pode executar uma chamada escrita no meio do raciocínio. *Correção:* Classe nova na classificação dos turnos. O risco inverso está no por_que_importa e nas incertezas.
- **[baixa] Pesquisa (8) / B1 / B10** — thinking_token_budget só funciona com --reasoning-parser e --reasoning-config, ou se o vLLM conseguir deduzir as marcas do parser. Isso não estava dito. *Correção:* Texto e pergunta à CCOE incluem --reasoning-config. O teste dirigido do B10 (com e sem orçamento) passa a ser a prova de que o orçamento é aplicado.
- **[baixa] B1** — p50/p95 com 10 amostras: o p95 vira o máximo. *Correção:* Trocado por mediana e máximo.
- **[baixa] B3** — A regra de 3 (0 falhas em 50 dá limite superior de ~6%) supõe chamadas independentes. As 5 repetições da mesma transcrição não são independentes. *Correção:* Nota de que o limite é otimista. A validação em Python continua obrigatória.
- **[baixa] Pesquisa (4)** — A remove_strict_from_schema do LiteLLM apaga qualquer chave chamada 'strict', de forma recursiva, inclusive um parâmetro de ferramenta com esse nome. Ela roda sempre que há tools no prefixo hosted_vllm. *Correção:* Registrado no texto. Regra: nenhum parâmetro de ferramenta se chama 'strict'.
- **[baixa] B2** — Com format date, a trava garante só a forma AAAA-MM-DD, não que a data exista no calendário (2025-02-30 passaria). *Correção:* Passo novo: conferir em Python se a data existe.
- **[baixa] B1 (lacuna)** — O LiteLLM pode transformar response_format em outra coisa ou descartá-lo (additional_drop_params). Se nenhum campo de raciocínio vier, ele tenta extrair tags <think> do content, o que pode esconder um vazamento de raciocínio. *Correção:* O B1 registra 'content vazio com tool_calls' e confere '</think>' no content (o template do Qwen já abre o <think> no prompt, então o vazamento aparece só com a tag de fechamento). Isso entrou também nas incertezas.
- **[baixa] Ordem e volume** — A ordem colocava o B8 no fim sem necessidade. O volume não contava os braços novos. *Correção:* Nova ordem: descoberta passiva, B1, B2, B5, depois B3/B4/B6/B8/B9 em paralelo, depois B7, B10, B11 e B12. Volume revisto para cerca de 2.100 chamadas.

---

## Bloco C · Visão, contexto longo, cache de prefixo e desempenho

O Bloco C responde a quatro perguntas práticas sobre o qwen-latest no GPU Hub. (1) O hub enxerga imagem, e lê gráfico e tabela bem o bastante para o crítico? (2) Até que tamanho de prompt o modelo continua rápido e não perde informação? Isso decide se a descoberta de dados manda o catálogo inteiro (~131K tokens) numa chamada só ou passa antes por um funil em Python. (3) O cache de prefixo funciona de verdade neste modelo híbrido, e por quanto tempo? A resposta define o formato de todos os prompts. (4) Quanto o hub aguenta em paralelo, e o MTP e o KV em FP8 estão ligados?

São 12 testes (C1 a C12). O C12, que conta os tokens antes do envio, entrou nesta revisão. Todos usam só dados inventados com seed fixa, e o Python confere cada resposta contra um gabarito que ele mesmo gerou, sem avaliação manual. Os critérios de aprovação são METAS do copiloto, não medições: o gestor e o time precisam validá-las antes da bateria.

O que as docs oficiais mudaram no roteiro:
(a) A receita oficial do vLLM para o Qwen3.8-27B diz que cobre e verificou só o serviço de texto. Num exemplo da mesma receita, --language-model-only aumenta a memória de KV, o que dá ao CCOE um motivo para desligar a visão. Por isso o C1 vem primeiro.
(b) A mesma receita sobe o modelo com KV em FP8 (--kv-cache-dtype fp8) e oferece pesos quantizados (NVFP4). É provável que o hub siga algo parecido. Isso afeta a qualidade em contexto longo (C5, C6 e C11) e vira pergunta ao CCOE.
(c) No modelo híbrido, o cache guarda o estado do DeltaNet só no último token de cada passo do agendador, e só quando esse token cai num múltiplo do bloco. Por isso o reaproveitamento anda em degraus de G tokens. G é o tamanho do bloco (N) quando --max-num-batched-tokens fica entre N e 2N−1; acima disso, G é praticamente o tamanho do passo. O N aparece no log do vLLM ('Setting attention block size to N tokens'), e a doc do LMCache cita N = 784 para o Qwen3.8-27B em 1 GPU. Na doc atual, --mamba-cache-mode está obsoleto e o modo segue o --enable-prefix-caching. Em versões antigas, porém, era preciso ligar 'align', e o padrão 'none' deixava o híbrido sem cache nenhum. Tudo isso depende da versão.
(d) O campo usage.prompt_tokens_details.cached_tokens só aparece com --enable-prompt-tokens-details, e o LiteLLM tem um bug aberto (#22984) em que ignora esse campo no custo. Já o campo usage.completion_tokens_details.reasoning_tokens não está documentado no vLLM: quando não vier, conte os tokens pelo texto do raciocínio (tokenizador do C12).
(e) Em versões antigas, o KV em FP8 derrubou a recuperação de agulha a 128K de 91% para 13% (kernel FA3, em GPUs Hopper); com a correção, voltou a 89%. A correção deixa o prefill mais lento em cabeças de atenção maiores que 128, e este modelo usa 256. Em Blackwell com FlashInfer, a correção não é necessária.
(f) Parâmetro aceito não quer dizer parâmetro aplicado. O vLLM ignora campos que não conhece e só avisa no log do servidor. O LiteLLM repassa no corpo os parâmetros que não são da OpenAI (como top_k e chat_template_kwargs), mas, com drop_params ligado, descarta sem erro os parâmetros OpenAI que acha não suportados. Na API 0.16 do vLLM, o reasoning_effort no nível de cima aceitava só low, medium ou high, e nada garante que ele chegue ao template do Qwen: o card do HF mostra esse caminho, e a receita do vLLM usa chat_template_kwargs. Por isso todo teste registra uma 'prova de aplicação' (convenções abaixo).
(g) Há retentativas escondidas. O SDK openai repete sozinho os erros 429 e 5xx duas vezes. O LiteLLM também pode repetir e trocar de modelo (fallback) por dentro. Nos testes de carga, use max_retries=0 e leia os headers x-litellm-attempted-retries e x-litellm-attempted-fallbacks.

Convenções (valem para todos os testes):
- Chave: ADEX_LLM_BASE e ADEX_LLM_KEY vêm de variáveis de ambiente. No notebook OCI, leia a chave do OCI Vault direto para os.environ, sem imprimir. Nunca use %env, !echo nem a chave escrita numa célula. Exemplo:
sig = oci.auth.signers.get_resource_principals_signer()
b = oci.secrets.SecretsClient({}, signer=sig).get_secret_bundle(os.environ["ADEX_LLM_SECRET_OCID"]).data.secret_bundle_content.content
os.environ["ADEX_LLM_KEY"] = base64.b64decode(b).decode()
- curl: nunca use -v, --trace ou --trace-ascii, porque mostram o header Authorization. Também não escreva -H "Authorization: Bearer $ADEX_LLM_KEY": a chave expandida fica visível na lista de processos. Use esta função, em que a chave chega ao curl pela entrada padrão (printf é um comando interno do shell):
hub() { printf 'header = "Authorization: Bearer %s"\n' "$ADEX_LLM_KEY" | curl -sS -K - -H "Content-Type: application/json" "$@"; }
Para ver os headers da resposta, use -D cab.txt, que grava só os headers recebidos.
- Python: crie o cliente com openai.OpenAI(base_url=..., api_key=..., max_retries=0, timeout=900); o padrão do SDK é repetir 2 vezes e esperar 600 s. Para ler headers, chame c.chat.completions.with_raw_response.create(...) e use raw.headers e raw.parse(). Não ligue log DEBUG (OPENAI_LOG=debug, httpx) durante os testes. Todo texto de erro passa por redige() antes de ir para a tela ou para o CSV:
def redige(t):
    k = os.environ.get("ADEX_LLM_KEY", "")
    t = str(t).replace(k, "***") if k else str(t)
    return re.sub(r"sk-[\w-]{6,}", "sk-***", t)
- Amostragem: mande sempre explícita, porque o vLLM aplica o generation_config.json do modelo (perfil com raciocínio) quando o cliente não manda nada. Sem raciocínio: temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5. Com raciocínio: 1.0, 0.95, 20 e 0. top_k e chat_template_kwargs vão em extra_body (Python) ou direto no corpo (curl). reasoning_effort vai só dentro de chat_template_kwargs, com low, medium ou xhigh (nunca high).
- Prova de aplicação, em toda chamada: registre o tamanho do raciocínio (campos reasoning e reasoning_content), se o content traz '<think>', o finish_reason, os completion_tokens e, se vier, usage.completion_tokens_details.reasoning_tokens. Com thinking off, o raciocínio tem de vir vazio; com low, tem de vir e, na mediana, ser menor que com medium. Se não bater, o parâmetro se perdeu no caminho: pare e volte ao Bloco B.
- Sem fallback nem cache de resposta: registre o campo model e os headers x-litellm-model-group, x-litellm-attempted-fallbacks, x-litellm-attempted-retries e x-litellm-cache-key (este só aparece quando a resposta veio do cache do LiteLLM). Mande "cache": {"no-cache": true} (controle do LiteLLM) e um ID de execução diferente em toda requisição. Fallback ou cache-key invalidam a medição.
- Nonce: em teste de tempo frio, coloque um nonce na 1ª linha do prompt. Nos testes de cache (C6 e C7), o nonce é por rodada ou por par, de propósito.
- Dados: só inventados, gerados por script com seed fixa. Nenhum nome real de tabela, coluna, produto ou cliente, nem como inspiração.
- Resultados: em CSV, sem a chave e sem os headers da requisição.
- Rede × servidor: x-litellm-response-duration-ms é o tempo dentro do proxy, então o tempo no cliente menos o header ≈ o tempo de rede até o proxy. Isso só vale em chamada sem streaming: em streaming os headers saem antes do corpo, e a doc não diz o que o header mede.

Ordem sugerida: C1 → C3 → C2; C8 → C12 → C4 → C10; C7 → C5 → C6 → C11; C9 (em janela combinada com o CCOE). Esforço: P = algumas horas, M = 1 a 2 dias, G = 3 dias ou mais. Os testes de tempo rodam nos dois lugares: no notebook OCI, que é o caminho real, e na máquina local, para comparar. Avise o CCOE antes dos testes com prompts acima de 100K, porque cada prefill longo pesa no hub compartilhado. Pela nossa estimativa do roteiro, C4, C5, C6 e C11 somam na ordem de 20 milhões de tokens de entrada.


### C1 · A imagem entra no modelo?

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* Bloco A: chave e base URL funcionando (local e notebook OCI)

**Pergunta.** O qwen-latest aceita um PNG enviado dentro da mensagem, e o modelo realmente 'vê' a imagem?

**Por que importa.** Destrava o momento 'visão de PNG de gráfico' do crítico. A receita oficial do vLLM para o Qwen3.8-27B diz que cobre e verificou só o serviço de texto. Além disso, o hub pode ter sido subido com --language-model-only, que zera os limites de imagem para ganhar memória de KV. Sem este teste, não sabemos se o crítico visual existe.

**Como medir.**

1) Gere com matplotlib (dados inventados, seed fixa) um gráfico de barras de 640×480 (figsize 6.4×4.8, dpi 100) com um código aleatório de 5 caracteres no título, ex.: 'Estudo fictício K7-QX'. Sorteie um código novo a cada repetição, para que nem cache nem acaso expliquem o acerto.
2) Chamada A: a mesma pergunta, só em texto, sem imagem. Chamada B: a pergunta + um item image_url com data:image/png;base64,....
3) Use thinking off, a amostragem sem raciocínio explícita e max_tokens 300. Repita a chamada B 3 vezes, na máquina local e no notebook OCI.
4) Registre: status HTTP, texto do erro (redigido), usage.prompt_tokens de A e de B, a resposta, o campo model e o header x-litellm-attempted-fallbacks.
5) Calcule Δ = prompt_tokens(B) − prompt_tokens(A) e compare com a estimativa (largura/32)×(altura/32) = 300 tokens para 640×480, mais uns 2 tokens de início e fim de imagem. A conta é deduzida do preprocessor_config.json oficial (patch 16 com fusão 2×2); não é um número publicado.
6) Controle: a resposta da chamada A não pode trazer o código.

**Exemplo de requisição.**

```
import os, base64, uuid, openai
c = openai.OpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0, timeout=900)
b64 = base64.b64encode(open("grafico.png","rb").read()).decode()
raw = c.chat.completions.with_raw_response.create(model="qwen-latest", max_tokens=300,
  temperature=0.7, top_p=0.8, presence_penalty=1.5,
  extra_body={"top_k":20, "chat_template_kwargs":{"enable_thinking":False}, "cache":{"no-cache":True}},
  messages=[{"role":"user","content":[
    {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}},
    {"type":"text","text":f"[exec {uuid.uuid4().hex[:8]}] Qual código aparece no título do gráfico? Responda só o código."}]}])
r = raw.parse()
print(r.usage.prompt_tokens, r.choices[0].message.content, raw.headers.get("x-litellm-attempted-fallbacks"))
```

**Métrica.** Status HTTP; Δprompt_tokens comparado ao estimado; acerto exato do código do título (3 de 3); resposta de A sem o código; fallbacks.

**Critério de aprovação.** Aprovado se, nos dois ambientes:
- HTTP 200 nas 3 repetições;
- Δprompt_tokens entre 240 e 390 (80% a 130% de 300), prova de que a imagem chegou ao modelo e não foi descartada no caminho;
- o código aparece exato nas 3 respostas;
- a chamada A não traz o código;
- nenhuma chamada teve fallback.

**Se falhar.** Se vier 400 dizendo que o modelo não aceita imagem, ou que o limite é 0: pedir ao CCOE para conferir --language-model-only e --limit-mm-per-prompt. Se vier 200 com Δ≈0: o proxy descartou a imagem; abrir o caso com o CCOE. Enquanto isso, o crítico visual sai do desenho. No lugar dele entra um 'lint de gráfico' em Python, que lê o próprio objeto matplotlib (ax.get_ylim(), ax.get_yscale(), textos da legenda e dos eixos) e manda só esse resumo, em texto, ao LLM.


### C2 · Gráficos com defeitos plantados e tabelas renderizadas

*Quem:* nós · *Onde:* local · *Esforço:* M · *Depende de:* C1, C3 (tamanho de exportação), C12 (contagem dos tokens de raciocínio), Bloco B: json_schema funcionando junto com raciocínio (exige --reasoning-parser no hub) e reasoning_effort comprovadamente aplicado

**Pergunta.** Olhando o PNG, o modelo percebe um gráfico enganoso e lê uma tabela sem errar números?

**Por que importa.** Decide se a visão vira uma camada do crítico capaz de bloquear o handoff ou fica só como comentário consultivo. Também decide se o perfil do momento 'visão' fica em low ou precisa subir para medium. Não dá para confiar sem medir: terceiros mediram ~30% de alucinação no modelo (número de terceiros, não do hub).

**Como medir.**

1) Gere 16 gráficos inventados (seed fixa), no tamanho de exportação escolhido no C3 (ex.: 1280×960):
- 4 limpos;
- 4 com eixo Y truncado (barras que começam em 90 em vez de 0);
- 4 com rótulo trocado (a legenda diz 'Região Alfa' na série que, pela especificação, é 'Região Beta');
- 4 em escala log sem aviso (ax.set_yscale('log') sem 'log' no rótulo do eixo).
2) Junto da imagem, mande em texto a 'especificação' que o Python pretendia desenhar: séries, ordem e faixa de valores. É assim que o crítico do copiloto vai trabalhar.
3) Peça a resposta em JSON via json_schema: ok (verdadeiro ou falso) + uma lista de defeitos, cada um com tipo (eixo_truncado, rotulo_trocado, escala_log_sem_aviso ou outro) e uma evidência curta.
4) Gere também 6 tabelas renderizadas em PNG (matplotlib table, 8 linhas × 5 colunas, números no formato brasileiro, ex.: 1.234,56, com alguns negativos). Peça a transcrição de 10 células indicadas (linha, coluna), em JSON.
5) Rode cada imagem 3 vezes com reasoning_effort=low e a amostragem com raciocínio (temp 1.0, top_p 0.95, top_k 20, presence 0), com um ID de execução em cada chamada. Se o low ficar abaixo da meta, rode o conjunto INTEIRO de novo com medium, e não só os erros: repetir só os erros dá uma 2ª chance apenas a eles e deixa sem medir o alarme falso em medium.
6) Registre os tokens de raciocínio: usage.completion_tokens_details.reasoning_tokens, se vier; se não, conte o texto do raciocínio com o tokenizador do C12. A mediana em medium tem de ser maior que em low. Se for igual, o reasoning_effort não está sendo aplicado: pare e volte ao Bloco B.
7) finish_reason=length ou JSON inválido contam como erro.
8) O Python compara tudo com o gabarito. O resultado não depende da rede, então rodar local basta.

**Exemplo de requisição.**

```
hub "$ADEX_LLM_BASE/chat/completions" -d @c2.json
# c2.json (essencial):
{"model":"qwen-latest","max_tokens":2048,"temperature":1.0,"top_p":0.95,"top_k":20,"presence_penalty":0,
 "chat_template_kwargs":{"reasoning_effort":"low"},"cache":{"no-cache":true},
 "response_format":{"type":"json_schema","json_schema":{"name":"critica","schema":{"type":"object","required":["ok","defeitos"],"properties":{"ok":{"type":"boolean"},"defeitos":{"type":"array","items":{"type":"object","required":["tipo","evidencia"],"properties":{"tipo":{"enum":["eixo_truncado","rotulo_trocado","escala_log_sem_aviso","outro"]},"evidencia":{"type":"string"}}}}}}}},
 "messages":[{"role":"user","content":[
  {"type":"text","text":"[exec 7f3a] Especificação: barras; Região Alfa=42, Beta=37; eixo linear a partir de 0. Confira o gráfico."},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,<...>"}}]}]}
```

**Métrica.** Taxa de detecção por tipo de defeito; alarme falso nos gráficos limpos; % de células exatas; nº de células com valor que não existe na tabela; tokens de raciocínio (low × medium); nº de respostas cortadas (length) ou com JSON inválido.

**Critério de aprovação.** Aprovado (metas) se, no mesmo nível de esforço:
- cada tipo de defeito for detectado em ≥ 10 de 12 execuções (≥ 80%);
- o alarme falso nos gráficos limpos ocorrer em ≤ 1 de 12 execuções;
- ≥ 171 de 180 células vierem exatas (≥ 95%);
- nenhuma célula vier 'inventada' (com valor que não existe na tabela);
- nenhuma resposta vier cortada ou com JSON inválido.
São metas do copiloto, não medições.

**Se falhar.** Se passar só com medium: o perfil da visão sobe para medium. Se não passar nem assim: a visão fica só consultiva, como comentário no relatório do crítico, e nunca bloqueia. O bloqueio fica com o lint de gráfico em Python:
- truncamento: ylim[0] > 0 em gráfico de barras;
- escala: get_yscale() == 'log' sem 'log' no rótulo;
- rótulo: textos da legenda comparados com a especificação.
Tabelas nunca são lidas por visão para tirar números: eles já vêm dos marcadores do Python.


### C3 · Limite de tamanho e quantidade de imagens

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* P · *Depende de:* C1

**Pergunta.** Quantas imagens, e de que tamanho, cabem numa chamada? E quanto do contexto cada uma consome?

**Por que importa.** Define três coisas: o padrão de exportação do copiloto (tamanho e dpi do PNG); se o crítico pode comparar 'antes e depois' na mesma chamada; e quanto do orçamento de contexto as imagens gastam (o contador de tokens da biblioteca, C12, precisa saber disso).

**Como medir.**

1) Quantidade: mande 1, 2, 4, 8 e 16 imagens de 640×480 na mesma mensagem, cada uma com um código inventado diferente.
2) Resolução: o mesmo gráfico em 200×150, 640×480, 1280×960, 1920×1440, 4096×3072 e 6000×4500. O menor testa o piso do preprocessor (65.536 pixels ≈ 64 tokens). O maior passa do teto (16.777.216 pixels ≈ 16.384 tokens) e deve ser reduzido pelo próprio servidor.
3) Peso do arquivo, sem mudar os pixels: pegue o PNG de 640×480 e infle o arquivo com um bloco de texto (tEXt, via PIL PngInfo.add_text) até ~0,5, 2, 5, 10 e 20 MB. Ruído não serve: um PNG de 640×480 não passa de ~0,9 MB, e subir a resolução mistura bytes com pixels. O base64 ainda acrescenta ~33% sobre esses tamanhos.
4) Anote também o tamanho real, em bytes, dos PNGs que o padrão de exportação (1280×960, dpi 100) gera para os 16 gráficos do C2.
5) Use thinking off, a amostragem sem raciocínio, max_tokens 200 e a pergunta 'quantas imagens você recebeu e qual o código de cada uma?'.
6) Registre: status, texto do erro (redigido), prompt_tokens, tempo total, tamanho do corpo da requisição em bytes e a origem do erro (mensagem do vLLM, do LiteLLM ou página HTML do ingress).
7) Rode local e no notebook OCI: o caminho de rede pode ter um limite de corpo próprio (ex.: erro 413).

**Exemplo de requisição.**

```
import os, uuid, openai
c = openai.OpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0, timeout=900)
conteudo = [{"type":"image_url","image_url":{"url":u}} for u in data_urls]  # n imagens
conteudo.append({"type":"text","text":f"[exec {uuid.uuid4().hex[:8]}] Quantas imagens você recebeu? Liste o código de cada uma."})
try:
    r = c.chat.completions.create(model="qwen-latest", max_tokens=200, temperature=0.7, top_p=0.8, presence_penalty=1.5,
        extra_body={"top_k":20,"chat_template_kwargs":{"enable_thinking":False},"cache":{"no-cache":True}},
        messages=[{"role":"user","content":conteudo}])
    print(len(data_urls), r.usage.prompt_tokens, r.choices[0].message.content)
except openai.APIStatusError as e:
    print(len(data_urls), e.status_code, redige(e.message)[:200])
```

**Métrica.** Máximo de imagens aceitas; maior arquivo aceito; tokens por imagem em função da resolução (piso de ~64 e teto de ~16.384, se o hub não mudou o preprocessor); tipo e origem do erro acima do limite; acerto da contagem e dos códigos.

**Critério de aprovação.** Aprovado (metas) se:
- ≥ 2 imagens de 1280×960 couberem numa chamada (o 'antes e depois' do crítico); 4 é desejável;
- for aceito um PNG com pelo menos 2× o tamanho do maior arquivo que o padrão de exportação gerou no passo 4;
- os tokens por imagem ficarem a ±30% de largura×altura/1024 entre o piso e o teto;
- todo excesso de limite voltar como erro 4xx com mensagem clara (nunca 5xx nem tempo esgotado);
- a contagem e os códigos vierem certos até o maior número de imagens aceito.
Mínimo aceitável para seguir: 1 imagem de 1280×960 por chamada.

**Se falhar.** O copiloto exporta os gráficos num tamanho fixo abaixo do limite (ex.: 1024×768) e manda 1 imagem por chamada; a comparação 'antes e depois' vira 2 chamadas. Se aparecer 413 ou 5xx, pedir ao CCOE o limite de corpo do proxy e do ingress, e o valor de --limit-mm-per-prompt.


### C4 · Contexto longo em degraus: tempo e velocidade

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* Bloco A: chave e base URL funcionando, Bloco A: teto real de max_tokens, C8 (o TTFT é medido pelo stream), C12 (calibração do tamanho sem gastar o hub)

**Pergunta.** Quanto o hub demora para começar a responder, e quão rápido escreve, quando o prompt tem 8K, 32K, 64K, 128K e ~180K tokens?

**Por que importa.** Destrava a descoberta de dados com ~131K tokens de metadados numa chamada e o plano com um dossiê de ~38K. Se o tempo for aceitável e não houver queda nem timeout, a descoberta sai em 1 chamada; se não, entra o funil do Python. O teste também confirma a guarda prompt + max_tokens ≤ 196.608, que a biblioteca vai precisar vigiar.

**Como medir.**

1) Monte textos inventados (catálogo fictício + notas fictícias) e calibre o tamanho com o tokenizador do C12 até ficar a ±2% do alvo. Confirme cada degrau com uma única chamada real. Não calibre por tentativa e erro no hub, porque cada tentativa de 180K é um prefill inteiro no hub compartilhado.
2) Coloque um nonce aleatório na 1ª linha de cada prompt. O cache só reaproveita começos iguais, então o nonce garante cache frio.
3) Use stream=true, stream_options.include_usage=true, thinking off, max_tokens 256 e a amostragem sem raciocínio.
4) Faça 3 repetições por degrau, uma requisição por vez, em 2 horários (manhã e tarde), local e no notebook OCI.
5) Meça no cliente:
- TTFT = tempo até o 1º pedaço com conteúdo (o 1º pedaço só com 'role' não conta);
- velocidade = (completion_tokens − 1) ÷ (hora do último pedaço com conteúdo − hora do primeiro);
- tempo total.
Se o CCOE ligar --enable-per-request-metrics e o LiteLLM repassar o objeto, anote também, no último pedaço, metrics.queue_time_ms e metrics.time_to_first_token_ms. Este último conta a partir do agendamento, sem a fila.
6) Rede × servidor: em cada degrau, faça 1 chamada extra SEM streaming e compare o tempo do cliente com x-litellm-response-duration-ms.
7) Extras no degrau de ~180K:
(a) max_tokens 8192 deve ser aceito (180K + 8.192 < 196.608);
(b) um prompt que, somado ao max_tokens, passe de 196.608 deve voltar como erro 400 limpo;
(c) um prompt que sozinho passe de 196.608 também.
Anote se o erro veio do vLLM ou do LiteLLM (verificação prévia do proxy) e confira se x-litellm-attempted-fallbacks = 0: o proxy não pode desviar para outro modelo.

**Exemplo de requisição.**

```
import os, time, uuid, openai
c = openai.OpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0, timeout=900)
t0 = time.perf_counter(); t1 = tN = None; u = None
s = c.chat.completions.create(model="qwen-latest", stream=True, stream_options={"include_usage":True},
  max_tokens=256, temperature=0.7, top_p=0.8, presence_penalty=1.5,
  extra_body={"top_k":20,"chat_template_kwargs":{"enable_thinking":False},"cache":{"no-cache":True}},
  messages=[{"role":"user","content":f"[{uuid.uuid4()}]\n{TEXTO_128K}\nResuma em 3 frases."}])
for ch in s:
    if ch.choices and ch.choices[0].delta.content:
        tN = time.perf_counter(); t1 = t1 or tN
    if ch.usage: u = ch.usage
print("TTFT", t1-t0, "tok/s", (u.completion_tokens-1)/(tN-t1), "prompt", u.prompt_tokens)
```

**Métrica.** TTFT (mediana e pior caso), tok/s de escrita e tempo total por degrau; erros e timeouts por degrau; bytes do corpo da requisição; diferença entre o tempo do cliente e x-litellm-response-duration-ms (chamadas sem stream); fila e TTFT do servidor (se disponíveis); origem dos erros de estouro.

**Critério de aprovação.** Metas do copiloto (a validar com o gestor), ligadas aos momentos:
- nenhum erro ou timeout até ~180K;
- TTFT frio: a mediana das repetições fica dentro da meta, e nenhuma repetição passa do dobro dela. Metas: ≤ 5 s em 8K (entrevista, extração para a carta); ≤ 15 s em 32K e ≤ 30 s em 64K (plano com dossiê de ~38K); ≤ 60 s em 128K (descoberta, 1 vez por estudo); ≤ 120 s em ~180K (limite, só informativo para o desenho);
- escrita ≥ 20 tok/s em 8K e ≥ 12 tok/s em 128K (uma narrativa de ~600 tokens em menos de 1 minuto);
- max_tokens 8192 aceito a ~180K, e os estouros de 196.608 devolvidos como erro 400 claro, sem fallback.

**Se falhar.** Timeout ou 504 nos degraus altos: pedir ao CCOE os timeouts do LiteLLM e do ingress. Durante o prefill provavelmente nada chega ao cliente, então um timeout de ociosidade pode cortar a conexão. Erro vindo do LiteLLM antes do vLLM: pedir ao CCOE para alinhar o model_info do proxy ao max_model_len real. TTFT acima da meta: usar o cache (C7) com uma chamada de 'aquecimento', ou reduzir o que vai ao modelo com o funil do Python. Escrita lenta: narrativas mais curtas e streaming, para o dev ver o progresso.


### C5 · Agulha no palheiro em cada degrau

*Quem:* nós · *Onde:* local · *Esforço:* M · *Depende de:* C4, C12

**Pergunta.** Em prompts longos, o modelo ainda acha um fato escondido no começo, no meio e no fim?

**Por que importa.** Define o 'contexto útil' do copiloto: o maior tamanho de prompt em que dá para confiar que nada se perde. Até esse tamanho, a descoberta pode ir em uma chamada; acima dele, entra o funil. Vira um teto duro na biblioteca.

**Como medir.**

1) Reaproveite os textos dos degraus do C4 (8K, 32K, 64K, 128K e ~180K).
2) Plante um fato inventado em 3 profundidades (10%, 50% e 90% do texto), um por chamada, ex.: 'O código de homologação do estudo fictício Aurora é TQX-5821.' Sorteie um código diferente em cada chamada e garanta que o texto de enchimento não tenha nenhum outro código no mesmo formato.
3) No fim do prompt, pergunte: 'Qual é o código de homologação do estudo Aurora? Responda só o código.'
4) Rode em dois modos: thinking off, com a amostragem sem raciocínio; e reasoning_effort=low (o perfil da descoberta), com a amostragem com raciocínio. Use max_tokens 2048, para o raciocínio não estourar, e registre o finish_reason e a prova de aplicação.
5) Faça 2 repetições: 5 degraus × 3 profundidades × 2 modos × 2 = 60 chamadas, ou 12 por degrau. O Python extrai da resposta o 1º trecho no formato AAA-9999 e compara com o gabarito. Anote o custo pelo header x-litellm-response-cost.
Esta é a versão 'fácil', porque a pergunta repete as palavras do fato. A versão difícil, com fatos parecidos, é o C11; a versão semântica é o C6.

**Exemplo de requisição.**

```
r = c.chat.completions.create(model="qwen-latest", max_tokens=2048, temperature=1.0, top_p=0.95, presence_penalty=0,
  extra_body={"top_k":20, "chat_template_kwargs":{"reasoning_effort":"low"}, "cache":{"no-cache":True}},
  messages=[{"role":"user","content": f"[{uuid.uuid4()}]\n" + palheiro_com_agulha +
    "\n\nQual é o código de homologação do estudo Aurora? Responda só o código."}])
m = re.search(r"[A-Z]{3}-\d{4}", r.choices[0].message.content or "")
print(bool(m) and m.group() == codigo, r.choices[0].finish_reason)
# c = cliente do C4 (max_retries=0, timeout=900)
```

**Métrica.** % de acerto exato por degrau e por profundidade; nº de respostas vazias com finish_reason=length; nº de códigos 'inventados' (formato certo, valor errado); tokens de raciocínio; custo.

**Critério de aprovação.** Aprovado se:
- 100% de acerto até 64K (36 de 36 chamadas);
- ≥ 11 de 12 em 128K (≥ 90%);
- ≥ 10 de 12 em ~180K (≥ 80%);
- nenhum código inventado até 128K;
- nenhuma resposta vazia por finish_reason=length.
O maior degrau com ≥ 90% de acerto vira o 'contexto útil' oficial do desenho. O C11 pode baixá-lo.

**Se falhar.** Limitar o contexto útil ao maior degrau aprovado. A descoberta passa a usar o funil: o Python pré-filtra os metadados (busca por palavras em nomes, descrições e colunas) e manda ao modelo só as ~200 tabelas mais prováveis. Outra saída é dividir o catálogo em pedaços e juntar as respostas (mapa-redução).


### C6 · Tabela certa num catálogo inventado de ~1.800 tabelas

*Quem:* nós · *Onde:* local · *Esforço:* G · *Depende de:* C4, C5, C7 (layout do prompt e efeito do cache), C12

**Pergunta.** Com um catálogo inteiro de ~1.800 tabelas inventadas no prompt (~131K tokens), o modelo aponta a tabela certa e nunca inventa nome?

**Por que importa.** É exatamente o momento 'descoberta de dados por metadados' (perfil low, com cache). Decide entre mandar o catálogo inteiro numa chamada ou passar antes pelo funil do Python.

**Como medir.**

1) Gere (seed fixa) um catálogo fictício: ~1.800 tabelas em ~12 esquemas inventados, cada uma com nome, descrição, granularidade, frequência de atualização e 5 a 15 colunas com tipo. Meça o tamanho com o tokenizador do C12 (a meta é ~131K).
2) Escreva 20 perguntas com gabarito, ex.: 'onde está o histórico diário de recargas do produto fictício Ômega por região?'. Para cada tabela-alvo, plante 3 distratores: uma versão antiga (_v1, marcada como descontinuada); a mesma tabela em granularidade mensal; e um nome parecido em outro esquema.
3) Varie a posição sem quebrar o cache: mantenha UMA ordem fixa do catálogo e espalhe as tabelas-alvo, 7 no primeiro terço, 6 no meio e 7 no último. Reordenar o catálogo entre perguntas mudaria o prefixo e anularia o cache.
4) Monte o prompt com instruções + catálogo primeiro (a parte estável, no layout aprovado no C7) e a pergunta no fim. Use reasoning_effort=low, a amostragem com raciocínio e json_schema pedindo o top-3, cada item com nome e motivo curto. Se o hub recusar o maxItems do schema, tire-o: o Python confere a quantidade.
5) Rode as 20 perguntas 3 vezes (60 chamadas), uma após a outra, com stream. Ponha o nonce só no início de cada rodada de 20: assim a 1ª chamada da rodada é fria e as outras pegam cache.
6) O Python valida: todos os nomes existem no catálogo? O alvo veio em 1º? O alvo está no top-3?
7) Meça o TTFT até o 1º pedaço de QUALQUER tipo (raciocínio ou conteúdo), porque com raciocínio ligado o 1º conteúdo só chega depois de o modelo pensar. Meça também o tempo total. Compare a 1ª chamada de cada rodada (fria) com as seguintes (quentes).

**Exemplo de requisição.**

```
hub -N "$ADEX_LLM_BASE/chat/completions" -d @c6.json
# c6.json (essencial):
{"model":"qwen-latest","stream":true,"stream_options":{"include_usage":true},"max_tokens":4096,
 "temperature":1.0,"top_p":0.95,"top_k":20,"presence_penalty":0,
 "chat_template_kwargs":{"reasoning_effort":"low"},"cache":{"no-cache":true},
 "response_format":{"type":"json_schema","json_schema":{"name":"top3","schema":{"type":"object","required":["candidatas"],"properties":{"candidatas":{"type":"array","maxItems":3,"items":{"type":"object","required":["tabela","motivo"],"properties":{"tabela":{"type":"string"},"motivo":{"type":"string"}}}}}}}},
 "messages":[{"role":"system","content":"[nonce-da-rodada]\nEscolha tabelas só deste catálogo fictício: <~131K tokens de metadados>"},
  {"role":"user","content":"Onde está o histórico diário de recargas do produto Ômega por região?"}]}
```

**Métrica.** Acerto no top-1 e no top-3, geral e por terço do catálogo; nº de nomes inexistentes; tokens de raciocínio; TTFT e tempo total, frio × quente.

**Critério de aprovação.** Aprovado (metas) se:
- top-3 ≥ 54 de 60 (90%);
- top-1 ≥ 45 de 60 (75%);
- zero nomes inexistentes nas 60 chamadas;
- em cada terço do catálogo, top-3 ≥ 85%: ≥ 18 de 21 no 1º e no último terço, e ≥ 16 de 18 no meio.

**Se falhar.** Funil: o Python ranqueia as tabelas por palavras (nome, descrição, colunas) e manda ao modelo só o top-200 (~15K tokens); o modelo escolhe entre elas. Outra opção a avaliar: restringir o campo 'tabela' do json_schema a uma lista fechada (enum) com os nomes do catálogo, se o hub compilar esse schema em tempo aceitável. Em qualquer caso, nomes inventados nunca passam adiante, porque a biblioteca valida cada nome contra o catálogo (regra do desenho: o Python valida).


### C7 · Cache de prefixo: aparece, acelera e dura?

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* C4, C8

**Pergunta.** Quando duas chamadas começam com o mesmo texto grande, a segunda começa a responder mais rápido? O hub informa quanto foi reaproveitado? E o ganho continua depois de alguns minutos parado?

**Por que importa.** Decide quatro coisas: o formato de todos os prompts (parte estável grande primeiro, parte variável no fim); se vale uma chamada de 'aquecimento' antes da descoberta; quanto tempo o dev pode ficar parado sem perder o cache; e se o custo informado reflete o cache. No modelo híbrido, o reaproveitamento anda em degraus de G tokens (ver o item c do resumo): só se aproveita até o último degrau guardado antes do ponto em que os prompts ficam diferentes.

**Como medir.**

1) Prepare prefixos estáveis inventados de ~3K, 6K, 16K, 32K e 128K tokens (instruções + catálogo fictício), com um nonce na 1ª linha de cada par.
2) Para cada par: a chamada A = prefixo + pergunta 1. A chamada B = o mesmo prefixo + pergunta 2, enviada assim que A terminar (nunca em paralelo com A). O controle C = o prefixo com 1 caractere mudado no início + pergunta 1; o controle não deve ganhar nada.
3) Use thinking off, stream com include_usage (TTFT medido como no C4) e max_tokens 16. Faça 3 pares por tamanho.
4) Registre: TTFT, usage.prompt_tokens, usage.prompt_tokens_details (veio null, 0 ou um número?), x-litellm-response-cost e os headers de cache e fallback das convenções. Atenção: x-litellm-model-id identifica o deployment configurado no LiteLLM, não a réplica do vLLM, então não diz se A e B caíram na mesma GPU. Isso só o CCOE responde.
5) Tempo de vida do cache: depois de aquecer o prefixo de 32K, repita a chamada B após 1, 5 e 15 minutos, porque o dev lê e pensa entre uma chamada e outra. Repita num horário de uso alto e num de uso baixo: o cache é compartilhado por todos e descarta primeiro o que foi usado há mais tempo.
6) Conversa: 5 turnos sobre o prefixo de 32K, em que cada turno reenvia tudo + a resposta anterior + a nova pergunta. A rodada 1, com thinking off, é a que vale para o critério. A rodada 2, com low, é só informativa: com o preserve_thinking ligado (padrão do modelo), o template do Qwen remonta o raciocínio dos turnos antigos. Se o cliente ou o LiteLLM não reenviar esse raciocínio igualzinho, o prefixo diverge no turno anterior.
7) Se o CCOE der acesso ao /metrics, leia vllm:prefix_cache_queries e vllm:prefix_cache_hits antes e depois. No Prometheus, eles aparecem com o sufixo _total.
8) Estime G. Com cached_tokens, G é o maior divisor comum dos valores; sem eles, é o tamanho a partir do qual o TTFT de B passa a cair. Compare com o N do log e com o --max-num-batched-tokens informados pelo CCOE.

**Exemplo de requisição.**

```
hub -D cab.txt "$ADEX_LLM_BASE/chat/completions" -d @par_B.json | jq '.usage'
grep -iE "x-litellm-(response-cost|response-duration-ms|attempted-fallbacks|cache-key)" cab.txt   # cab.txt só tem headers da resposta
# esta chamada lê o cached_tokens; o TTFT vem da versão em stream (código do C4)
# par_B.json (essencial):
{"model":"qwen-latest","max_tokens":16,"top_k":20,"chat_template_kwargs":{"enable_thinking":false},"cache":{"no-cache":true},
 "messages":[{"role":"system","content":"[nonce-do-par]\n<instruções + catálogo fictício ~32K>"},
             {"role":"user","content":"Pergunta 2"}]}
```

**Métrica.** Presença e valor de cached_tokens; razão TTFT_B ÷ TTFT_A; razão TTFT_C ÷ TTFT_A; G estimado; TTFT de B após 1, 5 e 15 minutos; TTFT por turno na conversa; custo por chamada (o custo reflete o cache?).

**Critério de aprovação.** Aprovado (metas) se, com prefixo ≥ 32K:
- TTFT_B ≤ 40% de TTFT_A em ≥ 2 de 3 pares;
- o controle C fica ≥ 80% de TTFT_A (prova de que o ganho vem do cache);
- na conversa sem raciocínio, os turnos 2 a 5 ficam ≤ 40% do TTFT frio de um prompt do mesmo tamanho (curva do C4);
- o ganho continua após 5 minutos parado, que é o intervalo típico entre passos do dev (meta a validar).
Ter cached_tokens é desejável, mas não bloqueia. Se vier, deve ser ≥ prefixo comum − G. G = N quando --max-num-batched-tokens < 2N; acima disso, G é praticamente o tamanho do passo. Os tamanhos de 3K e 6K são só informativos: podem dar zero por desenho, quando G for maior que eles.

**Se falhar.** Sem ganho de tempo: pedir ao CCOE para confirmar --enable-prefix-caching e a versão e o modo do cache híbrido, e perguntar se há várias réplicas sem roteamento 'grudado'. No desenho: reduzir o reenvio do catálogo (funil) e manter conversas curtas, com resumo. cached_tokens null mas com ganho de TTFT: medir o cache só pelo tempo e pedir --enable-prompt-tokens-details e o repasse do campo pelo LiteLLM. Cache que some antes de 5 minutos: aquecimento só imediatamente antes da chamada longa, e perguntar ao CCOE sobre a memória reservada ao cache. G grande: prompts com o bloco estável bem maior que G, e nada variável (data, id, nonce) antes dele.


### C8 · Streaming de ponta a ponta

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* Bloco A: chave e base URL funcionando, Bloco B: enable_thinking e reasoning_effort chegam ao vLLM

**Pergunta.** A resposta chega aos pedaços até o Jupyter, inclusive pelo notebook OCI? O último pedaço traz a contagem de tokens? E em qual campo o raciocínio chega ao cliente?

**Por que importa.** Decide se a interface no Jupyter mostra o progresso (e um resumo do raciocínio) ou espera a resposta inteira, e de onde a biblioteca lê o uso e o custo no modo streaming. Também fixa o nome do campo de raciocínio que chega ao cliente. A doc do vLLM diz que reasoning_content foi renomeado para reasoning, mas o LiteLLM padroniza a saída em reasoning_content e não lista o hosted_vllm entre os provedores conferidos. O cliente pode ver um nome, o outro, ou nenhum (raciocínio descartado no caminho).

**Como medir.**

1) Peça ~600 tokens de texto inventado com stream=true e stream_options.include_usage=true. Faça uma rodada com thinking off e outra com reasoning_effort=low.
2) Registre: a hora de chegada de cada pedaço; quais campos vêm no delta (content, reasoning, reasoning_content); o finish_reason; o [DONE]; e o usage no último pedaço.
3) Repita a rodada com low SEM streaming, para ver o nome do campo em message.
4) Prova de aplicação: com thinking off, nenhum pedaço traz raciocínio; com low, o raciocínio vem antes do conteúdo. Se completion_tokens for bem maior que os tokens do content e nenhum campo de raciocínio vier, o raciocínio foi descartado no caminho. Se o content trouxer '<think>' ou '</think>', o hub está sem --reasoning-parser.
5) Rode local e no notebook OCI: proxies corporativos às vezes 'seguram' o stream e entregam tudo de uma vez.
6) Faça também uma chamada que estoura o max_tokens (ex.: max_tokens 64), para ver o finish_reason=length no stream.

**Exemplo de requisição.**

```
hub -N "$ADEX_LLM_BASE/chat/completions" \
 -d '{"model":"qwen-latest","stream":true,"stream_options":{"include_usage":true},"max_tokens":1500,
      "temperature":1.0,"top_p":0.95,"top_k":20,"chat_template_kwargs":{"reasoning_effort":"low"},
      "messages":[{"role":"user","content":"Explique em ~400 palavras o funil de vendas de uma loja fictícia."}]}' \
 | while IFS= read -r l; do printf '%s %s\n' "$(date +%s.%N)" "${l:0:90}"; done
```

**Métrica.** Nº de pedaços com conteúdo; tempo do 1º pedaço ÷ tempo total; nome do campo de raciocínio (com e sem stream); raciocínio presente ou ausente por modo; '<think>' no content; usage presente no fim; finish_reason; [DONE].

**Critério de aprovação.** Aprovado se, nos dois ambientes:
- o conteúdo chega em ≥ 10 pedaços espalhados no tempo, com o 1º antes de 50% do tempo total;
- o raciocínio vem sempre no mesmo campo, com e sem stream, e nunca dentro do content;
- com thinking off, não vem raciocínio; com low, vem;
- o último pedaço traz o usage;
- o finish_reason vem correto (stop ou length) e o stream termina com [DONE].

**Se falhar.** Stream 'segurado' no notebook: usar o modo sem streaming, com um indicador de espera, e pedir ao CCOE e à rede que não bufferizem SSE. Sem usage no stream: pedir ao CCOE --enable-force-include-usage, ou fazer a contabilidade por chamadas sem stream. Campo de raciocínio variável: a biblioteca lê os dois nomes (reasoning e reasoning_content). Raciocínio descartado pelo LiteLLM: pedir ao CCOE uma versão ou configuração do proxy que repasse o campo; enquanto isso, o resumo do raciocínio sai da interface. '<think>' no content: pedir ao CCOE --reasoning-parser qwen3.


### C9 · Vazão com 1, 4 e 8 requisições simultâneas e carga mista

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* C4, C8, Pergunta ao CCOE: cota da chave e janela de teste

**Pergunta.** Quando várias chamadas rodam ao mesmo tempo, quanto cada uma fica mais lenta? O hub começa a recusar (429)? E uma chamada longa trava as curtas?

**Por que importa.** Define o paralelismo máximo da biblioteca (um semáforo global), a política de backoff para 429, e se dá para rodar o crítico em paralelo com a narrativa ou dividir o catálogo em pedaços paralelos. O hub é compartilhado, então o resultado também orienta o uso por vários devs ao mesmo tempo, inclusive quando um deles roda a descoberta longa.

**Como medir.**

1) Combine a janela antes com o CCOE: o hub é compartilhado, e 8 simultâneas com 32K pesam para todos.
2) Use duas cargas: curta (~2K tokens de entrada, 300 de saída) e longa (~32K de entrada, 300 de saída). Thinking off, amostragem sem raciocínio e um nonce no início de cada requisição.
3) Para 1, 4 e 8 simultâneas (asyncio), rode 3 rodadas; com 8 simultâneas, são 24 requisições por carga. Use max_retries=0 no SDK.
4) Carga mista, que é o caso real de um dev rodando a descoberta enquanto outro conversa: 1 requisição de ~128K + 4 curtas disparadas 2 s depois. Meça o TTFT das curtas.
5) Meça: TTFT, tok/s por requisição, vazão total (soma dos completion_tokens ÷ tempo de parede), erros por código (429, 5xx, timeout), o texto do erro (redigido) e os headers x-ratelimit-remaining-requests, x-ratelimit-remaining-tokens, x-litellm-attempted-retries e Retry-After. O vLLM normalmente enfileira em vez de recusar, então um 429 tende a vir do LiteLLM: ou do limite da chave, ou de um modelo em 'cooldown'. O texto do erro diz qual dos dois.
6) Em 429: backoff exponencial com jitter (1, 2 e 4 s, ou o Retry-After, se vier), com no máximo 3 tentativas. Registre se recuperou.
7) Repita em 2 horários, local e no notebook OCI.

**Exemplo de requisição.**

```
import os, asyncio, time, uuid, openai
c = openai.AsyncOpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0, timeout=900)
async def uma(txt):
    t0 = time.perf_counter()
    raw = await c.chat.completions.with_raw_response.create(model="qwen-latest", stream=True,
        stream_options={"include_usage":True}, max_tokens=300, temperature=0.7, top_p=0.8, presence_penalty=1.5,
        extra_body={"top_k":20,"chat_template_kwargs":{"enable_thinking":False},"cache":{"no-cache":True}},
        messages=[{"role":"user","content":f"[{uuid.uuid4()}]\n{txt}"}])
    tentativas = raw.headers.get("x-litellm-attempted-retries")
    async for ch in raw.parse(): ...  # mesmo medidor de TTFT e tok/s do C4
async def nivel(n, txt): await asyncio.gather(*[uma(txt) for _ in range(n)], return_exceptions=True)
asyncio.run(nivel(8, TEXTO_2K))  # no Jupyter: await nivel(8, TEXTO_2K)
```

**Métrica.** TTFT p50 e p95 por nível (com 24 amostras, o p95 é praticamente o 2º pior valor); tok/s por requisição; vazão total; nº de 429, 5xx e timeouts, por origem; retentativas internas do LiteLLM; recuperação após 429; TTFT das curtas na carga mista.

**Critério de aprovação.** Condição de validade: x-litellm-attempted-retries = 0 em todas as chamadas. Se não, os tempos incluem repetições escondidas, e o teste é refeito com o CCOE.
Aprovado (metas) se:
- zero 5xx e zero timeouts;
- todo 429 recuperado em ≤ 3 tentativas;
- com 4 simultâneas, cada requisição mantém ≥ 60% da velocidade de escrita da requisição sozinha, e o TTFT p95 fica ≤ 2× o da requisição sozinha;
- com 8 simultâneas, todas terminam, e o TTFT p95 da carga curta fica ≤ 10 s;
- na carga mista, o TTFT p95 das curtas fica ≤ 10 s.
O maior nível que cumpre tudo vira o limite de paralelismo da biblioteca.

**Se falhar.** A biblioteca fica com paralelismo de 1 a 2 e uma fila; o crítico roda em sequência; o mapa-redução do catálogo fica desaconselhado. Carga mista ruim: a descoberta (chamada longa) vai para uma fila própria e nunca roda junto com o crítico ou a narrativa. Pedir ao CCOE a cota da chave (rpm, tpm, requisições paralelas) e perguntar se há janelas de menor uso.


### C10 · MTP / decodificação especulativa ligada?

*Quem:* nós + CCOE · *Onde:* local · *Esforço:* P · *Depende de:* C8, C4 (velocidade de referência)

**Pergunta.** O hub usa a 'escrita adiantada' (MTP), que acelera textos previsíveis como JSON e código?

**Por que importa.** Se estiver ligada, saídas previsíveis (o JSON da carta, código, cópia de marcadores) saem bem mais rápido que texto livre, o que muda os tempos esperados em cada momento do copiloto. Também conversa com o cache: a doc do vLLM liga o checkpoint de prefixo compartilhado do modelo híbrido (--enable-mamba-shared-prefix-checkpoint) ao uso de MTP/EAGLE. A qualidade não muda: a doc diz que a especulação é sem perda, até a precisão numérica do hardware.

**Como medir.**

1) Primeiro, pergunte ao CCOE o --speculative-config e o --stream-interval usados. A receita oficial usa {"method":"mtp","num_speculative_tokens":3}. Com a resposta em mãos, o teste abaixo é só confirmação.
2) Inferência pelo cliente, 1 requisição por vez, thinking off, 5 repetições de cada caso: (a) previsível: 'copie exatamente o texto abaixo', com ~600 tokens inventados; (b) livre: 'escreva uma história original de ~600 tokens'. Use a MESMA amostragem nos dois (temperature 0.7, top_p 0.8, top_k 20, presence_penalty 0). A presence_penalty 1.5 da amostragem oficial pune repetir palavras e atrapalharia a cópia.
3) Mande stream_interval=1 no corpo. É um parâmetro por requisição no vLLM atual; versões antigas o ignoram. Ele importa porque um --stream-interval maior que 1 junta vários tokens por pedaço mesmo sem MTP.
4) No stream, conte os pedaços com conteúdo e compare com completion_tokens.
5) Se o CCOE ligar --per-request-spec-decode-metrics summary e o LiteLLM repassar o objeto, anote metrics.speculative_decoding.mean_acceptance_length e draft_acceptance_rate. Com acesso ao /metrics, leia vllm:spec_decode_num_accepted_tokens_total e vllm:spec_decode_num_draft_tokens_total.

**Exemplo de requisição.**

```
n_ped = 0; t1 = tN = None; u = None
s = c.chat.completions.create(model="qwen-latest", stream=True, stream_options={"include_usage":True},
  max_tokens=800, temperature=0.7, top_p=0.8, presence_penalty=0,
  extra_body={"top_k":20,"stream_interval":1,"chat_template_kwargs":{"enable_thinking":False},"cache":{"no-cache":True}},
  messages=[{"role":"user","content":f"[{uuid.uuid4()}]\nCopie exatamente o texto abaixo:\n" + TEXTO_600}])
for ch in s:
    if ch.choices and ch.choices[0].delta.content:
        n_ped += 1; tN = time.perf_counter(); t1 = t1 or tN
    if ch.usage: u = ch.usage
print("tokens/pedaço", u.completion_tokens/n_ped, "tok/s", (u.completion_tokens-1)/(tN-t1))
```

**Métrica.** Razão tok/s previsível ÷ tok/s livre; tokens por pedaço de stream nos dois casos; confirmação do CCOE; aceitação média, se disponível.

**Critério de aprovação.** Teste informativo, aprovado quando a pergunta tem resposta com evidência:
- 'ligado', se o CCOE confirmar, ou se a velocidade de escrita do caso previsível for ≥ 1,4× a do caso livre (sem especulação, a velocidade não depende de o texto ser previsível);
- 'desligado', se a razão de velocidade ficar entre 0,85 e 1,15 e vier ~1 token por pedaço nos dois casos;
- tokens por pedaço iguais e maiores que 1 nos dois casos indicam agrupamento do stream (--stream-interval), não MTP.
Os limiares são heurísticas. Resultado ambíguo exige a resposta do CCOE.

**Se falhar.** Sem MTP: orçar os tempos pelo C4, sem ganho extra, e perguntar ao CCOE se dá para ligar (a receita oficial suporta). Resultado ambíguo (por exemplo, se o LiteLLM reagrupar os pedaços): a decisão fica com a resposta do CCOE.


### C11 · KV em FP8 e recuperação difícil

*Quem:* nós + CCOE · *Onde:* local · *Esforço:* M · *Depende de:* C5, Perguntas ao CCOE: KV dtype, GPU e versão do vLLM

**Pergunta.** O hub guarda a 'memória de atenção' (KV) em FP8? Se sim, isso faz o modelo confundir fatos parecidos em prompts muito longos?

**Por que importa.** O KV em FP8 economiza muita memória neste modelo. Pela nossa conta, a partir da arquitetura do card, as 16 camadas de atenção plena (4 cabeças KV de dimensão 256) gastam cerca de 64 KiB por token em BF16, ou ~8 GiB numa sequência de 131K tokens, e o FP8 corta isso pela metade. A receita oficial já sobe o modelo com --kv-cache-dtype fp8. Mas, em versões antigas, isso derrubou a recuperação em contexto longo: no blog do vLLM, a agulha a 128K caiu de 91% (BF16) para 13% antes da correção (kernel FA3 em GPUs Hopper) e voltou a 89% depois. Este teste diz se o 'contexto útil' do C5 também vale para casos difíceis, com vários fatos parecidos, que é o caso real de um catálogo com tabelas quase iguais.

**Como medir.**

1) Pergunte ao CCOE: --kv-cache-dtype, se as escalas são calibradas, o backend de atenção (FA3 ou FlashInfer), o tipo de GPU e a versão do vLLM. Com acesso ao /metrics, o rótulo cache_dtype de vllm:cache_config_info responde a primeira pergunta.
2) Teste multi-agulha com distratores nos degraus de 64K, 128K e ~180K: 5 fatos inventados com códigos parecidos, espalhados no texto, cada um com 1 distrator (ex.: estudo Aurora = TQX-5821; distrator: estudo Áurea = TQX-5812). A pergunta pede os 5 códigos em JSON. Sorteie códigos novos a cada chamada.
3) Rode com reasoning_effort=low (amostragem com raciocínio) e com thinking off (amostragem sem raciocínio), 3 repetições cada: 5 fatos × 2 modos × 3 = 30 respostas por degrau.
4) Se o CCOE puder, repita numa janela com KV em BF16 (auto), para comparar A/B. No A/B, use 5 repetições (50 respostas por degrau), porque com 30 a diferença tem de ser grande para aparecer.

**Exemplo de requisição.**

```
r = c.chat.completions.create(model="qwen-latest", max_tokens=3000, temperature=1.0, top_p=0.95, presence_penalty=0,
  extra_body={"top_k":20, "chat_template_kwargs":{"reasoning_effort":"low"}, "cache":{"no-cache":True}},
  response_format={"type":"json_schema","json_schema":{"name":"codigos","schema":{"type":"object",
    "properties":{k:{"type":"string"} for k in ["aurora","boreal","cirro","delta","eco"]},
    "required":["aurora","boreal","cirro","delta","eco"]}}},
  messages=[{"role":"user","content": f"[{uuid.uuid4()}]\n" + palheiro_128k_com_5_fatos_e_distratores +
    "\n\nDê o código de homologação de cada estudo fictício."}])
```

**Métrica.** Fatos certos por degrau (de 30); trocas pelo distrator por degrau; diferença FP8 × BF16 (se houver A/B).

**Critério de aprovação.** Aprovado se:
- ≥ 27 de 30 fatos certos em 128K (90%) e ≥ 24 de 30 em ~180K (80%);
- no máximo 1 troca pelo distrator em 30, por degrau (≤ 5%);
- havendo A/B (50 respostas por degrau), o FP8 fica no máximo 2 fatos abaixo do BF16 no mesmo degrau (4 pontos).

**Se falhar.** Reduzir o contexto útil ao maior degrau aprovado e usar o funil. Pedir ao CCOE escalas calibradas, uma versão do vLLM com a correção do FA3 ou KV em BF16 só para o qwen-latest. Argumento honesto para o pedido: o FP8 dá memória de verdade (metade do KV), então o pedido precisa vir com a evidência deste teste mostrando o custo em qualidade.


### C12 · Contar tokens antes de enviar

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* P · *Depende de:* Bloco A: chave e base URL funcionando

**Pergunta.** A biblioteca consegue saber, sem chamar o modelo, quantos tokens um prompt vai ocupar?

**Por que importa.** A guarda prompt + max_tokens ≤ 196.608 e o teto de 'contexto útil' (C5) precisam ser checados ANTES do envio; senão, o dev só descobre o estouro depois de esperar o erro. A contagem local também barateia a bateria: calibrar o C4 chamando o hub com max_tokens=1 a 180K gasta um prefill inteiro do hub compartilhado a cada tentativa. E o contador do próprio LiteLLM cai num tokenizador genérico (tiktoken) quando não conhece o provedor, o que erra a conta do Qwen.

**Como medir.**

1) Monte 10 prompts inventados de 1K a ~180K tokens, com mensagem system + user, e 3 prompts com 1 imagem de tamanhos diferentes.
2) Conte de três jeitos: (a) tokenizador oficial do Qwen3.8-27B, com o chat template, via transformers (apply_chat_template com add_generation_prompt=True e enable_thinking=False); (b) o endpoint /tokenize do vLLM, se o CCOE o expuser; (c) a heurística de caracteres ÷ token.
3) Compare com o usage.prompt_tokens da chamada real (max_tokens 1, thinking off), uma chamada por prompt. Nos prompts com imagem, some a fórmula de tokens por imagem do C3.
4) No notebook OCI, verifique se dá para baixar os arquivos do tokenizador do Hugging Face. Se não der, eles terão de ir junto com a biblioteca (conferir a licença do modelo).

**Exemplo de requisição.**

```
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.8-27B")  # baixa só o tokenizador, não os pesos
n_local = len(tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, enable_thinking=False))
r = c.chat.completions.create(model="qwen-latest", max_tokens=1, messages=msgs,
      extra_body={"chat_template_kwargs":{"enable_thinking":False}, "cache":{"no-cache":True}})
print(n_local, r.usage.prompt_tokens, (n_local - r.usage.prompt_tokens) / r.usage.prompt_tokens)
```

**Métrica.** Erro relativo e absoluto de cada método, por tamanho de prompt; erro com imagem (tokenizador + fórmula do C3); disponibilidade do tokenizador no notebook OCI.

**Critério de aprovação.** Aprovado (meta) se o tokenizador oficial errar no máximo 0,5% ou 50 tokens (o que for maior) em todos os prompts de texto, e no máximo 2% nos prompts com imagem. Com esse erro, a guarda pode usar uma margem fixa pequena sem desperdiçar contexto. Para a heurística, registre a pior razão medida: ela vira a margem do plano B.

**Se falhar.** Tokenizador indisponível no notebook: empacotar os arquivos dele na biblioteca. Contagem divergente (o hub usa outro chat template): pedir ao CCOE o --chat-template em uso e/ou acesso ao /tokenize. Último recurso: heurística com margem = pior erro medido + 5%, e tratamento do erro 400 de contexto como última linha de defesa.


### Perguntas ao CCOE · Bloco C

- **Qual é o comando completo do 'vllm serve' do qwen-latest e quais são as versões do vLLM e do LiteLLM? Qual checkpoint é servido (BF16, FP8 ou NVFP4)? Há --chat-template próprio, --default-chat-template-kwargs, --generation-config, --reasoning-parser e --tool-call-parser?** — Vários comportamentos mudam de versão para versão: --mamba-cache-mode ficou obsoleto, reasoning_content virou reasoning, e flags do cache híbrido foram renomeadas. A receita oficial usa pesos NVFP4 e KV em FP8, o que afeta a qualidade em contexto longo (C5, C6, C11). Valores padrão no servidor (template, kwargs, amostragem) mudam o que 'sem parâmetro' significa. Sem --reasoning-parser, o raciocínio vaza no content e o json_schema briga com o raciocínio (C2, C8, C12).
- **O serviço usa --language-model-only, --limit-mm-per-prompt ou --mm-processor-kwargs (que pode mudar o limite de pixels por imagem)?** — Decide se a visão existe (C1), quantas imagens cabem por chamada (C3) e quantos tokens cada imagem consome. A receita oficial cobre só texto e, num exemplo, mostra que --language-model-only aumenta o KV, então é uma escolha provável.
- **Qual é a linha 'Setting attention block size to N tokens' do log de inicialização? Quanto valem --max-num-batched-tokens, --enable-prefix-caching, --prefix-match-unit e --enable-mamba-shared-prefix-checkpoint (e, se a versão for antiga, --mamba-cache-mode)? O cache é compartilhado entre todos os times ou isolado (cache_salt)? Quanta memória fica para ele?** — No modelo híbrido, o reaproveitamento anda em degraus G, que vêm de N e do tamanho do passo. Em versões antigas, sem --mamba-cache-mode align o híbrido fica sem cache nenhum. Isso define o C7 e o formato dos prompts. O isolamento entre times e a memória disponível definem quanto tempo nosso prefixo sobrevive com outros times usando o hub.
- **--enable-prompt-tokens-details, --enable-force-include-usage e --enable-per-request-metrics estão ligados? O LiteLLM repassa ao cliente usage.prompt_tokens_details, usage.completion_tokens_details, o objeto 'metrics' e o campo de raciocínio (reasoning ou reasoning_content)? Dá para expor o endpoint /tokenize do vLLM?** — Sem essas flags, cached_tokens vem null e não há tempo medido no servidor. Há um bug aberto (#22984) em que o LiteLLM ignora cached_tokens no custo. Isso afeta C4, C7, C8 e C12 e o custo que a biblioteca registra.
- **Quantas réplicas e GPUs há atrás do LiteLLM (tipo de GPU, tensor parallel)? O roteamento mantém o mesmo cliente na mesma réplica?** — O cache de prefixo só funciona dentro da mesma réplica. O header x-litellm-model-id identifica só o deployment configurado no proxy, não a réplica, então só o CCOE sabe se a 2ª chamada caiu em outra GPU (C7). A topologia também explica os números de C4 e C9.
- **Qual é o --kv-cache-dtype? As escalas são calibradas? Qual backend de atenção é usado (FA3 ou FlashInfer) e em que GPU (Hopper ou Blackwell)? Quanto valem --mamba-cache-dtype e --mamba-ssm-cache-dtype? Dá para abrir uma janela de teste A/B com KV em BF16?** — O KV em FP8 já derrubou a recuperação em contexto longo no kernel FA3 em Hopper, e a correção deixa o prefill mais lento em cabeças maiores que 128 (este modelo usa 256). O estado do DeltaNet tem um tipo de dado próprio, que não depende do --kv-cache-dtype. Decide o 'contexto útil' (C11).
- **Há decodificação especulativa (--speculative-config, método, quantos tokens)? Qual é o --stream-interval? Podem ligar --per-request-spec-decode-metrics summary ou nos dar leitura do /metrics?** — Só o servidor confirma a especulação com certeza. A inferência pelo cliente (C10) pode ser ambígua se o proxy reagrupar os pedaços, ou se o --stream-interval for maior que 1.
- **Qual é a cota da nossa virtual key (rpm, tpm, requisições paralelas, orçamento em custo)? Os limites são por chave ou globais? O 429 vem com Retry-After? Ele pode vir de um 'cooldown' do roteador, e não só do limite da chave?** — Define o semáforo e o backoff da biblioteca (C9). Pela nossa estimativa, C4, C5, C6 e C11 somam na ordem de 20 milhões de tokens de entrada, e precisamos saber se isso cabe no orçamento da chave.
- **Na configuração do LiteLLM para o qwen-latest: drop_params está ligado? Há num_retries, fallbacks (inclusive context_window_fallbacks para o gpt-oss-20b), cache de respostas e verificação prévia de contexto (pre-call checks)? Que max_input_tokens e max_output_tokens estão no model_info?** — drop_params descarta parâmetros sem avisar o cliente. Retentativas e fallbacks internos escondem 429 e 5xx e podem trocar o modelo sem aviso. O cache de respostas devolve a mesma resposta para pedidos iguais e falseia tempos e repetições. Um model_info errado faz o proxy recusar prompts que o vLLM aceitaria. Isso afeta todos os testes do bloco.
- **Quais são os timeouts do LiteLLM (request e stream) e do ingress/balanceador (ociosidade)? Qual é o tamanho máximo do corpo da requisição, em MB?** — Um prompt de ~180K pode ficar dezenas de segundos sem enviar nada durante o prefill e ser cortado por ociosidade. Imagens grandes em base64 podem bater no limite de corpo (C3, C4).
- **O teto de 8192 de max_tokens é imposto pelo LiteLLM ou pelo vLLM? Pode subir para o qwen-latest?** — Raciocínio e resposta dividem o max_tokens. Com 8192, os tetos de raciocínio de 12K/16K dos perfis 'caça de riscos', 'plano' e 'crítico' são impossíveis, mesmo com espaço de sobra no contexto de 196.608.
- **Podemos combinar uma janela de testes de carga e ter leitura do /metrics ou do Grafana nesse período (vllm:time_to_first_token_seconds, vllm:request_queue_time_seconds, vllm:prefix_cache_hits, vllm:kv_cache_usage_perc)?** — O hub é compartilhado: 8 requisições simultâneas com 32K, ou uma de 128K, atrapalham outros times e misturam a carga deles com a nossa nas medições (C9). As métricas do servidor separam fila, prefill e escrita.
- **Qual é o caminho de rede de um notebook do OCI Data Science até o hub (rede padrão com NAT ou rede própria)? O IP de saída precisa estar numa lista de permissão? Qual URL é a oficial (hub-gpus ou hub-gpus-lab)?** — Os testes de tempo valem no caminho real, que é o notebook. Pela doc do OCI, a rede padrão sai para a internet via NAT, e a rede de uma sessão de notebook não muda depois de criada: se o hub exigir rede própria, a sessão tem de ser recriada. Em 16/10, a URL -lab documentada deu 401.

### Resultado → decisão no desenho

| Resultado | Decisão no desenho |
| --- | --- |
| Prova de aplicação falha (thinking off com raciocínio, low igual a medium, '<think>' no content) | O Bloco C para. O problema volta ao Bloco B e ao CCOE (drop_params, versão do LiteLLM, reasoning parser). Nenhum perfil de esforço é calibrado enquanto isso. |
| C1: imagem aceita e Δprompt_tokens dentro do esperado | O momento 'visão de PNG de gráfico' entra no crítico com perfil low. A biblioteca exporta o PNG e manda junto a especificação em texto. |
| C1: imagem recusada (400) ou descartada em silêncio (Δ≈0) | O crítico visual sai do desenho. Entra o lint de gráfico em Python (ylim, yscale, legenda × especificação), que manda só um resumo em texto ao LLM. Abre-se um pedido ao CCOE. |
| C2: metas batidas com low / só com medium / abaixo da meta | Com low: a visão pode bloquear o handoff, junto com o lint. Só com medium: o perfil da visão sobe para medium. Abaixo da meta: a visão fica só consultiva, e o lint em Python é o bloqueio. Tabelas nunca são lidas por visão para tirar números. |
| C3: limites de quantidade, resolução e bytes, e curva de tokens por imagem | Define o padrão de exportação (ex.: 1280×960, PNG abaixo do limite), quantas imagens vão por chamada (1, ou 2 a 4 para 'antes e depois') e o custo em tokens de cada imagem no contador de contexto da biblioteca (C12). |
| C12: tokenizador oficial bate com o hub / não bate ou não está disponível | Bate: a guarda de contexto e o teto de contexto útil são checados antes do envio, com margem fixa pequena; os arquivos do tokenizador vão junto com a biblioteca se o notebook não baixar do Hugging Face. Não bate: heurística com margem = pior erro medido + 5%, e tratamento do erro 400 de contexto como última linha de defesa. |
| C4: TTFT e tok/s por degrau, mais o teste da guarda de orçamento | Define o orçamento de tempo por momento (com mensagens de espera no Jupyter), o timeout do cliente (pior TTFT medido + margem) e a guarda prompt + max_tokens ≤ 196.608 na biblioteca. |
| C4: timeout, 504 ou recusa do proxy nos degraus altos | Pedir ao CCOE o ajuste de timeouts e do model_info. Até lá, o contexto máximo enviado fica limitado ao maior degrau sem erro. |
| C5: maior degrau com ≥ 90% de acerto | Esse tamanho vira o 'contexto útil', um teto duro na biblioteca (o C11 pode baixá-lo). Se for ≥ 128K: a descoberta vai em 1 chamada. Se for menor: funil do Python (pré-ranking por palavras) ou mapa-redução. |
| C6: top-3 ≥ 54/60, top-1 ≥ 45/60, zero nomes inventados e todos os terços ≥ 85% | O catálogo inteiro (até ~131K tokens) vai numa chamada com cache. Abaixo disso: o funil do Python manda o top-200 e o modelo escolhe. Em qualquer caso, os nomes são validados contra o catálogo. |
| C7: cache acelera e a granularidade G é conhecida | Layout fixo de prompt: bloco estável (instruções + metadados + dossiê) no início, bem maior que G; nada volátil antes dele; pergunta no fim. |
| C7: o ganho dura ≥ 5 minutos / some antes | Dura: chamada de 'aquecimento' com max_tokens=1 no início do passo de descoberta, e reaproveitamento entre as perguntas do dev. Some antes: aquecimento só imediatamente antes da chamada longa, sem contar com reaproveitamento entre passos. |
| C7: cache não acelera | Reduzir o reenvio de contexto: funil, conversas curtas com resumo e dossiê enxuto. Pedido ao CCOE (flags, versão, réplicas, roteamento). |
| C7: cached_tokens presente / null | Presente: a biblioteca registra a taxa de cache por chamada e alerta quando ela cai. Null: o cache é monitorado só pelo tempo, e o custo do header é tratado como teto (o bug do LiteLLM ignora o cache no custo). |
| C8: streaming íntegro nos dois ambientes / segurado no notebook | Íntegro: progresso ao vivo no Jupyter e contabilidade lida do último pedaço, aceitando 'reasoning' e 'reasoning_content'. Segurado: modo sem stream, com indicador de espera. Raciocínio descartado pelo proxy: o resumo do raciocínio sai da interface até o CCOE corrigir. |
| C9: maior nível de concorrência que cumpre as metas, e resultado da carga mista | O nível vira o semáforo global da biblioteca (N requisições em voo). Política de 429: backoff com jitter, respeitando o Retry-After, até 3 tentativas. Crítico em paralelo com a narrativa só se N ≥ 2; mapa-redução do catálogo só se N ≥ 4. Carga mista ruim: a descoberta ganha uma fila própria. |
| C10: MTP ligado / desligado | Ligado: tempos esperados menores para JSON, código e cópias de marcadores, e saídas estruturadas favorecidas. Desligado: orçar pelo C4 e pedir ao CCOE (a receita oficial suporta). |
| C11: FP8 mantém a recuperação / piora a recuperação | Mantém: o contexto útil do C5 vale. Piora: o contexto útil cai ao maior degrau aprovado no teste difícil, e vai ao CCOE o pedido de escalas calibradas, de uma versão corrigida ou de KV em BF16, com a evidência do teste. |
| Pergunta ao CCOE: o teto de max_tokens fica em 8192 | Os tetos de raciocínio de 12K/16K saem dos perfis. thinking_token_budget e a resposta cabem juntos em ≤ 8192, com folga para a resposta. Os momentos xhigh precisam ser recalibrados (por exemplo, dividindo o plano em etapas menores). |

### Incertezas

- Visão no hub: a receita oficial do vLLM diz que só o serviço de texto foi coberto e verificado. Não há doc oficial de limites de imagem para o Qwen3.8 além do preprocessor_config.json. A conta de ~1 token por 32×32 pixels é dedução a partir de patch 16 e fusão 2×2, não um número publicado.
- O padrão de --limit-mm-per-prompt (a ajuda da CLI fala em 999 por modalidade) e a mensagem de erro ao passar do limite não estão documentados de forma estável; podem variar por versão.
- Granularidade do cache: a fórmula de G (N quando --max-num-batched-tokens < 2N; senão ≈ o maior múltiplo de N que cabe no passo) é inferência nossa, a partir da doc vllm.config.cache e da receita do LMCache. Também é hipótese nossa que o '~1,6K' do contexto seja 2×784 = 1.568 (passo com --max-num-batched-tokens 2048). Só o log e a config do hub resolvem.
- Flags do cache híbrido mudam entre versões: --mamba-cache-mode está obsoleto e sem efeito na CLI atual, mas era obrigatório ('align') nas versões citadas pelo LMCache. O checkpoint fino se chama --enable-mamba-fine-grained-prefix-cache na v0.30 e --enable-mamba-shared-prefix-checkpoint na doc atual. prefix_match_unit equivale a hash_block_size.
- cached_tokens: há relatos de prompt_tokens_details vir null mesmo com --enable-prompt-tokens-details em versões recentes. A doc do LiteLLM não confirma que o campo chega ao cliente; o bug #22984 só confirma que ele é ignorado no custo.
- reasoning_tokens: a doc do vLLM não documenta usage.completion_tokens_details.reasoning_tokens, e não confirmamos se o LiteLLM preenche esse campo para o hosted_vllm. O plano é contar pelo texto do raciocínio com o tokenizador.
- Nome do campo de raciocínio visto pelo cliente: o vLLM atual usa 'reasoning'; o LiteLLM padroniza em 'reasoning_content', mas não lista o hosted_vllm entre os provedores conferidos. Pode chegar um, o outro ou nenhum, e só o C8 responde.
- Caminho do reasoning_effort: o card do HF usa o parâmetro no nível de cima; a receita do vLLM usa chat_template_kwargs. Na API 0.16 do vLLM, o campo no nível de cima era tipado low/medium/high (xhigh daria 400), e não confirmamos se ele chega ao template do Qwen. O LiteLLM passou a aceitar reasoning_effort no hosted_vllm (issue #13608), mas não documenta o que faz com ele. O comportamento de ignorar campos desconhecidos com aviso só no log foi visto no código do vLLM, não na doc.
- x-litellm-response-duration-ms em streaming: a doc não diz o que o header mede quando os headers saem antes do corpo. Por isso a separação rede × servidor ficou só nas chamadas sem stream.
- Não está confirmado na doc se o vLLM ou o LiteLLM mandam algum byte ao cliente durante o prefill de prompts longos. O risco de timeout por ociosidade é suposição, e o C4 verifica.
- Origem dos 429: que o vLLM enfileira em vez de recusar, e que o LiteLLM devolve 429 também quando o modelo está em 'cooldown', vem do conhecimento do código, não da doc conferida. O texto do erro no C9 vai dizer.
- Mensagens de erro do LiteLLM podem ecoar parte da chave ou o hash dela; isso não foi confirmado na doc. O redige() é precaução.
- Inferência de MTP pelo cliente: os limiares (razão de velocidade ≥ 1,4; 0,85 a 1,15 para 'desligado') são heurísticas nossas. Não está documentado se o stream_interval por requisição vence o --stream-interval do servidor. O LiteLLM pode reagrupar pedaços do stream. Os nomes das métricas Prometheus aparecem com e sem o sufixo _total em páginas diferentes da doc.
- O objeto 'metrics' (por requisição e de especulação) fica no corpo da resposta, fora do esquema OpenAI. O SDK tipado e o LiteLLM podem descartá-lo; não foi confirmado.
- FP8: a conta de ~64 KiB por token de KV em BF16 é nossa, a partir da arquitetura do card (16 camadas de atenção plena, 4 cabeças KV, dimensão 256), e ignora sobras de página. O estado do DeltaNet é fixo por sequência e tem tipo de dado próprio (--mamba-cache-dtype). O blog não diz qual modelo usou no teste 91% → 13% → 89%, e o efeito do FP8 na recuperação do Qwen3.8 não foi publicado.
- Limite de corpo do proxy: o max_request_size_mb do LiteLLM aparece só em espelhos de código (talvez restrito à versão enterprise), e o ingress pode ter limite próprio. É desconhecido até o CCOE responder.
- "cache": {"no-cache": true} é um controle do LiteLLM. Se o cache de respostas estiver desligado, supomos que o campo é removido pelo proxy ou ignorado pelo vLLM (só aviso no log), sem efeito na resposta; não foi confirmado.
- C12: supomos que o chat template do hub é o do repositório oficial e que AutoTokenizer baixa só os arquivos do tokenizador. Um --chat-template próprio no hub muda a contagem. A licença do modelo precisa ser conferida antes de empacotar o tokenizador na biblioteca.
- Não consegui abrir a referência oficial da API de Chat Completions da OpenAI (HTTP 403). Os formatos de image_url em base64 e de stream_options foram conferidos pela doc do vLLM, pelo guia de visão da OpenAI e pelo README do SDK.
- Todas as metas de tempo, vazão e acerto dos critérios são propostas de produto (o que o copiloto precisa para ser usável no Jupyter), não medições. Precisam ser validadas pelo gestor antes da bateria, e a carga dos outros times no hub compartilhado vai influenciar os números. A estimativa de ~20 milhões de tokens é uma conta do roteiro, não uma medição.

### Fontes consultadas

- [vLLM Recipes — Qwen/Qwen3.8-27B (comando com --kv-cache-dtype fp8, NVFP4, MTP, reasoning_effort em chat_template_kwargs, 'só texto verificado')](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)
- [Hugging Face — model card Qwen/Qwen3.8-27B (arquitetura, amostragem, reasoning_effort, preserve_thinking)](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Hugging Face — preprocessor_config.json do Qwen3.8-27B (longest_edge 16.777.216, shortest_edge 65.536, patch 16, merge 2)](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/preprocessor_config.json)
- [vLLM Recipes — Qwen/Qwen3.5-27B (prefix caching Mamba experimental, --language-model-only, MTP)](https://recipes.vllm.ai/Qwen/Qwen3.5-27B)
- [vLLM docs — Automatic Prefix Caching (híbridos Mamba, --prefix-match-unit, --enable-mamba-shared-prefix-checkpoint)](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html)
- [vLLM docs — Design: Prefix Caching (só blocos cheios, cache_salt, LRU, hash de imagem)](https://docs.vllm.ai/en/latest/design/prefix_caching.html)
- [vLLM API — vllm.config.cache (mamba_cache_mode 'align': só o último token de cada passo, em múltiplo do bloco; mamba_cache_dtype; prefix_match_unit)](https://docs.vllm.ai/en/latest/api/vllm/config/cache.html)
- [vLLM CLI — vllm serve (--enable-prompt-tokens-details, --enable-force-include-usage, --enable-per-request-metrics, --per-request-spec-decode-metrics, --language-model-only, --limit-mm-per-prompt, --mamba-cache-mode obsoleto, --stream-interval, --default-chat-template-kwargs, --generation-config)](https://docs.vllm.ai/en/latest/cli/serve.html)
- [vLLM docs — OpenAI-Compatible Server (parâmetros extras, generation_config.json aplicado por padrão, stream_interval e cache_salt por requisição)](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM API 0.16 — protocolo de chat completion (reasoning_effort tipado low/medium/high)](https://docs.vllm.ai/en/v0.16.0/api/vllm/entrypoints/openai/chat_completion/protocol/)
- [vLLM docs — Multimodal Inputs (image_url, --limit-mm-per-prompt, hash de mídia)](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)
- [vLLM docs — Reasoning Outputs (campo reasoning, ex-reasoning_content; thinking_token_budget)](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)
- [vLLM docs — Speculative Decoding (sem perda, métodos)](https://docs.vllm.ai/en/latest/features/speculative_decoding/)
- [vLLM docs — Multi-Token Prediction (MTP)](https://docs.vllm.ai/en/latest/features/speculative_decoding/mtp/)
- [vLLM docs — Per-Request Acceptance Metrics (metrics.speculative_decoding, mean_acceptance_length, draft_acceptance_rate)](https://docs.vllm.ai/en/latest/features/speculative_decoding/acceptance_metrics/)
- [vLLM docs — Per-Request Metrics (time_to_first_token_ms a partir do agendamento, queue_time_ms, no último pedaço do stream)](https://docs.vllm.ai/en/latest/features/per_request_metrics/)
- [vLLM docs — Design: Metrics (nomes Prometheus, vllm:cache_config_info)](https://docs.vllm.ai/en/latest/design/metrics/)
- [vLLM docs — Quantized KV Cache (FP8)](https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache/)
- [vLLM blog — The State of FP8 KV-Cache and Attention Quantization in vLLM (91% → 13% → 89%, FA3 em Hopper, cabeças > 128)](https://vllm.ai/blog/2026-04-22-fp8-kvcache)
- [vLLM docs — Benchmark CLI (TTFT/TPOT/ITL, nota sobre especulação)](https://docs.vllm.ai/en/latest/benchmarking/cli/)
- [vLLM CLI — vllm bench serve (--base-url, --header, --extra-body)](https://docs.vllm.ai/en/latest/cli/bench/serve.html)
- [vLLM PR #46384 — partial prefix cache hit para modelos híbridos (estado Mamba só no último limite)](https://github.com/vllm-project/vllm/pull/46384)
- [vLLM issue #40696 — prefix caching ineficaz em híbridos abaixo do block size](https://github.com/vllm-project/vllm/issues/40696)
- [LMCache docs — receita Qwen3.5/3.6/3.8 (N = 784 para Qwen3.8-27B em 1 GPU; granularidade × --max-num-batched-tokens)](https://docs.lmcache.ai/recipes/qwen3_5.html)
- [LiteLLM docs — Response Headers (duration, overhead, attempted-retries/fallbacks, model-id = deployment, cost, x-ratelimit-*)](https://docs.litellm.ai/docs/proxy/response_headers)
- [LiteLLM docs — drop_params, additional_drop_params, allowed_openai_params](https://docs.litellm.ai/docs/completion/drop_params)
- [LiteLLM docs — Provider-specific params (parâmetros não OpenAI repassados no corpo)](https://docs.litellm.ai/docs/completion/provider_specific_params)
- [LiteLLM docs — Caching (x-litellm-cache-key)](https://docs.litellm.ai/docs/proxy/caching)
- [LiteLLM docs — Caching controls (cache no-cache por requisição)](https://docs.litellm.ai/docs/proxy/caching_controls)
- [LiteLLM docs — reasoning_content (padronização; hosted_vllm fora da lista)](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM docs — Prompt Caching (prompt_tokens_details.cached_tokens)](https://docs.litellm.ai/docs/completion/prompt_caching)
- [LiteLLM docs — Provider vLLM (hosted_vllm)](https://docs.litellm.ai/docs/providers/vllm)
- [LiteLLM issue #22984 — cached_tokens do vLLM ignorado no custo (aberta)](https://github.com/BerriAI/litellm/issues/22984)
- [LiteLLM issue #13608 — reasoning_effort no hosted_vllm (UnsupportedParamsError, fechada por PR)](https://github.com/BerriAI/litellm/issues/13608)
- [NVIDIA AIPerf — Vendor Usage Field Reference (vLLM: cached_tokens só com --enable-prompt-tokens-details)](https://docs.nvidia.com/aiperf/dev/reference/vendor-usage-field-reference)
- [OpenAI — Images and vision guide (data URL base64, detail)](https://developers.openai.com/api/docs/guides/images-vision)
- [openai-python — README (retries automáticos em 429/5xx, timeout padrão, with_raw_response, extra_body)](https://github.com/openai/openai-python/blob/main/README.md)
- [Oracle — Creating a Notebook Session (rede padrão com NAT, rede própria, rede imutável após criar)](https://docs.oracle.com/iaas/data-science/using/create-notebook-sessions.htm)

### O que a revisão adversarial corrigiu (27)

- **[alta] Geral (C2, C5, C6, C8, C11 e todos os de tempo)** — Nenhum teste provava que os parâmetros foram APLICADOS, só que foram aceitos. Pela doc e pelo código: o vLLM ignora campos que não conhece e só avisa no log do servidor; o LiteLLM, com drop_params ligado, descarta parâmetros OpenAI 'não suportados' sem erro para o cliente; e, na API 0.16 do vLLM, o reasoning_effort no nível de cima aceitava só low/medium/high, sem garantia de chegar ao template do Qwen. O card do HF usa esse caminho; a receita do vLLM usa chat_template_kwargs. C2, C5, C6 e C11 calibravam o perfil 'low' sem conferir se o low valeu de fato. *Correção:* Nova convenção 'prova de aplicação', registrada em toda chamada: o raciocínio vem vazio com thinking off e presente com low, a mediana do low fica abaixo da do medium e não aparece '<think>' no content. reasoning_effort só dentro de chat_template_kwargs. Dependência explícita do Bloco B. Nova decisão: se a prova falhar, o bloco para.
- **[alta] Geral (C1, C3, C5 modo off, C7, C9, C10)** — Vários exemplos não mandavam temperature/top_p/presence_penalty. A doc do vLLM diz que o servidor aplica o generation_config.json do modelo quando o cliente omite. Neste modelo, esse arquivo traz a amostragem COM raciocínio (1.0/0.95/20, segundo a receita), então testes com thinking off rodavam com a amostragem errada. *Correção:* Amostragem sempre explícita, dita nas convenções (sem raciocínio: 0.7/0.8/20/1.5; com raciocínio: 1.0/0.95/20/0). Todos os exemplos foram atualizados. O C10 usa presence 0 de propósito.
- **[alta] C7** — O teste usava x-litellm-model-id para saber se A e B 'caíram na mesma réplica'. Pela doc do LiteLLM, esse header é o ID do deployment configurado no proxy (model_info.id), não o pod ou a GPU do vLLM. Com um deployment apontando para um serviço com várias réplicas, o header é igual mesmo quando a réplica muda, e a conclusão sairia errada. *Correção:* A inferência saiu. O header fica só como prova de que não houve troca de deployment. A topologia (réplicas, roteamento 'grudado') virou pergunta ao CCOE.
- **[alta] Geral (C1, C2, C3, C5, C7, C9)** — Se o cache de RESPOSTAS do LiteLLM estiver ligado, requisições idênticas (a mesma imagem e a mesma pergunta repetidas 3 vezes) voltam do cache. Aí o '3 de 3' do C1, a variância do C2 e os tempos medem o cache do proxy, não o modelo. *Correção:* ID de execução ou nonce em toda requisição, mais "cache": {"no-cache": true} (controle do LiteLLM), e registro do header x-litellm-cache-key, que só aparece em resposta vinda do cache. Se ele aparecer, a medição é invalidada. Pergunta nova ao CCOE sobre a config do proxy.
- **[alta] C7** — A granularidade do cache estava subestimada. Pela doc vllm.config.cache, no modo align o estado do DeltaNet só é guardado no último token de cada passo do agendador, e só quando esse token cai num múltiplo do bloco. Segundo o LMCache, com --max-num-batched-tokens ≥ 2N o reaproveitamento anda em degraus do tamanho do passo (ex.: 8192 → 7.840 com N=784). O critério 'cached_tokens ≥ 90% do prefixo comum' podia reprovar um cache que funciona, e os prefixos de 3K e 6K podem dar zero por desenho. *Correção:* Critério relativo a G (cached_tokens ≥ prefixo comum − G), com a fórmula de G explicada no resumo e a hipótese de que o '~1,6K' do contexto seja 2×784 (passo com --max-num-batched-tokens 2048). Os tamanhos de 3K e 6K ficam só informativos.
- **[alta] C11** — O argumento ao CCOE estava errado. Ele dizia que o FP8 rende pouca memória porque a maior parte do estado é DeltaNet. Pela arquitetura do card do HF (16 camadas de atenção plena, 4 cabeças KV, dimensão 256), o KV gasta cerca de 64 KiB por token em BF16, ou ~8 GiB numa sequência de 131K, contra um estado DeltaNet fixo de dezenas a centenas de MB por sequência. Em contexto longo, o FP8 economiza muito, e a receita oficial já sobe o modelo com --kv-cache-dtype fp8. Além disso, o bug do blog era do kernel FA3 em GPUs Hopper; em Blackwell com FlashInfer, a correção não é necessária. *Correção:* Argumento trocado: o FP8 dá memória de verdade, então o pedido ao CCOE precisa vir com a evidência de qualidade deste teste. Entraram as perguntas sobre o tipo de GPU, o backend de atenção e --mamba-cache-dtype/--mamba-ssm-cache-dtype. O texto sobre a lentidão do prefill foi corrigido para 'cabeças maiores que 128'.
- **[alta] C4, C9, C10 (rede × servidor)** — O roteiro usava x-litellm-response-duration-ms para separar rede de servidor também em streaming. Em streaming, os headers HTTP saem antes do corpo, então o header não pode conter o tempo total, e a doc do LiteLLM não diz o que ele mede nesse caso. *Correção:* A separação rede × servidor passa a usar só chamadas sem streaming (uma extra por degrau no C4). Em streaming, o tempo de servidor vem de metrics.queue_time_ms e metrics.time_to_first_token_ms, se o CCOE ligar a flag e o proxy repassar o objeto. Este último é contado a partir do agendamento, sem a fila.
- **[alta] C9** — max_retries=0 no SDK não basta para enxergar os 429. O LiteLLM pode repetir por dentro (num_retries) e trocar de modelo (fallbacks), escondendo erros e inflando os tempos. O roteiro também supunha que todo 429 é 'limite da chave'. O vLLM normalmente enfileira em vez de recusar, então o 429 tende a vir do proxy, seja pelo limite da chave, seja por um modelo em cooldown. *Correção:* Registrar x-litellm-attempted-retries, x-litellm-attempted-fallbacks e Retry-After, e o texto do erro já redigido, para saber a origem. Retentativas internas viram condição de validade do teste. O backoff respeita o Retry-After.
- **[alta] Segurança (C2, C3, C6, C7, C8 e convenções)** — Os exemplos em curl usavam -H "Authorization: Bearer $ADEX_LLM_KEY". O shell expande a chave na linha de comando do processo, que fica visível na lista de processos. O C3 imprimia o texto do erro sem redação, e erros do proxy podem ecoar parte da chave. Faltava proibir log DEBUG (OPENAI_LOG, httpx), --trace e %env no Jupyter. *Correção:* Criada a função hub(): a chave vai por printf (comando interno do shell) para curl -K -, e não aparece em argv. Criada a função redige() para todo texto de erro que vai para a tela ou para o CSV. Proibidos -v, --trace, --trace-ascii, OPENAI_LOG=debug, %env e !echo. Incluído um trecho de leitura do OCI Vault direto para os.environ.
- **[media] C4** — Faltavam quatro coisas. (1) A dependência do C8: o TTFT é medido pelo stream, e um stream segurado no notebook transforma o TTFT em tempo total. (2) Uma calibração mais barata: ajustar o tamanho com max_tokens=1 por tentativa e erro a 180K gasta um prefill inteiro do hub compartilhado a cada tentativa. (3) Critérios de TTFT claros, que não diziam se valia a mediana ou o pior caso. (4) Checar se o erro de estouro vem do proxy ou do vLLM, e se houve fallback para outro modelo. *Correção:* Dependências de C8 e C12 incluídas. Calibração feita pelo tokenizador, com 1 chamada de confirmação. Critério novo: a mediana fica na meta e nenhuma repetição passa do dobro dela, e cada degrau foi ligado a um momento do copiloto. Incluídos o caso de prompt sozinho acima de 196.608, o registro da origem do erro e attempted-fallbacks = 0.
- **[media] C6** — Havia contradição entre cache e posição: para variar a posição do alvo era preciso reordenar o catálogo, o que muda o prefixo e anula o cache que o próprio teste queria medir. O critério 'diferença ≤ 10 pontos entre início, meio e fim' também não era mensurável, porque com ~7 perguntas por terço um único erro vale 14 pontos. *Correção:* Ordem fixa do catálogo, com as tabelas-alvo espalhadas 7/6/7 pelos terços e 3 repetições (60 chamadas). Critérios em contagens: top-3 ≥ 54/60, top-1 ≥ 45/60, e top-3 ≥ 85% em cada terço (18/21, 16/18, 18/21). Nonce só por rodada.
- **[media] C6** — O teste pedia o TTFT frio × quente, mas o exemplo não usava stream. Além disso, com raciocínio ligado, o 1º pedaço de conteúdo só chega depois de pensar, então medir até ele mistura prefill e raciocínio. *Correção:* Stream obrigatório, com o TTFT medido até o 1º pedaço de qualquer tipo (raciocínio ou conteúdo), e o tempo total registrado à parte.
- **[media] C3** — Ruído não infla um PNG de 640×480 até 20 MB, porque o bruto tem ~0,9 MB. Para chegar lá seria preciso subir a resolução, o que mistura bytes com pixels. Os critérios '5 MB' e '1600×1200' não estavam ligados a nenhuma necessidade e não batiam com o padrão de exportação de 1280×960. Faltava também testar o piso do preprocessor (65.536 px ≈ 64 tokens). *Correção:* O arquivo é inflado com um bloco tEXt (PIL PngInfo), sem mudar os pixels. Critério de bytes: aceitar 2× o maior PNG que o padrão de exportação gera. Mínimo para seguir: 1 imagem de 1280×960. Incluídos o degrau de 200×150, o registro da origem do erro (vLLM, LiteLLM ou ingress) e um mínimo de 2 imagens por chamada para o 'antes e depois'.
- **[media] C2** — 'Repita só os erros com medium' enviesa o resultado: só os erros ganham uma 2ª chance, e o alarme falso em medium nunca é medido. Resposta cortada (length) e JSON inválido não contavam como erro. O tamanho das imagens não estava ligado ao padrão do C3, e não havia como saber se o low foi aplicado. *Correção:* Se o low reprovar, o conjunto inteiro roda de novo com medium. length e JSON inválido contam como erro. As imagens usam o tamanho de exportação do C3. Os tokens de raciocínio são medidos (pelo campo do usage ou pelo tokenizador do C12), e a mediana do medium tem de passar a do low. Dependências de C3, C12 e Bloco B incluídas.
- **[media] C5** — O exemplo quebrava com content None, que acontece quando finish_reason=length. A igualdade exata reprovava respostas como 'TQX-5821.'. O modo thinking off estava sem amostragem, e os critérios em % não diziam quantas chamadas cada degrau tem (12). *Correção:* Uso de (content or "") e extração por regex no formato AAA-9999. Código sorteado por chamada, com o enchimento livre de outros códigos no mesmo formato. Amostragem dos dois modos explícita. Critérios em contagens (36/36 até 64K, 11/12 em 128K, 10/12 em ~180K).
- **[media] C10** — Havia confusões que podiam produzir um falso 'ligado'. O --stream-interval (no servidor ou por requisição) junta vários tokens por pedaço mesmo sem MTP. A presence_penalty 1.5 da amostragem oficial pune repetir palavras e atrapalha o 'copie exatamente'. A flag --per-request-spec-decode-metrics exige um valor (summary ou detailed), e os nomes dos campos e das métricas não estavam conferidos. *Correção:* O CCOE é consultado primeiro, e o teste só confirma. stream_interval=1 vai na requisição, com a mesma amostragem (presence 0) nos dois casos. Critério reescrito: o sinal principal é a razão de velocidade; tokens por pedaço iguais e maiores que 1 nos dois casos indicam agrupamento do stream. Nomes conferidos: mean_acceptance_length, draft_acceptance_rate e vllm:spec_decode_num_*_tokens_total.
- **[media] C8** — O teste não checava três falhas silenciosas. (1) Raciocínio vazando no content ('<think>'), o que acontece quando o hub roda sem --reasoning-parser. (2) Raciocínio descartado pelo proxy, quando completion_tokens é alto e nenhum campo de raciocínio vem. O LiteLLM padroniza a saída em reasoning_content e não lista o hosted_vllm entre os provedores conferidos, então o cliente pode ver o nome antigo, o novo ou nenhum. (3) O nome do campo na resposta sem stream. *Correção:* Entraram uma rodada sem stream, as checagens de vazamento e de descarte, a dependência do Bloco B e critérios para thinking off (sem raciocínio) e low (com raciocínio).
- **[media] Lacuna: novo C12** — A guarda 'prompt + max_tokens ≤ 196.608' e o teto de contexto útil precisam contar os tokens ANTES do envio, mas nenhum teste verificava isso. Pela doc, o contador do LiteLLM cai num tokenizador genérico (tiktoken) quando não conhece o provedor, o que erra a conta do Qwen. E calibrar o C4 pelo hub sai caro para todos. *Correção:* Novo C12, que compara o tokenizador oficial (com chat template), o /tokenize do vLLM (se exposto) e uma heurística contra usage.prompt_tokens, e verifica se o notebook OCI consegue baixar o tokenizador.
- **[media] C7 (lacuna)** — Não havia medida de quanto tempo o cache sobrevive. O cache é compartilhado e descarta o que foi usado há mais tempo, e o dev passa minutos lendo entre uma chamada e outra no Jupyter, então a chamada de 'aquecimento' pode ser inútil. A conversa com raciocínio também não considerava o preserve_thinking (ligado por padrão): se o raciocínio antigo não for reenviado igualzinho, o prefixo diverge. *Correção:* Novo passo que repete B após 1, 5 e 15 minutos, em horário de uso alto e baixo, com a meta 'o ganho continua após 5 minutos'. A conversa com low fica só informativa, com a explicação do motivo. Nova decisão sobre o aquecimento.
- **[media] C9 (lacuna)** — Faltava a carga mista: uma chamada longa (a descoberta de ~128K) rodando enquanto chegam chamadas curtas. É o caso real com vários devs, e também o da própria biblioteca se o crítico rodar junto com a descoberta. *Correção:* Novo passo com 1 chamada de ~128K + 4 curtas, com meta de TTFT p95 das curtas ≤ 10 s, e nova decisão de fila separada para a descoberta.
- **[media] Perguntas ao CCOE** — Faltavam várias perguntas: qual checkpoint é servido (a receita oficial usa NVFP4); os padrões do servidor (--default-chat-template-kwargs, --generation-config, --chat-template); se --reasoning-parser está ligado; a config do LiteLLM (drop_params, num_retries, fallbacks, cache de respostas, pre-call checks, model_info); --stream-interval; --mamba-cache-dtype; o isolamento do cache entre times (cache_salt); a exposição do /tokenize; e o aviso de que a sessão de notebook não muda de rede depois de criada. *Correção:* As perguntas foram incluídas ou ampliadas, cada uma com o motivo e os testes que destrava.
- **[baixa] C11** — A dependência do C6 era desnecessária. Os critérios em % não diziam o tamanho da amostra (30 respostas por degrau). Com 30 amostras, o A/B FP8 × BF16 não consegue separar uma diferença de 5 pontos. *Correção:* Dependência trocada para C5 + respostas do CCOE. Critérios em contagens (27/30, 24/30, ≤ 1 troca). No A/B, 5 repetições (50 por degrau), e o FP8 pode ficar no máximo 2 fatos abaixo do BF16.
- **[baixa] C1** — O código 'K7-QX' era fixo nas 3 repetições, o que abre espaço para cache de resposta, e a resposta da chamada A, só em texto, não era conferida. *Correção:* Código sorteado a cada repetição, e controle de que A não traz o código. Faixa de Δ em tokens (240 a 390). Leitura dos headers via with_raw_response.
- **[baixa] C9** — O p95 com 24 amostras não estava explicado (é praticamente o 2º pior valor), e o backoff ignorava o Retry-After. *Correção:* Explicação incluída na métrica, e o backoff passa a usar o Retry-After quando ele vier.
- **[baixa] C6** — O schema usava maxItems, que pode não ser aceito por todo backend de saída estruturada. *Correção:* Se o hub recusar o maxItems, ele sai do schema e o Python confere a quantidade. Também entrou, como opção a avaliar, o enum de nomes no schema.
- **[baixa] Resumo (pontos b a e)** — Havia imprecisões em quatro pontos. (1) --mamba-cache-mode está obsoleto na doc atual, mas em versões antigas o 'align' era obrigatório e o padrão 'none' deixava o modelo sem cache. (2) A lentidão da correção do FP8 vale para cabeças maiores que 128, não só para 256. (3) O bug era do FA3 em Hopper. (4) A doc do vLLM não documenta usage.completion_tokens_details.reasoning_tokens. *Correção:* Resumo reescrito com os quatro ajustes, e incluídos o ponto sobre a receita usar FP8 e NVFP4 e o ponto sobre parâmetro aceito × aplicado.
- **[baixa] Ordem e dependências** — A ordem tinha três problemas: o C7 vinha depois do C6, embora o C6 use o layout e o efeito do cache medidos no C7; o C4 não dependia do C8; e o C10 não dependia do C4, de onde vem a velocidade de referência. *Correção:* Nova ordem: C1 → C3 → C2; C8 → C12 → C4 → C10; C7 → C5 → C6 → C11; C9 (em janela combinada com o CCOE). As dependências foram ajustadas em cada teste.

---

## D · Operação, segurança, governança e troca silenciosa do modelo

O Bloco D responde a duas perguntas: o copiloto funciona com segurança no caminho real (do notebook do OCI Data Science até o GPU Hub)? E continua confiável com o passar do tempo? São 14 testes, todos com dados inventados. Operação: rede e certificado (D1), latência do caminho (D2), chave pelo Vault (D3), limites e 429 (D4), timeouts em chamadas longas (D5), custo e rastreio (D6) e hub fora do ar (D7). Governança e segurança: o que o hub grava, medido com marcadores "canário" (D8); o que pode ir ao hub pela LGPD e pela classificação da empresa (D9); escopo e rotação da chave (D10); vazamento do nosso lado (D14). Troca silenciosa: se os parâmetros chegam de fato ao modelo (D13), a impressão digital do qwen-latest (D11) e o papel do gpt-oss-20b (D12). Quatro testes decidem se a Fase 0 avança ou para: D1, D8, D9 e D11, sendo que o D11 depende do D13. Quase todos precisam do CCOE, porque configuração do LiteLLM, logs, limites e política do apelido ficam com eles. Cinco achados da documentação mudam o desenho. (1) O LiteLLM conta tokens totais (entrada + saída) no limite por minuto e reserva, ANTES da chamada, a entrada estimada mais o max_tokens pedido. Uma descoberta de cerca de 131 mil tokens pode ser recusada sozinha, e um max_tokens exagerado piora isso. (2) A partir do LiteLLM Proxy v1.85.0, as flags mock_testing_* são removidas dos pedidos ao proxy. Uma falha real do servidor só o CCOE provoca; o tratamento de erros da lib é testado contra um servidor falso local. (3) O LiteLLM repassa ao vLLM os parâmetros que não são da OpenAI, mas pode descartar parâmetros (drop_params, additional_drop_params), injetar padrões, alterar o pedido (guardrails), servir respostas do cache e tirar campos da resposta, sempre com HTTP 200. Por isso o D13 prova, parâmetro por parâmetro, que houve efeito, e a impressão digital desliga o cache. (4) return_prompt_text só aparece na doc mais recente do vLLM. return_token_ids (prompt_token_ids) já existe em versões mais antigas e vira a sonda principal do template. (5) O SDK openai retenta 2 vezes por padrão (inclusive 429, 5xx e timeouts), com timeout de 10 min. A lib desliga isso e fica com uma regra única de retentativa. Regras para todos os testes: base em $ADEX_LLM_BASE (hoje https://hub-gpus.usto.re/v1); chave em $ADEX_LLM_KEY, carregada sem eco (na máquina local, read -rs ADEX_LLM_KEY; export ADEX_LLM_KEY; no notebook, pelo Vault, D3). No curl, a chave entra pela entrada padrão, para não aparecer na lista de processos: H(){ printf 'Authorization: Bearer %s\n' "$ADEX_LLM_KEY"; } e depois H | curl -H @- ... (curl 7.55 ou mais novo). Nunca usar curl -v, --trace, --trace-ascii, set -x, %env com a chave nem echo da chave. Nunca salvar uma saída que contenha a chave. Marcadores e textos são inventados e gerados na hora.


### D1 · Caminho de rede até o hub (notebook × local)

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* —

**Pergunta.** O notebook do OCI Data Science chega ao hub pelo endereço oficial, com certificado válido e sem desligar a verificação de segurança?

**Por que importa.** Decide o tipo de rede das sessões de notebook. A Default networking sai para a internet por um NAT gerenciado pela Oracle; a Custom networking usa um NAT próprio ou uma conexão privada com a rede corporativa. Decide também a base URL oficial da lib e se a lib precisa aceitar um pacote de certificados corporativo e um proxy. A doc da OCI diz que a configuração de rede não pode ser trocada quando a sessão é reativada; errar obriga a criar sessões novas. Sem este teste aprovado, nada do copiloto roda no ambiente real.

**Como medir.**

1) Anotar o tipo de rede da sessão: Default ou Custom. Se for Custom, anotar a VCN, a sub-rede e se a rota vai para um NAT gateway, um service gateway ou um DRG. 2) Em cada ambiente, resolver hub-gpus.usto.re e hub-gpus-lab.usto.re com Python (socket.getaddrinfo), porque nslookup e dig podem não existir na imagem do notebook. Anotar se os IPs são privados (10.x, 172.16–31.x, 192.168.x) ou públicos. 3) Listar só os NOMES das variáveis de proxy e de certificado (HTTPS_PROXY, NO_PROXY, SSL_CERT_FILE, REQUESTS_CA_BUNDLE etc.), nunca os valores, que podem conter senha. 4) Testar o TLS em duas pilhas. (a) openssl s_client, para ver emissor e validade; se houver proxy, passar -proxy host:porta, porque o openssl não lê HTTPS_PROXY. (b) A mesma pilha da lib: openai SDK sobre httpx, que usa o pacote certifi e não o repositório de certificados do sistema, sempre com a verificação ligada. 5) Chamar GET /v1/models 10 vezes por endereço e anotar o código HTTP e os tempos de DNS, TCP e TLS. 6) Interpretar o 401 da -lab: ele significa que um servidor respondeu e não reconheceu a chave (provavelmente outra instância do LiteLLM, com outra base de chaves). Não é falha de rede. Confirmar com o CCOE. 7) Entregar ao CCOE o x-litellm-call-id de uma chamada e pedir o IP de origem visto no ingress e se esse IP está numa lista de liberação. 8) Repetir num segundo notebook criado em outro dia, para ver se o IP de saída muda (o NAT da Default networking é gerenciado pela Oracle).

**Exemplo de requisição.**

```
python -c "import socket; print(sorted({a[4][0] for a in socket.getaddrinfo('hub-gpus.usto.re', 443)}))"
env | grep -ioE '^[a-z_]*(proxy|ca_bundle|cert_file|cert_dir)[a-z_]*='
openssl s_client -connect hub-gpus.usto.re:443 -servername hub-gpus.usto.re </dev/null 2>/dev/null | openssl x509 -noout -issuer -enddate
for i in $(seq 10); do H | curl -sS -o /dev/null -H @- -w '%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect}\n' "$ADEX_LLM_BASE/models"; done
```

**Métrica.** Código HTTP de /v1/models por endereço e por ambiente; IPs resolvidos (públicos ou privados); emissor do certificado e se ele valida no openssl e no openai SDK sem ajuste; nomes das variáveis de proxy e de certificado presentes; IP de origem visto pelo CCOE e se ele é estável.

**Critério de aprovação.** No notebook, 10 de 10 chamadas a /v1/models voltam com HTTP 200 pelo endereço oficial, com a verificação TLS ligada no openssl e no openai SDK (client.models.list()), sem verify=False. O CCOE confirma por escrito a base URL oficial e o que é a -lab. O IP de saída do notebook é estável e está liberado, ou o hub não exige liberação por IP. Se for preciso pacote de CA ou proxy, os valores necessários (nunca a chave) ficam documentados.

**Se falhar.** Se o DNS não resolve, ou se o IP é privado e inalcançável: criar as sessões com Custom networking numa VCN ligada à rede corporativa (DRG + FastConnect ou VPN), com encaminhamento de DNS no resolvedor da VCN. Se o hub bloqueia por IP: Custom networking com NAT gateway e IP público reservado, liberado pelo CCOE. Se o certificado é de CA interna: a lib aceita um ca_bundle na configuração e monta com ele um contexto TLS próprio, sem depender só de SSL_CERT_FILE. Se há proxy corporativo: HTTPS_PROXY e NO_PROXY ficam explícitos na configuração da lib. Se nada disso sair no prazo, o copiloto fica bloqueado para notebook; é critério de avançar ou parar da Fase 0.


### D2 · Latência do caminho (ida e volta)

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* D1

**Pergunta.** Quanto tempo cada chamada perde só no caminho (rede, proxy e ingress), sem contar o tempo que o modelo leva para pensar e escrever?

**Por que importa.** Define quantas chamadas curtas o copiloto pode fazer por etapa sem o dev sentir demora. Escolha de ferramenta e extração para a carta são chamadas curtas e frequentes. Também diz se vale juntar passos numa chamada só, se a lib precisa de um cliente HTTP único que reaproveite a conexão e se o notebook fica melhor ou pior que a máquina local.

**Como medir.**

1) Usar uma chamada mínima: 1 token de saída, raciocínio desligado, prompt de uma palavra. 2) Em cada ambiente, fazer 30 chamadas em sequência com um cliente Python único (conexão reaproveitada) e 30 com conexão nova (um curl por chamada). 3) Em cada chamada, guardar o tempo total no cliente, os tempos do curl (DNS, TCP, TLS e primeiro byte) e dois headers: x-litellm-response-duration-ms (tempo total dentro do proxy, da chegada do pedido até a saída da resposta) e x-litellm-overhead-duration-ms (o que o próprio LiteLLM acrescenta). 4) Tempo de caminho = tempo total no cliente − x-litellm-response-duration-ms. 5) Repetir em 3 janelas (manhã, começo da tarde e fim do dia) durante 2 dias. 6) Calcular p50 e p95 por ambiente juntando as janelas. Com 30 amostras, o p95 de uma janela é praticamente o segundo maior valor; ele serve só para comparar janelas.

**Exemplo de requisição.**

```
H | curl -sS -D - -o /dev/null -H @- -w 'total=%{time_total} ttfb=%{time_starttransfer}\n' "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d '{"model":"qwen-latest","messages":[{"role":"user","content":"ok"}],"max_tokens":1,"chat_template_kwargs":{"enable_thinking":false}}' | grep -iE 'x-litellm-(response|overhead)-duration-ms|total='
```

**Métrica.** p50 e p95 do tempo total da chamada mínima; p50 e p95 do tempo de caminho; x-litellm-overhead-duration-ms; diferença entre conexão nova e reaproveitada; diferença entre notebook e local.

**Critério de aprovação.** Metas do copiloto no notebook: p95 do tempo de caminho de até 300 ms com conexão reaproveitada; p95 da chamada mínima completa de até 2 s (esse tempo inclui a fila do vLLM, não só o caminho); nenhuma das 360 chamadas do notebook acima de 10 s. Se a máquina local for muito mais rápida que o notebook, o motivo tem que estar identificado (proxy ou rota).

**Se falhar.** A lib usa um cliente HTTP único por sessão, com keep-alive. Passos curtos são juntados numa chamada só (por exemplo, escolha de ferramenta junto com extração). As respostas que o dev lê vêm em streaming. Se a causa for o proxy corporativo, pedir para tirar o hub do proxy (NO_PROXY).


### D3 · Chave pelo OCI Vault com resource principal

*Quem:* nós + CCOE · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* D1

**Pergunta.** O notebook busca a chave do hub no cofre (OCI Vault) sozinho, sem arquivo de chave pessoal, e só as sessões certas conseguem lê-la?

**Por que importa.** Define o jeito padrão de a lib obter a chave dentro do OCI e é a base da rotação (D10). Evita chave colada no notebook, que vaza no .ipynb salvo, no git e nas cópias. A leitura pelo Vault fica registrada no OCI Audit, o que dá uma trilha de quem leu.

**Como medir.**

1) O admin do tenancy, junto com o CCOE, cria no Vault o segredo com a virtual key (nome sugerido: adex-llm-key). Cria também um segundo segredo com valor inventado (adex-teste-negado), para a prova negativa. 2) Criar o grupo dinâmico: ALL {resource.type='datasciencenotebooksession', resource.compartment.id='<ocid>'}. Atenção: ele inclui TODAS as sessões de notebook do compartimento, então todo dev desse compartimento lê a chave. 3) Criar a política mínima: Allow dynamic-group <grupo> to read secret-bundles in compartment <c> where target.secret.name = 'adex-llm-key'. Pela referência do Vault, read secret-bundles dá o GetSecretBundle, target.secret.name vale para secret bundles e não é preciso permissão sobre keys. 4) Esperar 15 min: a doc diz que o token do resource principal fica 15 min em cache. 5) No notebook, obter o signer de resource principal, ler o bundle, decodificar o base64 e guardar só em memória. Chamar /v1/models e imprimir só o código HTTP. 6) Provas negativas: (a) uma sessão de outro compartimento, fora do grupo, tem que ser negada; (b) ler adex-teste-negado no mesmo compartimento tem que ser negado. 7) Conferir no OCI Audit que cada leitura do segredo aparece. 8) Medir o tempo da leitura, para decidir o cache em memória. 9) Nunca colocar a chave nas variáveis de ambiente customizadas da sessão nem numa célula. A varredura de vestígios é o D14.

**Exemplo de requisição.**

```
import oci, base64, os
sig = oci.auth.signers.get_resource_principals_signer()
sc = oci.secrets.SecretsClient(config={}, signer=sig)
b = sc.get_secret_bundle(os.environ["ADEX_LLM_SECRET_OCID"])
key = base64.b64decode(b.data.secret_bundle_content.content).decode()  # só em memória; nunca print(key)
```

**Métrica.** Resultado de cada cenário (lido ou negado); tempo da leitura do segredo; registros no OCI Audit; política final que foi necessária.

**Critério de aprovação.** Três sessões diferentes leem o segredo sem ~/.oci/config e sem chave de API pessoal. As duas provas negativas são negadas. Cada leitura aparece no OCI Audit. A política mínima que funcionou está documentada. O D14, rodado depois, encontra 0 vestígios da chave.

**Se falhar.** Se a política não sair a tempo, a lib pede a chave com getpass no início da sessão e a guarda só em memória, sem gravar em arquivo nem imprimir. Na máquina local, a chave vem da variável de ambiente ADEX_LLM_KEY, carregada com read -rs. Se o Vault não responder numa sessão com Custom networking, adicionar um service gateway (ou uma rota de NAT) na sub-rede.


### D4 · Limites de taxa, 429 e backoff

*Quem:* nós + CCOE · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* D1

**Pergunta.** Quantas chamadas e quantos tokens por minuto a nossa chave aguenta, o que o hub responde quando passamos do limite e a nossa retentativa resolve sozinha?

**Por que importa.** Decide se a descoberta de dados, com cerca de 131 mil tokens de metadados, cabe numa chamada só. Pela doc do LiteLLM, o tpm_limit conta tokens totais (entrada + saída) por padrão e vale antes da chamada: o proxy reserva a entrada estimada mais o max_tokens pedido (ou um valor padrão, se o pedido não disser) e acerta a conta depois. Também decide quantas chamadas simultâneas o copiloto pode fazer (por exemplo, crítico junto com narrativa), se precisamos de uma chave por dev e qual é a regra única de retentativa da lib. E mostra um risco de troca silenciosa: pela doc, estourar um limite por modelo cai, por padrão, num fallback configurado, e a resposta volta 200 de outro modelo.

**Como medir.**

1) Pedir ao CCOE: rpm_limit, tpm_limit, token_rate_limit_type (input, output ou total), default_estimated_output_tokens, max_parallel_requests, model_rpm_limit e model_tpm_limit, max_budget, budget_duration, se disable_fallbacks_on_per_model_rate_limits está ligado e se a chave é compartilhada por todos os devs. 2) Numa chamada normal, anotar os headers: x-ratelimit-limit/remaining/reset-requests e -tokens (refletem a chave só se ela tiver limite; senão vêm do provedor, e o vLLM normalmente não manda); x-litellm-key-remaining-requests-<modelo> e x-litellm-key-remaining-tokens-<modelo> (só existem com limite por modelo); e retry-after, se vier. 3) Numa janela combinada com o CCOE, para não atrapalhar outros times, fazer uma rampa de chamadas mínimas com 1, 2, 4 e 8 simultâneas, 60 s cada, parando no primeiro 429. Conferir x-litellm-model-id em todas as respostas da rampa. 4) No 429, guardar o corpo (tipo e mensagem), os headers e o tempo até o hub voltar a aceitar. 5) Rodar o SDK com max_retries=0, porque o padrão dele é retentar 2 vezes, inclusive em 429 e 5xx, o que se somaria à regra da lib. Aplicar a regra da lib: espera exponencial com variação aleatória (1, 2, 4, 8 e 16 s, teto de 30 s, até 6 tentativas), respeitando o retry-after. Contar os sucessos. 6) Fazer uma chamada com cerca de 131 mil tokens de entrada inventados, primeiro com max_tokens 1024 e depois com 8192. Anotar se ela é aceita e, logo depois, fazer 1 chamada mínima a cada 10 s durante 60 s, para ver se a janela ficou consumida. 7) Separar três casos: 429 de taxa, budget_exceeded (a doc mostra 400 ou 429) e 503 de limitador indisponível (só aparece com fail_closed_rate_limit_enforcement).

**Exemplo de requisição.**

```
from openai import OpenAI; import os
c = OpenAI(base_url=os.environ["ADEX_LLM_BASE"], api_key=os.environ["ADEX_LLM_KEY"], max_retries=0)  # sem retentativa do SDK
r = c.chat.completions.with_raw_response.create(model="qwen-latest", messages=[{"role":"user","content":"ok"}], max_tokens=1, extra_body={"chat_template_kwargs":{"enable_thinking":False}})
print(r.status_code, {k:v for k,v in r.headers.items() if k.startswith(("x-ratelimit","x-litellm-key-remaining","retry-after","x-litellm-model-id","x-litellm-attempted"))})
```

**Métrica.** Limites declarados comparados com os observados; ponto da rampa em que aparece o 429; formato do erro; tempo até recuperar; percentual de sucesso com a regra da lib; consumo da janela causado pela descoberta, com cada max_tokens; presença de retry-after e dos x-ratelimit-*; model-id de cada resposta.

**Critério de aprovação.** Metas: (a) com a chave ociosa, a descoberta (cerca de 131 mil tokens de entrada, com max_tokens realista) passa sem 429, e o consumo da janela que ela causa está medido, para a lib programar as chamadas seguintes; (b) pelo menos 3 chamadas simultâneas por dev passam sem 429; (c) com a regra da lib, 100% das chamadas da rampa terminam com sucesso em até 60 s, sem o dev fazer nada; (d) o limite é por dev, ou o total compartilhado comporta o número de devs previsto; (e) 0 respostas da rampa vêm de um x-litellm-model-id diferente do homologado.

**Se falhar.** Pedir ao CCOE limites maiores, ou uma chave por dev ou por equipe, e disable_fallbacks_on_per_model_rate_limits ligado. A lib passa a ter uma fila interna com um número máximo de chamadas simultâneas. O max_tokens é sempre do tamanho da necessidade, nunca o teto por garantia, porque ele entra na reserva. A descoberta é dividida em lotes menores (por exemplo, por grupo de tabelas); cada lote é resumido e os resumos são juntados. Crítico e narrativa rodam um depois do outro.


### D5 · Timeouts em chamadas longas

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* D1, D4

**Pergunta.** Uma chamada demorada (raciocínio longo ou entrada enorme) é cortada por algum limite de tempo do caminho (proxy corporativo, balanceador ou LiteLLM), inclusive antes de o primeiro pedaço da resposta chegar?

**Por que importa.** Decide se as chamadas longas do copiloto (plano e crítico com raciocínio alto, descoberta com cerca de 131 mil tokens de entrada) precisam ser em streaming, se a descoberta precisa ser dividida e qual timeout a lib usa. Há duas armadilhas. Primeira: sem streaming, nenhum byte passa até o fim, e um balanceador corta por ociosidade. Segunda: com streaming, o stream_timeout do LiteLLM é o tempo máximo até o PRIMEIRO pedaço; enquanto o modelo lê 131 mil tokens ou espera na fila, nada é enviado, então o streaming não protege essa fase. Junto com o teto real de saída (se for 8.192), este teste diz quais tetos de raciocínio são viáveis.

**Como medir.**

1) Pedir ao CCOE os valores de timeout e stream_timeout de cada modelo (litellm_params), o timeout do router, o litellm_settings.request_timeout, o tempo de ociosidade e a duração máxima no balanceador ou ingress, e se há proxy corporativo no caminho. 2) Sem streaming: provocar chamadas cada vez mais longas (entrada grande inventada, saída no teto que valer, raciocínio ligado por padrão) e anotar a duração de cada uma. O objetivo é achar o corte por ociosidade (por exemplo, acima de 60, 120 e 300 s, se conseguirmos chegar lá). 3) Com streaming: medir o tempo até o primeiro pedaço (TTFT) com cerca de 131 mil tokens de entrada inventados, e comparar com o stream_timeout e com a ociosidade. 4) Usar no cliente um timeout explícito de 30 min e max_retries=0. O SDK, por padrão, usa 10 min e retenta 2 vezes, inclusive em timeouts, o que duplicaria chamadas longas no GPU. 5) Anotar onde a chamada foi cortada: HTTP 408, 502 ou 504, conexão fechada sem resposta, ou APITimeoutError do cliente. finish_reason=length não é corte do caminho, é o teto de tokens, e fica registrado à parte. 6) Comparar local com notebook, porque o proxy corporativo pode cortar só num dos dois. 7) Prova de 'aceitou e aplicou' do timeout por requisição do LiteLLM: mandar "timeout": 5 no corpo de uma chamada que levaria mais que isso. Ela tem que ser cortada em cerca de 5 s, com erro identificável.

**Exemplo de requisição.**

```
H | curl -sS -N --max-time 1800 -o /dev/null -H @- -w 'code=%{http_code} ttfb=%{time_starttransfer} total=%{time_total}\n' "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d @pedido_longo.json
# pedido_longo.json (inventado): {"model":"qwen-latest","stream":true,"max_tokens":8192,"messages":[{"role":"user","content":"<~131 mil tokens de metadados inventados> Resuma."}]}
# prova do timeout por requisição: mesmo pedido com "timeout":5 -> erro em ~5 s
```

**Métrica.** Maior duração que completa sem streaming, por ambiente; TTFT da descoberta, com streaming; código e instante de cada corte; resultado da prova de timeout por requisição; timeouts declarados pelo CCOE.

**Critério de aprovação.** Metas: (a) os timeouts declarados pelo CCOE (incluindo stream_timeout e a ociosidade do balanceador) são pelo menos o dobro da duração p95 da chamada mais longa do copiloto, quando os blocos de desempenho a medirem. Até lá, a maior chamada que conseguimos provocar completa 5 de 5 vezes em streaming, no notebook. (b) O TTFT da descoberta fica abaixo da metade do stream_timeout e da ociosidade. (c) O timeout por requisição é aplicado: a chamada é cortada em até timeout + 5 s. (d) Todo corte gera um erro identificável; nenhuma chamada fica pendurada; nenhuma chamada longa é reenviada automaticamente.

**Se falhar.** Streaming obrigatório nas chamadas longas. Se o TTFT da descoberta passar do limite, a descoberta vai em lotes (como no D4), ou pedimos ao CCOE um stream_timeout maior. Pedir ao CCOE uma ociosidade maior no balanceador. Reduzir os tetos de raciocínio. Dividir plano e crítico em chamadas menores. Configurar o timeout da lib um pouco abaixo do timeout do caminho, para a falha ser controlada.


### D6 · Custo, uso de tokens e headers de rastreio por chamada

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* D1

**Pergunta.** Cada resposta diz quanto custou, quantos tokens usou (inclusive de cache e de raciocínio), quanto tempo levou, qual deployment atendeu e qual é o número de protocolo da chamada?

**Por que importa.** Define a trilha de auditoria do copiloto. No handoff de cada estudo ficam registrados o id de cada chamada (para o CCOE achá-la do lado dele), o deployment que respondeu, os tokens e o custo total. Também alimenta a impressão digital (D11) e o controle de orçamento.

**Como medir.**

1) Fazer 20 chamadas variadas: com e sem raciocínio, com e sem streaming (em streaming, sempre com stream_options.include_usage=true, porque sem isso o usage não vem) e uma com erro proposital. 2) Guardar todos os headers x-litellm-*, x-ratelimit-* e llm_provider-*. 3) Conferir a presença destes headers: x-litellm-call-id; x-litellm-model-id (id do deployment); x-litellm-model-group (é o nome que o cliente pediu, não o modelo que respondeu); x-litellm-model-api-base; x-litellm-version; x-litellm-response-cost e as partes -input, -output, -cache-read, -cache-creation e -tool-usage (-reasoning é uma parte do -output e não entra na soma; pela doc, as partes só vêm em respostas sem streaming); x-litellm-key-spend; x-litellm-response-duration-ms; x-litellm-overhead-duration-ms; x-litellm-attempted-retries; x-litellm-max-retries (só quando houve retentativa); x-litellm-attempted-fallbacks; x-litellm-cache-key (só em resposta vinda do cache); e llm_provider-x-request-id (só se o vLLM rodar com --enable-request-id-headers). 4) No corpo, registrar usage.prompt_tokens, usage.completion_tokens, usage.prompt_tokens_details.cached_tokens (o vLLM só manda com --enable-prompt-tokens-details) e usage.completion_tokens_details.reasoning_tokens (a doc do vLLM não documenta; pode vir ausente). 5) Conferir a coerência do custo: (a) a soma das partes é igual ao total; (b) custo ≈ p_in × tokens de entrada + p_out × tokens de saída, resolvido com chamadas de tamanhos diferentes e comparado com a tabela do CCOE (o custo por token NÃO é constante, porque entrada e saída têm preços diferentes); (c) key-spend: anotar o valor, fazer N chamadas, esperar 2 min (a atualização do gasto pode ser assíncrona) e comparar o aumento com a soma dos custos, numa janela combinada com o CCOE, sem outros usuários da chave. 6) Na chamada com erro, ver se o corpo traz litellm_call_id (só com include_call_id_in_error_body ligado). 7) Comparar local com notebook, para ver se o proxy corporativo remove headers. 8) Perguntar ao CCOE a unidade e o significado do custo.

**Exemplo de requisição.**

```
H | curl -sS -D - -o /dev/null -H @- "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d '{"model":"qwen-latest","messages":[{"role":"user","content":"ok"}],"max_tokens":1}' | grep -iE '^(x-litellm|x-ratelimit|llm_provider)'
# streaming: {"stream":true,"stream_options":{"include_usage":true}} -> o último pedaço traz o usage
```

**Métrica.** Percentual de chamadas com cada header, com e sem streaming; presença de usage, cached_tokens e reasoning_tokens; diferença entre a soma das partes e o total; preços de entrada e de saída estimados; diferença entre o aumento de key-spend e a soma dos custos.

**Critério de aprovação.** x-litellm-call-id e x-litellm-model-id presentes em 100% das respostas com sucesso, inclusive em streaming. usage presente em 100% das respostas (em streaming, com include_usage). x-litellm-response-cost presente em 100% das respostas sem streaming, e a soma das partes (sem -reasoning) igual ao total, com diferença só de arredondamento. Na janela exclusiva, o aumento de key-spend bate com a soma dos custos, com diferença só de arredondamento. O CCOE confirma a unidade do custo. A presença ou ausência de cached_tokens e reasoning_tokens fica registrada (não reprova; só orienta a lib).

**Se falhar.** Sem call-id, a lib gera o próprio id e o envia num campo combinado com o CCOE (metadata ou header). Sem usage em streaming, pedir ao CCOE --enable-force-include-usage no vLLM, ou que o LiteLLM repasse o usage. Sem custo, a lib calcula pelo usage com a tabela de preços do CCOE. Sem reasoning_tokens, a lib registra só completion_tokens (raciocínio + resposta) e marca o raciocínio como não medido. Se o proxy cortar headers, pedir exceção ou usar só o corpo da resposta.


### D7 · Hub fora do ar ou degradado

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* D1, D4

**Pergunta.** Quando o hub cai, fica lento ou recusa a chave, o dev recebe uma mensagem clara em português e não perde o trabalho já feito?

**Por que importa.** Define o modo degradado do copiloto (o que continua funcionando só com Python e SQL), as mensagens de erro, como separar 'hub fora' de 'modelo fora' e a necessidade de um monitor de saúde barato.

**Como medir.**

1) Frente A, comportamento da lib contra um servidor falso local: um pequeno servidor compatível com OpenAI (http.server ou FastAPI), só com respostas inventadas, que devolve 401, modelo inexistente, 400 de contexto, 429 com e sem retry-after, 500, 502, 503, resposta lenta, corte no meio do streaming e JSON malformado. É reproduzível e não depende do hub nem do CCOE. 2) Frente B, hub real: endereço inexistente (DNS), porta sem resposta (timeout de conexão), timeout curto de leitura, 401 com uma chave inválida inventada (nunca a nossa), modelo inexistente e entrada maior que o contexto. 3) Sondas de saúde do LiteLLM. /health/liveliness não pede chave e só diz que o processo está vivo. /health/readiness não pede chave; por padrão só mostra o status e o banco, e volta 503 se o banco cair. Nenhuma das duas testa o modelo. /health/readiness/details pede autenticação e mostra versão e callbacks. /health com chave faz uma chamada real a cada modelo; não usar como rotina. 4) Observar por 10 dias úteis: sondar /health/liveliness a cada 10 min e fazer 1 chamada mínima por hora, registrando cada falha com horário. Rodar isso num Job agendado do OCI Data Science, ou numa sessão que fique ativa, com a mesma rede; se a sessão for desativada, a série fica com buracos. 5) Pedir ao CCOE as janelas de manutenção, a meta de disponibilidade (SLO), o canal de aviso de incidentes, como abrir chamado em caso de 500 e se include_call_id_in_error_body está ligado. 6) A partir do LiteLLM Proxy v1.85.0, as flags mock_testing_* são removidas dos pedidos ao proxy (confirmado na doc). Uma falha real do lado do servidor só o CCOE provoca, num ambiente de teste.

**Exemplo de requisição.**

```bash
curl -sS -m 5 "${ADEX_LLM_BASE%/v1}/health/liveliness"
curl -sS -m 5 "${ADEX_LLM_BASE%/v1}/health/readiness"
# timeout forçado: OpenAI(base_url=..., api_key=os.environ["ADEX_LLM_KEY"], timeout=0.5, max_retries=0) -> APITimeoutError
# servidor falso local (dados inventados): responde 429 com retry-after: 3 e depois 200; corta o streaming no 5º pedaço
```

**Métrica.** Tabela falha → código ou exceção → mensagem mostrada ao dev → retentou ou não; tempo até detectar cada falha; disponibilidade observada em percentual; duração das quedas.

**Critério de aprovação.** Frente A: 100% dos cenários viram mensagem clara em português, com o estado do estudo salvo; zero retentativas em 400, 401, 403 e 404; retentativa limitada em 429 e 5xx, respeitando retry-after; um corte no meio do streaming nunca é gravado como resposta completa. Frente B: hub inalcançável detectado em até 15 s (meta); a disponibilidade observada no horário comercial é compatível com a meta acordada com o CCOE (sugestão: pelo menos 99%).

**Se falhar.** Modo degradado: as etapas em Python e SQL continuam, e as partes de IA ficam marcadas como pendentes para rodar depois. Fila de retentativa. Os cenários do servidor falso viram teste automático da lib. Se as quedas forem frequentes, avaliar o gpt-oss-20b como reserva só para tarefas pequenas (D12) e pedir janelas de manutenção com aviso.


### D8 · O que o hub registra: teste do canário

*Quem:* nós + CCOE · *Onde:* notebook OCI · *Esforço:* M · *Depende de:* D1, D6

**Pergunta.** O que fica gravado no hub das perguntas e respostas do copiloto, em qual camada, por quanto tempo e quem consegue ler?

**Por que importa.** Decide o que o copiloto pode mandar no prompt, se toda chamada deve ir com "no-log" ou com redação de mensagens, e se precisamos pedir ao CCOE uma configuração que não grave conteúdo. A doc do LiteLLM só diz que o "no-log" deixa de registrar nos callbacks; ela não fala dos spend logs, e o no-log não alcança os logs do vLLM nem os do ingress. Por isso a resposta tem que ser medida camada por camada. É pré-requisito da decisão sobre LGPD e classificação (D9).

**Como medir.**

1) Questionário ao CCOE, com resposta por escrito. LiteLLM: store_prompts_in_spend_logs (desligado por padrão), disable_spend_logs, disable_error_logs, maximum_spend_logs_retention_period e maximum_spend_logs_retention_interval (sem um prazo configurado, a limpeza não roda e os registros ficam para sempre), turn_off_message_logging, global_disable_no_log_param (se estiver ligado, o "no-log" é ignorado), redact_user_api_key_info, callbacks ativos (globais, por equipe e por chave em metadata.logging) e onde guardam os dados, cache de respostas (Redis ou semântico, que guarda respostas) e guardrails ativos (por exemplo, mascaramento de PII que envia o texto a outro serviço). vLLM: --enable-log-requests (em nível DEBUG, grava o prompt), --enable-log-outputs (grava as respostas), --max-log-len (sem limite por padrão), VLLM_REQUEST_BODY_LOG_LEVEL e o nível de log. Ingress e balanceador: gravam o corpo? Há backups? Acesso: quem tem proxy_admin, proxy_admin_viewer ou permissão de /spend/logs na equipe. Local: em que país e datacenter fica o hub. 2) Verificação do nosso lado: POST /key/health com a nossa chave (lista os callbacks da chave) e GET /health/readiness/details, se a chave puder. Os resultados têm que bater com o questionário. 3) Canário no notebook, com marcadores aleatórios gerados na hora e nunca reaproveitados, em 4 cenários: (a) chamada normal; (b) com "no-log": true; (c) com o header x-litellm-enable-message-redaction: true (beta, pela doc); (d) erro proposital (entrada maior que o contexto, contendo o marcador), para ver os logs de erro. Cada chamada pede ao modelo que repita um segundo marcador na resposta. 4) Entregar ao CCOE só os x-litellm-call-id e os marcadores. O CCOE procura cada marcador em cada camada (spend logs, cada callback, cache de respostas, logs do vLLM, logs do ingress) depois de 1 h. A busca se repete depois do prazo de retenção declarado, como acompanhamento.

**Exemplo de requisição.**

```
import os, secrets, requests
p, r = "ADEX-CANARIO-P-" + secrets.token_hex(6), "ADEX-CANARIO-R-" + secrets.token_hex(6)
x = requests.post(os.environ["ADEX_LLM_BASE"] + "/chat/completions", timeout=60, headers={"Authorization": "Bearer " + os.environ["ADEX_LLM_KEY"]}, json={"model":"qwen-latest","no-log":True,"max_tokens":20,"chat_template_kwargs":{"enable_thinking":False},"messages":[{"role":"user","content":f"{p}. Responda só: {r}"}]})
print(x.status_code, x.headers.get("x-litellm-call-id"), p, r)  # nunca imprimir headers do pedido
```

**Métrica.** Para cada cenário e cada camada: marcador de prompt e marcador de resposta encontrados ou não; retenção declarada comparada com a observada; lista de papéis e pessoas com acesso; callbacks declarados comparados com os vistos em /key/health.

**Critério de aprovação.** O CCOE responde por escrito a 100% do questionário. Cada camada tem um sim ou não medido pelo canário, sem contradizer o questionário. No cenário que o copiloto vai usar, (b) ou (c), nenhum marcador aparece em camada de conteúdo do LiteLLM (callbacks, spend logs, cache). Se o vLLM ou o ingress gravarem conteúdo, a retenção e os acessos estão documentados e aceitos pela governança/DPO. Existe um prazo de retenção definido, e não 'para sempre'. A releitura depois do prazo é acompanhamento e não bloqueia a Fase 0.

**Se falhar.** Se o "no-log" ou a redação não forem respeitados, pedir ao CCOE turn_off_message_logging para a chave ou equipe do copiloto, ou uma chave dedicada sem callbacks e sem cache. Se o vLLM gravar conteúdo, pedir para desligar --enable-log-outputs e o DEBUG no modelo do copiloto. Reduzir o que vai no prompt: nomes de tabela e coluna trocados por apelidos e nenhum valor de categoria. Se o conteúdo for gravado com retenção longa e acesso amplo, só metadados não sensíveis vão ao hub até uma nova decisão.


### D9 · Dados confidenciais agregados: o que pode ir (LGPD e classificação)

*Quem:* nós + CCOE · *Onde:* local · *Esforço:* M · *Depende de:* D8

**Pergunta.** Que tipo de informação o copiloto pode mandar ao hub (só metadados, também números agregados, imagens de gráficos) e com que cuidados?

**Por que importa.** Fecha a regra de saída de dados da lib: o que entra no prompt em cada momento (entrevista, carta, descoberta de dados, narrativa, crítico e visão de PNG). A IA já não vê linhas individuais nem PII. Mas agregados com grupos muito pequenos podem identificar pessoas, e aí voltam a contar como dado pessoal pela LGPD: dado anonimizado só deixa de ser pessoal se a anonimização não puder ser revertida com esforço razoável. Uma célula suprimida também pode ser recalculada pela diferença entre o total e as células visíveis. Informação confidencial da empresa segue regra própria, mesmo sem dado pessoal. Se o hub ficar fora do Brasil, entra a regra de transferência internacional.

**Como medir.**

1) Montar 5 amostras de carga INVENTADAS, uma por momento (enquadramento, descoberta com metadados, narrativa com marcadores, crítico e PNG de gráfico), marcando a classe de cada campo: metadado técnico, agregado, texto do dev ou imagem (incluindo títulos, eixos e legendas). 2) Incluir de propósito dois casos de risco: uma célula pequena (por exemplo, contagem 3 numa combinação de UF com faixa de idade) e um total que permite recalcular essa célula por diferença. 3) Levar as amostras ao CCOE e à governança/DPO, junto com o resultado do D8 (o que é gravado e por quanto tempo) e o país e datacenter do hub. 4) Para cada classe, obter decisão por escrito: permitido, permitido com condições (quais) ou proibido. Obter também a contagem mínima por grupo (k), a regra de supressão complementar (que células extras esconder para impedir o recálculo) e se haverá arredondamento. 5) Perguntar se o hub é compartilhado com outros times e se o cache de prefixo do vLLM fica isolado. O vLLM aceita cache_salt por requisição para isso; se o LiteLLM o repassa, o D13 mostra.

**Exemplo de requisição.**

```
Amostra inventada (não é chamada à API): {"momento":"narrativa","tabela":"base_ficticia","agregados":{"SP|18-24":{"n":1532,"taxa":"{{M1}}"},"AC|65+":{"n":3,"taxa":"{{M2}}"},"AC|total":{"n":40}}}  -> n=3 é o caso de risco; se as outras células do AC somarem 37, a célula suprimida se recalcula pelo total
```

**Métrica.** Decisão por classe de dado e por momento; valor de k mínimo; regra de supressão complementar e de arredondamento; regra para imagens; condições impostas.

**Critério de aprovação.** Nenhuma classe fica sem resposta. A decisão está registrada por escrito (e-mail ou ata) pelo CCOE e pela governança/DPO. O k mínimo e a regra de supressão complementar estão definidos. A regra para imagens de gráficos, incluindo os rótulos, está definida. Se o hub ficar fora do Brasil, a base para a transferência internacional está registrada.

**Se falhar.** Enquanto não houver decisão, o copiloto manda só metadados técnicos (nomes de colunas, tipos e contagens totais) e marcadores. Não manda valores de categorias nem agregados por grupo. A visão de gráfico fica desligada.


### D10 · Escopo da chave: 2 modelos, rotação e revogação

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* D1, D3

**Pergunta.** A chave só faz o que deveria (os 2 modelos, sem nenhuma administração), e dá para trocá-la ou cortá-la sem quebrar o copiloto?

**Por que importa.** Define a lista de modelos permitidos na lib, a rotina de rotação pelo Vault e o procedimento se a chave vazar. Também ensina a lib a separar três erros parecidos: nome de modelo inexistente, modelo fora do escopo da chave e chave inválida.

**Como medir.**

1) Chamar GET /v1/models e anotar os ids. O esperado é exatamente qwen-latest e openai/gpt-oss-20b; pela doc, o campo models da chave esconde os demais dessa lista e bloqueia as chamadas a eles. 2) Fazer dois testes distintos: (a) um nome que não existe no hub; (b) o nome de um modelo que EXISTE no hub mas está fora da nossa chave (o CCOE informa o nome). Anotar código e mensagem de cada um; só o (b) prova o escopo. A doc não fixa o código; um exemplo de equipe mostra até 500. 3) Rotas de administração: do nosso lado, só rotas de leitura (GET /key/list, /user/list, /spend/logs), que têm que ser negadas ou mostrar só o que é nosso. NÃO chamar /key/generate, /model/new nem outras rotas que criam ou mudam algo: se a chave tiver permissão (por exemplo, internal_user com permissão de equipe), a chamada criaria uma chave ou um modelo de verdade. O CCOE confirma por escrito o papel da chave, os allowed_routes e as permissões de equipe. Chamar /key/info e /model/info e anotar o que aparece (validade, modelos, limites, gasto). 4) Ensaio de rotação com o CCOE. /key/{key}/regenerate com grace_period é recurso Enterprise do LiteLLM. Sem licença, a rotação é: o CCOE gera uma chave nova, nós gravamos uma nova versão do segredo no Vault, e o CCOE apaga a antiga depois de um prazo combinado. Nos dois caminhos, confirmar que um notebook aberto continua funcionando: a lib relê o segredo uma única vez no primeiro 401 e, se o 401 continuar, para e avisa, sem laço. A chave nunca vai em URL nem em comando do nosso lado. 5) Ensaio de revogação: o CCOE faz POST /key/block (exige master key). Medir em quantos segundos as chamadas passam a falhar (pode haver cache da chave no LiteLLM) e qual é a mensagem. Depois, o CCOE faz /key/unblock. 6) Perguntar a validade da chave (duration e expires) e se há rotação automática (auto_rotate e rotation_interval, que exigem LITELLM_KEY_ROTATION_ENABLED).

**Exemplo de requisição.**

```
H | curl -sS -H @- "$ADEX_LLM_BASE/models" | python -c "import sys,json; print([m['id'] for m in json.load(sys.stdin)['data']])"
H | curl -sS -H @- -w ' code=%{http_code}\n' "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d '{"model":"modelo-inexistente-x","messages":[{"role":"user","content":"ok"}],"max_tokens":1}'
# repetir com o nome de um modelo que existe no hub e está fora da nossa chave (o CCOE informa)
```

**Métrica.** Ids listados; código e mensagem para nome inexistente e para modelo fora do escopo; rotas de leitura negadas ou permitidas; papel e permissões declarados; falhas que o dev vê durante a rotação; tempo até o bloqueio valer.

**Critério de aprovação.** Exatamente 2 modelos listados. O modelo existente fora do escopo é negado, com uma mensagem que a lib consegue distinguir de 'nome inexistente' e de 'chave inválida'. As rotas de leitura de administração são negadas ou mostram só o que é nosso, e o CCOE confirma por escrito que a chave não cria nem altera nada. A rotação tem 0 falhas para o dev. O bloqueio vale em até 5 min (meta) e gera mensagem clara. Validade e calendário de rotação estão documentados.

**Se falhar.** Se a chave enxerga mais modelos ou rotas, pedir ao CCOE para restringir (campo models e allowed_routes da chave); a lib usa uma lista fixa de modelos permitidos. Sem grace_period, a rotação acontece numa janela combinada. Se /key/info for negado, o CCOE informa validade e limites por escrito.


### D11 · Impressão digital do modelo (troca silenciosa)

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* G · *Depende de:* D6, D10, D13

**Pergunta.** Como saber, sempre que o copiloto abre, que qwen-latest ainda é o mesmo modelo, com o mesmo template e a mesma configuração que homologamos?

**Por que importa.** O apelido latest pode passar a apontar para outro modelo sem aviso, e o CCOE pode mudar o template ou os padrões do servidor (por exemplo, --default-chat-template-kwargs) sem mudar o nome. Se isso acontecer, os perfis calibrados na Fase 0 (reasoning_effort, tetos, amostragem), o parser de tools e o crítico deixam de valer. Este teste decide três coisas: a verificação automática no início da sessão, o bloqueio ou alerta quando algo muda e o pedido ao CCOE de um apelido com versão fixa.

**Como medir.**

0) Pré-condições. O D13 diz quais parâmetros chegam ao vLLM e quais campos voltam na resposta. Toda chamada de impressão digital vai com "cache": {"no-cache": true, "no-store": true} e confere que x-litellm-cache-key não veio; se o LiteLLM tiver cache de respostas, um prompt fixo com temperatura 0 voltaria do cache e esconderia a troca. 1) Impressão digital de referência em camadas, da mais fraca à mais forte. (a) LiteLLM: x-litellm-model-id, x-litellm-model-api-base, x-litellm-version e x-litellm-attempted-fallbacks. Mudam quando o CCOE muda a configuração do LiteLLM, mas não percebem uma troca feita só no servidor vLLM atrás do mesmo endereço. (b) Campo model e system_fingerprint da resposta: model pode ser só o --served-model-name escolhido pelo CCOE, e o vLLM costuma deixar system_fingerprint vazio. São fracos. (c) GET /v1/models e, se a chave puder, /model/info. (d) usage.prompt_tokens de 3 mensagens fixas, que muda se o tokenizer ou o template mudarem. (e) prompt_token_ids, pedidos com return_token_ids=true (já existe em versões como a 0.13 do vLLM): guardar o sha256 da lista, que capta o template e o tokenizer de forma exata. return_prompt_text=true só existe na doc mais recente; usar se o hub tiver. Conferir que o LiteLLM devolve esses campos. (f) Sonda de contexto: pedir um max_tokens absurdo e ler o limite na mensagem de erro. É barato, porque não gera texto, e o texto do erro também muda com a versão do vLLM. (g) Comportamento do template: se o raciocínio vem ligado por padrão; em qual campo ele vem (reasoning no vLLM recente, reasoning_content no LiteLLM ou em vLLM antigo); e o que acontece com reasoning_effort "high". Registrar o que vier (400 ou 200), sem esperar 400: a doc do vLLM mapeia low/medium/high para enable_thinking, e o CCOE pode ajustar o template. (h) top_logprobs (até 5; o vLLM limita em --max-logprobs, que por padrão é 20) do primeiro token, com raciocínio desligado, temperatura 0 e prompt fixo. Comparar o token mais provável e o conjunto do top-5, com uma tolerância numérica tirada da linha de base, porque o vLLM não garante números idênticos entre lotes. 2) Controle negativo: a mesma impressão digital no openai/gpt-oss-20b tem que dar modelo diferente. 3) Controle positivo (sensibilidade): repetir (d) e (e) mudando só chat_template_kwargs (enable_thinking verdadeiro ou falso) e confirmar que as camadas mudam. Isso prova que elas percebem uma mudança de template, e não só a troca por um modelo inteiro diferente. 4) Rodar 2 vezes por dia durante 10 dias úteis, para ver o que é estável e quanto os logprobs variam naturalmente, e definir a tolerância. 5) Definir uma versão curta para o início da sessão (até 2 chamadas: a de impressão digital e a sonda de contexto) e uma versão completa (diária ou semanal). 6) Pedir ao CCOE um apelido fixo por versão, além do qwen-latest, o campo model_info.version e aviso prévio de troca.

**Exemplo de requisição.**

```
H | curl -sS -D - -H @- "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d '{"model":"qwen-latest","messages":[{"role":"user","content":"Diga apenas: azul"}],"max_tokens":3,"temperature":0,"logprobs":true,"top_logprobs":5,"return_token_ids":true,"cache":{"no-cache":true,"no-store":true},"chat_template_kwargs":{"enable_thinking":false}}'
# guardar: x-litellm-model-id, x-litellm-version, ausência de x-litellm-cache-key, model, usage.prompt_tokens, sha256(prompt_token_ids), top_logprobs do 1º token
# sonda de contexto: mesmo pedido com "max_tokens":400000 -> ler o limite na mensagem de erro
```

**Métrica.** Campos estáveis ao longo de 10 dias; variação dos logprobs; acerto nos controles negativo e positivo; camadas que o LiteLLM repassa; custo e tempo da verificação curta.

**Critério de aprovação.** A impressão digital distingue gpt-oss-20b de qwen-latest em 100% das rodadas. O controle positivo é detectado em 100% das rodadas. Pelo menos uma camada forte, (d), (e) ou (h), é repassada e estável. 0 alarmes sem mudança real confirmada pelo CCOE em 10 dias úteis. Verificação curta com até 2 chamadas e até 3 s (meta). O CCOE responde por escrito se oferece apelido fixo e aviso de troca. Para a Fase 0 avançar, basta uma das duas: apelido fixo com aviso formal do CCOE, ou impressão digital aprovada.

**Se falhar.** Sem apelido fixo, a lib guarda a impressão digital homologada. Se ela mudar, o dev é avisado e os resultados ficam marcados como 'modelo não homologado' até rodar uma mini-bateria de re-homologação (parte dos blocos de qualidade). Se nenhuma camada forte for repassada ou estável, dependemos do aviso formal do CCOE (changelog) e rodamos a versão completa toda semana.


### D12 · Papel do gpt-oss-20b e fallbacks do servidor

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* D4, D7, D11, D13

**Pergunta.** O gpt-oss-20b serve de reserva quando o qwen-latest cair? Para quais tarefas, se ele só tem 8.192 tokens de contexto? E o hub troca de modelo sozinho em algum caso?

**Por que importa.** Decide se o copiloto tem reserva, e para quais momentos, ou se entra direto em modo degradado. Também protege contra uma troca silenciosa feita pelo próprio hub. Pela doc do LiteLLM, há três caminhos para isso: fallbacks ou default_fallbacks configurados; context_window_fallbacks, quando enable_pre_call_checks está ligado; e o fallback por estouro de limite por modelo, que é o padrão quando há fallback configurado. Nos três, a resposta volta 200 de outro modelo.

**Como medir.**

1) Confirmar o contexto com a sonda barata (max_tokens absurdo e limite lido na mensagem de erro). 2) Listar os momentos que cabem em 8.192 tokens, somando entrada, raciocínio e saída. Provavelmente cabem escolha de ferramenta ou bloco, extração curta e narrativa curta com marcadores. Não cabem a descoberta (cerca de 131 mil) nem o plano (cerca de 38 mil). 3) Para cada momento que cabe, rodar 20 tarefas inventadas com json_schema e tools e medir o percentual de JSON válido e de tool calls corretas. Usar o D13 para saber quais parâmetros chegam: o reasoning_effort do gpt-oss (low, medium, high) e o chat_template_kwargs do Qwen, que deve ser ignorado ou dar erro. 4) Perguntar ao CCOE se o gpt-oss-20b roda em GPU ou servidor separado do qwen (se cair junto, não serve de reserva), quais fallbacks, default_fallbacks e context_window_fallbacks existem, se disable_fallbacks_on_per_model_rate_limits está ligado e se o contexto pode aumentar. 5) Em todas as medições dos outros blocos, conferir x-litellm-model-id e x-litellm-attempted-fallbacks. x-litellm-model-group não serve para isso, porque mostra o nome que o cliente pediu.

**Exemplo de requisição.**

```
H | curl -sS -H @- "$ADEX_LLM_BASE/chat/completions" -H 'Content-Type: application/json' -d '{"model":"openai/gpt-oss-20b","max_tokens":300,"reasoning_effort":"low","messages":[{"role":"user","content":"Escolha o bloco para: comparar 2 grupos inventados"}],"response_format":{"type":"json_schema","json_schema":{"name":"escolha","schema":{"type":"object","properties":{"bloco":{"type":"string"}},"required":["bloco"]}}}}'
```

**Métrica.** Momentos que cabem em 8K; percentual de JSON válido e de tool calls corretas por momento; infraestrutura independente (sim ou não); fallbacks configurados por tipo (sim ou não); respostas vindas de deployment não homologado.

**Critério de aprovação.** A reserva só é aprovada para os momentos com pelo menos 95% de JSON válido e 90% de tool calls corretas (metas), e só se a infraestrutura for independente. Os fallbacks do servidor para qwen-latest estão desligados, ou ficam sempre visíveis em x-litellm-model-id e x-litellm-attempted-fallbacks, com 0 trocas silenciosas em todas as medições.

**Se falhar.** O gpt-oss-20b fica fora do copiloto, ou serve só para testar conectividade. Uma queda do qwen leva ao modo degradado (D7). Pedir ao CCOE para desligar os fallbacks automáticos de qwen-latest e ligar disable_fallbacks_on_per_model_rate_limits.


### D13 · Repasse de parâmetros: chegou ao modelo ou foi descartado?

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* D1

**Pergunta.** Os parâmetros de que o copiloto depende chegam ao vLLM e têm efeito, ou o LiteLLM (ou um guardrail) os aceita e descarta sem avisar? E os campos extras da resposta voltam até nós?

**Por que importa.** O LiteLLM repassa ao provedor os parâmetros que não são da OpenAI, mas pode removê-los (drop_params, additional_drop_params), injetar valores padrão por modelo em litellm_params e alterar o pedido com guardrails. Um parâmetro descartado não dá erro: a resposta vem 200 e o perfil calibrado simplesmente não vale. Além disso, a resposta passa por uma conversão do LiteLLM, que pode tirar campos que o vLLM mandou (por exemplo, prompt_token_ids). Os perfis do copiloto, o D11 e o D12 dependem deste teste. Se outro bloco já calibra os perfis, este teste fica só com a parte 'chegou ou foi descartado'.

**Como medir.**

1) Listar os parâmetros que o copiloto usa: chat_template_kwargs.enable_thinking, reasoning_effort, temperature, top_p, top_k, min_p, presence_penalty, max_tokens, response_format json_schema, tools e tool_choice, logprobs e top_logprobs, return_token_ids, cache_salt, stream_options.include_usage, timeout (do LiteLLM), "no-log" e cache. 2) Para cada um, duas sondas. (a) Efeito observável, com um valor que muda algo verificável: enable_thinking=false faz o campo de raciocínio sumir ou vir vazio; max_tokens=1 dá completion_tokens=1 e finish_reason=length; top_k=1 (ou min_p=1.0) com temperature=1.0 dá 5 saídas idênticas, enquanto sem ele as 5 variam; json_schema com enum de um único valor dá uma saída igual a esse valor; logprobs faz o campo logprobs aparecer; return_token_ids faz o campo prompt_token_ids aparecer; cache_salt: com o mesmo prompt de mais de 5 mil tokens repetido, cached_tokens fica acima de 0 sem salt novo e em 0 com salt novo (só se o hub expõe cached_tokens). (b) Valor inválido, por exemplo top_k="abc", min_p=5 ou um schema inválido: tem que voltar 400. Se voltar 200, o parâmetro foi descartado antes de chegar a quem valida. 3) Perguntar ao CCOE: drop_params, additional_drop_params, allowed_openai_params, valores fixos em litellm_params do modelo (e quem vence, o pedido ou a configuração) e guardrails ativos. 4) Repetir no gpt-oss-20b só para os parâmetros que o D12 usar.

**Exemplo de requisição.**

```
# sonda de efeito (top_k chega?): rodar 5 vezes e comparar os textos
{"model":"qwen-latest","temperature":1.0,"top_k":1,"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"Invente um nome de bairro"}]}
# sonda de valor inválido: tem que voltar 400
{"model":"qwen-latest","top_k":"abc","max_tokens":1,"messages":[{"role":"user","content":"ok"}]}
```

**Métrica.** Tabela parâmetro → efeito visto (sim ou não) → valor inválido dá 400 (sim ou não) → campo extra volta na resposta (sim ou não); configuração declarada pelo CCOE.

**Critério de aprovação.** 100% dos parâmetros marcados como essenciais nos perfis têm efeito confirmado. Nenhum valor inválido de parâmetro essencial volta 200. Os campos de resposta usados pelo D11 estão presentes. O CCOE confirma por escrito drop_params, additional_drop_params, os padrões em litellm_params e os guardrails.

**Se falhar.** Um parâmetro descartado sai do perfil, e o perfil é recalibrado sem ele; ou pedimos ao CCOE que o libere (allowed_openai_params) ou o tire de additional_drop_params. Se um campo da resposta é removido, a camada correspondente do D11 sai. Se um guardrail altera o prompt, o comportamento dele vira parte da homologação.


### D14 · Vazamento local: chave e conteúdo em logs, erros e no notebook

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* —

**Pergunta.** A chave ou o conteúdo das chamadas ficam gravados do nosso lado: no .ipynb salvo, em logs do SDK, em mensagens de erro, no histórico do terminal ou na configuração da sessão?

**Por que importa.** O hub não é o único lugar que guarda coisas. Uma exceção impressa numa célula, um log em DEBUG, um %env (o Jupyter mostra o valor na saída da célula) ou uma variável de ambiente da sessão do OCI (fica gravada na configuração da sessão, visível a quem a lê no Console ou pela API) podem gravar a chave num arquivo que vai para o git ou numa tela que outros veem. Este teste define as proteções da lib: filtro de log, mensagens de erro sem headers do pedido e o guia do que o dev nunca deve fazer.

**Como medir.**

1) No notebook e na máquina local, rodar um roteiro com dados inventados: chamadas normais, streaming e erros provocados (400 de contexto, timeout, modelo inexistente e 401 com uma chave inválida inventada, nunca a nossa), com OPENAI_LOG=debug e logging em DEBUG para openai, httpx e httpcore. Imprimir as exceções completas (traceback e repr) numa célula e salvar o notebook. 2) Fazer a varredura em Python, com a chave só em memória (nunca digitada num comando): procurar a chave inteira, a chave sem o prefixo e a chave em base64 no home, em /tmp, em .ipynb_checkpoints, em ~/.local/share/jupyter, nos históricos do shell e do Python, nos logs da sessão e nas saídas do notebook. Procurar também um marcador inventado que foi no prompt, para ver se a lib ou o SDK gravam conteúdo em disco. 3) Conferir as práticas proibidas: %env ADEX_LLM_KEY=...; os.environ como última linha de uma célula (mostra tudo); export da chave digitado no terminal (vai para o histórico; usar read -rs); a chave nas variáveis de ambiente customizadas da sessão do OCI; curl -v ou --trace. 4) Repetir depois do D3, com a chave vinda do Vault.

**Exemplo de requisição.**

```
import os, base64, pathlib
k = os.environ["ADEX_LLM_KEY"]
alvos = [a.encode() for a in {k, k.removeprefix("sk-"), base64.b64encode(k.encode()).decode()}]
def tem(p):
    try: return p.is_file() and p.stat().st_size < 50_000_000 and any(a in p.read_bytes() for a in alvos)
    except OSError: return False
achados = [str(p) for r in (pathlib.Path.home(), pathlib.Path("/tmp")) for p in r.rglob("*") if tem(p)]
print(len(achados), achados)  # só caminhos; nunca o conteúdo
```

**Métrica.** Ocorrências da chave por local; ocorrências do marcador de conteúdo em disco; práticas proibidas encontradas.

**Critério de aprovação.** 0 ocorrências da chave em qualquer arquivo, saída de célula ou log, nos dois ambientes, inclusive com DEBUG ligado e nos erros provocados. 0 ocorrências do marcador de conteúdo em disco, fora do que a lib grava de propósito e deixa documentado.

**Se falhar.** A lib instala um filtro de log que troca a chave por ***; as mensagens de erro da lib não incluem headers do pedido; a lib deixa o DEBUG do httpx e do httpcore desligado por padrão. O guia de onboarding proíbe %env, export digitado e variável de ambiente da sessão. Se o SDK gravar a chave, fixar uma versão segura ou envolver o cliente.


### Perguntas ao CCOE · D

- **Qual é a base URL oficial: hub-gpus.usto.re ou hub-gpus-lab.usto.re? O 401 da -lab sugere outra instância do LiteLLM, com outra base de chaves. Ela é outro ambiente, foi desligada ou exige outra chave?** — Em 16/10 a -lab deu 401 e só a outra funcionou. A lib precisa de um endereço oficial, configurável e sem valor fixo no código (D1).
- **O hub está exposto na internet com liberação por IP, ou só na rede interna (FastConnect ou VPN)? Se houver liberação por IP, aceitam o IP de saída da Default networking do OCI, gerenciado pela Oracle, ou exigem um NAT nosso com IP reservado?** — Define o tipo de rede das sessões de notebook, que não pode ser trocado ao reativar a sessão (D1).
- **O certificado TLS do hub é emitido por uma CA pública ou interna? Se for interna, qual é a cadeia oficial? Há proxy corporativo no caminho a partir do OCI?** — Sem a cadeia, o dev cairia em verify=False, o que é proibido. A lib precisa de um ca_bundle e de configuração de proxy (D1).
- **Quais são os valores de rpm_limit, tpm_limit, token_rate_limit_type, default_estimated_output_tokens, max_parallel_requests, model_rpm_limit/model_tpm_limit, max_budget e budget_duration da nossa chave e dos modelos? disable_fallbacks_on_per_model_rate_limits está ligado? A chave é compartilhada por todos os devs?** — O LiteLLM reserva, antes da chamada, a entrada estimada mais o max_tokens. Uma descoberta de cerca de 131 mil tokens pode ser recusada sozinha, e estourar um limite por modelo pode cair em fallback com 200 (D4, D12).
- **Quais são os timeouts do LiteLLM (timeout e stream_timeout por modelo, timeout do router, request_timeout) e o tempo de ociosidade e a duração máxima do balanceador ou ingress? O teto de max_tokens de 8.192 é imposto no LiteLLM ou no vLLM, e pode subir? Quando o cliente desconecta, a geração é abortada no vLLM?** — O stream_timeout vale até o primeiro pedaço da resposta, e a descoberta lê cerca de 131 mil tokens antes de responder. Define o streaming, o tamanho dos lotes e se tetos de raciocínio de 12K ou 16K são possíveis (D5).
- **O que o hub grava? store_prompts_in_spend_logs, disable_spend_logs, disable_error_logs, turn_off_message_logging, callbacks ativos (globais, por equipe e por chave), cache de respostas do LiteLLM, guardrails, flags do vLLM (--enable-log-requests, --enable-log-outputs, --max-log-len, nível de log, VLLM_REQUEST_BODY_LOG_LEVEL), logs do ingress com corpo e backups. Qual é a retenção (maximum_spend_logs_retention_period)? Quem tem proxy_admin, proxy_admin_viewer ou permissão de /spend/logs? Em que país e datacenter o hub fica?** — Sem um prazo de retenção configurado, os spend logs ficam para sempre. Sem essas respostas, não há decisão de LGPD nem de classificação de dados (D8, D9).
- **O parâmetro "no-log" na requisição é respeitado (global_disable_no_log_param desligado)? O header x-litellm-enable-message-redaction funciona na nossa chave? Algum dos dois afeta os spend logs, ou só os callbacks?** — A doc só garante o efeito sobre os callbacks. Se forem respeitados, o copiloto manda um deles em toda chamada (D8).
- **Quais classes de dado o hub está autorizado a receber (pública, interna, confidencial, restrita)? Existe RIPD/DPIA do hub? Quem é o contato de governança/DPO? Se o hub ficar fora do Brasil, qual é a base da transferência internacional?** — Fecha a regra de saída de dados da lib em cada momento do copiloto (D9).
- **O hub é compartilhado com outros times? O cache de prefixo do vLLM fica isolado por time ou chave (por exemplo, com cache_salt)?** — Com cache compartilhado, existe risco de inferir prompts alheios pelo tempo de resposta. Pode exigir cache_salt nas chamadas do copiloto (D9, D13).
- **Qual é a política do apelido qwen-latest: quando ele muda, com quanto aviso e por qual canal? Podem publicar também um apelido fixo por versão (por exemplo, qwen3.8-27b-AAAAMMDD) e um campo model_info.version? Template de chat, --default-chat-template-kwargs, --served-model-name, parsers de tool, KV em FP8, MTP ou cache de prefixo podem mudar sem trocar o nome?** — É a defesa principal contra troca silenciosa. Sem ela, só resta a impressão digital (D11).
- **Há fallbacks, default_fallbacks ou context_window_fallbacks configurados no LiteLLM para qwen-latest? enable_pre_call_checks está ligado?** — Um fallback automático é uma troca silenciosa feita pelo próprio hub. Ele precisa estar desligado ou visível nos headers (D11, D12).
- **O gpt-oss-20b roda em GPUs e servidor separados do qwen? Por que o contexto é de 8.192 tokens, e ele pode aumentar?** — Ele só serve de reserva se não cair junto com o qwen. O contexto define quais tarefas ele cobre (D12).
- **Qual é a meta de disponibilidade (SLO), quais são as janelas de manutenção, qual é o canal de aviso de incidentes e como abrir chamado para 500 (contato e horário)? include_call_id_in_error_body está ligado?** — Define o modo degradado, o critério de disponibilidade do D7 e se o id da chamada aparece nos erros (D6, D7).
- **Qual é a validade da chave (duration e expires)? O hub tem licença Enterprise do LiteLLM (necessária para /key/regenerate com grace_period)? Há rotação automática? Quem pode bloquear a chave, quanto tempo o bloqueio leva para valer em todas as instâncias e qual é o procedimento se ela vazar?** — Define a rotina de rotação pelo Vault e o plano de incidente (D10).
- **Qual é o papel da nossa chave (internal_user ou outro), quais são os allowed_routes e as permissões de equipe? Ela consegue criar chaves ou modelos? Qual é o nome de um modelo que existe no hub e está fora do nosso escopo, para o teste negativo?** — Nós não chamamos rotas que criam ou mudam algo. A confirmação tem que vir por escrito, e o teste de escopo precisa de um modelo real fora da chave (D10).
- **O x-litellm-response-cost está em qual unidade e o que representa: custo real de rateio, preço de referência ou valor simbólico? Há orçamento por chave? O vLLM roda com --enable-prompt-tokens-details, --enable-force-include-usage e --enable-request-id-headers?** — Define se o custo entra no handoff como número de gestão ou só como indicador, e quais campos de uso e de rastreio existem (D6).
- **Quais são as versões do LiteLLM e do vLLM no hub? drop_params, additional_drop_params ou allowed_openai_params estão configurados? Há valores fixos em litellm_params dos modelos, e quem vence, o pedido ou a configuração? Há guardrails? O LiteLLM devolve campos extras do vLLM (prompt_token_ids, prompt_text)? A nossa chave pode chamar /key/info, /model/info e /health/readiness/details?** — A versão muda headers, flags de teste (ignoradas a partir do v1.85.0) e parâmetros disponíveis (return_prompt_text só existe no vLLM mais recente). A configuração decide o que é descartado em silêncio (D6, D7, D11, D13).

### Resultado → decisão no desenho

| Resultado | Decisão no desenho |
| --- | --- |
| D1: o notebook com Default networking chega ao hub com TLS válido. | O guia de instalação recomenda a sessão padrão (Default networking), sem trabalho de rede. |
| D1: o hub exige IP liberado ou só existe na rede interna. | Os notebooks do copiloto passam a usar Custom networking (NAT com IP reservado, ou DRG mais DNS encaminhado). Isso vira pré-requisito de onboarding. |
| D1: o certificado é de CA interna, ou há proxy corporativo no caminho. | A configuração da lib ganha os campos ca_bundle (a lib monta um contexto TLS próprio com ele) e proxy/no_proxy. verify=False continua proibido. |
| D2: o tempo de caminho fica acima da meta. | Cliente HTTP único por sessão, passos curtos juntados numa chamada só e streaming nas respostas que o dev lê. |
| D3: a leitura pelo Vault com resource principal funciona. | O padrão no OCI é Vault com resource principal e chave só em memória. A variável ADEX_LLM_KEY fica só para a máquina local. getpass fica como plano B. |
| D4: o tpm_limit é menor que a reserva da descoberta (entrada + max_tokens), ou o paralelismo é menor que 3. | A descoberta passa a ser em lotes com resumo, sempre com max_tokens realista. A lib ganha uma fila com limite de simultaneidade. Crítico e narrativa rodam em série. Pedido formal de chave por dev ou de limite maior. |
| D4: a regra de retentativa da lib recupera 100% das chamadas. | A lib tem uma regra única: SDK com max_retries=0, espera exponencial com variação aleatória e teto de 30 s, respeitando retry-after. Não retenta 400, 401, 403 nem 404. |
| D5: o caminho corta chamadas longas sem streaming, ou o TTFT da descoberta passa do stream_timeout. | Streaming obrigatório no plano, no crítico e na descoberta; descoberta em lotes se o TTFT não couber; timeout explícito na lib, abaixo do timeout do caminho, sem reenvio automático. Se o teto de saída for 8.192, os tetos de raciocínio de 12K e 16K saem do desenho e são substituídos por chamadas divididas. |
| D6: call-id, model-id, usage e custo vêm na resposta. | O handoff de cada estudo registra a trilha de chamadas (call-id, model-id, tokens e custo). Em streaming, a lib sempre pede include_usage. Sem esses dados, a lib gera o próprio id e calcula o custo pelo usage. |
| D7: as falhas foram mapeadas e a disponibilidade é conhecida. | Modo degradado (Python e SQL seguem, IA fica pendente), mensagens em português por tipo de falha, sonda /health/liveliness antes de etapas longas, e os cenários do servidor falso viram teste automático da lib. |
| D8: o "no-log" ou a redação são respeitados nas camadas do LiteLLM. | Toda chamada do copiloto vai com o mecanismo que funcionou. Se nenhum for respeitado, pedir uma chave dedicada sem gravação de conteúdo e sem cache, ou mandar ao hub só metadados não sensíveis. |
| D9: há decisão por classe de dado, um k mínimo e uma regra de supressão complementar. | A lib ganha um filtro de saída: lista branca do que vai no prompt em cada momento, supressão de células com n abaixo de k, supressão complementar e regra para PNG e rótulos. Sem decisão, vale o modo só metadados. |
| D10: a chave tem escopo correto e a rotação funciona. | A lib usa uma lista fixa dos 2 modelos, distingue modelo inexistente, modelo fora do escopo e chave inválida, e relê o segredo do Vault uma única vez no 401. O calendário de rotação fica documentado com o CCOE, conforme a licença do LiteLLM. |
| D11: há uma camada forte estável e os controles negativo e positivo funcionam. | Verificação curta (até 2 chamadas, sem cache) no início de cada sessão e completa uma vez por dia ou por semana. Se algo mudar, o dev é alertado, os resultados são marcados como não homologados e a mini-bateria de re-homologação é disparada. Se o CCOE oferecer apelido fixo, a lib usa esse apelido em vez de qwen-latest. |
| D12: o gpt-oss-20b passa só em tarefas pequenas e roda em infraestrutura independente. | A reserva fica limitada aos momentos curtos (escolha de ferramenta, extração e narrativa curta); os outros momentos entram em modo degradado. Se ele falhar ou cair junto com o qwen, o copiloto fica sem reserva. |
| D4/D11/D12: existe algum fallback automático no servidor. | Pedir ao CCOE para desligar os fallbacks de qwen-latest. Enquanto isso, a lib confere x-litellm-model-id e x-litellm-attempted-fallbacks em toda resposta e rejeita respostas de um deployment não homologado. |
| D13: algum parâmetro essencial é descartado ou algum campo da resposta é removido. | Os perfis só usam parâmetros com efeito provado. O parâmetro descartado sai do perfil ou é liberado pelo CCOE; a camada correspondente do D11 sai. |
| D14: a chave ou o conteúdo aparecem em disco ou em saídas. | A lib ganha filtro de log, erros sem headers do pedido e DEBUG de transporte desligado por padrão. O guia de onboarding proíbe %env, export digitado e variável de ambiente da sessão com a chave. |

### Incertezas

- A doc do LiteLLM não diz se o campo model da resposta traz o apelido (qwen-latest) ou o nome real do upstream. E, mesmo que traga o nome real, ele pode ser só o --served-model-name escolhido pelo CCOE. É preciso medir (D11).
- A doc de controle de acesso diz que membros de equipe podem usar /key/info e /key/health por padrão, mas não documenta o acesso de uma virtual key comum a /model/info nem a /health/readiness/details. É preciso medir (D7, D10, D11).
- A doc do LiteLLM não fixa o código HTTP para modelo fora do escopo da chave nem para timeout. Um exemplo de restrição por equipe mostra até código 500 no JSON. É preciso medir (D5, D10).
- Os headers x-ratelimit-* refletem a chave só quando ela tem limite; caso contrário vêm do provedor e podem vir vazios (o vLLM normalmente não manda). Os x-litellm-key-remaining-* só existem com limite por modelo.
- No LiteLLM, o estouro de orçamento (budget_exceeded) aparece com códigos diferentes nos exemplos da doc (400, 429 e 401 para usuário final). O 503 só aparece com fail_closed_rate_limit_enforcement.
- return_prompt_text e return_weight_versions estão na doc 'latest' do vLLM, mas não nas docs das versões 0.13 e 0.16; return_token_ids e cache_salt já estão na 0.13. Não sabemos a versão do vLLM no hub. A doc do LiteLLM não diz se campos extras da resposta do vLLM (prompt_token_ids, prompt_text) passam pela conversão que ele faz. É preciso medir (D13).
- As duas páginas do vLLM se contradizem sobre thinking_token_budget: a do servidor compatível com OpenAI o lista só nos parâmetros da Completions API; a de reasoning outputs diz que funciona no chat com --reasoning-config. É assunto de outro bloco, mas afeta os perfis.
- A doc do vLLM mapeia reasoning_effort low/medium/high (e none) para enable_thinking; o card do Qwen3.8-27B lista xhigh, medium e low e não menciona high. O comportamento real no hub (e se o LiteLLM repassa xhigh) precisa ser medido. Por isso a camada (g) do D11 só registra o resultado.
- O vLLM recente devolve o raciocínio em reasoning (reasoning_content está obsoleto); o LiteLLM diz que padroniza em reasoning_content, mas não lista hosted_vllm entre os provedores. É preciso medir qual campo chega (D11 e outros blocos).
- Nem a doc do vLLM nem a do LiteLLM documentam usage.completion_tokens_details.reasoning_tokens para esse caminho. cached_tokens só aparece com --enable-prompt-tokens-details. É preciso medir (D6).
- A doc do LiteLLM só garante que o "no-log" pula os callbacks. O efeito sobre os spend logs não está documentado, e a redação por header está marcada como beta. O canário (D8) decide.
- A semântica de no-cache e no-store no cache do LiteLLM muda entre versões da doc (uma diz 'não ler do cache', outra 'não gravar'). Por isso a impressão digital manda os dois e confere x-litellm-cache-key; o CCOE confirma se há cache ligado.
- As flags mock_testing_* são removidas pelo proxy a partir do v1.85.0 (confirmado). Não sabemos a versão do hub; em versões anteriores elas ainda funcionariam, mas o desenho não depende disso.
- A atualização do x-litellm-key-spend pode ser assíncrona (gravação em lote). Isso não foi confirmado na doc; o D6 trata como hipótese e espera 2 min antes de comparar.
- Não se sabe se hub-gpus.usto.re é público ou interno. A doc da OCI não diz se o IP do NAT gerenciado da Default networking é estável.
- A política do Vault foi confirmada na referência (read secret-bundles, target.secret.name, sem permissão de keys), mas precisa ser testada no tenancy. O exemplo de código vem de uma doc de outro produto da Oracle (Life Sciences); nomes de classe e campo devem ser conferidos na versão do SDK instalada no notebook.
- Pela doc do HTTPX, SSL_CERT_FILE, HTTPS_PROXY e NO_PROXY são respeitados por padrão. O httpx 0.28 tornou obsoleto o verify como texto (usar um contexto TLS). O README atual do openai-python fala em httpx2 e não diz se o SDK respeita retry-after. É preciso conferir na versão instalada.
- Não foi conferido na doc da OCI que as variáveis de ambiente customizadas da sessão ficam visíveis a quem lê a sessão. Tratamos como risco provável, e a regra é nunca colocar a chave ali.
- O texto oficial da LGPD (planalto.gov.br) não abriu (erro de conexão, duas vezes). O ponto de que dado anonimizado deixa de ser pessoal, salvo reversão com esforço razoável (art. 12), e a regra de transferência internacional (art. 33) vieram de conhecimento prévio e de fontes secundárias. O guia de anonimização da ANPD estava em consulta pública em 2024; não confirmei a versão final.
- A semântica de x-litellm-response-cost para um modelo auto-hospedado é desconhecida: pode ser 0, um preço configurado pelo CCOE ou um valor de referência do mapa do LiteLLM.
- Todos os números dos critérios (300 ms, 2 s, 10 s, 15 s, 5 min, 99%, 95%, 90%, pelo menos 3 simultâneas, folga de 2×) são metas propostas para o copiloto, não medições. Devem ser revistos depois dos blocos de desempenho e qualidade.

### Fontes consultadas

- [LiteLLM: Response Headers (x-litellm-*, partes do custo, x-ratelimit-*, llm_provider-*)](https://docs.litellm.ai/docs/proxy/response_headers)
- [LiteLLM: Users, budgets e rate limits (tpm_limit total por padrão, token_rate_limit_type, reserva antes da chamada, fallback em limite por modelo)](https://docs.litellm.ai/docs/proxy/users)
- [LiteLLM: Virtual Keys (models, /key/regenerate Enterprise com grace_period, /key/block, duration, auto_rotate)](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [LiteLLM: Model access (models da chave esconde em /v1/models e bloqueia chamadas)](https://docs.litellm.ai/docs/proxy/model_access)
- [LiteLLM: Model Management (/model/info com chaves mascaradas)](https://docs.litellm.ai/docs/proxy/model_management)
- [LiteLLM: Logging (no-log, global_disable_no_log_param, turn_off_message_logging, x-litellm-enable-message-redaction)](https://docs.litellm.ai/docs/proxy/logging)
- [LiteLLM: UI/Spend logs (store_prompts_in_spend_logs, disable_spend_logs, retenção)](https://docs.litellm.ai/docs/proxy/ui_logs)
- [LiteLLM: Access control (proxy_admin, proxy_admin_viewer, internal_user e /key/generate)](https://docs.litellm.ai/docs/proxy/access_control)
- [LiteLLM: Team/key logging (/key/health, metadata.logging)](https://docs.litellm.ai/docs/proxy/team_logging)
- [LiteLLM: Health checks (/health/liveliness, /health/readiness, /health/readiness/details, /health)](https://docs.litellm.ai/docs/proxy/health)
- [LiteLLM: Reliability (fallbacks, default_fallbacks, context_window_fallbacks; mock_testing_* removidos desde v1.85.0)](https://docs.litellm.ai/docs/proxy/reliability)
- [LiteLLM: Timeouts (timeout, stream_timeout até o primeiro pedaço, request_timeout, timeout por requisição)](https://docs.litellm.ai/docs/proxy/timeout)
- [LiteLLM: Drop params (drop_params, additional_drop_params, allowed_openai_params)](https://docs.litellm.ai/docs/completion/drop_params)
- [LiteLLM: Provider-specific params (parâmetros que não são da OpenAI vão ao provedor no corpo)](https://docs.litellm.ai/docs/completion/provider_specific_params)
- [LiteLLM: Caching (x-litellm-cache-key, caches semânticos)](https://docs.litellm.ai/docs/proxy/caching)
- [LiteLLM: Cache Controls (cache no corpo: no-cache, no-store, ttl)](https://docs.litellm.ai/docs/proxy/caching_controls)
- [LiteLLM: Reasoning content (padronização em reasoning_content)](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM: Client-side fallbacks](https://docs.litellm.ai/docs/proxy/client_side_fallbacks)
- [LiteLLM: Config (model_name × litellm_params.model, model_info)](https://docs.litellm.ai/docs/proxy/configs)
- [LiteLLM: provider vLLM (hosted_vllm/, reasoning_effort)](https://docs.litellm.ai/docs/providers/vllm)
- [OCI Data Science: Creating a Notebook Session (Default × Custom networking; rede não muda ao reativar)](https://docs.oracle.com/en-us/iaas/Content/data-science/using/create-notebook-sessions.htm)
- [OCI Data Science: Troubleshooting de notebook sessions (rede)](https://docs.oracle.com/en-us/iaas/Content/data-science/using/tshoot-notebook.htm)
- [OCI Data Science: Using Notebook Sessions (resource principals, cache de 15 min)](https://docs.oracle.com/en-us/iaas/data-science/using/use-notebook-sessions.htm)
- [OCI Data Science: grupos dinâmicos (regra datasciencenotebooksession)](https://docs.oracle.com/en-us/iaas/Content/data-science/using/data-flow-studio-tenancy.htm)
- [OCI IAM: Details for the Vault Service (read secret-bundles → GetSecretBundle; target.secret.name)](https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/keypolicyreference.htm)
- [Oracle: exemplo de SecretsClient com resource principal e base64](https://docs.oracle.com/en/industries/health/lifesciences-ai-data-platform/haiug/authenticate-adw-using-resource-principal-authentication-recommended.html)
- [OCI Networking: NAT Gateway (IP público reservado)](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/NATgateway.htm)
- [OCI DNS: Private DNS (resolvedor, forwarding endpoint, FastConnect/VPN)](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/privatedns.htm)
- [vLLM: Online serving (/version, /tokenizer_info)](https://docs.vllm.ai/en/latest/serving/online_serving/)
- [vLLM latest: OpenAI-compatible server (chat_template_kwargs, return_token_ids, return_prompt_text, cache_salt)](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM 0.13: OpenAI-compatible server (return_token_ids e cache_salt presentes; return_prompt_text ausente)](https://docs.vllm.ai/en/v0.13.0/serving/openai_compatible_server/)
- [vLLM: Reasoning outputs (campo reasoning; reasoning_content obsoleto; reasoning_effort; thinking_token_budget)](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)
- [vLLM: vllm serve CLI (--enable-log-requests, --enable-log-outputs, --max-log-len, --enable-prompt-tokens-details, --default-chat-template-kwargs, --enable-force-include-usage, --enable-request-id-headers)](https://docs.vllm.ai/en/latest/cli/serve/)
- [Hugging Face: card do Qwen/Qwen3.8-27B (reasoning_effort xhigh/medium/low, amostragem, contexto)](https://huggingface.co/Qwen/Qwen3.8-27B)
- [HTTPX: Environment variables (HTTPS_PROXY, NO_PROXY, SSL_CERT_FILE)](https://www.python-httpx.org/environment_variables/)
- [openai-python README (max_retries padrão 2, timeout padrão 10 min, with_raw_response, extra_body)](https://github.com/openai/openai-python)

### O que a revisão adversarial corrigiu (20)

- **[alta] D11** — A impressão digital usa um prompt fixo com temperatura 0. Se o CCOE tiver ligado o cache de respostas do LiteLLM (Redis ou semântico), a resposta vem do cache e repete o modelo antigo para sempre, então a troca nunca seria percebida. A doc do LiteLLM diz que uma resposta vinda do cache traz o header x-litellm-cache-key. *Correção:* Toda chamada de impressão digital vai com "cache": {"no-cache": true, "no-store": true} e confere que x-litellm-cache-key não veio. Entrou também uma pergunta ao CCOE sobre cache de respostas, que além disso é um lugar onde conteúdo fica gravado (D8).
- **[alta] D11 / resumo** — O desenho tratava return_prompt_text como recurso do 'vLLM recente'. Ele só aparece na doc 'latest' e não existe nas docs das versões 0.13 e 0.16. Também faltava considerar que o LiteLLM converte a resposta para o formato dele e pode tirar campos extras do vLLM. Nesse caso o parâmetro é aceito, aplicado e a resposta volta 200 sem o campo. *Correção:* A sonda principal de template passa a ser return_token_ids=true, que devolve prompt_token_ids e já existe na 0.13; guardamos o sha256 da lista. return_prompt_text fica como opcional. O critério exige que o campo esteja presente na resposta, e não só HTTP 200. Isso é verificado antes, no novo D13.
- **[alta] D11** — As camadas do LiteLLM (x-litellm-model-id, model-group, api-base) só mudam quando muda a configuração do LiteLLM. Uma troca de pesos ou de template feita só no servidor vLLM, atrás do mesmo endereço, passa sem ser vista. O campo model pode ser apenas o --served-model-name que o CCOE escolheu. Faltava também um controle positivo que provasse que as camadas percebem uma mudança pequena, e não só a troca por um modelo totalmente diferente. *Correção:* As camadas foram reordenadas da mais fraca à mais forte e o limite de cada uma está explicado. Entrou um controle positivo: mudar só chat_template_kwargs (enable_thinking verdadeiro ou falso) tem que alterar usage.prompt_tokens e prompt_token_ids. O critério exige pelo menos uma camada forte estável.
- **[alta] Novo D13** — Nenhum teste separava 'o servidor aceitou e aplicou' de 'aceitou e ignorou'. O LiteLLM repassa ao provedor os parâmetros que não são da OpenAI, mas pode descartá-los (drop_params, additional_drop_params), injetar padrões por modelo em litellm_params e alterar o pedido com guardrails. Em todos esses casos a resposta é HTTP 200. D11, D12 e os perfis do copiloto dependem disso. *Correção:* Novo teste D13. Para cada parâmetro há duas sondas: uma de efeito observável (por exemplo, top_k=1 com temperature=1 dá 5 saídas idênticas; logprobs faz o campo aparecer) e uma de valor inválido, que tem que voltar 400 (se voltar 200, o parâmetro foi descartado). Entram também perguntas ao CCOE. D11 e D12 passam a depender do D13.
- **[alta] D10** — O teste mandava chamar /key/generate e /model/new com a nossa chave para provar que seriam negadas. Pela doc de controle de acesso do LiteLLM, um internal_user pode criar chaves quando a equipe tem essa permissão. Se a chave tiver o papel errado, o teste cria uma chave ou um modelo de verdade, ou seja, um teste com efeito colateral. *Correção:* Do nosso lado, só rotas de leitura (/key/list, /user/list, /spend/logs), que têm que ser negadas ou mostrar só o que é nosso. Papel, allowed_routes e permissões de equipe são confirmados por escrito pelo CCOE. Rotas que criam ou mudam algo nunca são chamadas por nós.
- **[alta] Regras gerais, D1–D11 e D3 passo 7** — (1) Os exemplos com curl passam -H "Authorization: Bearer $ADEX_LLM_KEY" na linha de comando, e a chave fica visível na lista de processos (/proc/*/cmdline). (2) O D3 mandava procurar 'os 8 primeiros caracteres' da chave, o que leva o dev a digitar parte da chave num grep, que vai para o histórico. Além disso, chaves sk- dão falso positivo. (3) Faltavam vazamentos comuns: %env no Jupyter mostra o valor na saída da célula, que fica salva no .ipynb; export no terminal vai para o histórico; a chave nas variáveis de ambiente customizadas da sessão fica na configuração da sessão do OCI. *Correção:* Regra nova: a chave entra no curl pela entrada padrão, com H(){ printf 'Authorization: Bearer %s\n' "$ADEX_LLM_KEY"; } e H | curl -H @-. Na máquina local, a chave é carregada com read -rs. Também ficam proibidos --trace, %env, export digitado e variável da sessão. A varredura passa a ser feita em Python, com a chave só em memória, no novo D14.
- **[alta] D8** — (1) A doc do LiteLLM só diz que "no-log" deixa de registrar nos callbacks. Ela não diz nada sobre spend logs, e o no-log não alcança os logs do vLLM nem os do ingress. Mesmo assim, o critério cobrava que no cenário (b) não houvesse 'nenhum log de conteúdo'. (2) Faltavam no questionário: --enable-log-outputs e VLLM_REQUEST_BODY_LOG_LEVEL do vLLM (com --enable-log-requests em DEBUG, o prompt é gravado), o cache de respostas, os guardrails e os logs de erro. (3) Sem maximum_spend_logs_retention_period configurado, a limpeza não roda e os registros ficam para sempre. (4) Os marcadores eram fixos no exemplo. *Correção:* O resultado agora é medido por camada (spend logs, cada callback, cache, vLLM, ingress). O questionário foi completado e entrou um cenário (d) com erro proposital. Os marcadores são gerados na hora com secrets.token_hex. Entraram /key/health e /health/readiness/details como verificação nossa. A releitura depois do prazo de retenção virou acompanhamento e não bloqueia a Fase 0.
- **[alta] D5** — (1) O exemplo usava reasoning_effort xhigh, que pode ser descartado pelo LiteLLM ou rejeitado pela versão do vLLM, e aí a chamada não seria longa. (2) Talvez seja impossível provocar uma chamada de 15 min com teto de saída de 8.192 tokens. (3) Faltava o principal: o stream_timeout do LiteLLM é o tempo até o PRIMEIRO pedaço da resposta. Enquanto o modelo processa ~131 mil tokens de entrada ou espera na fila, nada é enviado, então o streaming não protege essa fase. (4) Sem streaming, o corte por ociosidade do balanceador precisa ser testado com durações crescentes. (5) finish_reason=length estava listado como corte, mas é o teto de tokens. (6) O SDK tem timeout padrão de 10 min e retenta 2 vezes, inclusive em timeouts, o que duplicaria chamadas longas no GPU. *Correção:* O exemplo usa entrada grande inventada e saída no teto, sem depender de reasoning_effort. Entraram a medição do tempo até o primeiro pedaço (TTFT) e as durações crescentes sem streaming. finish_reason=length é registrado à parte. Entrou uma prova de que o timeout por requisição foi aplicado (cortar em ~5 s). O critério compara com os timeouts declarados pelo CCOE em vez de exigir 15 min fixos.
- **[media] D4** — (1) O LiteLLM aplica o tpm_limit ANTES da chamada, reservando a entrada estimada mais o max_tokens (ou default_estimated_output_tokens), e acerta depois. Um max_tokens exagerado na descoberta piora a recusa, e isso não estava descrito. (2) Pela doc, estourar um limite por modelo, por padrão, cai num fallback configurado e volta 200 de outro modelo: uma troca silenciosa que não estava no teste. (3) O SDK retenta 429 duas vezes por padrão, o que se somaria à regra da lib. *Correção:* Entraram a medição com max_tokens 1024 e 8192 e o efeito sobre as chamadas seguintes na mesma janela. A rampa confere x-litellm-model-id. Entraram perguntas sobre token_rate_limit_type, default_estimated_output_tokens e disable_fallbacks_on_per_model_rate_limits. A lib usa o SDK com max_retries=0 e tem uma regra única de retentativa.
- **[media] D10** — (1) Chamar 'modelo-inexistente-x' testa um nome que não existe no hub, não um modelo que existe e está fora do escopo da chave. Só o segundo prova o escopo. (2) /key/{key}/regenerate com grace_period é recurso Enterprise do LiteLLM. (3) /key/block e /key/unblock exigem a master key. (4) 'Reler o segredo no primeiro 401' podia virar laço infinito. *Correção:* Agora são dois casos: nome inexistente e modelo existente fora da chave (o CCOE informa o nome). Entrou uma rotação alternativa para quando não houver licença Enterprise. Bloqueio e desbloqueio são feitos pelo CCOE. A lib relê o segredo uma única vez e, se continuar 401, para e avisa.
- **[media] D6** — (1) 'Custo dividido pelos tokens dá preço constante' está errado: entrada e saída têm preços diferentes. (2) x-litellm-response-cost-reasoning já faz parte do -output e não pode entrar na soma. (3) A igualdade de key-spend entre duas chamadas seguidas falha por atualização assíncrona do gasto e por outros devs usando a mesma chave. (4) Faltavam stream_options.include_usage (sem isso não há usage em streaming) e onde ficam os tokens de cache e de raciocínio. (5) x-litellm-model-group é o nome que o cliente pediu, não o modelo que respondeu. *Correção:* A coerência agora é custo ≈ p_in × entrada + p_out × saída, com chamadas de tamanhos diferentes. Os componentes são somados sem -reasoning. O key-spend é comparado depois de 2 min, numa janela exclusiva. Entraram usage.prompt_tokens_details.cached_tokens (só com --enable-prompt-tokens-details) e usage.completion_tokens_details.reasoning_tokens (não documentado no vLLM, pode vir ausente).
- **[media] D7** — (1) O tratamento de erro da lib dependia do hub real, e as flags mock_testing_* são ignoradas desde o v1.85.0 (confirmado). Faltava um servidor falso local, que reproduz 429 com retry-after, 5xx, corte no meio do streaming e JSON malformado sem depender do CCOE. (2) /health/readiness, por padrão, só informa status e banco; liveliness e readiness não testam o modelo. (3) Uma série de 10 dias a partir de um notebook fica com buracos se a sessão for desativada. *Correção:* O teste foi dividido em duas frentes: (A) servidor falso local com dados inventados e (B) hub real. Ficou explicado o que cada sonda de saúde prova. A série de 10 dias roda num Job agendado ou numa sessão que fique ativa, com a mesma rede. Entrou uma pergunta sobre include_call_id_in_error_body.
- **[media] Novo D14** — Faltava testar o vazamento do nosso lado: a chave ou o conteúdo podem ficar gravados em logs DEBUG do SDK, httpx ou httpcore, em tracebacks impressos no notebook, em .ipynb_checkpoints, em históricos ou na configuração da sessão do OCI. *Correção:* Novo teste D14: um roteiro com erros provocados e DEBUG ligado, seguido de varredura em Python (chave só em memória, imprime só caminhos) e verificação das práticas proibidas. Critério: 0 ocorrências.
- **[media] D1** — (1) nslookup e dig podem não existir na imagem do notebook. (2) O openssl s_client não lê HTTPS_PROXY. (3) 'Valida com o pacote padrão do Python' é ambíguo: o openai SDK e o httpx usam o pacote certifi, não o repositório do sistema, então a validação tem que usar a pilha da lib. (4) O 401 da -lab indica que um servidor respondeu e não reconheceu a chave, não uma falha de rede. (5) A doc da OCI fala especificamente que a rede não pode ser trocada ao reativar a sessão. *Correção:* O DNS passou a ser resolvido com socket.getaddrinfo; entrou o -proxy no openssl; a validação é feita também com client.models.list(); o 401 da -lab está interpretado. A redação da regra de rede da OCI foi ajustada. O plano B usa um contexto TLS próprio com o ca_bundle.
- **[media] D11** — (1) A camada 'reasoning_effort high dá 400' é frágil. A doc do vLLM mapeia low/medium/high para enable_thinking, o card do Qwen lista xhigh/medium/low, e o CCOE pode ajustar o template; esperar 400 gera alarme falso. (2) O vLLM não garante logprobs idênticos entre lotes, e --max-logprobs limita o top-k. (3) '0 alarmes falsos' não distinguia uma mudança real confirmada. (4) O critério de avançar ou parar não dizia se o apelido fixo do CCOE substitui a impressão digital. *Correção:* O resultado de reasoning_effort high passa a ser registrado como observado, sem valor esperado. A comparação de logprobs usa o token mais provável, o conjunto do top-5 e uma tolerância tirada da linha de base. O critério virou '0 alarmes sem mudança confirmada pelo CCOE'. Para avançar: apelido fixo com aviso formal, ou impressão digital aprovada.
- **[media] D12** — Faltavam caminhos de troca silenciosa documentados: context_window_fallbacks (com enable_pre_call_checks) e o fallback por estouro de limite por modelo, que volta 200. x-litellm-model-group mostra o nome pedido e não serve para detectar fallback. Faltavam também as dependências D4 e D13. *Correção:* Esses caminhos foram incluídos; a detecção usa x-litellm-model-id e x-litellm-attempted-fallbacks; as dependências foram corrigidas.
- **[media] D9** — Suprimir só as células com n abaixo de k não basta: com o total visível, a célula suprimida se recalcula por diferença. Faltavam também: rótulos, títulos e legendas dos PNG seguem a mesma regra do texto; cache_salt só funciona se o LiteLLM o repassar (D13); e há transferência internacional (art. 33 da LGPD) se o hub ficar fora do Brasil. *Correção:* Entraram supressão complementar e arredondamento na decisão, uma regra para rótulos de imagem, o cache_salt ligado ao D13 e a pergunta sobre o país do hub ligada à transferência internacional.
- **[baixa] D2** — O critério falava em '180 chamadas', mas o desenho dá 360 por ambiente (60 por janela × 3 janelas × 2 dias). O p95 de 30 amostras por janela é praticamente o segundo maior valor. *Correção:* O total foi corrigido para 360 no notebook; o p95 final usa as janelas juntas; ficou anotado que o p95 da chamada mínima inclui a fila do vLLM.
- **[baixa] D3** — A política estava certa (confirmado na referência do Vault: read secret-bundles dá GetSecretBundle, target.secret.name vale para secret bundles e não precisa de permissão de keys), mas faltavam: o aviso de que o grupo dinâmico inclui todas as sessões do compartimento, o segundo segredo inventado para a prova negativa e a trilha no OCI Audit. *Correção:* Esses pontos foram incluídos, a varredura de vestígios foi para o D14 e a incerteza sobre 'use keys' foi removida.
- **[baixa] Resumo, fontes e incertezas** — O link de reliability terminava em .md. Havia incertezas já resolvidas (Vault) e faltava registrar que duas páginas do vLLM se contradizem sobre thinking_token_budget no chat. A contagem de testes e os achados do resumo estavam desatualizados. *Correção:* O link foi corrigido, entraram fontes novas (drop_params, provider_specific_params, caching, caching_controls, reasoning_content, reasoning_outputs do vLLM, doc do vLLM 0.13, card do Qwen) e o resumo e as incertezas foram atualizados.

---

## E · Mini-avaliação de qualidade nas tarefas do copiloto (pt-BR)

O Bloco E responde a uma pergunta prática: o Qwen do GPU Hub (qwen-latest) faz bem as tarefas reais do copiloto em português, e qual nível de raciocínio cada tarefa precisa? A bateria tem 45 casos, todos com dados inventados, em 8 tarefas com gabarito: enquadramento, citações para a carta, descoberta de tabelas, SQL Oracle, escolha de método, crítico, narrativa com marcadores e visão. Duas medições percorrem todas as respostas: idioma e estabilidade. Cada caso roda nos 4 níveis (desligado, low, medium, xhigh), 3 vezes cada, o que dá cerca de 540 chamadas. Em cada chamada registramos a qualidade (comparada ao gabarito), o tempo (no cliente e no header x-litellm-response-duration-ms), os tokens (campo usage) e o custo (x-litellm-response-cost). A escolha do nível segue sempre a mesma regra, a escada de custo. Testamos do mais barato ao mais caro (desligado, low, medium, xhigh) e ficamos com o primeiro nível que cumpre quatro condições: (1) bate a meta mínima; (2) empata na prática com o melhor nível, ou seja, perde por no máximo 1 caso ou 10 pontos percentuais; (3) nunca estoura o teto de saída (nenhum finish_reason=length com max_tokens=8192); (4) cabe na meta de tempo. A régua é feita antes de ver qualquer resposta: o E0 congela os gabaritos e prova que o nível de raciocínio realmente chega ao modelo pelo LiteLLM. Sem essa prova, nenhuma comparação vale. O E11 consolida tudo numa tabela de perfis por momento do copiloto e resolve um conflito do desenho. Os tetos de raciocínio de 12K e 16K são impossíveis se a saída for limitada a 8.192 tokens. Nesse caso, o momento passa a usar um nível mais barato que funcione, divide a chamada, ou o CCOE precisa subir o teto. Atenção: com 45 casos, a bateria só enxerga diferenças grandes. É uma triagem para decidir o desenho, não um benchmark. Um empate aqui quer dizer que não há diferença que importe para o produto.


### E0 · Régua pronta e nível de raciocínio funcionando

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* G · *Depende de:* Bloco de conexão e chave (Fase 0), Bloco de controle de raciocínio: como ligar, desligar e trocar o nível (Fase 0), Bloco de limite de saída / max_tokens (Fase 0), Bloco de saída estruturada json_schema (Fase 0)

**Pergunta.** A régua de avaliação está pronta e confiável? E mudar o nível (desligado/low/medium/xhigh) muda de fato o que o modelo faz?

**Por que importa.** Todos os outros testes dependem deste. Ele destrava três coisas: (1) o formato oficial de chamada para cada nível, usado em E1 a E11; (2) a prova de que o nível passa pelo LiteLLM e chega ao vLLM, pois se o proxy descartar o campo em silêncio, os 4 níveis viram o mesmo; (3) gabaritos congelados antes de qualquer resposta, para ninguém ajustar a régua depois de ver o resultado.

**Como medir.**

1) Escrever os 45 casos inventados e seus gabaritos em arquivos versionados (JSON/YAML), calcular o SHA-256 e registrar o hash antes da primeira rodada. 2) Montar o harness em Python (openai SDK) com 4 configurações. Desligado: chat_template_kwargs.enable_thinking=false, temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5. Low, medium e xhigh: chat_template_kwargs.reasoning_effort=<nível>, ou o campo que o bloco de controle de raciocínio validar, com temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence_penalty 0. Todos com max_tokens=8192. 3) Gravar em JSONL, por chamada: caso, nível, repetição, status HTTP, finish_reason, usage completo (incluindo completion_tokens_details e prompt_tokens_details quando vierem), tamanho do texto de raciocínio (o harness lê os campos reasoning e reasoning_content), latência no cliente, os headers x-litellm-response-duration-ms, x-litellm-response-cost e x-litellm-model-id, e o campo model da resposta. Nunca gravar headers da requisição. 4) Retentar só em caso de 429, esperando 2, 4, 8, 16 e 32 s (máx. 5 tentativas). Um 500 para a rodada e aciona o CCOE. Concorrência máxima de 2 até o CCOE informar a cota. 5) Piloto: 3 casos (um de E1, um de E4, um de E6) × 4 níveis × 3 repetições. 6) Calibração: 2 avaliadores pontuam às cegas (nível escondido, ordem embaralhada) 20 respostas do piloto com as rubricas, e calculamos o kappa de Cohen. 7) A rodada oficial de tempos roda dentro do notebook OCI, que é o caminho de rede real. A qualidade pode ser avaliada em qualquer lugar.

**Exemplo de requisição.**

```
import os, time
from openai import OpenAI
cli = OpenAI(base_url=os.environ['ADEX_LLM_BASE_URL'], api_key=os.environ['ADEX_LLM_KEY'])  # base: https://hub-gpus.usto.re/v1
NIVEL = {'desligado': {'chat_template_kwargs': {'enable_thinking': False}},
         'low': {'chat_template_kwargs': {'reasoning_effort': 'low'}}}  # medium e xhigh: idem
t0 = time.perf_counter()
raw = cli.chat.completions.with_raw_response.create(
    model='qwen-latest', messages=msgs, max_tokens=8192,
    temperature=1.0, top_p=0.95, presence_penalty=0,
    extra_body={'top_k': 20, 'min_p': 0, **NIVEL['low']})
r, h = raw.parse(), raw.headers
log = {'lat_s': time.perf_counter() - t0, 'fim': r.choices[0].finish_reason,
       'uso': r.usage.model_dump(), 'custo': h.get('x-litellm-response-cost'),
       'dur_ms': h.get('x-litellm-response-duration-ms'), 'deploy': h.get('x-litellm-model-id')}
```

**Métrica.** % de chamadas com registro completo; mediana e p95 de completion_tokens por nível no piloto; % de chamadas desligadas sem texto de raciocínio; kappa de Cohen entre avaliadores; número de vezes que a chave aparece em logs e arquivos.

**Critério de aprovação.** Aprovado se tudo isto acontecer: 100% das chamadas do piloto registradas com todos os campos; a chave aparece 0 vezes em logs e arquivos (busca automática, sem imprimir a chave); 100% das chamadas desligadas vêm sem texto de raciocínio; a mediana de completion_tokens do low é menor que a do xhigh nos 3 casos do piloto (prova de que o nível chega ao modelo); kappa ≥ 0,7; hash dos gabaritos registrado antes da rodada; model e x-litellm-model-id iguais em todas as chamadas.

**Se falhar.** Níveis com o mesmo consumo: testar o campo reasoning_effort no topo da requisição e, com o CCOE, o padrão do servidor (--default-chat-template-kwargs). Se nada funcionar, a bateria compara só desligado × ligado (xhigh) e o desenho perde os níveis intermediários. Kappa < 0,7: reescrever a rubrica com exemplos de certo e errado e recalibrar. Model ou x-litellm-model-id mudou no meio da rodada: descartar a rodada e repetir com o CCOE avisado, porque troca de modelo é mudança material. 429 frequente: reduzir a concorrência para 1 e pedir janela ao CCOE.


### E1 · Enquadramento: as perguntas certas

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* E0

**Pergunta.** Diante de um pedido vago, o modelo faz no máximo 3 perguntas, e são as perguntas que faltam para enquadrar o estudo?

**Por que importa.** É a primeira impressão do dev e acontece em toda interação. Se o modelo pergunta demais, ou pergunta o que o dev já disse, a entrevista fica chata e o dev abandona. Destrava o perfil do momento 'enquadramento', hoje rascunhado como medium com teto de 4K de raciocínio e JSON estrito, e mostra se o JSON consegue limitar a 3 perguntas (maxItems).

**Como medir.**

1) 5 pedidos vagos inventados, de áreas diferentes: 'quero saber se a campanha de fim de ano funcionou' (marketing); 'o tempo de atendimento das lojas piorou' (operações); 'quais lojas eu deveria fechar?' (expansão); 'o app novo diminuiu as ligações para a central?' (digital); 'preciso prever a demanda de aparelhos no próximo trimestre' (suprimentos). 2) Gabarito de cada caso: 2 a 3 perguntas obrigatórias (ex.: o que conta como 'funcionar', comparar com quê, qual decisão depende do resultado) e perguntas proibidas (já respondidas no pedido, ou que pedem dado linha a linha). 3) A saída é um JSON com até 3 perguntas, cada uma com seu motivo. 4) Python confere se o JSON é válido e quantas perguntas vieram. 5) 2 avaliadores cegos ligam cada pergunta a um item do gabarito ou marcam 'fora'. Divergências vão para um terceiro. 6) Cobertura = obrigatórias cobertas ÷ obrigatórias, com média das 3 repetições por caso e nível.

**Exemplo de requisição.**

```bash
curl -s "$ADEX_LLM_BASE_URL/chat/completions" -H "Authorization: Bearer $ADEX_LLM_KEY" -H "Content-Type: application/json" -d '{"model":"qwen-latest","max_tokens":8192,"temperature":1.0,"top_p":0.95,"top_k":20,"chat_template_kwargs":{"reasoning_effort":"medium"},"messages":[{"role":"system","content":"Você é o copiloto de estudos. Em pt-BR, faça no máximo 3 perguntas, só o que falta para enquadrar o estudo."},{"role":"user","content":"Quero saber se a campanha de fim de ano funcionou."}],"response_format":{"type":"json_schema","json_schema":{"name":"perguntas","schema":{"type":"object","properties":{"perguntas":{"type":"array","maxItems":3,"items":{"type":"object","properties":{"texto":{"type":"string"},"motivo":{"type":"string"}},"required":["texto","motivo"]}}},"required":["perguntas"]}}}}'
# Gabarito do caso: obrigatórias = [métrica de 'funcionou'; grupo ou período de comparação; decisão que depende do resultado]
```

**Métrica.** Cobertura média das perguntas obrigatórias; % de respostas com até 3 perguntas; % de perguntas redundantes ou proibidas; % de JSON válido; mediana e p95 de tempo e de completion_tokens por nível.

**Critério de aprovação.** Meta mínima: 100% das respostas com JSON válido e até 3 perguntas; cobertura média ≥ 80%, sem nenhum caso com cobertura média zero; perguntas redundantes ou proibidas ≤ 10%. Como decide o perfil: aplicar a escada. Se desligado ou low ficar a até 10 pp de cobertura do melhor nível e passar na meta, ele substitui o medium do rascunho. Se só o xhigh passar, o nível vai para o E11, que pesa a latência, porque esta é uma etapa interativa.

**Se falhar.** 1) Pôr no prompt a checklist das dimensões do enquadramento (decisão, métrica, população, período, comparação) e pedir que o modelo escolha as 3 que faltam. 2) Se ainda falhar: o Python decide quais dimensões faltam (checklist determinística) e o modelo só redige as perguntas. 3) Se o maxItems não for respeitado pelo backend, cortar e regenerar no Python. A regra de no máximo 3 perguntas continua valendo.


### E2 · Carta: citações 'você disse' fiéis

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* E0

**Pergunta.** Quando o modelo monta a carta do estudo, as frases 'você disse' são cópias exatas do que o dev falou, e não do que o copiloto falou?

**Por que importa.** A carta é o contrato do estudo. Uma única citação inventada ou parafraseada, ou uma fala do copiloto apresentada como do dev, quebra a confiança. Destrava o perfil do momento 'extração para a carta' (rascunho: low) e uma decisão de arquitetura: o modelo escreve a citação, ou só aponta a fala e o Python copia o texto?

**Como medir.**

1) 5 transcrições inventadas de entrevista, com 8 a 15 falas e IDs (F01, F02...), marcadas DEV ou COPILOTO. 2) Armadilhas: o copiloto propõe algo e o dev recusa; o dev se corrige ('na verdade, só pós-pago'); o dev diz um número; duas falas parecidas; uma fala longa cheia de vírgulas. 3) A saída é a carta em JSON, com os campos objetivo, decisão, público, período, métrica e restrições, mais a lista de citações {campo, fala_id, texto}. 4) Python normaliza o texto (caixa, espaços, aspas) e confere se cada citação é um trecho exato da fala indicada, se a fala é do DEV e se, quando o dev se corrigiu, a versão citada é a final. 5) Um avaliador confere se cada campo obrigatório do gabarito foi preenchido com o sentido certo.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'low'}},
  messages=[{'role': 'system', 'content': 'Monte a carta do estudo. Cada citação deve ser cópia literal de uma fala do DEV, com o id da fala.'},
            {'role': 'user', 'content': '[F01 COPILOTO] Olhamos 2025 inteiro?\n[F02 DEV] Não, só de julho em diante, antes disso o sistema era outro.'}],
  response_format={'type': 'json_schema', 'json_schema': {'name': 'carta', 'schema': {'type': 'object',
    'properties': {'citacoes': {'type': 'array', 'items': {'type': 'object',
      'properties': {'campo': {'type': 'string'}, 'fala_id': {'type': 'string'}, 'texto': {'type': 'string'}},
      'required': ['campo', 'fala_id', 'texto']}}}, 'required': ['citacoes']}}})
# Gabarito: período ← F02 'só de julho em diante'. Citar F01 = erro (fala do copiloto).
```

**Métrica.** Fidelidade = citações literais e atribuídas ao DEV ÷ total de citações; % de casos com pelo menos 1 citação inventada; % de correções do dev respeitadas; cobertura dos campos obrigatórios; tempo e tokens por nível.

**Critério de aprovação.** Meta mínima: fidelidade de 100%, ou seja, zero citação inventada, parafraseada ou atribuída ao copiloto, em todas as repetições do nível escolhido; 100% das correções do dev respeitadas; cobertura ≥ 90%. Como decide o perfil: extração pede pouco raciocínio. Se desligado empatar com low (mesma fidelidade e cobertura até 10 pp abaixo), usar desligado. Se nenhum nível chegar a 100% de fidelidade, a decisão é de arquitetura (plano B), não de nível.

**Se falhar.** Modo extrativo, que acaba com a invenção por construção: o modelo devolve só fala_id e as posições de início e fim do trecho (ou escolhe entre frases já segmentadas pelo Python, via enum), e o Python copia o texto literal para a carta. O validador de substring fica obrigatório no produto, qualquer que seja o resultado.


### E3 · Descoberta: tabelas certas, nenhum nome inventado

*Quem:* nós · *Onde:* ambos · *Esforço:* G · *Depende de:* E0, Bloco de contexto longo e cache de prefixo (Fase 0)

**Pergunta.** Num catálogo grande só de metadados, o modelo acha as tabelas certas, evita as iscas e nunca inventa nome de tabela ou coluna?

**Por que importa.** Nome inventado vira SQL quebrado ou, pior, uma tabela errada usada com confiança. Destrava o perfil do momento 'descoberta' (rascunho: low, cerca de 131K tokens de metadados numa chamada, com cache) e decide se o catálogo inteiro pode ir numa chamada só, ou se é preciso um pré-filtro em Python.

**Como medir.**

1) Um gerador Python com semente fixa cria um catálogo inventado em 5 domínios (vendas, atendimento, logística, cadastro, finanças), com nome, descrição e colunas de cada tabela. 2) Ajustar o número de tabelas até usage.prompt_tokens dar cerca de 131K (versão longa) e cerca de 20K (versão curta). 3) Iscas: nomes quase iguais (VND_PEDIDO_DIA × VND_PEDIDOS_DIA_OLD), tabelas DESCONTINUADAS, cópias _TMP e _BKP, e algumas descrições em inglês. 4) 5 casos: 4 perguntas com a tabela certa no início, no meio e no fim do catálogo, e 1 pergunta cuja tabela não existe (a resposta certa é 'não encontrei' e dizer o que falta). Os 4 casos com resposta também rodam no catálogo curto. 5) A saída é um JSON com tabelas, colunas-chave e lacunas. 6) Python confere cada nome de tabela e de coluna contra o catálogo, por igualdade exata, e compara com o gabarito (obrigatórias, aceitáveis e erradas). 7) Registrar prompt_tokens_details.cached_tokens, se vier, e a latência das repetições, para ver se o cache de prefixo pega.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'low'}},
  messages=[{'role': 'system', 'content': 'Catálogo (só metadados):\nDW_VND.VND_PEDIDO_DIA | pedidos agregados por dia e loja | DT_REF, ID_LOJA, QT_PEDIDO, VL_BRUTO\nDW_VND.VND_PEDIDOS_DIA_OLD | DESCONTINUADA em 2024 | ...\nDW_CAD.CAD_LOJA_REFORMA | lojas reformadas | ID_LOJA, DT_INI_OBRA, DT_FIM_OBRA\n...(~131K tokens)'},
            {'role': 'user', 'content': 'Quero comparar as vendas das lojas reformadas em 2025 com as não reformadas. Use só nomes do catálogo; se faltar algo, diga.'}],
  response_format={'type': 'json_schema', 'json_schema': {'name': 'fontes', 'schema': {'type': 'object',
    'properties': {'tabelas': {'type': 'array', 'items': {'type': 'string'}}, 'lacunas': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['tabelas', 'lacunas']}}})
# Gabarito: obrigatórias {VND_PEDIDO_DIA, CAD_LOJA_REFORMA}; errada: VND_PEDIDOS_DIA_OLD
```

**Métrica.** Taxa de tabelas inventadas e taxa de colunas inventadas; recall das tabelas obrigatórias; precisão; acerto no caso sem resposta; diferença de recall entre o catálogo curto e o longo; cached_tokens; p50 e p95 de tempo no catálogo longo.

**Critério de aprovação.** Meta mínima: 0 tabelas inventadas em todas as respostas; 0 colunas inventadas entre as colunas-chave; recall ≥ 90%; precisão ≥ 70%; caso sem resposta tratado certo nas 3 repetições; recall no longo no máximo 10 pp abaixo do curto, com nenhuma posição da agulha (início, meio, fim) com recall zero. Como decide o perfil: se desligado empatar com low no catálogo longo, usar desligado. Se low só passar no catálogo curto, a decisão é o plano B (pré-filtro), não um nível mais alto.

**Se falhar.** 1) Pré-filtro em Python: busca por palavras nos metadados entrega as cerca de 50 melhores candidatas, e o modelo escolhe entre elas, com contexto muito menor. 2) json_schema com enum dos nomes candidatos, para que o modelo não consiga inventar (conferir se o backend aguenta um enum grande). 3) Dividir o catálogo por domínio, em chamadas separadas. 4) Se a queda no contexto longo for grande, perguntar ao CCOE se o cache KV está em FP8, que piora a recuperação em contexto longo. Em qualquer cenário, o validador de nomes contra o catálogo é obrigatório.


### E4 · SQL Oracle correto, agregado e sem PII

*Quem:* nós · *Onde:* ambos · *Esforço:* G · *Depende de:* E0

**Pergunta.** O SQL que o modelo escreve funciona no Oracle, dá o resultado certo e devolve só agregados, nunca linhas individuais nem colunas de PII?

**Por que importa.** O universo agregado é montado no banco pelo SQL do copiloto. SQL errado gera números errados, e SQL com PII viola a regra central do produto. Destrava o perfil do momento 'rascunho de código' (rascunho: medium) e mostra se vale um ciclo de correção: o erro ORA volta ao modelo uma vez.

**Como medir.**

1) Esquema inventado com 8 tabelas (lojas, regiões, vendas agregadas por dia, campanhas, atendimentos agregados, produtos, metas, calendário), incluindo colunas fictícias marcadas [PII], como NM_GERENTE e CPF_HASH. 2) Dados inventados gerados com semente fixa e carregados num Oracle Free local (container gvenzl/oracle-free, serviço FREEPDB1), com um usuário só de leitura. 3) 6 perguntas, cada uma com uma consulta-gabarito: total por mês (TRUNC de data); top-N (FETCH FIRST); variação mês a mês (LAG); junção por período de vigência; nulos (NVL, e lembrar que no Oracle '' vale NULL); filtro de datas (DATE literal ou TO_DATE). 4) Validação em camadas: sqlglot.parse_one(sql, read='oracle'), onde um ParseError reprova; execução no Oracle local com timeout, capturando o código ORA; resultado comparado ao do gabarito, em ordem só quando a pergunta pede ordem, com tolerância numérica; regra de privacidade, em que o parser lista as colunas usadas e reprova coluna [PII] ou consulta sem agregação. 5) Rodar também uma variante com 1 retentativa: o erro ORA volta ao modelo. 6) Como o banco real pode ser mais antigo que o Oracle Free (23/26), conferir também a sintaxe que só existe nas versões novas.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'medium'}},
  messages=[{'role': 'system', 'content': 'Gere UMA consulta Oracle que devolva só agregados. Nunca use colunas [PII].'},
            {'role': 'user', 'content': 'Esquema: LOJA(ID_LOJA, UF, NM_GERENTE [PII]); VENDA_DIA(ID_LOJA, DT_REF DATE, VL_BRUTO NUMBER). Pergunta: as 3 UFs com maior faturamento em 2025.'}])
# Gabarito: SELECT l.UF, SUM(v.VL_BRUTO) fat FROM VENDA_DIA v JOIN LOJA l ON l.ID_LOJA = v.ID_LOJA
#   WHERE v.DT_REF >= DATE '2025-01-01' AND v.DT_REF < DATE '2026-01-01' GROUP BY l.UF ORDER BY fat DESC FETCH FIRST 3 ROWS ONLY
# Validação local: sqlglot.parse_one(sql, read='oracle'); oracledb.connect(...).cursor().execute(sql)
```

**Métrica.** % que passa no parser; % que executa sem erro ORA; % com resultado igual ao gabarito, com e sem 1 retentativa; % com violação de privacidade; erros mais comuns (ex.: LIMIT em vez de FETCH FIRST); tempo e tokens por nível.

**Critério de aprovação.** Meta mínima: parser ≥ 95%; execução ≥ 90%; resultado correto ≥ 80% (pelo menos 5 das 6 perguntas certas pela maioria das 3 repetições); violação de privacidade = 0. Como decide o perfil: se um nível mais barato bater a meta com 1 retentativa e o caro só sem retentativa, comparar o tempo e os tokens totais (duas chamadas baratas contra uma cara) e ficar com o menor. Se low empatar com medium, usar low.

**Se falhar.** 1) Ciclo de correção de 1 volta no executor, com o erro ORA devolvido ao modelo. 2) Exemplos de dialeto Oracle no prompt (FETCH FIRST, DATE literal, NVL). 3) Para as agregações comuns, usar SQL-modelo parametrizado em blocos, e o modelo só preenche os parâmetros. 4) Antes de executar no banco real, validar com EXPLAIN PLAN. O validador de privacidade (sem PII, só agregado) é obrigatório no produto, qualquer que seja o resultado.


### E5 · Escolha de método com resposta conhecida

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* E0

**Pergunta.** Diante de um cenário com resposta conhecida, o modelo escolhe o método certo (por exemplo, diferença-em-diferenças em vez de antes × depois) e cita a premissa que sustenta a escolha?

**Por que importa.** O método errado leva a uma conclusão errada com cara de certa, como chamar de efeito uma simples comparação de antes e depois. Destrava o perfil do momento mais caro, 'plano e jeitos de enquadrar' (rascunho: xhigh, teto de 16K, dossiê de cerca de 38K), e decide se a escolha do bloco de método pode ser feita pelo modelo ou deve ser uma árvore de decisão em Python.

**Como medir.**

1) 6 cenários inventados, cada um com resposta conhecida: (a) programa lançado em 3 regionais, outras 2 sem programa, dados mensais de 3 anos: diferença-em-diferenças, com premissa de tendências paralelas; (b) preço mudou em todas as lojas no mesmo dia, série diária longa, sem controle: série temporal interrompida, com evidência mais fraca; (c) base agregada completa no banco, pergunta descritiva: censo, sem amostra; (d) auditoria que exige leitura manual de cada registro: amostra aleatória com tamanho calculado; (e) evento raro (0,2% das contas) e busca de fatores associados: caso-controle; (f) dá para sortear quem recebe a ação: experimento A/B. 2) A saída é um JSON com o método (enum que inclui 'antes_depois' como isca e 'outro'), as premissas, o 'por que não' do método tentador e o nível de evidência (descritivo, associativo ou causal). 3) Python compara o enum com o gabarito. Um avaliador confere se a premissa-chave apareceu e se o nível de evidência não foi exagerado.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'xhigh'}},
  messages=[{'role': 'user', 'content': 'Cenário: em mar/2025 o programa de fidelidade começou nas regionais N, NE e CO; S e SE não tiveram. Há vendas mensais agregadas por regional de jan/2023 a dez/2025. O programa aumentou as vendas?'}],
  response_format={'type': 'json_schema', 'json_schema': {'name': 'metodo', 'schema': {'type': 'object', 'properties': {
    'metodo': {'enum': ['antes_depois', 'dif_em_dif', 'serie_interrompida', 'experimento_ab', 'caso_controle', 'censo', 'amostra_aleatoria', 'previsao_serie', 'outro']},
    'premissas': {'type': 'array', 'items': {'type': 'string'}},
    'nivel_evidencia': {'enum': ['descritivo', 'associativo', 'causal']}}, 'required': ['metodo', 'premissas', 'nivel_evidencia']}}})
# Gabarito: dif_em_dif; premissa = tendências paralelas antes de mar/2025
```

**Métrica.** Acerto do método (pela maioria das 3 repetições e por repetição); % com a premissa-chave citada; % de exagero (nível causal onde o gabarito diz associativo ou descritivo); tempo, tokens e estouros (finish_reason=length) por nível.

**Critério de aprovação.** Meta mínima: pelo menos 5 dos 6 cenários certos pela maioria das 3 repetições, e o cenário (a), diferença-em-diferenças contra antes × depois, certo nas 3 repetições; premissa-chave em ≥ 80% das respostas; exagero = 0. Como decide o perfil: este é o teste que mais pode economizar. Se medium ou low empatar com xhigh (no máximo 1 cenário de diferença, mesma taxa de premissa), o plano deixa de usar xhigh. Se o xhigh estourar 8.192 em alguma repetição, ele é inviável com o teto atual (ver E11).

**Se falhar.** Árvore de decisão determinística em Python, com perguntas fechadas (há grupo de controle? houve sorteio? o evento é raro? a série é longa? a base é completa?). O modelo só responde às perguntas da árvore a partir da carta e explica a escolha em linguagem simples. A escolha do bloco passa a ser feita por regra, e o modelo continua explicando e apontando ressalvas.


### E6 · Crítico: acha erros plantados sem alarme falso

*Quem:* nós · *Onde:* ambos · *Esforço:* G · *Depende de:* E0

**Pergunta.** Numa chamada separada, o crítico encontra os erros que plantamos nos estudos e fica quieto nos estudos bem feitos?

**Por que importa.** O crítico é a rede de segurança antes do handoff. Se perde erro grave, o estudo sai errado. Se dá alarme falso demais, o dev passa a ignorar o crítico. Destrava o perfil do momento 'crítico' (rascunho: xhigh) e serve de referência para a 'caça de riscos na entrevista' (rascunho: xhigh, teto de 12K), que usa a mesma habilidade.

**Como medir.**

1) 6 dossiês de estudo inventados, cada um com objetivo, método, tabela agregada, especificação do gráfico e conclusão, em trechos numerados (S1, S2...). 2) Erros plantados: D1 com vazamento de informação (variável medida depois do desfecho) e eixo truncado (eixo Y começa em 95); D2 com paradoxo de Simpson (o total inverte quando se olha por segmento, e a tabela por segmento está no dossiê); D3 com causalidade reversa (lojas com mais promotores vendem mais, mas os promotores foram para as lojas que já vendiam mais) e testes múltiplos (40 segmentos testados, 2 'significativos' relatados sem correção); D4 com viés de sobrevivência e comparação de períodos com sazonalidade diferente. D5 e D6 são estudos limpos. No total, 7 erros plantados. 3) Chamada separada, sem o histórico de quem escreveu o estudo. A saída é um JSON de achados {categoria (enum), gravidade, trecho_id, explicação}. 4) Python casa cada achado com o gabarito por categoria e trecho. Um avaliador revisa os achados que não casaram e os classifica como 'problema real extra' ou 'alarme falso'.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'xhigh'}},
  messages=[{'role': 'system', 'content': 'Você é o crítico. Aponte só problemas reais, com o id do trecho.'},
            {'role': 'user', 'content': 'S2: o modelo que prevê atraso de pagamento usa QT_RENEGOCIACAO, contada nos 60 dias APÓS o vencimento.\nS4: testamos 40 segmentos; 2 deram p<0,05; recomendamos campanha nesses 2.'}],
  response_format={'type': 'json_schema', 'json_schema': {'name': 'achados', 'schema': {'type': 'object', 'properties': {'achados': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'categoria': {'enum': ['vazamento', 'simpson', 'causalidade_reversa', 'testes_multiplos', 'eixo_truncado', 'sobrevivencia', 'sazonalidade', 'outro']},
    'gravidade': {'enum': ['grave', 'medio', 'leve']}, 'trecho_id': {'type': 'string'}, 'explicacao': {'type': 'string'}},
    'required': ['categoria', 'gravidade', 'trecho_id', 'explicacao']}}}, 'required': ['achados']}}})
# Gabarito: S2 = vazamento (grave); S4 = testes_multiplos (grave)
```

**Métrica.** Recall dos erros plantados (geral e por categoria); alarmes falsos graves por dossiê limpo; precisão dos achados graves; tempo, tokens e estouros por nível.

**Critério de aprovação.** Meta mínima: recall geral ≥ 80%; nenhuma das 5 categorias principais (vazamento, Simpson, causalidade reversa, testes múltiplos, eixo truncado) perdida nas 3 repetições; no máximo 0,5 alarme falso grave por dossiê limpo, em média; precisão dos graves ≥ 70%. Como decide o perfil: se medium ficar a até 10 pp de recall do xhigh, com alarmes falsos iguais ou menores, o crítico usa medium. A caça de riscos na entrevista herda o resultado, a confirmar com 2 ou 3 casos próprios no E11. Se o xhigh estourar 8.192, ver E11.

**Se falhar.** 1) Crítico em checklist: uma chamada curta por categoria, com pergunta fechada ('há vazamento? em qual trecho?') e nível low. Em vez de uma chamada longa, várias curtas. 2) Checagens determinísticas em Python onde for possível: eixo do gráfico pela especificação, número de testes feitos contra a correção aplicada, data de cálculo das variáveis contra a data do desfecho (pelos metadados). 3) O modelo fica só com os erros de raciocínio (causalidade reversa, Simpson) e explica.


### E7 · Narrativa sem números soltos

*Quem:* nós · *Onde:* ambos · *Esforço:* M · *Depende de:* E0

**Pergunta.** Ao escrever a narrativa, o modelo usa só os marcadores (que o Python preenche depois) e nunca escreve um número por conta própria?

**Por que importa.** Pela regra do produto, todo número da narrativa vem do Python. Um número escrito pelo modelo pode estar errado e parecer oficial. Destrava o perfil do momento 'narrativa com marcadores' (rascunho: low) e decide se basta validar o texto e pedir de novo, ou se a narrativa precisa vir em pedaços estruturados.

**Como medir.**

1) 4 casos. Em cada um, o modelo recebe a lista de marcadores com nome, descrição e rótulo qualitativo dado pelo Python (ex.: direção = alta, significância = não), mas nunca o valor. 2) Armadilhas: o dev cita um número no pedido ('compare com os 12% do ano passado'); um marcador com dígito no nome ({{m.ic95_var}}); uma descrição de contexto que contém um ano; o pedido 'diga o valor'. 3) Python remove os marcadores válidos (regex {{m.<nome>}} com nome da lista) e procura, no texto que sobra, dígitos e números por extenso ('doze', 'dobro', 'metade', 'por cento' etc.). Também procura marcadores com nome fora da lista. 4) Medir antes e depois de 1 retentativa automática, em que o validador devolve o trecho problemático. 5) Um avaliador confere, numa amostra, se cada marcador foi usado no sentido certo.

**Exemplo de requisição.**

```
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'low'}},
  messages=[{'role': 'system', 'content': 'Escreva a narrativa em pt-BR. NUNCA escreva números: use só os marcadores {{m.nome}} da lista.'},
            {'role': 'user', 'content': 'Marcadores: {{m.var_vendas_pct}} = variação % das vendas pós-reforma (direção: alta); {{m.ic95_var}} = intervalo de confiança; {{m.n_lojas}} = nº de lojas reformadas. Pedido do dev: compare com os 12% do ano passado.'}])
# Validação: resto = re.sub(r'\{\{m\.(var_vendas_pct|ic95_var|n_lojas)\}\}', '', txt); reprova se re.search(r'\d', resto)
# Gabarito: nenhum '12' no texto; o modelo avisa que esse número não vem do estudo
```

**Métrica.** % de narrativas com dígito solto (na 1ª tentativa e após 1 retentativa); % com número por extenso; número de marcadores inventados; cobertura dos marcadores obrigatórios; % de uso no sentido certo (amostra).

**Critério de aprovação.** Meta mínima: marcadores inventados = 0; narrativas com dígito ou número por extenso solto ≤ 5% na 1ª tentativa e 0% após 1 retentativa; cobertura ≥ 90%; uso no sentido certo ≥ 90% na amostra. Como decide o perfil: narrativa é tarefa de escrita. Se desligado empatar com low (taxa de dígito solto até 5 pp acima e mesma cobertura), usar desligado.

**Se falhar.** 1) Narrativa em segmentos estruturados [{texto, marcador?}], com json_schema que proíbe dígitos no campo texto, se o backend respeitar 'pattern' (validar no Python de qualquer forma). 2) Modelos de frase por tipo de achado, com o modelo escolhendo e ajustando. 3) Em qualquer cenário, o validador bloqueia a publicação de uma narrativa com número solto.


### E8 · Visão: gráficos com defeito plantado

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* E0, Resposta do CCOE sobre visão ligada (--language-model-only, --limit-mm-per-prompt, tamanho máximo da requisição)

**Pergunta.** Ao olhar o PNG de um gráfico, o modelo aponta o defeito que engana o leitor, fica quieto no gráfico limpo e não inventa valores?

**Por que importa.** Destrava o perfil do momento 'visão de PNG de gráfico' (rascunho: low) e uma decisão de escopo: a visão entra no MVP, ou a checagem de gráficos vira uma verificação determinística da especificação?

**Como medir.**

1) Confirmar com o CCOE que o hub serve o modelo com visão: se o servidor usa --language-model-only, imagens são recusadas. 2) Gerar 5 PNGs com matplotlib a partir de dados inventados: (1) barras com o eixo Y começando em 95; (2) dois eixos Y com escalas que forçam um 'cruzamento' falso; (3) legenda com as cores trocadas em relação às anotações; (4) pizza cujas fatias somam 130%; (5) gráfico limpo (controle). 3) Enviar cada imagem como image_url com data URL em base64 e pedir um JSON com a categoria do defeito (enum) e a explicação. 4) Python compara a categoria com o gabarito e confere qualquer número citado contra os dados que geraram o gráfico.

**Exemplo de requisição.**

```
b64 = base64.b64encode(open('g1_eixo_truncado.png', 'rb').read()).decode()
cli.chat.completions.create(model='qwen-latest', max_tokens=8192, temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'low'}},
  messages=[{'role': 'user', 'content': [
    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
    {'type': 'text', 'text': 'Este gráfico engana o leitor? Responda em pt-BR com a categoria do defeito.'}]}],
  response_format={'type': 'json_schema', 'json_schema': {'name': 'defeito', 'schema': {'type': 'object', 'properties': {
    'categoria': {'enum': ['eixo_truncado', 'eixos_duplos_enganosos', 'legenda_trocada', 'soma_maior_100', 'sem_defeito', 'outro']},
    'explicacao': {'type': 'string'}}, 'required': ['categoria', 'explicacao']}}})
```

**Métrica.** Recall dos defeitos (4 gráficos com defeito); alarmes falsos no gráfico limpo; % de respostas com valor numérico que não existe nos dados; tempo e tokens de entrada da imagem por nível.

**Critério de aprovação.** Meta mínima: pelo menos 3 dos 4 defeitos certos pela maioria das 3 repetições; alarme falso no gráfico limpo em no máximo 1 das 3 repetições; 0 valores inventados. Como decide o perfil: se desligado empatar com low, usar desligado. Se nenhum nível passar, a visão sai do MVP (plano B).

**Se falhar.** Lint determinístico pela especificação do gráfico: o copiloto gera os próprios gráficos em Python, então dá para checar o eixo de barras começando em zero, eixos duplos, soma das fatias, legenda contra as séries e título contra a direção dos dados, sem visão. A visão fica só para gráficos trazidos pelo dev, como opcional, ou sai do escopo. Se o hub estiver sem visão, pedir ao CCOE para ligar, ou adotar o lint como solução definitiva.


### E9 · Idioma: nada de mistura

*Quem:* nós · *Onde:* ambos · *Esforço:* P · *Depende de:* E0, E1, E2, E3, E4, E5, E6, E7, E8

**Pergunta.** O modelo responde sempre em pt-BR, inclusive depois de receber um erro em inglês ou um catálogo com descrições em inglês, e nunca solta trechos em chinês?

**Por que importa.** Mudar de idioma no meio da resposta parece defeito e confunde o dev e o gestor. Há relatos de troca de idioma depois do resultado de tool, e o próprio card oficial avisa que presence_penalty alto pode causar mistura de idiomas, sendo que o perfil sem raciocínio recomendado usa 1,5. Destrava o valor de presence_penalty no perfil sem raciocínio e diz se é preciso um validador de idioma.

**Como medir.**

1) Medição transversal: analisar todas as saídas de E1 a E8. 2) Mais 3 casos de provocação: (a) conversa com uma chamada de tool cujo resultado é 'ORA-00942: table or view does not exist', e o modelo precisa explicar ao dev; (b) catálogo com descrições em inglês; (c) pedido do dev cheio de termos em inglês ('quero um forecast do backlog'). 3) Os casos de provocação rodam nos 4 níveis e, no desligado, com presence_penalty 1,5 (oficial) e 0. 4) Python remove blocos de código, identificadores (NOMES_EM_MAIÚSCULAS), marcadores e nomes de tabela e coluna, e então: procura caracteres CJK com regex (faixas ぀-ヿ, 㐀-鿿, 가-힯) e classifica cada frase com lingua-py, restrito a português, inglês, espanhol e chinês. 5) Um avaliador revisa as frases sinalizadas. Termos técnicos de uma lista permitida (ex.: forecast, dashboard) não contam.

**Exemplo de requisição.**

```
msgs = [{'role': 'system', 'content': 'Responda sempre em pt-BR.'},
        {'role': 'user', 'content': 'Conte as lojas por UF.'},
        {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'executar_sql', 'arguments': '{"sql": "SELECT UF, COUNT(*) FROM LOJAS GROUP BY UF"}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'ORA-00942: table or view does not exist'}]
for pp in (1.5, 0.0):  # variante desligado
    cli.chat.completions.create(model='qwen-latest', messages=msgs, tools=TOOLS, max_tokens=8192,
        temperature=0.7, top_p=0.8, presence_penalty=pp,
        extra_body={'top_k': 20, 'chat_template_kwargs': {'enable_thinking': False}})
```

**Métrica.** % de respostas com caractere CJK; % de respostas com pelo menos uma frase fora do pt-BR (fora da lista permitida); as mesmas taxas por nível e por valor de presence_penalty; % nos casos de provocação.

**Critério de aprovação.** Meta mínima: 0 respostas com caractere CJK; respostas com frase em outro idioma ≤ 2% no total e 0 nos casos de provocação do nível escolhido. Como decide o perfil: se presence_penalty 1,5 misturar mais idiomas que 0 no desligado (qualquer CJK, ou mais de 2 pp de diferença), o perfil sem raciocínio usa o valor menor. Se a mistura se concentrar num nível, esse nível perde o empate na escada.

**Se falhar.** 1) Reforçar a instrução de idioma no system e repeti-la depois de cada resultado de tool. 2) Traduzir os erros conhecidos antes de devolvê-los ao modelo (tabela de códigos ORA para frases em pt-BR, no Python). 3) Validador de idioma com 1 retentativa antes de mostrar a resposta ao dev.


### E10 · Estabilidade entre 3 respostas

*Quem:* nós · *Onde:* local · *Esforço:* P · *Depende de:* E1, E2, E3, E4, E5, E6, E7, E8

**Pergunta.** Se fizermos a mesma pergunta 3 vezes, de forma independente, o modelo chega à mesma decisão?

**Por que importa.** Um copiloto que escolhe um método diferente a cada vez que o dev pergunta não merece confiança, mesmo que acerte na média. Destrava o desempate entre níveis de qualidade parecida (fica o mais estável) e diz em quais momentos vale votar entre 3 respostas, ao custo de 3 vezes mais chamadas.

**Como medir.**

1) Usar as 3 repetições já feitas de cada caso e nível em E1 a E8: requisições separadas, sem histórico em comum. 2) Definir a decisão-chave de cada tarefa: E1, o conjunto de itens do gabarito cobertos; E2, o conjunto de falas citadas; E3, o conjunto de tabelas; E4, o hash do resultado da consulta; E5, o método; E6, o conjunto de categorias graves; E7, o conjunto de marcadores usados; E8, a categoria do defeito. 3) Para decisões fechadas: % de casos com 3/3 iguais e % com 3 respostas diferentes. Para conjuntos: Jaccard médio entre os pares de repetições. 4) Cruzar estabilidade com acerto: estável e errado é pior que instável.

**Exemplo de requisição.**

```
# Sem requisição nova: a mesma chamada do caso E5-a, enviada 3 vezes de forma independente
resp = [cli.chat.completions.create(model='qwen-latest', messages=caso_E5a, max_tokens=8192,
          temperature=1.0, top_p=0.95, extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'medium'}},
          response_format=SCHEMA_METODO) for _ in range(3)]
metodos = [json.loads(r.choices[0].message.content)['metodo'] for r in resp]  # concordância = len(set(metodos)) == 1
```

**Métrica.** % de casos com concordância 3/3 (decisões fechadas); % de casos com 3 respostas diferentes; Jaccard médio (conjuntos); tudo por tarefa e por nível.

**Critério de aprovação.** Meta mínima: nas decisões fechadas (método, defeito do gráfico, resultado do SQL), 3/3 em ≥ 80% dos casos e nenhum caso com 3 respostas diferentes; nos conjuntos (tabelas, achados, citações), Jaccard médio ≥ 0,8. Como decide o perfil: entre dois níveis empatados em qualidade, fica o mais estável. Um nível que acerta na média mas tem caso com 3 respostas diferentes num momento crítico (método, crítico, SQL) perde o empate.

**Se falhar.** 1) Votação entre 3 respostas, só nos momentos críticos instáveis, ao custo de 3 vezes mais chamadas, pesado no E11. 2) Trocar a decisão do modelo por uma checagem determinística (árvore de método, validador de SQL). 3) Testar amostragem fora dos presets oficiais só como experimento separado, nunca como padrão sem novo teste.


### E11 · Consolidação: perfil por momento do copiloto

*Quem:* nós + CCOE · *Onde:* ambos · *Esforço:* M · *Depende de:* E1, E2, E3, E4, E5, E6, E7, E8, E9, E10

**Pergunta.** Qual nível de raciocínio, teto de tokens e amostragem cada momento do copiloto deve usar, e os tetos de 12K e 16K do rascunho são possíveis?

**Por que importa.** É a saída do Bloco E para o desenho: a tabela de perfis do Motor Qwen. Destrava a decisão sobre o conflito do teto: se a saída real é 8.192 tokens (raciocínio + resposta), os momentos rascunhados com 12K e 16K de raciocínio precisam mudar.

**Como medir.**

1) Montar a matriz tarefa × nível com: qualidade (métrica e aprovado ou não), estabilidade (E10), idioma (E9), completion_tokens p50 e p95 (separando raciocínio e resposta), taxa de finish_reason=length, latência p50 e p95 (no cliente e x-litellm-response-duration-ms, medidas no notebook OCI) e custo (x-litellm-response-cost). Tokens de raciocínio vêm de completion_tokens_details.reasoning_tokens, se vier, ou de uma estimativa: completion_tokens menos os tokens da resposta. 2) Ligar cada momento à sua tarefa: enquadramento ← E1; caça de riscos ← E6; extração ← E2; descoberta ← E3; escolha de bloco ← E5; narrativa ← E7; plano ← E5 + E6; crítico ← E6; visão ← E8; rascunho de código ← E4. 3) Aplicar a escada de custo em cada momento. 4) Teto: para o nível escolhido, o orçamento de raciocínio é o p95 medido mais uma margem, limitado a 8.192 menos o p95 da resposta. Se o p95 de raciocínio não couber, o nível é inviável. 5) Rodada de confirmação: cada perfil final, com max_tokens e, se o campo passar pelo proxy, thinking_token_budget, roda de novo nos casos da sua tarefa (3 repetições) e precisa continuar aprovado.

**Exemplo de requisição.**

```
# Rodada de confirmação de um perfil (ex.: crítico), só se o bloco de controle de raciocínio confirmou que thinking_token_budget passa pelo LiteLLM
cli.chat.completions.create(model='qwen-latest', messages=dossie_D3, max_tokens=8192,
  temperature=1.0, top_p=0.95,
  extra_body={'top_k': 20, 'chat_template_kwargs': {'reasoning_effort': 'xhigh'},
              'thinking_token_budget': ORCAMENTO_MEDIDO})  # ORCAMENTO_MEDIDO = p95 de raciocínio + margem, sem passar de 8192 - p95 da resposta
```

**Métrica.** Para cada momento: nível escolhido, orçamento de raciocínio, max_tokens, amostragem, meta de qualidade atingida, taxa de estouro, p95 de latência, tokens e custo médios por chamada. Para a bateria: número de momentos sem perfil viável.

**Critério de aprovação.** Aprovado se cada momento do copiloto tiver um perfil que: (a) cumpre a meta mínima da sua tarefa na rodada de confirmação; (b) tem 0 estouros (finish_reason=length) com o teto real; (c) tem p95 de latência dentro das metas provisórias de produto (a validar pelo gestor; são metas, não medições): até 30 s nos momentos interativos (enquadramento, extração, narrativa, escolha de bloco) e até 3 min nos pesados (descoberta, plano, crítico); (d) cumpre a meta de idioma (E9) e de estabilidade (E10). Momento sem perfil viável vira item de redesenho, com o plano B da sua tarefa.

**Se falhar.** 1) xhigh necessário mas estourando 8.192: dividir a chamada (ex.: plano em duas etapas, primeiro enquadramentos e depois detalhe), usar thinking_token_budget, se funcionar, ou pedir ao CCOE um teto maior para esses momentos. 2) Latência acima da meta: descer um nível, se a qualidade aguentar, ou mostrar progresso por streaming e rodar em segundo plano no notebook. 3) O gpt-oss-20b não é plano B para momentos grandes, porque tem só 8.192 de contexto. Só serve para momentos curtos, e precisaria de uma mini-rodada própria. 4) Mudança de modelo por trás de qwen-latest depois da bateria: repetir a rodada de confirmação antes de aceitar.


### Perguntas ao CCOE · E

- **O teto de 8.192 em max_tokens vale para o qwen-latest? Ele é aplicado no LiteLLM ou no vLLM? Pode ser maior para chamadas específicas (plano, crítico)?** — No vLLM, raciocínio e resposta dividem o mesmo max_tokens. Com 8.192, os tetos de raciocínio de 12K e 16K do rascunho são impossíveis, e o xhigh pode estourar (finish_reason=length). A resposta muda o desenho de E5, E6 e E11.
- **Quais são a cota e os limites da virtual key (requisições por minuto, tokens por minuto, concorrência, orçamento)? Podemos rodar cerca de 540 chamadas, com algo na ordem de 10 milhões de tokens de entrada (quase tudo do catálogo longo do E3)? Em qual janela?** — Sem isso a bateria toma 429 e os tempos medidos ficam distorcidos. Se a cota for curta, reduzimos as repetições do catálogo longo, e isso enfraquece o E10 nesse momento.
- **O LiteLLM repassa sem descartar os campos chat_template_kwargs (enable_thinking, reasoning_effort, preserve_thinking), top_k, min_p, thinking_token_budget, response_format json_schema e imagens em base64? Há drop_params ligado? O campo reasoning_effort no topo da requisição é traduzido para o modelo?** — Se o proxy descartar o nível em silêncio, os 4 níveis viram o mesmo e a comparação inteira perde o sentido. O E0 detecta o problema, mas só o CCOE corrige.
- **Com quais flags o vLLM sobe o qwen-latest: versão do vLLM, --reasoning-parser, --tool-call-parser, backend de structured outputs, --enable-prefix-caching com --mamba-cache-mode align, --enable-prompt-tokens-details, --kv-cache-dtype (FP8?), quantização dos pesos (BF16, FP8, NVFP4?), MTP (--speculative-config), --language-model-only e --limit-mm-per-prompt?** — Cada flag muda o que medimos. Cache KV em FP8 piora a recuperação em contexto longo (E3). Sem visão, o E8 não roda. Sem prompt-tokens-details, não vemos cached_tokens. A quantização dos pesos afeta a qualidade em todas as tarefas.
- **Dá para congelar o modelo e a configuração por trás de qwen-latest durante a bateria e avisar antes de qualquer troca? O header x-litellm-model-id identifica de forma estável o deployment?** — Troca de modelo, quantização ou flags no meio da rodada invalida a comparação entre níveis. Para o produto, uma troca por trás de qwen-latest é mudança material e exige repetir a confirmação do E11.
- **Qual é o timeout do proxy por requisição e o tamanho máximo do corpo (cerca de 131K tokens de texto; PNG em base64)?** — O xhigh com contexto longo pode passar de minutos, e um timeout do proxy seria contado erradamente como falha do modelo. Imagem grande pode ser recusada antes de chegar ao vLLM.
- **A latência do hub varia muito com a carga de outros times? Existe um horário de baixa carga ou um ambiente dedicado para medir tempos?** — O trade-off qualidade × tempo do E11 depende de tempos representativos. Medir num pico ou num vale distorce a escolha do nível.

### Resultado → decisão no desenho

| Resultado | Decisão no desenho |
| --- | --- |
| Os níveis low, medium e xhigh consomem os mesmos tokens no piloto (E0) | O nível não está chegando ao modelo. Parar a bateria e corrigir o campo com o CCOE (chat_template_kwargs ou reasoning_effort no topo, padrão do servidor). Se não houver correção, o Motor Qwen trabalha só com desligado × ligado. |
| Um nível mais barato bate a meta e fica a até 1 caso (ou 10 pp) do melhor | Usar o nível mais barato naquele momento (escada: desligado < low < medium < xhigh). O xhigh só fica onde ganha com folga. |
| Só o xhigh passa, mas estoura 8.192 (finish_reason=length) em alguma repetição | O xhigh é inviável com o teto atual. Em ordem de preferência: dividir a chamada em etapas, usar thinking_token_budget (se passar pelo proxy) ou pedir ao CCOE um teto maior. Os tetos de 12K e 16K saem do desenho. |
| Pelo menos 1 citação inventada, parafraseada ou atribuída ao copiloto em qualquer nível (E2) | A carta passa ao modo extrativo: o modelo devolve fala_id e posições, e o Python copia o texto literal. O validador de substring fica obrigatório. |
| Tabela inventada, ou recall no catálogo longo mais de 10 pp abaixo do curto (E3) | O catálogo inteiro deixa de ir numa chamada: pré-filtro em Python (cerca de 50 candidatas) e enum de nomes no json_schema. O validador de nomes contra o catálogo é obrigatório. |
| SQL correto abaixo de 80% sem retentativa, mas acima de 80% com 1 retentativa do erro ORA (E4) | O executor ganha um ciclo de correção de 1 volta, e o nível fica no mais barato que passa com o ciclo. |
| Qualquer SQL com coluna PII ou sem agregação (E4) | O validador de privacidade bloqueia a consulta antes de executar. Ele é obrigatório em qualquer nível e nunca depende do modelo. |
| Método certo em menos de 5 dos 6 cenários, ou erro no cenário diferença-em-diferenças × antes e depois (E5) | A escolha do bloco de método passa a ser uma árvore de decisão em Python. O modelo responde às perguntas da árvore e explica. |
| Recall do crítico abaixo de 80%, ou mais de 0,5 alarme falso grave por dossiê limpo (E6) | O crítico vira checklist: chamadas curtas por categoria, mais checagens determinísticas (eixo, número de testes, datas das variáveis contra o desfecho). |
| Número solto na narrativa mesmo após 1 retentativa (E7) | A narrativa passa a vir em segmentos estruturados {texto, marcador}, e o validador bloqueia a publicação de qualquer texto com dígito ou número por extenso solto. |
| Hub sem visão, ou menos de 3 dos 4 defeitos encontrados (E8) | A visão sai do MVP. Os gráficos do copiloto passam por lint determinístico da especificação (eixo, eixos duplos, soma das fatias, legenda). |
| Caractere CJK ou mistura de idiomas acima de 2%, pior com presence_penalty 1,5 (E9) | O perfil sem raciocínio usa presence_penalty menor. Entra um validador de idioma com 1 retentativa, e os erros de tool são traduzidos no Python antes de voltar ao modelo. |
| Concordância 3/3 abaixo de 80%, ou caso com 3 respostas diferentes, num momento crítico (E10) | Votação entre 3 respostas só naquele momento, se o custo couber no E11, ou troca por uma checagem determinística. |
| O campo model ou x-litellm-model-id mudou durante a bateria | Descartar a rodada e repetir. No produto, uma troca do modelo por trás de qwen-latest dispara a rodada de confirmação do E11 antes de seguir. |

### Incertezas

- Onde vai o nível de raciocínio. A receita do vLLM põe reasoning_effort dentro de chat_template_kwargs; o LiteLLM documenta reasoning_effort no topo da requisição. Não sei qual dos dois atravessa este proxy até o modelo. O E0 detecta pela diferença de tokens, mas a correção é do CCOE.
- thinking_token_budget: a página de raciocínio do vLLM diz que é um parâmetro por requisição, mas a lista de parâmetros extras do Chat API não o mostra, o card do Qwen não o menciona e o LiteLLM pode descartá-lo. Está tratado como opcional no E11.
- Tokens de raciocínio: não há garantia de que o vLLM preencha usage.completion_tokens_details.reasoning_tokens. Se não vier, o harness estima (completion_tokens menos os tokens da resposta), o que é aproximado.
- Nome do campo de raciocínio: o vLLM atual usa 'reasoning' (antes 'reasoning_content') e o LiteLLM padroniza 'reasoning_content'. Não sei qual chega ao cliente neste hub, então o harness lê os dois.
- Restrições de json_schema: não encontrei na documentação oficial do xgrammar se maxItems, pattern e enums grandes são respeitados. Tudo é revalidado em Python, e o schema só ajuda.
- Visão: não está confirmado que o hub sirva o modelo com visão. A receita cita --language-model-only como opção para liberar memória, o que desligaria imagens. O E8 depende da resposta do CCOE.
- Versão do Oracle: o Oracle Free local é da linha 23/26, e o banco real do ADEX pode ser mais antigo (ex.: 19c). Uma consulta que roda localmente pode falhar no banco real se usar sintaxe nova. É preciso saber a versão com o time de dados, que não é o CCOE. Também não consegui abrir a página oficial do container no Oracle Container Registry (redirecionamento em loop), então usei a imagem gvenzl/oracle-free.
- Poder estatístico: com 45 casos e 3 repetições, a bateria só enxerga diferenças grandes. Os empates são empates práticos, não prova estatística. É uma triagem para o desenho, não um benchmark.
- Dois momentos do copiloto não têm tarefa própria nesta bateria: 'caça de riscos na entrevista' herda do E6 e 'escolha de ferramenta/bloco sem raciocínio' herda do E5 e dos casos com tool do E9. Se o E11 mostrar dúvida, vale somar 3 a 5 casos próprios para cada um.
- As metas de latência do E11 (30 s nos momentos interativos, 3 min nos pesados) são metas provisórias de produto, não medições, e precisam ser validadas pelo gestor.
- Os tempos medidos dependem da carga do hub naquele momento. A mesma bateria pode dar tempos diferentes em outro horário.
- Fatos de terceiros citados no contexto (xhigh gastando cerca de 8 vezes mais tokens sem ganho, alucinação de cerca de 30%) não foram conferidos nesta pesquisa. O card oficial só avisa que um nível de raciocínio menor nem sempre reduz o tempo total, então o tempo precisa ser medido, não suposto.
- O volume estimado (cerca de 540 chamadas, na ordem de 10 milhões de tokens de entrada) é uma conta feita a partir do desenho, não uma medição. Depende do tamanho real do catálogo longo e dos dossiês.

### Fontes consultadas

- [Qwen/Qwen3.8-27B: card oficial do modelo (amostragem, enable_thinking, reasoning_effort, preserve_thinking, aviso sobre presence_penalty e mistura de idiomas, formato de imagem)](https://huggingface.co/Qwen/Qwen3.8-27B)
- [vLLM Recipes: Qwen3.8-27B (reasoning-parser qwen3, tool-call-parser qwen3_coder, reasoning_effort em chat_template_kwargs, --language-model-only, MTP, kv-cache fp8)](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)
- [vLLM: Reasoning Outputs (campo reasoning, antigo reasoning_content; thinking_token_budget; structured outputs e tools com raciocínio)](https://docs.vllm.ai/en/latest/features/reasoning_outputs.html)
- [vLLM: Structured Outputs (response_format json_schema, extra_body structured_outputs, xgrammar/guidance)](https://docs.vllm.ai/en/latest/features/structured_outputs.html)
- [vLLM: Tool Calling (parsers, tool_choice, --enable-auto-tool-choice)](https://docs.vllm.ai/en/latest/features/tool_calling.html)
- [vLLM: OpenAI-Compatible Server, Chat API (top_k, min_p, chat_template_kwargs, extra_body)](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM: Multimodal Inputs (image_url com data URL base64, --limit-mm-per-prompt)](https://docs.vllm.ai/en/latest/features/multimodal_inputs/)
- [vLLM issue #16162: --enable-prompt-tokens-details no V1 (cached_tokens), corrigida](https://github.com/vllm-project/vllm/issues/16162)
- [LiteLLM: Response Headers (x-litellm-response-cost, x-litellm-response-duration-ms, x-litellm-model-id, x-ratelimit-*)](https://docs.litellm.ai/docs/proxy/response_headers)
- [LiteLLM: provedor vLLM (reasoning_effort em hosted_vllm, drop_params)](https://docs.litellm.ai/docs/providers/vllm)
- [LiteLLM: Reasoning content (reasoning_content padronizado, valores de reasoning_effort)](https://docs.litellm.ai/docs/reasoning_content)
- [SQLGlot: documentação (dialeto oracle, ParseError, aviso de que é transpilador e não validador)](https://sqlglot.com/sqlglot.html)
- [gvenzl/oci-oracle-free: imagens Oracle Free em container (ORACLE_PASSWORD, APP_USER, porta 1521, FREEPDB1, scripts de init)](https://github.com/gvenzl/oci-oracle-free)
- [python-oracledb: introdução (modo thin sem Oracle Client)](https://python-oracledb.readthedocs.io/en/latest/user_guide/introduction.html)
- [lingua-py: detecção de idioma (from_languages, detect_multiple_languages_of)](https://github.com/pemistahl/lingua-py)
