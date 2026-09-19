# 线程预算与恢复

翻译过程中会大量看图（页面渲染、裁剪件、公式）。Codex 每回合把当前窗口的完整历史重发给 DeepSeek `/responses`，图片以 base64 内嵌、反复重发；请求体有服务端体积上限，撞上就报 `413 Payload Too Large` 并反复重连；带图片的工具输出还可能触发 `No tool output found for tool call ...`（invalid_request_error）中断回合。Codex 的自动压缩按 token 触发（本机 DeepSeek 模型目录为 1M 窗口、约 99.6 万阈值），图片字节大、token 少，常在压缩前就撞上体积上限。

本节规则随本技能分发，不依赖其它技能；执行顺序与本文一致。

## 图片与字节预算

1. 预算是字节，不是 token。`scripts/make_preview.py` 默认对 ≤1.5 MB 的原图直接透传（不重编码），只有大图才降采样到长边 2000 px、JPEG q90；A4 整页预览约 0.3–0.6 MB。
2. 分级看图：页面级排版用 2000 px 预览；小字、公式上下标、图注、图例、裁剪边界用 `--crop L,T,W,H` 取全分辨率区域，必要时 `--scale 2`。不用缩略图判断细节。
3. 同一线程累计 `view_image` 不超过 25 张，或按 `scripts/thread_budget.py` 的红线执行，以先到者为准。
4. 同一张图只 `view_image` 一次；看过立刻把结论写进检查点或检查记录，后续引用文件结论。
5. 能脚本判定的检查（尺寸、溢出、空白页、像素统计、文字提取、页码）不用看图完成。
6. 大段工具输出重定向到文件，只回读摘要；不要把日志、表格、整页渲染贴进对话。

## 看图串行化

每批最多 2 张 `view_image`，等返回并登记结论后再发起下一批；不使用 3–4 张并行批量（暂存区 STA-006，验收中）。

## 检查点与跨线程续做

每完成一个小单元（一页、一张图、一次构建或验证）就更新论文进度文件的检查点小节，假设线程随时会被切断：

```markdown
## 会话检查点（日期时间）

- 状态：进行中
- 当前任务：第 2 篇 s00382-023-06726-6；正文已提取；fig11 裁剪完成待目检
- 下一步：看 crops/fig11.png 的预览 → preflight → build → verify → 逐页目检
- 关键文件：LITERATURE/s00382-023-06726-6_支持内容/tools/...（相对项目根）
- 已知问题：第 23 页空白页待修；check_fit.py 已写好待运行
- 本线程图片：已看 12 张（上限 25）
```

检查点只写路径与结论，不嵌图片、不贴日志。新线程开场提示词模板：

```text
读取 <进度文件绝对路径> 的"会话检查点"和 references/thread-budget-and-recovery.md，从"下一步"继续。
工作约束：图片先看预览缩略图、同一张图只看一次、每批 view_image 不超过 2 张、每完成一个单元就更新检查点；
本线程累计看图达到 25 张时停下来提醒我换线程。
```

## 故障处置

| 症状 | 处置 |
| --- | --- |
| `413 Payload Too Large` / `Failed to buffer the request body` | 不在原线程重试——重发的是同一个超大请求。写检查点，开新线程继续。 |
| 反复"正在重新连接 N/N" | 等本轮结束看最终错误；是 413 按上一行处理；只有 DNS、超时、5xx 才按网络问题查。 |
| `No tool output found for tool call ...` | 先发一条最短消息（如"继续"）重试一次；再次出现就写检查点并轮换线程。 |
| 新线程仍报 413 | 交接内容本身带了图片或大文件；改成只交接文件路径与结论。 |

不要连续重试同一个失败请求，也不要为了"试试看"重发大段内容。

## 随技能自带的脚本

- `scripts/make_preview.py IMAGE... [--crop L,T,W,H] [--scale 2] [--png] [--out DIR] [--max-edge 2000] [--quality 90] [--force]`
  - ≤1.5 MB 原图透传；大图生成长边 2000 px、JPEG q90 的预览；`--crop` 输出全分辨率区域（默认 PNG）。
  - 输出 TSV：路径、结果字节数、原字节数、比例、模式（passthrough/jpeg/png）。
- `scripts/thread_budget.py [--cwd DIR] [--file SESSION.jsonl] [--json]`
  - 统计当前压缩窗口的图片数量与体积、估算请求体大小和最近请求 tokens，给出绿/黄/红与建议动作；`--cwd` 默认取当前目录对应项目最近的会话文件。

两个脚本只用标准库与 Pillow（Pillow 已在 `scripts/requirements.txt` 中）。Windows 上 `python3` 可能只是应用商店占位符，失败时改用环境中可用的 Python（例如 Codex 工作区运行时自带的 `python.exe`）；不要在技能或脚本里写死个人绝对路径。
