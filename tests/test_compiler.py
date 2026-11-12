import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from compiler.build import compiler_env
from compiler.templates import BuildError


class CompilerCheckout(unittest.TestCase):
    def checkout(self, path, native=True):
        (path/'bin').mkdir(parents=True)
        (path/'neo').touch()
        if native:
            (path/'bin/neoc').touch()
        return path.resolve()

    def test_explicit_neo_home_selects_matching_native_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chosen = self.checkout(root/'custom compiler')
            self.checkout(root/'neo')
            with patch('compiler.build.ROOT', root/'neokit'), patch.dict(os.environ, {
                'NEO_HOME': str(chosen), 'NEO_ROOT': '/other/compiler', 'NEOC': '/other/bin/neoc',
            }):
                env = compiler_env()
                self.assertEqual(env['NEO_HOME'], str(chosen))
                self.assertEqual(env['NEO_ROOT'], str(chosen))
                self.assertNotIn('NEOC', env)

    def test_auto_detection_uses_sibling_neo_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.checkout(root/'axon')
            with patch('compiler.build.ROOT', root/'neokit'), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(BuildError, 'Set NEO_HOME'):
                    compiler_env()
                sibling = self.checkout(root/'neo')
                self.assertEqual(compiler_env()['NEO_HOME'], str(sibling))

    def test_missing_native_compiler_is_rejected_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            old = self.checkout(Path(directory)/'old', native=False)
            with patch.dict(os.environ, {'NEO_HOME': str(old)}):
                with self.assertRaisesRegex(BuildError, 'Set NEO_HOME.*native Neo toolchain'):
                    compiler_env()
