"""Redação: mascara dados pessoais e segredos antes de qualquer saída.

Usada nos logs, nos rastros do Langfuse (função ``mask``), na fila local, no
explicador de erros e em tudo o que vai para a IA a partir de metadados
(comentários de colunas, por exemplo).

Ordem: primeiro os segredos carregados na sessão (correspondência exata), depois
padrões de segredo e, por fim, padrões de dados pessoais brasileiros. Números de
negócio (contagens, valores em reais, códigos de pedido) não são mascarados: cada
padrão confere o que achou (dígito do CPF, DDD do telefone, faixa do IP, Luhn do cartão).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

from adex_copiloto.seguranca.segredos import valores_sensiveis

MASCARA = "▇▇▇"

# Nome de campo cujo valor é sempre segredo (dicionários, JSON, "chave: valor").
_NOME_SENSIVEL = (
    r"api[_-]?key|apikey|secret|client[_-]?secret|secret[_-]?key|senha|password|passwd|pwd|token|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|authorization|private[_-]?key|cookie|set-cookie"
)
_CAMPO_SENSIVEL = re.compile(rf"(?i)^(?:{_NOME_SENSIVEL})$")


def _manter_nome(m: re.Match[str]) -> str:
    return f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{m.group(4)}{MASCARA}"


_PADROES_SEGREDO: list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), MASCARA),
    ("chave", re.compile(r"\b[sp]k-lf-[A-Za-z0-9-]{8,}"), MASCARA),
    ("chave", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), MASCARA),
    ("chave", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASCARA),
    ("chave", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), MASCARA),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASCARA),
    ("token", re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{12,}"), lambda m: f"{m.group(1)} {MASCARA}"),
    # "senha: x", senha=x, "api_key": "x", 'token' = 'x'
    ("token", re.compile(rf"(?i)(['\"]?)({_NOME_SENSIVEL})\1(\s*[=:]\s*)(['\"]?)[^\s'\",;}}]{{6,}}"), _manter_nome),
]

# Faixas válidas de DDD no Brasil (Anatel).
_DDD = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38, 41, 42, 43, 44, 45, 46, 47,
    48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87,
    88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}

_PADROES_PII: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("cnpj", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")),
    ("cpf", re.compile(r"(?<![\d.])\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}(?![\d])")),
    ("telefone", re.compile(
        r"(?<![\d\w])(?:\+?55[\s.-]?)?(?:\(0?\d{2}\)|0?\d{2})[\s.-]?(?:9[\s.-]?)?\d{4}[\s.-]?\d{4}(?!\d)"
        r"|(?<![\d\w-])9\d{4}-\d{4}(?![\d-])"
    )),
    ("cartao", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?<![ -])(?!\d)")),
    ("cep", re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")),
    ("ip", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
]


def _luhn_ok(digitos: str) -> bool:
    soma, alternar = 0, False
    for ch in reversed(digitos):
        d = ord(ch) - 48
        if alternar:
            d *= 2
            if d > 9:
                d -= 9
        soma += d
        alternar = not alternar
    return soma % 10 == 0


def _cpf_valido(digitos: str) -> bool:
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(digitos[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(digitos[n]):
            return False
    return True


def _eh_cpf(bruto: str) -> bool:
    # Formato oficial (000.000.000-00) mascara sempre; os outros, só com dígito verificador certo.
    if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", bruto):
        return True
    return _cpf_valido(re.sub(r"\D", "", bruto))


def _eh_telefone(bruto: str) -> bool:
    digitos = re.sub(r"\D", "", bruto)
    if re.fullmatch(r"9\d{4}-\d{4}", bruto):
        return True  # celular sem DDD, com hífen
    formatado = bool(re.search(r"[()+\s.-]", bruto))
    if digitos.startswith("55") and len(digitos) >= 12:
        digitos = digitos[2:]
    if len(digitos) in (11, 12) and digitos.startswith("0"):
        digitos = digitos[1:]
    if len(digitos) not in (10, 11) or int(digitos[:2]) not in _DDD:
        return False
    local = digitos[2:]
    if len(local) == 9:
        return local[0] == "9"  # celular: com ou sem formatação
    # fixo (8 dígitos começando em 2-5): só formatado, senão vira qualquer número de 10 dígitos
    return formatado and local[0] in "2345"


def _eh_ip(bruto: str) -> bool:
    return all(int(o) <= 255 for o in bruto.split("."))


_CONFERIR: dict[str, Callable[[str], bool]] = {
    "cpf": _eh_cpf,
    "telefone": _eh_telefone,
    "cartao": lambda b: _luhn_ok(re.sub(r"\D", "", b)),
    "ip": _eh_ip,
}


def mascarar_texto(texto: str, *, pii: bool = True) -> str:
    if not texto:
        return texto
    saida = texto
    for valor in valores_sensiveis():
        if valor in saida:
            saida = saida.replace(valor, MASCARA)
    for _, padrao, substituto in _PADROES_SEGREDO:
        saida = padrao.sub(substituto, saida)
    if not pii:
        return saida
    for nome, padrao in _PADROES_PII:
        conferir = _CONFERIR.get(nome)
        if conferir is None:
            saida = padrao.sub(MASCARA, saida)
        else:
            saida = padrao.sub(lambda m, c=conferir: MASCARA if c(m.group(0)) else m.group(0), saida)
    return saida


def contem_pii(texto: str) -> list[str]:
    """Nomes dos padrões de dado pessoal encontrados (para testes de contrato)."""
    achados = []
    for nome, padrao in _PADROES_PII:
        if nome in ("ip", "cep"):
            continue  # pouco específicos para reprovar uma saída sozinhos
        conferir = _CONFERIR.get(nome)
        if any(conferir is None or conferir(m.group(0)) for m in padrao.finditer(texto or "")):
            achados.append(nome)
    return achados


def mascarar(obj: Any, *, pii: bool = True, _prof: int = 0) -> Any:
    """Mascara recursivamente textos em dicts (chaves e valores), listas, tuplas, conjuntos e bytes.

    O valor de um campo com nome de segredo (``senha``, ``token``, ``api_key``...) é
    trocado inteiro, seja qual for o formato.
    """
    if _prof > 40:
        return "…"
    if isinstance(obj, str):
        return mascarar_texto(obj, pii=pii)
    if isinstance(obj, (bytes, bytearray)):
        texto = bytes(obj).decode("utf-8", errors="replace")
        mascarado = mascarar_texto(texto, pii=pii)
        return obj if mascarado == texto else mascarado.encode("utf-8")
    if isinstance(obj, Mapping):
        saida = {}
        for k, v in obj.items():
            chave = mascarar(k, pii=pii, _prof=_prof + 1) if isinstance(k, (str, bytes)) else k
            if isinstance(k, str) and _CAMPO_SENSIVEL.match(k) and v not in (None, "", b""):
                saida[chave] = MASCARA
            else:
                saida[chave] = mascarar(v, pii=pii, _prof=_prof + 1)
        return saida
    if isinstance(obj, (list, tuple, set, frozenset)):
        itens = [mascarar(v, pii=pii, _prof=_prof + 1) for v in obj]
        if isinstance(obj, list):
            return itens
        if isinstance(obj, tuple) and hasattr(obj, "_fields"):
            return type(obj)(*itens)  # namedtuple
        return type(obj)(itens)
    return obj


# --- logging --------------------------------------------------------------------------------

# Bibliotecas que a lib usa e que podem logar conteúdo de estudo (prompts, respostas, URLs).
_LOGGERS_DA_LIB = ("adex_copiloto", "langfuse", "langchain", "langgraph", "deepagents", "openai", "httpx", "httpcore")


def _mascarar_registro(record: logging.LogRecord) -> None:
    """Mascara mensagem, traceback e pilha. Segredos sempre; dado pessoal nos loggers da lib."""
    pii = record.name.split(".")[0] in _LOGGERS_DA_LIB
    try:
        mensagem = record.getMessage()
    except Exception:  # noqa: BLE001
        mensagem = str(record.msg)
    record.msg = mascarar_texto(mensagem, pii=pii)
    record.args = ()
    if record.exc_info and record.exc_info[0] is not None:
        record.exc_text = mascarar_texto(logging.Formatter().formatException(record.exc_info), pii=pii)
        record.exc_info = None  # o formatter usa exc_text (já mascarado) em vez de refazer o traceback
    elif record.exc_text:
        record.exc_text = mascarar_texto(record.exc_text, pii=pii)
    if record.stack_info:
        record.stack_info = mascarar_texto(record.stack_info, pii=pii)


class FiltroRedacao(logging.Filter):
    """Filtro de logging que mascara a mensagem final de cada registro (e o traceback)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _mascarar_registro(record)
        return True


_FABRICA_ORIGINAL: Callable[..., logging.LogRecord] | None = None


def instalar_filtro_logging(nome_logger: str = "adex_copiloto") -> None:
    """Mascara todo registro de log no momento em que é criado.

    Um filtro só no logger ``adex_copiloto`` não pega o que vem dos loggers filhos
    (``adex_copiloto.langfuse``) nem os tracebacks de ``log.exception``; por isso a
    máscara vai na fábrica de registros, que vale para qualquer logger e handler,
    inclusive os que o Jupyter acrescenta depois.
    """
    global _FABRICA_ORIGINAL
    if _FABRICA_ORIGINAL is None:
        original = logging.getLogRecordFactory()
        _FABRICA_ORIGINAL = original

        def fabrica(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = original(*args, **kwargs)
            try:
                _mascarar_registro(record)
            except Exception:  # noqa: BLE001 - log nunca derruba o copiloto
                record.msg, record.args = "[mensagem de log omitida: falha ao mascarar]", ()
            return record

        logging.setLogRecordFactory(fabrica)
    logger = logging.getLogger(nome_logger)
    if not any(isinstance(f, FiltroRedacao) for f in logger.filters):
        logger.addFilter(FiltroRedacao())
