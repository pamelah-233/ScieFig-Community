# ScieFig R0.95 Offline License Issuer

This directory contains the developer-only issuer for ScieFig `R0.95` offline
machine-bound licenses.

## Rules

- Provide an explicit `--private-key` path every time.
- Provide an explicit `--machine-code`, `--output`, and `--license-id`.
- Never generate, copy, or store a release private key inside this repository,
  build artifacts, installer output, or the packaged application.
- The runtime application packages only the public key that matches the release
  private key.
- Licenses are perpetual. There is no expiry field or expiry behavior.

## Example

```powershell
python scripts/licensing/issue_r095_license.py `
  --private-key C:\secure\sciefig_r095_private_key.pem `
  --machine-code 3B29-4E2D-0A2F-B7E8 `
  --output C:\secure\customer\sciefig_r095_license.json `
  --license-id customer-20260622-001
```

Return the produced JSON file to the user through your offline channel. The app
imports that file into the system application-data license location and opens
the workspace only after signature and machine-code verification succeed.

## Trial User Instructions To Send With A License

1. Install ScieFig R0.95 with `ScieFig_R095_Setup.exe`.
2. Start ScieFig and copy the machine code shown in the activation window.
3. Send that machine code to the license issuer.
4. After receiving the license JSON, click `Import License` in ScieFig and
   select the JSON file.
5. Keep the license JSON for reinstalling on the same computer.

The license is permanent for the activated computer. It will not activate a
different computer.
