"""Excecao de validacao de pedido com codigo estavel (Etapa 10.1).

`code` e um identificador curto, estavel e independente de idioma (ex.: "prompt_required")
consumido pela interface para exibir a mensagem no idioma corrente (frontend/js/locales).
A mensagem original (pt-BR, tecnica) continua valendo para diagnostico e e o fallback da UI
quando o codigo nao e conhecido. `details` carrega valores estruturados (limites, recebidos)
para interpolacao nos dicionarios — nunca texto pronto do usuario.
"""


class ValidationError(ValueError):
    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details
