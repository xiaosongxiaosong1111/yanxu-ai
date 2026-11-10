# Gold input freeze repair

Scope: only `tools/evaluate_knowledge_gold.py`, `tests/test_knowledge_gold.py`, and the new historical fixture `tests/fixtures/knowledge_gold/public_bundle_manifest.json`. No production publication code, public bundles, runtime, credentials, original corpus/questions/texts/splits, or benchmark hash constants were edited by this task. No commit or full national publication was run.

## Problem and change

The previous loader compared the entire current public bundle manifest with the reviewed historical manifest SHA. An unrelated national catalog extension therefore prevented both gold and hybrid-gold test setup, even when every evaluated input stayed identical.

The historical manifest is now an exact frozen fixture, verified against the original `public_bundle_manifest_sha256` inside the unchanged freeze manifest. The loader then validates only its actual public inputs: 29 original documents and the complete `data/units.json` used for source attribution. For all 30 paths it checks:

1. A unique historical and current manifest entry exists; duplicate current paths are rejected in either order.
2. Current SHA and integer size equal the reviewed original metadata.
3. Expanded bytes match the reviewed size and SHA.
4. The current package stays inside `data/bundles`, has exactly one exact ZIP entry for the selected path, and that entry's size and raw SHA match the reviewed original.

Current dataset counters, unrelated package members, and changed package-level SHA/size caused by unrelated growth do not redefine gold. This is an input-specific benchmark guard, not a replacement for the full bundle release validator. A changed selected original cannot be made acceptable by updating the current manifest, ZIP and expanded bytes together.

The original `_read_original` and attribution/read logic remain in place. Development/holdout membership, the existing non-blind-holdout notice, source identities, expected quotes, 34 questions, 29 sources, and all existing metric thresholds remain unchanged.

## TDD evidence

- `red.log`: 8 initial new tests; 2 errors reproduce the old whole-manifest coupling for the current real bundle and unrelated growth.
- `red-archive-scope.log`: a separate test first proves the draft broad project-root archive boundary attempted to open `data/outside.zip`; this test failed before narrowing the archive root to `data/bundles`.
- `green.log`: all 11 new input-freeze tests pass, including manifest conflicts, both duplicate orderings, SHA/size/type changes, missing/tampered expanded originals and units, synchronized current-input tampering, changed/missing/duplicate ZIP members, malformed/missing archives, historical fixture tampering, and archive path escape.
- `regressions.log`: all 33 gold and hybrid-gold tests pass in 19.232 seconds. This includes the real 29-source extraction/index/bounded-read pipeline with socket networking forbidden by the integration test. No model or running application database is accessed.
- `coverage.json`: the three new helpers plus `load_fixture` have 37/37 statements and 16/16 pure branch outcomes covered (100%). The complete existing tool has 206/226 statements (91.15%), 67/88 pure branch outcomes (76.14%), 86.94% combined coverage. Remaining uncovered old-tool branches were not expanded for this narrowly scoped repair.
- `git diff --check` passes for the two changed Python files.

## Exact historical bytes and Git checkout

Historical source: `89dfac4:data/bundles/manifest.json`.

- Raw bytes: 1,578,525.
- Raw SHA256: `287bb81ab8e0893c8cf57fc817a489557e33c91d08611f64257a5a8e50f6112e`.
- Git blob SHA1: `5d0460d086f9ab294a163588b4e589e9dd57feff`.
- Line endings: LF, zero CRLF sequences.
- Existing `tests/fixtures/knowledge_gold/.gitattributes` contains `* -text`; `git check-attr text` reports `unset` for the new file. No attribute change is required.
- The source Git blob and new fixture bytes are identical. No normalization or JSON reserialization was used.

Unchanged corpus SHA256: `0b46b370bb75215fac0fa3c028a80309f37b28c300b0b36f7fa9cd04448edcd9`.

Unchanged freeze-manifest SHA256: `922ab5e31cfee1db239fedc990e3e99b948b9c320b2a0b9b3371b9cd5323c1ce`.

## Deliberate verification limits

This repair was tested against the accepted 136-unit / 22,630-direction public package and prepared files already present in ROOT. It does not rerun the complete Python suite, native BGE tests, full national publication, cold restore, deployed API, or browser. Those remain the root agent's release checks. The five prior BGE environment failures were not touched.

The complete `units.json` remains conservatively frozen, as requested. A future legitimate edit to that file still requires a separately reviewed gold-context change; this repair does not introduce a new per-unit snapshot scheme. Standalone fixture loading now verifies prepared originals and their public ZIP entries, so it requires the public data preparation prerequisite rather than treating manifest-only presence as proof of input integrity.
