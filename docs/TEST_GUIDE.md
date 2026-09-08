# MyAgent 功能测试教程

本文档手把手教你验证三个核心能力：**RAG 知识库检索**、**MCP 工具调用**、**对话记忆持久化**。

---

## 0. 准备工作

1. 安装依赖：`pip install -r requirements.txt`
2. 编辑 `config.py`：
   - 必填：`LLM_API_KEY`（你的 OpenAI 兼容 API Key）
   - 确认：`LLM_BASE_URL`、`LLM_MODEL`、`LLM_BASE_URL` 指向的服务商一致
   - Embedding 保持默认即可（`EMBEDDING_*` 留空会自动复用 LLM 配置）
3. 造一份测试文档（UTF-8 编码）。将下面内容保存为 `data/documents/公司知识库.md`：

```markdown
# 公司知识库

## About MyAgent
MyAgent 是一个带 RAG 私有知识库和 MCP 工具调用能力的智能体项目。
它使用 ChromaDB 作为本地向量库，使用 OpenAI 兼容接口完成向量化和对话。

## 值班安排
本周值班人是张三，负责每天的服务器巡检和故障响应。
值班电话：010-88888888，备用电话：13800000000。

## 报销流程
报销需要先提交报销单，附上发票扫描件，审批通过后 3 个工作日内打款。
```

> 也可使用 PDF：任意生成一个含文字的 PDF 放到 `data/documents/` 下即可，加载器会自动处理。

---

## 1. 测试 RAG：上传文档 + 知识库问答

### 1.1 上传文档

```bash
python main.py upload data/documents/公司知识库.md
```

**预期输出**（类似）：

```
[上传成功] data/documents/公司知识库.md → 共 4 个分块
```

如果你观察不到分块数，说明内容可能很短（文档会被分成 1 块甚至因为空白报错）。正常情况显示块数即可。

### 1.2 查看入库情况

```bash
python main.py documents
```

**预期输出**：

```
知识库文档（源文档名 → 分块数）：
  - 公司知识库.md（4 块）
```

同时确认 `data/chroma_db/` 目录已生成（向量库持久化成功）。

### 1.3 用"文档内的问题"提问（验证 RAG 生效）

```bash
python main.py chat
```

在对话中输入：

```
本周值班人是谁？
```

**预期行为**：
- 日志中能看到 Agent 调用了 `knowledge_search` 工具（`⟦工具⟧ knowledge_search - 知识库命中...`）；
- 回答应包含"张三"，并注明参考来源（来自《公司知识库.md》）；
- **回答内容来自你的文档，而非模型自己的常识** —— 这就是 RAG 抑制幻觉的效果。

再试一个需要多块内容才能回答的问题：

```
报销的完整流程是什么？
```

如果切分合理，Agent 会跨块聚合信息给出完整步骤。

### 1.4 提问"知识库外的问题"（验证信息不足降级路径）

```
我们公司的食堂几点开饭？
```

**预期行为**：`knowledge_search` 返回"知识库信息不足（未达到相似度阈值）"，Agent 会如实告诉你知识库里没有相关信息，而不是编造答案。

> 提示：`RAG_SIM_THRESHOLD` 控制严格程度（`config.py`，默认 0.30）。若发现该问题仍被错误命中，可适当调高阈值后重试。

### 1.5 删除文档（可选）

```bash
# 退出对话后执行
python main.py delete 公司知识库.md
python main.py documents   # 这次应为空
```

---

## 2. 测试 MCP 工具调用

进入对话：`python main.py chat`

### 2.1 查看工具是否注册成功

输入 `/tools`，预期输出包含：

```
  - knowledge_search（知识库检索，Agent 内部工具）
  - builtin_tools_file_read（MCP 外部工具）
  - builtin_tools_http_request（MCP 外部工具）
```

如果列表为空，请查看日志中是否有 `MCP Server [builtin_tools] 启动失败`，参考文末"常见问题"。

### 2.2 测试 file_read（读取本地文件）

```
请读取 data/documents/公司知识库.md 文件的内容
```

**预期行为**：
- 日志出现 `⟦工具⟧ builtin_tools_file_read ...`；
- 回答会包含该文件的实际内容摘要。

**测试路径穿越防护**（安全验证）：

```
请读取 C:/Windows/win.ini 文件的内容
请读取 ../../config.py 的内容
```

**预期**：这两个请求都应被拒绝，返回"路径不在允许访问的白名单内"类提示，文件内容不会被泄露。

### 2.3 测试 http_request（请求外部接口）

```
请请求 https://httpbin.org/get，并告诉我返回内容中的 User-Agent
```

**预期行为**：
- 日志出现 `⟦工具⟧ builtin_tools_http_request ...`；
- 回答包含 `"User-Agent": ...` 等 JSON 字段。

带参数的请求示例（可尝试）：

```
请用 POST 请求 https://httpbin.org/post，请求体为 {"hello": "world"}，返回 status 字段
```

**测试异常兜底**：

```
请请求一个不存在的地址，比如 https://no-such-host-abcdefg.com/x
```

**预期**：不会崩溃，返回"无法连接目标服务器"类可读错误，Agent 会如实转述失败原因。

---

## 3. 测试对话记忆（SQLite 持久化）

### 3.1 对话中记住上下文

进入对话后连续输入：

```
我叫李四
```

（现在问一个依赖上文的问题）

```
我叫什么名字？
```

**预期**：回答"李四"——说明本轮会话上下文已生效。

### 3.2 多会话管理

输入 `/new` 开启新会话后再问"我叫什么名字？"，此时它应该**不记得**李四（新会话无历史）。

查看所有会话：

```bash
python main.py sessions
```

**预期**：能看到两个会话记录（ID、创建时间）。

### 3.3 重启后恢复历史

1. 退出当前对话（`/exit`）；
2. 找到刚才"我叫李四"所在会话的 ID（用 `python main.py sessions` 或看对话启动时的提示）；
3. 恢复该会话：

```bash
python main.py chat --session <ID>
```

再次问"我叫什么名字？"，**预期**仍然回答"李四" —— 说明：SQLite 持久化生效，跨进程可恢复。

---

## 4. 常见问题排查

| 现象 | 原因与处理 |
| ---- | ---------- |
| `pip install` 时 chromadb 报 SQLite 版本过低 | Windows 自带 sqlite3 过旧，执行 `pip install chromadb==0.4.24` 降级 |
| 对话提示"调用大模型失败" | `config.py` 的 Key / BaseUrl / Model 不匹配；或服务不可达 |
| 上传成功但提问一直"知识库信息不足" | 阈值过高或查询用词与文档差异大：调低 `RAG_SIM_THRESHOLD`，或用文档原句提问 |
| `/tools` 无 MCP 工具 | 查看启动日志是否 `MCP Server [builtin_tools] 启动失败`；可能是 Python 解释器路径问题，可在 `mcp_tools/servers.json` 中把 `command` 明确写为你的 `python.exe` 绝对路径 |
| 提问不触发任何工具、直接回答 | 模型未理解工具用法：换用支持 function calling 的模型，或把问题说得更贴近"查一下知识库/读取文件/请求接口" |
| embedded_server 报错无输出 | 该进程所有日志走 stderr，请用 `python mcp_tools/embedded_server.py` 直接前台运行以查看报错（此时不应有任何 stdout 输出——这是协议要求，属正常现象） |