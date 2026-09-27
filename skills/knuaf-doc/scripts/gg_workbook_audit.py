#!/usr/bin/env python3
"""Read-only XLSX formula inventory and scoped financial relation audit.

Only the Python standard library is used. Values and cached results stay in memory.
The JSON results contain formulas, coordinates, and synthetic deltas only.
"""
import argparse
import copy
from collections import defaultdict, namedtuple
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET


NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
FORMULA = namedtuple("Formula", "text anchor cached")
REF = re.compile(r"(?:(?P<sheet>'(?:[^']|'')+'|[^\W\d][\w.]*)!)?(?P<colabs>\$?)(?P<col>[A-Z]{1,3})(?P<rowabs>\$?)(?P<row>[1-9][0-9]*)", re.UNICODE)
LEX = re.compile(r"\s*(?:(?P<ref>(?:(?:'(?:[^']|'')+'|[^\W\d][\w.]*)!)?\$?[A-Z]{1,3}\$?[1-9][0-9]*)|(?P<num>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)|(?P<error>\#(?:DIV/0!|VALUE!|REF!|N/A|NUM!|NAME\?|NULL!))|(?P<ident>[A-Za-z_][A-Za-z_0-9.]*)|(?P<op><>|<=|>=|[+*/^(),:%=<>-]))", re.UNICODE)
ERRORS = {"#DIV/0!", "#VALUE!", "#REF!", "#N/A", "#NUM!", "#NAME?", "#NULL!"}
SUPPORTED = {"SUM", "AVERAGE", "PMT"}
TOL_ABS = 1e-7
TOL_REL = 1e-9


class Unsupported(Exception):
    pass


def colnum(s):
    n = 0
    for ch in s:
        n = n * 26 + ord(ch) - 64
    return n


def colname(n):
    s = ""
    while n:
        n, d = divmod(n - 1, 26)
        s = chr(65 + d) + s
    return s


def split_addr(addr):
    m = REF.fullmatch(addr)
    if not m or m.group("sheet"):
        raise ValueError("invalid cell address")
    return colnum(m.group("col")), int(m.group("row"))


def translate(formula, anchor, follower):
    """Translate only A1 references in an actual shared follower's anchor."""
    ac, ar = split_addr(anchor)
    fc, fr = split_addr(follower)

    def replace(m):
        c = colnum(m.group("col")) + (0 if m.group("colabs") else fc - ac)
        r = int(m.group("row")) + (0 if m.group("rowabs") else fr - ar)
        if c < 1 or r < 1:
            return "#REF!"
        return (m.group("sheet") + "!" if m.group("sheet") else "") + m.group("colabs") + colname(c) + m.group("rowabs") + str(r)

    return REF.sub(replace, formula)


def tokens(text):
    if text.startswith("="):
        text = text[1:]
    out, pos = [], 0
    while pos < len(text):
        m = LEX.match(text, pos)
        if not m:
            raise Unsupported("syntax at offset " + str(pos))
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    out.append(("end", ""))
    return out


class Parser:
    def __init__(self, text):
        self.toks = tokens(text)
        self.i = 0

    def peek(self):
        return self.toks[self.i][1]

    def pop(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        ast = self.expr(0)
        if self.toks[self.i][0] != "end":
            raise Unsupported("trailing formula tokens")
        return ast

    def expr(self, minimum):
        kind, value = self.pop()
        if value in ("+", "-"):
            left = ("unary", value, self.expr(5))
        elif value == "(":
            left = self.expr(0)
            if self.pop()[1] != ")":
                raise Unsupported("missing close parenthesis")
        elif kind == "num":
            left = ("num", float(value))
        elif kind == "error":
            left = ("error", value)
        elif kind == "ref":
            left = ("ref", value)
        elif kind == "ident":
            if self.pop()[1] != "(":
                raise Unsupported("named range " + value)
            args = []
            if self.peek() != ")":
                while True:
                    args.append(self.expr(0))
                    if self.peek() != ",":
                        break
                    self.pop()
            if self.pop()[1] != ")":
                raise Unsupported("function arguments")
            if value.upper() not in SUPPORTED:
                raise Unsupported("function " + value)
            left = ("call", value.upper(), args)
        else:
            raise Unsupported("unexpected token " + value)
        while True:
            op = self.peek()
            if op == "%" and 7 >= minimum:
                self.pop()
                left = ("percent", left)
                continue
            precedence = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1,
                          "+": 2, "-": 2, "*": 3, "/": 3, "^": 4, ":": 6}.get(op, -1)
            if precedence < minimum:
                break
            self.pop()
            right = self.expr(precedence + (0 if op == "^" else 1))
            left = ("binary", op, left, right)
        return left


def reference(ref, current_sheet):
    m = REF.fullmatch(ref)
    if not m:
        raise Unsupported("reference " + ref)
    sheet = m.group("sheet")
    if sheet:
        sheet = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
    return sheet or current_sheet, colnum(m.group("col")), int(m.group("row"))


def ast_refs(ast, sheet):
    if ast[0] == "ref":
        s, c, r = reference(ast[1], sheet)
        return [(s, colname(c) + str(r))]
    if ast[0] == "binary" and ast[1] == ":":
        endpoints = []
        inherited = sheet
        def collect(part):
            nonlocal inherited
            if part[0] == "ref":
                point = reference(part[1], inherited)
                endpoints.append(point)
                inherited = point[0]
            elif part[0] == "binary" and part[1] == ":":
                collect(part[2]); collect(part[3])
            else:
                raise Unsupported("range endpoints")
        collect(ast)
        sheets = {x[0] for x in endpoints}
        if len(sheets) != 1:
            raise Unsupported("cross-sheet or oversized range")
        s1 = endpoints[0][0]
        c1, c2 = min(x[1] for x in endpoints), max(x[1] for x in endpoints)
        r1, r2 = min(x[2] for x in endpoints), max(x[2] for x in endpoints)
        if (c2-c1+1) * (r2-r1+1) > 100000:
            raise Unsupported("oversized range")
        return [(s1, colname(c) + str(r)) for c in range(c1, c2+1)
                for r in range(r1, r2+1)]
    found = []
    for child in ast[1:]:
        if isinstance(child, tuple):
            found += ast_refs(child, sheet)
        elif isinstance(child, list):
            for part in child:
                found += ast_refs(part, sheet)
    return found


def ast_functions(ast):
    out = []
    if ast[0] == "call":
        out.append(ast[1])
    for child in ast[1:]:
        if isinstance(child, tuple):
            out += ast_functions(child)
        elif isinstance(child, list):
            for part in child:
                out += ast_functions(part)
    return sorted(set(out))


def ast_ranges(ast, sheet):
    if ast[0] == "binary" and ast[1] == ":":
        cells = ast_refs(ast, sheet)
        cs = [split_addr(a)[0] for _, a in cells]
        rs = [split_addr(a)[1] for _, a in cells]
        return [{"sheet": cells[0][0],
                 "start": colname(min(cs)) + str(min(rs)),
                 "end": colname(max(cs)) + str(max(rs)),
                 "scope": "same_sheet" if cells[0][0] == sheet else "cross_sheet"}]
    out = []
    for child in ast[1:]:
        if isinstance(child, tuple):
            out += ast_ranges(child, sheet)
        elif isinstance(child, list):
            for part in child:
                out += ast_ranges(part, sheet)
    return out


def relative_pattern(text, address):
    c0, r0 = split_addr(address)
    def replace(m):
        c, r = colnum(m.group("col")), int(m.group("row"))
        return ((m.group("sheet") + "!" if m.group("sheet") else "") +
                "R" + (str(r) if m.group("rowabs") else "[" + str(r-r0) + "]") +
                "C" + (str(c) if m.group("colabs") else "[" + str(c-c0) + "]"))
    return REF.sub(replace, text)


class Workbook:
    def __init__(self):
        self.sheets = []
        self.values = {}
        self.formulas = {}
        self.types = {}
        self._ast = {}
        self.shared = defaultdict(dict)

    @classmethod
    def load(cls, path):
        book = cls()
        with ZipFile(path) as z:
            root = ET.fromstring(z.read("xl/workbook.xml"))
            relroot = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            rels = {x.get("Id"): x.get("Target") for x in relroot.findall(PKG + "Relationship")}
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings = ["".join(t.text or "" for t in si.iter(NS + "t"))
                           for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(NS + "si")]
            for sh in root.findall(".//" + NS + "sheet"):
                name = sh.get("name")
                target = rels[sh.get(REL + "id")]
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                target = re.sub(r"^xl/xl/", "xl/", target)
                book.sheets.append(name)
                sheetroot = ET.fromstring(z.read(target))
                for c in sheetroot.findall(".//" + NS + "sheetData/" + NS + "row/" + NS + "c"):
                    addr, typ = c.get("r"), c.get("t", "n")
                    f, v = c.find(NS + "f"), c.find(NS + "v")
                    raw = v.text if v is not None else None
                    if typ == "s" and raw is not None:
                        value = strings[int(raw)]
                    elif typ == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(NS + "t"))
                    elif typ == "e":
                        value = raw
                    elif typ == "b" and raw is not None:
                        value = raw == "1"
                    elif raw is None:
                        value = None
                    elif typ in ("str", "d"):
                        value = raw
                    else:
                        try:
                            value = float(raw)
                        except ValueError:
                            value = raw
                    book.values[name, addr] = value
                    book.types[name, addr] = typ
                    if f is not None:
                        text = f.text or ""
                        si = f.get("si") if f.get("t") == "shared" else None
                        if si is not None and text:
                            book.shared[name][si] = (addr, text)
                        book.formulas[name, addr] = FORMULA(text, (si if not text else (addr if si is not None else None)), value)
                # Followers are explicit <f t="shared" si=...> members only.
                for (s, addr), formula in list(book.formulas.items()):
                    if s != name or formula.anchor is None or formula.text:
                        continue
                    anchor, text = book.shared[name].get(formula.anchor, (None, None))
                    if anchor is None:
                        raise Unsupported("shared anchor missing " + addr)
                    book.formulas[s, addr] = FORMULA(translate(text, anchor, addr), anchor, formula.cached)
        return book

    def parse(self, key):
        if key not in self._ast:
            self._ast[key] = Parser(self.formulas[key].text).parse()
        return self._ast[key]

    def evaluate(self, sheet, address, overrides=None, memo=None, stack=None):
        key = (sheet, address)
        overrides = overrides or {}
        memo = {} if memo is None else memo
        stack = set() if stack is None else stack
        if key in overrides:
            return overrides[key]
        if key in memo:
            return memo[key]
        if key in stack:
            raise Unsupported("circular reference")
        if key not in self.formulas:
            return self.values.get(key)
        stack.add(key)
        try:
            result = self._eval(self.parse(key), sheet, overrides, memo, stack)
            # A formula consisting solely of an empty reference displays zero.
            # Keep the empty reference typed as None inside SUM/AVERAGE first.
            if result is None:
                result = 0
            memo[key] = result
            return result
        finally:
            stack.remove(key)

    def _eval(self, node, sheet, overrides, memo, stack):
        kind = node[0]
        if kind in ("num", "error"):
            return node[1]
        if kind == "ref":
            s, c, r = reference(node[1], sheet)
            return self.evaluate(s, colname(c) + str(r), overrides, memo, stack)
        if kind == "unary" or kind == "percent":
            v = self._eval(node[-1], sheet, overrides, memo, stack)
            if isinstance(v, str) and v in ERRORS:
                return v
            x = self._number(v)
            if isinstance(x, str):
                return x
            return (x / 100 if kind == "percent" else (-x if node[1] == "-" else x))
        if kind == "binary" and node[1] == ":":
            return [self.evaluate(s, a, overrides, memo, stack) for s, a in ast_refs(node, sheet)]
        if kind == "binary":
            a = self._eval(node[2], sheet, overrides, memo, stack)
            b = self._eval(node[3], sheet, overrides, memo, stack)
            for v in (a, b):
                if isinstance(v, str) and v in ERRORS:
                    return v
            a, b = self._number(a), self._number(b)
            if isinstance(a, str) or isinstance(b, str):
                return "#VALUE!"
            op = node[1]
            try:
                return {"+": lambda: a+b, "-": lambda: a-b, "*": lambda: a*b,
                        "/": lambda: a/b, "^": lambda: a**b,
                        "=": lambda: a == b, "<>": lambda: a != b,
                        "<": lambda: a < b, ">": lambda: a > b,
                        "<=": lambda: a <= b, ">=": lambda: a >= b}[op]()
            except ZeroDivisionError:
                return "#DIV/0!"
            except (OverflowError, ValueError):
                return "#NUM!"
        if kind == "call":
            args = [self._eval(x, sheet, overrides, memo, stack) for x in node[2]]
            flat = [v for arg in args for v in (arg if isinstance(arg, list) else [arg])]
            for value in flat:
                if isinstance(value, str) and value in ERRORS:
                    return value
            if node[1] in ("SUM", "AVERAGE"):
                nums = [v for v in flat if isinstance(v, (int, float, bool))]
                if node[1] == "SUM":
                    return sum(nums)
                return sum(nums) / len(nums) if nums else "#DIV/0!"
            if node[1] == "PMT":
                if not 3 <= len(args) <= 5:
                    raise Unsupported("PMT arity")
                rate, periods, present = (self._number(x) for x in args[:3])
                future = self._number(args[3]) if len(args) >= 4 else 0
                timing = self._number(args[4]) if len(args) == 5 else 0
                if any(isinstance(x, str) for x in (rate, periods, present, future, timing)):
                    return "#VALUE!"
                if periods == 0:
                    return "#DIV/0!"
                try:
                    growth = (1 + rate) ** periods
                    return (-(present + future) / periods if rate == 0 else
                            -(present * growth + future) * rate / ((growth - 1) * (1 + rate * timing)))
                except ZeroDivisionError:
                    return "#DIV/0!"
        raise Unsupported("AST " + kind)

    @staticmethod
    def _number(value):
        if value is None:
            return 0
        if isinstance(value, (int, float, bool)):
            return value
        return "#VALUE!"


def equal_value(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=TOL_REL, abs_tol=TOL_ABS)


def inventory(book, include_cache_values=False):
    """Inventory formulas; raw cache pairs are local-only when explicitly asked."""
    entries, unsupported, disagreement, dynamic = [], [], [], []
    supported = agreeing = 0
    memo = {}
    for (sheet, addr), formula in sorted(book.formulas.items()):
        item = {"sheet": sheet, "cell": addr, "formula": formula.text,
                "shared_anchor": formula.anchor, "relative_pattern": relative_pattern(formula.text, addr)}
        try:
            ast = book.parse((sheet, addr))
            refs = ast_refs(ast, sheet)
            item["references"] = [{"sheet": s, "cell": a,
                                   "scope": "same_sheet" if s == sheet else "cross_sheet"}
                                  for s, a in sorted(set(refs))]
            item["ranges"] = ast_ranges(ast, sheet)
            item["functions"] = ast_functions(ast)
            value = book.evaluate(sheet, addr, memo=memo)
            supported += 1
            if equal_value(value, formula.cached):
                agreeing += 1
            else:
                mismatch = {"sheet": sheet, "cell": addr,
                            "reason": "cache_disagreement"}
                if include_cache_values:
                    mismatch["evaluated_value"] = value
                    mismatch["cached_value"] = formula.cached
                disagreement.append(mismatch)
        except Unsupported as exc:
            item["references"] = []
            item["ranges"] = []
            item["functions"] = []
            unsupported.append({"sheet": sheet, "cell": addr, "reason": str(exc)})
        if re.search(r"\b(?:INDIRECT|OFFSET)\s*\(", formula.text, re.IGNORECASE):
            dynamic.append({"sheet": sheet, "cell": addr, "reason": "dynamic reference syntax"})
        entries.append(item)
    anchors = sum(addr == f.anchor for (_,addr), f in book.formulas.items() if f.anchor)
    followers = sum(addr != f.anchor for (_,addr), f in book.formulas.items() if f.anchor)
    return entries, {"structural_formula_count": len(entries),
                     "shared_anchor_count": anchors, "shared_follower_count": followers,
                     "evaluator_supported_count": supported,
                     "cache_agreement_count": agreeing,
                     "tolerance": {"absolute": TOL_ABS, "relative": TOL_REL},
                     "unsupported_syntax": unsupported,
                     "dynamic_references": dynamic, "cache_disagreements": disagreement}


def sheet_number(book, number):
    matches = [s for s in book.sheets if re.match(r"^\s*" + str(number) + r"\s*[. ]", s)]
    if len(matches) != 1:
        raise ValueError("sheet role is not unique: " + str(number))
    return matches[0]


def in_rectangle(addr, rectangle):
    a, _, b = rectangle.partition(":")
    b = b or a
    c, r = split_addr(addr)
    ca, ra = split_addr(a)
    cb, rb = split_addr(b)
    return min(ca, cb) <= c <= max(ca, cb) and min(ra, rb) <= r <= max(ra, rb)


def graph(book, entries=None):
    if entries is None:
        entries, _ = inventory(book)
    deps = {}
    for item in entries:
        deps[item["sheet"], item["cell"]] = {(r["sheet"], r["cell"])
                                                  for r in item["references"]}
    return deps


def reaches(deps, start, target):
    todo, seen = [start], set()
    while todo:
        key = todo.pop()
        if key == target:
            return True
        if key not in seen:
            seen.add(key)
            todo.extend(deps.get(key, ()))
    return False


def downstream(deps, source):
    return downstream_many(deps, [source])


def downstream_many(deps, sources):
    """Strict transitive dependent closure of every seed, including range refs."""
    rev = defaultdict(set)
    for output, inputs in deps.items():
        for input_key in inputs:
            rev[input_key].add(output)
    seeds = set(sources)
    todo, seen, affected = list(seeds), set(seeds), set()
    while todo:
        key = todo.pop()
        for dependent in rev.get(key, ()):
            if dependent not in seen:
                seen.add(dependent)
                affected.add(dependent)
                todo.append(dependent)
    return affected


def numeric_delta(before, after):
    if isinstance(before, (int, float, bool)) and isinstance(after, (int, float, bool)):
        d = round(float(after) - float(before), 8)
        return 0 if d == 0 else d
    if before == after:
        return "unchanged_non_numeric"
    return "changed_type"


def perturb(book, input_key, delta, outputs):
    """One synthetic delta. Outputs are changes only, never source values."""
    baseline_input = book.evaluate(*input_key)
    if baseline_input is None:
        baseline_input = 0
    if not isinstance(baseline_input, (int, float, bool)):
        raise Unsupported("probe input is not numeric")
    override = {input_key: baseline_input + delta}
    result = []
    for key in outputs:
        before = book.evaluate(*key)
        after = book.evaluate(*key, overrides=override)
        result.append({"sheet": key[0], "cell": key[1], "delta": numeric_delta(before, after)})
    return {"input": {"sheet": input_key[0], "cell": input_key[1], "delta": delta,
                      "injection": "formula_result" if input_key in book.formulas else "input_cell"},
            "outputs": result}


def analyze_rules(book, blocks, entries, ref="X01"):
    """Return structural candidates; exception review happens in finding disposition."""
    deps = graph(book, entries)
    out = []
    regions_by_number = {k:list(v) for k,v in blocks["regions"].items()}
    for override in blocks.get("file_region_maps",{}).get(ref,{}).get("overrides",[]):
        regions_by_number.setdefault(override["sheet"],[]).append(override)
    def add(rule, sheet, cells, relation, status="unresolved"):
        out.append({"rule_id": rule, "sheet": sheet, "cells": sorted(set(cells)),
                    "expected_relation": relation, "exception_check": status})
    for number, rows in blocks["year_pattern_rows"].items():
        sheet = sheet_number(book, number)
        columns = blocks["periods"][number]["columns"]
        fixed = {(sheet_number(book, item["sheet"]), item["cell"])
                 for item in blocks.get("intentional_fixed_refs", {}).get(number, [])}
        for row in rows:
            patterns = [(col, relative_pattern(book.formulas[sheet, col+str(row)].text,
                                              col+str(row)))
                        for col in columns if (sheet, col+str(row)) in book.formulas]
            if len(patterns) >= 2:
                base = patterns[0][1]
                deviants = {col+str(row) for col, pattern in patterns[1:] if pattern != base}
                # A literal absolute source can keep the same relative pattern
                # in every year. Compare resolved dependencies as well.
                for (left, _), (right, _) in zip(patterns, patterns[1:]):
                    left_key, right_key = (sheet, left+str(row)), (sheet, right+str(row))
                    repeated = (deps.get(left_key, set()) & deps.get(right_key, set())) - fixed
                    if repeated:
                        deviants.add(right+str(row))
                if deviants:
                    add("R1", sheet, sorted(deviants),
                        "annual formulas should advance their year dependencies; only listed fixed references may repeat")
    for item in entries:
        number = re.match(r"^\s*(\d+)", item["sheet"])
        if not number:
            continue
        number = number.group(1)
        regions = regions_by_number.get(number, [])
        if not any(x["role"] == "calculation" and in_rectangle(item["cell"], x["range"])
                   for x in regions):
            continue
        blank = [(r["sheet"], r["cell"]) for r in item["references"]
                 if book.values.get((r["sheet"], r["cell"])) is None
                 and (r["sheet"], r["cell"]) not in book.formulas]
        if blank:
            add("R2", item["sheet"], [item["cell"]] + [a for s,a in blank if s == item["sheet"]],
                "calculation reference to blank input must be justified",
                "optional_blank_unresolved")
    for spec in blocks["subtotals"]:
        sheet = sheet_number(book, spec["sheet"])
        for col in spec["columns"]:
            subtotal = (sheet, col+str(spec["row"]))
            missing = [col+str(r) for r in spec["members"]
                       if not reaches(deps, subtotal, (sheet, col+str(r)))]
            if missing:
                add("R3", sheet, [subtotal[1]] + missing,
                    "each listed cost row should reach the subtotal through the dependency graph")
    for number, cells in blocks["constant_siblings"].items():
        sheet = sheet_number(book, number)
        for addr in cells:
            if (sheet, addr) not in book.formulas:
                add("R4", sheet, [addr],
                    "stored calculation cell needs an intentional manual-input rule")
    downstream_refs = {r for refs in deps.values() for r in refs}
    for number, regions in regions_by_number.items():
        input_regions = [x for x in regions if x["role"] == "input"]
        if not input_regions:
            continue
        input_sheet = sheet_number(book, number)
        unreachable = [addr for (s, addr), v in book.values.items()
                       if s == input_sheet and (s, addr) not in book.formulas
                       and isinstance(v, (int, float))
                       and any(in_rectangle(addr, x["range"]) for x in input_regions)
                       and (s, addr) not in downstream_refs]
        if unreachable:
            add("R5", input_sheet, unreachable,
                "entered input cells should reach a downstream calculation",
                "optional_input_unresolved")
    for link in blocks.get("continuity_links", []):
        sheet = sheet_number(book, link["sheet"])
        if not reaches(deps, (sheet,link["target"]), (sheet,link["source"])):
            add("R6", sheet, [link["target"],link["source"]], link["meaning"],
                "timing_or_opening_ledger_unresolved")
    cash, pl, invest = (sheet_number(book, n) for n in ("14","12","3"))
    cash_formulas = [k for k in deps if k[0] == cash]
    sources = [(pl,"C12"),(pl,"C25"),(invest,blocks["variants"][ref]["later_loan"]),
               (invest,"G19"),(sheet_number(book,"4"),"E42")]
    for source in sources:
        if not any(reaches(deps, target, source) for target in cash_formulas):
            add("R7", source[0], [source[1]],
                "P&L expense, tax or financing source should have a stated cash or payable path",
                "payment_timing_unresolved")
    base = blocks["base_date_cell"]
    bal = blocks["first_balance_year_cell"]
    base_s, balance_s = sheet_number(book, base["sheet"]), sheet_number(book, bal["sheet"])
    base_label = book.values.get((base_s, base["cell"])) or ""
    balance_label = book.values.get((balance_s, bal["cell"])) or ""
    by = re.search(r"(20\d{2})년", str(base_label))
    sy = re.search(r"(20\d{2})년", str(balance_label))
    if by and sy and by.group(1) != sy.group(1):
        add("R8", base_s, [base["cell"]],
            "opening survey date should match the balance sheet opening year")
    labor = sheet_number(book,"8")
    cost = sheet_number(book,"11")
    ly = re.search(r"20\d{2}년", str(book.values.get((labor,"F6"))))
    cy = re.search(r"20\d{2}년", str(book.values.get((cost,"C4"))))
    if ly and cy and ly.group() != cy.group():
        add("R8", labor, ["F6"], "labor plan first year should match production cost first year")
    return sorted(out, key=lambda x: (x["rule_id"], x["sheet"], x["cells"]))


# Meanings are shared; coordinates are reviewed separately for each source.
XR = {
  1: ("2", "ratio of first-year surplus to income", "ratio needs a defined zero-denominator rule", "R2", "open"),
  2: ("11", "annual rent subtotal", "each year's rent subtotal uses that year's detail", "R1", "defect"),
  3: ("15", "rent cost by income-analysis year", "income year maps to the same production-cost year", "R1", "defect"),
  4: ("2", "farm surplus indicator", "main-product and gross-revenue surplus are separately named", "R8", "policy_dependent"),
  5: ("3", "investment funding total", "meaning of total includes or excludes loan funding explicitly", "R3", "policy_dependent"),
  6: ("10", "asset depreciation", "residual rate and cap follow a declared asset policy", "R4", "policy_dependent"),
  7: ("1", "opening survey date", "opening date matches first balance-sheet opening period", "R8", "open"),
  8: ("10", "biological asset cohort carry", "later acquisitions get their own depreciation cohorts", "R6", "open"),
  9: ("11", "production cost after inventory adjustments", "each year includes its own opening, closing and transfer adjustments", "R2", "defect"),
 10: ("14", "annual investment cash outflow", "manual stored costs reconcile to each year's investment plan", "R4", "open"),
 11: ("14", "cash production payment", "noncash family labor is excluded when unpaid", "R7", "policy_dependent"),
 12: ("12", "first-year selling and administration expense", "a cash-paid expense reaches cash or payable", "R7", "defect"),
 13: ("12", "income tax expense", "cash-paid tax reaches cash or tax payable", "R7", "defect"),
 14: ("3", "follow-on loan funding", "new borrowing reaches cash, debt, interest and repayment schedule", "R7", "defect"),
 15: ("4", "principal repayment", "principal payment reaches cash and next opening debt", "R6", "defect"),
 16: ("13", "opening assets and liabilities", "opening liability reaches opening equity and next year's debt", "R6", "defect"),
 17: ("1", "inherited operating assets", "entered inherited assets reach balance and depreciation schedules", "R5", "open"),
 18: ("11", "annual miscellaneous fees", "year-specific fee input affects only its own year's cost", "R1", "defect"),
 19: ("11", "annual small-tool costs", "each year's small-tool expense reaches production cost and income once", "R1", "defect"),
 20: ("11", "contracted farming expense subtotal", "contracted farming expense reaches total production cost", "R3", "defect"),
 21: ("3", "investment grants", "each grant receipt reaches cash and its approved income/equity treatment", "R7", "open"),
 22: ("10", "truck acquisition carry-forward", "later acquisition price carries the original price unless a new acquisition or disposal occurs", "R4", "defect"),
}

# Exact audited cells, including intentionally blank or stored cells. Each
# formula is read from its own cell and included below in cell_formulas.
XR_FILE_CELLS = {
  "X01": {
    1: ["C12"], 2: ["C25","D25","E25","F25","G25"],
    3: ["E21","F21","G21","H21","I21","H22","I22"],
    4: ["C10","C11","C12"], 5: ["E25","E26","E27","E28"],
    6: ["H8","H9","H10","H12"], 7: ["B2"],
    8: ["J8","J20","J32"],
    9: ["C35","D35","E35","F35","G35","C38"],
    10: ["E15","E16"], 11: ["D17"], 12: ["C12","C26"],
    13: ["C25","C26"], 14: ["H25"], 15: ["E42","C43"],
    16: ["C25","C28","D25","D34"], 17: ["E16","E22","E34","E47"],
    18: ["C30","D30","E30","F30","G30"],
    19: ["C29","D29","E29","F29","G29"],
    20: ["C16","C28","D16","D28"],
    21: ["G19","G22","G25"], 22: ["H20","H32","H44","H56"],
  },
  "X02": {
    1: ["C12"], 2: ["C25","D25","E25","F25","G25"],
    3: ["E21","F21","G21","H21","I21","H22","I22"],
    4: ["C10","C11","C12"], 5: ["E26","E27","E28","E29"],
    6: ["H8","H9","H10","H12","J8","J9","J10","J12"],
    7: ["B2"], 8: ["J8","J20","J32","J44","J56","K12","K20","K32","K44","K56"],
    9: ["C35","D35","E35","F35","G35","C38"],
    10: ["E15","E16","E17"], 11: ["D18"], 12: ["C12","C26"],
    13: ["C25","C26"], 14: ["H26"], 15: ["E42","C43"],
    16: ["C25","C28","D25","D34"], 17: ["E16","E22","E34","E47"],
    18: ["C30","D30","E30","F30","G30"],
    19: ["C29","D29","E29","F29","G29"],
    20: ["C16","C28","D16","D28"],
    21: ["G19","G23","G26"], 22: ["H20","H32","H44","H56"],
  },
}

PROBES = {
  2: ("11","D26",3,[("11","D25")]),
  3: ("11","D26",3,[("15","F21")]),
  4: ("15","E6",20,[("15","E7"),("15","E27"),("15","E31")]),
  5: ("3","H25",5,[("3","E25")]),
  6: ("10","H9",0.1,[("10","H12")]),
  9: ("11","D32",10,[("11","D35")]),
 11: ("11","C12",20,[("14","D17")]),
 12: ("12","C12",7,[("12","C26"),("14","D18"),("13","D34")]),
 13: ("12","C25",7,[("12","C26"),("14","D18"),("13","D34")]),
 14: ("3","H25",11,[("14","E12"),("13","E34")]),
 15: ("4","E42",7,[("4","C43"),("14","D20"),("13","D34")]),
 17: ("1","E16",7,[("13","C28"),("13","D34")]),
 18: ("9","C29",3,[("11","C30"),("11","D30"),("11","G30")]),
 19: ("9","C28",5,[("11","C29"),("11","D29"),("15","F14")]),
 20: ("11","C28",17,[("11","C16"),("11","C31"),("13","D34")]),
 21: ("3","G19",19,[("14","D9"),("13","D29")]),
 22: ("3","G19",19,[("10","H8"),("10","H20"),("10","H32"),("10","H44"),("10","H56"),
                         ("13","D34"),("13","E34"),("13","F34"),("13","G34"),("13","H34")]),
}

REASONS = {
  1: "A zero denominator yields a typed divide-by-zero result; its intended first-year display remains undefined.",
  2: "Later rent subtotals repeat the first-year detail; a later-year delta does not reach its own subtotal.",
  3: "Income-analysis rent references skip or repeat production-cost years.",
  4: "The two surplus definitions use different revenue bases; final school row mapping is unresolved.",
  5: "The displayed total omits the loan column under a total-investment interpretation; the label needs authority.",
  6: "Fixed residual percentages and displayed residual inputs require an asset policy decision.",
  7: "Opening survey year and balance-sheet opening year require alignment.",
  8: "Later biological-asset cohorts are not separately linked by the observed carry formulas.",
  9: "Later production-cost formulas omit annual inventory and transfer adjustments; C38 is blank.",
 10: "Stored investment outflow cells may be intentional manual entries or stale links.",
 11: "If family labor is unpaid, the cash formula includes a noncash economic cost.",
 12: "A first-year expense has no cash or payable path; payment timing is unresolved.",
 13: "Tax expense has no cash or payable path; payment timing is unresolved.",
 14: "Later borrowing has no loan-tranche schedule; draw timing is unresolved.",
 15: "Principal repayment has no cash or next-opening path; payment timing is unresolved.",
 16: "An opening liability affects the opening position but disappears from forecast debt.",
 17: "Opening asset input cells have no automatic path to forecast balance and depreciation.",
 18: "The first-year fee reference is repeated across later cost years.",
 19: "Later small-tool cost rows are disconnected and income analysis repeats the first year.",
 20: "The contracted farming cost row has no dependency path to the expense subtotal.",
 21: "Grant receipts are incompletely linked across items and years; accounting treatment remains open.",
 22: "X01 has four stored later-year acquisition prices; X02 carries formulas from the prior year.",
}

ASSUMPTIONS = {
  2: ["Annual rental costs may differ."], 3: ["Income and production columns denote the same calendar year."],
  5: ["Total investment includes loans if the displayed label means all funding."],
  9: ["Annual stock and transfer adjustments can be nonzero."],
 11: ["Family labor valuation is unpaid."], 12: ["The first-year expense is paid in that year."],
 13: ["Tax expense is paid in that year; otherwise a payable is recorded."],
 14: ["A later loan is actually drawn."], 15: ["Principal is paid in cash."],
 16: ["Opening debt is inherited and remains outstanding."],
 17: ["An inherited asset is to be used by the new enterprise."],
 18: ["Miscellaneous fees may differ by year."], 19: ["Small-tool costs may differ by year."],
 20: ["Contracted farming services are used."],
 21: ["The grant is received; its income/equity classification is undecided."],
 22: ["No new truck purchase or disposal is introduced during this probe."],
}

DEPENDENCIES = {
  1: ["first-year zero denominator convention"], 4: ["school row mapping"],
  5: ["meaning of investment total label"], 6: ["asset depreciation policy"],
  7: ["opening date decision"], 8: ["cohort investment schedule"],
 10: ["manual input versus automatic investment link"],
 11: ["family labor payment status"], 12: ["payment timing"],
 13: ["tax payment timing"], 14: ["loan contract and draw timing"],
 15: ["repayment timing"], 16: ["opening liability ledger"],
 17: ["opening asset ledger"], 21: ["grant policy and receipt timing"],
}

CORRECTIONS = {
  2: "D25 =SUM(D26:D27); E25 =SUM(E26:E27); F25 =SUM(F26:F27); G25 =SUM(G26:G27)",
  3: "F21 ='11. 생산원가계획'!D26; H22 ='11. 생산원가계획'!F27; apply same-year mapping to all affected cells",
  9: "D35 =D31+D32-D33-D34; E35 =E31+E32-E33-E34; copy by year through G35",
 12: "Add the paid first-year expense to cash outflow, or create a payable if unpaid.",
 13: "Connect cash-paid tax to cash, or record a tax payable until payment.",
 14: "Add each later borrowing tranche to debt, interest, repayment and cash schedules.",
 15: "Connect principal to cash outflow and carry closing debt into the next opening year.",
 16: "Carry opening liabilities into planned debt and derive opening equity net of liabilities.",
 18: "D30 ='9 .경비계획'!$C49; E30 ='9 .경비계획'!$C69; then the corresponding later-year fee cells",
 19: "D29 ='9 .경비계획'!$C48; F14 ='11. 생산원가계획'!D29; map remaining years separately",
 20: "C16 =C17+C20+C25+C21+C28+C29+C30; copy the annual pattern through G16",
 22: "X01 H20 =H8; H32 =H20; H44 =H32; H56 =H44, if no later purchase/disposal",
}


def period_for(book, blocks, sheet, cells):
    match = re.match(r"^\s*(\d+)", sheet)
    number = match.group(1) if match else None
    spec = blocks["periods"].get(number) if number else None
    periods = []
    if spec:
        for cell in cells:
            col = re.match(r"[A-Z]+", cell)
            if col and col.group() in spec["columns"]:
                year = spec["first_year"] + spec["columns"].index(col.group())
                periods.append(str(year))
    if number == "10":
        periods += [str(2026 + max(0, (split_addr(a)[1]-8)//12)) for a in cells]
    elif number == "9":
        periods += [str(2026 + max(0, (split_addr(a)[1]-12)//20)) for a in cells]
    elif number == "1":
        label = book.values.get((sheet,"B2")) or ""
        year = re.search(r"20\d{2}", str(label))
        periods.append((year.group() if year else "opening year") + " opening survey")
    elif number == "2":
        periods += [str(2026 + max(0, colnum(re.match(r"[A-Z]+",a).group())-3)) for a in cells]
    elif number == "3":
        periods.append("investment funding block; later borrowing tested in 2027")
    elif number == "4":
        periods.append("2026 closing / 2027 opening loan schedule")
    elif number == "8":
        year = re.search(r"20\d{2}년", str(book.values.get((sheet,"F6"))))
        periods.append((year.group() if year else "unknown year") + " labor plan")
    return sorted(set(periods)) or ["opening or block-specific period"]


IMPACT_CATEGORIES = ("production_cost", "P&L", "balance", "cash", "other")


def impact_category(sheet):
    match = re.match(r"^\s*(\d+)\s*[. ]", sheet)
    return {"11": "production_cost", "12": "P&L", "13": "balance",
            "14": "cash"}.get(match.group(1) if match else None, "other")


def impact_for(deps, sheet, cells):
    """Count all transitive formula dependents of the finding's full cell set."""
    affected = downstream_many(deps, ((sheet, cell) for cell in cells))
    counts = {category: 0 for category in IMPACT_CATEGORIES}
    dependent_cells = []
    for affected_sheet, cell in sorted(affected):
        category = impact_category(affected_sheet)
        counts[category] += 1
        dependent_cells.append({"sheet": affected_sheet, "cell": cell,
                                "category": category})
    return {"dependent_cell_count": len(affected),
            "category_counts": counts,
            "categories": [category for category in IMPACT_CATEGORIES if counts[category]],
            "dependent_cells": dependent_cells}


def opening_liability_probe(book, delta=7):
    """Compare opening-debt flow before/after two formulas change in memory."""
    opening = sheet_number(book, "1")
    balance = sheet_number(book, "13")
    key = (opening, "E58")
    base = book.evaluate(*key)
    if base is None:
        base = 0
    if not isinstance(base, (int, float, bool)):
        raise Unsupported("opening liability probe input is not numeric")
    correction = copy.copy(book)
    correction.formulas = book.formulas.copy()
    correction._ast = {}
    replacements = {"C28": "C22-C25", "D25": "C25+'4. 원리금상환계획'!F8"}
    for cell, formula in replacements.items():
        correction.formulas[balance, cell] = FORMULA(formula, None, None)

    def measure(model, overrides):
        def value(cell):
            return model.evaluate(balance, cell, overrides=overrides)
        return {"opening_equity": value("C28"),
                "opening_imbalance": value("C22")-value("C25")-value("C28"),
                "forecast_debt": value("D25")}

    comparison = {}
    for label, model in (("original",book),("corrected_in_memory",correction)):
        before = measure(model,{})
        after = measure(model,{key:base+delta})
        comparison[label] = {name:numeric_delta(before[name],after[name])
                             for name in before}
    expected = {"original": {"opening_equity":0,"opening_imbalance":-delta,
                             "forecast_debt":0},
                "corrected_in_memory": {"opening_equity":-delta,"opening_imbalance":0,
                                        "forecast_debt":delta}}
    if comparison != expected:
        raise ValueError("XR-16 opening-debt discriminating probe changed")
    return {"input":{"sheet":opening,"cell":"E58","delta":delta,"injection":"input_cell"},
            "outputs":[{"sheet":balance,"cell":"C28","delta":comparison["original"]["opening_equity"]},
                       {"sheet":balance,"cell":"D25","delta":comparison["original"]["forecast_debt"]}],
            "comparison":comparison,"expected_deltas":expected,
            "derived_measure":"opening_imbalance = 13!C22 - 13!C25 - 13!C28",
            "in_memory_formulas":replacements}


def probe_for(book, ref, index):
    if index == 16:
        return opening_liability_probe(book)
    spec = PROBES.get(index)
    if not spec or (index == 4 and ref == "X01"):
        return None
    n, cell, delta, outputs = spec
    if index in (5, 14) and ref == "X02":
        cell = "H26"
    if index == 5 and ref == "X02":
        outputs = [("3", "E26")]
    if index in (11, 12, 13, 15) and ref == "X02":
        outputs = [(role, {"D17":"D18","D18":"D19","D20":"D21"}.get(addr,addr)
                    if role == "14" else addr) for role,addr in outputs]
    input_key = (sheet_number(book, n), cell)
    output_keys = [(sheet_number(book, role), addr) for role, addr in outputs]
    return perturb(book, input_key, delta, output_keys)


def required_fields_check(finding):
    required = ("id", "sheet", "cells", "formula", "cell_formulas", "expected_relation", "period", "meaning",
                "normal_exception", "normal_exception_reason", "exception_decision", "impact", "verdict", "evidence",
                "correction_proposal", "needs_professor_interpretation", "engine_disposition",
                "assumptions", "tested_conditions", "unresolved_dependencies", "open_reason")
    missing = [k for k in required if k not in finding]
    if missing:
        raise ValueError(f"{finding.get('id', '?')}: missing {missing}")
    if finding["verdict"] not in {"defect", "normal_exception", "policy_dependent", "open"}:
        raise ValueError("invalid verdict")
    if finding["engine_disposition"] not in {"replace_in_new_engine", "fill_as_is", "fill_after_correction", "undecided"}:
        raise ValueError("invalid engine disposition")
    if finding["meaning"].get("semantic_authority") not in {"sheet_label", "column_header", "fruit_research", "professor_needed", "inferred"}:
        raise ValueError("meaning authority missing")
    if not isinstance(finding["period"], list) or not finding["period"]:
        raise ValueError("period missing")
    if not isinstance(finding["impact"].get("dependent_cell_count"), int):
        raise ValueError("impact missing")
    impact = finding["impact"]
    counts = impact.get("category_counts")
    if not isinstance(counts, dict) or set(counts) != set(IMPACT_CATEGORIES):
        raise ValueError("impact category counts missing")
    if any(not isinstance(n, int) or n < 0 for n in counts.values()):
        raise ValueError("impact category count invalid")
    if sum(counts.values()) != impact["dependent_cell_count"]:
        raise ValueError("impact count mismatch")
    if len(impact.get("dependent_cells", [])) != impact["dependent_cell_count"]:
        raise ValueError("impact cell list mismatch")
    if not isinstance(finding["tested_conditions"], list) or not isinstance(finding["unresolved_dependencies"], list):
        raise ValueError("conditions/dependencies missing")
    if finding["verdict"] == "open" and not finding["open_reason"]:
        raise ValueError("open finding lacks reason")
    if finding["verdict"] == "defect" and not any(e.get("counterexample") for e in finding["evidence"]):
        raise ValueError("defect lacks counterexample")
    if not finding["cells"] or not finding["meaning"]["text"] or not finding["evidence"]:
        raise ValueError("incomplete A43 finding")
    if [x["cell"] for x in finding["cell_formulas"]] != finding["cells"]:
        raise ValueError("per-cell formula map does not match finding cells")
    for e in finding["evidence"]:
        if e.get("provenance") not in {"workbook_derived", "synthetic_math", "external_source"}:
            raise ValueError("evidence provenance missing")
        if not e.get("rule_id"):
            raise ValueError("evidence rule missing")
    return True


def content_policy_check(doc):
    if doc.get("schema") != "knuaf-workbook-formula-audit/v1":
        raise ValueError("audit schema")
    forbidden = ("cached_value", "input_value", "example_value", "computed_value")
    def visit(value):
        if isinstance(value, dict):
            if set(value) & set(forbidden):
                raise ValueError("source values may not be shipped")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(doc)
    if any("모델농장분석" in item["sheet"] for item in doc["inventory"]):
        raise ValueError("example-only sheet must stay metadata-only")
    for finding in doc["findings"]:
        required_fields_check(finding)
    ids = {f["id"] for f in doc["findings"]}
    if not all(f"XR-{i:02d}" in ids for i in range(1,23)):
        raise ValueError("XR disposition missing")
    return True


def validate_xr02_impact(doc):
    """Local real-file --check gate for the reported subtotal dependents."""
    finding = next(f for f in doc["findings"] if f["id"] == "XR-02")
    impact = finding["impact"]
    if (impact["dependent_cell_count"] <= 0 or
            impact["category_counts"]["production_cost"] <= 0 or
            not any(item["sheet"] == finding["sheet"] and item["cell"] == "D16"
                    for item in impact["dependent_cells"])):
        raise ValueError(doc["ref"] + " XR-02 impact omitted production-cost dependent D16")
    return True


def cell_formula(book, sheet, cell):
    source = book.formulas.get((sheet, cell))
    return {"cell": cell, "formula": source.text if source else None,
            "cell_kind": "formula" if source else
                         "blank" if book.values.get((sheet, cell)) is None else "stored"}


def validate_xr_landmarks(book, ref, candidates):
    """Fail the real-source check if the reviewed XR anchors drift."""
    cash = sheet_number(book, "14")
    cost = sheet_number(book, "11")
    dep = sheet_number(book, "10")
    cash_cell = XR_FILE_CELLS[ref][11][0]
    if book.formulas[cash,cash_cell].text != (
            "'11. 생산원가계획'!C31-'11. 생산원가계획'!C21"):
        raise ValueError(ref + " XR-11 production-payment anchor changed")
    if ref == "X02":
        if (book.formulas[dep,"J8"].text != "'3.투자계획'!E20" or
                book.formulas[dep,"J20"].text != "J8" or
                book.formulas[dep,"K20"].text != "SUM(J20:J20)"):
            raise ValueError("X02 XR-08 biological-asset J/K chain changed")
    elif any(book.formulas.get((dep,a)) or book.values.get((dep,a)) is not None
             for a in XR_FILE_CELLS[ref][8]):
        raise ValueError("X01 XR-08 empty biological-asset block changed")
    if not any(x["rule_id"] == "R1" and x["sheet"] == cost and "D30" in x["cells"]
               for x in candidates):
        raise ValueError(ref + " XR-18 needs generic R1 detection")


def build_result(ref, path, blocks, extract):
    expected_hash = extract["members"][ref]["sha256"]
    actual_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(ref + " source hash mismatch")
    book = Workbook.load(path)
    source_meta = extract["members"][ref]["sheet_meta"]
    if [x["name"] for x in source_meta] != book.sheets:
        raise ValueError("sheet identities differ from pinned extract")
    if [x["name"] for x in source_meta if x["role"] == "example_only"] != ["참고1. 모델농장분석"]:
        raise ValueError("example-only role changed")
    entries, coverage = inventory(book)
    if len(entries) != sum(extract["members"][ref]["formula_counts"].values()):
        raise ValueError("formula inventory differs from pinned extract")
    expected_shared = {"X01": (101,468), "X02": (114,478)}[ref]
    if (coverage["shared_anchor_count"],coverage["shared_follower_count"]) != expected_shared:
        raise ValueError("actual shared formula members changed")
    deps = graph(book, entries)
    candidates = analyze_rules(book, blocks, entries, ref)
    validate_xr_landmarks(book, ref, candidates)
    findings = []
    if set(XR_FILE_CELLS[ref]) != set(XR):
        raise ValueError("variant XR mapping incomplete")
    for index, (role, meaning, relation, rule, proposed) in XR.items():
        sheet = sheet_number(book, role)
        cells = XR_FILE_CELLS[ref][index]
        cell_formulas = [cell_formula(book, sheet, a) for a in cells]
        formula = next((item["formula"] for item in cell_formulas
                        if item["formula"] is not None), None)
        verdict = proposed
        if index == 7 and ref == "X02":
            verdict = "normal_exception"
        if index == 8 and ref == "X01":
            if any(book.values.get((sheet,a)) is not None or (sheet,a) in book.formulas for a in cells):
                raise ValueError("X01 biological-asset block is no longer empty")
            verdict = "normal_exception"
        if index == 22 and ref == "X02":
            verdict = "normal_exception"
        if index in (12,13,14,15):
            verdict = "open"
        if index in (17,21):
            verdict = "defect"
        probe = probe_for(book, ref, index)
        counterexample = verdict == "defect"
        # A defect needs an observed mismatch, not a borrowed conclusion.
        if counterexample:
            if not probe or all(x["delta"] not in (0, "unchanged_non_numeric") for x in probe["outputs"]):
                if index not in (14, 18, 22):
                    raise ValueError(f"XR-{index:02d}: missing independent probe mismatch")
            if index == 14 and not any(x["cell"] == "E34" and x["delta"] == 11 for x in probe["outputs"]):
                raise ValueError("follow-on loan counterexample changed")
            if index == 22 and ref == "X01" and not any(x["cell"] == "H20" and x["delta"] == 0 for x in probe["outputs"]):
                raise ValueError("acquisition carry counterexample changed")
            if index == 18 and not any(x["cell"] == "D30" and x["delta"] == 3 for x in probe["outputs"]):
                raise ValueError("annual fee repetition counterexample changed")
        if index == 22 and ref == "X02":
            if not all(x["delta"] == 0 for x in probe["outputs"] if x["sheet"] == sheet_number(book,"13")):
                raise ValueError("X02 balance carry relation failed")
        if index == 22:
            balance_deltas = [x["delta"] for x in probe["outputs"] if x["sheet"] == sheet_number(book,"13")]
            expected = [0, -17.29, -17.29, -17.29, -17.29] if ref == "X01" else [0] * 5
            if balance_deltas != expected:
                raise ValueError("XR-22 pinned synthetic balance relation changed")
        if index == 7 and ref == "X02" and any(x["rule_id"] == "R8" for x in candidates):
            raise ValueError("X02 opening date no longer aligns")
        observation = REASONS[index]
        if index == 7 and ref == "X02":
            observation = "The opening survey and balance-sheet opening year match in this source."
        if index == 22 and ref == "X02":
            observation = "All four later acquisition cells contain prior-year carry formulas; the specified grant delta preserves the balance difference."
        if index == 1 and ref == "X02":
            observation = "The ratio cell is a stored constant in X02; the first-year meaning is not documented."
        if index == 1 and ref == "X01":
            if book.evaluate(sheet,"C12") != "#DIV/0!":
                raise ValueError("X01 first-year ratio no longer has typed error")
            observation = "The first-year ratio evaluates to a typed divide-by-zero error; the display rule is undefined."
        if index == 8 and ref == "X01":
            observation = "The X01 biological-asset J block is empty; the cohort question applies to X02."
        if index in (12,13,14,15):
            observation += " The probe assumes payment or draw timing that the source does not establish."
        evidence = [{"rule_id": rule, "provenance": "workbook_derived",
                     "observation": observation, "counterexample": counterexample}]
        if probe:
            evidence.append({"rule_id": "PERTURB", "provenance": "workbook_derived",
                             "counterexample": counterexample, "synthetic_probe": probe,
                             "scope": "one-input delta on the pinned workbook formula graph; no Office recalculation"})
        if index == 21:
            variant = blocks["variants"][ref]
            later = perturb(book, (sheet_number(book,"3"),variant["later_grant"]),23,
                            [(sheet_number(book,"14"),"E9"),(sheet_number(book,"13"),"E29")])
            if later["outputs"][0]["delta"] != 0:
                raise ValueError("follow-on grant counterexample changed")
            evidence.append({"rule_id": "PERTURB", "provenance": "workbook_derived",
                             "counterexample": True, "synthetic_probe": later,
                             "scope": "later-year grant receipt relation; accounting classification unresolved"})
        if index == 22:
            evidence.append({"rule_id": "VARIANT", "provenance": "workbook_derived",
                             "observation": "X01 four stored constants; X02 four prior-year formulas. XR-21 is evaluated separately.",
                             "counterexample": False})
        normal = None
        if verdict == "normal_exception":
            normal = ("Source dates align" if index == 7 else
                      "No biological-asset cohort exists in this source" if index == 8 else
                      "The four carry-forward formulas implement this specific no-new-acquisition relation")
        normal_reason = ("Payment, tax, borrowing or repayment timing has not been decided" if index in (12,13,14,15) else
                         "Alternative payment, input or policy interpretation requires authority" if not normal
                         else "The stated structural relation was directly checked")
        finding = {
            "id": f"XR-{index:02d}", "sheet": sheet, "cells": cells,
            "formula": formula, "cell_formulas": cell_formulas, "expected_relation": relation,
            "period": period_for(book, blocks, sheet, cells),
            "meaning": {"text": meaning, "semantic_authority":
                        "sheet_label" if index in (1,7,10,20) else "fruit_research"},
            "normal_exception": normal, "normal_exception_reason": normal_reason,
            "exception_decision": ("confirmed_not_applicable" if index == 8 and ref == "X01" else
                                   "confirmed_relation" if verdict == "normal_exception" else "unresolved"),
            "verdict": verdict, "evidence": evidence,
            "impact": impact_for(deps, sheet, cells),
            "correction_proposal": None if verdict == "normal_exception" else CORRECTIONS.get(index),
            "needs_professor_interpretation": (index in (12,13,14,15,21) or
                                                (bool(DEPENDENCIES.get(index)) and index not in (8,16,17,22))),
            "engine_disposition": ("undecided" if index == 21 else
                                   "fill_after_correction" if verdict == "defect" else
                                   "fill_as_is" if verdict == "normal_exception" else "undecided"),
            "assumptions": ASSUMPTIONS.get(index, []),
            "tested_conditions": (["one synthetic input delta; other workbook inputs held fixed; baseline caches agree"]
                                  if probe else ["pinned formula structure and labels only"]),
            "unresolved_dependencies": DEPENDENCIES.get(index, []),
            "open_reason": ("Payment, tax, loan draw or repayment timing remains unresolved; the synthetic delta is conditional."
                            if index in (12,13,14,15) else REASONS[index] if verdict == "open" else None),
        }
        required_fields_check(finding)
        findings.append(finding)
    # Suppress structural duplicates only when an XR finding covers the same
    # rule, sheet and a reported cell. All other candidates remain open.
    for candidate in candidates:
        if any(candidate["rule_id"] == f["evidence"][0]["rule_id"] and
               candidate["sheet"] == f["sheet"] and
               set(candidate["cells"]) & set(f["cells"]) for f in findings):
            continue
        sheet, cells = candidate["sheet"], candidate["cells"]
        formula = next((book.formulas[sheet,a].text for a in cells
                        if (sheet,a) in book.formulas), None)
        finding = {
            "id": f"W-{1+sum(x['id'].startswith('W-') for x in findings):03d}",
            "sheet": sheet, "cells": cells, "formula": formula,
            "cell_formulas": [cell_formula(book, sheet, a) for a in cells],
            "expected_relation": candidate["expected_relation"],
            "period": period_for(book, blocks, sheet, cells),
            "meaning": {"text": candidate["expected_relation"], "semantic_authority": "inferred"},
            "normal_exception": None,
            "normal_exception_reason": candidate["exception_check"],
            "exception_decision": "unresolved",
            "verdict": "open",
            "evidence": [{"rule_id": candidate["rule_id"], "provenance": "workbook_derived",
                          "observation": "Structural candidate; semantic exception has not been ruled out.",
                          "counterexample": False}],
            "impact": impact_for(deps, sheet, cells),
            "correction_proposal": None,
            "needs_professor_interpretation": True,
            "engine_disposition": "undecided",
            "assumptions": [], "tested_conditions": ["structural scan only"],
            "unresolved_dependencies": [candidate["exception_check"]],
            "open_reason": "No counterexample or authoritative normal-exception rule yet.",
        }
        required_fields_check(finding)
        findings.append(finding)
    research = {
      "workbook_rederived": ["M01/XR-02", "M02/XR-03", "M03/XR-05", "M04/XR-09",
                             "M05/XR-04", "M06/XR-11", "M09/XR-06", "XR-12–22"],
      "synthetic_math_only": ["M07", "M08", "M10", "M12"],
      "external_source_manual": ["M11/S04 PDF"],
      "difference_from_fruit": ["No contradictory workbook path found in the tested cases. XR-17 and XR-21 receive explicit conditional-defect classifications here; their policy dependencies remain open."],
      "limit": "Synthetic-math and external-source examples are provenance references, not workbook rederivations."
    }
    doc = {"schema": "knuaf-workbook-formula-audit/v1", "ref": ref,
           "source_sha256": actual_hash, "inventory": entries, "coverage": coverage,
           "findings": findings, "research_reconciliation": research,
           "region_map": {"file": "blocks.json", "variant": ref},
           "sheet_roles": {x["name"]:x["role"] for x in source_meta},
           "model_farm_sheet": {"role": "example_only", "metadata_only": True}}
    content_policy_check(doc)
    return doc


def json_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def audit_markdown(x01, x02):
    from collections import Counter
    lines = ["# X01·X02 formula audit — A43 / XR-01–22", "",
             "Sources are SHA-256 pinned. The XLSX files are read only; no corrected workbook is shipped.",
             "Formula caches agree with this evaluator at the fixed input state. This does not certify",
             "perturbation behavior or constitute Office recalculation.", "",
             "## Coverage", "",
             "| Source | Structural formulas | Evaluator supported | Cache agreeing | Unsupported syntax | Dynamic refs |",
             "|---|---:|---:|---:|---:|---:|"]
    for doc in (x01,x02):
        c = doc["coverage"]
        lines.append(f"| {doc['ref']} `{doc['source_sha256']}` | {c['structural_formula_count']} | "
                     f"{c['evaluator_supported_count']} | {c['cache_agreement_count']} | "
                     f"{len(c['unsupported_syntax'])} | {len(c['dynamic_references'])} |")
    lines += ["", "The evaluator keeps an empty reference blank until consumed: arithmetic and",
              "comparison coerce it to zero, while SUM/AVERAGE ignore it. For A1 empty and",
              "A2=10, both `AVERAGE(A1,A2)` and `AVERAGE(A1:A2)` are 10;",
              "`AVERAGE(A1+0,A2)` is 5. Microsoft documents that empty cells in",
              "[AVERAGE cell-reference or range arguments](https://support.microsoft.com/en-us/excel/functions/average-function)",
              "are ignored. [SUM](https://support.microsoft.com/en-us/excel/functions/sum-function)",
              "accepts either individual cell references or ranges; the independent fixture",
              "checks both forms.",
              "typed errors propagate, `%` divides by 100, and PMT uses Excel argument order.",
              f"Cache tolerance: absolute {TOL_ABS:g}, relative {TOL_REL:g}.", "",
              "## Verdict counts", "",
              "| Source | defect | normal_exception | policy_dependent | open |",
              "|---|---:|---:|---:|---:|"]
    for doc in (x01,x02):
        count = Counter(f["verdict"] for f in doc["findings"])
        lines.append("| {} | {} | {} | {} | {} |".format(
            doc["ref"], *(count[x] for x in ("defect","normal_exception","policy_dependent","open"))))
    lines += ["", "## XR dispositions", "",
              "The provenance of every XR row is `workbook_derived`; synthetic deltas are",
              "recorded in the JSON evidence. A defect means its stated assumptions admit a",
              "counterexample. An open item retains its reason and unresolved dependencies.", "",
              "| ID | X01 | X02 | One-line reason |",
              "|---|---|---|---|"]
    for i in range(1,23):
        f1, f2 = (next(f for f in doc["findings"] if f["id"] == f"XR-{i:02d}")
                  for doc in (x01,x02))
        reason = REASONS[i].replace("|", "/")
        if i == 7:
            reason = "X01 opening survey year differs from balance opening; X02 aligns."
        if i == 1:
            reason = "X01 first-year ratio yields a divide-by-zero error; X02 stores a constant; intended display remains unresolved."
        if i == 22:
            reason = "X01 four stored prices versus X02 four carry formulas; +19 grant probe changes X01 balance difference by [0, −17.29 × 4], X02 by zero."
        if i == 8:
            reason = "X01 has no biological-asset cohort in its J block; X02 J/K carry formulas omit later cohorts."
        lines.append(f"| XR-{i:02d} | {f1['verdict']} | {f2['verdict']} | {reason} |")
    lines += ["", "## Finding impact", "",
              "Counts are strict transitive formula dependents of every cell in each finding;",
              "the finding cells themselves are excluded. Per-category counts and exact",
              "dependent coordinates are in the JSON. W IDs are local to each source.", "",
              "| ID | X01 dependents | X02 dependents |", "|---|---:|---:|"]
    by_ref = [{f["id"]:f["impact"]["dependent_cell_count"] for f in doc["findings"]}
              for doc in (x01,x02)]
    ids = [f"XR-{i:02d}" for i in range(1,23)]
    ids += [f"W-{i:03d}" for i in range(1,1+max(
        sum(f["id"].startswith("W-") for f in doc["findings"]) for doc in (x01,x02)))]
    for identifier in ids:
        left = by_ref[0].get(identifier, "—")
        right = by_ref[1].get(identifier, "—")
        lines.append(f"| {identifier} | {left} | {right} |")
    lines += ["", "## Opening-liability discrimination (XR-16)", "",
              "For a synthetic +7 at opening liability `1!E58`, the original formulas",
              "yield opening equity Δ0, opening imbalance Δ−7 and forecast debt Δ0.",
              "Replacing only `13!C28` with `C22-C25` and `13!D25` with",
              "`C25+'4. 원리금상환계획'!F8` in memory yields Δ−7, Δ0 and Δ+7",
              "respectively. The opening imbalance is `13!C22-C25-C28`.",
              "These are deltas, not source values or Office recalculation."]
    lines += ["", "## Rule scan and evidence limits", "",
              "R1–R8 run over committed per-sheet blocks. Extra `W-nnn` structural candidates",
              "remain open until fixed-reference, optional-blank, payment-timing and label",
              "exceptions are checked; no candidate becomes a defect solely from a repeated",
              "formula, blank, constant, cached zero, or balanced difference.", "",
              "M01–M06 and M09 correspond to the workbook XR relations here. Fruit M07, M08,",
              "M10 and M12 are `synthetic_math`; M11 is `external_source` (S04 PDF, manual).",
              "They are not represented as workbook-derived findings. No contradictory",
              "workbook path was found in the tested cases. XR-17 and XR-21 are conditional",
              "defects here when inherited assets",
              "and grants are actually carried/received; the related accounting policy remains",
              "unresolved. XR-21 is independent of XR-22's X02 balance result.", "",
              "The model-farm example sheet contributes metadata only. Shipped results include",
              "formulas, coordinates and synthetic deltas; no example inputs or cache values.", ""]
    return ("\n".join(lines)).encode("utf-8")


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    output_dir = root / "references/common-workbooks/formula-audit"
    blocks = json.loads((output_dir / "blocks.json").read_text(encoding="utf-8"))
    extract = json.loads((root / "references/common-workbooks/kang-finance-workbook.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("inventory", "audit"))
    parser.add_argument("--ref", choices=("X01", "X02"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--x01", type=Path)
    parser.add_argument("--x02", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        if not args.x01 or not args.x02:
            parser.error("--check requires --x01 and --x02")
        results = []
        for ref, source in (("X01",args.x01),("X02",args.x02)):
            result = build_result(ref, source, blocks, extract)
            validate_xr02_impact(result)
            results.append(result)
            actual = json_bytes(result)
            target = output_dir / (ref.lower() + ".json")
            if actual != target.read_bytes():
                raise SystemExit(ref + " audit differs from committed result")
            print(ref + " byte-identical match")
        if audit_markdown(*results) != (output_dir / "AUDIT.md").read_bytes():
            raise SystemExit("AUDIT.md differs from derived report")
        print("AUDIT.md byte-identical match")
        return 0
    if not args.ref or not args.source:
        parser.error("--ref and --source required")
    if args.command == "inventory":
        source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
        if source_hash != extract["members"][args.ref]["sha256"]:
            raise SystemExit("source hash mismatch")
        entries, coverage = inventory(Workbook.load(args.source), include_cache_values=True)
        result = {"ref": args.ref, "source_sha256": source_hash,
                  "inventory": entries, "coverage": coverage}
    elif args.command == "audit":
        result = build_result(args.ref, args.source, blocks, extract)
    else:
        parser.error("choose inventory, audit, or --check")
    payload = json_bytes(result)
    if args.out:
        args.out.write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
