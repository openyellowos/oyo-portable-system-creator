import io
import unittest

from scripts.run_release_tests import run_suite


class ReleaseTestRunnerTests(unittest.TestCase):
    def test_skipped_test_is_a_release_gate_failure(self):
        class InjectedSkip(unittest.TestCase):
            @unittest.skip("intentional release-gate failure injection")
            def test_skip(self):
                self.fail("a skipped test must not execute")

        output = io.StringIO()
        result = run_suite(
            unittest.TestSuite([InjectedSkip("test_skip")]),
            stream=output,
            verbosity=0,
        )

        self.assertEqual(result, 1)
        self.assertIn("release test gate failed: 1 test(s) were skipped", output.getvalue())

    def test_empty_suite_is_a_release_gate_failure(self):
        output = io.StringIO()

        result = run_suite(unittest.TestSuite(), stream=output, verbosity=0)

        self.assertEqual(result, 1)
        self.assertIn("no tests were discovered", output.getvalue())

    def test_successful_suite_passes(self):
        class PassingTest(unittest.TestCase):
            def test_pass(self):
                self.assertTrue(True)

        result = run_suite(
            unittest.TestSuite([PassingTest("test_pass")]),
            stream=io.StringIO(),
            verbosity=0,
        )

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
