# Open-source preparation — 2026-10-27

> **2026-10-28:** The maintainer chose AGPL publication with the approved model
> weights and full functionality. The source and installer are separately verified;
> the installer release workflow remains disabled pending a verified build.

Maintainer/copyright identity: **pamelah-233**. Application baseline: **1.2.2**.
This is a preparation revision, not a replacement of the published 1.2.2 tags,
source archives, or installers. No new public repository or installer is created
by this document.

## Protection and permitted reuse

- Original application code: **AGPL-3.0-only**, with the complete unmodified GNU
  license text in `LICENSE` and attribution in `NOTICE`.
- AGPL preserves source-availability obligations when applicable, including
  modified network-service versions. It permits commercial use and lawful forks;
  it does not stop copying, independently written alternatives, or all misuse.
- `TRADEMARKS.md` distinguishes official branding from unofficial distributions.
  No trademark registration, copyright registration, or guaranteed enforcement
  is claimed.
- Contributions retain their authors' copyright. `CONTRIBUTING.md` records the
  required license and right-to-submit certification; it does not silently grant
  future proprietary relicensing rights.
- Scientific outputs and user images are not automatically licensed as AGPL
  merely because ScieFig generated or processed them. Their own rights apply.

## Completed preparation

- Community source startup no longer requests Windows device activation.
  Historical activation support and old installers are preserved privately.
- Application copyright, license, third-party boundaries, brand guidance,
  contribution policy and private security-reporting guidance prepared.
- Portable local-fixture environment variables replace institution/user paths in
  the two directly affected tests.
- A source exporter includes application/build/test source and explicit public
  documentation; it excludes private history, internal handoffs/design archives,
  research exports, signing material, and unapproved model-weight binaries. Four
  approved local weight files are included with separate notices.
- Public-source checks stop on detected private paths/credentials, source links
  leaving the reviewed tree, missing license files, or an existing destination.
- Private Git history scan covered **142 reachable commits** with Gitleaks 8.30.1.
  Nine generic-key alerts were reviewed as model identifiers/test assertions;
  no actual credential was confirmed. This is a bounded scan, not proof that
  every possible sensitive datum has been identified.

## Targeted verification

- The community source entrypoint reached a real main window using isolated,
  empty settings, without importing activation support; startup and exit returned 0.
- Thirty-one source-export, startup and packaging-rule checks passed; two
  directly changed portable-fixture checks also passed.
- Four approved model archives had their pickle metadata inspected without
  executing it; the checked private path patterns were not found.
- Exact source locations were recorded for PyQt6, PyQt6-sip, PyMuPDF, Qt and
  MuPDF. The official Qt/MuPDF source archives responded successfully.
- The Windows community recipe verifies the frozen main window before installer
  compression, uploads directly from the runner, and refuses replacement of an
  already published community release. It has not been dispatched yet.
- Mac packaging version metadata follows the application constant and includes
  the same notices. A native Mac community installer has not been built/tested
  on this Windows machine.

## Required before publishing complete model-enabled installers

- [x] Record upstream author clarification applying BSD3 to stock weights, the
  pinned model/data declarations, and the unresolved data-rights interpretation.
  See `docs/MODEL_LICENSE_REVIEW.md`; do not promise unrestricted commercial rights.
- [x] Maintainer pamelah-233 confirmed public distribution rights for the three
  local adaptations on 2026-10-27. Original images, annotations and validation
  archives remain private.
- [x] Preserve TRUEFAD model-specific MIT attribution, author RDF and conversion
  metadata; distinguish these from the repository macros' GPL terms.
- [ ] Verify actual application/worker/Qt/CUDA/OCR package notices and required
  corresponding source; a list of URLs alone is not blanket license clearance.
- [ ] Build community installers from the reviewed public snapshot, install and
  launch them, and check version, activation-free startup and the chosen model
  execution paths. Do not rename old activated installers as community builds.

Current weight-publication status is machine-readable in
`packaging/models/PUBLIC_MODEL_STATUS.json`. Working private models and installed
software are not removed or replaced to make the public snapshot look complete.

## Public repository boundary and final handoff

Prepare a **new repository from the clean snapshot**, rather than exposing the
existing private repository's history and release attachments. The private
repository contains research/validation archives that must stay private.

Review the generated `PUBLIC_SOURCE_MANIFEST.json`, run the public source's
targeted checks and scan, choose the public repository name and a distinct
community release tag, and enable private vulnerability reporting and available
secret/push protection. Protect the default branch and require reviewed changes
where the account plan supports it. Public CI uses read-only token permissions.

Publish a new model-enabled installer only after its package and installation
checks above pass. Keep the upstream data-rights caveat in every model-enabled
distribution. Public code licensing does not erase rights in training data.
