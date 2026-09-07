# -*- coding: utf-8 -*-
"""
main.py 的最小資源版本:只保留「斷線偵測」與「定時重啟」兩個功能。

不做怪物偵測、角色/補師位置辨識、小地圖巡邏、跨平台爬繩等任何需要持續截圖比對的工作,
每個檢查週期只截一次圖、只跑一次斷線特徵比對,而且用 time.sleep 拉開檢查間隔,
CPU/GPU 占用遠低於 main.py 的完整版本,適合純粹想維持連線、不需要掛機打怪的情境。

直接重用 main.py 已經寫好的 ReconnectManager/ReconnectConfig,不重複實作一份重連邏輯
(斷線重連涉及點擊 Chrome 登入、選角、選服等一長串流程,分兩份維護容易漏改、產生分歧)。
"""

import time
from dataclasses import dataclass

import mss
import numpy as np
import pydirectinput

from main import ReconnectConfig, ReconnectManager, get_game_window, keyup_all


@dataclass
class MinimalBotConfig:
    # 定時重啟: 每隔幾分鐘強制重啟一次遊戲,設為 0 或負數停用
    restart_interval_minutes: float = 60.0
    # 每個檢查週期間隔幾秒。拉長可以再降低資源占用,但斷線後要多等一點才會被偵測到
    check_interval_seconds: float = 2.0
    # 連續重連失敗達門檻就停止腳本,避免無限重試
    max_consecutive_reconnect_failures: int = 3


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
                success = reconnector.handle_reconnect()

                if success:
                    consecutive_reconnect_failures = 0
                else:
                    consecutive_reconnect_failures += 1
                    if consecutive_reconnect_failures >= cfg.max_consecutive_reconnect_failures:
                        print(f"[主程式] 連續 {consecutive_reconnect_failures} 次重連失敗,"
                              f"停止腳本,請人工檢查狀況！")
                        break

            time.sleep(cfg.check_interval_seconds)
