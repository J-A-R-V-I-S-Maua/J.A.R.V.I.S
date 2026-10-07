"""Verificação da permissão de Acessibilidade do macOS."""

from ApplicationServices import AXIsProcessTrusted


def has_accessibility_permission() -> bool:
    """Diz se o programa em execução foi autorizado a ler a árvore de acessibilidade.

    Não abre nenhuma janela do sistema: apenas consulta o estado atual.
    """
    return bool(AXIsProcessTrusted())
