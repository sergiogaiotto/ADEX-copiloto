"""Respostas roteirizadas do 'Qwen' para o caso Wi-Fi 6 (do zero), por tarefa do prompt."""

from __future__ import annotations

import json
import re

from falsos import FakeHub, completion

RESPOSTAS_DEV = (
    "1. Decidir se a onda 4 (14 cidades, uns R$ 21 mi) sai em 2027. O comitê decide em 28/11.\n"
    "2. Por cidade, em ondas: mar, jun e set/2025. A Engenharia escolheu a ordem,\n"
    "   acho que pelas cidades com mais reclamação.\n"
    "3. Menos chamado técnico de Wi-Fi. Visita técnica é o que mais custa.\n"
)

ENQUADRAMENTO = {
    "resumo": "Julgar se a troca de roteadores por Wi-Fi 6 reduziu problemas o bastante para justificar a próxima onda.",
    "tipo_pergunta": "efeito",
    "duvida_tipo": "",
    "entendimento": [
        {"item": "Tipo de pergunta", "o_que_entendi": "Julgar o efeito de uma ação que já aconteceu", "certeza": "media"},
        {"item": "Ação", "o_que_entendi": "Troca do roteador antigo por um modelo Wi-Fi 6", "certeza": "alta"},
        {"item": "Quando", "o_que_entendi": "Ano passado (a confirmar)", "certeza": "baixa"},
        {"item": "Como foi distribuída", "o_que_entendi": "não sei", "certeza": "nao_sei"},
    ],
    "perguntas": [
        {"pergunta": "Que decisão depende deste estudo, e quando?", "por_que_pergunto": "Muda o sentido de valer a pena.",
         "opcoes": ["Continuar ou parar as trocas", "Escolher fornecedor", "Prestar contas"]},
        {"pergunta": "Como a troca foi distribuída e quem escolheu a ordem?", "por_que_pergunto": "Diz se dá para comparar quem recebeu com quem não recebeu.", "opcoes": []},
        {"pergunta": "Para quem decide, valer a pena quer dizer o quê?", "por_que_pergunto": "Escolher a medida antes de ver os dados.", "opcoes": []},
    ],
}

CARTA = {
    "campos": [
        {"campo": "pergunta_decisao", "valor": "A troca por Wi-Fi 6 reduziu os chamados de Wi-Fi o bastante para justificar a onda 4 em 2027?",
         "origem": "voce_disse", "citacoes": ["Decidir se a onda 4 (14 cidades, uns R$ 21 mi) sai em 2027"]},
        {"campo": "objeto", "valor": "Troca do roteador; a cidade conta como trocada no mês em que 50% ou mais dos roteadores são novos",
         "origem": "sugerido", "citacoes": []},
        {"campo": "unidade_analise", "valor": "Cidade × mês", "origem": "sugerido", "citacoes": []},
        {"campo": "populacao", "valor": "Domicílios de fibra ativos nas cidades das ondas, jan/2024 a set/2026", "origem": "sugerido", "citacoes": []},
        {"campo": "desfecho", "valor": "Chamados de Wi-Fi por 1.000 domicílios por mês", "origem": "voce_disse",
         "citacoes": ["Menos chamado técnico de Wi-Fi"]},
        {"campo": "horizonte_comparacao", "valor": "Antes e depois de cada troca, contra cidades que ainda não tinham trocado",
         "origem": "voce_escolheu", "citacoes": []},
        {"campo": "restricoes", "valor": "Só leitura; grupos com 50 ou mais domicílios", "origem": "padrao", "citacoes": []},
        {"campo": "criterios_pronto", "valor": "Efeito com intervalo de 95%; teste de tendências antes da troca; placebo; sensibilidade",
         "origem": "sugerido", "citacoes": []},
        {"campo": "fora_escopo", "valor": "Cancelamento, receita, escolha de fornecedor", "origem": "sugerido", "citacoes": []},
        {"campo": "riscos_conhecidos", "valor": "H-01 ordem por reclamação", "origem": "voce_disse",
         "citacoes": ["acho que pelas cidades com mais reclamação"]},
    ],
    "frases_que_mudam_metodo": [{"trecho": "acho que pelas cidades com mais reclamação",
                                 "explicacao": "Se a ordem seguiu a reclamação, antes × depois engana (regressão à média).",
                                 "risco_id": "H-01"}],
    "hipoteses": [{"id": "H-01", "texto": "A ordem das ondas seguiu as cidades com mais reclamação."}],
    "lacunas": ["Custo por visita técnica não informado."],
}

LOTE = {"candidatas": [
    {"tabela": "CAD.BASE_FIBRA_MENSAL", "papel": "populacao", "para_que": "domicílios ativos por mês e cidade", "colunas": ["ID_DOM", "COD_CIDADE", "MES"]},
    {"tabela": "OPS_RES.CHAMADO_TECNICO", "papel": "desfecho", "para_que": "chamados com motivo e data", "colunas": ["ID_DOM", "DT_ABERTURA", "COD_MOTIVO"]},
    {"tabela": "OPS_RES.TROCA_EQUIPAMENTO", "papel": "tratamento", "para_que": "quando cada roteador foi trocado", "colunas": ["ID_DOM", "DT_TROCA"]},
    {"tabela": "REF.MOTIVO_CHAMADO", "papel": "dicionario", "para_que": "dicionário de motivos", "colunas": ["COD_MOTIVO", "DESCRICAO"]},
    {"tabela": "CAMPO.VISITA_TECNICA", "papel": "desfecho", "para_que": "visitas técnicas (secundário)", "colunas": ["ID_DOM"]},
    {"tabela": "OPS_RES.ONDA_WIFI6", "papel": "tratamento", "para_que": "calendário das ondas", "colunas": []},
]}

MAPEAMENTO = {
    "populacao": "CAD.BASE_FIBRA_MENSAL", "unidade": "ID_DOM", "tempo": "MES", "grupos": ["COD_CIDADE"], "atributos": [],
    "desfechos": [{"nome": "chamados_wifi", "tabela": "OPS_RES.CHAMADO_TECNICO", "chave_unidade": "ID_DOM", "coluna_data": "DT_ABERTURA",
                   "coluna_codigo": "COD_MOTIVO", "tabela_dicionario": "REF.MOTIVO_CHAMADO", "coluna_dicionario_codigo": "COD_MOTIVO",
                   "coluna_dicionario_descricao": "DESCRICAO", "descricao": "chamados de Wi-Fi"}],
    "tratamento": {"tabela": "OPS_RES.TROCA_EQUIPAMENTO", "chave_unidade": "ID_DOM", "coluna_data": "DT_TROCA", "descricao": "troca de roteador"},
    "alvo": None,
    "ligacoes": [{"de_tabela": "OPS_RES.CHAMADO_TECNICO", "de_coluna": "ID_DOM", "para_tabela": "CAD.BASE_FIBRA_MENSAL", "para_coluna": "ID_DOM"},
                 {"de_tabela": "OPS_RES.TROCA_EQUIPAMENTO", "de_coluna": "ID_DOM", "para_tabela": "CAD.BASE_FIBRA_MENSAL", "para_coluna": "ID_DOM"}],
    "periodo_inicio": "2024-01-01", "periodo_fim": "2026-10-01",
    "candidatas": LOTE["candidatas"][:5],
    "lacunas": ["O calendário oficial das ondas não está no catálogo."],
}

PLANO = {
    "jeitos": [
        {"codigo": "A", "titulo": "A troca causou a queda?", "descricao": "Diferença-em-diferenças escalonada por coorte de cidades.",
         "responde": "efeito por grupo e no total", "nao_responde": "o efeito num cliente específico", "forca_maxima": "B", "recomendado": True},
        {"codigo": "B", "titulo": "Está caindo? Onde?", "descricao": "Painel de acompanhamento por cidade.", "responde": "onde caiu",
         "nao_responde": "se foi a troca que causou", "forca_maxima": "C", "recomendado": False},
    ],
    "escolhido": "A",
    "passos": [
        {"id": "tendencias_pre", "bloco": "estudo_evento_pre", "versao": "1.0", "papel": "Estatístico",
         "parametros": [{"nome": "n_boot", "valor": 120}], "decisao_humana_se_falhar": True, "por_que": "testar tendências antes"},
        {"id": "codigos_estaveis", "bloco": "desfecho_estavel", "versao": "1.0", "papel": "Analista de dados", "parametros": []},
        {"id": "efeito", "bloco": "did_escalonado", "versao": "1.0", "papel": "Estatístico",
         "parametros": [{"nome": "n_boot", "valor": 120}, {"nome": "ponderar_por", "valor": "unidades"}]},
        {"id": "placebo", "bloco": "placebo", "versao": "1.0", "papel": "Estatístico",
         "parametros": [{"nome": "n_boot", "valor": 99}, {"nome": "deslocamento", "valor": 4}]},
        {"id": "sensibilidade", "bloco": "sensibilidade", "versao": "1.0", "papel": "Estatístico",
         "parametros": [{"nome": "n_boot", "valor": 99},
                        {"nome": "excluir_grupos", "valor": ["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09"]}]},
        {"id": "inventado", "bloco": "bloco_que_nao_existe", "versao": "1.0", "parametros": []},
    ],
    "pre_mortem": [{"risco": "seleção por pico de reclamação", "protecao": "sensibilidade sem a 1ª onda", "passo": "sensibilidade"}],
}

CRITICO = {"achados": [{"severidade": "alerta", "texto": "A troca de empresa de campo pode confundir o efeito em uma cidade.",
                        "refere": "efeito", "o_que_fazer": "Rodar a sensibilidade sem essa cidade."}], "resumo": "Desenho adequado."}


def classificar(corpo: dict) -> dict:
    conteudo = corpo["messages"][-1]["content"]
    codigos = re.findall(r'"codigo":\s*"(\d+)"', conteudo)
    wifi = {"3114", "3121", "3133", "3140", "3152"}
    duvida = {"3190", "3207"}
    return completion(json.dumps({"codigos": [
        {"codigo": c, "decisao": "incluir" if c in wifi else ("duvida" if c in duvida else "excluir"), "motivo": "regra do roteiro"}
        for c in codigos]}))


def narrar(corpo: dict) -> dict:
    marcadores = re.findall(r"⟦(M-\d+)⟧", corpo["messages"][-1]["content"])
    efeito = marcadores[-1] if marcadores else "M-01"
    return completion(json.dumps({"texto": f"A troca reduziu os chamados de Wi-Fi em ⟦{efeito}⟧, e a onda 4 pode seguir em 2027 com o "
                                           "limite do pico de reclamação (H-01) registrado."}))


def descobrir(corpo: dict) -> dict:
    conteudo = corpo["messages"][-1]["content"]
    if "proponha o mapeamento final" in conteudo or "corrija só o que" in conteudo:
        return completion(json.dumps(MAPEAMENTO))
    return completion(json.dumps(LOTE))


def hub_wifi() -> FakeHub:
    hub = FakeHub()
    hub.quando(r"Tarefa: enquadrar", completion(json.dumps(ENQUADRAMENTO)))
    hub.quando(r"Tarefa: montar a carta", completion(json.dumps(CARTA)))
    hub.quando(r"Tarefa: decidir quais códigos", classificar)
    hub.quando(r"Tarefa: descobrir", descobrir)
    hub.quando(r"Tarefa: propor como responder", completion(json.dumps(PLANO)))
    hub.quando(r"Tarefa: ser o crítico", completion(json.dumps(CRITICO)))
    hub.quando(r"Tarefa: escrever o texto", narrar)
    hub.quando(r"Tarefa: avaliar", completion(json.dumps({"notas": [{"criterio": "clareza", "nota": 4, "justificativa": "ok"}]})))
    return hub
