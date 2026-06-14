# api.py - COMPLETE UPDATED VERSION WITH DISEASE DETAILS
"""
Panacea — Unified API Server (UPDATED)
=====================================
- Added disease details endpoint (description, primary_severity, secondary_severity, specialist)
- Integrated disease info into predictions
"""

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import joblib
import numpy as np
import warnings
from pathlib import Path
import json
from datetime import datetime, timedelta
import secrets
import sqlite3
import asyncio
import os
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor

# ============================================================
# AES-256 ENCRYPTION FOR SENSITIVE HEALTH DATA AT REST
# ============================================================
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    print("⚠  cryptography library not installed — run: pip install cryptography")
    print("   Health data encryption will be DISABLED until installed.")

# Derive a 256-bit key from PANACEA_SECRET_KEY env var (or a dev fallback).
# In production, always set PANACEA_SECRET_KEY to a long random string.
_RAW_SECRET = os.environ.get("PANACEA_SECRET_KEY", "panacea-dev-secret-change-in-production-2024!")
_AES_KEY = hashlib.sha256(_RAW_SECRET.encode()).digest()  # 32 bytes → AES-256

def encrypt_field(plaintext: str) -> str:
    """AES-256-GCM encrypt a string. Returns base64-encoded 'nonce:ciphertext'."""
    if not _CRYPTO_AVAILABLE or not plaintext:
        return plaintext
    try:
        aesgcm = AESGCM(_AES_KEY)
        nonce = os.urandom(12)   # 96-bit nonce (GCM standard)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()
    except Exception as e:
        print(f"Encryption error: {e}")
        return plaintext

def decrypt_field(ciphertext: str) -> str:
    """Decrypt a value produced by encrypt_field. Returns original plaintext."""
    if not _CRYPTO_AVAILABLE or not ciphertext:
        return ciphertext
    try:
        raw = base64.b64decode(ciphertext)
        nonce, ct = raw[:12], raw[12:]
        aesgcm = AESGCM(_AES_KEY)
        return aesgcm.decrypt(nonce, ct, None).decode()
    except Exception:
        # If decryption fails the value was stored as plaintext (pre-encryption migration)
        return ciphertext

def encrypt_sensitive_metrics(metrics: Dict) -> Dict:
    """Encrypt PII fields in a health metrics dict before persisting."""
    sensitive = [
        'allergies', 'chronic_conditions', 'current_medications',
        'emergency_contact_name', 'emergency_contact_phone'
    ]
    out = dict(metrics)
    for field in sensitive:
        if out.get(field) is not None:
            val = out[field]
            if isinstance(val, (list, dict)):
                val = json.dumps(val)
            out[field] = encrypt_field(str(val))
    return out

def decrypt_sensitive_metrics(metrics: Dict) -> Dict:
    """Decrypt PII fields after reading from DB."""
    sensitive = [
        'allergies', 'chronic_conditions', 'current_medications',
        'emergency_contact_name', 'emergency_contact_phone'
    ]
    out = dict(metrics)
    for field in sensitive:
        if out.get(field) is not None:
            decrypted = decrypt_field(out[field])
            try:
                out[field] = json.loads(decrypted)
            except Exception:
                out[field] = decrypted
    return out

# Thread pool for CPU-heavy blocking work
_THREAD_POOL = ThreadPoolExecutor(max_workers=2)

warnings.filterwarnings('ignore')

# ============================================================
# PATH CONFIGURATION
# ============================================================
CURRENT_DIR = Path(__file__).parent
PROJECT_ROOT = CURRENT_DIR
FRONTEND_DIR = PROJECT_ROOT / "frontend"
BACKEND_DIR = PROJECT_ROOT / "backend"
DATABASE_DIR = PROJECT_ROOT / "database"
MODEL_DIR = BACKEND_DIR / "ml_model"

# Create directories if they don't exist
DATABASE_DIR.mkdir(exist_ok=True)
BACKEND_DIR.mkdir(exist_ok=True)
FRONTEND_DIR.mkdir(exist_ok=True)

# ============================================================
# LOAD DISEASE DATABASE FROM CONVERTED JSON
# ============================================================
DISEASES_DB: Dict[str, Dict] = {}
DISEASES_LIST: List[Dict] = []

def load_diseases_database():
    """Load the converted diseases.json with descriptions, severities, and specialists"""
    global DISEASES_DB, DISEASES_LIST
    
    json_paths = [
        BACKEND_DIR / "diseases.json",
        PROJECT_ROOT / "diseases.json",
        PROJECT_ROOT / "backend" / "diseases.json",
        Path("diseases.json"),
    ]
    
    for json_path in json_paths:
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    DISEASES_LIST = json.load(f)
                    # Create lookup dictionary for quick access
                    for disease in DISEASES_LIST:
                        disease_name_lower = disease.get("disease_name", "").lower()
                        DISEASES_DB[disease_name_lower] = disease
                print(f"✓ Loaded disease database from {json_path} ({len(DISEASES_LIST)} diseases)")
                return True
            except Exception as e:
                print(f"Failed to load from {json_path}: {e}")
    
    print("⚠ No diseases.json found. Disease details will be unavailable.")
    print("   Please place the converted JSON file as 'diseases.json' in the backend folder.")
    return False

load_diseases_database()

def get_disease_info(disease_name: str) -> Optional[Dict]:
    """Get full disease information by name (case-insensitive)"""
    if not disease_name:
        return None
    return DISEASES_DB.get(disease_name.lower())

# ============================================================
# DATABASE SETUP (SQLite)
# ============================================================
DB_PATH = DATABASE_DIR / "panacea.db"

def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            age INTEGER,
            gender TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            profile_completed BOOLEAN DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            height_cm REAL,
            weight_kg REAL,
            blood_group TEXT,
            blood_pressure_systolic INTEGER,
            blood_pressure_diastolic INTEGER,
            heart_rate INTEGER,
            allergies TEXT,
            chronic_conditions TEXT,
            current_medications TEXT,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(user_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symptoms TEXT NOT NULL,
            age_group TEXT,
            gender TEXT,
            predictions TEXT,
            top_disease TEXT,
            confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('''
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
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    # Extended schema for pharmacy & consultation features
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            type TEXT DEFAULT 'info',
            reference_id INTEGER,
            reference_type TEXT,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            generic_name TEXT,
            description TEXT,
            category_id INTEGER,
            price REAL DEFAULT 0,
            stock_quantity INTEGER DEFAULT 0,
            requires_prescription BOOLEAN DEFAULT 0,
            image_url TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image_url TEXT NOT NULL,
            alt_text TEXT,
            is_primary BOOLEAN DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
            UNIQUE(user_id, product_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cart_share_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            cart_snapshot TEXT,
            patient_message TEXT,
            status TEXT DEFAULT 'pending',
            doctor_note TEXT,
            prescription_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users (id),
            FOREIGN KEY (doctor_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            cart_request_id INTEGER,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (doctor_id) REFERENCES users (id),
            FOREIGN KEY (patient_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prescription_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prescription_id INTEGER NOT NULL,
            product_id INTEGER,
            medicine_name TEXT,
            dosage TEXT,
            duration TEXT,
            quantity INTEGER DEFAULT 1,
            FOREIGN KEY (prescription_id) REFERENCES prescriptions (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            total_amount REAL DEFAULT 0,
            payment_method TEXT DEFAULT 'COD',
            shipping_address TEXT,
            prescription_id INTEGER,
            status TEXT DEFAULT 'pending',
            order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER,
            medicine_name TEXT,
            quantity INTEGER DEFAULT 1,
            price REAL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS doctors_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            specialization TEXT,
            qualification TEXT,
            experience_years INTEGER DEFAULT 0,
            consultation_fee REAL DEFAULT 0,
            bio TEXT,
            rating REAL DEFAULT 0,
            total_consultations INTEGER DEFAULT 0,
            available_days TEXT,
            is_verified BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS consultation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER NOT NULL,
            assessment_id INTEGER,
            symptoms_summary TEXT,
            predicted_disease TEXT,
            patient_message TEXT,
            doctor_response TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES users (id),
            FOREIGN KEY (doctor_id) REFERENCES users (id)
        )
    ''')

    # Add user_type and phone columns to users if they don't exist (migration)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN user_type TEXT DEFAULT 'patient'")
    except Exception:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()
    print("✓ Database initialized successfully")

init_database()


# ============================================================
# NOTIFICATION MANAGER
# ============================================================
class NotificationManager:
    @staticmethod
    def create(user_id: int, title: str, message: str,
               notif_type: str = 'info',
               reference_id: int = None,
               reference_type: str = None) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO notifications
            (user_id, title, message, type, reference_id, reference_type)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, title, message, notif_type, reference_id, reference_type))
        nid = cursor.lastrowid
        conn.commit()
        conn.close()
        return nid

    @staticmethod
    def get_for_user(user_id: int, unread_only: bool = False, limit: int = 50) -> List[Dict]:
        conn = get_db_connection()
        q = 'SELECT * FROM notifications WHERE user_id = ?'
        params = [user_id]
        if unread_only:
            q += ' AND is_read = 0'
        q += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def mark_read(notification_id: int, user_id: int) -> bool:
        conn = get_db_connection()
        conn.execute(
            'UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?',
            (notification_id, user_id)
        )
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def mark_all_read(user_id: int) -> bool:
        conn = get_db_connection()
        conn.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def unread_count(user_id: int) -> int:
        conn = get_db_connection()
        count = conn.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0',
            (user_id,)
        ).fetchone()[0]
        conn.close()
        return count

# ============================================================
# DATABASE OPERATIONS CLASSES
# ============================================================

class UserProfile:
    @staticmethod
    def create_profile(email: str, name: str, age: Optional[int] = None, gender: Optional[str] = None) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO users (email, name, age, gender, profile_completed)
                VALUES (?, ?, ?, ?, ?)
            ''', (email.lower(), name, age, gender, 1 if age and gender else 0))
            user_id = cursor.lastrowid
            conn.commit()
            return user_id
        except sqlite3.IntegrityError:
            cursor.execute('SELECT id FROM users WHERE email = ?', (email.lower(),))
            result = cursor.fetchone()
            return result['id'] if result else None
        finally:
            conn.close()
    
    @staticmethod
    def get_profile(user_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, email, name, age, gender, created_at, updated_at, profile_completed
            FROM users WHERE id = ?
        ''', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    @staticmethod
    def get_profile_by_email(email: str) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, email, name, age, gender, created_at, updated_at, profile_completed
            FROM users WHERE email = ?
        ''', (email.lower(),))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    @staticmethod
    def update_profile(user_id: int, **kwargs) -> bool:
        allowed_fields = ['name', 'age', 'gender']
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if not updates:
            return False
        
        current = UserProfile.get_profile(user_id)
        if current:
            new_age = updates.get('age', current['age'])
            new_gender = updates.get('gender', current['gender'])
            updates['profile_completed'] = 1 if (new_age and new_gender) else 0
        
        updates['updated_at'] = datetime.now().isoformat()
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [user_id]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f'UPDATE users SET {set_clause} WHERE id = ?', values)
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

class HealthMetrics:
    @staticmethod
    def add_or_update(user_id: int, **metrics) -> bool:
        allowed_fields = [
            'height_cm', 'weight_kg', 'blood_group', 'blood_pressure_systolic',
            'blood_pressure_diastolic', 'heart_rate', 'allergies', 
            'chronic_conditions', 'current_medications', 'emergency_contact_name',
            'emergency_contact_phone'
        ]
        
        # Serialize lists to JSON strings first, then encrypt sensitive fields
        for field in ['allergies', 'chronic_conditions', 'current_medications']:
            if field in metrics and metrics[field] is not None:
                if isinstance(metrics[field], (list, dict)):
                    metrics[field] = json.dumps(metrics[field])
        
        updates = {k: v for k, v in metrics.items() if k in allowed_fields}
        if not updates:
            return False

        # Encrypt sensitive PII fields before storing
        sensitive_fields = [
            'allergies', 'chronic_conditions', 'current_medications',
            'emergency_contact_name', 'emergency_contact_phone'
        ]
        for field in sensitive_fields:
            if field in updates and updates[field] is not None:
                updates[field] = encrypt_field(str(updates[field]))
        
        updates['updated_at'] = datetime.now().isoformat()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM health_metrics WHERE user_id = ?', (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [user_id]
            cursor.execute(f'UPDATE health_metrics SET {set_clause} WHERE user_id = ?', values)
        else:
            columns = ', '.join(updates.keys())
            placeholders = ', '.join(['?' for _ in updates])
            values = [user_id] + list(updates.values())
            cursor.execute(f'INSERT INTO health_metrics (user_id, {columns}) VALUES (?, {placeholders})', values)
        
        success = cursor.rowcount > 0 or not exists
        conn.commit()
        conn.close()
        return success
    
    @staticmethod
    def get_metrics(user_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM health_metrics WHERE user_id = ?', (user_id,))
        metrics = cursor.fetchone()
        conn.close()
        
        if metrics:
            result = dict(metrics)
            # Decrypt sensitive fields, then parse JSON lists
            sensitive_fields = [
                'allergies', 'chronic_conditions', 'current_medications',
                'emergency_contact_name', 'emergency_contact_phone'
            ]
            for field in sensitive_fields:
                if result.get(field):
                    decrypted = decrypt_field(result[field])
                    try:
                        result[field] = json.loads(decrypted)
                    except Exception:
                        result[field] = decrypted
            return result
        return None

class AssessmentHistory:
    @staticmethod
    def save_assessment(user_id: int, symptoms: List[str], age_group: str, 
                        gender: str, predictions: List[Dict], 
                        top_disease: str, confidence: float) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO assessments (user_id, symptoms, age_group, gender, 
                                    predictions, top_disease, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, json.dumps(symptoms), age_group, gender,
              json.dumps(predictions), top_disease, confidence))
        assessment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return assessment_id
    
    @staticmethod
    def get_history(user_id: int, limit: int = 10) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, symptoms, predictions, top_disease, confidence, 
                   age_group, gender, created_at
            FROM assessments WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit))
        assessments = cursor.fetchall()
        conn.close()
        
        results = []
        for assessment in assessments:
            row = dict(assessment)
            row['symptoms'] = json.loads(row['symptoms']) if row['symptoms'] else []
            row['predictions'] = json.loads(row['predictions']) if row['predictions'] else []
            results.append(row)
        return results

class MedicalRecords:
    @staticmethod
    def add_record(user_id: int, title: str, record_type: str = None, 
                   description: str = None, date: str = None, 
                   doctor_name: str = None, hospital_name: str = None,
                   metadata: Dict = None) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO medical_records (user_id, title, record_type, description, 
                                        date, doctor_name, hospital_name, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, title, record_type, description, date,
              doctor_name, hospital_name, json.dumps(metadata) if metadata else None))
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return record_id
    
    @staticmethod
    def get_records(user_id: int, record_type: str = None) -> List[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        if record_type:
            cursor.execute('''
                SELECT * FROM medical_records
                WHERE user_id = ? AND record_type = ?
                ORDER BY date DESC, created_at DESC
            ''', (user_id, record_type))
        else:
            cursor.execute('''
                SELECT * FROM medical_records
                WHERE user_id = ?
                ORDER BY date DESC, created_at DESC
            ''', (user_id,))
        records = cursor.fetchall()
        conn.close()
        
        results = []
        for record in records:
            row = dict(record)
            if row.get('metadata'):
                try:
                    row['metadata'] = json.loads(row['metadata'])
                except:
                    pass
            results.append(row)
        return results

class SessionManager:
    @staticmethod
    def create_session(user_id: int, expires_days: int = 7) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=expires_days)  # Use UTC to match SQLite datetime("now")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (user_id, session_token, expires_at)
            VALUES (?, ?, ?)
        ''', (user_id, token, expires_at.isoformat()))
        conn.commit()
        conn.close()
        return token
    
    @staticmethod
    def get_user_from_token(token: str) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.email, u.name, u.age, u.gender, u.profile_completed,
                   COALESCE(u.user_type, 'patient') AS user_type
            FROM users u
            JOIN sessions s ON u.id = s.user_id
            WHERE s.session_token = ? AND s.expires_at > datetime('now')
            ORDER BY s.created_at DESC LIMIT 1
        ''', (token,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    @staticmethod
    def logout(token: str) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM sessions WHERE session_token = ?', (token,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

# ============================================================
# LOAD ML MODEL
# ============================================================
print("Loading model bundle…")
model_paths = [
    MODEL_DIR / "disease_model_bundle_v2.pkl",
    BACKEND_DIR / "disease_model_bundle_v2.pkl",
    PROJECT_ROOT / "disease_model_bundle_v2.pkl",
    Path("disease_model_bundle_v2.pkl"),
]

BUNDLE = None
for model_path in model_paths:
    if model_path.exists():
        try:
            BUNDLE = joblib.load(str(model_path))
            print(f"✓ Model loaded from {model_path}")
            break
        except Exception as e:
            print(f"Failed to load from {model_path}: {e}")

if BUNDLE is None:
    raise RuntimeError("disease_model_bundle_v2.pkl not found in any of the expected locations!")

XGB = BUNDLE['xgb_model']
LE = BUNDLE['label_encoder']
SYMPTOM_NAMES = BUNDLE['symptom_names']
FULL_FEATS = BUNDLE['full_feature_names']
SDP = BUNDLE['symptom_disease_prob']
PRIOR = BUNDLE['disease_prior']
AGE_CLASSES = BUNDLE['age_classes']
GEN_CLASSES = BUNDLE['gender_classes']
N_SYM = len(SYMPTOM_NAMES)
N_FEATURES = len(FULL_FEATS)

print(f"✓ Model ready | Diseases: {len(LE.classes_)} | Symptoms: {N_SYM} | Features: {N_FEATURES}")

# ============================================================
# LOAD SYMPTOM CATALOGUE FROM JSON FILE
# ============================================================
SYMPTOM_CATALOGUE = []

def load_symptom_catalogue():
    """Load the complete symptom catalogue from symptoms.json"""
    global SYMPTOM_CATALOGUE
    
    json_paths = [
        BACKEND_DIR / "symptoms.json",
        PROJECT_ROOT / "symptoms.json",
        PROJECT_ROOT / "backend" / "symptoms.json",
        Path("symptoms.json"),
    ]
    
    for json_path in json_paths:
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    SYMPTOM_CATALOGUE = json.load(f)
                print(f"✓ Loaded symptom catalogue from {json_path} ({len(SYMPTOM_CATALOGUE)} symptoms)")
                return True
            except Exception as e:
                print(f"Failed to load from {json_path}: {e}")
    
    print("⚠ No symptoms.json found, creating minimal catalogue from model data")
    SYMPTOM_CATALOGUE = [
        {
            "symptom_name": name,
            "gender_specific": "both",
            "explanation": f"Medical symptom: {name}. Please consult a healthcare professional for more information."
        }
        for name in SYMPTOM_NAMES
    ]
    print(f"✓ Created minimal catalogue with {len(SYMPTOM_CATALOGUE)} symptoms")
    return False

load_symptom_catalogue()

# ============================================================
# FAST FEATURE CONTRIBUTIONS (Replaces SHAP) - FIXED VERSION
# ============================================================
_CACHE: Dict[str, List[Dict]] = {}

def get_feature_contributions_fast(full_vec: np.ndarray, top_k: int = 8) -> List[Dict]:
    """
    Returns contributions ONLY for symptoms the user actually reported (active in full_vec).
    Values are normalized to 0.0–1.0 relative to the max active-symptom contribution.
    """
    # Identify which symptoms the user actually has active
    active_mask = full_vec[0, :N_SYM] > 0.5
    active_indices = list(np.where(active_mask)[0])

    if not active_indices:
        return []

    try:
        # Method 1: XGBoost pred_contribs (most accurate)
        try:
            import xgboost as xgb
            dmat = xgb.DMatrix(full_vec, feature_names=FULL_FEATS)
            booster = XGB.get_booster()
            contributions = booster.predict(dmat, pred_contribs=True)

            # Handle multi-class (3D) vs binary (2D)
            if contributions.ndim == 3:
                contrib_values = contributions[0, :, :-1].sum(axis=0)  # (n_features,)
            else:
                contrib_values = contributions[0, :-1]  # (n_features,)

            sym_contributions = contrib_values[:N_SYM]

            # Only keep symptoms the user actually selected
            raw = [(SYMPTOM_NAMES[i], float(sym_contributions[i])) for i in active_indices]
            raw.sort(key=lambda x: abs(x[1]), reverse=True)

            if raw:
                max_val = max(abs(v) for _, v in raw) or 1.0
                result = [{"symptom": s, "value": float(v) / max_val} for s, v in raw[:top_k]]
                return result
        except Exception as e:
            print(f"Method 1 (pred_contribs) failed: {e}")

        # Method 2: Feature importance weighted by user's active symptoms only
        sym_importance = np.abs(XGB.feature_importances_[:N_SYM])
        raw = [(SYMPTOM_NAMES[i], float(sym_importance[i])) for i in active_indices]
        raw.sort(key=lambda x: x[1], reverse=True)

        if raw:
            max_val = max(v for _, v in raw) or 1.0
            return [{"symptom": s, "value": v / max_val} for s, v in raw[:top_k]]

        return []

    except Exception as e:
        print(f"Feature contributions error: {e}")
        return []

async def get_feature_contributions_async(full_vec: np.ndarray, top_k: int = 8) -> List[Dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _THREAD_POOL, get_feature_contributions_fast, full_vec, top_k
    )

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="Panacea Unified API", version="4.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# SERVE STATIC FILES (FRONTEND)
# ============================================================

@app.get("/")
async def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    if Path("index.html").exists():
        return FileResponse("index.html")
    raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/assessment")
async def serve_assessment():
    assessment_path = FRONTEND_DIR / "assessment.html"
    if assessment_path.exists():
        return FileResponse(str(assessment_path))
    if Path("assessment.html").exists():
        return FileResponse("assessment.html")
    raise HTTPException(status_code=404, detail="assessment.html not found")

@app.get("/{filename}.html")
async def serve_other_html(filename: str):
    html_path = FRONTEND_DIR / f"{filename}.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    if Path(f"{filename}.html").exists():
        return FileResponse(f"{filename}.html")
    raise HTTPException(status_code=404, detail=f"{filename}.html not found")

@app.get("/assets/{filepath:path}")
async def serve_assets(filepath: str):
    assets_path = FRONTEND_DIR / "assets" / filepath
    if assets_path.exists():
        return FileResponse(str(assets_path))
    raise HTTPException(status_code=404, detail="Asset not found")

# ============================================================
# DISEASE DETAILS ENDPOINTS (NEW)
# ============================================================

@app.get("/api/diseases")
async def get_all_diseases(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = None
):
    """Get all diseases with pagination and optional search"""
    if not DISEASES_LIST:
        raise HTTPException(status_code=503, detail="Disease database not loaded")
    
    result = DISEASES_LIST.copy()
    
    if search:
        search_lower = search.lower()
        result = [d for d in result if search_lower in d.get("disease_name", "").lower()]
    
    total = len(result)
    paginated = result[skip:skip + limit]
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "diseases": paginated
    }

@app.get("/api/diseases/search")
async def search_diseases(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50)
):
    """Search diseases by name (autocomplete)"""
    if not DISEASES_LIST:
        raise HTTPException(status_code=503, detail="Disease database not loaded")
    
    q_lower = q.lower()
    matches = []
    
    for disease in DISEASES_LIST:
        name = disease.get("disease_name", "")
        if q_lower in name.lower():
            matches.append({
                "disease_name": name,
                "primary_severity": disease.get("primary_severity"),
                "secondary_severity": disease.get("secondary_severity"),
                "specialist": disease.get("specialist")
            })
            if len(matches) >= limit:
                break
    
    return {"query": q, "results": matches}

@app.get("/api/diseases/{disease_name}")
async def get_disease_details(disease_name: str):
    """Get complete disease details including description, severities, and specialist"""
    info = get_disease_info(disease_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_name}' not found")
    return info

@app.get("/api/diseases/{disease_name}/summary")
async def get_disease_summary(disease_name: str):
    """Get a simplified summary of the disease (for display in predictions)"""
    info = get_disease_info(disease_name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Disease '{disease_name}' not found")
    
    return {
        "disease_name": info.get("disease_name"),
        "primary_severity": info.get("primary_severity"),
        "secondary_severity": info.get("secondary_severity"),
        "specialist": info.get("specialist"),
        "description": info.get("description")
    }

# ============================================================
# MODEL API HELPERS
# ============================================================

def one_hot(value: str, classes: list) -> np.ndarray:
    vec = np.zeros(len(classes), dtype=np.float32)
    v = value.strip().lower()
    if v in classes:
        vec[classes.index(v)] = 1.0
    return vec

def build_feature_vector(symptoms: List[str], age_group: str, gender: str, overrides: dict) -> tuple[np.ndarray, int]:
    sl = [s.lower().strip() for s in symptoms]
    vec_sym = np.zeros(N_SYM, dtype=np.float32)
    matched = 0
    
    for i, feat in enumerate(SYMPTOM_NAMES):
        fl = feat.lower()
        if any(s in fl or fl in s for s in sl):
            vec_sym[i] = 1.0
            matched += 1
    
    for sym_name, answer in overrides.items():
        sl2 = sym_name.lower().strip()
        for i, feat in enumerate(SYMPTOM_NAMES):
            if sl2 in feat.lower() or feat.lower() in sl2:
                vec_sym[i] = 1.0 if answer else 0.0
    
    vec_age = one_hot(age_group, AGE_CLASSES)
    vec_gen = one_hot(gender, GEN_CLASSES)
    vec_full = np.concatenate([vec_sym, vec_age, vec_gen]).reshape(1, -1)
    return vec_full, matched

def top_predictions(full_vec: np.ndarray, prior_weight: float, top_n: int) -> List[Dict]:
    n_all = len(LE.classes_)
    raw_prob = XGB.predict_proba(full_vec)[0]
    full_prob = np.zeros(n_all, dtype=np.float64)
    for local_i, global_i in enumerate(XGB.classes_):
        full_prob[global_i] = raw_prob[local_i]
    
    blended = (1 - prior_weight) * full_prob + prior_weight * PRIOR
    blended /= blended.sum()
    sorted_idx = np.argsort(blended)[::-1]
    pool_size = 15
    pool_set = set(sorted_idx[:pool_size].tolist())
    
    results = []
    for rank, d_idx in enumerate(sorted_idx[:top_n], 1):
        disease_name = LE.classes_[d_idx]
        results.append({
            "rank": rank,
            "disease": disease_name,
            "confidence": float(blended[d_idx]),
            "in_pool": d_idx in pool_set,
            "disease_info": get_disease_info(disease_name)  # NEW: Add full disease details
        })
    return results

# ============================================================
# MODEL API ENDPOINTS
# ============================================================

class PredictRequest(BaseModel):
    symptoms_present: List[str]
    age_group: str = "adult"
    gender: str = "female"
    override_answers: Optional[dict] = {}
    interactive: bool = False
    top_n_final: int = 5
    prior_weight: float = 0.20
    fast_mode: bool = False

class ProfileCreate(BaseModel):
    email: EmailStr
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None

class HealthMetricsUpdate(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    blood_group: Optional[str] = None
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    heart_rate: Optional[int] = None
    allergies: Optional[List[str]] = None
    chronic_conditions: Optional[List[str]] = None
    current_medications: Optional[List[str]] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

class AssessmentSave(BaseModel):
    symptoms: List[str]
    age_group: str
    gender: str
    predictions: List[Dict]
    top_disease: str
    confidence: float

class MedicalRecordCreate(BaseModel):
    title: str
    record_type: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None
    doctor_name: Optional[str] = None
    hospital_name: Optional[str] = None
    metadata: Optional[Dict] = None

class LoginRequest(BaseModel):
    email: EmailStr
    name: str

@app.get("/health")
def health():
    return {
        "status": "ok",
        "diseases": len(LE.classes_),
        "symptoms": N_SYM,
        "features": N_FEATURES,
        "database": "connected",
        "explanation_method": "xgboost_contributions",
        "disease_db_loaded": len(DISEASES_DB) > 0,
        "version": "4.2"
    }

@app.get("/symptoms")
def get_symptoms():
    return {"symptoms": SYMPTOM_NAMES, "count": N_SYM}

@app.get("/symptoms/catalogue")
def get_symptom_catalogue():
    return {
        "symptoms": SYMPTOM_CATALOGUE,
        "count": len(SYMPTOM_CATALOGUE),
        "message": "Full symptom catalogue with explanations"
    }

@app.post("/predict")
async def predict(req: PredictRequest):
    if not req.symptoms_present:
        raise HTTPException(status_code=400, detail="symptoms_present cannot be empty")
    
    age_map = {'teen': 'adolescent', 'teenager': 'adolescent', 'senior': 'elderly', 'toddler': 'infant', 'baby': 'infant'}
    req.age_group = age_map.get(req.age_group.strip().lower(), req.age_group.strip().lower())
    if req.age_group not in AGE_CLASSES:
        req.age_group = 'adult'
    req.gender = req.gender.strip().lower()
    if req.gender not in GEN_CLASSES:
        req.gender = 'female'
    
    full_vec, matched = build_feature_vector(
        req.symptoms_present, req.age_group, req.gender, req.override_answers or {}
    )
    
    preds = top_predictions(full_vec, req.prior_weight, req.top_n_final)
    
    explanations = []
    if preds:
        explanations = await get_feature_contributions_async(full_vec)
    
    return {
        "predictions": preds,
        "explanations": explanations,
        "matched_count": matched,
        "age_group": req.age_group,
        "gender": req.gender,
    }

# ============================================================
# FAST BAYESIAN CLARIFICATION API ENDPOINTS (REFACTORED)
# ============================================================
import uuid as _uuid

_CLARIFY_SESSIONS: Dict[str, Dict] = {}

def _entropy_local(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-np.sum(p * np.log(p + 1e-12)))

def _dynamic_pool_size_local(sorted_proba: np.ndarray, min_k=15, max_k=30, conf_thresh=0.50) -> int:
    top1 = sorted_proba[0]
    if top1 >= conf_thresh:
        return min_k
    gap = top1 - sorted_proba[min(max_k - 1, len(sorted_proba) - 1)]
    ratio = float(np.clip(gap / (top1 + 1e-9), 0.0, 1.0))
    return int(np.clip(int(round(max_k - ratio * (max_k - min_k))), min_k, max_k))

def _best_question(candidate_indices: np.ndarray, posterior: np.ndarray,
                   already_asked: set, top_var: int = 200) -> int:
    p_mat = SDP[candidate_indices]
    var_all = p_mat.var(axis=0)
    top_syms = list(np.argsort(var_all)[::-1][:top_var])
    to_check = [int(s) for s in top_syms if int(s) not in already_asked]
    if not to_check:
        return -1

    post_cand = posterior[candidate_indices].copy()
    post_cand = post_cand / (post_cand.sum() + 1e-12)
    current_H = _entropy_local(post_cand)

    best_s, best_gain = -1, -np.inf
    for s in to_check:
        p_yes_d = p_mat[:, s]
        p_yes = float(np.dot(post_cand, p_yes_d))
        p_no = 1.0 - p_yes
        if p_yes < 1e-6 or p_no < 1e-6:
            continue
        py = post_cand * p_yes_d
        py /= (py.sum() + 1e-12)
        pn = post_cand * (1 - p_yes_d)
        pn /= (pn.sum() + 1e-12)
        gain = current_H - (p_yes * _entropy_local(py) + p_no * _entropy_local(pn))
        if gain > best_gain:
            best_gain, best_s = gain, s
    return best_s

def _apply_bayes_update(posterior: np.ndarray, candidates: np.ndarray,
                         sym_idx: int, answer_yes: bool) -> np.ndarray:
    updated = posterior.copy()
    for d in candidates:
        lk = SDP[d, sym_idx] if answer_yes else (1.0 - SDP[d, sym_idx])
        updated[d] *= lk
    s_before = posterior[candidates].sum()
    s_after = updated[candidates].sum()
    if s_after > 1e-12:
        updated[candidates] *= (s_before / s_after)
    return updated

def _build_initial_posterior(symptoms: List[str], age_group: str, gender: str,
                              prior_weight: float = 0.10):
    full_vec, matched = build_feature_vector(symptoms, age_group, gender, {})
    n_all = len(LE.classes_)
    raw_prob = XGB.predict_proba(full_vec)[0]
    full_prob = np.zeros(n_all, dtype=np.float64)
    for li, gi in enumerate(XGB.classes_):
        full_prob[gi] = raw_prob[li]
    blended = (1 - prior_weight) * full_prob + prior_weight * PRIOR
    blended /= blended.sum()

    sorted_idx = np.argsort(blended)[::-1]
    pool_size = _dynamic_pool_size_local(blended[sorted_idx])
    candidates = sorted_idx[:pool_size].copy()

    sl = [s.lower().strip() for s in symptoms]
    answered = set()
    for i, feat in enumerate(SYMPTOM_NAMES):
        fl = feat.lower()
        if any(s in fl or fl in s for s in sl):
            answered.add(i)

    return blended, candidates, answered, matched

def _make_final_predictions(posterior: np.ndarray, candidates: np.ndarray,
                              top_n: int = 5) -> List[Dict]:
    post_norm = posterior / (posterior.sum() + 1e-12)
    final_sorted = np.argsort(post_norm)[::-1]
    cset = set(int(i) for i in candidates)
    results = []
    for r, d in enumerate(final_sorted[:top_n], 1):
        disease_name = LE.classes_[d]
        results.append({
            "rank": r, 
            "disease": disease_name, 
            "confidence": float(post_norm[d]),
            "in_pool": d in cset,
            "disease_info": get_disease_info(disease_name)
        })
    return results

class ClarifyStartRequest(BaseModel):
    symptoms_present: List[str]
    age_group: str = "adult"
    gender: str = "female"
    max_questions: int = 12
    min_questions: int = 3          # Ask at least 3 questions by default
    confidence_stop: float = 0.95   # High threshold to stop early only if past min_questions
    prior_weight: float = 0.10
    fast_mode: bool = False
    exhaustive: bool = False

class ClarifyAnswerRequest(BaseModel):
    session_id: str
    symptom_name: str
    answer: bool

@app.post("/clarify/start")
async def clarify_start(req: ClarifyStartRequest):
    if not req.symptoms_present:
        raise HTTPException(400, "symptoms_present cannot be empty")

    # Fast mode: skip clarification entirely
    if req.fast_mode:
        full_vec, matched = build_feature_vector(
            req.symptoms_present, req.age_group, req.gender, {}
        )
        preds = top_predictions(full_vec, req.prior_weight, 5)
        explanations = await get_feature_contributions_async(full_vec)
        return {
            "session_id": None,
            "ready_for_result": True,
            "predictions": preds,
            "explanations": explanations,
            "matched_count": matched
        }

    # Normalize inputs
    age_map = {'teen': 'adolescent', 'teenager': 'adolescent', 'senior': 'elderly',
               'toddler': 'infant', 'baby': 'infant'}
    age_group = age_map.get(req.age_group.strip().lower(), req.age_group.strip().lower())
    if age_group not in AGE_CLASSES:
        age_group = 'adult'
    gender = req.gender.strip().lower()
    if gender not in GEN_CLASSES:
        gender = 'female'

    posterior, candidates, answered, matched = _build_initial_posterior(
        req.symptoms_present, age_group, gender, req.prior_weight
    )
    session_id = str(_uuid.uuid4())

    # Always ask at least one question (unless min_questions=0 but we set default 3)
    best_s = _best_question(candidates, posterior, answered)
    if best_s == -1:
        # No questions available – signal client to fetch result
        _CLARIFY_SESSIONS[session_id] = {
            "posterior": posterior,
            "candidates": candidates,
            "answered": answered,
            "symptoms_present": req.symptoms_present,
            "age_group": age_group,
            "gender": gender,
            "max_questions": req.max_questions,
            "min_questions": req.min_questions,
            "confidence_stop": req.confidence_stop,
            "prior_weight": req.prior_weight,
            "exhaustive": req.exhaustive,
            "q_count": 0,
            "matched_count": matched,
            "finished": False
        }
        return {
            "session_id": session_id,
            "ready_for_result": True,
            "question_number": 0
        }

    answered.add(best_s)
    _CLARIFY_SESSIONS[session_id] = {
        "posterior": posterior,
        "candidates": candidates,
        "answered": answered,
        "symptoms_present": req.symptoms_present,
        "age_group": age_group,
        "gender": gender,
        "max_questions": req.max_questions,
        "min_questions": req.min_questions,
        "confidence_stop": req.confidence_stop,
        "prior_weight": req.prior_weight,
        "exhaustive": req.exhaustive,
        "q_count": 1,
        "matched_count": matched,
        "finished": False
    }
    
    question_explanation = ""
    for s in SYMPTOM_CATALOGUE:
        if s["symptom_name"].lower() == SYMPTOM_NAMES[best_s].lower():
            question_explanation = s.get("explanation", "")
            break
    
    return {
        "session_id": session_id,
        "ready_for_result": False,
        "question": SYMPTOM_NAMES[best_s],
        "question_index": best_s,
        "question_explanation": question_explanation,
        "question_number": 1,
        "matched_count": matched
    }

@app.post("/clarify/answer")
async def clarify_answer(req: ClarifyAnswerRequest):
    sess = _CLARIFY_SESSIONS.get(req.session_id)
    if not sess:
        raise HTTPException(404, "Clarification session not found or expired")
    if sess.get("finished"):
        raise HTTPException(400, "Session already finalized")

    posterior = sess["posterior"]
    candidates = sess["candidates"]
    answered = sess["answered"]

    sym_idx = next((i for i, n in enumerate(SYMPTOM_NAMES)
                    if n.lower() == req.symptom_name.lower().strip()), -1)
    if sym_idx == -1:
        raise HTTPException(400, f"Unknown symptom: {req.symptom_name}")

    posterior = _apply_bayes_update(posterior, candidates, sym_idx, req.answer)
    sess["posterior"] = posterior
    sess["q_count"] += 1

    q_count = sess["q_count"]
    min_q = sess.get("min_questions", 3)
    max_q = sess["max_questions"]
    conf_stop = sess["confidence_stop"]
    exhaustive = sess.get("exhaustive", False)

    cand_post = posterior[candidates]
    top_conf = float((cand_post / (cand_post.sum() + 1e-12)).max())

    past_min = q_count >= min_q
    hit_max  = q_count >= max_q
    hit_conf = top_conf >= conf_stop and past_min and not exhaustive

    if hit_max or hit_conf:
        # Mark as finished but keep session for result retrieval
        sess["finished"] = True
        return {
            "session_id": req.session_id,
            "ready_for_result": True,
            "question_number": q_count
        }

    best_s = _best_question(candidates, posterior, answered)
    if best_s == -1 and past_min:
        sess["finished"] = True
        return {
            "session_id": req.session_id,
            "ready_for_result": True,
            "question_number": q_count
        }
    elif best_s == -1:
        # No good question but haven't hit min_questions – still finish
        sess["finished"] = True
        return {
            "session_id": req.session_id,
            "ready_for_result": True,
            "question_number": q_count
        }

    answered.add(best_s)
    sess["answered"] = answered
    
    question_explanation = ""
    for s in SYMPTOM_CATALOGUE:
        if s["symptom_name"].lower() == SYMPTOM_NAMES[best_s].lower():
            question_explanation = s.get("explanation", "")
            break
    
    return {
        "session_id": req.session_id,
        "ready_for_result": False,
        "question": SYMPTOM_NAMES[best_s],
        "question_index": best_s,
        "question_explanation": question_explanation,
        "question_number": sess["q_count"],
        "matched_count": sess["matched_count"]
    }

@app.get("/clarify/result/{session_id}")
async def clarify_result(session_id: str):
    """Fetch final predictions for a session. Deletes session immediately after."""
    sess = _CLARIFY_SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(404, "Session not found or already finalized")
    
    preds = _make_final_predictions(sess["posterior"], sess["candidates"])
    fv, _ = build_feature_vector(sess["symptoms_present"], sess["age_group"], sess["gender"], {})
    explanations = await get_feature_contributions_async(fv)
    
    # Delete session permanently
    del _CLARIFY_SESSIONS[session_id]
    
    return {
        "predictions": preds,
        "explanations": explanations,
        "matched_count": sess["matched_count"]
    }

# Keep /clarify/finalize for backward compatibility but mark as deprecated
@app.post("/clarify/finalize")
async def clarify_finalize(body: Dict[str, Any]):
    sid = body.get("session_id")
    sess = _CLARIFY_SESSIONS.get(sid)
    if not sess:
        raise HTTPException(404, "Session not found")
    preds = _make_final_predictions(sess["posterior"], sess["candidates"])
    fv, _ = build_feature_vector(sess["symptoms_present"], sess["age_group"], sess["gender"], {})
    explanations = await get_feature_contributions_async(fv)
    del _CLARIFY_SESSIONS[sid]
    return {
        "predictions": preds,
        "explanations": explanations,
        "matched_count": sess["matched_count"]
    }
# ============================================================
# PROFILE API ENDPOINTS
# ============================================================

@app.post("/api/profile/login")
async def login(request: LoginRequest):
    user = UserProfile.get_profile_by_email(request.email)
    
    if not user:
        user_id = UserProfile.create_profile(email=request.email, name=request.name)
        profile_completed = False
    else:
        user_id = user['id']
        profile_completed = user.get('profile_completed', False)
        if user['name'] != request.name:
            UserProfile.update_profile(user_id, name=request.name)
    
    token = SessionManager.create_session(user_id)
    
    return {
        "success": True,
        "user_id": user_id,
        "session_token": token,
        "profile_completed": profile_completed
    }


# ── Doctor login ──────────────────────────────────────────────
class DoctorLoginRequest(BaseModel):
    email: EmailStr
    license_number: str   # acts as the doctor's password / verification token


@app.post("/api/auth/doctor-login")
async def doctor_login(request: DoctorLoginRequest):
    """
    Doctor-specific login.
    Verifies the user exists, has user_type='doctor', and their license_number matches.
    Returns a session token on success.
    """
    conn = get_db_connection()
    user_row = conn.execute(
        "SELECT id, name, email, user_type FROM users WHERE email = ? AND is_active = 1",
        (request.email.lower(),)
    ).fetchone()

    if not user_row:
        conn.close()
        raise HTTPException(status_code=401, detail="No account found with that email.")

    user = dict(user_row)

    if user.get("user_type") != "doctor":
        conn.close()
        raise HTTPException(status_code=403, detail="This account is not registered as a doctor.")

    # Verify license number against doctors_profile
    profile_row = conn.execute(
        "SELECT license_number FROM doctors_profile WHERE user_id = ?",
        (user["id"],)
    ).fetchone()
    conn.close()

    if not profile_row or profile_row["license_number"] != request.license_number.strip():
        raise HTTPException(status_code=401, detail="Invalid license number.")

    token = SessionManager.create_session(user["id"])

    return {
        "success": True,
        "user_id": user["id"],
        "session_token": token,
        "user_type": "doctor",
        "name": user["name"],
    }


# ── Doctor registration ───────────────────────────────────────
class DoctorRegisterRequest(BaseModel):
    email: EmailStr
    name: str
    license_number: str
    specialization: Optional[str] = None
    qualification: Optional[str] = None
    experience_years: Optional[int] = None
    consultation_fee: Optional[float] = None
    bio: Optional[str] = None
    phone: Optional[str] = None


@app.post("/api/auth/doctor-register")
async def doctor_register(request: DoctorRegisterRequest):
    """
    Register a new doctor account.
    Creates a user with user_type='doctor' and populates doctors_profile.
    The license_number serves as the doctor's login credential.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check for duplicate email
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (request.email.lower(),)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    try:
        # Create user row with user_type = 'doctor'
        cursor.execute(
            """INSERT INTO users (email, name, user_type, phone, profile_completed)
               VALUES (?, ?, 'doctor', ?, 1)""",
            (request.email.lower(), request.name, request.phone)
        )
        user_id = cursor.lastrowid

        # Add license_number column to doctors_profile if not present (migration guard)
        try:
            cursor.execute("ALTER TABLE doctors_profile ADD COLUMN license_number TEXT")
        except Exception:
            pass  # Column already exists

        available_days_json = json.dumps(["Mon", "Tue", "Wed", "Thu", "Fri"])

        cursor.execute(
            """INSERT INTO doctors_profile
               (user_id, specialization, qualification, experience_years,
                consultation_fee, bio, license_number, available_days, is_verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                user_id,
                request.specialization or "",
                request.qualification or "",
                request.experience_years or 0,
                request.consultation_fee or 0.0,
                request.bio or "",
                request.license_number.strip(),
                available_days_json,
            )
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

    token = SessionManager.create_session(user_id)
    conn.close()

    return {
        "success": True,
        "user_id": user_id,
        "session_token": token,
        "user_type": "doctor",
        "name": request.name,
    }

@app.get("/api/profile/{user_id}")
async def get_profile(user_id: int):
    profile = UserProfile.get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    
    metrics = HealthMetrics.get_metrics(user_id)
    return {"profile": profile, "health_metrics": metrics}

@app.put("/api/profile/{user_id}")
async def update_profile(user_id: int, update: ProfileUpdate):
    success = UserProfile.update_profile(
        user_id,
        name=update.name,
        age=update.age,
        gender=update.gender
    )
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}

@app.put("/api/profile/{user_id}/metrics")
async def update_health_metrics(user_id: int, metrics: HealthMetricsUpdate):
    metrics_dict = {k: v for k, v in metrics.dict().items() if v is not None}
    success = HealthMetrics.add_or_update(user_id, **metrics_dict)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update metrics")
    return {"success": True}

@app.get("/api/profile/{user_id}/metrics")
async def get_health_metrics(user_id: int):
    metrics = HealthMetrics.get_metrics(user_id)
    return {"metrics": metrics}

@app.post("/api/profile/{user_id}/assessments")
async def save_assessment(user_id: int, assessment: AssessmentSave):
    assessment_id = AssessmentHistory.save_assessment(
        user_id=user_id,
        symptoms=assessment.symptoms,
        age_group=assessment.age_group,
        gender=assessment.gender,
        predictions=assessment.predictions,
        top_disease=assessment.top_disease,
        confidence=assessment.confidence
    )
    return {"success": True, "assessment_id": assessment_id}

@app.get("/api/profile/{user_id}/assessments")
async def get_assessment_history(user_id: int, limit: int = 10):
    history = AssessmentHistory.get_history(user_id, limit)
    return {"history": history}

@app.post("/api/profile/{user_id}/records")
async def add_medical_record(user_id: int, record: MedicalRecordCreate):
    record_id = MedicalRecords.add_record(
        user_id=user_id,
        title=record.title,
        record_type=record.record_type,
        description=record.description,
        date=record.date,
        doctor_name=record.doctor_name,
        hospital_name=record.hospital_name,
        metadata=record.metadata
    )
    return {"success": True, "record_id": record_id}

@app.get("/api/profile/{user_id}/records")
async def get_medical_records(user_id: int, record_type: Optional[str] = None):
    records = MedicalRecords.get_records(user_id, record_type)
    return {"records": records}

@app.delete("/api/profile/{user_id}/assessments/{assessment_id}")
async def delete_assessment(user_id: int, assessment_id: int):
    """Delete a specific assessment for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM assessments WHERE id = ? AND user_id = ?',
        (assessment_id, user_id)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return {"success": True, "deleted_id": assessment_id}

@app.delete("/api/profile/{user_id}/records/{record_id}")
async def delete_medical_record(user_id: int, record_id: int):
    """Delete a specific medical record for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM medical_records WHERE id = ? AND user_id = ?',
        (record_id, user_id)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"success": True, "deleted_id": record_id}

@app.get("/api/profile/{user_id}/summary")
async def get_profile_summary(user_id: int):
    """
    Lightweight summary for the profile page:
    returns profile, health_metrics, assessment count, and records count.
    """
    profile = UserProfile.get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")

    metrics = HealthMetrics.get_metrics(user_id)

    conn = get_db_connection()
    assessment_count = conn.execute(
        'SELECT COUNT(*) FROM assessments WHERE user_id = ?', (user_id,)
    ).fetchone()[0]
    records_count = conn.execute(
        'SELECT COUNT(*) FROM medical_records WHERE user_id = ?', (user_id,)
    ).fetchone()[0]
    conn.close()

    return {
        "profile": profile,
        "health_metrics": metrics,
        "assessment_count": assessment_count,
        "records_count": records_count,
    }

@app.post("/api/auth/verify")
async def verify_session(request: Request):
    body = await request.json()
    token = body.get("session_token")
    if not token:
        raise HTTPException(status_code=400, detail="No token provided")
    user = SessionManager.get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return {"valid": True, "user": user}

@app.post("/api/auth/logout")
async def logout(request: Request):
    body = await request.json()
    token = body.get("session_token")
    if token:
        SessionManager.logout(token)
    return {"success": True}
# ============================================================
# NEW ENDPOINTS FOR PHARMACY & CONSULTATION
# ============================================================

# ---------- PRODUCTS (PHARMACY) ----------
# ============================================================
# PRODUCT IMAGE ENDPOINTS (ADD TO YOUR api.py)
# ============================================================

@app.get("/api/products/{product_id}/images")
async def get_product_images(product_id: int):
    """Get all images for a product (gallery)"""
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, image_url, alt_text, is_primary, sort_order
        FROM product_images 
        WHERE product_id = ?
        ORDER BY is_primary DESC, sort_order ASC, id ASC
    """, (product_id,)).fetchall()
    conn.close()
    
    images = [dict(row) for row in rows]
    
    # If no images in product_images table, fall back to product's image_url
    if not images:
        conn = get_db_connection()
        product = conn.execute(
            "SELECT id, name, image_url FROM products WHERE id = ?", 
            (product_id,)
        ).fetchone()
        conn.close()
        
        if product and product["image_url"]:
            images = [{
                "id": 0,
                "image_url": product["image_url"],
                "alt_text": product["name"],
                "is_primary": 1,
                "sort_order": 0
            }]
    
    return {"product_id": product_id, "images": images, "count": len(images)}


@app.get("/api/products/{product_id}/image/primary")
async def get_product_primary_image(product_id: int):
    """Get only the primary (main) image URL for a product"""
    conn = get_db_connection()
    
    # First try to get primary from product_images table
    row = conn.execute("""
        SELECT image_url, alt_text
        FROM product_images 
        WHERE product_id = ? AND is_primary = 1
        LIMIT 1
    """, (product_id,)).fetchone()
    
    # Fall back to product's image_url
    if not row:
        row = conn.execute(
            "SELECT image_url, name as alt_text FROM products WHERE id = ?",
            (product_id,)
        ).fetchone()
    
    conn.close()
    
    if not row or not row["image_url"]:
        # Return a placeholder image URL
        return {
            "product_id": product_id, 
            "image_url": "/static/placeholder.png",
            "has_image": False
        }
    
    return {
        "product_id": product_id,
        "image_url": row["image_url"],
        "alt_text": row["alt_text"],
        "has_image": True
    }


@app.get("/api/products/batch/images")
async def get_batch_product_images(product_ids: str = Query(..., description="Comma-separated product IDs")):
    """Get primary images for multiple products at once (for cart/order listings)"""
    ids = [int(x.strip()) for x in product_ids.split(",") if x.strip().isdigit()]
    
    if not ids:
        return {"images": {}}
    
    placeholders = ",".join(["?"] * len(ids))
    conn = get_db_connection()
    
    # Get primary images from product_images
    rows = conn.execute(f"""
        SELECT p.id as product_id, 
               COALESCE(pi.image_url, p.image_url) as image_url,
               p.name as product_name
        FROM products p
        LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.is_primary = 1
        WHERE p.id IN ({placeholders})
    """, ids).fetchall()
    
    conn.close()
    
    result = {}
    for row in rows:
        result[row["product_id"]] = {
            "image_url": row["image_url"] or "/static/placeholder.png",
            "product_name": row["product_name"]
        }
    
    return {"images": result}


# ============================================================
# ALSO UPDATE YOUR EXISTING /api/products ENDPOINT
# ============================================================

@app.get("/api/products")
async def get_products(
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    prescription_only: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0
):
    """Get catalog of medicines with primary image URL"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT p.*, c.name as category_name,
               COALESCE(pi.image_url, p.image_url) as image_url
        FROM products p 
        LEFT JOIN categories c ON p.category_id = c.id 
        LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.is_primary = 1
        WHERE p.is_active = 1
    """
    params = []
    
    if category_id:
        query += " AND p.category_id = ?"
        params.append(category_id)
    if search:
        query += " AND (p.name LIKE ? OR p.generic_name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if prescription_only is not None:
        query += " AND p.requires_prescription = ?"
        params.append(1 if prescription_only else 0)
    
    query += " GROUP BY p.id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = cursor.execute(query, params).fetchall()
    conn.close()
    
    products = []
    for row in rows:
        product = dict(row)
        # Ensure every product has an image_url (fallback to placeholder)
        if not product.get("image_url"):
            product["image_url"] = "/static/placeholder.png"
        products.append(product)
    
    return {"products": products, "count": len(products)}


@app.get("/api/products/{product_id}")
async def get_product(product_id: int):
    """Get single product with its image gallery"""
    conn = get_db_connection()
    
    # Get product with primary image
    row = conn.execute("""
        SELECT p.*, c.name as category_name,
               COALESCE(pi.image_url, p.image_url) as image_url
        FROM products p 
        LEFT JOIN categories c ON p.category_id = c.id 
        LEFT JOIN product_images pi ON p.id = pi.product_id AND pi.is_primary = 1
        WHERE p.id = ?
    """, (product_id,)).fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(404, "Product not found")
    
    product = dict(row)
    if not product.get("image_url"):
        product["image_url"] = "/static/placeholder.png"
    
    conn.close()
    
    # Get all images for gallery (optional - can be fetched separately)
    # To avoid extra DB call, you can call get_product_images separately
    
    return product

from fastapi.responses import Response
import base64

@app.get("/static/placeholder.png")
async def placeholder_image():
    """Return a simple data:image SVG placeholder"""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
        <rect width="200" height="200" fill="#f0f0f0"/>
        <text x="100" y="110" text-anchor="middle" fill="#999" font-family="Arial" font-size="14">No Image</text>
        <text x="100" y="130" text-anchor="middle" fill="#ccc" font-family="Arial" font-size="10">Medicine</text>
    </svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/categories")
async def get_categories():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    conn.close()
    return {"categories": [dict(r) for r in rows]}


# ---------- CART ----------
@app.get("/api/cart")
async def get_cart(request: Request):
    """Get current user's cart"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT ci.*, p.name, p.price, p.requires_prescription, p.image_url, p.stock_quantity
        FROM cart_items ci
        JOIN products p ON ci.product_id = p.id
        WHERE ci.user_id = ?
    """, (user["id"],)).fetchall()
    conn.close()
    
    items = []
    total = 0
    for row in rows:
        item = dict(row)
        item["subtotal"] = item["quantity"] * item["price"]
        total += item["subtotal"]
        items.append(item)
    
    return {"items": items, "total": total, "count": len(items)}


@app.post("/api/cart/add")
async def add_to_cart(request: Request, body: dict):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    product_id = body.get("product_id")
    quantity = body.get("quantity", 1)
    
    conn = get_db_connection()
    # Check if already in cart
    existing = conn.execute(
        "SELECT id, quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
        (user["id"], product_id)
    ).fetchone()
    
    if existing:
        conn.execute(
            "UPDATE cart_items SET quantity = quantity + ? WHERE id = ?",
            (quantity, existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?,?,?)",
            (user["id"], product_id, quantity)
        )
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Added to cart"}


from contextlib import contextmanager

@contextmanager
def get_db_connection_with_retry(max_retries=3, timeout=20.0):
    """Context manager for database connections with retry logic"""
    conn = None
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
            break
        except sqlite3.OperationalError as e:
            if conn:
                conn.rollback()
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue
            raise
        finally:
            if conn:
                conn.close()

# Update your remove_from_cart to use the context manager
@app.delete("/api/cart/remove/{item_id}")
async def remove_from_cart(request: Request, item_id: int):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    with get_db_connection_with_retry() as conn:
        cursor = conn.cursor()
        # Try deleting by cart_item.id first
        cursor.execute(
            "DELETE FROM cart_items WHERE id = ? AND user_id = ?", 
            (item_id, user["id"])
        )
        
        # If nothing deleted, try by product_id
        if cursor.rowcount == 0:
            cursor.execute(
                "DELETE FROM cart_items WHERE product_id = ? AND user_id = ?", 
                (item_id, user["id"])
            )
        
        if cursor.rowcount == 0:
            raise HTTPException(404, "Cart item not found")
    
    return {"success": True}


@app.delete("/api/cart/clear")
async def clear_cart(request: Request):
    """Remove all items from the current user's cart."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")

    conn = get_db_connection()
    conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user["id"],))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Cart cleared"}


@app.get("/api/orders/{order_id}/items")
async def get_order_items(order_id: int, request: Request):
    """Get all items for a specific order."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")

    conn = get_db_connection()
    order = conn.execute(
        "SELECT id FROM orders WHERE id = ? AND user_id = ?", (order_id, user["id"])
    ).fetchone()
    if not order:
        conn.close()
        raise HTTPException(404, "Order not found")

    items = conn.execute("""
        SELECT oi.*, p.image_url, p.generic_name
        FROM order_items oi
        LEFT JOIN products p ON oi.product_id = p.id
        WHERE oi.order_id = ?
    """, (order_id,)).fetchall()
    conn.close()
    return {"order_id": order_id, "items": [dict(i) for i in items]}


@app.put("/api/cart/update/{product_id}")
async def update_cart_item(request: Request, product_id: int, body: dict):
    """Update quantity of a cart item, keyed by product_id."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")

    quantity = body.get("quantity", 1)

    with get_db_connection_with_retry() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cart_items SET quantity = ? WHERE product_id = ? AND user_id = ?",
            (quantity, product_id, user["id"])
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Cart item not found")

    return {"success": True}


# ---------- CART SHARE (FOR PRESCRIPTION APPROVAL) ----------
@app.post("/api/cart/share")
async def share_cart_with_doctor(request: Request, body: dict):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    doctor_id = body.get("doctor_id")
    patient_message = body.get("message", "")
    
    # Get current cart
    conn = get_db_connection()
    cart_items = conn.execute("""
        SELECT ci.*, p.name, p.generic_name, p.requires_prescription
        FROM cart_items ci
        JOIN products p ON ci.product_id = p.id
        WHERE ci.user_id = ?
    """, (user["id"],)).fetchall()
    
    cart_snapshot = [dict(item) for item in cart_items]
    
    conn.execute("""
        INSERT INTO cart_share_requests 
        (patient_id, doctor_id, cart_snapshot, patient_message, status)
        VALUES (?,?,?,?,'pending')
    """, (user["id"], doctor_id, json.dumps(cart_snapshot), patient_message))
    
    share_id = conn.lastrowid
    conn.commit()
    
    # Notify doctor
    NotificationManager.create(
        doctor_id, 
        "Cart Share Request", 
        f"Patient {user['name']} wants prescription approval for {len(cart_items)} items",
        "cart_share", share_id, "cart_share"
    )
    conn.close()
    
    return {"success": True, "share_id": share_id, "message": "Request sent to doctor"}


@app.get("/api/cart/shares")
async def get_my_cart_shares(request: Request):
    """For patients — get their own cart share requests and their current status."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")

    conn = get_db_connection()
    rows = conn.execute("""
        SELECT cs.*, u.name as doctor_name, d.specialization
        FROM cart_share_requests cs
        JOIN users u ON cs.doctor_id = u.id
        LEFT JOIN doctors_profile d ON u.id = d.user_id
        WHERE cs.patient_id = ?
        ORDER BY cs.created_at DESC
        LIMIT 20
    """, (user["id"],)).fetchall()
    conn.close()

    shares = []
    for row in rows:
        share = dict(row)
        try:
            share["cart_snapshot"] = json.loads(share["cart_snapshot"])
        except Exception:
            share["cart_snapshot"] = []
        shares.append(share)

    return {"shares": shares}


@app.get("/api/cart/shares/pending")
async def get_pending_cart_shares(request: Request):
    """For doctors - get pending cart shares"""
    user = await get_current_user(request)
    if not user or user.get("user_type") != "doctor":
        raise HTTPException(403, "Only doctors can view this")
    
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT cs.*, u.name as patient_name, u.email as patient_email
        FROM cart_share_requests cs
        JOIN users u ON cs.patient_id = u.id
        WHERE cs.doctor_id = ? AND cs.status = 'pending'
        ORDER BY cs.created_at DESC
    """, (user["id"],)).fetchall()
    conn.close()
    
    shares = []
    for row in rows:
        share = dict(row)
        share["cart_snapshot"] = json.loads(share["cart_snapshot"])
        shares.append(share)
    
    return {"shares": shares}


@app.post("/api/cart/share/respond")
async def respond_to_cart_share(request: Request, body: dict):
    user = await get_current_user(request)
    if not user or user.get("user_type") != "doctor":
        raise HTTPException(403, "Only doctors can respond")
    
    share_id = body.get("share_id")
    approve = body.get("approve", False)
    doctor_note = body.get("note", "")
    
    conn = get_db_connection()
    
    if approve:
        # Create prescription
        share = conn.execute(
            "SELECT * FROM cart_share_requests WHERE id = ? AND doctor_id = ?",
            (share_id, user["id"])
        ).fetchone()
        
        if not share:
            conn.close()
            raise HTTPException(404, "Share request not found")
        
        # Create prescription
        conn.execute("""
            INSERT INTO prescriptions (doctor_id, patient_id, cart_request_id, notes, status)
            VALUES (?,?,?,?,'active')
        """, (user["id"], share["patient_id"], share_id, doctor_note))
        
        prescription_id = conn.lastrowid
        
        # Add prescription items from cart snapshot
        cart_items = json.loads(share["cart_snapshot"])
        for item in cart_items:
            conn.execute("""
                INSERT INTO prescription_items 
                (prescription_id, product_id, medicine_name, dosage, duration, quantity)
                VALUES (?,?,?,?,?,?)
            """, (prescription_id, item["product_id"], item["name"], 
                  "As prescribed", "As needed", item["quantity"]))
        
        # Update share request
        conn.execute("""
            UPDATE cart_share_requests 
            SET status = 'approved', doctor_note = ?, prescription_id = ?, responded_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (doctor_note, prescription_id, share_id))
        
        # Notify patient
        NotificationManager.create(
            share["patient_id"],
            "Prescription Approved",
            f"Doctor {user['name']} has approved your medicine request. You can now checkout.",
            "prescription", prescription_id, "prescription"
        )
    else:
        conn.execute("""
            UPDATE cart_share_requests 
            SET status = 'rejected', doctor_note = ?, responded_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (doctor_note, share_id))
        
        share = conn.execute("SELECT patient_id FROM cart_share_requests WHERE id = ?", (share_id,)).fetchone()
        if share:
            NotificationManager.create(
                share["patient_id"],
                "Prescription Request Denied",
                f"Doctor {user['name']} could not approve your request. Note: {doctor_note[:100]}",
                "warning", share_id, "cart_share"
            )
    
    conn.commit()
    conn.close()
    
    return {"success": True}


# ---------- PRESCRIPTIONS ----------
@app.get("/api/prescriptions")
async def get_my_prescriptions(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    
    if user.get("user_type") == "doctor":
        rows = conn.execute("""
            SELECT p.*, u.name as patient_name
            FROM prescriptions p
            JOIN users u ON p.patient_id = u.id
            WHERE p.doctor_id = ?
            ORDER BY p.created_at DESC
        """, (user["id"],)).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.*, u.name as doctor_name
            FROM prescriptions p
            JOIN users u ON p.doctor_id = u.id
            WHERE p.patient_id = ?
            ORDER BY p.created_at DESC
        """, (user["id"],)).fetchall()
    
    conn.close()
    return {"prescriptions": [dict(r) for r in rows]}


@app.get("/api/prescriptions/{prescription_id}/items")
async def get_prescription_items(prescription_id: int):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT pi.*, 
               COALESCE(pi.medicine_name, p.name) as medicine_name,
               p.price, p.id as product_id_exists
        FROM prescription_items pi
        LEFT JOIN products p ON pi.product_id = p.id
        WHERE pi.prescription_id = ?
    """, (prescription_id,)).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


# ---------- ORDERS ----------
@app.post("/api/orders/create")
async def create_order(request: Request, body: dict):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    payment_method = body.get("payment_method", "COD")
    shipping_address = body.get("shipping_address", "")
    prescription_id = body.get("prescription_id")
    
    conn = get_db_connection()
    
    # Get cart items
    cart_items = conn.execute("""
        SELECT ci.*, p.name, p.price, p.requires_prescription
        FROM cart_items ci
        JOIN products p ON ci.product_id = p.id
        WHERE ci.user_id = ?
    """, (user["id"],)).fetchall()
    
    if not cart_items:
        conn.close()
        raise HTTPException(400, "Cart is empty")
    
    # Calculate total
    total = sum(item["quantity"] * item["price"] for item in cart_items)
    
    # Check prescription requirement and ownership
    for item in cart_items:
        if item["requires_prescription"] and not prescription_id:
            conn.close()
            raise HTTPException(400, f"{item['name']} requires a prescription")

    # Verify prescription belongs to this user and is still active
    if prescription_id:
        rx = conn.execute(
            "SELECT id FROM prescriptions WHERE id = ? AND patient_id = ? AND status = 'active'",
            (prescription_id, user["id"])
        ).fetchone()
        if not rx:
            conn.close()
            raise HTTPException(400, "Invalid or already-used prescription")
    
    # Create order
    conn.execute("""
        INSERT INTO orders (user_id, total_amount, payment_method, shipping_address, prescription_id, status)
        VALUES (?,?,?,?,?,'pending')
    """, (user["id"], total, payment_method, shipping_address, prescription_id))
    
    order_id = conn.lastrowid
    
    # Create order items
    for item in cart_items:
        conn.execute("""
            INSERT INTO order_items (order_id, product_id, medicine_name, quantity, price)
            VALUES (?,?,?,?,?)
        """, (order_id, item["product_id"], item["name"], item["quantity"], item["price"]))
        
        # Update stock
        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
            (item["quantity"], item["product_id"])
        )
    
    # Clear cart
    conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user["id"],))
    
    # Update prescription status if used
    if prescription_id:
        conn.execute("UPDATE prescriptions SET status = 'used' WHERE id = ?", (prescription_id,))
    
    conn.commit()
    conn.close()
    
    NotificationManager.create(
        user["id"],
        "Order Confirmed",
        f"Your order #{order_id} has been placed successfully!",
        "order", order_id, "order"
    )
    
    return {"success": True, "order_id": order_id, "total": total}


@app.get("/api/orders")
async def get_my_orders(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT o.*, COUNT(oi.id) as item_count
        FROM orders o
        LEFT JOIN order_items oi ON o.id = oi.order_id
        WHERE o.user_id = ?
        GROUP BY o.id
        ORDER BY o.order_date DESC
    """, (user["id"],)).fetchall()
    conn.close()
    return {"orders": [dict(r) for r in rows]}


@app.get("/api/orders/{order_id}")
async def get_order_details(order_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user["id"])).fetchone()
    if not order:
        conn.close()
        raise HTTPException(404, "Order not found")
    
    items = conn.execute("""
        SELECT * FROM order_items WHERE order_id = ?
    """, (order_id,)).fetchall()
    conn.close()
    
    return {"order": dict(order), "items": [dict(i) for i in items]}


# ---------- DOCTORS ----------
@app.get("/api/doctors")
async def get_doctors(
    specialization: Optional[str] = None,
    search: Optional[str] = None
):
    conn = get_db_connection()
    query = """
        SELECT u.id, u.name, u.email, u.phone, 
               d.specialization, d.qualification, d.experience_years,
               d.consultation_fee, d.bio, d.rating, d.total_consultations,
               d.available_days, d.is_verified
        FROM users u
        JOIN doctors_profile d ON u.id = d.user_id
        WHERE u.user_type = 'doctor' AND u.is_active = 1
    """
    params = []
    
    if specialization:
        query += " AND d.specialization = ?"
        params.append(specialization)
    if search:
        query += " AND (u.name LIKE ? OR d.specialization LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    
    query += " ORDER BY d.rating DESC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    doctors = []
    for row in rows:
        dr = dict(row)
        if dr.get("available_days"):
            dr["available_days"] = json.loads(dr["available_days"])
        doctors.append(dr)
    
    return {"doctors": doctors}


@app.get("/api/doctors/specializations")
async def get_specializations():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT DISTINCT specialization FROM doctors_profile ORDER BY specialization
    """).fetchall()
    conn.close()
    return {"specializations": [r["specialization"] for r in rows]}


# ---------- CONSULTATIONS ----------
@app.post("/api/consultations/request")
async def request_consultation(request: Request, body: dict):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    doctor_id = body.get("doctor_id")
    assessment_id = body.get("assessment_id")
    symptoms_summary = body.get("symptoms_summary", "")
    predicted_disease = body.get("predicted_disease", "")
    patient_message = body.get("message", "")
    
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO consultation_requests 
        (patient_id, doctor_id, assessment_id, symptoms_summary, predicted_disease, 
         patient_message, status)
        VALUES (?,?,?,?,?,?,'pending')
    """, (user["id"], doctor_id, assessment_id, symptoms_summary, predicted_disease, patient_message))
    
    request_id = conn.lastrowid
    conn.commit()
    
    # Get doctor name for notification
    doctor = conn.execute("SELECT name FROM users WHERE id = ?", (doctor_id,)).fetchone()
    
    NotificationManager.create(
        doctor_id,
        "New Consultation Request",
        f"Patient {user['name']} requests consultation. Disease: {predicted_disease[:50]}",
        "consultation", request_id, "consultation"
    )
    conn.close()
    
    return {"success": True, "request_id": request_id}


@app.get("/api/consultations")
async def get_consultations(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    
    if user.get("user_type") == "doctor":
        rows = conn.execute("""
            SELECT cr.*, u.name as patient_name, u.email as patient_email, u.phone as patient_phone,
                   a.symptoms, a.top_disease
            FROM consultation_requests cr
            JOIN users u ON cr.patient_id = u.id
            LEFT JOIN assessments a ON cr.assessment_id = a.id
            WHERE cr.doctor_id = ?
            ORDER BY cr.created_at DESC
        """, (user["id"],)).fetchall()
    else:
        rows = conn.execute("""
            SELECT cr.*, u.name as doctor_name, d.specialization
            FROM consultation_requests cr
            JOIN users u ON cr.doctor_id = u.id
            LEFT JOIN doctors_profile d ON u.id = d.user_id
            WHERE cr.patient_id = ?
            ORDER BY cr.created_at DESC
        """, (user["id"],)).fetchall()
    
    conn.close()
    return {"consultations": [dict(r) for r in rows]}


@app.post("/api/consultations/respond")
async def respond_to_consultation(request: Request, body: dict):
    user = await get_current_user(request)
    if not user or user.get("user_type") != "doctor":
        raise HTTPException(403, "Only doctors can respond")
    
    request_id = body.get("request_id")
    accept = body.get("accept", False)
    doctor_response = body.get("response", "")
    
    conn = get_db_connection()
    
    if accept:
        conn.execute("""
            UPDATE consultation_requests 
            SET status = 'accepted', doctor_response = ?
            WHERE id = ? AND doctor_id = ?
        """, (doctor_response, request_id, user["id"]))
    else:
        conn.execute("""
            UPDATE consultation_requests 
            SET status = 'cancelled', doctor_response = ?
            WHERE id = ? AND doctor_id = ?
        """, (doctor_response, request_id, user["id"]))
    
    conn.commit()
    
    # Get patient info for notification
    req = conn.execute("SELECT patient_id FROM consultation_requests WHERE id = ?", (request_id,)).fetchone()
    if req:
        NotificationManager.create(
            req["patient_id"],
            f"Consultation {'Accepted' if accept else 'Declined'}",
            doctor_response[:100] if doctor_response else f"Your consultation request was {'accepted' if accept else 'declined'}",
            "consultation", request_id, "consultation"
        )
    
    conn.close()
    return {"success": True}


def get_user_from_token_sync(token: str):
    """Helper for async routes"""
    return SessionManager.get_user_from_token(token)


# Update the get_current_user helper
async def get_current_user(request: Request):
    """Extract current user from session token in request"""
    try:
        body = await request.json() if request.method in ["POST", "PUT", "DELETE"] else {}
        token = body.get("session_token")
    except:
        token = None
    
    if not token:
        # Try headers
        token = request.headers.get("X-Session-Token")
    
    if token:
        return SessionManager.get_user_from_token(token)
    return None


# ============================================================
# PRESCRIPTION OCR + INVENTORY MATCHING
# Model: Mistral's Pixtral-12B (free tier via api.mistral.ai)
#        Best open-weight vision model for handwritten medical text.
#        Sign up at https://console.mistral.ai → API Keys (free tier available)
#        Set env var: MISTRAL_API_KEY=your_key
# ============================================================
import os
import re
import httpx
import base64 as b64lib
from fastapi import UploadFile, File

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
if not MISTRAL_API_KEY:
    print("⚠  MISTRAL_API_KEY is not set — prescription OCR will return 503 until the key is provided.")
    print("   Set it with: export MISTRAL_API_KEY=your_key_here")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
MISTRAL_VISION_MODEL = "mistral-small-latest"   # Mistral Small 3.1 (2503) — current vision model, free tier

PRESCRIPTION_SYSTEM_PROMPT = """You are a clinical pharmacist AI specialising in deciphering doctor prescriptions — including heavily cursive or messy handwriting.

Given an image of a prescription, extract EVERY medicine name written on it.
For each medicine output a JSON array of objects with these exact keys:
  - "name": the medicine name as written (best guess, corrected for obvious misspellings)
  - "generic": generic/INN name if you can infer it, else null
  - "dosage": dosage string if visible (e.g. "500mg"), else null
  - "frequency": frequency if visible (e.g. "twice daily"), else null
  - "duration": duration if visible (e.g. "5 days"), else null

Return ONLY the raw JSON array — no markdown, no explanation, no preamble.
If you cannot read the prescription at all return: []"""


import asyncio
import json
from fastapi import HTTPException

async def call_mistral_vision(image_bytes: bytes, mime_type: str, max_retries: int = 3) -> list[dict]:
    """Send prescription image to Mistral Pixtral with rate limit handling."""
    if not MISTRAL_API_KEY:
        raise HTTPException(503, "MISTRAL_API_KEY environment variable is not set.")

    b64_image = b64lib.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": MISTRAL_VISION_MODEL,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                    },
                    {
                        "type": "text",
                        "text": PRESCRIPTION_SYSTEM_PROMPT
                    }
                ]
            }
        ]
    }

    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                MISTRAL_API_URL,
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

        if resp.status_code == 200:
            break  # Success, exit retry loop
        
        # Handle rate limit (429) specifically
        if resp.status_code == 429:
            wait_time = (2 ** attempt) + 1  # Exponential: 1, 3, 7 seconds
            print(f"Rate limited (attempt {attempt + 1}/{max_retries}). Waiting {wait_time}s...")
            await asyncio.sleep(wait_time)
            continue
        
        # Other errors: don't retry
        error_body = resp.text[:500]
        print(f"Mistral API error {resp.status_code}: {error_body}")
        raise HTTPException(502, f"Mistral API error {resp.status_code}: {error_body}")
    else:
        # All retries exhausted
        raise HTTPException(429, "Rate limit exceeded. Please try again in a few minutes.")

    raw_text = resp.json()["choices"][0]["message"]["content"].strip()
    
    # Strip any accidental markdown fences
    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```$", "", raw_text)

    try:
        medicines = json.loads(raw_text)
        if not isinstance(medicines, list):
            medicines = []
    except json.JSONDecodeError:
        medicines = []

    return medicines


def match_medicines_in_inventory(medicines: list[dict]) -> list[dict]:
    """
    For each extracted medicine, search the products table for the best matches.
    Uses multi-strategy fuzzy matching: exact name, generic name, partial LIKE.
    Returns enriched list with matched inventory products.
    """
    conn = get_db_connection()
    results = []

    for med in medicines:
        name_query = (med.get("name") or "").strip()
        generic_query = (med.get("generic") or "").strip()

        matched_products = []

        # Strategy 1: exact name match (case-insensitive)
        for search_term in [name_query, generic_query]:
            if not search_term:
                continue
            rows = conn.execute("""
                SELECT p.*, c.name as category_name
                FROM products p
                LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.is_active = 1
                  AND (LOWER(p.name) = LOWER(?) OR LOWER(p.generic_name) = LOWER(?))
                LIMIT 3
            """, (search_term, search_term)).fetchall()
            for r in rows:
                matched_products.append({"match_type": "exact", **dict(r)})
            if matched_products:
                break

        # Strategy 2: partial / substring match
        if not matched_products:
            for search_term in [name_query, generic_query]:
                if not search_term or len(search_term) < 3:
                    continue
                # Try progressively shorter prefixes to handle messy handwriting
                for prefix_len in [len(search_term), max(4, len(search_term) - 3)]:
                    prefix = search_term[:prefix_len]
                    rows = conn.execute("""
                        SELECT p.*, c.name as category_name
                        FROM products p
                        LEFT JOIN categories c ON p.category_id = c.id
                        WHERE p.is_active = 1
                          AND (p.name LIKE ? OR p.generic_name LIKE ?)
                        LIMIT 5
                    """, (f"{prefix}%", f"{prefix}%")).fetchall()
                    if rows:
                        for r in rows:
                            matched_products.append({"match_type": "partial", **dict(r)})
                        break
                if matched_products:
                    break

        # Strategy 3: word-by-word search (handles "Amox 500" → "Amoxicillin")
        if not matched_products:
            words = [w for w in name_query.split() if len(w) >= 4]
            for word in words:
                rows = conn.execute("""
                    SELECT p.*, c.name as category_name
                    FROM products p
                    LEFT JOIN categories c ON p.category_id = c.id
                    WHERE p.is_active = 1
                      AND (p.name LIKE ? OR p.generic_name LIKE ?)
                    LIMIT 5
                """, (f"%{word}%", f"%{word}%")).fetchall()
                if rows:
                    for r in rows:
                        matched_products.append({"match_type": "keyword", **dict(r)})
                    break

        # Deduplicate by product id
        seen_ids = set()
        unique_matches = []
        for p in matched_products:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                # Clean up image fallback
                if not p.get("image_url"):
                    p["image_url"] = "/static/placeholder.png"
                unique_matches.append(p)

        results.append({
            "prescribed": med,
            "inventory_matches": unique_matches[:3],   # top 3 per medicine
            "found": len(unique_matches) > 0
        })

    conn.close()
    return results


@app.get("/api/prescription/test-key")
async def test_mistral_key():
    """Quick check that the Mistral API key is valid and Pixtral model is accessible."""
    if not MISTRAL_API_KEY:
        return {"ok": False, "error": "MISTRAL_API_KEY is not set"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                MISTRAL_API_URL,
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
                json={"model": MISTRAL_VISION_MODEL, "max_tokens": 10,
                      "messages": [{"role": "user", "content": "Hi"}]}
            )
        if resp.status_code == 200:
            return {"ok": True, "model": MISTRAL_VISION_MODEL, "status": "API key valid ✓ (Mistral Small 3.1 vision)"}
        else:
            return {"ok": False, "status_code": resp.status_code, "error": resp.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/prescription/analyze")
async def analyze_prescription(file: UploadFile = File(...)):
    """
    Upload a prescription image (JPG/PNG/WEBP/PDF-first-page).
    1. Mistral Pixtral-12B reads the handwritten text and extracts medicines.
    2. Each medicine is matched against the local inventory (products table).
    Returns extracted medicines + best inventory matches for each.

    Requires env var: MISTRAL_API_KEY
    """
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    content_type = file.content_type or "image/jpeg"
    if content_type not in allowed_types:
        raise HTTPException(400, f"Unsupported file type: {content_type}. Use JPEG, PNG, or WEBP.")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(400, "Image too large. Maximum 10 MB.")

    # Step 1: Extract medicines via Mistral Pixtral
    medicines = await call_mistral_vision(image_bytes, content_type)

    if not medicines:
        return {
            "success": True,
            "medicines_extracted": [],
            "inventory_results": [],
            "message": "No medicines could be extracted from this image. "
                       "Please ensure the prescription is clearly photographed."
        }

    # Step 2: Match against inventory
    inventory_results = match_medicines_in_inventory(medicines)

    total_found = sum(1 for r in inventory_results if r["found"])

    return {
        "success": True,
        "medicines_extracted": medicines,
        "inventory_results": inventory_results,
        "summary": {
            "total_medicines": len(medicines),
            "found_in_inventory": total_found,
            "not_found": len(medicines) - total_found
        },
        "model_used": MISTRAL_VISION_MODEL
    }


# ============================================================
# RUN SERVER
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("Panacea Unified API Server v4.2")
    print("="*50)
    print(f"Server running at: http://localhost:8000")
    print(f"\nFrontend URLs:")
    print(f"  Main page:     http://localhost:8000/")
    print(f"  Assessment:    http://localhost:8000/assessment")
    print(f"\n✨ New Endpoints:")
    print(f"  GET  /api/diseases               - List all diseases")
    print(f"  GET  /api/diseases/search        - Search diseases")
    print(f"  GET  /api/diseases/{{name}}       - Get complete disease details")
    print(f"  GET  /api/diseases/{{name}}/summary - Get simplified summary")
    print(f"\n📊 Prediction now includes disease_info field")
    print(f"\n👤 Profile Page Endpoints (NEW):")
    print(f"  GET  /api/profile/{{user_id}}/summary    - Profile + metrics + counts")
    print(f"  DELETE /api/profile/{{user_id}}/assessments/{{id}} - Delete assessment")
    print(f"  DELETE /api/profile/{{user_id}}/records/{{id}}     - Delete medical record")
    print(f"\n🔬 Prescription Analysis (NEW):")
    print(f"  POST /api/prescription/analyze  - Upload Rx image → Mistral Small 3.1 vision OCR → inventory match")
    print(f"  Model: mistral-small-latest  (Mistral Small 3.1, current free-tier vision model)")
    print("="*50 + "\n")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)