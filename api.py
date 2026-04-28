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
from concurrent.futures import ThreadPoolExecutor

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
    
    conn.commit()
    conn.close()
    print("✓ Database initialized successfully")

init_database()

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
        
        for field in ['allergies', 'chronic_conditions', 'current_medications']:
            if field in metrics and metrics[field] is not None:
                if isinstance(metrics[field], (list, dict)):
                    metrics[field] = json.dumps(metrics[field])
        
        updates = {k: v for k, v in metrics.items() if k in allowed_fields}
        if not updates:
            return False
        
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
            for field in ['allergies', 'chronic_conditions', 'current_medications']:
                if result.get(field):
                    try:
                        result[field] = json.loads(result[field])
                    except:
                        pass
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
        expires_at = datetime.now() + timedelta(days=expires_days)
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
            SELECT u.id, u.email, u.name, u.age, u.gender, u.profile_completed
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
# FAST BAYESIAN CLARIFICATION API ENDPOINTS
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
            "disease_info": get_disease_info(disease_name)  # NEW: Add disease details
        })
    return results

class ClarifyStartRequest(BaseModel):
    symptoms_present: List[str]
    age_group: str = "adult"
    gender: str = "female"
    max_questions: int = 12        # frontend can override
    min_questions: int = 5         # always ask at least this many
    confidence_stop: float = 0.92  # very high threshold — must be extremely confident to stop early
    prior_weight: float = 0.10     # lower = XGBoost less dominant = more Bayesian questions
    fast_mode: bool = False
    exhaustive: bool = False       # if True, always ask max_questions regardless of confidence

class ClarifyAnswerRequest(BaseModel):
    session_id: str
    symptom_name: str
    answer: bool

@app.post("/clarify/start")
async def clarify_start(req: ClarifyStartRequest):
    if not req.symptoms_present:
        raise HTTPException(400, "symptoms_present cannot be empty")

    if req.fast_mode:
        full_vec, matched = build_feature_vector(
            req.symptoms_present, req.age_group, req.gender, {}
        )
        preds = top_predictions(full_vec, req.prior_weight, 5)
        explanations = await get_feature_contributions_async(full_vec)
        return {
            "session_id": None,
            "done": True,
            "question": None,
            "question_number": 0,
            "top_confidence": preds[0]["confidence"] if preds else 0,
            "predictions": preds,
            "explanations": explanations,
            "matched_count": matched
        }

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
    cand_post = posterior[candidates]
    top_conf = float((cand_post / (cand_post.sum() + 1e-12)).max())
    session_id = str(_uuid.uuid4())

    async def _done_response(sid, preds, explanations, qnum=0):
        return {
            "session_id": sid,
            "done": True,
            "question": None,
            "question_number": qnum,
            "top_confidence": top_conf,
            "predictions": preds,
            "explanations": explanations,
            "matched_count": matched
        }

    # Only stop early at start if confidence is very high AND we don't need min_questions
    # With exhaustive=True, ALWAYS ask questions regardless of confidence
    if top_conf >= req.confidence_stop and not req.exhaustive and req.min_questions == 0:
        preds = _make_final_predictions(posterior, candidates)
        fv, _ = build_feature_vector(req.symptoms_present, age_group, gender, {})
        explanations = await get_feature_contributions_async(fv)
        return await _done_response(session_id, preds, explanations)

    best_s = _best_question(candidates, posterior, answered)
    if best_s == -1:
        preds = _make_final_predictions(posterior, candidates)
        fv, _ = build_feature_vector(req.symptoms_present, age_group, gender, {})
        explanations = await get_feature_contributions_async(fv)
        return await _done_response(session_id, preds, explanations)

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
    }
    
    question_explanation = ""
    for s in SYMPTOM_CATALOGUE:
        if s["symptom_name"].lower() == SYMPTOM_NAMES[best_s].lower():
            question_explanation = s.get("explanation", "")
            break
    
    return {
        "session_id": session_id,
        "done": False,
        "question": SYMPTOM_NAMES[best_s],
        "question_index": best_s,
        "question_explanation": question_explanation,
        "question_number": 1,
        "top_confidence": top_conf,
        "predictions": None,
        "explanations": None,
        "matched_count": matched
    }

@app.post("/clarify/answer")
async def clarify_answer(req: ClarifyAnswerRequest):
    sess = _CLARIFY_SESSIONS.get(req.session_id)
    if not sess:
        raise HTTPException(404, "Clarification session not found or expired")

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

    cand_post = posterior[candidates]
    top_conf = float((cand_post / (cand_post.sum() + 1e-12)).max())

    async def _done_async(sid):
        preds = _make_final_predictions(posterior, candidates)
        fv, _ = build_feature_vector(sess["symptoms_present"], sess["age_group"], sess["gender"], {})
        explanations = await get_feature_contributions_async(fv)
        del _CLARIFY_SESSIONS[sid]
        return {
            "session_id": sid,
            "done": True,
            "question": None,
            "question_number": sess["q_count"],
            "top_confidence": top_conf,
            "predictions": preds,
            "explanations": explanations,
            "matched_count": sess["matched_count"]
        }

    q_count = sess["q_count"]
    min_q = sess.get("min_questions", 5)
    max_q = sess["max_questions"]
    conf_stop = sess["confidence_stop"]
    exhaustive = sess.get("exhaustive", False)

    # Determine if we should stop:
    # - Always continue if we haven't hit min_questions yet
    # - Always stop if we've hit max_questions
    # - Stop on confidence only if: past min_questions AND not exhaustive mode
    past_min = q_count >= min_q
    hit_max  = q_count >= max_q
    hit_conf = top_conf >= conf_stop and past_min and not exhaustive

    if hit_max or hit_conf:
        return await _done_async(req.session_id)

    best_s = _best_question(candidates, posterior, answered)
    if best_s == -1 and past_min:
        return await _done_async(req.session_id)
    elif best_s == -1:
        # No good question but haven't hit min_questions — return done anyway
        return await _done_async(req.session_id)

    answered.add(best_s)
    sess["answered"] = answered
    
    question_explanation = ""
    for s in SYMPTOM_CATALOGUE:
        if s["symptom_name"].lower() == SYMPTOM_NAMES[best_s].lower():
            question_explanation = s.get("explanation", "")
            break
    
    return {
        "session_id": req.session_id,
        "done": False,
        "question": SYMPTOM_NAMES[best_s],
        "question_index": best_s,
        "question_explanation": question_explanation,
        "question_number": sess["q_count"],
        "top_confidence": top_conf,
        "predictions": None,
        "explanations": None,
        "matched_count": sess["matched_count"]
    }

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
        "session_id": sid,
        "done": True,
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
@app.get("/api/products")
async def get_products(
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    prescription_only: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0
):
    """Get catalog of medicines"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT p.*, c.name as category_name 
        FROM products p 
        LEFT JOIN categories c ON p.category_id = c.id 
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
    
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = cursor.execute(query, params).fetchall()
    conn.close()
    
    return {"products": [dict(r) for r in rows], "count": len(rows)}


@app.get("/api/products/{product_id}")
async def get_product(product_id: int):
    conn = get_db_connection()
    row = conn.execute("""
        SELECT p.*, c.name as category_name 
        FROM products p 
        LEFT JOIN categories c ON p.category_id = c.id 
        WHERE p.id = ?
    """, (product_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Product not found")
    return dict(row)


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


@app.delete("/api/cart/remove/{item_id}")
async def remove_from_cart(request: Request, item_id: int):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    conn = get_db_connection()
    conn.execute("DELETE FROM cart_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    conn.commit()
    conn.close()
    return {"success": True}


@app.put("/api/cart/update/{item_id}")
async def update_cart_item(request: Request, item_id: int, body: dict):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Please login")
    
    quantity = body.get("quantity", 1)
    conn = get_db_connection()
    conn.execute(
        "UPDATE cart_items SET quantity = ? WHERE id = ? AND user_id = ?",
        (quantity, item_id, user["id"])
    )
    conn.commit()
    conn.close()
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
    
    # Check prescription requirement
    for item in cart_items:
        if item["requires_prescription"] and not prescription_id:
            conn.close()
            raise HTTPException(400, f"{item['name']} requires a prescription")
    
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
    print("="*50 + "\n")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)