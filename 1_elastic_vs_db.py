import pandas as pd
import psycopg2
import requests
import json
from datetime import datetime
import warnings
import sys
import base64
from tqdm import tqdm

warnings.filterwarnings("ignore")

# =====================================================
# GLOBAL CONFIGURATION
# =====================================================

# POSTGRES CONFIG
DB_HOST             = ""        # From PG Admin > Server > Properties > Connection
DB_PORT             = 5432
DB_NAME             = ""        # e.g. "ngcentralprd"
DB_USER             = ""        # Read-only user from TST Checklist
DB_PASSWORD         = ""        # Password from TST Checklist
DB_SSLMODE          = "require"
DB_CONNECT_TIMEOUT  = 30

DB_TABLE     = "bo.project_task"   # Update schema prefix per campaign (e.g. bo, ko)
DB_ID_COLUMN = "clientreferenceid"

# Update this query per campaign and category
QUERY = f"""
SELECT {DB_ID_COLUMN}
FROM {DB_TABLE} pt
JOIN bo.project p
  ON pt.projectid = p.id
WHERE pt.status = 'ADMINISTRATION_SUCCESS'
AND EXISTS (
    SELECT 1
    FROM jsonb_array_elements(pt.additionaldetails->'fields') f
    WHERE f->>'key' = 'doseIndex'
      AND f->>'value' = '01'
)
"""

# ELASTIC CONFIG
ELASTIC_BASE_URL    = "https://elasticsearch-data.es-cluster-v8:9200"
ELASTIC_INDEX       = "bo-project-task-index-v1"   # Update per campaign
ELASTIC_SCROLL_TIME = "2m"
ELASTIC_BATCH_SIZE  = 1000

# Base64 encoded credentials — get from TST Checklist and encode online
ELASTIC_USERNAME_B64 = ""   # e.g. "ZHN0"
ELASTIC_PASSWORD_B64 = ""   # e.g. "RHN0I2VHb3ZAMTIz"
ELASTIC_ID_FIELD     = "Data.taskClientReferenceId"

# Update filters to match campaign and category
ELASTIC_QUERY_BODY = {
   "query": {
    "bool": {
      "filter": [
        {
          "terms": {
            "Data.administrationStatus.keyword": ["ADMINISTRATION_SUCCESS"]
          }
        },
        {
          "bool": {
            "should": [
              {
                "term": {
                  "Data.additionalDetails.doseIndex.keyword": {"value": "01"}
                }
              }
            ],
            "minimum_should_match": 1
          }
        }
      ]
    }
  }
}

# OUTPUT FILES
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
FILE_DB_NOT_IN_ELASTIC          = f"DB_not_in_Elastic_{TIMESTAMP}.xlsx"
FILE_ELASTIC_NOT_IN_DB          = f"Elastic_not_in_DB_{TIMESTAMP}.xlsx"
FILE_SUMMARY                    = f"Summary_Report_{TIMESTAMP}.xlsx"
FILE_DB_DETAILS_NOT_IN_ELASTIC  = f"DB_details_not_in_elastic_{TIMESTAMP}.xlsx"
FILE_ELASTIC_DETAILS_NOT_IN_DB  = f"Elastic_details_not_in_db_{TIMESTAMP}.xlsx"

# =====================================================
# RUNTIME VALUES
# =====================================================

DB_CONFIG = {
    "host": DB_HOST, "port": DB_PORT, "database": DB_NAME,
    "user": DB_USER, "password": DB_PASSWORD,
    "sslmode": DB_SSLMODE, "connect_timeout": DB_CONNECT_TIMEOUT,
}

ELASTIC_USERNAME = base64.b64decode(ELASTIC_USERNAME_B64).decode()
ELASTIC_PASSWORD = base64.b64decode(ELASTIC_PASSWORD_B64).decode()
_encoded_credentials = base64.b64encode(f"{ELASTIC_USERNAME}:{ELASTIC_PASSWORD}".encode()).decode()

ELASTIC_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {_encoded_credentials}",
}

ELASTIC_SEARCH_URL = f"{ELASTIC_BASE_URL}/{ELASTIC_INDEX}/_search?scroll={ELASTIC_SCROLL_TIME}"
ELASTIC_SCROLL_URL = f"{ELASTIC_BASE_URL}/_search/scroll"
_FIELD_PATH = tuple(ELASTIC_ID_FIELD.split("."))

# =====================================================
# STEP 1 — FETCH FROM POSTGRES
# =====================================================
print("\nSTEP 1 — Fetching IDs from PostgreSQL")
try:
    conn   = psycopg2.connect(**DB_CONFIG)
    db_df  = pd.read_sql(QUERY, conn)
    conn.close()
    db_ids = set(db_df[DB_ID_COLUMN].dropna().astype(str))
    print("Total IDs from DB:", len(db_ids))
except Exception as e:
    print("Postgres Error:", e)
    sys.exit(1)

# =====================================================
# STEP 2 — FETCH FROM ELASTICSEARCH
# =====================================================
print("\nSTEP 2 — Fetching IDs from Elasticsearch")
elastic_ids = set()
try:
    scroll_query = {**ELASTIC_QUERY_BODY, "_source": [ELASTIC_ID_FIELD], "size": ELASTIC_BATCH_SIZE}
    response  = requests.post(ELASTIC_SEARCH_URL, headers=ELASTIC_HEADERS, data=json.dumps(scroll_query), verify=False)
    data      = response.json()
    scroll_id = data["_scroll_id"]
    hits      = data["hits"]["hits"]

    while hits:
        for hit in hits:
            value = hit.get("_source", {})
            for key in _FIELD_PATH:
                value = value.get(key, {}) if isinstance(value, dict) else None
            if value:
                elastic_ids.add(str(value))

        scroll_response = requests.post(
            ELASTIC_SCROLL_URL, headers=ELASTIC_HEADERS,
            data=json.dumps({"scroll": ELASTIC_SCROLL_TIME, "scroll_id": scroll_id}), verify=False
        )
        scroll_data = scroll_response.json()
        hits        = scroll_data["hits"]["hits"]
        scroll_id   = scroll_data["_scroll_id"]

    print("Total IDs from Elastic:", len(elastic_ids))
except Exception as e:
    print("Elastic Error:", e)
    sys.exit(1)

# =====================================================
# STEP 3 — COMPARISON
# =====================================================
print("\nSTEP 3 — Comparing")
missing_in_elastic = db_ids - elastic_ids
missing_in_db      = elastic_ids - db_ids
matched            = db_ids & elastic_ids

print("Matched            :", len(matched))
print("Missing in Elastic :", len(missing_in_elastic))
print("Missing in DB      :", len(missing_in_db))

# =====================================================
# STEP 4 — EXPORT ID FILES
# =====================================================
print("\nSTEP 4 — Exporting ID results")
pd.DataFrame(list(missing_in_elastic), columns=[f"{DB_ID_COLUMN}_missing_in_elastic"]).to_excel(FILE_DB_NOT_IN_ELASTIC, index=False)
pd.DataFrame(list(missing_in_db),      columns=[f"{ELASTIC_ID_FIELD}_missing_in_db"]).to_excel(FILE_ELASTIC_NOT_IN_DB, index=False)
print("ID files exported")

# =====================================================
# STEP 5 — FETCH DB FULL RECORDS
# =====================================================
print("\nSTEP 5 — Fetching DB full records")
if missing_in_elastic:
    conn         = psycopg2.connect(**DB_CONFIG)
    id_list      = list(missing_in_elastic)
    placeholders = ",".join(["%s"] * len(id_list))
    detail_query = f"SELECT * FROM {DB_TABLE} WHERE {DB_ID_COLUMN} IN ({placeholders})"
    db_detail_df = pd.read_sql(detail_query, conn, params=id_list)
    conn.close()
    db_detail_df.to_excel(FILE_DB_DETAILS_NOT_IN_ELASTIC, index=False)
    print("DB detail file created:", FILE_DB_DETAILS_NOT_IN_ELASTIC)
else:
    print("No DB records missing in Elastic")

# =====================================================
# STEP 6 — FETCH ELASTIC FULL DOCUMENTS
# =====================================================
print("\nSTEP 6 — Fetching Elastic documents")
elastic_docs = []
for eid in tqdm(missing_in_db):
    query = {"query": {"term": {f"{ELASTIC_ID_FIELD}.keyword": eid}}}
    resp  = requests.post(f"{ELASTIC_BASE_URL}/{ELASTIC_INDEX}/_search", headers=ELASTIC_HEADERS, data=json.dumps(query), verify=False)
    for hit in resp.json().get("hits", {}).get("hits", []):
        elastic_docs.append(hit.get("_source", {}))

if elastic_docs:
    pd.json_normalize(elastic_docs).to_excel(FILE_ELASTIC_DETAILS_NOT_IN_DB, index=False)
    print("Elastic detail file created:", FILE_ELASTIC_DETAILS_NOT_IN_DB)

# =====================================================
# STEP 7 — SUMMARY
# =====================================================
summary_data = {
    "Metric": ["DB Table", "Elasticsearch Index", "Total DB IDs", "Total Elastic IDs",
                "Matched", "Missing in Elastic", "Missing in DB", "Run Timestamp"],
    "Value":  [DB_TABLE, ELASTIC_INDEX, len(db_ids), len(elastic_ids),
               len(matched), len(missing_in_elastic), len(missing_in_db), TIMESTAMP]
}
pd.DataFrame(summary_data).to_excel(FILE_SUMMARY, index=False)
print("\nProcess Completed Successfully")
