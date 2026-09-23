Improve the preview demo onboarding: `examples/preview_example.py` gains a `--keep-open` flag
that keeps the session live for clicking (it calls `preview.wait_closed()`), the URL and
human-facing notes move to stderr so stdout carries only the JSON capture report, the
quickstart's next-steps list links to the preview guide, and CONTRIBUTING documents which
optional extras unlock the preview tests, example, and type-checking.
