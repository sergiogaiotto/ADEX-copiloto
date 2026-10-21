"""Dados sintéticos para experimentar o copiloto sem a OCI (e para os testes).

Nada aqui é dado real. Os casos reproduzem situações típicas e transversais:

- ``wifi6``: efeito de uma ação implantada em ondas (troca de roteadores), com
  seleção por pico de reclamação antes da 1ª onda, datas fora das ondas e
  códigos de motivo ambíguos — o caso "do zero" do mock;
- ``churn``: classificação (quem cancela nos próximos 3 meses);
- ``receita``: regressão (o que se associa à receita de lojas).

Uso::

    ex = adex.exemplo("wifi6")      # ou adex_copiloto.exemplos.carregar("wifi6")
    estudo = adex.create_session(objective=ex.objetivo, data_sources=ex.fonte(adex.config))
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from adex_copiloto.estudo.mapeamento import FonteAlvo, FonteDesfecho, FonteTratamento, Ligacao, Mapeamento


@dataclass
class Exemplo:
    nome: str
    objetivo: str
    respostas: str
    tabelas: dict[str, pd.DataFrame]
    comentarios: dict[str, str] = field(default_factory=dict)
    mapeamento: Mapeamento | None = None
    sem_acesso: list[str] = field(default_factory=list)
    ondas_declaradas: list[str] = field(default_factory=list)

    def fonte(self, cfg):
        from adex_copiloto.dados.fontes import FonteLocal

        f = FonteLocal.de_dataframes(cfg, self.tabelas, comentarios=self.comentarios)
        for t in self.sem_acesso:
            f.negar_acesso(t)
        return f


MOTIVOS = [
    (3114, "WIFI LENTO / QUEDA FREQUENTE"), (3121, "ROTEADOR REINICIANDO"), (3133, "SINAL WIFI FRACO EM COMODO"),
    (3140, "WIFI NAO CONECTA"), (3152, "TROCA DE SENHA WIFI - SUPORTE"), (3190, "SENHA WIFI - ORIENTACAO"),
    (3207, "PONTO MESH - CONTRATACAO"), (2101, "FATURA - SEGUNDA VIA"), (2105, "FATURA - CONTESTACAO"),
    (4100, "SEM SINAL - FIBRA ROMPIDA"), (4110, "LENTIDAO CABEADA"), (5200, "MUDANCA DE ENDERECO"),
    (5210, "CANCELAMENTO - INFORMACAO"), (6100, "TV - SEM SINAL"), (6110, "TV - CONTROLE"),
]
CODIGOS_WIFI = [3114, 3121, 3133, 3140, 3152]


def _cpf_falso(rng: np.random.Generator, n: int) -> list[str]:
    # Formato de CPF com dígitos aleatórios (não são CPFs válidos de propósito).
    d = rng.integers(0, 10, size=(n, 11))
    return [f"{a}{b}{c}.{e}{f}{g}.{h}{i}{j}-0{k}" for a, b, c, e, f, g, h, i, j, _, k in d]


def wifi6(seed: int = 7, *, cidades: int = 41, domicilios_por_cidade: tuple[int, int] = (160, 420)) -> Exemplo:
    rng = np.random.default_rng(seed)
    meses = pd.date_range("2024-01-01", "2026-09-01", freq="MS")
    codigos = [f"C{i:02d}" for i in range(1, cidades + 1)]
    ondas: dict[str, pd.Timestamp | None] = {}
    for i, c in enumerate(codigos):
        if i < 9:
            ondas[c] = pd.Timestamp("2025-03-01")
        elif i < 18:
            ondas[c] = pd.Timestamp("2025-06-01")
        elif i < 24:
            ondas[c] = pd.Timestamp("2025-09-01")
        elif i < 26:
            ondas[c] = pd.Timestamp("2025-08-01")  # atraso por falta de estoque
        elif i < 27:
            ondas[c] = pd.Timestamp("2024-11-01")  # piloto com modelo antigo
        else:
            ondas[c] = None  # onda 4 ainda não aconteceu
    base_linhas, trocas, chamados = [], [], []
    id_dom = 100_000
    id_chamado = 9_000_000
    for c in codigos:
        n = int(rng.integers(*domicilios_por_cidade))
        doms = np.arange(id_dom, id_dom + n)
        id_dom += n
        taxa_cidade = rng.uniform(0.020, 0.040)
        onda = ondas[c]
        troca_dom: dict[int, pd.Timestamp] = {}
        if onda is not None:
            for d in doms:
                if rng.uniform() < 0.88:
                    troca_dom[d] = onda + pd.Timedelta(days=int(rng.integers(0, 12)))
                elif rng.uniform() < 0.5:
                    troca_dom[d] = onda + pd.DateOffset(months=int(rng.integers(1, 4)))
            for d, dt in troca_dom.items():
                trocas.append({"id_dom": d, "dt_troca": dt, "modelo_novo": "WIFI6-AX3000" if onda.year >= 2025 else "WIFI5-AC1200"})
        cpfs = _cpf_falso(rng, n)
        for k, mes in enumerate(meses):
            for j, d in enumerate(doms):
                base_linhas.append((d, cpfs[j], c, mes, "RESIDENCIAL", "ATIVO"))
            sazonal = 1 + 0.15 * np.sin(2 * np.pi * (mes.month - 1) / 12)
            lam = np.full(n, taxa_cidade * sazonal)
            if onda is not None and onda == pd.Timestamp("2025-03-01") and onda - pd.DateOffset(months=3) <= mes < onda:
                lam *= 1.34  # pico de reclamação que motivou a escolha da 1ª onda
            if troca_dom:
                tratado = np.array([d in troca_dom and troca_dom[d] < mes + pd.offsets.MonthEnd(0) for d in doms])
                lam = np.where(tratado, lam * (0.75 if onda.year >= 2025 else 0.95), lam)
            n_wifi = rng.poisson(lam)
            n_outros = rng.poisson(0.03, n)
            for j, d in enumerate(doms):
                for _ in range(int(n_wifi[j])):
                    cod = int(rng.choice(CODIGOS_WIFI, p=[0.35, 0.25, 0.2, 0.15, 0.05]))
                    chamados.append((id_chamado, d, mes + pd.Timedelta(days=int(rng.integers(0, 27))), cod))
                    id_chamado += 1
                for _ in range(int(n_outros[j])):
                    cod = int(rng.choice([2101, 2105, 4100, 4110, 5200, 5210, 6100, 6110, 3190, 3207]))
                    chamados.append((id_chamado, d, mes + pd.Timedelta(days=int(rng.integers(0, 27))), cod))
                    id_chamado += 1
    base = pd.DataFrame(base_linhas, columns=["id_dom", "cpf", "cod_cidade", "mes", "segmento", "status"])
    chamado = pd.DataFrame(chamados, columns=["id_chamado", "id_dom", "dt_abertura", "cod_motivo"])
    troca = pd.DataFrame(trocas)
    motivo = pd.DataFrame(MOTIVOS, columns=["cod_motivo", "descricao"])
    visitas = chamado.sample(frac=0.2, random_state=seed)[["id_dom", "dt_abertura"]].rename(columns={"dt_abertura": "dt_visita"})
    mapeamento = Mapeamento(
        populacao="CAD.BASE_FIBRA_MENSAL", unidade="ID_DOM", tempo="MES", grupos=["COD_CIDADE"],
        desfechos=[FonteDesfecho(nome="chamados_wifi", tabela="OPS_RES.CHAMADO_TECNICO", chave_unidade="ID_DOM",
                                 coluna_data="DT_ABERTURA", coluna_codigo="COD_MOTIVO", codigos=CODIGOS_WIFI, principal=True,
                                 descricao="chamados de Wi-Fi por 1.000 domicílios")],
        tratamento=FonteTratamento(tabela="OPS_RES.TROCA_EQUIPAMENTO", chave_unidade="ID_DOM", coluna_data="DT_TROCA", limiar=0.5),
        ligacoes=[Ligacao(de_tabela="OPS_RES.CHAMADO_TECNICO", de_coluna="ID_DOM", para_tabela="CAD.BASE_FIBRA_MENSAL", para_coluna="ID_DOM"),
                  Ligacao(de_tabela="OPS_RES.TROCA_EQUIPAMENTO", de_coluna="ID_DOM", para_tabela="CAD.BASE_FIBRA_MENSAL", para_coluna="ID_DOM")],
        periodo_inicio="2024-01-01", periodo_fim="2026-10-01",
    )
    return Exemplo(
        nome="wifi6",
        objetivo="A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?",
        respostas=(
            "1. Decidir se a onda 4 (14 cidades, uns R$ 21 mi) sai em 2027. O comitê decide em 28/11.\n"
            "2. Por cidade, em ondas: mar, jun e set/2025. A Engenharia escolheu a ordem,\n"
            "   acho que pelas cidades com mais reclamação.\n"
            "3. Menos chamado técnico de Wi-Fi. Visita técnica é o que mais custa.\n"
        ),
        tabelas={
            "CAD.BASE_FIBRA_MENSAL": base,
            "OPS_RES.CHAMADO_TECNICO": chamado,
            "OPS_RES.TROCA_EQUIPAMENTO": troca,
            "REF.MOTIVO_CHAMADO": motivo,
            "CAMPO.VISITA_TECNICA": visitas,
        },
        comentarios={
            "CAD.BASE_FIBRA_MENSAL": "Domicílios de fibra ativos por mês",
            "OPS_RES.CHAMADO_TECNICO": "Chamados técnicos com motivo e data de abertura",
            "OPS_RES.TROCA_EQUIPAMENTO": "Trocas de roteador por domicílio",
            "REF.MOTIVO_CHAMADO": "Dicionário de motivos de chamado",
            "CAMPO.VISITA_TECNICA": "Visitas técnicas de campo",
        },
        mapeamento=mapeamento,
        sem_acesso=["CAMPO.VISITA_TECNICA"],
        ondas_declaradas=["2025-03", "2025-06", "2025-09"],
    )


def churn(seed: int = 11, *, clientes: int = 6000) -> Exemplo:
    rng = np.random.default_rng(seed)
    ids = np.arange(1, clientes + 1)
    uf = rng.choice(["SP", "RJ", "MG", "BA", "PR", "RS"], clientes, p=[0.35, 0.15, 0.15, 0.12, 0.12, 0.11])
    plano = rng.choice(["300MB", "500MB", "1GB"], clientes, p=[0.5, 0.35, 0.15])
    tempo = rng.integers(1, 96, clientes)
    fatura = np.round(rng.normal(120, 25, clientes) + np.where(plano == "1GB", 60, 0), 2)
    chamados_3m = rng.poisson(0.6, clientes)
    atrasos_6m = rng.poisson(0.3, clientes)
    logit = -3.0 + 0.5 * chamados_3m + 0.6 * atrasos_6m - 0.02 * tempo + 0.004 * (fatura - 120)
    cancela = rng.uniform(size=clientes) < 1 / (1 + np.exp(-logit))
    ref = pd.Timestamp("2026-06-01")
    base = pd.DataFrame({"id_cliente": ids, "mes": ref, "uf": uf, "plano": plano, "tempo_casa_meses": tempo,
                         "valor_fatura": fatura, "chamados_3m": chamados_3m, "atrasos_6m": atrasos_6m})
    canc = pd.DataFrame({"id_cliente": ids[cancela],
                         "dt_cancelamento": ref + pd.to_timedelta(rng.integers(1, 88, int(cancela.sum())), unit="D")})
    mapeamento = Mapeamento(
        populacao="CRM.CLIENTE_MENSAL", unidade="ID_CLIENTE", tempo="MES", grupos=["UF"],
        atributos=["UF", "PLANO", "TEMPO_CASA_MESES", "VALOR_FATURA", "CHAMADOS_3M", "ATRASOS_6M"],
        alvo=FonteAlvo(tabela="CRM.CANCELAMENTO", chave_unidade="ID_CLIENTE", coluna="DT_CANCELAMENTO", tipo="binario",
                       coluna_data="DT_CANCELAMENTO", descricao="cancelou nos 3 meses seguintes"),
    )
    return Exemplo(
        nome="churn",
        objetivo="Quais clientes de fibra têm mais risco de cancelar nos próximos 3 meses?",
        respostas="1. Priorizar a lista de retenção de julho; a capacidade é de 2.000 contatos por dia.\n2. Cancelamento em até 3 meses.\n",
        tabelas={"CRM.CLIENTE_MENSAL": base, "CRM.CANCELAMENTO": canc},
        comentarios={"CRM.CLIENTE_MENSAL": "Foto mensal dos clientes de fibra", "CRM.CANCELAMENTO": "Pedidos de cancelamento"},
        mapeamento=mapeamento,
    )


def receita(seed: int = 3, *, lojas: int = 180) -> Exemplo:
    rng = np.random.default_rng(seed)
    meses = pd.date_range("2025-01-01", periods=18, freq="MS")
    linhas = []
    for l in range(1, lojas + 1):
        regiao = rng.choice(["Sudeste", "Sul", "Nordeste", "Norte", "Centro-Oeste"])
        area = rng.uniform(80, 600)
        for m in meses:
            promo = int(rng.integers(0, 5))
            receita_ = 50 + 0.4 * area + 12 * promo + (20 if regiao == "Sudeste" else 0) + rng.normal(0, 25)
            linhas.append({"cod_loja": f"L{l:03d}", "mes": m, "regiao": regiao, "area_m2": round(area, 1),
                           "promocoes": promo, "receita_mil": round(receita_, 2), "clientes": int(rng.integers(800, 4000))})
    df = pd.DataFrame(linhas)
    return Exemplo(
        nome="receita",
        objetivo="Investigar fatores associados à receita das lojas",
        respostas="1. Revisar o plano de promoções de 2027.\n2. Receita mensal por loja.\n",
        tabelas={"VAREJO.VENDAS_LOJA_MES": df},
        comentarios={"VAREJO.VENDAS_LOJA_MES": "Receita mensal por loja (mil R$)"},
        mapeamento=Mapeamento(populacao="VAREJO.VENDAS_LOJA_MES", unidade="COD_LOJA", tempo="MES", grupos=["REGIAO"],
                              atributos=["REGIAO", "AREA_M2", "PROMOCOES", "RECEITA_MIL", "CLIENTES"]),
    )


EXEMPLOS = {"wifi6": wifi6, "churn": churn, "receita": receita}


def carregar(nome: str, **kw) -> Exemplo:
    if nome not in EXEMPLOS:
        raise KeyError(f"exemplo desconhecido: {nome}. Opções: {', '.join(EXEMPLOS)}")
    return EXEMPLOS[nome](**kw)
