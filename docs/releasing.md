# Release process

This project follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`).

- `MAJOR`: incompatible API, configuration, or behavior changes
- `MINOR`: backward-compatible new features
- `PATCH`: backward-compatible fixes and documentation corrections

## Process

1. Implement the change and test it on real target hardware where applicable.
2. Update the README and technical documentation.
3. Add the change under `Unreleased` in `CHANGELOG.md`.
4. Run syntax and plausibility checks.
5. Commit the tested state to `main`.
6. Move the relevant changelog entries from `Unreleased` to the new version.
7. Create an annotated Git tag and push it to GitHub.
8. Create a GitHub Release from that tag using the changelog notes.

Example:

```bash
git tag -a v1.2.0 -m "Release v1.2.0"
git push origin v1.2.0
```

A release should only mark a state that has been sufficiently tested on the intended real hardware.
