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
| Elastic credentials | TST Checklist → Base64 encode online |
| Auth Token | OAuth cURL above |
| Campaign index | TST Checklist / DevOps |
| Tenant ID | Campaign config (e.g. `ko`, `bo`, `oy`) |
