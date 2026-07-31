# Azure OpenAI API 接入教程

本文面向第一次接触 LLM API 的使用者。完成配置不需要修改 Python 代码。

## 1. 先理解五个概念

| 配置 | 是什么 | 示例 | 是否敏感 |
|---|---|---|---|
| Endpoint | Azure OpenAI 资源的基础地址 | `https://my-resource.openai.azure.com/` | 通常不算密钥，但不要随意公开内部资源名 |
| API Key | 调用该资源的访问密钥 | 一长串随机字符 | **是，绝对不要发到聊天、截图或 Git** |
| Auth Mode | 使用 Key 或 Microsoft Entra ID | `key` / `entra` | 否 |
| Tenant ID | Entra 资源所在目录 ID | GUID | 否 |
| Deployment | 在 Azure 中部署模型时设置的部署名称 | `data-agent-gpt4-mini` | 否 |
| Embedding Deployment | 可选的向量模型部署名称 | `data-agent-embedding-small` | 否 |
| API Mode | Azure OpenAI 接口模式 | `v1` | 否 |
| Model Family | Deployment 背后的模型 ID | `gpt-5.4-mini` | 否 |

最容易混淆的是 **Deployment Name** 和 **Model Name**。

例如管理员部署的模型可能是 `gpt-5.4-mini`，但部署名称可以被设置为：

```text
data-analysis-chat
```

项目里的 `AZURE_OPENAI_DEPLOYMENT` 必须填写 Chat 部署名称 `data-agent-chat`，`AZURE_OPENAI_MODEL_FAMILY` 则填写底层模型 `gpt-5.4-mini`。Hybrid RAG 可选使用独立的 `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`，推荐部署 `text-embedding-3-small`；未配置时自动使用离线确定性特征哈希 Embedding。

### 当前推荐模型

本项目推荐 `gpt-5.4-mini`（GA），而不是 `gpt-4.1-mini` 或预览版 Chat 模型。它支持 Chat Completions、Structured Outputs、Functions/Tools 和 Parallel Tool Calls，适合任务规划、回答合成和 LLM-as-Judge。当前 Gateway 会使用 Azure `/openai/v1`，省略 GPT-5 不支持的 `temperature`，并默认设置 `reasoning_effort=low`。

`gpt-5.4` 全量版能力更强，但 Global 按量价格约为输入 `$2.50/M`、输出 `$15/M`；`gpt-5.4-mini` 约为输入 `$0.75/M`、输出 `$4.50/M`。在当前多次规划调用的 Agent 链路中，mini 更适合作为第一轮质量/成本平衡。价格以 Azure 订阅和部署页面当天显示为准。

## 2. 你需要从哪里获得配置

### 情况 A：公司已有 Azure OpenAI

这是最推荐的方式。联系领导、Azure 管理员或 AI 平台负责人，说明：

> 我正在开发一个内部业务数据问答 Agent，需要一个支持 Chat Completions 和 JSON 输出的 Azure OpenAI 模型部署，用于工具规划和基于工具证据生成回答。请提供资源 Endpoint、Deployment Name、允许使用的 API Version，以及符合公司安全规范的认证方式。

如果公司允许 API Key，再请管理员通过安全渠道提供 Key。若资源禁用了 Key，使用 Microsoft Entra ID 浏览器登录，并确认当前账号在资源上具有 `Cognitive Services OpenAI User` 角色。不要通过公开群、普通文档或代码仓库传递 Key。

你应获得：

```text
Endpoint
API Key（如果公司允许 Key 认证）
Deployment Name
Embedding Deployment Name（可选，推荐 text-embedding-3-small）
API Mode 或管理员要求的兼容模式
模型配额或每分钟 Token 限制
允许访问该 Endpoint 的网络/VPN 条件
```

### 情况 B：自己有 Azure 订阅

大致步骤：

1. 在 Azure Portal 或 Azure AI Foundry 创建/选择 Azure OpenAI 资源；
2. 在模型部署页面创建一个 Chat 模型 Deployment；
3. 如需 Azure 向量检索，再创建一个 `text-embedding-3-small` Deployment；
4. 记录你设置的两个 Deployment Name；
5. 从资源的 **Keys and Endpoint** 页面取得基础 Endpoint 和 Key；
6. 确认订阅、区域和模型配额允许调用；
7. 不要把 Key 写进源码或提交 Git。

模型选择建议：

- 首选 `gpt-5.4-mini` 的 Global Standard 或公司批准的 Data Zone 部署；
- 不要为了“更强”直接选择最昂贵模型；
- Planner 更重视结构化输出稳定性，Composer 更重视中文表达和长上下文；
- 当前代码让两者使用同一个 Deployment，后续有真实成本数据后再考虑拆成两个模型。

## 3. 在本机创建配置文件

进入项目：

```powershell
cd C:\Users\t-wcui\Downloads\ASR_agent\data_insight_agent
.\.venv\Scripts\Activate.ps1
```

生成本地配置模板：

```powershell
data-agent llm init
```

命令会创建：

```text
data_insight_agent/.env
```

`.env` 已被 `.gitignore` 排除。命令不会覆盖已有文件。

在 VS Code 中本地打开 `.env`，替换三个占位符：

```dotenv
AZURE_OPENAI_ENDPOINT=https://你的资源名.openai.azure.com/
AZURE_OPENAI_API_KEY=你的真实Key
AZURE_OPENAI_AUTH_MODE=key
AZURE_OPENAI_TENANT_ID=
AZURE_OPENAI_TOKEN_SCOPE=https://ai.azure.com/.default
AZURE_OPENAI_DEPLOYMENT=你的Deployment名称
AZURE_OPENAI_MODEL_FAMILY=gpt-5.4-mini
AZURE_OPENAI_API_MODE=v1
AZURE_OPENAI_REASONING_EFFORT=low
AZURE_OPENAI_MAX_COMPLETION_TOKENS=8192
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=你的EmbeddingDeployment名称
AZURE_OPENAI_API_VERSION=2024-10-21
DATA_AGENT_LLM_TIMEOUT=45
DATA_AGENT_LLM_RETRIES=2
```

注意：

- Endpoint 只填基础资源地址；
- 不要在 Endpoint 后拼接 `/openai/deployments/...`；
- Key 不要加多余引号或空格；
- Key 被组织策略禁用时设置 `AZURE_OPENAI_AUTH_MODE=entra`，并将 Key 留空；
- Entra 模式首次测试会打开浏览器登录，令牌使用本机加密缓存；
- Entra 模式配置完成后先主动运行一次 `data-agent llm login`。命令会保存不含访问令牌的账号记录，令牌本身由 Windows 加密缓存管理；后续 `ask`、`eval`、`ui` 和 `llm test` 都静默复用，不会每次弹浏览器；
- 企业会话失效时，普通业务命令不会自行弹窗，而会提示重新运行 `data-agent llm login`；
- 多租户账号应填写资源所在目录的 Tenant ID，避免令牌租户不匹配；
- Deployment 填 Azure 中的部署名称；
- Model Family 填底层模型 ID，例如 `gpt-5.4-mini`；
- GPT-5 推荐 `AZURE_OPENAI_API_MODE=v1`；`legacy` 仅用于旧部署；
- `reasoning_effort=low` 是当前成本、延迟和规划质量的默认平衡；
- Embedding Deployment 可留空；留空时 Hybrid RAG 使用离线特征哈希向量；
- 如果管理员要求其他 API Version，以管理员提供的版本为准。

## 4. 只检查配置，不访问网络

```powershell
data-agent llm status
```

正确状态类似：

```json
{
  "configured": true,
  "endpoint_host": "your-resource.openai.azure.com",
  "deployment": "data-analysis-chat",
  "model_family": "gpt-5.4-mini",
  "api_mode": "v1",
  "auth_mode": "entra",
  "tenant_id_configured": true,
  "reasoning_model": true,
  "reasoning_effort": "low",
  "embedding_deployment": "data-agent-embedding-small",
  "embedding_configured": true,
  "api_version": "2024-10-21",
  "api_key_present": true,
  "errors": []
}
```

这个命令不会输出 Key，也不会调用 Azure。

如果 `configured` 是 `false`，根据 `errors` 修正 `.env`。

## 5. 发送最小连接测试

Entra 模式第一次先执行：

```powershell
data-agent llm login
```

仅这个显式命令会打开浏览器。登录成功后再测试：

```powershell
data-agent llm test
```

这一步会发送一条非常小的 `ping -> OK` 请求。成功结果包含：

- `connected: true`
- Deployment Name
- 延迟
- Prompt Tokens
- Completion Tokens
- Total Tokens

它不会把 API Key 打印出来。

## 6. 使用真实 LLM Agent

连接测试成功后，先跑一个简单问题：

```powershell
data-agent ask "七种语言中哪个错误率最高？" --mode azure
```

再跑复合问题：

```powershell
data-agent ask "比较德语和法语 mediaControl 的错误率，并分别列出各自 3 条错误案例" --mode azure
```

日常先运行 6 条低成本 Azure smoke：

```powershell
data-agent eval --mode azure --dataset azure_smoke.json
```

当前基线为 6/6、LLM-as-Judge 4.7/5、无策略违规。Web 的“系统与评测”页面也默认运行这组 smoke，并显示本轮 calls、tokens、P95 和估算费用。

发布前如需运行完整 Azure Agent 回归集：

```powershell
data-agent eval --mode azure
```

这会真实产生模型调用和费用。Web 中选择 25 条完整回归时必须勾选费用确认；CLI 运行前也应人工确认。

只有人工确认结果和 Analysis/Governance 委派合理后，才更新对应基线：

```powershell
data-agent eval --mode azure --dataset azure_smoke.json --update-baseline
# 发布前完整基线：
data-agent eval --mode azure --update-baseline
```

之后相同数据集和模式会与各自 Azure 基线比较。Pass Rate、Intent Accuracy、Entity Accuracy、Tool Accuracy、Agent Accuracy、Answer Accuracy 或 Citation Accuracy 相对基线下降超过 5% 时，命令会报告 regression 并返回失败状态；Judge 失败、平均分低于 4.0、出现策略违规或 Retrieval 未达门槛时，禁止更新基线。

启动 Web：

```powershell
data-agent ui
```

配置正确后，左侧会出现：

```text
Azure OpenAI Agent
```

“系统与评测”页面会显示：

- 模型调用次数；
- 成功率；
- P50/P95 延迟；
- Prompt/Completion/Total Tokens 和估算费用；
- 按 operation 分类的调用次数；
- 失败类型。

这些指标持久化在本机 SQLite 中；每次评测还单独保存运行摘要和逐条 Judge 结果，但不保存 Prompt、回答正文或 API Key。

## 7. 一次问题会调用几次模型

Azure 模式不是只调用一次：

### 简单问题

```text
任务编排 Agent Planner：选择专业任务
简单且参数完整：专业 Agent 直接执行 ToolCall
工具执行
任务编排 Agent Planner：判断证据充分
AnswerSynthesizer 组件：组织回答
```

通常约 3 次 Chat 调用。首次构建或文档变化时，Hybrid RAG 还会调用 Embedding Deployment；未变化分片不会重复 Embedding。

### 复合问题

```text
任务编排 Agent Planner：拆分数据分析 / 数据治理任务
复杂专业子任务：专业 Agent Planner 按需细化工具，可并行
专业工具并行执行
任务编排 Agent Planner：读取 SpecialistResult 后结束或继续委派
AnswerSynthesizer 组件：组织回答
```

显式并行的复杂场景通常约 4–6 次 Chat 调用；如果后续调查依赖第一轮结果，则会增加一次任务编排规划轮次。

运行 Azure 评测时，每个用例还会增加一次 LLM-as-Judge 调用，用于相关性、完整性、清晰度、行动性和证据使用评分。确定性事实、工具和引用指标仍独立计算，不由 Judge 替代。

所以运行 25 条 Azure Multi-Agent 评测前应确认 Chat、Embedding 和 Judge 的配额与成本。任务编排、专业规划、回答合成、Embedding 和 Judge operation 的 token 与延迟都会被记录。

## 8. 常见错误

### `401 Unauthorized`

常见原因：

- Key 错误或已经轮换；
- Endpoint 和 Key 不属于同一个 Azure 资源；
- Key 中有多余空格。

处理：重新从同一资源获取 Endpoint 和 Key。若 Key 已泄露，应立即在 Azure 中轮换。

如果错误码是 `AuthenticationTypeDisabled`，说明资源禁用了 Key。将认证模式切换为：

```dotenv
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_AUTH_MODE=entra
AZURE_OPENAI_TENANT_ID=资源所在目录的Tenant ID
AZURE_OPENAI_TOKEN_SCOPE=https://ai.azure.com/.default
```

如果错误码是 `PermissionDenied`，并提示缺少 `deployments/chat/completions/action`，请在 Azure OpenAI 资源的 `访问控制 (IAM)` 中为当前账号添加最小角色 `Cognitive Services OpenAI User`。角色通常需要数分钟传播。

如果提示 `Token tenant ... does not match resource tenant`，说明登录到了错误目录；填写正确 Tenant ID 后重新登录。

### `403 Forbidden`

常见原因：

- 公司网络、Private Endpoint 或防火墙限制；
- 当前账号或资源策略不允许调用；
- 内容安全策略拦截。

处理：连接公司 VPN，并联系资源管理员检查网络和权限。

### `404 Resource not found` / `DeploymentNotFound`

常见原因：

- 把 Model Name 当成 Deployment Name；
- Deployment 建在另一个资源中；
- Endpoint 写错；
- API Version 不支持该部署。

处理：复制 Azure 部署页面显示的 Deployment Name，并确认资源和 API Version。

### `429 Too Many Requests`

表示配额或速率限制不足。处理方式：

- 等待后重试；
- 降低并发；
- 联系管理员提高 TPM/RPM 配额；
- 使用成本更低或配额更充足的 Deployment。

### Timeout / Connection Error

检查：

- 公司 VPN；
- 代理；
- 防火墙；
- Endpoint 是否可访问；
- `DATA_AGENT_LLM_TIMEOUT` 是否太低。

不要为了绕过公司网络规则把数据发送到未经批准的外部模型服务。

### JSON Mode / `response_format` 错误

Planner 依赖 JSON 输出。如果模型或 API Version 不支持 JSON Mode：

- 换用支持 JSON 输出的 Chat Deployment；
- 使用管理员建议的 API Version；
- 不要简单删除结构化输出校验，否则工具参数可能不可靠。

## 9. 安全规则

必须遵守：

- 不把 Key 发给我或其他聊天机器人；
- 不把 Key 写进 Python、README、截图或提交记录；
- `.env` 只保留在本机；
- 发现泄露立即轮换 Key；
- 使用公司数据前确认模型资源和数据区域符合公司政策；
- 当前 Agent 会把问题、工具 Schema 和工具 Observation 发给 Azure；敏感字段进入模型前应脱敏；
- 不使用个人购买的外部 API 处理公司内部数据，除非公司明确批准。

## 10. 你现在应该按什么顺序做

```text
1. 询问领导/管理员公司是否有可用 Azure OpenAI
2. 获取 Endpoint、Deployment、API Version 和安全认证方式
3. 运行 data-agent llm init
4. 只在本机 .env 填写配置
5. 运行 data-agent llm status
6. 运行 data-agent llm test
7. 用 --mode azure 跑一个简单问题
8. 跑一个复合问题并检查 Agent Trace
9. 最后运行 data-agent eval --mode azure
10. 人工检查 Trace 和回答后再运行 --update-baseline
11. 记录成功率、延迟和 token，决定是否调整模型或 Prompt
```

如果公司暂时不给 API，继续使用离线模式并不影响数据查询、Trace、Grounding 和回归测试。拿到合规 API 后再启用 Azure 模式即可。
