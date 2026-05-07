import json
import time
import warnings

import requests
from tqdm import tqdm

warnings.filterwarnings("ignore")

_SCROLL_TIME = "15m"
_BATCH_SIZE = 10000
_TIMEOUT = 60


def _post_es(session, url, payload, retries=3):
    for attempt in range(retries):
        try:
            r = session.post(url, data=json.dumps(payload), timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            print(f"  ES error (attempt {attempt + 1}): {r.text[:200]}")
        except Exception as e:
            print(f"  Retry {attempt + 1}: {e}")
            time.sleep(3)
    raise RuntimeError("Elasticsearch request failed after retries")


def transform_es_to_api(d):
    """Convert a raw ES Data document into the HCM API task payload format."""
    additional_details = d.get("additionalDetails", {})

    fields = []
    for key in ["dateOfDelivery", "dateOfAdministration", "dateOfVerification",
                "cycleIndex", "doseIndex", "deliveryStrategy",
                "latitude", "longitude", "deliveryType", "name"]:
        value = additional_details.get(key)
        if value is not None:
            fields.append({"key": key, "value": str(value)})

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
            "lastModifiedTime": d.get("lastModifiedTime"),
        },
        "additionalFields": None,
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
            "geometry": None, "auditDetails": None, "additionalDetails": None,
        },
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
            "lastModifiedTime": d.get("lastModifiedTime"),
        },
        "clientReferenceId": d.get("taskClientReferenceId"),
        "clientAuditDetails": {
            "createdBy": d.get("createdBy"),
            "lastModifiedBy": d.get("lastModifiedBy"),
            "createdTime": d.get("createdTime"),
            "lastModifiedTime": d.get("lastModifiedTime"),
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
        "status": d.get("status"),
    }


def fetch_and_transform(es_base, es_index, es_auth_header, task_ids, log=print):
    """Fetch task documents from ES by taskClientReferenceId and transform to API payload."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json", "Authorization": es_auth_header})
    session.verify = False

    search_url = f"{es_base}/{es_index}/_search"
    scroll_url = f"{es_base}/_search/scroll"
    all_tasks = []

    log(f"Fetching & transforming {len(task_ids):,} tasks from ES...")

    for i in tqdm(range(0, len(task_ids), _BATCH_SIZE), desc="ES batches", unit="batch"):
        batch = task_ids[i:i + _BATCH_SIZE]
        query = {
            "size": _BATCH_SIZE,
            "_source": True,
            "query": {"terms": {"Data.taskClientReferenceId.keyword": batch}},
            "sort": ["_doc"],
        }

        data = _post_es(session, f"{search_url}?scroll={_SCROLL_TIME}", query)
        scroll_id = data["_scroll_id"]

        while True:
            hits = data["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                all_tasks.append(transform_es_to_api(h["_source"]["Data"]))
            data = _post_es(session, scroll_url, {"scroll": _SCROLL_TIME, "scroll_id": scroll_id})
            scroll_id = data["_scroll_id"]

        try:
            session.delete(scroll_url, data=json.dumps({"scroll_id": [scroll_id]}))
        except Exception:
            pass

    log(f"Total tasks transformed: {len(all_tasks):,}")
    return all_tasks


def validate_payload(tasks):
    """
    Returns (valid_tasks, issues).
    Drops tasks where clientReferenceId or projectBeneficiaryClientReferenceId is null.
    resources[].clientReferenceId is allowed to be null.
    """
    valid = []
    issues = []

    for t in tasks:
        if not t.get("clientReferenceId"):
            issues.append({"reason": "null clientReferenceId", "task": t})
        elif not t.get("projectBeneficiaryClientReferenceId"):
            issues.append({"reason": "null projectBeneficiaryClientReferenceId", "task": t})
        else:
            valid.append(t)

    return valid, issues
