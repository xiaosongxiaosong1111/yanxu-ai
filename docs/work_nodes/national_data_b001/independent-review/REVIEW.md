# Final entry-boundary fix independent review

**Verdict: passed within the five reported defect families and the planned own-entry regression correction. No remaining P1/P2 found in this reviewed change.** The parent owns final full-suite, dependency audit and full-public-bundle CLI acceptance.

Scope: read-only review of `fix/national-b001-entry-boundaries` in `D:/CodexRuntime/yanxu-b001-recheck-0b4d8f6`, based on `0b4d8f6`. This reviewer made no production edits, network calls or runtime/Key access. Only external review artifacts and automatic TEMP originals were written.

## Final independently executed validation

- Final `tests.test_national_entry_regressions`: **48 passed, 0 failed**. See [final-regressions.txt](final-regressions.txt).
- Replayed **all 931 existing saved-original entry proofs** through the final `verify_entries`, including the real institution registry and known-name/host context: **400 verified / 531 rejected**.
- Compared every result with the `0b4d8f6` submitted queue: **zero status or rejection-reason changes**. Thus the six actual own-entry false rejections found during this implementation have been recovered without forcing a target count or changing adoption flags.
- Source hashes before and after the independent replay are equal. See [final-original-delta.json](final-original-delta.json) and [review-summary.json](review-summary.json).

## Structural and security checks

- No original function/class is missing. Forty-six original function/class ASTs remain unchanged; changes are confined to the planned HTML/URL/ownership helpers and audit source provenance.
- All three production modules stay below 800 lines. Baseline compatibility re-exports preserve existing direct-helper callers. No statistical, qualification, adoption or publication function changed outside audit provenance.
- `code_sha256` binds imported HTML/proof code plus the CLI with an explicit canonical LF policy. Legacy `module_sha256` remains available.
- Explicit owner identity no longer relies on shared name fragments. The final grammar delimits access/publication predicates and leading conjunctions, while preserving complete current names such as 和田学院 rather than blindly splitting name characters. Unknown longer names cannot use a contained current name as an alias.
- High-confidence token-pattern scans on changed Python source/test files found no actual API key, Tencent ID or GitHub-token-shaped literals. No new network, credential or user-data write path was introduced.

## Feedback closed during implementation

1. ASCII comma, semicolon and apostrophe initially split an outer URL and exposed an inner query URL as another declaration. Permanent RED cases and complete-token ranges now reject them while keeping independent wrapped URLs valid.
2. Inline `display:/**/none` initially escaped hidden detection. Comment/token handling now rejects that hidden value, keeps a hidden keyword inside only a comment visible, and does not merge invalid `no/**/ne` into `none`.
3. Six real institutions were briefly rejected because prose prefixes such as 将在/均可在/见/及 were inferred to be part of an institution name. The final rule treats those as syntax boundaries, preserves strict explicit-owner identity and restores those originals. See [OWNER_DELTA_DIAGNOSIS.md](OWNER_DELTA_DIAGNOSIS.md) for historical diagnosis.
4. The full target range now distinguishes ASCII URL tokens from a broader anti-nesting IRI envelope, and handles unmatched ASCII/fullwidth wrapping punctuation without swallowing a later independent URL. This recovers publication prose adjoining real URLs while retaining nested-URL rejection.

Earlier 11 independent synthetic end-to-end controls are preserved in [final-probes.json](final-probes.json); the final 48-test regression run covers their semantics plus the own-entry/Unicode-wrapper cases on final source.

## Boundaries

This remains a conservative saved-original replay checker, not a full browser executing external CSS or JavaScript. This review does not validate final regenerated fee/duration/adoption counts, full application deployment, or unrelated local embedding assets. The parent should report missing local embedding-model tests at their actual environment scope without modifying those tests to manufacture a pass.
