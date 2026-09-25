"""Motor de plantillas mínimo y seguro para el Quedan.

Sintaxis soportada:

    {{ ruta.con.puntos }}
    {% if [not] ruta %} ... {% else %} ... {% endif %}
    {% for item in ruta %} ... {% endfor %}   (expone loop.index, loop.first, loop.last)

Los bloques se parsean con una pila, así que IF/FOR se pueden anidar en
cualquier combinación.

Seguridad:
- No usa eval() ni ejecuta Python: una ruta solo se resuelve contra dicts y
  listas construidos por el módulo, nunca con getattr sobre registros ORM.
- El HTML de la plantilla es configuración del administrador y se emite tal
  cual; los valores insertados con {{ }} son datos y se escapan siempre,
  salvo que el propio módulo los haya marcado como Markup (p. ej. el logo).
- Una variable inexistente, None o False produce "". Listas y dicts también
  producen "" para no imprimir representaciones internas.

Este archivo no importa nada de Odoo para poder probarse de forma aislada.
"""

import re

from markupsafe import Markup, escape

_TOKEN_RE = re.compile(r'(\{\{.*?\}\}|\{%.*?%\})', re.DOTALL)
_PATH = r'[A-Za-z_]\w*(?:\.\w+)*'
_VAR_RE = re.compile(r'^\{\{\s*(%s)\s*\}\}$' % _PATH)
_IF_RE = re.compile(r'^\{%%\s*if\s+(not\s+)?(%s)\s*%%\}$' % _PATH)
_FOR_RE = re.compile(r'^\{%%\s*for\s+([A-Za-z_]\w*)\s+in\s+(%s)\s*%%\}$' % _PATH)
_SIMPLE_TAG_RE = re.compile(r'^\{%\s*(else|endif|endfor)\s*%\}$')

MAX_DEPTH = 20


class TemplateSyntaxError(ValueError):
    """Error de sintaxis en la plantilla, con la línea donde ocurrió."""


class _Node:
    __slots__ = ('kind', 'value', 'negate', 'path', 'var', 'body', 'else_body', 'line')

    def __init__(self, kind, line=0, **kwargs):
        self.kind = kind
        self.line = line
        self.value = kwargs.get('value')
        self.negate = kwargs.get('negate', False)
        self.path = kwargs.get('path')
        self.var = kwargs.get('var')
        self.body = []
        self.else_body = None


def parse(template):
    """Convierte la plantilla en un árbol de nodos.

    Lanza TemplateSyntaxError ante tags desconocidos, mal formados o sin
    cerrar: es preferible avisar al guardar que imprimir tags sueltos.
    """
    root = _Node('root')
    stack = [root]
    line = 1
    for token in _TOKEN_RE.split(template or ''):
        if not token:
            continue
        current = stack[-1]
        target = current.else_body if current.else_body is not None else current.body
        if token.startswith('{{'):
            match = _VAR_RE.match(token)
            if not match:
                raise TemplateSyntaxError(
                    "Línea %s: variable no válida %s. Use {{ nombre.campo }}." % (line, token.strip()))
            target.append(_Node('var', line, path=match.group(1)))
        elif token.startswith('{%'):
            if_match = _IF_RE.match(token)
            for_match = _FOR_RE.match(token)
            simple = _SIMPLE_TAG_RE.match(token)
            if if_match or for_match:
                if len(stack) > MAX_DEPTH:
                    raise TemplateSyntaxError("Línea %s: demasiados bloques anidados." % line)
                if if_match:
                    node = _Node('if', line, negate=bool(if_match.group(1)), path=if_match.group(2))
                else:
                    node = _Node('for', line, var=for_match.group(1), path=for_match.group(2))
                target.append(node)
                stack.append(node)
            elif simple:
                tag = simple.group(1)
                if tag == 'else':
                    if current.kind != 'if' or current.else_body is not None:
                        raise TemplateSyntaxError("Línea %s: {%% else %%} fuera de un {%% if %%}." % line)
                    current.else_body = []
                else:
                    expected = 'if' if tag == 'endif' else 'for'
                    if current.kind != expected:
                        raise TemplateSyntaxError(
                            "Línea %s: {%% %s %%} no corresponde a ningún {%% %s %%} abierto." % (line, tag, expected))
                    stack.pop()
            else:
                raise TemplateSyntaxError(
                    "Línea %s: etiqueta no soportada %s. Use if / else / endif / for / endfor." % (line, token.strip()))
        else:
            target.append(_Node('text', line, value=token))
        line += token.count('\n')
    if len(stack) > 1:
        node = stack[-1]
        closing = 'endif' if node.kind == 'if' else 'endfor'
        raise TemplateSyntaxError(
            "Línea %s: falta {%% %s %%} para cerrar el bloque abierto." % (node.line, closing))
    return root


def validate(template):
    """Lanza TemplateSyntaxError si la plantilla no es válida."""
    parse(template)


def render(template, context):
    """Renderiza la plantilla con el contexto dado y devuelve Markup."""
    root = parse(template)
    out = []
    _render_nodes(root.body, [context or {}], out)
    return Markup(''.join(out))


def _lookup(scopes, path):
    keys = path.split('.')
    value = None
    for scope in reversed(scopes):
        if keys[0] in scope:
            value = scope[keys[0]]
            break
    else:
        return None
    for key in keys[1:]:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, (list, tuple)) and key.isdigit():
            index = int(key)
            value = value[index] if index < len(value) else None
        else:
            return None
        if value is None:
            return None
    return value


def _to_text(value):
    if value is None or value is False:
        return ''
    if isinstance(value, Markup):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        return ''
    return str(escape(str(value)))


def _render_nodes(nodes, scopes, out):
    for node in nodes:
        if node.kind == 'text':
            out.append(node.value)
        elif node.kind == 'var':
            out.append(_to_text(_lookup(scopes, node.path)))
        elif node.kind == 'if':
            truthy = bool(_lookup(scopes, node.path))
            if node.negate:
                truthy = not truthy
            if truthy:
                _render_nodes(node.body, scopes, out)
            elif node.else_body is not None:
                _render_nodes(node.else_body, scopes, out)
        elif node.kind == 'for':
            items = _lookup(scopes, node.path)
            if not isinstance(items, (list, tuple)):
                continue
            total = len(items)
            for index, item in enumerate(items):
                scope = {
                    node.var: item,
                    'loop': {
                        'index': index + 1,
                        'index0': index,
                        'first': index == 0,
                        'last': index == total - 1,
                        'length': total,
                    },
                }
                _render_nodes(node.body, scopes + [scope], out)
