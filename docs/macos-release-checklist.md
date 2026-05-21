# macOS Release Setup Checklist

Use this when you are ready to turn SciPlotter macOS releases into properly signed and notarized app bundles.

## Goal

Produce a `SciPlotter-macos.zip` release asset that:

- is signed with a `Developer ID Application` certificate
- is submitted to Apple notarization automatically in GitHub Actions
- is stapled before upload so normal Finder opening works on user machines

## One-Time Apple Account Setup

1. Enroll in the Apple Developer Program.
2. Create or confirm access to a team that can issue `Developer ID Application` certificates.
3. On a Mac, create or download a `Developer ID Application` certificate for the release owner.
4. Export that certificate from Keychain Access as a `.p12` file with a password.
5. In App Store Connect, create an API key for notarization use.
6. Save the `.p8` file, the key ID, and the issuer ID.

## GitHub Secrets To Add

Add these repository or organization secrets before running a notarized macOS release:

- `MACOS_CERTIFICATE_P12_BASE64`: base64 of the exported `.p12` file
- `MACOS_CERTIFICATE_PASSWORD`: password used when exporting the `.p12`
- `MACOS_CODESIGN_IDENTITY`: full certificate name, for example `Developer ID Application: Example Name (TEAMID)`
- `MACOS_NOTARY_KEY_BASE64`: base64 of the App Store Connect `.p8` file
- `MACOS_NOTARY_KEY_ID`: App Store Connect API key ID
- `MACOS_NOTARY_ISSUER`: App Store Connect issuer UUID

To generate the base64 values locally:

```bash
base64 -i path/to/certificate.p12 | pbcopy
base64 -i path/to/AuthKey_ABC123XYZ.p8 | pbcopy
```

## Release Workflow Behavior

When those secrets are present:

1. The macOS GitHub Actions job imports the certificate into a temporary keychain.
2. The build signs `SciPlotter.app` with hardened runtime and a timestamp.
3. The build submits `SciPlotter-macos.zip` to `notarytool`.
4. The build staples the approved ticket to `SciPlotter.app`.
5. The build re-zips the stapled app and publishes that artifact.

When the secrets are missing:

- the workflow still builds a macOS zip
- the app is only ad-hoc signed
- Finder launch may still be blocked by macOS security policy

## Pre-Release Checks

Before cutting a macOS release, verify:

1. The GitHub secrets above are populated.
2. The certificate identity text exactly matches `MACOS_CODESIGN_IDENTITY`.
3. `scripts/build_release.py --clean` succeeds on macOS.
4. The GitHub Actions macOS job completes without skipping notarization unexpectedly.

## Post-Release Checks

After a release is published:

1. Download `SciPlotter-macos.zip` from GitHub Releases.
2. Unzip it on a clean Mac.
3. Move `SciPlotter.app` into `Applications`.
4. Open it normally from Finder.
5. Confirm the app launches without the previous security-policy failure.

## Troubleshooting Hints

- If the workflow says no certificate is configured, check `MACOS_CERTIFICATE_P12_BASE64` and `MACOS_CERTIFICATE_PASSWORD`.
- If notarization is skipped, check `MACOS_CODESIGN_IDENTITY`, `MACOS_NOTARY_KEY_BASE64`, `MACOS_NOTARY_KEY_ID`, and `MACOS_NOTARY_ISSUER`.
- If notarization submission fails, inspect the macOS job log for the `xcrun notarytool submit` output.
- If Finder launch still fails after notarization, retest the published zip rather than a locally modified app bundle.