# Python 后端运行依赖许可证核验

> 这是依赖许可证盘点，不是法律意见。OwlPath 的 MIT 许可证不会将第三方依赖重新许可为 MIT；使用者仍需遵守每个依赖的许可条款和通知义务。

## 范围与方法

- 核对对象是 `backend/requirements.lock` 中 28 个“包名 + 版本”条目；`websockets` 因 Python 版本条件而有两个版本条目。
- macOS arm64 + Python 3.11.11 实际激活 25 个条目；`colorama 0.4.6`、`exceptiongroup 1.3.1` 和 `websockets 16.1.1` 在该环境不激活。
- 25 个已安装条目优先使用已安装 wheel 的 `.dist-info/METADATA` 和随包许可文件核验。
- 3 个当前未激活条目使用指定版本的 PyPI wheel 内 `METADATA` 和随包许可文件核验，没有根据包名猜测。
- `License-Expression` 优先于旧式 `License` 字段；两者缺失时，明确标出所使用的 classifier 或许可文本证据。

## 逐项结果

| # | 包与锁定版本 | 激活条件 | 元数据/分发包证据支持的许可标识 | 证据口径 |
|---:|---|---|---|---|
| 1 | `annotated-doc 0.0.5` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 2 | `annotated-types 0.8.0` | 通用 | `MIT` | `License-Expression`; classifier; `LICENSE` |
| 3 | `anyio 4.14.2` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 4 | `certifi 2026.7.22` | 通用 | `MPL-2.0` | `License`; classifier; `LICENSE` |
| 5 | `cffi 2.1.1` | 非 PyPy | `MIT-0` | `License-Expression`; `LICENSE` |
| 6 | `click 8.4.2` | 通用 | `BSD-3-Clause` | `License-Expression`; `LICENSE.txt` |
| 7 | `colorama 0.4.6` | 仅 Windows | `BSD-3-Clause`¹ | BSD classifier; 随包三条款 BSD 文本 `LICENSE.txt` |
| 8 | `cryptography 50.0.0` | 通用 | `Apache-2.0 OR BSD-3-Clause` | `License-Expression`; `LICENSE.APACHE`; `LICENSE.BSD` |
| 9 | `exceptiongroup 1.3.1` | Python < 3.11 | `MIT`² | MIT classifier; `LICENSE` 同时包含所复用 Python 标准库代码的 PSF-2.0 通知 |
| 10 | `fastapi 0.141.1` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 11 | `h11 0.16.0` | 通用 | `MIT` | `License`; classifier; `LICENSE.txt` |
| 12 | `httpcore 1.0.9` | 通用 | `BSD-3-Clause` | `License-Expression`; classifier; `LICENSE.md` |
| 13 | `httptools 0.8.0` | 通用 | `MIT`³ | `License-Expression`; 分发包另附带 vendor 许可文件 |
| 14 | `httpx 0.28.1` | 通用 | `BSD-3-Clause` | `License`; classifier; `LICENSE.md` |
| 15 | `idna 3.18` | 通用 | `BSD-3-Clause` | `License-Expression`; `LICENSE.md` |
| 16 | `pycparser 3.0` | 非 PyPy | `BSD-3-Clause` | `License-Expression`; `LICENSE` |
| 17 | `pydantic 2.13.4` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 18 | `pydantic-core 2.46.4` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 19 | `python-dotenv 1.2.2` | 通用 | `BSD-3-Clause` | `License`; `LICENSE` |
| 20 | `PyYAML 6.0.3` | 通用 | `MIT` | `License`; classifier; `LICENSE` |
| 21 | `starlette 1.6.0` | 通用 | `BSD-3-Clause` | `License-Expression`; `LICENSE.md` |
| 22 | `typing-extensions 4.16.0` | 通用 | `PSF-2.0` | `License-Expression`; `LICENSE` |
| 23 | `typing-inspection 0.4.4` | 通用 | `MIT` | `License-Expression`; `LICENSE` |
| 24 | `uvicorn 0.52.3` | 通用 | `BSD-3-Clause` | `License-Expression`; `LICENSE.md` |
| 25 | `uvloop 0.22.1` | 非 PyPy，且非 Windows/Cygwin | **需保留双文本说明**⁴ | 旧式 `License` 称 MIT；classifiers 同时列 Apache 和 MIT；分发包包含 `LICENSE-APACHE` 与 `LICENSE-MIT`，但未声明 SPDX 布尔关系 |
| 26 | `watchfiles 1.2.0` | 通用 | `MIT` | `License`; classifier; `LICENSE` |
| 27 | `websockets 16.1.1` | Python < 3.11 | `BSD-3-Clause` | `License-Expression`; `LICENSE` |
| 28 | `websockets 17.0.1` | Python >= 3.11 | `BSD-3-Clause` | `License-Expression`; `LICENSE` |

¹ `colorama 0.4.6` 没有 `License-Expression`；这里的 SPDX 名称来自随包三条款 BSD 全文，不是从泛化的“BSD License” classifier 单独推断。  
² `exceptiongroup 1.3.1` 的项目许可 classifier 是 MIT；其 `LICENSE` 还包含复用 Python 标准库代码所需的 PSF 通知，因此不应在再分发时删掉该通知。  
³ `httptools 0.8.0` 的项目元数据表达式是 MIT；wheel 中还包含 `vendor/http-parser/LICENSE-MIT` 和 `vendor/llhttp/LICENSE`，再分发时应保留。  
⁴ 不把 `uvloop 0.22.1` 自行改写为 `MIT OR Apache-2.0` 或 `MIT AND Apache-2.0`，因为该 wheel 的 `METADATA` 没有声明这个逻辑关系。公开 SBOM 保留元数据原话和双许可文件事实。

### 当前未激活条目的 wheel 证据

以下三个指定版本 wheel 的 SHA-256 均存在于 `backend/requirements.lock` 的允许哈希中：

| wheel | SHA-256 |
|---|---|
| `colorama-0.4.6-py2.py3-none-any.whl` | `4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6` |
| `exceptiongroup-1.3.1-py3-none-any.whl` | `a7a39a3bd276781e98394987d3a5701d0c4edffb633bb7a5144577f82c773598` |
| `websockets-16.1.1-cp310-cp310-macosx_11_0_arm64.whl` | `9246a0d063cfcbcc85f2359dd6876d681213f4790832272aa16641b4ed5d64d4` |

## 当前结论

- macOS arm64 + Python 3.11.11 的 25/25 个已激活运行条目均已找到包内许可证证据。
- 通用锁文件的 28/28 个包版本条目均已找到包内许可证证据。
- `uvloop 0.22.1` 的 SPDX 关系不明确，已透明保留为人工许可复核项，未猜测。
- 本核验只覆盖 Python 后端运行锁定依赖；不覆盖开发/文档依赖、前端依赖、系统库、容器基础镜像、外部 API/模型条款或医学内容来源。
