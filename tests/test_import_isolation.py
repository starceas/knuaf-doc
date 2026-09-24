"""Import-isolation contract (B01): runtime modules must resolve inside the
candidate tree even under a polluted import environment."""
import sys
import types
import unittest
from pathlib import Path

from tests._harness import ContractCase, SCRIPTS, runtime


class ImportIsolationTests(ContractCase):
    def test_runtime_modules_resolve_inside_candidate(self):
        for name in (
            "gg_core", "gg_document", "gg_school_excel", "gg_excel_print",
            "gg_excel_template", "gg_office", "gg_finance", "gg",
        ):
            with self.subTest(module=name):
                module = runtime(name)
                self.assertTrue(
                    Path(module.__file__).resolve().is_relative_to(SCRIPTS),
                    f"{name} resolved to {module.__file__}",
                )

    def test_foreign_module_in_cache_is_purged(self):
        """A pre-loaded gg_core from another location must not shadow the
        candidate module."""
        real = runtime("gg_core")
        fake = types.ModuleType("gg_core")
        fake.__file__ = "/foreign/gg_core.py"
        sys.modules["gg_core"] = fake
        try:
            again = runtime("gg_core")
        finally:
            # leave the real module cached regardless of outcome
            sys.modules["gg_core"] = real
        self.assertIsNot(again, fake)
        self.assertTrue(
            Path(again.__file__).resolve().is_relative_to(SCRIPTS),
            f"gg_core resolved to {again.__file__}",
        )


if __name__ == "__main__":
    unittest.main()
