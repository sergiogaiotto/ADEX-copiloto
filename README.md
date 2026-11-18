<<<<<<< HEAD
# ADEX Copiloto

Copiloto de inteligência de dados para o **Jupyter do OCI Data Science** (módulo datascience do ADEX), movido pelo
**Qwen3.8-27B do GPU Hub** (`qwen-latest`). É uma biblioteca Python: sem portal, sem Teams, sem backend próprio.

> A IA **interpreta, planeja e explica**. O **Python e o SQL executam e validam**. Decisões relevantes passam por
> **aprovação humana**. Exploração e produção são equipes diferentes: o estudo termina num **pacote de handoff que roda
> sem o copiloto**.

O caso típico começa **do zero**: o dev chega só com uma pergunta ("a troca de roteadores por Wi-Fi 6 valeu a pena?") e o
copiloto conduz até um resultado defensável. Playbooks e lentes são atalhos, oferecidos só quando servem de verdade
(aderência ≥ 0,70, contada pelo Python).

## Em 30 segundos

```python
from adex_copiloto import AdexCopiloto

adex = AdexCopiloto.from_env()                       # config em camadas + Admin assinado + chave conferida
estudo = adex.create_session(objective="A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?")

estudo.frame()                                       # enquadra (nenhum dado lido) e faz até 3 perguntas
estudo.frame(answers="""1. Decidir se a onda 4 sai em 2027...""")
estudo.charter().approve()                           # ⛔ carta do estudo (10 campos, origem de cada um)
estudo.discover(hint="acho que tem algo no esquema OPS_RES")   # só metadados; nomes conferidos no catálogo
estudo.confirm_mapping()
estudo.run("universo")                               # painel agregado NA FONTE, pelo porteiro de SQL
estudo.passport().approve()                          # ⛔ Passaporte de Amostragem
plano = estudo.plan()                                # jeitos de responder + método por blocos + pré-mortem
plano.approve()                                      # ⛔ plano
estudo.run()                                         # etapas do plano (blocos registrados)
estudo.review()                                      # crítico (Python + Qwen em chamada separada) e narrativa
estudo.accept(reason="...")                          # ⛔ aceite
estudo.export_handoff().approve()                    # ⛔ pacote independente (spec, seção 14)
```

Sem código: `adex.start()` abre o mesmo fluxo num painel. No notebook: `%load_ext adex_copiloto` liga `%adex` e
`%%adex sql` (o SQL do dev passa pelo **mesmo porteiro** que o da IA).

## O que é garantido (e não é parâmetro)

| Garantia | Como |
|---|---|
| A IA nunca executa SQL nem código | SQL só por modelos fixos ou pelo dev, sempre pelo **porteiro**; código sugerido vai para a próxima célula |
| Porteiro de SQL | um SELECT só leitura; lista de permissão de construções (sqlglot); tabelas e colunas conferidas no catálogo (também em subconsultas correlacionadas); dado pessoal só direto em `COUNT`, `IS NULL` ou junção por igualdade (nunca em `CASE WHEN`, filtro ou `MIN`/`MAX`); nenhum valor com cara de dado pessoal em parte alguma, nem montado por `\|\|`; identificador nunca na saída agregada; subconsulta sobre tabela de pessoas só por chave ou agregada; literais viram binds; **grupo mínimo** por unidade distinta em todo escopo que agrega; teto de linhas; `EXPLAIN PLAN` antes; `SET TRANSACTION READ ONLY` |
| Nenhuma linha de cliente vai para a IA | a IA vê metadados (comentários mascarados), agregados com grupo mínimo e marcadores |
| Números são do Python | a IA escreve `⟦M-01⟧`; texto com número solto é recusado e refeito; senão, texto-modelo sem IA |
| Regra de aceitação da resposta | `finish_reason`, corte por teto (vLLM #61206), vazia, `<think>` vazado, JSON fora do esquema → reparo dirigido ou recusa |
| 5 portões humanos | carta, passaporte, plano, aceite, handoff (LangGraph `interrupt`); voltar a uma etapa nunca pula um portão; parâmetro novo num plano aprovado gera versão nova que precisa de aprovação; o pacote só sai de estudo aceito, com plano, passaporte e mapeamento aprovados |
| Selos de evidência A–D | regra única em Python; a IA nunca dá selo acima de D |
| Trilha | dossiê só de acréscimo, encadeado por **HMAC** com a chave dos checkpoints e selado no último evento (corte do fim é detectado); último hash também no Langfuse |
| Reprodução | o pacote com agregados roda o pipeline offline e precisa dar o **mesmo efeito** do estudo, senão é recusado |
| Segredos | valor no Vault/DPAPI; a configuração guarda só `sha256(salt‖valor)`; a lib confere e recusa se divergir; a chave do hub só vai para o endereço homologado, conferido em todo envio; logs, tracebacks e rastros saem mascarados |
| Governança só do Admin | retenção, Langfuse, política de dados, orçamento, impressões, proxy/CA do hub, destinos do handoff: arquivo **assinado (Ed25519)**, com validade e sem volta a versão antiga; trocar a seção inteira não contorna a trava; na OCI, sem Admin confirmado o copiloto não abre |

## Instalação

```bash
pip install -e .            # núcleo
pip install -e ".[oci]"     # python-oracledb (thin), SDK OCI e ocifs (no notebook da OCI)
pip install -e ".[dev]"     # testes
```

No OCI Data Science, publique o conda de `environment.yaml` (Python 3.12) com `odsc conda publish`.

## Configuração (tudo o que depende do hub é parâmetro)

Camadas, da mais fraca para a mais forte: **padrão do pacote < Admin (assinado) < projeto (`adex.toml`) < local
(`config/local.toml`) < ambiente (`ADEX__SECAO__CHAVE`) < código**. Chaves do Admin ignoram as camadas de baixo
(algumas só podem ser **apertadas**, como `data_policy.min_group_size`, que só sobe).

```bash
adex-configuracoes mostrar          # cada valor e de onde veio (🔒 = só Admin)
adex-segredo definir hub.chave      # grava com DPAPI e imprime impressão + salt para o Admin
adex-segredo conferir hub.chave
```

Principais seções: `[hub]` (URL oficial `https://hub-gpus.usto.re/v1`, teto de saída, onde vai o nível de raciocínio,
saída estruturada, visão, tempos), `[perfis.*]` (rápido, ação, pensar, crítico, visão, narrar: corpo de requisição
completo, `top_k=20` sempre), `[langfuse]` (só Admin), `[retencao]` (só Admin), `[adw]`, `[objectstorage]`, `[vault]`,
`[agente]` (Deep Agents só em descoberta e plano), `[checkpoint]`, `[data_policy]`, `[orcamento]`.

## Fase 0: homologar o GPU Hub antes de fixar os perfis

```bash
adex-fase0 listar
adex-fase0 rodar                     # blocos A, B, D, E, F
adex-fase0 rodar --blocos C --pesado # contexto longo, agulha, vazão
```

47 provas automáticas com os ids de `docs/fase0/` (teto real de saída, nível de raciocínio, JSON travado, ferramentas,
visão, cache, latência, custo, impressão do modelo, qualidade por momento do copiloto, stack e ambiente OCI). O
relatório sai em `fase0-resultados/` com **recomendações de configuração**. A chave nunca aparece.

## Arquitetura

- `llm/`: `ClienteHub` (direto) e `ChatQwenHub` (LangChain), perfis, aceitação, marcadores, impressão do modelo.
- `dados/`: porteiro, fichas (PII e identificadores), catálogo, fontes (ADW thin; DuckDB local), armazenamento.
- `estudo/`: carta, evidência, passaporte, plano, mapeamento, descoberta, planejamento, dossiê, sessão (`Estudo`).
- `blocos/`: 25 blocos de método (universo, amostragem, BI, inferência, modelos, séries, causal) e `estatistica.py`
  (autossuficiente; vai para o handoff).
- `harness/`: grafo LangGraph de 13 passos e 5 portões, checkpoint SQLite cifrado (AES) com msgpack estrito.
- `agentes/`: Deep Agents com rédea curta (sem execute/edit/delete, escrita só em `/rascunho/`, auditor, limites).
- `critico/`, `narrativa/`, `observabilidade/` (Langfuse 4.x, fila local, retenção, juiz Qwen), `handoff/`, `ui/`,
  `extensao/` (`@adex.tool`, `@adex.block`, playbooks, lentes), `fase0/`.

Diagramas: `docs/diagramas/`. Guias: `docs/guia-dev.md` e `docs/guia-admin.md`.

## Testes

```bash
python -m pytest            # sem rede: hub falso no nível HTTP, DuckDB local, dados sintéticos
python -m pytest -m hub     # (reservado) provas contra o hub real
```

## Limites honestos

- Dentro do kernel, quem tem acesso pode alterar a biblioteca em memória. A garantia vem das permissões (IAM, prefixo do
  Admin), da assinatura, da retenção aplicada em cada armazenamento e do dossiê, que registra o sha256 das configurações.
- Os perfis do Qwen partem de valores de pior caso até a Fase 0 medir o hub.
- O kernel não isola as ferramentas do dev: a garantia está no que chega à IA (saída conferida e mascarada) e no registro.
- Duas consultas agregadas aprovadas e parecidas podem ser subtraídas para isolar um grupo pequeno (ataque por
  diferença). O grupo mínimo por consulta não impede; o registro de toda consulta e o grupo mínimo alto mitigam.
- Quem controla o kernel pode ler a chave dos checkpoints e refazer o dossiê com HMAC. A âncora externa (último hash no
  Langfuse, em outra máquina) é o que permite perceber isso numa auditoria.
=======
# ADEX-copiloto
ADEX-copiloto
>>>>>>> f29b657b0a63edcd18b9c241715f5d5d4011273a
