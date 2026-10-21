# Fase 0 · Blocos F e G (revisão de 19/10/2026)

Estes blocos complementam `bateria-detalhada.md` (Blocos A a E).

As decisões que motivaram os blocos foram tomadas em 19/10:
- LangGraph, LangChain e Deep Agents na orquestração;
- Langfuse na observabilidade;
- ambientes OCI parametrizados.

As evidências e as fontes estão em `docs/pesquisa/`. Nada foi medido ainda.

Regras comuns a todos os testes:
- usar só dados inventados;
- a chave nunca aparece em tela, log ou arquivo;
- o SDK e o LangChain rodam com retentativa zero;
- cada chamada registra modelo, parâmetros, uso de tokens e os headers `x-litellm-*`.

## Bloco F · A stack no caminho até o hub

O Bloco F refaz as provas dos Blocos A e B passando pela stack (LangChain, LangGraph, Deep Agents e Langfuse). Cada camada pode perder algo sem avisar.

| ID | O que queremos saber | Passa se | Se falhar |
|---|---|---|---|
| F1 | Os parâmetros do Qwen (liga/desliga, nível, teto de raciocínio, top_k, min_p) chegam ao modelo pelo `ChatQwenHub`? | Mesmas diferenças de `prompt_tokens` do A2 em 100% dos casos. Nenhum parâmetro da instância some numa chamada (o merge do `extra_body` é raso). | Uma instância por perfil e nunca parâmetro por chamada. O parâmetro perdido sai do perfil. |
| F2 | O raciocínio chega à lib, com e sem streaming? | O `ChatQwenHub` traz o raciocínio em 10 de 10. O `ChatOpenAI` puro confirma o descarte (controle). | Ler a resposta bruta antes do LangChain. |
| F3 | Retentativas, custo e uso chegam certos? | `x-litellm-attempted-retries` = 0. Custo e `x-litellm-call-id` aparecem em `response_metadata`. Uso de tokens também em streaming (`stream_usage=True`). | Custo calculado pelo uso de tokens e pela tabela do CCOE. |
| F4 | O JSON travado funciona pelo LangChain, com e sem raciocínio? | `with_structured_output` (json_schema, strict) dá 10 de 10 válidos nos dois modos. Comparar ProviderStrategy e ToolStrategy no `create_agent`. | Pensar e formatar em duas chamadas. |
| F5 | As ferramentas funcionam nos subagentes do Deep Agents? | IDs de tool call não vazios e únicos (com e sem streaming). Nenhuma chamada cortada passa como completa (`completion_tokens ≥ max_tokens` vira corte). `</think>` literal não some dos argumentos. No máximo 2% de turnos só com raciocínio. | Sem streaming nos subagentes, detector de corte e menos ferramentas por subagente. |
| F6 | O agente só enxerga as ferramentas permitidas? | Snapshot por nó e por subagente mostra só `ls`, `read_file`, `glob`, `grep` (e `write_file` só em `/rascunho/`). Zero `execute`, `edit_file` e `delete`. Um plug-in falso (entry point `deepagents.*_profiles`) que injeta ferramenta é bloqueado pelo auditor. | Bloqueante: nenhuma versão do Deep Agents sobe sem passar aqui. |
| F7 | O resumo automático de contexto dispara no ponto certo e cabe no teto? | Com `profile.max_input_tokens`, dispara a 85% da janela configurada, e a chamada de resumo cabe em 8.192 tokens de saída. | Desligar o resumo e encurtar as conversas do agente. |
| F8 | Os portões sobrevivem a reiniciar o kernel? | Os 5 portões retomam do SqliteSaver cifrado (`EncryptedSerializer`, `LANGGRAPH_STRICT_MSGPACK=true`). O nó de portão não repete modelo nem SQL. Voltar no tempo não reexecuta SQL. | Durabilidade síncrona e cópia a cada portão. |
| F9 | O rastro no Langfuse fica completo? | Árvore grafo → agente → subagentes → chamadas. Religação após `Command(resume)`. Aprovações como score CATEGORICAL. A lib não inicia sem `langfuse.base_url`. Flush funciona no Jupyter. | IDs próprios nos metadados e religação feita pela lib. |
| F10 | O mascaramento funciona? | Zero marcadores canário (amostra, literal SQL, string de conexão, chave falsa, raciocínio) em qualquer span. Se `mask_otel_spans` falhar, o lote é descartado, nunca enviado cru. | Bloqueante: captura só de referências até corrigir. |
| F11 | Prompts versionados e experimentos funcionam? | `get_prompt` com label `production` e fallback embutido usado quando o Langfuse cai. A mini-avaliação roda como `run_experiment`. O Qwen funciona como avaliador pelo worker (host liberado, `tool_choice` forçado). | Prompts só embutidos e avaliação rodada pelo notebook. |
| F12 | Quanto a stack custa por chamada? | O texto fixo de ferramentas do agente (~2 a 3 mil tokens) é medido, cabe no orçamento e é aproveitado pelo cache de prefixo. | Menos ferramentas e descrições mais curtas. |

## Bloco G · O ambiente da OCI, quando houver acesso

O Bloco G só roda com acesso. Até lá: parâmetros de pior caso e serviços falsos nos testes.

| ID | O que queremos saber | Passa se | Se falhar |
|---|---|---|---|
| G1 | O conda da lib (Python 3.12, versões fixadas com hash) instala numa sessão nova sem internet? | Publicado com `odsc conda publish`, instalado só via service gateway e persistente depois de reativar. | Espelho interno de pacotes, ou NAT. |
| G2 | Os painéis funcionam no JupyterLab 4.4.6? | ipywidgets e anywidget renderizam. O fallback em HTML estático também. | Só HTML estático, com chamadas em código no lugar dos botões. |
| G3 | O notebook alcança o hub, o Langfuse, o ADW, o Object Storage e o Vault? | Os 5 respondem com DNS e certificado válidos. | Custom networking e pedidos de rota. |
| G4 | O resource principal lê o segredo e grava no bucket, sem chave pessoal? | Leitura e escrita OK, provas negativas negadas e registro no Audit. | Ajustar o dynamic group e as políticas (o cache leva até 15 min). |
| G5 | A conexão com o ADW funciona como planejado? | oracledb thin, mTLS 1522 (ou TLS 1521), credenciais do Vault, serviço LOW, leitura com `fetch_df_all`. | TLS com ACL, ou config provider no Object Storage. |
| G6 | O dicionário mostra o que a descoberta precisa? | `ALL_*` mostra só a allowlist. `NUM_ROWS` e comentários preenchidos em ≥ 90% das tabelas (meta). | Coleta de estatísticas pelo time de banco, e a descoberta avisa quando falta informação. |
| G7 | O porteiro estima custo antes de rodar? | EXPLAIN PLAN funciona com o usuário só leitura. A diferença entre estimado e real fica medida em 10 consultas. Consultas acima do teto são barradas. | Limites fixos de linhas e de tempo. |
| G8 | O checkpoint sobrevive à vida real da sessão? | O SQLite em `/home/datascience` persiste depois de reativar, e o snapshot no Object Storage restaura o estudo. | Gravação síncrona e cópias mais frequentes. |
| G9 | O handoff entra no Model Catalog? | Modelo de teste registrado com proveniência git, metadados < 32 KB e version set, legível pela produção. | Handoff só no bucket, com manifesto completo. |
| G10 | O que acontece numa célula com mais de ~30 min? | O comportamento no limite fica registrado, e a mesma tarefa roda como Data Science Job, com logs. | Corte por `datascience.cell_budget_s` e oferta de rodar como Job. |
| G11 | A auditoria cobre o que a governança precisa? | O Audit registra a leitura de segredo e a criação de modelo. Os logs do bucket registram leitura e escrita de objetos. | Pedir os logs do bucket, e o dossiê guarda os eventos. |
