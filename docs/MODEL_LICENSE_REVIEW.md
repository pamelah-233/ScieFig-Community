# Model license and provenance review

Checked 2026-10-27. This record distinguishes declared permissions from an
unresolved legal question; it is not a guarantee of rights against all claims.

## Cellpose stock and adapted models

- The pinned **v3.1.1.3 LICENSE** is BSD-3-Clause, copyright 2020 Howard Hughes
  Medical Institute. The README's old badge text saying GPL is not the license text.
- In upstream [issue 192](https://github.com/MouseLand/cellpose/issues/192),
  Cellpose author `marius10p` explicitly states that the model files also have
  the Cellpose BSD3 license. The same reply says whether weights inherit the
  training images' non-commercial restriction is a gray zone.
- The pinned v3.1.1.3 README identifies non-commercial training data for cyto3
  and nuclei. The model host's pinned README declares `bsd-3-clause-clear`.
  These declarations do not waive possible third-party data rights.
- On 2026-10-27, maintainer **pamelah-233** confirmed the right to publicly
  redistribute the three local DAPI, HE-fiber and Laminin adaptations. ScieFig's
  adaptation contributions use BSD-3-Clause with the upstream notice preserved;
  see `LICENSES/ScieFig-Model-Adaptations-BSD-3-Clause.txt`.
- Original images, annotations and private validation archives are not included.
  A statement about code licensing is not a promise of unrestricted commercial
  model rights. A commercial deployment should independently resolve this issue.

Sources:

- https://github.com/MouseLand/cellpose/blob/v3.1.1.3/LICENSE
- https://github.com/MouseLand/cellpose/blob/v3.1.1.3/README.md
- https://github.com/MouseLand/cellpose/issues/192
- https://huggingface.co/spaces/mouseland/cellpose/blob/b97a37a6237a937c862ad6b6a0ab4ca7adf0925e/README.md

## TRUEFAD

`truefad_source_rdf.yaml` declares the **model** MIT and identifies the authors,
citation, preprocessing and original formats. ScieFig's conversion transposes
the existing Keras parameters into PyTorch; it is not new training. Preserve
that original RDF and `truefad_myotube.json` with the converted weights.
The original repository's GPLv3 macros and the model's declared MIT terms are
different; no copied macro is relabeled as MIT.

Original copyright remains with the upstream rightsholders. Attribution and
the MIT permission text are in `LICENSES/TRUEFAD-Model-MIT.txt`. ScieFig claims
copyright only for its own code/contributions, not for the original model.

## Public package boundary

The clean source snapshot includes the four approved local weight files and
their notices. The original stock cyto3/nuclei files can be obtained from the
pinned sources listed in the build documentation; they must retain the Cellpose
BSD notice and this data-rights note when bundled. The public-source manifest
records exactly which files were exported. No training dataset is shipped.
