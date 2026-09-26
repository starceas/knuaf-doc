# X01·X02 formula audit — A43 / XR-01–22

Sources are SHA-256 pinned. The XLSX files are read only; no corrected workbook is shipped.
Formula caches agree with this evaluator at the fixed input state. This does not certify
perturbation behavior or constitute Office recalculation.

## Coverage

| Source | Structural formulas | Evaluator supported | Cache agreeing | Unsupported syntax | Dynamic refs |
|---|---:|---:|---:|---:|---:|
| X01 `5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80` | 1643 | 1643 | 1643 | 0 | 0 |
| X02 `54d4e55cd57bcb1db0fbfaafe2ed42422767f1561959b8a4f3c673daf77d7e78` | 1713 | 1713 | 1713 | 0 | 0 |

The evaluator keeps an empty reference blank until consumed: arithmetic and
comparison coerce it to zero, while SUM/AVERAGE ignore it. For A1 empty and
A2=10, both `AVERAGE(A1,A2)` and `AVERAGE(A1:A2)` are 10;
`AVERAGE(A1+0,A2)` is 5. Microsoft documents that empty cells in
[AVERAGE cell-reference or range arguments](https://support.microsoft.com/en-us/excel/functions/average-function)
are ignored. [SUM](https://support.microsoft.com/en-us/excel/functions/sum-function)
accepts either individual cell references or ranges; the independent fixture
checks both forms.
typed errors propagate, `%` divides by 100, and PMT uses Excel argument order.
Cache tolerance: absolute 1e-07, relative 1e-09.

## Verdict counts

| Source | defect | normal_exception | policy_dependent | open |
|---|---:|---:|---:|---:|
| X01 | 10 | 1 | 4 | 30 |
| X02 | 9 | 2 | 4 | 27 |

## XR dispositions

The provenance of every XR row is `workbook_derived`; synthetic deltas are
recorded in the JSON evidence. A defect means its stated assumptions admit a
counterexample. An open item retains its reason and unresolved dependencies.

| ID | X01 | X02 | One-line reason |
|---|---|---|---|
| XR-01 | open | open | X01 first-year ratio yields a divide-by-zero error; X02 stores a constant; intended display remains unresolved. |
| XR-02 | defect | defect | Later rent subtotals repeat the first-year detail; a later-year delta does not reach its own subtotal. |
| XR-03 | defect | defect | Income-analysis rent references skip or repeat production-cost years. |
| XR-04 | policy_dependent | policy_dependent | The two surplus definitions use different revenue bases; final school row mapping is unresolved. |
| XR-05 | policy_dependent | policy_dependent | The displayed total omits the loan column under a total-investment interpretation; the label needs authority. |
| XR-06 | policy_dependent | policy_dependent | Fixed residual percentages and displayed residual inputs require an asset policy decision. |
| XR-07 | open | normal_exception | X01 opening survey year differs from balance opening; X02 aligns. |
| XR-08 | normal_exception | open | X01 has no biological-asset cohort in its J block; X02 J/K carry formulas omit later cohorts. |
| XR-09 | defect | defect | Later production-cost formulas omit annual inventory and transfer adjustments; C38 is blank. |
| XR-10 | open | open | Stored investment outflow cells may be intentional manual entries or stale links. |
| XR-11 | policy_dependent | policy_dependent | If family labor is unpaid, the cash formula includes a noncash economic cost. |
| XR-12 | open | open | A first-year expense has no cash or payable path; payment timing is unresolved. |
| XR-13 | open | open | Tax expense has no cash or payable path; payment timing is unresolved. |
| XR-14 | open | open | Later borrowing has no loan-tranche schedule; draw timing is unresolved. |
| XR-15 | open | open | Principal repayment has no cash or next-opening path; payment timing is unresolved. |
| XR-16 | defect | defect | An opening liability affects the opening position but disappears from forecast debt. |
| XR-17 | defect | defect | Opening asset input cells have no automatic path to forecast balance and depreciation. |
| XR-18 | defect | defect | The first-year fee reference is repeated across later cost years. |
| XR-19 | defect | defect | Later small-tool cost rows are disconnected and income analysis repeats the first year. |
| XR-20 | defect | defect | The contracted farming cost row has no dependency path to the expense subtotal. |
| XR-21 | defect | defect | Grant receipts are incompletely linked across items and years; accounting treatment remains open. |
| XR-22 | defect | normal_exception | X01 four stored prices versus X02 four carry formulas; +19 grant probe changes X01 balance difference by [0, −17.29 × 4], X02 by zero. |

## Finding impact

Counts are strict transitive formula dependents of every cell in each finding;
the finding cells themselves are excluded. Per-category counts and exact
dependent coordinates are in the JSON. W IDs are local to each source.

| ID | X01 dependents | X02 dependents |
|---|---:|---:|
| XR-01 | 0 | 0 |
| XR-02 | 105 | 104 |
| XR-03 | 23 | 23 |
| XR-04 | 0 | 0 |
| XR-05 | 0 | 0 |
| XR-06 | 236 | 315 |
| XR-07 | 0 | 0 |
| XR-08 | 0 | 203 |
| XR-09 | 55 | 54 |
| XR-10 | 22 | 22 |
| XR-11 | 27 | 27 |
| XR-12 | 29 | 28 |
| XR-13 | 22 | 21 |
| XR-14 | 27 | 27 |
| XR-15 | 258 | 258 |
| XR-16 | 23 | 23 |
| XR-17 | 0 | 0 |
| XR-18 | 105 | 104 |
| XR-19 | 138 | 137 |
| XR-20 | 64 | 63 |
| XR-21 | 281 | 330 |
| XR-22 | 177 | 177 |
| W-001 | 44 | 44 |
| W-002 | 24 | 24 |
| W-003 | 59 | 6 |
| W-004 | 53 | 28 |
| W-005 | 6 | 2 |
| W-006 | 45 | 27 |
| W-007 | 37 | 21 |
| W-008 | 29 | 16 |
| W-009 | 29 | 11 |
| W-010 | 2 | 6 |
| W-011 | 27 | 6 |
| W-012 | 6 | 18 |
| W-013 | 18 | 13 |
| W-014 | 13 | 6 |
| W-015 | 6 | 6 |
| W-016 | 6 | 36 |
| W-017 | 36 | 28 |
| W-018 | 28 | 20 |
| W-019 | 20 | 0 |
| W-020 | 0 | 7 |
| W-021 | 168 | — |
| W-022 | 7 | — |
| W-023 | 0 | — |

## Opening-liability discrimination (XR-16)

For a synthetic +7 at opening liability `1!E58`, the original formulas
yield opening equity Δ0, opening imbalance Δ−7 and forecast debt Δ0.
Replacing only `13!C28` with `C22-C25` and `13!D25` with
`C25+'4. 원리금상환계획'!F8` in memory yields Δ−7, Δ0 and Δ+7
respectively. The opening imbalance is `13!C22-C25-C28`.
These are deltas, not source values or Office recalculation.

## Rule scan and evidence limits

R1–R8 run over committed per-sheet blocks. Extra `W-nnn` structural candidates
remain open until fixed-reference, optional-blank, payment-timing and label
exceptions are checked; no candidate becomes a defect solely from a repeated
formula, blank, constant, cached zero, or balanced difference.

M01–M06 and M09 correspond to the workbook XR relations here. Fruit M07, M08,
M10 and M12 are `synthetic_math`; M11 is `external_source` (S04 PDF, manual).
They are not represented as workbook-derived findings. No contradictory
workbook path was found in the tested cases. XR-17 and XR-21 are conditional
defects here when inherited assets
and grants are actually carried/received; the related accounting policy remains
unresolved. XR-21 is independent of XR-22's X02 balance result.

The model-farm example sheet contributes metadata only. Shipped results include
formulas, coordinates and synthetic deltas; no example inputs or cache values.
