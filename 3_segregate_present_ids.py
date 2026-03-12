import pandas as pd

# =====================================================
# CONFIG — Update before running
# =====================================================
INPUT_FILE    = ""    # Path to project beneficiary Excel e.g. "kogi-projectbeneficary.xlsx"
REMOVE_CSV    = "not_present_ids.csv"   # Output from script 2_verify_project_beneficiary.py
OUTPUT_FILE   = "kogi-output.csv"       # Update naming per campaign

MAIN_ID_COLUMN   = "Data.projectBeneficiaryClientReferenceId"
REMOVE_ID_COLUMN = "clientReferenceId"

# =====================================================
# LOAD IDs TO REMOVE
# =====================================================
remove_df  = pd.read_csv(REMOVE_CSV)
remove_ids = set(remove_df[REMOVE_ID_COLUMN].dropna().astype(str))
print(f"Total IDs to remove: {len(remove_ids)}")

# =====================================================
# READ EXCEL AND FILTER
# =====================================================
df          = pd.read_excel(INPUT_FILE)
filtered_df = df[~df[MAIN_ID_COLUMN].astype(str).isin(remove_ids)]
removed     = len(df) - len(filtered_df)
kept        = len(filtered_df)

# =====================================================
# SAVE RESULT
# =====================================================
filtered_df.to_csv(OUTPUT_FILE, index=False)

print("COMPLETED SUCCESSFULLY")
print(f"Removed records   : {removed}")
print(f"Remaining records : {kept}")
print(f"Output file       : {OUTPUT_FILE}")
