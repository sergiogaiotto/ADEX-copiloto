# Pesquisa · Stack LangGraph, LangChain, Deep Agents e Langfuse

Data: 19/10/2026. Pesquisa feita por subagente em PyPI, GitHub (código-fonte nas tags citadas) e docs oficiais. Nenhum código foi executado.

**Legenda.** [F] é fato com fonte. [I] é inferência.

## Versões verificadas em 19/10/2026

| Pacote | Versão (data) | Fonte |
|---|---|---|
| deepagents | 0.7.28 (16/10) | https://pypi.org/project/deepagents/ |
| langgraph | 1.2.16 (16/10) | https://pypi.org/project/langgraph/ |
| langchain / langchain-core / langchain-openai | 1.4.6 / 1.6.12 / 1.7.2 | https://pypi.org/project/langchain-openai/ |
| langgraph-checkpoint / -sqlite / -postgres | 4.3.0 / 3.1.2 / 3.2.0 | https://pypi.org/project/langgraph-checkpoint-sqlite/ |
| langgraph-oracledb (oficial Oracle) | 1.0.2 (12/10) | https://pypi.org/project/langgraph-oracledb/ |
| Langfuse servidor | v4.63.0 (16/10) | https://github.com/langfuse/langfuse/releases |
| Langfuse Helm chart | 2.1.6 (19/10) | https://github.com/langfuse/langfuse-k8s/releases |
| SDK Python langfuse | 5.0.2 (16/10) | https://pypi.org/project/langfuse/ |
| openai SDK / litellm / vllm (mais recentes no PyPI, não as do hub) | 3.35.1 / 1.106.0 / 0.32.0 | PyPI |

**Correção de premissa.** O Langfuse atual é a linha v4, e não a v3, com o SDK Python v5. A v3 recebe só patches de segurança até o fim de janeiro de 2027 (https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4).

## A) Deep Agents 0.7.28

**Construção do agente**
- [F] A assinatura é `create_deep_agent(model, tools, *, system_prompt, middleware, subagents, skills, memory, permissions, backend, interrupt_on, response_format, state_schema, context_schema, checkpointer, store, debug, name, cache)`. Não existe `instructions`.
- [F] O agente é montado via `langchain.agents.create_agent`, com `recursion_limit: 9_999` por padrão.
- Fonte: https://github.com/langchain-ai/deepagents/blob/deepagents%3D%3D0.7.28/libs/deepagents/deepagents/graph.py

**Ordem dos middlewares [F]**
1. Filesystem
2. SubAgent
3. Summarization
4. PatchToolCalls
5. Os do usuário
6. Skills/Memory
7. HumanInTheLoop (só com `interrupt_on`)
8. Cache de prompt Anthropic (não faz nada fora da Anthropic)

Filesystem e SubAgent são estrutura protegida: não dá para excluí-los, mas um middleware com o mesmo `.name` os substitui.

**Ferramentas embutidas [F]**
- `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`.
- `execute` só existe com backend de sandbox (`SandboxBackendProtocol`).
- `task` só existe quando há subagentes. Um subagente "general-purpose" é criado automaticamente.
- Fonte: https://github.com/langchain-ai/deepagents/blob/deepagents%3D%3D0.7.28/libs/deepagents/deepagents/middleware/filesystem.py

**Mudanças que quebram compatibilidade na 0.7.0 [F]**
- `write_todos` saiu do padrão. Para voltar: `middleware=[TodoListMiddleware()]`.
- O prompt base padrão agora é vazio.
- `write_file` passou a sobrescrever arquivos.
- Surgiu um `delete` recursivo.
- `FilesystemBackend` e `LocalShellBackend` usam `virtual_mode=True` por padrão.
- Fonte: https://github.com/langchain-ai/deepagents/blob/deepagents%3D%3D0.7.28/libs/deepagents/CHANGELOG.md

**Backends [F]**
- `StateBackend` é o padrão. Os arquivos ficam no estado do grafo, portanto entram no checkpoint.
- Os demais: `StoreBackend`, `CompositeBackend`, `FilesystemBackend` e sandboxes.
- `LocalShellBackend` roda shell no host "sem sandboxing ou isolamento" (backends/local_shell.py).
- Há uma ferramenta `eval` de JavaScript (QuickJS), opcional, com o extra `deepagents[quickjs]`.

**Como restringir as ferramentas [F]**
1. `FilesystemMiddleware(backend=StateBackend(), tools=[...])` funciona como lista de permissão (`read_file` é obrigatório). Ele substitui o padrão no agente principal e no general-purpose. Nos subagentes declarativos, vai em `spec["middleware"]`.
2. `register_harness_profile("openai:qwen-latest", HarnessProfile(excluded_tools=..., general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))`. Sem subagentes, a ferramenta `task` não existe.
3. `permissions=[FilesystemPermission(...)]` com modos allow, deny ou interrupt. A primeira regra que casar vale. Não funciona com backends que executam comandos.

**Riscos [F]**
- Perfis de terceiros são carregados por entry points (`deepagents.provider_profiles`, `deepagents.harness_profiles`). Qualquer pacote instalado pode mexer em prompts e ferramentas (profiles/_builtin_profiles.py).
- Exige Python ≥ 3.11.
- Instala junto `langchain-anthropic`, `langchain-google-genai` e `langsmith`.

**Modelo compatível com OpenAI**
- [F] Uma string `openai:...` liga a Responses API, o que quebra gateways que só oferecem Chat Completions (https://github.com/langchain-ai/deepagents/issues/3973). Use uma instância `ChatOpenAI(base_url=..., use_responses_api=False)`.
- [F] O requisito é ser "qualquer chat model LangChain com tool calling". A lista de modelos abertos recomendados não inclui o Qwen (https://docs.langchain.com/oss/python/deepagents/models).

**Tamanho do prompt e resumo de contexto**
- [F] As descrições das ferramentas somam cerca de 6 mil caracteres, mais 1.072 do `task`. [I] Isso dá uns 2 a 3 mil tokens por chamada.
- [F] Sem `profile.max_input_tokens`, o resumo dispara em 170.000 tokens e guarda 6 mensagens. Com o perfil, dispara em 85% da janela e guarda 10% (middleware/summarization.py). O `ChatOpenAI` aceita `profile=`.

**Problemas conhecidos [F]**
- #3587: endpoints OpenAI-compatíveis do Qwen devolvem `tool_call.id = ""` em streaming, e o `task` falha (https://github.com/langchain-ai/deepagents/issues/3587).
- #36809: o raciocínio some com ChatOpenAI + vLLM no deepagents (https://github.com/langchain-ai/langchain/issues/36809).
- `AsyncSubAgent` exige servidor remoto. Evitar.
- `RubricMiddleware` (beta) faz um laço de nota e revisão.

## B) LangGraph 1.2.16

**Uso como biblioteca**
- [F] Funciona só como biblioteca (MIT, "can be used standalone"). O Agent Server/Platform não é necessário (https://docs.langchain.com/oss/python/langgraph/overview).

**Subgrafos [F]** (https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- Podem ser o grafo compilado como nó, ou chamados dentro de uma função de nó.
- `checkpointer` do subgrafo:
  - `None`: herda e reinicia a cada chamada.
  - `True`: acumula por thread, sem chamadas paralelas.
  - `False`: sem checkpoint e sem interrupções.
- Interrupções sobem para o grafo pai.

**`interrupt()` e `Command(resume=...)` [F]** (https://docs.langchain.com/oss/python/langgraph/interrupts)
- Exige checkpointer e `thread_id`.
- Na retomada, **o nó inteiro reexecuta**.
- Não envolver em try/except.
- Efeitos antes do interrupt precisam ser idempotentes.
- Interrupções estáticas não são recomendadas para aprovação humana.
- O `HumanInTheLoopMiddleware` aceita approve, edit, reject e respond.

**Checkpointers [F]** (https://docs.langchain.com/oss/python/langgraph/checkpointers)
- Opções: InMemory, Sqlite, Postgres, MongoDB e Cosmos.
- Para Oracle há o `langgraph-oracledb` oficial (OracleSaver, AsyncOracleSaver e store). Evitar o comunitário `langgraph-checkpoint-oracle`.
- O SqliteSaver é "para casos leves e síncronos".
- O SQLite corrompe em sistemas de arquivos de rede (https://www.sqlite.org/useovernet.html).
- No OCI Data Science, só `/home/datascience` persiste.
- [I] Conclusão: SQLite no block volume, com snapshots no Object Storage, e nunca SQLite montado sobre o Object Storage.

**Serialização [F]**
- `JsonPlusSerializer`.
- `EncryptedSerializer.from_pycryptodome_aes()`, com a chave em `LANGGRAPH_AES_KEY`.
- `LANGGRAPH_STRICT_MSGPACK=true`.

**Durabilidade e time travel [F]**
- `durability`: sync, async (padrão) ou exit.
- `get_state`, `get_state_history` e `update_state` (fork).
- O replay **reexecuta chamadas de LLM**. [I] Por isso a reprodutibilidade vem dos artefatos aprovados (SQL, código e parâmetros), não de gerar a resposta de novo.

**Streaming [F]**
- `stream_mode` aceita values, updates, messages e custom, e funciona no Jupyter.
- `stream_events(version="v3")` é experimental.

## C) LangChain v1 e ChatOpenAI contra vLLM via LiteLLM

**Parâmetros extras [F]**
- `extra_body=` é o caminho para `chat_template_kwargs`, `top_k`, `min_p` e `thinking_token_budget`.
- O merge é **raso**: um `extra_body` passado por chamada substitui o da instância inteira (langchain_openai/chat_models/base.py, tag 1.7.2).

**Responses API [F]**
- `reasoning=`, `include`, `truncation` ou `output_version="responses/v1"` trocam para a Responses API, a menos que `use_responses_api=False`.
- No vLLM, `reasoning_effort` liga o `enable_thinking`, mas um `enable_thinking` explícito tem prioridade.
- O campo de saída do vLLM agora se chama `reasoning` (https://docs.vllm.ai/en/latest/features/reasoning_outputs.html).

**Raciocínio descartado [F]**
- O ChatOpenAI **não extrai nem preserva** `reasoning_content`/`reasoning` (docstring do base.py). O pedido para mudar isso foi fechado como "not planned" (https://github.com/langchain-ai/langchain/issues/35901).
- Alternativas:
  - `ChatLiteLLM`, mas o SDK litellm teve versões maliciosas no PyPI em mar/2026 (https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/).
  - `langchain-qwq`, da comunidade.
  - [I] Uma subclasse fina de ChatOpenAI.

**Saída estruturada e ferramentas [F]**
- `with_structured_output` usa `json_schema` por padrão e aceita `strict`.
- `bind_tools` converte `"any"` em `"required"` e aceita `parallel_tool_calls`.
- No `create_agent`, a escolha entre ProviderStrategy e ToolStrategy segue o `profile["structured_output"]`.
- `tool_choice` nomeado com raciocínio ligado foi rejeitado no DashScope (https://github.com/langchain-ai/langchain/issues/40897).

**Retries, uso e headers [F]**
- `max_retries=None` herda 2 retentativas e timeout de 600 s do SDK openai.
- `stream_usage` vem desligado com `base_url` customizado.
- `include_response_headers=True` coloca os headers em `response_metadata["headers"]`.
- O LiteLLM expõe `x-litellm-response-cost`, `x-litellm-call-id` e `x-litellm-attempted-retries`. O custo por componente só vem sem streaming.

**Qwen3.8-27B com vLLM `qwen3_coder` [F]** (https://github.com/vllm-project/vllm/issues/61206)
- Um `</think>` literal some dos argumentos.
- Em corte por `length`, o servidor informa `finish_reason=tool_calls` (22 de 22 casos): **uma ferramenta truncada parece completa**.
- Resultados paralelos fora de ordem perdem o vínculo pelo ID.
- PR #62368: chamadas sem o wrapper `<tool_call>`.

## D) Langfuse (servidor v4, SDK Python v5)

**Arquitetura [F]**
- Componentes: web, worker, PostgreSQL ≥ 15, ClickHouse ≥ 25.12 (26.4 recomendado), Redis ≥ 7.0 e blob S3 obrigatório.
- Modelo de dados "observations-first". Ingestão legada dá 404, e o SDK Python ≤ v2 é rejeitado.

**Deploy [F]**
- Docker Compose não tem HA nem backup, e pede no mínimo 4 núcleos e 16 GiB.
- Helm/Kubernetes é o caminho para produção.

**OCI Object Storage nativo [F]** (https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage)
- `LANGFUSE_USE_OCI_NATIVE_OBJECT_STORAGE=true`.
- `LANGFUSE_OCI_AUTH_TYPE` aceita instance_principal, workload_identity, resource_principal, oci_profile ou session_token.
- Suporte "Community". Retenção por lifecycle do bucket.

**Licença [F]**
- MIT fora de `/ee`. O OSS inclui SSO, RBAC por organização, LLM-as-a-judge, prompts, datasets, experimentos e filas de anotação.
- Só no Enterprise: RBAC por projeto, logs de auditoria, gestão de retenção, mascaramento no servidor e SCIM (https://langfuse.com/pricing-self-host).
- Telemetria se desliga com `TELEMETRY_ENABLED=false`.

**SDK v5 [F]**
- Exige Python 3.11+ e servidor ≥ v4.59.0.
- Atributos de trace vão por `propagate_attributes()`.
- **O `base_url` padrão é `https://cloud.langfuse.com` (UE).** Sem chaves, o cliente se desliga.
- Parâmetros relevantes:
  - `sample_rate`;
  - `mask_otel_spans` (por lote; se lançar exceção, o lote é descartado);
  - `should_export_span`.
- Flush a cada 512 spans ou 5 s.
- No mascaramento, os metadados chegam em JSON, então é preciso fazer `json.loads` antes.

**Integração LangChain/LangGraph [F]**
- `CallbackHandler` vem de `langfuse.langchain`.
- Metadados aceitos: `langfuse_session_id`, `langfuse_user_id`, `langfuse_trace_name`.
- Depois de um `Command(resume)`, o handler religa o trace pelo `thread_id`, mas esse mapa fica em memória (até 1.024 itens).
- O handler registra uso de tokens, não custo vindo de headers.
- [I] Ele grava entradas e saídas dos nós, ou seja, o estado do grafo.

**Custos, scores, prompts e experimentos [F]**
- `usage_details`/`cost_details` enviados pela aplicação têm prioridade. Dá para cadastrar modelos próprios, com regex e preço.
- `create_score` aceita os tipos NUMERIC, BOOLEAN, CATEGORICAL, CORRECTION e TEXT.
- `get_prompt(name, version|label, cache_ttl_seconds, fallback, ...)`: TTL padrão de 60 s, com stale-while-revalidate.
- `run_experiment(...)` ou `dataset.run_experiment(...)`.

**LLM-as-a-judge [F]**
- Usa conexão OpenAI com base URL própria e exige `tool_choice` forçado.
- Hosts internos ficam bloqueados por anti-SSRF; para liberar, `LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST`.
- Roda no worker.

**LiteLLM → Langfuse no proxy [F]**
- O preset é `langfuse_otel`. Com `turn_off_message_logging`, o proxy não grava mensagens.
- [I] Se o hub também gravar, o conteúdo fica duplicado no proxy do CCOE.

**LGPD**
- [F] A Langfuse Cloud tem regiões US, UE, JP e HIPAA, todas na AWS. Não há região no Brasil.
- [F] A OCI tem `sa-saopaulo-1` e `sa-vinhedo-1`.
- [I] A Cloud implicaria transferência internacional (LGPD, art. 33). Self-host na OCI Brasil é o caminho natural.

## Recomendações do relatório (resumo)

**Arquitetura**
1. Grafo externo determinístico, com portões em nós que só chamam `interrupt()`.
2. Deep Agents só na descoberta e no plano, chamados dentro de função de nó.
3. SQL nunca como ferramenta livre: `propor_sql` só devolve texto, e quem executa é um nó determinístico com porteiro. Código sai por `sugerir_celula`.

**Segurança do Deep Agents**
4. `StateBackend` sempre.
5. Ferramentas de arquivo só de leitura; escrita apenas em `/rascunho/`.
6. Subagente general-purpose desligado.
7. Limites de recursão e de chamadas.
8. Auditor que falha fechado se a superfície de ferramentas divergir ou se surgirem entry points não autorizados.

**Modelo**
9. Subclasse `ChatQwenHub(ChatOpenAI)` com:
   - `use_responses_api=False`, `stream_usage=True`, `include_response_headers=True`;
   - `parallel_tool_calls=False`;
   - `profile` com a janela de entrada;
   - extração do raciocínio.
10. Uma instância por perfil, com `extra_body` completo.
11. Middlewares para:
    - turno só com raciocínio;
    - argumentos validados com Pydantic;
    - truncamento quando `completion_tokens ≥ max_tokens`.

**Checkpoint**
12. SqliteSaver cifrado com `STRICT_MSGPACK` e snapshot no Object Storage a cada portão. Avaliar OracleSaver num schema dedicado.

**Langfuse**
13. Self-host na OCI Brasil:
    - blob nativo OCI;
    - telemetria desligada;
    - `LANGFUSE_BASE_URL` obrigatório (a lib não inicia sem ele);
    - `session_id` = estudo e aprovações como score CATEGORICAL;
    - mascaramento de amostras, literais SQL, conexões, arquivos do agente e raciocínio;
    - custo do header como metadata;
    - combinar com o CCOE a gravação no proxy;
    - prompts versionados com fallback embutido;
    - mini-avaliação via `run_experiment`.

**Novos testes para a Fase 0:** repasse do `extra_body`; preservação do raciocínio; IDs de tool call e truncamento nos subagentes; saída estruturada com raciocínio; superfície de ferramentas; resumo de contexto dentro de 8.192 tokens; retomada após reiniciar o kernel; árvore, religação e mascaramento no Langfuse; LLM-as-a-judge alcançando o hub; conda com Python ≥ 3.11 instalável pelo mirror.
