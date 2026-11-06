# 自用 fork 的 Windows 安装包构建流程（2026-11-06）

> 本文件记录「把本工作区变成自己的 git 仓库 → 推送到自己的 fork → 用 GitHub Actions
> 出 Windows 安装包」这条自用链路的实际做法、踩到的坑与判别依据。上游 PR 流程
> （`docs/DEV-HANDOVER.md` §8.2）对自用 fork 不适用：本仓库只服务自用版本。
>
> 一句话结论：**`gh workflow run 'Build Windows App' -f upload_artifacts=true` 跑完（本机实测 49 分钟），
> 从该 run 的 artifact 里拿 `dsh-pet-standalone-webm-chat-setup.exe`；想要长期可下载
> 就把同一批文件挂成 Release，因为 Actions artifact 只保留 30 天。**

## 一、仓库结构：upstream 保历史，origin 收自用提交

本工作区原本是从上游下载的 zip（无 `.git`）。为了让将来的上游更新还能 merge，
没有直接 `git init` 造一个无历史的仓库，而是：

1. `git clone --filter=blob:none --no-checkout https://github.com/MerZlin/dsh-pet-indesktop.git`（部分克隆，按需取 blob）
2. `git checkout -B main 34d810b`——`34d810b` 是上游当时的 HEAD（`chore(update): update.json 指向 v4.4.0`）
3. `git remote rename origin upstream` + `git remote add origin https://github.com/JackeyWilder/dsh-pet-indesktop.git`
4. 把克隆出来的 `.git` 复制进工作区，`git config core.autocrlf false`（全局是 `input`，会让 Windows 换行在 diff 里抖动），`core.safecrlf false`

基线判断依据：逐文件 `git hash-object` 对比本地工作区与 `upstream/main`（`34d810b`），
`AGENTS.md` / `CONTEXT.md` / `update.json` / `pyproject.toml` / `注意事项.txt` 等全部一致，
只有本次要提交的 6 个文件不同（4 改 2 新增）。所以「上游历史 + 一次本地提交」在内容上是
诚实的，`git status --porcelain` 正好列出这 6 项。

远端关系：

| remote | 地址 | 角色 |
|---|---|---|
| `origin` | `https://github.com/JackeyWilder/dsh-pet-indesktop.git` | 自己的 fork，CI 从这里跑 |
| `upstream` | `https://github.com/MerZlin/dsh-pet-indesktop.git` | 原作者仓库，只读拉更新 |

### 推送必须走代理（本机实测）

直连 `github.com:443` 在本机是断的（`Test-NetConnection github.com -Port 443` → False，
`git push` 报 `Failed to connect to github.com:443 after 21090 ms`），而系统 IE 代理
`127.0.0.1:7892` 可用、`gh`（走 libcurl + 自己的配置）能通。`git` 默认不读 IE 代理设置，
所以仓库级加一条：

```powershell
git config http.https://github.com.proxy 'http://127.0.0.1:7892'
```

只为 `github.com` 生效，不影响其它远端；换机场端口时改这一条即可。
（**不要把这条写进全局配置**，会波及所有仓库。）

## 二、跑一次 CI 出安装包

### 方式 A：手动 dispatch（本次用的方式，最快拿到产物）

```powershell
gh workflow run 'Build Windows App' -R JackeyWilder/dsh-pet-indesktop --ref main -f upload_artifacts=true
gh run list   -R JackeyWilder/dsh-pet-indesktop --workflow 'Build Windows App' --limit 3
gh run watch  <run-id> -R JackeyWilder/dsh-pet-indesktop
```

`upload_artifacts=true` 是关键：`.github/workflows/build-windows.yml` 默认
（`push: tags: v*` 之外）**不上传 artifact**，手跑不带这个输入会白跑一趟，产物只在
runner 的临时磁盘上。

### 方式 B：打 tag（产物直接进 draft Release，永久可下载）

```powershell
git tag -a v4.4.0-jw.1 -m 'self-use build'
git push origin v4.4.0-jw.1
```

`push: tags: v*` 触发同一条 workflow，并额外跑 `softprops/action-gh-release@v2`
（`draft: true`），把 `*-portable.zip` 与 `*-setup.exe` 挂进 draft Release。
draft 需要你自己点发布；不发布也能下载（仓库 owner 可见）。

**别用 `v4.4.0` 这类上游已用过的 tag 名**——fork 会继承上游 tag，重名标签会被拒。
安装包内部版本号来自 `pet/__init__.py` 的 `__version__`（当前 `4.4.0`），与 tag 名无关。

### 拿产物

```powershell
gh run download <run-id> -R JackeyWilder/dsh-pet-indesktop -D G:\Dev\agent\dsh\ci-artifacts
```

两个变体（都带 keyring，见 `scripts/build_onedir.ps1` 注释）。文件名里**没有版本号**
（`OutputBaseFilename={#MyAppShortName}-setup`），版本只体现在安装包的 ProductVersion 上：

| 文件 | 大小（实测） | 是什么 |
|---|---|---|
| `dsh-pet-standalone-webm-chat-setup.exe` | 287.9 MB | 主变体（WebM + Chat）安装包，`PrivilegesRequired=lowest`，装到 `{localappdata}\Programs\`，不需要管理员 |
| `dsh-pet-standalone-webm-chat-portable.zip` | 340.1 MB | 同上的绿色版（onedir：`dsh-pet-standalone-webm-chat.exe` + `_internal/`），解压即用 |
| `dsh-pet-standalone-webm-setup.exe` | 287.6 MB | no-chat 变体，独立 AppId（`{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}`）与显示名 `dsh-pet-standalone (WebM)`，可与主变体并存 |
| `dsh-pet-standalone-webm-portable.zip` | 339.8 MB | no-chat 绿色版 |

Actions artifact 保留 30 天。要长期保存与分享，把下载到的文件挂成 Release：

```powershell
gh release create v4.4.0-jw.1 -R JackeyWilder/dsh-pet-indesktop `
  --title 'dsh-pet self-use v4.4.0-jw.1' --notes '自用构建' `
  G:\Dev\agent\dsh\ci-artifacts\**\*-setup.exe G:\Dev\agent\dsh\ci-artifacts\**\*-portable.zip
```

## 三、workflow 到底做了什么（`build-windows.yml`，221 行）

`windows-latest` + Python 3.11 + `pip install -r requirements.txt pyinstaller pytest`，
然后严格串行：

1. `choco install innosetup`，取 `ChineseSimplified.isl`（中文安装向导）
2. **四段 pytest**：主套件（`--ignore` 4 个时序文件）→ webm lifecycle 族隔离（含一次重试）
   → low-priority warm 族隔离 → frameseq native-crash 隔离。任一红即整条 run 失败，
   后续构建步骤全部跳过——**测试是构建的前置门，不是并行项**
3. 精简内置资产：`rm -rf assets/characters/shenshen/frameseq/{events,random}` 与
   `videos/{events,random}`，只留核心闭包
4. `actions/cache@v4` 恢复帧序列素材缓存 → `scripts/prepare_frameseq_bundle.py --character shenshen --include ""`
   生成缺失的帧序列（**冷缓存这一步最慢**，本次首次跑实测见第五节）
5. `scripts/build_onedir.ps1 -Variant webm-chat` 与 `-Variant webm`（PyInstaller onedir）
6. `ISCC.exe` 编两个 Inno Setup 安装包（版本号从 `pet/__init__.py` 的 `__version__` 读）
7. 上传 artifact（条件：tag push 或 `upload_artifacts=true`）→ tag push 时再挂 draft Release

## 四、踩过的坑

### 1. 上游 main 有 3 条时间炸弹用例，会先把你拦在构建之前

`34d810b` 的干净 runner 上，主套件实测 `3 failed, 9018 passed, 17 skipped in 1133.52s`：

| 用例 | 现象 | 根因 |
|---|---|---|
| `test_balance.py::test_statutory_holiday_name_reports_free_days_only` | `assert '元旦节' is None` | 用例假设「2027 没有节假日安排表」，但 `lunar-python` 已内嵌 2027 表，`2027-01-01` 现在返回「元旦节」 |
| `test_balance.py::test_pricing_degrades_without_holiday_calendar_data` | `assert 'idle' == 'peak'` | 同上：元旦放假 → 高峰判断变空闲 |
| `test_memory_recall.py::test_manual_pin_remember_topic_unarchives` | `assert True is not True` | 用例时钟写死 `2026-10-21`，而 `ChatMemory.load()` 用真实时钟按 `TOPIC_ARCHIVE_DAYS=14` 做隐式老化，超过 14 天后「手动钉解除归档」又被标回 archived |

上游最近一次成功构建是 2026-10-22（tag `v4.4.0`），此后没再跑过 CI，所以这 3 条一直没被
发现。修法（commit `b9d10e9`，2 文件 +49/−15）**不改产品代码，只让用例不依赖外部时钟与
依赖内嵌的数据**：

- 两条 balance 用例用 `monkeypatch.setattr(festival_calendar, 'HolidayUtil', _NoTable)`
  把数据源打成空表来验证「未知 → 按星期降级」，并在前后 `statutory_holiday_name.cache_clear()`
  （该函数带 `lru_cache`，不清会把桩结果带到后续用例）
- memory 用例的 `clock` 改成 `lambda: datetime.now(timezone.utc)`，与 `load()` 的判据对齐

### 2. 手跑 workflow 默认不留 artifact

见「方式 A」里的 `upload_artifacts=true`。

### 3. 测试时长决定等待时间

主套件单次约 19 分钟（`1133.52s`），后面还有三段隔离套件。**不要**以为 run 卡死；
用 `gh run watch` 或按 step 名轮询即可。

### 4. 版本号只有一个来源，但它不改进文件名

`pet/__init__.py` 的 `__version__`（当前 `4.4.0`）决定安装包的 `ProductVersion`、开始菜单
/卸载项里显示的版本与 draft Release 的标题；**文件名不变**（`OutputBaseFilename={#MyAppShortName}-setup`，
见 `packaging/dsh-pet.iss:50`）。`update.json` 是另一套（在线更新用），两者不联动。

### 5. 产物没有代码签名

`setup.exe` 未签名（iss 里没有 SignTool），首次运行会吃 SmartScreen「Windows 已保护你的电脑」
提示，需要点「更多信息 → 仍要运行」。自用可以接受；要消掉得买证书并给 iss 配 `SignTool`。

### 6. 改 `REPO` 会连带改动角色包注册表的信任面

`pet/pack_download.py:143` 的 `parse_registry()` 用**同一个** `updater._trusted_https_url()`
校验注册表里的下载地址，而那个函数里 `github.com` 分支要求路径含 `/{REPO}/`。所以把
`REPO` 换成自有仓库的那一刻，任何仍写着 `github.com/MerZlin/...` 的注册表条目都会变成
「不受信任的地址」。本次就是这样被 CI 抓到一条用例
（`tests/test_pack_download.py::test_registry_allows_same_version_in_two_resolutions`，
run 42263637817 的唯一红，改指自有仓库即绿）。两条推论：

- 自己发布的角色包如果挂在**上游** Release 上，自用版将拒绝安装——要兼容得把上游路径
  加进白名单，而不是改 `REPO`。
- 排查这类红时先 grep `MerZlin` 与 `_trusted_https_url` 的所有调用点，别只看 `updater.py`。

### 7. 一次 tag 推送会触发三端构建

`build-linux.yml`、`build-macos.yml`、`build-windows.yml` 的 `on.push.tags` 都监听
`v*`，所以打一个 tag 会同时起三个 workflow（macOS 那次还直接 failure）。自用只需要
Windows，已把前两个 `gh workflow disable`；之后 tag 推送只构建 Windows 并创建 draft Release。

## 五、本次实跑记录

### 5.1 两次 run 的对照

| | run 42243503670（失败） | run 42247784467（成功） |
|---|---|---|
| head | `541fbe2`（只有 opencode 修复） | `b9d10e9`（+ 时间炸弹修复） |
| 主套件 | `3 failed, 9018 passed, 17 skipped in 1133.52s` | 通过 |
| 结论 | 失败，后续步骤全部 skipped | 全部 success，`Publish Release` skipped（非 tag 触发） |

主套件（`python -m pytest -q --ignore=…`，`QT_QPA_PLATFORM=offscreen`）耗时在两次 run 里
分别是 18.9 分钟与 26.4 分钟——同一个 `windows-latest` 池子上的机器差异就有这么大，
**不要把 20 分钟当成超时阈值**。

### 5.2 成功 run 的分步耗时（`gh run view <id> --json jobs`）

| 步骤 | 耗时 |
|---|---|
| Set up job / Checkout / Setup Python / 装依赖 / Inno Setup / 中文语言文件 | 共 ≈1.5 分钟 |
| Pytest（主套件） | **26.4 分钟** |
| Pytest（webm lifecycle 族隔离） | 0.4 分钟 |
| Pytest（low-priority warm 族隔离） | 0.6 分钟 |
| Pytest（frameseq 原生崩溃隔离） | 0.3 分钟 |
| 精简内置资产 / 算缓存 key / 恢复缓存 | ≈0 分钟（首次无缓存可恢复） |
| 准备帧序列素材（冷缓存） | **8.8 分钟** |
| 构建 onedir 两个变体 | 5.7 分钟 |
| 编译两个 Inno Setup 安装包 | 4.2 分钟 |
| 上传 artifact | 0.7 分钟 |
| **整条 run（含排队与清理）** | **48 分 49 秒**（15:57:04Z → 16:45:53Z） |

帧序列素材缓存在这一步之后会存下来（key 由源 webm 摘要 + 转换器实现 + 编码档 + 平台/
Python/Pillow/imageio-ffmpeg 版本算出），下一次构建会接近零成本。

### 5.3 产物（artifact `dsh-pet-standalone-windows-x86_64`，id 13522251741）

artifact 是 **1,313,377,956 B（1.22 GiB）的 zip**，解开是四个文件：

| 文件 | 字节 | SHA-256（本机 `Get-FileHash` 实测） |
|---|---|---|
| `dsh-pet-standalone-webm-chat-setup.exe` | 301,850,829 | `0BD0621F5C1B9B041A8E22CE796C4A41030984B88FC1EBD622CDD833400A0298` |
| `dsh-pet-standalone-webm-chat-portable.zip` | 356,665,046 | `455CE0DDA10C1D27CDD9E985DAEE9A109BA23608EEFC3BF51D6511952FD624AE` |
| `dsh-pet-standalone-webm-setup.exe` | 301,520,971 | `0F9A8314773AAC6A507F9E7DE0738CFB8634608FD17E34C202FAB6E8C2D83614` |
| `dsh-pet-standalone-webm-portable.zip` | 356,335,459 | `4DB4232BAC789A82416EACA1C75993B4A4200FC5E1DA92B8F8F6386A1BC80537` |

下载方式：`gh run download 42247784467 -R JackeyWilder/dsh-pet-indesktop -D G:\Dev\agent\dsh\ci-artifacts`
（走 `gh` 自己的网络栈；本机直连 github.com 不通，但 `gh` 走系统代理可用。1.22 GB 在本机
约 15–20 分钟）。**注意**：artifact 里装的是「webm-chat 变体」的安装包与绿色版各一份 +
「webm（no-chat）变体」同样两份，所以 1.22 GB ≈ 4 × 300 MB。

### 5.4 PE 元数据与安装位置（`(Get-Item …).VersionInfo`）

```
FileDescription : dsh-pet-standalone (WebM Chat) Setup
ProductName     : dsh-pet-standalone (WebM Chat)
ProductVersion  : 4.4.0
CompanyName     : merzlin            ← 来自 packaging/dsh-pet.iss 的默认值，自用版没改
FileVersion     : （空）
```

安装行为（`packaging/dsh-pet.iss`）：`PrivilegesRequired=lowest`（不弹 UAC）、
`DefaultDirName={localappdata}\Programs\{#MyAppShortName}`、`CloseApplications=yes`、
`ArchitecturesInstallIn64BitMode=x64compatible`；chat 变体用默认 AppId
`{3424d6cc-af3c-4383-8797-ab520b923aa6}`，no-chat 变体用
`{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}`，两者可并存。

### 5.5 绿色版实跑（真实启动，不是只看文件有没有解出来）

把 `dsh-pet-standalone-webm-chat-portable.zip` 解到临时目录（`dsh-pet-standalone-webm-chat.exe`
16,474,600 B + `_internal/` 3805 项），用**隔离的 `APPDATA`/`LOCALAPPDATA`/`TEMP`** 启动，
避免碰到本机正在用的开发版配置：

- 启动 15 秒后进程仍存活（`alive_after_15s=True`），stdout/stderr 均为空，随后主动结束
- 它自建的配置目录是 `%APPDATA%\dsh-pet-standalone-webm-chat\`（与开发版的 `dsh-pet` 分开）
- 运行日志（`pet-<pid>.log`）关键行：

```
01:15:26 INFO 功能模块激活：新装安装，初始启用集合 0 项（随下次配置保存落盘）
01:15:26 INFO dsh-pet-standalone 启动 (slot: 0, instance: )
01:15:26 INFO 当前形象: shenshen
01:15:26 INFO 渲染拓扑: overlay(单合成窗)
01:15:27 INFO 素材加载完成：shenshen 11 段动画
01:15:27 INFO 进入事件循环
01:15:32 INFO 帧序列首跑供给收尾：…帧序列映射 11 段
01:15:37 INFO 播放音效 path=…\_internal\assets\sounds\click.wav vol=0.70
```

即：打包产物能起、能加载动画、能进事件循环、能播内置音效，`_internal` 里 numpy/onnxruntime
等重依赖没有引发启动期导入失败。**未验证**：设置窗口、聊天、语音、识屏等需要网络/麦克风/API Key
的路径（自用版首次使用时自行确认）。

清理：临时解压目录与进程都已删除/结束，`Get-Process dsh-pet*` 为空。


### 5.6 tag 触发的一次完整出包与发布（`v4.4.0-jw.2`）

| 项 | 实测 |
|---|---|
| 触发 | `git push origin v4.4.0-jw.2`（此 Linux/macOS workflow 已 disable，只起 Windows 一条） |
| run | `42269416183`，**success**，**43 分 59 秒**（18:17:57Z → 19:01:56Z） |
| 主套件 | 通过（单步约 31 min；同一池子上一次是 26.4 min，波动不小，别拿去当超时阈值） |
| Release | <https://github.com/JackeyWilder/dsh-pet-indesktop/releases/tag/v4.4.0-jw.2> —— CI 建 draft，`gh release edit --draft=false` 发布 |

四个资产（数字来自 Release API 的 `size`/`digest`，`scripts/refresh_update_json.py` 直接抄）：

| 文件 | 字节 | sha256（前 16 位） |
|---|---|---|
| `dsh-pet-standalone-webm-chat-setup.exe` | 301,864,810 | `935941e771e4e2e7…` |
| `dsh-pet-standalone-webm-chat-portable.zip` | 356,667,798 | `0638e8b3bd47b8d0…` |
| `dsh-pet-standalone-webm-setup.exe` | 301,538,513 | `e9135c398f202cfe…` |
| `dsh-pet-standalone-webm-portable.zip` | 356,338,470 | `17cade43cf80c929…` |

发布后的实测（真实网络、走系统代理、无效凭据一律不涉及）：

- 无凭据 `curl -I` 该资产 → **HTTP 302**（跳到 assets CDN；私有仓库在此会是 404，见下文私有化一节）。
- `updater.latest_release()` → `version=4.4.0-jw.2`、4 个资产；`select_windows_installer()`
  选中 `…-webm-chat-setup.exe`，`size=301864810`、`sha256=935941e7…`（来自 API 的 `digest`）。
- `_trusted_https_url(自己的下载地址)` → `True`；`_trusted_https_url(github.com/MerZlin/…)` → `False`。
- 生成的 `update.json` 经 `_release_from_payload()` 解析出的 size/sha256 与 Release 一致。

## 六、自更新链路：已改指自用 fork（2026-11-07）

原状：`pet/updater.py:26` 写死 `REPO = 'MerZlin/dsh-pet-indesktop'`，`RELEASE_API` 取
上游 `/releases/latest`，`_UPDATE_JSON_URLS` 走 jsDelivr 的上游 `update.json`。自用版点
「检查更新」拉下来的是**上游**安装包，会覆盖自用改动。菜单入口见 `pet/app.py:5112`、
`pet/app.py:4223`、`pet/sprite_menu_facade.py:412`；没有启动期自动检查。

改法：`REPO` 换成 `JackeyWilder/dsh-pet-indesktop`。四个地址（`RELEASE_API`、`REPO_URL`、
三条 `_UPDATE_JSON_URLS`、以及 `_trusted_https_url` 里那条「github.com 路径必须含
`/{REPO}/`」的判定）全部由这一个常量派生，所以换轨是一处改动，不需要逐处打补丁。

三条连带处理，缺任一条都会让自更新链路半通：

1. **`update.json` 必须一起换**。它是 GitHub API 不可达时的 jsDelivr 回退清单，里面
   8 条 asset 全部指向上游 `releases/download/...`。只改 `REPO` 不改它，回退路径照样
   发上游包。本仓库的处理是**删掉上游那份**，每次发布后按下面「发布后重新生成
   update.json」重新生成——清单里的 `size`/`sha256` 直接从 Release API 的
   `size`/`digest` 抄，不用自己算。
2. **`QUARK_PAN_URL` 改指自己的 Release 列表**。上下文菜单项 `quark_download`
   （`pet/context_menus/registry.py:370` → `:285`）打开的就是这个常量，原来的夸克分享
   里放的是上游包。**标签文案仍是上游的「夸克网盘下载」**（`registry.py:91`，
   `tests/test_menu_layout.py:611` 也在断言这句文案）——改文案要连菜单标签与
   该用例一起动，本次不做，只保证点开的目的地不再是上游。
3. **回归用例**（`tests/test_updater.py`）：`test_updater_targets_self_fork_only`
   锁死 `REPO`/`RELEASE_API`/`REPO_URL`/`QUARK_PAN_URL`/三条 manifest 地址；
   `test_upstream_github_asset_urls_are_not_trusted` 断言上游
   `releases/download/...` 已落在信任白名单之外、自己的地址仍在白名单内；
   `test_download_asset_refuses_upstream_release_url` 端到端确认 `download_asset`
   对上游地址抛 `RuntimeError: untrusted URL`。

`download_asset` 要求资产同时给出 `size` 与 `sha256`，否则拒绝自动安装。实测 GitHub
Release API 的每个 asset 都带 `digest`（`sha256:...`）与 `size`，所以**只靠 API 就
够**，`update.json` 只是 API 不可达时的兜底。

### 发布后重新生成 update.json

用 `scripts/refresh_update_json.py`（从 Release API 抄 `size` 与 `digest`，不手算哈希）：

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7892'
python scripts/refresh_update_json.py --dry-run   # 先看内容
python scripts/refresh_update_json.py             # 写 update.json
git add update.json; git commit -m 'chore: 更新 update.json 指向 vX.Y.Z 自用构建'
```

脚本行为：取 `/releases/latest` → 按上游同名形状写 `{version, html_url, notes, assets}`；
`sha256` 取 `digest` 去掉 `sha256:` 前缀；任一 asset 缺 digest 或清单为空时**直接退出**，
不写半成品。提交后 jsDelivr 有缓存（`@main` 约 12 小时），期间回退路径可能还是旧清单
——正常，API 路径优先且可用。

**关于版本号**（本机实测，2026-11-07）：`version_parts()` 会把 tag 里的数字全部取出来，
所以 `version_parts('4.4.0-jw.2') == [4, 4, 0, 2]`，`is_newer('4.4.0-jw.2', '4.4.0')` 为
`True`——**带自增后缀的 tag 会正常触发更新提示**。但如果 tag 是 `v4.4.0-jw`（后缀没有
数字），就退化成 `[4, 4, 0]` 与 `4.4.0` 相等，不会提示。真要发新版时更稳妥的做法是把
`pet/__init__.py` 的 `__version__` 一并抬到更高（比如 `4.4.1`），它同时决定安装包的
ProductVersion；tag 名只需别与上游已有 tag 重名。

### 与之配套的两条仓库设置

- `PR Test Gate`（`.github/workflows/pr-test.yml`，`on.push.branches: [main]`）已
  `gh workflow disable`：Windows-only 自用仓库跑三端测试只会拖慢每次 push，且
  macOS job 会被后续 push 的 `cancel-in-progress` 取消而显示成失败，纯噪音。
- 构建 workflow 保持 active。它只在 `workflow_dispatch` 与 `push tag v*` 上触发，
  push main 不会自动构建。

## 七、将来同步上游

```powershell
git fetch upstream
git merge upstream/main          # 或 rebase；两者都会在本次改动的 6 个文件上要冲突判断
```

冲突面：`pet/chat/providers.py`（新增 4 个函数与两处调用）、`pet/vision.py`、
`pet/screen_capture/translate.py`（各 1 行 model + 1 行 headers）、`pet/updater.py`
（`REPO`/`QUARK_PAN_URL` 两个常量）、`docs/INDEX.md:148`（报告登记行）。测试文件冲突只可能
来自上游自己也去修那 3 条时间炸弹，或者也去改 `test_updater.py` / `test_pack_download.py`
里的地址——届时以「不依赖外部时钟/依赖内嵌数据」的原则取舍，别退回写死日期的写法。

## 八、如果改成 private 仓库（2026-11-07 核实）

**结论：CI 还能跑，但计费与自更新链路都会变糟，不建议。** 依据
[Actions 计费文档](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
与 [Actions limits](https://docs.github.com/en/actions/reference/limits)：

| 维度 | public（现在） | private |
|---|---|---|
| 标准 GitHub 托管 runner | **完全免费、不计量** | 计量，从计划的 included compute 里扣 |
| Free 计划额度 | 不适用 | 2,000 分钟/月（Windows 按 **2 倍**计费）→ 一次 44 分钟的构建约占 **88 分钟**配额，约 22 次/月 |
| 构建产物存储 | 不占你的存储额度 | 计入 storage 配额；Free 只有 **500 MB**，而一次构建的 artifact 是 **1.22 GiB** → 一次就爆 |
| Release | 任何人可直接下载 | **能建**，但资产下载要认证，未登录的 `curl`/更新器拿到的都是 **404** |
| jsDelivr 回退 | 可用（`@main` 抓公开仓库） | 不可用（jsDelivr 只服务公开仓库） |

对本项目的直接后果：`pet/updater.py` 的下载是**无凭据**的 HTTPS GET + 可选 jsDelivr
清单，仓库一旦私有，`检查更新` 就再也下不到东西；要让它继续工作只能把 PAT 塞进客户端
（等于把一个长期有效凭据分发给每个安装副本），不划算。

如果确实要隐藏代码，可行折中是：**代码留 private，另建一个 public 的 releases-only
仓库**承载安装包，把 `updater.REPO` 指到那个仓库；或者干脆保持 public——这是一份公开
项目的 fork，真正属于你的只有那几个文件的改动。

