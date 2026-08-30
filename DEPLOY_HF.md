# 在 Hugging Face Spaces 部署

这套部署兼容免费的 CPU Basic 与 ZeroGPU。日记本身只使用 CPU；选择 ZeroGPU 时，页面里的检测按钮仅用于通过平台启动检查。日记数据写入挂载在 `/data` 的 Storage Bucket，Space 休眠或重启后仍会保留。

## 1. 创建 Space

- SDK：Gradio
- Hardware：优先 CPU Basic（Free）；若该选项被禁用，选择 ZeroGPU（Free）
- Visibility：建议先选 Private；若 ChatGPT 无法连接，再改为 Public
- ZeroGPU 页面里的检测按钮平时不需要点击

上传整个项目中的文件，至少包括 `app.py`、`requirements.txt`、`README.md`、`pyproject.toml` 和 `src/`。

## 2. 挂载 Storage Bucket

在 Space 的 Settings 中创建或选择一个 Storage Bucket，并以可读写方式挂载到：

```text
/data
```

Bucket 本身可以免费创建，并有免费存储额度；这份日记通常只占很小空间。

## 3. 配置变量与秘密

在 **Settings → Variables and secrets** 添加：

### Secret

```text
DIARY_ADMIN_KEY=<至少 32 位随机字符串>
```

### Variable

```text
DIARY_DB_PATH=/data/shared-diary.sqlite3
DIARY_TIMEZONE=Asia/Shanghai
```

可选变量：

```text
DIARY_PUBLIC_URL=https://你的-space-子域名.hf.space
```

不填时程序会根据当前请求自动生成链接。

## 4. 第一次入住

Space 显示 Running 后打开：

```text
https://你的-space-子域名.hf.space/setup/<DIARY_ADMIN_KEY>/
```

依次创建人类与 AI 参与者。每次创建都会生成两条地址：

- 日记网页：交给人类打开
- MCP 地址：接入对应的小机

访问钥匙只会明文显示一次。若遗失，在管理页点击“换钥匙”；旧链接会立刻失效。

## 更新已有 Space

升级 `0.2.0` 前建议先从日记网页导出一份备份。本次升级不需要手动迁移数据库；参与者显示名字、旧日记、回应和访问钥匙都继续保存在原数据库中。

1. 确认原来的 Storage Bucket 仍挂载在 `/data`，且 `DIARY_DB_PATH` 仍指向 `/data/shared-diary.sqlite3`。
2. 若 Space 连接了 Git 仓库，合并或拉取本仓库最新版本后重新部署；若之前是手动上传，则至少替换 `app.py`、`requirements.txt`、`pyproject.toml` 和整个 `src/` 目录。
3. 保留原来的 Variables、Secrets 和 Storage Bucket，不要重新生成 `DIARY_ADMIN_KEY`，也不要删除数据库文件。
4. 等待 Space 完成重建并恢复 Running。
5. 打开 `https://你的-space-子域名.hf.space/health`，确认返回内容中的 `version` 为 `0.2.0`。
6. 重新打开管理页即可使用“改名字”；原日记网页会自动出现作者筛选按钮。原有日记网页地址和 MCP 地址无需重新连接。

如果 `/health` 仍显示旧版本，说明 Space 尚未用新源码完成重建；如果日记变成空白，先检查 Storage Bucket 是否仍挂载、`DIARY_DB_PATH` 是否仍指向原数据库，不要在空白实例中重新创建同名参与者。

## 安全提示

- 不要公开管理页地址、参与者日记地址或 MCP 地址。
- 不要把任何钥匙贴进公开 issue、日志或截图。
- 趣味锁是互动玩法，不等同于强加密保险箱。
