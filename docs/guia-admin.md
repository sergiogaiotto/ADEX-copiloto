# Guia do Admin do ADEX Copiloto

O Admin cuida do que **não pode depender do dev**: retenção, observabilidade (Langfuse), política de dados, orçamento de
IA e as impressões digitais de segredos e endpoints. Tudo isso fica no módulo **Configurações**: um arquivo TOML
**assinado** que só o Admin publica.

## 1. Como a garantia funciona

1. **Fonte única.** `configuracoes.toml` fica num prefixo do Object Storage em que só o grupo Admin escreve (política
   do IAM). Os notebooks só leem. O caminho vai em `[admin] arquivo = "oci://bucket@namespace/adex/admin/configuracoes.toml"`.
2. **Assinatura Ed25519.** O arquivo leva a chave pública em `[_meta]` e a assinatura em `configuracoes.toml.sig`.
3. **Âncora de confiança.** A impressão (sha256) da chave pública fica gravada no pacote, em
   `src/adex_copiloto/recursos/ancora_admin.txt`, na build oficial do conda. Se o pacote não tiver âncora, vale a
   variável `ADEX_ADMIN_ANCORA`, **só fora da OCI** (uso local). Na OCI, a variável é ignorada: qualquer dev poderia
   gerar o próprio par de chaves.
4. **Local fixo e sem volta atrás.** Grave o local do arquivo em `recursos/admin_arquivo.txt` na build oficial: ele
   vale no lugar de `admin.arquivo` (que o dev controla). `validade_ate` é obrigatória (o `assinar` põe 365 dias se
   você não disser). Uma versão mais antiga que a maior já aplicada no ambiente é recusada, para que ninguém aponte
   para um arquivo antigo ainda assinado. Por isso, **suba `versao` a cada publicação**.
5. **Recuo restritivo.** Sem arquivo, sem âncora, com assinatura inválida, vencido ou com valor fora do esquema, valem
   os **padrões do pacote**. Na OCI, o copiloto **nem abre** sem Configurações confirmadas, porque os padrões do pacote
   podem ser mais frouxos que os da organização. O dossiê de cada estudo registra a situação.
6. **Trava de precedência.** Chaves do Admin nunca são sobrescritas por projeto, dev, ambiente ou código, nem trocando
   a seção inteira (`ADEX__HUB='{...}'`, `hub = {}` no TOML, objeto no código): a seção é desmontada e cada chave do
   Admin é conferida. `nan` e `inf` são recusados. Algumas chaves só podem ser apertadas pelo dev:
   `data_policy.min_group_size` (só sobe), tetos de linhas, de leitura, de custo do plano no ADW e orçamento (só
   descem) e `langfuse.captura` (só fica mais restrita).
7. **Limite honesto.** Dentro do kernel, a biblioteca pode ser alterada em memória. A garantia real vem da permissão de
   escrita, da assinatura, da retenção aplicada por cada armazenamento e do dossiê, que registra o sha256 da
   configuração efetiva.

## 2. Primeira vez

```bash
adex-configuracoes gerar-chaves --saida ~/.adex/admin --senha    # chave privada (guarde no Vault) e âncora
adex-configuracoes modelo --saida configuracoes.toml             # todas as chaves do Admin com o padrão
# edite configuracoes.toml
adex-configuracoes assinar configuracoes.toml --chave-privada ~/.adex/admin/admin-ed25519.pem
adex-configuracoes verificar configuracoes.toml
adex-configuracoes publicar configuracoes.toml --destino oci://bucket@ns/adex/admin/configuracoes.toml
```

Grave a âncora (`ancora_admin.txt` gerado) no pacote antes de publicar o conda.

Desenvolvimento local, sem OCI: `adex-configuracoes iniciar-local` cria um Admin local, imprime a
`ADEX_ADMIN_ANCORA` e o `[admin] arquivo` para o `adex.toml`.

## 3. Segredos: valor no cofre, impressão na configuração

| Item | Onde mora o valor | O que vai nas Configurações |
|---|---|---|
| `hub.chave` | Vault (OCI) ou DPAPI (Windows local) | `hub.chave.impressao`, `hub.chave.salt` |
| `langfuse.public_key`, `langfuse.secret_key` | Vault ou ambiente | impressão e salt de cada uma |
| `langfuse.base_url` | Vault ou ambiente | impressão e salt (garante o endpoint homologado; o SDK cai na nuvem da Europa por padrão) |
| `adw.senha`, `adw.wallet_senha` | Vault | impressão e salt |
| `hub.base_url` | configuração | `hub.base_url_impressao` e `hub.base_url_salt` (obrigatórios se não for o endereço oficial) |

O dev roda `adex-segredo definir hub.chave` (ou `impressao`) e entrega **impressão e salt**, que não são segredo. Na
rotação, valor novo exige salt novo e impressão nova.

A chave do hub só vai para o endereço homologado (ou o oficial), conferido em **todo** ponto de envio: cliente,
LangChain, lista de modelos, Fase 0 e `adex-segredo smoke`. Também são do Admin: `hub.proxy` e `hub.ca_bundle` (um
proxy com CA própria interceptaria a chave), `objectstorage.destinos_autorizados` (para onde o handoff pode ir) e
`adw.custo_max_plano` (só desce).

## 3a. Trilha (dossiê)

Cada evento do dossiê é encadeado com **HMAC-SHA256**, com uma chave derivada da chave dos checkpoints (Vault na OCI,
DPAPI no Windows). O arquivo `dossie.selo.json` guarda o número e o hash do último evento, também com HMAC: cortar
eventos do fim, apagar o selo ou refazer a cadeia sem a chave é detectado, e o copiloto recusa abrir o estudo. O último
hash vai nos metadados de cada comando no Langfuse (outra máquina), que serve de âncora externa para auditoria.

## 4. Langfuse (máquina própria, no Brasil)

```toml
[langfuse]
habilitado = true
server_version = "4.49"        # define o SDK: 4.x (o 5 exige servidor >= 4.59)
data_location = "Brasil"
project = "adex-copiloto"
environment = "exploracao"
rota = "pendente"              # "ok" quando houver rota OCI -> máquina do Langfuse
on_unavailable = "fila"        # sem rota: spans e notas mascarados em fila local; ou "desligado"
captura = "mascarado"          # metadados | mascarado | completo
amostragem = 1.0
[langfuse.avaliacao]           # LLM como juiz com o próprio Qwen
habilitada = true
amostragem = 0.2
criterios = ["fidelidade_numeros", "clareza", "aderencia_carta"]
```

- A máquina do Langfuse alcança o hub: os avaliadores do próprio Langfuse (LLM-as-a-judge) podem usar o Qwen como
  conexão OpenAI-compatível (`https://hub-gpus.usto.re/v1`, modelo `qwen-latest`), com chave própria.
- Sem rota a partir da OCI, a fila local fica em `<estado>/fila_langfuse/` e é reenviada pela função
  `reenviar_fila` (módulo `observabilidade.langfuse_adaptador`) quando a rota existir.

## 5. Retenção, aplicada onde o dado mora

```toml
[retencao]                  # sugestões, pendentes de aval da governança
langfuse_dias = 90
checkpoint_dias_pos_handoff = 30
snapshot_dias = 180
fila_local_dias = 7
dossie_local_dias = 365
handoff_dias = 0            # 0 = definido pela equipe de produção
```

- **Notebook:** a lib aplica ao iniciar (fila local, checkpoints de estudos entregues, cópias locais antigas com snapshot).
- **Bucket:** `adex-configuracoes retencao-bucket` gera a política de lifecycle (`oci os object-lifecycle-policy put`).
- **Langfuse:** a retenção nativa é paga; na **máquina do Langfuse**, rode
  `adex-configuracoes retencao-langfuse` (lista) e `--executar` (apaga pela API).

## 6. Política de dados e orçamento

```toml
[data_policy]
min_group_size = 50          # só sobe
max_linhas_resultado = 100000
max_linhas_amostra = 2000000
teto_gb_lidos = 50.0
padroes_pii_extra = []       # regex de colunas pessoais da organização
funcoes_sql_extras = []      # funções Oracle extras aceitas pelo porteiro

[orcamento]
tokens_por_estudo = 3000000
custo_usd_por_estudo = 25.0
chamadas_por_estudo = 600
```

O que o porteiro garante com isso, em toda consulta (da IA ou do dev):

- dado pessoal só como argumento direto de `COUNT`/`COUNT(DISTINCT)`, em `IS NULL` ou numa junção por igualdade entre
  colunas; nunca em `CASE WHEN`, filtro com valor, `MIN`/`MAX` ou outra função;
- nenhum valor que pareça dado pessoal em qualquer parte da consulta, nem montado por `||`;
- no modo agregado, identificador de cliente ou domicílio só em contagem, junção, `IS NULL` ou como chave dentro de
  CTE/subconsulta; nunca na saída;
- subconsulta que lê tabela de pessoas: no filtro, só por chave do indivíduo ou agregada (ganha o grupo mínimo); na
  lista do `SELECT`, só agregada.

**Risco aceito:** duas consultas aprovadas e parecidas podem ser subtraídas uma da outra para isolar um grupo pequeno
(ataque por diferença). O grupo mínimo por consulta não impede isso. As defesas são o grupo mínimo alto, o registro de
toda consulta no dossiê e no Langfuse (auditável) e o fato de que só o dev, já autorizado a ver os dados, escreve SQL
livre; a IA só usa modelos fixos.

## 7. Impressão do modelo

`qwen-latest` é um apelido. `adex-fase0 rodar --ids A1,D11` dá a impressão atual. Grave-a em `hub.modelo_impressao`;
com `fingerprint.on_change = "bloquear"`, uma troca silenciosa do modelo para o copiloto até a nova homologação.
