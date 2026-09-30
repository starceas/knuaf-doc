#!/usr/bin/env python3
"""Rebuild the public finance result registry from the source and map.

Adds teacher_example_coords: coordinates of transform entries flagged
`teacher_example` (cells that held H01 teaching values).  The list ships
inside finance-verify-classes.json so verify can fail closed when such a
cell survives unmapped with its original value.

Rebuilds finance-verify-classes.json's registry from the private H01
workbook XML, the public transform map and the public param registry ONLY.
It never opens a recalculated (native) workbook and never reads a PDF:
the sealed lane inputs are

  --source   H01 copy (SHA-256 must equal the public SOURCE_SHA)
  --map      finance-transform.json   (public contract)
  --params   finance-params.json      (public contract)

Classification rules (static, no cache fitting):
  * carrier formulas can yield a conditional result: a literal NA() or an
    "" branch, or a pure single-cell reference that mirrors its target.
  * a row is conditional_unused_na when a carrier (or an NA-propagating
    dependency) can reach at least one optional input parameter through
    the formula dependency graph.  The licence key lists the slot/item
    group for each reachable optional param (or the param itself if it has
    no group).  Mutually exclusive fields inside an active group, such as
    hourly vs daily wage, must not make the group appear inactive.  An
    unconditional #N/A without such a dependency is a
    defect and stays unclassified by licence.
  * active_kind is the static result type the cell must satisfy while the
    licence condition is false, inferred from the formula grammar
    (""-branch, string concat/TEXT, pure-ref target type, IF branch types).

Usage:
  gg_hort_registry.py [--write]   regenerate finance-verify-classes.json
  gg_hort_registry.py --check     exit 1 when the published file differs
  gg_hort_registry.py --diff OLD  print the row-level diff against a snapshot
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
import gg_hort_workbook as h
from gg_hort_formula_eval import Parser, FormulaError, normalize_ref

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REF = ROOT / "skills/knuaf-doc/references/hort-env-systems"
DEFAULT_SOURCE = ROOT / ".hw-work/fin/lanes/F1-CELLMAP/private/H-X1.xlsx"

# Financial output anchors for DESIGN-FIN §5.  Dependency closure is
# derived afresh from the source and transform formulas, without a prior
# reachability study or native cache as an input.
CORE_OUTPUT_ROWS = {
    "14. 생산원가계획": {25: "CDEFG"},
    "15. 손익계획": {6: "CDEFG", 19: "CDEFG", 35: "CDEFG"},
    "17. 추정대차대조표": {30: "CDEFGH", 46: "CDEFGH"},
    "18. 현금흐름계획": {27: "DEFGH"},
}

TRUE = {"all": []}

# Function heads whose result is statically text vs logical.
_STRING_FUNCS = {"TEXT", "T", "CONCAT", "CONCATENATE", "TEXTJOIN", "LEFT",
                 "RIGHT", "MID", "TRIM", "UPPER", "LOWER", "PROPER", "CHAR",
                 "REPT", "SUBSTITUTE", "REPLACE", "FIXED", "VALUE_OR_TEXT"}
_BOOL_FUNCS = {"AND", "OR", "NOT", "ISNUMBER", "ISBLANK", "ISTEXT", "ISERROR",
               "ISERR", "ISEVEN", "ISODD", "ISLOGICAL", "ISNONTEXT", "ISREF",
               "TRUE", "FALSE", "EXACT", "ISFORMULA"}

RETURNS_EMPTY = re.compile(r",\s*\"\"\s*[,)]|^=\s*\"\"\s*$")
PURE_REF = re.compile(
    r"^=\s*(?:'[^']+'!|[0-9A-Za-z_.\uac00-\ud7a3 ]+!)?"
    r"\$?[A-Z]{1,3}\$?[0-9]+\s*$")
REF_TOKEN = re.compile(
    r"(?:'([^']+)'!|(\b[0-9\uac00-\ud7a3A-Za-z_\. ]+?)!)?"
    r"\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?")


def colnum(col):
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def colname(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def group_of(key):
    """Slot/item group prefix for a param key (SystemExit when unpatterned)."""
    m = re.match(
        r"(invest\.slot\d+|loan\.slot\d+|asset\.opening_equipment\.slot\d+|"
        r"cost\.material\.y\d\.item\d+|cost\.overhead\.y\d\.slot\d+|"
        r"cost\.labor\.y\d\.slot\d+|cost\.salary\.y\d|sales\.y\d\.m\d+|sales\.y\d)\.",
        key)
    if m:
        return m.group(1)
    m = re.match(r"opening\.equipment_(slot\d+)", key)
    if m:
        return "asset.opening_equipment." + m.group(1)
    ym = re.match(r"(.+?\.y\d)\.", key)
    if ym:
        return ym.group(1)
    raise SystemExit(f"no group for {key}")


def group_or_self(key):
    try:
        return group_of(key)
    except SystemExit:
        return key


def refs_of(text, sheet):
    out = []
    for m in REF_TOKEN.finditer(text or ""):
        sh = m.group(1) or m.group(2) or sheet
        c1, r1 = m.group(3), int(m.group(4))
        if m.group(5):
            c2, r2 = m.group(5), int(m.group(6))
            for cc in range(colnum(c1), colnum(c2) + 1):
                for rr in range(min(r1, r2), max(r1, r2) + 1):
                    out.append((sh, f"{colname(cc)}{rr}"))
        else:
            out.append((sh, f"{c1}{r1}"))
    return out


def load_source(path):
    """Read the private H01 copy: states, formulas (shared expanded), print areas."""
    if h.file_digest(path) != h.SOURCE_SHA:
        raise SystemExit("source hash mismatch: generator requires the H01 copy")
    z = zipfile.ZipFile(path)
    sheets = h.workbook_sheets(z)
    shared = h.load_shared(z)
    cells, formulas = {}, {}
    print_areas = {}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    defined = wb.find(f"{{{M}}}definedNames")
    for name, spath in sheets:
        dom, cellmap, nonanchors, arrays = h._sheet_structure(z.read(spath), spath)
        h._expand_shared(dom)
        for ref, c in cellmap.items():
            cells[(name, ref)] = h._cell_state(c, ref in arrays)
            f = h._direct(c, "f")
            if f is not None:
                formulas[(name, ref)] = h._text(f)
    for i, (name, spath) in enumerate(sheets):
        if defined is None:
            continue
        for dn in defined:
            if dn.get("name") == "_xlnm.Print_Area" and dn.get("localSheetId") == str(i):
                for rng in (dn.text or "").split(","):
                    rng = rng.split("!")[-1].replace("'", "").replace("$", "")
                    print_areas.setdefault(name, []).append(rng)
    return sheets, cells, formulas, shared, print_areas


def in_ranges(ref, ranges):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col, row = colnum(m.group(1)), int(m.group(2))
    for rng in ranges:
        parts = rng.split(":")
        a, b = (parts[0], parts[0]) if len(parts) == 1 else (parts[0], parts[1])
        am, bm = re.match(r"([A-Z]+)(\d+)", a), re.match(r"([A-Z]+)(\d+)", b)
        if colnum(am.group(1)) <= col <= colnum(bm.group(1)) and +                int(am.group(2)) <= row <= int(bm.group(2)):
            return True
    return False


def split_top_args(body):
    """Split a function-call argument list at top-level commas."""
    args, depth, in_str, cur = [], 0, False, ""
    for ch in body:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if ch == "," and depth == 0 and not in_str:
            args.append(cur)
            cur = ""
        else:
            cur += ch
    args.append(cur)
    return args


def outer_if(expr):
    """Return the IF branch expressions when expr is exactly one IF call."""
    m = re.match(r"^IF\s*\(", expr.strip(), re.I)
    if not m:
        return None
    depth, in_str = 0, False
    inner = expr.strip()[m.end():]
    for i, ch in enumerate(inner):
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    if inner[i + 1:].strip():
                        return None
                    return split_top_args(inner[:i])
                depth -= 1
    return None


class RegistryBuilder:
    def __init__(self, source, tr, params):
        self.sheets, self.cells, self.formulas, self.shared, self.print_areas = \
            load_source(source)
        self.entries = {(e["sheet"], e["cell"]): e for e in tr["entries"]}
        self.params = params
        self.deps = {}
        for e in tr["entries"]:
            coord = (e["sheet"], e["cell"])
            if e.get("after_formula"):
                self.deps[coord] = refs_of(e["after_formula"], e["sheet"])
        for coord, text in self.formulas.items():
            self.deps.setdefault(coord, refs_of(text, coord[0]))
        # Transform replaces the content of input/clear/label cells, so any
        # source formula there is gone from the output workbook.  Keeping it
        # would let the dep graph propagate conditionality through cells that
        # no longer carry a formula.
        for e in tr["entries"]:
            if e.get("after_formula"):
                continue
            coord = (e["sheet"], e["cell"])
            if e.get("label_key") == "source_prefix_plus_area_m2":
                target = (e.get("label_value_cell") or "").rsplit("!", 1)
                self.deps[coord] = [tuple(target)] if len(target) == 2 else []
            elif e["action"] in ("input_param", "clear") or \
                    e.get("after_value") is not None or e.get("label_key"):
                self.deps.pop(coord, None)
        # Optional input cells: params whose required_if is not unconditional.
        self.optional_param_cell = {}
        for key, spec in params.items():
            cell = spec.get("canonical_cell")
            if not cell:
                continue
            req = spec.get("required_if")
            if req is None or req == TRUE:
                continue
            sheet, ref = cell.rsplit("!", 1)
            self.optional_param_cell[(sheet, ref)] = key
        # Cells a param actually feeds (canonical cell may be referenced
        # through mirrors, so the dep graph already covers indirection).
        self.start_year_cells = set()
        for key, spec in params.items():
            if key == "plan.start_year" and spec.get("canonical_cell"):
                self.start_year_cells.add(tuple(spec["canonical_cell"].rsplit("!", 1)))
        self._formula_cache = {}
        self._produces_cache = {}
        self._can_na = {}
        self._can_empty = {}
        self._licensed = {}
        self._unit_cache = {}
        self._param_units = {
            tuple(spec["canonical_cell"].rsplit("!", 1)): spec["unit_code"]
            for spec in params.values() if spec.get("canonical_cell")}

    def formula_of(self, coord):
        if coord in self._formula_cache:
            return self._formula_cache[coord]
        e = self.entries.get(coord)
        f = None
        if e is not None:
            if e.get("after_formula"):
                f = e["after_formula"]
            elif e.get("label_key") == "source_prefix_plus_area_m2":
                f = '="x"&TEXT(A1,"0")'   # concat shape: produces string
            elif e["action"] in ("input_param", "clear") or \
                    e.get("after_value") is not None or e.get("label_key"):
                f = ""   # transform writes a value/blank: source formula gone
        if f is None:
            f = self.formulas.get(coord) or ""
        self._formula_cache[coord] = f
        return f

    def produces(self, coord, seen=None):
        """Static result-kind set the expression at coord can yield."""
        if coord in self._produces_cache:
            return self._produces_cache[coord]
        if seen is None:
            seen = set()
        if coord in seen:
            return {"number"}
        seen = seen | {coord}
        f = self.formula_of(coord).strip()
        out = self._expr_kinds(f, coord, seen) if f else self._nonformula_kinds(coord)
        self._produces_cache[coord] = out
        return out

    def _nonformula_kinds(self, coord):
        e = self.entries.get(coord)
        if e is not None and e.get("action") == "input_param":
            spec = self.params.get(e.get("param_key"), {})
            return {"string"} if spec.get("data_type") == "string" else {"number"}
        if e is not None and e.get("action") == "label_template":
            return {"string"}
        state = self.cells.get(coord, "absent")
        if state in ("shared_string", "inline_string"):
            return {"string"}
        if state in ("number",):
            return {"number"}
        return {"blank"}

    def _expr_kinds(self, expr, coord, seen):
        expr = expr.strip()
        if expr.startswith("="):
            expr = expr[1:].strip()
        if not expr:
            return {"number"}
        if re.fullmatch(r'\s*""\s*', expr):
            return {"empty"}
        branches = outer_if(expr)
        if branches is not None:
            kinds = set()
            for branch in branches[1:]:
                kinds |= self._expr_kinds(branch, coord, seen)
            if len(branches) < 3:
                kinds.add("bool")
            return kinds or {"number"}
        if PURE_REF.match("=" + expr):
            refs = refs_of(expr, coord[0])
            if len(refs) == 1:
                return self.produces(refs[0], seen)
        if re.fullmatch(r"NA\s*\(\s*\)", expr):
            return {"na"}
        head = re.match(r"^([A-Za-z][A-Za-z0-9.]*)\s*\(", expr)
        if head:
            fname = head.group(1).upper()
            if fname == "IFERROR":
                inner = expr[head.end():]
                depth = inner.rfind(")")
                args = split_top_args(inner[:depth] if depth >= 0 else inner)
                kinds = set()
                for arg in args:
                    kinds |= self._expr_kinds(arg, coord, seen)
                kinds.discard("na")
                return kinds or {"number"}
            if fname in _STRING_FUNCS:
                return {"string"}
            if fname in _BOOL_FUNCS:
                return {"bool"}
        # A concatenation at top level (outside calls/strings) yields text.
        depth, in_str = 0, False
        for ch in expr:
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "&" and depth == 0:
                    return {"string"}
        if expr.startswith('"'):
            return {"string"}
        return {"number"}

    def _fixpoint(self, seeds, propagate):
        flags = dict(seeds)
        changed = True
        while changed:
            changed = False
            for coord in self.deps:
                if flags.get(coord):
                    continue
                if propagate(coord, flags):
                    flags[coord] = True
                    changed = True
        return flags

    def can_na(self, coord):
        if not self._can_na:
            seeds = {c: "NA()" in self.formula_of(c) or "na" in self.produces(c)
                     for c in self.deps}
            self._can_na = self._fixpoint(
                seeds, lambda c, fl: any(fl.get(d) for d in self.deps.get(c, ())))
        return self._can_na.get(coord, False)

    def can_empty(self, coord):
        if not self._can_empty:
            seeds = {c: bool(RETURNS_EMPTY.search(self.formula_of(c)))
                          or "empty" in self.produces(c)
                     for c in self.deps}
            def prop(c, fl):
                f = self.formula_of(c)
                if not PURE_REF.match(f):
                    return "empty" in self.produces(c)
                return any(fl.get(d) for d in self.deps.get(c, ()))
            self._can_empty = self._fixpoint(seeds, prop)
        return self._can_empty.get(coord, False)

    def _reach_optional(self, coord):
        """Optional param keys reachable through the formula dep graph."""
        found, seen, stack = set(), set(), [coord]
        while stack:
            cur = stack.pop()
            for dep in self.deps.get(cur, ()):
                if dep in self.optional_param_cell:
                    found.add(self.optional_param_cell[dep])
                elif dep not in seen and dep in self.deps:
                    seen.add(dep)
                    stack.append(dep)
        return found

    def licensed_groups(self, coord):
        """Condition licence parts: group_or_self(K) per reachable param."""
        if coord in self._licensed:
            return self._licensed[coord]
        f = self.formula_of(coord)
        carrier = ("NA()" in f or self.can_na(coord) or self.can_empty(coord)
                   or bool(PURE_REF.match(f)))
        parts = set()
        if carrier:
            for key in self._reach_optional(coord):
                parts.add(group_or_self(key))
        elif any(self.can_na(d) for d in self.deps.get(coord, ())):
            for key in self._reach_optional(coord):
                parts.add(group_or_self(key))
        parts = {p for p in parts if h._group_members(self.params, p)}
        self._licensed[coord] = parts
        return parts

    def base_kind(self, coord):
        produces = self.produces(coord)
        produces -= {"na", "bool", "blank"}
        if produces == {"empty"}:
            return "normal_empty_string"
        if produces == {"string"} or produces == {"string", "empty"}:
            return "display_string"
        if produces == {"number"} or produces == {"number", "empty"}:
            return "numeric_required"
        return "numeric_required"

    def active_tokens(self, coord):
        """'|'-joined union of cached-value shapes the formula can yield.

        Produce kinds map to registry tokens 1:1 (number->number,
        string->string, empty/blank->empty), so a formula that can honestly
        render a label next to a SUM keeps 'empty|number|string' instead of
        being squeezed into one sealed kind.  A pure #N/A/bool formula has
        no honest active shape: refuse to emit a licence (fail closed).
        """
        produces = self.produces(coord) - {"na", "bool"}
        tokens = {"empty" if p in ("empty", "blank") else p
                  for p in produces}
        if not tokens:
            raise SystemExit(f"no active kind derivable at {coord[0]}!{coord[1]}")
        return "|".join(sorted(tokens))

    def inferred_unit(self, coord, stack=None):
        if coord in self._param_units:
            return self._param_units[coord]
        if coord in self._unit_cache:
            return self._unit_cache[coord]
        stack = stack or set()
        if coord in stack:
            return None
        formula = self.formula_of(coord)
        if not formula:
            return ("TEXT" if self.cells.get(coord) in ("shared_string", "inline_string")
                    else None)
        try:
            ast = Parser(formula).parse()
            unit = self._expr_unit(ast, coord[0], stack | {coord})
        except FormulaError:
            unit = None
        self._unit_cache[coord] = unit
        return unit

    def _expr_unit(self, node, sheet, stack):
        kind = node[0]
        if kind == "number":
            return None
        if kind == "string":
            return "TEXT"
        if kind == "ref":
            return self.inferred_unit(normalize_ref(node[1], sheet), stack)
        if kind == "name":
            return None
        if kind == "unary":
            return self._expr_unit(node[2], sheet, stack)
        if kind == "binary":
            op = node[1]
            a = self._expr_unit(node[2], sheet, stack)
            b = self._expr_unit(node[3], sheet, stack)
            if op in ("+", "-"):
                return a if a == b or b is None else b if a is None else None
            if op == ":":
                return a if a == b or b is None else b if a is None else None
            if op == "&":
                return "TEXT"
            if op in ("=", "<>", "<", ">", "<=", ">="):
                return None
            if op == "*":
                if (a == "KRW_1000" and node[3] == ("number", 1000.0)) or \
                        (b == "KRW_1000" and node[2] == ("number", 1000.0)):
                    return "KRW"
                if a in (None, "RATIO", "COUNT"):
                    return b
                if b in (None, "RATIO", "COUNT"):
                    return a
                pair = {a, b}
                if pair in ({"SALES_UNIT", "KRW_PER_SALES_UNIT"},
                            {"ITEM_UNIT", "KRW_PER_ITEM_UNIT"},
                            {"M2", "KRW_PER_M2"},
                            {"HOUR", "KRW_PER_HOUR"},
                            {"HOUR", "KRW_PER_DAY"}):
                    return "KRW"
                return None
            if op == "/":
                if b is None or b in ("RATIO", "COUNT"):
                    if a == "KRW" and node[3] == ("number", 1000.0):
                        return "KRW_1000"
                    return a
                if a == b:
                    return "RATIO"
                if a == "KRW" and b == "SALES_UNIT":
                    return "KRW_PER_SALES_UNIT"
                return None
            return None
        if kind == "call":
            fn, args = node[1], node[2]
            if fn == "IF":
                branches = [self._expr_unit(a, sheet, stack) for a in args[1:]]
                numeric = [u for u in branches if u not in (None, "TEXT")]
                return numeric[0] if numeric and len(set(numeric)) == 1 else None
            if fn in ("IFERROR", "SUM", "AVERAGE", "MAX", "MIN"):
                units = [self._expr_unit(a, sheet, stack) for a in args]
                units = [u for u in units if u is not None]
                return units[0] if units and len(set(units)) == 1 else None
            if fn == "PMT" and len(args) >= 3:
                return self._expr_unit(args[2], sheet, stack)
            if fn == "SUMIF" and len(args) >= 3:
                return self._expr_unit(args[2], sheet, stack)
            if fn == "SUMIFS" and args:
                return self._expr_unit(args[0], sheet, stack)
            if fn in ("COUNT", "COUNTIF", "COUNTIFS", "LEN", "DAY"):
                return "COUNT"
            if fn == "YEAR":
                return "YEAR"
            if fn == "MONTH":
                return "MONTH"
            if fn == "DATE":
                return "DATE"
            if fn in ("TEXT", "RIGHT"):
                return "TEXT"
        return None

    def unit_for(self, coord, action, state, kind):
        if coord[0] == "16. 수익성분석" and coord[1] in {
                f"{col}27" for col in "GHIJK"}:
            return "RATIO"
        if coord[0] == "4.투자계획" and coord[1] in {"H39", "H47"}:
            return "KRW_1000"
        match = re.fullmatch(r"([C-O])(7|20|23|29|32|38|41|47|50|56|59)", coord[1])
        if coord[0] == " 7. 판매계획" and match:
            return "SALES_UNIT"
        inferred = self.inferred_unit(coord)
        if inferred == "TEXT" and kind == "conditional_unused_na" \
                and PURE_REF.match(self.formula_of(coord)):
            # A direct Excel reference to an undeclared text input can cache
            # numeric 0.  That zero is a dimensionless placeholder, while a
            # populated source remains checked as a string by result_kind.
            return "SCALAR"
        if inferred in {"KRW", None}:
            if action == "label_template" or state in ("shared_string", "inline_string"):
                return "TEXT"
            if coord[0].startswith(("5.", "17.", "18.", "15.", "14.", "12 .", "13.", "16.")):
                return "KRW_1000"
            return "SCALAR"
        return inferred

    def build(self):
        cells, formulas, entries = self.cells, self.formulas, self.entries
        # Include direct references to blank original cells.  Range members
        # are excluded: a SUM range with a few blanks does not make each
        # blank coordinate an independently classified result cell.  Mapped
        # blank targets are already part of the universe below.
        blank_targets = set()
        for (sheet, _), formula in formulas.items():
            for match in REF_TOKEN.finditer(formula):
                if match.group(5):
                    continue
                target = (match.group(1) or match.group(2) or sheet,
                          match.group(3) + match.group(4))
                if target not in entries and cells.get(target, "absent") in ("absent", "blank_node"):
                    blank_targets.add(target)
        core_reachable = set()
        for sheet, rows in CORE_OUTPUT_ROWS.items():
            for row, cols in rows.items():
                for col in cols:
                    core_reachable.add((sheet, f"{col}{row}"))
        for output in tuple(core_reachable):
            stack = [output]
            while stack:
                cur = stack.pop()
                for dep in self.deps.get(cur, []):
                    if dep not in core_reachable:
                        core_reachable.add(dep)
                        stack.append(dep)

        universe = set(entries)
        universe |= {(sn, ref) for (sn, ref), st in cells.items()
                     if st.startswith("formula") and (sn, ref) not in entries}
        for (sn, ref), st in cells.items():
            if st == "absent" or (sn, ref) in universe:
                continue
            if st != "blank_node" and in_ranges(ref, self.print_areas.get(sn, [])):
                universe.add((sn, ref))
        universe |= blank_targets

        registry = {}
        for coord in sorted(universe, key=lambda c: (
                c[0], int(re.match(r"[A-Z]+(\d+)", c[1]).group(1)),
                colnum(re.match(r"([A-Z]+)", c[1]).group(1)))):
            sn, ref = coord
            e = entries.get(coord)
            state = cells.get(coord, "absent")
            action = e["action"] if e else "retain"
            printed = in_ranges(ref, self.print_areas.get(sn, []))
            is_formula = bool(self.formula_of(coord))
            cond_key = None
            active_kind = None
            if action == "input_param":
                kind = "intentional_blank_nonformula"
                expected = "input_value"
                spec = self.params.get(e.get("param_key"), {})
                req = spec.get("required_if")
                if e.get("param_key") and req is not None and req != TRUE:
                    cond_key = "param:" + e["param_key"]
            elif action == "clear":
                kind = "intentional_blank_nonformula"
                expected = "cleared_blank"
            else:
                groups = self.licensed_groups(coord) if is_formula else set()
                if groups:
                    kind = "conditional_unused_na"
                    cond_key = "any_inactive:" + ",".join(sorted(groups))
                    active_kind = self.active_tokens(coord)
                    expected = "formula"
                else:
                    kind = self.base_kind(coord) if is_formula else (
                        "display_string" if state in ("shared_string", "inline_string")
                        else "intentional_blank_nonformula"
                        if coord in blank_targets or state in ("blank_node", "absent")
                        else "numeric_required")
                    if action == "label_template" and not is_formula:
                        kind = "display_string"
                    expected = ("formula" if is_formula else
                                "retained_value" if state in ("shared_string", "inline_string", "number")
                                else "retained_blank")
                    if action == "label_template" and not is_formula:
                        expected = "label"
            unit = self.unit_for(coord, action, state, kind)
            final_kind = active_kind or kind
            if kind != "conditional_unused_na":
                if final_kind == "display_string" and unit == "UNKNOWN":
                    unit = "TEXT"
                if final_kind == "numeric_required" and unit != "YEAR":
                    deps = self.deps.get(coord, [])
                    if deps and set(deps) <= self.start_year_cells:
                        unit = "YEAR"
            row = {"sheet": sn, "cell": ref, "action": action, "result_kind": kind,
                   "unit": unit, "printed": bool(printed),
                   "core_reachable": coord in core_reachable,
                   "expected_state": expected}
            if cond_key:
                row["condition_key_if_conditional"] = cond_key
            if active_kind:
                row["active_kind"] = active_kind
            registry[f"{sn}!{ref}"] = row
        return registry


def diff_rows(old_rows, new_rows):
    old = {(r["sheet"], r["cell"]): r for r in old_rows}
    new = {(r["sheet"], r["cell"]): r for r in new_rows}
    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    changed = [(c, old[c], new[c]) for c in sorted(set(old) & set(new)) if old[c] != new[c]]
    return only_old, only_new, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--map", default=str(REF / "finance-transform.json"))
    ap.add_argument("--params", default=str(REF / "finance-params.json"))
    ap.add_argument("--out", default=str(REF / "finance-verify-classes.json"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--diff", default=None, help="compare against a registry snapshot JSON")
    args = ap.parse_args()

    tr = json.loads(Path(args.map).read_text(encoding="utf-8"))
    params_doc = json.loads(Path(args.params).read_text(encoding="utf-8"))
    builder = RegistryBuilder(args.source, tr, params_doc["parameters"])
    registry = builder.build()
    rows = [registry[k] for k in sorted(registry)]
    print("registry rows:", len(rows))

    if args.diff:
        old = json.loads(Path(args.diff).read_text(encoding="utf-8"))["registry"]
        only_old, only_new, changed = diff_rows(old, rows)
        print("only-in-snapshot:", len(only_old), "only-in-generated:", len(only_new))
        print("changed rows:", len(changed))
        for coord, o, n in changed:
            diffs = {k: (o.get(k), n.get(k)) for k in set(o) | set(n) if o.get(k) != n.get(k)}
            print(" ", coord, diffs)

    out_path = Path(args.out)
    if args.check:
        current = json.loads(out_path.read_text(encoding="utf-8"))
        same = (current.get("registry") == rows
                and current.get("registry_count") == len(rows)
                and current.get("teacher_example_coords") == [
                    {"sheet": e["sheet"], "cell": e["cell"]}
                    for e in tr["entries"] if e.get("teacher_example") is True])
        print("check:", "IDENTICAL" if same else "DIFFERS")
        return 0 if same else 1
    teacher = [{"sheet": e["sheet"], "cell": e["cell"]}
               for e in tr["entries"] if e.get("teacher_example") is True]
    if args.write:
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        doc["registry"] = rows
        doc["registry_count"] = len(rows)
        doc["teacher_example_coords"] = teacher
        out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        print("written", out_path)
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    sys.exit(main())
