# MapleStory Auto-Bot (新楓之谷自動化輔助)

基於 Python 與 OpenCV 電腦視覺開發的《新楓之谷》自動化腳本。利用畫面即時擷取、樣板匹配（Template Matching）與非極大值抑制（NMS）演算法，實現角色定位、怪物自動偵測、警戒範圍攻擊與血量監控。

---

## 🌟 核心功能

* **視窗自動聚焦**：自動尋找遊戲視窗（`新楓之谷`）並切換至最上層。
* **角色動態定位**：透過角色頭頂標籤（`player_tag.png`）辨識座標，自動忽略底部 UI 儀表板區域干擾。
* **怪物雙向偵測**：支援怪物樣板左右翻轉比對（`mo_00065.png`），並透過 NMS 技術去除重複重疊框。
* **狀態監控 (HP/MP)**：指定區域像素顏色判定，計算當前血量與魔力百分比。
* **警戒距離攻擊**：計算角色與怪物的歐氏距離（Euclidean Distance），當怪物進入警戒半徑且高低差符合時自動觸發攻擊（`a` 鍵）。
* **防呆定時微調**：隨機間隔（200~250 秒）進行微幅位移與動作，降低被判定為機器人的風險。
* **除錯圖層繪製**：支援可視化 Debug 模式，儲存畫有 HP/MP 範圍、角色圓心及怪物距離的分析圖片。

---

## 🔄 系統架構與模組運作流程

主迴圈以 `mss` 抓取全螢幕畫面為起點，將影像分流至各個模組進行分析與決策：

```mermaid
flowchart TD
    Start([主迴圈開始]) --> WinCheck{取得遊戲視窗\nget_game_window}
    
    WinCheck -- 失敗 --> SleepWait[等待 1 秒] --> Start
    WinCheck -- 成功 --> GrabScreen[擷取遊戲畫面\nmss.grab]

    subgraph 畫面分析與模組演算
        GrabScreen --> HPMPModule[血量監控模組\nget_hp_mp_region\ncalculate_bar_percentage]
        GrabScreen --> PlayerModule[角色定位模組\nfind_player_by_tag]
        GrabScreen --> MonsterModule[怪物辨識模組\nfind_monsters & NMS]
    end

    HPMPModule --> HPCheck{HP < 50% ?}
    HPCheck -- 是 --> UsePotion[使用藥水/技能] --> PlayerCheck
    HPCheck -- 否 --> PlayerCheck{找到玩家座標 ?}

    PlayerModule --> PlayerCheck
    PlayerCheck -- 否 (None) --> SkipLoop[跳過本次迴圈] --> SleepLoop
    PlayerCheck -- 成功 --> MoveModule[定時移動模組\nperiodic_move]

    MoveModule --> DistCheck{是否有怪物進入\n攻擊距離與高低差 ?}
    MonsterModule --> DistCheck

    DistCheck -- 是 --> Attack[觸發攻擊 pydirectinput.press 'a'] --> DebugCheck
    DistCheck -- 否 --> DebugCheck{DEBUG == 1 ?}

    DebugCheck -- 是 --> SaveDebug[繪製並存檔\nsave_full_debug_image] --> SleepLoop
    DebugCheck -- 否 --> SleepLoop[主迴圈休眠 main_loop_sleep]

    SleepLoop --> Start