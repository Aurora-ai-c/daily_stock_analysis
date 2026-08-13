# vendored/alphaevo

AlphaEvo 0.5.0 的**只读同步副本**，供云端 GitHub Actions 安装（alphaevo 未发布到
PyPI，云端无法 `pip install alphaevo`）。

## 同步机制

- 源：`D:\AI\alphaevo`（本地开发仓库，origin = `github.com/ZhuLinsen/alphaevo`）
- 本目录只包含：`src/`、`pyproject.toml`、`requirements.txt`、`LICENSE`、`README.md`
- 手动同步：复制上述文件到此目录并提交（版本升级时执行）

## 为什么 vendor 而不是 pip 依赖

1. alphaevo 从未发布到 PyPI，`pip install alphaevo` 云端必然失败
2. 上游仓库 ZhuLinsen/alphaevo 是原作者仓库，不应推入本项目的信号改动
3. 评审结论「策略 + 信号引擎进 DSA 仓库」——vendoring 是最贴合的落地方式

## 云端安装

`.github/workflows/00-daily-analysis.yml` 安装依赖时执行
`pip install ./vendor/alphaevo`，失败仅跳过策略信号章节，不阻塞主流程。

## 注意

- 本地开发继续使用 `pip install -e D:\AI\alphaevo`（editable），两侧互不影响
- 同步副本时若 alphaevo 升级，请同步更新本文件的版本号