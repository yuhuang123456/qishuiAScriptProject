# -*- coding: utf-8 -*-
"""
汽水音乐 Android 自动化脚本 — 自动看广告获取免费听歌权限
====================================================
本分支由 iOS 版（main 分支）移植到 Android 平台，基于 AScript 框架。

功能：
  1. 启动汽水音乐 App
  2. 全局弹窗清理（会员续费等）
  3. 监测右上角“免”字 / “立即解锁、立即领取”按钮，点击进入广告
  4. 等待广告倒计时结束（支持视频广告 & 直播间广告）
  5. 两步领取奖励（点“领取成功” → 点“领取奖励”），循环直到当日额度用完

技术栈（Android）：
  - 应用管理          ：ascript.android.system（open / wait_for_package / 前台包名）
  - 屏幕信息          ：ascript.android.system.Device.display()
  - 动作执行          ：ascript.android.action（click / Key）
  - 文字识别          ：ascript.android.screen.Ocr（paddle / mlkit 双引擎）
  - 图像识别（兜底）  ：ascript.android.screen.FindImages
  - 控件检索（兜底）  ：ascript.android.node.Selector（仅最后手段，默认少用）

稳妥 / 低介入策略（在实现功能前提下尽量降低被风控关注）：
  - 观察以 OCR / 找图为主，不反复 dump 控件树；Selector 仅作最后兜底且可关闭
  - 点击前后等待时间加随机抖动，坐标中心加小范围随机偏移，避免固定机器节奏
  - 点击通道可配置：默认 action 通道；如需物理级点击可切换 HID（见 CLICK_CHANNEL）
  - 关键动作前校验前台包名，避免在其他 App 上误点
  - 内置连续空跑退出 + 当日领取次数上限，避免长时间高频空跑

注意：自动化观看激励广告可能违反汽水音乐用户协议，存在账号受限风险。
      本项目仅作技术学习参考，请自行评估风险、遵守平台规则，控制使用频率。
"""

import os
import json
import time
import random
import traceback

# ============== 基础模块导入 ==============
from ascript.android import action  # type: ignore
from ascript.android import system  # type: ignore
from ascript.android.system import R, Device  # type: ignore
from ascript.android.screen import Ocr, FindImages  # type: ignore

# 跨天累计进度需要持久化（App 重启不丢），部分版本可能无此模块，做兜底
try:
    from ascript.android.system import KeyValue  # type: ignore
    _KV_AVAILABLE = True
except Exception:
    KeyValue = None  # type: ignore
    _KV_AVAILABLE = False

# Selector 仅在有控件树的模式（accessibility / root / hid）下可用；
# screen_only 模式下导入或检索可能失败，这里单独兜底，不影响主流程。
try:
    from ascript.android.node import Selector  # type: ignore
    _SELECTOR_AVAILABLE = True
except Exception:
    Selector = None  # type: ignore
    _SELECTOR_AVAILABLE = False


# ============== 全局配置 ==============

# 汽水音乐 App 名称 / Android 包名（包名优先，启动更快更稳）
APP_NAME = "汽水音乐"
APP_PACKAGE = "com.luna.music"

# 主循环控制
MAX_LOOP_COUNT = 50          # 最大循环次数（防止无限跑）
LOOP_INTERVAL = 3            # 每次主循环间隔（秒）
MAX_IDLE_ROUNDS = 10        # 连续多少轮没领到奖励后自动退出（判断当日额度用完）
DAILY_MAX_CLAIMS = 30       # 单日领取次数上限保护（达到即停，避免长时间高频运行）

# ========== 目标达成即停 ==========
# TARGET_MODE 三种模式：
#   "today"   —— 解锁「今日畅听」即停（默认，最省事）
#   "days"    —— 累计畅听天数达到 TARGET_DAYS 天（半月=15，整月=30）；每天跑一次累计
#   "minutes" —— 累计「免费听时长」达到 TARGET_MINUTES 分钟（需 App 内能读到该数值）
TARGET_MODE = "today"
TARGET_DAYS = 15          # TARGET_MODE="days" 时生效：累计目标天数
TARGET_MINUTES = 600      # TARGET_MODE="minutes" 时生效：累计目标分钟数

# 单次运行的单日领取规划（2026-11 起「今日畅听」需看满 4 次广告）
DAILY_CLAIM_TARGET = 4    # 满这么多次视为解锁 1 天畅听
CONTINUE_PRECLAIM = True  # 今日达成后，是否继续点「提前领明日畅听」多攒天数
MAX_CLAIMS_PER_RUN = 12   # 单次运行最多领取次数（含提前领），防止一口气刷太多

# 进度持久化
STATE_KEY = "qishui_progress"   # KeyValue 键名，记录跨天累计进度

# OCR 引擎：Android 可选 "paddle"（PP-OCR V5，高精度多语言）/ "mlkit"（谷歌 ML Kit，快）
OCR_ENGINE = "paddle"

# 点击通道：
#   "action" —— AScript 动作通道（无障碍 / root / hid 控件模式下可用）
#   "hid"    —— 外接 HID 通道（PC 虚拟 HID / ESP32 蓝牙外设），需自行接入对应插件，
#               接入方式见 _hid_click() 内注释
CLICK_CHANNEL = "action"

# Selector 控件检索兜底开关（True=允许在 OCR / 找图都失败后用 Selector；False=禁用）
ENABLE_SELECTOR_FALLBACK = True
# Selector 检索模式：accessibility 实战默认 2（SIMPLE|FILTERED），拿不到再试 3 / 0 / 1
SELECTOR_MODE = 2

# 拟人化参数
TAP_JITTER_PX = 3           # 点击坐标随机偏移像素范围（±）
BASE_TAP_WAIT = 0.8         # 点击后基础等待秒数
TAP_WAIT_JITTER = 0.25      # 点击后等待抖动比例（±25%）
POLL_MIN = 0.2              # 轮询间隔下限（秒）
POLL_MAX = 0.5              # 轮询间隔上限（秒）

# 区域配置（基于屏幕百分比，适配不同分辨率；安卓端 UI 若有差异需在真机上校准）
REGION_TOP_RIGHT = None      # 运行时根据屏幕尺寸动态计算
REGION_FULL = None
# 直播间关闭按钮模板图是否存在（启动时检查一次）
HAS_LIVE_ROOM_TEMPLATE = False


# ============== 工具函数 ==============

def log(msg: str):
    """统一日志打印"""
    ts = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def human_sleep(base: float, jitter: float = TAP_WAIT_JITTER):
    """拟人化等待：基础时长上加随机抖动，避免固定 sleep 节奏"""
    if base <= 0:
        return
    delta = base * jitter
    time.sleep(max(0.05, base + random.uniform(-delta, delta)))


def poll_sleep():
    """轮询间隔随机化"""
    time.sleep(random.uniform(POLL_MIN, POLL_MAX))


def screen_size():
    """获取屏幕尺寸 (width, height)，失败返回兜底分辨率"""
    try:
        display = Device.display()
        return int(display.widthPixels), int(display.heightPixels)
    except Exception:
        return 1080, 1920


def foreground_is_target() -> bool:
    """校验前台 App 是否为汽水音乐；拿不到前台包名时不阻断主流程"""
    try:
        pkg = system.get_foreground_app()
        if pkg:
            return pkg == APP_PACKAGE
    except Exception:
        pass
    return True


def _hid_click(x: int, y: int):
    """
    HID 通道点击（物理级输入，App 侧更难识别为自动化操作）。
    默认使用 AScript 内置虚拟 HID：from ascript.android.action import Hid; Hid.click(x, y)。
    若使用 ESP32 BLE 外设（官方插件 id=103），改为插件提供的 BleDevice.click(x, y)，
    具体 API 以插件文档为准，不要凭猜测写接口。
    """
    from ascript.android.action import Hid  # type: ignore
    Hid.click(x, y)


def safe_click(x: int, y: int, desc: str = ""):
    """
    安全点击：日志 + 异常捕获 + 坐标抖动 + 通道切换。
    后续如需切换点击通道，只需改 CLICK_CHANNEL 或本函数内部实现。
    """
    try:
        w, h = screen_size()
        jx = random.randint(-TAP_JITTER_PX, TAP_JITTER_PX)
        jy = random.randint(-TAP_JITTER_PX, TAP_JITTER_PX)
        cx = max(0, min(w - 1, int(x) + jx))
        cy = max(0, min(h - 1, int(y) + jy))
        log(f"点击 [{desc}] -> ({cx}, {cy})")
        if CLICK_CHANNEL == "hid":
            _hid_click(cx, cy)
        else:
            action.click(cx, cy)
        human_sleep(BASE_TAP_WAIT)
        return True
    except Exception as e:
        log(f"点击失败 [{desc}]: {e}")
        return False


def click_center():
    """点击屏幕中心（关闭弹窗的兜底策略）"""
    w, h = screen_size()
    if w > 0 and h > 0:
        safe_click(w // 2, h // 2, "屏幕中心")
    else:
        safe_click(200, 400, "默认中心")


def notify_safe(msg: str):
    """结束通知：Android 端如有 toast / notify 接口则调用，异常静默跳过"""
    try:
        fn = getattr(system, "notify", None) or getattr(system, "toast", None)
        if callable(fn):
            fn(msg)
    except Exception:
        pass


def init_regions():
    """根据屏幕尺寸初始化搜索区域，并检查资源文件"""
    global REGION_TOP_RIGHT, REGION_FULL, HAS_LIVE_ROOM_TEMPLATE
    w, h = screen_size()
    log(f"屏幕尺寸: {w} x {h}")
    # 右上角区域：x 从 60%~100%，y 从 0%~20%
    REGION_TOP_RIGHT = [int(w * 0.6), 0, w, int(h * 0.2)]
    REGION_FULL = [0, 0, w, h]
    # 检查直播间关闭按钮模板图是否存在
    try:
        template_path = R.img("img_zhibojian-close01.png")
        HAS_LIVE_ROOM_TEMPLATE = os.path.exists(template_path)
    except Exception:
        HAS_LIVE_ROOM_TEMPLATE = False
    if HAS_LIVE_ROOM_TEMPLATE:
        log(f"直播间关闭按钮模板图: ✅ 存在")
    else:
        log("直播间关闭按钮模板图: ❌ 不存在（将使用 OCR / Selector 兜底方案）")


# ============== 阶段一：启动 App ==============

def launch_app() -> bool:
    """启动汽水音乐 App（优先包名，等待进入前台；失败再找桌面图标兜底）"""
    log("=" * 50)
    log("阶段一：启动汽水音乐 App")
    try:
        system.open(APP_PACKAGE)
        if system.wait_for_package(APP_PACKAGE, timeout=8000):
            log("App 已进入前台，开始监控开屏跳过...")
            return True
        log("包名启动未确认前台，尝试应用名称启动...")
        system.open(APP_NAME)
        if system.wait_for_package(APP_PACKAGE, timeout=8000):
            log("App 已进入前台（名称启动）")
            return True
        log("应用启动未确认前台，尝试找图点击桌面图标...")
        return try_click_app_icon()
    except Exception as e:
        log(f"启动 App 异常: {e}")
        return try_click_app_icon()


def try_click_app_icon() -> bool:
    """兜底：通过找图点击桌面汽水音乐图标"""
    icon_path = R.img("qishuiyinyue.png")
    log(f"尝试找图点击桌面图标: {icon_path}")
    try:
        for attempt in range(3):
            pos = FindImages.find(icon_path, confidence=0.8)
            if pos:
                return safe_click(pos["center_x"], pos["center_y"], "汽水音乐图标")
            log(f"  第 {attempt + 1} 次未找到图标，重试...")
            poll_sleep()
        log("未找到汽水音乐图标，请检查图片或屏幕内容。")
        return False
    except Exception as e:
        log(f"找图点击图标异常: {e}")
        return False


# ============== 阶段一 B：开屏广告跳过 ==============

def skip_splash_ad(timeout: int = 5) -> bool:
    """
    启动 App 后立即检测并跳过开屏广告。
    在右上角小区域用 paddleocr 精准识别「跳过」文字；
    rect 按屏幕比例动态计算：x:80%~97%, y:6%~10%。
    """
    log("阶段一B：检测并跳过开屏广告（OCR 精准识别中...）")
    w, h = screen_size()
    rect_skip = [int(w * 0.80), int(h * 0.06), int(w * 0.97), int(h * 0.10)]

    deadline = time.time() + timeout
    scan_count = 0

    while time.time() < deadline:
        scan_count += 1
        try:
            results = Ocr.paddleocr(rect=rect_skip, pattern=r"跳过")
            if results and isinstance(results, list) and len(results) > 0:
                for item in results:
                    cx = item.get("center_x", 0)
                    cy = item.get("center_y", 0)
                    if cx > 0 and cy > 0:
                        text = item.get("text", "")
                        conf = item.get("confidence", 0) or 0
                        log(f"  [扫描#{scan_count} 命中] '{text}' 置信度={conf:.2f} ({cx},{cy})")
                        safe_click(cx, cy, "开屏跳过按钮")
                        human_sleep(0.2)
                        return True
        except Exception as e:
            log(f"  扫描#{scan_count} OCR 异常: {e}")

        poll_sleep()
    log(f"  扫描结束（共 {scan_count} 次），未检测到开屏跳过按钮")
    return False


# ============== 阶段二：弹窗清理 ==============

# 弹窗关键词黑名单（出现即尝试关闭）
POPUP_KEYWORDS = [
    "续费会员", "会员享专属", "开通会员", "立即开通",
    "看视频领取", "领取更多时长", "看视频得",
]

# 弹窗关闭按钮关键词
CLOSE_KEYWORDS = [
    "关闭", "叉", "×", "✕", "✖",
    "取消", "以后再说", "稍后", "暂不", "跳过",
]


def paddle_find_first(pattern: str, rect, log_label: str = ""):
    """
    用 Ocr.paddleocr 在指定小区域精准识别关键词，返回第一个有效命中结果 dict 或 None。
    pattern 是正则；rect = [x1, y1, x2, y2]。
    """
    try:
        results = Ocr.paddleocr(rect=rect, pattern=pattern)
        if results and isinstance(results, list):
            for item in results:
                cx = item.get("center_x", 0)
                cy = item.get("center_y", 0)
                if cx > 0 and cy > 0:
                    if log_label:
                        conf = item.get("confidence", 0) or 0
                        log(f"  [{log_label}] '{pattern}' -> '{item.get('text', '')}' "
                            f"置信度={conf:.2f} ({cx},{cy})")
                    return item
    except Exception as e:
        log(f"  paddle_find '{pattern}' 异常: {e}")
    return None


def close_membership_popup() -> bool:
    """
    检测并关闭“续费会员”弹窗。
    判定特征：底部出现“元开通”文字（如“12元开通”、“连续包月9元开通”）。
    处理：点击弹窗外部遮罩区域（屏幕中上方）关闭弹窗。
    返回：是否检测到并执行了关闭。
    """
    w, h = screen_size()
    # 底部“元开通”区域：x:4.4%~95.3%, y:87.7%~96.3%（按屏幕比例动态计算）
    bottom_rect = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]
    try:
        results = Ocr.paddleocr(rect=bottom_rect, pattern=r"元开通")
        if results and isinstance(results, list) and len(results) > 0:
            item = results[0]
            log(f"  [续费会员弹窗] 检测到底部 '{item.get('text', '')}'，点击弹窗外部关闭")
            try:
                safe_click(int(w * 0.5), int(h * 0.30), "弹窗遮罩区")
            except Exception as e:
                log(f"  点击关闭异常: {e}")
            human_sleep(1.2)
            return True
    except Exception as e:
        log(f"  续费会员弹窗检测异常: {e}")
    return False


# 兼容旧调用名
check_and_back_from_membership_page = close_membership_popup


def check_and_close_thirdparty_download() -> bool:
    """
    检测是否跳转到了第三方应用下载页面（广告倒计时结束、出现“领取成功”后可能触发）。
    判定特征：左上角出现“完成”按钮（下载页 / 广告落地页的关闭入口）。
    处理：点击“完成”关闭该页面，回到汽水音乐。
    """
    w, h = screen_size()
    # 左上角“完成”按钮区域：x:2%~19%, y:7.5%~11.5%
    rect_done = [int(w * 0.02), int(h * 0.075), int(w * 0.19), int(h * 0.115)]
    try:
        results = Ocr.paddleocr(rect=rect_done, pattern=r"完成")
        if results and isinstance(results, list) and len(results) > 0:
            item = results[0]
            cx = item.get("center_x", 0)
            cy = item.get("center_y", 0)
            if cx > 0 and cy > 0:
                log(f"  [第三方下载页] 检测到左上角 '完成'，点击关闭 ({cx},{cy})")
                safe_click(cx, cy, "'完成'按钮")
                human_sleep(1.0)
                return True
    except Exception as e:
        log(f"  第三方下载页检测异常: {e}")
    return False


def dismiss_popups(max_rounds: int = 3) -> int:
    """
    弹窗清理：只处理“续费会员”弹窗（底部“元开通”）。
    返回：已处理的弹窗数量
    """
    log("阶段二：弹窗清理（仅处理续费会员弹窗）")
    handled = 0
    for _ in range(max_rounds):
        if close_membership_popup():
            handled += 1
            human_sleep(1.0)
            continue
        break
    if handled == 0:
        log("  未检测到续费会员弹窗，无需清理。")
    log(f"弹窗清理结束，共处理 {handled} 个")
    return handled


def light_background_cleanup() -> bool:
    """轻量级背景清理：只检测续费会员弹窗并关闭。"""
    return close_membership_popup()


def _detect_membership_popup(rect_pay) -> bool:
    """内部辅助：检测底部是否有“元开通”续费会员弹窗。返回 True=有弹窗。"""
    try:
        results = Ocr.paddleocr(rect=rect_pay, pattern=r"元开通")
        return bool(results and isinstance(results, list) and len(results) > 0)
    except Exception:
        return False


# ============== 阶段三：监测“免”字按钮并进入广告 ==============

def wait_for_mian_button(timeout: int = 60, allow_mian: bool = True) -> bool:
    """
    监测进广告入口。按 allow_mian 决定是否搜索右上角“免”字：
      - allow_mian=True  (第1轮 / 开屏后场景)：
          1) 先搜“立即解锁”/“立即领取”弹窗按钮 → 点击
          2) 再搜右上角“免”字 → 点击（有续费弹窗时点 2 次并验证，无弹窗点 1 次）
          3) 都没有 → 每 8s 清理一次续费会员弹窗。
      - allow_mian=False (第2轮及以后 / 进入广告后回到播放界面)：
          只搜 1) 和 3)，不搜“免”字（免字只在开屏后出现）。

    timeout: 最长等待秒数（0 表示只检查一次不等待）
    返回: 是否成功点击进入广告
    """
    if allow_mian:
        log("阶段三：监测进广告入口（优先“立即解锁/领取”弹窗，其次“免”字）")
    else:
        log("阶段三：监测进广告入口（仅“立即解锁/领取”弹窗，不再找“免”字）")
    w, h = screen_size()
    rect_mian = [int(w * 0.65), int(h * 0.04), int(w * 0.92), int(h * 0.12)]
    rect_unlock = [int(w * 0.029), int(h * 0.52), int(w * 0.981), int(h * 0.982)]
    rect_pay = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]

    deadline = time.time() + timeout if timeout > 0 else 0
    check_count = 0
    last_err_log = 0.0
    last_cleanup = 0.0
    CLEANUP_INTERVAL = 8
    REVERIFY_WAIT = 0.7

    while True:
        check_count += 1

        # 前台包名校验：不在汽水音乐时不做点击决策
        if not foreground_is_target():
            log("  前台非汽水音乐，本轮跳过点击决策")
            if timeout > 0 and time.time() >= deadline:
                return False
            poll_sleep()
            continue

        # ========== 1) 最高优先：“立即解锁”/“立即领取”（弹窗进广告按钮） ==========
        unlock_hit = None
        try:
            results = Ocr.paddleocr(rect=rect_unlock, pattern=r"立即(解锁|领取)")
            if results and isinstance(results, list) and len(results) > 0:
                item = results[0]
                cx = item.get("center_x", 0)
                cy = item.get("center_y", 0)
                if cx > 0 and cy > 0:
                    unlock_hit = (item, cx, cy)
        except Exception as e:
            if time.time() - last_err_log > 5:
                log(f"  OCR 识别异常: {e}")
                last_err_log = time.time()

        if unlock_hit is not None:
            item, cx, cy = unlock_hit
            text = item.get("text", "")
            conf = item.get("confidence", 0) or 0
            log(f"  [扫描#{check_count} 命中] '{text}' 置信度={conf:.2f} ({cx},{cy}) → 点击进入广告")
            safe_click(cx, cy, f"'{text}'按钮")
            human_sleep(1.5)
            return True

        # ========== 2) 只有 allow_mian=True 时才搜“免”字按钮 ==========
        if allow_mian:
            mian_hit = None
            try:
                results = Ocr.paddleocr(rect=rect_mian, pattern=r"免")
                if results and isinstance(results, list) and len(results) > 0:
                    item = results[0]
                    cx = item.get("center_x", 0)
                    cy = item.get("center_y", 0)
                    if cx > 0 and cy > 0:
                        mian_hit = (item, cx, cy)
            except Exception as e:
                if time.time() - last_err_log > 5:
                    log(f"  OCR 识别异常: {e}")
                    last_err_log = time.time()

            if mian_hit is not None:
                item, cx, cy = mian_hit
                text = item.get("text", "")
                conf = item.get("confidence", 0) or 0

                # 有续费弹窗 → 最多重试 2 次（点击+验证）；无弹窗 → 点 1 次即返回
                has_popup = _detect_membership_popup(rect_pay)
                max_retry = 2 if has_popup else 1
                log(f"  [扫描#{check_count} 命中] '免' -> '{text}' 置信度={conf:.2f} ({cx},{cy})"
                    f"  检测到续费弹窗={'是' if has_popup else '否'} → 重试上限={max_retry}")

                for attempt in range(1, max_retry + 1):
                    log(f"  [点击尝试 {attempt}/{max_retry}] '免'字按钮 ({cx},{cy})")
                    safe_click(cx, cy, f"'免'字按钮(尝试{attempt})")

                    if max_retry == 1:
                        log("  [成功] 无续费会员弹窗，点击 1 次即生效，进入广告")
                        human_sleep(1.0)
                        return True

                    # 有弹窗时验证免字是否消失
                    time.sleep(REVERIFY_WAIT)
                    verify_hit = None
                    try:
                        vr = Ocr.paddleocr(rect=rect_mian, pattern=r"免")
                        if vr and isinstance(vr, list) and len(vr) > 0:
                            vi = vr[0]
                            vx = vi.get("center_x", 0)
                            vy = vi.get("center_y", 0)
                            if vx > 0 and vy > 0:
                                verify_hit = (vx, vy, vi.get("confidence", 0) or 0)
                    except Exception:
                        verify_hit = None

                    if verify_hit is None:
                        log(f"  [成功] 点击后'免'字消失（尝试 {attempt} 次生效）")
                        human_sleep(0.8)
                        return True

                    vx, vy, vconf = verify_hit
                    log(f"  [未生效] 点击后'免'字仍在屏上 ({vx},{vy}) 置信度={vconf:.2f}，继续重试点击")

                log("  [重试耗尽] 连续多次点击'免'字未触发跳转，本轮放弃")

        # ========== 3) 都没命中 → 每 8s 清理一次续费会员弹窗 ==========
        now = time.time()
        if now - last_cleanup >= CLEANUP_INTERVAL:
            try:
                light_background_cleanup()
            except Exception as e:
                log(f"  背景清理异常: {e}")
            last_cleanup = now

        if timeout > 0 and time.time() >= deadline:
            log(f"  等待进广告入口超时 ({timeout}s)，本轮跳过")
            return False
        if timeout == 0:
            log("  单次检查：未发现进广告入口")
            return False
        poll_sleep()


# ============== 阶段四：等待广告倒计时结束 ==============

def check_and_click_continue_watching() -> bool:
    """
    检测并点击“继续观看”按钮——仅限屏幕中间显眼位置。

    区分两种“继续观看”：
      - 中间显眼区（y:25%~68%）：广告中途误点关闭后弹出的恢复对话框 → 该点
      - 屏幕底部：广告商诱导下载应用按钮 → 绝不点，会跳下载页
    所以只在中间区域检测，绝不在底部/全屏检测。
    """
    w, h = screen_size()
    # 中间显眼区域：x:7%~93%, y:25%~68%
    rect_continue = [int(w * 0.07), int(h * 0.25), int(w * 0.93), int(h * 0.68)]
    item = paddle_find_first(r"继续观看", rect_continue, "继续观看(中间区)")
    if item:
        ok = safe_click(item["center_x"], item["center_y"], "继续观看(中间区)")
        if ok:
            human_sleep(2.5)
            return True
    return False


def _selector_close_live_room() -> bool:
    """
    最后兜底：用 Selector 找 label/text='关闭' 的直播间关闭按钮。
    仅在 OCR / 找图都失败后调用；screen_only 模式下不可用。
    """
    if not (ENABLE_SELECTOR_FALLBACK and _SELECTOR_AVAILABLE):
        return False
    try:
        log("  使用 Selector 控件搜索兜底（text='关闭'）...")
        node = Selector(mode=SELECTOR_MODE).text("关闭").find()
        if node:
            try:
                node.click()
            except Exception:
                r = node.rect
                safe_click((r.x1 + r.x2) // 2, (r.y1 + r.y2) // 2, "关闭按钮(Selector)")
            human_sleep(1.5)
            log("  已点击关闭按钮（Selector），继续等待'领取成功'出现...")
            return True
        log("  Selector 未找到 text='关闭' 的控件")
    except Exception as e:
        log(f"  Selector 控件搜索异常: {e}")
    return False


def wait_ad_countdown(timeout: int = 30) -> bool:
    """
    阶段四：在广告界面等待倒计时结束。
    支持两种广告类型：
      1) 视频广告：倒计时结束 → 右上角出现“领取成功” → 返回，交给 claim_reward 处理
      2) 直播间广告：无倒计时，直接进入直播间 → 识别后点关闭按钮 → 关闭后出现“领取成功”
    轮询优先级：领取成功 > 更多直播/关注(直播间需关闭) > 倒计时文案(仅打印日志)
    """
    log("阶段四：等待广告倒计时结束（支持视频广告 & 直播间广告）")
    w, h = screen_size()
    # 右上角“领取成功”区域（视频广告结束 或 直播间关闭后）
    rect_claim_top = [int(w * 0.55), int(h * 0.03), w, int(h * 0.14)]
    # 右上角倒计时文案区域（仅打印日志）
    rect_countdown = [int(w * 0.58), int(h * 0.045), int(w * 0.97), int(h * 0.09)]
    # 直播间底部区域（检测是否为直播间广告）
    rect_live_room = [int(w * 0.092), int(h * 0.609), int(w * 0.911), int(h * 0.786)]
    # 直播间右上角“更多直播”区域
    rect_more_live = [int(w * 0.694), int(h * 0.099), int(w * 0.971), int(h * 0.145)]
    # 直播间左上角“关注”按钮区域（“更多直播”未识别时的兜底判定）
    rect_follow = [0, int(h * 0.05), int(w * 0.56), int(h * 0.107)]
    # 直播间右上角关闭按钮(×)区域（找图 / OCR）
    rect_close_btn = [int(w * 0.619), int(h * 0.055), w, int(h * 0.10)]

    deadline = time.time() + timeout
    last_sec = -1
    checks = 0
    is_live_room_logged = False

    while time.time() < deadline:
        checks += 1

        # ========== 1) 最高优先：检测“领取成功”是否出现 ==========
        hit_claim = paddle_find_first(r"领取成功", rect_claim_top, "倒计时结束标志")
        if hit_claim:
            log(f"  检测到'领取成功'出现（轮询 {checks} 次）→ 返回，交给 claim_reward 执行两步点击")
            return True

        # ========== 2) 次高优先：检测是否在直播间内（需要关闭） ==========
        if not is_live_room_logged:
            live_check = paddle_find_first(r"直播间", rect_live_room, "直播间检测")
            if live_check:
                log(f"  检测到直播间广告（轮询 {checks} 次）→ 将尝试关闭直播间")
                is_live_room_logged = True

        more_live_hit = paddle_find_first(r"更多直播", rect_more_live, "直播间识别")
        follow_hit = None
        if not more_live_hit:
            follow_hit = paddle_find_first(r"关注", rect_follow, "关注按钮(左上角)")

        if more_live_hit or follow_hit:
            if more_live_hit:
                log("  命中'更多直播' → 确认在直播间内，尝试点击关闭按钮...")
            else:
                log("  未命中'更多直播'，但左上角命中'关注' → 确认仍为直播间，尝试关闭按钮...")
            close_clicked = False

            # 优先：FindImages 匹配关闭按钮模板图（如果模板存在）
            if HAS_LIVE_ROOM_TEMPLATE:
                try:
                    template_path = R.img("img_zhibojian-close01.png")
                    result = FindImages.find(template_path, rect=rect_close_btn)
                    if result:
                        cx = result.get("center_x", 0)
                        cy = result.get("center_y", 0)
                        conf = result.get("confidence", 0) or 0
                        log(f"  找图匹配到关闭按钮 (置信度={conf:.2f}) @ ({cx},{cy}) → 点击")
                        safe_click(cx, cy, f"直播间关闭按钮(模板匹配,置信度={conf:.2f})")
                        human_sleep(1.5)
                        close_clicked = True
                except Exception as e:
                    log(f"  找图匹配异常: {e}，改用 OCR 方案")

            # 兜底一：OCR 在关闭按钮区域识别 × 符号
            if not close_clicked:
                close_hit = paddle_find_first(r"[×✕✖]", rect_close_btn, "关闭按钮OCR")
                if close_hit:
                    cx = close_hit.get("center_x", 0)
                    cy = close_hit.get("center_y", 0)
                    log(f"  OCR 命中关闭符号 @ ({cx},{cy}) → 点击")
                    safe_click(cx, cy, "关闭按钮(OCR)")
                    human_sleep(1.5)
                    close_clicked = True

            # 兜底二：Selector 控件搜索（仅前面两层都失败时）
            if not close_clicked:
                close_clicked = _selector_close_live_room()

            if close_clicked:
                continue
            log("  未找到关闭按钮，本轮跳过")

        # ========== 3) 最低优先：倒计时文案 → 仅打印日志 ==========
        cd_hit = paddle_find_first(r"秒后可领奖励", rect_countdown, "倒计时")
        if cd_hit:
            text = cd_hit.get("text", "")
            sec = None
            for ch in text:
                if ch.isdigit():
                    sec = int(ch)
                    break
            if sec is not None and sec != last_sec:
                log(f"  广告倒计时中... {text}")
                last_sec = sec

        poll_sleep()

    log(f"  等待超时 ({timeout}s)，'领取成功'未出现（本轮可能没有广告可领）")
    return False


# ============== 阶段五：领取奖励 ==============

def claim_reward() -> int:
    """
    领取奖励流程（确定性两步走）：
      倒计时结束 → 右上角出现“领取成功” → 点击它
        → 弹出中间对话框包含“领取奖励”按钮 → 点击“领取奖励” → 进入下一轮广告

    返回值：1=两步都成功，已进入下一轮广告；0=超时失败
    """
    log("阶段五：领取奖励（两步：点领取成功 → 点领取奖励 → 进入下一轮广告）")
    w, h = screen_size()
    # 右上角“领取成功”专属区（倒计时结束标志）
    rect_claim_top = [int(w * 0.55), int(h * 0.03), w, int(h * 0.14)]
    # “领取奖励”弹窗按钮专属区（超出绝不点）
    rect_reward_btn = [int(w * 0.13), int(h * 0.25), int(w * 0.87), int(h * 0.70)]

    # ========== 第一步：等并点右上角“领取成功” ==========
    log("  [步骤 1/2] 等待右上角出现'领取成功'...")
    hit_claim = None
    deadline1 = time.time() + 20
    checks1 = 0
    while time.time() < deadline1:
        checks1 += 1
        hit_claim = paddle_find_first(r"领取成功", rect_claim_top, "领取成功(右上角)")
        if hit_claim:
            break
        poll_sleep()
    if not hit_claim:
        log(f"  [失败] 20s 内未等到右上角'领取成功'（轮询 {checks1} 次），本轮放弃")
        return 0
    cx1 = hit_claim["center_x"]
    cy1 = hit_claim["center_y"]
    conf1 = hit_claim.get("confidence", 0) or 0
    log(f"  [命中] '领取成功' @ ({cx1:.1f},{cy1:.1f}) 置信度={conf1:.2f} → 点击")
    safe_click(cx1, cy1, "'领取成功'(右上角)")

    # ========== 第二步：点完“领取成功”，等“领取奖励”弹窗出现并点击 ==========
    log("  [步骤 2/2] 等待中间弹窗出现'领取奖励'按钮...")
    human_sleep(0.6)  # 弹窗动画留一点时间
    hit_reward = None
    deadline2 = time.time() + 15
    checks2 = 0
    while time.time() < deadline2:
        checks2 += 1
        hit_reward = paddle_find_first(r"领取奖励", rect_reward_btn, "领取奖励(弹窗)")
        if hit_reward:
            break
        poll_sleep()
    if not hit_reward:
        log(f"  [失败] 点击'领取成功'后 15s 内未等到'领取奖励'弹窗（轮询 {checks2} 次），本轮放弃")
        return 0
    cx2 = hit_reward["center_x"]
    cy2 = hit_reward["center_y"]
    conf2 = hit_reward.get("confidence", 0) or 0
    log(f"  [命中] '领取奖励' @ ({cx2:.1f},{cy2:.1f}) 置信度={conf2:.2f} → 点击（进入下一轮广告）")
    safe_click(cx2, cy2, "'领取奖励'(弹窗)")
    human_sleep(1.5)

    log("  [成功] 两步领取完成，自动进入下一轮广告倒计时")
    return 1


# ============== 跨天进度（目标达成即停的基础） ==============

def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def load_state() -> dict:
    """读取跨天累计进度；读取失败按空进度处理，不阻断主流程"""
    if not _KV_AVAILABLE:
        log("  ⚠️  KeyValue 不可用，跨天进度只在本机内存中有效（重启后清零）")
        return {}
    try:
        raw = KeyValue.get(STATE_KEY, "")
        if not raw:
            return {}
        return json.loads(raw) if isinstance(raw, str) else {}
    except Exception as e:
        log(f"  进度读取异常: {e}")
        return {}


def save_state(state: dict):
    if not _KV_AVAILABLE:
        return
    try:
        KeyValue.save(STATE_KEY, json.dumps(state, ensure_ascii=False))
    except Exception as e:
        log(f"  进度保存异常: {e}")


def roll_daily(state: dict) -> dict:
    """跨天自动归零当日计数"""
    d = today_str()
    if state.get("date") != d:
        state["date"] = d
        state["claims_today"] = 0
        state["days_counted_today"] = 0
    state.setdefault("claims_today", 0)
    state.setdefault("days_counted_today", 0)
    state.setdefault("total_days", 0)
    state.setdefault("total_minutes", 0)
    return state


def count_days_from_claims(state: dict):
    """
    按领取次数折算累计天数：每满 DAILY_CLAIM_TARGET 次折算 1 天
    （「今日畅听」1 天 + 后续「提前领明日畅听」各折算 1 天）
    """
    per = max(1, DAILY_CLAIM_TARGET)
    counted = state.get("claims_today", 0) // per
    delta = counted - state.get("days_counted_today", 0)
    if delta > 0:
        state["days_counted_today"] = counted
        state["total_days"] = state.get("total_days", 0) + delta
        log(f"  📅 累计畅听天数 +{delta} → {state['total_days']} 天")


def read_free_minutes():
    """尝试读取 App 内的「免费听时长」数值（分钟）；读不到返回 None"""
    w, h = screen_size()
    rect = [0, int(h * 0.06), w, int(h * 0.34)]
    try:
        results = Ocr.paddleocr(rect=rect, pattern=r"(免费听时长|听时长|时长)")
        if results and isinstance(results, list):
            for item in results:
                digits = "".join(ch for ch in item.get("text", "") if ch.isdigit())
                if digits:
                    return int(digits)
    except Exception:
        pass
    return None


def target_reached(state: dict, success_count: int) -> tuple:
    """返回 (是否达成目标, 说明)"""
    if TARGET_MODE == "days":
        if state.get("total_days", 0) >= TARGET_DAYS:
            return True, f"累计畅听已达 {state.get('total_days', 0)}/{TARGET_DAYS} 天，目标完成"
        return False, ""
    if TARGET_MODE == "minutes":
        if state.get("total_minutes", 0) >= TARGET_MINUTES:
            return True, f"累计时长已达 {state.get('total_minutes', 0)}/{TARGET_MINUTES} 分钟，目标完成"
        return False, ""
    # "today"：今日畅听解锁（按次数判定），且不再继续提前领
    if not CONTINUE_PRECLAIM and success_count >= DAILY_CLAIM_TARGET:
        return True, f"今日畅听已解锁（本次领取 {success_count} 次），目标完成"
    return False, ""


def print_progress(state: dict, success_count: int):
    log("=" * 60)
    log(f"📊 本次领取 {success_count} 次 | 今日累计 {state.get('claims_today', 0)} 次")
    if TARGET_MODE == "days":
        log(f"📅 累计畅听 {state.get('total_days', 0)}/{TARGET_DAYS} 天"
            f"（还差 {max(0, TARGET_DAYS - state.get('total_days', 0))} 天）")
    elif TARGET_MODE == "minutes":
        log(f"⏱ 累计时长 {state.get('total_minutes', 0)}/{TARGET_MINUTES} 分钟")
    else:
        log("🎯 目标模式：今日畅听")


# ============== 主流程 ==============

def run_ad_loop() -> int:
    """
    核心循环：用户手动进入广告后，脚本负责
      等广告倒计时结束 → 点“领取成功” → 点“领取奖励” → 自动进入下一轮广告 → 循环
    每次成功领取计 1 次，累计到 success_count；达成 TARGET_MODE 指定目标即停。
    返回本次运行累计成功次数。
    """
    log("\n" + "#" * 60)
    log(f"🎯 进入核心广告领取循环（TARGET_MODE={TARGET_MODE}，达成即停）")
    log("#" * 60)
    log("   ⚠️  请在 App 上手动进入一个广告（点右上角'免'字或'立即解锁'弹窗），脚本会自动接管后续轮次")
    success_count = 0
    round_idx = 0
    idle_count = 0

    state = roll_daily(load_state())
    print_progress(state, 0)

    while True:
        round_idx += 1
        log(f"\n---- 第 {round_idx} 轮广告循环 ----")

        # 目标达成判断（每轮开头都查一次）
        reached, reason = target_reached(state, success_count)
        if reached:
            log(f"  🎯 {reason}，自动停止")
            break

        # 单次运行领取次数保护
        if success_count >= MAX_CLAIMS_PER_RUN:
            log(f"  ⚠️  本次运行已达上限 {MAX_CLAIMS_PER_RUN} 次，停止（避免一口气刷太多）")
            break
        # 单日总次数保护
        if state.get("claims_today", 0) >= DAILY_MAX_CLAIMS:
            log(f"  ⚠️  今日累计已达上限 {DAILY_MAX_CLAIMS} 次，停止")
            break

        # 步骤 1：等广告倒计时结束（30s 超时）
        got_ad = wait_ad_countdown(timeout=30)
        if not got_ad:
            idle_count += 1
            log(f"  第 {round_idx} 轮：等超时，未检测到广告倒计时结束（连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮无广告）")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮没领到广告，可能当日额度已用完，自动退出循环")
                break
            human_sleep(LOOP_INTERVAL)
            continue

        # 步骤 2：两步领取
        claim_res = claim_reward()
        if claim_res == 1:
            success_count += 1
            idle_count = 0
            state["claims_today"] = state.get("claims_today", 0) + 1
            count_days_from_claims(state)
            save_state(state)
            log(f"  ✅ 第 {round_idx} 轮领取成功！本次累计 = {success_count}，今日累计 = {state['claims_today']}")
        else:
            idle_count += 1
            log(f"  ⚠️  第 {round_idx} 轮领取失败（连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮领取失败）")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮领取失败，自动退出循环")
                break

        # 每轮结束后短暂休息，避免过快
        human_sleep(1)

    # 收尾：读一次「免费听时长」并入账（读不到不影响流程）
    minutes = read_free_minutes()
    if minutes is not None:
        state["total_minutes"] = max(state.get("total_minutes", 0), minutes)
        save_state(state)
        log(f"  ⏱ 读到「免费听时长」≈ {minutes} 分钟")
    print_progress(state, success_count)
    return success_count



def auto_enter_first_ad() -> bool:
    """
    启动后自动进入第一个广告：
      1) 先清一遍续费会员弹窗
      2) 找「立即解锁 / 立即领取」弹窗按钮，其次找右上角「免」字
    都没找到返回 False，交给用户手动点。
    """
    log("阶段三B：尝试自动进入第一个广告")
    try:
        dismiss_popups(max_rounds=2)
    except Exception as e:
        log(f"  预清理弹窗异常: {e}")
    ok = wait_for_mian_button(timeout=25, allow_mian=True)
    if ok:
        log("  ✅ 已自动进入广告，脚本接管后续轮次")
        human_sleep(2)
    else:
        log("  ⚠️ 未自动找到进广告入口，请手动点右上角'免'字或'立即解锁'弹窗")
    return ok


def main():
    """脚本入口主函数"""
    log("🎵 汽水音乐自动看广告脚本（Android 版）启动")
    log("   核心功能：跳过开屏广告 → 等待用户手动进广告 → 循环领奖励 → 达成目标即停")
    log_target_plan()
    try:
        # 1. 初始化
        init_regions()
        Ocr.set_engine(OCR_ENGINE)
        # 2. 启动 App
        if not launch_app():
            log("❌ 无法启动汽水音乐 App，脚本终止")
            return
        # 3. 跳开屏广告
        skip_splash_ad(timeout=5)
        # 3.5 自动进入第一个广告（失败则等用户手动进）
        auto_enter_first_ad()
        # 4. 核心循环（进入广告后自动接管）
        total = run_ad_loop()
        # 5. 结束
        state = roll_daily(load_state())
        log("=" * 60)
        log(f"✅ 脚本运行结束，本次成功领取 {total} 次")
        if TARGET_MODE == "days" and state.get("total_days", 0) < TARGET_DAYS:
            left = TARGET_DAYS - state.get("total_days", 0)
            log(f"📅 还需 {left} 天达成目标，明天再跑一次（建议配定时任务每日自动执行）")
            notify_safe(f"汽水音乐：今日完成，累计 {state.get('total_days', 0)}/{TARGET_DAYS} 天")
        else:
            notify_safe(f"汽水音乐 Android 版完成，成功 {total} 次")
    except Exception as e:
        log(f"❌ 脚本主流程异常: {e}")
        traceback.print_exc()
    finally:
        log("脚本退出")


def log_target_plan():
    if TARGET_MODE == "days":
        st = roll_daily(load_state())
        log(f"🎯 目标：累计畅听 {TARGET_DAYS} 天（当前 {st.get('total_days', 0)} 天）"
            f"；每日领满 {DAILY_CLAIM_TARGET} 次折算 1 天")
    elif TARGET_MODE == "minutes":
        st = roll_daily(load_state())
        log(f"🎯 目标：累计免费听时长 {TARGET_MINUTES} 分钟（当前 {st.get('total_minutes', 0)} 分钟）")
    else:
        log(f"🎯 目标：解锁今日畅听（约 {DAILY_CLAIM_TARGET} 次广告）"
            f"{'，并继续提前领明日畅听' if CONTINUE_PRECLAIM else ''}")



# ============== 入口：AScript 工程约定顶层直接执行 ==============
main()
