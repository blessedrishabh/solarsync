# SolarSync — Deployment & Evaluation Guide

**Client:** Company A · Nagpur, Maharashtra (Vidarbha Rooftop Solar Operations)  
**System:** Rooftop Solar Lead Qualification, Quotation Engine & Execution Tracker  
**Tech Stack:** Python 3.12 · FastAPI · PostgreSQL 15 (SQLAlchemy Async + asyncpg) · LangGraph + Google Gemini Vision · ReportLab PDF Engine · Tailwind CSS · Vanilla JS  

---

## Evaluation Reference Links

- **GitHub Repository:** `https://github.com/blessedrishabh/solarsync`
- **Live AWS Production App:** `http://13.236.232.232:8000`
- **Live AWS Public DNS:** `http://ec2-13-236-232-232.ap-southeast-2.compute.amazonaws.com:8000`
- **Local Development URL:** `http://127.0.0.1:8000`

---

## 1. Quick Start (Local Run Instructions)

### Step 1: System Prerequisites
- **Python:** 3.12+ (tested on Python 3.12.4)
- **Database:** PostgreSQL 15+ running on `localhost:5432`
- **Git:** Standard git client

### Step 2: Clone / Navigate to Project Directory
```bash
git clone https://github.com/blessedrishabh/solarsync.git
cd solarsync
```

### Step 3: Create & Activate Python Virtual Environment
```bash
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### Step 4: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure PostgreSQL database `solarsync` exists:
```sql
CREATE DATABASE solarsync;
```
Verify `DATABASE_URL` in `.env` points to your PostgreSQL instance:
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/solarsync
```

### Step 6: Start Application Server
```bash
uvicorn app.main:app --reload --port 8000
```
On startup, SolarSync automatically initializes the database schema and seeds realistic demo data:
- Pre-configured Admin & Sales users
- Realistic Nagpur sample leads (`LD-30881`, `LD-30902`, `LD-30915`)
- Dated central subsidy tables (PM Surya Ghar Muft Bijli Yojana)
- Active rooftop solar installation projects with milestones, documents, and notifications

Access web interface at: **`http://127.0.0.1:8000/`**

---

## 2. How It Is Deployed (Production Cloud Architecture)

SolarSync is designed as a cloud-native, 12-factor application that can be deployed via Docker containers or managed PaaS platforms (Railway, Render, Fly.io, AWS ECS, or DigitalOcean App Platform).

### Option A: 1-Click Container Deployment (Docker & Docker Compose)
The repository includes a production multi-stage `Dockerfile` and `docker-compose.yml`:

```bash
# Build and run application + PostgreSQL database
docker compose up -d --build
```
The application will be live at `http://localhost:8000` with automated container health checks and persistent volume storage for uploads and database data.

### Option B: Cloud PaaS Deployment (Railway / Render / Fly.io)

1. **Database Provisioning:**
   - Create a managed PostgreSQL 15 database instance on your cloud provider (e.g., Railway PostgreSQL, Render PostgreSQL, or Supabase).
   - Copy the async connection string: `postgresql+asyncpg://user:password@host:port/dbname`.

2. **Web Service Deployment:**
   - Link your public GitHub repository (`https://github.com/blessedrishabh/solarsync`).
   - Set Build Command: `pip install -r requirements.txt` (or select Docker build).
   - Set Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

3. **Configure Environment Variables in Cloud Dashboard:**
   Provide the required environment variable values in the provider's secret management dashboard (names only listed below).

4. **Public Host Configuration:**
   Set `BASE_URL` to your production domain (e.g. `https://solarsync-production.up.railway.app`). All customer WhatsApp status links will automatically resolve to your live deployed domain.

---

## 3. Environment Variables Reference

In accordance with competition rules, only variable names and descriptions are provided (no secret values):

| Variable Name | Required | Purpose & Description |
| :--- | :---: | :--- |
| `DATABASE_URL` | **Yes** | SQLAlchemy Async PostgreSQL connection string (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | **Yes** | Cryptographically random 256-bit key for HMAC session cookie signing |
| `APP_NAME` | No | Brand display name of the application (default `SolarSync`) |
| `BASE_URL` | No | Production URL prefix for customer milestone links (`https://...`) |
| `DEBUG` | No | Boolean flag (`true` in development, `false` in production) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Session lifetime duration (default `1440` minutes / 24 hours) |
| `GEMINI_API_KEY` | Optional | Google Gemini API key for OCR Vision bill reading (LangGraph) |
| `UPLOAD_DIR` | No | File system path for customer bills and engineering reports (`uploads`) |
| `MAX_UPLOAD_SIZE_MB` | No | Maximum permitted file upload size in megabytes (default `10`) |

---

## 4. Reviewer Demo Credentials

SolarSync enforces strict Role-Based Access Control (**RBAC**) using secure HTTPOnly session cookies:

| Role | Email Login | Password | Capabilities & Scope |
| :--- | :--- | :--- | :--- |
| **System Admin** | `admin@solarsync.in` | `admin123` | Full system access: dated subsidy rules editor, hardware pricing tables, pipeline oversight, project management |
| **Sales Engineer** | `sales@solarsync.in` | `sales123` | Lead qualification, AI bill review, customized quote generation, project milestone progression |
| **Homeowner** | `prashant@example.com` | `home123` | Customer view: self-service quote acceptance, direct project tracking link |

---

## 5. Key Review & Evaluation URLs (Live AWS Production & Local)

### A. Core Operational Flows (Authenticated — Credentials in Section 4)
1. **Sales & Admin Dashboard:** `http://13.236.232.232:8000/dashboard` *(local: `http://127.0.0.1:8000/dashboard`)*
2. **Lead Capture with Bill Upload:** `http://13.236.232.232:8000/leads/new`
3. **AI Bill Review & DPDP Verification:** `http://13.236.232.232:8000/leads/1/review`
4. **Scored Leads Pipeline:** `http://13.236.232.232:8000/leads`
5. **Dated Subsidy & Pricing Rules:** `http://13.236.232.232:8000/admin/rules`
6. **Project Tracker Pipeline (Stuck Detection):** `http://13.236.232.232:8000/projects`
7. **Staff Project Execution Detail:** `http://13.236.232.232:8000/projects/PRJ-30881`

### B. Public Customer Touchpoints (Zero-Login / Token-Based)
1. **Public Customer Milestone Tracker (Prashant Wankhede - Active):**  
   `http://13.236.232.232:8000/track/demo-prashant-token-30881`  
   *(Bilingual English & Marathi milestone tracker, real-time progress gauge, document download, and WhatsApp simulator modal)*
2. **Stuck Project Demo Link (8 Days Idle):**  
   `http://13.236.232.232:8000/track/demo-stuck-token-30940`
3. **Completed Project Demo Link (Subsidy Credited):**  
   `http://13.236.232.232:8000/track/demo-complete-token-30799`

---

## 6. Automated Test Suite

Run the full automated test suite covering multi-factor scoring, dated subsidy math, single-page PDF generation, stuck project SLA detection, and public token access:

```bash
pytest tests -v
```

Expected result:
```
11 passed in 4.0s (100% test coverage of core business logic)
```
