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
import pyautogui
import subprocess

# ---------------------------------------------------------
# 設定集中管理
# ---------------------------------------------------------

@dataclass
class LayerConfig:
    """
    平台設定:Y 座標落在 [y_min, y_max] 視為在這一層,並套用這一層專屬的左右巡邏邊界。
    index 只是平台代號,供 RopeConfig 參照連接關係用。

    這裡的座標是透過小地圖上的色點換算出來的「遊戲視窗絕對像素座標」,
    和 debug_game_screen.png(或任何一張完整遊戲視窗截圖)裡的像素座標是同一個座標系,
    可以直接用圖片檢視器在截圖上點選色點讀出像素座標填入,不需要另外換算小地圖裁切區域內部的相對值。
    """
    index: int
    y_min: int
    y_max: int
    left_bound: int
    right_bound: int


@dataclass
class RopeConfig:
    """
    繩索設定:連接的下層/上層 index(對應 LayerConfig.index),以及繩索所在的 X 座標。

    X 座標同樣是「遊戲視窗絕對像素座標」(見 LayerConfig 說明),不是小地圖裁切區域內部的相對值,
    也不受角色移動/畫面捲動影響(因為是從小地圖上的色點換算而來,小地圖本身固定不動)。
    """
    x: int
    lower_layer: int
    upper_layer: int


@dataclass
class BotConfig:
    main_loop_sleep: float = 0.05
    monsters_threshold: float = 0.82
    template_paths: List[str] = field(default_factory=lambda: [
      # 'image/骷髏狗2.png',
       'image/木面人.png',
      # 'image/骷髏士兵1.png',
    ])
    player_threshold: float = 0.6
    attack_distance_threshold: int = 220
    move_center: int = 764

    # 骷髏狗
    #minimap_left_bound: int = 60
    #minimap_right_bound: int = 100

    # 木面
    minimap_left_bound: int = 55
    minimap_right_bound: int = 70

    hp_threshold: float = 10.0
    mp_threshold: float = 10.0
    heal_key: str = 'v'
    rest_key: str = 'x'

    single_attack_key: str = 'shift'
    aoe_attack_key: str = 'a'
    aoe_monster_count: int = 2  # 範圍內達到幾隻怪就改用範圍攻擊

    # ---- 效能相關設定 ----
    # 每隔多少個 tick 重新搶一次遊戲視窗焦點 (0 = 只在啟動時搶一次,之後不再搶)
    reactivate_interval_ticks: int = 1
    
    # 角色/補師標籤的局部搜尋半徑。上次有偵測到位置時,只在該位置附近搜尋,找不到才退回全螢幕搜尋
    player_search_margin: int = 150
    healer_search_margin: int = 200
    
    # ---- debug ----
    debug: bool = False
    debug_show_window: bool = False   # debug 時是否即時顯示監看視窗
    debug_save_image: bool = True   # debug 時是否額外存成檔案
    

    # ---- 補師跟隨模組 ----
    enable_healer_follow: bool = False   # 關閉時完全不搜尋補師、視為找不到補師,退回一般邊界巡邏
    healer_tag_path: str = 'image/healer_tag.png'
    healer_tag_threshold: float = 0.6
    healer_y_tolerance: int = 100   # Y座標差在此範圍內視為同一層
    healer_x_dead_zone: int = 100   # X座標差在此範圍內視為已到達補師旁邊,不再移動

    # ---- 斷線重連模組 ---- 
    enable_reconnect: bool = True

    # ---- 跨平台爬繩模組 ----
    # 定義地圖有哪些平台(遊戲視窗絕對像素座標的 Y 範圍 + 該層左右巡邏邊界,見 LayerConfig 說明)。
    # 保持空清單則完全不啟用跨層爬繩,行為退回原本單層 minimap_left_bound/right_bound 巡邏。
    layers: List[LayerConfig] = field(default_factory=list)
    # 定義每一條繩索的 X 座標(同樣是遊戲視窗絕對像素座標,見 RopeConfig 說明),
    # 以及它連接的上下兩層 index(需對應 layers 裡的 index)。
    ropes: List[RopeConfig] = field(default_factory=list)

    climb_up_key: str = 'up'
    drop_down_key: str = 'down'
    drop_jump_key: str = 'alt'

    rope_x_tolerance: int = 4          # 判定「已對齊繩索正下方/正上方」的小地圖 X 容忍度(像素)
    layer_reach_tolerance: int = 1     # 判定「已爬到目標層」的小地圖 Y 容忍度(像素)
    climb_timeout_seconds: float = 6.0        # 爬繩逾時保護,避免卡在半路不動
    min_seconds_between_climbs: float = 4.0   # 同一條繩索避免立刻來回爬,兩次使用間至少間隔幾秒
    post_transition_cooldown: float = 1.5     # 完成爬繩/掉落後,暫停幾秒讓動作播放完畢再重新判斷巡邏

    use_jump_to_grab_rope: bool = True
    grab_x_tolerance: int = 10          # 改用斜跳抓繩時,允許比 rope_x_tolerance 更寬鬆的對齊容忍度
    grab_hold_seconds: float = 0.15     # 起跳瞬間持續按住方向鍵的時間,製造橫向位移去咬繩
    grab_retry_interval: float = 0.6    # 還沒偵測到爬繩姿勢時,每隔多久重新嘗試跳一次抓繩
    grab_max_retries: int = 3           # 抓繩最多重試幾次,超過就放棄這次爬繩,交還一般巡邏判斷

    # 爬繩姿勢範本比對: 用來確認「真的已經抓到繩子在爬」,也用來判斷「是否已經爬完」,
    climbing_pose_template: str = 'image/climbing_pose.png'
    climbing_pose_threshold: float = 0.7
    climbing_pose_search_margin: int = 60
    # 連續幾次都偵測不到爬繩姿勢,才視為「真的已經離開繩索」,避免單一 tick 誤判
    climb_pose_lost_confirm_ticks: int = 2

    # 爬繩(往上)期間額外持續按著這個方向鍵,直到確認爬完為止才放開。
    # 用於繩索緊貼平台邊緣的地圖:角色爬到頂端剛好落在邊緣,容易被怪物撞下去,
    # 持續按著方向鍵可以讓角色一到平台就順勢往內側移動一點。設為 None 停用此行為。
    climb_drift_key: Optional[str] = None

    # 同一層至少要巡邏(觸碰邊界折返)幾次,才允許嘗試爬繩換到下一層,
    # 一趟「從左邊界走到右邊界」算 1 次折返,一個來回(左->右->左)則是 2 次。
    min_patrol_bounces_before_climb: int = 2

    # ---- 其他玩家 / 隊友偵測 (換平台前避讓用) ----
    # 目標平台小地圖上若偵測到其他玩家或隊友的色點,本次就放棄換到那一層,留在原地繼續巡邏。
    detect_other_players: bool = True

    # ---- 卡住偵測模組 (安全網,獨立於 RopeTraverser 運作) ----
    # 角色若因為被怪物擊退等原因意外掛在繩子上(不是 RopeTraverser 主動觸發的爬繩),
    # 小地圖座標會長時間停在原地不動、且左右方向鍵操控不了 X 座標。
    # 連續 stuck_ticks_threshold 個 tick 座標都沒什麼變化就視為卡住,
    # 若同時比對到 climbing_pose_template,就嘗試按住爬繩鍵直到脫離繩索為止。
    stuck_position_tolerance: int = 1            # 座標變化在這個範圍內視為「沒有移動」
    stuck_ticks_threshold: int = 30              # 連續幾個 tick 沒有移動才視為卡住
    stuck_recovery_timeout_seconds: float = 8.0  # 嘗試脫困最多幾秒,逾時放棄,避免卡死在回復邏輯裡

# ---------------------------------------------------------
# tool
# ---------------------------------------------------------

def activate_window(win):
    """
    將視窗帶到前景並取得焦點。
    若視窗已最小化,先還原,否則 ShowWindow(hwnd, 5) 對最小化視窗可能無效。
    """
    hwnd = win._hWnd
    try:
        if win.isMinimized:
            win.restore()
    except Exception:
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    ctypes.windll.user32.ShowWindow(hwnd, 5)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def get_window(title_exact=None, title_contains=None, activate=False):
    """
    通用視窗尋找函式。
    先用完整標題精準匹配,找不到再用「標題包含關鍵字」模糊匹配(例如 Chrome 分頁標題會變動)。
    """
    wins = []
    if title_exact:
        wins = gw.getWindowsWithTitle(title_exact)
    if not wins and title_contains:
        wins = [w for w in gw.getAllWindows() if title_contains in w.title]
    if not wins:
        return None
    win = wins[0]
    if activate:
        activate_window(win)
    return win


def get_game_window(title='新楓之谷', activate=False):
    """
    取得遊戲視窗物件。
    activate=True 時才會呼叫 SetForegroundWindow 搶焦點 -- 這個系統呼叫較慢,
    平常每個 tick 只需要更新視窗座標,不需要每次都搶焦點。
    """
    return get_window(title_exact=title, activate=activate)


def keyup_all(keys=('left', 'right')):
    # 放開按鍵是否成功不影響時序精準度,略過內建 pause 節省時間
    for key in keys:
        pydirectinput.keyUp(key, _pause=False)


def print_debug_img(img):
    cv2.imwrite('debug_game_img.png', img)
    

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
    """讀取並快取單一灰階範本圖片"""
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
    透過角色名字標籤或勛章來定位角色位置。
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
    """
    region = _build_search_roi(prev_pos, game_screen_gray.shape, margin)
    pos = find_player_by_tag(game_screen_gray, tag_template_path, threshold, search_region=region)
    if pos is None and region is not None:
        pos = find_player_by_tag(game_screen_gray, tag_template_path, threshold, search_region=None)
    return pos


def is_player_climbing(game_screen_gray, player_pos, template_path, threshold, search_margin):
    """
    在玩家上次位置附近搜尋「爬繩姿勢」範本,用來確認角色是否真的抓到繩子在爬,
    而不是只憑「已經按下爬繩鍵」就假設一定成功(咬繩子有機率咬不到)。
    """
    if player_pos is None:
        return False
    template = _load_tag_template(template_path)
    if template is None:
        return False

    region = _build_search_roi(player_pos, game_screen_gray.shape, search_margin)
    if region is None:
        return False
    x1, y1, x2, y2 = region
    roi = game_screen_gray[y1:y2, x1:x2]

    th, tw = template.shape[:2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return False

    res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return max_val >= threshold


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
    """搜尋多種怪物位置，回傳怪物中心點座標列表 [(x1, y1), ...]"""
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
        and abs(my-py) < 100    # 木面 @@@
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


def find_colored_dots_on_minimap(minimap_crop_bgr, lower_hsv, upper_hsv,
                                  lower_hsv2=None, upper_hsv2=None,
                                  min_area=1, max_area=25):
    """
    在小地圖裁切影像 (BGR) 中,找出符合 HSV 顏色範圍的所有色點中心座標。
    紅色在 HSV 色環頭尾都算紅,lower_hsv2/upper_hsv2 用來補上靠近 179 那一段範圍。
    """
    hsv = cv2.cvtColor(minimap_crop_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lower_hsv), np.array(upper_hsv))
    if lower_hsv2 is not None and upper_hsv2 is not None:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, np.array(lower_hsv2), np.array(upper_hsv2)))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    positions = []
    for c in contours:
        area = cv2.contourArea(c)
        if min_area <= area <= max_area:
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                positions.append((cx, cy))
    return positions


def get_other_player_minimap_positions(game_img, win):
    """
    回傳小地圖上其他玩家的絕對座標 (相對視窗左上) 列表。
    其他玩家的色點固定顯示為 #EE0000 (紅),是遊戲寫死的顏色、不會隨地圖或帳號改變,
    比照 find_player_on_minimap 對自己黃點的作法,直接寫死在這裡不放進 BotConfig。
    """
    minimap_region = get_minimap_region(win)
    minimap_crop = crop_region(game_img, minimap_region, win)
    bgr = cv2.cvtColor(minimap_crop, cv2.COLOR_BGRA2BGR)

    # 紅色在色環頭尾都算紅,故補一段靠近 179 的範圍。
    lower_red = np.array([0, 180, 180])
    upper_red = np.array([3, 255, 255])
    lower_red2 = np.array([177, 180, 180])
    upper_red2 = np.array([180, 255, 255])

    rel_positions = find_colored_dots_on_minimap(bgr, lower_red, upper_red, lower_red2, upper_red2)

    mm_x1 = minimap_region["left"] - win.left
    mm_y1 = minimap_region["top"] - win.top
    return [(mm_x1 + x, mm_y1 + y) for (x, y) in rel_positions]


def get_teammate_minimap_positions(game_img, win):
    """
    回傳小地圖上隊友的絕對座標 (相對視窗左上) 列表。
    隊友的色點固定顯示為 #FF7700 (橘),是遊戲寫死的顏色、不會隨地圖或帳號改變,
    比照 find_player_on_minimap 對自己黃點的作法,直接寫死在這裡不放進 BotConfig。
    """
    minimap_region = get_minimap_region(win)
    minimap_crop = crop_region(game_img, minimap_region, win)
    bgr = cv2.cvtColor(minimap_crop, cv2.COLOR_BGRA2BGR)

    # 對應 RGB(255, 119, 0) -> HSV 值
    lower_orange = np.array([12, 180, 180])
    upper_orange = np.array([16, 255, 255])

    rel_positions = find_colored_dots_on_minimap(bgr, lower_orange, upper_orange)

    mm_x1 = minimap_region["left"] - win.left
    mm_y1 = minimap_region["top"] - win.top
    return [(mm_x1 + x, mm_y1 + y) for (x, y) in rel_positions]


def find_occupied_layers(positions, layers: List['LayerConfig']):
    """依 Y 座標把每個點對應到所在平台,回傳有出現點的平台 index 集合"""
    occupied = set()
    for _, y in positions:
        layer = find_layer_by_y(y, layers)
        if layer is not None:
            occupied.add(layer.index)
    return occupied


def decide_move_direction(abs_mm_x, current_direction, cfg: BotConfig, left_bound=None, right_bound=None):
    """依小地圖 X 座標決定是否需要在邊界折返。
    left_bound/right_bound 未提供時退回 cfg.minimap_left_bound/right_bound(向後相容單層巡邏)。"""
    left_bound = cfg.minimap_left_bound if left_bound is None else left_bound
    right_bound = cfg.minimap_right_bound if right_bound is None else right_bound

    if abs_mm_x <= left_bound:
        if current_direction != "right":
            print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 right")
        return "right"
    elif abs_mm_x >= right_bound:
        if current_direction != "left":
            print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 left")
        return "left"
    return current_direction


def decide_move_target(player_pos, healer_pos, abs_mm_x, current_direction, cfg: BotConfig,
                        left_bound=None, right_bound=None):
    """
    決定本次要往哪個方向移動 (或停止)。

    - 若偵測到補師,且補師 Y 座標與玩家 Y 座標相近 (視為同一平台/樓層):
      改成朝補師的 X 座標靠攏。已經在容忍範圍內就回傳 None (代表停止移動)。
    - 否則維持原本邊界折返邏輯 (decide_move_direction)。

    left_bound/right_bound: 可傳入目前所在平台(LayerConfig)的邊界,取代 cfg 裡的預設值,
    供跨平台爬繩模組依「目前平台」而非全域單一邊界做巡邏判斷。

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
    return decide_move_direction(abs_mm_x, current_direction, cfg, left_bound, right_bound)


def find_layer_by_y(abs_mm_y, layers: List[LayerConfig]) -> Optional[LayerConfig]:
    """依 Y 座標(遊戲視窗絕對像素座標,見 LayerConfig 說明)找出目前所在的平台,找不到回傳 None"""
    for layer in layers:
        if layer.y_min <= abs_mm_y <= layer.y_max:
            return layer
    return None


def find_rope_near_x(abs_mm_x, layer_index, ropes: List[RopeConfig], tolerance) -> Optional[RopeConfig]:
    """在目前平台中,找出 X 座標(遊戲視窗絕對像素座標,見 RopeConfig 說明)落在容忍範圍內、
    且與這一層相連(上層或下層皆可)的繩索。用於「往上爬」的情境——抓繩需要對齊繩索的確切位置。"""
    for rope in ropes:
        if layer_index not in (rope.lower_layer, rope.upper_layer):
            continue
        if abs(abs_mm_x - rope.x) <= tolerance:
            return rope
    return None


def find_rope_down_from_layer(layer_index, ropes: List[RopeConfig]) -> Optional[RopeConfig]:
    """
    找出可以從這一層直接往下掉落到下層的繩索連接,不檢查 X 座標。
    掉落(下+跳躍鍵)是瞬間動作,平台上任何位置都能直接掉到下層,不像往上爬繩需要先對齊繩索位置;
    有些繩索的 X 座標甚至落在下層平台自己的巡邏邊界之外,單靠 find_rope_near_x 永遠不會被觸發到。
    """
    for rope in ropes:
        if rope.upper_layer == layer_index:
            return rope
    return None


class RopeTraverser:
    """
    跨平台爬繩/掉落狀態機。

    一旦呼叫 start() 啟動,接下來每個 tick 都改由 step() 接管移動判斷,依序經過:

    - "align": 左右移動,對齊繩索的 X 座標(遊戲視窗絕對像素座標,見 RopeConfig 說明)。
    - "grab" (只有往上爬、且 cfg.use_jump_to_grab_rope 開啟時才會經過):
      對齊後改用「方向鍵 + 跳躍鍵」斜向跳起、同時按住爬繩鍵去咬繩,
      比站在原地直接按爬繩鍵更容易真的抓到(咬繩子有機率咬不到,斜跳能多涵蓋一點橫向距離)。
      每隔 grab_retry_interval 秒用 climbing_pose_template 比對確認是否已經抓到繩子,
      沒抓到就再跳一次,重試達 grab_max_retries 次仍失敗就放棄這次爬繩。
    - "climb": 已確認在爬繩(或掉落已觸發),持續按著爬繩鍵,直到連續
      climb_pose_lost_confirm_ticks 次都偵測不到爬繩姿勢才視為爬完(而不是用小地圖 Y 座標推測——
      小地圖太小,Y 範圍常常在角色實際到達平台前就先進入判定範圍,導致提前放開爬繩鍵卡在半路)。
      往下掉落是瞬間動作,對齊後按一次即完成,不會經過 grab 階段。
      若設定了 cfg.climb_drift_key,確認抓到繩子起就會額外持續按著這個方向鍵直到爬完放開,
      讓角色到達平台時順勢往內側移動一點,避免落在繩索正上方的平台邊緣被怪物撞下去。

    start() 也可以傳入 skip_align=True(僅用於 direction="down"),跳過 "align" 直接觸發掉落——
    掉落是瞬間動作,平台上任何位置都能觸發,不需要先走到繩索的 X 座標才能掉下去。
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.active = False
        self.rope: Optional[RopeConfig] = None
        self.direction: Optional[str] = None    # "up" 或 "down"
        self.phase: Optional[str] = None        # "align" / "grab" / "climb"
        self.jump_dir: Optional[str] = None     # 抓繩起跳時使用的方向鍵
        self.start_time: float = 0.0
        self.grab_attempts: int = 0
        self.last_grab_attempt_time: float = 0.0
        self.pose_lost_streak: int = 0   # "climb" 階段連續幾次沒偵測到爬繩姿勢
        self.last_transition_time: float = 0.0
        self.last_rope_x: Optional[int] = None

    def can_start(self, rope: RopeConfig) -> bool:
        """避免同一條繩索剛完成動作就立刻來回爬"""
        if self.last_rope_x == rope.x and \
                (time.time() - self.last_transition_time) < self.cfg.min_seconds_between_climbs:
            return False
        return True

    def start(self, rope: RopeConfig, direction: str, skip_align: bool = False):
        """
        skip_align=True 用於「往下掉落且不需要對齊 X 座標」的情境:掉落是瞬間動作,
        平台上任何位置都能觸發,不用像往上爬繩一樣先走到繩索的確切位置。
        """
        action_label = "爬繩上樓" if direction == "up" else "掉落下樓"
        print(f"[跨平台爬繩模組] 開始{action_label} (繩索 X={rope.x}, "
              f"平台 {rope.lower_layer} <-> {rope.upper_layer})")
        self.active = True
        self.rope = rope
        self.direction = direction
        self.jump_dir = None
        self.start_time = time.time()
        self.grab_attempts = 0
        self.pose_lost_streak = 0

        if skip_align and direction == "down":
            self.phase = "climb"
            self._perform_drop()
        else:
            self.phase = "align"

    def _perform_drop(self):
        """按住下鍵再點一下跳躍鍵,觸發掉落到下層平台的動作(瞬間動作,不需對齊 X 座標)"""
        pydirectinput.keyDown(self.cfg.drop_down_key, _pause=False)
        time.sleep(0.05)
        pydirectinput.press(self.cfg.drop_jump_key, _pause=False)

    def _finish(self, reason=""):
        keyup_all(('left', 'right', self.cfg.climb_up_key, self.cfg.drop_down_key))
        if self.rope is not None:
            self.last_rope_x = self.rope.x
        self.last_transition_time = time.time()
        self.active = False
        self.rope = None
        self.direction = None
        self.phase = None
        self.jump_dir = None
        if reason:
            print(f"[跨平台爬繩模組] 動作結束 ({reason})")

    def _attempt_grab_jump(self, abs_mm_x):
        """對齊完成的瞬間,用「方向鍵 + 跳躍鍵」斜向跳起去咬繩,再按住爬繩鍵"""
        direction = self.jump_dir or ("right" if self.rope.x >= abs_mm_x else "left")
        self.jump_dir = direction
        keyup_all(('left', 'right'))
        pydirectinput.keyDown(direction, _pause=False)
        pydirectinput.press(self.cfg.drop_jump_key, _pause=False)
        pydirectinput.keyDown(self.cfg.climb_up_key, _pause=False)
        time.sleep(self.cfg.grab_hold_seconds)
        pydirectinput.keyUp(direction, _pause=False)
        self.grab_attempts += 1
        self.last_grab_attempt_time = time.time()

    def step(self, abs_mm_x, abs_mm_y, layers: List[LayerConfig],
             player_screen_gray=None, player_pos=None) -> bool:
        """執行一個 tick 的爬繩/掉落動作。回傳 True 代表本 tick 的移動已由本模組接管"""
        if not self.active or self.rope is None:
            return False

        if time.time() - self.start_time > self.cfg.climb_timeout_seconds:
            self._finish("逾時保護,放棄本次動作")
            return True

        if self.phase == "align":
            dx = self.rope.x - abs_mm_x
            grab_via_jump = self.direction == "up" and self.cfg.use_jump_to_grab_rope
            tolerance = self.cfg.grab_x_tolerance if grab_via_jump else self.cfg.rope_x_tolerance

            if abs(dx) <= tolerance:
                keyup_all(('left', 'right'))
                if self.direction == "up":
                    if grab_via_jump:
                        self.phase = "grab"
                        self._attempt_grab_jump(abs_mm_x)
                    else:
                        self.phase = "climb"
                        pydirectinput.keyDown(self.cfg.climb_up_key, _pause=False)
                        if self.cfg.climb_drift_key:
                            pydirectinput.keyDown(self.cfg.climb_drift_key, _pause=False)
                else:
                    self.phase = "climb"
                    self._perform_drop()
            else:
                move_dir = "right" if dx > 0 else "left"
                self.jump_dir = move_dir
                keyup_all(('left', 'right'))
                pydirectinput.keyDown(move_dir, _pause=False)
            return True

        if self.phase == "grab":
            climbing = player_screen_gray is not None and is_player_climbing(
                player_screen_gray, player_pos,
                self.cfg.climbing_pose_template, self.cfg.climbing_pose_threshold,
                self.cfg.climbing_pose_search_margin
            )

            if climbing:
                print("[跨平台爬繩模組] 已偵測到爬繩姿勢,確認抓到繩子")
                self.phase = "climb"
                if self.cfg.climb_drift_key:
                    pydirectinput.keyDown(self.cfg.climb_drift_key, _pause=False)
                return True

            if self.grab_attempts >= self.cfg.grab_max_retries:
                self._finish("多次嘗試仍未抓到繩子,放棄本次爬繩")
                return True

            if time.time() - self.last_grab_attempt_time >= self.cfg.grab_retry_interval:
                print(f"[跨平台爬繩模組] 尚未抓到繩子,重試第 {self.grab_attempts + 1} 次")
                self._attempt_grab_jump(abs_mm_x)
            return True

        if self.phase == "climb":
            if self.direction == "down":
                # 掉落是瞬間動作,放開下鍵後就視為完成,由下個 tick 重新判斷所在平台
                self._finish("掉落動作已觸發")
                return True

            # 用「是否還在爬繩姿勢」來判斷有沒有爬完,而不是單靠小地圖 Y 座標推測 ——
            # 小地圖太小,Y 範圍常常在角色實際到達平台前就先進入判定範圍,導致提前放開爬繩鍵、
            # 卡在繩索中途。連續 climb_pose_lost_confirm_ticks 次都偵測不到才視為真的爬完,
            # 避免單一 tick 的誤判(動畫過場、短暫遮擋)就提前結束。
            if player_screen_gray is not None:
                still_climbing = is_player_climbing(
                    player_screen_gray, player_pos,
                    self.cfg.climbing_pose_template, self.cfg.climbing_pose_threshold,
                    self.cfg.climbing_pose_search_margin
                )
                if still_climbing:
                    self.pose_lost_streak = 0
                else:
                    self.pose_lost_streak += 1
                    if self.pose_lost_streak >= self.cfg.climb_pose_lost_confirm_ticks:
                        self._finish("已不再偵測到爬繩姿勢,視為已離開繩索")
            else:
                # 沒有畫面可比對姿勢時,退回用小地圖 Y 座標當備援判斷
                target_layer = next((layer for layer in layers if layer.index == self.rope.upper_layer), None)
                if target_layer is not None and \
                        target_layer.y_min - self.cfg.layer_reach_tolerance <= abs_mm_y \
                        <= target_layer.y_max + self.cfg.layer_reach_tolerance:
                    self._finish("已到達目標層(備援判斷)")
            return True

        return False


class PatrolLapTracker:
    """
    追蹤角色在目前平台已經來回巡邏(觸碰邊界折返)幾次。
    換到新平台時自動歸零重算,達到 cfg.min_patrol_bounces_before_climb 之前不允許嘗試爬繩換層,
    避免角色一靠近繩索附近就馬上換平台、同一層還沒打幾隻怪就走了。
    """

    def __init__(self):
        self.layer_index: Optional[int] = None
        self.bounce_count: int = 0
        self._at_bound: bool = False

    def update(self, current_layer: Optional[LayerConfig], abs_mm_x: int) -> int:
        """回傳目前這一層累積的折返次數;current_layer 為 None 時回傳 0"""
        if current_layer is None:
            return 0

        if self.layer_index != current_layer.index:
            self.layer_index = current_layer.index
            self.bounce_count = 0
            self._at_bound = False

        at_bound_now = abs_mm_x <= current_layer.left_bound or abs_mm_x >= current_layer.right_bound
        if at_bound_now and not self._at_bound:
            self.bounce_count += 1
        self._at_bound = at_bound_now

        return self.bounce_count

    def reset(self):
        self.layer_index = None
        self.bounce_count = 0
        self._at_bound = False


class StuckWatchdog:
    """
    偵測角色是否長時間停在同一個小地圖座標(可能被怪物擊退等原因意外掛在繩子上,
    而不是透過 RopeTraverser 主動觸發的爬繩)。

    連續 cfg.stuck_ticks_threshold 個 tick 座標都沒什麼變化就視為卡住;此時若比對
    climbing_pose_template 確認角色真的掛在繩子上,就按住爬繩鍵嘗試往上爬,
    直到偵測不到爬繩姿勢(代表已經踩上平台、左右方向鍵應該可以重新操控 X 座標)為止,
    或超過 stuck_recovery_timeout_seconds 逾時放棄。
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.last_pos: Optional[Tuple[int, int]] = None
        self.stuck_ticks: int = 0
        self.recovering: bool = False
        self.recovery_start_time: float = 0.0

    def update(self, abs_mm_x, abs_mm_y, player_screen_gray, player_pos) -> bool:
        """每個 tick 呼叫一次。回傳 True 代表本 tick 已由脫困邏輯接管,不應再執行一般巡邏判斷。"""
        if self.recovering:
            still_climbing = is_player_climbing(
                player_screen_gray, player_pos,
                self.cfg.climbing_pose_template, self.cfg.climbing_pose_threshold,
                self.cfg.climbing_pose_search_margin
            )
            timed_out = (time.time() - self.recovery_start_time) > self.cfg.stuck_recovery_timeout_seconds
            if not still_climbing or timed_out:
                reason = "逾時放棄" if timed_out else "已脫離繩索"
                print(f"[卡住偵測模組] 脫困動作結束 ({reason})")
                pydirectinput.keyUp(self.cfg.climb_up_key, _pause=False)
                self.recovering = False
                self.stuck_ticks = 0
                self.last_pos = (abs_mm_x, abs_mm_y)
                return False
            return True

        current_pos = (abs_mm_x, abs_mm_y)
        if self.last_pos is not None and \
                abs(current_pos[0] - self.last_pos[0]) <= self.cfg.stuck_position_tolerance and \
                abs(current_pos[1] - self.last_pos[1]) <= self.cfg.stuck_position_tolerance:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
        self.last_pos = current_pos

        if self.stuck_ticks < self.cfg.stuck_ticks_threshold:
            return False

        if is_player_climbing(player_screen_gray, player_pos,
                               self.cfg.climbing_pose_template, self.cfg.climbing_pose_threshold,
                               self.cfg.climbing_pose_search_margin):
            print(f"[卡住偵測模組] 座標連續 {self.stuck_ticks} 個 tick 沒有變化,"
                  f"且偵測到爬繩姿勢,嘗試按住爬繩鍵脫困")
            keyup_all()
            pydirectinput.keyDown(self.cfg.climb_up_key, _pause=False)
            self.recovering = True
            self.recovery_start_time = time.time()
            self.stuck_ticks = 0
            return True

        # 卡住但不是掛在繩子上,交由其他既有機制處理(例如找不到玩家標籤時的重新判斷),
        # 這裡只歸零計數,避免每個 tick 都重複比對爬繩姿勢
        self.stuck_ticks = 0
        return False


class LayerSweepDirector:
    """
    決定跨平台移動時該往上還是往下,讓角色像電梯一樣完整掃過所有平台
    (例如 0 -> 1 -> 2 -> 1 -> 0 -> 1 -> 2 -> ...),而不是因為「往下掉落不需要對齊、
    永遠比往上爬容易觸發」,就一直卡在下面幾層來回移動、永遠上不到最上層。

    邏輯很單純(類似磁碟排程的 SCAN 演算法):目前往哪個方向走(up/down)就盡量繼續往那個方向走,
    直到那個方向已經沒有平台可以再移動(到頂或到底)才反過來。
    這假設平台是像樓層一樣「一條線」排列、沒有分岔,符合目前地圖的接法。
    """

    def __init__(self, initial_direction: str = "up"):
        self.direction = initial_direction

    def decide(self, current_layer_index: int, ropes: List[RopeConfig]) -> str:
        """回傳這次應該嘗試的方向("up" 或 "down"),必要時會先反轉掃描方向"""
        has_up = any(r.lower_layer == current_layer_index for r in ropes)
        has_down = any(r.upper_layer == current_layer_index for r in ropes)

        if self.direction == "up" and not has_up and has_down:
            self.direction = "down"
        elif self.direction == "down" and not has_down and has_up:
            self.direction = "up"

        return self.direction


# ---------------------------------------------------------
# debug 模組
# ---------------------------------------------------------

def build_debug_image(win, screen, template_path='image/mo_00065.png', threshold=0.5,
                       attack_radius=300, player_target_pos=(0, 0),
                       healer_tag_path=None, healer_threshold=0.55,
                       healer_y_tolerance=30, healer_x_dead_zone=15,
                       layers: Optional[List['LayerConfig']] = None,
                       ropes: Optional[List['RopeConfig']] = None,
                       show_other_players: bool = False):
    """
    根據傳入的 screen(已經截好的 BGRA 畫面) 繪製除錯用的標記與疊圖,回傳 debug_img。
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

    # 跨平台爬繩模組校正輔助: layers/ropes 座標本來就是遊戲視窗絕對像素座標,
    # 直接疊在整張截圖上畫,不能再加小地圖裁切偏移量(mm_x1/mm_y1),
    # 這樣才能直接對照畫面裡實際的平台/繩索位置來校正數值。
    layer_y_ranges = {}
    if layers:
        for layer in layers:
            cv2.rectangle(debug_img, (layer.left_bound, layer.y_min),
                          (layer.right_bound, layer.y_max), (0, 255, 0), 1)
            cv2.putText(debug_img, f"L{layer.index}", (layer.left_bound, layer.y_min - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            layer_y_ranges[layer.index] = (layer.y_min, layer.y_max)

    if ropes:
        img_h = debug_img.shape[0]
        for rope in ropes:
            lower_range = layer_y_ranges.get(rope.lower_layer)
            upper_range = layer_y_ranges.get(rope.upper_layer)
            # 兩端平台都有設定時,只畫兩層之間的那一段;缺一邊就整條貫穿畫面方便排查設定問題
            ry1 = lower_range[0] if lower_range else 0
            ry2 = upper_range[1] if upper_range else img_h
            cv2.line(debug_img, (rope.x, ry1), (rope.x, ry2), (0, 0, 255), 1)
            cv2.putText(debug_img, f"{rope.lower_layer}<->{rope.upper_layer}",
                        (rope.x + 4, (ry1 + ry2) // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

    # 其他玩家 / 隊友偵測校正輔助: 把實際偵測到的色點畫出來,方便排查誤判
    if show_other_players:
        for (ox, oy) in get_other_player_minimap_positions(screen, win):
            cv2.drawMarker(debug_img, (ox, oy), (0, 0, 255), markerType=cv2.MARKER_TILTED_CROSS,
                            markerSize=12, thickness=2)
            cv2.putText(debug_img, f"Other({ox},{oy})", (ox + 8, oy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
        for (tx, ty) in get_teammate_minimap_positions(screen, win):
            cv2.drawMarker(debug_img, (tx, ty), (0, 140, 255), markerType=cv2.MARKER_TILTED_CROSS,
                            markerSize=12, thickness=2)
            cv2.putText(debug_img, f"Teammate({tx},{ty})", (tx + 8, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 140, 255), 1)

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


def build_debug_image_from_cfg(win, game_img, player_target_pos, cfg: BotConfig):
    """build_debug_image 的固定參數都取自 win/game_img/cfg"""
    return build_debug_image(
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
        layers=cfg.layers,
        ropes=cfg.ropes,
        show_other_players=cfg.detect_other_players,
    )


def save_debug_snapshot(win, game_img, player_target_pos, cfg: BotConfig, path):
    """組好除錯畫面並直接存檔"""
    save_debug_image(build_debug_image_from_cfg(win, game_img, player_target_pos, cfg), path=path)


# ---------------------------------------------------------
# 斷線重連模組
# ---------------------------------------------------------

@dataclass
class ReconnectConfig:
    # 特徵範本
    disconnect_template: str = 'image/disconnect_notice.png'
    disconnect_threshold: float = 0.7
    web_header_template: str = 'image/chrome_header_feature.png'
    web_header_threshold: float = 0.8
    server_select_template: str = 'image/server_select_feature.png'
    server_select_threshold: float = 0.8
    disconnect_character_template: str = 'image/disconnect_character.png'
    disconnect_character_threshold: float = 0.7
    
    # LIE DETECTOR 防外掛檢測視窗
    lie_detector_template: str = 'image/lie_detector_notice.png'
    lie_detector_threshold: float = 0.7

    # 視窗 / 程序名稱
    game_window_title: str = '新楓之谷'
    game_process_name: str = 'MapleStory_Classic.exe'
    chrome_window_title: str = 'Google Chrome'
    reconnect_url: str = 'https://maplestoryclassic.beanfun.com/Main'  # 找不到 Chrome 視窗時的備援啟動網址

    # 重試策略
    max_reconnect_attempts: int = 3
    retry_backoff_seconds: float = 15.0   # 一輪嘗試失敗後,休息多久再試下一輪
    max_server_select_retries: int = 3    # 進入角色選擇畫面前偵測到斷線,最多重選伺服器幾次

    # 除錯: 每次點擊前是否存一張標記點擊座標的截圖 (方便校正比例)
    debug_click_screenshots: bool = False

    # ---- Chrome 視窗內的點擊比例 (相對視窗寬高,不是相對整個桌面!) ----
    # 每台電腦情況可能不同，第一次使用建議搭配 debug_click_screenshots=True 校正一次
    launch_game_btn_ratio: Tuple[float, float] = (0.93, 0.68)
    gamapass_btn_ratio: Tuple[float, float] = (0.50, 0.54)
    account_entry_ratio: Tuple[float, float] = (0.50, 0.39)
    bagel_char_ratio: Tuple[float, float] = (0.50, 0.49)
    continue_btn_ratio: Tuple[float, float] = (0.50, 0.72)

    # ---- 遊戲視窗內的點擊比例 ----
    server_name_ratio: Tuple[float, float] = (0.335, 0.28)
    channel_scrollbar_ratio: Tuple[float, float] = (0.66, 0.67)
    channel_area_ratio_ch57: Tuple[float, float] = (0.4, 0.6)
    channel_area_ratio_ch3: Tuple[float, float] = (0.54, 0.51)
    channel_enter_ratio: Tuple[float, float] = (0.60, 0.47)

class ReconnectManager:
    def __init__(self, rc: ReconnectConfig):
        self.rc = rc

    # ---------------- 基礎工具 ----------------

    def debug_click(self, x, y, action_name="click"):
        """在點擊前截圖並在點擊目標座標上標記紅圈與十字,方便校正比例是否正確。"""
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            img = np.array(sct.grab(monitor))

            cv2.circle(img, (int(x), int(y)), 10, (0, 0, 255), 2)
            cv2.line(img, (int(x) - 15, int(y)), (int(x) + 15, int(y)), (0, 0, 255), 2)
            cv2.line(img, (int(x), int(y) - 15), (int(x), int(y) + 15), (0, 0, 255), 2)

            filename = f"debug_{action_name}_{int(x)}_{int(y)}.png"
            cv2.imwrite(filename, img)
            print(f"[Debug] 已將點擊座標 ({x}, {y}) 標記並保存至 {filename}")

    def _get_chrome_window(self, activate=False):
        return get_window(title_exact=self.rc.chrome_window_title,
                           title_contains='Chrome', activate=activate)

    def _click_ratio(self, win, ratio, label):
        """在指定視窗內,依照 (rx, ry) 比例點擊,不依賴整個桌面/多螢幕尺寸"""
        if win is None:
            print(f"[斷線重連模組] 警告: 視窗不存在,無法點擊「{label}」,跳過")
            return False
        rx, ry = ratio
        x = win.left + int(win.width * rx)
        y = win.top + int(win.height * ry)
        if self.rc.debug_click_screenshots:
            self.debug_click(x, y, action_name=label)
        pyautogui.click(x=x, y=y)
        return True

    # ---------------- 偵測 ----------------

    def is_disconnected(self, game_img):
        """判斷目前遊戲畫面是否顯示斷線對話框"""
        template = _load_tag_template(self.rc.disconnect_template)
        if template is None:
            return False
        try:
            game_gray = cv2.cvtColor(game_img, cv2.COLOR_BGRA2GRAY)
            res = cv2.matchTemplate(game_gray, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            return max_val >= self.rc.disconnect_threshold
        except Exception as e:
            print(f"[斷線重連模組] is_disconnected 發生例外: {e}")
            return False

    def is_lie_detector_open(self, game_img):
        """判斷目前遊戲畫面是否顯示 LIE DETECTOR 防外掛檢測視窗(遊戲畫面內的圖案,非獨立跳出的視窗)"""
        template = _load_tag_template(self.rc.lie_detector_template)
        if template is None:
            return False
        try:
            game_gray = cv2.cvtColor(game_img, cv2.COLOR_BGRA2GRAY)
            res = cv2.matchTemplate(game_gray, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            return max_val >= self.rc.lie_detector_threshold
        except Exception as e:
            print(f"[斷線重連模組] is_lie_detector_open 發生例外: {e}")
            return False

    def force_close_game(self, max_retries=3, retry_delay=3.0):
        """強制關閉舊的遊戲視窗與相關程序，失敗時會重複嘗試。"""
        print("[斷線重連模組] 正在檢查並強制關閉舊的遊戲視窗與程序...")

        for attempt in range(1, max_retries + 1):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", self.rc.game_process_name, "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"[斷線重連模組] 第 {attempt} 次關閉指令執行異常: {e}")

            time.sleep(1.0)

            remaining_wins = gw.getWindowsWithTitle(self.rc.game_window_title)
            check_process = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.rc.game_process_name}"],
                capture_output=True,
                text=True
            )
            process_exists = self.rc.game_process_name in check_process.stdout

            if not remaining_wins and not process_exists:
                print(f"[斷線重連模組] 第 {attempt} 次嘗試成功！遊戲視窗與進程已完全清理。")
                time.sleep(1.0)
                return True

            print(f"[斷線重連模組] 第 {attempt} 次強制關閉未完全成功，{retry_delay} 秒後重試...")
            time.sleep(retry_delay)

        print("[斷線重連模組] 警告：已達到最大重試次數，將嘗試繼續執行重連流程。")
        return False

    def wait_for_web_feature(self, timeout=10.0):
        """等待並判斷螢幕畫面上是否出現 Chrome 官網導覽列特徵。"""
        print("[斷線重連模組] 等待官網頁面載入並搜尋特徵標籤...")
        template = _load_tag_template(self.rc.web_header_template)
        if template is None:
            print(f"[警告] 找不到特徵檔 '{self.rc.web_header_template}'，跳過網頁特徵檢測！")
            return True

        start_time = time.time()
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            while time.time() - start_time < timeout:
                screen_shot = np.array(sct.grab(monitor))
                screen_gray = cv2.cvtColor(screen_shot, cv2.COLOR_BGRA2GRAY)

                res = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)

                if max_val >= self.rc.web_header_threshold:
                    print(f"[斷線重連模組] 成功偵測到官網標籤特徵 (匹配度: {max_val:.2f})！")
                    return True

                time.sleep(0.5)

        print("[斷線重連模組] 警告：等待官網特徵逾時！將嘗試直接繼續執行。")
        return False

    def wait_for_game_server_page(self, timeout=90.0):
        """等待並驗證遊戲是否成功彈出並載入到「伺服器選擇」畫面"""
        print("[斷線重連模組] 等待遊戲視窗載入伺服器選擇畫面...")

        template = _load_tag_template(self.rc.server_select_template)
        if template is None:
            print(f"[警告] 找不到伺服器畫面特徵檔 '{self.rc.server_select_template}'，改用視窗標題判斷。")
            return True

        start_time = time.time()
        with mss.mss() as sct:
            while time.time() - start_time < timeout:
                # Chrome 有時會在等待過程中自己跳回最上層(例如頁面導向、彈出提示),
                # 縮小的話它還在,可能又被拉回前景形成縮小/彈出的循環,乾脆直接關閉它
                chrome_win = self._get_chrome_window()
                if chrome_win is not None:
                    try:
                        chrome_win.close()
                    except Exception:
                        pass

                game_win = get_game_window(title=self.rc.game_window_title, activate=True)
                if game_win:
                    monitor = {
                        "left": game_win.left,
                        "top": game_win.top,
                        "width": game_win.width,
                        "height": game_win.height
                    }
                    game_img = np.array(sct.grab(monitor))
                    game_gray = cv2.cvtColor(game_img, cv2.COLOR_BGRA2GRAY)

                    res = cv2.matchTemplate(game_gray, template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(res)

                    if max_val >= self.rc.server_select_threshold:
                        print(f"[斷線重連模組] 成功檢測到伺服器選擇畫面 (匹配度: {max_val:.2f})！")
                        return True

                time.sleep(0.5)

        print("[斷線重連模組] 警告：等待伺服器選擇畫面逾時，嘗試直接繼續執行。")
        return False

    # ---------------- 主流程 ----------------

    def _run_reconnect_once(self):
        """
        執行一輪完整的重連流程。任何一步失敗會提早回傳 False,
        由外層 handle_reconnect 決定要不要重試。
        """
        self.force_close_game()

        chrome_win = self._get_chrome_window(activate=True)
        if chrome_win is None:
            print("[斷線重連模組] 找不到 Chrome 視窗,嘗試啟動瀏覽器...")
            try:
                subprocess.Popen(["cmd", "/c", "start", "chrome", self.rc.reconnect_url])
            except Exception as e:
                print(f"[斷線重連模組] 啟動 Chrome 失敗: {e}")
            time.sleep(3.0)
            chrome_win = self._get_chrome_window(activate=True)

        if chrome_win is None:
            print("[斷線重連模組] 錯誤: 仍找不到 Chrome 視窗,本輪嘗試失敗")
            return False

        time.sleep(1.0)
        self.wait_for_web_feature(timeout=10.0)

        # Step 2: 點擊右側的「下載遊戲 / 啟動遊戲」按鈕
        self._click_ratio(chrome_win, self.rc.launch_game_btn_ratio, "launch_game")
        time.sleep(2.5)

        # Step 3: 點擊 "Sign in with gamapass"
        chrome_win = self._get_chrome_window() or chrome_win
        self._click_ratio(chrome_win, self.rc.gamapass_btn_ratio, "gamapass_login")
        time.sleep(3.5)
        
        # Step 4: 點選帳號
        chrome_win = self._get_chrome_window() or chrome_win
        self._click_ratio(chrome_win, self.rc.account_entry_ratio, "account_entry")
        time.sleep(3.5)

        # Step 5: 點擊帳號並按 "繼續"
        chrome_win = self._get_chrome_window() or chrome_win
        self._click_ratio(chrome_win, self.rc.bagel_char_ratio, "select_bagel")
        time.sleep(1.5)
        self._click_ratio(chrome_win, self.rc.continue_btn_ratio, "continue_btn")
        time.sleep(15.0)

        # Chrome 這裡的操作已經全部做完,之後只需要讀遊戲視窗的畫面,
        chrome_win = self._get_chrome_window() or chrome_win
        if chrome_win is not None:
            try:
                chrome_win.close()
            except Exception as e:
                print(f"[斷線重連模組] 關閉 Chrome 視窗失敗: {e}")

        # 輪詢直到伺服器選擇畫面真的出現為止
        if not self.wait_for_game_server_page(timeout=90.0):
            print("[斷線重連模組] 逾時仍未看到伺服器選擇畫面,本輪嘗試失敗")
            return False

        game_win = get_game_window(title=self.rc.game_window_title, activate=True)
        if game_win is None:
            print("[斷線重連模組] 錯誤: 找不到遊戲視窗,本輪嘗試失敗")
            return False
        
        time.sleep(5.0)

        # 點擊 "1 雪吉拉" 伺服器
        self._click_ratio(game_win, self.rc.server_name_ratio, "server_name")
        time.sleep(2.0)


        # 展開頻道選單,捲動到最下面
        self._click_ratio(game_win, self.rc.channel_scrollbar_ratio, "scroll_to_bottom")
        time.sleep(2.0)

        # 點擊頻道區域,再用方向鍵微調到 ch.57
        self._click_ratio(game_win, self.rc.channel_area_ratio_ch57, "channel_area")
        time.sleep(0.5)
        pydirectinput.press('down')
        time.sleep(0.5)
        pydirectinput.press('down')
        time.sleep(0.5)
        pydirectinput.press('down')
        time.sleep(2.0)
        pydirectinput.press('down')
        time.sleep(2.0)
        '''

        # 點擊頻道 ch.3
        self._click_ratio(game_win, self.rc.channel_area_ratio_ch3, "channel_area")
        time.sleep(2.0)
        '''

        # 進入伺服器
        self._click_ratio(game_win, self.rc.channel_enter_ratio, "channel_area")
        time.sleep(5.0)

        '''
        # 進入伺服器
        pydirectinput.press('enter')
        time.sleep(2.0)
        '''

        # 進入遊戲(選角色)
        pydirectinput.press('enter')
        time.sleep(5.0)

        return True

    def handle_reconnect(self):
        """
        對外的重連入口。內部最多重試 max_reconnect_attempts 次,
        每次都包在 try/except 裡,避免任何一步的例外把整個 bot 拖垮。
        回傳 True/False 代表這次重連是否成功。
        """
        print("[斷線重連模組] 偵測到斷線！開始執行重新連線流程...")

        for attempt in range(1, self.rc.max_reconnect_attempts + 1):
            print(f"[斷線重連模組] === 第 {attempt}/{self.rc.max_reconnect_attempts} 次嘗試 ===")
            try:
                if self._run_reconnect_once():
                    print("[斷線重連模組] 重新連線完成，準備回歸主腳本！")
                    return True
            except Exception as e:
                print(f"[斷線重連模組] 第 {attempt} 次嘗試發生未預期例外: {e}")

            if attempt < self.rc.max_reconnect_attempts:
                print(f"[斷線重連模組] {self.rc.retry_backoff_seconds:.0f} 秒後重試...")
                time.sleep(self.rc.retry_backoff_seconds)

        print("[斷線重連模組] 已達最大重試次數，重新連線失敗！請人工介入檢查畫面狀態。")
        return False


if __name__ == "__main__":
    """要用系統管理員權限啟動 IDE 才能正確觸發 DirectInput 按鍵"""
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.05

    cfg = BotConfig()
    rc_cfg = ReconnectConfig()

    # 這張地圖的繩索都在平台左側,爬到頂端容易卡在邊緣被怪物撞下去,爬繩時額外持續按右鍵往內側移動
    cfg.climb_drift_key = 'right'


    cfg.layers = [
        # index=0: 最下層平台
        LayerConfig(index=0, y_min=168, y_max=173, left_bound=40, right_bound=110),
    
        # index=1: 中間層平台
        LayerConfig(index=1, y_min=150, y_max=154, left_bound=55, right_bound=80),
    
        # index=2: 上層平台
        LayerConfig(index=2, y_min=127, y_max=131, left_bound=42, right_bound=80),
    
    ]
    
    cfg.ropes = [
        # 右下平台(0) <-> 中間層平台(1) 的繩索
        RopeConfig(x=52, lower_layer=0, upper_layer=1),
    
        # 中間層平台(1) <-> 上層平台(2) 的繩索
        RopeConfig(x=56, lower_layer=1, upper_layer=2),
    
    ]



    # 初始化重連模組
    reconnector = ReconnectManager(rc_cfg)

    # 迴圈間狀態
    move_direction = "left"
    timed_key_task = TimedKeyTrigger(key='n', interval_seconds=120)
    rope_traverser = RopeTraverser(cfg)
    patrol_lap_tracker = PatrolLapTracker()
    stuck_watchdog = StuckWatchdog(cfg)
    layer_sweep_director = LayerSweepDirector()
    tick_count = 0

    # 上次偵測到的角色/補師位置,用來做局部搜尋加速
    last_player_pos: Optional[Tuple[int, int]] = None
    last_healer_pos: Optional[Tuple[int, int]] = None

    # 連續重連失敗次數: 超過門檻就不再自動重試,避免無限狂點
    consecutive_reconnect_failures = 0
    max_consecutive_reconnect_failures = 3

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

            # 斷線檢測模組 (含 LIE DETECTOR 防外掛檢測視窗:偵測到就直接強制關閉遊戲重新連線,
            # 不嘗試自動配合檢測,避免處理不了而卡在檢測畫面被判定為外掛)
            lie_detector_open = cfg.enable_reconnect and reconnector.is_lie_detector_open(game_img)
            if lie_detector_open:
                print("[斷線重連模組] 偵測到 LIE DETECTOR 防外掛檢測視窗,強制關閉遊戲並重新連線...")
                # 診斷用: 存一張當下畫面,方便觀察比對是否正常觸發(player_target_pos 此時可能還沒算出來,先用預設值)
                save_debug_snapshot(win, game_img, (0, 0), cfg, path=f"debug_lie_detector_{tick_count}.png")

            if cfg.enable_reconnect and (lie_detector_open or reconnector.is_disconnected(game_img)):
                keyup_all()
                success = reconnector.handle_reconnect()

                # 重連後畫面完全不同,舊的局部搜尋座標一定不能再用,強制回到全螢幕搜尋
                last_player_pos = None
                last_healer_pos = None

                if success:
                    consecutive_reconnect_failures = 0
                else:
                    consecutive_reconnect_failures += 1
                    if consecutive_reconnect_failures >= max_consecutive_reconnect_failures:
                        print(f"[主程式] 連續 {consecutive_reconnect_failures} 次重連失敗,"
                              f"停止自動重連,請人工檢查狀況！")
                        break
                continue

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
                pydirectinput.press('alt')
                time.sleep(cfg.main_loop_sleep)
                continue

            last_player_pos = player_target_pos
            px, py = player_target_pos

            # 補師位置 (找不到時為 None,屬正常情況,不中止流程;關閉補師跟隨模組時直接略過搜尋)
            healer_target_pos = None
            if cfg.enable_healer_follow:
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

            current_layer = find_layer_by_y(abs_mm_y, cfg.layers) if cfg.layers else None

            print(f"玩家位置: {player_target_pos}, 補師位置: {healer_target_pos}, "
                  f"小地圖座標: ({abs_mm_x}, {abs_mm_y}), 怪物數: {len(monster_positions)}, "
                  f"平台: {current_layer.index if current_layer else '無'}")

            if cfg.layers:
                if current_layer is None:
                    print(f"警告: 小地圖 Y={abs_mm_y} 沒有對應的 layers 設定,暫用預設邊界巡邏")
            else:
                # 木面@@@ (未設定 cfg.layers 時,維持原本的單層邊界切換寫法)
                if abs_mm_y >= 150:
                    cfg.minimap_right_bound = 100
                    cfg.minimap_left_bound = 40

            if cfg.debug:
                debug_img = build_debug_image_from_cfg(win, game_img, player_target_pos, cfg)
                if cfg.debug_show_window:
                    show_debug_window(debug_img)
                if cfg.debug_save_image:
                    save_debug_image(debug_img)
                #time.sleep(cfg.main_loop_sleep)
                continue

            # 卡住偵測模組(安全網):RopeTraverser 沒有主動在爬繩時,若座標長時間沒變化
            # 且確認掛在繩子上,由它接管按住爬繩鍵嘗試脫困,本 tick 不執行其他判斷
            if not rope_traverser.active and \
                    stuck_watchdog.update(abs_mm_x, abs_mm_y, game_img_gray, player_target_pos):
                continue

            # 跨平台爬繩模組:若正在爬繩/掉落中,由它接管本次移動判斷
            handled_by_rope = rope_traverser.step(
                abs_mm_x, abs_mm_y, cfg.layers,
                player_screen_gray=game_img_gray, player_pos=player_target_pos
            )

            if not handled_by_rope:
                cooldown_ok = (time.time() - rope_traverser.last_transition_time) >= cfg.post_transition_cooldown
                bounce_count = patrol_lap_tracker.update(current_layer, abs_mm_x)

                if current_layer is not None and cooldown_ok and \
                        bounce_count >= cfg.min_patrol_bounces_before_climb:
                    # 用 LayerSweepDirector 決定這次該往上還是往下,讓角色完整掃過所有平台,
                    direction = layer_sweep_director.decide(current_layer.index, cfg.ropes)
                    skip_align = (direction == "down")

                    if direction == "down":
                        # 往下掉落不需要對齊繩索 X 座標,平台上任何位置都能觸發
                        rope = find_rope_down_from_layer(current_layer.index, cfg.ropes)
                    else:
                        # 往上爬需要先對齊繩索的確切 X 座標
                        rope = find_rope_near_x(abs_mm_x, current_layer.index, cfg.ropes, cfg.rope_x_tolerance)
                        if rope is not None and rope.lower_layer != current_layer.index:
                            rope = None  # find_rope_near_x 也會配對到下層繩索,這裡只要「往上」的

                    if rope is not None and rope_traverser.can_start(rope):
                        target_layer_index = rope.upper_layer if direction == "up" else rope.lower_layer

                        target_occupied = False
                        if cfg.detect_other_players:
                            other_positions = get_other_player_minimap_positions(game_img, win)
                            teammate_positions = get_teammate_minimap_positions(game_img, win)
                            if other_positions or teammate_positions:
                                print(f"[跨平台爬繩模組] 偵測位置 - 其他玩家:{other_positions}, 隊友:{teammate_positions}")
                                #save_debug_snapshot(win, game_img, player_target_pos, cfg, path=f"debug_game_screen{tick_count}.png")

                            occupied_layers = find_occupied_layers(
                                other_positions + teammate_positions, cfg.layers
                            )
                            target_occupied = target_layer_index in occupied_layers

                        if target_occupied:
                            print(f"[跨平台爬繩模組] 目標平台 {target_layer_index} 偵測到其他玩家/隊友,暫緩換層")
                        else:
                            keyup_all()
                            rope_traverser.start(rope, direction, skip_align=skip_align)
                            patrol_lap_tracker.reset()
                            handled_by_rope = True

                if not handled_by_rope:
                    left_bound = current_layer.left_bound if current_layer else None
                    right_bound = current_layer.right_bound if current_layer else None

                    # 移動目標判斷:同層有補師 -> 靠攏補師，否則維持邊界折返
                    new_direction = decide_move_target(
                        player_target_pos, healer_target_pos, abs_mm_x, move_direction, cfg,
                        left_bound=left_bound, right_bound=right_bound
                    )

                    keyup_all()
                    if new_direction is None:
                        # 已到達補師附近,停止左右移動
                        pass
                    else:
                        move_direction = new_direction
                        pydirectinput.keyDown(move_direction, _pause=False)

            # 爬繩/掉落過程中無法攻擊,只有在一般巡邏移動時才判斷攻擊
            if not rope_traverser.active:
                # 找出範圍內所有怪物,依數量決定範圍攻擊或單體攻擊
                monsters_in_range = find_monsters_in_range(
                    monster_positions, player_target_pos, cfg.attack_distance_threshold
                )
                handle_attack(monsters_in_range, player_target_pos, move_direction, cfg)

            #time.sleep(cfg.main_loop_sleep)