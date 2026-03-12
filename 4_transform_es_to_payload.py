import json
import pandas as pd
import requests
import warnings
from tqdm import tqdm
import time

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# =====================================================
# CONFIG — Update before running
# =====================================================
ES_BASE               = "https://elasticsearch-data.es-cluster-v8:9200"
ES_PROJECT_TASK_INDEX = f"{ES_BASE}/<campaign>-project-task-index-v1/_search"  # Update campaign index
ES_SCROLL_URL         = f"{ES_BASE}/_search/scroll"

ELASTIC_AUTH      = "Basic <base64_encoded_credentials>"  # Update with encoded credentials
INPUT_CSV         = "kogi-output.csv"           # Output from script 3_segregate_present_ids.py
TASKS_OUTPUT_FILE = "tasks_output_KOGI.json"    # Update naming per campaign

SCROLL_TIME = "15m"
BATCH_SIZE  = 10000
TIMEOUT     = 60

session = requests.Session()
session.headers.update({"Content-Type": "application/json", "Authorization": ELASTIC_AUTH})
session.verify = False

# =====================================================
# ES HELPER WITH RETRY
# =====================================================
def post_es(url, payload):
    for attempt in range(3):
        try:
            r = session.post(url, data=json.dumps(payload), timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"ES error (attempt {attempt+1}): {r.text[:200]}")
        except Exception as e:
            print(f"Retry {attempt+1} due to: {e}")
            time.sleep(3)
    raise RuntimeError("Elasticsearch request failed after 3 retries")

# =====================================================
# TRANSFORM ES DATA → API FORMAT
# =====================================================
def transform_es_to_api(d):
    additional_details = d.get("additionalDetails", {})

    fields = []
    field_keys = [
        "dateOfDelivery", "dateOfAdministration", "dateOfVerification",
        "cycleIndex", "doseIndex", "deliveryStrategy",
        "latitude", "longitude", "deliveryType", "name"
    ]
    for key in field_keys:
        value = additional_details.get(key)
        if value is not None:
            fields.append({"key": key, "value": str(value)})

    resources = [{
        "id": None,
        "tenantId": d.get("tenantId"),
        "clientReferenceId": None,
        "taskId": d.get("taskId"),
        "productVariantId": d.get("productVariant"),
        "quantity": float(d.get("quantity") or 0),
        "isDelivered": d.get("isDelivered", False),
        "deliveryComment": d.get("deliveryComments"),
        "isDeleted": False,
        "auditDetails": {
            "createdBy": d.get("createdBy"),
            "lastModifiedBy": d.get("lastModifiedBy"),
            "createdTime": d.get("createdTime"),
            "lastModifiedTime": d.get("lastModifiedTime")
        },
        "additionalFields": None
    }]

    address = {
        "id": None,
        "tenantId": d.get("tenantId"),
        "clientReferenceId": None,
        "doorNo": None,
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
        "locationAccuracy": d.get("locationAccuracy"),
        "type": "CORRESPONDENCE",
        "addressLine1": None, "addressLine2": None, "landmark": None,
        "city": None, "pincode": None, "buildingName": None, "street": None,
        "boundaryType": None, "boundary": None,
        "locality": {
            "id": None, "tenantId": None,
            "code": d.get("localityCode"),
            "geometry": None, "auditDetails": None, "additionalDetails": None
        }
    }

    return {
        "id": d.get("taskId"),
        "tenantId": d.get("tenantId"),
        "source": None,
        "rowVersion": 1,
        "applicationId": None,
        "hasErrors": False,
        "additionalFields": {"schema": "Task", "version": 1, "fields": fields},
        "auditDetails": {
            "createdBy": d.get("createdBy"),
            "lastModifiedBy": d.get("lastModifiedBy"),
            "createdTime": d.get("createdTime"),
            "lastModifiedTime": d.get("lastModifiedTime")
        },
        "clientReferenceId": d.get("clientReferenceId"),
        "clientAuditDetails": {
            "createdBy": d.get("createdBy"),
            "lastModifiedBy": d.get("lastModifiedBy"),
            "createdTime": d.get("createdTime"),
            "lastModifiedTime": d.get("lastModifiedTime")
        },
        "projectId": d.get("projectId"),
        "projectBeneficiaryId": None,
        "projectBeneficiaryClientReferenceId": d.get("projectBeneficiaryClientReferenceId"),
        "resources": resources,
        "plannedStartDate": 0, "plannedEndDate": 0,
        "actualStartDate": 0, "actualEndDate": 0,
        "createdBy": None, "createdDate": None,
        "address": address,
        "isDeleted": False,
        "status": d.get("status")
    }

# =====================================================
# READ IDs FROM CSV
# =====================================================
def read_ids_from_csv():
    print(f"\nReading IDs from {INPUT_CSV}...")
    df  = pd.read_csv(INPUT_CSV)
    ids = df["Data.projectBeneficiaryClientReferenceId"].dropna().unique().tolist()
    print(f"Loaded {len(ids):,} unique beneficiary IDs")
    return ids

# =====================================================
# FETCH ALL TASKS AND TRANSFORM
# =====================================================
def fetch_all_tasks_for_ids(beneficiary_ids):
    all_tasks = []
    print(f"\nFetching & transforming tasks for {len(beneficiary_ids):,} beneficiary IDs...")

    for i in tqdm(range(0, len(beneficiary_ids), BATCH_SIZE), unit="batch"):
        batch = beneficiary_ids[i:i + BATCH_SIZE]
        query = {
            "size": BATCH_SIZE,
            "_source": True,
            "query": {"terms": {"Data.projectBeneficiaryClientReferenceId.keyword": batch}},
            "sort": ["_doc"]
        }
        data      = post_es(f"{ES_PROJECT_TASK_INDEX}?scroll={SCROLL_TIME}", query)
        scroll_id = data["_scroll_id"]

        while True:
            hits = data["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                all_tasks.append(transform_es_to_api(h["_source"]["Data"]))
            data      = post_es(ES_SCROLL_URL, {"scroll": SCROLL_TIME, "scroll_id": scroll_id})
            scroll_id = data["_scroll_id"]

        try:
            session.delete(ES_SCROLL_URL, data=json.dumps({"scroll_id": [scroll_id]}))
        except Exception:
            pass

    print(f"Total tasks transformed: {len(all_tasks):,}")
    return all_tasks

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    beneficiary_ids = read_ids_from_csv()
    all_tasks       = fetch_all_tasks_for_ids(beneficiary_ids)

    with open(TASKS_OUTPUT_FILE, "w") as f:
        json.dump(all_tasks, f, indent=2)

    print(f"\nTasks saved: {TASKS_OUTPUT_FILE} ({len(all_tasks):,} objects)")

    # ── VALIDATE: Check for null clientReferenceId ──
    null_client_ref = [t for t in all_tasks if not t.get("clientReferenceId")]
    null_benef_ref  = [t for t in all_tasks if not t.get("projectBeneficiaryClientReferenceId")]
    print(f"\nValidation:")
    print(f"  Tasks with null clientReferenceId                   : {len(null_client_ref)}")
    print(f"  Tasks with null projectBeneficiaryClientReferenceId : {len(null_benef_ref)}")
    if null_client_ref or null_benef_ref:
        print("  ⚠  WARNING: Remove null records before ingestion!")
    else:
        print("  ✅ All records valid — ready for ingestion")
