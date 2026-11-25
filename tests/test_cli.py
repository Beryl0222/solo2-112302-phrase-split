"""CLI 冒烟测试：核心命令链路在子进程外直接驱动 main()。"""
import io
import json
import sys
from contextlib import redirect_stdout

from corpuskit.cli import main

from tests.support import COLLEGE_A, SEED, WorkspaceCase


class CliSmokeTest(WorkspaceCase):
    def _run(self, *argv: str) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--store", str(self.root), *argv])
        self.assertEqual(code, 0, buf.getvalue())
        return buf.getvalue()

    def test_full_cli_flow(self):
        out = self._run("init", "--seed", str(SEED))
        self.assertIn("种子引导", out)

        self._run("term-list")
        out = self._run("sense-index", "主键回填", "主键回填表",
                        "--context", "工单", "--context", "工具菜单")
        self.assertIn("t-1#s1", out)
        self.assertIn("t-2#s1", out)

        self._run("fragment-list")
        self._run("fragment-show", "f-1")
        self._run("edge-list")
        self._run("evidence-clusters")
        out = self._run("leakage", "--college", COLLEGE_A)
        self.assertIn("泄漏", out)

        out = self._run("plan-make", "--college", COLLEGE_A)
        self.assertIn("规划", out)

        out = self._run("plan-list", "--college", COLLEGE_A)
        plan_id = next(line.split()[0] for line in out.splitlines() if line)
        self._run("package-questionnaire", "--college", COLLEGE_A,
                  "--plan", plan_id)
        pkg_list = self._run("package-list", "--kind", "questionnaire")
        pkg_id = pkg_list.splitlines()[0].split()[0]
        self._run("package-show", pkg_id)
        self._run("revision-list")
        self._run("events")

    def test_json_output_is_parseable(self):
        self._run("init", "--seed", str(SEED), "--json")
        out = self._run("term-list", "--json")
        payload = json.loads(out)
        self.assertIsInstance(payload, list)
        self.assertTrue(payload)

    def test_cli_error_returns_nonzero(self):
        self._run("init")
        code = main(["--store", str(self.root), "fragment-show", "f-404"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
