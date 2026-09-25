# ADR-0010 — Conservative M1 links, rename continuity, and compact FTS

Status: Accepted · Date: 2026-09-25

## Decision

Resolve only imports that uniquely identify an indexed local file. Rebuild import
edges after source changes so added and removed targets are reflected. Keep raw
import statements as file metadata; unresolvable external packages stay unlinked.

Mine the latest 200 Git commits and map changed line ranges to the narrowest
current symbols covering those lines. Skip commits mapping to more than 20
symbols, and require at least two shared commits with at least 50% support
against both symbols' observed changes. Cap each symbol at eight co-change
peers. Historical line numbers are approximate against the current tree, so
these edges are ranking hints rather than proof of a dependency. A shallow
history may produce no edge. On pinned Django with 200 commits this produced
44 directed edges; with one shallow commit it produced none.

Mask a declaration name when hashing its normalized body. If one old and one new
symbol uniquely share that hash and the old location disappeared, move matching
episodic anchors to the new path/name/span hash. A body edit, copy, or ambiguous
match does not move a fact. Structural nodes remain rebuildable and may get new
IDs; episodic node IDs and provenance stay intact.

Use contentless FTS5 in schema v3. The nodes table remains the sole stored copy
of previews; FTS retains token postings and rowids. Migrate v1/v2 indexes in
place, preserve episodic nodes, then VACUUM once to reclaim the old text copy.
The stable 0.1.2 index of pinned Django is 125.6 MB against 45.1 MB of source
(279%), below ADR-0007's 400% target. Full indexing took 25.4 seconds and an
unchanged reindex took 1.6 seconds on the release machine.

## Consequences

- Import and co-change coverage is conservative. More precise module and
  historical symbol resolution can be justified by M2 retrieval evaluation.
- Rename continuity is intentionally withheld for duplicate bodies and edits
  that change the body alongside the name.
- The v3 migration rewrites FTS and compacts the database once. Normal indexing
  remains incremental and an unchanged reindex does not load all symbols.
- Search uses FTS rowid joined to nodes; text ranking and secret exclusion remain
  covered by tests.
