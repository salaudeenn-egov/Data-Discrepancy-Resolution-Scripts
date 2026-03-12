import csv
import requests
import time
import json

# =====================================================
# CONFIG — Update before running
# =====================================================
API_URL    = "https://<campaign>-hcm.digit.org/project/beneficiary/v1/_search"  # Update campaign domain
PARAMS     = {"limit": 1000, "offset": 0, "tenantId": "<tenant_id>"}            # Update tenantId e.g. "ko"
HEADERS    = {"Accept": "application/json", "Content-Type": "application/json"}
AUTH_TOKEN = ""              # Paste auth token from OAuth login
INPUT_CSV  = "kogi-output.csv"           # CSV with projectBeneficiaryClientReferenceId column
                                         # Generated from DB_details_not_in_elastic Excel
OUTPUT_JSON = "kogi-beneficiaries.json"  # Update naming per campaign
BATCH_SIZE  = 50
DELAY       = 0.3

# =====================================================
# READ IDs FROM CSV
# =====================================================
with open(INPUT_CSV, newline="") as f:
    all_ids = [row["Data.projectBeneficiaryClientReferenceId"] for row in csv.DictReader(f)]

print(f"Total IDs loaded: {len(all_ids)}")

def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

# =====================================================
# FETCH BENEFICIARIES IN BATCHES
# =====================================================
all_beneficiaries = []

for batch in chunks(all_ids, BATCH_SIZE):
    payload = {
        "RequestInfo": {"authToken": AUTH_TOKEN},
        "ProjectBeneficiary": {"clientReferenceId": batch}
    }
    try:
        response = requests.post(API_URL, headers=HEADERS, params=PARAMS, json=payload, timeout=60)
        response.raise_for_status()
        data          = response.json()
        beneficiaries = data.get("ProjectBeneficiaries", [])
        all_beneficiaries.extend(beneficiaries)
        print(f"Processed batch of {len(batch)} | Found: {len(beneficiaries)}")
    except Exception as e:
        print(f"⚠  Batch failed: {e}")

    time.sleep(DELAY)

# =====================================================
# WRITE FINAL JSON
# =====================================================
with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(all_beneficiaries, f, ensure_ascii=False, indent=2)

print(f"\n✅ Completed. Total records: {len(all_beneficiaries)}")
print(f"📄 Output JSON: {OUTPUT_JSON}")
print("\nNext step: Transform this JSON to API payload, then run 7_bulk_update_tasks.py")
print("⚠  Remember to ask DevOps to scale up transformer and indexer before ingestion!")
