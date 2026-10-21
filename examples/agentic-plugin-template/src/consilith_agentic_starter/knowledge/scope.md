# Task preparation scope

The starter copies exact task text into a text output, combines it with review notes,
labels, metadata and duration into JSON, and serializes the same document to UTF-8 bytes.
No normalization is applied. The input text limit is 10,000 Unicode code points.
Output format all returns all three; text, structured and artifact return one each.
No numeric placeholder is needed. A checker reconstructs expected content from recorded
inputs and checks exact file hashes. This is software contract verification only.

The optional numeric format counts Unicode code points, including whitespace and combining
characters. It does not count bytes, words, visible glyphs or terminal columns.
Duration preserves its recorded unit and is metadata, not a computed scientific result.

Setting request metadata requestReview to true requests a proposal for the review owner.
The owner must accept the proposal; acceptance changes the snapshot and requires rerunning.
