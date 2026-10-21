"""Erros do ADEX Copiloto.

Todo erro da biblioteca carrega duas coisas além da mensagem técnica:
- ``explicacao``: o que aconteceu, em termos simples;
- ``como_resolver``: o próximo passo concreto para o dev.

O explicador de erros do notebook usa esses campos sem chamar a IA.
"""

from __future__ import annotations


class AdexErro(Exception):
    """Base de todos os erros do copiloto."""

    explicacao_padrao = "Algo não saiu como esperado no copiloto."
    como_resolver_padrao = "Leia a mensagem acima; se persistir, abra um chamado com o id do estudo."

    def __init__(
        self,
        mensagem: str,
        *,
        explicacao: str | None = None,
        como_resolver: str | None = None,
        detalhes: dict | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.explicacao = explicacao or self.explicacao_padrao
        self.como_resolver = como_resolver or self.como_resolver_padrao
        self.detalhes = detalhes or {}

    def para_dict(self) -> dict:
        return {
            "tipo": type(self).__name__,
            "mensagem": self.mensagem,
            "explicacao": self.explicacao,
            "como_resolver": self.como_resolver,
            "detalhes": self.detalhes,
        }

    def _repr_html_(self) -> str:  # pragma: no cover - visual
        from adex_copiloto.ui.html import cartao_erro

        return cartao_erro(self)


# --- Configuração e segredos -------------------------------------------------


class ConfigErro(AdexErro):
    explicacao_padrao = "A configuração do copiloto tem um valor ausente ou inválido."
    como_resolver_padrao = "Confira o arquivo de configuração indicado e rode `adex-configuracoes mostrar`."


class ConfiguracaoAdminErro(ConfigErro):
    explicacao_padrao = (
        "As Configurações do Admin não puderam ser confirmadas (assinatura, impressão ou arquivo)."
    )
    como_resolver_padrao = (
        "O copiloto segue com os valores mais restritivos. Peça ao Admin para publicar e assinar o "
        "arquivo de Configurações."
    )


class PermissaoAdminErro(ConfigErro):
    explicacao_padrao = "Só o Admin pode alterar as Configurações (retenção e governança)."
    como_resolver_padrao = "Peça a alteração ao Admin do ADEX; você pode ver os valores em modo leitura."


class SegredoErro(AdexErro):
    explicacao_padrao = "Um segredo (chave ou endereço protegido) não pôde ser lido ou conferido."
    como_resolver_padrao = (
        "Rode `adex-segredo conferir <item>`. Se a impressão não bater, o valor mudou: peça ao Admin "
        "a impressão nova."
    )


class ImpressaoDivergenteErro(SegredoErro):
    explicacao_padrao = (
        "O valor lido não corresponde à impressão (sha256 + salt) homologada. Por segurança, o "
        "copiloto não usa um valor que não foi aprovado."
    )


# --- Motor Qwen ---------------------------------------------------------------


class HubErro(AdexErro):
    explicacao_padrao = "O GPU Hub não respondeu como esperado."
    como_resolver_padrao = "Tente de novo em alguns minutos; se continuar, rode `adex-fase0 rodar --blocos A`."


class HubIndisponivelErro(HubErro):
    explicacao_padrao = "O GPU Hub não está acessível agora (rede, limite de uso ou fora do ar)."


class HubRecusouErro(HubErro):
    explicacao_padrao = "O GPU Hub recusou o pedido (parâmetro inválido ou chave sem permissão)."
    como_resolver_padrao = "Confira os perfis em `[perfis]` da configuração; um valor pode não ser aceito pelo hub."


class RespostaRecusadaErro(HubErro):
    explicacao_padrao = (
        "A resposta do Qwen não passou na regra de aceitação (cortada, vazia, com raciocínio vazado "
        "ou fora do formato). Respostas assim nunca são usadas."
    )
    como_resolver_padrao = "O copiloto já tentou de novo. Simplifique o pedido ou aumente `hub.saida_max`."


class ModeloTrocadoErro(HubErro):
    explicacao_padrao = (
        "O modelo por trás de 'qwen-latest' mudou desde a homologação. Isso é uma mudança material: "
        "resultados podem mudar."
    )
    como_resolver_padrao = "Peça ao Admin para revisar e homologar a nova impressão do modelo."


class OrcamentoEsgotadoErro(AdexErro):
    explicacao_padrao = "O estudo atingiu o limite de uso da IA definido pelo Admin (tokens, custo ou chamadas)."
    como_resolver_padrao = "Veja `estudo.costs()`. Para ampliar o limite, peça ao Admin."


# --- Dados ----------------------------------------------------------------------


class DadosErro(AdexErro):
    explicacao_padrao = "Houve um problema ao acessar os dados."


class PorteiroRecusouErro(DadosErro):
    explicacao_padrao = (
        "O porteiro de SQL não deixou esta consulta passar. Ele só aceita leitura, nas tabelas "
        "autorizadas, com parâmetros e (para a IA) só agregados com grupos grandes."
    )
    como_resolver_padrao = "Leia os motivos listados e ajuste a consulta; o porteiro diz o que mudar."


class FonteIndisponivelErro(DadosErro):
    explicacao_padrao = "A fonte de dados não está acessível (rede, credencial ou permissão)."
    como_resolver_padrao = (
        "Confira `[adw]` na configuração e a rede da sessão do notebook (a rede default não alcança "
        "ADW privado)."
    )


class DestinoNaoAutorizadoErro(DadosErro):
    explicacao_padrao = "O copiloto só grava em destinos autorizados pela configuração."
    como_resolver_padrao = "Use um caminho dentro de `objectstorage.destinos_autorizados` ou da pasta do estudo."


class LeituraNaoDeclaradaErro(DadosErro):
    explicacao_padrao = "A ferramenta tentou ler um caminho que ela não declarou em `reads=`."
    como_resolver_padrao = "Inclua o caminho em `reads=[...]` no `@adex.tool` ou remova a leitura."


# --- Estudo e harness -----------------------------------------------------------


class EstudoErro(AdexErro):
    explicacao_padrao = "O estudo não pode fazer isso agora."


class PortaoPendenteErro(EstudoErro):
    explicacao_padrao = "Há uma aprovação humana pendente antes deste passo."
    como_resolver_padrao = "Aprove (ou ajuste) o item indicado no painel e repita o comando."


class ForaDeOrdemErro(EstudoErro):
    explicacao_padrao = "Este passo depende de um passo anterior que ainda não foi feito."


class DecisaoPendenteErro(EstudoErro):
    explicacao_padrao = "Há decisões em aberto que bloqueiam este portão."
    como_resolver_padrao = "Responda com `estudo.decide('D-xx', 'opção', reason='...')`."


class BlocoErro(EstudoErro):
    explicacao_padrao = "Um bloco de método não pôde rodar."


class PrecondicaoErro(BlocoErro):
    explicacao_padrao = "As pré-condições do bloco não foram atendidas; rodar assim daria um resultado enganoso."


class FerramentaErro(EstudoErro):
    explicacao_padrao = "A ferramenta do dev não passou nos testes de contrato ou falhou ao rodar."


class AgenteErro(EstudoErro):
    explicacao_padrao = (
        "O agente de descoberta ou de plano saiu dos limites (ferramenta inesperada, chamadas demais "
        "ou resposta inválida) e foi parado."
    )
    como_resolver_padrao = "O passo pode ser repetido; o copiloto registrou o motivo no dossiê."


class IntegridadeErro(AdexErro):
    explicacao_padrao = "O registro do estudo (dossiê) foi alterado fora do copiloto."
    como_resolver_padrao = "Não edite os arquivos do estudo à mão. Restaure a cópia do Object Storage."


class HandoffErro(AdexErro):
    explicacao_padrao = "O pacote de handoff não passou nas verificações de independência."
