import cv2
import mediapipe as mp
import numpy as np
import json
import time
import copy
import os
from collections import deque

# ── MediaPipe setup ────────────────────────────────────────────────────────────
mp_pose    = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
mp_styles  = mp.solutions.drawing_styles

# ── Constants ──────────────────────────────────────────────────────────────────
WINDOW_SIZE       = 60
TEST_DURATION     = 20
WARMUP_FRAMES     = 20
FULL_BODY_TIMEOUT = 30

# Set DEBUG=1 in environment to write raw signal values to debug_signals.txt
# Usage:  DEBUG=1 python ataxia_analyser.py
DEBUG = os.environ.get("DEBUG", "0") == "1"

REQUIRED_LANDMARKS   = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
VISIBILITY_THRESHOLD = 0.55

# ── Severity thresholds ────────────────────────────────────────────────────────
# Normal ≥ 7.5 | Mild 5.5–7.5 | Moderate 3.5–5.5 | Severe < 3.5
SEVERITY_THRESHOLDS = {
    "Normal":   (7.5, 10.0),
    "Mild":     (5.5,  7.5),
    "Moderate": (3.5,  5.5),
    "Severe":   (0.0,  3.5),
}

# ── Penalty ceiling table ──────────────────────────────────────────────────────
# Each value is the signal level at which the penalty reaches its FULL weight.
# Below this value → proportional (partial) penalty.
# Above this value → penalty clipped at maximum weight.
#
# Calibrated so a healthy walker's signals stay at ~25–40% of each ceiling
# (meaning low penalty and an overall score ≥ 7.5).  A severe-ataxia walker's
# signals should exceed most ceilings, driving the score below 3.5.
#
# HOW TO RE-TUNE if still wrong:
#   Run:  DEBUG=1 python ataxia_analyser.py
#   Walk normally for 20 s, press Q.
#   Open debug_signals.txt and look at column means.
#   Set each ceiling ≈ 2.5× the normal-walker mean so that:
#     normal  → ~40% penalty fraction → small score deduction
#     severe  → >100% fraction        → full score deduction
CEILINGS = {
    # BALANCE
    "sway":     0.006,   # normalised trunk-x variance / bh²   (normal ≈ 0.0005–0.002)
    "sh_tilt":  0.040,   # mean shoulder height diff / bh       (normal ≈ 0.008–0.018)
    "hip_tilt": 0.035,   # mean hip height diff / bh            (normal ≈ 0.005–0.015)
    "lean":     0.060,   # mean lateral mid-sh vs mid-hip / bh  (normal ≈ 0.010–0.025)
    # GAIT
    "jerk":     0.012,   # std(hip vertical velocity)           (normal ≈ 0.003–0.007)
    "sw_var":   0.003,   # variance of ankle separation / bh    (normal ≈ 0.0002–0.001)
    # COORDINATION
    "elb_var":  0.015,   # variance of elbow angle asym/180     (normal ≈ 0.002–0.007)
    "kn_var":   0.012,   # variance of knee angle asym/180      (normal ≈ 0.002–0.006)
    "wri_mean": 0.10,    # mean wrist height diff / bh          (normal ≈ 0.04–0.09)
    "rot_mean": 0.25,    # mean trunk rotation / 90°            (normal ≈ 0.05–0.12)
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def lm_xy(landmarks, idx):
    lm = landmarks[idx]
    return np.array([lm.x, lm.y])

def lm_xyz(landmarks, idx):
    lm = landmarks[idx]
    return np.array([lm.x, lm.y, lm.z])

def angle_between(a, b, c):
    ba = a - b
    bc = c - b
    cos_a = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-9)
    return np.degrees(np.arccos(np.clip(cos_a, -1.0, 1.0)))

def body_height(lm):
    nose    = lm_xy(lm, 0)
    l_ank   = lm_xy(lm, 27)
    r_ank   = lm_xy(lm, 28)
    mid_ank = (l_ank + r_ank) / 2
    return max(abs(nose[1] - mid_ank[1]), 0.15)

def check_full_body(landmarks):
    lm      = landmarks.landmark
    missing = sum(1 for idx in REQUIRED_LANDMARKS
                  if lm[idx].visibility < VISIBILITY_THRESHOLD)
    return missing == 0, missing

def pen(signal_val, ceiling):
    """Linear penalty fraction: 0.0 at signal=0, 1.0 at signal=ceiling."""
    return float(np.clip(signal_val / ceiling, 0.0, 1.0))


# ── Core Analyser ─────────────────────────────────────────────────────────────
class AtaxiaAnalyser:

    def __init__(self):
        self.trunk_x_buf   = deque(maxlen=WINDOW_SIZE)
        self.sh_tilt_buf   = deque(maxlen=WINDOW_SIZE)
        self.hip_tilt_buf  = deque(maxlen=WINDOW_SIZE)
        self.lean_buf      = deque(maxlen=WINDOW_SIZE)

        self.hip_y_buf     = deque(maxlen=WINDOW_SIZE)
        self.hip_vel_buf   = deque(maxlen=WINDOW_SIZE)
        self.ankle_sep_buf = deque(maxlen=WINDOW_SIZE)

        self.elb_asym_buf  = deque(maxlen=WINDOW_SIZE)
        self.kn_asym_buf   = deque(maxlen=WINDOW_SIZE)
        self.wri_asym_buf  = deque(maxlen=WINDOW_SIZE)
        self.trunk_rot_buf = deque(maxlen=WINDOW_SIZE)

        self.balance_scores = deque(maxlen=WINDOW_SIZE * 2)
        self.gait_scores    = deque(maxlen=WINDOW_SIZE * 2)
        self.coord_scores   = deque(maxlen=WINDOW_SIZE * 2)

        self.prev_hip_y  = None
        self.prev_time   = None
        self.frame_count = 0

        self._dbg = None
        if DEBUG:
            self._dbg = open("debug_signals.txt", "w")
            self._dbg.write(
                "frame\tsway_var\tsh_tilt\thip_tilt\tlean\t"
                "jerk\tsw_var\telb_var\tkn_var\twri_mean\trot_mean\t"
                "bal\tgait\tcoord\n"
            )

    def analyse(self, landmarks):
        self.frame_count += 1
        if self.frame_count < WARMUP_FRAMES:
            return

        lm = landmarks.landmark
        bh = body_height(lm)

        l_sh   = lm_xy(lm, 11);  r_sh   = lm_xy(lm, 12)
        l_hip  = lm_xy(lm, 23);  r_hip  = lm_xy(lm, 24)
        l_ank  = lm_xy(lm, 27);  r_ank  = lm_xy(lm, 28)
        l_wri3 = lm_xyz(lm, 15); r_wri3 = lm_xyz(lm, 16)
        l_elb3 = lm_xyz(lm, 13); r_elb3 = lm_xyz(lm, 14)
        l_sh3  = lm_xyz(lm, 11); r_sh3  = lm_xyz(lm, 12)
        l_hp3  = lm_xyz(lm, 23); r_hp3  = lm_xyz(lm, 24)
        l_kn3  = lm_xyz(lm, 25); r_kn3  = lm_xyz(lm, 26)
        l_an3  = lm_xyz(lm, 27); r_an3  = lm_xyz(lm, 28)

        mid_sh  = (l_sh  + r_sh)  / 2
        mid_hip = (l_hip + r_hip) / 2

        # ══════════════════════════════════════════════════════════════════════
        # BALANCE  — sway 4 pts + sh_tilt 1.5 + hip_tilt 1.5 + lean 3 = 10
        # ══════════════════════════════════════════════════════════════════════
        self.trunk_x_buf.append(mid_sh[0])
        self.sh_tilt_buf.append(abs(l_sh[1]  - r_sh[1])  / bh)
        self.hip_tilt_buf.append(abs(l_hip[1] - r_hip[1]) / bh)
        self.lean_buf.append(abs(mid_sh[0] - mid_hip[0]) / bh)

        bal_score    = None
        sway_var_log = sh_tilt_log = hip_tilt_log = lean_log = 0.0

        if len(self.trunk_x_buf) >= 10:
            sway_var_log  = float(np.var(self.trunk_x_buf)) / (bh ** 2)
            sh_tilt_log   = float(np.mean(self.sh_tilt_buf))
            hip_tilt_log  = float(np.mean(self.hip_tilt_buf))
            lean_log      = float(np.mean(self.lean_buf))

            sway_pen  = pen(sway_var_log, CEILINGS["sway"])     * 4.0
            sh_pen    = pen(sh_tilt_log,  CEILINGS["sh_tilt"])  * 1.5
            hip_pen   = pen(hip_tilt_log, CEILINGS["hip_tilt"]) * 1.5
            lean_pen  = pen(lean_log,     CEILINGS["lean"])     * 3.0

            bal_score = max(0.0, 10.0 - sway_pen - sh_pen - hip_pen - lean_pen)
            self.balance_scores.append(bal_score)

        # ══════════════════════════════════════════════════════════════════════
        # GAIT  — freq 3 + jerk 3 + width 4 = 10
        # ══════════════════════════════════════════════════════════════════════
        hip_y_now  = mid_hip[1]
        now        = time.time()
        step_width = abs(l_ank[0] - r_ank[0]) / bh
        self.ankle_sep_buf.append(step_width)

        gait_score = None
        jerk_log   = sw_var_log = 0.0

        if self.prev_hip_y is not None and self.prev_time is not None:
            dt      = now - self.prev_time
            hip_vel = abs(hip_y_now - self.prev_hip_y) / (dt + 1e-9)
            self.hip_y_buf.append(hip_y_now)
            self.hip_vel_buf.append(hip_vel)

            if len(self.hip_y_buf) >= 15:
                y_arr    = np.array(self.hip_y_buf)
                dy       = np.diff(y_arr)
                sign_chg = int(np.sum(np.diff(np.sign(dy)) != 0))

                if sign_chg < 2:
                    freq_pen = float(np.clip((2 - sign_chg) / 2.0, 0, 1)) * 3.0
                elif sign_chg > 10:
                    freq_pen = float(np.clip((sign_chg - 10) / 8.0, 0, 1)) * 3.0
                else:
                    freq_pen = 0.0

                jerk_log = float(np.std(np.array(self.hip_vel_buf)))
                jerk_pen = pen(jerk_log, CEILINGS["jerk"]) * 3.0

                if len(self.ankle_sep_buf) >= 10:
                    sw_var_log = float(np.var(self.ankle_sep_buf))
                    width_pen  = pen(sw_var_log, CEILINGS["sw_var"]) * 4.0
                else:
                    width_pen = 0.0

                gait_score = max(0.0, 10.0 - freq_pen - jerk_pen - width_pen)
                self.gait_scores.append(gait_score)

        self.prev_hip_y = hip_y_now
        self.prev_time  = now

        # ══════════════════════════════════════════════════════════════════════
        # COORDINATION  — elb 2.5 + kn 2.5 + wri 2.5 + rot 2.5 = 10
        # ══════════════════════════════════════════════════════════════════════
        l_elb_ang = angle_between(l_sh3, l_elb3, l_wri3)
        r_elb_ang = angle_between(r_sh3, r_elb3, r_wri3)
        self.elb_asym_buf.append(abs(l_elb_ang - r_elb_ang) / 180.0)

        l_kn_ang = angle_between(l_hp3, l_kn3, l_an3)
        r_kn_ang = angle_between(r_hp3, r_kn3, r_an3)
        self.kn_asym_buf.append(abs(l_kn_ang - r_kn_ang) / 180.0)

        self.wri_asym_buf.append(abs(l_wri3[1] - r_wri3[1]) / bh)

        sh_vec = r_sh3[:2] - l_sh3[:2]
        hp_vec = r_hp3[:2] - l_hp3[:2]
        sh_ang = np.degrees(np.arctan2(sh_vec[1], sh_vec[0]))
        hp_ang = np.degrees(np.arctan2(hp_vec[1], hp_vec[0]))
        rot    = abs(sh_ang - hp_ang)
        if rot > 90:
            rot = 180 - rot
        self.trunk_rot_buf.append(rot / 90.0)

        coord_score  = None
        elb_var_log  = kn_var_log = wri_mean_log = rot_mean_log = 0.0

        if len(self.elb_asym_buf) >= 10:
            elb_var_log  = float(np.var(self.elb_asym_buf))
            kn_var_log   = float(np.var(self.kn_asym_buf))
            wri_mean_log = float(np.mean(self.wri_asym_buf))
            rot_mean_log = float(np.mean(self.trunk_rot_buf))

            elb_pen  = pen(elb_var_log,  CEILINGS["elb_var"])  * 2.5
            kn_pen   = pen(kn_var_log,   CEILINGS["kn_var"])   * 2.5
            wri_pen  = pen(wri_mean_log, CEILINGS["wri_mean"]) * 2.5
            rot_pen  = pen(rot_mean_log, CEILINGS["rot_mean"]) * 2.5

            coord_score = max(0.0, 10.0 - elb_pen - kn_pen - wri_pen - rot_pen)
            self.coord_scores.append(coord_score)

        # ── Debug logging ──────────────────────────────────────────────────────
        if DEBUG and self._dbg and bal_score is not None:
            self._dbg.write(
                f"{self.frame_count}\t"
                f"{sway_var_log:.6f}\t{sh_tilt_log:.4f}\t{hip_tilt_log:.4f}\t{lean_log:.4f}\t"
                f"{jerk_log:.6f}\t{sw_var_log:.6f}\t"
                f"{elb_var_log:.6f}\t{kn_var_log:.6f}\t{wri_mean_log:.4f}\t{rot_mean_log:.4f}\t"
                f"{bal_score:.2f}\t"
                f"{gait_score if gait_score is not None else 'n/a'}\t"
                f"{coord_score if coord_score is not None else 'n/a'}\n"
            )
            self._dbg.flush()

    @property
    def has_enough_data(self):
        return (len(self.balance_scores) >= 8 and
                len(self.gait_scores)    >= 8 and
                len(self.coord_scores)   >= 8)

    @property
    def scores(self):
        g = round(float(np.mean(self.gait_scores))    if self.gait_scores    else 5.0, 2)
        b = round(float(np.mean(self.balance_scores)) if self.balance_scores else 5.0, 2)
        c = round(float(np.mean(self.coord_scores))   if self.coord_scores   else 5.0, 2)
        overall  = round(min(10.0, g * 0.30 + b * 0.40 + c * 0.30), 2)
        severity = "Severe"
        for sev, (lo, hi) in SEVERITY_THRESHOLDS.items():
            if lo <= overall <= hi:
                severity = sev
                break
        return {
            "gait_score":         g,
            "balance_score":      b,
            "coordination_score": c,
            "overall_score":      overall,
            "severity":           severity,
            "notes":              "BlazePose real-time analysis"
        }

    def close(self):
        if self._dbg:
            self._dbg.close()
            self._dbg = None


# ── HUD overlay ────────────────────────────────────────────────────────────────
def draw_hud(frame, scores, elapsed, total):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (10, 10), (330, 210), (8, 8, 24), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    font = cv2.FONT_HERSHEY_DUPLEX
    cv2.putText(frame, "ATAXIA GUARD", (20, 42), font, 0.72, (0, 210, 255), 2)

    bars = [
        ("Gait",    scores["gait_score"],         (0, 210, 255)),
        ("Balance", scores["balance_score"],       (140, 80, 200)),
        ("Coord",   scores["coordination_score"],  (255, 100, 160)),
    ]
    for i, (label, val, color) in enumerate(bars):
        y     = 75 + i * 42
        bar_w = int((val / 10.0) * 190)
        cv2.putText(frame, f"{label}: {val:.1f}", (20, y), font, 0.55, (180, 200, 230), 1)
        cv2.rectangle(frame, (120, y - 15), (315, y - 4), (35, 35, 55), -1)
        if bar_w > 0:
            cv2.rectangle(frame, (120, y - 15), (120 + bar_w, y - 4), color, -1)

    sev       = scores["severity"]
    sev_color = {"Normal": (80, 220, 80), "Mild": (0, 210, 255),
                 "Moderate": (0, 140, 255), "Severe": (60, 60, 255)}.get(sev, (200, 200, 200))
    remaining = max(0, total - elapsed)
    cv2.putText(frame, f"Time: {remaining:.0f}s  |  {sev}", (20, 195), font, 0.55, sev_color, 1)

    prog = min(1.0, elapsed / total)
    cv2.rectangle(frame, (10, h - 22), (w - 10, h - 10), (35, 35, 55), -1)
    cv2.rectangle(frame, (10, h - 22), (10 + int((w - 20) * prog), h - 10), (0, 210, 255), -1)


def draw_fullbody_prompt(frame, elapsed_waiting, timeout):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

    font   = cv2.FONT_HERSHEY_DUPLEX
    font_b = cv2.FONT_HERSHEY_SIMPLEX

    bx1, by1 = w // 2 - 290, h // 2 - 105
    bx2, by2 = w // 2 + 290, h // 2 + 115
    cv2.rectangle(frame, (bx1, by1), (bx2, by2), (16, 16, 40), -1)
    cv2.rectangle(frame, (bx1, by1), (bx2, by2), (0, 180, 255), 2)

    cv2.putText(frame, "FULL BODY REQUIRED",
                (bx1 + 30, by1 + 48), font, 0.88, (0, 210, 255), 2)
    cv2.putText(frame, "Please step back so your entire",
                (bx1 + 30, by1 + 83), font_b, 0.62, (200, 220, 255), 1)
    cv2.putText(frame, "body is visible in the frame.",
                (bx1 + 30, by1 + 108), font_b, 0.62, (200, 220, 255), 1)
    cv2.putText(frame, "Head to feet must be in view.",
                (bx1 + 30, by1 + 133), font_b, 0.55, (150, 170, 200), 1)

    remaining = max(0, timeout - elapsed_waiting)
    cv2.putText(frame, f"Timeout in: {remaining:.0f}s",
                (bx1 + 30, by1 + 168), font_b, 0.6, (255, 160, 60), 1)
    prog    = 1.0 - min(1.0, elapsed_waiting / timeout)
    bar_end = bx1 + 10 + int((bx2 - bx1 - 20) * prog)
    color   = (0, 200, 100) if prog > 0.4 else (0, 120, 255) if prog > 0.2 else (0, 60, 220)
    cv2.rectangle(frame, (bx1 + 10, by1 + 178), (bx2 - 10, by1 + 193), (35, 35, 55), -1)
    cv2.rectangle(frame, (bx1 + 10, by1 + 178), (bar_end, by1 + 193), color, -1)


def _draw_landmarks_mirrored(display_frame, pose_landmarks):
    mirrored = copy.deepcopy(pose_landmarks)
    for lm in mirrored.landmark:
        lm.x = 1.0 - lm.x
    mp_drawing.draw_landmarks(
        display_frame, mirrored, mp_pose.POSE_CONNECTIONS,
        landmark_drawing_spec=mp_styles.get_default_pose_landmarks_style())


# ── Main ───────────────────────────────────────────────────────────────────────
def run_test():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        import random
        g = round(random.uniform(3, 8), 2)
        b = round(random.uniform(3, 8), 2)
        c = round(random.uniform(3, 8), 2)
        o = round(g * 0.3 + b * 0.4 + c * 0.3, 2)
        sev = ("Severe" if o < 3.5 else "Moderate" if o < 5.5
               else "Mild" if o < 7.5 else "Normal")
        print(json.dumps({
            "gait_score": g, "balance_score": b,
            "coordination_score": c, "overall_score": o,
            "severity": sev, "notes": "Simulated (no camera)"
        }))
        return

    analyser            = AtaxiaAnalyser()
    state               = "waiting_fullbody"
    fullbody_wait_start = time.time()
    scoring_start       = None

    with mp_pose.Pose(
        model_complexity=2,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
        smooth_landmarks=True,
        enable_segmentation=False,
    ) as pose:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = pose.process(rgb)
            rgb.flags.writeable = True

            display_frame = cv2.flip(frame, 1)
            now = time.time()

            if state == "waiting_fullbody":
                elapsed_wait = now - fullbody_wait_start
                if elapsed_wait >= FULL_BODY_TIMEOUT:
                    cap.release()
                    cv2.destroyAllWindows()
                    analyser.close()
                    print(json.dumps({
                        "insufficient_data": True,
                        "reason": "Full body not detected within 30 seconds",
                        "notes": "Please ensure your full body (head to feet) is visible"
                    }))
                    return

                if results.pose_landmarks:
                    _draw_landmarks_mirrored(display_frame, results.pose_landmarks)
                    full_body, missing = check_full_body(results.pose_landmarks)
                    if full_body:
                        state, scoring_start = "scoring", now
                    else:
                        draw_fullbody_prompt(display_frame, elapsed_wait, FULL_BODY_TIMEOUT)
                        cv2.putText(display_frame, f"Missing {missing} key points",
                                    (20, display_frame.shape[0] - 35),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 150, 255), 1)
                else:
                    draw_fullbody_prompt(display_frame, elapsed_wait, FULL_BODY_TIMEOUT)

            elif state == "scoring":
                elapsed_score = now - scoring_start
                if elapsed_score > TEST_DURATION:
                    break

                if results.pose_landmarks:
                    full_body, _ = check_full_body(results.pose_landmarks)
                    _draw_landmarks_mirrored(display_frame, results.pose_landmarks)
                    if full_body:
                        analyser.analyse(results.pose_landmarks)
                    else:
                        cv2.putText(display_frame, "Move back into frame!",
                                    (20, 55), cv2.FONT_HERSHEY_DUPLEX, 0.8, (0, 100, 255), 2)

                draw_hud(display_frame, analyser.scores, elapsed_score, TEST_DURATION)

            cv2.imshow("AtaxiaGuard — BlazePose Analysis  [Q to finish early]", display_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    cap.release()
    cv2.destroyAllWindows()
    analyser.close()

    if not analyser.has_enough_data:
        print(json.dumps({
            "insufficient_data": True,
            "reason": "Not enough frames captured for reliable analysis",
            "notes": "Ensure full body is visible and complete the full 20-second test"
        }))
    else:
        print(json.dumps(analyser.scores))


if __name__ == "__main__":
    run_test()