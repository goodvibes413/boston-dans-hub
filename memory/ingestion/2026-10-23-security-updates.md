# Reproducible automation dependencies

[Source session](../source/2026-10-23-security-updates.md).

The existing Google SDK pin and persona/model choices are preserved. Requirements now pin transitive dependencies with hashes; workflows bootstrap hash-verified pip 26.2.2, enforce those hashes, and pin official GitHub Actions v6 commits. Tests validate the same locked dependency set. Job-specific write permissions remain only where publishing or issue management requires them. This change is prepared for review; no production run was requested.
