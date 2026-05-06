import pandas as pd
import psycopg2
import requests
import json
import base64
import os
from tqdm import tqdm
from datetime import datetime

# =====================================================
# OUTPUT FOLDER
# =====================================================

OUTPUT_FOLDER = "nampula_stock"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# =====================================================
# CONFIG  —  set these variables in your environment before running:
#   DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSLMODE
#   ELASTIC_BASE_URL, ELASTIC_USERNAME, ELASTIC_PASSWORD, ELASTIC_INDEX
#   PROJECT_TYPE_ID, PROJECT_HIERARCHY_PREFIX
# =====================================================

DB_CONFIG = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", 5432)),
    "database": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "sslmode": os.environ.get("DB_SSLMODE", "require"),
}

ELASTIC_BASE_URL = os.environ["ELASTIC_BASE_URL"]
ELASTIC_INDEX = os.environ.get("ELASTIC_INDEX", "stock-index-v1")

SCROLL = "2m"
BATCH = 5000

USERNAME = os.environ["ELASTIC_USERNAME"]
PASSWORD = os.environ["ELASTIC_PASSWORD"]

AUTH = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {AUTH}",
}

PROJECT_TYPE_ID = os.environ["PROJECT_TYPE_ID"]
PROJECT_HIERARCHY_PREFIX = os.environ["PROJECT_HIERARCHY_PREFIX"]

TS = datetime.now().strftime("%Y%m%d_%H%M%S")

# =====================================================
# STEP 1 — DB DATA
# =====================================================

print("\nSTEP 1 — DB")

DB_QUERY = """
SELECT s.id, s.clientreferenceid, s.quantity
FROM stock s
JOIN facility f ON s.receiverid = f.id
JOIN project p ON s.referenceid = p.id
WHERE s.transactiontype = 'RECEIVED'
  AND f.usage = 'Health Facility'
  AND p.projecttypeid = %(project_type_id)s
  AND p.projecthierarchy ILIKE %(hierarchy_prefix)s
  AND s.isdeleted = False
"""

conn = psycopg2.connect(**DB_CONFIG)
db_df = pd.read_sql(
    DB_QUERY,
    conn,
    params={
        "project_type_id": PROJECT_TYPE_ID,
        "hierarchy_prefix": PROJECT_HIERARCHY_PREFIX + "%",
    },
)
conn.close()

db_df["clientreferenceid"] = db_df["clientreferenceid"].astype(str)

db_map = dict(zip(db_df["clientreferenceid"], db_df["quantity"]))

print("DB records:", len(db_map))

# =====================================================
# STEP 2 — ELASTIC DATA (SCROLL)
# =====================================================

print("\nSTEP 2 — ELASTIC")

elastic_map = {}
elastic_docs = {}

query = {
    "size": BATCH,
    "_source": True,
    "query": {
        "bool": {
            "filter": [
                {"term": {"Data.additionalDetails.projectTypeId.keyword": PROJECT_TYPE_ID}},
                {"term": {"Data.boundaryHierarchy.province.keyword": "Nampula"}},
                {"term": {"Data.facilityType.keyword": "Health Facility"}},
                {"term": {"Data.eventType.keyword": "RECEIVED"}},
            ]
        }
    },
}

url = f"{ELASTIC_BASE_URL}/{ELASTIC_INDEX}/_search?scroll={SCROLL}"

res = requests.post(url, headers=HEADERS, data=json.dumps(query), verify=False)
data = res.json()

scroll_id = data["_scroll_id"]
hits = data["hits"]["hits"]

total = data["hits"]["total"]
total = total["value"] if isinstance(total, dict) else total

pbar = tqdm(total=total, desc="Elastic Scroll")

while hits:
    pbar.update(len(hits))

    for h in hits:
        src = h["_source"]["Data"]

        cid = str(src.get("clientReferenceId"))
        qty = src.get("physicalCount", 0)

        if cid:
            elastic_map[cid] = qty
            elastic_docs[cid] = src

    scroll = requests.post(
        f"{ELASTIC_BASE_URL}/_search/scroll",
        headers=HEADERS,
        data=json.dumps({"scroll": SCROLL, "scroll_id": scroll_id}),
        verify=False,
    )

    sdata = scroll.json()
    hits = sdata["hits"]["hits"]
    scroll_id = sdata["_scroll_id"]

pbar.close()
print("Elastic records:", len(elastic_map))

# =====================================================
# STEP 3 — COMPARE
# =====================================================

print("\nSTEP 3 — COMPARISON")

db_ids = set(db_map.keys())
es_ids = set(elastic_map.keys())

missing_in_es = db_ids - es_ids
missing_in_db = es_ids - db_ids
common = db_ids & es_ids

mismatch = []

for cid in common:
    if db_map[cid] != elastic_map[cid]:
        mismatch.append({
            "clientreferenceid": cid,
            "db_quantity": db_map[cid],
            "es_physicalCount": elastic_map[cid],
        })

print("Matched:", len(common))
print("Missing in ES:", len(missing_in_es))
print("Missing in DB:", len(missing_in_db))
print("Quantity mismatch:", len(mismatch))

# =====================================================
# STEP 4 — EXPORT IDS
# =====================================================

pd.DataFrame(list(missing_in_es)).to_csv(
    os.path.join(OUTPUT_FOLDER, f"missing_in_es_{TS}.csv"), index=False)

pd.DataFrame(list(missing_in_db)).to_csv(
    os.path.join(OUTPUT_FOLDER, f"missing_in_db_{TS}.csv"), index=False)

pd.DataFrame(mismatch).to_csv(
    os.path.join(OUTPUT_FOLDER, f"quantity_mismatch_{TS}.csv"), index=False)

# =====================================================
# STEP 5 — DB DETAILS
# =====================================================

print("\nSTEP 5 — DB DETAILS")

if missing_in_es:
    db_df[db_df["clientreferenceid"].isin(missing_in_es)] \
        .to_csv(os.path.join(OUTPUT_FOLDER, f"db_details_missing_in_es_{TS}.csv"), index=False)

# =====================================================
# STEP 6 — ES DETAILS
# =====================================================

print("\nSTEP 6 — ES DETAILS")

if missing_in_db:
    es_detail_list = [elastic_docs[cid] for cid in missing_in_db]

    pd.json_normalize(es_detail_list) \
        .to_csv(os.path.join(OUTPUT_FOLDER, f"es_details_missing_in_db_{TS}.csv"), index=False)

# =====================================================
# STEP 7 — SUMMARY
# =====================================================

summary = pd.DataFrame({
    "Metric": ["DB", "Elastic", "Matched", "Missing ES", "Missing DB", "Mismatch"],
    "Value": [
        len(db_ids),
        len(es_ids),
        len(common),
        len(missing_in_es),
        len(missing_in_db),
        len(mismatch),
    ],
})

summary.to_csv(
    os.path.join(OUTPUT_FOLDER, f"summary_{TS}.csv"), index=False)

# =====================================================
# DONE
# =====================================================

print("\nAll files saved in folder:", OUTPUT_FOLDER)
print("DONE — FULL STOCK AUDIT (CLIENTREFERENCEID + DETAILS)")
