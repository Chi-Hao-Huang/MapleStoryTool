# -*- coding: utf-8 -*-
"""
main.py 的最小資源版本:只保留「斷線偵測」與「定時重啟」兩個功能。

不做怪物偵測、角色/補師位置辨識、小地圖巡邏、跨平台爬繩等任何需要持續截圖比對的工作,
每個檢查週期只截一次圖、只跑一次斷線特徵比對,而且用 time.sleep 拉開檢查間隔,
CPU/GPU 占用遠低於 main.py 的完整版本,適合純粹想維持連線、不需要掛機打怪的情境。

重啟時走的是「啟動本機登入啟動器 exe(輸入授權碼登入的那種) -> 點擊登录按鈕 -> 等待遊戲視窗出現」
這條流程,而不是 main.py 預設的 Chrome 網頁登入流程,適合私服客戶端常見的本機啟動器環境。
斷線偵測與強制關閉遊戲進程仍重用 main.py 已經寫好的 ReconnectManager,只有登入這一段是
這個檔案自己的邏輯,避免整套重連流程分兩份維護。
"""

import subprocess
import time
from dataclasses import dataclass
from typing import Tuple

import mss
import numpy as np
import pydirectinput
import pyautogui

from main import ReconnectConfig, ReconnectManager, get_game_window, get_window, activate_window, keyup_all


@dataclass
class MinimalBotConfig:
    # 定時重啟: 每隔幾分鐘強制重啟一次遊戲,設為 0 或負數停用
    restart_interval_minutes: float = 60.0
    # 每個檢查週期間隔幾秒。拉長可以再降低資源占用,但斷線後要多等一點才會被偵測到
    check_interval_seconds: float = 2.0
    # 連續重連失敗達門檻就停止腳本,避免無限重試
    max_consecutive_reconnect_failures: int = 3

    # ---- 本機登入啟動器 ----
    launcher_exe_path: str = r'C:\path\to\Client.exe'   # 請改成實際的啟動器執行檔路徑
    launcher_window_title: str = 'MapleStoryClassic'    # 啟動器視窗標題
    launcher_login_btn_ratio: Tuple[float, float] = (0.5, 0.5)  # 「登录」按鈕相對視窗寬高的比例,需自行校正
    launcher_wait_seconds: float = 10.0          # 執行 exe 後,等待啟動器視窗出現的逾時秒數
    launcher_after_login_wait_seconds: float = 3.0  # 點擊登入後,等待遊戲開始啟動的秒數
    game_window_wait_seconds: float = 60.0       # 點擊登入後,等待遊戲視窗出現的逾時秒數


def click_ratio(win, ratio: Tuple[float, float]):
    """在指定視窗內,依照 (rx, ry) 比例點擊,不依賴整個桌面/多螢幕尺寸"""
    rx, ry = ratio
    x = win.left + int(win.width * rx)
    y = win.top + int(win.height * ry)
    pyautogui.click(x=x, y=y)


def run_reconnect(reconnector: ReconnectManager, cfg: MinimalBotConfig) -> bool:
    """
    執行一輪重啟流程:強制關閉遊戲 -> 啟動本機登入啟動器並點擊登入 -> 等待遊戲視窗出現。
    回傳 True/False 代表這輪重啟是否成功。
    """
    reconnector.force_close_game()

    launcher_win = get_window(title_exact=cfg.launcher_window_title)
    if launcher_win is None:
        print("[主程式] 啟動本機登入啟動器...")
        try:
            subprocess.Popen([cfg.launcher_exe_path])
        except Exception as e:
            print(f"[主程式] 啟動本機登入啟動器失敗: {e}")
            return False

        start_time = time.time()
        while time.time() - start_time < cfg.launcher_wait_seconds:
            launcher_win = get_window(title_exact=cfg.launcher_window_title)
            if launcher_win is not None:
                break
            time.sleep(0.5)

    if launcher_win is None:
        print("[主程式] 逾時仍未看到本機登入啟動器視窗,本輪嘗試失敗")
        return False

    activate_window(launcher_win)
    time.sleep(0.5)
    click_ratio(launcher_win, cfg.launcher_login_btn_ratio)
    time.sleep(cfg.launcher_after_login_wait_seconds)

    start_time = time.time()
    while time.time() - start_time < cfg.game_window_wait_seconds:
        if get_game_window(activate=True):
            print("[主程式] 遊戲視窗已出現,重啟完成")
            return True
        time.sleep(1.0)

    print("[主程式] 逾時仍未看到遊戲視窗,本輪嘗試失敗")
    return False


if __name__ == "__main__":
    """要用系統管理員權限啟動 IDE 才能正確觸發 DirectInput 按鍵"""
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.05

    cfg = MinimalBotConfig()
    rc_cfg = ReconnectConfig()
    reconnector = ReconnectManager(rc_cfg)

    last_restart_time = time.time()
    consecutive_reconnect_failures = 0

    win = get_game_window(activate=True)

    with mss.MSS() as sct:
        while True:
            win = get_game_window(activate=False)
            if not win:
                print("找不到遊戲視窗,等待中...")
                time.sleep(cfg.check_interval_seconds)
                continue

            game_region = {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
            game_img = np.array(sct.grab(game_region))

            need_restart = False
            reason = ""

            if reconnector.is_disconnected(game_img):
                need_restart = True
                reason = "偵測到斷線通知"
            elif cfg.restart_interval_minutes > 0 and \
                    (time.time() - last_restart_time) >= cfg.restart_interval_minutes * 60:
                need_restart = True
                reason = f"腳本已執行超過 {cfg.restart_interval_minutes} 分鐘"

            if need_restart:
                print(f"[主程式] {reason},進入重啟流程...")
                keyup_all()
                last_restart_time = time.time()
                success = run_reconnect(reconnector, cfg)

                if success:
                    consecutive_reconnect_failures = 0
                else:
                    consecutive_reconnect_failures += 1
                    if consecutive_reconnect_failures >= cfg.max_consecutive_reconnect_failures:
                        print(f"[主程式] 連續 {consecutive_reconnect_failures} 次重連失敗,"
                              f"停止腳本,請人工檢查狀況！")
                        break

            time.sleep(cfg.check_interval_seconds)
