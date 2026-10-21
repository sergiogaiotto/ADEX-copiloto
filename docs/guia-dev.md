# Guia do dev: do zero a um estudo entregue

Você chega com uma pergunta. O copiloto enquadra, monta a carta com você, descobre os dados, conhece o universo, planeja
com blocos de método, roda, critica e entrega um pacote que a equipe de produção usa sem o copiloto. Em cada portão,
quem decide é você.

## 1. Começar

```python
from adex_copiloto import AdexCopiloto

adex = AdexCopiloto.from_env()
estudo = adex.create_session(objective="Quais clientes têm mais risco de cancelar nos próximos 3 meses?")
estudo.frame()
```

Nenhum dado é lido e nenhuma IA é chamada até o `frame()`. Ele mostra como o copiloto entendeu (com certeza por item),
até 3 perguntas (cada uma diz por que pergunta) e os atalhos com a aderência contada pelo Python.

Responda numa rodada só:

```python
carta = estudo.frame(answers="""1. ...
2. ...
3. ...""")
carta                      # 10 campos e a origem de cada um: você disse ✓ · você escolheu · sugerido · padrão
carta.edit("desfecho", "...")
carta.approve()
```

"Você disse" só fica se o trecho existe **literalmente** no seu texto (o Python confere). Frases que mudam o método
aparecem destacadas e viram hipóteses H-xx.

## 2. Dados

```python
estudo.discover(hint="acho que tem algo no esquema OPS_RES")   # só metadados
estudo.mapping().set(grupos=["COD_CIDADE"])                     # ajuste, se precisar
estudo.confirm_mapping()
estudo.run("universo")                                           # agregado na fonte
estudo.passport().approve()
```

- A IA vê nome, tipo, comentário mascarado e contagem de linhas. Nunca valores, mínimos, máximos ou histogramas.
- Nome inventado é descartado; ligações são testadas com contagens; códigos de motivo são classificados a partir do
  dicionário (sem clientes), e os em dúvida viram decisão sua.
- O Passaporte de Amostragem registra método, seed, cobertura, comparação com o universo e riscos de viés. Ele nunca
  declara "sem viés" sem evidência.

## 3. Plano

```python
plano = estudo.plan()                       # ou estudo.plan(playbook="classificacao")
plano.add_step("codigos_estaveis", adex.blocks.get("desfecho_estavel", "1.0"), before="efeito")
plano.set("efeito", ponderar_por="unidades")
plano.diff()
plano.approve()
```

O plano mostra os **jeitos de responder** (o que cada um responde e o que **não** responde), o método composto por
blocos registrados e o **pré-mortem**. O Python confere bloco, versão, parâmetros e a ordem dos dados.

## 4. Rodar, revisar, aceitar, entregar

```python
estudo.run()                    # ou estudo.run("efeito")
estudo.decide("D-03", "a", reason="...")
estudo.review()                 # crítico (Python + Qwen) e narrativa com números do Python
estudo.accept(reason="...")
h = estudo.export_handoff(incluir_dados="nenhum")      # ou "agregados" (só resultados agregados)
h.approve()                                            # publica
h.register_model()                                     # opcional: Model Catalog
```

O aceite exige corrigir os bloqueios do Python e decidir (aceitar ou ajustar) cada alerta. O pacote traz
`src/adex_metodos.py` e `src/pipeline.py`, que refazem os cálculos sem o copiloto. Com `incluir_dados="agregados"`, o
pipeline roda na hora e precisa dar o mesmo efeito do estudo; senão o pacote é recusado.

Voltar é sempre permitido, pular um portão nunca:

- `estudo.run("efeito", n_boot=999)` num plano aprovado gera a **versão nova** do plano, que precisa de aprovação
  (`estudo.plan_view().approve()`) antes de rodar;
- mudar a carta, o mapeamento (`estudo.mapping().set(...)`) ou o plano depois de aprovados reabre só o que depende
  deles, até o portão certo (passaporte, plano), e um estudo aceito volta a "em andamento";
- rodar um passo de novo substitui o que ele tinha produzido; decisões já tomadas continuam valendo;
- estudo descartado ou entregue não muda mais: para seguir, crie um estudo novo.

## 5. Conversar, custos, SQL próprio

```python
estudo.chat("Por que o placebo importa aqui?")
estudo.costs()
%load_ext adex_copiloto
```

```sql
%%adex sql --para df
SELECT b.cod_cidade, COUNT(*) n FROM cad.base_fibra_mensal b GROUP BY b.cod_cidade
```

O seu SQL passa pelo mesmo porteiro que o da IA. Modo detalhe (`--modo detalhe`) traz linhas ao kernel (nunca à IA),
sempre sem colunas pessoais e com teto.

## 6. Estender o copiloto

**Ferramenta própria** (a IA só usa depois dos testes de contrato, sem IA):

```python
@adex.tool(name="calendario_ondas", reads=["oci://eng/rollout/ondas.xlsx"],
           returns={"cod_ibge": "str", "onda": "str"}, privacy="aggregate", scope="study",
           exemplo={"cidades": ["3550308"]})
def calendario_ondas(cidades: list[str]) -> pd.DataFrame:
    with adex.io.open("oci://eng/rollout/ondas.xlsx") as f:
        df = pd.read_excel(f, dtype={"cod_ibge": str})
    return df[df.cod_ibge.isin(cidades)][["cod_ibge", "onda"]]

estudo.tools.test("calendario_ondas")
estudo.chat("Confere as cidades A, B e C no calendario_ondas")
adex.tools.scaffold("calendario_ondas", "ler a planilha da Engenharia")   # rascunho do Qwen na próxima célula
```

**Bloco próprio** (versão imutável; a IA só monta planos com blocos registrados):

```python
@adex.block(nome="minha_medida", versao="1.0", requer=("painel",), produz=("minha_tabela",))
def minha_medida(ctx, janela: int = 3) -> dict:
    p = ctx.dados["painel"]
    return {"minha_tabela": p.groupby("grupo").tail(janela)}
```

**Atalhos:** `estudo.save_as_playbook("efeito_em_ondas")` e `adex.lenses.draft(estudo, "operacoes_residenciais")`.
Para a equipe, atalhos e lentes precisam de curadoria (`curado = false` até lá).

## 7. Retomar

```python
adex.studies()
estudo = adex.resume("EST-2026-0142")      # estado, dossiê e orçamento continuam
```

## 8. Experimentar sem a OCI

```python
ex = adex.exemplo("wifi6")                 # também "churn" e "receita" (dados sintéticos)
estudo = adex.create_session(objective=ex.objetivo, data_sources=ex.fonte(adex.config))
```
