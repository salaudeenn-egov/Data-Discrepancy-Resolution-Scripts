import json
import requests
import time

# =====================================================
# CONFIG — Update before running
# =====================================================
API_URL    = "https://<campaign>-hcm.digit.org/project/task/v1/bulk/_update"  # Update campaign domain
AUTH_TOKEN = ""                        # Paste auth token from OAuth login
TENANT_ID  = ""                        # e.g. "ko"
INPUT_JSON  = "tasks_output_KOGI.json"  # Transformed payload JSON
FAILED_JSON = "failed_tasks.json"
BATCH_SIZE  = 50
DELAY       = 0.3
HEADERS     = {"Content-Type": "application/json"}

# =====================================================
# HELPERS
# =====================================================
def chunks(data, size):
    for i in range(0, len(data), size):
        yield data[i:i + size]

# =====================================================
# INGESTION
# =====================================================
def ingest_tasks(tasks):
    success        = 0
    failed         = 0
    failed_records = []

    for index, batch in enumerate(chunks(tasks, BATCH_SIZE), start=1):
        payload = {
            "RequestInfo": {"authToken": AUTH_TOKEN},
            "Tasks": batch
        }
        try:
            response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
            if response.status_code in (200, 201):
                print(f"✅ Batch {index} | Updated: {len(batch)}")
                success += len(batch)
            else:
                print(f"❌ Batch {index} failed | Status: {response.status_code}")
                print(response.text[:300])
                failed += len(batch)
                failed_records.append({
                    "batch": index,
                    "statusCode": response.status_code,
                    "response": response.text,
                    "tasks": batch
                })
        except Exception as e:
            print(f"🔥 Batch {index} error: {e}")
            failed += len(batch)
            failed_records.append({"batch": index, "error": str(e), "tasks": batch})

        time.sleep(DELAY)

    if failed_records:
        with open(FAILED_JSON, "w") as f:
            json.dump(failed_records, f, indent=2)

    print("\n----- INGESTION SUMMARY -----")
    print(f"✅ Success : {success}")
    print(f"❌ Failed  : {failed}")
    if failed_records:
        print(f"📄 Failed records saved to: {FAILED_JSON}")
        print("   Re-run this script with INPUT_JSON = 'failed_tasks.json' to retry.")

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    with open(INPUT_JSON, "r") as f:
        task_data = json.load(f)

    # If retrying failed_tasks.json, flatten the tasks list
    if task_data and isinstance(task_data[0], dict) and "tasks" in task_data[0]:
        task_data = [t for record in task_data for t in record.get("tasks", [])]

    print(f"📦 Total tasks to update: {len(task_data)}")
    ingest_tasks(task_data)
