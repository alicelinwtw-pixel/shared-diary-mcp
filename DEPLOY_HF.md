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

## 安全提示

- 不要公开管理页地址、参与者日记地址或 MCP 地址。
- 不要把任何钥匙贴进公开 issue、日志或截图。
- 趣味锁是互动玩法，不等同于强加密保险箱。
