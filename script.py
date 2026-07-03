import streamlit as st
import pandas as pd
import pdfplumber
import re
from io import BytesIO
import plotly.express as px

# ------------------------------------------------------------
# 1. PDF PARSER (handles the messy text extraction robustly)
# ------------------------------------------------------------
def parse_pdf_table(pdf_file):
    """
    Extracts the attendance table from the PDF using pdfplumber.
    Assumes the table has columns: No., Name, and then pairs of times for each day.
    Returns a pandas DataFrame.
    """
    with pdfplumber.open(pdf_file) as pdf:
        # We expect the table on the first page (or across pages)
        # Try to extract tables from all pages and combine
        all_rows = []
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                # Skip empty tables
                if not table:
                    continue
                # The first row is the header
                header = table[0]
                # Find column indices for 'No.', 'Name', and the date columns
                # We'll search for 'No' and 'Name' in the header
                try:
                    no_idx = next(i for i, col in enumerate(header) if col and 'No' in col)
                    name_idx = next(i for i, col in enumerate(header) if col and 'Name' in col)
                except StopIteration:
                    # Fallback: assume first two columns are No. and Name
                    no_idx, name_idx = 0, 1

                # The remaining columns are date pairs (In/Out)
                # We'll extract the date strings from the header
                date_cols = []
                for i, col in enumerate(header):
                    if i not in (no_idx, name_idx) and col:
                        # Try to parse a date like "2026/03/09"
                        if re.search(r'\d{4}/\d{2}/\d{2}', col):
                            date_cols.append((i, col.strip()))
                # Group date columns into pairs (In, Out) – we assume they alternate
                # Some PDFs may have a single column per date with both times, but we'll handle both.

                # Now iterate over data rows (skip header)
                for row in table[1:]:
                    if not row or len(row) < 2:
                        continue
                    no = row[no_idx].strip() if row[no_idx] else ''
                    name = row[name_idx].strip() if row[name_idx] else ''
                    if not name or not no:
                        continue
                    # Collect time pairs for each date
                    record = {'No': no, 'Name': name}
                    # For each date column pair, extract the times
                    for idx, (col_pos, date_str) in enumerate(date_cols):
                        # If we have enough columns, take the next two after the name
                        # But the table might have a single column per date with both times concatenated
                        # We'll try to get the cell value at col_pos and col_pos+1 if available
                        in_time = out_time = None
                        if col_pos < len(row):
                            cell = row[col_pos].strip() if row[col_pos] else ''
                            # If cell contains a time pattern, it might be a single entry
                            # Otherwise, try to get the next column for out time
                            if re.search(r'\d{2}:\d{2}', cell):
                                # Could be just the in time, or both separated by space/newline
                                times = re.findall(r'\d{2}:\d{2}', cell)
                                if len(times) >= 1:
                                    in_time = times[0]
                                if len(times) >= 2:
                                    out_time = times[1]
                                else:
                                    # If only one time, check if next column has the out time
                                    if col_pos + 1 < len(row) and row[col_pos+1]:
                                        next_cell = row[col_pos+1].strip()
                                        if re.search(r'\d{2}:\d{2}', next_cell):
                                            out_time = re.search(r'\d{2}:\d{2}', next_cell).group()
                            else:
                                # Cell might be empty or dashes – try next column for in time
                                if col_pos + 1 < len(row) and row[col_pos+1]:
                                    next_cell = row[col_pos+1].strip()
                                    if re.search(r'\d{2}:\d{2}', next_cell):
                                        in_time = re.search(r'\d{2}:\d{2}', next_cell).group()
                        # Store in/out for this date
                        record[f'{date_str}_in'] = in_time
                        record[f'{date_str}_out'] = out_time
                    all_rows.append(record)

    # Build DataFrame
    df = pd.DataFrame(all_rows)
    if df.empty:
        st.error("No data extracted. Please check the PDF format.")
        return df

    # Melt the DataFrame to long format: each row is (No, Name, Date, In, Out)
    # Identify all date columns (those ending with _in or _out)
    in_cols = [c for c in df.columns if c.endswith('_in')]
    out_cols = [c for c in df.columns if c.endswith('_out')]
    # Extract date strings
    dates = sorted(set([c.replace('_in', '').replace('_out', '') for c in in_cols + out_cols]))
    # Create long format
    long_rows = []
    for _, row in df.iterrows():
        for d in dates:
            in_time = row.get(f'{d}_in')
            out_time = row.get(f'{d}_out')
            # Only add if at least one time exists
            if in_time or out_time:
                long_rows.append({
                    'No': row['No'],
                    'Name': row['Name'],
                    'Date': d,
                    'SignIn': in_time,
                    'SignOut': out_time
                })
    long_df = pd.DataFrame(long_rows)
    # Convert Date to datetime
    long_df['Date'] = pd.to_datetime(long_df['Date'])
    # Sort
    long_df = long_df.sort_values(['No', 'Date']).reset_index(drop=True)
    return long_df

# ------------------------------------------------------------
# 2. HELPER FUNCTIONS FOR METRICS
# ------------------------------------------------------------
def is_late(time_str, threshold='08:00'):
    """Check if sign-in time is later than threshold."""
    if not time_str or time_str == '-':
        return False
    try:
        t = pd.to_datetime(time_str, format='%H:%M').time()
        th = pd.to_datetime(threshold, format='%H:%M').time()
        return t > th
    except:
        return False

def compute_attendance_summary(df):
    """Compute per-teacher attendance stats."""
    if df.empty:
        return pd.DataFrame()
    # Group by teacher
    summary = df.groupby(['No', 'Name']).agg(
        Days_Attended=('SignIn', 'count'),
        Late_Days=('SignIn', lambda x: sum(is_late(v) for v in x if pd.notna(v))),
        Total_Days=('Date', 'nunique')  # total available days (though we have only days present)
    ).reset_index()
    # Total possible days = number of unique dates in the whole report
    total_possible = df['Date'].nunique()
    summary['Attendance_Rate'] = (summary['Days_Attended'] / total_possible * 100).round(1)
    summary['Late_Rate'] = (summary['Late_Days'] / summary['Days_Attended'] * 100).round(1).fillna(0)
    return summary, total_possible

# ------------------------------------------------------------
# 3. STREAMLIT DASHBOARD
# ------------------------------------------------------------
st.set_page_config(page_title="Teacher Attendance Dashboard", layout="wide")
st.title("📋 Teacher Attendance Dashboard – Term 1, Week 5 (9–13 March 2026)")

# Sidebar for file upload
st.sidebar.header("Upload PDF Report")
uploaded_file = st.sidebar.file_uploader("Choose PDF file", type="pdf")

if uploaded_file is not None:
    with st.spinner("Parsing PDF ..."):
        df = parse_pdf_table(uploaded_file)
    
    if df.empty:
        st.stop()
    
    # Display raw data (optional)
    with st.expander("📄 View Extracted Raw Data"):
        st.dataframe(df)
    
    # Compute summary
    summary, total_days = compute_attendance_summary(df)
    
    # --------------------------------------------------------
    # DASHBOARD LAYOUT
    # --------------------------------------------------------
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
    
    # --------------------------------------------------------
    # Attendance Rate Distribution
    # --------------------------------------------------------
    st.subheader("📈 Attendance Rate per Teacher")
    fig = px.bar(summary, x='Name', y='Attendance_Rate', color='Attendance_Rate',
                 color_continuous_scale='Blues', text='Attendance_Rate',
                 labels={'Attendance_Rate': 'Attendance Rate (%)'})
    fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
    st.plotly_chart(fig, use_container_width=True)
    
    # --------------------------------------------------------
    # Individual Teacher Details
    # --------------------------------------------------------
    st.subheader("🔍 Teacher Details")
    teacher_list = summary['Name'].tolist()
    selected_teacher = st.selectbox("Select Teacher", teacher_list)
    
    if selected_teacher:
        teacher_data = df[df['Name'] == selected_teacher].copy()
        teacher_data = teacher_data.sort_values('Date')
        # Format times
        teacher_data['SignIn'] = teacher_data['SignIn'].fillna('-')
        teacher_data['SignOut'] = teacher_data['SignOut'].fillna('-')
        # Add Late flag
        teacher_data['Late'] = teacher_data['SignIn'].apply(lambda x: 'Yes' if is_late(x) else 'No')
        st.dataframe(teacher_data[['Date', 'SignIn', 'SignOut', 'Late']], use_container_width=True)
        
        # Teacher's summary
        t_summary = summary[summary['Name'] == selected_teacher].iloc[0]
        st.write(f"**Days Attended:** {t_summary['Days_Attended']} / {total_days}  "
                 f"**Late Days:** {t_summary['Late_Days']}  "
                 f"**Attendance Rate:** {t_summary['Attendance_Rate']}%")
    
    # --------------------------------------------------------
    # Export Data
    # --------------------------------------------------------
    st.subheader("📥 Export Data")
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Attendance Data (CSV)", data=csv, file_name="attendance_data.csv", mime="text/csv")
    
else:
    st.info("👈 Please upload the PDF report to get started.")
