# Third-party licenses and model boundaries

Original ScieFig application code: **AGPL-3.0-only**, copyright 2026
**pamelah-233 and contributors**. Other people's code, weights, and data retain
their own terms. This file is a verified inventory of the principal components,
not a claim that every supplied artifact is covered by AGPL.

| Component | Applicable terms / action |
| --- | --- |
| PyQt6 | GPLv3 or a separate Riverbank commercial license. Community distribution follows the GPL-compatible route. |
| Qt distributed with PyQt | Qt component licenses apply separately; preserve the actual Qt notices and corresponding-source obligations. |
| PyMuPDF / MuPDF | AGPLv3 or a separate Artifex commercial license; use the AGPL-compatible route for this community source. |
| Cellpose 3.1.1.3 code | BSD-3-Clause; pinned upstream notice is in `LICENSES/Cellpose-BSD-3-Clause.txt`. |
| Cellpose stock/adapted weights | Upstream author clarification in issue 192 applies BSD3 to the pretrained files. The pinned v3.1.1.3 README also flags non-commercial training data; the upstream author describes possible inherited data restrictions as a gray zone. Local adaptation distribution is authorized by the maintainer. See `docs/MODEL_LICENSE_REVIEW.md` and the model BSD notice. |
| TRUEFAD converted weights | The supplied model RDF declares MIT. The author repository's macros use GPLv3; ScieFig does not claim those macros are MIT. Conversion metadata, the original author list, and the model MIT notice are preserved with the converted weights. |
| Python | PSF terms; retain the Python license in binary distributions. |
| NumPy, SciPy, pandas, scikit-image, scikit-learn, matplotlib, statsmodels, Pillow, openpyxl, fontTools and image readers | Retain each pinned distribution's actual license and notice material. Do not substitute this inventory for their license texts. |
| Tesseract and traineddata | Preserve the supplied OCR runtime and traineddata notices; Tesseract is Apache-2.0. |
| Lucide / Feather-derived SVG icons | Preserve `LICENSES/Lucide-LICENSE.txt`, including the upstream ISC/MIT attributions. Project branding does not replace these notices. |
| PyTorch and NVIDIA CUDA libraries | Preserve PyTorch and bundled NVIDIA terms. NVIDIA binary components are not relicensed as AGPL or represented as open-source weights. |

## Primary references checked on 2026-10-27

- PyQt: https://www.riverbankcomputing.com/software/pyqt/intro
- PyMuPDF: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- Cellpose pinned license: https://github.com/MouseLand/cellpose/blob/v3.1.1.3/LICENSE
- Cellpose weight-license clarification: https://github.com/MouseLand/cellpose/issues/192
- Cellpose pinned model/data note: https://github.com/MouseLand/cellpose/blob/v3.1.1.3/README.md
- TRUEFAD provenance: `packaging/models/truefad_source_rdf.yaml` and
  `packaging/models/truefad_myotube.json`; https://github.com/AurBrun/TRUEFAD
- AGPLv3: https://www.gnu.org/licenses/agpl-3.0.html

The upstream note about training data is a reason for clarification, not a
legal conclusion that the software or all resulting weights inherit CC-BY-NC.
Do not advertise unrestricted commercial model rights until this is resolved.

Version-specific source directions for copyleft components are in
`docs/DEPENDENCY_SOURCES.json`; publish this file alongside the binary and recheck
the versions for each actual build. GNU AGPLv3 section 6(d) allows another server
for corresponding source with clear directions and continuing availability;
the distributor remains responsible for that availability. No private signing
key or token is needed to obtain these upstream sources.

For a binary build, collect installed dependency notices with
`packaging/collect_cellpose_licenses.py`, preserve source-availability information
for copyleft components, and verify the actual package contents. Attribution,
source offers and required installation information cannot be replaced with a
"research use" disclaimer. The current private historical installers are not
automatically converted into reviewed community releases by these documents.
