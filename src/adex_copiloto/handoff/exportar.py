"""Pacote de handoff independente do Harness (spec, seção 14).

    handoff_<estudo>/
      README.md  manifest.json  environment.lock
      notebooks/  src/  sql/  data_contracts/
      sampling_passport.json  metrics.json  validations/  reports/
      decisions.md  limitations.md

- ``src/adex_metodos.py`` é a cópia do módulo de métodos (autossuficiente);
- ``src/pipeline.py`` refaz os passos na ordem do estudo usando os SQLs de ``sql/``;
- nenhum arquivo importa ``adex_copiloto`` (conferido por análise do código e por
  execução num processo isolado);
- dados NÃO entram no pacote, a não ser ``incluir_dados="agregados"`` (só
  resultados agregados com grupo mínimo), por decisão registrada do dev.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import inspect
import json
import platform
import shutil
import subprocess
import sys
import textwrap
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from adex_copiloto.erros import HandoffErro
from adex_copiloto.harness import estado as E

PACOTES_LOCK = ("numpy", "pandas", "scipy", "statsmodels", "scikit-learn", "pyarrow", "matplotlib", "oracledb")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _escrever(p: Path, conteudo: str | bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(conteudo, bytes):
        p.write_bytes(conteudo)
    else:
        p.write_text(conteudo, encoding="utf-8")


def _lock() -> str:
    linhas = [f"# python {platform.python_version()} ({platform.system()})"]
    for pkg in PACOTES_LOCK:
        try:
            linhas.append(f"{pkg}=={version(pkg)}")
        except PackageNotFoundError:
            continue
    return "\n".join(linhas) + "\n"


PIPELINE_CABECALHO = '''"""Pipeline reprodutível do estudo {estudo} — gerado pelo ADEX Copiloto em {quando}.

Não depende do copiloto. Para rodar:

    python src/pipeline.py --offline     # usa data/*.parquet, se o pacote trouxer agregados
    python src/pipeline.py               # consulta a fonte (configure ADEX_DSN, ADEX_USUARIO, ADEX_SENHA)

A equipe de produção deve revisar, testar e adaptar (agendamento, logs, segredos).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))
import adex_metodos as M  # noqa: E402

SEED = {seed}
OFFLINE = "--offline" in sys.argv
_conexao = None


def _conectar():
    global _conexao
    if _conexao is None:
        import oracledb

        _conexao = oracledb.connect(user=os.environ["ADEX_USUARIO"], password=os.environ["ADEX_SENHA"], dsn=os.environ["ADEX_DSN"],
                                    config_dir=os.environ.get("ADEX_WALLET"), wallet_location=os.environ.get("ADEX_WALLET"),
                                    wallet_password=os.environ.get("ADEX_WALLET_SENHA"))
    return _conexao


def consulta(passo: str, nome: str) -> pd.DataFrame:
    """Lê o resultado de um SQL do estudo: do arquivo agregado (offline) ou da fonte."""
    base = f"{{passo}}__{{nome}}"
    if OFFLINE:
        arq = RAIZ / "data" / f"{{base}}.parquet"
        if not arq.exists():
            raise SystemExit(f"modo offline sem o arquivo {{arq.name}} (o pacote não trouxe dados)")
        return pd.read_parquet(arq)
    sql = (RAIZ / "sql" / f"{{base}}.sql").read_text(encoding="utf-8")
    binds = json.loads((RAIZ / "sql" / f"{{base}}.binds.json").read_text(encoding="utf-8"))
    with _conectar().cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(sql, binds)
        nomes = [d[0].lower() for d in cur.description]
        df = pd.DataFrame.from_records(cur.fetchall(), columns=nomes)
    _conexao.rollback()
    return df


dados: dict = {{}}
'''

PIPELINE_RODAPE = '''

def _resumo(valor):
    if isinstance(valor, pd.DataFrame):
        return {"linhas": len(valor), "colunas": list(map(str, valor.columns))[:20]}
    if isinstance(valor, dict):
        return {k: (v if isinstance(v, (int, float, str, bool)) or v is None else type(v).__name__) for k, v in valor.items()}
    return str(type(valor).__name__)


if __name__ == "__main__":
    saida = {k: _resumo(v) for k, v in dados.items()}
    for chave in ("efeito",):
        if chave in dados and isinstance(dados[chave], dict):
            saida[chave] = {k: dados[chave][k] for k in ("att", "ic_inf", "ic_sup", "p_valor") if k in dados[chave]}
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))
'''


def gerar_pipeline(estado: dict, estudo_id: str, seed: int) -> str:
    quando = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    partes = [PIPELINE_CABECALHO.format(estudo=estudo_id, quando=quando, seed=seed)]
    for passo, r in (estado.get("resultados") or {}).items():
        if r.get("status") != "feito":
            continue
        codigo = (r.get("codigo") or "").strip()
        partes.append(f"\n# --- passo '{passo}' ({r.get('bloco')}) ---")
        if not codigo or codigo.startswith("#"):
            partes.append(f"# {codigo.lstrip('# ') or 'sem cálculo equivalente: ver reports/ e sql/'}")
            continue
        partes.append(codigo)
    partes.append(PIPELINE_RODAPE)
    return "\n".join(partes)


def _imports(caminho: Path) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes.add(no.module.split(".")[0])
    return nomes


def _comparar_saida(saida: str, esperado: dict[str, Any]) -> list[str]:
    """O pipeline do pacote precisa dar o mesmo número que o estudo (estimativa pontual, sem bootstrap)."""
    import math

    try:
        obtido = json.loads(saida)
    except ValueError:
        return ["a saída do pipeline offline não é JSON; não deu para conferir os números"]
    problemas = []
    for chave, valores in esperado.items():
        for nome, valor in valores.items():
            if valor is None or (isinstance(valor, float) and not math.isfinite(valor)):
                continue
            achado = (obtido.get(chave) or {}).get(nome)
            if not isinstance(achado, (int, float)) or not math.isclose(float(achado), float(valor), rel_tol=1e-6, abs_tol=1e-9):
                problemas.append(f"o pipeline do pacote não reproduz o estudo: {chave}.{nome} = {achado!r}, no estudo {valor!r}")
    return problemas


def verificar_independencia(pasta: Path, *, executar: bool = False, timeout: int = 600,
                            esperado: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nenhum .py importa adex_copiloto; opcionalmente roda o pipeline offline num processo isolado.

    Com ``esperado`` (ex.: ``{"efeito": {"att": -12.5}}``), confere que o pipeline dá os mesmos números.
    """
    problemas = []
    for py in sorted(pasta.rglob("*.py")):
        if "adex_copiloto" in _imports(py):
            problemas.append(f"{py.relative_to(pasta)} importa adex_copiloto")
        try:
            compile(py.read_text(encoding="utf-8"), str(py), "exec")
        except SyntaxError as e:
            problemas.append(f"{py.relative_to(pasta)} não compila: {e}")
    resultado: dict[str, Any] = {"ok": not problemas, "problemas": problemas, "executado": False}
    if executar and not problemas and (pasta / "data").exists():
        proc = subprocess.run(
            [sys.executable, "-I", str(pasta / "src" / "pipeline.py"), "--offline"],
            cwd=str(pasta), capture_output=True, text=True, timeout=timeout,
        )
        resultado.update({"executado": True, "codigo_saida": proc.returncode, "saida": proc.stdout[-4000:],
                          "erro": proc.stderr[-2000:]})
        if proc.returncode != 0:
            resultado["ok"] = False
            resultado["problemas"].append("o pipeline offline falhou: " + proc.stderr.strip().splitlines()[-1][:300] if proc.stderr.strip() else "falhou")
        elif esperado:
            diferencas = _comparar_saida(proc.stdout, esperado)
            resultado["reproduz"] = not diferencas
            if diferencas:
                resultado["ok"] = False
                resultado["problemas"].extend(diferencas)
    return resultado


def _decisions_md(estado: dict) -> str:
    linhas = ["# Decisões do estudo", ""]
    for p in estado.get("portoes") or []:
        linhas.append(f"- **Portão {p['portao']}** — {p['acao']} por {p['por']} em {p['em']}" + (f" ({p.get('motivo')})" if p.get("motivo") else ""))
    linhas.append("")
    for d in E.decisoes(estado):
        escolha = d.opcao_escolhida.rotulo if d.opcao_escolhida else "(em aberto)"
        linhas.append(f"## {d.id} · {d.titulo}\n\n{d.pergunta}\n\n- Escolha: **{escolha}**" + (f"\n- Motivo: {d.motivo}" if d.motivo else "")
                      + (f"\n- Por: {d.autor} em {d.em}" if d.autor else "") + "\n")
    return "\n".join(linhas)


def _limitations_md(estado: dict, marcadores) -> str:
    from adex_copiloto.llm.marcadores import preencher

    linhas = ["# Limitações e riscos", ""]
    pa = E.passaporte(estado)
    if pa:
        linhas += ["## Amostragem", *[f"- {r}" for r in pa.riscos_vies + pa.limitacoes], ""]
    abertas = [l for l in E.lacunas(estado) if l.status == "aberta"]
    if abertas:
        linhas += ["## Lacunas em aberto", *[f"- {l.id}: {l.descricao}" + (f" — {l.impacto}" if l.impacto else "") for l in abertas], ""]
    hip = [a for a in E.afirmacoes(estado) if a.tipo == "hipotese"]
    if hip:
        linhas += ["## Hipóteses não confirmadas", *[f"- {a.id} (selo {a.selo}): {preencher(a.texto, marcadores)}" for a in hip], ""]
    achados = [a for a in estado.get("achados") or [] if a.get("severidade") in ("alerta", "bloqueio")]
    if achados:
        linhas += ["## Riscos apontados pelo crítico", *[f"- {a['id']} ({a['severidade']}, {a['origem']}): {a['texto']}" for a in achados], ""]
    linhas += ["## Escopo", f"- Fora do escopo: {E.carta(estado).valor('fora_escopo')}" if E.carta(estado) else "-"]
    return "\n".join(linhas) + "\n"


def _relatorio_html(estado: dict, marcadores, figuras: list[tuple[str, str]]) -> str:
    from html import escape

    from adex_copiloto.llm.marcadores import preencher

    carta = E.carta(estado)
    narr = (estado.get("narrativa") or {}).get("html") or ""
    partes = [f"<!doctype html><meta charset='utf-8'><title>Relatório {escape(estado.get('estudo_id', ''))}</title>",
              "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.5}"
              "b.adex-num{background:#eef4ff;padding:0 .2em;border-radius:3px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}</style>",
              f"<h1>{escape(carta.valor('pergunta_decisao') if carta else estado.get('objetivo', ''))}</h1>",
              f"<p><i>Estudo {escape(estado.get('estudo_id', ''))} · carta v{carta.versao if carta else '?'}</i></p>",
              "<h2>Conclusão</h2>", f"<div>{narr}</div>", "<h2>Afirmações</h2><ul>"]
    for a in E.afirmacoes(estado):
        partes.append(f"<li><b>{escape(a.tipo)}</b> (selo {a.selo}): {preencher(escape(a.texto), marcadores, html=True)}</li>")
    partes.append("</ul><h2>Figuras</h2>")
    for arquivo, descricao in figuras:
        partes.append(f"<figure><img src='{escape(arquivo)}' alt='{escape(descricao)}' style='max-width:100%'><figcaption>{escape(descricao)}</figcaption></figure>")
    return "\n".join(partes)


def exportar_pacote(servicos, estado: dict, *, destino: str | None = None, incluir_dados: str = "nenhum") -> dict[str, Any]:
    """Gera o pacote na pasta do estudo (e, se ``destino`` for oci://, prepara a publicação)."""
    estudo_id = estado["estudo_id"]
    pasta = servicos.pasta / "handoff" / f"handoff_{estudo_id}"
    if pasta.exists():
        shutil.rmtree(pasta)
    pasta.mkdir(parents=True)
    marcadores = E.marcadores(estado)
    carta = E.carta(estado)
    # src: métodos + pipeline
    from adex_copiloto.blocos import estatistica

    _escrever(pasta / "src" / "adex_metodos.py", inspect.getsource(estatistica))
    _escrever(pasta / "src" / "pipeline.py", gerar_pipeline(estado, estudo_id, int(estado.get("seed", 42))))
    # sql + contratos de dados
    tabelas_usadas: set[str] = set()
    for passo, r in (estado.get("resultados") or {}).items():
        for c in r.get("consultas") or []:
            base = f"{passo}__{c['nome']}"
            _escrever(pasta / "sql" / f"{base}.sql", c["sql"] + "\n")
            _escrever(pasta / "sql" / f"{base}.binds.json", json.dumps(c["binds"], ensure_ascii=False, indent=1, default=str))
            for t in _tabelas_do_sql(c["sql"]):
                tabelas_usadas.add(t)
    if servicos.fonte is not None:
        cat = servicos.fonte.catalogo()
        for t in sorted(tabelas_usadas):
            f = cat.resolver(t)
            if f is not None:
                contrato = {"tabela": f.nome_completo, "fonte": f.fonte,
                            "colunas": [{"nome": c.nome, "tipo": c.tipo, "anulavel": c.anulavel, "dado_pessoal": c.bloqueada,
                                         "identificador": c.identificador} for c in f.colunas]}
                _escrever(pasta / "data_contracts" / f"{f.nome_completo}.json", json.dumps(contrato, ensure_ascii=False, indent=1))
    # passaporte, métricas, validações
    pa = E.passaporte(estado)
    _escrever(pasta / "sampling_passport.json", json.dumps(pa.para_handoff() if pa else {"metodo": "nao_se_aplica"}, ensure_ascii=False, indent=1))
    metricas = [{"id": v.id, "valor": E.valor_json(v.valor), "texto": v.texto, "descricao": v.descricao, "unidade": v.unidade,
                 "origem": v.origem} for v in marcadores.valores.values()]
    _escrever(pasta / "metrics.json", json.dumps(metricas, ensure_ascii=False, indent=1, default=str))
    _escrever(pasta / "validations" / "achados_do_critico.json", json.dumps(estado.get("achados") or [], ensure_ascii=False, indent=1))
    _escrever(pasta / "validations" / "afirmacoes.json", json.dumps(estado.get("afirmacoes") or [], ensure_ascii=False, indent=1))
    # relatórios e figuras
    figuras = []
    for r in (estado.get("resultados") or {}).values():
        for f in r.get("figuras") or []:
            origem = servicos.pasta / f["arquivo"]
            if origem.exists():
                destino_fig = pasta / "reports" / "figuras" / origem.name
                _escrever(destino_fig, origem.read_bytes())
                figuras.append((f"figuras/{origem.name}", f["descricao"]))
    _escrever(pasta / "reports" / "relatorio.html", _relatorio_html(estado, marcadores, figuras))
    narr = (estado.get("narrativa") or {}).get("texto") or ""
    _escrever(pasta / "reports" / "conclusao.md", f"# Conclusão\n\n{narr}\n")
    _escrever(pasta / "decisions.md", _decisions_md(estado))
    _escrever(pasta / "limitations.md", _limitations_md(estado, marcadores))
    _escrever(pasta / "environment.lock", _lock())
    _escrever(pasta / "notebooks" / "reproduzir.ipynb", _notebook(estudo_id))
    # dados agregados (opcional e explícito)
    if incluir_dados == "agregados":
        for passo, r in (estado.get("resultados") or {}).items():
            for c in r.get("consultas") or []:
                if c.get("modo") != "agregado":
                    continue
                chave = f"consulta__{passo}__{c['nome']}"
                df = servicos.armazem[chave] if chave in servicos.armazem else None
                if df is not None:
                    (pasta / "data").mkdir(exist_ok=True)
                    df.to_parquet(pasta / "data" / f"{passo}__{c['nome']}.parquet", index=False)
    _escrever(pasta / "README.md", _readme(estado, carta, incluir_dados))
    # manifesto por último (com sha256 de tudo)
    arquivos = {str(p.relative_to(pasta)).replace("\\", "/"): _sha(p) for p in sorted(pasta.rglob("*")) if p.is_file()}
    manifesto = {
        "estudo": estudo_id, "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "carta_versao": carta.versao if carta else None, "plano_versao": (estado.get("plano") or {}).get("versao"),
        "seed": estado.get("seed"), "config_sha256": servicos.efetiva.hash(), "admin": servicos.efetiva.admin.situacao,
        "dossie_ultimo_hash": servicos.dossie.ultimo_hash, "copiloto_versao": _versao_lib(), "incluir_dados": incluir_dados,
        "arquivos": arquivos,
    }
    _escrever(pasta / "manifest.json", json.dumps(manifesto, ensure_ascii=False, indent=1))
    esperado = None
    if "efeito" in servicos.armazem and isinstance(servicos.armazem["efeito"], dict):
        att = servicos.armazem["efeito"].get("att")
        esperado = {"efeito": {"att": float(att)}} if att is not None else None
    verificacao = verificar_independencia(pasta, executar=incluir_dados == "agregados", esperado=esperado)
    if not verificacao["ok"]:
        raise HandoffErro("O pacote de handoff não passou na verificação de independência: " + "; ".join(verificacao["problemas"]),
                          detalhes=verificacao)
    return {"pasta": str(pasta), "destino": destino, "arquivos": len(arquivos), "verificacao": verificacao,
            "manifesto_sha256": _sha(pasta / "manifest.json"), "incluir_dados": incluir_dados}


def publicar_pacote(servicos, info: dict[str, Any]) -> dict[str, Any]:
    """Depois do portão: compacta e grava no destino autorizado (ou só registra o local)."""
    pasta = Path(info["pasta"])
    destino = info.get("destino")
    zip_local = pasta.with_suffix(".zip")
    with zipfile.ZipFile(zip_local, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pasta.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(pasta.parent).as_posix())
    info = {**info, "zip": str(zip_local), "zip_sha256": _sha(zip_local)}
    if destino:
        uri = destino.rstrip("/") + "/" + zip_local.name
        servicos.armazenamento.escrever(uri, zip_local.read_bytes())
        info["publicado_em"] = uri
    else:
        info["publicado_em"] = str(zip_local)
    meta = servicos.pasta / "meta.json"
    dados = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    dados["handoff_em"] = dt.datetime.now(dt.timezone.utc).isoformat()
    meta.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def _tabelas_do_sql(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp

    try:
        arvore = sqlglot.parse_one(sql, read="oracle")
    except Exception:  # noqa: BLE001
        return set()
    return {f"{t.db}.{t.name}".upper() for t in arvore.find_all(exp.Table) if t.db}


def _versao_lib() -> str:
    from adex_copiloto._versao import __version__

    return __version__


def _readme(estado: dict, carta, incluir_dados: str) -> str:
    pergunta = carta.valor("pergunta_decisao") if carta else estado.get("objetivo", "")
    return textwrap.dedent(f"""\
        # Handoff do estudo {estado['estudo_id']}

        **Pergunta de decisão:** {pergunta}

        Este pacote não depende do ADEX Copiloto. Ele traz o método, os SQLs, os contratos de dados,
        o passaporte de amostragem, as métricas, as decisões e as limitações.

        ## Como reproduzir

        1. Crie o ambiente com as versões de `environment.lock`.
        2. Configure a fonte (`ADEX_DSN`, `ADEX_USUARIO`, `ADEX_SENHA`; wallet em `ADEX_WALLET`).
        3. Rode `python src/pipeline.py` (ou `--offline` se `data/` existir — aqui: {incluir_dados}).

        ## Conteúdo

        - `src/adex_metodos.py`: métodos estatísticos (sem dependência do copiloto).
        - `src/pipeline.py`: passos do estudo, na ordem.
        - `sql/`: consultas aprovadas pelo porteiro (somente leitura, com binds).
        - `data_contracts/`: colunas usadas, tipos e marcação de dado pessoal.
        - `sampling_passport.json`, `metrics.json`, `validations/`, `reports/`.
        - `decisions.md` e `limitations.md`: o que foi decidido e o que o estudo não garante.

        A industrialização (testes, agendamento, monitoramento, segredos) é responsabilidade da equipe de produção.
        """)


def _notebook(estudo_id: str) -> str:
    celulas = [
        {"cell_type": "markdown", "metadata": {}, "source": [f"# Reproduzir o estudo {estudo_id}\n", "Roda o pipeline do pacote sem o ADEX Copiloto."]},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
         "source": ["import subprocess, sys\n", "print(subprocess.run([sys.executable, '../src/pipeline.py', '--offline'], capture_output=True, text=True).stdout)"]},
    ]
    return json.dumps({"cells": celulas, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
                       "nbformat": 4, "nbformat_minor": 5}, ensure_ascii=False, indent=1)
