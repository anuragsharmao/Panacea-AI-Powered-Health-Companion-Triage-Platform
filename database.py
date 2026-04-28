"""
Panacea — Complete Database Layer v2.0
=======================================
Includes:
  - users (customers, doctors, admins)
  - doctors_profile (specialization, license, verification)
  - categories, products (medicines)
  - cart_items, orders, order_items
  - digital_prescriptions (doctor → patient)
  - prescription_items (medicines in prescription)
  - consultation_requests (patient selects doctor after screening)
  - notifications (in-app alerts)
  - assessments, medical_records, health_metrics, sessions
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from pathlib import Path
import secrets

DB_PATH = Path(__file__).parent / "database" / "panacea.db"
DB_PATH.parent.mkdir(exist_ok=True)


def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    conn = get_db_connection()
    c = conn.cursor()

    # ── USERS ──────────────────────────────────────────────────
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

    # ── DOCTOR PROFILES ────────────────────────────────────────
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
            available_days TEXT,       -- JSON: ["Mon","Tue","Wed"]
            available_hours TEXT,      -- JSON: {"start":"09:00","end":"17:00"}
            is_verified BOOLEAN DEFAULT 0,
            rating REAL DEFAULT 0,
            total_consultations INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── HEALTH METRICS ─────────────────────────────────────────
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
            allergies TEXT,            -- JSON array
            chronic_conditions TEXT,   -- JSON array
            current_medications TEXT,  -- JSON array
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── SYMPTOM ASSESSMENTS ────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symptoms TEXT NOT NULL,     -- JSON array
            age_group TEXT,
            gender TEXT,
            predictions TEXT,           -- JSON array
            top_disease TEXT,
            confidence REAL,
            suggested_specialist TEXT,  -- e.g. "Cardiologist"
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── CONSULTATION REQUESTS ──────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS consultation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            assessment_id INTEGER,       -- which screening triggered this
            symptoms_summary TEXT,       -- brief description
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

    # ── DIGITAL PRESCRIPTIONS ──────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            consultation_id INTEGER,
            cart_request_id INTEGER,     -- if triggered by cart share
            notes TEXT,
            status TEXT DEFAULT 'active', -- active|used|expired|rejected
            valid_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doctor_id) REFERENCES users(id),
            FOREIGN KEY (patient_id) REFERENCES users(id),
            FOREIGN KEY (consultation_id) REFERENCES consultation_requests(id)
        )
    ''')

    # ── PRESCRIPTION ITEMS ─────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS prescription_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prescription_id INTEGER NOT NULL,
            product_id INTEGER,
            medicine_name TEXT NOT NULL,  -- snapshot
            dosage TEXT,                  -- e.g. "1 tablet twice daily"
            duration TEXT,               -- e.g. "7 days"
            quantity INTEGER DEFAULT 1,
            instructions TEXT,
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id) ON DELETE CASCADE
        )
    ''')

    # ── MEDICINE CATEGORIES ────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            icon TEXT
        )
    ''')

    # ── PRODUCTS (MEDICINES) ───────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            generic_name TEXT,
            description TEXT,
            category_id INTEGER,
            price REAL NOT NULL,
            stock_quantity INTEGER DEFAULT 0,
            requires_prescription BOOLEAN DEFAULT 0,
            image_url TEXT,
            manufacturer TEXT,
            dosage_form TEXT,     -- tablet|syrup|injection|cream|drops
            strength TEXT,        -- e.g. "500mg"
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')

    # ── CART ITEMS ─────────────────────────────────────────────
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

    # ── CART SHARE REQUESTS ────────────────────────────────────
    # When user wants to buy prescription medicine → shares cart with doctor
    c.execute('''
        CREATE TABLE IF NOT EXISTS cart_share_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            cart_snapshot TEXT NOT NULL,   -- JSON of cart items at share time
            patient_message TEXT,
            status TEXT DEFAULT 'pending', -- pending|approved|rejected
            doctor_note TEXT,
            prescription_id INTEGER,       -- set when doctor approves
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users(id),
            FOREIGN KEY (doctor_id) REFERENCES users(id),
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        )
    ''')

    # ── ORDERS ─────────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',  -- pending|confirmed|shipped|delivered|cancelled
            payment_method TEXT DEFAULT 'COD',
            payment_status TEXT DEFAULT 'pending',
            shipping_address TEXT,
            prescription_id INTEGER,        -- if order used a prescription
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        )
    ''')

    # ── ORDER ITEMS ────────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,   -- snapshot
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,           -- price at purchase time
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')

    # ── NOTIFICATIONS ──────────────────────────────────────────
    c.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            type TEXT DEFAULT 'info',     -- info|success|warning|prescription|order
            related_id INTEGER,           -- ID of related entity
            related_type TEXT,            -- prescription|order|consultation|cart_share
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')

    # ── MEDICAL RECORDS ────────────────────────────────────────
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

    # ── SESSIONS ───────────────────────────────────────────────
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

    # ── SEED DATA ───────────────────────────────────────────────
    _seed_categories(c)
    _seed_products(c)
    _seed_demo_doctors(c)

    conn.commit()
    conn.close()
    print("✓ Database initialized with all tables and seed data")


def _seed_categories(c):
    cats = [
        ("Pain Relief", "Analgesics and anti-inflammatory medicines", "💊"),
        ("Antibiotics", "Antibacterial and antimicrobial medicines", "🦠"),
        ("Vitamins & Supplements", "Nutritional supplements and vitamins", "🌿"),
        ("Cardiovascular", "Heart and blood pressure medicines", "❤️"),
        ("Diabetes Care", "Blood sugar management medicines", "💉"),
        ("Respiratory", "Medicines for breathing and lungs", "🫁"),
        ("Gastro", "Digestive system medicines", "🫃"),
        ("Skin Care", "Topical and dermatological medicines", "🧴"),
        ("Mental Health", "Psychiatric and neurological medicines", "🧠"),
        ("Eye & Ear", "Ophthalmological and ear medicines", "👁️"),
    ]
    for name, desc, icon in cats:
        c.execute("INSERT OR IGNORE INTO categories (name, description, icon) VALUES (?,?,?)",
                  (name, desc, icon))


def _seed_products(c):
    products = [
        # Pain Relief
        ("Paracetamol 500mg", "Paracetamol", "Common pain reliever and fever reducer", 1, 12.50, 500, 0, "tablet", "500mg", "Sun Pharma"),
        ("Ibuprofen 400mg", "Ibuprofen", "Anti-inflammatory pain reliever", 1, 18.00, 300, 0, "tablet", "400mg", "Cipla"),
        ("Aspirin 75mg", "Aspirin", "Low-dose aspirin for blood thinning", 1, 8.50, 200, 1, "tablet", "75mg", "Bayer"),
        ("Diclofenac Gel", "Diclofenac", "Topical pain relief gel", 1, 45.00, 150, 0, "cream", "1%", "Novartis"),
        # Antibiotics
        ("Amoxicillin 500mg", "Amoxicillin", "Broad-spectrum antibiotic", 2, 65.00, 200, 1, "capsule", "500mg", "Cipla"),
        ("Azithromycin 500mg", "Azithromycin", "Macrolide antibiotic for infections", 2, 89.00, 150, 1, "tablet", "500mg", "Pfizer"),
        ("Cetirizine 10mg", "Cetirizine", "Antihistamine for allergies", 2, 22.00, 400, 0, "tablet", "10mg", "UCB"),
        # Vitamins
        ("Vitamin C 1000mg", "Ascorbic Acid", "Immune system booster", 3, 35.00, 600, 0, "tablet", "1000mg", "HealthKart"),
        ("Vitamin D3 60K IU", "Cholecalciferol", "Bone health and immunity", 3, 55.00, 400, 0, "capsule", "60000 IU", "Mankind"),
        ("Omega-3 Fish Oil", "Omega-3 Fatty Acids", "Heart and brain health", 3, 120.00, 300, 0, "capsule", "1000mg", "Himalaya"),
        ("Multivitamin Daily", "Multivitamins", "Complete daily nutritional supplement", 3, 85.00, 500, 0, "tablet", "1 tab", "Abbott"),
        # Cardiovascular
        ("Atenolol 50mg", "Atenolol", "Beta blocker for blood pressure", 4, 42.00, 200, 1, "tablet", "50mg", "Sun Pharma"),
        ("Amlodipine 5mg", "Amlodipine", "Calcium channel blocker", 4, 38.00, 250, 1, "tablet", "5mg", "Cipla"),
        ("Losartan 50mg", "Losartan", "ARB for hypertension", 4, 55.00, 200, 1, "tablet", "50mg", "Glenmark"),
        # Diabetes
        ("Metformin 500mg", "Metformin", "First-line diabetes medicine", 5, 28.00, 300, 1, "tablet", "500mg", "USV"),
        ("Glimepiride 1mg", "Glimepiride", "Sulfonylurea for blood sugar", 5, 35.00, 200, 1, "tablet", "1mg", "Sanofi"),
        ("Insulin Glargine", "Insulin", "Long-acting insulin", 5, 280.00, 100, 1, "injection", "100 IU/mL", "Novo Nordisk"),
        # Respiratory
        ("Salbutamol Inhaler", "Salbutamol", "Bronchodilator for asthma", 6, 145.00, 150, 1, "inhaler", "100mcg", "GSK"),
        ("Montelukast 10mg", "Montelukast", "Leukotriene inhibitor for asthma", 6, 62.00, 200, 1, "tablet", "10mg", "MSD"),
        ("Levocetrizine 5mg", "Levocetirizine", "Antihistamine for allergic rhinitis", 6, 25.00, 350, 0, "tablet", "5mg", "Sun Pharma"),
        # Gastro
        ("Omeprazole 20mg", "Omeprazole", "Proton pump inhibitor for acidity", 7, 32.00, 400, 0, "capsule", "20mg", "AstraZeneca"),
        ("ORS Electrolytes", "Oral Rehydration Salts", "Rehydration for diarrhea", 7, 15.00, 600, 0, "sachet", "21.8g", "Cipla"),
        ("Pantoprazole 40mg", "Pantoprazole", "Acid reducer for GERD", 7, 48.00, 300, 0, "tablet", "40mg", "Wyeth"),
        # Skin Care
        ("Clotrimazole Cream", "Clotrimazole", "Antifungal cream", 8, 38.00, 200, 0, "cream", "1%", "Bayer"),
        ("Hydrocortisone 1%", "Hydrocortisone", "Mild steroid for skin inflammation", 8, 55.00, 150, 0, "cream", "1%", "Cipla"),
        # Mental Health
        ("Melatonin 3mg", "Melatonin", "Sleep aid supplement", 9, 45.00, 300, 0, "tablet", "3mg", "Himalaya"),
        ("Sertraline 50mg", "Sertraline", "SSRI antidepressant", 9, 92.00, 100, 1, "tablet", "50mg", "Pfizer"),
        # Eye & Ear
        ("Ciprofloxacin Eye Drops", "Ciprofloxacin", "Antibiotic eye drops", 10, 68.00, 200, 1, "drops", "0.3%", "Alcon"),
        ("Tobramycin Eye Drops", "Tobramycin", "Antibiotic for eye infections", 10, 72.00, 150, 1, "drops", "0.3%", "Alcon"),
        ("Otrivin Nasal Spray", "Xylometazoline", "Nasal decongestant", 10, 55.00, 250, 0, "spray", "0.1%", "Novartis"),
    ]
    for p in products:
        name, generic, desc, cat_id, price, stock, rx, form, strength, mfr = p
        c.execute("""
            INSERT OR IGNORE INTO products
            (name, generic_name, description, category_id, price, stock_quantity,
             requires_prescription, dosage_form, strength, manufacturer)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name, generic, desc, cat_id, price, stock, rx, form, strength, mfr))


def _seed_demo_doctors(c):
    doctors = [
        {
            "name": "Dr. Priya Sharma", "email": "dr.priya@panacea.com",
            "specialization": "Cardiologist", "license": "MCI-2024-001",
            "qualification": "MBBS, MD (Cardiology)", "experience": 12,
            "fee": 800, "bio": "Specialist in heart diseases and hypertension with 12 years of experience.",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"]
        },
        {
            "name": "Dr. Rajesh Kumar", "email": "dr.rajesh@panacea.com",
            "specialization": "General Physician", "license": "MCI-2024-002",
            "qualification": "MBBS, MD (General Medicine)", "experience": 8,
            "fee": 500, "bio": "General medicine expert handling common illnesses, fever, infections.",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        },
        {
            "name": "Dr. Sunita Patel", "email": "dr.sunita@panacea.com",
            "specialization": "Diabetologist", "license": "MCI-2024-003",
            "qualification": "MBBS, MD (Endocrinology)", "experience": 10,
            "fee": 700, "bio": "Expert in diabetes management and metabolic disorders.",
            "days": ["Mon", "Wed", "Fri"]
        },
        {
            "name": "Dr. Arjun Mehta", "email": "dr.arjun@panacea.com",
            "specialization": "Pulmonologist", "license": "MCI-2024-004",
            "qualification": "MBBS, MD (Pulmonology)", "experience": 15,
            "fee": 900, "bio": "Specialist in respiratory diseases, asthma, and COPD.",
            "days": ["Tue", "Thu", "Sat"]
        },
        {
            "name": "Dr. Meena Iyer", "email": "dr.meena@panacea.com",
            "specialization": "Dermatologist", "license": "MCI-2024-005",
            "qualification": "MBBS, MD (Dermatology)", "experience": 7,
            "fee": 650, "bio": "Skin specialist treating dermatitis, acne, fungal infections.",
            "days": ["Mon", "Tue", "Wed", "Fri"]
        },
        {
            "name": "Dr. Vikram Singh", "email": "dr.vikram@panacea.com",
            "specialization": "Neurologist", "license": "MCI-2024-006",
            "qualification": "MBBS, DM (Neurology)", "experience": 18,
            "fee": 1200, "bio": "Expert neurologist for headaches, migraines, and nervous system disorders.",
            "days": ["Mon", "Wed", "Thu"]
        },
        {
            "name": "Dr. Ananya Das", "email": "dr.ananya@panacea.com",
            "specialization": "Gastroenterologist", "license": "MCI-2024-007",
            "qualification": "MBBS, MD (Gastroenterology)", "experience": 9,
            "fee": 750, "bio": "GI specialist for stomach, liver, and digestive disorders.",
            "days": ["Tue", "Wed", "Fri", "Sat"]
        },
        {
            "name": "Dr. Sanjay Gupta", "email": "dr.sanjay@panacea.com",
            "specialization": "Psychiatrist", "license": "MCI-2024-008",
            "qualification": "MBBS, MD (Psychiatry)", "experience": 14,
            "fee": 1000, "bio": "Mental health expert for depression, anxiety, and sleep disorders.",
            "days": ["Mon", "Tue", "Thu", "Fri"]
        },
    ]
    for d in doctors:
        # Insert user
        c.execute("""
            INSERT OR IGNORE INTO users (email, name, user_type, is_active, profile_completed)
            VALUES (?,?,?,1,1)
        """, (d["email"], d["name"], "doctor"))
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
            round(4.2 + (hash(d["email"]) % 8) / 10, 1)
        ))


# ──────────────────────────────────────────────────────────────
# UTILITY CLASSES
# ──────────────────────────────────────────────────────────────

class SessionManager:
    @staticmethod
    def create_session(user_id: int, expires_days: int = 7) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=expires_days)
        conn = get_db_connection()
        conn.execute("INSERT INTO sessions (user_id, session_token, expires_at) VALUES (?,?,?)",
                     (user_id, token, expires_at.isoformat()))
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
            INSERT INTO notifications (user_id, title, message, type, related_id, related_type)
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


if __name__ == "__main__":
    init_database()
    print("✓ Panacea database ready!")