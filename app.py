import streamlit as st
import sqlite3
import hashlib
import os
import subprocess
import sys
import json
import io
from datetime import datetime

# ── DB setup ──────────────────────────────────────────────────────────────────
DB_PATH = "ataxia.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        test_date TEXT,
        gait_score REAL,
        balance_score REAL,
        coordination_score REAL,
        overall_score REAL,
        severity TEXT,
        notes TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.commit()
    conn.close()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def register_user(username, password, email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username,password,email,created_at) VALUES (?,?,?,?)",
                  (username, hash_password(password), email, datetime.now().isoformat()))
        conn.commit()
        return True, "Account created successfully!"
    except sqlite3.IntegrityError:
        return False, "Username already exists."
    finally:
        conn.close()

def login_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE username=? AND password=?",
              (username, hash_password(password)))
    row = c.fetchone()
    conn.close()
    return row


# ── PDF Report Generator ──────────────────────────────────────────────────────
def generate_pdf_report(username, row_data):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, HRFlowable)
    from reportlab.lib.enums import TA_CENTER

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    ACCENT = colors.HexColor("#00d4ff")
    PURPLE = colors.HexColor("#7b5ea7")
    WHITE  = colors.HexColor("#e8f0fe")
    GREY   = colors.HexColor("#8899aa")
    SEV_COLORS = {
        "Normal":   colors.HexColor("#50dc64"),
        "Mild":     colors.HexColor("#00d4ff"),
        "Moderate": colors.HexColor("#ffaa00"),
        "Severe":   colors.HexColor("#ff5050"),
    }
    sev       = row_data.get("severity", "Unknown")
    sev_color = SEV_COLORS.get(sev, ACCENT)

    title_style = ParagraphStyle("title", fontSize=26, textColor=ACCENT, alignment=TA_CENTER,
                                  spaceAfter=2, fontName="Helvetica-Bold", leading=32)
    sub_style   = ParagraphStyle("sub",   fontSize=10, textColor=GREY, alignment=TA_CENTER,
                                  spaceAfter=14, fontName="Helvetica")
    h2_style    = ParagraphStyle("h2",    fontSize=14, textColor=WHITE, fontName="Helvetica-Bold",
                                  spaceBefore=12, spaceAfter=6)
    body_style  = ParagraphStyle("body",  fontSize=10, textColor=GREY, fontName="Helvetica", leading=16)
    sev_style   = ParagraphStyle("sev",   fontSize=22, textColor=sev_color, alignment=TA_CENTER,
                                  fontName="Helvetica-Bold")
    note_style  = ParagraphStyle("note",  fontSize=9,  textColor=GREY, fontName="Helvetica-Oblique", leading=14)

    story = []
    story.append(Paragraph("AtaxiaGuard", title_style))
    story.append(Paragraph("NEUROLOGICAL MOVEMENT ANALYSIS REPORT", sub_style))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=12))

    test_date = row_data.get("test_date", "")
    try:
        date_str = datetime.fromisoformat(test_date).strftime("%d %B %Y  %H:%M")
    except Exception:
        date_str = test_date

    meta_data = [
        ["Patient", username, "Test Date", date_str],
        ["Report ID", f"AG-{abs(hash(test_date)) % 100000:05d}", "Analysis", "BlazePose CV"],
    ]
    meta_table = Table(meta_data, colWidths=[35*mm, 65*mm, 35*mm, 55*mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), colors.HexColor("#0a0e1a")),
        ("TEXTCOLOR",   (0,0), (0,-1), GREY), ("TEXTCOLOR", (2,0), (2,-1), GREY),
        ("TEXTCOLOR",   (1,0), (1,-1), WHITE), ("TEXTCOLOR", (3,0), (3,-1), WHITE),
        ("FONTNAME",    (0,0), (-1,-1), "Helvetica"),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"), ("FONTNAME", (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#0a0e1a"), colors.HexColor("#0d1220")]),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#1e2a40")),
        ("PADDING",     (0,0), (-1,-1), 8),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    sev_labels = {"Normal":"Normal Gait — All Clear","Mild":"Mild Ataxia Indicators",
                  "Moderate":"Moderate Ataxia Detected","Severe":"Severe Ataxia Detected"}
    story.append(Paragraph(sev_labels.get(sev, sev), sev_style))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Assessment Scores", h2_style))

    g = row_data.get("gait_score", 0)
    b = row_data.get("balance_score", 0)
    c = row_data.get("coordination_score", 0)
    o = row_data.get("overall_score", 0)

    def score_bar(val):
        filled = int((float(val) / 10.0) * 20)
        return "=" * filled + "-" * (20 - filled)

    score_data = [
        ["Metric", "Score", "Visual (0-10)", "Status"],
        ["Gait",         f"{g}/10", score_bar(g), "Good" if float(g)>=7.5 else "Low"],
        ["Balance",      f"{b}/10", score_bar(b), "Good" if float(b)>=7.5 else "Low"],
        ["Coordination", f"{c}/10", score_bar(c), "Good" if float(c)>=7.5 else "Low"],
        ["Overall",      f"{o}/10", score_bar(o), sev],
    ]
    score_table = Table(score_data, colWidths=[40*mm, 25*mm, 80*mm, 30*mm])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a2540")),
        ("TEXTCOLOR",  (0,0), (-1,0), ACCENT), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("BACKGROUND", (0,1), (-1,-1), colors.HexColor("#0a0e1a")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#0a0e1a"), colors.HexColor("#0d1220")]),
        ("TEXTCOLOR",  (0,1), (0,-1), WHITE), ("TEXTCOLOR", (1,1), (1,-1), ACCENT),
        ("TEXTCOLOR",  (2,1), (2,-1), GREY),  ("TEXTCOLOR", (3,1), (3,-1), sev_color),
        ("FONTNAME",   (3,-1), (3,-1), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#1e2a40")),
        ("PADDING",    (0,0), (-1,-1), 8),
        ("ALIGN",      (1,0), (1,-1), "CENTER"), ("ALIGN", (3,0), (3,-1), "CENTER"),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PURPLE, spaceAfter=10))

    if sev == "Normal":
        story.append(Paragraph("Clinical Interpretation", h2_style))
        story.append(Paragraph(
            "All movement parameters are within normal clinical ranges. "
            "Gait symmetry, balance stability, and limb coordination show no indicators "
            "of cerebellar or neuromotor impairment. No intervention required at this time.", body_style))
    else:
        food_map = {
            "Mild":     [("Blueberries","Daily — neuroprotective flavonoids"),("Avocado","Vitamin E, B6 and folate"),
                         ("Turmeric Milk","Curcumin anti-inflammatory"),("Fatty Fish","Omega-3 rich, 3x per week"),
                         ("Brazil Nuts","Selenium for cerebellar function")],
            "Moderate": [("CoQ10 Foods","Beef, sardines, organ meats"),("Mediterranean","Proven neurological benefits"),
                         ("Fortified Dairy","Vitamin D and B12"),("Lentils","Folate, magnesium"),
                         ("Olive Oil","Anti-inflammatory oleocanthal")],
            "Severe":   [("Neurologist","Professional supplementation guidance"),("Soft Foods","Easy-to-swallow"),
                         ("Nutritional Shakes","High-calorie if weight loss"),("Lean Protein","Every meal"),
                         ("Hydration","2L+ water daily minimum")]
        }
        exercise_map = {
            "Mild":     [("Single-Leg Stand","30 sec x 3 daily"),("Tai Chi","Proven cerebellar stimulation"),
                         ("Terrain Walking","Grass or sand surfaces"),("Resistance Bands","Upper and lower body"),
                         ("Gaze Stabilisation","Vestibular training")],
            "Moderate": [("Frenkel Exercises","Physio-guided coordination rehab"),("Chair Yoga","Seated stretches"),
                         ("Parallel Bar Walk","Supported gait training"),("Fine Motor Tasks","Clay, writing, threading"),
                         ("Hydrotherapy","Buoyancy reduces fall risk")],
            "Severe":   [("Supervised Physio","Inpatient essential"),("Bed Exercises","Ankle pumps and leg raises"),
                         ("Assistive Training","Mobility device training"),("Speech Therapy","Swallowing and speech rehab"),
                         ("Cognitive Exercises","Maintain neuroplasticity")]
        }
        story.append(Paragraph("Dietary Recommendations", h2_style))
        f_data  = [["Food Item","Benefit"]] + list(food_map.get(sev, food_map["Mild"]))
        f_table = Table(f_data, colWidths=[60*mm, 110*mm])
        f_table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a2540")),("TEXTCOLOR",(0,0),(-1,0),ACCENT),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#0a0e1a"),colors.HexColor("#0d1220")]),
            ("TEXTCOLOR",(0,1),(0,-1),WHITE),("TEXTCOLOR",(1,1),(1,-1),GREY),
            ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#1e2a40")),("FONTSIZE",(0,0),(-1,-1),9),("PADDING",(0,0),(-1,-1),7),
        ]))
        story.append(f_table)
        story.append(Spacer(1, 12))
        story.append(Paragraph("Exercise Plan", h2_style))
        e_data  = [["Exercise","Details"]] + list(exercise_map.get(sev, exercise_map["Mild"]))
        e_table = Table(e_data, colWidths=[60*mm, 110*mm])
        e_table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a2540")),("TEXTCOLOR",(0,0),(-1,0),PURPLE),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#0a0e1a"),colors.HexColor("#0d1220")]),
            ("TEXTCOLOR",(0,1),(0,-1),WHITE),("TEXTCOLOR",(1,1),(1,-1),GREY),
            ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#1e2a40")),("FONTSIZE",(0,0),(-1,-1),9),("PADDING",(0,0),(-1,-1),7),
        ]))
        story.append(e_table)

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY, spaceAfter=8))
    story.append(Paragraph(
        "This report is generated by AtaxiaGuard for informational purposes only. "
        "It does not constitute medical advice. Please consult a qualified neurologist "
        "for clinical diagnosis and treatment.", note_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="AtaxiaGuard", page_icon="🧠",
                   layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&display=swap');

*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; font-size: 16px; }

/* ── Background ── */
.stApp {
    background: #04060f;
    min-height: 100vh;
    position: relative;
    overflow-x: hidden;
}
.stApp::before {
    content: '';
    position: fixed;
    inset: 0;
    background:
        radial-gradient(ellipse 900px 600px at 10% 20%, rgba(6,182,212,0.09) 0%, transparent 70%),
        radial-gradient(ellipse 700px 500px at 90% 80%, rgba(139,92,246,0.09) 0%, transparent 70%),
        radial-gradient(ellipse 500px 400px at 50% 50%, rgba(20,184,166,0.05) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
    animation: bgPulse 8s ease-in-out infinite alternate;
}
@keyframes bgPulse { 0% { opacity:0.7; } 100% { opacity:1; } }
.stApp::after {
    content: '';
    position: fixed;
    width: 600px; height: 600px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(6,182,212,0.05) 0%, transparent 70%);
    top: -200px; right: -200px;
    animation: orbFloat 12s ease-in-out infinite;
    pointer-events: none;
    z-index: 0;
}
@keyframes orbFloat {
    0%, 100% { transform: translate(0,0) scale(1); }
    33% { transform: translate(-40px, 30px) scale(1.05); }
    66% { transform: translate(20px, -20px) scale(0.97); }
}
.stApp > * { position: relative; z-index: 1; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: rgba(4,6,15,0.95) !important;
    border-right: 1px solid rgba(6,182,212,0.15) !important;
    backdrop-filter: blur(32px) saturate(180%) !important;
}
[data-testid="stSidebar"]::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(6,182,212,0.5), transparent);
}
[data-testid="stSidebar"] .stRadio label {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    color: #7aa8c0 !important;
    padding: 0.4rem 0;
    transition: color 0.3s;
}
[data-testid="stSidebar"] .stRadio label:hover { color: #22d3ee !important; }

/* ── Hero ── */
.hero-eyebrow {
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    font-weight: 500;
    color: #22d3ee;
    letter-spacing: 4px;
    text-transform: uppercase;
    text-align: center;
    margin-bottom: 0.6rem;
    animation: fadeSlideUp 0.6s ease both;
}
.hero-title {
    font-family: 'Syne', sans-serif;
    font-size: clamp(2.6rem, 5vw, 3.8rem);
    font-weight: 800;
    background: linear-gradient(135deg, #e0f7ff 0%, #06b6d4 30%, #8b5cf6 65%, #ec4899 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    text-align: center;
    letter-spacing: -2px;
    line-height: 1.0;
    margin-bottom: 0.5rem;
    animation: fadeSlideUp 0.7s ease both;
    animation-delay: 0.1s;
}
.hero-sub {
    text-align: center;
    color: #7aa8c0;
    font-size: 0.85rem;
    font-weight: 500;
    font-family: 'DM Mono', monospace;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 2.8rem;
    animation: fadeSlideUp 0.8s ease both;
    animation-delay: 0.2s;
}
@keyframes fadeSlideUp { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }

/* ── Glass card ── */
.card {
    background: linear-gradient(135deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.02) 100%);
    border: 1px solid rgba(255,255,255,0.1);
    border-top: 1px solid rgba(255,255,255,0.16);
    border-radius: 20px;
    padding: 2rem;
    backdrop-filter: blur(20px);
    box-shadow: 0 4px 40px rgba(0,0,0,0.5), 0 1px 0 rgba(255,255,255,0.07) inset;
    margin-bottom: 1.5rem;
    transition: border-color 0.3s, box-shadow 0.3s;
    animation: cardReveal 0.5s ease both;
}
.card:hover {
    border-color: rgba(6,182,212,0.2);
    box-shadow: 0 8px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(6,182,212,0.08), 0 1px 0 rgba(255,255,255,0.09) inset;
}
@keyframes cardReveal { from { opacity:0; transform:translateY(16px); } to { opacity:1; transform:translateY(0); } }

/* ── Stat blocks ── */
.stat-block {
    text-align: center;
    padding: 1.5rem 1rem;
    background: rgba(6,182,212,0.05);
    border: 1px solid rgba(6,182,212,0.15);
    border-radius: 16px;
    transition: all 0.3s;
}
.stat-block:hover { background: rgba(6,182,212,0.1); border-color: rgba(6,182,212,0.3); transform: translateY(-3px); }
.stat-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: #7aa8c0;
    margin-bottom: 0.5rem;
}
.stat-value {
    font-family: 'Syne', sans-serif;
    font-size: 2.8rem;
    font-weight: 700;
    color: #22d3ee;
    line-height: 1;
    text-shadow: 0 0 30px rgba(6,182,212,0.5);
}
.stat-unit { font-family: 'DM Mono', monospace; font-size: 0.75rem; color: #4a6a7a; margin-top: 0.3rem; }

/* ── Badges ── */
.badge {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 6px 16px;
    border-radius: 100px;
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}
.badge-dot { width: 6px; height: 6px; border-radius: 50%; animation: pulseDot 2s ease-in-out infinite; }
@keyframes pulseDot { 0%,100% { opacity:1; transform:scale(1); } 50% { opacity:0.5; transform:scale(0.7); } }
.badge-mild     { background: rgba(6,182,212,0.12);  color: #22d3ee; border: 1px solid rgba(6,182,212,0.35); }
.badge-mild .badge-dot     { background: #22d3ee; }
.badge-moderate { background: rgba(245,158,11,0.12); color: #fbbf24; border: 1px solid rgba(245,158,11,0.35); }
.badge-moderate .badge-dot { background: #fbbf24; }
.badge-severe   { background: rgba(239,68,68,0.12);  color: #f87171; border: 1px solid rgba(239,68,68,0.35); }
.badge-severe .badge-dot   { background: #f87171; }
.badge-normal   { background: rgba(16,185,129,0.12); color: #34d399; border: 1px solid rgba(16,185,129,0.35); }
.badge-normal .badge-dot   { background: #34d399; }

/* ── Score grid ── */
.score-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-top: 1.2rem; }
.score-item {
    position: relative;
    padding: 1.5rem 1rem;
    border-radius: 16px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    text-align: center;
    overflow: hidden;
    transition: transform 0.3s;
}
.score-item:hover { transform: scale(1.04); }
.score-item::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    border-radius: 0 0 16px 16px;
}
.score-item.gait::after    { background: linear-gradient(90deg, transparent, #22d3ee, transparent); }
.score-item.balance::after { background: linear-gradient(90deg, transparent, #a78bfa, transparent); }
.score-item.coord::after   { background: linear-gradient(90deg, transparent, #f472b6, transparent); }
.score-item.overall::after { background: linear-gradient(90deg, transparent, #34d399, transparent); }
.score-label { font-family:'DM Mono',monospace; font-size:0.75rem; letter-spacing:2px; text-transform:uppercase; color:#7aa8c0; margin-bottom:0.6rem; }
.score-value { font-family:'Syne',sans-serif; font-size:2.8rem; font-weight:700; line-height:1; }

/* ── Rec cards ── */
.rec-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 1.3rem 1rem;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    position: relative;
    overflow: hidden;
}
.rec-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(6,182,212,0.4), transparent);
    opacity: 0;
    transition: opacity 0.3s;
}
.rec-card:hover { transform: translateY(-6px); border-color: rgba(6,182,212,0.25); background: rgba(6,182,212,0.05); box-shadow: 0 12px 40px rgba(6,182,212,0.12); }
.rec-card:hover::before { opacity: 1; }
.rec-card-icon  { font-size: 1.9rem; margin-bottom: 0.6rem; display: block; }
.rec-card-title { font-family:'Syne',sans-serif; font-size:0.9rem; font-weight:700; color:#ddeeff; margin-bottom:0.4rem; }
.rec-card-desc  { font-size:0.8rem; color:#5a7a8a; line-height:1.55; font-family:'DM Sans',sans-serif; }

/* ── Step cards ── */
.step-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }
.step-card {
    position: relative;
    padding: 1.6rem 1.2rem;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    transition: all 0.3s;
}
.step-card:hover { background: rgba(6,182,212,0.05); border-color: rgba(6,182,212,0.2); transform: translateY(-4px); }
.step-number { font-family:'DM Mono',monospace; font-size:0.78rem; color:#22d3ee; letter-spacing:1px; margin-bottom:0.8rem; display:block; }
.step-title  { font-family:'Syne',sans-serif; font-size:1rem; font-weight:700; color:#ddeeff; margin-bottom:0.5rem; }
.step-desc   { font-size:0.85rem; color:#5a7a8a; line-height:1.6; }

/* ── Feature cards ── */
.feature-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 0.5rem; }
.feature-card {
    position: relative;
    overflow: hidden;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    padding: 1.8rem;
    transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.feature-card::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 30% 50%, rgba(6,182,212,0.07), transparent 70%);
    opacity: 0;
    transition: opacity 0.3s;
}
.feature-card:hover { border-color: rgba(6,182,212,0.25); transform: translateY(-5px); box-shadow: 0 16px 48px rgba(0,0,0,0.4); }
.feature-card:hover::before { opacity: 1; }
.feature-tag   { font-family:'DM Mono',monospace; font-size:0.72rem; letter-spacing:2.5px; text-transform:uppercase; color:#22d3ee; margin-bottom:0.7rem; }
.feature-title { font-family:'Syne',sans-serif; font-size:1.1rem; font-weight:700; color:#ddeeff; margin-bottom:0.6rem; }
.feature-desc  { font-size:0.88rem; color:#5a7a8a; line-height:1.65; }

/* ── Inputs ── */
div[data-testid="stForm"] { background: transparent !important; border: none !important; }
.stTextInput > label {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.78rem !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    color: #7aa8c0 !important;
    font-weight: 500 !important;
}
.stTextInput > div > div > input {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 12px !important;
    color: #ddeeff !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 1rem !important;
    padding: 0.75rem 1rem !important;
    transition: all 0.3s !important;
}
.stTextInput > div > div > input:focus {
    border-color: rgba(6,182,212,0.5) !important;
    box-shadow: 0 0 0 3px rgba(6,182,212,0.08) !important;
    background: rgba(6,182,212,0.04) !important;
}
.stTextInput > div > div > input::placeholder { color: #2a4050 !important; }

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, #0891b2 0%, #7c3aed 100%) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 12px !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    letter-spacing: 1px !important;
    padding: 0.75rem 2rem !important;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
    width: 100% !important;
    text-transform: uppercase !important;
}
.stButton > button:hover { transform: translateY(-3px) scale(1.01) !important; box-shadow: 0 8px 30px rgba(8,145,178,0.4) !important; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(255,255,255,0.03) !important;
    border-radius: 14px !important;
    padding: 5px !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] {
    color: #4a6a7a !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.92rem !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
    border-radius: 10px !important;
    padding: 0.5rem 1.5rem !important;
}
.stTabs [aria-selected="true"] { background: linear-gradient(135deg, rgba(8,145,178,0.2), rgba(124,58,237,0.15)) !important; color: #22d3ee !important; }

/* ── Sidebar user ── */
.sidebar-user {
    position: relative;
    overflow: hidden;
    background: rgba(6,182,212,0.07);
    border: 1px solid rgba(6,182,212,0.18);
    border-radius: 14px;
    padding: 1rem 1.2rem;
    margin-bottom: 2rem;
}
.sidebar-user::before { content:''; position:absolute; top:0; left:0; right:0; height:1px; background:linear-gradient(90deg,transparent,rgba(6,182,212,0.6),transparent); }
.sidebar-user-label { font-family:'DM Mono',monospace; font-size:0.7rem; letter-spacing:2.5px; text-transform:uppercase; color:#4a6a7a; margin-bottom:0.3rem; }
.sidebar-user-name  { font-family:'Syne',sans-serif; font-size:1.15rem; font-weight:700; color:#22d3ee; }

/* ── Metrics ── */
[data-testid="stMetricValue"] { font-family:'Syne',sans-serif !important; font-size:2rem !important; font-weight:700 !important; color:#22d3ee !important; }
[data-testid="stMetricLabel"] { font-family:'DM Mono',monospace !important; color:#7aa8c0 !important; font-size:0.75rem !important; letter-spacing:2px !important; text-transform:uppercase !important; }

/* ── Expander ── */
.streamlit-expanderHeader {
    background: rgba(255,255,255,0.025) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 12px !important;
    color: #7aa8c0 !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.88rem !important;
}
.streamlit-expanderContent { background: rgba(255,255,255,0.018) !important; border: 1px solid rgba(255,255,255,0.06) !important; border-top: none !important; border-radius: 0 0 12px 12px !important; }

/* ── Download button ── */
[data-testid="stDownloadButton"] > button {
    background: rgba(6,182,212,0.1) !important;
    border: 1px solid rgba(6,182,212,0.3) !important;
    color: #22d3ee !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
    font-size: 0.88rem !important;
}
[data-testid="stDownloadButton"] > button:hover { background: rgba(6,182,212,0.18) !important; box-shadow: 0 4px 20px rgba(6,182,212,0.25) !important; transform: translateY(-2px) !important; }

/* ── Progress bar ── */
[data-testid="stProgressBar"] > div { background: rgba(6,182,212,0.1) !important; border-radius: 100px !important; }
[data-testid="stProgressBar"] > div > div { background: linear-gradient(90deg, #06b6d4, #8b5cf6) !important; border-radius: 100px !important; }

/* ── HR dividers ── */
hr { border: none !important; height: 1px !important; background: linear-gradient(90deg, transparent, rgba(6,182,212,0.25) 20%, rgba(139,92,246,0.25) 80%, transparent) !important; margin: 2rem 0 !important; }

/* ── Tech pills ── */
.tech-pills { display:flex; flex-wrap:wrap; gap:0.5rem; justify-content:center; margin-top:3rem; margin-bottom:0.5rem; }
.tech-pill { font-family:'DM Mono',monospace; font-size:0.7rem; letter-spacing:1.5px; text-transform:uppercase; color:#4a6a7a; padding:5px 16px; border:1px solid rgba(255,255,255,0.08); border-radius:100px; background:rgba(255,255,255,0.025); }

/* ── Live dot ── */
.live-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#10b981; box-shadow:0 0 0 0 rgba(16,185,129,0.4); animation:livePulse 2s ease-in-out infinite; vertical-align:middle; margin-right:8px; }
@keyframes livePulse { 0%{box-shadow:0 0 0 0 rgba(16,185,129,0.4);} 70%{box-shadow:0 0 0 8px rgba(16,185,129,0);} 100%{box-shadow:0 0 0 0 rgba(16,185,129,0);} }

/* ── Spinner ── */
.stSpinner > div { border-top-color: #22d3ee !important; border-right-color: rgba(6,182,212,0.2) !important; border-bottom-color: rgba(6,182,212,0.2) !important; border-left-color: rgba(6,182,212,0.2) !important; }

/* ── Sidebar version ── */
.sidebar-version { font-family:'DM Mono',monospace; font-size:0.68rem; letter-spacing:1.5px; color:#2a4050; text-transform:uppercase; text-align:center; padding:0.6rem; border-top:1px solid rgba(255,255,255,0.05); margin-top:1rem; line-height:1.8; }

/* ── Global text readability fixes ── */
label { color: #7aa8c0 !important; font-size: 0.85rem !important; }
p, .stMarkdown p { color: #9bbccc !important; font-size: 0.95rem !important; line-height: 1.7 !important; }
</style>
""", unsafe_allow_html=True)

init_db()
if "user"     not in st.session_state: st.session_state.user     = None
if "nav_page" not in st.session_state: st.session_state.nav_page = "Overview"


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PAGE
# ══════════════════════════════════════════════════════════════════════════════
def auth_page():
    st.markdown('<div style="height:3rem"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-eyebrow">Neurological Intelligence System</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">AtaxiaGuard</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-sub">BlazePose · MediaPipe · OpenCV · v2.1</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2.2, 1])
    with col2:
        tab1, tab2 = st.tabs(["Sign In", "Create Account"])
        with tab1:
            with st.form("login_form"):
                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
                username = st.text_input("Username", placeholder="your_username")
                password = st.text_input("Password", type="password", placeholder="••••••••••")
                st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)
                if st.form_submit_button("Sign In →"):
                    user = login_user(username, password)
                    if user:
                        st.session_state.user = {"id": user[0], "username": user[1]}
                        st.rerun()
                    else:
                        st.error("Invalid credentials. Please check your username and password.")
        with tab2:
            with st.form("register_form"):
                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
                new_user = st.text_input("Username", placeholder="choose_username")
                email    = st.text_input("Email", placeholder="your@email.com")
                new_pass = st.text_input("Password", type="password", placeholder="min 6 characters")
                confirm  = st.text_input("Confirm Password", type="password", placeholder="repeat password")
                st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)
                if st.form_submit_button("Create Account →"):
                    if new_pass != confirm:
                        st.error("Passwords don't match.")
                    elif len(new_pass) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        ok, msg = register_user(new_user, new_pass, email)
                        if ok:
                            st.success(msg + " Please sign in.")
                        else:
                            st.error(msg)

    st.markdown("""
    <div class="tech-pills">
        <span class="tech-pill">BlazePose</span>
        <span class="tech-pill">MediaPipe</span>
        <span class="tech-pill">OpenCV</span>
        <span class="tech-pill">SQLite</span>
        <span class="tech-pill">ReportLab</span>
        <span class="tech-pill">Plotly</span>
    </div>
    <div style="text-align:center;color:#2a4050;font-family:'DM Mono',monospace;font-size:0.68rem;letter-spacing:2px;margin-top:0.5rem">
        FOR INFORMATIONAL USE ONLY · NOT MEDICAL ADVICE
    </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD SHELL
# ══════════════════════════════════════════════════════════════════════════════
def dashboard_page():
    user = st.session_state.user
    with st.sidebar:
        st.markdown(f"""
        <div class="sidebar-user">
            <div class="sidebar-user-label">Signed in as</div>
            <div class="sidebar-user-name">{user["username"]}</div>
        </div>""", unsafe_allow_html=True)
        nav_options = ["Overview", "Run Analysis", "Reports", "Sign Out"]
        idx  = nav_options.index(st.session_state.nav_page) if st.session_state.nav_page in nav_options else 0
        page = st.radio("Navigation", nav_options, index=idx, label_visibility="collapsed")
        st.session_state.nav_page = page
        st.markdown("""<div class="sidebar-version">AtaxiaGuard v2.1<br>
        <span style="color:#22d3ee;opacity:0.5">● Online</span></div>""", unsafe_allow_html=True)

    if page == "Sign Out":
        st.session_state.user = None; st.session_state.nav_page = "Overview"; st.rerun()
    elif page == "Overview":     home_page(user)
    elif page == "Run Analysis": test_page(user)
    elif page == "Reports":      reports_page(user)


# ══════════════════════════════════════════════════════════════════════════════
# HOME PAGE
# ══════════════════════════════════════════════════════════════════════════════
def home_page(user):
    st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-eyebrow">Welcome back</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="hero-title">{user["username"]}</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-sub">Neuro-motor health dashboard</div>', unsafe_allow_html=True)

    conn    = sqlite3.connect(DB_PATH)
    results = conn.execute(
        "SELECT * FROM test_results WHERE user_id=? ORDER BY test_date DESC",
        (user["id"],)).fetchall()
    conn.close()

    total    = len(results)
    avg_val  = round(sum(float(r[6]) for r in results) / total, 1) if results else None
    last_sev = results[0][7] if results else None
    sev_colors = {"Normal":"#34d399","Mild":"#22d3ee","Moderate":"#fbbf24","Severe":"#f87171"}
    sev_col    = sev_colors.get(last_sev, "#4a6a7a") if last_sev else "#4a6a7a"

    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:2rem">
        <div class="stat-block">
            <div class="stat-label">Total Sessions</div>
            <div class="stat-value">{total}</div>
            <div class="stat-unit">analyses run</div>
        </div>
        <div class="stat-block">
            <div class="stat-label">Avg Score</div>
            <div class="stat-value">{avg_val if avg_val is not None else "—"}</div>
            <div class="stat-unit">out of 10.0</div>
        </div>
        <div class="stat-block">
            <div class="stat-label">Last Severity</div>
            <div class="stat-value" style="color:{sev_col};font-size:1.7rem">{last_sev if last_sev else "—"}</div>
            <div class="stat-unit">classification</div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("""
    <div class="feature-grid">
        <div class="feature-card">
            <div class="feature-tag">Computer Vision</div>
            <div class="feature-title">BlazePose Gait Analysis</div>
            <div class="feature-desc">Real-time 33-landmark body tracking using MediaPipe BlazePose. Captures gait symmetry, stride consistency, and postural sway with millisecond precision.</div>
        </div>
        <div class="feature-card">
            <div class="feature-tag">Clinical Intelligence</div>
            <div class="feature-title">Reports & Trend Tracking</div>
            <div class="feature-desc">Score trends, radar plots, severity history, and personalised dietary and exercise plans. Exportable clinical PDF reports for every session.</div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Launch Analysis", key="home_start"): st.session_state.nav_page = "Run Analysis"; st.rerun()
    with c2:
        if st.button("View Reports", key="home_reports"):  st.session_state.nav_page = "Reports";      st.rerun()

    if results:
        st.markdown("---")
        st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;
                    text-transform:uppercase;color:#7aa8c0;margin-bottom:1rem">Recent Sessions</div>""",
                    unsafe_allow_html=True)
        for r in results[:3]:
            try:   dt = datetime.fromisoformat(r[2]).strftime("%d %b %Y  %H:%M")
            except: dt = r[2]
            sev = r[7]
            bc  = {"Normal":"badge-normal","Mild":"badge-mild","Moderate":"badge-moderate","Severe":"badge-severe"}.get(sev,"badge-mild")
            dot = sev_colors.get(sev, "#22d3ee")
            st.markdown(f"""
            <div style="display:flex;align-items:center;justify-content:space-between;
                        padding:1.1rem 1.4rem;background:rgba(255,255,255,0.025);
                        border:1px solid rgba(255,255,255,0.08);border-radius:14px;margin-bottom:0.6rem">
                <div style="font-family:'DM Mono',monospace;font-size:0.85rem;color:#7aa8c0">{dt}</div>
                <div style="display:flex;align-items:center;gap:1.5rem">
                    <div style="font-family:'Syne',sans-serif;font-size:1.15rem;font-weight:700;color:#22d3ee">
                        {r[6]}<span style="font-size:0.68rem;color:#4a6a7a;font-family:'DM Mono',monospace"> /10</span>
                    </div>
                    <span class="badge {bc}"><span class="badge-dot" style="background:{dot}"></span>{sev}</span>
                </div>
            </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEST PAGE
# ══════════════════════════════════════════════════════════════════════════════
def test_page(user):
    st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-eyebrow">Computer Vision · Real-time</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Ataxia Analysis</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-sub">BlazePose · 33 Landmarks · 20s Protocol</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card">
        <div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;text-transform:uppercase;color:#7aa8c0;margin-bottom:1.3rem">Test Protocol</div>
        <div class="step-grid">
            <div class="step-card"><span class="step-number">Step 01</span><div class="step-title">Position</div><div class="step-desc">Stand 2–3m from camera. Ensure head to feet are visible in frame.</div></div>
            <div class="step-card"><span class="step-number">Step 02</span><div class="step-title">Confirm</div><div class="step-desc">System waits for full-body landmark detection. 30s limit.</div></div>
            <div class="step-card"><span class="step-number">Step 03</span><div class="step-title">Walk</div><div class="step-desc">Walk naturally for 20 seconds. Gait, balance & coordination scored.</div></div>
            <div class="step-card"><span class="step-number">Step 04</span><div class="step-title">Results</div><div class="step-desc">Scores saved with severity class and clinical recommendations.</div></div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown("""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.9rem;margin-bottom:1.6rem">
        <div style="text-align:center;padding:1.3rem;background:rgba(6,182,212,0.05);border:1px solid rgba(6,182,212,0.15);border-radius:14px">
            <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#22d3ee;text-shadow:0 0 20px rgba(6,182,212,0.4)">33</div>
            <div style="font-family:'DM Mono',monospace;font-size:0.75rem;letter-spacing:2px;color:#7aa8c0;text-transform:uppercase;margin-top:5px">Landmarks</div>
        </div>
        <div style="text-align:center;padding:1.3rem;background:rgba(139,92,246,0.05);border:1px solid rgba(139,92,246,0.15);border-radius:14px">
            <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#a78bfa;text-shadow:0 0 20px rgba(139,92,246,0.4)">20s</div>
            <div style="font-family:'DM Mono',monospace;font-size:0.75rem;letter-spacing:2px;color:#7aa8c0;text-transform:uppercase;margin-top:5px">Test Window</div>
        </div>
        <div style="text-align:center;padding:1.3rem;background:rgba(16,185,129,0.05);border:1px solid rgba(16,185,129,0.15);border-radius:14px">
            <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#34d399;text-shadow:0 0 20px rgba(16,185,129,0.4)">4</div>
            <div style="font-family:'DM Mono',monospace;font-size:0.75rem;letter-spacing:2px;color:#7aa8c0;text-transform:uppercase;margin-top:5px">Metrics Scored</div>
        </div>
    </div>""", unsafe_allow_html=True)

    ATAXIA_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ataxia.py")
    if st.button("Launch Camera Analysis →", use_container_width=True):
        st.session_state.run_test = True

    if st.session_state.get("run_test"):
        if not os.path.exists(ATAXIA_SCRIPT):
            st.error(f"ataxia.py not found at: {ATAXIA_SCRIPT}")
            st.session_state.run_test = False
        else:
            st.markdown("""
            <div style="background:rgba(6,182,212,0.06);border:1px solid rgba(6,182,212,0.2);
                        border-radius:14px;padding:1.1rem 1.4rem;margin:0.8rem 0;
                        font-family:'DM Sans',sans-serif;font-size:0.95rem;color:#22d3ee">
                <span class="live-dot"></span>
                Camera opening — show full body, complete the 20s test, press <strong>Q</strong> to finish early.
            </div>""", unsafe_allow_html=True)
            with st.spinner("Running BlazePose analysis…"):
                try:
                    proc   = subprocess.run([sys.executable, ATAXIA_SCRIPT],
                                            capture_output=True, text=True, timeout=120)
                    stdout = proc.stdout.strip()
                    stderr = proc.stderr.strip()
                    result = None
                    if stdout:
                        try: result = json.loads(stdout.splitlines()[-1])
                        except json.JSONDecodeError: pass

                    if result:
                        if result.get("insufficient_data"):
                            st.markdown(f"""
                            <div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);
                                        border-radius:16px;padding:2rem;text-align:center;margin:1rem 0">
                                <div style="font-family:'DM Mono',monospace;font-size:0.75rem;letter-spacing:3px;
                                            color:#fbbf24;margin-bottom:0.6rem;text-transform:uppercase">Insufficient Data</div>
                                <div style="font-family:'Syne',sans-serif;color:#ddeeff;font-size:1.1rem;font-weight:700;margin-bottom:0.5rem">
                                    {result.get("reason","Full body was not detected.")}
                                </div>
                                <div style="color:#5a7a8a;font-size:0.88rem">{result.get("notes","")}</div>
                            </div>
                            <div style="font-family:'DM Mono',monospace;color:#4a6a7a;font-size:0.78rem;text-align:center;margin-top:0.6rem;letter-spacing:1px">
                                Tip: Stand 2–3 metres from camera. Ensure head and feet are both visible.
                            </div>""", unsafe_allow_html=True)
                        else:
                            save_result(user["id"], result)
                            st.success("Analysis complete — results saved to your profile.")
                            _show_result_card(result)
                    else:
                        hint = f"\n\nError: `{stderr[:300]}`" if stderr else ""
                        st.error(f"Could not parse results.{hint}")
                except subprocess.TimeoutExpired:
                    st.warning("Test timed out after 120 seconds.")
                except Exception as e:
                    st.error(f"Unexpected error: {e}")
            st.session_state.run_test = False


def _show_result_card(scores):
    sev = scores.get("severity", "Unknown")
    sev_colors = {"Normal":"#34d399","Mild":"#22d3ee","Moderate":"#fbbf24","Severe":"#f87171"}
    sev_color  = sev_colors.get(sev, "#ddeeff")
    badge_cls  = {"Normal":"badge-normal","Mild":"badge-mild","Moderate":"badge-moderate","Severe":"badge-severe"}.get(sev,"badge-mild")
    dot_color  = sev_colors.get(sev, "#22d3ee")
    st.markdown(f"""
    <div class="card" style="margin-top:1.2rem">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
            <div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;text-transform:uppercase;color:#7aa8c0">Analysis Result</div>
            <span class="badge {badge_cls}"><span class="badge-dot" style="background:{dot_color}"></span>{sev}</span>
        </div>
        <div class="score-grid">
            <div class="score-item overall"><div class="score-label">Overall</div><div class="score-value" style="color:{sev_color}">{scores.get('overall_score','—')}</div></div>
            <div class="score-item gait"><div class="score-label">Gait</div><div class="score-value" style="color:#22d3ee">{scores.get('gait_score','—')}</div></div>
            <div class="score-item balance"><div class="score-label">Balance</div><div class="score-value" style="color:#a78bfa">{scores.get('balance_score','—')}</div></div>
            <div class="score-item coord"><div class="score-label">Coord</div><div class="score-value" style="color:#f472b6">{scores.get('coordination_score','—')}</div></div>
        </div>
    </div>""", unsafe_allow_html=True)
    st.progress(float(scores.get("overall_score", 5)) / 10.0)


def save_result(user_id, scores):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT INTO test_results
        (user_id,test_date,gait_score,balance_score,coordination_score,overall_score,severity,notes)
        VALUES (?,?,?,?,?,?,?,?)""",
        (user_id, datetime.now().isoformat(),
         scores.get("gait_score"), scores.get("balance_score"),
         scores.get("coordination_score"), scores.get("overall_score"),
         scores.get("severity"), scores.get("notes","")))
    conn.commit(); conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# REPORTS PAGE
# ══════════════════════════════════════════════════════════════════════════════
def reports_page(user):
    import pandas as pd
    import plotly.graph_objects as go
    import plotly.express as px

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT test_date,gait_score,balance_score,coordination_score,
                  overall_score,severity,notes
           FROM test_results WHERE user_id=? ORDER BY test_date DESC""",
        (user["id"],)).fetchall()
    conn.close()

    if not rows:
        st.markdown('<div style="height:2rem"></div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-eyebrow">Nothing here yet</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title">No Sessions</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-sub">Run your first analysis to see reports</div>', unsafe_allow_html=True)
        if st.button("Run Analysis Now"):
            st.session_state.nav_page = "Run Analysis"; st.rerun()
        return

    df = pd.DataFrame(rows, columns=["Date","Gait","Balance","Coordination","Overall","Severity","Notes"])
    df["Date"] = pd.to_datetime(df["Date"])
    latest = df.iloc[0]
    sev    = latest["Severity"]

    st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-eyebrow">Clinical Overview</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Health Reports</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="hero-sub">Last session · {latest["Date"].strftime("%d %b %Y")}</div>', unsafe_allow_html=True)

    latest_row = {
        "test_date": latest["Date"].isoformat(), "gait_score": latest["Gait"],
        "balance_score": latest["Balance"], "coordination_score": latest["Coordination"],
        "overall_score": latest["Overall"], "severity": latest["Severity"], "notes": latest["Notes"]
    }
    pdf_bytes = generate_pdf_report(user["username"], latest_row)
    st.download_button(
        label="⬇  Download Latest Clinical Report (PDF)",
        data=pdf_bytes,
        file_name=f"AtaxiaGuard_{user['username']}_{latest['Date'].strftime('%Y%m%d')}.pdf",
        mime="application/pdf", use_container_width=True)

    st.markdown("---")

    sev_colors = {"Normal":"#34d399","Mild":"#22d3ee","Moderate":"#fbbf24","Severe":"#f87171"}
    badge_cls  = {"Normal":"badge-normal","Mild":"badge-mild","Moderate":"badge-moderate","Severe":"badge-severe"}.get(sev,"badge-mild")
    sev_color  = sev_colors.get(sev, "#ddeeff")
    dot_color  = sev_colors.get(sev, "#22d3ee")

    st.markdown(f"""
    <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem">
            <div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;text-transform:uppercase;color:#7aa8c0">Latest Assessment</div>
            <span class="badge {badge_cls}"><span class="badge-dot" style="background:{dot_color}"></span>{sev}</span>
        </div>
        <div class="score-grid">
            <div class="score-item gait"><div class="score-label">Gait</div><div class="score-value" style="color:#22d3ee">{latest['Gait']}</div></div>
            <div class="score-item balance"><div class="score-label">Balance</div><div class="score-value" style="color:#a78bfa">{latest['Balance']}</div></div>
            <div class="score-item coord"><div class="score-label">Coordination</div><div class="score-value" style="color:#f472b6">{latest['Coordination']}</div></div>
            <div class="score-item overall"><div class="score-label">Overall</div><div class="score-value" style="color:{sev_color}">{latest['Overall']}</div></div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;
                text-transform:uppercase;color:#7aa8c0;margin:1.8rem 0 0.8rem;
                display:flex;align-items:center;gap:1rem">Score Trends
                <span style="flex:1;height:1px;background:linear-gradient(90deg,rgba(6,182,212,0.3),transparent)"></span>
                </div>""", unsafe_allow_html=True)

    fig = go.Figure()
    for col, color in [("Gait","#22d3ee"),("Balance","#a78bfa"),("Coordination","#f472b6"),("Overall","#fbbf24")]:
        r,g,b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
        fig.add_trace(go.Scatter(
            x=df["Date"][::-1], y=df[col][::-1], name=col,
            line=dict(color=color, width=2.5), mode="lines+markers",
            marker=dict(size=7, color=color, line=dict(width=2, color='rgba(4,6,15,0.9)')),
            fill="tozeroy", fillcolor=f"rgba({r},{g},{b},0.05)"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Mono", color="#7aa8c0", size=13),
        legend=dict(orientation="h", y=-0.18, font=dict(size=13, color="#9bbccc"), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0,r=0,t=10,b=0),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", showline=False, zeroline=False, tickfont=dict(color="#7aa8c0", size=12)),
        yaxis=dict(gridcolor="rgba(255,255,255,0.04)", range=[0,10.5], showline=False, zeroline=False, tickfont=dict(color="#7aa8c0", size=12)))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;
                        text-transform:uppercase;color:#7aa8c0;margin-bottom:0.7rem">Ability Radar</div>""", unsafe_allow_html=True)
        radar = go.Figure(go.Scatterpolar(
            r=[float(latest["Gait"]),float(latest["Balance"]),float(latest["Coordination"]),float(latest["Overall"])],
            theta=["Gait","Balance","Coordination","Overall"],
            fill="toself", fillcolor="rgba(6,182,212,0.07)",
            line=dict(color="#22d3ee", width=2),
            marker=dict(color="#22d3ee", size=7, line=dict(width=2, color='rgba(4,6,15,0.9)'))))
        radar.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            polar=dict(
                radialaxis=dict(visible=True, range=[0,10], gridcolor="rgba(255,255,255,0.05)",
                                color="#7aa8c0", tickfont=dict(size=12, color="#7aa8c0")),
                angularaxis=dict(gridcolor="rgba(255,255,255,0.05)", color="#7aa8c0",
                                 tickfont=dict(size=13, color="#9bbccc")),
                bgcolor="rgba(0,0,0,0)"),
            font=dict(family="DM Mono", color="#7aa8c0", size=13),
            margin=dict(l=20,r=20,t=20,b=20))
        st.plotly_chart(radar, use_container_width=True)

    with c2:
        st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;
                        text-transform:uppercase;color:#7aa8c0;margin-bottom:0.7rem">Severity Breakdown</div>""", unsafe_allow_html=True)
        sev_counts = df["Severity"].value_counts()
        pie = px.pie(values=sev_counts.values, names=sev_counts.index,
            color_discrete_map={"Normal":"#34d399","Mild":"#22d3ee","Moderate":"#fbbf24","Severe":"#f87171"},
            hole=0.6)
        pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="DM Mono", color="#7aa8c0", size=13),
            legend=dict(font=dict(size=13, color="#9bbccc"), bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=0,r=0,t=0,b=0))
        pie.update_traces(
            textfont=dict(family="DM Mono", size=13, color="#ddeeff"),
            marker=dict(line=dict(color='rgba(4,6,15,0.9)', width=2)))
        st.plotly_chart(pie, use_container_width=True)

    recommendations(sev)

    st.markdown("---")
    st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:2.5px;
                text-transform:uppercase;color:#7aa8c0;margin-bottom:1rem;
                display:flex;align-items:center;gap:1rem">Session History
                <span style="flex:1;height:1px;background:linear-gradient(90deg,rgba(6,182,212,0.3),transparent)"></span>
                </div>""", unsafe_allow_html=True)

    for i, row in df.iterrows():
        date_label = row["Date"].strftime("%d %b %Y  %H:%M")
        with st.expander(f"{date_label}   ·   {row['Overall']}/10   ·   {row['Severity']}"):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Gait", row["Gait"])
            c2.metric("Balance", row["Balance"])
            c3.metric("Coordination", row["Coordination"])
            c4.metric("Overall", row["Overall"])
            if row["Notes"]:
                st.markdown(f'<div style="color:#7aa8c0;font-size:0.9rem;margin-top:0.5rem;line-height:1.6">{row["Notes"]}</div>', unsafe_allow_html=True)
            row_data = {
                "test_date": row["Date"].isoformat(), "gait_score": row["Gait"],
                "balance_score": row["Balance"], "coordination_score": row["Coordination"],
                "overall_score": row["Overall"], "severity": row["Severity"], "notes": row["Notes"]
            }
            row_pdf   = generate_pdf_report(user["username"], row_data)
            date_slug = row["Date"].strftime("%Y%m%d_%H%M")
            st.download_button(label="Download Report (PDF)", data=row_pdf,
                file_name=f"AtaxiaGuard_{user['username']}_{date_slug}.pdf",
                mime="application/pdf", key=f"dl_{i}")


# ══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════════
def recommendations(severity):
    if severity == "Normal":
        st.markdown("""
        <div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.18);
                    border-radius:18px;padding:2rem;margin-top:1.5rem;text-align:center;position:relative;overflow:hidden">
            <div style="position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(16,185,129,0.5),transparent)"></div>
            <div style="font-family:'DM Mono',monospace;font-size:0.75rem;letter-spacing:3px;text-transform:uppercase;color:#34d399;margin-bottom:0.7rem">All Clear</div>
            <div style="font-family:'Syne',sans-serif;color:#ddeeff;font-size:1.2rem;font-weight:700;margin-bottom:0.6rem">Movement Patterns Normal</div>
            <div style="color:#5a7a8a;font-size:0.92rem;line-height:1.7;font-family:'DM Sans',sans-serif;max-width:480px;margin:0 auto">
                Gait symmetry, balance stability and limb coordination are within healthy ranges.
                Keep up your active lifestyle — no intervention required at this time.
            </div>
        </div>""", unsafe_allow_html=True)
        return

    food_map = {
        "Mild":     [("🫐","Blueberries","Daily — neuroprotective flavonoids"),("🥑","Avocado","Vitamin E, B6 and folate for nerve function"),
                     ("🌿","Turmeric Milk","Curcumin anti-inflammatory benefits"),("🐟","Fatty Fish","Omega-3 rich, 3× per week"),
                     ("🌰","Brazil Nuts","Selenium for cerebellar function")],
        "Moderate": [("🧃","CoQ10 Foods","Beef, sardines, organ meats"),("🥗","Mediterranean Diet","Clinically proven neurological benefits"),
                     ("🥛","Fortified Dairy","Vitamin D and B12 for nerve myelin"),("🫘","Lentils","Folate and magnesium for neuromotor function"),
                     ("🫚","Olive Oil","Oleocanthal natural anti-inflammatory")],
        "Severe":   [("💊","Neurologist","Professional supplementation guidance"),("🥣","Soft Foods","Easy-to-swallow if dysphagia present"),
                     ("🧃","Nutritional Shakes","High-calorie if weight loss occurring"),("🥩","Lean Protein","Every meal — preserve muscle mass"),
                     ("💧","Hydration","2L+ water daily minimum")]
    }
    exercise_map = {
        "Mild":     [("⚖️","Single-Leg Stand","30 sec × 3 sets daily"),("🧘","Tai Chi","Proven cerebellar stimulation"),
                     ("🚶","Terrain Walking","Grass or sand surfaces"),("💪","Resistance Bands","Upper and lower body"),
                     ("👁️","Gaze Stabilisation","Vestibular training")],
        "Moderate": [("🧑‍⚕️","Frenkel Exercises","Physio-guided coordination rehab"),("🪑","Chair Yoga","Seated stretches, safe for moderate"),
                     ("⚖️","Parallel Bar Walk","Supported gait training"),("🙌","Fine Motor Tasks","Clay, writing, threading"),
                     ("🏊","Hydrotherapy","Buoyancy reduces fall risk")],
        "Severe":   [("🧑‍⚕️","Supervised Physio","Inpatient or supervised essential"),("🛌","Bed Exercises","Ankle pumps and leg raises"),
                     ("🦽","Assistive Training","Mobility device training"),("🗣️","Speech Therapy","Swallowing and speech rehab"),
                     ("🧠","Cognitive Exercises","Maintain neuroplasticity")]
    }

    if severity in ("Moderate","Severe"):
        st.error("Medical Alert — Significant motor impairment detected. Please consult a neurologist.")
    elif severity == "Mild":
        st.warning("Early indicators detected. Consistent exercise and diet can slow progression.")

    food_items     = food_map.get(severity, food_map["Mild"])
    exercise_items = exercise_map.get(severity, exercise_map["Mild"])

    st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:3px;
                text-transform:uppercase;color:#7aa8c0;margin:2rem 0 0.9rem;
                display:flex;align-items:center;gap:1rem">Recommended Foods
                <span style="flex:1;height:1px;background:linear-gradient(90deg,rgba(6,182,212,0.3),transparent)"></span>
                </div>""", unsafe_allow_html=True)
    cols = st.columns(len(food_items))
    for i, (icon, title, desc) in enumerate(food_items):
        with cols[i]:
            st.markdown(f'<div class="rec-card"><span class="rec-card-icon">{icon}</span>'
                        f'<div class="rec-card-title">{title}</div>'
                        f'<div class="rec-card-desc">{desc}</div></div>', unsafe_allow_html=True)

    st.markdown("""<div style="font-family:'DM Mono',monospace;font-size:0.78rem;letter-spacing:3px;
                text-transform:uppercase;color:#7aa8c0;margin:2rem 0 0.9rem;
                display:flex;align-items:center;gap:1rem">Exercise Plan
                <span style="flex:1;height:1px;background:linear-gradient(90deg,rgba(139,92,246,0.3),transparent)"></span>
                </div>""", unsafe_allow_html=True)
    cols = st.columns(len(exercise_items))
    for i, (icon, title, desc) in enumerate(exercise_items):
        with cols[i]:
            st.markdown(f'<div class="rec-card"><span class="rec-card-icon">{icon}</span>'
                        f'<div class="rec-card-title">{title}</div>'
                        f'<div class="rec-card-desc">{desc}</div></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.user is None:
    auth_page()
else:
    dashboard_page()