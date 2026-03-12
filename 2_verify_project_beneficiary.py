import csv
import requests
import time
import pandas as pd

# =====================================================
# CONFIG — Update before running
# =====================================================
API_URL   = "https://<campaign>-hcm.digit.org/project/beneficiary/v1/_search"  # Update campaign domain
PARAMS    = {"limit": 1000, "offset": 0, "tenantId": "<tenant_id>"}             # Update tenantId e.g. "ko"
HEADERS   = {"Accept": "application/json", "Content-Type": "application/json"}
AUTH_TOKEN = ""          # Paste auth token from OAuth login
INPUT_FILE = ""          # Path to project beneficiary Excel e.g. "kogi-projectbeneficary.xlsx"
NOT_FOUND_CSV = "not_present_ids.csv"
BATCH_SIZE = 50
DELAY      = 0.3

# =====================================================
# READ IDs FROM EXCEL
# =====================================================
df      = pd.read_excel(INPUT_FILE)
all_ids = df["Data.projectBeneficiaryClientReferenceId"].dropna().tolist()
print(f"Total IDs loaded: {len(all_ids)}")

not_found = []

def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

# =====================================================
# VERIFY IN BATCHES
# =====================================================
for batch in chunks(all_ids, BATCH_SIZE):
    payload = {
        "RequestInfo": {"authToken": AUTH_TOKEN},
        "ProjectBeneficiary": {"clientReferenceId": batch}
    }
    try:
        response = requests.post(API_URL, headers=HEADERS, params=PARAMS, json=payload, timeout=60)
        response.raise_for_status()
        data      = response.json()
        returned  = data.get("ProjectBeneficiaries", [])
        found_ids = {obj.get("clientReferenceId") for obj in returned if obj.get("clientReferenceId")}

        for cid in batch:
            if cid not in found_ids:
                not_found.append(cid)

        print(f"Processed {len(batch)} | Missing: {len(batch) - len(found_ids)}")
    except Exception as e:
        print(f"Batch failed: {e}")
        not_found.extend(batch)

    time.sleep(DELAY)

# =====================================================
# WRITE MISSING IDs TO CSV
# =====================================================
with open(NOT_FOUND_CSV, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["clientReferenceId"])
    for cid in not_found:
        writer.writerow([cid])

print(f"\nCompleted. Missing IDs: {len(not_found)}")
print(f"Output file: {NOT_FOUND_CSV}")
