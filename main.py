# -*- coding: utf-8 -*-
"""
Created on Mon Aug  3 20:40:45 2026

@author: chi
"""

import mss
import numpy as np
import time
import pygetwindow as gw
import ctypes
import cv2
import pydirectinput
from PIL import Image
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------
# 設定集中管理
# ---------------------------------------------------------

@dataclass
class BotConfig:
    monsters_threshold: float = 0.82
    template_paths: List[str] = field(default_factory=lambda: [
        'image/骷髏狗2.png',
    #   'image/木面人.png',
    ])
    player_threshold: float = 0.6
    attack_distance_threshold: int = 220
    move_center: int = 764

    main_loop_sleep: float = 0.05
    minimap_left_bound: int = 60
    minimap_right_bound: int = 100

    hp_threshold: float = 10.0
    mp_threshold: float = 10.0
    heal_key: str = 'v'
    rest_key: str = 'x'

    single_attack_key: str = 'shift'
    aoe_attack_key: str = 'a'
    aoe_monster_count: int = 2  # 範圍內達到幾隻怪就改用範圍攻擊

    # 補師跟隨模組
    healer_tag_path: str = 'image/healer_tag.png'
    healer_tag_threshold: float = 0.6
    healer_y_tolerance: int = 100   # Y座標差在此範圍內視為同一層
    healer_x_dead_zone: int = 100   # X座標差在此範圍內視為已到達補師旁邊,不再移動

    debug: bool = False
    debug_show_window: bool = False   # debug 時是否即時顯示監看視窗
    debug_save_image: bool = True   # debug 時是否額外存成檔案

    # ---- 效能相關設定 ----
    # 每隔多少個 tick 重新搶一次遊戲視窗焦點 (0 = 只在啟動時搶一次,之後不再搶)
    reactivate_interval_ticks: int = 1
    # 角色/補師標籤的局部搜尋半徑(像素)。上次有偵測到位置時,只在該位置附近搜尋,
    # 找不到才退回全螢幕搜尋,可大幅降低 matchTemplate 的運算量。
    player_search_margin: int = 150
    healer_search_margin: int = 200


# ---------------------------------------------------------
# tool
# ---------------------------------------------------------

def activate_window(win):
    hwnd = win._hWnd
    ctypes.windll.user32.ShowWindow(hwnd, 5)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def get_game_window(title='新楓之谷', activate=False):
    """
    取得遊戲視窗物件。
    activate=True 時才會呼叫 SetForegroundWindow 搶焦點 -- 這個系統呼叫較慢,
    平常每個 tick 只需要更新視窗座標,不需要每次都搶焦點。
    """
    wins = gw.getWindowsWithTitle(title)
    if not wins:
        return None
    win = wins[0]
    if activate:
        activate_window(win)
    return win


def keyup_all(keys=('left', 'right')):
    # 放開按鍵是否成功不影響時序精準度,略過內建 pause 節省時間
    for key in keys:
        pydirectinput.keyUp(key, _pause=False)


# ---------------------------------------------------------
# HP / SP 判斷模組
# ---------------------------------------------------------

def get_hp_mp_region(win):
    """
    這個像素位置可能每台電腦都不一樣
    要看 debug 視窗調整
    """
    hp_region = {
        "left": win.left + 511,
        "top": win.top + 780,
        "width": 105,
        "height": 16
    }
    mp_region = {
        "left": hp_region['left'] + 108,
        "top": hp_region['top'],
        "width": hp_region['width'],
        "height": hp_region['height']
    }
    return hp_region, mp_region


def crop_region(img, region, win):
    """依照 region(全螢幕座標) 裁切出相對於視窗的 ROI"""
    top = region["top"] - win.top
    left = region["left"] - win.left
    return img[top: top + region["height"], left: left + region["width"]]


def calculate_bar_percentage(img_bgra, color_check_fn):
    """根據傳入的截圖圖像計算百分比"""
    mid_row = img_bgra[img_bgra.shape[0] // 2]
    filled = sum(1 for (b, g, r, a) in mid_row if color_check_fn(r, g, b))
    return (filled / len(mid_row)) * 100


def is_hp_color(r, g, b):
    return r > 150 and g < 100 and b < 100


def is_mp_color(r, g, b):
    return b > 150 and r < 100


def read_hp_mp(game_img, win):
    """回傳 (hp_percent, mp_percent)"""
    hp_region, mp_region = get_hp_mp_region(win)
    hp_crop = crop_region(game_img, hp_region, win)
    mp_crop = crop_region(game_img, mp_region, win)
    hp_percent = calculate_bar_percentage(hp_crop, is_hp_color)
    mp_percent = calculate_bar_percentage(mp_crop, is_mp_color)
    return hp_percent, mp_percent


def handle_hp_mp(hp_percent, mp_percent, cfg: BotConfig):
    """依照血量/魔力做出反應 (吃藥 / 坐下休息)"""
    if hp_percent < cfg.hp_threshold:
        print(f"警告: HP < {cfg.hp_threshold}%!")
        if cfg.heal_key:
            pydirectinput.press(cfg.heal_key)
            time.sleep(0.2)

    if mp_percent < cfg.mp_threshold:
        print(f"警告: MP < {cfg.mp_threshold}%!")
        time.sleep(5)
        pydirectinput.press(cfg.rest_key)
        time.sleep(5)
        pydirectinput.press(cfg.rest_key)


# ---------------------------------------------------------
# 模板快取 (避免每個 tick 都重新讀檔+解碼)
# ---------------------------------------------------------

_TAG_TEMPLATE_CACHE = {}      # path -> 灰階 template (np.ndarray) 或 None
_MONSTER_TEMPLATE_CACHE = {}  # path -> (gray_left, gray_right, tw, th) 或 None


def _load_tag_template(path):
    """讀取並快取單一標籤(角色/補師)模板的灰階版本"""
    if path not in _TAG_TEMPLATE_CACHE:
        template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            print(f"錯誤: 找不到標籤圖片 {path}")
        _TAG_TEMPLATE_CACHE[path] = template
    return _TAG_TEMPLATE_CACHE[path]


def _load_monster_templates(path):
    """讀取並快取怪物模板的灰階版本 (含左右翻轉)"""
    if path not in _MONSTER_TEMPLATE_CACHE:
        try:
            pil_img = Image.open(path).convert("L")  # 直接轉灰階
            gray_left = np.array(pil_img)
            gray_right = cv2.flip(gray_left, 1)
            th, tw = gray_left.shape[:2]
            _MONSTER_TEMPLATE_CACHE[path] = (gray_left, gray_right, tw, th)
        except Exception as e:
            print(f"警告: 載入怪物圖片失敗 {path}, 錯誤原因: {e}")
            _MONSTER_TEMPLATE_CACHE[path] = None
    return _MONSTER_TEMPLATE_CACHE[path]


def _build_search_roi(prev_pos, screen_shape, margin):
    """依照上次偵測到的位置,算出一個較小的搜尋範圍 (x1, y1, x2, y2)"""
    if prev_pos is None:
        return None
    x, y = prev_pos
    h, w = screen_shape[:2]
    x1 = max(0, x - margin)
    x2 = min(w, x + margin)
    y1 = max(0, y - margin)
    y2 = min(h, y + margin)
    return (x1, y1, x2, y2)


# ---------------------------------------------------------
# 角色位置辨識模組
# ---------------------------------------------------------

def find_player_by_tag(game_screen_gray, tag_template_path='image/player_tag.png',
                        threshold=0.55, search_region=None):
    """
    透過角色名字標籤或勛章來定位角色位置 (灰階比對)。
    通用函式:傳入不同的 tag_template_path 就能定位不同角色(自己、補師...等)。

    search_region: 可選的 (x1, y1, x2, y2),只在這個範圍內搜尋以加速比對。
                    未提供時搜尋全螢幕(避開底部約 100 像素的 UI 儀表板區域)。
    """
    tag_template = _load_tag_template(tag_template_path)
    if tag_template is None:
        return None

    th, tw = tag_template.shape[:2]
    h, w = game_screen_gray.shape[:2]

    if search_region is not None:
        x1, y1, x2, y2 = search_region
    else:
        x1, y1, x2, y2 = 0, 0, w, h - 100

    roi = game_screen_gray[y1:y2, x1:x2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None

    res = cv2.matchTemplate(roi, tag_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= threshold:
        tag_x = x1 + max_loc[0] + tw // 2
        tag_y = y1 + max_loc[1] + th // 2
        # 名字標籤在角色下方約 40 像素處
        return (tag_x, tag_y - 40)

    return None


def locate_tag_with_fallback(game_screen_gray, tag_template_path, threshold, prev_pos, margin):
    """
    先在「上次位置附近」小範圍搜尋 (快);找不到才退回全螢幕搜尋 (慢,用來重新定位)。
    大部分 tick 角色/補師位置變化不大,這樣可以省下大部分的運算量。
    """
    region = _build_search_roi(prev_pos, game_screen_gray.shape, margin)
    pos = find_player_by_tag(game_screen_gray, tag_template_path, threshold, search_region=region)
    if pos is None and region is not None:
        pos = find_player_by_tag(game_screen_gray, tag_template_path, threshold, search_region=None)
    return pos


# ---------------------------------------------------------
# 怪物辨識與攻擊模組
# ---------------------------------------------------------

def non_max_suppression_fast_indices(boxes, overlap_thresh=0.3):
    """自訂 NMS，回傳保留框的 index 列表"""
    if len(boxes) == 0:
        return []

    if boxes.dtype.kind == "i":
        boxes = boxes.astype("float")

    pick = []
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = boxes[:, 2], boxes[:, 3]

    area = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(y2)

    while len(idxs) > 0:
        last = len(idxs) - 1
        i = idxs[last]
        pick.append(i)

        xx1 = np.maximum(x1[i], x1[idxs[:last]])
        yy1 = np.maximum(y1[i], y1[idxs[:last]])
        xx2 = np.minimum(x2[i], x2[idxs[:last]])
        yy2 = np.minimum(y2[i], y2[idxs[:last]])

        w = np.maximum(0, xx2 - xx1 + 1)
        h = np.maximum(0, yy2 - yy1 + 1)

        overlap = (w * h) / area[idxs[:last]]

        idxs = np.delete(idxs, np.concatenate(([last], np.where(overlap > overlap_thresh)[0])))

    return pick


def find_monsters(game_screen_gray, template_paths, threshold=0.5):
    """
    搜尋多種怪物位置，回傳怪物中心點座標列表 [(x1, y1), ...]
    灰階比對版本,模板已預先快取,不再每個 tick 重新讀檔。
    :param template_paths: 圖片路徑列表，例如 ['image/mo1.png', 'image/mo2.png'] 或單一字串
    """
    if isinstance(template_paths, str):
        template_paths = [template_paths]

    rects = []

    for path in template_paths:
        cached = _load_monster_templates(path)
        if cached is None:
            continue
        gray_left, gray_right, tw, th = cached

        for template in (gray_left, gray_right):
            res = cv2.matchTemplate(game_screen_gray, template, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res >= threshold)
            for pt in zip(*loc[::-1]):
                x, y = int(pt[0]), int(pt[1])
                rects.append([x, y, x + tw, y + th, tw, th])

    if not rects:
        return []

    rects_np = np.array(rects)
    boxes_for_nms = rects_np[:, :4]
    pick_indices = non_max_suppression_fast_indices(boxes_for_nms, overlap_thresh=0.3)

    monsters = []
    for idx in pick_indices:
        x1, y1, x2, y2, tw, th = rects_np[idx]
        monsters.append((int(x1 + tw // 2), int(y1 + th // 2)))

    return monsters


def find_monsters_in_range(monster_positions, player_pos, distance_threshold):
    """回傳範圍內所有怪物座標列表 (不只最近的一隻)"""
    px, py = player_pos
    return [
        (mx, my) for (mx, my) in monster_positions
        if np.hypot(mx - px, my - py) <= distance_threshold
    ]


def handle_attack(monsters_in_range, player_pos, move_direction, cfg: BotConfig):
    """
    範圍內怪物數量 >= cfg.aoe_monster_count -> 範圍攻擊 (不用轉向)
    範圍內只有 1 隻 -> 單體攻擊,只能打面向那一側,面向不對要先轉身
    範圍內沒有怪 -> 不做事
    """
    count = len(monsters_in_range)
    if count == 0:
        return

    if count >= cfg.aoe_monster_count:
        print(f"[怪物偵測模組] 範圍內有 {count} 隻怪，範圍攻擊觸發")
        pydirectinput.press(cfg.aoe_attack_key)
        return

    # 只有一隻怪 -> 單體攻擊,需要面向判斷
    px = player_pos[0]
    monster_x = monsters_in_range[0][0]
    monster_is_left = px > monster_x
    facing_matches = (monster_is_left and move_direction == "left") or \
                      (not monster_is_left and move_direction == "right")

    side_label = "左側" if monster_is_left else "右側"

    if facing_matches:
        print(f"[怪物偵測模組] 怪物接近！距離{side_label}，單體攻擊觸發")
        pydirectinput.press(cfg.single_attack_key)

    else:
        turn_key = "left" if monster_is_left else "right"
        print(f"[怪物偵測模組] 怪物接近！距離{side_label}，轉身單體攻擊觸發")
        keyup_all()
        pydirectinput.press(turn_key)
        pydirectinput.press(cfg.single_attack_key)
        pydirectinput.keyDown(move_direction, _pause=False)


# ---------------------------------------------------------
# 定時移動模組 (目前主迴圈未使用,保留供未來開啟)
# ---------------------------------------------------------

def periodic_move(last_move_time, interval_seconds=300, player_target_pos=(0, 0), move_center=687, debug=False):
    """達到時間間隔時觸發移動，移動完畢回傳當前時間與新產生的隨機間隔時間"""
    current_time = time.time()
    if current_time - last_move_time >= interval_seconds:
        px, _ = player_target_pos
        direction = "left" if px >= move_center else "right"
        print(f"執行 {interval_seconds} 秒定時移動, 向{direction}移動 1 步")
        pydirectinput.press(direction)
        new_next_interval = 1 if debug else random.randint(200, 250)
        return current_time, new_next_interval

    return last_move_time, interval_seconds


# ---------------------------------------------------------
# 定時技能模組
# ---------------------------------------------------------

class TimedKeyTrigger:
    def __init__(self, key='end', interval_seconds=300):
        self.key = key
        self.interval = interval_seconds
        self.last_triggered_time = time.time()

    def update(self):
        """於 Main Loop 中持續調用，自動檢查並執行按鍵任務"""
        current_time = time.time()
        if current_time - self.last_triggered_time >= self.interval:
            print(f"[定時技能模組] 達到 {self.interval} 秒間隔，觸發按鍵: {self.key}")
            time.sleep(.5)
            pydirectinput.press(self.key)
            self.last_triggered_time = current_time
            return True
        return False


# ---------------------------------------------------------
# 小地圖偵測模組
# ---------------------------------------------------------

def get_minimap_region(win):
    """小地圖在遊戲視窗左上角"""
    return {
        "left": win.left + 10,
        "top": win.top + 100,
        "width": 150,
        "height": 100
    }


def find_player_on_minimap(game_img, minimap_region, win):
    """判斷小地圖中玩家位置，並回傳黃點相對座標"""
    minimap_crop = crop_region(game_img, minimap_region, win)

    bgr = cv2.cvtColor(minimap_crop, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # 對應 RGB(255, 255, 136) -> HSV 值的精準上下界
    lower_yellow = np.array([25, 100, 200])
    upper_yellow = np.array([35, 255, 255])

    mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if contours:
        # 圓點面積大小過濾 (小地圖黃點面積極小，通常在 1 ~ 20 像素之間)
        valid_contours = [c for c in contours if 1 <= cv2.contourArea(c) <= 25]
        if valid_contours:
            c = max(valid_contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                return (cx, cy)

    return None


def get_minimap_player_abs_pos(game_img, win):
    """回傳小地圖玩家絕對座標 (相對視窗左上),找不到回傳 None"""
    minimap_region = get_minimap_region(win)
    minimap_pos = find_player_on_minimap(game_img, minimap_region, win)
    if not minimap_pos:
        return None
    mm_x1 = minimap_region["left"] - win.left
    mm_y1 = minimap_region["top"] - win.top
    return (mm_x1 + minimap_pos[0], mm_y1 + minimap_pos[1])


def decide_move_direction(abs_mm_x, current_direction, cfg: BotConfig):
    """依小地圖 X 座標決定是否需要在邊界折返"""
    if abs_mm_x <= cfg.minimap_left_bound:
        if current_direction != "right":
            print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 right")
        return "right"
    elif abs_mm_x >= cfg.minimap_right_bound:
        if current_direction != "left":
            print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 left")
        return "left"
    return current_direction


def decide_move_target(player_pos, healer_pos, abs_mm_x, current_direction, cfg: BotConfig):
    """
    決定本次要往哪個方向移動 (或停止)。

    - 若偵測到補師,且補師 Y 座標與玩家 Y 座標相近 (視為同一平台/樓層):
      改成朝補師的 X 座標靠攏。已經在容忍範圍內就回傳 None (代表停止移動)。
    - 否則維持原本邊界折返邏輯 (decide_move_direction)。

    回傳值: "left" / "right" / None (None 代表這次不移動)
    """
    if healer_pos is not None:
        player_x, player_y = player_pos
        healer_x, healer_y = healer_pos

        if abs(player_y - healer_y) <= cfg.healer_y_tolerance:
            dx = healer_x - player_x
            if abs(dx) <= cfg.healer_x_dead_zone:
                print(f"[補師跟隨模組] 已到達補師附近 (dx={dx:.0f})，停止移動")
                return None

            direction = "right" if dx > 0 else "left"
            print(f"[補師跟隨模組] 同層偵測到補師 (dx={dx:.0f})，朝 {direction} 移動")
            return direction

    # 沒偵測到補師 或 不同層 -> 維持原本邊界折返邏輯
    return decide_move_direction(abs_mm_x, current_direction, cfg)


# ---------------------------------------------------------
# debug 模組
# ---------------------------------------------------------

def build_debug_image(win, screen, template_path='image/mo_00065.png', threshold=0.5,
                       attack_radius=300, player_target_pos=(0, 0),
                       healer_tag_path=None, healer_threshold=0.55,
                       healer_y_tolerance=30, healer_x_dead_zone=15):
    """
    根據傳入的 screen(已經截好的 BGRA 畫面) 繪製除錯圖層,回傳 debug_img。
    debug 模式著重可讀性而非效能,因此這裡仍用全螢幕搜尋,不套用 ROI 加速。
    """
    hp_region, mp_region = get_hp_mp_region(win)
    minimap_region = get_minimap_region(win)

    debug_img = cv2.cvtColor(screen, cv2.COLOR_BGRA2BGR)
    screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGRA2GRAY)

    for region, label, color in [
        (hp_region, "HP Bar", (0, 0, 255)),
        (mp_region, "MP Bar", (255, 0, 0)),
    ]:
        x1, y1 = region["left"] - win.left, region["top"] - win.top
        x2, y2 = x1 + region["width"], y1 + region["height"]
        cv2.rectangle(debug_img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(debug_img, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    mm_x1 = minimap_region["left"] - win.left
    mm_y1 = minimap_region["top"] - win.top
    mm_x2 = mm_x1 + minimap_region["width"]
    mm_y2 = mm_y1 + minimap_region["height"]

    cv2.rectangle(debug_img, (mm_x1, mm_y1), (mm_x2, mm_y2), (255, 255, 0), 2)
    cv2.putText(debug_img, "Minimap ROI", (mm_x1, mm_y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    minimap_pos = find_player_on_minimap(screen, minimap_region, win)
    if minimap_pos:
        abs_mm_x = mm_x1 + minimap_pos[0]
        abs_mm_y = mm_y1 + minimap_pos[1]
        cv2.circle(debug_img, (abs_mm_x, abs_mm_y), 7, (255, 255, 0), 2)
        cv2.putText(debug_img, f"Minimap Player ({abs_mm_x},{abs_mm_y})",
                    (mm_x2 + 10, mm_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
    else:
        cv2.putText(debug_img, "Minimap Player: NOT FOUND",
                    (mm_x2 + 10, mm_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

    player_x, player_y = player_target_pos
    h, w = debug_img.shape[:2]

    cv2.circle(debug_img, (player_x, player_y), attack_radius, (0, 255, 255), 1)
    cv2.circle(debug_img, (player_x, player_y), 6, (0, 255, 255), -1)
    cv2.putText(debug_img, f"Player ({player_x},{player_y})", (player_x + 10, player_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    # 畫出補師位置與跟隨容忍範圍 (橘色系,與玩家的黃色/怪物的桃紅區分)
    if healer_tag_path:
        healer_pos = find_player_by_tag(screen_gray, tag_template_path=healer_tag_path, threshold=healer_threshold)

        # 畫出「同層」判定帶: 玩家 Y ± healer_y_tolerance,橫跨整個畫面寬度
        band_top = player_y - healer_y_tolerance
        band_bottom = player_y + healer_y_tolerance
        overlay = debug_img.copy()
        cv2.rectangle(overlay, (0, band_top), (w, band_bottom), (0, 165, 255), -1)
        cv2.addWeighted(overlay, 0.15, debug_img, 0.85, 0, debug_img)
        cv2.line(debug_img, (0, band_top), (w, band_top), (0, 165, 255), 1)
        cv2.line(debug_img, (0, band_bottom), (w, band_bottom), (0, 165, 255), 1)
        cv2.putText(debug_img, f"Healer Y-tolerance ({healer_y_tolerance}px)", (10, band_top - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

        if healer_pos:
            healer_x, healer_y = healer_pos
            cv2.circle(debug_img, (healer_x, healer_y), 6, (0, 165, 255), -1)
            cv2.putText(debug_img, f"Healer ({healer_x},{healer_y})", (healer_x + 10, healer_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

            # 畫出「停止移動」死區: 補師 X ± healer_x_dead_zone
            dz_left = healer_x - healer_x_dead_zone
            dz_right = healer_x + healer_x_dead_zone
            cv2.rectangle(debug_img, (dz_left, healer_y - 60), (dz_right, healer_y + 60),
                          (0, 165, 255), 1)
            cv2.putText(debug_img, f"Dead zone ({healer_x_dead_zone}px)", (dz_left, healer_y + 75),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1)

            same_layer = abs(player_y - healer_y) <= healer_y_tolerance
            status = "SAME LAYER - FOLLOWING" if same_layer else "DIFF LAYER - IGNORED"
            cv2.putText(debug_img, f"Healer status: {status}", (10, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
        else:
            cv2.putText(debug_img, "Healer: NOT FOUND", (10, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

    monsters = find_monsters(screen_gray, template_path, threshold=threshold)
    for idx, (mx, my) in enumerate(monsters):
        dist = np.hypot(mx - player_x, my - player_y)
        color = (255, 0, 255) if dist <= attack_radius else (0, 255, 0)
        cv2.circle(debug_img, (mx, my), 8, color, -1)
        cv2.putText(debug_img, f"Monster #{idx+1} ({dist:.0f}px)", (mx + 10, my),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    return debug_img


def show_debug_window(debug_img, window_name="Bot Debug View"):
    """即時顯示 debug 畫面。要搭配主迴圈中每次呼叫 cv2.waitKey(1) 才會刷新。"""
    cv2.imshow(window_name, debug_img)
    cv2.waitKey(1)


def save_debug_image(debug_img, path="debug_game_screen.png"):
    cv2.imwrite(path, debug_img)
    print(f"{path} saved.")


if __name__ == "__main__":
    """要用系統管理員權限啟動 IDE 才能正確觸發 DirectInput 按鍵"""
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.05

    cfg = BotConfig()

    # 迴圈間狀態
    move_direction = "left"
    timed_key_task = TimedKeyTrigger(key='n', interval_seconds=120)
    tick_count = 0

    # 上次偵測到的角色/補師位置,用來做局部搜尋加速
    last_player_pos: Optional[Tuple[int, int]] = None
    last_healer_pos: Optional[Tuple[int, int]] = None

    # 啟動時搶一次焦點
    win = get_game_window(activate=True)

    with mss.MSS() as sct:
        while True:
            tick_count += 1

            # 只更新視窗座標,不搶焦點 (省下 SetForegroundWindow 的開銷)
            win = get_game_window(activate=False)
            if not win:
                print("找不到遊戲視窗")
                time.sleep(1)
                continue

            # 安全網: 每隔一段 tick 數重新搶一次焦點,避免使用者不慎切走視窗
            if cfg.reactivate_interval_ticks and tick_count % cfg.reactivate_interval_ticks == 0:
                activate_window(win)

            game_region = {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
            game_img = np.array(sct.grab(game_region))
            game_img_gray = cv2.cvtColor(game_img, cv2.COLOR_BGRA2GRAY)

            print("=" * 50)

            # 定時按鍵觸發
            timed_key_task.update()

            # 撿取道具 (非關鍵時序,略過內建 pause)
            pydirectinput.press('z', _pause=False)

            # HP / MP
            '''
            hp_percent, mp_percent = read_hp_mp(game_img, win)
            handle_hp_mp(hp_percent, mp_percent, cfg)
            print(f"HP: {hp_percent:.1f}%  MP: {mp_percent:.1f}%")
            '''

            # 怪物位置 (全螢幕搜尋,怪物會到處出現,無法用 ROI 加速)
            monster_positions = find_monsters(game_img_gray, cfg.template_paths, threshold=cfg.monsters_threshold)

            # 角色位置: 先在上次位置附近找,找不到才退回全螢幕搜尋
            player_target_pos = locate_tag_with_fallback(
                game_img_gray, 'image/player_tag.png', cfg.player_threshold,
                last_player_pos, cfg.player_search_margin
            )

            if player_target_pos is None:
                print("警告: 未偵測到玩家位置，重新判斷")
                keyup_all()
                time.sleep(cfg.main_loop_sleep)
                continue

            last_player_pos = player_target_pos
            px, py = player_target_pos

            # 補師位置 (找不到時為 None,屬正常情況,不中止流程)
            healer_target_pos = locate_tag_with_fallback(
                game_img_gray, cfg.healer_tag_path, cfg.healer_tag_threshold,
                last_healer_pos, cfg.healer_search_margin
            )
            if healer_target_pos is not None:
                last_healer_pos = healer_target_pos

            # 小地圖玩家座標
            abs_pos = get_minimap_player_abs_pos(game_img, win)
            if abs_pos is None:
                print("警告: 小地圖未偵測到玩家位置，重新判斷")
                keyup_all()
                #time.sleep(cfg.main_loop_sleep)
                continue
            abs_mm_x, abs_mm_y = abs_pos

            print(f"玩家位置: {player_target_pos}, 補師位置: {healer_target_pos}, "
                  f"小地圖座標: ({abs_mm_x}, {abs_mm_y}), 怪物數: {len(monster_positions)}")

            if cfg.debug:
                debug_img = build_debug_image(
                    win,
                    game_img,
                    attack_radius=cfg.attack_distance_threshold,
                    template_path=cfg.template_paths,
                    threshold=cfg.monsters_threshold,
                    player_target_pos=player_target_pos,
                    healer_tag_path=cfg.healer_tag_path,
                    healer_threshold=cfg.healer_tag_threshold,
                    healer_y_tolerance=cfg.healer_y_tolerance,
                    healer_x_dead_zone=cfg.healer_x_dead_zone,
                )
                if cfg.debug_show_window:
                    show_debug_window(debug_img)
                if cfg.debug_save_image:
                    save_debug_image(debug_img)
                #time.sleep(cfg.main_loop_sleep)
                continue

            # 移動目標判斷:同層有補師 -> 靠攏補師;否則維持邊界折返
            new_direction = decide_move_target(
                player_target_pos, healer_target_pos, abs_mm_x, move_direction, cfg
            )

            keyup_all()
            if new_direction is None:
                # 已到達補師附近,停止左右移動
                pass
            else:
                move_direction = new_direction
                pydirectinput.keyDown(move_direction, _pause=False)

            # 找出範圍內所有怪物,依數量決定範圍攻擊或單體攻擊
            monsters_in_range = find_monsters_in_range(
                monster_positions, player_target_pos, cfg.attack_distance_threshold
            )
            handle_attack(monsters_in_range, player_target_pos, move_direction, cfg)

            #time.sleep(cfg.main_loop_sleep)