"""Go text/template, parsed the way Alertmanager parses notification templates.

A port of the lexer and parser in Go's text/template/parse, enough to find what makes
Alertmanager fail: syntax errors, functions it doesn't define, variables used before they
are declared, and the template names a template defines and calls. It builds no tree for
execution; `parse` returns the first error, as Go does, and what the text defines and calls.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

# Functions every Go template has.
GO_FUNCS = frozenset({
    "and", "call", "html", "index", "slice", "js", "len", "not", "or", "print", "printf",
    "println", "urlquery", "eq", "ge", "gt", "le", "lt", "ne",
})

# Token types. Keywords are the ones Go's lexer turns into keyword items.
EOF, ERROR, TEXT, LEFT, RIGHT, SPACE, COMMENT = "EOF", "error", "text", "{{", "}}", "space", "/**/"
IDENT, FIELD, VAR, DOT, STRING, RAW, CHAR, NUMBER, BOOL = (
    "identifier", "field", "variable", ".", "string", "raw string", "char", "number", "bool")
PIPE, ASSIGN, DECLARE, LPAREN, RPAREN, PUNCT = "|", "=", ":=", "(", ")", "punct"
KEYWORDS = frozenset({"block", "break", "continue", "define", "else", "end", "if", "range",
                      "nil", "template", "with"})


class Token(NamedTuple):
    typ: str
    val: str
    pos: int


class TemplateError(Exception):
    def __init__(self, message: str, pos: int) -> None:
        super().__init__(message)
        self.message = message
        self.pos = pos


class Parsed(NamedTuple):
    error: Optional[TemplateError]
    defines: Dict[str, int]          # template name -> where it is defined
    calls: List[Tuple[str, int]]     # {{template "name"}} and {{block "name"}}, in order
    # fields that don't exist on the data: (position, field, type, define it is in or "")
    missing: List[Tuple[int, str, str, str]]
    # {{template "name" pipeline}}: (name, type of the pipeline or None)
    typed_calls: List[Tuple[str, Optional[str]]]


# Alertmanager's template data (template/template.go), by type: field or method -> its type.
# None is a type amlint doesn't follow (time.Time). KV is a map: any other name is a key.
DATA = "Data"
MEMBERS: Dict[str, Dict[str, Optional[str]]] = {
    "Data": {"Receiver": "string", "Status": "string", "Alerts": "Alerts",
             "NotificationReason": "string", "GroupLabels": "KV", "CommonLabels": "KV",
             "CommonAnnotations": "KV", "RouteLabels": "KV", "ExternalURL": "string"},
    "Alert": {"Status": "string", "Labels": "KV", "Annotations": "KV", "StartsAt": None,
              "EndsAt": None, "GeneratorURL": "string", "Fingerprint": "string"},
    "Alerts": {"Firing": "[]Alert", "Resolved": "[]Alert"},
    "[]Alert": {},
    "KV": {"SortedPairs": "Pairs", "Remove": "KV", "Names": "Strings", "Values": "Strings",
           "String": "string"},
    "Pairs": {"Names": "Strings", "Values": "Strings", "String": "string"},
    "Pair": {"Name": "string", "Value": "string"},
    "Strings": {"Join": "string"},
    "string": {},
}
ELEMENT = {"Alerts": "Alert", "[]Alert": "Alert", "KV": "string", "Pairs": "Pair",
           "Strings": "string"}
# Grafana's template data (grafana/alerting templates: ExtendedData, ExtendedAlert). Values
# maps a query's ref ID to its value.
GRAFANA_MEMBERS: Dict[str, Dict[str, Optional[str]]] = dict(MEMBERS, **{
    "Data": {"Receiver": "string", "Status": "string", "Alerts": "Alerts",
             "GroupLabels": "KV", "CommonLabels": "KV", "CommonAnnotations": "KV",
             "ExternalURL": "string", "AppVersion": "string", "GroupKey": "string",
             "TruncatedAlerts": None, "Vars": "KV"},
    "Alert": {"Status": "string", "Labels": "KV", "Annotations": "KV", "StartsAt": None,
              "EndsAt": None, "GeneratorURL": "string", "Fingerprint": "string",
              "SilenceURL": "string", "DashboardURL": "string", "PanelURL": "string",
              "RuleUID": "string", "Values": "Values", "ValueString": "string",
              "ImageURL": "string", "EmbeddedImage": "string", "OrgID": None,
              "ExtraData": None},
    "Values": {},
})
# Map types: any other name is a key, of this type.
MAPS = {"KV": "string", "Values": None}
GO_TYPE = {"Data": "*template.Data", "Alert": "template.Alert", "Alerts": "template.Alerts",
           "[]Alert": "[]template.Alert", "KV": "template.KV", "Pairs": "template.Pairs",
           "Pair": "template.Pair", "Strings": "template.Strings", "string": "string",
           "Values": "map[string]float64"}


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ── Lexer ──────────────────────────────────────────────────────────────

_SPACE = " \t\r\n"
_NUMBER = re.compile(
    r"[+-]?(?:0[xX](?:_?[0-9a-fA-F])+(?:\.(?:_?[0-9a-fA-F])*)?(?:[pP][+-]?[0-9]+)?"
    r"|0[bB](?:_?[01])+|0[oO](?:_?[0-7])+"
    r"|(?:[0-9](?:_?[0-9])*)?\.?(?:[0-9](?:_?[0-9])*)?(?:[eE][+-]?[0-9]+)?)i?"
)


def _alnum(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


class _Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.tokens: List[Token] = []
        self.paren = 0

    def emit(self, typ: str, start: int, end: int) -> None:
        self.tokens.append(Token(typ, self.text[start:end], start))

    def error(self, message: str, pos: int) -> None:
        self.tokens.append(Token(ERROR, message, pos))

    def run(self) -> List[Token]:
        text = self.text
        while True:
            at = text.find("{{", self.pos)
            if at < 0:
                if self.pos < len(text):
                    self.emit(TEXT, self.pos, len(text))
                self.tokens.append(Token(EOF, "", len(text)))
                return self.tokens
            trim = text[at + 2:at + 3] == "-" and text[at + 3:at + 4] in tuple(_SPACE)
            end = at
            if trim:
                while end > self.pos and text[end - 1] in _SPACE:
                    end -= 1
            if end > self.pos:
                self.emit(TEXT, self.pos, end)
            self.pos = at + 2 + (2 if trim else 0)
            if text.startswith("/*", self.pos):
                if not self.comment(at):
                    return self.tokens
                continue
            self.tokens.append(Token(LEFT, "{{", at))
            if not self.action():
                return self.tokens

    def right_delim(self) -> Optional[Tuple[int, bool]]:
        """(length, trim) when a right delimiter starts here."""
        text, pos = self.text, self.pos
        if text[pos:pos + 1] in tuple(_SPACE) and text.startswith("-}}", pos + 1):
            return 4, True
        if text.startswith("}}", pos):
            return 2, False
        return None

    def comment(self, start: int) -> bool:
        close = self.text.find("*/", self.pos + 2)
        if close < 0:
            self.error("unclosed comment", start)
            return False
        self.pos = close + 2
        delim = self.right_delim()
        if delim is None:
            self.error("comment ends before closing delimiter", start)
            return False
        self.pos += delim[0]
        if delim[1]:
            while self.pos < len(self.text) and self.text[self.pos] in _SPACE:
                self.pos += 1
        return True

    def at_terminator(self) -> bool:
        ch = self.text[self.pos:self.pos + 1]
        return not ch or ch in _SPACE or ch in ".,|:()" or self.right_delim() is not None

    def action(self) -> bool:
        """Tokens up to and including the right delimiter; False after an error."""
        text = self.text
        while True:
            delim = self.right_delim()
            if delim is not None:
                if self.paren:
                    self.error("unclosed left paren", self.pos)
                    return False
                self.tokens.append(Token(RIGHT, "}}", self.pos))
                self.pos += delim[0]
                if delim[1]:
                    while self.pos < len(text) and text[self.pos] in _SPACE:
                        self.pos += 1
                return True
            if self.pos >= len(text):
                self.error("unclosed action", self.pos)
                return False
            start, ch = self.pos, text[self.pos]
            self.pos += 1
            if ch in _SPACE:
                while self.pos < len(text) and text[self.pos] in _SPACE and not (
                        text.startswith("-}}", self.pos + 1)):
                    self.pos += 1
                self.emit(SPACE, start, self.pos)
            elif ch == "=":
                self.emit(ASSIGN, start, self.pos)
            elif ch == ":":
                if text[self.pos:self.pos + 1] != "=":
                    self.error("expected :=", start)
                    return False
                self.pos += 1
                self.emit(DECLARE, start, self.pos)
            elif ch == "|":
                self.emit(PIPE, start, self.pos)
            elif ch == '"':
                if not self.quote(start):
                    return False
            elif ch == "`":
                close = text.find("`", self.pos)
                if close < 0:
                    self.error("unterminated raw quoted string", start)
                    return False
                self.pos = close + 1
                self.emit(RAW, start, self.pos)
            elif ch == "'":
                if not self.char(start):
                    return False
            elif ch == "$":
                if not self.field_or_variable(start, VAR):
                    return False
            elif ch == ".":
                if text[self.pos:self.pos + 1].isdigit():
                    self.pos = start
                    if not self.number():
                        return False
                elif not self.field_or_variable(start, FIELD):
                    return False
            elif ch in "+-" or ch.isdigit():
                self.pos = start
                if not self.number():
                    return False
            elif _alnum(ch):
                while self.pos < len(text) and _alnum(text[self.pos]):
                    self.pos += 1
                if not self.at_terminator():
                    self.error(f"bad character {_rune(text[self.pos])}", self.pos)
                    return False
                word = text[start:self.pos]
                typ = word if word in KEYWORDS else BOOL if word in ("true", "false") else IDENT
                self.emit(typ, start, self.pos)
            elif ch == "(":
                self.paren += 1
                self.emit(LPAREN, start, self.pos)
            elif ch == ")":
                self.paren -= 1
                if self.paren < 0:
                    self.error("unexpected right paren", start)
                    return False
                self.emit(RPAREN, start, self.pos)
            elif ch.isascii() and ch.isprintable():
                self.emit(PUNCT, start, self.pos)
            else:
                self.error(f"unrecognized character in action: {_rune(ch)}", start)
                return False

    def quote(self, start: int) -> bool:
        text = self.text
        while True:
            ch = text[self.pos:self.pos + 1]
            self.pos += 1
            if ch == "\\":
                ch = text[self.pos:self.pos + 1]
                self.pos += 1
                if ch in ("", "\n"):
                    self.error("unterminated quoted string", start)
                    return False
            elif ch in ("", "\n"):
                self.error("unterminated quoted string", start)
                return False
            elif ch == '"':
                self.emit(STRING, start, self.pos)
                return True

    def char(self, start: int) -> bool:
        text = self.text
        while True:
            ch = text[self.pos:self.pos + 1]
            self.pos += 1
            if ch == "\\":
                ch = text[self.pos:self.pos + 1]
                self.pos += 1
                if ch in ("", "\n"):
                    self.error("unterminated character constant", start)
                    return False
            elif ch in ("", "\n"):
                self.error("unterminated character constant", start)
                return False
            elif ch == "'":
                self.emit(CHAR, start, self.pos)
                return True

    def field_or_variable(self, start: int, typ: str) -> bool:
        if self.at_terminator():
            self.emit(VAR if typ == VAR else DOT, start, self.pos)
            return True
        while self.pos < len(self.text) and _alnum(self.text[self.pos]):
            self.pos += 1
        if not self.at_terminator():
            self.error(f"bad character {_rune(self.text[self.pos])}", self.pos)
            return False
        self.emit(typ, start, self.pos)
        return True

    def number(self) -> bool:
        start = self.pos
        hit = _NUMBER.match(self.text, start)
        end = hit.end() if hit else start
        if end == start or (self.text[end:end + 1] and _alnum(self.text[end])):
            self.error(f"bad number syntax: {_quote(self.text[start:end + 1])}", start)
            return False
        self.pos = end
        self.emit(NUMBER, start, end)
        return True


def _rune(ch: str) -> str:
    return f"U+{ord(ch):04X} '{ch}'" if ch.isprintable() else f"U+{ord(ch):04X}"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _describe(tok: Token) -> str:
    """A token as Go's item.String() writes it in errors."""
    if tok.typ == EOF:
        return "EOF"
    if tok.typ in KEYWORDS:
        return f"<{tok.val}>"
    if len(tok.val) > 10:
        return _quote(tok.val[:10]) + "..."
    return _quote(tok.val)


# ── Parser ─────────────────────────────────────────────────────────────

class _End(Exception):
    """An {{end}} or {{else}} closing a list."""

    def __init__(self, kind: str, tok: Token, else_if: bool = False) -> None:
        super().__init__(kind)
        self.kind = kind
        self.tok = tok
        self.else_if = else_if


class _Parser:
    def __init__(self, text: str, funcs: Set[str], dot: Optional[str],
                 define_dots: Dict[str, Optional[str]],
                 members: Dict[str, Dict[str, Optional[str]]]) -> None:
        self.tokens = _Lexer(text).run()
        self.i = 0
        self.funcs = funcs
        self.vars: List[str] = ["$"]
        self.range_depth = 0
        self.defines: Dict[str, int] = {}
        self.full: Set[str] = set()  # defined templates with more than space and comments
        self.calls: List[Tuple[str, int]] = []
        # the type of dot and of $, when known, and the define being parsed
        self.dot, self.root, self.define = dot, dot, ""
        self.define_dots = define_dots
        self.members = members
        self.missing: List[Tuple[int, str, str, str]] = []
        self.typed_calls: List[Tuple[str, Optional[str]]] = []

    # tokens

    def next(self) -> Token:
        tok = self.tokens[self.i]
        if tok.typ == ERROR:
            raise TemplateError(tok.val, tok.pos)
        if tok.typ != EOF:
            self.i += 1
        return tok

    def backup(self, n: int = 1) -> None:
        self.i -= n

    def peek(self) -> Token:
        tok = self.next()
        if tok.typ != EOF:
            self.backup()
        return tok

    def next_non_space(self) -> Token:
        tok = self.next()
        while tok.typ == SPACE:
            tok = self.next()
        return tok

    def peek_non_space(self) -> Token:
        start = self.i
        tok = self.next_non_space()
        self.i = start
        return tok

    def unexpected(self, tok: Token, context: str):
        if tok.typ == ERROR:
            raise TemplateError(tok.val, tok.pos)
        raise TemplateError(f"unexpected {_describe(tok)} in {context}", tok.pos)

    def expect(self, typ: str, context: str) -> Token:
        tok = self.next_non_space()
        if tok.typ != typ:
            self.unexpected(tok, context)
        return tok

    # structure

    def parse(self) -> None:
        while self.peek().typ != EOF:
            if self.peek().typ == LEFT:
                start = self.i
                self.next()
                if self.next_non_space().typ == "define":
                    self.definition()
                    continue
                self.i = start
            self.text_or_action(top=True)

    def definition(self) -> None:
        tok = self.next_non_space()
        name = self.template_name(tok, "define clause")
        self.expect(RIGHT, "define clause")
        self.vars = ["$"]
        saved = self.dot, self.root, self.define
        self.dot = self.root = self.define_dots.get(name)
        self.define = name
        end, full = self.item_list()
        self.dot, self.root, self.define = saved
        if end.kind != "end":
            raise TemplateError("unexpected {{else}} in define clause", end.tok.pos)
        self.add(name, tok.pos, full)

    def add(self, name: str, pos: int, full: bool) -> None:
        """Tree.add: one text may define a name twice only if one of them is empty."""
        if name in self.full and full:
            raise TemplateError(f'template: multiple definition of template "{name}"', pos)
        self.defines.setdefault(name, pos)
        if full:
            self.full.add(name)

    def item_list(self) -> Tuple[_End, bool]:
        """Items up to the {{end}} or {{else}} that closes them, and whether there is more
        than space among them."""
        full = False
        while True:
            if self.peek_non_space().typ == EOF:
                raise TemplateError("unexpected EOF", self.peek_non_space().pos)
            try:
                full = self.text_or_action(top=False) or full
            except _End as end:
                return end, full

    def text_or_action(self, top: bool) -> bool:
        """One text or action; True unless it is only space."""
        tok = self.next_non_space()
        if tok.typ == TEXT:
            return bool(tok.val.strip())
        if tok.typ == LEFT:
            try:
                self.action()
            except _End as end:
                if top:
                    word = "{{end}}" if end.kind == "end" else "{{else}}"
                    raise TemplateError(f"unexpected {word}", end.tok.pos)
                raise
            return True
        if tok.typ == EOF:
            raise TemplateError("unexpected EOF", tok.pos)
        self.unexpected(tok, "input")

    def action(self) -> None:
        tok = self.next_non_space()
        if tok.typ == "block":
            self.block()
        elif tok.typ in ("break", "continue"):
            self.expect(RIGHT, f"{{{{{tok.val}}}}}")
            if not self.range_depth:
                raise TemplateError(f"{{{{{tok.val}}}}} outside {{{{range}}}}", tok.pos)
        elif tok.typ == "else":
            peek = self.peek_non_space()
            if peek.typ in ("if", "with"):
                raise _End("else", tok, else_if=True)
            self.expect(RIGHT, "else")
            raise _End("else", tok)
        elif tok.typ == "end":
            self.expect(RIGHT, "end")
            raise _End("end", tok)
        elif tok.typ in ("if", "range", "with"):
            self.control(tok.typ)
        elif tok.typ == "template":
            name_tok = self.next_non_space()
            name = self.template_name(name_tok, "template clause")
            self.calls.append((name, name_tok.pos))
            typ = None
            if self.next_non_space().typ != RIGHT:
                self.backup()
                typ = self.pipeline("template clause", RIGHT)
            self.typed_calls.append((name, typ))
        elif tok.typ == "define":
            self.unexpected(tok, "command")
        else:
            self.backup()
            self.pipeline("command", RIGHT)

    def block(self) -> None:
        name_tok = self.next_non_space()
        name = self.template_name(name_tok, "block clause")
        typ = self.pipeline("block clause", RIGHT)
        saved, depth = self.vars, self.range_depth
        types = self.dot, self.root, self.define
        self.vars, self.range_depth = ["$"], 0
        self.dot = self.root = typ  # the block runs on its pipeline's value
        self.define = name
        end, full = self.item_list()
        self.vars, self.range_depth = saved, depth
        self.dot, self.root, self.define = types
        if end.kind != "end":
            raise TemplateError(f"unexpected {{{{{end.kind}}}}} in block clause", end.tok.pos)
        self.add(name, name_tok.pos, full)
        self.calls.append((name, name_tok.pos))

    def control(self, context: str) -> None:
        depth = len(self.vars)
        outer = self.dot
        try:
            typ = self.pipeline(context, RIGHT)
            if context == "range":
                self.range_depth += 1
                self.dot = ELEMENT.get(typ) if typ else None
            elif context == "with":
                self.dot = typ
            end, _ = self.item_list()
            self.dot = outer  # an else runs on the outer dot
            if context == "range":
                self.range_depth -= 1
            if end.kind == "else":
                if end.else_if:
                    tok = self.next_non_space()  # the `if` or `with` after else
                    if tok.typ != context:
                        self.unexpected(tok, "input")
                    self.control(context)
                    return
                end, _ = self.item_list()
                if end.kind != "end":
                    raise TemplateError("expected end; found {{else}}", end.tok.pos)
        finally:
            del self.vars[depth:]
            self.dot = outer

    def template_name(self, tok: Token, context: str) -> str:
        if tok.typ == STRING:
            return _unquote_go(tok.val)
        if tok.typ == RAW:
            return tok.val[1:-1]
        self.unexpected(tok, context)
        return ""

    def pipeline(self, context: str, end: str) -> Optional[str]:
        """Parse a pipeline; returns the type of its value when amlint can tell."""
        declared = 0
        while True:
            peek = self.peek_non_space()
            if peek.typ != VAR:
                break
            start = self.i
            var = self.next_non_space()
            nxt = self.peek_non_space()
            if nxt.typ in (ASSIGN, DECLARE):
                self.next_non_space()
                self.vars.append(var.val)  # Go checks neither form here
                declared += 1
                break
            if nxt.typ == PUNCT and nxt.val == ",":
                self.next_non_space()
                self.vars.append(var.val)
                declared += 1
                if context == "range" and declared < 2:
                    if self.peek_non_space().typ in (VAR, RIGHT, RPAREN):
                        continue
                    raise TemplateError("range can only initialize variables", nxt.pos)
                raise TemplateError(f"too many declarations in {context}", nxt.pos)
            self.i = start
            break
        commands, value = 0, None
        while True:
            tok = self.next_non_space()
            if tok.typ == end:
                if not commands:
                    raise TemplateError(f"missing value for {context}", tok.pos)
                return value if commands == 1 else None
            if tok.typ in (BOOL, CHAR, DOT, FIELD, IDENT, NUMBER, "nil", RAW, STRING, VAR,
                           LPAREN):
                self.backup()
                first, value = self.command()
                if commands and first in (BOOL, CHAR, DOT, NUMBER, "nil", RAW, STRING):
                    raise TemplateError(
                        f"non executable command in pipeline stage {commands + 1}", tok.pos)
                commands += 1
            else:
                self.unexpected(tok, context)

    def command(self) -> Tuple[str, Optional[str]]:
        """One command of a pipeline: the kind of its first operand, and the type of its
        value when it is a single operand amlint can follow."""
        first, value, operands = None, None, 0
        while True:
            self.peek_non_space()
            typ, value_type = self.operand()
            if typ is not None:
                operands += 1
                if first is None:
                    first, value = typ, value_type
            tok = self.next()
            if tok.typ == SPACE:
                continue
            if tok.typ in (RIGHT, RPAREN):
                self.backup()
            elif tok.typ != PIPE:
                self.unexpected(tok, "operand")
            break
        if first is None:
            raise TemplateError("empty command", self.tokens[self.i].pos)
        return first, value if operands == 1 else None

    def operand(self) -> Tuple[Optional[str], Optional[str]]:
        """The kind of an operand and the type of its value, or (None, None) if there is
        no operand here."""
        typ, value = self.term()
        if typ is None:
            return None, None
        if self.peek().typ == FIELD:
            if typ in (BOOL, STRING, RAW, NUMBER, CHAR, "nil", DOT):
                tok = self.peek()
                raise TemplateError(f"unexpected . after term {_describe(tok)}", tok.pos)
            while self.peek().typ == FIELD:
                value = self.field(value, self.next())
        return typ, value

    def field(self, typ: Optional[str], tok: Token) -> Optional[str]:
        """The type of field `tok` of a value of type `typ`; records a field that isn't
        there."""
        if typ is None:
            return None
        name = tok.val[1:]
        members = self.members.get(typ, {})
        if name in members:
            return members[name]
        if typ in MAPS:
            return MAPS[typ]  # a map key; a missing one is the zero value (missingkey=zero)
        self.missing.append((tok.pos, name, typ, self.define))
        return None

    def term(self) -> Tuple[Optional[str], Optional[str]]:
        tok = self.next_non_space()
        if tok.typ == IDENT:
            if tok.val not in self.funcs:
                raise TemplateError(f'function "{tok.val}" not defined', tok.pos)
            return IDENT, None
        if tok.typ == VAR:
            self.use_var(tok)
            return VAR, self.root if tok.val == "$" else None
        if tok.typ == LPAREN:
            return LPAREN, self.pipeline("parenthesized pipeline", RPAREN)
        if tok.typ == STRING:
            if _unquote_go(tok.val) is None:
                raise TemplateError(f"invalid syntax: {tok.val}", tok.pos)
            return STRING, "string"
        if tok.typ == NUMBER and not re.search("[0-9]", tok.val):
            raise TemplateError(f"illegal number syntax: {_quote(tok.val)}", tok.pos)
        if tok.typ == DOT:
            return DOT, self.dot
        if tok.typ == FIELD:
            return FIELD, self.field(self.dot, tok)
        if tok.typ in ("nil", BOOL, CHAR, NUMBER, RAW):
            return tok.typ, "string" if tok.typ == RAW else None
        self.backup()
        return None, None

    def use_var(self, tok: Token) -> None:
        name = tok.val.split(".")[0]
        if name not in self.vars:
            raise TemplateError(f"undefined variable \"{name}\"", tok.pos)


_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
            "\\": "\\", '"': '"', "'": "'"}


def _unquote_go(literal: str) -> Optional[str]:
    """A double-quoted Go string literal's value, or None if it is invalid."""
    body, out, i = literal[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1:i + 2]
        if nxt in _ESCAPES and nxt != "'":
            out.append(_ESCAPES[nxt])
            i += 2
        elif nxt in ("x", "u", "U"):
            size = {"x": 2, "u": 4, "U": 8}[nxt]
            digits = body[i + 2:i + 2 + size]
            if not re.fullmatch("[0-9a-fA-F]{%d}" % size, digits):
                return None
            out.append(chr(int(digits, 16)) if int(digits, 16) <= 0x10FFFF else "")
            i += 2 + size
        elif re.fullmatch("[0-7]{3}", body[i + 1:i + 4]):
            out.append(chr(int(body[i + 1:i + 4], 8)))
            i += 4
        else:
            return None
    return "".join(out)


def parse(text: str, funcs: Set[str], dot: Optional[str] = None,
          define_dots: Optional[Dict[str, Optional[str]]] = None,
          members: Optional[Dict[str, Dict[str, Optional[str]]]] = None) -> Parsed:
    """Parse `text` with the functions in `funcs` (plus Go's own) defined. `dot` is the type
    of the data the text runs on (DATA for a notification field, None if unknown), and
    `define_dots` the type each {{define}} in it is called with."""
    parser = _Parser(text, set(funcs) | GO_FUNCS, dot, define_dots or {}, members or MEMBERS)
    try:
        parser.parse()
    except TemplateError as e:
        return Parsed(e, parser.defines, parser.calls, [], [])
    return Parsed(None, parser.defines, parser.calls, parser.missing, parser.typed_calls)
