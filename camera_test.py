import cv2
import mediapipe as mp
import numpy as np
from collections import deque
import time
import json
import sys
import os

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CALIBRATION_TIME = 3
WALK_TIME = 8
EMA_ALPHA = 0.15
FINAL_ATAXIA_THRESHOLD = 1.5

# ─── MEDIAPIPE SETUP ──────────────────────────────────────────────────────────
mp_pose = mp.solutions.pose
mp_draw = mp.solutions.drawing_utils
mp_styles = mp.solutions.drawing_styles

# ─── DATA BUFFERS ─────────────────────────────────────────────────────────────
nose_x        = deque(maxlen=120)
nose_y        = deque(maxlen=120)
center_x      = deque(maxlen=120)
wrist_mid_x   = deque(maxlen=120)
step_time     = deque(maxlen=120)
ankle_width   = deque(maxlen=120)
knee_angle_hist = deque(maxlen=120)

prev_left_ankle_y = None
ema_score = 0.0
walk_scores = []

baseline = {
    "sway_lr": 0.0,
    "sway_ud": 0.0,
    "trunk": 0.0,
    "wide_base": 0.0,
    "knee_inst": 0.0
}

state = "CALIBRATION"
start_time = time.time()

# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────
def safe_variance(data):
    return np.var(data) if len(data) > 20 else 0.0

def tremor_energy(data):
    if len(data) < 20:
        return 0.0
    return np.mean(np.abs(np.diff(data)))

def angle(a, b, c):
    a, b, c = np.array(a), np.array(b), np.array(c)
    ba = a - b
    bc = c - b
    cosang = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-6)
    return np.degrees(np.arccos(np.clip(cosang, -1, 1)))

def step_irregularity(current_y, prev_y, history):
    if prev_y is None:
        return current_y
    history.append(abs(current_y - prev_y))
    return current_y

def draw_rounded_rect(img, pt1, pt2, color, alpha=0.6, radius=12):
    overlay = img.copy()
    x1, y1 = pt1
    x2, y2 = pt2
    cv2.rectangle(overlay, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(overlay, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    for cx, cy in [(x1+radius, y1+radius), (x2-radius, y1+radius),
                   (x1+radius, y2-radius), (x2-radius, y2-radius)]:
        cv2.circle(overlay, (cx, cy), radius, color, -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)

def draw_score_bar(frame, score, threshold, x, y, w, h):
    """Draw a visual score bar."""
    cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 50, 50), -1)
    ratio = min(score / (threshold * 2), 1.0)
    fill_w = int(w * ratio)
    color = (0, 200, 80) if score < threshold * 0.6 else \
            (0, 200, 255) if score < threshold else (0, 60, 255)
    if fill_w > 0:
        cv2.rectangle(frame, (x, y), (x + fill_w, y + h), color, -1)
    # threshold marker
    tx = x + int(w * (threshold / (threshold * 2)))
    cv2.line(frame, (tx, y - 4), (tx, y + h + 4), (255, 255, 255), 2)
    cv2.rectangle(frame, (x, y), (x + w, y + h), (100, 100, 100), 1)

def draw_ui(frame, state, elapsed, ema_score, raw_score=0):
    h, w = frame.shape[:2]

    # ── top banner ──────────────────────────────────────────────────────────
    draw_rounded_rect(frame, (10, 10), (w - 10, 70), (15, 15, 25), alpha=0.75)

    if state == "CALIBRATION":
        remaining = max(0, CALIBRATION_TIME - elapsed)
        banner_text = f"  CALIBRATING — Stand Still   {remaining:.1f}s"
        banner_color = (0, 220, 220)
        # animated progress dots
        dots = "." * (int(elapsed * 3) % 4)
        cv2.putText(frame, dots, (w - 60, 48),
                    cv2.FONT_HERSHEY_DUPLEX, 1.0, banner_color, 2)

    elif state == "WALK":
        remaining = max(0, WALK_TIME - elapsed)
        banner_text = f"  WALK FORWARD — Stay in frame   {remaining:.1f}s"
        banner_color = (0, 230, 110)

    else:
        banner_text = "  ANALYSIS COMPLETE"
        banner_color = (200, 200, 255)

    cv2.putText(frame, banner_text, (20, 48),
                cv2.FONT_HERSHEY_DUPLEX, 0.75, banner_color, 2)

    # ── bottom HUD (only during WALK) ───────────────────────────────────────
    if state == "WALK":
        hud_y = h - 90
        draw_rounded_rect(frame, (10, hud_y), (w - 10, h - 10),
                          (10, 10, 20), alpha=0.75)

        cv2.putText(frame, "INSTABILITY SCORE", (20, hud_y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 200), 1)
        draw_score_bar(frame, ema_score, FINAL_ATAXIA_THRESHOLD,
                       20, hud_y + 30, w - 40, 18)

        cv2.putText(frame, f"EMA: {ema_score:.3f}   Raw: {raw_score:.3f}   Threshold: {FINAL_ATAXIA_THRESHOLD}",
                    (20, hud_y + 68),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # ── landmark legend ─────────────────────────────────────────────────────
    if state in ("CALIBRATION", "WALK"):
        legend_items = [
            ((0, 220, 110),  "Shoulder/Hip center"),
            ((255, 140,   0), "Knee angle tracking"),
            ((0, 160, 255),  "Ankle step width"),
        ]
        lx, ly = w - 220, 85
        draw_rounded_rect(frame, (lx - 8, ly - 8),
                          (w - 10, ly + len(legend_items) * 22 + 4),
                          (15, 15, 25), alpha=0.65)
        for i, (c, label) in enumerate(legend_items):
            cv2.circle(frame, (lx + 6, ly + i * 22 + 6), 5, c, -1)
            cv2.putText(frame, label, (lx + 16, ly + i * 22 + 11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

def draw_final_result(frame, avg_score):
    h, w = frame.shape[:2]
    ataxia = avg_score > FINAL_ATAXIA_THRESHOLD

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, h),
                  (0, 0, 40) if ataxia else (0, 30, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    draw_rounded_rect(frame, (w//2 - 300, h//2 - 110),
                      (w//2 + 300, h//2 + 110),
                      (20, 20, 50), alpha=0.9)

    if ataxia:
        title  = "ATAXIA INDICATORS DETECTED"
        sub    = "Abnormal gait pattern observed"
        t_col  = (80, 80, 255)
        s_col  = (150, 150, 255)
    else:
        title  = "NORMAL GAIT DETECTED"
        sub    = "No significant ataxia indicators"
        t_col  = (60, 230, 120)
        s_col  = (120, 230, 160)

    tw = cv2.getTextSize(title, cv2.FONT_HERSHEY_DUPLEX, 0.9, 2)[0][0]
    cv2.putText(frame, title, (w//2 - tw//2, h//2 - 40),
                cv2.FONT_HERSHEY_DUPLEX, 0.9, t_col, 2)

    sw = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)[0][0]
    cv2.putText(frame, sub, (w//2 - sw//2, h//2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, s_col, 1)

    score_txt = f"Average Instability Score: {avg_score:.3f}  |  Threshold: {FINAL_ATAXIA_THRESHOLD}"
    stw = cv2.getTextSize(score_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
    cv2.putText(frame, score_txt, (w//2 - stw//2, h//2 + 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

    cv2.putText(frame, "Window closes in 5 seconds...", (w//2 - 130, h//2 + 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 120, 120), 1)

    return ataxia, avg_score

# ─── SAVE RESULT (for Streamlit to read back) ─────────────────────────────────
def save_result(ataxia: bool, avg_score: float):
    result = {
        "ataxia": ataxia,
        "avg_score": round(float(avg_score), 4),
        "threshold": FINAL_ATAXIA_THRESHOLD,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f)
    print(json.dumps(result))   # also print so Streamlit subprocess can capture it

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    global prev_left_ankle_y, ema_score, state, start_time

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Cannot open webcam", file=sys.stderr)
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)

    cv2.namedWindow("AtaxiaGuard — Walk Test", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("AtaxiaGuard — Walk Test", 1280, 720)

    with mp_pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        smooth_landmarks=True,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.7
    ) as pose:

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame   = cv2.flip(frame, 1)
            rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result  = pose.process(rgb)
            elapsed = time.time() - start_time

            raw_score = 0.0

            if result.pose_landmarks:
                lm = result.pose_landmarks.landmark

                # ── draw skeleton with custom colours ───────────────────────
                custom_style = mp_draw.DrawingSpec(color=(0,220,110), thickness=2, circle_radius=3)
                conn_style   = mp_draw.DrawingSpec(color=(0,160,255), thickness=2)
                mp_draw.draw_landmarks(
                    frame, result.pose_landmarks,
                    mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=custom_style,
                    connection_drawing_spec=conn_style
                )

                # ── highlight key joints ─────────────────────────────────────
                key_joints = {
                    11: (0,220,110), 12: (0,220,110),   # shoulders
                    23: (0,220,110), 24: (0,220,110),   # hips
                    25: (255,140,0), 26: (255,140,0),   # knees
                    27: (0,160,255), 28: (0,160,255),   # ankles
                    0:  (255,255,255)                   # nose
                }
                h_px, w_px = frame.shape[:2]
                for idx, col in key_joints.items():
                    lmk = lm[idx]
                    cx, cy = int(lmk.x * w_px), int(lmk.y * h_px)
                    cv2.circle(frame, (cx, cy), 7,  col, -1)
                    cv2.circle(frame, (cx, cy), 9,  (255,255,255), 1)

                # ── feature extraction ───────────────────────────────────────
                nose_x.append(lm[0].x)
                nose_y.append(lm[0].y)
                center_x.append((lm[11].x + lm[12].x) / 2)
                wrist_mid_x.append((lm[15].x + lm[16].x) / 2)

                prev_left_ankle_y = step_irregularity(
                    lm[27].y, prev_left_ankle_y, step_time)

                ankle_width.append(abs(lm[27].x - lm[28].x))

                ka = angle(
                    (lm[23].x, lm[23].y),
                    (lm[25].x, lm[25].y),
                    (lm[27].x, lm[27].y)
                )
                knee_angle_hist.append(ka)

                sway_lr       = safe_variance(nose_x)
                sway_ud       = safe_variance(nose_y)
                trunk         = safe_variance(center_x)
                gait          = safe_variance(step_time)
                tremor        = tremor_energy(wrist_mid_x)
                wide_base     = safe_variance(ankle_width)
                knee_instability = safe_variance(knee_angle_hist)

                # ── state machine ────────────────────────────────────────────
                if state == "CALIBRATION":
                    if elapsed > CALIBRATION_TIME:
                        baseline["sway_lr"]   = sway_lr   / 0.05
                        baseline["sway_ud"]   = sway_ud   / 0.05
                        baseline["trunk"]     = trunk     / 0.05
                        baseline["wide_base"] = wide_base / 0.05
                        baseline["knee_inst"] = knee_instability / 30.0
                        walk_scores.clear()
                        ema_score = 0.0
                        state     = "WALK"
                        start_time = time.time()

                elif state == "WALK":
                    sway_lr_score   = max(0, sway_lr   / 0.05 - baseline["sway_lr"])
                    sway_ud_score   = max(0, sway_ud   / 0.05 - baseline["sway_ud"])
                    trunk_score     = max(0, trunk     / 0.05 - baseline["trunk"])
                    wide_base_score = max(0, wide_base / 0.05 - baseline["wide_base"])
                    knee_inst_score = max(0, knee_instability / 30.0 - baseline["knee_inst"])

                    raw_score = (
                        sway_lr_score   * 1.0 +
                        sway_ud_score   * 1.0 +
                        trunk_score     * 1.0 +
                        gait            * 1.0 +
                        tremor          * 0.5 +
                        wide_base_score * 1.0 +
                        knee_inst_score * 1.0
                    )
                    ema_score = EMA_ALPHA * raw_score + (1 - EMA_ALPHA) * ema_score
                    walk_scores.append(ema_score)

                    if elapsed > WALK_TIME:
                        state = "FINAL"

            # ── draw UI ───────────────────────────────────────────────────────
            if state != "FINAL":
                draw_ui(frame, state, elapsed, ema_score, raw_score)
            else:
                avg_score        = float(np.mean(walk_scores)) if walk_scores else 0.0
                ataxia, avg_sc   = draw_final_result(frame, avg_score)

                cv2.imshow("AtaxiaGuard — Walk Test", frame)
                cv2.waitKey(5000)

                save_result(ataxia, avg_sc)
                break

            cv2.imshow("AtaxiaGuard — Walk Test", frame)
            if cv2.waitKey(1) & 0xFF == 27:   # ESC to quit early
                break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()