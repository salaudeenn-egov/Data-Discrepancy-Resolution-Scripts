import base64
import json
import os
import warnings
from datetime import datetime

import pandas as pd
import psycopg2
import requests
from tqdm import tqdm

warnings.filterwarnings("ignore")

_SCROLL_TIME = "2m"
_BATCH_SIZE = 1000


def _es_headers(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Content-Type": "application/json", "Authorization": f"Basic {encoded}"}


def fetch_db_ids(db_config, query, id_column, log=print):
    conn = psycopg2.connect(**db_config)
    ids = set()
    log("  (query running — bar starts when first rows arrive)")
    with conn.cursor(name="fetch_ids_cur") as cur:
        cur.itersize = 50_000
        cur.execute(query)
        col_idx = [d[0].lower() for d in cur.description].index(id_column.lower())
        with tqdm(desc="  DB fetch", unit=" rows", unit_scale=True) as pbar:
            while True:
                rows = cur.fetchmany(50_000)
                if not rows:
                    break
                ids.update(str(r[col_idx]) for r in rows if r[col_idx] is not None)
                pbar.update(len(rows))
    conn.close()
    return ids


def fetch_elastic_ids(es_base, es_index, headers, query_body, id_field, log=print):
    ids = set()
    field_path = tuple(id_field.split("."))
    url = f"{es_base}/{es_index}/_search?scroll={_SCROLL_TIME}"
    scroll_query = {**query_body, "_source": [id_field], "size": _BATCH_SIZE}

    resp = requests.post(url, headers=headers, data=json.dumps(scroll_query), verify=False)
    data = resp.json()
    scroll_id = data["_scroll_id"]
    total = data["hits"]["total"]
    total = total["value"] if isinstance(total, dict) else total
    hits = data["hits"]["hits"]

    with tqdm(total=total, desc="  ES scroll", unit="doc") as pbar:
        while hits:
            for hit in hits:
                value = hit.get("_source", {})
                for key in field_path:
                    value = value.get(key, {}) if isinstance(value, dict) else None
                if value:
                    ids.add(str(value))
            pbar.update(len(hits))

            scroll_resp = requests.post(
                f"{es_base}/_search/scroll", headers=headers,
                data=json.dumps({"scroll": _SCROLL_TIME, "scroll_id": scroll_id}), verify=False
            )
            scroll_data = scroll_resp.json()
            hits = scroll_data["hits"]["hits"]
            scroll_id = scroll_data["_scroll_id"]

    return ids


def fetch_db_details(db_config, table, id_column, ids):
    conn = psycopg2.connect(**db_config)
    placeholders = ",".join(["%s"] * len(ids))
    query = f"SELECT * FROM {table} WHERE {id_column} IN ({placeholders})"
    rows = []
    col_names = None
    with conn.cursor(name="fetch_details_cur") as cur:
        cur.itersize = 50_000
        cur.execute(query, list(ids))
        col_names = [d[0] for d in cur.description]
        with tqdm(desc="  DB details", unit=" rows", unit_scale=True) as pbar:
            while True:
                batch = cur.fetchmany(50_000)
                if not batch:
                    break
                rows.extend(batch)
                pbar.update(len(batch))
    conn.close()
    return pd.DataFrame(rows, columns=col_names) if rows else pd.DataFrame()


def fetch_elastic_details(es_base, es_index, headers, id_field, ids, log=print):
    docs = []
    for eid in tqdm(ids, desc="Fetching ES docs"):
        query = {"query": {"term": {f"{id_field}.keyword": eid}}}
        resp = requests.post(
            f"{es_base}/{es_index}/_search", headers=headers,
            data=json.dumps(query), verify=False
        )
        for hit in resp.json().get("hits", {}).get("hits", []):
            docs.append(hit.get("_source", {}))
    return docs


def _resolve(template_str, params):
    try:
        return template_str.format(**params)
    except KeyError as e:
        raise KeyError(f"Missing value for placeholder {e} — add it to the runtime params widgets") from e


def _resolve_es_body(obj, params):
    """Recursively resolve {placeholder} only in string values, not JSON structure."""
    if isinstance(obj, dict):
        return {k: _resolve_es_body(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_es_body(item, params) for item in obj]
    if isinstance(obj, str) and '{' in obj:
        return _resolve(obj, params)
    return obj


def run_compare(campaign_cfg, metric_cfg, db_config, es_username, es_password,
                output_dir=".", runtime_params=None, log=print):
    metric_type = metric_cfg.get("type", "task")
    es_base     = campaign_cfg["es_base_url"]
    headers     = _es_headers(es_username, es_password)

    if metric_type == "stock":
        type_cfg  = campaign_cfg["stock"]
        db_table  = type_cfg["db_table"]
        id_column = type_cfg["db_id_column"]
        es_id_field = type_cfg["es_id_field"]
        extra_table_params = {
            "stock_table":    type_cfg["db_table"],
            "facility_table": type_cfg.get("facility_table", ""),
            "project_table":  type_cfg.get("project_table", campaign_cfg.get("task", {}).get("project_table", "")),
        }
    else:
        type_cfg  = campaign_cfg["task"]
        db_table  = type_cfg["db_table"]
        id_column = type_cfg["db_id_column"]
        es_id_field = type_cfg["es_id_field"]
        extra_table_params = {
            "db_table":     db_table,
            "project_table": type_cfg.get("project_table", ""),
        }

    es_index = type_cfg["es_index"]

    all_params = {
        "db_id_column":    id_column,
        "projectTypeId":   campaign_cfg.get("projectTypeId", ""),
        "projectHierarchy":campaign_cfg.get("projectHierarchy", ""),
        "province":        campaign_cfg.get("province", ""),
        **extra_table_params,
        **metric_cfg.get("params", {}),
        **(runtime_params or {}),
    }

    db_query  = _resolve(metric_cfg["db_query_template"], all_params)
    es_body   = _resolve_es_body(metric_cfg["es_query_body"], all_params)

    log("Fetching DB IDs...")
    db_ids = fetch_db_ids(db_config, db_query, id_column, log=log)
    log(f"  DB total: {len(db_ids):,}")

    log("Fetching Elasticsearch IDs...")
    es_ids = fetch_elastic_ids(es_base, es_index, headers, es_body, es_id_field)
    log(f"  ES total: {len(es_ids):,}")

    missing_in_elastic = db_ids - es_ids   # Scenario B: DB has it, ES doesn't
    missing_in_db = es_ids - db_ids        # Scenario A: ES has it, DB doesn't
    matched = db_ids & es_ids

    log(f"  Matched           : {len(matched):,}")
    log(f"  Missing in ES (B) : {len(missing_in_elastic):,}")
    log(f"  Missing in DB (A) : {len(missing_in_db):,}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)

    # Save ID lists
    pd.DataFrame(sorted(missing_in_elastic), columns=[f"{id_column}_missing_in_elastic"]) \
        .to_csv(os.path.join(output_dir, f"DB_not_in_Elastic_{ts}.csv"), index=False)
    pd.DataFrame(sorted(missing_in_db), columns=[f"{es_id_field}_missing_in_db"]) \
        .to_csv(os.path.join(output_dir, f"Elastic_not_in_DB_{ts}.csv"), index=False)

    # Fetch full records
    db_details_df = pd.DataFrame()
    if missing_in_elastic:
        log(f"Fetching {len(missing_in_elastic):,} DB detail records...")
        db_details_df = fetch_db_details(db_config, db_table, id_column, missing_in_elastic)
        db_details_df.to_csv(os.path.join(output_dir, f"DB_details_not_in_elastic_{ts}.csv"), index=False)
        log(f"  Saved DB details")

    es_docs_df = pd.DataFrame()
    if missing_in_db:
        log(f"Fetching {len(missing_in_db):,} ES detail documents...")
        es_docs = fetch_elastic_details(es_base, es_index, headers, es_id_field, missing_in_db, log)
        if es_docs:
            es_docs_df = pd.json_normalize(es_docs)
            es_docs_df.to_csv(os.path.join(output_dir, f"Elastic_details_not_in_db_{ts}.csv"), index=False)
            log(f"  Saved ES details")

    # Summary
    pd.DataFrame({
        "Metric": ["DB Table", "ES Index", "Total DB", "Total ES", "Matched", "Missing in ES (B)", "Missing in DB (A)", "Timestamp"],
        "Value": [db_table, es_index, len(db_ids), len(es_ids), len(matched), len(missing_in_elastic), len(missing_in_db), ts],
    }).to_csv(os.path.join(output_dir, f"Summary_Report_{ts}.csv"), index=False)

    return {
        "ts": ts,
        "db_ids": db_ids,
        "es_ids": es_ids,
        "matched": matched,
        "missing_in_elastic": missing_in_elastic,
        "missing_in_db": missing_in_db,
        "db_details_df": db_details_df,
        "es_docs_df": es_docs_df,
        "headers": headers,
        "es_base": es_base,
        "es_index": es_index,
    }
