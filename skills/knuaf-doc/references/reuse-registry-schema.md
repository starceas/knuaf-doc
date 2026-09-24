# Reuse registry schema — `p5-reuse-registry/3`

Restatement of the accepted stage-g006 v19 design (frozen `SCHEMA.md`
sections 1–6, `API.md` section 1, `SPEC.md` section 2). This document
**restates** the accepted contract; it defines no variant. Where this
file and the frozen documents differ, the frozen documents govern.

`builtin-sources.json` (this directory) is the shipped registry document.

## 1. Document shape (SCHEMA §1)

```json
{ "schema_version": "p5-reuse-registry/3",
  "classifier_version": "<str>",
  "artifact_base": { "kind": "installed_runtime",
                     "declaration": { "relative_root": <str>,
                                      "marker": <str> } },
  "entries": [ <entry>, ... ],
  "p4_trust": { "packs_dir": { "relative_root": <str>, "marker": <str> },
                "packs": { "<pack_id>": { "records_relpath": <str>,
                                          "records_file_sha256": <64-hex> } } } }
```

- `schema_version` MUST be exactly `p5-reuse-registry/3`. A loader
  expecting a different schema reports the observed token — it never
  silently reinterprets.
- `artifact_base.kind` is `installed_runtime`, never `workspace` — the
  registry describes the deployment's own installed artifact base, not
  the authoring workspace.
- `registry_digest` = `sha256(canonical_json(document))` where
  `canonical_json` is UTF-8, `sort_keys`, compact `(",", ":")`
  separators, `ensure_ascii=False` (SCHEMA §7.4). The digest covers the
  **document only** — resolved absolute paths are runtime transport
  values and never enter it (SCHEMA §2, §6.1).

## 2. Portable resolution (SCHEMA §2)

`load_registry(registry_path)` is the ONLY producer of the typed
`RegistryResolution` transport:

- `resolved_base` = normalized join of the **registry file's own
  directory** with `artifact_base.declaration.relative_root`; the
  declared `marker` must exist at the resolved root — fail closed, no
  fallback, no search.
- `resolved_packs_dir` = same rule applied to `p4_trust.packs_dir`
  (`null` iff the registry declares none).
- The serialized document stays portable; absolute bases exist only on
  the constructed object.
- **Rule 3**: `resolve_reuse` compares `context["runtime_root"]` (the
  deployment's own base, bound by the caller) against
  `registry.resolved_base`. A mismatch is `blocked /
  runtime_base_mismatch`. `selection.byte_source.root` is never an
  operand of this check.

## 3. Entries (SCHEMA §3)

```json
{ "source_id": "<^[a-z0-9][a-z0-9._-]{0,63}$>",
  "class": <str>, "role": <role>,
  "source_sha256": [<64-hex>, ...],
  "runtime": <str|null>, "runtime_present": <bool>,
  "artifacts": [ { "relative_path": <str>, "sha256": <64-hex>,
                   "bytes": <int> } ],
  "coverage": { "<kind>": { "keys": [ { "key_ref": <key_ref>,
      "authority": "verified|unverified|rejected",
      "catalog_digest": <64-hex|null>,
      "receipt_sha256": <64-hex|null> } ] } },
  "lineage": [ { "to": <artifact relative_path>,
                 "relationship": "derived_excerpt|derived_snapshot|
                                  verbatim_copy|shared_artifact",
                 "verification": "verified|unverified|rejected",
                 "verifier": <str|null>,
                 "receipt_sha256": <64-hex|null> } ],
  "build_allowlist_ref": <str|null> }
```

- A `statistical_pack` entry's `artifacts[]` are relative to
  `resolved_packs_dir` (the §6.1 packs root), not the §2 artifact base,
  and MUST contain the pack's `records.jsonl` with `sha256` equal to the
  pinned `records_file_sha256`. Every other entry's artifacts are
  relative to `resolved_base`.
- Role → admissible `requested_use.kind` (§3.2):
  `primary_finance_template → template_structure`;
  `secondary_finance_exemplar → template_structure, narrative_reference`;
  `official_guideline → writing_rule`;
  `narrative_exemplar → narrative_reference`;
  `statistical_pack → statistical_observation`;
  `reference_only → none`.
- **Non-inheritance**: coverage and verification claims bind to their
  own entry. Two entries sharing one physical artifact inherit nothing
  from each other.
- Effective authority for a key = the weaker of the registry coverage
  claim and the key-catalog claim (precedence `rejected` > `unverified`
  > `verified`, §3.3/§5.1). A key claimed in `coverage` but absent from
  the key catalog is `authority_conflict`.

## 4. Key identity (SCHEMA §4)

- `statistical_observation` keys are structured AuditKeys:
  `{ "kind": "audit_key", "pack_id", "records_file_sha256",
     "physical_jsonl_line_1based", "raw_line_sha256" }` on the declared
  P5 domain — `pack_id` matches the source-id grammar, hashes are
  lowercase 64-hex (uppercase rejected, never lowercased), the line
  number is an `int >= 1` with **explicit bool rejection**. `pack_revision`
  and `record_id` are metadata, never identity substitutes.
- Other kinds use the delimited form `{ "kind": "delimited",
  "value": "<id>:<id>" }` with `<id> = [a-z0-9][a-z0-9._-]{0,63}`.
- Requested key lists are non-empty, canonically ordered (AuditKey tuple
  order / value-string order) and duplicate-free.
- Canonical serialization (§4.3): sorted canonical JSON of the four
  identity fields for statistical keys; the plain `value` for delimited
  keys. No unit-separator joins for statistical keys.

## 5. Key catalog (SCHEMA §5)

`context["key_catalog"]` = `{ "<kind>": { "<canonical-key>":
{ "authority": "verified|unverified|rejected",
    "catalog_digest": <64-hex|null>,
    "receipt_sha256": <64-hex|null> } } }`.

## 6. Catalog trust (SCHEMA §6)

- `context["packs_dir"]` is the caller's packs-root claim and MUST equal
  `registry.resolved_packs_dir`; a mismatching claim is a malformed
  context (`ValueError` naming the field), never a verdict.
- `load_accepted_catalog` is the only trusted-object construction:
  `p5-catalog-export/1` → per-key §4.3 deserialization → verified-index
  reconstruction per declared pack → `accept_catalog` with the export's
  named pinned authority. All failures raise `ValueError`; the caller
  owns the mapping (`blocked / catalog_not_accepted` at the resolver;
  `not_committed / adoption_context_invalid` at registration).
- The serialized export's `entries` map the §4.3 canonical key
  serialization to the accepted catalog's entry objects verbatim —
  the pinned authority digest authenticates the whole content, so the
  exported values must be the accepted rows exactly.
- Statistical trust (§6.4): live `.accepted`, live `.digest` equality
  with `context["catalog_digest"]`, index membership, and a
  `verified_observation` entry with a complete receipt — all four, all
  live.
