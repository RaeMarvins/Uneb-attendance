import streamlit as st
import pandas as pd
import plotly.express as px
import re
from io import BytesIO

try:
    import pdfplumber
except ImportError:
    st.error("pdfplumber is not installed. Please add it to requirements.txt and redeploy.")
    st.stop()

# ------------------------------------------------------------
# PARSER WITH DEBUG AND FALLBACK
# ------------------------------------------------------------
@st.cache_data
def parse_pdf(pdf_file):
    all_rows = []
    debug_info = {}

    with pdfplumber.open(pdf_file) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # 1. Try table extraction
            tables = page.extract_tables()
            if tables:
                debug_info[f"page_{page_num}_tables"] = tables
                for table in tables:
                    if not table:
                        continue
                    # Try to find header with "No" and "Name"
                    header = table[0]
                    try:
                        no_idx = next(i for i, col in enumerate(header) if col and 'No' in col)
                        name_idx = next(i for i, col in enumerate(header) if col and 'Name' in col)
                    except StopIteration:
                        continue

                    # The remaining columns should be paired (in/out per day)
                    # We'll extract all date-like strings from the header
                    date_cols = []
                    for i, col in enumerate(header):
                        if i not in (no_idx, name_idx) and col:
                            if re.search(r'\d{4}/\d{2}/\d{2}', col):
                                date_cols.append(i)

                    # For each data row
                    for row in table[1:]:
                        if not row or len(row) < 2:
                            continue
                        no = str(row[no_idx]).strip() if row[no_idx] else ''
                        name = str(row[name_idx]).strip() if row[name_idx] else ''
                        if not name or not no:
                            continue

                        # For each date column, the next cell might be the out time
                        # But if the date header is merged, we might have only one column per date
                        # We'll try to get both in and out from the cell or the next cell
                        for i in date_cols:
                            in_time = out_time = None
                            if i < len(row):
                                cell = str(row[i]).strip() if row[i] else ''
                                # Find times
                                times = re.findall(r'\d{2}:\d{2}', cell)
                                if len(times) >= 1:
                                    in_time = times[0]
                                if len(times) >= 2:
                                    out_time = times[1]
                                # If only one time, check next cell for out
                                if len(times) == 1 and i+1 < len(row):
                                    next_cell = str(row[i+1]).strip() if row[i+1] else ''
                                    if re.search(r'\d{2}:\d{2}', next_cell):
                                        out_time = re.search(r'\d{2}:\d{2}', next_cell).group()
                            if in_time or out_time:
                                # We need the date label; we can derive from header but we might not have it
                                # We'll use the column index as placeholder; later we'll map to actual dates
                                date_label = f"Day_{i}"  # temporary
                                all_rows.append({
                                    'No': no,
                                    'Name': name,
                                    'Date': date_label,
                                    'SignIn': in_time,
                                    'SignOut': out_time
                                })

            # 2. If we got rows from tables, skip fallback
            if all_rows:
                continue

            # 3. Fallback: extract raw text and parse line by line
            text = page.extract_text()
            if text:
                debug_info[f"page_{page_num}_text"] = text
                lines = text.split('\n')
                # We expect each teacher on a line like "001KARAMAGI JAMES08:28 21:05..."
                # But lines may have spaces; we'll try to find patterns
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Try to match No and Name at start
                    # Pattern: digits followed by a name (letters and spaces), then the rest
                    m = re.match(r'^(\d+)\s*([A-Za-z ]+?)(\d{2}:\d{2}.*)', line)
                    if not m:
                        # Maybe there is no space between number and name
                        m = re.match(r'^(\d+)([A-Za-z ]+?)(\d{2}:\d{2}.*)', line)
                    if m:
                        no = m.group(1)
                        name = m.group(2).strip()
                        rest = m.group(3)
                        # Now extract all tokens: either a time or a dash
                        tokens = re.findall(r'(\d{2}:\d{2}|-)', rest)
                        # We will pair tokens into (in,out) pairs
                        # We need to know how many days; we can try to infer from the header
                        # For now, assume 5 days (since we know the report is week 5)
                        # But we'll just pair sequentially; if odd number, last has only in
                        pairs = []
                        for i in range(0, len(tokens), 2):
                            if i+1 < len(tokens):
                                pairs.append((tokens[i], tokens[i+1]))
                            else:
                                pairs.append((tokens[i], None))
                        # Assign to days (we don't know actual dates; we'll use Day1..Day5)
                        for idx, (in_time, out_time) in enumerate(pairs, start=1):
                            if in_time or out_time:
                                all_rows.append({
                                    'No': no,
                                    'Name': name,
                                    'Date': f'Day{idx}',
                                    'SignIn': in_time if in_time != '-' else None,
                                    'SignOut': out_time if out_time != '-' else None
                                })

    # Convert to DataFrame
    df = pd.DataFrame(all_rows)
    if not df.empty:
        # Try to convert Date to actual dates if we have them from header
        # For now, we keep as string; later we can map
        df['Date'] = df['Date'].astype(str)
        df = df.sort_values(['No', 'Date']).reset_index(drop=True)
    return df, debug_info

# ------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------
def is_late(time_str, threshold='08:00'):
    if not time_str or time_str == '-':
        return False
    try:
        t = pd.to_datetime(time_str, format='%H:%M').time()
        th = pd.to_datetime(threshold, format='%H:%M').time()
        return t > th
    except:
        return False

def compute_summary(df):
    if df.empty:
        return pd.DataFrame(), 0
    total_days = df['Date'].nunique()
    summary = df.groupby(['No', 'Name']).agg(
        Days_Attended=('SignIn', 'count'),
        Late_Days=('SignIn', lambda x: sum(is_late(v) for v in x if pd.notna(v)))
    ).reset_index()
    summary['Attendance_Rate'] = (summary['Days_Attended'] / total_days * 100).round(1)
    summary['Late_Rate'] = (summary['Late_Days'] / summary['Days_Attended'] * 100).round(1).fillna(0)
    return summary, total_days

# ------------------------------------------------------------
# STREAMLIT DASHBOARD
# ------------------------------------------------------------
st.set_page_config(page_title="Teacher Attendance Dashboard", layout="wide")
st.title("📋 Teacher Attendance Dashboard – Term 1, Week 5 (9–13 March 2026)")

uploaded_file = st.sidebar.file_uploader("Upload PDF Report", type="pdf")

if uploaded_file is not None:
    with st.spinner("Parsing PDF ..."):
        df, debug = parse_pdf(uploaded_file)

    # Show debug info (expandable)
    with st.expander("🔍 Debug: Raw Extracted Data"):
        st.write("Number of rows extracted:", len(df))
        if df.empty:
            st.warning("No attendance data found. Debug info below may help.")
        st.write("Debug dictionary (tables, text, etc.):")
        st.json(debug)  # show raw tables/text if any

    if df.empty:
        st.stop()

    with st.expander("📄 View Extracted Data"):
        st.dataframe(df)

    summary, total_days = compute_summary(df)

    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("👩‍🏫 Total Teachers", summary.shape[0])
    with col2:
        st.metric("📅 Total Days", total_days)
    with col3:
        avg_att = summary['Attendance_Rate'].mean()
        st.metric("📊 Avg Attendance Rate", f"{avg_att:.1f}%")
    with col4:
        total_late = summary['Late_Days'].sum()
        st.metric("⏰ Total Late Arrivals", total_late)

    # Chart
    st.subheader("📈 Attendance Rate per Teacher")
    fig = px.bar(summary, x='Name', y='Attendance_Rate', color='Attendance_Rate',
                 color_continuous_scale='Blues', text='Attendance_Rate',
                 labels={'Attendance_Rate': 'Attendance Rate (%)'})
    fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
    st.plotly_chart(fig, use_container_width=True)

    # Teacher details
    st.subheader("🔍 Teacher Details")
    teacher_list = summary['Name'].tolist()
    selected = st.selectbox("Select Teacher", teacher_list)
    if selected:
        teacher_data = df[df['Name'] == selected].copy().sort_values('Date')
        teacher_data['SignIn'] = teacher_data['SignIn'].fillna('-')
        teacher_data['SignOut'] = teacher_data['SignOut'].fillna('-')
        teacher_data['Late'] = teacher_data['SignIn'].apply(lambda x: 'Yes' if is_late(x) else 'No')
        st.dataframe(teacher_data[['Date', 'SignIn', 'SignOut', 'Late']], use_container_width=True)

        t_summary = summary[summary['Name'] == selected].iloc[0]
        st.write(f"**Days Attended:** {t_summary['Days_Attended']} / {total_days}  "
                 f"**Late Days:** {t_summary['Late_Days']}  "
                 f"**Attendance Rate:** {t_summary['Attendance_Rate']}%")

    # Export
    st.subheader("📥 Export Data")
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Attendance Data (CSV)", data=csv,
                       file_name="attendance_data.csv", mime="text/csv")

else:
    st.info("👈 Please upload the PDF report to get started.")
