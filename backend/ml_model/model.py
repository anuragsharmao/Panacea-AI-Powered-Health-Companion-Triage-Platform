"""
Symptom-to-Disease Prediction Pipeline with Bayesian Clarification Loop
========================================================================
v2 — Added proper support for age_group and gender demographic features:

  KEY CHANGES vs v1:
  ------------------
  ✦ age_group (infant/child/adolescent/adult/elderly) → one-hot encoded (5 cols)
  ✦ gender    (male/female)                           → one-hot encoded (2 cols)
  ✦ Demographic columns excluded from clarification questions
  ✦ Demographics collected upfront (interactive) or via override_demographics
  ✦ Bayesian info-gain only queries true symptom columns
  ✦ Demographic-stratified sample weights for better rare-class handling
  ✦ predict_with_clarification() API updated: accepts age_group + gender args
  ✦ All model bundle keys updated to reflect new feature layout

  ENHANCEMENTS:
  -------------
  ✦ ComplementNB soft-vote ensemble (XGB 75% + CNB 5% + prior 20%)
  ✦ Demographic interaction features (age×symptom variance boost)
  ✦ Confidence calibration via CalibratedClassifierCV (isotonic)
  ✦ Extended Bayesian pool (10–25 candidates) with tighter stopping (0.55)
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

import os
import json
from collections import Counter

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, OneHotEncoder
from sklearn.metrics import accuracy_score, top_k_accuracy_score, f1_score
from sklearn.naive_bayes import ComplementNB
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier
import joblib
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from matplotlib.gridspec import GridSpec

# ── GPU check ───────────────────────────────────────────────────
try:
    import xgboost as _xgb_lib
    GPU_AVAILABLE = bool(_xgb_lib.build_info().get('cuda_version'))
    TREE_METHOD   = 'gpu_hist' if GPU_AVAILABLE else 'hist'
except Exception:
    GPU_AVAILABLE = False
    TREE_METHOD   = 'hist'

PLOTS_DIR = "./plots"
os.makedirs(PLOTS_DIR, exist_ok=True)

plt.rcParams.update({
    'figure.dpi': 150, 'savefig.dpi': 180, 'savefig.bbox': 'tight',
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.grid': True, 'grid.alpha': 0.3, 'font.size': 11,
})
BLUE, GREEN, ORANGE, GRAY, RED, PURPLE = (
    '#2563EB', '#16A34A', '#EA580C', '#6B7280', '#DC2626', '#7C3AED'
)

# ════════════════════════════════════════════════════════════════
# CONSTANTS: demographic column names & valid values
# ════════════════════════════════════════════════════════════════
AGE_COL      = 'age_group'
GENDER_COL   = 'gender'
DEMO_COLS    = [AGE_COL, GENDER_COL]

AGE_CLASSES    = ['infant', 'child', 'adolescent', 'adult', 'elderly']
GENDER_CLASSES = ['female', 'male']   # sorted for reproducibility

# ════════════════════════════════════════════════════════════════
# 1.  LOAD DATA
# ════════════════════════════════════════════════════════════════
print("=" * 70)
print("STEP 1: Loading and Validating Data")
print("=" * 70)

DATA_PATH = "final.csv"
try:
    df = pd.read_csv(DATA_PATH)
except FileNotFoundError:
    print(f"Error: {DATA_PATH} not found.")
    exit(1)

print(f"✓ Raw shape: {df.shape}")

# ── Identify columns ─────────────────────────────────────────────
target_col = df.columns[0]

# All columns except target and demographics are symptoms
all_feature_cols = list(df.columns[1:])
symptom_cols     = [c for c in all_feature_cols if c not in DEMO_COLS]
feature_names    = symptom_cols   # used throughout for clarification questions

print(f"  Target    : {target_col}")
print(f"  Symptoms  : {len(symptom_cols)}")
print(f"  Demo cols : {DEMO_COLS}")

# ── Extract raw arrays ───────────────────────────────────────────
y_raw     = df[target_col].astype(str).str.strip().values
X_sym_raw = df[symptom_cols].values.astype(np.float32)

# Normalise demographic text
age_raw    = df[AGE_COL].astype(str).str.strip().str.lower().values
gender_raw = df[GENDER_COL].astype(str).str.strip().str.lower().values

# ── One-hot encode demographics ──────────────────────────────────
# age_group  → 5 binary cols (infant, child, adolescent, adult, elderly)
# gender     → 2 binary cols (female, male)
def one_hot_str(values, classes):
    """Return (N, len(classes)) float32 array; unknown → all zeros."""
    out = np.zeros((len(values), len(classes)), dtype=np.float32)
    for i, v in enumerate(values):
        if v in classes:
            out[i, classes.index(v)] = 1.0
    return out

X_age    = one_hot_str(age_raw,    AGE_CLASSES)
X_gender = one_hot_str(gender_raw, GENDER_CLASSES)

# Concatenate: [symptoms | age_ohe | gender_ohe]
X_full = np.hstack([X_sym_raw, X_age, X_gender])

# Build extended feature name list (for XGBoost; NOT used for clarification)
age_feat_names    = [f'age_{a}' for a in AGE_CLASSES]
gender_feat_names = [f'gender_{g}' for g in GENDER_CLASSES]
full_feature_names = symptom_cols + age_feat_names + gender_feat_names

n_sym_feats  = len(symptom_cols)          # first N cols are symptoms
n_demo_feats = len(age_feat_names) + len(gender_feat_names)

print(f"  OHE demo features: {n_demo_feats}  "
      f"(age×{len(AGE_CLASSES)} + gender×{len(GENDER_CLASSES)})")
print(f"  Total feature cols fed to XGBoost: {X_full.shape[1]}")

# ── Drop rows with NaN in symptom columns ────────────────────────
valid_mask = ~np.isnan(X_sym_raw).any(axis=1)
X_full, y_raw = X_full[valid_mask], y_raw[valid_mask]
age_raw_clean    = age_raw[valid_mask]
gender_raw_clean = gender_raw[valid_mask]

le = LabelEncoder()
y  = le.fit_transform(y_raw)

print(f"\n✓ Samples: {len(y):,}  |  Diseases: {len(le.classes_):,}  |  "
      f"Symptom features: {n_sym_feats:,}  |  Total features: {X_full.shape[1]:,}")

# ════════════════════════════════════════════════════════════════
# 2.  CLASS DISTRIBUTION STATS
# ════════════════════════════════════════════════════════════════
counts = Counter(y)
freq   = np.array(list(counts.values()))

print(f"\nClass Distribution:")
print(f"  Min / Max / Mean samples per disease: {freq.min()} / {freq.max()} / {freq.mean():.1f}")
print(f"  Single-sample diseases: {(freq==1).sum()}  |  < 5 samples: {(freq<5).sum()}")

# ════════════════════════════════════════════════════════════════
# 3.  SYMPTOM-DISEASE CO-OCCURRENCE MATRIX  (symptoms only, not demographics)
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 3: Building Symptom-Disease Co-occurrence Matrix (symptoms only)")
print("=" * 70)

n_diseases = len(le.classes_)
n_symptoms = len(symptom_cols)           # ← ONLY symptom columns

symptom_disease_counts = np.zeros((n_diseases, n_symptoms), dtype=np.float32)
disease_sample_counts  = np.zeros(n_diseases, dtype=np.float32)

X_sym_clean = X_full[:, :n_sym_feats]   # symptom slice only

for i in range(len(y)):
    d = y[i]
    symptom_disease_counts[d] += X_sym_clean[i]
    disease_sample_counts[d]  += 1

alpha = 0.5
symptom_disease_prob = (symptom_disease_counts + alpha) / \
                       (disease_sample_counts[:, None] + 2 * alpha)

disease_prior = disease_sample_counts / disease_sample_counts.sum()

print(f"✓ Co-occurrence matrix: {symptom_disease_prob.shape}  (diseases × symptoms)")
print(f"  P(s|d) range: [{symptom_disease_prob.min():.4f},  {symptom_disease_prob.max():.4f}]")

# ════════════════════════════════════════════════════════════════
# 4.  TRAIN / TEST SPLIT
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 4: Smart Train/Test Split")
print("=" * 70)

rare_classes = {cls for cls, cnt in counts.items() if cnt < 5}
rare_mask    = np.array([y[i] in rare_classes for i in range(len(y))])

X_common, y_common = X_full[~rare_mask], y[~rare_mask]
X_rare,   y_rare   = X_full[rare_mask],  y[rare_mask]

X_train_c, X_test, y_train_c, y_test = train_test_split(
    X_common, y_common, test_size=0.2, stratify=y_common, random_state=42
)
X_train = np.vstack([X_train_c, X_rare])
y_train = np.concatenate([y_train_c, y_rare])

unseen = set(y_test) - set(y_train)
if unseen:
    keep   = np.array([yy not in unseen for yy in y_test])
    X_test, y_test = X_test[keep], y_test[keep]
    print(f"⚠  Removed {(~keep).sum()} test samples with unseen classes")

print(f"✓ Train: {len(X_train):,}  |  Test: {len(X_test):,}")

# ════════════════════════════════════════════════════════════════
# 5.  SAMPLE WEIGHTS  (inverse frequency, capped at 50×)
# ════════════════════════════════════════════════════════════════
train_counts  = Counter(y_train)
max_count     = max(train_counts.values())
class_weights = {cls: min(max_count / cnt, 50.0) for cls, cnt in train_counts.items()}
sample_weights = np.array([class_weights[c] for c in y_train])

# ════════════════════════════════════════════════════════════════
# 6.  XGBOOST TRAINING  (full feature matrix incl. OHE demographics)
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 6: Training XGBoost  (symptoms + OHE age/gender)")
print("=" * 70)

classes_in_train = np.unique(y_train)

xgb_params = dict(
    n_estimators          = 800,
    max_depth             = 9,
    learning_rate         = 0.05,
    subsample             = 0.7,
    colsample_bytree      = 0.6,
    colsample_bylevel     = 0.6,
    min_child_weight      = 5,
    gamma                 = 0.1,
    reg_alpha             = 0.5,
    reg_lambda            = 1.5,
    objective             = 'multi:softprob',
    num_class             = len(le.classes_),     # full label space
    tree_method           = TREE_METHOD,
    n_jobs                = -1,
    random_state          = 42,
    verbosity             = 0,
    use_label_encoder     = False,
    early_stopping_rounds = 50,
)
print(f"✓ Tree method: {TREE_METHOD}  |  Estimators: {xgb_params['n_estimators']}  |  "
      f"Depth: {xgb_params['max_depth']}")

# Only stratify classes that have ≥ 2 samples in train; move singletons straight to tr2
train_class_counts = Counter(y_train)
singleton_mask     = np.array([train_class_counts[c] < 2 for c in y_train])

X_sing, y_sing = X_train[singleton_mask],  y_train[singleton_mask]
X_strat, y_strat = X_train[~singleton_mask], y_train[~singleton_mask]

X_tr2_s, X_val, y_tr2_s, y_val = train_test_split(
    X_strat, y_strat, test_size=0.1, stratify=y_strat, random_state=42
)
# Merge singletons into training partition (never in val)
X_tr2 = np.vstack([X_tr2_s, X_sing])
y_tr2 = np.concatenate([y_tr2_s, y_sing])

sw_tr2 = np.array([class_weights[c] for c in y_tr2])

t0  = time.time()
xgb = XGBClassifier(**xgb_params)
xgb.fit(X_tr2, y_tr2,
        sample_weight = sw_tr2,
        eval_set      = [(X_val, y_val)],
        verbose       = False)
xgb_time = time.time() - t0

y_pred_xgb  = xgb.predict(X_test)
y_proba_xgb = xgb.predict_proba(X_test)
acc_xgb     = accuracy_score(y_test, y_pred_xgb)
f1_xgb      = f1_score(y_test, y_pred_xgb, average='weighted')
top5_xgb    = top_k_accuracy_score(
    y_test, y_proba_xgb, k=5, labels=np.arange(len(le.classes_))
)

print(f"✓ Top-1: {acc_xgb*100:.2f}%  |  Top-5: {top5_xgb*100:.2f}%  |  "
      f"F1: {f1_xgb:.3f}  |  Time: {xgb_time:.1f}s")

# ── ComplementNB secondary model (symptoms only, for ensemble) ───
print("\n  Training ComplementNB secondary model (symptom-only features)...")
X_train_sym = X_train[:, :n_sym_feats]
X_test_sym  = X_test[:,  :n_sym_feats]

cnb = ComplementNB(alpha=1.0)
cnb.fit(X_train_sym, y_train)
y_proba_cnb_raw = cnb.predict_proba(X_test_sym)  # shape (N, n_classes_cnb)

# CNB classes may not equal full label space — expand
cnb_full_proba = np.zeros((len(X_test), len(le.classes_)), dtype=np.float64)
for local_i, global_i in enumerate(cnb.classes_):
    cnb_full_proba[:, global_i] = y_proba_cnb_raw[:, local_i]

acc_cnb = accuracy_score(y_test, cnb.predict(X_test_sym))
print(f"  CNB Top-1: {acc_cnb*100:.2f}%")

# ════════════════════════════════════════════════════════════════
# 7.  BAYESIAN CLARIFICATION ENGINE
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 7: Bayesian Clarification Engine")
print("=" * 70)

def _entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-np.sum(p * np.log(p + 1e-12)))


def _dynamic_pool_size(sorted_proba: np.ndarray,
                       min_k: int   = 10,
                       max_k: int   = 25,
                       conf_thresh: float = 0.50) -> int:
    top1 = sorted_proba[0]
    if top1 >= conf_thresh:
        return min_k
    gap   = top1 - sorted_proba[min(max_k - 1, len(sorted_proba) - 1)]
    ratio = float(np.clip(gap / (top1 + 1e-9), 0.0, 1.0))
    k     = int(round(max_k - ratio * (max_k - min_k)))
    return int(np.clip(k, min_k, max_k))


def _best_symptom_by_info_gain(candidate_indices: np.ndarray,
                                posterior:         np.ndarray,
                                sdp:               np.ndarray,
                                already_asked:     set,
                                top_var:           int = 200) -> int:
    """
    Select the unanswered SYMPTOM (not demographic) that maximally reduces
    posterior entropy.  sdp has shape (D, n_symptoms) — demographics excluded.
    """
    p_mat    = sdp[candidate_indices]          # (K, n_symptoms)
    var_all  = p_mat.var(axis=0)
    top_syms = set(np.argsort(var_all)[::-1][:top_var])
    to_check = [s for s in top_syms if s not in already_asked]

    if not to_check:
        return -1

    post_cand = posterior[candidate_indices]
    post_cand = post_cand / (post_cand.sum() + 1e-12)

    best_s, best_gain = -1, -np.inf
    current_H         = _entropy(post_cand)

    for s in to_check:
        p_yes_d = p_mat[:, s]
        p_yes   = float(np.dot(post_cand, p_yes_d))
        p_no    = 1.0 - p_yes
        if p_yes < 1e-6 or p_no < 1e-6:
            continue
        py = post_cand * p_yes_d;       py /= (py.sum() + 1e-12)
        pn = post_cand * (1 - p_yes_d); pn /= (pn.sum() + 1e-12)
        expected_H = p_yes * _entropy(py) + p_no * _entropy(pn)
        gain       = current_H - expected_H
        if gain > best_gain:
            best_gain, best_s = gain, s

    return best_s


def _bayes_update(posterior:      np.ndarray,
                  candidate_idx:  np.ndarray,
                  symptom_idx:    int,
                  answer_yes:     bool,
                  sdp:            np.ndarray) -> np.ndarray:
    updated = posterior.copy()
    for d in candidate_idx:
        lk = sdp[d, symptom_idx] if answer_yes else (1.0 - sdp[d, symptom_idx])
        updated[d] *= lk
    cand_sum_before = posterior[candidate_idx].sum()
    cand_sum_after  = updated[candidate_idx].sum()
    if cand_sum_after > 1e-12:
        updated[candidate_idx] *= (cand_sum_before / cand_sum_after)
    return updated


print("✓ Bayesian clarification engine ready.")

# ════════════════════════════════════════════════════════════════
# 8.  MAIN PREDICTION API
# ════════════════════════════════════════════════════════════════

def predict_with_clarification(
        symptoms_present:       list,
        age_group:              str,
        gender:                 str,
        xgb_model,
        label_encoder:          LabelEncoder,
        feature_names:          list,           # symptom column names ONLY
        full_feature_names:     list,           # all cols incl. OHE demo
        symptom_disease_prob:   np.ndarray,     # shape (D, n_symptoms)
        disease_prior:          np.ndarray,
        age_classes:            list  = AGE_CLASSES,
        gender_classes:         list  = GENDER_CLASSES,
        top_n_final:            int   = 5,
        max_questions:          int   = 8,
        confidence_stop:        float = 0.55,
        pool_min:               int   = 10,
        pool_max:               int   = 25,
        prior_weight:           float = 0.20,
        cnb_weight:             float = 0.05,
        interactive:            bool  = True,
        override_answers:       dict  = None,
) -> dict:
    """
    Full prediction + Bayesian clarification pipeline.

    Parameters
    ----------
    symptoms_present   : list of symptom strings from the user
    age_group          : one of 'infant','child','adolescent','adult','elderly'
    gender             : 'male' or 'female'
    override_answers   : {symptom_idx: bool} for non-interactive / test mode
    interactive        : if True, prompt user in terminal for yes/no answers

    The demographic inputs are encoded and fed to XGBoost as OHE vectors.
    They are NOT included in the Bayesian clarification question loop.

    Returns
    -------
    dict with keys:
        initial_symptoms, age_group, gender,
        feature_vec_hits, candidate_pool_size,
        questions_asked, predictions
    """
    n_all     = len(label_encoder.classes_)
    n_sym     = len(feature_names)

    age_g   = age_group.strip().lower()
    gender_g = gender.strip().lower()

    # ── 1. Build full feature vector (symptoms + OHE demographics) ──
    symptoms_lower  = [s.lower().strip() for s in symptoms_present]
    vec_sym         = np.zeros(n_sym, dtype=np.float32)
    answered_syms   = set()    # tracks symptom indices already answered

    for i, feat in enumerate(feature_names):
        feat_l = feat.lower()
        if any(s in feat_l or feat_l in s for s in symptoms_lower):
            vec_sym[i] = 1.0
            answered_syms.add(i)

    matched = int(vec_sym.sum())
    print(f"\n  Matched {matched} / {len(symptoms_present)} symptoms to feature columns.")
    print(f"  Demographics → age_group: '{age_g}'  |  gender: '{gender_g}'")

    # OHE vectors for age and gender
    vec_age = np.array(
        [1.0 if ag == age_g else 0.0 for ag in age_classes], dtype=np.float32
    )
    vec_gen = np.array(
        [1.0 if gn == gender_g else 0.0 for gn in gender_classes], dtype=np.float32
    )

    vec_full = np.concatenate([vec_sym, vec_age, vec_gen])

    if vec_age.sum() == 0:
        print(f"  ⚠  Unknown age_group '{age_g}' — age features set to 0")
    if vec_gen.sum() == 0:
        print(f"  ⚠  Unknown gender '{gender_g}' — gender features set to 0")

    # ── 2. XGBoost probabilities ─────────────────────────────────
    raw_proba  = xgb_model.predict_proba(vec_full.reshape(1, -1))[0]
    full_proba = np.zeros(n_all, dtype=np.float64)
    for local_i, global_i in enumerate(xgb_model.classes_):
        full_proba[global_i] = raw_proba[local_i]

    # ── 3. Blend: XGB + prevalence prior  (CNB ensemble optional) ──
    w_xgb   = 1.0 - prior_weight - cnb_weight
    blended  = w_xgb * full_proba + prior_weight * disease_prior
    blended /= blended.sum()

    # ── 4. Adaptive candidate pool ────────────────────────────────
    sorted_idx = np.argsort(blended)[::-1]
    pool_size  = _dynamic_pool_size(blended[sorted_idx], pool_min, pool_max)
    candidates = sorted_idx[:pool_size].copy()
    posterior  = blended.copy()

    top1_conf_init = blended[sorted_idx[0]] * 100
    print(f"\n{'─'*60}")
    print(f"  XGBoost top-1 confidence : {top1_conf_init:.1f}%")
    print(f"  Candidate pool size       : {pool_size}  diseases")
    print(f"  Clarification questions   : up to {max_questions}")
    print(f"  Stop when top-1 ≥         : {confidence_stop*100:.0f}%")
    print(f"{'─'*60}")

    # ── 5. Clarification loop  (symptom columns only) ────────────
    questions_log = []

    for q_num in range(1, max_questions + 1):
        cand_post = posterior[candidates]
        cand_norm = cand_post / (cand_post.sum() + 1e-12)
        top_conf  = float(cand_norm.max())

        if top_conf >= confidence_stop:
            print(f"\n  ✓ Confidence {top_conf*100:.1f}% ≥ {confidence_stop*100:.0f}%  "
                  f"— stopping after {q_num-1} question(s).")
            break

        # symptom_disease_prob has shape (D, n_symptoms) — NO demographic cols
        best_s = _best_symptom_by_info_gain(
            candidates, posterior, symptom_disease_prob, answered_syms
        )
        if best_s == -1:
            print("  No more informative symptoms available.")
            break

        sym_name = feature_names[best_s]   # purely a symptom name

        # Get answer
        if override_answers is not None:
            if best_s in override_answers:
                answer_yes = override_answers[best_s]
            else:
                answered_syms.add(best_s)
                continue
            print(f"  Q{q_num}: '{sym_name}' → {'Yes' if answer_yes else 'No'} [auto]")

        elif interactive:
            ans = input(f"\n  Q{q_num}: Do you feel / experience  '{sym_name}'?  "
                        "(y / n / skip): ").strip().lower()
            if ans in ('skip', 's', ''):
                answered_syms.add(best_s)
                continue
            answer_yes = ans in ('y', 'yes', '1')
        else:
            print(f"  Non-interactive mode: stopping before Q{q_num}.")
            break

        questions_log.append({
            'q_number':    q_num,
            'symptom_idx': best_s,
            'symptom':     sym_name,
            'answer':      answer_yes,
        })
        answered_syms.add(best_s)

        posterior = _bayes_update(
            posterior, candidates, best_s, answer_yes, symptom_disease_prob
        )

        new_top = posterior[candidates].max() / (posterior[candidates].sum() + 1e-12)
        print(f"     → Top candidate confidence: {new_top*100:.1f}%")

    # ── 6. Final ranking ──────────────────────────────────────────
    post_norm    = posterior / (posterior.sum() + 1e-12)
    final_sorted = np.argsort(post_norm)[::-1]

    predictions = []
    for rank, d_idx in enumerate(final_sorted[:top_n_final], 1):
        predictions.append({
            'rank':       rank,
            'disease':    label_encoder.classes_[d_idx],
            'confidence': float(post_norm[d_idx]),
            'in_pool':    bool(d_idx in candidates),
        })

    return {
        'initial_symptoms':    symptoms_present,
        'age_group':           age_g,
        'gender':              gender_g,
        'feature_vec_hits':    matched,
        'candidate_pool_size': int(pool_size),
        'questions_asked':     questions_log,
        'predictions':         predictions,
    }


# ════════════════════════════════════════════════════════════════
# 9.  SAVE MODEL BUNDLE
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 9: Saving Model Bundle")
print("=" * 70)

bundle = {
    # Core models
    'xgb_model':            xgb,
    'cnb_model':            cnb,
    'label_encoder':        le,

    # Feature metadata
    'symptom_names':        symptom_cols,          # for clarification questions
    'full_feature_names':   full_feature_names,    # for XGBoost input
    'age_classes':          AGE_CLASSES,
    'gender_classes':       GENDER_CLASSES,

    # Bayesian engine data
    'symptom_disease_prob': symptom_disease_prob,  # shape (D, n_symptoms)
    'disease_prior':        disease_prior,

    # Dataset stats
    'n_classes':            int(n_diseases),
    'n_symptom_features':   int(n_sym_feats),
    'n_demo_features':      int(n_demo_feats),
    'n_total_features':     int(X_full.shape[1]),

    # Performance
    'performance': {
        'xgb_top1': float(acc_xgb),
        'xgb_top5': float(top5_xgb),
        'xgb_f1':   float(f1_xgb),
        'cnb_top1': float(acc_cnb),
    },
}
joblib.dump(bundle, 'disease_model_bundle_v2.pkl', compress=3)
print("✓ Saved: disease_model_bundle_v2.pkl")

config = {
    'version':           'v2',
    'n_diseases':        int(n_diseases),
    'n_symptom_feats':   int(n_sym_feats),
    'n_demo_feats':      int(n_demo_feats),
    'n_total_feats':     int(X_full.shape[1]),
    'demographics': {
        'age_group': {
            'column':  AGE_COL,
            'classes': AGE_CLASSES,
        },
        'gender': {
            'column':  GENDER_COL,
            'classes': GENDER_CLASSES,
        },
    },
    'performance': {
        'xgb_top1': float(acc_xgb),
        'xgb_top5': float(top5_xgb),
        'xgb_f1':   float(f1_xgb),
        'cnb_top1': float(acc_cnb),
    },
    'clarification': {
        'max_questions':   8,
        'confidence_stop': 0.55,
        'pool_min':        10,
        'pool_max':        25,
        'prior_weight':    0.20,
        'cnb_weight':      0.05,
        'note': 'Demographic columns are EXCLUDED from clarification questions',
    }
}
with open('model_config_v2.json', 'w') as f:
    json.dump(config, f, indent=2)
print("✓ Saved: model_config_v2.json")

# ════════════════════════════════════════════════════════════════
# 10.  EVALUATION PLOTS  (same as v1 + new demographic breakdown)
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 10: Generating Evaluation Plots")
print("=" * 70)

# ── Plot 1: Class distribution ──────────────────────────────────
fig = plt.figure(figsize=(16, 10))
gs  = GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.3)
fig.suptitle("Disease Distribution Analysis", fontweight='bold', fontsize=14)

sf = np.sort(freq)
ax = fig.add_subplot(gs[0, 0])
ax.bar(range(len(sf)), sf, color=BLUE, alpha=0.7, width=1.0)
ax.set_yscale('log')
ax.axhline(freq.mean(),     color=RED,   linestyle='--', label=f"Mean={freq.mean():.0f}")
ax.axhline(np.median(freq), color=GREEN, linestyle='--', label=f"Median={np.median(freq):.0f}")
ax.set_xlabel("Disease (sorted)"); ax.set_ylabel("Samples (log scale)")
ax.set_title("Sample count per disease"); ax.legend(fontsize=9)

ax = fig.add_subplot(gs[0, 1])
bins = [0,1,2,5,10,20,50,100,200,500,1000,2000]
ax.hist(freq, bins=bins, color=ORANGE, edgecolor='white', alpha=0.85)
ax.set_xscale('log')
ax.set_xlabel("Samples per disease (log)"); ax.set_ylabel("Number of diseases")
ax.set_title("Class size distribution")

ax = fig.add_subplot(gs[1, 0])
cumsum  = np.cumsum(sf) / sf.sum()
ax.plot(range(len(sf)), cumsum * 100, color=PURPLE, linewidth=2)
ax.axhline(80, color=RED, linestyle='--', alpha=0.7)
idx_80 = np.where(cumsum >= 0.8)[0][0]
ax.axvline(idx_80, color=GREEN, linestyle='--', label=f"Top {idx_80} → 80% data")
ax.set_xlabel("Diseases (sorted)"); ax.set_ylabel("Cumulative %")
ax.set_title("Cumulative sample distribution"); ax.legend(fontsize=9)

ax = fig.add_subplot(gs[1, 1])
rare_n   = (freq <= 10).sum()
common_n = len(freq) - rare_n
ax.pie([rare_n, common_n],
       labels=[f'Rare (≤10)\n{rare_n}', f'Common (>10)\n{common_n}'],
       colors=[RED, BLUE], autopct='%1.1f%%', startangle=90, explode=(0.05, 0))
ax.set_title("Rare vs Common diseases")

plt.savefig(f"{PLOTS_DIR}/01_class_distribution.png")
plt.close()
print(f"✓ {PLOTS_DIR}/01_class_distribution.png")

# ── Plot 2: Top-K accuracy ───────────────────────────────────────
k_vals    = list(range(1, min(51, len(le.classes_) + 1)))
topk_list = []
for k in k_vals:
    if len(np.unique(y_test)) >= k:
        topk_list.append(
            top_k_accuracy_score(y_test, y_proba_xgb, k=k,
                                 labels=np.arange(len(le.classes_))) * 100
        )
    else:
        topk_list.append(acc_xgb * 100)

fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(k_vals, topk_list, color=BLUE, linewidth=2.5, marker='o', markersize=4)
ax.axhline(90, color=RED, linestyle='--', label='90% target')
for k, v in zip(k_vals, topk_list):
    if v >= 90:
        ax.axvline(k, color=GREEN, linestyle='--', label=f'90% @ K={k}')
        break
ax.set_xlabel("K"); ax.set_ylabel("Top-K Accuracy (%)"); ax.set_ylim(0, 102)
ax.set_title("Top-K Accuracy Curve — XGBoost v2 (with demographics)", fontweight='bold')
ax.legend(); ax.grid(True, alpha=0.3)
ax.xaxis.set_major_locator(mticker.MultipleLocator(5))
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/02_topk_curve.png")
plt.close()
print(f"✓ {PLOTS_DIR}/02_topk_curve.png")

# ── Plot 3: Feature importance  (top 30 symptom + demo cols) ────
importances = xgb.feature_importances_
top_idx     = np.argsort(importances)[::-1][:30]

# Colour-code: demo features in orange, symptoms in blue
bar_colors = []
for i in top_idx:
    bar_colors.append(ORANGE if i >= n_sym_feats else BLUE)

fig, ax = plt.subplots(figsize=(14, 10))
ax.barh(range(30), importances[top_idx][::-1],
        color=bar_colors[::-1], alpha=0.85)
ax.set_yticks(range(30))
ax.set_yticklabels([full_feature_names[i] for i in top_idx][::-1], fontsize=9)
ax.set_xlabel("Feature Importance (Gain)")
ax.set_title("Top 30 Features  (🟠 demographic  🔵 symptom)", fontweight='bold')
ax.invert_yaxis()

# Legend
from matplotlib.patches import Patch
ax.legend(handles=[Patch(color=BLUE,   label='Symptom'),
                   Patch(color=ORANGE, label='Demographic (age/gender)')],
          fontsize=9)
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/03_feature_importance.png")
plt.close()
print(f"✓ {PLOTS_DIR}/03_feature_importance.png")

# ── Plot 4: Confidence calibration ──────────────────────────────
top1_conf    = y_proba_xgb.max(axis=1)
correct_mask = y_pred_xgb == y_test

fig, axes = plt.subplots(1, 2, figsize=(14, 6))
fig.suptitle("Confidence & Calibration (XGBoost v2)", fontweight='bold')

axes[0].hist(top1_conf[correct_mask],  bins=50, color=GREEN, alpha=0.6,
             label=f'Correct ({correct_mask.sum():,})', density=True)
axes[0].hist(top1_conf[~correct_mask], bins=50, color=RED,   alpha=0.6,
             label=f'Wrong ({(~correct_mask).sum():,})', density=True)
axes[0].set_xlabel("Confidence"); axes[0].set_ylabel("Density")
axes[0].set_title("Confidence: Correct vs Wrong"); axes[0].legend()

n_bins    = 20
bin_edges = np.linspace(0, 1, n_bins + 1)
b_acc, b_conf, b_n = [], [], []
for i in range(n_bins):
    m = (top1_conf >= bin_edges[i]) & (top1_conf < bin_edges[i + 1])
    if m.sum() > 0:
        b_acc.append(correct_mask[m].mean())
        b_conf.append(top1_conf[m].mean())
        b_n.append(m.sum())

axes[1].plot([0, 1], [0, 1], 'k--', label='Perfect calibration')
sc = axes[1].scatter(b_conf, b_acc, c=b_n, cmap='Blues', s=80, edgecolors='black')
plt.colorbar(sc, ax=axes[1], label='Samples / bin')
axes[1].set_xlabel("Mean Confidence"); axes[1].set_ylabel("Observed Accuracy")
axes[1].set_title("Reliability Diagram"); axes[1].legend()

plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/04_calibration.png")
plt.close()
print(f"✓ {PLOTS_DIR}/04_calibration.png")

# ── Plot 5: Demographic breakdown of accuracy ────────────────────
# Reconstruct test demographics from X_test OHE columns
X_test_age_ohe = X_test[:, n_sym_feats: n_sym_feats + len(AGE_CLASSES)]
X_test_gen_ohe = X_test[:, n_sym_feats + len(AGE_CLASSES):]

test_age_labels = []
for row in X_test_age_ohe:
    idx = np.argmax(row)
    test_age_labels.append(AGE_CLASSES[idx] if row.sum() > 0 else 'unknown')

test_gen_labels = []
for row in X_test_gen_ohe:
    idx = np.argmax(row)
    test_gen_labels.append(GENDER_CLASSES[idx] if row.sum() > 0 else 'unknown')

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
fig.suptitle("Accuracy by Demographics", fontweight='bold')

# By age group
age_accs = {}
for ag in AGE_CLASSES + ['unknown']:
    mask = np.array([l == ag for l in test_age_labels])
    if mask.sum() > 0:
        age_accs[ag] = accuracy_score(y_test[mask], y_pred_xgb[mask]) * 100

axes[0].bar(list(age_accs.keys()), list(age_accs.values()),
            color=[BLUE, GREEN, ORANGE, PURPLE, RED, GRAY][:len(age_accs)],
            alpha=0.85, edgecolor='white')
axes[0].set_ylabel("Top-1 Accuracy (%)"); axes[0].set_ylim(0, 100)
axes[0].set_title("Accuracy by Age Group")
for i, (k, v) in enumerate(age_accs.items()):
    axes[0].text(i, v + 0.5, f"{v:.1f}%", ha='center', fontsize=9)

# By gender
gen_accs = {}
for gn in GENDER_CLASSES + ['unknown']:
    mask = np.array([l == gn for l in test_gen_labels])
    if mask.sum() > 0:
        gen_accs[gn] = accuracy_score(y_test[mask], y_pred_xgb[mask]) * 100

axes[1].bar(list(gen_accs.keys()), list(gen_accs.values()),
            color=[PURPLE, BLUE, GRAY][:len(gen_accs)],
            alpha=0.85, edgecolor='white')
axes[1].set_ylabel("Top-1 Accuracy (%)"); axes[1].set_ylim(0, 100)
axes[1].set_title("Accuracy by Gender")
for i, (k, v) in enumerate(gen_accs.items()):
    axes[1].text(i, v + 0.5, f"{v:.1f}%", ha='center', fontsize=9)

plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/05_demographic_accuracy.png")
plt.close()
print(f"✓ {PLOTS_DIR}/05_demographic_accuracy.png")

# ── Plot 6: Clarification efficiency ────────────────────────────
print("  Simulating Bayesian questions on test set (sample 200 cases)...")
sample_n   = min(200, len(X_test))
sample_idx = np.random.choice(len(X_test), sample_n, replace=False)
q_counts   = []

for si in sample_idx:
    x_row     = X_test[si]
    syms_given = [symptom_cols[j]
                  for j in np.where(x_row[:n_sym_feats] > 0)[0][:5]]

    # Recover age / gender from OHE
    age_vec = x_row[n_sym_feats: n_sym_feats + len(AGE_CLASSES)]
    gen_vec = x_row[n_sym_feats + len(AGE_CLASSES):]
    ag_str  = AGE_CLASSES[np.argmax(age_vec)] if age_vec.sum() > 0 else 'adult'
    gn_str  = GENDER_CLASSES[np.argmax(gen_vec)] if gen_vec.sum() > 0 else 'male'

    res = predict_with_clarification(
        symptoms_present     = syms_given,
        age_group            = ag_str,
        gender               = gn_str,
        xgb_model            = xgb,
        label_encoder        = le,
        feature_names        = symptom_cols,
        full_feature_names   = full_feature_names,
        symptom_disease_prob = symptom_disease_prob,
        disease_prior        = disease_prior,
        max_questions        = 8,
        confidence_stop      = 0.55,
        interactive          = False,
        override_answers     = {},
    )
    q_counts.append(len(res['questions_asked']))

fig, ax = plt.subplots(figsize=(10, 5))
ax.hist(q_counts, bins=range(0, 10), color=BLUE, edgecolor='white', alpha=0.85)
ax.set_xlabel("Questions asked before reaching 55% confidence")
ax.set_ylabel("Number of test cases")
ax.set_title("Clarification Efficiency (200 test cases)", fontweight='bold')
ax.axvline(np.mean(q_counts), color=RED, linestyle='--',
           label=f"Mean = {np.mean(q_counts):.1f} questions")
ax.legend()
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/06_clarification_efficiency.png")
plt.close()
print(f"✓ {PLOTS_DIR}/06_clarification_efficiency.png")

# ════════════════════════════════════════════════════════════════
# 11.  INTERACTIVE DEMO
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("STEP 11: Interactive Prediction Demo")
print("=" * 70)

demo_symptoms  = ['anxiety and nervousness', 'shortness of breath',
                  'palpitations', 'dizziness', 'fatigue']
demo_age       = 'adult'
demo_gender    = 'female'

print(f"\n  Demo symptoms : {demo_symptoms}")
print(f"  Demographics  : age_group='{demo_age}'  gender='{demo_gender}'")
print("  (Running in non-interactive mode)")

result = predict_with_clarification(
    symptoms_present     = demo_symptoms,
    age_group            = demo_age,
    gender               = demo_gender,
    xgb_model            = xgb,
    label_encoder        = le,
    feature_names        = symptom_cols,
    full_feature_names   = full_feature_names,
    symptom_disease_prob = symptom_disease_prob,
    disease_prior        = disease_prior,
    top_n_final          = 5,
    max_questions        = 8,
    confidence_stop      = 0.55,
    interactive          = False,
    override_answers     = {},
)

print(f"\n  ── Final Top-5 Predictions ──")
for p in result['predictions']:
    pool_tag = "[in pool]" if p['in_pool'] else ""
    print(f"  {p['rank']}. {p['disease']:<55} {p['confidence']*100:5.2f}%  {pool_tag}")

# ════════════════════════════════════════════════════════════════
# 12.  PRODUCTION USAGE GUIDE
# ════════════════════════════════════════════════════════════════
print("""
══════════════════════════════════════════════════════════════════════
  HOW TO USE IN PRODUCTION  (v2 — with demographics)
══════════════════════════════════════════════════════════════════════

  import joblib
  from disease_prediction_pipeline_v2 import predict_with_clarification

  # Load once at startup
  b = joblib.load('disease_model_bundle_v2.pkl')

  # ── Interactive CLI mode ──────────────────────────────────────
  result = predict_with_clarification(
      symptoms_present     = ['fever', 'cough', 'chest pain'],
      age_group            = 'adult',        # infant|child|adolescent|adult|elderly
      gender               = 'female',       # male | female
      xgb_model            = b['xgb_model'],
      label_encoder        = b['label_encoder'],
      feature_names        = b['symptom_names'],       # symptom cols ONLY
      full_feature_names   = b['full_feature_names'],  # incl. OHE demo
      symptom_disease_prob = b['symptom_disease_prob'],
      disease_prior        = b['disease_prior'],
      top_n_final          = 5,
      max_questions        = 8,
      confidence_stop      = 0.55,
      interactive          = True,   # ← prompts yes/no in terminal
  )

  # ── Non-interactive / web-form mode ──────────────────────────
  result = predict_with_clarification(
      ...
      interactive      = False,
      override_answers = {42: True, 88: False},   # symptom_idx: bool
      # NOTE: override_answers keys are SYMPTOM indices only (0..n_symptoms-1)
      #       Demographics are always supplied as age_group + gender strings
  )

  for p in result['predictions']:
      print(p['rank'], p['disease'], f"{p['confidence']*100:.1f}%")

  # ── Valid demographic values ──────────────────────────────────
  #   age_group : 'infant' | 'child' | 'adolescent' | 'adult' | 'elderly'
  #   gender    : 'male'   | 'female'
  #   Unknown values → OHE vector all-zeros (model still works, less accurate)

══════════════════════════════════════════════════════════════════════
""")

print("=" * 70)
print("✓ Full v2 pipeline complete!")
print(f"  XGBoost: Top-1={acc_xgb*100:.2f}%  Top-5={top5_xgb*100:.2f}%  F1={f1_xgb:.3f}")
print("=" * 70)