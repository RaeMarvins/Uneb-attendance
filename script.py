import streamlit as st
import pandas as pd
import plotly.express as px
import re
from io import BytesIO

# Try importing pdfplumber, show error if missing
try:
    import pdfplumber
except ImportError:
    st.error("pdfplumber is not installed. Please add it to your requirements.txt and redeploy.")
    st.stop()

# ------------------------------------------------------------
# CACHED PARSER – runs only once per uploaded file
# ------------------------------------------------------------
@st.cache_data
def parse_pdf(pdf_file):
    """
    Extracts the attendance table from the uploaded PDF.
    Returns a long-format DataFrame with columns: No, Name, Date, SignIn, SignOut.
    """
    all_rows = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                header = table[0]
                # Locate "No." and "Name" columns
                try:
                    no_idx = next(i for i, col in enumerate(header) if col and 'No' in col)
                    name_idx = next(i for i, col in enumerate(header) if col and 'Name' in col)
                except StopIteration:
                    # Fallback: assume first two columns
                    no_idx, name_idx = 0, 1

                # Extract date strings from header (e.g., "2026/03/09")
                date_cols = []
                for i, col in enumerate(header):
                    if i not in (no_idx, name_idx) and col:
                        m = re.search(r'\d{4}/\d{2}/\d{2}', col)
                        if m:
                            date_cols.append((i, m.group()))

                # Process data rows
                for row in table[1:]:
                    if not row or len(row) < 2:
                        continue
                    no = str(row[no_idx]).strip() if row[no_idx] else ''
                    name = str(row[name_idx]).strip() if row[name_idx] else ''
                    if not name or not no:
                        continue

                    # For each date, extract in/out times
                    for col_pos, date_str in date_cols:
                        in_time = out_time = None
                        if col_pos < len(row):
                            cell = str(row[col_pos]).strip() if row[col_pos] else ''
                            # Find all HH:MM in the cell
                            times = re.findall(r'\d{2}:\d{2}', cell)
                            if len(times) >= 1:
                                in_time = times[0]
                            if len(times) >= 2:
                                out_time = times[1]
                            # If only one time, check next column for out time
                            if len(times) == 1 and col_pos + 1 < len(row):
                                next_cell = str(row[col_pos+1]).strip() if row[col_pos+1] else ''
                                if re.search(r'\d{2}:\d{2}', next_cell):
                                    out_time = re.search(r'\d{2}:\d{2}', next_cell).group()
                        # Only store if at least one time exists
                        if in_time or out_time:
                            all_rows.append({
                                'No': no,
                                'Name': name,
                                'Date': date_str,
                                'SignIn': in_time,
                                'SignOut': out_time
                            })

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values(['No', 'Date']).reset_index(drop=True)
    return df

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
        df = parse_pdf(uploaded_file)

    if df.empty:
        st.warning("No attendance data found in the PDF. Please check the file format.")
        st.stop()

    with st.expander("📄 View Extracted Raw Data"):
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
