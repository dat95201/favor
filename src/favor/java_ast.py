"""Tree-sitter helpers for Java: method extraction, token streams, and the
normalized forms behind the L1 (textual) and L2 (syntactic) duplication levels
of Section 5.6.

L1: token stream with comments dropped and whitespace normalized.
L2: AST serialization of the same stream, with identifiers declared inside the
    method (parameters, locals, catch/for/lambda/resource names) renamed
    consistently in order of first declaration. L1 implies L2.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache

import tree_sitter_java
from tree_sitter import Language, Node, Parser

_COMMENT_TYPES = {"line_comment", "block_comment", "comment"}
_METHOD_TYPES = {"method_declaration", "constructor_declaration"}


@lru_cache(maxsize=1)
def _language() -> Language:
    return Language(tree_sitter_java.language())


def _parser() -> Parser:
    lang = _language()
    try:
        return Parser(lang)
    except TypeError:  # tree-sitter < 0.23
        p = Parser()
        p.set_language(lang)
        return p


def parse_java(src: str):
    return _parser().parse(src.encode("utf8", errors="replace"))


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf8", errors="replace")


@dataclass(frozen=True)
class MethodInfo:
    name: str
    params: str          # parameter list text, e.g. "(int a, String b)"
    n_params: int
    start_byte: int
    end_byte: int
    start_line: int      # 0-based
    end_line: int        # 0-based, inclusive
    src: str

    @property
    def signature(self) -> str:
        return f"{self.name}/{self.n_params}"


def methods_in_source(src: str) -> list[MethodInfo]:
    """All method and constructor declarations in a compilation unit."""
    tree = parse_java(src)
    out: list[MethodInfo] = []
    for node in _walk(tree.root_node):
        if node.type not in _METHOD_TYPES:
            continue
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        if name_node is None:
            continue
        params_text = _text(params_node) if params_node is not None else "()"
        n_params = (
            sum(1 for c in params_node.named_children if "parameter" in c.type)
            if params_node is not None
            else 0
        )
        out.append(
            MethodInfo(
                name=_text(name_node),
                params=params_text,
                n_params=n_params,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0],
                end_line=node.end_point[0],
                src=_text(node),
            )
        )
    return out


def method_containing_line(methods: list[MethodInfo], line: int) -> MethodInfo | None:
    """The innermost method whose span contains a 0-based line."""
    best = None
    for m in methods:
        if m.start_line <= line <= m.end_line:
            if best is None or (m.end_line - m.start_line) < (best.end_line - best.start_line):
                best = m
    return best


# --- parsing LLM output -------------------------------------------------------

_WRAP_PREFIX = "class __FavorWrap {\n"
_WRAP_SUFFIX = "\n}\n"


def _has_error(node: Node) -> bool:
    if node.type == "ERROR" or node.is_missing:
        return True
    return any(_has_error(c) for c in node.children)


def extract_single_method(code: str) -> str | None:
    """Parse `code` as the body of a class; if it contains exactly one method or
    constructor declaration and no parse errors, return that method's text.
    Returns None otherwise (Section 4.3: unparseable output has no class)."""
    code = code.strip()
    if not code:
        return None
    wrapped = _WRAP_PREFIX + code + _WRAP_SUFFIX
    tree = parse_java(wrapped)
    if _has_error(tree.root_node):
        return None
    methods = [n for n in _walk(tree.root_node) if n.type in _METHOD_TYPES]
    if len(methods) != 1:
        return None
    return _text(methods[0])


# --- normalization (L1 / L2) --------------------------------------------------

def _leaf_tokens(node: Node) -> list[Node]:
    out: list[Node] = []

    def rec(n: Node) -> None:
        if n.type in _COMMENT_TYPES:
            return
        if len(n.children) == 0:
            if _text(n).strip():
                out.append(n)
            return
        for c in n.children:
            rec(c)

    rec(node)
    return out


def _method_root(method_src: str) -> Node | None:
    wrapped = _WRAP_PREFIX + method_src.strip() + _WRAP_SUFFIX
    tree = parse_java(wrapped)
    methods = [n for n in _walk(tree.root_node) if n.type in _METHOD_TYPES]
    if not methods:
        return None
    # outermost method (a method containing a local class could have nested ones)
    return min(methods, key=lambda n: n.start_byte)


def l1_normalize(method_src: str) -> str:
    """Comments dropped, whitespace collapsed: the L1 textual form."""
    root = _method_root(method_src)
    if root is None:
        # Fall back to a crude normalization so every patch still gets a key.
        return " ".join(method_src.split())
    return " ".join(_text(t) for t in _leaf_tokens(root))


_DECL_NAME_PARENTS = {
    "formal_parameter",
    "spread_parameter",
    "catch_formal_parameter",
    "variable_declarator",
    "enhanced_for_statement",
    "resource",
}


def _declared_local_names(root: Node) -> dict[str, str]:
    """Names declared inside the method, mapped to canonical v0, v1, ... in
    order of first declaration."""
    found: list[tuple[int, str]] = []
    for node in _walk(root):
        if node.type in _DECL_NAME_PARENTS:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                name_node = next(
                    (c for c in node.named_children if c.type == "identifier"), None
                )
            if name_node is not None:
                found.append((name_node.start_byte, _text(name_node)))
        elif node.type == "inferred_parameters":
            for c in node.named_children:
                if c.type == "identifier":
                    found.append((c.start_byte, _text(c)))
        elif node.type == "lambda_expression":
            p = node.child_by_field_name("parameters")
            if p is not None and p.type == "identifier":
                found.append((p.start_byte, _text(p)))
    mapping: dict[str, str] = {}
    for _, name in sorted(found):
        if name not in mapping:
            mapping[name] = f"v{len(mapping)}"
    return mapping


def _is_member_name(n: Node) -> bool:
    """True when the identifier names a member, not a value: the called method
    in `obj.get()`, the field in `obj.size`, or a method reference. Such names
    must never be alpha-renamed even when a local variable shares their text
    (the renaming is text-keyed, not scope-resolved)."""
    p = n.parent
    if p is None:
        return False
    if p.type == "method_invocation":
        name = p.child_by_field_name("name")
        return name is not None and name.start_byte == n.start_byte
    if p.type == "field_access":
        field = p.child_by_field_name("field")
        return field is not None and field.start_byte == n.start_byte
    return p.type == "method_reference"


def l2_normalize(method_src: str, alpha_rename: bool = True) -> str:
    """The L2 syntactic form: AST-shaped serialization of the comment-free
    token stream, with local identifiers consistently renamed when
    `alpha_rename` is on (the default normalization of Section 5.6). Member
    names (called methods, accessed fields) are never renamed."""
    root = _method_root(method_src)
    if root is None:
        return "RAW " + " ".join(method_src.split())
    rename = _declared_local_names(root) if alpha_rename else {}

    parts: list[str] = []

    def rec(n: Node) -> None:
        if n.type in _COMMENT_TYPES:
            return
        if len(n.children) == 0:
            t = _text(n)
            if not t.strip():
                return
            if n.type == "identifier" and t in rename and not _is_member_name(n):
                t = rename[t]
            parts.append(t)
            return
        if n.is_named:
            parts.append(f"({n.type}")
            for c in n.children:
                rec(c)
            parts.append(")")
        else:
            for c in n.children:
                rec(c)

    rec(root)
    return " ".join(parts)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf8", errors="replace")).hexdigest()


def l1_key(method_src: str) -> str:
    return _sha1(l1_normalize(method_src))


def l2_key(method_src: str, alpha_rename: bool = True) -> str:
    return _sha1(l2_normalize(method_src, alpha_rename=alpha_rename))
