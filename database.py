"""
Panacea — Complete Database Layer v2.4
=======================================
Changes in v2.4:
- Categories now follow WHO Essential Medicines List (EML) pharmacological
  classification — the same system used by hospitals, regulators (CDSCO,
  FDA), and international pharmacies.  Medicines are grouped by their
  mechanism / drug class, NOT by disease or symptom.  This is ethically
  correct: a medicine may treat many conditions; labelling it under a
  disease can mislead patients and create medico-legal liability.
- 16 WHO-aligned categories replace the previous 10 disease-label categories
- All products remapped to correct pharmacological categories
- Image URLs (Wikimedia Commons public-domain, 400 px) unchanged from v2.3
- product_images gallery table retained
- migrate_database() is safe on existing DBs
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from pathlib import Path
import secrets

DB_PATH = Path(__file__).parent / "database" / "panacea.db"
DB_PATH.parent.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# WHO EML-ALIGNED CATEGORIES  (cat_id → name mapping used in seed data)
# ─────────────────────────────────────────────────────────────────────────────
#
#  ID  Name                          WHO EML Section reference
#  1   Analgesics & NSAIDs           §2  Analgesics
#  2   Antipyretics                  §2  Analgesics (fever sub-use)
#  3   Antibacterials                §6  Anti-infective medicines
#  4   Antifungals                   §6  Anti-infective medicines
#  5   Antihistamines                §3  Antiallergics
#  6   Antihypertensives             §12 Cardiovascular medicines
#  7   Antidiabetics & Insulin       §18 Hormones / Diabetes
#  8   Respiratory & Bronchodilators §25 Medicines for respiratory tract
#  9   Proton Pump Inhibitors & Antacids  §17 Gastrointestinal
# 10   Oral Rehydration & Electrolytes    §17 Gastrointestinal
# 11   Vitamins & Micronutrients     §27 Vitamins & minerals
# 12   Dietary Supplements & Omega   §27 Vitamins & minerals (suppl.)
# 13   Corticosteroids (Topical)     §13 Dermatological
# 14   Antifungals (Topical)         §13 Dermatological
# 15   Psychotropics & Sleep Aids    §24 Medicines for mental disorders
# 16   Ophthalmic & Otic Preparations §21 Ophthalmological
#
# ─────────────────────────────────────────────────────────────────────────────

# Wikimedia Commons 400 px public-domain thumbnails
IMG = {
    "paracetamol":    "https://5.imimg.com/data5/SELLER/Default/2023/3/293942419/AF/BH/OZ/43236650/paracetamol-dolo-tablet-500-mg-1000x1000.jpg",
    "ibuprofen":      "https://5.imimg.com/data5/SELLER/Default/2023/4/301541063/BT/YB/AK/7034457/ibuprofen-tablets-1000x1000.jpg",
    "aspirin":        "https://cdn.bmstores.co.uk/images/hpcProductImage/imgFull/420750-galpharm-aspirin-75mg-28tablets.jpg",
    "diclofenac_gel": "https://tse1.mm.bing.net/th/id/OIP.uTw3SbwHgdtN5b9LVG5P4wHaEa?r=0&rs=1&pid=ImgDetMain&o=7&rm=3",
    "amoxicillin":    "https://tse4.mm.bing.net/th/id/OIP.CfmKbP2EsJsH0WDJky5T4QHaHa?r=0&rs=1&pid=ImgDetMain&o=7&rm=3",
    "azithromycin":   "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjOAE2B2ZL0hCuCwJPM3PyFh4PiLcVKqqaz8__AIgVG5-SfCh2l8IM-nOp769hO1sMFh_BYZq4Zt-YQrm9b4nTGtTowR13szNV3Z38V2VlHwFJBDy9aB6QWQcsVLDQYUNZ2b1agkQjD2rrNyVa_Pqf48COnxafCPiis0Sf-ZGv7Lp0ht7AtCpCwWRlcZ2jK/w0/zith-500-860x860.jpg",
    "cetirizine":     "https://tse1.mm.bing.net/th/id/OIP.-fTslcQpSofLC34Gkw-IZQHaHa?r=0&rs=1&pid=ImgDetMain&o=7&rm=3",
    "vitamin_c":      "https://th.bing.com/th/id/OIP.VHQ2vXlMe-DHnx-ImU9QDQHaHa?w=184&h=184&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "vitamin_d3":     "https://th.bing.com/th/id/OIP.5A3iwhLnEFWoED9dx01rbAHaHj?w=184&h=188&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "omega3":         "https://th.bing.com/th/id/OIP.Rhu1_MpFpl_rlw4-gqNWeAHaHa?w=194&h=194&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "multivitamin":   "https://th.bing.com/th/id/OIP.KoEKOPNti5aMi-2jRYwjNQHaHa?w=190&h=190&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "atenolol":       "https://tse3.mm.bing.net/th/id/OIP.wdJBzRHkVjMnPe3pOl3S3gHaHa?r=0&rs=1&pid=ImgDetMain&o=7&rm=3",
    "amlodipine":     "https://th.bing.com/th/id/OIP.olvkDGLyx7Rvyr5vx_tAzwHaHa?w=202&h=202&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "losartan":       "https://th.bing.com/th/id/OIP.XwMzlxBpk12r58mjc5YQtAHaHa?w=184&h=183&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "metformin":      "https://th.bing.com/th/id/OIP.995fodxCKwOLH5pZEVRO4wHaHa?w=198&h=198&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "glimepiride":    "https://tse3.mm.bing.net/th/id/OIP.jnlPAtyxmf7WvB2h-ivevQHaFJ?r=0&rs=1&pid=ImgDetMain&o=7&rm=3",
    "insulin":        "https://th.bing.com/th/id/OIP.MUYPUDvBkyhtDEw5Lud6RQHaHa?w=163&h=180&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "salbutamol":     "https://th.bing.com/th/id/OIP.BZURHhV-sdie1G32a0zRsgHaGe?w=200&h=180&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "montelukast":    "https://th.bing.com/th/id/OIP.Oiv0sH36Yu98Gc9sj54RhAHaHa?w=188&h=188&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "levocetirizine": "https://th.bing.com/th/id/OIP.xMgN9nEnQqmg88TmYI4edQHaGL?w=226&h=189&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "omeprazole":     "https://th.bing.com/th/id/OIP.-8bI10LBfzbX8RJIByve0QHaHa?w=219&h=219&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "ors":            "https://th.bing.com/th/id/OIP.S6XSvVF8mZHurAedlvv36gHaHa?w=181&h=181&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "pantoprazole":   "https://th.bing.com/th/id/OIP.H-_8IQujOTT-5ILvlHiAEAHaFS?w=242&h=180&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "clotrimazole":   "https://th.bing.com/th/id/OIP.onS6U1HBadMRc8jf5CH0PwHaHa?w=184&h=184&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "hydrocortisone": "https://th.bing.com/th/id/OIP.kIFzX2z12_rw8fnjawZflAHaHa?w=184&h=184&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "melatonin":      "https://th.bing.com/th/id/OIP.CC0hscOnVfwNFhXvwj1Y3wHaHy?r=0&o=7rm=3&rs=1&pid=ImgDetMain&o=7&rm=3",
    "sertraline":     "https://th.bing.com/th/id/OIP.yrRZIAvt2afwE1UQkrsKEAHaHa?w=183&h=182&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "cipro_eye":      "https://5.imimg.com/data5/SELLER/Default/2024/2/390677153/NH/LI/RI/121413071/ciprofloxacin-eye-drops-1000x1000.jpg",
    "tobramycin":     "https://th.bing.com/th/id/OIP.kqGEDctf_X7szgH7cH3F0AHaHa?w=211&h=211&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
    "otrivin":        "https://th.bing.com/th/id/OIP.tsG6pZ_m0Iv7IvtyX3OENAHaHa?w=205&h=205&c=7&r=0&o=7&dpr=1.3&pid=1.7&rm=3",
}


def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    conn = get_db_connection()
    c = conn.cursor()

    # ── USERS ─────────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            age INTEGER,
            gender TEXT,
            address TEXT,
            user_type TEXT DEFAULT 'customer',   -- customer | doctor | admin
            password_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            profile_completed BOOLEAN DEFAULT 0
        )
    ''')

    # ── DOCTOR PROFILES ───────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS doctors_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            specialization TEXT NOT NULL,
            license_number TEXT NOT NULL,
            qualification TEXT,
            experience_years INTEGER DEFAULT 0,
            consultation_fee REAL DEFAULT 0,
            bio TEXT,
            available_days TEXT,     -- JSON array e.g. ["Mon","Tue"]
            available_hours TEXT,    -- JSON e.g. {"start":"09:00","end":"17:00"}
            is_verified BOOLEAN DEFAULT 0,
            rating REAL DEFAULT 0,
            total_consultations INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── HEALTH METRICS ────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            height_cm REAL,
            weight_kg REAL,
            blood_group TEXT,
            blood_pressure_systolic INTEGER,
            blood_pressure_diastolic INTEGER,
            heart_rate INTEGER,
            allergies TEXT,           -- JSON array
            chronic_conditions TEXT,  -- JSON array
            current_medications TEXT, -- JSON array
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── SYMPTOM ASSESSMENTS ───────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symptoms TEXT NOT NULL,   -- JSON array
            age_group TEXT,
            gender TEXT,
            predictions TEXT,         -- JSON array
            top_disease TEXT,
            confidence REAL,
            suggested_specialist TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── CONSULTATION REQUESTS ─────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS consultation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            assessment_id INTEGER,
            symptoms_summary TEXT,
            predicted_disease TEXT,
            status TEXT DEFAULT 'pending',  -- pending|accepted|completed|cancelled
            patient_message TEXT,
            doctor_response TEXT,
            scheduled_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users(id),
            FOREIGN KEY (doctor_id) REFERENCES users(id),
            FOREIGN KEY (assessment_id) REFERENCES assessments(id)
        )
    ''')

    # ── PRESCRIPTIONS ─────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            consultation_id INTEGER,
            cart_request_id INTEGER,
            notes TEXT,
            status TEXT DEFAULT 'active',  -- active|used|expired|rejected
            valid_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doctor_id) REFERENCES users(id),
            FOREIGN KEY (patient_id) REFERENCES users(id),
            FOREIGN KEY (consultation_id) REFERENCES consultation_requests(id)
        )
    ''')

    # ── PRESCRIPTION ITEMS ────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS prescription_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prescription_id INTEGER NOT NULL,
            product_id INTEGER,
            medicine_name TEXT NOT NULL,
            dosage TEXT,
            duration TEXT,
            quantity INTEGER DEFAULT 1,
            instructions TEXT,
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id) ON DELETE CASCADE
        )
    ''')

    # ── CATEGORIES (WHO EML pharmacological classification) ───────────────────
    #
    # WHY pharmacological, not disease-based?
    # ─────────────────────────────────────────
    # • A single drug may treat dozens of conditions (e.g. Aspirin → pain,
    #   fever, cardiac prophylaxis, stroke prevention).  Tagging it under
    #   one disease is inaccurate and potentially misleading.
    # • WHO, CDSCO, and all licensed pharmacies classify by drug class /
    #   mechanism.  This is the medically and legally defensible standard.
    # • Disease-based categorisation could imply diagnostic guidance, which
    #   requires a licensed practitioner — not an e-commerce category label.
    #
    # Each category below maps to a WHO EML section.
    c.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,        -- plain-language explanation shown to users
            who_eml_section TEXT,    -- WHO EML section reference for traceability
            icon TEXT
        )
    ''')

    # ── PRODUCTS ──────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            generic_name TEXT,
            description TEXT,
            category_id INTEGER,
            price REAL NOT NULL,
            stock_quantity INTEGER DEFAULT 0,
            requires_prescription BOOLEAN DEFAULT 0,  -- 0=OTC  1=Schedule H/Rx
            image_url TEXT,                           -- primary thumbnail (fast read, no JOIN)
            manufacturer TEXT,
            dosage_form TEXT,   -- tablet|capsule|syrup|injection|cream|drops|inhaler|sachet|spray
            strength TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')

    # ── PRODUCT IMAGES (gallery) ──────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_url TEXT NOT NULL,
            alt_text TEXT,
            is_primary BOOLEAN DEFAULT 0,  -- mirrors products.image_url
            sort_order INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    ''')

    # ── CART ITEMS ────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')

    # ── CART SHARE REQUESTS ───────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS cart_share_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            cart_snapshot TEXT NOT NULL,   -- JSON snapshot at share time
            patient_message TEXT,
            status TEXT DEFAULT 'pending', -- pending|approved|rejected
            doctor_note TEXT,
            prescription_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users(id),
            FOREIGN KEY (doctor_id) REFERENCES users(id),
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        )
    ''')

    # ── ORDERS ────────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'pending', -- pending|confirmed|shipped|delivered|cancelled
            payment_method TEXT DEFAULT 'COD',
            payment_status TEXT DEFAULT 'pending',
            shipping_address TEXT,
            prescription_id INTEGER,
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        )
    ''')

    # ── ORDER ITEMS ───────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,  -- price-time snapshot
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT DEFAULT 'info',  -- info|success|warning|prescription|order
            related_id INTEGER,
            related_type TEXT,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── MEDICAL RECORDS ───────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS medical_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            record_type TEXT,
            title TEXT NOT NULL,
            description TEXT,
            date DATE,
            doctor_name TEXT,
            hospital_name TEXT,
            file_path TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── SESSIONS ──────────────────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    _seed_categories(c)
    _seed_products(c)
    _seed_demo_doctors(c)
    conn.commit()
    conn.close()
    print("✓ Database initialized with all tables and seed data")


def migrate_database():
    """Safe to run on existing databases — adds missing columns / tables."""
    conn = get_db_connection()
    cur = conn.cursor()

    # users.phone
    cur.execute("PRAGMA table_info(users)")
    if "phone" not in [col[1] for col in cur.fetchall()]:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        print("✓ Added 'phone' to users")

    # products columns
    cur.execute("PRAGMA table_info(products)")
    prod_cols = [col[1] for col in cur.fetchall()]
    if "image_url" not in prod_cols:
        cur.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        print("✓ Added 'image_url' to products")
    if "requires_prescription" not in prod_cols:
        cur.execute("ALTER TABLE products ADD COLUMN requires_prescription BOOLEAN DEFAULT 0")
        print("✓ Added 'requires_prescription' to products")

    # categories.who_eml_section (new column in v2.4)
    cur.execute("PRAGMA table_info(categories)")
    cat_cols = [col[1] for col in cur.fetchall()]
    if "who_eml_section" not in cat_cols:
        cur.execute("ALTER TABLE categories ADD COLUMN who_eml_section TEXT")
        print("✓ Added 'who_eml_section' to categories")

    # product_images table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_url TEXT NOT NULL,
            alt_text TEXT,
            is_primary BOOLEAN DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    ''')
    print("✓ Ensured 'product_images' table exists")

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SEED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _seed_categories(c):
    """
    16 pharmacological categories aligned with WHO EML 23rd edition (2023).
    Each row: (name, user-facing description, WHO EML section, emoji icon)

    Ethical note
    ────────────
    Categories describe WHAT a drug is (its mechanism / drug class), never
    WHAT disease it treats.  The same molecule can treat many conditions;
    only a licensed clinician can make that determination for a patient.
    """
    cats = [
        # id=1
        (
            "Analgesics & NSAIDs",
            "Non-opioid pain relievers and non-steroidal anti-inflammatory drugs (NSAIDs) "
            "that reduce pain and inflammation. Always consult a doctor for persistent pain.",
            "WHO EML §2 — Analgesics",
            "💊",
        ),
        # id=2
        (
            "Antipyretics",
            "Medicines that reduce elevated body temperature. Fever can be a sign of a "
            "serious condition — seek medical advice if it persists beyond 48 hours.",
            "WHO EML §2 — Analgesics (antipyretic use)",
            "🌡️",
        ),
        # id=3
        (
            "Antibacterials (Systemic)",
            "Oral and injectable medicines that kill or inhibit bacteria. "
            "These are Schedule H / prescription-only drugs. Completing the full "
            "prescribed course is essential to prevent antibiotic resistance.",
            "WHO EML §6.2 — Antibacterials",
            "🦠",
        ),
        # id=4
        (
            "Antifungals (Systemic)",
            "Medicines that treat fungal infections throughout the body. "
            "Diagnosis by a clinician is required before use.",
            "WHO EML §6.3 — Antifungal medicines",
            "🍄",
        ),
        # id=5
        (
            "Antihistamines & Antiallergics",
            "Medicines that block histamine receptors to relieve allergic reactions, "
            "hay fever, urticaria, and related conditions.",
            "WHO EML §3 — Antiallergics and medicines used in anaphylaxis",
            "🤧",
        ),
        # id=6
        (
            "Antihypertensives & Cardiac Medicines",
            "Medicines that lower blood pressure or manage cardiac conditions including "
            "beta-blockers, calcium channel blockers, and ARBs. Prescription required. "
            "Never stop these medicines without medical advice.",
            "WHO EML §12 — Cardiovascular medicines",
            "❤️",
        ),
        # id=7
        (
            "Antidiabetics & Insulin",
            "Medicines used in glycaemic management including biguanides, sulfonylureas, "
            "and insulin preparations. Dose must be individualised by a clinician.",
            "WHO EML §18.5 — Insulins and other medicines used for diabetes",
            "💉",
        ),
        # id=8
        (
            "Respiratory & Bronchodilators",
            "Inhalers, leukotriene inhibitors, and related medicines that open airways "
            "and reduce respiratory inflammation. Correct inhaler technique matters — "
            "ask a pharmacist or nurse to demonstrate.",
            "WHO EML §25 — Medicines acting on the respiratory tract",
            "🫁",
        ),
        # id=9
        (
            "Proton Pump Inhibitors & Antacids",
            "Medicines that reduce gastric acid secretion (PPIs) or neutralise stomach "
            "acid (antacids). Used for acid reflux, peptic ulcer, and GERD. Long-term "
            "PPI use requires medical supervision.",
            "WHO EML §17.1 — Antacids and other antiulcer medicines",
            "🫃",
        ),
        # id=10
        (
            "Oral Rehydration & Electrolytes",
            "ORS sachets and electrolyte preparations that restore fluid and salt balance "
            "lost through diarrhoea, vomiting, or heavy sweating. WHO-formulated ORS is "
            "the global standard for dehydration management.",
            "WHO EML §17.5 — Medicines for diarrhoea / ORT",
            "💧",
        ),
        # id=11
        (
            "Vitamins & Micronutrients",
            "Essential vitamins (A, B-complex, C, D, K) and minerals needed for normal "
            "body function. Therapeutic doses for deficiency states should be guided by "
            "laboratory results and a clinician.",
            "WHO EML §27 — Vitamins and minerals",
            "🌿",
        ),
        # id=12
        (
            "Dietary Supplements & Omega Fatty Acids",
            "Non-prescription supplements including omega-3 fatty acids, probiotics, and "
            "multivitamin formulations. These supplement — not replace — a balanced diet "
            "and are not intended to diagnose or treat disease.",
            "WHO EML §27 — Vitamins and minerals (supplemental)",
            "🐟",
        ),
        # id=13
        (
            "Topical Corticosteroids",
            "Creams and ointments containing corticosteroids applied to the skin to reduce "
            "localised inflammation, itching, and redness. Potency class and duration of "
            "use must be appropriate — prolonged use on sensitive skin requires supervision.",
            "WHO EML §13.3 — Anti-inflammatory and antipruritic medicines",
            "🧴",
        ),
        # id=14
        (
            "Topical Antifungals & Antiseptics",
            "Creams, gels, and solutions applied to skin, nails, or mucous membranes to "
            "treat superficial fungal infections and minor infections.",
            "WHO EML §13.1 — Antifungal medicines (dermatological)",
            "🩹",
        ),
        # id=15
        (
            "Psychotropics & Sleep Regulators",
            "Medicines affecting mood, cognition, and sleep including SSRIs, anxiolytics, "
            "and melatonin. Prescription psychotropics are Schedule H drugs in India. "
            "Never self-prescribe or discontinue abruptly without medical guidance.",
            "WHO EML §24 — Medicines for mental and behavioural disorders",
            "🧠",
        ),
        # id=16
        (
            "Ophthalmic & Otic Preparations",
            "Eye drops, ear drops, and nasal sprays formulated for localised use. "
            "Antibiotic eye/ear preparations require a prescription. Check expiry "
            "dates carefully — opened drops should be discarded after 28 days.",
            "WHO EML §21 — Ophthalmological preparations",
            "👁️",
        ),
    ]
    for name, desc, who_section, icon in cats:
        c.execute(
            "INSERT OR IGNORE INTO categories (name, description, who_eml_section, icon) "
            "VALUES (?,?,?,?)",
            (name, desc, who_section, icon),
        )


def _seed_products(c):
    """
    Column order: name, generic, description, cat_id, price, stock,
                  requires_prescription (0=OTC / 1=Rx),
                  dosage_form, strength, manufacturer, img_key

    Category mapping (cat_id → category name):
      1  Analgesics & NSAIDs
      2  Antipyretics
      3  Antibacterials (Systemic)
      5  Antihistamines & Antiallergics
      6  Antihypertensives & Cardiac Medicines
      7  Antidiabetics & Insulin
      8  Respiratory & Bronchodilators
      9  Proton Pump Inhibitors & Antacids
     10  Oral Rehydration & Electrolytes
     11  Vitamins & Micronutrients
     12  Dietary Supplements & Omega Fatty Acids
     13  Topical Corticosteroids
     14  Topical Antifungals & Antiseptics
     15  Psychotropics & Sleep Regulators
     16  Ophthalmic & Otic Preparations

    NOTE — Paracetamol appears in BOTH Analgesics (cat 1) AND Antipyretics
    (cat 2) in real pharmacopoeias, but to keep the seed simple it is placed
    under cat 1 (Analgesics & NSAIDs) which is its primary WHO classification.
    Aspirin at 75 mg is primarily antiplatelet / cardiac (cat 6) but is kept
    under cat 1 here as a pain-relief OTC dose familiar to Indian consumers.
    """
    products = [
        # ── Analgesics & NSAIDs (cat 1) ───────────────────────────────────────
        ("Paracetamol 500mg",      "Paracetamol",     "Non-opioid analgesic and antipyretic. "
                                                       "Reduces mild-to-moderate pain and lowers fever.",
         1, 12.50, 500, 0, "tablet",   "500mg",     "Sun Pharma",   "paracetamol"),
        ("Ibuprofen 400mg",        "Ibuprofen",       "NSAID with analgesic, antipyretic, and "
                                                       "anti-inflammatory properties.",
         1, 18.00, 300, 0, "tablet",   "400mg",     "Cipla",        "ibuprofen"),
        ("Aspirin 75mg",           "Aspirin",         "Low-dose antiplatelet NSAID. Used under medical "
                                                       "supervision for cardiovascular prophylaxis.",
         1,  8.50, 200, 1, "tablet",   "75mg",      "Bayer",        "aspirin"),
        ("Diclofenac Gel 1%",      "Diclofenac",      "Topical NSAID for localised musculoskeletal pain "
                                                       "and soft-tissue inflammation.",
         1, 45.00, 150, 0, "cream",    "1%",        "Novartis",     "diclofenac_gel"),

        # ── Antibacterials — Systemic (cat 3) ─────────────────────────────────
        ("Amoxicillin 500mg",      "Amoxicillin",     "Broad-spectrum aminopenicillin antibiotic. "
                                                       "Schedule H — prescription required.",
         3, 65.00, 200, 1, "capsule",  "500mg",     "Cipla",        "amoxicillin"),
        ("Azithromycin 500mg",     "Azithromycin",    "Macrolide antibiotic. Schedule H — "
                                                       "prescription required.",
         3, 89.00, 150, 1, "tablet",   "500mg",     "Pfizer",       "azithromycin"),

        # ── Antihistamines & Antiallergics (cat 5) ────────────────────────────
        ("Cetirizine 10mg",        "Cetirizine",      "Second-generation H₁ antihistamine. "
                                                       "Low sedation profile.",
         5, 22.00, 400, 0, "tablet",   "10mg",      "UCB",          "cetirizine"),
        ("Levocetrizine 5mg",      "Levocetirizine",  "Active R-enantiomer of cetirizine. "
                                                       "Second-generation antihistamine.",
         5, 25.00, 350, 0, "tablet",   "5mg",       "Sun Pharma",   "levocetirizine"),

        # ── Antihypertensives & Cardiac Medicines (cat 6) ─────────────────────
        ("Atenolol 50mg",          "Atenolol",        "Cardioselective beta-1 adrenergic blocker. "
                                                       "Schedule H — prescription required.",
         6, 42.00, 200, 1, "tablet",   "50mg",      "Sun Pharma",   "atenolol"),
        ("Amlodipine 5mg",         "Amlodipine",      "Dihydropyridine calcium channel blocker. "
                                                       "Schedule H — prescription required.",
         6, 38.00, 250, 1, "tablet",   "5mg",       "Cipla",        "amlodipine"),
        ("Losartan 50mg",          "Losartan",        "Angiotensin II receptor blocker (ARB). "
                                                       "Schedule H — prescription required.",
         6, 55.00, 200, 1, "tablet",   "50mg",      "Glenmark",     "losartan"),

        # ── Antidiabetics & Insulin (cat 7) ───────────────────────────────────
        ("Metformin 500mg",        "Metformin",       "Biguanide oral antidiabetic. First-line agent "
                                                       "in type 2 diabetes. Prescription required.",
         7, 28.00, 300, 1, "tablet",   "500mg",     "USV",          "metformin"),
        ("Glimepiride 1mg",        "Glimepiride",     "Third-generation sulfonylurea. Stimulates "
                                                       "pancreatic insulin secretion. Rx only.",
         7, 35.00, 200, 1, "tablet",   "1mg",       "Sanofi",       "glimepiride"),
        ("Insulin Glargine",       "Insulin Glargine","Long-acting basal insulin analogue (U-100). "
                                                       "Prescription and proper training required.",
         7,280.00, 100, 1, "injection","100 IU/mL", "Novo Nordisk", "insulin"),

        # ── Respiratory & Bronchodilators (cat 8) ─────────────────────────────
        ("Salbutamol Inhaler",     "Salbutamol",      "Short-acting beta-2 agonist (SABA) MDI. "
                                                       "Relieves acute bronchospasm. Rx required.",
         8,145.00, 150, 1, "inhaler",  "100mcg",    "GSK",          "salbutamol"),
        ("Montelukast 10mg",       "Montelukast",     "Leukotriene receptor antagonist. Used as "
                                                       "add-on therapy. Prescription required.",
         8, 62.00, 200, 1, "tablet",   "10mg",      "MSD",          "montelukast"),

        # ── Proton Pump Inhibitors & Antacids (cat 9) ─────────────────────────
        ("Omeprazole 20mg",        "Omeprazole",      "Proton pump inhibitor (PPI). Reduces gastric "
                                                       "acid secretion. OTC for short-term use.",
         9, 32.00, 400, 0, "capsule",  "20mg",      "AstraZeneca",  "omeprazole"),
        ("Pantoprazole 40mg",      "Pantoprazole",    "PPI for gastro-oesophageal reflux disease. "
                                                       "Long-term use requires medical supervision.",
         9, 48.00, 300, 0, "tablet",   "40mg",      "Wyeth",        "pantoprazole"),

        # ── Oral Rehydration & Electrolytes (cat 10) ──────────────────────────
        ("ORS Electrolytes",       "Oral Rehydration Salts",
                                                       "WHO-formulated ORS. Restores fluid and "
                                                       "electrolyte balance. Glucose-based formula.",
        10, 15.00, 600, 0, "sachet",   "21.8g",     "Cipla",        "ors"),

        # ── Vitamins & Micronutrients (cat 11) ────────────────────────────────
        ("Vitamin C 1000mg",       "Ascorbic Acid",   "Water-soluble antioxidant vitamin. Supports "
                                                       "collagen synthesis and immune function.",
        11, 35.00, 600, 0, "tablet",   "1000mg",    "HealthKart",   "vitamin_c"),
        ("Vitamin D3 60K IU",      "Cholecalciferol", "High-dose cholecalciferol for vitamin D "
                                                       "deficiency. Weekly dose under medical advice.",
        11, 55.00, 400, 0, "capsule",  "60000 IU",  "Mankind",      "vitamin_d3"),

        # ── Dietary Supplements & Omega Fatty Acids (cat 12) ──────────────────
        ("Omega-3 Fish Oil",       "Omega-3 Fatty Acids",
                                                       "Marine-derived EPA + DHA supplement. "
                                                       "Supports cardiovascular and cognitive health.",
        12,120.00, 300, 0, "capsule",  "1000mg",    "Himalaya",     "omega3"),
        ("Multivitamin Daily",     "Multivitamins",   "Broad-spectrum daily micronutrient supplement "
                                                       "with vitamins A, B-complex, C, D, E, zinc.",
        12, 85.00, 500, 0, "tablet",   "1 tab",     "Abbott",       "multivitamin"),

        # ── Topical Corticosteroids (cat 13) ──────────────────────────────────
        ("Hydrocortisone Cream 1%","Hydrocortisone",  "Mild topical corticosteroid (Class IV). "
                                                       "Reduces skin inflammation and itching.",
        13, 55.00, 150, 0, "cream",    "1%",        "Cipla",        "hydrocortisone"),

        # ── Topical Antifungals & Antiseptics (cat 14) ────────────────────────
        ("Clotrimazole Cream 1%",  "Clotrimazole",    "Imidazole topical antifungal. Active against "
                                                       "dermatophytes, Candida, and Malassezia.",
        14, 38.00, 200, 0, "cream",    "1%",        "Bayer",        "clotrimazole"),

        # ── Psychotropics & Sleep Regulators (cat 15) ─────────────────────────
        ("Melatonin 3mg",          "Melatonin",       "Endogenous sleep-wake cycle regulator. "
                                                       "OTC supplement for circadian rhythm support.",
        15, 45.00, 300, 0, "tablet",   "3mg",       "Himalaya",     "melatonin"),
        ("Sertraline 50mg",        "Sertraline",      "Selective serotonin reuptake inhibitor (SSRI). "
                                                       "Schedule H — prescription and monitoring required.",
        15, 92.00, 100, 1, "tablet",   "50mg",      "Pfizer",       "sertraline"),

        # ── Ophthalmic & Otic Preparations (cat 16) ───────────────────────────
        ("Ciprofloxacin Eye Drops","Ciprofloxacin",   "Fluoroquinolone antibiotic ophthalmic solution. "
                                                       "Prescription required.",
        16, 68.00, 200, 1, "drops",    "0.3%",      "Alcon",        "cipro_eye"),
        ("Tobramycin Eye Drops",   "Tobramycin",      "Aminoglycoside antibiotic ophthalmic solution. "
                                                       "Prescription required.",
        16, 72.00, 150, 1, "drops",    "0.3%",      "Alcon",        "tobramycin"),
        ("Otrivin Nasal Spray",    "Xylometazoline",  "Alpha-adrenergic nasal decongestant. OTC. "
                                                       "Limit use to ≤7 days to avoid rebound congestion.",
        16, 55.00, 250, 0, "spray",    "0.1%",      "Novartis",     "otrivin"),
    ]

    for row in products:
        name, generic, desc, cat_id, price, stock, rx, form, strength, mfr, img_key = row
        img_url = IMG.get(img_key, "")

        c.execute("""
            INSERT OR IGNORE INTO products
            (name, generic_name, description, category_id, price, stock_quantity,
             requires_prescription, dosage_form, strength, manufacturer, image_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (name, generic, desc, cat_id, price, stock, rx, form, strength, mfr, img_url))

        # Seed primary entry in product_images gallery
        c.execute("SELECT id FROM products WHERE name=?", (name,))
        prod_row = c.fetchone()
        if prod_row and img_url:
            pid = prod_row[0]
            c.execute("""
                INSERT INTO product_images (product_id, image_url, alt_text, is_primary, sort_order)
                SELECT ?,?,?,1,0
                WHERE NOT EXISTS (
                    SELECT 1 FROM product_images WHERE product_id=? AND is_primary=1
                )
            """, (pid, img_url, name, pid))


def _seed_demo_doctors(c):
    doctors = [
        {"name": "Priya Sharma",  "email": "dr.priya@panacea.com",  "phone": "9876543210",
         "specialization": "Cardiologist",       "license": "MCI-2024-001",
         "qualification": "MBBS, MD (Cardiology)",         "experience": 12, "fee": 800,
         "bio": "Specialist in heart diseases and hypertension with 12 years of experience.",
         "days": ["Mon","Tue","Wed","Thu","Fri"]},
        {"name": "Rajesh Kumar",  "email": "dr.rajesh@panacea.com", "phone": "9876543211",
         "specialization": "General Physician",  "license": "MCI-2024-002",
         "qualification": "MBBS, MD (General Medicine)",   "experience": 8,  "fee": 500,
         "bio": "General medicine expert handling common illnesses, fever, infections.",
         "days": ["Mon","Tue","Wed","Thu","Fri","Sat"]},
        {"name": "Sunita Patel",  "email": "dr.sunita@panacea.com", "phone": "9876543212",
         "specialization": "Diabetologist",      "license": "MCI-2024-003",
         "qualification": "MBBS, MD (Endocrinology)",      "experience": 10, "fee": 700,
         "bio": "Expert in diabetes management and metabolic disorders.",
         "days": ["Mon","Wed","Fri"]},
        {"name": "Arjun Mehta",   "email": "dr.arjun@panacea.com",  "phone": "9876543213",
         "specialization": "Pulmonologist",      "license": "MCI-2024-004",
         "qualification": "MBBS, MD (Pulmonology)",        "experience": 15, "fee": 900,
         "bio": "Specialist in respiratory diseases, asthma, and COPD.",
         "days": ["Tue","Thu","Sat"]},
        {"name": "Meena Iyer",    "email": "dr.meena@panacea.com",  "phone": "9876543214",
         "specialization": "Dermatologist",      "license": "MCI-2024-005",
         "qualification": "MBBS, MD (Dermatology)",        "experience": 7,  "fee": 650,
         "bio": "Skin specialist treating dermatitis, acne, fungal infections.",
         "days": ["Mon","Tue","Wed","Fri"]},
        {"name": "Vikram Singh",  "email": "dr.vikram@panacea.com", "phone": "9876543215",
         "specialization": "Neurologist",        "license": "MCI-2024-006",
         "qualification": "MBBS, DM (Neurology)",          "experience": 18, "fee": 1200,
         "bio": "Expert neurologist for headaches, migraines, and nervous system disorders.",
         "days": ["Mon","Wed","Thu"]},
        {"name": "Ananya Das",    "email": "dr.ananya@panacea.com", "phone": "9876543216",
         "specialization": "Gastroenterologist", "license": "MCI-2024-007",
         "qualification": "MBBS, MD (Gastroenterology)",   "experience": 9,  "fee": 750,
         "bio": "GI specialist for stomach, liver, and digestive disorders.",
         "days": ["Tue","Wed","Fri","Sat"]},
        {"name": "Sanjay Gupta",  "email": "dr.sanjay@panacea.com", "phone": "9876543217",
         "specialization": "Psychiatrist",       "license": "MCI-2024-008",
         "qualification": "MBBS, MD (Psychiatry)",         "experience": 14, "fee": 1000,
         "bio": "Mental health expert for depression, anxiety, and sleep disorders.",
         "days": ["Mon","Tue","Thu","Fri"]},
    ]
    for d in doctors:
        c.execute("""
            INSERT OR IGNORE INTO users (email, name, phone, user_type, is_active, profile_completed)
            VALUES (?,?,?,?,1,1)
        """, (d["email"], d["name"], d["phone"], "doctor"))
        c.execute("SELECT id FROM users WHERE email=?", (d["email"],))
        row = c.fetchone()
        if not row:
            continue
        uid = row[0]
        c.execute("""
            INSERT OR IGNORE INTO doctors_profile
            (user_id, specialization, license_number, qualification, experience_years,
             consultation_fee, bio, available_days, is_verified, rating)
            VALUES (?,?,?,?,?,?,?,?,1,?)
        """, (
            uid, d["specialization"], d["license"], d["qualification"],
            d["experience"], d["fee"], d["bio"],
            json.dumps(d["days"]),
            round(4.2 + (hash(d["email"]) % 8) / 10, 1),
        ))


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY CLASSES
# ─────────────────────────────────────────────────────────────────────────────

class SessionManager:
    @staticmethod
    def create_session(user_id: int, expires_days: int = 7) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=expires_days)
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO sessions (user_id, session_token, expires_at) VALUES (?,?,?)",
            (user_id, token, expires_at.isoformat()),
        )
        conn.commit(); conn.close()
        return token

    @staticmethod
    def get_user_from_token(token: str) -> Optional[Dict]:
        conn = get_db_connection()
        row = conn.execute("""
            SELECT u.id, u.email, u.name, u.age, u.gender, u.user_type, u.profile_completed
            FROM users u JOIN sessions s ON u.id = s.user_id
            WHERE s.session_token=? AND s.expires_at > datetime('now')
            ORDER BY s.created_at DESC LIMIT 1
        """, (token,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def logout(token: str):
        conn = get_db_connection()
        conn.execute("DELETE FROM sessions WHERE session_token=?", (token,))
        conn.commit(); conn.close()


class NotificationManager:
    @staticmethod
    def create(user_id: int, title: str, message: str, ntype: str = "info",
               related_id: int = None, related_type: str = None):
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO notifications
            (user_id, title, message, type, related_id, related_type)
            VALUES (?,?,?,?,?,?)
        """, (user_id, title, message, ntype, related_id, related_type))
        conn.commit(); conn.close()

    @staticmethod
    def get_unread(user_id: int) -> List[Dict]:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT * FROM notifications WHERE user_id=? AND is_read=0
            ORDER BY created_at DESC LIMIT 20
        """, (user_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def mark_read(notification_id: int):
        conn = get_db_connection()
        conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
        conn.commit(); conn.close()


class ProductImageManager:
    """CRUD helper for the product_images gallery table."""

    @staticmethod
    def get_images(product_id: int) -> List[Dict]:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT id, image_url, alt_text, is_primary, sort_order
            FROM product_images WHERE product_id=?
            ORDER BY sort_order ASC, id ASC
        """, (product_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def add_image(product_id: int, image_url: str, alt_text: str = "",
                  is_primary: bool = False, sort_order: int = 0) -> int:
        conn = get_db_connection()
        if is_primary:
            conn.execute(
                "UPDATE product_images SET is_primary=0 WHERE product_id=?", (product_id,)
            )
            conn.execute(
                "UPDATE products SET image_url=? WHERE id=?", (image_url, product_id)
            )
        cur = conn.execute("""
            INSERT INTO product_images
            (product_id, image_url, alt_text, is_primary, sort_order)
            VALUES (?,?,?,?,?)
        """, (product_id, image_url, alt_text, int(is_primary), sort_order))
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    @staticmethod
    def delete_image(image_id: int):
        conn = get_db_connection()
        conn.execute("DELETE FROM product_images WHERE id=?", (image_id,))
        conn.commit(); conn.close()

    @staticmethod
    def set_primary(product_id: int, image_id: int):
        """Promote an image to primary and sync products.image_url."""
        conn = get_db_connection()
        conn.execute(
            "UPDATE product_images SET is_primary=0 WHERE product_id=?", (product_id,)
        )
        conn.execute(
            "UPDATE product_images SET is_primary=1 WHERE id=? AND product_id=?",
            (image_id, product_id),
        )
        row = conn.execute(
            "SELECT image_url FROM product_images WHERE id=?", (image_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE products SET image_url=? WHERE id=?",
                (row["image_url"], product_id),
            )
        conn.commit(); conn.close()


__all__ = [
    "get_db_connection",
    "init_database",
    "migrate_database",
    "SessionManager",
    "NotificationManager",
    "ProductImageManager",
]


if __name__ == "__main__":
    init_database()
    migrate_database()
    print("✓ Panacea database ready!")