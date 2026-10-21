"""Fábrica dos Deep Agents (descoberta e plano), com rédea curta.

Restrições (deepagents 0.7.28, conferidas no fonte):
- backend ``StateBackend`` (arquivos só no estado do agente, nada no disco);
- ferramentas de arquivo só de leitura (ls, read_file, glob, grep) e escrita
  apenas em ``/rascunho/``; sem execute, edit ou delete (perfil de harness +
  allowlist do FilesystemMiddleware + auditor);
- sem subagente general-purpose (perfil de harness);
- limites de chamadas ao modelo e às ferramentas; ``recursion_limit`` próprio;
- auditor que falha fechado se surgir ferramenta inesperada ou plug-in de perfil
  (entry points ``deepagents.*_profiles``) que o Admin não permitiu.
"""

from __future__ import annotations

import threading
from importlib.metadata import entry_points
from typing import Any

from adex_copiloto.agentes.guardas import AuditorFerramentas, ContadorUso, GuardaQwen
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import AgenteErro
from adex_copiloto.llm.chat_langchain import PROVEDOR_LS

FERRAMENTAS_ARQUIVO = ("ls", "read_file", "glob", "grep", "write_file")
GRUPOS_PLUGIN = ("deepagents.provider_profiles", "deepagents.harness_profiles")
_registrado = threading.Lock()
_feito = False


def auditar_plugins(cfg: Config) -> list[str]:
    """Plug-ins de perfil instalados no ambiente. Falha fechada se houver algum não permitido."""
    encontrados = []
    for grupo in GRUPOS_PLUGIN:
        try:
            eps = entry_points(group=grupo)
        except Exception:  # noqa: BLE001
            continue
        encontrados.extend(f"{grupo}:{ep.name}" for ep in eps)
    permitidos = set(cfg.agente.entry_points_permitidos)
    estranhos = [e for e in encontrados if e.split(":", 1)[1] not in permitidos and e not in permitidos]
    if estranhos:
        raise AgenteErro(
            "Há plug-ins de perfil do Deep Agents instalados que o Admin não permitiu: " + ", ".join(estranhos),
            como_resolver="Remova os pacotes do conda ou peça ao Admin para incluí-los em agente.entry_points_permitidos.",
        )
    return encontrados


def registrar_perfil() -> None:
    """Perfil do provedor 'adex_gpu_hub': sem execute/edit/delete e sem general-purpose."""
    global _feito
    with _registrado:
        if _feito:
            return
        from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile

        register_harness_profile(
            PROVEDOR_LS,
            HarnessProfile(
                excluded_tools=frozenset({"execute", "edit_file", "delete"}),
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )
        _feito = True


def permissoes():
    from deepagents import FilesystemPermission

    return [
        FilesystemPermission(operations=["write"], paths=["/rascunho/**"], mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


def criar_agente(
    cfg: Config,
    chat_model,
    *,
    nome: str,
    prompt_sistema: str,
    ferramentas: list[Any],
    esquema_resposta: type,
    subagentes: list[dict[str, Any]] | None = None,
):
    """Monta o Deep Agent com todas as travas. Devolve o grafo compilado."""
    auditar_plugins(cfg)
    registrar_perfil()
    from deepagents import FilesystemMiddleware, create_deep_agent
    from deepagents.backends import StateBackend
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from langchain.agents.structured_output import ToolStrategy

    backend = StateBackend()
    nomes_ferramentas = {getattr(f, "name", None) or getattr(f, "__name__", "") for f in ferramentas}
    permitidas = set(FERRAMENTAS_ARQUIVO) | nomes_ferramentas | {esquema_resposta.__name__}
    if subagentes:
        permitidas.add("task")
    fs = FilesystemMiddleware(backend=backend, tools=list(FERRAMENTAS_ARQUIVO), _permissions=permissoes())
    comuns = [
        GuardaQwen(),
        AuditorFerramentas(permitidas),
        ModelCallLimitMiddleware(run_limit=cfg.agente.max_chamadas_modelo, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=cfg.agente.max_chamadas_ferramenta, exit_behavior="error"),
    ]
    specs = []
    for s in (subagentes or [])[: cfg.agente.max_subagentes]:
        nomes_sub = {getattr(f, "name", "") for f in s.get("tools", ferramentas)}
        specs.append({
            **s,
            "model": chat_model,
            "tools": s.get("tools", ferramentas),
            "middleware": [GuardaQwen(), AuditorFerramentas(set(FERRAMENTAS_ARQUIVO) | nomes_sub)],
            "permissions": permissoes(),
        })
    return create_deep_agent(
        model=chat_model,
        tools=ferramentas,
        system_prompt=prompt_sistema,
        middleware=[fs, *comuns],
        subagents=specs or None,
        permissions=permissoes(),
        backend=backend,
        response_format=ToolStrategy(esquema_resposta),
        name=nome,
    )


def rodar_agente(agente, pedido: str, *, cfg: Config, callbacks: list[Any] | None = None, arquivos: dict[str, str] | None = None):
    """Executa e devolve a resposta estruturada (ou levanta AgenteErro)."""
    from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
    from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError

    entrada: dict[str, Any] = {"messages": [{"role": "user", "content": pedido}]}
    if arquivos:
        from deepagents.backends.utils import create_file_data

        entrada["files"] = {caminho: create_file_data(conteudo) for caminho, conteudo in arquivos.items()}
    try:
        saida = agente.invoke(entrada, config={"recursion_limit": cfg.agente.recursion_limit, "callbacks": callbacks or []})
    except (ModelCallLimitExceededError, ToolCallLimitExceededError) as e:
        raise AgenteErro(f"O agente passou do limite de chamadas ({type(e).__name__}).") from e
    except AgenteErro:
        raise
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "GraphRecursionError":
            raise AgenteErro("O agente passou do limite de passos (recursion_limit).") from e
        raise AgenteErro(f"O agente falhou ({type(e).__name__}: {str(e)[:200]}).") from e
    estruturada = saida.get("structured_response")
    if estruturada is None:
        raise AgenteErro("O agente terminou sem a resposta no formato pedido.")
    return estruturada


def contador_para(telemetria, cfg: Config, etapa: str) -> ContadorUso:
    return ContadorUso(telemetria, etapa=etapa, max_tokens=cfg.perfis["acao"].max_tokens)
