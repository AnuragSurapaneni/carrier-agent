"""
Shared test setup.

The test-carrier and test-load fixtures are env-flag gated and the
application reads those flags at import time, so the flags must be
set before any test module imports the application. Pytest loads
this conftest before the test modules, so the flags are in time for
the first `app` import.
"""

import os

os.environ["ENABLE_TEST_CARRIERS"] = "true"
os.environ["ENABLE_TEST_LOADS"] = "true"
