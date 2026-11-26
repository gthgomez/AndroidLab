# Android Lab project context

Android Lab is a Windows-native CLI and MCP stdio server for Android testing. The Python package lives in `android_lab/`. The CLI, MCP interface and tests share the broker. Machine-local policy and state are outside the repository.

Runtime prerequisites are Python 3.10 or later and the SDK and JDK for the requested task. The emulator and scrcpy are external dependencies. The installer reuses the SDK and AVD store.

Use `python -m unittest discover -s tests` and `python -m unittest discover -s fixtures -p test_evaluate.py` for host regressions. Use the [canaries](fixtures/README.md) for runtime checks. The [qualification procedure](docs/qualification.md) governs physical device eligibility. Record simulated results separately from hardware observations.
