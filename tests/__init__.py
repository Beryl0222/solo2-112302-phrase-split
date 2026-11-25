"""测试包初始化：即使以 ``python3 -I`` 运行也能导入项目根下的 corpuskit。

``-I`` 会隔离环境变量与用户 site 包，且不把当前目录隐式加入 sys.path，
而 unittest discover 会把测试起始目录（tests/）加入 sys.path，
因此在这里显式登记项目根目录。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
