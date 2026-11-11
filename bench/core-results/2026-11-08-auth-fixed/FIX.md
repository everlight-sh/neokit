# Authenticated rendering: reusable Neo fixes

The observed 8.2% regression was measured against a saved pre-async prepared
dashboard executable. The fix changes Neo's compiler and JSON standard library;
the NeoKit dashboard's application/authentication implementation is unchanged.

## Compiler budgets

Profiles and retained LLVM input showed that the regressed build had lost
forced inlining for JSON, escaping and hashing helpers. The old expansion guard
removed a helper's annotation globally when a large caller exceeded its budget.
The expanded runtime could therefore change unrelated application code.

Neo now constrains expensive caller-to-helper edges with call-site `noinline`,
preserving the helper's policy in other callers. The lowered body-size guard and
explicit inline requests are retained. The conservative library-object budget
is applied only when preparing the object. Optimization hints are saved before
those local constraints and before LLVM optimization; application specialization
uses its configured expansion budget. Both object input and hint input are part
of the cache key. Their checksums and checked ownership/effect facts remain
validated on reuse.

The fix applies in dependency-object preparation and application hint merging.
It has no framework names, benchmark routes or auth-specific compiler rules.
[Neo's budget documentation](../../../../axon/docs/prepared-library-inlining.md)
and [compiler regression checks](../../../../axon/tests/test_inline_cost.py).

## JSON string decoding

Escaped strings previously advanced byte by byte, creating temporary arrays
and repeatedly appending decoded text. The shared `json_text` implementation now
measures decoded length, allocates one exact-sized output buffer and copies
ordinary text in runs. Escapes retain the existing control-byte/UTF-8/surrogate
semantics. Unicode escapes still use the shared UTF-8 encoder. The escape-free
string path is unchanged.

The decoder retains checked copies/writes and verifies the final output length.
It does not introduce a parsed-data cache or remove JSON validation. Differential
corpora compare against Python JSON and existing independent accessors across
2,385 cases, including long runs, dense escapes, controls, Unicode boundaries,
duplicate/escaped keys, aliases, nested values and invalid inputs.

## Evidence

The authentication benchmark still reads the 120-task file, filters 100 rows by
owner, verifies sessions and renders escaped output without a response cache.
Before and after every timing trial, its independent validator checks rows,
UTF-8/escaping, CSRF and authorization behavior plus a durable mutation.

Seven rounds shuffle all four builds within each round: pre-async control,
regressed async control, fixed prepared and fixed monolithic. This reduces
host-load drift; it does not eliminate the shared Mac's scheduling/thermal
noise. See [all 28 authentication trials](PREPARED.md), [raw measurements and
binary identities](prepared-results.json), [workspace identities](identities.json)
and [functional verification](VALIDATION.md). Earlier diagnostic candidates are
kept separately under the ignored build directory and are not the final result.
