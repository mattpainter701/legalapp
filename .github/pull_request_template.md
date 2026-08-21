## Summary

Describe the user, operational, or security outcome of this change.

## Validation

List the focused checks you ran and any relevant manual verification.

## Merge policy attestations

- [ ] Documentation updated
- [ ] No documentation impact
- [ ] Customer release notes updated
- [ ] No customer-facing release note
- [ ] Security and privacy impact reviewed

Select exactly one documentation option and exactly one customer release-note
option. If customer release notes changed, update
`backend/app/release_notes.json`, regenerate `RELEASE_NOTES.md`, and add the
technical detail to `CHANGELOG.md`. If this change updates a dependency,
container image, Compose image, or AI route, regenerate the SBOM inventory with
`python scripts/generate_sbom_inventory.py`.
