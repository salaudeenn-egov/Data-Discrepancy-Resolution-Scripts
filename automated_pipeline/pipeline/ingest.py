import json
import time
import warnings

import requests
from tqdm import tqdm

warnings.filterwarnings("ignore")

_DELAY = 0.3


def _chunks(data, size):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def fetch_tasks_from_api(api_base, tenant_id, auth_token, client_reference_ids, batch_size=50, log=print):
    """
    Fetch full task objects from HCM API by clientReferenceId.
    Used in Scenario B to get task payloads for re-indexing.
    """
    url = f"{api_base}/project/task/v1/_search"
    params = {"limit": batch_size, "offset": 0, "tenantId": tenant_id}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    all_tasks = []
    batches = list(_chunks(client_reference_ids, batch_size))

    consecutive_failures = 0
    for batch in tqdm(batches, desc="  Fetching tasks from API", unit="batch"):
        payload = {
            "RequestInfo": {"authToken": auth_token},
            "Task": {"clientReferenceId": batch},
        }
        try:
            r = requests.post(url, headers=headers, params=params, json=payload, timeout=60, verify=False)
            r.raise_for_status()
            tasks = r.json().get("Task", [])
            all_tasks.extend(tasks)
            consecutive_failures = 0
        except Exception as e:
            log(f"  Batch failed: {e}")
            consecutive_failures += 1
            if consecutive_failures >= 3:
                log(f"\n  Stopping — 3 consecutive failures. Check API URL and network connectivity.")
                break
        time.sleep(_DELAY)

    log(f"  Total fetched from API: {len(all_tasks):,}")
    return all_tasks


def bulk_ingest(api_url, auth_token, tasks, batch_size=50, delay=_DELAY, failed_path="failed_tasks.json", log=print):
    """
    POST tasks in batches. Saves failed batches to failed_path for retry.
    Returns dict: {success, failed, failed_records}.
    """
    headers = {"Content-Type": "application/json"}
    success = 0
    failed = 0
    failed_records = []
    batches = list(_chunks(tasks, batch_size))

    for index, batch in enumerate(tqdm(batches, desc="  Ingesting", unit="batch"), start=1):
        payload = {"RequestInfo": {"authToken": auth_token}, "Tasks": batch}
        try:
            r = requests.post(api_url, headers=headers, json=payload, timeout=60, verify=False)
            if r.status_code in (200, 201):
                success += len(batch)
            else:
                log(f"  Batch {index:>4} | ERR | {r.status_code} — {r.text[:200]}")
                failed += len(batch)
                failed_records.append({"batch": index, "statusCode": r.status_code, "response": r.text, "tasks": batch})
        except Exception as e:
            log(f"  Batch {index:>4} | EXC | {e}")
            failed += len(batch)
            failed_records.append({"batch": index, "error": str(e), "tasks": batch})
        time.sleep(delay)

    if failed_records:
        with open(failed_path, "w") as f:
            json.dump(failed_records, f, indent=2)
        log(f"  Failed records saved to: {failed_path}")

    return {"success": success, "failed": failed, "failed_records": failed_records}


def retry_failed(failed_path, api_url, auth_token, batch_size=50, log=print):
    """Load failed_tasks.json and retry ingestion. Returns same summary dict."""
    with open(failed_path) as f:
        data = json.load(f)

    tasks = [t for record in data for t in record.get("tasks", [])]
    log(f"  Retrying {len(tasks):,} tasks from {failed_path}")
    return bulk_ingest(api_url, auth_token, tasks, batch_size=batch_size, failed_path=failed_path, log=log)
