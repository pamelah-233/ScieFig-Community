# ScieFig 1.2.3 for macOS

This branch contains the macOS 1.2.3 source based on the integrated 1.2.3 development source. The GitHub Actions build targets Apple silicon (arm64), macOS 14 or later, and includes the offline Cellpose worker and bundled models. The installer is retained as a workflow artifact; this branch does not publish a GitHub Release.

- The fluorescence spatial workflow uses saved nuclear segmentation for multi-marker phenotypes, nearest-neighbor distances, neighborhood statistics, and CSV export.
- The right sidebar allocates space for the updated summary, cell inspector, and legend before repainting when analysis finishes. Checked with the same tonsil dataset in narrow and native full-screen macOS windows.
- The full spatial results dialog formats table cells on demand and loads the detailed tabs on first use. On the local 6,223-object tonsil example, the median of three openings dropped from 0.86 s to 0.04 s in the same test environment. All five tabs, numeric sorting, and selection on the canvas were checked.
- Nearest-neighbor lines are displayed on the canvas. Image export does not yet include that line or its neighborhood circle.

See [the integrated 1.2.3 changes](UPDATES_1.2.3.md) and [model provenance](MODEL_LICENSE_REVIEW.md).
