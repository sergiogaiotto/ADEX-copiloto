# Pesquisa · OCI Data Science, Model Catalog, ADW, Object Storage, Vault e Logging

**Data:** 19/10/2026.

Pesquisa nas docs oficiais da Oracle, do SDK ADS e do python-oracledb.

**Legenda:** [F] fato com fonte · [I] inferência · NE = não encontrado.

As referências Rn estão no fim.

## 1. Notebook sessions

**Versões**
- [F] JupyterLab 4.4.6 desde 04/11/2025. Sessões antigas ficam na 3.6.6 até serem reativadas [R2].
- NE: versão de Python/SO da imagem base. [F] O kernel "Python 3" é mínimo, e a Oracle recomenda usar um conda [R8].
- [F] O `runtime.yaml` aceita Python 3.10 a 3.14. O padrão é 3.12 (`generalml_p312_cpu_x86_64_v1`) [R25].

**Conda**
- [F] Três tipos [R12]:
  - Data Science: curados pela Oracle.
  - Published: guardados no seu bucket.
  - Installed: em `/home/datascience/conda`.
- [F] `odsc conda install --slug <slug>` persiste depois da desativação. Bibliotecas instaladas no ambiente base NÃO persistem; clone o ambiente [R9].
- [F] `odsc conda create -n -v -f environment.yaml` acrescenta sozinho `oci`, `ipykernel`, `jupyterlab`, `nb_conda_kernels` e `cx_oracle`. Não use `--empty` [R10].
- [F] Para publicar no bucket: `odsc conda init -b <bucket> -n <ns> -a resource_principal` e depois `odsc conda publish -s <slug>`, que usa conda-pack [R11].
- [F] Os condas do serviço exigem NAT ou service gateway [R12].
- [I] pip público só com NAT ou mirror. Uma wheel dentro de um published conda funciona sem internet, via service gateway.

**Armazenamento**
- [F] Block volume de 50 a 10.240 GB (padrão 100) [R1].
- [F] Só `/home/datascience` persiste [R4].

**Rede**
- [F] Default networking: VCN gerenciada, com NAT e service gateway, sem políticas de rede [R1].
- [F] Custom networking: VCN/subnet do cliente, mais NAT, mais service gateway, mais a política `allow service datascience to use virtual-network-family` [R1, R15].
- [F] Não dá para mudar de default para custom ao reativar [R1, R13].
- [F] O "private endpoint" do notebook trata de entrada, não de saída [R1, R17].
- [I] Para alcançar um ADW com private endpoint ou o on-prem, é preciso custom networking [R40].

**Resource principal**
- [F] Variáveis disponíveis: `TENANCY_OCID`, `PROJECT_OCID`, `PROJECT_COMPARTMENT_OCID`, `USER_OCID`, `NB_SESSION_OCID`, `NB_SESSION_COMPARTMENT_OCID`, `OCI_RESOURCE_PRINCIPAL_RPT_PATH` e `OCI_RESOURCE_PRINCIPAL_RPT_ID` [R5].
- [F] Use `ads.set_auth(auth='resource_principal')` ou `oci.auth.signers.get_resource_principals_signer()`. Sem isso, o SDK cai para a API key [R5, R20].
- [F] Há um cache de 15 min: mudanças de política demoram até 15 min para valer [R5].
- [F] Dynamic group: `all {resource.type='datasciencenotebooksession', resource.compartment.id='<ocid>'}` [R14].

**Limites**
- [F] Memória de até 64 GB por OCPU. O máximo aparece como 512 GB [R1] ou 1.024 GB [R18]: as fontes divergem.
- [F] Recomenda-se memória de cerca de 3× o volume de dados [R13].
- [F] **Uma célula roda por no máximo cerca de 30 min.** Para processos longos, use terminal com `nohup` [R6].
- [F] Logout por inatividade em 1 h, extensível a 24 h [R7].
- [F] Recomenda-se um usuário por sessão [R6].
- [F] Limite de 10.000 notebooks, modelos e jobs por tenancy [R19].

**Configuração, Git e extensões**
- [F] Variáveis de ambiente na runtime config:
  - até 30 pares;
  - chave com 2–32 caracteres, valor com 1–256;
  - alterar exige reativar a sessão [R1, R3].
- [F] Git na runtime config: até 3 repositórios, **só públicos**, e exige internet. Os repositórios ficam em `/home/datascience/repos`. Repositórios privados, via SSH. A extensão Git está disponível [R3, R16].
- NE: suporte a ipywidgets e anywidget. [I] No JupyterLab 4, o front-end dos widgets precisa estar no ambiente do servidor. Testar na Fase 0 e ter fallback em HTML estático.

## 2. Model Catalog, Jobs e Pipelines

**Artefato**
- [F] O artefato é um zip com o modelo, `score.py` e `runtime.yaml` na raiz. Ele é **imutável** [R21, R24].
- [F] Pelo Console, o limite é 100 MB. Pelo ADS, SDK ou CLI não há esse limite [R21].
- [F] Teto para artefato grande: 400 GB [R21, R22] ou 6 GB [R23]; as fontes divergem.
- [F] Acima de 2 GB, `save()` exige `bucket_uri` [R23].
- [F] Model by Reference: o artefato pode ficar em bucket/prefixo do cliente [R22].

**Metadados**
- [F] Provenance: git URL, branch, commit, diretório, script/notebook e recurso de treino.
- [F] Taxonomy: caso de uso, framework, versão, algoritmo, hiperparâmetros e testes de introspecção.
- [F] Custom metadata com categorias.
- [F] **Taxonomy e custom metadata somam no máximo 32.000 bytes.**
- [F] Os schemas de entrada/saída também têm 32.000 bytes, só podem ser definidos na criação e servem apenas como documentação [R21, R22, R26].

**Version sets e ADS**
- [F] Version sets: o nome é único por compartimento, a versão é automática e o modelo não sai do set [R22, R28].
- [F] Fluxo do ADS: `GenericModel(...)`, depois `prepare()`, `verify()` e `save()`. `ModelProperties` vem de `PROJECT_OCID`/`NB_SESSION_COMPARTMENT_OCID` [R26, R27].

**Outros recursos e permissões**
- [F] Há backup em outra região, retenção (arquivar ou excluir) e assinatura de artefato com chave do Vault (13/10/2026) [R22, R29].
- [F] Permissões necessárias [R14]:
  - criar modelo: `manage data-science-models` + `read data-science-projects`;
  - criar version set: `manage data-science-modelversionsets`.

**[I] Model Catalog como destino de handoff**
- Serve como registro imutável e auditável, com proveniência git, sem deploy.
- Limitações: 32 KB de metadados e schemas só documentais.
- Proposta: o pacote fica no Object Storage como fonte da verdade, e o catálogo guarda o registro que aponta para ele.

**Jobs e Pipelines**
- [F] Jobs fazem parte do Data Science [R15, R30]:
  - infraestrutura efêmera, duração de até 30 dias e block volume de até 1 TB;
  - logs no OCI Logging;
  - dynamic group com `datasciencejobrun`.
- [I] Jobs resolvem cálculos de mais de 30 min sem serviço novo.
- [F] Pipelines são um DAG de passos (scripts ou jobs) [R31].

## 3. ADW a partir do notebook

**Driver e conexão**
- [F] python-oracledb usa o modo Thin por padrão, sem Oracle Client. O modo Thick só é necessário para banco ≤ 11, NNE/checksum ou AC/TAC [R34].
- [F] Duas formas de conexão:
  - **TLS sem wallet:** exige ACL ou private endpoint; `tcps`, porta 1521, `ssl_server_dn_match=yes` [R33, R38].
  - **mTLS:** porta 1522; no modo Thin bastam `tnsnames.ora` + `ewallet.pem`, com `config_dir`, `wallet_location` e `wallet_password` (a senha da wallet é diferente da senha do banco) [R33, R38, R40].
- [F] Proxy: `https_proxy` e `https_proxy_port` [R33].

**Credenciais**
- [F] Config provider no Object Storage: DSN `config-ociobject:`, com a senha no Vault (`"type":"ocivault"`) e autenticação por resource principal [R33].
- [F] O plugin `oci_tokens` (token IAM no banco) aceita resource principal desde a versão 4.0.0 [R37].

**Versões e leitura**
- [F] A versão 26.0.x (set/2026) passou a numerar por ano e removeu o Python 3.9 [R37].
- [F] `fetch_df_all`/`fetch_df_batches` existem desde a 3.0 e saíram de pre-release na 3.4. Para pandas: `pyarrow.table(odf).to_pandas()` [R36, R37].
- [F] `arraysize` padrão é 100. Para resultados grandes, use cerca de 1000 e mantenha `prefetchrows` 2. No `fetch_df_*`, o prefetch é o próprio arraysize [R35].

**Private endpoint e ACL**
- [F] Private endpoint: acesso pela VCN, por VCN pareada ou on-prem via DRG. NSG libera 1522 (mTLS) ou 1521/1522 (TLS). DNS `*.adb.<region>.oraclecloud.com` [R40].
- [F] ACL por VCN quando o tráfego vem do service gateway; pelo NAT, a ACL precisa do IP público [R39].

**Serviços do banco [F, R41]**

| Serviço | Paralelismo | Shares | Concorrência |
|---|---|---|---|
| TPURGENT | manual | 12 | 75 × ECPU sessões |
| TP | nenhum | 8 | 75 × ECPU |
| HIGH | sim | 4 | 3 SQLs (9 com autoscaling), o resto em fila |
| MEDIUM | sim | 2 | 0,25125 × ECPU |
| LOW | nenhum | 1 | 75 × ECPU |

**Dicionário e privilégios**
- [F] `ALL_TABLES` mostra só o que o usuário acessa. `NUM_ROWS`/`BLOCKS`/`AVG_ROW_LEN` só vêm preenchidos com estatísticas [R43].
- [F] `SELECT_CATALOG_ROLE` libera o dicionário. `SELECT ANY DICTIONARY` não alcança SYS/SYSTEM [R42].

**EXPLAIN PLAN, sessão só leitura e controle de recursos**
- [F] EXPLAIN PLAN grava em `PLAN_TABLE`, que é temporária e privada por sessão. É DML e exige privilégio para executar o SQL [R44, R45].
- [F] `DBMS_XPLAN.DISPLAY` mostra rows, bytes e cost, mas o resultado pode divergir da execução real [R44].
- [F] `ALTER SESSION SET READ_ONLY=TRUE` só existe a partir do 26ai [R47]. NE: se EXPLAIN PLAN funciona numa sessão read-only.
- [F] `CS_RESOURCE_MANAGER` define limites de CPU, IO e tempo, com ação `cancel_sql`/`kill_session`, DOP e consumer groups [R46].
- [F] Pelo ADS: `pd.DataFrame.ads.read_sql(sql, connection_parameters=..., bind_variables=..., chunksize=...)`. O ADS usa oracledb desde a 2.6.7 [R32, R48].

## 4. Object Storage

**Acesso**
- [F] ocifs: URI `oci://bucket@namespace/caminho`. A autenticação segue a ordem signer > config > env (`OCIFS_IAM_TYPE`) > resource principal. Use `storage_options` no pandas e `refresh=True` para evitar cache [R50].

**Versionamento, retenção e ciclo de vida**
- [F] Versionamento: não pode ser desligado, só suspenso. Toda versão é cobrada [R51].
- [F] Retention rules: o lock fica irreversível depois de 14 dias, e a regra é incompatível com versionamento [R52].
- [F] Lifecycle: até 1.000 regras, roda cerca de uma vez por dia, exige política para o serviço objectstorage [R53].

**PAR e API S3**
- [F] PAR: quem tem a URL acessa. A expiração é obrigatória e exige `PAR_MANAGE` [R54].
- [F] API compatível com S3:
  - endpoint `https://<ns>.compat.objectstorage.<region>.oci.customer-oci.com`;
  - Customer Secret Key (a secret não pode ser recuperada depois);
  - sem SigV2;
  - buckets criados via S3 vão para o compartimento raiz [R55].
- [F] Customer Secret Keys: até 2 por usuário [R56].

**Criptografia e permissões**
- [F] Criptografia em repouso está sempre ligada. Chave do cliente exige política [R55, R67].
- [F] `use objects` permite ler e sobrescrever; `manage objects` também cria e apaga. Condições: `target.bucket.name` e `target.object.name` [R57].

## 5. Vault

```python
import oci, base64
signer = oci.auth.signers.get_resource_principals_signer()
sc = oci.secrets.SecretsClient(config={}, signer=signer)
b = sc.get_secret_bundle(secret_id=OCID)  # estágio padrão: CURRENT
valor = base64.b64decode(b.data.secret_bundle_content.content).decode()
```

[I] O trecho foi montado com peças oficiais. O exemplo oficial usa arquivo de config.

- [F] Métodos: `get_secret_bundle(secret_id, stage, version_number)` e `get_secret_bundle_by_name(secret_name, vault_id)` [R62].
- [F] Bundle de até **25 KB**. Até 30 versões ativas mais 30 pendentes. Rotação de 1 a 12 meses e réplica em até 3 regiões [R59–R61].
- [F] A política `read secret-bundles` aceita as condições `target.secret.name`/`target.secret.id` [R58].
- [F] O `ADBSecretKeeper` do ADS guarda cada arquivo da wallet como um segredo separado [R49].

## 6. Logging, Monitoring e Audit

- [F] O Audit registra chamadas de API, inclusive leitura de segredo e criação de modelo. **Para Object Storage, só eventos de bucket, não de objeto**: para objetos é preciso ligar os service logs do bucket [R63, R66].
- [F] Retenção do Audit: 365 dias [R63b]. Outras páginas falam em 90 dias configuráveis; as fontes divergem.
- [F] Logs customizados via `oci.loggingingestion.LoggingClient.put_logs`, com a política `use log-content` [R64, R65]. [I] Não exige serviço novo se o log group já existir.
- [F] O notebook expõe métricas de CPU e memória, com alarmes recomendados [R13].

## Parâmetros de ambiente sugeridos

| Parâmetro | Controla | Valores | Quem informa | Padrão seguro |
|---|---|---|---|---|
| `oci.auth_mode` | assinatura | resource_principal / api_key / instance_principal / security_token | Plataforma | resource_principal |
| `oci.region` | endpoints | ex.: sa-saopaulo-1 | Plataforma | detectar |
| `oci.config_file` / `oci.profile` | uso local | caminho / perfil | Dev | ~/.oci/config / DEFAULT |
| `datascience.project_ocid` / `.compartment_ocid` | Model Catalog | OCID | variáveis de ambiente | automático |
| `datascience.model_version_set` | handoff | nome | Industrialização | obrigatório, sem criar sozinho |
| `datascience.inference_conda_slug` / `_type` / `_path` | runtime.yaml | — | Plataforma | conda publicado |
| `datascience.handoff_target` | destino | catalog / bucket / both | Industrialização | both |
| `datascience.cell_budget_s` | corte antes de ~30 min | segundos | Nós | 1500; acima disso, Job |
| `adw.connect_mode` | rede | tls / mtls | Banco | mtls |
| `adw.dsn` / `.tns_alias` / `.host` / `.port` | endereço | 1521 / 1522 | Banco | — |
| `adw.service` | prioridade | low / medium / high / tp / tpurgent | Banco | low |
| `adw.user_secret_ocid` / `.password_secret_ocid` | credenciais | OCID | Vault | obrigatório |
| `adw.wallet_secret_ocids` / `.wallet_dir` / `.wallet_password_secret_ocid` | mTLS | — | Vault | tmp 0700 |
| `adw.driver_mode` | Thin/Thick | thin / thick | Nós | thin |
| `adw.arraysize` / `.prefetchrows` | fetch | inteiros | Nós | 1000 / 2 |
| `adw.fetch_backend` | formato | dataframe / cursor | Nós | dataframe (oracledb ≥ 3.4) |
| `adw.schema_allowlist` | escopo | lista | Banco | vazio = nega |
| `adw.gate.max_cost` / `.max_rows_est` / `.max_bytes_est` | porteiro | números | Banco | conservador |
| `adw.session_read_only` | READ_ONLY | true / false | Nós (26ai) | true se suportado |
| `adw.https_proxy` / `.https_proxy_port` | proxy | — | Plataforma | vazio |
| `objectstorage.namespace` | namespace | — | Plataforma | detectar |
| `objectstorage.state_uri` / `.handoff_uri` / `.staging_uri` | prefixos | oci://… | Plataforma | obrigatório |
| `objectstorage.client` | acesso | ocifs / oci_sdk | Nós | ocifs com signer |
| `vault.vault_ocid` / `.llm_key_secret_ocid` | segredos | OCID | Segurança | obrigatório |
| `vault.secret_stage` / `.cache_ttl_s` | leitura | CURRENT / LATEST; segundos | Nós | CURRENT; 300, só em memória |
| `logging.log_ocid` / `.enabled` | logs | — | Plataforma | false |
| `network.mode_expected` | diagnóstico | default / custom | Plataforma | informativo |

## Políticas IAM mínimas (validar com a plataforma)

```
ALL {resource.type='datasciencenotebooksession', resource.compartment.id='<ocid-cmp-notebooks>'}
allow dynamic-group dg-adex-nb to read secret-bundles in compartment <cmp-vault> where target.secret.name='<nome>'
allow dynamic-group dg-adex-nb to read buckets in compartment <cmp-dados> where target.bucket.name='<bkt-estado>'
allow dynamic-group dg-adex-nb to {OBJECT_INSPECT, OBJECT_READ, OBJECT_CREATE, OBJECT_OVERWRITE} in compartment <cmp-dados> where target.bucket.name='<bkt-estado>'
allow dynamic-group dg-adex-nb to manage objects in compartment <cmp-dados> where target.bucket.name='<bkt-staging>'
allow dynamic-group dg-adex-nb to read data-science-projects in compartment <cmp-ds>
allow dynamic-group dg-adex-nb to manage data-science-models in compartment <cmp-ds>
allow dynamic-group dg-adex-nb to manage data-science-modelversionsets in compartment <cmp-ds>
allow dynamic-group dg-adex-nb to read objects in compartment <cmp> where target.bucket.name='<bkt-conda-publicado>'
allow dynamic-group dg-adex-nb to use log-content in compartment <cmp-log>
allow service datascience to use virtual-network-family in compartment <cmp-rede>
```

## Perguntas ao time de plataforma OCI

1. Região, realm, namespace e compartimentos.
2. Rede:
   - Default ou custom networking?
   - Há NAT?
   - O service gateway cobre todos os serviços?
   - Há proxy ou mirror de PyPI/conda?
   - Existe rota até o GPU Hub, o Langfuse e o ADW, com DNS resolvendo?
3. Shapes, memória e tamanho do block volume.
4. Condas do serviço permitidos? Há bucket de published conda?
5. As sessões já estão no JupyterLab 4.4.6? `ipywidgets`, `jupyterlab_widgets` e `anywidget` estão no servidor?
6. Dynamic group e políticas: quem cria e quem mantém?
7. Vault: OCID, secrets existentes e rotação.
8. Buckets de estado e handoff: versionamento, retenção, lifecycle, chave do cliente (CMK) e tags.
9. Model Catalog: projeto, version sets e convenções. A industrialização lê do catálogo ou do bucket?
10. Log group e custom log. O Audit vai para o SIEM?
11. Jobs liberados (dynamic group `datasciencejobrun`)?
12. Langfuse (blob storage): Customer Secret Key de usuário técnico, ou integração nativa por instance principal?

## Perguntas ao time de banco

1. 19c ou 26ai? Quantos ECPUs? Há autoscaling? Quais serviços estão no tnsnames?
2. Private endpoint ou ACL? mTLS obrigatório? O DNS resolve a partir da subnet do notebook?
3. Usuário só leitura: quais schemas? SELECT ou READ? `SELECT_CATALOG_ROLE`? Autenticação IAM?
4. As estatísticas e os comentários estão em dia?
5. Consumer group dedicado? Quais limites de tempo, IO e DOP, e quantas sessões?
6. EXPLAIN PLAN é permitido? E DISPLAY_CURSOR/V$?
7. Há limite de volume extraído? Existem PII, VPD, Data Safe ou Database Vault?
8. Quem rotaciona a wallet e onde fica a senha?

## Riscos e armadilhas

- Célula de ~30 min, logout por inatividade, cache de 15 min do resource principal.
- A rede não muda ao reativar a sessão. A rede default não alcança private endpoint.
- O ambiente base do conda não persiste. Suporte a widgets desconhecido.
- `odsc conda create` injeta o `cx_oracle`. O oracledb mudou a numeração e removeu o Python 3.9.
- Usuários podem ficar em versões diferentes do JupyterLab.
- Model Catalog:
  - limite de tamanho com fontes divergentes;
  - 32 KB de metadados;
  - schemas imutáveis;
  - modelo preso ao version set.
- Segredo de até 25 KB. Retention lock irreversível e incompatível com versionamento.
- A URL de uma PAR dá acesso a qualquer um. Customer Secret Keys: no máximo 2 por usuário.
- O Audit não cobre objetos.
- HIGH aceita só 3 SQLs simultâneos, e LOW não tem paralelismo.
- EXPLAIN PLAN pode divergir da execução real. `NUM_ROWS` fica nulo sem estatísticas.

## Referências

- R1 https://docs.oracle.com/en-us/iaas/Content/data-science/using/create-notebook-sessions.htm
- R2 https://docs.oracle.com/en-us/iaas/releasenotes/data-science/juptyerlab446.htm
- R3 https://docs.oracle.com/en-us/iaas/data-science/using/notebook-runtime.htm
- R4 https://docs.oracle.com/en-us/iaas/data-science/using/notebook-persist-data.htm
- R5 https://docs.oracle.com/en-us/iaas/data-science/using/use-notebook-sessions.htm
- R6 https://docs.oracle.com/en-us/iaas/data-science/using/notebook-tips.htm
- R7 https://docs.oracle.com/en-us/iaas/data-science/using/notebook-timeout.htm
- R8 https://docs.oracle.com/en-us/iaas/data-science/using/conda_understand_environments.htm
- R9 https://docs.oracle.com/en-us/iaas/data-science/using/conda_installing.htm
- R10 https://docs.oracle.com/en-us/iaas/Content/data-science/using/conda_create_conda_env.htm
- R11 https://docs.oracle.com/en-us/iaas/Content/data-science/using/conda_publishs_object.htm
- R12 https://docs.oracle.com/en-us/iaas/data-science/using/conda_viewing.htm
- R13 https://docs.oracle.com/en-us/iaas/Content/data-science/using/tshoot-notebook.htm
- R14 https://docs.oracle.com/en-us/iaas/data-science/using/policies.htm
- R15 https://docs.oracle.com/en-us/iaas/Content/data-science/using/tutorial/get-started/get-started.htm
- R16 https://docs.oracle.com/en-us/iaas/data-science/using/add-git.htm
- R17 https://docs.oracle.com/en-us/iaas/data-science/using/pe-network.htm
- R18 https://docs.oracle.com/en-us/iaas/data-science/using/supported-shapes.htm
- R19 https://docs.oracle.com/en-us/iaas/Content/General/service-limits/default.htm
- R20 https://accelerated-data-science.readthedocs.io/en/latest/user_guide/cli/authentication.html
- R21 https://docs.oracle.com/en-us/iaas/data-science/using/models-about.htm
- R22 https://docs.oracle.com/en-us/iaas/Content/data-science/using/model-console-save.htm
- R23 https://docs.oracle.com/en-us/iaas/Content/data-science/using/model-large-artifacts.htm
- R24 https://docs.oracle.com/en-us/iaas/Content/data-science/using/models-prepare-artifact.htm
- R25 https://docs.oracle.com/en-us/iaas/Content/data-science/using/model_runtime_yaml.htm
- R26 https://accelerated-data-science.readthedocs.io/en/latest/user_guide/model_registration/model_metadata.html
- R27 https://accelerated-data-science.readthedocs.io/en/latest/user_guide/model_registration/frameworks/genericmodel.html
- R28 https://accelerated-data-science.readthedocs.io/en/v2.12.5/user_guide/model_registration/model_version_set.html
- R29 https://docs.oracle.com/en-us/iaas/releasenotes/data-science/artifact-signature.htm
- R30 https://docs.oracle.com/en-us/iaas/data-science/using/jobs-about.htm
- R31 https://docs.oracle.com/en-us/iaas/data-science/using/pipelines-about.htm
- R32 https://accelerated-data-science.readthedocs.io/en/latest/release_notes.html
- R33 https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html
- R34 https://python-oracledb.readthedocs.io/en/latest/user_guide/initialization.html
- R35 https://python-oracledb.readthedocs.io/en/latest/user_guide/tuning.html
- R36 https://python-oracledb.readthedocs.io/en/latest/user_guide/dataframes.html
- R37 https://python-oracledb.readthedocs.io/en/latest/release_notes.html
- R38 https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/support-tls-mtls-authentication.html
- R39 https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/access-control-rules-autonomous.html
- R40 https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/private-endpoints-autonomous.html
- R41 https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/predefined-database-services-names.html
- R42 https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/autonomous-admin-user-roles.html
- R43 https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/ALL_TABLES.html
- R44 https://docs.oracle.com/en/database/oracle/oracle-database/26/tgsql/generating-and-displaying-execution-plans.html
- R45 https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/EXPLAIN-PLAN.html
- R46 https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/manage-workload-resources-using-dbrm-autonomous-ai-database.html
- R47 https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/READ_ONLY.html
- R48 https://accelerated-data-science.readthedocs.io/en/latest/user_guide/loading_data/connect.html
- R49 https://accelerated-data-science.readthedocs.io/en/latest/user_guide/secrets/autonomous_database.html
- R50 https://ocifs.readthedocs.io/en/latest/
- R51 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingversioning.htm
- R52 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingretentionrules.htm
- R53 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usinglifecyclepolicies.htm
- R54 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingpreauthenticatedrequests.htm
- R55 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm
- R56 https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingcredentials.htm
- R57 https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/objectstoragepolicyreference.htm
- R58 https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/keypolicyreference.htm
- R59 https://docs.oracle.com/en-us/iaas/Content/KeyManagement/Tasks/tasks_managingsecrets_topic_to_update_a_secret_dita.htm
- R60 https://docs.oracle.com/en-us/iaas/Content/KeyManagement/Concepts/secretversionsrotationstates_topic-Secret_Versions.htm
- R61 https://docs.oracle.com/en-us/iaas/Content/secret-management/Concepts/manage-secrets.htm
- R62 https://docs.oracle.com/en-us/iaas/tools/python/latest/api/secrets/client/oci.secrets.SecretsClient.html
- R63 https://docs.oracle.com/en-us/iaas/Content/Audit/Concepts/auditoverview.htm
- R63b https://docs.oracle.com/en-us/iaas/Content/Audit/Tasks/settingretentionperiod.htm
- R64 https://docs.oracle.com/en-us/iaas/Content/Logging/Concepts/custom_logs.htm
- R65 https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/loggingpolicyreference.htm
- R66 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/logging.htm
- R67 https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/usingyourencryptionkeys.htm
