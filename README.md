# Panacea — AI-Powered Health Companion & Triage Platform

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/FastAPI-0.104-009688?style=for-the-badge&logo=fastapi&logoColor=white"/>
  <img src="https://img.shields.io/badge/XGBoost-2.0-EB5E28?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/SQLite-Database-07405E?style=for-the-badge&logo=sqlite&logoColor=white"/>
  <img src="https://img.shields.io/badge/Encryption-AES--256--GCM-1f6f43?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Mistral_AI-Vision_OCR-FF7000?style=for-the-badge"/>
</p>

> **Panacea** is a production-grade, AI-powered health companion that takes a user from "something feels wrong" to **diagnosis, doctor consultation, and prescription fulfillment** — all in one platform. At its core is an **XGBoost + ComplementNB ensemble** wrapped in a custom **Bayesian clarification engine** that asks the single most informative follow-up question at every step, narrowing 773 possible conditions in real time, with sub-100ms feature-contribution explanations. The platform is rounded out with an encrypted health-records vault, telemedicine consultation requests, a full e-pharmacy with cart/order management, and an AI vision-OCR prescription scanner.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Platform Interface](#platform-interface)
- [System Architecture](#system-architecture)
- [Machine Learning Pipeline](#machine-learning-pipeline)
  - [Dataset](#dataset)
  - [Feature Engineering](#feature-engineering)
  - [Model Architecture — XGBoost + ComplementNB Ensemble](#model-architecture--xgboost--complementnb-ensemble)
  - [Bayesian Clarification Engine](#bayesian-clarification-engine)
  - [Training & Evaluation](#training--evaluation)
- [Security & Data Engineering](#security--data-engineering)
- [API Reference](#api-reference)
- [Frontend](#frontend)
- [File Structure](#file-structure)
- [Setup & Installation](#setup--installation)
- [Environment Variables](#environment-variables)
- [Tech Stack](#tech-stack)
- [Contributing](#contributing)
- [License](#license)

---

## Project Overview

Symptom checkers are usually either too simplistic (keyword matching against a static list) or black boxes that spit out a single guess with no reasoning. **Panacea** addresses this by reasoning the way a clinician would: start from a broad symptom set, blend model predictions with population priors, then ask the *single most informative* follow-up question to narrow the differential — repeating until confidence is high enough to stop.

**What makes Panacea different from a simple symptom checker:**

- It combines an **XGBoost multi-class classifier** with a **ComplementNB secondary model** in a soft-vote ensemble, calibrated with isotonic regression so confidence scores are meaningful.
- It layers a **from-scratch Bayesian active-learning engine** on top — using entropy and information gain over a 377×773 symptom-disease probability matrix to pick the next best yes/no question.
- It is **demographic-aware**: age group (infant → elderly) and gender are modeled as first-class one-hot features, with demographic-stratified sample weighting and dedicated accuracy breakdowns.
- It returns **feature contribution explanations in under 100ms**, plus a fully cross-referenced **773-disease knowledge base** (description, severity tiers, recommended specialist) on every prediction.
- It extends beyond diagnosis into a full product: **encrypted medical records (AES-256-GCM)**, **telemedicine consultation requests**, an **e-pharmacy with cart sharing and order pipeline**, and an **AI vision-OCR prescription scanner** (Mistral Pixtral) that auto-matches handwritten prescriptions to live inventory.

---

## Platform Interface

The frontend is a 7-page responsive vanilla JS application (`index.html`, `assessment.html`, `consultation.html`, `pharmacy.html`, `cart.html`, `patient-dashboard.html`, `doctor-dashboard.html`) sharing a unified design system (`style.css`, `script.js`).

| Page | Purpose |
|---|---|
| **Home** | Landing page & platform overview |
| **Assessment** | Symptom input → live AI diagnosis flow with interactive Bayesian Q&A |
| **Consultation** | Browse specialists, request telemedicine consultations |
| **Pharmacy** | Browse/search medicines, categories, and product catalog |
| **Cart** | Cart management, cart-sharing between users, order placement |
| **Patient Dashboard** | Encrypted health records vault, assessment history, profile & metrics |
| **Doctor Dashboard** | Consultation request management for verified doctors |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                              │
│   index │ assessment │ consultation │ pharmacy │ cart │ dashboards │
│              (Vanilla JS + style.css, no framework)                │
└─────────────────────────────┬──────────────────────────────────────┘
                              │  HTTP (JSON)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FASTAPI BACKEND  (api.py)                       │
│                                                                    │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │                    INFERENCE PIPELINE                       │    │
│  │                                                              │    │
│  │  Symptoms + Age Group + Gender                              │    │
│  │       │                                                      │    │
│  │       ├──► Feature Vector Builder                           │    │
│  │       │     (377 symptom dims + age/gender one-hot)         │    │
│  │       │                                                      │    │
│  │       ▼                                                      │    │
│  │   XGBoost (multi:softprob, 773 classes)                     │    │
│  │       +  ComplementNB (symptom-only)                        │    │
│  │       +  Disease Prior  ──► Soft-Vote Blend + Calibration   │    │
│  │       │                                                      │    │
│  │       ▼                                                      │    │
│  │   Bayesian Posterior Pool (10–30 candidate diseases)         │    │
│  │       │                                                      │    │
│  │       ▼                                                      │    │
│  │   Info-Gain Question Engine                                  │    │
│  │     (entropy reduction over symptom-disease matrix,          │    │
│  │      Bayes posterior update on each answer)                  │    │
│  │       │                                                      │    │
│  │       ▼                                                      │    │
│  │   Top-5 Ranked Diagnoses + Feature Contributions              │    │
│  │     + Disease Knowledge Base (severity, specialist, etc.)     │    │
│  └───────────────────────────────────────────────────────────┘    │
│                                                                    │
│  ┌──────────────┐ ┌────────────────┐ ┌──────────────┐ ┌─────────┐ │
│  │ Auth &        │ │ Telemedicine    │ │ Pharmacy &    │ │ Rx OCR  │ │
│  │ Profiles       │ │ Consultations    │ │ Orders/Cart    │ │ (Mistral│ │
│  │               │ │                 │ │               │ │ Vision) │ │
│  └──────────────┘ └────────────────┘ └──────────────┘ └─────────┘ │
└──────────────┬──────────────────────────────┬──────────────────────┘
               │                              │
   ┌───────────▼──────────┐      ┌────────────▼─────────────┐
   │   ML Model Layer       │      │   SQLite + AES-256-GCM     │
   │  XGBoost / CNB / LE /  │      │  18+ tables, encrypted PII │
   │  Symptom-Disease Matrix│      │  (database.py)             │
   └────────────────────────┘      └─────────────────────────┘
                              │
                              ▼
                     JSON Response returned
                              │
                              ▼
                     Client renders result
```

---

## Machine Learning Pipeline

### Dataset

The model is trained on a structured symptom-disease dataset spanning **773 diseases** and **377 unique symptoms** (`diseases.json`, `symptoms.json`). Each symptom entry includes a `symptom_name`, `gender_specific` flag (`both` / `male` / `female`), and a plain-language `explanation` shown to users. Each disease entry includes `disease_name`, `primary_severity`, `secondary_severity`, `description`, and recommended `specialist`(s) — this knowledge base is joined directly into every prediction response.

**Key columns / dimensions used:**

| Field | Type | Description |
|---|---|---|
| `symptom_present` (×377) | Binary | One-hot symptom indicators forming the core feature space |
| `age_group` | Categorical → one-hot (5) | `infant`, `child`, `adolescent`, `adult`, `elderly` |
| `gender` | Categorical → one-hot (2) | `female`, `male` |
| `disease_name` (target, ×773) | Categorical | Label-encoded via `LabelEncoder` |
| `primary_severity` / `secondary_severity` | Categorical | e.g. `mild`, `moderate`, `life_threatening` (used in knowledge base, not training labels) |
| `specialist` | Text | Recommended specialist(s) per disease, joined into prediction output |

**Class imbalance handling:** the dataset is highly imbalanced across 773 disease classes (some have only a handful of samples). Singleton-sample classes are routed directly into the training partition (never into validation), and **demographic-stratified sample weights** (`class_weights`) are computed to reduce bias toward common conditions.

---

### Feature Engineering

Each case is transformed into a feature vector combining two feature types:

#### 1. Symptom Indicator Features (377 dimensions)

Binary one-hot indicators across all 377 catalogued symptoms (`build_feature_vector`), matched against the user's free-text symptom input.

#### 2. Demographic One-Hot Features (7 dimensions)

| # | Feature Group | Description |
|---|---|---|
| 1–5 | `age_group_*` | One-hot encoding of `infant / child / adolescent / adult / elderly` |
| 6–7 | `gender_*` | One-hot encoding of `female / male` |

Demographic columns are **excluded** from the Bayesian clarification questions (only true symptom columns are eligible info-gain questions), but are included as XGBoost training features — plus **age × symptom interaction terms** that boost variance for demographic-sensitive conditions.

---

### Model Architecture — XGBoost + ComplementNB Ensemble

Panacea uses a **soft-vote ensemble + Bayesian re-ranking** architecture rather than a single classifier:

#### Primary Model — XGBoost (`multi:softprob`)

| Setting | Value |
|---|---|
| Objective | `multi:softprob` over 773 disease classes |
| Tree method | `gpu_hist` if CUDA available, else `hist` |
| Regularization | `gamma=0.1`, `reg_alpha=0.5`, `reg_lambda=1.5` |
| Early stopping | `early_stopping_rounds=50` on a held-out validation split |
| Sample weighting | Demographic-stratified class weights |

#### Secondary Model — ComplementNB

A `ComplementNB` classifier trained on **symptom-only features** (demographics excluded), used as a lightweight secondary signal that complements XGBoost's tree-based decision boundaries — particularly useful for sparse, rare-disease symptom combinations.

#### Calibration

Confidence outputs are calibrated via **`CalibratedClassifierCV` (isotonic)**, so the reported probability for the top prediction reflects true observed accuracy at that confidence level — visualized in the reliability diagram (see [Plots](#training--evaluation)).

#### Soft-Vote Blend with Disease Prior

At inference time, the raw XGBoost probability distribution is blended with a population-level **disease prior** (`PRIOR`):

```python
blended = (1 - prior_weight) * full_prob + prior_weight * PRIOR
blended /= blended.sum()
```

`prior_weight` defaults to `0.10` and is configurable per request — higher values pull predictions toward population base rates when symptom evidence is weak.

#### Decision Output

The blended posterior is sorted, a **dynamic candidate pool** of 10–30 diseases is selected based on confidence concentration (see below), and the **Top-5 ranked diagnoses** are returned — each enriched with the full disease knowledge base entry (`description`, `primary_severity`, `secondary_severity`, `specialist`).

---

### Bayesian Clarification Engine

This is Panacea's signature component: a **from-scratch Bayesian active-learning loop** that decides, after every answer, which single yes/no question will most reduce diagnostic uncertainty.

#### 1. Dynamic Candidate Pool Sizing

```python
def _dynamic_pool_size(sorted_proba, min_k=10, max_k=30, conf_thresh=0.50):
    top1 = sorted_proba[0]
    if top1 >= conf_thresh:
        return min_k
    gap = top1 - sorted_proba[max_k - 1]
    ratio = clip(gap / (top1 + 1e-9), 0, 1)
    return clip(round(max_k - ratio * (max_k - min_k)), min_k, max_k)
```

If the model is already confident, the pool shrinks to 10 candidates (fewer questions needed). If the top prediction is weak and the field is wide open, the pool expands toward 30 candidates.

#### 2. Best-Question Selection (Information Gain)

For each candidate question:
1. Compute the **current entropy** of the normalized posterior over candidate diseases.
2. For each unanswered symptom, simulate a "yes" and "no" branch using the **symptom-disease probability matrix (SDP)**.
3. Compute the **expected entropy after the answer** (weighted by `P(yes)` / `P(no)`).
4. Pick the symptom that **maximizes entropy reduction** (information gain).

To keep this fast, candidate symptoms are first pre-filtered to the **top-200 by posterior variance** before the full info-gain scan runs.

#### 3. Bayesian Posterior Update

```python
def _apply_bayes_update(posterior, candidates, sym_idx, answer_yes):
    for d in candidates:
        lk = SDP[d, sym_idx] if answer_yes else (1 - SDP[d, sym_idx])
        posterior[d] *= lk
    # renormalize within candidate set
```

Each yes/no answer multiplies the posterior of every candidate disease by its symptom likelihood, then renormalizes — a textbook Bayesian update applied live, in milliseconds.

#### 4. Stopping Conditions

| Parameter | Default | Behavior |
|---|---|---|
| `min_questions` | 3 | Always ask at least this many questions |
| `max_questions` | 12 (8 in plots simulation) | Hard cap on questions per session |
| `confidence_stop` | 0.95 (0.55 in evaluation) | Stop early once top candidate's posterior exceeds this, *after* `min_questions` |

#### Feature Contribution Explanations (<100ms)

Every `/predict` and `/clarify/finalize` response includes **per-symptom feature contribution scores**, computed asynchronously (`get_feature_contributions_async`) so the user sees *why* each disease was suggested, not just a label and a number.

---

### Training & Evaluation

Run `model.py` to train the full pipeline from scratch:

```bash
python model.py
```

The training script saves **17 diagnostic visualizations** to the `backend/ml_model/plots/` directory, covering data distribution, model performance, calibration, and the Bayesian clarification engine:

| Plot File | Description |
|---|---|
| `01_class_distribution.png` | Class size distribution across 773 diseases (log scale), cumulative sample share curve highlighting the "top-N diseases → 80% of data" point, and a rare-vs-common disease pie chart (≤10 samples = rare) |
| `02_symptom_frequency.png` | Frequency distribution of the 377 symptoms across the dataset — highlights the most and least commonly reported symptoms |
| `02_topk_curve.png` | Top-K accuracy curve (K = 1 → 50) for the XGBoost ensemble, with the K at which 90% accuracy is first reached highlighted |
| `03_feature_importance.png` | Top feature importances (gain) from XGBoost, color-coded — symptom features vs demographic (age/gender) features |
| `04_calibration.png` | Two-panel calibration view: confidence distribution for correct vs incorrect predictions, plus a reliability diagram (predicted confidence vs observed accuracy, binned) |
| `04_model_comparison.png` | Side-by-side comparison of Precision, Recall, F1, and accuracy across the XGBoost, ComplementNB, and ensemble models |
| `05_demographic_accuracy.png` | Top-1 accuracy broken down by age group (infant/child/adolescent/adult/elderly) and by gender, with per-bar accuracy labels |
| `05_per_class_accuracy.png` | Per-disease-class accuracy distribution — surfaces which diseases the model predicts most/least reliably |
| `06_clarification_efficiency.png` | Histogram of how many Bayesian clarification questions were needed to reach target posterior confidence, across simulated test cases, with the mean overlaid |
| `06_worst_best_diseases.png` | Ranking of the best- and worst-performing disease classes by prediction accuracy |
| `07_confidence_analysis.png` | Distribution of model confidence scores across all predictions, segmented by correctness |
| `08_feature_importance.png` | Extended feature importance view across the full 384-dimensional feature space (377 symptoms + 7 demographic features) |
| `09_topk_accuracy_curve.png` | Final Top-K accuracy curve for the production model bundle, used as the headline evaluation chart |
| `10_confusion_matrix_top30.png` | Confusion matrix restricted to the 30 most frequent disease classes, for readability |
| `11_support_vs_accuracy.png` | Scatter plot of per-class accuracy vs. training sample count (support) — visualizes the impact of class imbalance on performance |

The trained pipeline is serialized via `joblib` as `disease_model_bundle_v2.pkl` and includes:

```python
{
    'xgb_model':            trained XGBClassifier,
    'cnb_model':            trained ComplementNB,
    'label_encoder':        LabelEncoder over 773 disease names,
    'calibrator':           CalibratedClassifierCV (isotonic),
    'symptom_cols':         list of 377 symptom feature names,
    'full_feature_names':   377 symptom + 7 demographic feature names,
    'symptom_disease_prob': SDP matrix (symptom-disease likelihoods),
    'disease_prior':        population prior over 773 diseases,
    'age_classes':          ['infant','child','adolescent','adult','elderly'],
    'gender_classes':       ['female','male'],
    'performance_metrics':  dict of Top-1, Top-5, F1, demographic accuracies
}
```

**Reported metrics** (printed during training): Top-1 accuracy, Top-5 accuracy, weighted F1-score, ComplementNB Top-1 accuracy, and per-demographic Top-1 accuracy breakdowns.

---

## Security & Data Engineering

- **AES-256-GCM field-level encryption** for sensitive health data — `allergies`, `chronic_conditions`, `current_medications`, `emergency_contact_name`, `emergency_contact_phone` — encrypted before being written to SQLite via `encrypt_sensitive_metrics()` / `decrypt_sensitive_metrics()`.
- The AES key is derived via **SHA-256** from an environment variable (`PANACEA_SECRET_KEY`), so no cryptographic material lives in source code.
- **Graceful decryption fallback**: if a stored value isn't valid ciphertext (e.g. pre-encryption legacy records), it's returned as-is rather than throwing — supporting safe migrations.
- **18+ relational tables** (`database.py`): `users`, `doctors_profile`, `health_metrics`, `assessments`, `consultation_requests`, `prescriptions`, `prescription_items`, `categories`, `products`, `product_images`, `cart_items`, `cart_share_requests`, `orders`, `order_items`, `notifications`, `medical_records`, `sessions`, plus migration support.
- Dedicated manager classes for **session management**, **notifications**, and **product images**, keeping cross-cutting concerns out of route handlers.

---

## API Reference

The FastAPI server exposes **60+ endpoints**. Selected highlights:

### `GET /health`

Returns system status — used by the frontend for connection/health checks.

---

### `POST /predict`

Main diagnosis endpoint. Accepts symptoms plus demographics and returns ranked predictions, explanations, and disease knowledge.

**Request Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `symptoms_present` | list[string] | Yes | List of symptom names |
| `age_group` | string | No | `infant`, `child`, `adolescent`, `adult`, `elderly` (aliases like `teen`, `senior`, `baby` are auto-mapped) |
| `gender` | string | No | `female`, `male` |
| `prior_weight` | float | No | Weight given to the population disease prior (default `0.10`) |
| `top_n_final` | int | No | Number of ranked predictions to return |
| `override_answers` | dict | No | Manual symptom overrides |

**Example Response:**
```json
{
  "predictions": [
    {
      "rank": 1,
      "disease": "abdominal aortic aneurysm",
      "confidence": 0.62,
      "in_pool": true,
      "disease_info": {
        "description": "A permanent, localized bulge in the aorta...",
        "primary_severity": "mild",
        "secondary_severity": "life_threatening",
        "specialist": "vascular surgeon, cardiologist"
      }
    }
  ],
  "explanations": ["..."],
  "matched_count": 4,
  "age_group": "adult",
  "gender": "female"
}
```

---

### `POST /clarify/start`

Begins a Bayesian clarification session. Builds the initial posterior, candidate pool, and returns the first info-gain question.

| Field | Type | Default |
|---|---|---|
| `symptoms_present` | list[string] | required |
| `age_group` / `gender` | string | `adult` / `female` |
| `max_questions` | int | 12 |
| `min_questions` | int | 3 |
| `confidence_stop` | float | 0.95 |
| `prior_weight` | float | 0.10 |

### `POST /clarify/answer`

Submits a yes/no answer (`session_id`, `symptom_name`, `answer`), applies the Bayesian update, and returns the next question or final results.

### `GET /clarify/result/{session_id}`

Retrieves the current ranked predictions for an in-progress or completed session.

### `POST /clarify/finalize`

Forces a session to finalize early and return Top-5 predictions with feature contributions.

---

### Disease Knowledge Base

| Endpoint | Description |
|---|---|
| `GET /api/diseases` | List all 773 diseases |
| `GET /api/diseases/search` | Search diseases by name/keyword |
| `GET /api/diseases/{disease_name}` | Full disease record (description, severities, specialist) |
| `GET /api/diseases/{disease_name}/summary` | Simplified summary view |

---

### Identity & Profiles

| Endpoint | Description |
|---|---|
| `POST /api/profile/login` | Patient login |
| `POST /api/auth/doctor-login` / `POST /api/auth/doctor-register` | Doctor authentication |
| `POST /api/auth/verify` / `POST /api/auth/logout` | Session verification & logout |
| `GET/PUT /api/profile/{user_id}` | Profile read/update |
| `PUT/GET /api/profile/{user_id}/metrics` | Encrypted health metrics |
| `GET /api/profile/{user_id}/summary` | Profile + metrics + record counts |
| `POST/GET/DELETE /api/profile/{user_id}/assessments` | Assessment history |
| `POST/GET/DELETE /api/profile/{user_id}/records` | Medical records vault |

---

### Telemedicine

| Endpoint | Description |
|---|---|
| `GET /api/doctors` | List doctors |
| `GET /api/doctors/specializations` | List available specializations |
| `POST /api/consultations/request` | Patient requests a consultation |
| `GET /api/consultations` | List consultations |
| `POST /api/consultations/respond` | Doctor responds to a request |

---

### Pharmacy, Cart & Orders

| Endpoint | Description |
|---|---|
| `GET /api/products` / `GET /api/products/{product_id}` | Product catalog |
| `GET /api/categories` | Product categories |
| `GET /api/products/{product_id}/images` / `/image/primary` / `/api/products/batch/images` | Product imagery |
| `GET /api/cart` / `POST /api/cart/add` / `PUT /api/cart/update/{product_id}` / `DELETE /api/cart/remove/{item_id}` / `DELETE /api/cart/clear` | Cart management |
| `POST /api/cart/share` / `GET /api/cart/shares` / `GET /api/cart/shares/pending` / `POST /api/cart/share/respond` | Cart sharing between users |
| `POST /api/orders/create` / `GET /api/orders` / `GET /api/orders/{order_id}` / `GET /api/orders/{order_id}/items` | Order pipeline |
| `GET /api/prescriptions` / `GET /api/prescriptions/{prescription_id}/items` | Prescription records |

---

### AI Prescription Scanner

#### `GET /api/prescription/test-key`

Validates the `MISTRAL_API_KEY` and confirms the Pixtral vision model is reachable.

#### `POST /api/prescription/analyze`

Uploads a prescription photo (JPEG/PNG/WEBP, max 10MB), runs it through **Mistral Small 3.1 (Pixtral) vision OCR** to extract medicine names, then matches each against the live pharmacy inventory.

**Example Response:**
```json
{
  "success": true,
  "medicines_extracted": ["Paracetamol 500mg", "Amoxicillin 250mg"],
  "inventory_results": [
    { "medicine": "Paracetamol 500mg", "found": true, "product_id": 12 },
    { "medicine": "Amoxicillin 250mg", "found": false }
  ],
  "summary": { "total_medicines": 2, "found_in_inventory": 1, "not_found": 1 },
  "model_used": "mistral-small-latest"
}
```

If `MISTRAL_API_KEY` is not set, this endpoint degrades gracefully and surfaces the missing-key status via `/api/prescription/test-key`.

---

## Frontend

All 7 pages (`index.html`, `assessment.html`, `consultation.html`, `pharmacy.html`, `cart.html`, `patient-dashboard.html`, `doctor-dashboard.html`) are built with **Vanilla HTML/CSS/JavaScript** — no build step or framework dependency. They share:

- A unified design system in `style.css`
- Shared interaction logic in `script.js` (API calls, session handling, cart/notification state)
- The `assessment.html` page hosts the live Bayesian clarification UI — rendering each info-gain question as it's returned by `/clarify/start` and `/clarify/answer`, and displaying the final ranked diagnoses with feature contributions and disease knowledge base info.

---

## File Structure

```
Panacea-AI-Powered-Health-Companion-Triage-Platform/
│
├── api.py                       # FastAPI app — 60+ endpoints, encryption, ML inference
├── model.py                      # XGBoost + ComplementNB training pipeline & Bayesian engine
├── database.py                   # SQLite schema, migrations, managers (session/notif/images)
├── requirements.txt
├── README.md                     # This file
│
├── symptoms.json                  # 377 symptoms with explanations & gender specificity
├── diseases.json                  # 773 diseases with descriptions, severity & specialists
│
├── index.html                     # Landing page
├── assessment.html                # Symptom assessment & AI diagnosis UI (Bayesian Q&A)
├── consultation.html              # Telemedicine / doctor booking
├── pharmacy.html                  # Medicine catalog
├── cart.html                      # Cart & checkout
├── patient-dashboard.html          # Patient health vault
├── doctor-dashboard.html           # Doctor portal
├── script.js                        # Shared frontend logic
├── style.css                         # Shared design system
│
└── backend/
    └── ml_model/
        ├── disease_model_bundle_v2.pkl   # Serialized trained pipeline (generated by model.py)
        └── plots/                          # Auto-generated training visualizations
            ├── 01_class_distribution.png
            ├── 02_symptom_frequency.png
            ├── 02_topk_curve.png
            ├── 03_feature_importance.png
            ├── 04_calibration.png
            ├── 04_model_comparison.png
            ├── 05_demographic_accuracy.png
            ├── 05_per_class_accuracy.png
            ├── 06_clarification_efficiency.png
            ├── 06_worst_best_diseases.png
            ├── 07_confidence_analysis.png
            ├── 08_feature_importance.png
            ├── 09_topk_accuracy_curve.png
            ├── 10_confusion_matrix_top30.png
            └── 11_support_vs_accuracy.png
```

---

## Setup & Installation

### Prerequisites

- Python 3.10 or higher
- pip

### 1. Clone the repository

```bash
git clone <repo-url>
cd panacea
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set environment variables

```bash
export PANACEA_SECRET_KEY="your-long-random-secret"   # required for AES-256 encryption
export MISTRAL_API_KEY="your-mistral-api-key"           # optional, enables Rx OCR
```

### 5. Train the model (optional — pretrained `.pkl` may already be included)

```bash
python model.py
```

This trains the XGBoost + ComplementNB ensemble, fits the calibrator, builds the symptom-disease probability matrix, and saves all 17 diagnostic plots to `backend/ml_model/plots/`.

### 6. Start the server

```bash
python api.py
```

The app serves both the API and the frontend at `http://localhost:8000`.

| URL | Description |
|---|---|
| `http://localhost:8000/` | Landing page |
| `http://localhost:8000/assessment` | AI symptom assessment |
| `http://localhost:8000/health` | API health check |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PANACEA_SECRET_KEY` | Recommended | Seed for AES-256-GCM encryption of sensitive health fields. Falls back to a dev default if unset — **always set in production**. |
| `MISTRAL_API_KEY` | Optional | Enables `/api/prescription/analyze` (Mistral Pixtral vision OCR). If absent, prescription scanning is disabled gracefully. |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.10+ |
| **Web Framework** | FastAPI + Uvicorn |
| **ML / Data** | XGBoost, scikit-learn (ComplementNB, CalibratedClassifierCV), NumPy, Pandas, Joblib |
| **Bayesian Engine** | Custom entropy/info-gain implementation over symptom-disease probability matrix |
| **Visualization** | Matplotlib, Seaborn |
| **Security** | `cryptography` (AES-256-GCM field-level encryption) |
| **External AI** | Mistral AI Vision API (`mistral-small-latest` / Pixtral) for prescription OCR |
| **Database** | SQLite (18+ tables, custom migration system) |
| **HTTP Client** | `httpx` (async external API calls) |
| **Frontend** | Vanilla JavaScript, HTML5, CSS3 (no framework, no build step) |
| **Validation** | Pydantic v2 |

---

## Contributing

Contributions are welcome. Please follow this workflow:

1. Fork the repository and create a feature branch: `git checkout -b feature/your-feature`
2. Make focused, well-scoped changes with clear commit messages.
3. Test changes locally by running `model.py` and verifying the `/predict` and `/clarify/*` endpoints.
4. Open a pull request with a description of what changed and why.

**Ideas for contribution:**
- Add cross-validation to `model.py` for more robust per-class evaluation across all 773 diseases.
- Expose the Bayesian clarification engine's info-gain scoring as a standalone `/clarify/explain` endpoint.
- Add a `/batch` endpoint for bulk symptom-set predictions.
- Integrate transformer-based symptom embeddings to handle free-text symptom descriptions beyond exact catalogue matches.
- Add unit tests for `build_feature_vector()`, `_best_question()`, and `_apply_bayes_update()`.
- Add SHAP-based global explainability plots alongside the existing feature-importance chart.

---

## License

This repository does not include a license file. If you intend to use, distribute, or build on this code, please add a license. The **MIT License** is recommended for open projects.

---

<p align="center">
  Built with Python, XGBoost, FastAPI, and Bayesian reasoning &nbsp;·&nbsp; Panacea Health Platform &copy; 2026
</p>