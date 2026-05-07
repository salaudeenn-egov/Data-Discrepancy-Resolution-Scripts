import warnings

import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")


def filter_test_data_es(df, test_prefixes, createdby_col="Data.createdBy", log=print):
    """
    Split ES dataframe into (clean_df, test_df).
    Test records: createdBy starts with any prefix in test_prefixes (case-insensitive).
    """
    if not test_prefixes or createdby_col not in df.columns:
        return df.copy(), pd.DataFrame(columns=df.columns)

    prefixes = [p.lower() for p in test_prefixes]
    mask = df[createdby_col].astype(str).str.lower().apply(
        lambda v: any(v.startswith(p) for p in prefixes)
    )
    test_df  = df[mask].copy()
    clean_df = df[~mask].copy()
    if len(test_df):
        log(f"  Test data removed (ES createdBy prefix): {len(test_df):,} records")
    return clean_df, test_df


def filter_test_data_db(df, db_config, individual_table, name_col="username",
                        test_name_patterns=None, createdby_col="createdby", log=print):
    """
    Split DB dataframe into (clean_df, test_df).
    Looks up createdby UUIDs in individual_table and checks if the name matches any test_name_patterns.
    Falls back to returning all clean if the lookup fails.
    """
    if not test_name_patterns or not individual_table or createdby_col not in df.columns:
        return df.copy(), pd.DataFrame(columns=df.columns)

    user_ids = df[createdby_col].dropna().unique().tolist()
    if not user_ids:
        return df.copy(), pd.DataFrame(columns=df.columns)

    try:
        conn = psycopg2.connect(**db_config)
        placeholders = ",".join(["%s"] * len(user_ids))
        query = f"SELECT id, {name_col} FROM {individual_table} WHERE id IN ({placeholders})"
        user_df = pd.read_sql(query, conn, params=user_ids)
        conn.close()

        patterns = [p.lower() for p in test_name_patterns]
        test_user_ids = set()
        for _, row in user_df.iterrows():
            name = str(row[name_col]).lower()
            if any(pat in name for pat in patterns):
                test_user_ids.add(str(row["id"]))

        mask     = df[createdby_col].astype(str).isin(test_user_ids)
        test_df  = df[mask].copy()
        clean_df = df[~mask].copy()
        if len(test_df):
            log(f"  Test data removed (DB individual lookup): {len(test_df):,} records")
        return clean_df, test_df

    except Exception as e:
        log(f"  Warning: individual table lookup failed ({e}) — skipping test data filter")
        return df.copy(), pd.DataFrame(columns=df.columns)


def mark_deleted_in_db(db_config, table, id_column, ids, log=print):
    """
    Directly UPDATE the DB table to set isdeleted=true for the given ids.
    Returns the number of rows updated.
    """
    if not ids:
        log("  No records to mark deleted.")
        return 0
    try:
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(ids))
        query = f"UPDATE {table} SET isdeleted = true WHERE {id_column} IN ({placeholders})"
        cur.execute(query, list(ids))
        conn.commit()
        updated = cur.rowcount
        cur.close()
        conn.close()
        log(f"  Marked {updated:,} records as isdeleted=true in {table}")
        return updated
    except Exception as e:
        log(f"  ERROR marking deleted in DB: {e}")
        raise
