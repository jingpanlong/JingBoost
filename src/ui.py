"""桌面 UI 控制器：主窗口、系统托盘、悬浮胶囊三者协同。"""
from __future__ import annotations

import threading
import time

from core import autostart, native
from core.tray import Tray

WIDGET_TITLE = "景同志·桌面悬浮胶囊"
WIDGET_W, WIDGET_H = 306, 54   # 加网速列后的尺寸
WIDGET_RADIUS = 15             # 窗口圆角裁剪半径
PEEK = 7                 # 贴边隐藏后露出的像素
EDGE_TRIGGER = 22        # 判定"靠近边界"的距离（放宽，别让用户非得贴到 0px）
EDGE_INSET = 28          # 启动/恢复/保存位置的安全边距，必须 > EDGE_TRIGGER
EDGE_DWELL = 0.6         # 连续贴边 0.6 秒才收起，防止拖动途中误触发
RESTORE_GRACE = 4.0      # 滑出后的宽限期，期间不再自动收起


class DesktopUI:
    def __init__(self, api, main_window, widget_window, main_title: str,
                 settings: dict, elevated: bool = False):
        self.api = api
        self.main = main_window
        self.widget = widget_window
        self.main_title = main_title
        self.main_hwnd = 0
        self.elevated = elevated           # 提权交接启动的新进程

        self.settings = settings
        self.widget_enabled = bool(settings.get("widget", True))
        self.edge_hide = bool(settings.get("widget_edge_hide", True))
        self.close_to_tray = bool(settings.get("close_to_tray", True))
        self.theme = settings.get("theme", "light")

        # 胶囊外观：透明度 / 配色 / 显示哪些信息
        try:
            self.widget_opacity = max(30, min(100, int(settings.get("widget_opacity", 100))))
        except Exception:  # noqa: BLE001
            self.widget_opacity = 100
        self.widget_accent = str(settings.get("widget_accent") or "blue")
        self.widget_color = str(settings.get("widget_color") or "")
        items = settings.get("widget_items")
        self.widget_items = list(items) if isinstance(items, list) else \
            ["score", "cpu", "ram", "net"]
        self.widget_w = WIDGET_W

        self.hwnd = 0
        self.hidden_side: str | None = None
        self.restore_xy: tuple[int, int] | None = None
        self.dragging = False
        self.animating = False        # 滑动动画期间不做贴边判定
        self.peek_armed = False      # 隐藏后鼠标必须先离开 peek 区，才允许滑出
        self.edge_since = 0.0        # 首次满足贴边条件的时间（去抖）
        self.edge_grace_until = 0.0  # 滑出后的宽限期截止时刻
        self.force_quit = False
        self._balloon_done = False
        self.boost_running = False
        self.boost_msg = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.tray = Tray(
            tip="景同志·电脑加速",
            commands={
                1: self.show_main,
                2: self.toggle_widget,
                3: self.quick_boost,
                4: self.toggle_autostart,
                9: self.quit,
            },
            autostart_getter=lambda: autostart.is_enabled(),
        )

    # ------------------------------------------------------------ 启动
    def boot(self) -> None:
        """由 webview.start(func=...) 在 GUI 就绪后调用。"""
        # 必须按 PID 过滤：提权交接时旧进程的同名窗口可能还活着，
        # FindWindowW 会先把旧句柄交给我们（旧胶囊被设成工具窗口、旧主窗口
        # 随后被销毁 → 新胶囊挂着任务栏按钮、主窗口再也叫不出来）。
        self.hwnd = native.find_own_window(WIDGET_TITLE, timeout=15.0)
        self.main_hwnd = native.find_own_window(self.main_title, timeout=6.0)
        if self.hwnd:
            native.make_tool_window(self.hwnd)
            if self.elevated:
                # 提权交接出来的新进程：先藏住胶囊，等位置/圆角都定好再亮，
                # 否则用户会看到它先在错误坐标闪一下再跳过去
                native.show_window(self.hwnd, False)
            self._place_initial()
            if not self.widget_enabled:
                native.show_window(self.hwnd, False)
            elif self.elevated:
                native.show_window(self.hwnd, True)
                # ShowWindow 之后外壳会重新登记任务栏按钮，必须立刻再摘一次；
                # 否则胶囊会在任务栏上闪一下（交接子进程首次显示时最容易撞上）
                native.make_tool_window(self.hwnd)
                self._apply_region()
        self.tray.start()
        threading.Thread(target=self._monitor, name="JingBoostWidgetMonitor",
                         daemon=True).start()
        if self.elevated:
            # UAC 安全桌面切换后新进程常落在后台，只有置顶胶囊看得见；
            # 先立刻把主窗口叫出来一次，再分几次补抢前台。
            try:
                self.main.show()
                self.main.restore()
            except Exception:
                pass
            self._bring_front()
            for delay in (0.3, 1.1, 2.4, 4.0):
                threading.Timer(delay, self._bring_front).start()
        # 外壳（explorer）在窗口创建后异步登记任务栏按钮，前面几次摘除可能
        # 早于登记；分几批补刷（前密后疏），确保胶囊不会留下独立任务栏按钮。
        for delay in (0.2, 0.55, 1.1, 2.2, 4.0, 6.5):
            threading.Timer(delay, self._ensure_tool_window).start()

    def _ensure_tool_window(self) -> None:
        if self.hwnd and not self._stop.is_set():
            native.make_tool_window(self.hwnd)
            self._apply_region()      # 重建窗口后 region 可能被清掉

    def _bring_front(self) -> None:
        if self.force_quit or self._stop.is_set():
            return
        try:
            self.main.show()
            self.main.restore()
        except Exception:
            pass
        h = self.main_hwnd or native.find_own_window(self.main_title, timeout=0)
        if not h:
            return
        try:
            native.show_window(h, True)
            native.user32.ShowWindow(h, 9)          # SW_RESTORE
            native.user32.BringWindowToTop(h)
            native.user32.SetForegroundWindow(h)
        except Exception:
            pass

    def prepare_elevation(self) -> None:
        """旧进程退出前：托盘、主窗口、胶囊全部撤掉，不能留任何残影。

        只在 UAC 已通过后调用，所以不需要做「取消后恢复」。退出由
        Api.elevate() 用 os._exit 完成，这里只负责让界面先彻底消失。
        """
        self._stop.set()                 # 停掉贴边/任务栏监控线程
        try:
            self.tray.stop()
        except Exception:
            pass
        # 只用 Win32 隐藏：elevate() 跑在 js_api 线程上，调 pywebview 的
        # window.hide() 会触发 WinForms 跨线程异常（日志里会刷一堆堆栈）
        for h in (self.hwnd, self.main_hwnd):
            if h:
                try:
                    native.show_window(h, False)
                except Exception:
                    pass

    def _safe_xy(self, x: int, y: int, w: int = 0, h: int = 0) -> tuple[int, int]:
        """把坐标夹到「完整可见且不会立刻被判定为贴边」的安全区。

        旧逻辑允许把隐藏时的越界坐标写进设置，下次启动胶囊就停在屏幕外、
        状态却是"未隐藏"；而且恢复位置的右边缘正好等于工作区边界，
        0.6 秒后又被自己收起来。EDGE_INSET > EDGE_TRIGGER 可杜绝这个循环。
        """
        wl, wt, wr, wb = native.work_area()
        w = w or WIDGET_W
        h = h or WIDGET_H
        x = max(wl + EDGE_INSET, min(x, wr - w - EDGE_INSET))
        y = max(wt + EDGE_INSET, min(y, wb - h - EDGE_INSET))
        return int(x), int(y)

    def _place_initial(self) -> None:
        wl, wt, wr, wb = native.work_area()
        x = self.settings.get("widget_x")
        y = self.settings.get("widget_y")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = wr - self.widget_w - EDGE_INSET, wb - WIDGET_H - EDGE_INSET
        x, y = self._safe_xy(x, y, self.widget_w, WIDGET_H)
        # 强制精确尺寸：pywebview 的 min_size / DPI 换算可能把窗口撑大
        native.move_window(self.hwnd, x, y, self.widget_w, WIDGET_H,
                           flags=native.SWP_NOZORDER | native.SWP_NOACTIVATE)
        self._apply_region()
        self._apply_alpha()
        self.restore_xy = (x, y)
        # 刚启动的两秒不做贴边判定，避免冷启动时窗口位置还没稳定就收起
        self.edge_grace_until = time.monotonic() + 2.0

    def _apply_region(self) -> None:
        """按实际窗口尺寸裁圆角，内容才能铺满且不留窗口底色。"""
        if not self.hwnd:
            return
        r = native.window_rect(self.hwnd)
        if r:
            native.set_round_region(self.hwnd, r[2] - r[0], r[3] - r[1],
                                    WIDGET_RADIUS)

    # ------------------------------------------------------------ 设置
    def _save(self, **kw) -> None:
        from api import _save_settings
        _save_settings(kw)
        self.settings.update(kw)

    def set_theme(self, theme: str) -> None:
        self.theme = theme

    def set_edge_hide(self, on: bool) -> None:
        self.edge_hide = bool(on)
        self.edge_since = 0.0
        self.edge_grace_until = 0.0
        if not on and self.hidden_side:
            self._restore()
        self._save(widget_edge_hide=bool(on))

    def set_close_to_tray(self, on: bool) -> None:
        self.close_to_tray = bool(on)
        self._save(close_to_tray=bool(on))

    # ------------------------------------------------------------ 胶囊外观
    def _apply_alpha(self) -> None:
        if self.hwnd:
            native.set_window_alpha(self.hwnd, round(self.widget_opacity * 255 / 100))

    def set_widget_opacity(self, pct: int) -> None:
        self.widget_opacity = max(30, min(100, int(pct)))
        self._apply_alpha()
        self._save(widget_opacity=self.widget_opacity)

    def set_widget_accent(self, key: str, color: str | None = None) -> None:
        self.widget_accent = str(key or "blue")
        if color:
            self.widget_color = str(color)
        self._save(widget_accent=self.widget_accent, widget_color=self.widget_color)

    def set_widget_items(self, items: list) -> None:
        from api import _sanitize_widget_items
        self.widget_items = _sanitize_widget_items(items)
        self._save(widget_items=self.widget_items)

    def resize_widget(self, width: int) -> dict:
        """容纳内容所需的最小宽度（由胶囊前端量出来后回传）。"""
        w = max(150, min(460, int(width)))
        if abs(w - self.widget_w) < 2:
            return {"ok": True, "w": self.widget_w, "changed": False}
        self.widget_w = w
        if not self.hwnd:
            return {"ok": True, "w": w, "changed": True}
        # 贴边收起状态：窗口正停在屏幕外只露 7px，这时候改宽度+夹安全区
        # 会把它整颗拽回屏幕中间。只记宽度，等滑出来再按新宽度恢复。
        if self.hidden_side:
            return {"ok": True, "w": w, "changed": True}
        r = native.window_rect(self.hwnd)
        if r:
            x, y = self._safe_xy(r[0], r[1], w, WIDGET_H)
            native.move_window(self.hwnd, x, y, w, WIDGET_H,
                               flags=(native.SWP_NOZORDER | native.SWP_NOACTIVATE))
            self.restore_xy = (x, y)
            self._apply_region()
        return {"ok": True, "w": w, "changed": True}

    def toggle_autostart(self) -> None:
        autostart.set_enabled(not autostart.is_enabled())

    # ------------------------------------------------------------ 主窗口 / 托盘
    def show_main(self) -> None:
        try:
            self.main.show()
            self.main.restore()
            if self.main_hwnd:
                native.user32.SetForegroundWindow(self.main_hwnd)
        except Exception:
            pass

    def minimize_to_tray(self) -> None:
        try:
            self.main.hide()
        except Exception:
            pass
        if not self._balloon_done:
            self._balloon_done = True
            self.tray.balloon("已收进托盘", "景同志·电脑加速仍在运行，单击托盘图标即可恢复。")

    def on_closing(self) -> bool:
        """返回 False 会取消关闭（pywebview Event 语义）。"""
        if self.force_quit:
            self.shutdown()
            return True
        if not self.close_to_tray:
            self.force_quit = True        # 让胶囊的 closing 钩子放行，别把它拦下来
            self.shutdown()

            def close_widget():
                time.sleep(0.15)
                try:
                    self.widget.destroy()
                except Exception:
                    pass
            threading.Thread(target=close_widget, daemon=True).start()
            return True
        self.minimize_to_tray()
        return False

    def shutdown(self) -> None:
        """窗口真正关闭时清理托盘与后台线程（可重复调用）。"""
        if self._stop.is_set():
            return
        self._stop.set()
        self.tray.stop()
        self._persist_position()

    def quit(self) -> None:
        self.force_quit = True
        self.shutdown()
        try:
            self.widget.destroy()
        except Exception:
            pass
        try:
            self.main.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ 悬浮胶囊
    def widget_state(self) -> dict:
        from api import widget_accent_colors
        a1, a2 = widget_accent_colors({"widget_accent": self.widget_accent,
                                       "widget_color": self.widget_color})
        return {
            "enabled": self.widget_enabled,
            "peek": self.hidden_side,
            "theme": self.theme,
            "edge_hide": self.edge_hide,
            "autostart": autostart.is_enabled(),
            "close_to_tray": self.close_to_tray,
            "boosting": self.boost_running,
            "boost_msg": self.boost_msg,
            "opacity": self.widget_opacity,
            "accent": a1, "accent2": a2,
            "items": list(self.widget_items),
        }

    def set_widget(self, on: bool) -> dict:
        on = bool(on)
        self.widget_enabled = on
        self._save(widget=on)
        if not self.hwnd:
            return {"ok": True, "enabled": on}
        if on:
            if self.hidden_side:
                self._restore()
            else:
                # 胶囊可能是「贴边隐藏」状态下被关掉的：窗口还停在屏幕外的
                # peek 坐标上（hidden_side 已被清空），直接显示就只露出 7px，
                # 紧接着又被贴边判定收走。显示前先夹回安全区。
                rect = native.window_rect(self.hwnd)
                if rect:
                    x, y = self._safe_xy(rect[0], rect[1],
                                         rect[2] - rect[0], rect[3] - rect[1])
                    if (x, y) != (rect[0], rect[1]):
                        native.move_window(self.hwnd, x, y, self.widget_w, WIDGET_H,
                                           flags=(native.SWP_NOZORDER
                                                  | native.SWP_NOACTIVATE))
                        self.restore_xy = (x, y)
                        self._persist_position()
            native.show_window(self.hwnd, True)
            self._apply_alpha()      # 显示后补一次，确保分层样式还在
        else:
            native.show_window(self.hwnd, False)
            self.hidden_side = None
            self.peek_armed = False
            self.edge_since = 0.0
            # 胶囊被关掉时无条件把主界面带回来：提权新进程可能把主窗口
            # 留在后台/托盘里，用户叉掉胶囊就会以为"新界面不显示"。
            try:
                threading.Timer(0.15, self._bring_front).start()
            except Exception:
                pass
        return {"ok": True, "enabled": on}

    def toggle_widget(self) -> dict:
        return self.set_widget(not self.widget_enabled)

    def on_widget_closing(self) -> bool:
        """从任务栏/Alt-Tab 关胶囊时只隐藏，不销毁窗口（销毁会拖累整个应用）。

        同时把主界面带回前台：提权后的新进程常常只有置顶胶囊可见，
        用户把胶囊叉掉后就会以为"程序没了"。
        """
        if self.force_quit:
            return True
        self.set_widget(False)
        threading.Timer(0.12, self._bring_front).start()
        return False

    def quick_boost(self) -> None:
        if self.boost_running:
            return
        self.boost_running = True
        self.boost_msg = ""

        def run():
            try:
                r = self.api.boost({})
                self.boost_msg = "加速失败" if r and r.get("ok") is False else "加速完成"
            except Exception:
                self.boost_msg = "加速失败"
            finally:
                self.boost_running = False
        threading.Thread(target=run, daemon=True, name="JingBoostQuickBoost").start()

    # ------------------------------------------------------------ 拖动
    def widget_drag_start(self) -> dict:
        if not self.hwnd or self.dragging:
            return {"ok": False}
        r = native.window_rect(self.hwnd)
        if not r:
            return {"ok": False}
        l, t = r[0], r[1]
        cx, cy = native.cursor_pos()
        if self.hidden_side:
            self._restore()
            r2 = native.window_rect(self.hwnd)
            if r2:
                l, t = r2[0], r2[1]
        offx, offy = cx - l, cy - t
        self.dragging = True
        threading.Thread(target=self._drag_loop, args=(offx, offy),
                         name="JingBoostWidgetDrag", daemon=True).start()
        return {"ok": True}

    def _drag_loop(self, offx: int, offy: int) -> None:
        try:
            # 用全局鼠标状态跟踪，鼠标移出 WebView 也不会丢失拖动
            while not self._stop.is_set() and native.left_button_down():
                cx, cy = native.cursor_pos()
                native.move_window(self.hwnd, cx - offx, cy - offy)
                time.sleep(0.008)
        finally:
            self.dragging = False
            self.edge_since = 0.0        # 拖动结束后重新计时，别把拖动过程算进去
            self.edge_grace_until = 0.0
            r = native.window_rect(self.hwnd)
            if r:
                self.restore_xy = (r[0], r[1])
            self._persist_position()

    def _persist_position(self) -> None:
        if not self.hwnd or self.hidden_side:
            return
        r = native.window_rect(self.hwnd)
        if not r:
            return
        x, y = self._safe_xy(r[0], r[1], r[2] - r[0], r[3] - r[1])
        from api import _save_settings
        _save_settings({"widget_x": x, "widget_y": y})
        self.settings["widget_x"], self.settings["widget_y"] = x, y

    # ------------------------------------------------------------ 贴边隐藏
    def _slide_to(self, x: int, y: int) -> None:
        r = native.window_rect(self.hwnd)
        if not r:
            return
        sx, sy = r[0], r[1]
        self.animating = True
        try:
            steps = 6
            for i in range(1, steps + 1):
                nx = int(sx + (x - sx) * i / steps)
                ny = int(sy + (y - sy) * i / steps)
                native.move_window(self.hwnd, nx, ny)
                time.sleep(0.016)
        finally:
            self.animating = False

    def _hide(self, side: str, rect: tuple[int, int, int, int]) -> None:
        l, t, r, b = rect
        w, h = r - l, b - t
        wl, wt, wr, wb = native.work_area()
        self.restore_xy = (l, t)
        if side == "left":
            x, y = wl + PEEK - w, t
        elif side == "right":
            x, y = wr - PEEK, t
        else:
            x, y = l, wt + PEEK - h
        self._slide_to(x, y)
        self.hidden_side = side
        # 刚收起时鼠标往往还在原位（拖动结束手没移开）。要求它先离开
        # peek 区一次，否则下一帧就会被 _cursor_on_peek 拉回来。
        self.peek_armed = False
        self.edge_since = 0.0

    def _restore(self) -> None:
        if not self.hidden_side or not self.hwnd:
            return
        rect = native.window_rect(self.hwnd)
        xy = self.restore_xy or ((rect[0], rect[1]) if rect else None)
        if not xy:
            self.hidden_side = None
            return
        x, y = xy
        w = self.widget_w
        h = (rect[3] - rect[1]) if rect else WIDGET_H
        # 隐藏前的位置通常已经部分越界；夹回安全区（留 EDGE_INSET 边距），
        # 保证整颗胶囊完整可见，且不会刚滑出来就又被贴边判定收走。
        x, y = self._safe_xy(x, y, w, h)
        self.hidden_side = None
        self.peek_armed = False
        self.edge_since = 0.0
        self.edge_grace_until = time.monotonic() + RESTORE_GRACE
        self._slide_to(x, y)
        # 收起期间可能在设置里改了显示项，滑出来后按新宽度落位
        native.move_window(self.hwnd, x, y, self.widget_w, WIDGET_H,
                           flags=(native.SWP_NOZORDER | native.SWP_NOACTIVATE))
        self._apply_region()
        self._persist_position()

    def _cursor_on_peek(self, side: str, rect: tuple[int, int, int, int]) -> bool:
        cx, cy = native.cursor_pos()
        l, t, r, b = rect
        wl, wt, wr, _ = native.work_area()
        pad = 6
        if side == "left":
            return cx <= wl + PEEK + pad and t - pad <= cy <= b + pad
        if side == "right":
            return cx >= wr - PEEK - pad and t - pad <= cy <= b + pad
        return cy <= wt + PEEK + pad and l - pad <= cx <= r + pad

    def _monitor(self) -> None:
        last_taskbar_check = time.monotonic()
        while not self._stop.wait(0.22):
            try:
                now = time.monotonic()
                # explorer.exe 重启、WinForms 重建句柄都会把任务栏按钮加回来，
                # 每 3 秒补一次 DeleteTab，代价极小。
                if self.hwnd and now - last_taskbar_check >= 3.0:
                    last_taskbar_check = now
                    if not native.taskbar_eligible(self.hwnd):
                        native.taskbar_delete_tab(self.hwnd)
                    else:
                        native.make_tool_window(self.hwnd)
                if not self.widget_enabled or not self.hwnd:
                    continue
                if self.dragging or self.animating:
                    self.edge_since = 0.0
                    continue
                rect = native.window_rect(self.hwnd)
                if not rect:
                    continue

                # --- 已收起：鼠标必须先离开 peek 区再回来才滑出 ---
                if self.hidden_side:
                    if not self._cursor_on_peek(self.hidden_side, rect):
                        self.peek_armed = True
                    elif self.peek_armed:
                        self._restore()
                    continue

                # --- 未收起：连续贴边 EDGE_DWELL 秒才隐藏 ---
                if not self.edge_hide or now < self.edge_grace_until:
                    self.edge_since = 0.0
                    continue
                l, t, r, b = rect
                wl, wt, wr, _ = native.work_area()
                cx, cy = native.cursor_pos()
                side = None
                if l <= wl + EDGE_TRIGGER:
                    side = "left"
                elif r >= wr - EDGE_TRIGGER:
                    side = "right"
                elif t <= wt + EDGE_TRIGGER:
                    side = "top"
                if not side:
                    self.edge_since = 0.0
                    continue
                # 不再因为"鼠标还搭在胶囊上"就永久跳过（拖完手必然还在上面，
                # 老逻辑正是这样让贴边隐藏看起来完全没反应）。改成只延长去抖。
                on_pill = l - 4 <= cx <= r + 4 and t - 4 <= cy <= b + 4
                dwell = EDGE_DWELL * 2 if on_pill else EDGE_DWELL
                if not self.edge_since:
                    self.edge_since = now
                elif now - self.edge_since >= dwell:
                    self.edge_since = 0.0
                    self._hide(side, rect)
            except Exception:
                continue
