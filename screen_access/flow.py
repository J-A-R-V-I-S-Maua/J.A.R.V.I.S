"""Diálogo de escolha e confirmação. Não depende do sistema operacional nem de onde vem a frase."""

from . import matching

NO_NAME = "(sem nome)"


def resolve(phrase, items, ask=None, say=print):
    """Devolve a posição do item que a frase pede, ou None se não houver escolha.

    Se a frase combinar com vários itens, mostra as opções e aceita um único esclarecimento.
    `items` é [(elemento, papel, nome)] na ordem dos números mostrados ao usuário.
    """
    ask = ask or input
    names = ["" if name == NO_NAME else name for _, _, name in items]
    choice = matching.choose(phrase, names)
    if choice.status == "none":
        say("Não encontrei nenhum item que combine com isso.")
        return None
    if choice.status == "match":
        return choice.index
    say("Vários itens combinam com isso:")
    for index in choice.candidates:
        say(f"  [{index + 1}] {items[index][1]} {items[index][2][:80]!r}")
    answer = ask("Qual deles? (número, Enter para cancelar): ").strip()
    if not answer:
        return None
    if answer.isdigit() and int(answer) - 1 in choice.candidates:
        return int(answer) - 1
    say("Esse número não está entre as opções.")
    return None


def confirm_and_press(index, items, press, describe_error, ask=None, say=print):
    """Pede confirmação e aciona o item. Devolve 0 se terminou sem erro, 1 se o app recusou."""
    ask = ask or input
    element, role, name = items[index]
    if ask(f"Acionar [{index + 1}] {role} {name[:80]!r}? (s/n): ").strip().lower() != "s":
        say("Cancelado.")
        return 0
    code = press(element)
    if code == 0:
        say("Pedido aceito pelo aplicativo.")
        return 0
    say(f"Recusado: {describe_error(code)} (código {code}).")
    return 1
