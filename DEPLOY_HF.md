# 在 Hugging Face Spaces 部署

这套部署以 Gradio Space 为入口，并兼容 CPU Basic 与 ZeroGPU。日记本身只使用 CPU；ZeroGPU 仅通过一个极轻量的检测按钮注册平台所需的 GPU hook，正常写日记和 MCP 调用不会占用 GPU。

> [!IMPORTANT]
> 截至 2026-09，Hugging Face 的普通 Gradio/Docker compute Space 创建需要付费计划。CPU Basic 的硬件单价仍显示为 Free，但新建计算型 Space 本身受付费计划门槛限制。免费个人账号在满足平台条件时可创建有限数量的 ZeroGPU Space，因此免费部署优先使用 ZeroGPU；若账号已有 PRO / Team / Enterprise，再考虑 CPU Basic。

## 部署前检查

开始前先确认：

- Hugging Face 账号已登录，且能创建 Gradio Space。
- 若使用免费个人账号：创建时选择 ZeroGPU，不要把 CPU Basic 当作必然可用的免费 fallback。
- 若需要让 ChatGPT 或其他外部 MCP 客户端直接访问 `*.hf.space`：
  - Public Space：应用端点可公开访问，但仓库源码也公开；
  - Protected Space：应用端点可公开访问、源码保持私有，但需要支持该可见性的付费计划；
  - Private Space：只对所有者/协作者开放，不适合作为无需额外 HF 鉴权的外部 MCP 入口。
- 已准备一个 Storage Bucket，并计划以可读写方式挂载到 `/data`。
- 已准备至少 32 位随机 `DIARY_ADMIN_KEY`，不要写进仓库。

## 1. 创建 Space

推荐配置：

- SDK：Gradio
- Python：3.12
- Hardware：
  - 免费个人账号：ZeroGPU
  - 付费账号：CPU Basic 即可；这套日记不需要专用 GPU
- Visibility：
  - 需要外部 MCP 直连且不介意源码公开：Public
  - 有支持 Protected 的付费方案：优先 Protected
  - Private 仅适合你自己/协作者通过 HF 权限访问，不适合作为通用 MCP 公网入口

仓库已经包含 Hugging Face front matter、`app.py`、`requirements.txt`、`pyproject.toml` 和完整 `src/`，因此建议直接把整个仓库作为 Space 源码同步，而不是零散手工上传文件。

## 2. 挂载 Storage Bucket

在 Space 的 Settings 中创建或选择一个 Storage Bucket，并以可读写方式挂载到：

```text
/data
```

Storage Bucket 是持久化存储；Space 自带的运行磁盘不是可靠的长期数据库位置。Bucket volume 在 Space 中默认可读写。

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

不填时程序会根据当前请求自动生成链接。若前面还有自定义域名或代理，建议显式设置为最终对外根地址。

## 4. 首次启动检查

Space 显示 Running 后，先打开：

```text
https://你的-space-子域名.hf.space/health
```

预期返回类似：

```json
{"ok":true,"service":"shared-diary-mcp","version":"0.2.0"}
```

若 `/health` 正常，再进入管理页：

```text
https://你的-space-子域名.hf.space/setup/<DIARY_ADMIN_KEY>/
```

依次创建人类与 AI 参与者。每次创建都会生成两条地址：

- 日记网页：交给人类打开
- MCP 地址：接入对应的小机

访问钥匙只会明文显示一次。若遗失，在管理页点击“换钥匙”；旧链接会立刻失效。

## 5. MCP 连通性检查

拿某个参与者的完整 MCP 地址：

```text
https://你的-space-子域名.hf.space/mcp/<participant-access-key>/
```

客户端必须使用 Streamable HTTP / HTTP POST 方式访问。GitHub 仓库 URL、Space 首页 URL、管理页 URL 都不是 MCP 地址。

若客户端收到 404：

1. 先确认参与者 access key 是否完整、是否已被轮换；
2. 确认 Space 可见性不是阻止外部访问的 Private；
3. 确认 `/health` 可从同一网络环境直接打开；
4. 不要把完整 MCP URL 贴到公开 issue 或日志中排查。

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
- Space 使用 Public/Protected 只代表应用入口可从公网访问，真正的日记身份仍由每位参与者的高熵 access key 保护。
- Uvicorn access log 已默认关闭，避免把带钥匙的 URL 路径写入普通应用日志。
- 趣味锁是互动玩法，不等同于强加密保险箱。
