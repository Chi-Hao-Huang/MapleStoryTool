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

# ---------------------------------------------------------
# tool
# ---------------------------------------------------------

def activate_window(win):
    hwnd = win._hWnd
    ctypes.windll.user32.ShowWindow(hwnd, 5)
    ctypes.windll.user32.SetForegroundWindow(hwnd)

def get_game_window():
    wins = gw.getWindowsWithTitle('新楓之谷')
    if not wins: 
        return None
    win = wins[0]
    activate_window(win)
    return win

def keyup_all(keys=['left', 'right']):
    for key in keys:
        pydirectinput.keyUp(key)

# ---------------------------------------------------------
# HP / SP 判斷模組
# ---------------------------------------------------------

def get_hp_mp_region(win):
    """
    這個像素位置可能每台電腦都不一樣
    要看 save_full_debug_image 存出來的圖調整
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

def calculate_bar_percentage(img_bgra, color_check_fn):
    """根據傳入的截圖圖像計算百分比"""
    mid_row = img_bgra[img_bgra.shape[0] // 2]
    filled = 0
    for px in mid_row:
        b, g, r, a = px
        if color_check_fn(r, g, b):
            filled += 1

    return (filled / len(mid_row)) * 100

    return filled / len(mid_row) * 100

def is_hp_color(r, g, b):
    return r > 150 and g < 100 and b < 100

def is_mp_color(r, g, b):
    return b > 150 and r < 100



# ---------------------------------------------------------
# 角色位置辨識模組
# ---------------------------------------------------------

def find_player_by_tag(game_screen_bgra, tag_template_path='image/player_tag.png', threshold=0.55):
    """
    透過角色名字標籤或勛章來定位角色位置
    """
    tag_template = cv2.imread(tag_template_path, cv2.IMREAD_COLOR)
    if tag_template is None:
        print(f"錯誤: 找不到標籤圖片 {tag_template_path}")
        return None

    th, tw = tag_template.shape[:2]
    screen_bgr = cv2.cvtColor(game_screen_bgra, cv2.COLOR_BGRA2BGR)
    
    # 避開底部約 100 像素的 UI 儀表板區域
    h, w = screen_bgr.shape[:2]
    roi_bgr = screen_bgr[0:h - 100, :]

    # 模板匹配
    res = cv2.matchTemplate(roi_bgr, tag_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= threshold:
        # 取得標籤中心位置
        tag_x = max_loc[0] + tw // 2
        tag_y = max_loc[1] + th // 2
        
        # 名字標籤在角色下方約 40 像素處
        player_x = tag_x
        player_y = tag_y - 40  
        
        return (player_x, player_y)
    
    return None

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

def find_monsters(game_screen_bgra, template_paths, threshold=0.5):
    """
    搜尋多種怪物位置，回傳怪物中心點座標列表 [(x1, y1), ...]
    :param template_paths: 圖片路徑列表，例如 ['image/mo1.png', 'image/mo2.png'] 或單一字串
    """
    # 相容性處理：若傳入單一字串路徑，自動轉為列表
    if isinstance(template_paths, str):
        template_paths = [template_paths]

    screen_bgr = cv2.cvtColor(game_screen_bgra, cv2.COLOR_BGRA2BGR)
    rects = []

    # 1. 遍歷每一個怪物模板路徑
    for path in template_paths:
        try:
            pil_img = Image.open(path).convert("RGB")
            template_rgb = np.array(pil_img)
            template_left = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2BGR)
        except Exception as e:
            print(f"警告: 載入怪物圖片失敗 {path}, 錯誤原因: {e}")
            continue

        # 產生水平翻轉模板 (向右)
        template_right = cv2.flip(template_left, 1)
        th, tw = template_left.shape[:2]

        # 2. 分別比對向左與向右兩種方向
        for template in [template_left, template_right]:
            res = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res >= threshold)
            for pt in zip(*loc[::-1]):
                x, y = int(pt[0]), int(pt[1])
                # 將該模板的 (tw, th) 隨座標一併記錄
                rects.append([x, y, x + tw, y + th, tw, th])

    if not rects:
        return []

    rects_np = np.array(rects)
    # 取出前 4 欄 (x1, y1, x2, y2) 丟入 NMS 計算
    boxes_for_nms = rects_np[:, :4]
    
    # 3. 使用自訂 NMS 過濾跨模板、跨方向的重疊框
    pick_indices = non_max_suppression_fast_indices(boxes_for_nms, overlap_thresh=0.3)

    # 4. 依據 NMS 留下的框計算各自的中心點
    monsters = []
    for idx in pick_indices:
        x1, y1, x2, y2, tw, th = rects_np[idx]
        monsters.append((int(x1 + tw // 2), int(y1 + th // 2)))

    return monsters

# ---------------------------------------------------------
# 定時移動模組
# ---------------------------------------------------------

def periodic_move(last_move_time, interval_seconds=300, player_target_pos=(0,0), move_center=687, debug=False):
    """達到時間間隔時觸發移動，移動完畢回傳當前時間與新產生的隨機間隔時間"""
    current_time = time.time()
    if current_time - last_move_time >= interval_seconds:
        px, _ = player_target_pos
        #move_time = random.randint(10, 15) / 300

        direction = "left" if px >= move_center else "right"
        print(
            f"執行 {interval_seconds} 秒定時移動, 向{direction}移動 1 步"
        )

        '''
        pydirectinput.keyDown(direction)
        time.sleep(move_time)
        pydirectinput.keyUp(direction)
        '''
        pydirectinput.press(direction)

        # 重新生成下一次的隨機移動間隔
        
        if debug: 
            new_next_interval = 1
        
        else:
            new_next_interval = random.randint(200, 250)

        return current_time, new_next_interval

    return last_move_time, interval_seconds

# ---------------------------------------------------------
# 定時技能動模組
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
            print(f"[定時技能模組] 達到 {self.interval} 秒間隔，觸發按鍵: 魔心防禦({self.key})")
            time.sleep(.5)
            #keyup_all()
            pydirectinput.press(self.key)
            self.last_triggered_time = current_time
            return True
        return False


# ---------------------------------------------------------
# 小地圖偵測模組
# ---------------------------------------------------------

def get_minimap_region(win):
    """
    小地圖在遊戲視窗左上角
    """
    return {
        "left": win.left + 10,
        "top": win.top + 100,
        "width": 120,
        "height": 100
    }

def find_player_on_minimap(game_img, minimap_region, win):
    """判斷小地圖中玩家位置，並回傳黃點相對座標"""
    
    # 裁切小地圖 ROI
    x1 = minimap_region["left"] - win.left
    y1 = minimap_region["top"] - win.top
    x2 = x1 + minimap_region["width"]
    y2 = y1 + minimap_region["height"]
    
    minimap_crop = game_img[y1:y2, x1:x2]
    
    # 轉為 BGR 後再轉 HSV
    bgr = cv2.cvtColor(minimap_crop, cv2.COLOR_BGRA2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    
    # 對應 RGB(255, 255, 136) -> HSV 值的精準上下界
    lower_yellow = np.array([25, 100, 200])
    upper_yellow = np.array([35, 255, 255])
    
    mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
    
    # 尋找目標輪廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if contours:
        # 加上「圓點面積大小過濾」(小地圖黃點面積極小，通常在 1 ~ 20 像素之間)
        valid_contours = [c for c in contours if 1 <= cv2.contourArea(c) <= 25]
        
        if valid_contours:
            c = max(valid_contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                return (cx, cy)
            
    return None



# ---------------------------------------------------------
# debug 模組
# ---------------------------------------------------------

def save_full_debug_image(win, 
                          template_path='image/mo_00065.png', 
                          threshold=0.5, 
                          attack_radius=300,
                          player_target_pos=(0,0)):
    """擷取全螢幕，繪製除錯圖層並儲存"""
    hp_region, mp_region = get_hp_mp_region(win)
    minimap_region = get_minimap_region(win) # 取得小地圖區域
    
    game_region = {
        "left": win.left,
        "top": win.top,
        "width": win.width,
        "height": win.height,
    }
    
    with mss.MSS() as sct:
        screen = np.array(sct.grab(game_region))
        debug_img = cv2.cvtColor(screen, cv2.COLOR_BGRA2BGR)

        # 畫出 HP / MP 區域
        for region, label, color in [
            (hp_region, "HP Bar", (0, 0, 255)),
            (mp_region, "MP Bar", (255, 0, 0)),
        ]:
            x1, y1 = region["left"] - win.left, region["top"] - win.top
            x2, y2 = x1 + region["width"], y1 + region["height"]
            cv2.rectangle(debug_img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                debug_img,
                label,
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
            )

        # 畫出小地圖區域與黃點偵測位置
        mm_x1 = minimap_region["left"] - win.left
        mm_y1 = minimap_region["top"] - win.top
        mm_x2 = mm_x1 + minimap_region["width"]
        mm_y2 = mm_y1 + minimap_region["height"]
        
        # 畫出小地圖範圍框 (亮青色)
        cv2.rectangle(debug_img, (mm_x1, mm_y1), (mm_x2, mm_y2), (255, 255, 0), 2)
        cv2.putText(
            debug_img,
            "Minimap ROI",
            (mm_x1, mm_y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 0),
            1,
        )

        # 進行小地圖黃點偵測
        minimap_pos = find_player_on_minimap(screen, minimap_region, win)
        if minimap_pos:
            # 轉換為視窗內的絕對座標 (小地圖左上角 + 相對座標)
            abs_mm_x = mm_x1 + minimap_pos[0]
            abs_mm_y = mm_y1 + minimap_pos[1]
            
            # 在黃點位置畫標記
            cv2.circle(debug_img, (abs_mm_x, abs_mm_y), 7, (255, 255, 0), 1)
            cv2.putText(
                debug_img,
                f"Minimap Player ({abs_mm_x},{abs_mm_y})",
                (mm_x2 + 10, mm_y1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 0),
                1,
            )
        else:
            cv2.putText(
                debug_img,
                "Minimap Player: NOT FOUND",
                (mm_x2 + 10, mm_y1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
            )

        # 畫出角色與攻擊範圍
        player_x, player_y = player_target_pos
        cv2.circle(
            debug_img, (player_x, player_y), attack_radius, (0, 255, 255), 1
        )
        cv2.circle(debug_img, (player_x, player_y), 6, (0, 255, 255), -1)
        cv2.putText(
            debug_img,
            f"Player ({player_x},{player_y})",
            (player_x + 10, player_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
        )

        # 畫出怪物位置
        monsters = find_monsters(screen, template_path, threshold=threshold)
        for idx, (mx, my) in enumerate(monsters):
            dist = np.hypot(mx - player_x, my - player_y)
            color = (255, 0, 255) if dist <= attack_radius else (0, 255, 0)
            cv2.circle(debug_img, (mx, my), 8, color, -1)
            cv2.putText(
                debug_img,
                f"Monster #{idx+1} ({dist:.0f}px)",
                (mx + 10, my),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
            )

        cv2.imwrite("debug_game_screen.png", debug_img)
        print("debug_game_screen.png saved.")


if __name__ == "__main__":
    """要用系統管理員權限啟動 IDE 才能正確觸發 DirectInput 按鍵"""
    # DirectInput 全局按鍵時間間隔
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.05
    
    #
    DEBUG = 0
    
    # OpenCV 匹配相似度門檻 (0~1)
    monsters_threshold = 0.4
    
    # 怪物圖片檔名
    template_paths = [
    'image/mo_00065.png',
    'image/mo_00059.png',
    ]
    
    # 攻擊警戒距離範圍
    attack_distance_threshold = 250
    
    # 紀錄上次移動時間，初始化為當前時間
    last_move_time = time.time()
    
    
    # 定期移動的時間間隔
    if DEBUG:
        current_move_interval = 1
    else:
        current_move_interval = random.randint(200, 250)
        
    # 定期移動中心點
    #move_center = 687
    move_center = 764
    
    # 初始化移動方向
    move_direction = "left"
    
    # 主迴圈執行間隔
    main_loop_sleep = .05
    
    # 實例化定時任務物件
    timed_key_task = TimedKeyTrigger(key='n', interval_seconds=120)
    
    with mss.MSS() as sct:
        while True:
            win = get_game_window()
            if not win:
                print("找不到遊戲視窗")
                time.sleep(1)
                continue

            game_region = {
                "left": win.left,
                "top": win.top,
                "width": win.width,
                "height": win.height,
            }

            game_img = np.array(sct.grab(game_region))
            
            print("="*50)
            
            
            # 定時按鍵觸發
            timed_key_task.update()
            
            pydirectinput.press('z')
            
            # 裁切出 HP/MP 進行辨識
            '''
            hp_region, mp_region = get_hp_mp_region(win)
            hp_crop = game_img[
                hp_region["top"]
                - win.top : hp_region["top"]
                - win.top
                + hp_region["height"],
                hp_region["left"]
                - win.left : hp_region["left"]
                - win.left
                + hp_region["width"],
            ]
            mp_crop = game_img[
                mp_region["top"]
                - win.top : mp_region["top"]
                - win.top
                + mp_region["height"],
                mp_region["left"]
                - win.left : mp_region["left"]
                - win.left
                + mp_region["width"],
            ]

            hp_percent = calculate_bar_percentage(hp_crop, is_hp_color)
            mp_percent = calculate_bar_percentage(mp_crop, is_mp_color)

            if hp_percent < 10:
                pydirectinput.press('a')
                time.sleep(0.2)

            if mp_percent < 10:
                print("警告: MP < 20%! 坐下休息")
                time.sleep(5)
                pydirectinput.press('x')
                time.sleep(5)
                pydirectinput.press('x')
            print(f"HP: {hp_percent:.1f}%  MP: {mp_percent:.1f}%")
            '''

            # 搜尋怪物與角色
            monster_positions = find_monsters(
                game_img, 
                template_paths, 
                threshold=monsters_threshold
            )
            player_target_pos = find_player_by_tag(game_img)

            # 若未找尋到角色標籤則重新判斷
            if player_target_pos is None:
                print("警告: 未偵測到玩家位置，重新判斷")
                keyup_all()
                time.sleep(main_loop_sleep)
                continue

            px, py = player_target_pos
            pos_str = ", ".join(f"({x}, {y})" for x, y in monster_positions)
            
            # 取得黃點小地圖座標
            minimap_region = get_minimap_region(win)
            minimap_pos = find_player_on_minimap(game_img, minimap_region, win)
            
            if not minimap_pos:
                print("警告: 小地圖未偵測到玩家位置，重新判斷")
                keyup_all()
                time.sleep(main_loop_sleep)
                continue
            
            mm_x1 = minimap_region["left"] - win.left
            mm_y1 = minimap_region["top"] - win.top
            
            abs_mm_x = mm_x1 + minimap_pos[0]
            abs_mm_y = mm_y1 + minimap_pos[1]
            
            print(f"玩家位置: {player_target_pos}, 小地圖座標: ({abs_mm_x}, {abs_mm_y}) , 怪物數: {len(monster_positions)}")


            # 抵達或低於左界，強制向右折返
            if abs_mm_x <= 60:
                move_direction = "right"
                print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 {move_direction}")
            # 抵達或高於右界，強制向左折返
            elif abs_mm_x >= 65 :   
                move_direction = "left"
                print(f"[跑圖模組] 達到邊界 (X={abs_mm_x})，移動方向為 {move_direction}")

                
            keyup_all()
            pydirectinput.keyDown(move_direction)

            

            # 定時移動
            '''
            last_move_time, current_move_interval = periodic_move(
                last_move_time,
                interval_seconds=current_move_interval,
                player_target_pos=player_target_pos,
                move_center=move_center,
                debug=DEBUG
            )
            '''

            # 判斷怪物距離與攻擊
            for mx, my in monster_positions:
                distance = np.hypot(mx - px, my - py)
                if distance <= attack_distance_threshold and px > mx and move_direction == "left":
                    print(
                        f"[怪物偵測模組] 怪物接近！距離: 左側 {distance:.1f}，攻擊觸發"
                    )
                    #keyup_all()
                    pydirectinput.press("shift")
                    time.sleep(main_loop_sleep)
                    break
                
                elif distance <= attack_distance_threshold and px > mx and move_direction == "right":
                    print(
                        f"[怪物偵測模組] 怪物接近！距離: 左側 {distance:.1f}，攻擊觸發"
                    )
                    keyup_all()
                    pydirectinput.press("left")
                    pydirectinput.press("shift")
                    pydirectinput.keyDown(move_direction)
                    time.sleep(main_loop_sleep)
                    break

                elif distance <= attack_distance_threshold and px < mx and move_direction == "left":
                    print(
                        f"[怪物偵測模組] 怪物接近！距離: 右側 {distance:.1f}，攻擊觸發"
                    )
                    keyup_all()
                    pydirectinput.press("right")
                    pydirectinput.press("shift")
                    pydirectinput.keyDown(move_direction)
                    time.sleep(main_loop_sleep)
                    break

                elif distance <= attack_distance_threshold and px < mx and move_direction == "right":
                    print(
                        f"[怪物偵測模組] 怪物接近！距離: 右側 {distance:.1f}，攻擊觸發"
                    )
                    #keyup_all()
                    pydirectinput.press("shift")
                    pydirectinput.keyDown(move_direction)
                    time.sleep(main_loop_sleep) 
                    break

            # Debug 功能
            if DEBUG:
                save_full_debug_image(
                    win,
                    attack_radius=attack_distance_threshold,
                    template_path=template_paths,
                    threshold=monsters_threshold,
                    player_target_pos=player_target_pos,
                )

            time.sleep(main_loop_sleep)