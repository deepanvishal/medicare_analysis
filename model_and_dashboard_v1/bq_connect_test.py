import numpy as np
import pandas as pd
import streamlit as st

SPECIALTIES = ["Primary Care", "Cardiology", "Nephrology", "Orthopedics"]
AGE_BANDS = ["65-74", "75-84", "85+"]
MONTHS = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"]


# All values below are fictional mock data.
# Step 2 will replace only this function body with a BigQuery query.
@st.cache_data(show_spinner=False)
def load_data() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = len(SPECIALTIES) * len(MONTHS) * len(AGE_BANDS)
    specialty = [s for s in SPECIALTIES for _ in range(len(MONTHS) * len(AGE_BANDS))]
    claim_month = [m for _ in SPECIALTIES for m in MONTHS for _ in range(len(AGE_BANDS))]
    age_band = [b for _ in SPECIALTIES for _ in MONTHS for b in AGE_BANDS]
    return pd.DataFrame({
        "county": ["TEST_COUNTY_FL"] * n,
        "specialty": specialty,
        "age_band": age_band,
        "claim_month": claim_month,
        "claim_count": rng.integers(50, 901, n),
        "member_count": rng.integers(100, 2001, n),
    })


def main():
    st.title("Streamlit Data Test — Step 1 (pandas mock)")

    df = load_data()
    st.write(f"Rows loaded: {len(df)}")

    sel_specialty = st.sidebar.multiselect("Specialty", SPECIALTIES, default=SPECIALTIES)
    sel_age_band = st.sidebar.multiselect("Age band", AGE_BANDS, default=AGE_BANDS)
    max_rows = st.sidebar.slider("Max rows displayed", 1, 60, 20)

    filtered = df[df["specialty"].isin(sel_specialty)
                  & df["age_band"].isin(sel_age_band)]
    st.dataframe(filtered.head(max_rows))

    st.subheader("claim_count by specialty (filtered selection)")
    summary = (filtered.groupby("specialty", as_index=False)["claim_count"].sum()
               .sort_values("claim_count", ascending=False))
    st.dataframe(summary)

    if st.button("Reload data"):
        load_data.clear()
        st.rerun()


main()
