"""Check logging with tiny subprocesses, without running models."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import run_pipeline


class PipelineLoggingTests(unittest.TestCase):
    def add_review(self, root, succeeds=True):
        # The temporary project has its own tiny suite, preventing recursion.
        (root / 'review_fixture.py').write_text(
            "import unittest\nclass Review(unittest.TestCase):\n"
            f"    def test_check(self): self.assertTrue({succeeds!r})\n"
        )

    def test_console_and_dated_log(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.add_review(root)
            (root / 'stage.py').write_text("import sys\nprint('output')\nprint('error output', file=sys.stderr)\n")
            console = io.StringIO()
            with patch.object(run_pipeline, 'BASE_DIR', root), patch.object(run_pipeline, 'STAGES', (run_pipeline.STAGES[0], ('stage.py', ('stage.py',)))), redirect_stdout(console):
                path = run_pipeline.main()
                second = run_pipeline.main()
            self.assertLess(path.read_text().index('Finished review_*.py'), path.read_text().index('Starting stage.py'))
            self.assertNotEqual(path, second)
            self.assertEqual(path.parent, root / "logs")
            self.assertRegex(path.name, r'^pipeline-run_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}\.log$')
            text = path.read_text()
            for expected in ('output', 'error output', 'Pipeline completed.'):
                self.assertIn(expected, text)
                self.assertIn(expected, console.getvalue())

    def test_failure_logged_and_next_stage_not_started(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.add_review(root)
            (root / 'fail.py').write_text("raise RuntimeError('failure fixture')\n")
            with patch.object(run_pipeline, 'BASE_DIR', root), patch.object(run_pipeline, 'STAGES', (run_pipeline.STAGES[0], ('fail.py', ('fail.py',)), ('never.py', ('never.py',)))), redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_pipeline.main()
            text = next((root / 'logs').glob('*.log')).read_text()
            self.assertIn('failure fixture', text)
            self.assertIn('Pipeline failed:', text)
            self.assertNotIn('Starting never.py', text)


    def test_failed_reviews_prevent_processing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.add_review(root, succeeds=False)
            with patch.object(run_pipeline, 'BASE_DIR', root), patch.object(run_pipeline, 'STAGES', (run_pipeline.STAGES[0], ('never.py', ('never.py',)))), redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_pipeline.main()
            text = next((root / 'logs').glob('*.log')).read_text()
            self.assertIn('FAILED', text)
            self.assertNotIn('Starting never.py', text)

    def test_missing_reviews_prevent_processing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(run_pipeline, 'BASE_DIR', root), patch.object(run_pipeline, 'STAGES', (run_pipeline.STAGES[0], ('never.py', ('never.py',)))), redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_pipeline.main()
            text = next((root / 'logs').glob('*.log')).read_text()
            self.assertIn('Discovered 0 review tests', text)
            self.assertNotIn('Starting never.py', text)


if __name__ == '__main__':
    unittest.main()
