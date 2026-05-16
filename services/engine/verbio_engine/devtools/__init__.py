"""Developer + test tools that are not part of the runtime engine.

Anything under `devtools` is excluded from production wheels; the
modules exist purely so test harnesses (Playwright E2E, manual
debugging) can spin up fake LiveKit participants, seed scenarios, or
hit private endpoints without copy-pasting glue every time.
"""
