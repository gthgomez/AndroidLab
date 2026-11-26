# Contributing

Start with the smallest reproducible issue. Keep runtime adapters behind the broker and keep project requests separate from device policy. Describe the observed result, environment and cleanup. Submit sanitized synthetic evidence.

Run `scripts/Test-Release.ps1` on Windows. Changes to the Android adapter should also build the fixtures and run positive and negative emulator suites. A change to input, focus, display binding, APK approval, cleanup or firmware parsing needs independent review. Refresh physical qualification after relevant changes. Never run intentionally disruptive negative controls on a personal device.

The broker uses the Python standard library. New dependencies need a concrete rationale, a license review and a pinned verification path. Keep SDK tools and device enrollment outside this repository.
