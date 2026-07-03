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
# ROBUST PARSER – extracts all teachers line by line
# ------------------------------------------------------------
@st.cache_data
def parse_pdf(pdf_file):
    all_rows = []
    debug_info = {}

    with pdfplumber.open(pdf_file) as pdf:
        full_text = ""
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                full_text += text + "\n"
                debug_info[f"page_{page_num}_text_sample"] = text[:500]  # first 500 chars

        # Split into lines
        lines = full_text.split('\n')
        debug_info["total_lines"] = len(lines)

        # We'll try to find the date header to map days
        # The header has "2026/03/09" ... "2026/03/13"
        date_pattern = r'(\d{4}/\d{2}/\d{2})'
        all_dates = re.findall(date_pattern, full_text)
        # Assume the first 5 unique dates are the week
        unique_dates = list(dict.fromkeys(all_dates))  # preserve order
        if len(unique_dates) >= 5:
            dates = unique_dates[:5]
        else:
            # fallback: hardcode the known week
            dates = ['2026/03/09', '2026/03/10', '2026/03/11', '2026/03/12', '2026/03/13']
        debug_info["dates_used"] = dates

        # Process each line
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Find all time tokens (HH:MM) and dashes (-)
            tokens = re.findall(r'(\d{2}:\d{2}|-)', line)
            if not tokens:
                continue

            # Find the position of the first token to separate number+name
            first_token = tokens[0]
            idx = line.find(first_token)
            if idx == -1:
                continue

            # The part before first token contains number and name
            prefix = line[:idx].strip()
            # Extract number (leading digits)
            num_match = re.match(r'^(\d+)', prefix)
            if not num_match:
                continue
            no = num_match.group(1)
            # The rest is the name
            name = prefix[len(no):].strip()
            if not name:
                # If name is empty, skip
                continue

            # Pair tokens into (in, out) for each day
            # We expect 5 days, but we'll just pair sequentially
            pairs = []
            for i in range(0, len(tokens), 2):
                in_time = tokens[i] if tokens[i] != '-' else None
                out_time = tokens[i+1] if i+1 < len(tokens) and tokens[i+1] != '-' else None
                pairs.append((in_time, out_time))

            # Assign to the 5 dates (pad with None if fewer days)
            for day_idx, (in_time, out_time) in enumerate(pairs):
                if day_idx >= len(dates):
                    break
                date_str = dates[day_idx]
                all_rows.append({
                    'No': no,
                    'Name': name,
                    'Date': date_str,
                    'SignIn': in_time,
                    'SignOut': out_time
                })

            # If we have fewer pairs than dates, we can optionally add rows with None, but that would inflate attendance counts.
            # We'll only add rows where at least one time exists (already done)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values(['No', 'Date']).reset_index(drop=True)

    # Add debug info about teachers found
    debug_info["teachers_found"] = df['Name'].unique().tolist() if not df.empty else []
    debug_info["total_teachers"] = len(debug_info["teachers_found"])

    return df, debug_info

# ------------------------------------------------------------
# HELPER FUNCTIONS (same as before)
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
# STREAMLIT DASHBOARD (same UI, with extra debug)
# ------------------------------------------------------------
st.set_page_config(page_title="Teacher Attendance Dashboard", layout="wide")
st.title("📋 Teacher Attendance Dashboard – Term 1, Week 5 (9–13 March 2026)")

uploaded_file = st.sidebar.file_uploader("Upload PDF Report", type="pdf")

if uploaded_file is not None:
    with st.spinner("Parsing PDF ..."):
        df, debug = parse_pdf(uploaded_file)

    # Debug expander
    with st.expander("🔍 Debug Info"):
        st.write(f"**Total lines extracted:** {debug.get('total_lines', 0)}")
        st.write(f"**Teachers found:** {debug.get('total_teachers', 0)}")
        if debug.get('teachers_found'):
            st.write("**Names found:**")
            st.write(debug['teachers_found'])
        st.write("**Dates used:**", debug.get('dates_used', []))
        st.write("**Raw text sample (first 500 chars):**")
        st.text(debug.get('page_1_text_sample', 'No text extracted'))

    if df.empty:
        st.warning("No attendance data found. Please check the debug info above.")
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
