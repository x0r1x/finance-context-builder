from __future__ import annotations

from typing import Any

from finance_context.formulas.scan import FormulaSyntaxError, Token

_XL_FUNC_PREFIXES = ("_xlfn.", "_xlws.")


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token | None:
        if self.i >= len(self.tokens):
            return None
        return self.tokens[self.i]

    def eat(self, kind: str | None = None) -> Token:
        tok = self.peek()
        if tok is None or (kind is not None and tok.kind != kind):
            raise FormulaSyntaxError("unexpected token")
        self.i += 1
        return tok

    def parse_expr(self) -> dict[str, Any]:
        return self._parse_concat()

    def _parse_concat(self) -> dict[str, Any]:
        node = self._parse_compare()
        while self._op("&"):
            self.eat("OP")
            node = {"op": "bin", "kind": "&", "left": node, "right": self._parse_compare()}
        return node

    def _parse_compare(self) -> dict[str, Any]:
        node = self._parse_add()
        while True:
            tok = self.peek()
            compares = {"=", "<>", "<", ">", "<=", ">="}
            if tok is None or tok.kind != "OP" or tok.value not in compares:
                return node
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_add()}

    def _parse_add(self) -> dict[str, Any]:
        node = self._parse_mul()
        while self._op("+") or self._op("-"):
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_mul()}
        return node

    def _parse_mul(self) -> dict[str, Any]:
        node = self._parse_pow()
        while self._op("*") or self._op("/"):
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_pow()}
        return node

    def _parse_pow(self) -> dict[str, Any]:
        node = self._parse_unary()
        if self._op("^"):
            self.eat("OP")
            node = {"op": "bin", "kind": "^", "left": node, "right": self._parse_pow()}
        return node

    def _parse_unary(self) -> dict[str, Any]:
        if self._op("+") or self._op("-"):
            op = self.eat("OP").value
            return {"op": "unary", "kind": op, "expr": self._parse_unary()}
        return self._parse_postfix()

    def _parse_postfix(self) -> dict[str, Any]:
        node = self._parse_primary()
        if self._op("%"):
            self.eat("OP")
            node = {"op": "percent", "expr": node}
        return node

    def _parse_primary(self) -> dict[str, Any]:
        tok = self.peek()
        if tok is None:
            raise FormulaSyntaxError("expected value")
        if tok.kind == "NUMBER":
            self.eat()
            return {"op": "num", "value": tok.value}
        if tok.kind == "STRING":
            self.eat()
            return {"op": "str", "value": tok.value}
        if tok.kind == "ERROR":
            self.eat()
            return {"op": "err", "value": tok.value}
        if tok.kind == "REF":
            self.eat()
            return tok.value
        if tok.kind == "NAME":
            self.eat()
            if self.peek() is not None and self.peek().kind == "LPAREN":
                return self._parse_call(tok.value)
            if self.peek() is not None and self.peek().kind == "COLON":
                return self._parse_named_range(tok.value)
            return {"op": "name", "value": tok.value}
        if tok.kind == "LPAREN":
            self.eat()
            node = self.parse_expr()
            self.eat("RPAREN")
            return node
        raise FormulaSyntaxError("expected value")

    def _parse_call(self, name: str) -> dict[str, Any]:
        self.eat("LPAREN")
        args: list[dict[str, Any]] = []
        if self.peek() is not None and self.peek().kind != "RPAREN":
            args.append(self.parse_expr())
            while self.peek() is not None and self.peek().kind == "SEP":
                self.eat("SEP")
                args.append(self.parse_expr())
        self.eat("RPAREN")
        return {"op": "func", "name": _canonical_func_name(name), "args": args}

    def _parse_named_range(self, start: str) -> dict[str, Any]:
        self.eat("COLON")
        tok = self.peek()
        if tok is None or tok.kind != "NAME":
            raise FormulaSyntaxError("expected name after :")
        end = self.eat("NAME").value
        return {"op": "range", "named": True, "start_name": start, "end_name": end}

    def _op(self, value: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "OP" and tok.value == value


def _canonical_func_name(name: str) -> str:
    text = name
    while True:
        lowered = text.lower()
        matched = next((p for p in _XL_FUNC_PREFIXES if lowered.startswith(p)), None)
        if matched is None:
            return text
        text = text[len(matched) :]
