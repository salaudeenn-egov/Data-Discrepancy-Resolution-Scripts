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
ES_BASE = "https://elasticsearch-data.es-cluster-v8:9200"
ES_PROJECT_TASK_INDEX = f"{ES_BASE}/ab-project-task-index-v1/_search"
ES_SCROLL_URL = f"{ES_BASE}/_search/scroll"

ELASTIC_AUTH = "Basic ZHN0OkRzdCNlR292QDEyMw=="
INPUT_CSV = "FCT-output.csv"
TASKS_OUTPUT_FILE = "tasks_output_KOGI.json"

SCROLL_TIME = "15m"
BATCH_SIZE = 10000
TIMEOUT = 60

session = requests.Session()
session.headers.update({
    "Content-Type": "application/json",
    "Authorization": ELASTIC_AUTH
})
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

    # Convert additionalDetails → fields array
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

    # ✅ resources.clientReferenceId = original clientReferenceId
    resources = [{
        "id": None,
        "tenantId": d.get("tenantId"),
        "clientReferenceId": d.get("clientReferenceId"),
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
        "addressLine1": None,
        "addressLine2": None,
        "landmark": None,
        "city": None,
        "pincode": None,
        "buildingName": None,
        "street": None,
        "boundaryType": None,
        "boundary": None,
        "locality": {
            "id": None,
            "tenantId": None,
            "code": d.get("localityCode"),
            "geometry": None,
            "auditDetails": None,
            "additionalDetails": None
        }
    }

    return {
        "id": d.get("taskId"),
        "tenantId": d.get("tenantId"),
        "source": None,
        "rowVersion": 1,
        "applicationId": None,
        "hasErrors": False,

        "additionalFields": {
            "schema": "Task",
            "version": 1,
            "fields": fields
        },

        "auditDetails": {
            "createdBy": d.get("createdBy"),
            "lastModifiedBy": d.get("lastModifiedBy"),
            "createdTime": d.get("createdTime"),
            "lastModifiedTime": d.get("lastModifiedTime")
        },

        # ✅ ROOT now uses taskClientReferenceId
        "clientReferenceId": d.get("taskClientReferenceId"),

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

        "plannedStartDate": 0,
        "plannedEndDate": 0,
        "actualStartDate": 0,
        "actualEndDate": 0,

        "createdBy": None,
        "createdDate": None,

        "address": address,

        "isDeleted": False,
        "status": d.get("status")
    }

# =====================================================
# READ TASK CLIENT REFERENCE IDs FROM CSV
# =====================================================
def read_ids_from_csv():
    print(f"\nReading Task Client Reference IDs from {INPUT_CSV}...")
    df = pd.read_csv(INPUT_CSV)

    ids = df["Data.taskClientReferenceId"].dropna().unique().tolist()

    print(f"Loaded {len(ids):,} unique taskClientReferenceIds")
    return ids

# =====================================================
# FETCH ALL TASKS AND TRANSFORM
# =====================================================
def fetch_all_tasks_for_ids(task_ids):
    all_tasks = []

    print(f"\nFetching & transforming tasks for {len(task_ids):,} task IDs...")

    for i in tqdm(range(0, len(task_ids), BATCH_SIZE), unit="batch"):
        batch = task_ids[i:i + BATCH_SIZE]

        query = {
            "size": BATCH_SIZE,
            "_source": True,
            "query": {
                "terms": {
                    "Data.taskClientReferenceId.keyword": batch
                }
            },
            "sort": ["_doc"]
        }

        data = post_es(f"{ES_PROJECT_TASK_INDEX}?scroll={SCROLL_TIME}", query)
        scroll_id = data["_scroll_id"]

        while True:
            hits = data["hits"]["hits"]
            if not hits:
                break

            for h in hits:
                all_tasks.append(transform_es_to_api(h["_source"]["Data"]))

            data = post_es(ES_SCROLL_URL, {
                "scroll": SCROLL_TIME,
                "scroll_id": scroll_id
            })
            scroll_id = data["_scroll_id"]

        try:
            session.delete(
                ES_SCROLL_URL,
                data=json.dumps({"scroll_id": [scroll_id]})
            )
        except Exception:
            pass

    print(f"Total tasks transformed: {len(all_tasks):,}")
    return all_tasks

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    task_ids = read_ids_from_csv()
    all_tasks = fetch_all_tasks_for_ids(task_ids)

    with open(TASKS_OUTPUT_FILE, "w") as f:
        json.dump(all_tasks, f, indent=2)

    print(f"\nTasks saved: {TASKS_OUTPUT_FILE} ({len(all_tasks):,} objects)")

    # Validation
    null_client_ref = [t for t in all_tasks if not t.get("clientReferenceId")]
    null_benef_ref = [t for t in all_tasks if not t.get("projectBeneficiaryClientReferenceId")]

    print(f"\nValidation:")
    print(f"  Tasks with null clientReferenceId                   : {len(null_client_ref)}")
    print(f"  Tasks with null projectBeneficiaryClientReferenceId : {len(null_benef_ref)}")

    if null_client_ref or null_benef_ref:
        print("  WARNING: Remove null records before ingestion!")
    else:
        print("  ✅ All records valid — ready for ingestion")