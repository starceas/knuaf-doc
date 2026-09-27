"""Common major contract for knuaf-doc (DECISION-20260924).

The common layer owns major selection and result validation.  Each major
module is a *peer*: it declares what to ask, narrate, and treat as
evidence or finance candidates, and the router binds exactly one
explicit ``major_id``, hands the module a read-only canonical view, and
returns its proposal to the common validation/output path.

Hard rules carried by this module:

- A missing ``major_id`` fails closed.  It is never defaulted to
  specialty_crops; only common source organization may proceed.
- Major modules are peers.  ``imports`` must be empty and a module may
  not declare or reference a pack owned by another major.
- Modules never write the canonical project record.  They return
  proposals; revision compare, lock, write, and approval invalidation
  stay on the existing common transaction path.
- Answer states are the existing six ``gg_core`` states, reused — never
  redefined, and ``0`` is never inferred for a missing answer.
- industrial_insects keeps ``empty_slot`` data, performs no calculation,
  emits no specialty-crop paper wording, and claims no crop workbook.
- hort_env_systems collects question/document/evidence plans only — it
  performs no finance calculation and claims no paper or workbook
  output, so other majors' wording may not appear in its proposals.
"""

import json
import re
import zipfile
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType

import gg_core

CONTRACT_VERSION = "knuaf-major-contract/1"

# The six answer states are the gg_core contract — reused, not redefined.
ANSWER_STATES = gg_core.STATES

CAPABILITY_KINDS = ("question", "document", "evidence", "finance")

# Evidence contract axes (DECISION §3): kept on separate axes, never
# merged into one status string.  ``link_only`` and ``source_conflict``
# are NOT CatalogStatus values; they live on the acquisition and
# conflict axes respectively.
ACQUISITION_STATES = ("link_only", "bytes_held")
OBSERVATION_STATES = ("verified_observation", "exploratory", "quarantined")
RIGHTS_STATES = ("internal_view", "redistribution_confirmed", "unconfirmed")
CONFLICT_STATES = ("none", "unresolved", "corrected")
APPLICABILITY_STATES = ("applicable", "inapplicable", "unverified")
APPROVAL_STATES = ("unapproved", "approval_candidate", "promotable")
CONFLICT_ALIASES = {"source_conflict": "unresolved"}

EVIDENCE_AXES = (
    "acquisition",
    "observation",
    "rights",
    "conflict",
    "applicability",
    "approval",
)

# Promotion order: source -> parse -> cell/render compare ->
# verification/rights/conflict/applicability -> approval candidate.
PROMOTION_ORDER = (
    "source_acquired",
    "parsed_receipt",
    "cell_render_compared",
    "axes_checked",
    "approval_candidate",
)

# Common finance envelope: these stay distinct fields — a calculation
# flag never doubles as evidence-satisfied, recalculated, or submitted.
FINANCE_ENVELOPE_FIELDS = (
    "inputs",
    "formula",
    "source_refs",
    "unit",
    "period",
    "revision_hash",
    "calculation_performed",
    "evidence_satisfied",
    "recalculated",
    "submission_verified",
)


class MajorContractError(Exception):
    """Fail-closed base error.  ``reason`` is the machine-checkable id."""

    reason = "major_contract_error"

    def __init__(self, message=None, **detail):
        super().__init__(message or self.reason)
        self.detail = detail


class MissingMajorError(MajorContractError):
    reason = "major_required"


class UnknownMajorError(MajorContractError):
    reason = "unknown_major"


class BindingInvalidError(MajorContractError):
    reason = "binding_invalid"


class UnsupportedOutputError(MajorContractError):
    reason = "unsupported_output"


class CrossMajorDependencyError(MajorContractError):
    reason = "cross_major_dependency"


class DeclarationInvalidError(MajorContractError):
    reason = "declaration_invalid"


class ProposalInvalidError(MajorContractError):
    reason = "proposal_invalid"


@dataclass(frozen=True)
class QuestionSpec:
    """A major-owned question field (DECISION §3 field_id namespace)."""

    field_id: str
    meaning: str
    unit: str = None
    period: str = None
    target: str = None
    source: str = None


@dataclass(frozen=True)
class PrecedentSpec:
    """One precedent in the module's roster.

    All entries share the same ``kind`` — ``example_observed`` — so no
    single exemplar outranks its peers (F0 and E1–E4 are equal).
    """

    ref: str
    kind: str = "example_observed"


@dataclass(frozen=True)
class DocumentNodeSpec:
    """A document node: role plus *why* it was selected/transformed.

    ``required=False`` marks a proposed-but-optional node — the plan
    never mandates a section count.  ``precedent_refs`` may cite a
    subset of the module's declared precedent roster where the role
    was actually observed; empty means informed by the roster pool
    without a per-exemplar claim.  ``rationale`` records why the node
    is proposed, ``transform_reason`` why it deviates from precedent.
    """

    section_id: str
    role: str
    selectable: bool = True
    required: bool = False
    rationale: str = None
    precedent_refs: tuple = ()
    transform_reason: str = None
    evidence_ids: tuple = ()


@dataclass(frozen=True)
class FinanceCapability:
    """One named finance profile and whether the module may compute it."""

    profile: str
    status: str  # "supported" | "unsupported"
    reason: str = ""


@dataclass(frozen=True)
class MajorModule:
    """A peer major module declaration (DECISION §3 interface)."""

    major_id: str
    module_version: str
    field_namespace: str
    capabilities: object  # MappingProxyType {kind: "supported"|"unsupported"}
    question_schema: tuple
    document_plan: tuple
    evidence_applicability: object  # MappingProxyType
    finance_capabilities: tuple
    validation_rules: tuple
    supported_outputs: tuple
    precedents: tuple = ()  # equal-rank PrecedentSpec roster
    packs: tuple = ()
    pack_refs: tuple = ()
    imports: tuple = ()
    forbidden_terms: tuple = ()
    contract_version: str = CONTRACT_VERSION


# Binding basis is a closed set.  ``legacy_record`` bindings (existing
# projects handled through the compatibility adapter) require a confirmed
# binding-evidence pointer; ``explicit_selection`` does not.
BINDING_BASES = ("explicit_selection", "legacy_record")

# The common layer's own binding fact — a ``field_id`` outside every
# major namespace, written to a project's facts only through the
# existing gg_core transaction path, never by this module.
COMMON_MAJOR_FIELD = "common.major_id"

# A legacy binding requires a confirmed fact: only claim_supported
# verification counts.  Unreviewed or merely source-located answers
# cannot activate a major adapter.
_BINDING_REQUIRED_VERIFY = "claim_supported"


@dataclass(frozen=True)
class MajorBinding:
    """An explicit major binding — never inferred, never defaulted."""

    major_id: str
    basis: str
    module_version: str
    contract_version: str
    binding_evidence: str = None


@dataclass(frozen=True)
class ModuleProposal:
    """What a module hands back to the common layer.

    ``canonical_write`` is always False: proposals are data for common
    validation/commit, never direct writes to the canonical record.
    ``canonical`` is the deep read-only view the module was shown
    (``canonical_view`` output), or None when no record was handed over.
    It is intentionally excluded from ``to_dict`` — the canonical record
    is input context, not proposal content.
    """

    major_id: str
    module_version: str
    contract_version: str
    capability: str
    status: str  # "supported" | "unsupported"
    proposal: object  # frozen MappingProxyType payload
    canonical: object = None  # MappingProxyType view or None
    canonical_write: bool = False

    def to_dict(self):
        return {
            "major_id": self.major_id,
            "module_version": self.module_version,
            "contract_version": self.contract_version,
            "capability": self.capability,
            "status": self.status,
            "proposal": _thaw(self.proposal),
            "canonical_present": self.canonical is not None,
            "canonical_write": self.canonical_write,
        }


def _freeze(value):
    """Deep-read-only view: dict -> MappingProxyType, list -> tuple."""
    if isinstance(value, MappingProxyType):
        return value
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(_freeze(v) for v in value)
    return value


def _thaw(value):
    """Inverse of _freeze for serialization/scanning."""
    if isinstance(value, MappingProxyType):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_thaw(v) for v in value]
    return value


def canonical_view(canonical):
    """The read-only canonical record handed to a module."""
    return _freeze(canonical)


def normalize_answer_state(state):
    """Validate an answer state against the six gg_core states.

    Missing answers stay ``not_provided``; ``0`` is never inferred.
    """
    if not isinstance(state, str) or state not in ANSWER_STATES:
        raise ValueError("unknown answer state: %r" % (state,))
    return state


def check_dependencies(module, pack_owner=None):
    """Peer rule: no cross-major import, no foreign pack reference.

    ``pack_owner`` maps every *known* pack_id to its owning major_id,
    with None marking a common pack.  When the map is provided, a pack
    id absent from it is unknown and fails closed — unknown ids are
    never treated as common.  With ``pack_owner=None`` (declare time)
    only the imports rule is checked; pack resolution happens when the
    module is registered against a real catalog.
    """
    if module.imports:
        raise CrossMajorDependencyError(
            "major modules may not import other modules",
            major_id=module.major_id,
            imports=tuple(module.imports),
        )
    if pack_owner is None:
        return
    for ref in module.packs:
        if ref not in pack_owner:
            raise DeclarationInvalidError(
                "unknown pack %r" % ref, major_id=module.major_id,
                pack=ref,
            )
        if pack_owner[ref] != module.major_id:
            raise CrossMajorDependencyError(
                "pack %r is owned by major %r" % (ref, pack_owner[ref]),
                major_id=module.major_id,
                pack=ref,
                owner=pack_owner[ref],
            )
    for ref in module.pack_refs:
        if ref not in pack_owner:
            raise DeclarationInvalidError(
                "unknown pack %r" % ref, major_id=module.major_id,
                pack=ref,
            )
        if pack_owner[ref] not in (None, module.major_id):
            raise CrossMajorDependencyError(
                "pack %r is owned by major %r" % (ref, pack_owner[ref]),
                major_id=module.major_id,
                pack=ref,
                owner=pack_owner[ref],
            )


def validate_declaration(module, pack_owner=None):
    """Structural contract checks applied at declaration/register time."""
    if not isinstance(module, MajorModule):
        raise DeclarationInvalidError("not a MajorModule", got=type(module))
    if module.contract_version != CONTRACT_VERSION:
        raise DeclarationInvalidError(
            "contract_version mismatch",
            major_id=module.major_id,
            got=module.contract_version,
        )
    if not module.major_id or not isinstance(module.major_id, str):
        raise DeclarationInvalidError("major_id required")
    if module.field_namespace != module.major_id:
        raise DeclarationInvalidError(
            "field_namespace must equal major_id",
            major_id=module.major_id,
            field_namespace=module.field_namespace,
        )
    unknown_caps = set(module.capabilities) - set(CAPABILITY_KINDS)
    if unknown_caps:
        raise DeclarationInvalidError(
            "unknown capability kinds", unknown=sorted(unknown_caps)
        )
    prefix = module.field_namespace + "."
    for q in module.question_schema:
        if not q.field_id.startswith(prefix):
            raise DeclarationInvalidError(
                "field_id outside major namespace",
                major_id=module.major_id,
                field_id=q.field_id,
            )
    refs = [p.ref for p in module.precedents]
    if len(set(refs)) != len(refs):
        raise DeclarationInvalidError(
            "duplicate precedent refs", major_id=module.major_id
        )
    if len({p.kind for p in module.precedents}) > 1:
        raise DeclarationInvalidError(
            "precedents must share one kind (equal rank)",
            major_id=module.major_id,
        )
    for n in module.document_plan:
        unknown = set(n.precedent_refs) - set(refs)
        if unknown:
            raise DeclarationInvalidError(
                "node cites undeclared precedent",
                major_id=module.major_id,
                section_id=n.section_id,
                refs=sorted(unknown),
            )
    check_dependencies(module, pack_owner)


def declare_module(
    *,
    major_id,
    module_version,
    capabilities,
    question_schema=(),
    document_plan=(),
    precedents=(),
    evidence_applicability=None,
    finance_capabilities=(),
    validation_rules=(),
    supported_outputs=(),
    packs=(),
    pack_refs=(),
    imports=(),
    forbidden_terms=(),
    contract_version=CONTRACT_VERSION,
    field_namespace=None,
):
    """Build a structurally-validated, immutable module declaration."""
    module = MajorModule(
        major_id=major_id,
        module_version=module_version,
        field_namespace=field_namespace or major_id,
        capabilities=MappingProxyType(dict(capabilities)),
        question_schema=tuple(question_schema),
        document_plan=tuple(document_plan),
        precedents=tuple(precedents),
        evidence_applicability=MappingProxyType(
            dict(evidence_applicability or {})
        ),
        finance_capabilities=tuple(finance_capabilities),
        validation_rules=tuple(validation_rules),
        supported_outputs=tuple(supported_outputs),
        packs=tuple(packs),
        pack_refs=tuple(pack_refs),
        imports=tuple(imports),
        forbidden_terms=tuple(forbidden_terms),
        contract_version=contract_version,
    )
    validate_declaration(module, pack_owner=None)
    return module


class ModuleRegistry:
    """The peer registry.  ``pack_owner`` maps pack_id -> major_id, with
    None marking common packs (common is never a major default)."""

    def __init__(self, modules=(), pack_owner=None):
        self._pack_owner = dict(pack_owner or {})
        self._modules = {}
        for m in modules:
            self.register(m)

    def register(self, module):
        validate_declaration(module, self._pack_owner)
        if module.major_id in self._modules:
            raise DeclarationInvalidError(
                "duplicate major_id", major_id=module.major_id
            )
        self._modules[module.major_id] = module
        return module

    def resolve(self, major_id):
        """Explicit-only resolution — fail closed, never default."""
        if not major_id or not isinstance(major_id, str):
            raise MissingMajorError("explicit major_id required")
        module = self._modules.get(major_id)
        if module is None:
            raise UnknownMajorError(
                "major %r not registered" % major_id, major_id=major_id
            )
        check_dependencies(module, self._pack_owner)
        return module

    @property
    def major_ids(self):
        return tuple(self._modules)


def bind_major(
    registry, major_id, *, basis="explicit_selection", evidence=None
):
    """Bind one explicit major.  Missing/unknown fails closed; there is
    no global default — specialty_crops is bound only when named.

    ``basis`` must be one of ``BINDING_BASES``.  A ``legacy_record``
    binding (an existing project without a stored major) is accepted
    only with a confirmed binding-evidence pointer — never guessed.
    """
    if basis not in BINDING_BASES:
        raise BindingInvalidError(
            "basis %r not in %r" % (basis, BINDING_BASES), basis=basis
        )
    if basis == "legacy_record" and (
        not isinstance(evidence, str) or not evidence
    ):
        raise BindingInvalidError(
            "legacy_record binding requires confirmed evidence",
            major_id=major_id,
        )
    module = registry.resolve(major_id)
    return MajorBinding(
        major_id=module.major_id,
        basis=basis,
        module_version=module.module_version,
        contract_version=CONTRACT_VERSION,
        binding_evidence=evidence,
    )


def binding_from_project(registry, project):
    """Read a confirmed ``common.major_id`` fact from a loaded project.

    ``project`` is a ``gg_core.load(root)`` result (or an equivalent
    read-only mapping).  This helper is strictly read-only — it never
    writes canonical state; a new project records the fact through the
    existing ``gg_core.apply`` path.

    A fact binds only when it is ``provided`` with a major_id ``value``,
    carries ``claim_supported`` verification and ``source_refs`` that
    resolve in ``project["sources"]``, and records a ``module_version``
    equal to the registered module's version.  No provided fact ->
    ``MissingMajorError`` (a missing major is never inferred and never
    defaulted to specialty_crops); duplicate or conflicting provided
    facts, verification short of ``claim_supported``, unresolved
    source_refs, or a missing/mismatched module_version ->
    ``BindingInvalidError``.
    """
    facts = project.get("facts") or {}
    provided = [
        (fid, f)
        for fid, f in facts.items()
        if isinstance(f, dict)
        and f.get("field_id") == COMMON_MAJOR_FIELD
        and f.get("answer_state") == "provided"
    ]
    if not provided:
        raise MissingMajorError(
            "no provided %s fact" % COMMON_MAJOR_FIELD
        )
    if len(provided) > 1:
        values = {f.get("value") for _, f in provided}
        raise BindingInvalidError(
            "conflicting_major_facts"
            if len(values) > 1
            else "duplicate_major_facts",
            count=len(provided),
        )
    fact_id, fact = provided[0]
    if fact.get("verification") != _BINDING_REQUIRED_VERIFY:
        raise BindingInvalidError(
            "major fact verification %r is not claim_supported"
            % fact.get("verification")
        )
    major_id = fact.get("value")
    if not isinstance(major_id, str) or not major_id:
        raise BindingInvalidError("provided major fact has no major_id")
    module = registry.resolve(major_id)
    sources = project.get("sources") or {}
    refs = fact.get("source_refs") or []
    if not refs:
        raise BindingInvalidError("major fact has no source_refs")
    missing = [r.get("id") for r in refs if r.get("id") not in sources]
    if missing:
        raise BindingInvalidError(
            "source_refs unresolved", missing=missing
        )
    recorded = fact.get("module_version")
    if not isinstance(recorded, str) or not recorded:
        raise BindingInvalidError("module_version not recorded")
    if recorded != module.module_version:
        raise BindingInvalidError(
            "module_version mismatch",
            recorded=recorded,
            registered=module.module_version,
        )
    return bind_major(
        registry,
        major_id,
        basis="legacy_record",
        evidence="facts:%s" % fact_id,
    )


def common_only_plan(reason="major_required"):
    """Missing major: hold major-dependent work; common source org only."""
    return {
        "status": "held",
        "reason": reason,
        "common_only": True,
        "allowed": ("source_organization", "answer_state_recording"),
        "held": CAPABILITY_KINDS,
    }


# ---------------------------------------------------------------------
# Output guard — policy B (owner decision 2026-09-25)
# ---------------------------------------------------------------------
#
# Every path that returns a paper body or finance calculation, or writes
# a deliverable file or its receipt, calls ``authorize_output`` before any
# return value, staging file, or receipt exists.  An output is allowed only
# when the request names an explicit ``major_id`` AND the current canonical
# revision holds a valid ``common.major_id`` binding for that same major AND
# the bound module declares the requested output.  There is no specialty
# inference and no legacy compatibility path: crop names, cover labels, file
# age, template hashes, or X01/X02 selection never stand in for a major.
# Refusing never writes the canonical record.

OUTPUT_SCHOOL_PAPER = "school_paper"
OUTPUT_SCHOOL_WORKBOOK = "school_excel_workbook"
OUTPUT_FINANCE_CALCULATION = "finance_calculation"

# output kind -> ("output", supported_outputs entry) or
# ("capability", CAPABILITY_KINDS entry that must be "supported").
OUTPUT_REQUIREMENTS = MappingProxyType({
    OUTPUT_SCHOOL_PAPER: ("output", "school_paper"),
    OUTPUT_SCHOOL_WORKBOOK: ("output", "school_excel_workbook"),
    OUTPUT_FINANCE_CALCULATION: ("capability", "finance"),
})

OUTPUT_GUIDANCE = (
    "전공 표시 필요: 출력 요청에 major_id를 명시하고, 원답변 출처가 있는 "
    "common.major_id 사실을 gg.py apply로 정본에 등록한 뒤 다시 실행한다. "
    "작목명·표지·옛 출력으로 전공을 추정하지 않는다."
)


class OutputHeldError(MajorContractError):
    """Output refused before any return/staging/receipt (policy B).

    ``reason`` is the machine-checkable hold code for this instance.
    """

    reason = "output_held"

    def __init__(self, reason, message=None, **detail):
        self.reason = reason
        detail.setdefault("guidance", OUTPUT_GUIDANCE)
        super().__init__(message or reason, **detail)


class _Absent:
    """Marker for "no major named here" — distinct from an explicit null,
    which is an invalid ID (policy B treats a present null as invalid)."""

    def __repr__(self):
        return "ABSENT"


ABSENT = _Absent()


@dataclass(frozen=True)
class OutputContext:
    """What an output call hands the guard: a project root and, for tools
    without a spec, the explicit major.  It carries no permission — the
    guard recomputes every decision from the canonical record on disk, so
    extra caller-written flags (``allowed=True`` etc.) are never read.

    ``major_id`` defaults to ``ABSENT``; an explicit ``None`` is invalid."""

    project_root: object
    major_id: object = ABSENT


def output_context(project_root, major_id=None):
    """Context for an optional keyword/CLI argument: ``None`` there means
    the caller did not name a major (``ABSENT``), not an explicit null."""
    return OutputContext(
        project_root, ABSENT if major_id is None else major_id
    )


@dataclass(frozen=True)
class OutputAuthorization:
    """Result of a passed guard; recorded into receipts for lineage."""

    output: str
    major_id: str
    module_version: str
    contract_version: str
    project_revision: int
    binding_evidence: str

    def to_dict(self):
        return {
            "output": self.output,
            "major_id": self.major_id,
            "module_version": self.module_version,
            "contract_version": self.contract_version,
            "project_revision": self.project_revision,
            "binding_evidence": self.binding_evidence,
        }


def explicit_major_ids(spec):
    """Explicit major ids named by a spec: top-level ``major_id`` and
    ``school_profile.major_id``.  Returns ``[(where, value), ...]`` for
    every key that is present (a present null is kept — it is invalid,
    not missing)."""
    found = []
    if not isinstance(spec, dict):
        return found
    if "major_id" in spec:
        found.append(("spec.major_id", spec["major_id"]))
    profile = spec.get("school_profile")
    if isinstance(profile, dict) and "major_id" in profile:
        found.append(("school_profile.major_id", profile["major_id"]))
    return found


# Known cover/profile labels per major.  A label is never used to infer a
# major; it is only compared with the explicit ID so a request that names
# one major while its cover says another is refused (A32-L4).  Every
# registered peer (specialty_crops, industrial_insects, fruit_trees) is
# listed.  A new major module adds its labels here.
KNOWN_MAJOR_MARKERS = MappingProxyType({
    "specialty_crops": ("특용",),
    "industrial_insects": ("곤충", "insect"),
    "hort_env_systems": ("원예환경",),
    "fruit_trees": ("과수", "fruit"),
})

_MARKER_FIELDS = (
    ("spec.major", lambda spec, profile: spec.get("major")),
    ("spec.department", lambda spec, profile: spec.get("department")),
    ("school_profile.major", lambda spec, profile: profile.get("major")),
    ("school_profile.department",
     lambda spec, profile: profile.get("department")),
)


# Label matching.  Korean labels are compared after removing spaces and
# punctuation ("과 수전공" -> "과수전공"), with a seam after department /
# major suffixes so "...학과|수료" does not read as "과수".  Latin labels
# are whole words ("Fruit." -> "fruit", never "grapefruit").  A residual
# false positive (e.g. "원예과 수료") only refuses the output with a clear
# reason; the user fixes the label.  Labels never infer a major.
_NON_WORD = re.compile(r"[^0-9a-z\uac00-\ud7a3]+")
_SUFFIX_SPLIT = re.compile(r"(?<=학과)|(?<=학부)|(?<=전공)|(?<=계열)")


def _label_in(label, text):
    folded = text.lower()
    if label.isascii():
        words = [w for w in _NON_WORD.split(folded) if w]
        return label in words or label + "s" in words
    compact = _NON_WORD.sub("", folded)
    return any(label in part for part in _SUFFIX_SPLIT.split(compact))


def marker_conflicts(spec, major_id):
    """Cover/profile labels in ``spec`` that name a known major other than
    ``major_id``.  Returns ``[(field, other_major), ...]``.  It never
    infers a major on its own."""
    if not isinstance(spec, dict):
        return []
    profile = spec.get("school_profile")
    profile = profile if isinstance(profile, dict) else {}
    hits = []
    for field, read in _MARKER_FIELDS:
        text = read(spec, profile)
        if not isinstance(text, str):
            continue
        for other, labels in KNOWN_MAJOR_MARKERS.items():
            if other != major_id and any(
                _label_in(label.lower(), text) for label in labels
            ):
                hits.append((field, other))
    return hits


_SUFFIX_KINDS = MappingProxyType({
    ".xlsx": ("workbook", (OUTPUT_SCHOOL_WORKBOOK,)),
    ".xlsm": ("workbook", (OUTPUT_SCHOOL_WORKBOOK,)),
    ".xls": ("workbook", (OUTPUT_SCHOOL_WORKBOOK,)),
    ".docx": ("paper", (OUTPUT_SCHOOL_PAPER,)),
    ".hwpx": ("paper", (OUTPUT_SCHOOL_PAPER,)),
    ".hwp": ("paper", (OUTPUT_SCHOOL_PAPER,)),
    ".md": ("text", (OUTPUT_SCHOOL_PAPER,)),
    ".txt": ("text", (OUTPUT_SCHOOL_PAPER,)),
})
# Before an engine runs, a planned PDF takes PDF_ORIGINS[origin]; after it
# runs, output_kinds_for_file(pdf, pdf_origin=origin) checks the bytes.
PDF_ORIGINS = MappingProxyType({
    "paper": (OUTPUT_SCHOOL_PAPER,),
    "workbook": (OUTPUT_SCHOOL_WORKBOOK,),
    None: (OUTPUT_SCHOOL_PAPER, OUTPUT_SCHOOL_WORKBOOK),
})


_SNIFF_LIMIT = 64 * 1024 * 1024
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _ole_names(data):
    # Compound-file directory entries store stream names in UTF-16LE.
    return {
        name for name in ("FileHeader", "BodyText", "Workbook", "Book")
        if name.encode("utf-16-le") in data
    }


def _sniff_kind(path):
    """Document kind named by the file's own bytes: "pdf", "workbook",
    "paper", "text", or None when the bytes name no kind we can confirm
    (broken archives, unknown containers, ambiguous compound files)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(_SNIFF_LIMIT + 1)
    except OSError:
        return None
    if len(data) > _SNIFF_LIMIT:
        return None
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK"):
        # Every format marker inside the archive must agree; an archive
        # that claims two kinds (e.g. a workbook with an HWPX mimetype)
        # is refused rather than resolved by marker priority.
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                mimetype = (archive.read("mimetype").strip()
                            if "mimetype" in names else b"")
                types = (archive.read("[Content_Types].xml").decode(
                    "utf-8", "replace")
                    if "[Content_Types].xml" in names else "")
        except (OSError, KeyError, zipfile.BadZipFile, ValueError):
            return None
        claims = set()
        if mimetype == b"application/hwp+zip" or any(
                n.startswith("Contents/section") for n in names):
            claims.add("paper-hwpx")
        if "spreadsheetml" in types or "xl/workbook.xml" in names:
            claims.add("workbook")
        if "wordprocessingml" in types or "word/document.xml" in names:
            claims.add("paper-docx")
        if len(claims) != 1:
            return None
        return "workbook" if claims == {"workbook"} else "paper"
    if data.startswith(_OLE_MAGIC):
        names = _ole_names(data)
        hwp = bool(names & {"FileHeader", "BodyText"}) and \
            b"HWP Document File" in data
        xls = bool(names & {"Workbook", "Book"})
        if hwp and not xls:
            return "paper"
        if xls and not hwp:
            return "workbook"
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text"


def output_kinds_for_file(path, *, pdf_origin=None):
    """Output kinds a deliverable file needs, from its suffix AND bytes.

    Workbooks need the workbook output, paper files the paper output.  A
    PDF needs the kind of its known generator (``pdf_origin`` "paper" or
    "workbook"); a PDF of unknown origin needs both.  An unknown suffix,
    or bytes that contradict the suffix (e.g. workbook bytes renamed
    ``.docx``/``.dat``), are refused rather than defaulted."""
    suffix = Path(str(path)).suffix.lower()
    if pdf_origin not in PDF_ORIGINS:
        raise OutputHeldError(
            "unknown_output", "알 수 없는 PDF 출처 %r" % (pdf_origin,))
    if suffix == ".pdf":
        expected, kinds = "pdf", PDF_ORIGINS[pdf_origin]
    elif suffix in _SUFFIX_KINDS:
        expected, kinds = _SUFFIX_KINDS[suffix]
    else:
        raise OutputHeldError(
            "unknown_output", "출력 파일 형식을 판정할 수 없음",
            suffix=suffix)
    sniffed = _sniff_kind(path)
    if sniffed != expected:
        raise OutputHeldError(
            "unknown_output", "파일 내용과 확장자가 맞지 않거나 판별할 수 없음",
            suffix=suffix, sniffed=sniffed)
    return kinds


def companion_kinds(path, main_kinds):
    """Kinds a companion file needs when published with a main output.
    A ``.json`` receipt/manifest that parses as a JSON object is metadata
    about the main output and carries the main output's kinds (anything
    else named ``.json`` is refused); any other companion is a deliverable
    of its own and is judged by ``output_kinds_for_file``."""
    if Path(str(path)).suffix.lower() == ".json":
        try:
            with open(path, "rb") as handle:
                data = handle.read(_SNIFF_LIMIT + 1)
            record = (json.loads(data.decode("utf-8"))
                      if len(data) <= _SNIFF_LIMIT else None)
        except (OSError, UnicodeDecodeError, ValueError):
            record = None
        if not isinstance(record, dict):
            raise OutputHeldError(
                "unknown_output",
                "영수증·manifest companion이 JSON 객체가 아님",
                path=Path(str(path)).name)
        return tuple(main_kinds)
    return output_kinds_for_file(path)


def _normalize_context(context):
    if isinstance(context, OutputContext):
        return context
    if isinstance(context, dict) and "project_root" in context:
        return OutputContext(
            project_root=context["project_root"],
            major_id=context.get("major_id", ABSENT),
        )
    raise OutputHeldError(
        "output_context_required",
        "출력에는 프로젝트 정본 위치(context)가 필요함",
    )


def _requested_major(context, spec):
    named = explicit_major_ids(spec)
    if context.major_id is not ABSENT:
        named.append(("context.major_id", context.major_id))
    if not named:
        raise OutputHeldError(
            "major_id_required", "출력 요청에 명시 major_id가 없음"
        )
    for where, value in named:
        if not isinstance(value, str) or not value.strip() \
                or value != value.strip():
            raise OutputHeldError(
                "major_id_invalid",
                "%s 값이 유효한 전공 ID가 아님" % where,
                where=where,
            )
    values = {value for _, value in named}
    if len(values) > 1:
        raise OutputHeldError(
            "major_id_conflict",
            "명시 major_id가 서로 다름",
            named=[where for where, _ in named],
        )
    return values.pop()


def _load_for_output(context):
    try:
        return gg_core.load(context.project_root)
    except (OSError, ValueError, TypeError) as error:
        raise OutputHeldError(
            "project_unreadable",
            "프로젝트 정본(project.json)을 읽을 수 없음",
        ) from error


def authorize_output(output, context, *, spec=None, registry=None,
                     project=None):
    """Policy B guard.  Returns an ``OutputAuthorization`` or raises
    ``OutputHeldError`` (a ``MajorContractError``).

    ``context`` is an ``OutputContext`` (or ``{"project_root", "major_id"}``
    mapping).  ``spec`` is the request spec for spec-taking outputs; its
    ``major_id`` / ``school_profile.major_id`` and ``context.major_id`` must
    all agree and at least one must be present.  ``project`` is only for
    callers that already loaded the canonical record under the publication
    lock; everyone else lets the guard read ``project.json`` itself.
    """
    if output not in OUTPUT_REQUIREMENTS:
        raise OutputHeldError(
            "unknown_output", "알 수 없는 출력 종류 %r" % (output,),
            output=output,
        )
    context = _normalize_context(context)
    requested = _requested_major(context, spec)
    conflicts = marker_conflicts(spec, requested)
    if conflicts:
        raise OutputHeldError(
            "major_marker_conflict",
            "명시 major_id와 표지·프로필의 전공 표기가 다름",
            requested=requested,
            conflicts=[field for field, _ in conflicts],
        )
    registry = registry or default_registry()
    try:
        module = registry.resolve(requested)
    except MajorContractError as error:
        raise OutputHeldError(
            "unknown_major", "등록되지 않은 전공 %r" % requested,
            major_id=requested,
        ) from error
    if project is None:
        project = _load_for_output(context)
    try:
        binding = binding_from_project(registry, project)
    except MissingMajorError as error:
        raise OutputHeldError(
            "major_binding_required",
            "정본에 common.major_id 바인딩이 없음",
        ) from error
    except MajorContractError as error:
        raise OutputHeldError(
            "major_binding_invalid",
            "정본의 전공 바인딩이 유효하지 않음",
            binding_reason=error.reason,
            binding_detail=str(error),
        ) from error
    if binding.major_id != requested:
        raise OutputHeldError(
            "major_binding_mismatch",
            "명시 major_id와 정본 바인딩이 다름",
            requested=requested,
            bound=binding.major_id,
        )
    kind, name = OUTPUT_REQUIREMENTS[output]
    supported = (
        name in module.supported_outputs
        if kind == "output"
        else module.capabilities.get(name) == "supported"
    )
    if not supported:
        raise OutputHeldError(
            "unsupported_output",
            "전공 %r은 출력 %r을 지원하지 않음" % (module.major_id, output),
            major_id=module.major_id,
            output=output,
        )
    return OutputAuthorization(
        output=output,
        major_id=module.major_id,
        module_version=module.module_version,
        contract_version=CONTRACT_VERSION,
        project_revision=project["revision"],
        binding_evidence=binding.binding_evidence,
    )


def authorize_outputs(outputs, context, *, spec=None, registry=None,
                      project=None):
    """``authorize_output`` for every kind in ``outputs`` (deduplicated,
    order kept) — used when one request publishes several files."""
    seen = []
    for output in outputs:
        if output not in seen:
            seen.append(output)
    if not seen:
        raise OutputHeldError("unknown_output", "출력 종류가 없음")
    if project is None:
        # Read the canonical once so every authorization in the bundle is
        # issued against the same revision.
        project = _load_for_output(_normalize_context(context))
    return tuple(
        authorize_output(output, context, spec=spec, registry=registry,
                         project=project)
        for output in seen
    )


def reconfirm_output(authorization, context):
    """Re-read the canonical record just before a staged output is made
    visible.  Any change since ``authorize_output`` — a new revision, a
    removed or replaced binding — refuses with ``project_revision_changed``
    (the underlying hold code is kept in ``detail["cause"]``)."""
    context = _normalize_context(context)
    try:
        again = authorize_output(
            authorization.output,
            OutputContext(context.project_root, authorization.major_id),
        )
    except OutputHeldError as error:
        raise OutputHeldError(
            "project_revision_changed",
            "출력 준비 중 정본 개정 또는 바인딩이 바뀜",
            before=authorization.project_revision,
            cause=error.reason,
        ) from error
    if again != authorization:
        raise OutputHeldError(
            "project_revision_changed",
            "출력 준비 중 정본 개정 또는 바인딩이 바뀜",
            before=authorization.project_revision,
            after=again.project_revision,
        )
    return again


def _build_proposal(module, capability):
    if capability == "question":
        return {
            "fields": [
                {f.name: getattr(q, f.name) for f in fields(q)}
                for q in module.question_schema
            ],
            "answer_states": sorted(ANSWER_STATES),
        }
    if capability == "document":
        return {
            "sections": [
                {f.name: getattr(n, f.name) for f in fields(n)}
                for n in module.document_plan
            ],
            "precedents": [
                {"ref": p.ref, "kind": p.kind} for p in module.precedents
            ],
            "mandated_sections": [
                n.section_id
                for n in module.document_plan
                if n.required and not n.selectable
            ],
            "claims": (),
        }
    if capability == "evidence":
        return {
            "axes": {
                "acquisition": ACQUISITION_STATES,
                "observation": OBSERVATION_STATES,
                "rights": RIGHTS_STATES,
                "conflict": CONFLICT_STATES,
                "applicability": APPLICABILITY_STATES,
                "approval": APPROVAL_STATES,
            },
            "applicability": dict(module.evidence_applicability),
            "promotion_order": PROMOTION_ORDER,
        }
    if capability == "finance":
        return {
            "capabilities": [
                {f.name: getattr(c, f.name) for f in fields(c)}
                for c in module.finance_capabilities
            ],
            "envelope_fields": FINANCE_ENVELOPE_FIELDS,
            "blocks_document": False,
        }
    raise UnsupportedOutputError(
        "unknown capability %r" % capability, capability=capability
    )


def _computed_keys(value, hits):
    if isinstance(value, MappingProxyType) or isinstance(value, dict):
        items = value.items()
    elif isinstance(value, (tuple, list)):
        items = enumerate(value)
    else:
        return
    for k, v in items:
        if isinstance(k, str) and k in ("calculated", "computed", "result"):
            if v:
                hits.append(k)
        _computed_keys(v, hits)


def validate_proposal(module, proposal):
    """Common-side validation of a module proposal — fail closed."""
    if not isinstance(proposal, ModuleProposal):
        raise ProposalInvalidError("not a ModuleProposal")
    if proposal.canonical_write:
        raise ProposalInvalidError(
            "modules may not write the canonical record",
            major_id=proposal.major_id,
        )
    if proposal.major_id != module.major_id:
        raise ProposalInvalidError(
            "proposal/major mismatch",
            major_id=proposal.major_id,
            module=module.major_id,
        )
    if proposal.canonical is not None and not isinstance(
        proposal.canonical, MappingProxyType
    ):
        raise ProposalInvalidError(
            "canonical handoff must be a read-only view",
            major_id=proposal.major_id,
        )
    blob = json.dumps(_thaw(proposal.proposal), ensure_ascii=False)
    for term in module.forbidden_terms:
        if term in blob:
            raise ProposalInvalidError(
                "cross-major term %r in %s proposal"
                % (term, proposal.capability),
                major_id=module.major_id,
                term=term,
            )
    if proposal.capability == "document":
        payload = _thaw(proposal.proposal)
        if (
            "workbook" in payload or "sheets" in payload
        ) and "school_excel_workbook" not in module.supported_outputs:
            raise ProposalInvalidError(
                "workbook claim unsupported for this major",
                major_id=module.major_id,
            )
    if proposal.capability == "finance":
        if all(c.status == "unsupported" for c in
               module.finance_capabilities):
            hits = []
            _computed_keys(proposal.proposal, hits)
            if hits:
                raise ProposalInvalidError(
                    "unsupported finance emitted computed values",
                    major_id=module.major_id,
                    keys=hits,
                )
    return proposal


def route(registry, major_id, capability, *, output=None, canonical=None):
    """Select one explicit major and return its capability proposal.

    Fails closed on missing major, unknown major, unknown capability,
    or an output the module does not support.  When ``canonical`` is
    given, the module-facing handoff is a deep read-only view
    (``canonical_view``) carried on the proposal — modules return
    proposals, never writes.
    """
    module = registry.resolve(major_id)
    if capability not in CAPABILITY_KINDS:
        raise UnsupportedOutputError(
            "unknown capability %r" % capability, capability=capability
        )
    if output is not None and output not in module.supported_outputs:
        raise UnsupportedOutputError(
            "output %r unsupported for major %r" % (output, major_id),
            major_id=major_id,
            output=output,
        )
    view = None if canonical is None else canonical_view(canonical)
    status = module.capabilities.get(capability, "unsupported")
    proposal = ModuleProposal(
        major_id=module.major_id,
        module_version=module.module_version,
        contract_version=CONTRACT_VERSION,
        capability=capability,
        status=status,
        proposal=_freeze(_build_proposal(module, capability)),
        canonical=view,
    )
    return validate_proposal(module, proposal)


def capability_proposals(registry, major_id):
    """All four capability proposals for one bound major."""
    return {
        kind: route(registry, major_id, kind)
        for kind in CAPABILITY_KINDS
    }


def evidence_axes(
    *,
    acquisition,
    observation,
    rights,
    conflict,
    applicability,
    approval="unapproved",
):
    """Build a validated evidence-axes record (separate axes, no merge)."""
    conflict = CONFLICT_ALIASES.get(conflict, conflict)
    axes = {
        "acquisition": acquisition,
        "observation": observation,
        "rights": rights,
        "conflict": conflict,
        "applicability": applicability,
        "approval": approval,
    }
    valid = {
        "acquisition": ACQUISITION_STATES,
        "observation": OBSERVATION_STATES,
        "rights": RIGHTS_STATES,
        "conflict": CONFLICT_STATES,
        "applicability": APPLICABILITY_STATES,
        "approval": APPROVAL_STATES,
    }
    for axis, value in axes.items():
        if value not in valid[axis]:
            raise ValueError(
                "axis %s: %r not in %r" % (axis, value, valid[axis])
            )
    return MappingProxyType(axes)


def promotion_blockers(axes, audit_key=None):
    """Axes that block approval candidacy.

    An explicit ``audit_key`` is a physical-row identifier only — it
    confers no bypass over any axis.
    """
    blockers = []
    if axes["acquisition"] != "bytes_held":
        blockers.append("acquisition")
    if axes["observation"] != "verified_observation":
        blockers.append("observation")
    if axes["rights"] != "redistribution_confirmed":
        blockers.append("rights")
    if axes["conflict"] != "none":
        blockers.append("conflict")
    if axes["applicability"] != "applicable":
        blockers.append("applicability")
    return tuple(blockers)


def approval_candidate(axes, audit_key=None):
    return not promotion_blockers(axes, audit_key=audit_key)


def evaluate_applicability(module, candidate):
    """Whether a candidate observation applies to this major.

    A null-major (common pack) candidate is never auto-applicable — it
    stays ``unverified`` (a citation candidate, not a calculation
    input).  Another major's candidate is ``inapplicable``.
    """
    cand_major = candidate.get("major_id")
    if cand_major is not None and cand_major != module.major_id:
        return "inapplicable"
    rules = module.evidence_applicability
    if cand_major is None:
        return rules.get("common_pack_policy", "unverified")
    scopes = rules.get("use_scopes", ())
    if scopes and candidate.get("use_scope") not in scopes:
        return "unverified"
    return "applicable"


# ---------------------------------------------------------------------------
# Peer module declarations.  Neither imports nor references the other;
# pack ownership is verified against the shipped catalog at register.
# ---------------------------------------------------------------------------

_SPECIALTY_FORBIDDEN = (
    "곤충",
    "사육",
    "종충",
    "동애등에",
    "귀뚜라미",
    "분변토",
)

_INSECT_FORBIDDEN = (
    "특용작물",
    "본포",
    "토양관리",
    "재배기술",
    "재배 환경",
    "관수",
    "잡초방제",
    "농산물 가공",
    "수확 및 저장법",
)

_HORT_ENV_FORBIDDEN = (
    "곤충",
    "사육",
    "종충",
    "동애등에",
    "귀뚜라미",
    "특용작물",
)

SPECIALTY_CROPS_MODULE = declare_module(
    major_id="specialty_crops",
    module_version="1.0.0",
    capabilities={
        "question": "supported",
        "document": "supported",
        "evidence": "supported",
        "finance": "supported",
    },
    question_schema=(
        QuestionSpec(
            field_id="specialty_crops.crop_item",
            meaning="대상 작목(품목)",
            target="crop",
        ),
        QuestionSpec(
            field_id="specialty_crops.cultivation_area",
            meaning="재배 면적",
            unit="10a",
            target="crop",
        ),
        QuestionSpec(
            field_id="specialty_crops.region",
            meaning="재배 지역",
            target="crop",
        ),
        QuestionSpec(
            field_id="specialty_crops.cultivation_technique",
            meaning="재배 기술·관리 방법",
            target="crop",
        ),
        QuestionSpec(
            field_id="specialty_crops.processing_plan",
            meaning="가공·판매 계획",
            target="product",
        ),
    ),
    document_plan=(
        DocumentNodeSpec(
            "summary", "summary", selectable=False, required=True
        ),
        DocumentNodeSpec(
            "farm_status", "farm_status", selectable=False, required=True
        ),
        DocumentNodeSpec(
            "environment", "environment_analysis",
            selectable=False, required=True
        ),
        DocumentNodeSpec(
            "cultivation", "cultivation_technique",
            selectable=False, required=True
        ),
        DocumentNodeSpec(
            "processing_sales", "processing_sales",
            selectable=False, required=True
        ),
        DocumentNodeSpec(
            "finance", "finance_plan", selectable=False, required=True
        ),
        DocumentNodeSpec(
            "references", "references", selectable=False, required=True
        ),
    ),
    evidence_applicability=MappingProxyType(
        {
            "use_scopes": ("production_stat", "income", "econ_indicator"),
            "common_pack_policy": "unverified",
            "required_axes": (
                "acquisition",
                "observation",
                "rights",
                "conflict",
                "applicability",
            ),
        }
    ),
    finance_capabilities=(
        FinanceCapability(
            profile="single_annual_cash_v1",
            status="supported",
            reason="기존 단일 작목 연간 현금 산식 (gg_finance.calculate)",
        ),
        FinanceCapability(
            profile="composite_multi_crop",
            status="unsupported",
            reason="복합·가공·보조금 산식은 기존 계약상 미지원",
        ),
    ),
    validation_rules=(
        "namespace_fields",
        "no_foreign_pack_refs",
        "forbidden_terms_absent",
        "finance_status_literal",
    ),
    supported_outputs=(
        "question_list",
        "document_plan",
        "evidence_review",
        "school_paper",
        "school_excel_workbook",
        "finance_single_annual_cash_v1",
    ),
    packs=("mafra.specialty.production.2024",),
    forbidden_terms=_SPECIALTY_FORBIDDEN,
)

# industrial_insects product lines (0.2.0): one instance per product flow,
# declared by line_inventory; the stable id lives in the field id so the
# common question function counts every line (and plan year) separately.
# Line fields never inherit the project summary fields (different period
# and scope); see references/industrial-insects/README.md.
_INSECT_LINE_FIELDS = (
    ("label", "라인 이름(인스턴스 선언)", None),
    ("species", "라인 대상 곤충 종", None),
    ("purpose", "라인 용도(식용/사료/애완·학습/기타)", None),
    ("product_form", "라인 제품 형태", None),
    ("product_role", "라인 역할(main/byproduct/processed/service)", None),
    ("source_line", "부산물·서비스가 딸린 주 라인 id", None),
    ("input_line", "가공 라인의 원료 라인 id", None),
    ("sale_unit", "판매 단위", None),
    ("cycle_days", "한 회차 일수(입식→판매 가능)", "일"),
    ("cycles_per_year", "정상 가동 연도 연간 회차 수", "회/년"),
    ("first_sale_month", "첫 판매 예정 연·월", None),
    ("first_year_sold_cycles", "첫해 판매 완료 회차 수", "회"),
    ("stocking_input", "회차당 입식량", None),
    ("stocking_unit", "입식 단위", None),
    ("stock_source", "종충·알 확보 방식", None),
    ("survival_rate", "입식→판매 단계 생존율", "%"),
    ("survival_basis", "생존율 기준(개체 수/중량)", None),
    ("saleable_per_cycle", "회차당 외부 판매량", None),
    ("rearing_boxes", "사육 상자 수", "개"),
    ("box_tiers", "선반 단 수", "단"),
    ("density_per_box", "상자당 사육 밀도", None),
    ("feed_or_substrate", "먹이·배지 종류", None),
    ("feed_supply", "먹이·배지 조달 방식·월 소요량", None),
    ("channel", "라인 판로", None),
    ("unit_price", "판매 단위당 단가", "KRW"),
    ("price_basis", "단가 근거·확인 시점", None),
    ("processing_input_per_cycle", "가공 라인의 회차당 원료 투입량", None),
)
_INSECT_LINE_YEAR_FIELDS = (
    ("year_end_in_process", "그해 연말 사육 중 회차 존재 여부", None),
)

INDUSTRIAL_INSECTS_MODULE = declare_module(
    major_id="industrial_insects",
    module_version="0.2.0",
    capabilities={
        "question": "supported",
        "document": "supported",
        "evidence": "supported",
        "finance": "unsupported",
    },
    question_schema=(
        QuestionSpec(
            field_id="industrial_insects.species",
            meaning="대상 곤충 종",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.purpose",
            meaning="용도(식용/사료/애완·학습/기타)",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.product_form",
            meaning="제품 형태(알·종충·생충·건조·가공·부산물·서비스)",
            target="product",
        ),
        QuestionSpec(
            field_id="industrial_insects.cohort_or_batch",
            meaning="회차·배치 단위",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.cycle_days",
            meaning="사육 주기",
            unit="일",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.stocking_input",
            meaning="종충·알 등 입식 투입",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.survival_or_loss",
            meaning="생존율·폐사율",
            target="species",
        ),
        QuestionSpec(
            field_id="industrial_insects.saleable_yield",
            meaning="판매 가능 수량",
            target="product",
        ),
        QuestionSpec(
            field_id="industrial_insects.unit",
            meaning="계량 단위(kg·마리·g 난괴 등)",
            target="product",
        ),
        QuestionSpec(
            field_id="industrial_insects.facility_area",
            meaning="사육 시설 면적",
            unit="㎡",
            target="facility",
        ),
        QuestionSpec(
            field_id="industrial_insects.operating_days",
            meaning="연·월 가동일수",
            unit="일",
            target="facility",
        ),
        QuestionSpec(
            field_id="industrial_insects.feed_or_substrate",
            meaning="먹이·배지",
            target="input",
        ),
        QuestionSpec(
            field_id="industrial_insects.labor",
            meaning="노동 투입",
            target="input",
        ),
        QuestionSpec(
            field_id="industrial_insects.channel",
            meaning="판로·판매 행위",
            target="market",
        ),
        QuestionSpec(
            field_id="industrial_insects.price_date",
            meaning="가격 기준 시점",
            period="date",
            target="market",
        ),
        QuestionSpec(
            field_id="industrial_insects.regulation_check",
            meaning="종·용도·판매 행위별 법적 요건 확인",
            target="regulation",
        ),
        QuestionSpec(
            field_id="industrial_insects.claims_check",
            meaning="효능 표시·광고 규정 확인",
            target="regulation",
        ),
        QuestionSpec(
            field_id="industrial_insects.line_inventory",
            meaning="제품 라인 목록(선언된 라인 id)",
            target="line",
        ),
        QuestionSpec(
            field_id="industrial_insects.line_retired",
            meaning="폐기된 라인 id 목록",
            target="line",
        ),
        QuestionSpec(
            field_id="industrial_insects.plan_years",
            meaning="계획 연도 목록",
            target="period",
        ),
    ) + tuple(
        QuestionSpec("industrial_insects.line.{id}." + name, meaning,
                     unit=unit, target="line")
        for name, meaning, unit in _INSECT_LINE_FIELDS
    ) + tuple(
        QuestionSpec("industrial_insects.line.{id}.year.{yyyy}." + name,
                     meaning, unit=unit, target="line_year")
        for name, meaning, unit in _INSECT_LINE_YEAR_FIELDS
    ),
    # F0 and E1–E4 are equal-rank example_observed precedents — the
    # roster lives at module level; nodes cite no per-exemplar claim and
    # are all selectable/optional (no mandated section count).
    precedents=(
        PrecedentSpec("F0"),
        PrecedentSpec("E1"),
        PrecedentSpec("E2"),
        PrecedentSpec("E3"),
        PrecedentSpec("E4"),
    ),
    document_plan=(
        DocumentNodeSpec(
            "preface", "preface",
            rationale="종 선택 이유·사업 목적·근거 인용 서술 슬롯",
        ),
        DocumentNodeSpec(
            "farm_status", "farm_status",
            rationale="종 학명·제품 형태·생활사·사육 조건의 종별 슬롯",
        ),
        DocumentNodeSpec(
            "environment_analysis", "environment_analysis",
            rationale="내부 역량·외부 환경·모델 농장·전략 분리",
        ),
        DocumentNodeSpec(
            "vision_goals", "vision_goals",
            rationale="목표 연도와 생산/판매/투자 모델 일치 대조 지점",
        ),
        DocumentNodeSpec(
            "detailed_plan", "detailed_plan",
            rationale="자산·투자·생산·마케팅·재무 계획의 연결 슬롯",
        ),
        DocumentNodeSpec(
            "closing", "closing",
            rationale="계획·근거 요약 서술 슬롯",
        ),
        DocumentNodeSpec(
            "sources_appendix", "sources_appendix",
            rationale="인용 누락 검사·부록 선택 대상",
        ),
    ),
    evidence_applicability=MappingProxyType(
        {
            "use_scopes": (
                "industry_context",
                "technical_reference",
                "regulation_check",
            ),
            "common_pack_policy": "unverified",
            "required_axes": (
                "acquisition",
                "observation",
                "rights",
                "conflict",
                "applicability",
            ),
        }
    ),
    finance_capabilities=(
        FinanceCapability(
            profile="species_product_cycle_cash",
            status="unsupported",
            reason="종·제품·서비스·사육주기 산식 미검증 — "
            "시장 총액을 kg당 가격·농가 수율·학생 매출로 변환하지 않음",
        ),
    ),
    validation_rules=(
        "namespace_fields",
        "no_foreign_pack_refs",
        "forbidden_terms_absent",
        "no_workbook_output",
        "no_insect_calculation",
    ),
    # No rendered-paper claim: there is no insect document generator —
    # the document capability exposes the selectable plan only.
    supported_outputs=(
        "question_list",
        "document_plan",
        "evidence_review",
    ),
    packs=(),  # empty_slot preserved — no fabricated insect data
    forbidden_terms=_INSECT_FORBIDDEN,
)

_FRUIT_FORBIDDEN = (
    "곤충",
    "사육",
    "종충",
    "특용작물",
)

# Repeated groups (orchard blocks, planting cohorts) carry the instance in
# the field ID itself: ``fruit_trees.<group>.<stable_id>.<field>``.  The
# ``{id}`` entries below are templates — the fruit plan expands them per
# declared instance, so the common question/attempt contract works per
# instance without a new grammar.
_FRUIT_BLOCK_FIELDS = (
    ("label", "구역 표시 이름(선언)", None),
    ("area_m2", "구역 면적", "㎡"),
    ("ownership", "소유·임차·매입 구분", None),
    ("cultivation_form", "작형(노지/시설)", None),
    ("heating", "가온 여부(가온/무가온/해당없음)", None),
    ("protected_structure", "시설 구조", None),
)
_FRUIT_COHORT_FIELDS = (
    ("label", "식재집단 표시 이름(선언)", None),
    ("block_ref", "소속 구역 ID", None),
    ("origin", "신규식재/승계/갱신·보식", None),
    ("crop_species", "과종", None),
    ("cultivar", "품종", None),
    ("rootstock", "대목", None),
    ("planting_year", "식재 연도", "년"),
    ("age_basis_year", "수령 기준 연도", "년"),
    ("age_at_basis", "기준 연도의 수령", "년"),
    ("tree_count", "주수", "주"),
    ("spacing_m", "재식 거리(열간×주간)", "m"),
    ("bearing_status", "미성목/성목/갱신중", None),
    ("area_m2", "집단 식재 면적", "㎡"),
    ("active_from_year", "집단 활성 시작 연도", "년"),
    ("active_to_year", "집단 활성 마지막 연도(폐원·갱신 전)", "년"),
    ("tree_count_basis", "주수 근거(도면/실측/계획)", None),
    ("layout_note", "통로·시설·경계·수분수 반영 근거", None),
    ("yield_basis", "생산량 기준(per_tree/per_area)", None),
    ("yield_area_basis", "단수 분모(bearing_area/total_area)", None),
    ("bearing_trees", "연도별 결실 주수", "주"),
    ("yield_kg_per_tree", "연도별 주당 수확량", "kg/주"),
    ("bearing_area_m2", "연도별 결실 면적", "㎡"),
    ("yield_kg_per_10a", "연도별 10a당 수확량", "kg/10a"),
)
# Per-year cohort inputs: the year lives in fact.period ("YYYY"), so
# duplicates are judged per (field_id, period) for these fields only.
_FRUIT_YEARLY_FIELDS = ("bearing_trees", "yield_kg_per_tree",
                        "bearing_area_m2", "yield_kg_per_10a")
_FRUIT_BATCH_FIELDS = (
    ("label", "배치 표시 이름(선언)", None),
    ("origin", "배치 유형(harvest/opening/regrade/mix)", None),
    ("cohort_ref", "원 식재집단 ID(harvest)", None),
    ("crop_species", "과종(opening)", None),
    ("cultivar", "품종(opening)", None),
    ("harvest_year", "원물 수확 연도", "년"),
    ("opening_year", "기초 재고 기준 연도(opening)", "년"),
    ("grade", "등급", None),
    ("quantity_kg", "배치 수량(harvest/opening)", "kg"),
)
_FRUIT_MOVE_FIELDS = (
    ("label", "이동 표시 이름(선언)", None),
    ("batch_ref", "출발 배치 ID", None),
    ("kind", "이동 종류(sale/loss/own_use/process/experience/regrade/mix_in)",
     None),
    ("year", "이동 연도", "년"),
    ("quantity_kg", "이동 수량", "kg"),
    ("channel", "판로(sale)", None),
    ("target_batch", "도착 배치 ID(regrade/mix_in)", None),
)

_FRUIT_APPENDIX_ROLES = (
    "기초 자산", "중장기 목표", "투자", "원리금 상환", "판매",
    "생산·기반", "영농자재", "노동", "경비", "감가상각",
    "생산원가", "손익", "추정대차대조표", "현금흐름", "추정소득",
)

FRUIT_TREES_MODULE = declare_module(
    major_id="fruit_trees",
    module_version="0.2.0",
    capabilities={
        "question": "supported",
        "document": "supported",
        "evidence": "supported",
        "finance": "unsupported",
    },
    question_schema=(
        QuestionSpec("fruit_trees.business_start_year", "사업 시작 연도",
                     unit="년", period="year", target="plan"),
        QuestionSpec("fruit_trees.plan_end_year",
                     "검산 기간 마지막 연도(없으면 시작+9)", unit="년",
                     period="year", target="plan"),
        QuestionSpec("fruit_trees.school_template_edition",
                     "학교 양식 판본", target="plan"),
        QuestionSpec("fruit_trees.region", "과원 지역", target="site"),
        QuestionSpec("fruit_trees.site_note", "필지·입지 메모", target="site"),
        QuestionSpec("fruit_trees.crop_species", "과종(농장 기본값)",
                     target="farm_default"),
        QuestionSpec("fruit_trees.cultivar", "품종(농장 기본값)",
                     target="farm_default"),
        QuestionSpec("fruit_trees.rootstock", "대목(농장 기본값)",
                     target="farm_default"),
        QuestionSpec("fruit_trees.cultivation_form",
                     "작형(노지/시설, 농장 기본값)", target="farm_default"),
        QuestionSpec("fruit_trees.heating",
                     "가온 여부(농장 기본값)", target="farm_default"),
        QuestionSpec("fruit_trees.pollinizer", "수분수 계획", target="farm"),
        QuestionSpec("fruit_trees.sales_grade_scheme", "판매 등급 체계",
                     target="market"),
        QuestionSpec("fruit_trees.sales_channels", "판로", target="market"),
        QuestionSpec("fruit_trees.pest_control_registration_check",
                     "방제 약제의 최신 등록·안전사용 확인 상태",
                     target="regulation"),
        QuestionSpec("fruit_trees.climate_station_period",
                     "기상 관측소·분석 기간", target="site"),
        QuestionSpec("fruit_trees.research_handoff",
                     "등록된 연구 전달물 source ID", target="source"),
        QuestionSpec("fruit_trees.workbook.selection",
                     "현재 작업 XLSX 선택(inventory file_id 또는 none)",
                     target="source"),
    ) + tuple(
        QuestionSpec("fruit_trees.block.{id}." + name, meaning,
                     unit=unit, target="block")
        for name, meaning, unit in _FRUIT_BLOCK_FIELDS
    ) + tuple(
        QuestionSpec("fruit_trees.cohort.{id}." + name, meaning,
                     unit=unit, target="cohort",
                     period="year" if name in _FRUIT_YEARLY_FIELDS else None)
        for name, meaning, unit in _FRUIT_COHORT_FIELDS
    ) + tuple(
        QuestionSpec("fruit_trees.batch.{id}." + name, meaning,
                     unit=unit, target="batch")
        for name, meaning, unit in _FRUIT_BATCH_FIELDS
    ) + tuple(
        QuestionSpec("fruit_trees.move.{id}." + name, meaning,
                     unit=unit, target="move")
        for name, meaning, unit in _FRUIT_MOVE_FIELDS
    ),
    document_plan=(
        DocumentNodeSpec(
            "front", "front_matter", selectable=False, required=True,
            rationale="표지·제출·인준·요약·목차·표목차·그림목차. "
            "제출일과 졸업일은 별도 확인",
            evidence_ids=("FRT-S01",)),
        DocumentNodeSpec(
            "ch1_intro", "crop_selection", selectable=False, required=True,
            rationale="Ⅰ 선택 과종의 현황·선택 이유·기반조성·목표",
            evidence_ids=("FRT-S02",)),
        DocumentNodeSpec(
            "ch2_location", "site_conditions", selectable=False,
            required=True, rationale="Ⅱ-1 입지·교통·인구·출하 여건",
            evidence_ids=("FRT-S03",)),
        DocumentNodeSpec(
            "ch2_climate", "climate", selectable=False, required=True,
            rationale="최근 10년 월별 기후·서리. 미세먼지·자연재해는 "
            "원문상 연수 미지정 — 관측소·기간·결측을 기록",
            evidence_ids=("FRT-S04",)),
        DocumentNodeSpec(
            "ch2_soil", "soil", selectable=False, required=True,
            rationale="토양도 정보와 필지 토양검정을 구분",
            evidence_ids=("FRT-S05",)),
        DocumentNodeSpec(
            "ch2_industry", "industry_status", selectable=False,
            required=True,
            rationale="Ⅱ-2 국내외 면적·생산·수출입·가격. 단위·거래단계·"
            "기준기간 유지", evidence_ids=("FRT-S06",)),
        DocumentNodeSpec(
            "ch2_model_farm", "model_farm", selectable=False, required=True,
            rationale="Ⅱ-3 모델농장. 예시 농장 성과를 본인 사실로 쓰지 않음",
            evidence_ids=("FRT-S07",)),
        DocumentNodeSpec(
            "ch2_swot", "swot", selectable=False, required=True,
            rationale="Ⅱ-4 당면과제와 SWOT 대응",
            evidence_ids=("FRT-S08",)),
        DocumentNodeSpec(
            "ch2_policy", "policy", selectable=False, required=True,
            rationale="Ⅱ-5 지원정책. 공고 존재를 선정·대출 승인으로 쓰지 않음",
            evidence_ids=("FRT-S09",)),
        DocumentNodeSpec(
            "ch3_manager", "manager_profile", selectable=False,
            required=True,
            rationale="Ⅲ-1 경영주·실습·가족 역할. 인적 사항은 로컬 비공개",
            evidence_ids=("FRT-S10",)),
        DocumentNodeSpec(
            "ch3_cultivar", "cultivar", selectable=False, required=True,
            rationale="Ⅲ-2 품종·대목·수분 연계", evidence_ids=("FRT-S11",)),
        DocumentNodeSpec(
            "ch3_cultivation", "cultivation_plan", selectable=False,
            required=True,
            rationale="Ⅲ-3 작형·수형·재식·관수/시비·월별 재배력. 도면 주수와 "
            "이론 주수를 구분", evidence_ids=("FRT-S12",)),
        DocumentNodeSpec(
            "ch3_pest", "pest_control", selectable=False, required=True,
            rationale="Ⅲ-4 병해충·방제력. 옛 양식 약제 예시를 자동 채택하지 "
            "않고 최신 등록 확인 전 방제표 보류", evidence_ids=("FRT-S13",)),
        DocumentNodeSpec(
            "ch3_production", "production_plan", selectable=False,
            required=True,
            rationale="Ⅲ-5 연도별 생산량·단가·주/부산물 수입. 수령별 수량을 "
            "창작하지 않음", evidence_ids=("FRT-S14",)),
        DocumentNodeSpec(
            "ch3_marketing", "marketing", selectable=False, required=True,
            rationale="Ⅲ-6 등급×판로×시기 판매 전략",
            evidence_ids=("FRT-S15",)),
        DocumentNodeSpec(
            "ch3_investment", "investment", selectable=False, required=True,
            rationale="Ⅲ-7 과원·시설·농기계 투자와 재원·차입",
            evidence_ids=("FRT-S16",)),
        DocumentNodeSpec(
            "ch4_closing", "closing", selectable=False, required=True,
            rationale="Ⅳ 맺음말·참고문헌", evidence_ids=("FRT-S17",)),
        DocumentNodeSpec(
            "back_parent_consent", "parent_consent", selectable=False,
            required=True,
            rationale="학부모 동의서 — 합의·서명을 작성자가 만들지 않음",
            evidence_ids=("FRT-S17",)),
        DocumentNodeSpec(
            "back_ack", "acknowledgement", selectable=False, required=True,
            rationale="감사의 글", evidence_ids=("FRT-S17",)),
    ) + tuple(
        DocumentNodeSpec(
            "appendix_%02d" % (i + 1), "finance_appendix_role",
            selectable=False, required=True,
            rationale="재무 부록 역할 %d: %s (학교 10년 창, 계산 미지원)"
            % (i + 1, name), evidence_ids=("FRT-S17",))
        for i, name in enumerate(_FRUIT_APPENDIX_ROLES)
    ),
    evidence_applicability=MappingProxyType(
        {
            "use_scopes": (
                "industry_context",
                "technical_reference",
                "price_reference",
                "climate_soil",
                "policy_reference",
            ),
            "common_pack_policy": "unverified",
            "required_axes": (
                "acquisition",
                "observation",
                "rights",
                "conflict",
                "applicability",
            ),
        }
    ),
    finance_capabilities=(
        FinanceCapability(
            profile="perennial_orchard_cash",
            status="unsupported",
            reason="식재집단·수령·성목 전환·생물자산·10년 창 산식 미구현",
        ),
        FinanceCapability(
            profile="single_annual_cash_v1",
            status="unsupported",
            reason="다년생 과원을 연간 단작 산식으로 계산하지 않음",
        ),
        FinanceCapability(
            profile="school_17_sheet_v1",
            status="unsupported",
            reason="5년·annual 전용 생성기 — 과수 10년 부록과 다름",
        ),
    ),
    validation_rules=(
        "namespace_fields",
        "no_foreign_pack_refs",
        "forbidden_terms_absent",
        "no_workbook_output",
        "instance_field_ids",
    ),
    # Plan only: no paper generator, no workbook, no finance output yet.
    supported_outputs=(
        "question_list",
        "document_plan",
        "evidence_review",
    ),
    packs=(),  # empty_slot preserved — no fabricated orchard data
    forbidden_terms=_FRUIT_FORBIDDEN,
)

HORT_ENV_SYSTEMS_MODULE = declare_module(
    major_id="hort_env_systems",
    module_version="0.1.0",
    capabilities={
        "question": "supported",
        "document": "supported",
        "evidence": "supported",
        "finance": "unsupported",
    },
    question_schema=(
        QuestionSpec(
            field_id="hort_env_systems.business_type",
            meaning="사업 형태(생산·육묘·체험·가공 병행 여부)",
            target="business",
        ),
        QuestionSpec(
            field_id="hort_env_systems.startup_type",
            meaning="창업 유형(신규 창업·승계)",
            target="business",
        ),
        QuestionSpec(
            field_id="hort_env_systems.crop_item",
            meaning="대상 작목(품목)",
            target="crop",
        ),
        QuestionSpec(
            field_id="hort_env_systems.cultivar",
            meaning="품종",
            target="crop",
        ),
        QuestionSpec(
            field_id="hort_env_systems.product_unit",
            meaning="판매 계량 단위와 단위 규격(kg·판·본·상자 등)",
            target="product",
        ),
        QuestionSpec(
            field_id="hort_env_systems.region",
            meaning="사업지 시·군",
            target="site",
        ),
        QuestionSpec(
            field_id="hort_env_systems.site_area",
            meaning="부지 면적",
            unit="㎡",
            target="site",
        ),
        QuestionSpec(
            field_id="hort_env_systems.facility_type",
            meaning="시설 형식(단동·연동·광폭·유리/PET/PC·식물공장 등)과 "
            "내재해형 등록 규격명",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.facility_area",
            meaning="시설(온실) 면적",
            unit="㎡",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.cultivation_area",
            meaning="실제 재배 면적",
            unit="㎡",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.disaster_design_basis",
            meaning="사업지 적설심·풍속 기준 확인 여부와 출처",
            period="date",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.cultivation_system",
            meaning="재배 방식(토경·고형배지 수경·NFT·담액·분무경·고설 등)",
            target="cultivation",
        ),
        QuestionSpec(
            field_id="hort_env_systems.environment_control",
            meaning="환경제어·에너지 설비 범위(난방·냉방·커튼·CO2·양액 등)",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.water_energy_source",
            meaning="용수 수원과 에너지원",
            target="facility",
        ),
        QuestionSpec(
            field_id="hort_env_systems.crop_cycle",
            meaning="작기(정식~종료 월)와 연간 작기 수",
            target="cultivation",
        ),
        QuestionSpec(
            field_id="hort_env_systems.fiscal_year_mapping",
            meaning="작기와 회계연도(표 기준 축) 대응·첫해 부분년 처리",
            target="cultivation",
        ),
        QuestionSpec(
            field_id="hort_env_systems.yield_basis",
            meaning="단위면적당 생산량과 그 근거(조사값·견적·실측)",
            target="production",
        ),
        QuestionSpec(
            field_id="hort_env_systems.marketable_rate",
            meaning="상품화율",
            unit="%",
            target="production",
        ),
        QuestionSpec(
            field_id="hort_env_systems.growth_assumption",
            meaning="연도별 생산·가격 변화 가정과 근거",
            unit="%",
            target="production",
        ),
        QuestionSpec(
            field_id="hort_env_systems.channel",
            meaning="판로·거래 단계",
            target="market",
        ),
        QuestionSpec(
            field_id="hort_env_systems.price_basis",
            meaning="가격 근거(거래 단계·단위·조사 시점)",
            period="date",
            target="market",
        ),
        QuestionSpec(
            field_id="hort_env_systems.facility_quote",
            meaning="시설·설비 견적 근거",
            period="date",
            target="finance",
        ),
        QuestionSpec(
            field_id="hort_env_systems.funding_plan",
            meaning="자기자본·융자·보조 구성과 조건",
            target="finance",
        ),
        QuestionSpec(
            field_id="hort_env_systems.labor_plan",
            meaning="자가·고용 노동 계획",
            target="finance",
        ),
        QuestionSpec(
            field_id="hort_env_systems.workbook_selection",
            meaning="재무 참조 엑셀 선택(전공 교재 18시트 / 공용 17시트 / 기타)",
            target="finance",
        ),
    ),
    # HT1·HT2 are equal-rank example_observed precedents; the 3학년 발표
    # material is secondary observation and stays off the roster.  H-X1 is
    # a professor reference, not a precedent.
    precedents=(
        PrecedentSpec("HT1"),
        PrecedentSpec("HT2"),
    ),
    document_plan=(
        DocumentNodeSpec(
            "preface", "preface",
            rationale="지원 동기·사업 목적·계획 개요 서술 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "farm_status", "farm_status",
            rationale="농장 개요·생산 품목·재배 기술·생육장해/병해충 정리 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "environment_analysis", "environment_analysis",
            rationale="내부 역량·외부 환경·모델 농장·SWOT 분석 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "vision_goals", "vision_goals",
            rationale="비전·영농 목표·경영 전략 제시 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "investment_repayment", "investment_repayment",
            rationale="투자 계획과 원리금 상환 계획 연결 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "facility_plan", "facility_plan",
            rationale="입지·기반·구조·내재해 기준·설비·환경제어·에너지 "
            "계획 슬롯(NCS 0803/0804 체계)",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "production_plan", "production_plan",
            rationale="작기·연도 축을 명시한 생산 계획 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "sales_marketing", "sales_marketing",
            rationale="판로·판매 방법·홍보 전략 정리 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "cost_plan", "cost_plan",
            rationale="생산원가 산출 근거 정리 슬롯(HT2 본문 절)",
            precedent_refs=("HT2",),
        ),
        DocumentNodeSpec(
            "education_service", "education_service",
            rationale="교육 이수·봉사 활동 계획 슬롯(HT1·HT2 본문 절)",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "experience_processing", "experience_processing",
            rationale="체험 프로그램·가공 병행 사업일 때만 여는 슬롯",
            transform_reason="선배 전체논문에는 없는 절 — 3학년 발표"
            "(2차 관찰)의 체험·가공·승계 사례에서 도출, 해당 사업일 때만 선택",
        ),
        DocumentNodeSpec(
            "succession", "succession",
            rationale="승계 시 기존 자산·일정 인수 계획 슬롯",
            transform_reason="선배 전체논문에는 없는 절 — 3학년 발표"
            "(2차 관찰)의 체험·가공·승계 사례에서 도출, 해당 사업일 때만 선택",
        ),
        DocumentNodeSpec(
            "closing", "closing",
            rationale="계획 요약과 다짐 서술 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "references", "references",
            rationale="인용 근거 목록 정리 슬롯",
            precedent_refs=("HT1", "HT2"),
        ),
        DocumentNodeSpec(
            "appendix_workbook_tables", "appendix_workbook_tables",
            rationale="부록 18개 재무표 첨부 슬롯(H-X1 시트 대응, 선택형)",
            precedent_refs=("HT1", "HT2"),
        ),
    ),
    evidence_applicability=MappingProxyType(
        {
            "use_scopes": (
                "disaster_design_standard",
                "facility_spec_registry",
                "technical_reference",
                "income_comparison",
                "useful_life_reference",
                "industry_context",
            ),
            "common_pack_policy": "unverified",
            "required_axes": (
                "acquisition",
                "observation",
                "rights",
                "conflict",
                "applicability",
            ),
        }
    ),
    finance_capabilities=(
        FinanceCapability(
            profile="hort_18_sheet_workbook_v1",
            status="unsupported",
            reason="전공 교재 18시트의 입력칸 지도·수식 모순 처분·"
            "네이티브 재계산 미검증",
        ),
        FinanceCapability(
            profile="single_annual_cash_v1",
            status="unsupported",
            reason="연 1작기·단일 작목 산식은 다작기 회전·육묘(판)·"
            "화훼(본) 단위를 표현 못 함",
        ),
        FinanceCapability(
            profile="multi_cycle_facility_cash",
            status="unsupported",
            reason="작기×회계연도 이중 축 산식 미설계",
        ),
    ),
    validation_rules=(
        "namespace_fields",
        "no_foreign_pack_refs",
        "forbidden_terms_absent",
        "no_workbook_output",
        "no_hort_calculation",
    ),
    # No rendered-paper or workbook claim: the document capability
    # exposes the selectable plan only.
    supported_outputs=(
        "question_list",
        "document_plan",
        "evidence_review",
    ),
    packs=(),  # empty_slot preserved — no fabricated horticulture data
    forbidden_terms=_HORT_ENV_FORBIDDEN,
)

MODULES = (SPECIALTY_CROPS_MODULE, INDUSTRIAL_INSECTS_MODULE,
           FRUIT_TREES_MODULE, HORT_ENV_SYSTEMS_MODULE)


def _default_packs_dir():
    return (
        Path(__file__).resolve().parents[1]
        / "references"
        / "benchmark-packs"
    )


def load_pack_owner(catalog_path=None):
    """pack_id -> major_id map from the shipped catalog (None=common)."""
    path = (
        Path(catalog_path)
        if catalog_path
        else _default_packs_dir() / "catalog.json"
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {p["pack_id"]: p.get("major_id") for p in doc.get("packs", [])}


_DEFAULT_REGISTRY = None


def default_registry(catalog_path=None):
    """Registry of the peer modules bound to the shipped pack catalog."""
    global _DEFAULT_REGISTRY
    if catalog_path is None:
        if _DEFAULT_REGISTRY is None:
            _DEFAULT_REGISTRY = ModuleRegistry(
                MODULES, pack_owner=load_pack_owner()
            )
        return _DEFAULT_REGISTRY
    return ModuleRegistry(MODULES, pack_owner=load_pack_owner(catalog_path))
