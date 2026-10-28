# Local style extraction

The **Styles** module opens this workspace directly. PDF/image analysis and OCR run
locally. Tokens retain detected/estimated/unknown/user-edited evidence. Saving
uses the existing permanent style registry; unknown tokens use the existing
Nature defaults rather than being promoted to detections. Statistical values
are never extracted or replaced. Preview data are fixed illustrative data.

Development dependencies: PyMuPDF, OpenCV, NumPy, Pillow, fontTools; local
Tesseract executable plus English traineddata at `build/local_style_runtime/tesseract`.
The tested development runtime was copied from the existing local demo runtime;
it is not a user upload or network service. No paper corpus is distributed here.

The 1.0.6 installer bundles the extraction modules, PDF/font dependencies and local
Tesseract runtime. No separate Python installation or OCR download is required.
Commercial redistribution still requires the dependency-license audit, particularly
PyMuPDF's AGPL/commercial licensing; this build does not change project licensing.
Controlled synthetic PDF/PNG regression inputs are in
`tests/fixtures/local_style_extraction`.
