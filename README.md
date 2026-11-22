# 汽水音乐 Android 自动化脚本（android 分支）

基于 AScript 框架的 Android 端自动化脚本，用于自动观看汽水音乐广告以获取免费听歌时长。
本分支由上游 iOS 版本（`main` 分支）移植而来，业务流程与 iOS 版保持一致，平台层 API 已切换为 `ascript.android.*`。

> 上游仓库：https://github.com/wei6718189/qishuiAScriptProject
> 个人 Fork：https://github.com/yuhuang123456/qishuiAScriptProject（`android` 分支）

## ⚠️ 合规与风险提示

自动化观看激励广告可能违反汽水音乐用户协议，存在被限流、风控甚至账号受限的风险。
本仓库仅作为 AScript 自动化技术学习与参考用途，请自行评估风险、遵守平台规则，控制使用频率与单日时长，不要用于批量刷量或商业用途。

## 功能特性

- 自动启动汽水音乐 App（优先包名 `com.luna.music`，启动后校验前台包名）
- 自动跳过开屏广告（OCR 精准识别「跳过」）
- 支持两种广告类型：视频广告 + 直播间广告
- 广告结束后自动领取奖励（两步：点「领取成功」→ 点「领取奖励」）
- 循环领取，连续多轮无广告自动退出；内置当日领取次数上限保护
- 全程 OCR + 图像识别为主，控件树检索仅作最后兜底

## 技术栈

| 模块 | 用途 |
| --- | --- |
| `ascript.android.system` | 应用启动 / 前台包名 / 等待包名 / 设备信息 |
| `ascript.android.system.Device` | 屏幕尺寸 `Device.display()` |
| `ascript.android.action` | 点击等动作执行（可切换 HID 通道） |
| `ascript.android.screen.Ocr` | 文字识别（paddle PP-OCR V5 / mlkit 双引擎） |
| `ascript.android.screen.FindImages` | 图像模板匹配（直播间关闭按钮等） |
| `ascript.android.node.Selector` | 控件检索兜底（仅 OCR / 找图失败后使用，可关闭） |

## 运行环境

- **平台**：Android（AScript App 已安装并连接真机）
- **AScript 运行模式**：`accessibility` / `root` / `hid` 可用 Selector 兜底；`screen_only` 模式下无控件树，Selector 自动跳过，仅走 OCR / 找图
- **OCR 引擎**：`paddle`（PP-OCR V5，默认）或 `mlkit`（谷歌 ML Kit，速度快）
- **点击通道**：默认 `action` 动作通道；如需物理级点击，可切换 HID（PC 虚拟 HID / ESP32 BLE 外设），见 `__init__.py` 中 `CLICK_CHANNEL`

## 项目结构

```
qishuiAScriptProject/
├── __init__.py              # 脚本主入口（AScript 工程约定，必须叫这个名字）
├── res/
│   └── img/
│       ├── logo.png
│       ├── qishuiyinyue.png            # 汽水音乐桌面图标（兜底启动用）
│       ├── img_zhibojian-close01.png   # 直播间关闭按钮模板图
│       └── img_1785263403257.png
├── .vscode/
│   └── settings.json       # 项目配置（平台 / 设备地址）
├── .trae/
│   └── rules/
│       └── ascript.md       # AScript AI 编码规则
└── .gitignore
```

## 快速开始

### 1. 环境检查

确保 AScript App 已安装在 Android 设备上，并已连接（局域网 IP 或 USB）。

### 2. 设备连接

在 AScript 客户端 / MCP 工具中自动连接或手动填写设备地址（`.vscode/settings.json` 中 `ascript.deviceId`）。

### 3. 运行脚本

```bash
# 方式一：直接运行工程
run_project(project="qishuiAScriptProject")

# 方式二：先上传再运行
upload_file(...)
run_project(...)
```

## 脚本流程

```
启动 App（包名 com.luna.music，校验前台）
    ↓
[用户手动进入第一个广告] 或 [脚本自动找入口]
    ↓
┌───────────────────────────────────────┐
│         核心广告领取循环                │
│                                       │
│  等待广告倒计时结束（最长 30s）        │
│    ├─ 视频广告：等「领取成功」出现     │
│    └─ 直播间广告：关直播间 → 等领取   │
│          ↓                            │
│  步骤 1：点右上角「领取成功」          │
│          ↓                            │
│  步骤 2：点弹窗「领取奖励」→ 进下一轮 │
│          ↓                            │
│  成功次数 +1，重复循环                │
│                                       │
│  连续 10 轮无广告 → 自动退出          │
│  达到当日上限 → 自动停止              │
└───────────────────────────────────────┘
```

## 关键配置

修改 [__init__.py](__init__.py) 头部常量即可：

| 常量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_NAME` | `"汽水音乐"` | App 名称（兜底用） |
| `APP_PACKAGE` | `"com.luna.music"` | Android 包名（优先使用，更稳定） |
| `OCR_ENGINE` | `"paddle"` | OCR 引擎：`"paddle"` / `"mlkit"` |
| `CLICK_CHANNEL` | `"action"` | 点击通道：`"action"` / `"hid"` |
| `ENABLE_SELECTOR_FALLBACK` | `True` | 是否允许 Selector 控件检索兜底 |
| `SELECTOR_MODE` | `2` | Selector 检索模式（accessibility 默认 2，拿不到试 3 / 0 / 1） |
| `MAX_IDLE_ROUNDS` | `10` | 连续多少轮没领到奖励后自动退出 |
| `DAILY_MAX_CLAIMS` | `30` | 当日领取次数上限保护 |
| `TAP_JITTER_PX` | `3` | 点击坐标随机偏移像素范围（±） |
| `POLL_MIN` / `POLL_MAX` | `0.2` / `0.5` | 轮询间隔随机区间（秒） |

## 稳妥 / 低介入策略说明

在实现功能的前提下，脚本尽量降低自动化特征，采取的措施：

1. **观察以 OCR / 找图为主**：不反复 dump 控件树，Selector 仅在 OCR、找图都失败后作为最后兜底，且可整体关闭。
2. **拟人化节奏**：点击前后等待时间加随机抖动，轮询间隔在区间内随机，避免固定 `sleep` 节奏。
3. **坐标抖动**：点击目标中心时叠加小范围随机偏移，避免每次命中完全相同的像素点。
4. **点击通道可切换**：默认 `action` 通道；如需更接近物理输入，可切换 HID（虚拟 HID 或 ESP32 蓝牙外设），在 `_hid_click()` 中接入对应插件 API。
5. **前台包名校验**：关键点击决策前确认前台仍是汽水音乐，避免在其他 App 上误点。
6. **额度保护**：连续空跑自动退出 + 当日次数上限，避免长时间高频运行。

> 说明：以上措施只能降低常规自动化特征，**不能**也不应用于对抗平台安全风控（如隐藏 Root、伪造设备指纹、绕过检测 SDK 等）。如设备本身已被风控，应停止使用并遵守平台规则。

## OCR 区域设计原则

所有搜索区域都基于屏幕百分比动态计算，适配不同分辨率。区域比例沿用 iOS 版实测值，Android 端若 UI 布局有差异，需在真机上用图色工具重新校准：

| 区域 | 用途 | 坐标范围近似 |
| --- | --- | --- |
| 开屏跳过按钮 | 跳过广告 | x:80%~97%, y:6%~10% |
| 中间「继续观看」 | 广告中途恢复（防误点底部下载） | x:7%~93%, y:25%~68% |
| 弹窗「领取奖励」 | 奖励按钮（仅限中间弹窗区） | x:13%~87%, y:25%~70% |

## 注意事项

1. **资源文件**：`res/img/` 下的图标和模板图来自 iOS 截图，Android 分辨率 / 圆角 / 配色不同，模板匹配置信度可能偏低；建议在 Android 真机上重新裁剪模板图后替换。
2. **不要写 `if __name__ == "__main__"`**：AScript 工程入口不等于 `__main__`，主体代码直接写在顶层即可。
3. **当日额度**：汽水音乐每日有广告领取上限，连续多轮没领到会自动退出，不要反复空跑。
4. **Selector 模式**：`dump_ui_tree` 与 `Selector(mode=)` 必须使用同一个 mode；`screen_only` 模式下 Selector 不可用。
5. **包名核对**：若后续汽水音乐更换渠道包名（如车机版），以应用商店 / 查包工具实际显示为准，修改 `APP_PACKAGE`。

## 相关文档

- [AScript Android API 文档](https://www.ascript.cn/docs/android/guide/api/)
- [AScript 编码规则](.trae/rules/ascript.md)
- 主脚本：[__init__.py](__init__.py)
