---
type: Attested Computation
title: Compatibility needle
tags: one,two
status: deprecated
superseded_by: replacement.md
runtime: sql
parameters: {name: year, type: integer}
computation: query.sql
executor: {resource: runner.md}
attester: {resource: attest.py}
resource: replacement.md
sources: {id: policy, resource: replacement.md, author: 'team:finance'}
generated: {by: worker/1, at: 2026-11-25}
verified: {by: 'team:finance', at: 2026-11-26}
stale_after: 2099-01-01
x-extension: {preserve: true}
---

See [replacement](replacement.md), [README](README.md), and [missing](missing.md).

Policy.[^policy]

[^policy]: Policy reference.
