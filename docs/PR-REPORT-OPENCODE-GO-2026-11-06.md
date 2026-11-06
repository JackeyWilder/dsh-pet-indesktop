# OpenCode Go 端点支持（`x-opencode-session` + 模型 ID 还原）— 2026-11-06

> **基线**：无 commit 可引用——本工作区是上游仓库的 zip 副本，**不是 git 工作树**
> （`git status` → `fatal: not a git repository`），所以本轮没有分支、没有提交，
> 交付物是工作区文件本身（详见 §八）。
> **日期**：2026-11-06
> **范围**：6 个文件（实现 3、测试 1、文档 2——本报告 + `docs/INDEX.md` 登记）
> **关联**：[issue #368](https://github.com/MerZlin/dsh-pet-indesktop/issues/368)、
> [opencode 官方 Go 文档](https://opencode.ai/docs/go/)（重定向到 `/v2/docs/console/go/`）

## 一、核心特性

issue #368 的现象：在 AI 设置里填 opencode 的 Go 套餐端点
（`https://opencode.ai/zen/go/v1` + `opencode-go/deepseek-v4.1-flash` + 有效 Go Key），
「测试连接」**固定失败**，报 `请求失败（HTTP 400）：Request is missing
x-opencode-session and cannot be routed efficiently`。

根因有两条，都在 HTTP 组包这一层，与用户的 Key 无关：

1. **Go 端点要求每个请求带稳定的 `x-opencode-session`**。缺这个头时服务端在
   **鉴权之前**就 400 打回（`MissingSessionID`）；而 `build_browser_headers()`
   只发 UA / Accept / Accept-Language / Cache-Control / Pragma。
2. **`opencode-go/<id>` 是 opencode 自身配置文件的写法，不是直连 HTTP 的 model id**。
   官方 Endpoints 表里直连用的都是裸 id（`deepseek-v4.1-flash`、`kimi-k3`）；
   把配置里的前缀原样发出去，服务端只会当成"不存在的模型"。

本机对同一个无效 Key 的实测三连（完整命令与输出见 §五）：

| 端点 | 带 `x-opencode-session` | 结果 |
|---|---|---|
| `/zen/go/v1/chat/completions` | 不带 | **400** `MissingSessionID`（发生在鉴权之前） |
| `/zen/go/v1/chat/completions` | 带 | **401** `Invalid credential`（这才轮到 Key） |
| `/zen/v1/chat/completions` | 不带 | **401** `Invalid credential`（Zen 不校验该头） |

所以"400 → 401"就是这次修复的可用判据：**换到鉴权阶段，说明请求结构已经被接受**。

修复后用户看到的变化：同一个 Go 端点配置，「测试连接」从
`请求失败（HTTP 400）：Request is missing x-opencode-session…` 变成
`认证失败（HTTP 401）：Invalid credential`（Key 有效时即"连接成功"），
聊天/流式回答、截图问答、截图翻译四条真实发包路径也一起带上该头并还原模型名。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 端点专属请求头 | `endpoint_headers(base_url, extra)` = 浏览器头 + 调用点业务头 + opencode.ai 的 `x-opencode-session` |
| 2 | 会话 ID 进程内稳定 | 模块级 `uuid4().hex` 常量，不是每请求新生成（换 ID = 主动制造 400） |
| 3 | 模型 ID 还原 | `endpoint_model(base_url, model)`：opencode.ai 上把 `opencode-go/<id>` 还原成裸 `<id>` |
| 4 | 四条发包路径统一 | 测试连接 / 聊天流式（含检索工具轮）/ 截图问答 / 截图翻译共用上面两个函数 |

**红线 / 不变量**：

- `opencode.ai` 之外的 provider，**请求头与请求体逐字节不变**（回归用例逐个 provider 断言）。
- 形近域名（`notopencode.ai`、`opencode.ai.example.com`）不得命中——判据是
  `host == 'opencode.ai' or host.endswith('.opencode.ai')`，不是裸 `endswith`。
- 不新增配置键、不新增设置项、不新增第三方依赖、不新增线程与网络请求。
- 视觉独立端点（`vision_same_as_chat=False`）的 Key 纪律不变：不给它发聊天 Key。

## 二、修改文件说明

> **行数来源**：本工作区不是 git 仓库，`git diff --numstat` 不可用。下表用
> 「把改动逐段复原成原文 → 与当前文件做 `difflib` 差分」实算（等价于 numstat 的
> 增删行语义），命令见 §六末。**这是本报告唯一一处偏离模板的取值方式**，
> 原因是工作区没有版本库，不是省略证据。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/chat/providers.py` | +63 / −4 | 新增 `_is_opencode_base` / `_opencode_headers` / `endpoint_headers` / `endpoint_model` 与模块级会话常量；`test_connection()` 与 `OpenAICompatibleProvider._open()` 改用前两个函数 |
| `pet/vision.py` | +3 / −3 | `_post_vision_request()` 的端点改由 `endpoint_headers(base_url, ...)` / `endpoint_model(base_url, ...)` 产出（`base_url` 是解析后的视觉或聊天端点） |
| `pet/screen_capture/translate.py` | +3 / −3 | `translate_text()` 同上，按 `provider.base_url` 取头与模型名 |

逐处说明（为什么不是"顺手加一行"）：

- **`providers.py::test_connection`**（设置页「测试连接」的真实入口）：原来
  `'model': config.model` + `build_browser_headers(...)`。改成
  `endpoint_model(config.base_url, config.model)` +
  `endpoint_headers(config.base_url, {...})`——用户报的就是这一条路径。
- **`providers.py::_open`**：流式回答、`_research` 的工具轮、`_complete_json`
  三条 payload 构造点最后都从这里出去，所以模型名的还原放在 `_open`（用
  `dict(payload, model=...)` 复制，不改调用方传入的字典），头同理。**一处收口
  胜过三处各改一行**：将来再加一条 payload 路径不会漏。
- **`vision.py` / `translate.py`**：这两条也是"用户点一下就往 opencode.ai 发包"
  的路径。opencode 官方文档点名的问题客户端症状正是 *"session information
  arrives on some model paths, but is missing on others"*——只修聊天、留下视觉
  和翻译两条会 400 的路径，等于把同一个 bug 换个入口留着。改动各 3 行，且
  视觉走的是 `base_url`（可能是 `vision_base_url`），所以判定按实际发包端点算。
- **判定用主机名而不是路径**：官方文档的端点路径已经从 issue 里的
  `/zen/go/v1` 变成 `/inference/go/openai/v1`。按路径匹配会在下一次改版时失效，
  而 `opencode.ai` 这个域下所有端点都是 opencode 自家服务，整域带头是安全的
  （Zen 实测忽略该头，仍是 401 而不是 400）。

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_chat_opencode_go.py` | +318 / −0（新增） | 25 条：头命中与稳定性、形近域名反例、模型前缀还原、四条真实发包路径的 `Request` 级断言、Go 401 的用户可见文案 |

用例分层（全部离线，`urllib.request.urlopen` 一律替换成记录请求的假实现）：

- **端点头**：三个 opencode 端点（`/zen/v1`、`/zen/go/v1`、`/inference/go/openai/v1`）
  都带头且两次调用值相同；7 个非 opencode 输入（含两个形近域名、空串、`None`）不带头；
  调用点的 `Accept` / `Content-Type` 不被端点头覆盖。
- **模型名**：opencode 上 `opencode-go/deepseek-v4.1-flash` → `deepseek-v4.1-flash`；
  裸 id 原样；其它 provider 带前缀的名字**原样**（零影响）。
- **四条发包路径**：`test_connection`（断言真实 `Request` 的头与 body）、
  `_open`（流式出口）、`_stream_answer`（带一段真 SSE 走完解析）、
  `pet.vision._post_vision_request`、`screen_capture.translate.translate_text`。
- **失败路径**：Go 端点回 401 时 `test_connection` 返回
  `认证失败（HTTP 401）：Invalid credential`（用户可见文案不变）。

### 未改动（故意）

| 文件 | 为什么不动 |
|---|---|
| `pet/chat/web_search.py`、`pet/island/balance.py` | 它们用 `build_browser_headers()` 访问的是搜索引擎与余额查询接口，与 provider 端点无关；给它们加会话头没有任何收益，还会污染"这一课只属于 opencode provider 端点"的边界 |
| `pet/chat/models.py::ProviderConfig` | 方案 B（新增 `extra_headers` 字段 + 设置页「高级」输入框）没做：那是约 3 倍工作量，还多一个让用户手填错的入口，且不解决"每条发包路径都要记得带"的问题 |
| 设置页（`ai_settings_page.py` / `settings_dialog.py`） | 不新增任何控件；修复对用户完全透明 |
| `AGENTS.md`、`docs/NETWORK-PROXY-AND-VPN-2026-09-22.md` | 其余联网功能的"该不该走代理"清单不受影响，本条知识靠本报告 + INDEX 登记检索；为此加指针会把一份一次性端点适配写成长期规范 |

## 三、实现要点

```python
_OPENCODE_HOST = 'opencode.ai'
_OPENCODE_SESSION = uuid.uuid4().hex        # 模块级：进程内稳定

def _is_opencode_base(base_url) -> bool:     # 主机名判定（含子域、排除形近域名）
def _opencode_headers(base_url) -> dict:     # opencode.ai → {会话头}，其它 → {}
def endpoint_headers(base_url, extra=None):  # 浏览器头 + 业务头 + 端点专属头
def endpoint_model(base_url, model) -> str:   # opencode.ai 上去掉 opencode-go/ 前缀
```

1. **会话 ID 为什么是进程级常量**：官方要求"每个会话带稳定 ID"。进程级常量
   满足"稳定"，也让同一只桌宠的所有请求（聊天、检索、视觉、翻译）落在同一个
   会话上，便于服务端把空闲额度分摊给同一个用户。代价：进程内的多个聊天会话
   共享一个 ID——见 §七的已知限制。
2. **为什么把模型名还原放在 `_open` 而不是三个 payload 构造点**：`_open` 是唯一的
   HTTP 出口，流式、检索工具轮、非流式 JSON 都经过它；`dict(payload, model=...)`
   复制一份，调用方持有的字典不被改写。
3. **为什么用 `urllib.parse.urlsplit` 而不是字符串包含**：`'opencode.ai' in base_url`
   会把 `https://evil.example/opencode.ai/v1` 也算命中，且无法处理端口/路径；
   主机名比较 + 后缀边界（`.opencode.ai`）才是正确的判据，也顺手挡掉 `notopencode.ai`。
4. **异常兜底**：`urlsplit` 对含空格的非法 base_url 会抛 `ValueError`，这里按
   "普通 provider" 处理（返回 False），绝不让一个畸形配置在发包前抛出别的异常。
5. **备选方案与取舍**：方案 B（`ProviderConfig.extra_headers` + 设置页输入框）
   能让用户自定义任意头，但要新增配置键（三处登记）、设置页控件、序列化迁移，
   且把"该带什么头"变成用户的知识；方案 C（不做）不可接受——Go 套餐用户永远
   连不上。选方案 A，代价是判定硬编码了 `opencode.ai` 这个域名，这点在 §八登记。

## 四、性能分析

**方法（可复现）**：`python -c` 内联脚本，`timeit.repeat(number=200_000, repeat=5)` 取最好一轮；
内存用 `tracemalloc` 包住 10 万次调用。环境：Windows 11（22631）、Python **3.14.7**（embeddable）、
CPUID 实测输出 `Intel64 Family 6 Model 158 Stepping 10, GenuineIntel`（Coffee Lake Refresh 级
桌面/移动 CPU）。

| 指标 | 实测 | 归属 |
|---|---|---|
| `build_browser_headers({'Content-Type': ...})` | **1.540 µs/call** | 既有热路径基线 |
| `endpoint_headers('https://opencode.ai/zen/go/v1', {...})` | **3.872 µs/call** | 新增（opencode 端点） |
| `endpoint_headers('https://api.deepseek.com', {...})` | **3.317 µs/call** | 新增（其它 provider） |
| `endpoint_model('https://opencode.ai/zen/go/v1', 'opencode-go/…')` | **2.781 µs/call** | 新增 |
| `_is_opencode_base('https://opencode.ai/zen/go/v1')` | **2.035 µs/call** | 新增（上面两者的子调用） |
| 10 万次 `endpoint_headers` 后常驻堆增量 | **0.0 KiB** | 无缓存、无登记表 |

**结论**（逐条回答模板要求）：

1. **稳态开销有没有变、变了多少**：一次 HTTP 请求的组包成本从 1.540 µs 变成
   3.872 + 2.781 = **6.653 µs**，净增 **+5.113 µs**。一次聊天请求的网络往返
   在 ≥1 s 量级（本机对 opencode.ai 的实测往返数百毫秒起），即**净增占端到端
   耗时的 0.0005% 以下**；对本地 provider（`127.0.0.1` 上的推理服务）同样是
   微秒级对毫秒级。稳态用户感知不到。
2. **新增路径的绝对成本与触发频率**：三条都是"每次发包各一次"——
   `test_connection` 用户点一下算一次；聊天每一轮一次（开联网检索时该轮多一次
   工具轮，也是每轮一次）；截图问答/翻译各一次。没有任何按帧、按 tick、按
   定时器触发的调用。
3. **有没有新的系统调用 / 网络 / 磁盘 / 线程**：没有。改动只做 `urlsplit` 字符串
   解析与 dict 合并，全部在已存在的 HTTP 请求发出之前的内存里完成；不新增
   socket、文件、线程、定时器。
4. **内存与缓存有没有增长**：0 增长（实测 10 万次后 0.0 KiB）。唯一新增的常驻
   内存是一个 32 字符的模块级字符串（`uuid4().hex`）。

## 五、实机运行记录

全部在本机真实环境跑，**不是 CI、不是 mock**；网络是真的打 `opencode.ai`，
用的是**故意无效的探测 Key**（分片拼接，避免日志扫描误报）。

`方法`：单条 `python -c` 内联脚本（`sys.path.insert` 指向本仓库），输出重定向到文件
再用 UTF-8 读回，避免 PowerShell 控制台 GBK 乱码。原始输出的关键行：

```
== 端点归一化 ==
 go : https://opencode.ai/zen/go/v1/chat/completions
 zen: https://opencode.ai/zen/v1/chat/completions

== 修复前：裸 urllib + 同款浏览器头，不带 x-opencode-session ==
  400 {"type":"error","error":{"type":"MissingSessionID","message":"Request is missing x-opencode-session and cannot be routed efficiently. Please see https://opencode.ai/v2/docs/console/go/#where-can-i-use

== 修复后：项目 test_connection（设置页“测试连接”真实路径） ==
 go: ok=False 认证失败（HTTP 401）：Invalid credential
 zen: ok=False 认证失败（HTTP 401）：Invalid credential

== 修复后：聊天流式出口 _open 真实路径 ==
 ProviderError status= 401 -> HTTP 401: Invalid credential

== 零影响：非 opencode 端点 ==
  (False, '网络连接失败：[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1082)；如为代理/梯子拦截或自签名证书，可在 AI 设置中勾选"跳过 SSL 证书验证"后重试')
== Python == 3.14.7
```

逐条对应模板要求的四类证据：

- **根因/前提的现场复现**：第一段 400 `MissingSessionID`——用产品同款浏览器头
  但不带会话头，此刻在真实网络上稳定复现 issue 描述的报错（同一 body、同一 model）。
- **用户可见行为的确认**：第二段是**产品函数** `pet.chat.providers.test_connection()`
  的真实网络返回，即设置页点「测试连接」时用户看到的文案。修复前是
  `请求失败（HTTP 400）：…missing x-opencode-session…`，现在是
  `认证失败（HTTP 401）：Invalid credential`——错误类别从"请求不合法"变成"凭据不对"，
  说明服务端已经接受了请求结构（缺头时它根本走不到鉴权）。**没有有效 Go Key，
  所以"连接成功（HTTP 200）"这一步本机拿不到，见下条。**
- **边界与失败路径**：第四段把 base_url 指到不存在的主机，仍走原有的
  `网络连接失败：…` 分支（并带证书提示），证明新增的头/模型名逻辑没有改变其它
  provider 的错误路径；形近域名（`notopencode.ai`）不带头由单测断言。
- **无法自动验证的能力（为什么不能自动）**：真实补全（200 + 模型回答）需要
  **有效 Go 订阅凭据**，本机只有无效探测 Key，因此无法验证"能聊起来"这一段。
  排查证据不是"我猜它能通"，而是服务端状态码的位移：400 `MissingSessionID`
  （请求结构被拒）→ 401 `Invalid credential`（只差凭据）。另外 `_open` 真实网络
  返回的是 `ProviderError status=401`，说明**流式聊天这条出口**同样已经带上头——
  缺头时它会是 400。Key 有效时这两条路径即可继续（用户侧确认留给用户在真机上做）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | **All checks passed!** |
| 聚焦（新用例） | `pytest -q tests/test_chat_opencode_go.py` | **25 passed in 1.50s**（实现前为收集期 `ImportError`，后绿） |
| 受影响族 | `pytest -q tests/test_chat_subsystem.py tests/test_vision.py tests/test_chat_opencode_go.py tests/test_architecture.py` | **206 passed, 2 skipped in 30.65s** |
| 受影响族**满载 3 遍** | 同上，7 个忙等进程压满 8 核后连跑 3 次 | **3 ×（206 passed, 2 skipped）**：25.49 s / 23.87 s / 22.96 s |
| 全量 | `python -m pytest -q`（`QT_QPA_PLATFORM=offscreen`） | **9047 passed, 44 failed, 13 skipped, 3 errors in 2678.92s**（44 红逐族归因见下，**无一条落在本次改动上**） |
| 对照实验（A/B） | 把 3 个实现文件就地回退到改动前，重跑全量里那 44 项失败 | **43 failed, 3 errors**——同一批红；唯一差异是延迟预算用例在负载下的抖动（见下） |
| 断言有效性（消融） | 运行期把 `endpoint_headers`/`endpoint_model` 换回旧行为，重跑新用例 | **21 failed, 4 passed**（4 条是"其它 provider 原样"用例，新旧两版都成立，属预期） |
| 架构红线 | 含在受影响族里（`tests/test_architecture.py`） | 全绿 |

**先红后绿证据**：新用例先于实现落地，首跑是
`ImportError: cannot import name 'endpoint_headers' from 'pet.chat.providers'`
（收集期红），实现后 25 passed；中途一次真实失败也说明断言有效——最初
`_stream_answer` 用例把 `_open` 打成桩，于是断言看到的是**未经还原**的
`opencode-go/…`，测试红并促使把断言挪到真实发包口（见 §三第 2 条）。

### 全量 44 红 + 3 error 的逐族归因（都不是本次改动）

本机是 **Python 3.14.7 embeddable 发行版**（`python314._pth` ⇒ `sys.flags.isolated=1`，
cwd 与 `PYTHONPATH` 都不进 `sys.path`），且只装了 `requirements.txt` 的一部分依赖。
44 红按原因分族（每一族都实测到根因行，不是"大概是环境"）：

| 族 | 数量 | 根因（实测） |
|---|---|---|
| 子进程 `python -c "import pet…"` | 16 | 子进程拿不到仓库包：`ModuleNotFoundError: No module named 'pet'`（embeddable 的 `_pth` 让 cwd 不进 `sys.path`，而测试不设 PYTHONPATH）；首跑更早的表现是 GBK 控制台解码失败（`UnicodeDecodeError: 'gbk' codec can't decode byte 0xab` → `proc.stderr` 为 None）。含：配置并发锁(4+3 err)、上下文菜单动画、抽签跨进程、岛扩展清单、overlay 单实例(2)、profile 快照纯导入、插件清理、TTS 凭据、应用图标新建进程、动画 worker、app 懒加载(2) |
| 探针失败模拟不生效 | 10 | 用例把 `os`/权限探针打成抛错，本机（Python 3.14）上探针仍返回成功 → 产品如实报"成功"，断言 `探测失败 = 清空未确认完成` 落空。含 `proactive_memory_concurrency`(8)、`desktop_pet_features`(1)、`agent_link_dep_specs`(1) |
| `screen_capture_ocr` 真机识别 | 8 | 仓库不带 rapidocr 模型文件，worker 起不来（`test_real_worker_*` 整族） |
| 导入纯净性断言 | 4 | 多装了 `numpy`/`cv2`/`onnxruntime`/`edge-tts` 等，`sys.modules` 预热断言（`features`、`fps_monitor`、`voice_chime_service` ×2）不再成立 |
| `application_icon` shell 语法检查 `[linux]/[macos]` | 2 | 用例调 `C:\windows\system32\bash.EXE`（**WSL 启动器**）→ 路径被吞成 `G:Devagentdshdsh-…`，退出码 127 |
| `balance` 假日历降级 | 2 | 本机装了 `lunar-python`，"无日历数据 → 降级"分支不可达（用例期望 `元旦节 is None`） |
| `memory_recall` 归档语义 | 1 | 归档标记与用例期望相反（A/B 回退后同样红） |
| `chat_roleplay` 动作配对时延预算 | 1 | 满载（44 分钟全量套件同时跑）时超预算；单独复跑 3 次 3/3 通过（1.50 / 1.48 / 1.50 s） |
| **合计** | **44** | 另有 3 个 error 是配置并发锁那一族的用例级错误（同第 1 族） |

**A/B 对照怎么做的**（这是"红不是我的锅"的硬证据，也是本报告唯一一次改动落地文件）：

1. 备份 3 个实现文件，记录 sha256（`providers.py e22885c0…`、`vision.py ce898d21…`、
   `translate.py a8f430b2…`）；
2. 用脚本把 §二 的每处改动逐段 replace 回原文，断言每处**唯一匹配**，并要求
   行数回到 401→**342**、479、134；差分实得 `+4/−63`、`+3/−3`、`+3/−3`
   ——正好是 §二 增删行数的镜像，说明 §二 的 numstat 取值是准的；
3. 重跑全量里那 44 项：**43 failed + 3 errors**（同样那批），唯一差异是
   `chat_roleplay` 的时延预算用例（见上表末行：负载抖动，新旧两版单独跑都过）；
4. 从备份恢复并逐个比对 sha256（3/3 `match=True`），工作区回到修复态。

**为什么可以进一步断定与本改动无关**：改动对**非 opencode 主机**是恒等变换——
`endpoint_headers(base, extra)` 逐字节等于 `build_browser_headers(extra)`，
`endpoint_model(base, model)` 原样返回；上面 44 项里没有一条把 `base_url` 指到
`opencode.ai`（唯一会走新分支的是新增的 `tests/test_chat_opencode_go.py`，它 25/25 全绿）。

### 行数取值命令（替代 `git diff --numstat`）

```python
# 把 §二 每处改动逐段 replace 回原文 → 与当前文件 difflib 差分，
# 统计 replace/delete 为删除行、replace/insert 为新增行
python -c "…difflib.SequenceMatcher(None, orig, cur).get_opcodes()…"
```

输出：`pet/chat/providers.py +63 -4 (342→401 行)`、
`pet/vision.py +3 -3 (479 行)`、`pet/screen_capture/translate.py +3 -3 (134 行)`、
`tests/test_chat_opencode_go.py +318 -0（新增）`。

## 七、已知限制与后续

1. **会话 ID 是进程级，不是"每个聊天会话一个"**：同一只桌宠的所有请求共享一个
   `x-opencode-session`。官方措辞是"每个会话带稳定 ID"，进程级常量满足"稳定"，
   但无法让服务端把"聊天 A"和"聊天 B"当两个会话。真要按会话分，需要把 chat 的
   会话身份传进 providers 层（新增参数或上下文），本轮不做——收益不明显，而
   issue 的诉求是"先别 400"。触发条件：若将来 opencode 开始按会话限额且用户
   反馈"换个聊天窗口额度没重置"，再按会话生成。
2. **真实 200 未验**（无有效 Go Key）。见 §五最后一条的排查证据。
3. **域名硬编码**：`opencode.ai` 作为判据写在 `_OPENCODE_HOST`。若 opencode 换域，
   需要改一行常量；若别的 provider 恰好落在该域下（不存在这种情况），它也会收到
   这个头——该头对不校验的服务端是无害的（Zen 实测忽略）。
4. **本工作区不是 git 仓库**：没有分支、提交、PR，`git diff --numstat` 与
   `git revert` 都不可用；代码与文档以工作区文件交付，需要用户在自己的 fork 里
   重新落一次提交（建议 `feat: 支持 opencode Go 套餐端点（会话头 + 模型 ID 还原）`）。
   `docs/PR-MERGE-LESSONS` 里"stacked PR 冲突"一类风险因此不适用。
5. **本机验证环境的两个成因登记**（不是本改动的限制，但读 44 红时需要知道）：
   ① 解释器是 **embeddable 发行版**（`python314._pth`），任何 `subprocess` 起来
   再 `import pet…` 的用例都拿不到包；② 为了跑全量我按 `requirements.txt` 补装了
   `numpy` / `opencv-python-headless` / `onnxruntime` / `rapidocr` / `winrt-Windows.*`
   / `lunar-python`，其中 `lunar-python` 让 `balance` 的"无日历数据降级"分支不可达，
   另有两例"导入预热"类断言因多装了包而改变。这些都是**装了什么依赖**的后果，
   与 opencode 端点适配无关（A/B 对照已把这一点钉死，见 §六）。

## 八、风险与回滚

**影响面**：

- 只有 `base_url` 的主机名是 `opencode.ai`（或 `.opencode.ai` 子域）时行为改变；
  其余 provider 的头与 body 逐字节不变，有 4 组回归用例钉住。
- 新增的 `x-opencode-session` 是单值头，不进 `Authorization`、不落盘、不进日志；
  会话 ID 每次进程启动重新生成（无持久化，不涉及隐私标识）。
- 视觉独立端点（`vision_same_as_chat=False`）仍然只用自己的 Key；本次只加了
  头与模型名，Key 纪律未动。

**配置与迁移**：无新增配置键，无需三处登记，无落盘状态变更，无迁移。

**回滚**：还原 3 个实现文件（`providers.py` / `vision.py` / `translate.py`）即可，
没有残留状态；测试文件与本报告可直接删除或在下一轮报告里标注作废。
无 `git revert` 可用（见 §七第 4 条）。

**残余风险**：① 若 opencode 将来把 Go 端点迁到非 `opencode.ai` 域，本修复会静默
失效（症状仍是 400 `MissingSessionID`，报错文案能直接定位到本报告）；② 若官方
把"稳定会话 ID"升级为"必须每会话唯一且带校验"，需要按 §七第 1 条改造。
