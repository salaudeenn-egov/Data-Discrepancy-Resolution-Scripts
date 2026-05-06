# Data Discrepancy Resolution Scripts

Scripts to identify and fix data discrepancies between **PostgreSQL (DB)** and **Elasticsearch (Elastic)** for campaign data.

---

## Two Scenarios

| Scenario | Description | Scripts |
|----------|-------------|---------|
| **A** | Data in Elastic — NOT in DB | Scripts 1 → 2 → 3 → 4 → 5 |
| **B** | Data in DB — NOT in Elastic | Scripts 1 → 6 → 7 |

---

## Scripts

| # | File | Purpose |
|---|------|---------|
| 1 | `1_elastic_vs_db.py` | Compare DB vs Elastic. Outputs 5 Excel/report files |
| 2 | `2_verify_project_beneficiary.py` | Check which IDs have no project beneficiary in system |
| 3 | `3_segregate_present_ids.py` | Remove IDs with no project beneficiary from dataset |
| 4 | `4_transform_es_to_payload.py` | Fetch tasks from Elastic, transform to API payload JSON |
| 5 | `5_bulk_create_tasks.py` | Bulk create tasks via API (Scenario A ingestion) |
| 6 | `6_fetch_beneficiaries.py` | Fetch beneficiary objects from API (Scenario B) |
| 7 | `7_bulk_update_tasks.py` | Bulk update tasks via API (Scenario B ingestion) |
| 8 | `8_stock.py` | Stock audit — compare stock quantities between DB and Elastic |

---

## Prerequisites

```bash
pip install pandas psycopg2-binary requests tqdm openpyxl
```

---

## Usage

### Phase 0 — Run Comparison (Both Scenarios)

1. Open `1_elastic_vs_db.py`
2. Fill in DB credentials (from PG Admin + TST Checklist)
3. Fill in Elasticsearch credentials (Base64 encode from TST Checklist)
4. Update `DB_TABLE` schema prefix and `ELASTIC_INDEX` per campaign
5. Update the SQL query and Elastic query to match campaign/category
6. Run: `python 1_elastic_vs_db.py`
7. Download and open `Summary_Report_<timestamp>.xlsx` first to review counts

**Output files:**
- `Summary_Report_*.xlsx` — counts overview
- `DB_not_in_Elastic_*.xlsx` — IDs in DB missing from Elastic
- `Elastic_not_in_DB_*.xlsx` — IDs in Elastic missing from DB
- `DB_details_not_in_elastic_*.xlsx` — full DB records
- `Elastic_details_not_in_db_*.xlsx` — full Elastic documents

---

### Scenario A — Elastic NOT in DB

**Goal:** Re-ingest records from Elastic into DB via Bulk Create API

```
1_elastic_vs_db.py
      ↓  (open Elastic_details_not_in_db, filter isDeleted=false, copy projectBeneficiaryClientReferenceId to CSV)
2_verify_project_beneficiary.py   →  not_present_ids.csv
      ↓
3_segregate_present_ids.py        →  kogi-output.csv
      ↓
4_transform_es_to_payload.py      →  tasks_output_KOGI.json
      ↓  (verify no null clientReferenceId or projectBeneficiaryClientReferenceId)
5_bulk_create_tasks.py            →  failed_tasks.json (retry if needed)
```

**Get Auth Token first:**
```bash
curl --location 'https://<campaign>-hcm.digit.org/user/oauth/token' \
  --header 'authorization: Basic ZWdvdi11c2VyLWNsaWVudDo=' \
  --header 'content-type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=<username>' \
  --data-urlencode 'password=<password>' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode 'scope=read' \
  --data-urlencode 'tenantId=<tenant_id>' \
  --data-urlencode 'userType=EMPLOYEE'
```

---

### Scenario B — DB NOT in Elastic

**Goal:** Re-index records from DB into Elasticsearch via Bulk Update API

```
1_elastic_vs_db.py
      ↓  (open DB_details_not_in_elastic, filter isDeleted=false, copy projectBeneficiaryClientReferenceId to CSV)
6_fetch_beneficiaries.py          →  kogi-beneficiaries.json
      ↓  (transform to API payload)
      ↓  ⚠ Ask DevOps to scale up transformer + indexer before this step!
7_bulk_update_tasks.py            →  failed_tasks.json (retry if needed)
```

---

### Script 8 — Stock Audit (Standalone)

**Goal:** Compare `stock` table in DB against `stock-index-v1` in Elasticsearch to find quantity mismatches, records missing in ES, and records missing in DB.

All credentials and identifiers are read from **environment variables** — no values are hardcoded. Set the following before running:

| Variable | Description |
|----------|-------------|
| `DB_HOST` | PostgreSQL host (IP or hostname) |
| `DB_PORT` | PostgreSQL port (default: `5432`) |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DB_SSLMODE` | SSL mode (default: `require`) |
| `ELASTIC_BASE_URL` | Elasticsearch base URL (e.g. `https://host:9200`) |
| `ELASTIC_INDEX` | Elasticsearch index name (default: `stock-index-v1`) |
| `ELASTIC_USERNAME` | Elasticsearch username |
| `ELASTIC_PASSWORD` | Elasticsearch password |
| `PROJECT_TYPE_ID` | Project type UUID to filter by |
| `PROJECT_HIERARCHY_PREFIX` | Project hierarchy prefix to filter by (without trailing `%`) |

**Set env vars and run (Linux/macOS):**
```bash
export DB_HOST=<host>
export DB_NAME=<db>
export DB_USER=<user>
export DB_PASSWORD=<password>
export ELASTIC_BASE_URL=https://<host>:9200
export ELASTIC_USERNAME=<user>
export ELASTIC_PASSWORD=<password>
export PROJECT_TYPE_ID=<uuid>
export PROJECT_HIERARCHY_PREFIX=<hierarchy-prefix>
python 8_stock.py
```

**Set env vars and run (Windows PowerShell):**
```powershell
$env:DB_HOST="<host>"
$env:DB_NAME="<db>"
$env:DB_USER="<user>"
$env:DB_PASSWORD="<password>"
$env:ELASTIC_BASE_URL="https://<host>:9200"
$env:ELASTIC_USERNAME="<user>"
$env:ELASTIC_PASSWORD="<password>"
$env:PROJECT_TYPE_ID="<uuid>"
$env:PROJECT_HIERARCHY_PREFIX="<hierarchy-prefix>"
python 8_stock.py
```

**Output files** (written to `nampula_stock/` folder):

| File | Contents |
|------|----------|
| `summary_*.csv` | Counts overview |
| `missing_in_es_*.csv` | clientReferenceIds in DB but not in ES |
| `missing_in_db_*.csv` | clientReferenceIds in ES but not in DB |
| `quantity_mismatch_*.csv` | Records where DB quantity ≠ ES physicalCount |
| `db_details_missing_in_es_*.csv` | Full DB rows for records missing in ES |
| `es_details_missing_in_db_*.csv` | Full ES documents for records missing in DB |

---

## Retrying Failed Records

Both `5_bulk_create_tasks.py` and `7_bulk_update_tasks.py` save failures to `failed_tasks.json`.

To retry, update `INPUT_JSON`:
```python
INPUT_JSON = "failed_tasks.json"
```
Then re-run the same script. Repeat until failures reach 0.

---

## Validation Checklist

Before ingestion, verify the transformed JSON:
- [ ] `clientReferenceId` is NOT null
- [ ] `projectBeneficiaryClientReferenceId` is NOT null
- [ ] `resources[].clientReferenceId` — can be null (OK)

After ingestion:
- [ ] Re-run `1_elastic_vs_db.py`
- [ ] Record before/after counts in tracking sheet
- [ ] Difference should be **0**
- [ ] Document any duplicates found in Elastic with reason

---

## Special Case — Data Missing from Both DB and Elastic

1. Check **Ego Tracer / Error Tracer** for the record
2. If not found: check **Apache Kafka** (data retained for 7 days only)
3. After 7 days — data is permanently unrecoverable

---

## Config Reference

| Parameter | Where to Get It |
|-----------|----------------|
| DB Host/Port | PG Admin → Server → Properties → Connection |
| DB User/Password | TST Checklist (read-only user) |
| Elastic credentials | TST Checklist |
| Auth Token | OAuth cURL above |
| Campaign index | TST Checklist / DevOps |
| Tenant ID | Campaign config (e.g. `ko`, `bo`, `oy`) |
| `PROJECT_TYPE_ID` | TST Checklist / campaign config |
| `PROJECT_HIERARCHY_PREFIX` | TST Checklist / campaign boundary hierarchy |

> **Security note:** Scripts 1–7 use inline placeholder variables (fill before running, do not commit filled values).
> Script 8 (`8_stock.py`) reads all credentials exclusively from environment variables — never hardcode values into the file.
