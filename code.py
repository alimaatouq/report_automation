import streamlit as st
import pandas as pd
import os
import re
from io import BytesIO
import tempfile

st.set_page_config(page_title="Campaign Comparison Tool", layout="wide")
st.title("📊 Campaign Comparison Tool (Weekly)")
st.markdown("Upload multiple weekly `.csv` campaign files (e.g. `Campaign name week 1.csv`)")

uploaded_files = st.file_uploader("Upload your campaign CSVs", type="csv", accept_multiple_files=True)

def compare_two_files(previous_week_file, current_week_file):
    try:
        previous_week_df = pd.read_csv(previous_week_file)
        current_week_df = pd.read_csv(current_week_file)
    except FileNotFoundError:
        print(f"Error: One or both of the files not found: '{previous_week_file}', '{current_week_file}'")
        return None, None, None

    for df in [previous_week_df, current_week_df]:
        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    detail_cols = ['Campaign', 'Brand name', 'Sub-brand name', 'Product name', 'Market1', 'Market2', 'Ad view']
    for col in detail_cols:
        for df in [previous_week_df, current_week_df]:
            if col not in df.columns:
                df[col] = None # Add missing columns as None to avoid KeyError later

    def summarize(df):
        # Group by ID Campaign and collect unique Ad view URLs into a list
        # Ensure Ad view column exists and handle potential non-string types
        if 'Ad view' in df.columns:
            grouped_ad_views = df.groupby('ID Campaign')['Ad view'].apply(
                lambda x: [str(url).strip() for url in x.dropna().unique() if pd.notna(url) and str(url).strip()]
            ).to_dict()
        else:
            grouped_ad_views = {} # No 'Ad view' column, no creatives

        # Summarize other columns
        grouped = df.groupby('ID Campaign').agg({
            'Date': ['min', 'max'],
            'Campaign': 'first',
            'Brand name': 'first',
            'Sub-brand name': lambda x: ', '.join(sorted(set(x.dropna()))),
            'Product name': lambda x: ', '.join(sorted(set(x.dropna()))),
            'Market1': lambda x: ', '.join(sorted(set(x.dropna()))),
            'Market2': lambda x: ', '.join(sorted(set(x.dropna())))
        }).reset_index()
        grouped.columns = ['ID Campaign', 'Start Date', 'End Date',
                           'Campaign', 'Brand name',
                           'Sub-brand name', 'Product name', 'Market1', 'Market2']

        # Add creative links as separate columns
        max_creatives = 0
        if grouped_ad_views:
            max_creatives = max(len(v) for v in grouped_ad_views.values())

        for i in range(max_creatives):
            col_name = f'Creatives (Ad View) {i+1}'
            # Create the column with None values first
            grouped[col_name] = None
            # Then populate it with the URLs
            grouped[col_name] = grouped['ID Campaign'].map(
                lambda id_campaign: grouped_ad_views.get(id_campaign, [None] * (i + 1))[i]
                if id_campaign in grouped_ad_views and i < len(grouped_ad_views[id_campaign])
                else None
            )
        return grouped

    previous_summary = summarize(previous_week_df)
    current_summary = summarize(current_week_df)

    prev_ids = set(previous_summary['ID Campaign'])
    curr_ids = set(current_summary['ID Campaign'])

    # --- Ended campaigns ---
    ended = previous_summary[~previous_summary['ID Campaign'].isin(curr_ids)].copy()
    # Remove creative columns from 'ended' campaigns
    ended_creative_cols = [col for col in ended.columns if col.startswith('Creatives (Ad View)')]
    if ended_creative_cols:
        ended.drop(columns=ended_creative_cols, inplace=True)


    # --- Ongoing campaigns ---
    ongoing = pd.merge(previous_summary, current_summary, on='ID Campaign', suffixes=('_prev', '_curr'))

    # Determine columns for ongoing
    select_cols_ongoing = [
        'ID Campaign', 'Campaign_prev', 'Brand name_prev', 'Sub-brand name_prev',
        'Product name_prev', 'Market1_prev', 'Market2_prev',
        'Start Date_prev'
    ]

    # Dynamically add creative columns from previous_summary (which will have '_prev' suffix after merge)
    creative_cols_from_prev_summary = [col for col in previous_summary.columns if col.startswith('Creatives (Ad View)')]
    select_cols_ongoing.extend([f"{col}_prev" for col in creative_cols_from_prev_summary])

    # Filter `ongoing` to keep only the desired columns that actually exist in the merged DataFrame
    existing_cols_in_ongoing = [col for col in select_cols_ongoing if col in ongoing.columns]
    ongoing = ongoing[existing_cols_in_ongoing].rename(columns={
        'Campaign_prev': 'Campaign',
        'Brand name_prev': 'Brand name',
        'Sub-brand name_prev': 'Sub-brand name',
        'Product name_prev': 'Product name',
        'Market1_prev': 'Market 1',
        'Market2_prev': 'Market 2',
        'Start Date_prev': 'Start Date (Previous)'
    })
    # Also rename the creative columns to remove the '_prev' suffix for presentation
    rename_mapping = {f"{col}_prev": col for col in creative_cols_from_prev_summary}
    ongoing.rename(columns=rename_mapping, inplace=True)


    # --- New campaigns ---
    new = current_summary[~current_summary['ID Campaign'].isin(prev_ids)].copy()
    new.drop(columns=['End Date'], inplace=True, errors='ignore')


    return ended, ongoing, new

def write_to_excel(ended, ongoing, new, campaign, prev_week, curr_week):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        text_wrap_format = workbook.add_format({'text_wrap': True, 'valign': 'top'})

        def write_df(sheet_name, df):
            worksheet = workbook.add_worksheet(sheet_name)

            if df.empty:
                headers = ['ID Campaign', 'Campaign', 'Brand name', 'Sub-brand name',
                           'Product name', 'Market 1', 'Market 2', 'Start Date', 'End Date']
                if sheet_name != "Ended Campaigns":
                    for i in range(1, 6):
                        headers.append(f'Creatives (Ad View) {i}')
                for col_num, header in enumerate(headers):
                    worksheet.write(0, col_num, header)
                    worksheet.set_column(col_num, col_num, 20)
                return

            creative_cols = [col for col in df.columns if col.startswith("Creatives")]
            ordered_cols = [col for col in df.columns if col not in creative_cols] + sorted(creative_cols, key=lambda x: int(x.split()[-1]))

            df = df[ordered_cols]
            for col_num, col in enumerate(df.columns):
                worksheet.write(0, col_num, col)
                worksheet.set_column(col_num, col_num, max(len(col), 20))
                if col in ['Sub-brand name', 'Product name', 'Market 1', 'Market 2']:
                    worksheet.set_column(col_num, col_num, None, text_wrap_format)

            for row_idx, row in df.iterrows():
                for col_idx, col in enumerate(df.columns):
                    val = row[col]
                    if col.startswith("Creatives") and pd.notna(val):
                        worksheet.write_url(row_idx + 1, col_idx, val, string="View Creative")
                    else:
                        if col in ['Sub-brand name', 'Product name', 'Market 1', 'Market 2']:
                            worksheet.write(row_idx + 1, col_idx, val, text_wrap_format)
                        else:
                            worksheet.write(row_idx + 1, col_idx, val)

        write_df("Ended Campaigns", ended)
        write_df("Ongoing Campaigns", ongoing)
        write_df("New Campaigns", new)

    output.seek(0)
    return output


if uploaded_files:
    with st.spinner("Processing..."):
        # Store uploaded files temporarily
        temp_file_map = {}
        for f in uploaded_files:
            temp_file_map[f.name] = f

        # Organize files by campaign and week
        campaign_files = {}
        for name, file in temp_file_map.items():
            match = re.match(r"(.+?) week (\d+)\.csv", name, re.IGNORECASE)
            if match:
                campaign = match.group(1).strip()
                week = int(match.group(2))
                if campaign not in campaign_files:
                    campaign_files[campaign] = {}
                campaign_files[campaign][week] = file
            else:
                st.warning(f"⚠️ File skipped (invalid naming): {name}")

        # Compare
        for campaign, weeks_data in campaign_files.items():
            if len(weeks_data) >= 2:
                sorted_weeks = sorted(weeks_data.keys())
                for i in range(len(sorted_weeks) - 1):
                    prev_week = sorted_weeks[i]
                    curr_week = sorted_weeks[i + 1]
                    st.subheader(f"🔍 Comparing {campaign}: Week {prev_week} vs Week {curr_week}")
                    df1 = pd.read_csv(weeks_data[prev_week])
                    df2 = pd.read_csv(weeks_data[curr_week])
                    # Simulate file saving for comparison
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f1, \
                         tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f2:
                        df1.to_csv(f1.name, index=False)
                        df2.to_csv(f2.name, index=False)

                        ended, ongoing, new = compare_two_files(f1.name, f2.name)

                    if ended is not None:
                        st.markdown("**✅ Ended Campaigns**")
                        st.dataframe(ended if not ended.empty else pd.DataFrame({"Message": ["No campaigns ended."]}))

                        st.markdown("**🔁 Ongoing Campaigns**")
                        st.dataframe(ongoing if not ongoing.empty else pd.DataFrame({"Message": ["No ongoing campaigns."]}))

                        st.markdown("**🆕 New Campaigns**")
                        st.dataframe(new if not new.empty else pd.DataFrame({"Message": ["No new campaigns."]}))

                        excel_bytes = write_to_excel(ended, ongoing, new, campaign, prev_week, curr_week)
                        filename = f"{campaign} Week {prev_week}_vs_Week {curr_week} Comparison.xlsx"
                        st.download_button("📥 Download Excel", excel_bytes, file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.warning(f"⚠️ Not enough data to compare '{campaign}' — need at least 2 weeks.")

