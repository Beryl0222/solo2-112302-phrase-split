"""测试共用工具：临时工作区与种子引导。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from corpuskit.ingest import bootstrap_seed  # noqa: E402
from corpuskit.store import Store  # noqa: E402

SEED = ROOT / "data" / "corpus.json"
COLLEGE_A = "college-a"
COLLEGE_B = "college-b"


class WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="corpuskit-test-")
        self.root = Path(self._tmp.name)
        self.store = Store(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed(self) -> dict:
        return bootstrap_seed(self.store, SEED)
