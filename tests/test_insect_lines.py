"""industrial_insects line/plan-year question frame (B2 design §2, §11, §12).

Synthetic projects only: facts are injected into a loaded canonical
project dict after load and never written back.  Locks the declaration
question (line_inventory / line_retired / plan_years), instance
determination and integrity flags, the role reference rules, value
non-disclosure, and the ``insect-plan`` CLI without ``--input``.
"""
import copy
import json
import subprocess
import sys

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, fact_op, runtime, source_op,
    write_text,
)

MAJOR = "industrial_insects"
PREFIX = MAJOR + ".line."
INV = MAJOR + ".line_inventory"
RETIRED = MAJOR + ".line_retired"
YEARS = MAJOR + ".plan_years"


def _fact(fid, field_id, *, answer_state="provided",
          verification="source_located", value="synthetic-value",
          scope="farm", depends_on=None, revision=1):
    fact = {
        "id": fid,
        "field_id": field_id,
        "kind": "reported_fact",
        "value": value if answer_state == "provided" else None,
        "unit": "",
        "value_type": "text",
        "period": None,
        "scope": scope,
        "answer_state": answer_state,
        "verification": verification,
        "source_refs": [
            {"id": "major-answer", "locator": "line 1", "revision": 1}
        ],
        "revision": revision,
    }
    if answer_state in ("explicit_none", "withheld", "not_applicable"):
        fact["reason"] = "synthetic"
    if depends_on:
        fact["depends_on"] = list(depends_on)
    return fact


def _lfact(fid, lid, name, **kw):
    kw.setdefault("scope", "line:" + lid)
    return _fact(fid, PREFIX + lid + "." + name, **kw)


def _yfact(fid, lid, year, name="year_end_in_process", **kw):
    kw.setdefault("scope", "line:" + lid)
    return _fact(fid, "%s%s.year.%s.%s" % (PREFIX, lid, year, name), **kw)


def _walk_keys(value, out):
    if isinstance(value, dict):
        for key, sub in value.items():
            out.add(key)
            _walk_keys(sub, out)
    elif isinstance(value, list):
        for sub in value:
            _walk_keys(sub, out)
    return out


class _LineCase(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.doc = runtime("gg_insect_document")
        self.core = runtime("gg_core")
        self.registry = self.mc.default_registry()
        self.root = self.make_project()
        bind_major(self.root, MAJOR)
        self.project = self.core.load(self.root)

    def with_facts(self, *facts, questions=None):
        project = copy.deepcopy(self.project)
        project["facts"] = dict(project["facts"])
        for fact in facts:
            project["facts"][fact["id"]] = fact
        if questions:
            project["questions"] = dict(project["questions"])
            project["questions"].update(questions)
        return project

    def line_view(self, *facts, questions=None):
        return self.doc.build_line_view(
            self.registry, self.with_facts(*facts, questions=questions))

    def declare(self, *lids, retired=(), years=None):
        """Inventory + label (+retired / plan_years) facts for live lines."""
        facts = [_fact("inv", INV, value=",".join(lids), scope="project")]
        for lid in lids:
            facts.append(
                _lfact("lbl-" + lid, lid, "label", value=lid + "-name"))
        if retired:
            facts.append(_fact("ret", RETIRED, value=",".join(retired),
                               scope="project"))
        if years is not None:
            facts.append(_fact("yrs", YEARS, value=",".join(years),
                               scope="project"))
        return facts

    def instance_flags(self, view):
        return {i["id"]: i["flags"] for i in view["instances"]}


class DeclarationTests(_LineCase):
    def test_no_lines_inventory_is_the_only_candidate(self):
        view = self.line_view()
        self.assertEqual(view["status"], "needs_selection")
        self.assertEqual(view["instances"], [])
        self.assertEqual(view["line_questions"], {})
        inv = view["line_inventory"]
        self.assertEqual(inv["question_status"], "ask")
        self.assertEqual(inv["answer_states"], ["not_provided"])

    def test_inventory_attempts_preserved(self):
        for attempts, status in ((0, "ask"), (1, "help"), (2, "deferred")):
            with self.subTest(case=attempts):
                view = self.line_view(questions={INV: {"attempts": attempts}})
                self.assertEqual(
                    view["line_inventory"]["question_status"], status)

    def test_explicit_none_inventory_declares_no_lines(self):
        view = self.line_view(
            _fact("inv", INV, answer_state="explicit_none", scope="project"))
        self.assertEqual(view["instances"], [])
        self.assertEqual(view["line_questions"], {})
        self.assertEqual(view["line_inventory"]["question_status"], "reuse")
        self.assertEqual(view["line_inventory"]["answer_states"],
                         ["explicit_none"])

    def test_unknown_inventory_respected(self):
        view = self.line_view(
            _fact("inv", INV, answer_state="unknown", scope="project"))
        self.assertEqual(view["instances"], [])
        self.assertEqual(view["line_inventory"]["answer_states"],
                         ["unknown"])
        self.assertNotEqual(
            view["line_inventory"]["question_status"], "reuse")

    def test_declared_lines_become_instances(self):
        view = self.line_view(
            *self.declare("l01", "l02", years=["2027"]))
        self.assertEqual(
            view["instances"],
            [{"id": "l01", "flags": []}, {"id": "l02", "flags": []}])
        self.assertEqual(set(view["line_questions"]), {"l01", "l02"})
        l01 = view["line_questions"]["l01"]
        for key, st in l01.items():
            if key == "years":
                continue
            with self.subTest(case=key):
                self.assertEqual(
                    set(st), {"question_status", "answer_states", "flags",
                              "usable"} | ({"value"} if key in (
                                  "source_line", "input_line") else set()))
                # label was provided by the declaration; everything else
                # is still an unanswered candidate.
                self.assertEqual(
                    st["question_status"],
                    "reuse" if key == "label" else "ask")
                self.assertEqual(
                    st["answer_states"],
                    ["provided"] if key == "label" else ["not_provided"])
        self.assertEqual(set(l01["years"]), {"2027"})

    def test_next_line_id_never_reuses_retired_or_seen(self):
        view = self.line_view(*self.declare("l01", "l02"))
        self.assertEqual(view["next_line_id"], "l03")
        # Retire l02: the number is still not reissued (§11.5).
        view = self.line_view(
            _fact("inv", INV, value="l01", scope="project"),
            _fact("ret", RETIRED, value="l02", scope="project"),
            _lfact("lb1", "l01", "label", value="n"),
            _lfact("lb2", "l02", "label", value="n"),
            _lfact("sp2", "l02", "species", value="x"))
        flags = self.instance_flags(view)
        self.assertEqual(flags["l01"], [])
        self.assertEqual(flags["l02"], ["retired_instance"])
        self.assertEqual(view["next_line_id"], "l03")
        self.assertNotIn("l02", view["line_questions"])
        # An undeclared id seen in canonical also reserves its number.
        view = self.line_view(
            *self.declare("l01"),
            _lfact("ghost", "l09", "species", value="x"))
        self.assertEqual(view["next_line_id"], "l10")
        self.assertEqual(
            self.instance_flags(view)["l09"], ["undeclared_instance"])

    def test_inventory_mismatch_flagged(self):
        # Listed id without a provided label is not an instance.
        view = self.line_view(
            _fact("inv", INV, value="l01,l02", scope="project"),
            _lfact("lbl", "l01", "label", value="n"))
        flags = self.instance_flags(view)
        self.assertEqual(flags["l01"], [])
        self.assertEqual(flags["l02"], ["inventory_mismatch"])
        self.assertIn("inventory_mismatch",
                      view["line_inventory"]["flags"])
        self.assertIn("l01", view["line_questions"])
        self.assertNotIn("l02", view["line_questions"])

    def test_id_in_both_lists_is_mismatch_and_retired(self):
        view = self.line_view(
            _fact("inv", INV, value="l01", scope="project"),
            _fact("ret", RETIRED, value="l01", scope="project"),
            _lfact("lbl", "l01", "label", value="n"))
        self.assertEqual(
            self.instance_flags(view)["l01"],
            ["inventory_mismatch", "retired_instance"])
        self.assertNotIn("l01", view["line_questions"])

    def test_malformed_inventory_token_flagged_not_instanced(self):
        view = self.line_view(
            _fact("inv", INV, value="l01,nope", scope="project"),
            _lfact("lbl", "l01", "label", value="n"))
        self.assertIn("inventory_mismatch",
                      view["line_inventory"]["flags"])
        self.assertEqual([i["id"] for i in view["instances"]], ["l01"])

    def test_label_orphan_without_inventory_is_undeclared(self):
        view = self.line_view(_lfact("lbl", "l03", "label", value="n"))
        self.assertEqual(
            self.instance_flags(view)["l03"], ["undeclared_instance"])
        self.assertNotIn("l03", view["line_questions"])

    def test_superseded_label_does_not_declare(self):
        view = self.line_view(
            _fact("inv", INV, value="l01", scope="project"),
            _lfact("lbl", "l01", "label", value="n",
                   verification="superseded"))
        self.assertEqual(
            self.instance_flags(view)["l01"], ["inventory_mismatch"])
        self.assertNotIn("l01", view["line_questions"])

    def test_superseded_facts_still_reserve_numbers(self):
        # Astra final-1 finding 2: active l01 + superseded l02.label ->
        # next must be l03; the number is never reissued.
        view = self.line_view(
            *self.declare("l01"),
            _lfact("old", "l02", "label", value="n",
                   verification="superseded"))
        self.assertEqual(view["next_line_id"], "l03")
        # A superseded declaration-list fact reserves its ids as well.
        view = self.line_view(
            _fact("inv0", INV, value="l01,l07", scope="project",
                  verification="superseded"),
            *self.declare("l01"))
        self.assertEqual(view["next_line_id"], "l08")

    def test_line_ids_exhausted_at_grammar_ceiling(self):
        # l999 is the grammar ceiling: no l1000 may be issued.
        view = self.line_view(
            *self.declare("l01"),
            _lfact("old", "l999", "label", value="n",
                   verification="superseded"))
        self.assertIsNone(view["next_line_id"])
        self.assertIn("line_ids_exhausted",
                      view["line_inventory"]["flags"])
        view = self.line_view(
            *self.declare("l01"),
            _lfact("old", "l998", "label", value="n",
                   verification="superseded"))
        self.assertEqual(view["next_line_id"], "l999")
        self.assertNotIn("line_ids_exhausted",
                         view["line_inventory"]["flags"])

    def test_retired_list_state_is_reported(self):
        view = self.line_view(
            *self.declare("l01"),
            _fact("ret", RETIRED, value="l02", scope="project"),
            _lfact("lb2", "l02", "label", value="n"))
        state = view["line_retired"]
        self.assertEqual(
            set(state),
            {"question_status", "answer_states", "flags", "usable"})
        self.assertEqual(state["question_status"], "reuse")
        self.assertEqual(state["answer_states"], ["provided"])
        self.assertEqual(state["flags"], [])
        self.assertIs(state["usable"], True)
        self.assertEqual(
            self.instance_flags(view)["l02"], ["retired_instance"])

    def test_uncertain_retired_list_yields_no_definitive_verdicts(self):
        # Astra final-1 finding 3: disputed l02 + another active retired
        # fact -> the list keeps its conflict state and every verdict
        # grounded only in it becomes retired_uncertain.
        view = self.line_view(
            _fact("inv", INV, value="l01,l03", scope="project"),
            _fact("ret1", RETIRED, value="l02", scope="project",
                  verification="disputed"),
            _fact("ret2", RETIRED, value="l04", scope="project"),
            _lfact("lb1", "l01", "label", value="n"),
            _lfact("lb2", "l02", "label", value="n"),
            _lfact("lb3", "l03", "label", value="n"),
            _lfact("lb4", "l04", "label", value="n"),
            _lfact("r3", "l03", "product_role", value="byproduct"),
            _lfact("sl", "l03", "source_line", value="l02"))
        state = view["line_retired"]
        self.assertEqual(state["answer_states"], ["provided"])
        self.assertEqual(
            state["flags"],
            ["disputed", "inventory_mismatch", "multiple_active"])
        self.assertIs(state["usable"], False)
        flags = self.instance_flags(view)
        self.assertEqual(flags["l01"], [])
        self.assertEqual(flags["l02"], ["retired_uncertain"])
        self.assertEqual(flags["l03"], [])
        self.assertEqual(flags["l04"], ["retired_uncertain"])
        st = view["line_questions"]["l03"]["source_line"]
        self.assertEqual(st["value"], "l02")
        self.assertIn("retired_uncertain", st["flags"])
        self.assertIs(st["usable"], False)
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("retired_instance", blob)
        self.assertNotIn("retired_ref", blob)

    def test_declared_and_uncertain_retired_stays_a_candidate(self):
        # The uncertain list cannot push a declared line out of the
        # question candidates either.
        view = self.line_view(
            _fact("inv", INV, value="l01", scope="project"),
            _fact("ret1", RETIRED, value="l01", scope="project",
                  verification="disputed"),
            _fact("ret2", RETIRED, value="l02", scope="project"),
            _lfact("lb1", "l01", "label", value="n"),
            _lfact("lb2", "l02", "label", value="n"))
        self.assertEqual(
            self.instance_flags(view)["l01"],
            ["inventory_mismatch", "retired_uncertain"])
        self.assertIn("l01", view["line_questions"])
        # A clean retired list still grounds definitive verdicts.
        view = self.line_view(
            _fact("inv", INV, value="l01", scope="project"),
            _fact("ret", RETIRED, value="l01", scope="project"),
            _lfact("lb1", "l01", "label", value="n"))
        self.assertEqual(
            self.instance_flags(view)["l01"],
            ["inventory_mismatch", "retired_instance"])
        self.assertNotIn("l01", view["line_questions"])

    def test_malformed_retired_list_is_uncertain(self):
        view = self.line_view(
            *self.declare("l01"),
            _fact("ret", RETIRED, value="l02,bogus", scope="project"),
            _lfact("lb2", "l02", "label", value="n"))
        self.assertIn("inventory_mismatch",
                      view["line_retired"]["flags"])
        self.assertEqual(
            self.instance_flags(view)["l02"], ["retired_uncertain"])


class LineIndependenceTests(_LineCase):
    def test_project_answer_never_hides_line_question(self):
        facts = self.declare("l01") + [
            _fact("p1", MAJOR + ".species", value="PROJ-SECRET"),
            _fact("p2", MAJOR + ".channel", value="x"),
        ]
        questions = {MAJOR + ".species": {"attempts": 9},
                     MAJOR + ".channel": {"attempts": 9}}
        view = self.line_view(*facts, questions=questions)
        for name in ("species", "channel"):
            with self.subTest(case=name):
                st = view["line_questions"]["l01"][name]
                self.assertEqual(st["question_status"], "ask")
                self.assertEqual(st["answer_states"], ["not_provided"])

    def test_sibling_lines_are_independent(self):
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("s1", "l01", "species", value="a"))
        self.assertEqual(
            view["line_questions"]["l01"]["species"]["question_status"],
            "reuse")
        self.assertEqual(
            view["line_questions"]["l02"]["species"]["question_status"],
            "ask")

    def test_line_attempts_are_per_concrete_field(self):
        view = self.line_view(
            *self.declare("l01", "l02"),
            questions={PREFIX + "l01.species": {"attempts": 2},
                       PREFIX + "l02.species": {"attempts": 1}})
        self.assertEqual(
            view["line_questions"]["l01"]["species"]["question_status"],
            "deferred")
        self.assertEqual(
            view["line_questions"]["l02"]["species"]["question_status"],
            "help")

    def test_component_answers_split_11_1(self):
        # "상자 20개, 단 수·밀도 모름": only the answered component reuses.
        view = self.line_view(
            *self.declare("l01"),
            _lfact("b", "l01", "rearing_boxes", value="20"),
            _lfact("t", "l01", "box_tiers", answer_state="unknown"),
            _lfact("d", "l01", "density_per_box", answer_state="unknown"))
        l01 = view["line_questions"]["l01"]
        self.assertEqual(l01["rearing_boxes"]["question_status"], "reuse")
        for name in ("box_tiers", "density_per_box"):
            with self.subTest(case=name):
                self.assertEqual(l01[name]["answer_states"], ["unknown"])
                self.assertNotEqual(l01[name]["question_status"], "reuse")
        # survival_rate alone leaves survival_basis open (§11.1).
        view = self.line_view(
            *self.declare("l01"),
            _lfact("sr", "l01", "survival_rate", value="80"))
        l01 = view["line_questions"]["l01"]
        self.assertEqual(l01["survival_rate"]["question_status"], "reuse")
        self.assertEqual(l01["survival_basis"]["question_status"], "ask")

    def test_related_fact_pointer_carries_no_value(self):
        view = self.line_view(
            *self.declare("l01"),
            _fact("p1", MAJOR + ".species",
                  value="SECRET-PROJECT-VALUE", revision=3))
        ptr = view["related_project_facts"]["l01"]["species"]
        self.assertEqual(
            ptr, {"fact_id": "p1", "revision": 3,
                  "field_id": MAJOR + ".species"})
        self.assertNotIn("SECRET-PROJECT-VALUE",
                         json.dumps(view, ensure_ascii=False))

    def test_related_pointer_skips_answered_lines_and_dead_facts(self):
        # Line field already answered -> no pointer.
        view = self.line_view(
            *self.declare("l01"),
            _fact("p1", MAJOR + ".species", value="x"),
            _lfact("s", "l01", "species", value="y"))
        self.assertNotIn("species",
                         view["related_project_facts"].get("l01", {}))
        # Superseded project fact -> no pointer.
        view = self.line_view(
            *self.declare("l01"),
            _fact("p1", MAJOR + ".species", value="x",
                  verification="superseded"))
        self.assertEqual(view["related_project_facts"], {})
        # Field with no project counterpart -> never a pointer.
        view = self.line_view(
            *self.declare("l01"),
            _fact("p1", MAJOR + ".species", value="x"))
        self.assertNotIn("line_questions_entry_key_unused",
                         view["related_project_facts"].get("l01", {}))


class RefRuleTests(_LineCase):
    def _lines(self, *extra, lids=("l01", "l02"), roles=("main", "byproduct")):
        facts = list(self.declare(*lids))
        for lid, role in zip(lids, roles):
            facts.append(_lfact("role-" + lid, lid, "product_role",
                                value=role))
        facts.extend(extra)
        return self.line_view(*facts)

    def test_byproduct_without_source_line_flagged(self):
        view = self._lines()
        st = view["line_questions"]["l02"]["source_line"]
        self.assertIn("role_ref_rule", st["flags"])
        self.assertIsNone(st["value"])

    def test_service_without_source_line_flagged(self):
        view = self._lines(roles=("main", "service"))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertIn("role_ref_rule", st["flags"])

    def test_source_line_echoes_declared_main_id(self):
        view = self._lines(
            _lfact("sl", "l02", "source_line", value="l01"))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], "l01")
        self.assertNotIn("role_ref_rule", st["flags"])

    def test_source_line_to_non_main_flagged(self):
        view = self._lines(
            _lfact("sl", "l02", "source_line", value="l01"),
            roles=("processed", "byproduct"))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], "l01")
        self.assertIn("role_ref_rule", st["flags"])

    def test_processed_requires_input_line_not_source_line(self):
        view = self._lines(
            _lfact("sl", "l02", "source_line", value="l01"),
            roles=("main", "processed"))
        l02 = view["line_questions"]["l02"]
        self.assertIn("role_ref_rule", l02["input_line"]["flags"])
        self.assertIn("role_ref_rule", l02["source_line"]["flags"])

    def test_main_line_with_source_line_flagged(self):
        view = self._lines(
            _lfact("sl", "l01", "source_line", value="l01"))
        self.assertIn(
            "role_ref_rule",
            view["line_questions"]["l01"]["source_line"]["flags"])

    def test_self_reference_flagged(self):
        view = self._lines(
            _lfact("il", "l02", "input_line", value="l02"),
            roles=("main", "processed"))
        st = view["line_questions"]["l02"]["input_line"]
        self.assertEqual(st["value"], "l02")
        self.assertIn("role_ref_rule", st["flags"])

    def test_undeclared_ref_value_never_echoed(self):
        secret = "l99-SECRET-raw-value"
        view = self._lines(
            _lfact("sl", "l02", "source_line", value=secret))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], {"state": "invalid_ref"})
        self.assertIn("invalid_ref", st["flags"])
        self.assertNotIn(secret, json.dumps(view, ensure_ascii=False))
        # A grammar-valid but undeclared id is also invalid_ref.
        view = self._lines(
            _lfact("sl", "l02", "source_line", value="l09"))
        self.assertEqual(
            view["line_questions"]["l02"]["source_line"]["value"],
            {"state": "invalid_ref"})

    def test_retired_ref_flagged_but_id_echoed(self):
        view = self.line_view(
            _fact("inv", INV, value="l01,l03", scope="project"),
            _fact("ret", RETIRED, value="l02", scope="project"),
            _lfact("lb1", "l01", "label", value="n"),
            _lfact("lb2", "l02", "label", value="n"),
            _lfact("lb3", "l03", "label", value="n"),
            _lfact("r3", "l03", "product_role", value="byproduct"),
            _lfact("sl", "l03", "source_line", value="l02"))
        st = view["line_questions"]["l03"]["source_line"]
        self.assertEqual(st["value"], "l02")
        self.assertIn("retired_ref", st["flags"])

    def test_unknown_product_role_flagged(self):
        view = self._lines(roles=("main", "upcycled"))
        self.assertIn(
            "role_ref_rule",
            view["line_questions"]["l02"]["product_role"]["flags"])

    def test_disputed_target_role_is_referenced_role_uncertain(self):
        # Astra final-1 finding 4: l01 role=main but disputed — the
        # reference must read uncertain, never confirmed-clean.
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("r1", "l01", "product_role", value="main",
                   verification="disputed"),
            _lfact("r2", "l02", "product_role", value="byproduct"),
            _lfact("sl", "l02", "source_line", value="l01"))
        role = view["line_questions"]["l01"]["product_role"]
        self.assertEqual(role["flags"], ["disputed"])
        self.assertIs(role["usable"], False)
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], "l01")
        self.assertEqual(st["flags"], ["referenced_role_uncertain"])
        self.assertIs(st["usable"], False)

    def test_mixed_role_answer_states_are_unconfirmed(self):
        # Astra: provided main + unknown duplicate -> unconfirmed too.
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("r1a", "l01", "product_role", value="main"),
            _lfact("r1b", "l01", "product_role", answer_state="unknown"),
            _lfact("r2", "l02", "product_role", value="byproduct"),
            _lfact("sl", "l02", "source_line", value="l01"))
        self.assertEqual(
            view["line_questions"]["l01"]["product_role"]["flags"],
            ["multiple_active"])
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], "l01")
        self.assertIn("referenced_role_uncertain", st["flags"])
        self.assertIs(st["usable"], False)

    def test_input_line_to_unconfirmed_role_flagged(self):
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("r1a", "l01", "product_role", value="main"),
            _lfact("r1b", "l01", "product_role", value="main",
                   verification="disputed"),
            _lfact("r2", "l02", "product_role", value="processed"),
            _lfact("il", "l02", "input_line", value="l01"))
        st = view["line_questions"]["l02"]["input_line"]
        self.assertEqual(st["value"], "l01")
        self.assertIn("referenced_role_uncertain", st["flags"])
        self.assertIs(st["usable"], False)

    def test_target_without_role_answer_is_unconfirmed(self):
        # A target that never answered product_role cannot be confirmed
        # main — uncertain, not a definitive role_ref_rule violation.
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("r2", "l02", "product_role", value="byproduct"),
            _lfact("sl", "l02", "source_line", value="l01"))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], "l01")
        self.assertEqual(st["flags"], ["referenced_role_uncertain"])
        self.assertIs(st["usable"], False)

    def test_own_unconfirmed_role_skips_presence_verdict(self):
        # l01's role is genuinely ambiguous; its own source_line cannot
        # be judged against an unconfirmed role.
        view = self.line_view(
            *self.declare("l01", "l02"),
            _lfact("r1a", "l01", "product_role", value="main"),
            _lfact("r1b", "l01", "product_role", value="byproduct"),
            _lfact("r2", "l02", "product_role", value="main"),
            _lfact("sl", "l01", "source_line", value="l02"))
        self.assertEqual(
            view["line_questions"]["l01"]["product_role"]["flags"],
            ["multiple_active"])
        st = view["line_questions"]["l01"]["source_line"]
        self.assertEqual(st["value"], "l02")
        self.assertEqual(st["flags"], [])

    def test_trailing_newline_ref_value_is_invalid_ref(self):
        # Astra finding 5: grammar is a whole-string contract — "l01\n"
        # is not the declared id l01.
        view = self._lines(
            _lfact("sl", "l02", "source_line", value="l01\n"))
        st = view["line_questions"]["l02"]["source_line"]
        self.assertEqual(st["value"], {"state": "invalid_ref"})
        self.assertIn("invalid_ref", st["flags"])
        # The raw value never echoes — not even JSON-escaped.
        self.assertNotIn("l01\\n", json.dumps(view, ensure_ascii=False))


class IntegrityFlagTests(_LineCase):
    def test_multiple_active_and_disputed_mark_unusable(self):
        view = self.line_view(
            *self.declare("l01"),
            _lfact("s1", "l01", "species", value="a"),
            _lfact("s2", "l01", "species", value="b",
                   verification="disputed"))
        st = view["line_questions"]["l01"]["species"]
        self.assertEqual(st["question_status"], "reuse")
        self.assertEqual(st["answer_states"], ["provided"])
        self.assertEqual(st["flags"], ["disputed", "multiple_active"])
        self.assertIs(st["usable"], False)

    def test_missing_companion_fields_flagged(self):
        view = self.line_view(
            *self.declare("l01"),
            _lfact("sr", "l01", "survival_rate", value="80"),
            _lfact("si", "l01", "stocking_input", value="100"))
        l01 = view["line_questions"]["l01"]
        self.assertIn("missing_companion", l01["survival_rate"]["flags"])
        self.assertIn("missing_companion", l01["stocking_input"]["flags"])
        # Companions present -> clean.
        view = self.line_view(
            *self.declare("l01"),
            _lfact("sr", "l01", "survival_rate", value="80"),
            _lfact("sb", "l01", "survival_basis", value="count"),
            _lfact("si", "l01", "stocking_input", value="100"),
            _lfact("su", "l01", "stocking_unit", value="마리"))
        l01 = view["line_questions"]["l01"]
        self.assertNotIn("missing_companion", l01["survival_rate"]["flags"])
        self.assertNotIn("missing_companion", l01["stocking_input"]["flags"])

    def test_scope_mismatch_flagged(self):
        view = self.line_view(
            *self.declare("l01"),
            _lfact("s", "l01", "species", value="a", scope="farm"))
        self.assertIn(
            "scope_mismatch",
            view["line_questions"]["l01"]["species"]["flags"])

    def test_dependency_superseded_flagged(self):
        view = self.line_view(
            *self.declare("l01"),
            _fact("old", MAJOR + ".species", value="x",
                  verification="superseded"),
            _lfact("s", "l01", "species", value="y",
                   depends_on=[{"collection": "facts", "id": "old",
                                "revision": 1}]))
        self.assertIn(
            "dependency_superseded",
            view["line_questions"]["l01"]["species"]["flags"])
        # A live dependency is not flagged.
        view = self.line_view(
            *self.declare("l01"),
            _fact("src", MAJOR + ".species", value="x"),
            _lfact("s", "l01", "species", value="y",
                   depends_on=[{"collection": "facts", "id": "src",
                                "revision": 1}]))
        self.assertNotIn(
            "dependency_superseded",
            view["line_questions"]["l01"]["species"]["flags"])

    def test_invalid_field_id_never_emits_raw_text(self):
        # Astra final-1 finding 1: a bogus field_id must not echo its raw
        # text into instance_issues or any output — status codes plus
        # grammar-verified line id/year tokens only.
        marker = "SYNTHETIC_PRIVATE_MARKER"
        bad_line = PREFIX + "l01." + marker
        bad_ghost = PREFIX + "lX.species"
        view = self.line_view(
            *self.declare("l01"),
            _fact("bad1", bad_line, value="x", scope="line:l01"),
            _fact("bad2", bad_ghost, value="x"))
        blob = json.dumps(view, ensure_ascii=False)
        for raw in (marker, bad_line, bad_ghost):
            with self.subTest(case=raw):
                self.assertNotIn(raw, blob)
        self.assertEqual(
            view["instance_issues"],
            [{"kind": "invalid_field_id", "line_id": "l01"},
             {"kind": "invalid_field_id"}])
        issue_keys = _walk_keys(view["instance_issues"], set())
        self.assertNotIn("field_id", issue_keys)
        self.assertNotIn("value", issue_keys)

    def test_trailing_newline_field_id_is_invalid_not_aggregated(self):
        # Astra finding 5 counterexample: "…l01.species\n" used to count
        # as the species field.  Whole-string grammar sends it to
        # invalid_field_id and the real field stays unanswered.
        view = self.line_view(
            *self.declare("l01"),
            _fact("nl", PREFIX + "l01.species\n", value="x",
                  scope="line:l01"))
        species = view["line_questions"]["l01"]["species"]
        self.assertEqual(species["question_status"], "ask")
        self.assertEqual(species["answer_states"], ["not_provided"])
        self.assertEqual(species["flags"], [])
        self.assertEqual(
            view["instance_issues"],
            [{"kind": "invalid_field_id", "line_id": "l01"}])
        self.assertNotIn(
            "l01.species\\n", json.dumps(view, ensure_ascii=False))

    def test_trailing_newline_year_field_id_is_invalid(self):
        # Same counterexample on the year axis.
        view = self.line_view(
            *self.declare("l01", years=["2027"]),
            _fact("nl", PREFIX + "l01.year.2027.year_end_in_process\n",
                  value="x", scope="line:l01"))
        st = view["line_questions"]["l01"]["years"]["2027"][
            "year_end_in_process"]
        self.assertEqual(st["question_status"], "ask")
        self.assertEqual(st["answer_states"], ["not_provided"])
        self.assertEqual(st["flags"], [])
        self.assertEqual(
            view["instance_issues"],
            [{"kind": "invalid_field_id", "line_id": "l01",
              "year": "2027"}])

    def test_no_calculation_or_value_keys(self):
        view = self.line_view(
            *self.declare("l01", years=["2027"]),
            _lfact("s", "l01", "species", value="a"),
            _yfact("y", "l01", "2027", value="아니오"))
        keys = _walk_keys(view, set())
        for banned in ("calculated", "computed", "result", "total", "sum",
                       "revenue", "production", "forecast", "body",
                       "content", "authorization"):
            with self.subTest(case=banned):
                self.assertNotIn(banned, keys)
        self.assertIsInstance(view["next_line_id"], str)


class PlanYearTests(_LineCase):
    def _base(self, *extra, years=("2027", "2028"), questions=None):
        return self.line_view(
            *self.declare("l01", years=list(years)), *extra,
            questions=questions)

    def _year(self, view, year, lid="l01"):
        return view["line_questions"][lid]["years"][year][
            "year_end_in_process"]

    def test_declared_years_appear_per_line(self):
        view = self._base()
        years = view["line_questions"]["l01"]["years"]
        self.assertEqual(set(years), {"2027", "2028"})
        for year, entry in years.items():
            with self.subTest(case=year):
                st = entry["year_end_in_process"]
                self.assertEqual(st["question_status"], "ask")
                self.assertEqual(st["answer_states"], ["not_provided"])
                self.assertEqual(st["flags"], [])

    def test_answered_year_does_not_hide_other_years(self):
        # §12.1: 2027 answered, 2028 stays a candidate.
        view = self._base(_yfact("y27", "l01", "2027", value="아니오"))
        self.assertEqual(
            self._year(view, "2027")["question_status"], "reuse")
        self.assertEqual(
            self._year(view, "2028")["question_status"], "ask")

    def test_both_years_answered_is_not_duplicate(self):
        # §12.2: distinct concrete fields per year, no duplication.
        view = self._base(_yfact("y27", "l01", "2027", value="아니오"),
                          _yfact("y28", "l01", "2028", value="예"))
        for year in ("2027", "2028"):
            with self.subTest(case=year):
                st = self._year(view, year)
                self.assertEqual(st["question_status"], "reuse")
                self.assertEqual(st["answer_states"], ["provided"])
                self.assertEqual(st["flags"], [])

    def test_same_year_multiple_active(self):
        # §12.3: two active facts on one year field stay detected.
        view = self._base(_yfact("a", "l01", "2027", value="예"),
                          _yfact("b", "l01", "2027", value="아니오"))
        st = self._year(view, "2027")
        self.assertIn("multiple_active", st["flags"])
        self.assertIs(st["usable"], False)

    def test_unknown_year_stays_pending_no_substitution(self):
        # §12.4: an unknown year is never covered by another year's answer.
        view = self._base(_yfact("y27", "l01", "2027", value="아니오"),
                          _yfact("y28", "l01", "2028",
                                 answer_state="unknown"))
        st = self._year(view, "2028")
        self.assertEqual(st["answer_states"], ["unknown"])
        self.assertNotEqual(st["question_status"], "reuse")
        self.assertNotIn("provided", st["answer_states"])
        self.assertEqual(
            self._year(view, "2027")["question_status"], "reuse")

    def test_year_fact_outside_plan_years_is_undeclared(self):
        # §12.5: a year absent from plan_years is undeclared_instance.
        view = self._base(_yfact("y29", "l01", "2029", value="예"))
        st = self._year(view, "2029")
        self.assertIn("undeclared_instance", st["flags"])
        self.assertIs(st["usable"], False)
        self.assertNotIn("undeclared_instance",
                         self._year(view, "2027")["flags"])

    def test_no_plan_years_no_year_candidates(self):
        view = self.line_view(*self.declare("l01"))
        self.assertEqual(view["line_questions"]["l01"]["years"], {})
        # A stray year fact still surfaces, flagged undeclared.
        view = self.line_view(
            *self.declare("l01"), _yfact("y", "l01", "2027", value="예"))
        st = self._year(view, "2027")
        self.assertIn("undeclared_instance", st["flags"])

    def test_malformed_plan_year_token_flagged(self):
        view = self.line_view(*self.declare("l01", years=["2027", "27"]))
        self.assertIn("inventory_mismatch", view["plan_years"]["flags"])
        self.assertEqual(
            set(view["line_questions"]["l01"]["years"]), {"2027"})

    def test_plan_years_attempts_preserved(self):
        for attempts, status in ((0, "ask"), (1, "help"),
                                 (2, "deferred")):
            with self.subTest(case=attempts):
                view = self.line_view(
                    *self.declare("l01"),
                    questions={YEARS: {"attempts": attempts}})
                self.assertEqual(
                    view["plan_years"]["question_status"], status)


class NoInputCliTests(ContractCase):
    def _gg(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg.py"),
             *map(str, args)],
            capture_output=True, text=True)

    def test_insect_plan_without_input_lists_lines(self):
        core = runtime("gg_core")
        root = self.make_project()
        bind_major(root, MAJOR)
        write_text(root, "answer.txt", "라인 답변\n")
        ops = [
            source_op("answer.txt"),
            fact_op("inv", INV, "l01", "", scope="project"),
            fact_op("lbl", PREFIX + "l01.label", "SECRET-LINE-NAME", "",
                    scope="line:l01"),
            fact_op("sp", PREFIX + "l01.species", "SECRET-SPECIES", "",
                    scope="line:l01"),
            fact_op("yrs", YEARS, "2027", "", scope="project"),
            fact_op("y27", PREFIX + "l01.year.2027.year_end_in_process",
                    "SECRET-YEAR-VALUE", "", scope="line:l01"),
        ]
        core.apply(root, {"request_id": "declare-lines", "ops": ops},
                   core.load(root)["revision"])
        before = self._tree_bytes(root)
        proc = self._gg("insect-plan", root)
        self.assertEqual(before, self._tree_bytes(root))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual(value["status"], "needs_selection")
        self.assertNotIn("selected_sections", value)
        self.assertEqual(value["instances"], [{"id": "l01", "flags": []}])
        self.assertIn("l01", value["line_questions"])
        self.assertEqual(
            set(value["line_questions"]["l01"]["years"]), {"2027"})
        self.assertEqual(value["next_line_id"], "l02")
        self.assertEqual(value["line_inventory"]["question_status"],
                         "reuse")
        blob = proc.stdout + proc.stderr
        for secret in ("SECRET-LINE-NAME", "SECRET-SPECIES",
                       "SECRET-YEAR-VALUE"):
            with self.subTest(case=secret):
                self.assertNotIn(secret, blob)
        self.assertNotIn(str(root), blob)

    def test_insect_plan_without_input_wrong_major(self):
        other = self.make_project()
        bind_major(other, "specialty_crops")
        before = self._tree_bytes(other)
        proc = self._gg("insect-plan", other)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self._tree_bytes(other))

    def test_insect_plan_issues_never_echo_raw_field_id(self):
        # Astra final-1 finding 1 at the CLI surface: the marker-bearing
        # field_id must not appear in stdout/stderr at all.
        core = runtime("gg_core")
        root = self.make_project()
        bind_major(root, MAJOR)
        write_text(root, "answer.txt", "라인 답변\n")
        marker = "SYNTHETIC_PRIVATE_MARKER"
        ops = [
            source_op("answer.txt"),
            fact_op("inv", INV, "l01", "", scope="project"),
            fact_op("lbl", PREFIX + "l01.label", "n", "",
                    scope="line:l01"),
            fact_op("bad", PREFIX + "l01." + marker, "x", "",
                    scope="line:l01"),
        ]
        core.apply(root, {"request_id": "bad-field", "ops": ops},
                   core.load(root)["revision"])
        before = self._tree_bytes(root)
        proc = self._gg("insect-plan", root)
        self.assertEqual(before, self._tree_bytes(root))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        blob = proc.stdout + proc.stderr
        self.assertNotIn(marker, blob)
        self.assertNotIn(PREFIX + "l01." + marker, blob)
        value = json.loads(proc.stdout)
        self.assertEqual(
            value["instance_issues"],
            [{"kind": "invalid_field_id", "line_id": "l01"}])
