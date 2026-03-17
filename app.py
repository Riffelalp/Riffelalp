import os
import sqlite3
import io
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from cryptography.fernet import Fernet
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# --------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------

FERNET_KEY = os.environ.get("FERNET_KEY")
if not FERNET_KEY:
    # Generate a key on first run. In production this should be set as an
    # environment variable and kept secret.
    FERNET_KEY = Fernet.generate_key().decode()
    st.warning(
        "FERNET_KEY not found. Generated a new key for this session.\n"
        "Set the FERNET_KEY environment variable to persist data across restarts."
    )
fernet = Fernet(FERNET_KEY.encode())

DB_ENC_FILE = "responses.db.enc"
DB_FILE = "responses.db"
ADMIN_PWD = os.environ.get("ADMIN_PWD", "admin")

# --------------------------------------------------------------------
# Database helpers - database is stored encrypted with Fernet
# --------------------------------------------------------------------

def decrypt_db() -> None:
    """Decrypt the encrypted SQLite file to a temporary plain file."""
    if os.path.exists(DB_ENC_FILE):
        with open(DB_ENC_FILE, "rb") as f:
            encrypted = f.read()
        data = fernet.decrypt(encrypted)
        with open(DB_FILE, "wb") as f:
            f.write(data)

    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        cols = ", ".join([f"q{i} INTEGER" for i in range(1, 19)])
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS responses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                {cols},
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()
        encrypt_db()


def encrypt_db() -> None:
    """Encrypt the temporary plain SQLite file and remove it."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "rb") as f:
            data = f.read()
        encrypted = fernet.encrypt(data)
        with open(DB_ENC_FILE, "wb") as f:
            f.write(encrypted)
        os.remove(DB_FILE)


def open_conn():
    decrypt_db()
    return sqlite3.connect(DB_FILE)


def close_conn(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.close()
    encrypt_db()


def insert_response(name: str, role: str, answers: list[int]) -> int:
    conn = open_conn()
    cols = ",".join([f"q{i}" for i in range(1, 19)])
    placeholders = ",".join(["?" for _ in range(18 + 2)])
    cur = conn.execute(
        f"INSERT INTO responses(name, role, {cols}) VALUES ({placeholders})",
        [name, role, *answers],
    )
    row_id = cur.lastrowid
    close_conn(conn)
    return row_id


def delete_response(row_id: int) -> None:
    conn = open_conn()
    conn.execute("DELETE FROM responses WHERE id=?", (row_id,))
    close_conn(conn)


def fetch_data() -> pd.DataFrame:
    conn = open_conn()
    df = pd.read_sql_query("SELECT * FROM responses", conn)
    close_conn(conn)
    return df

# --------------------------------------------------------------------
# Scoring helpers
# --------------------------------------------------------------------

DIMS = {
    "Strategy": [f"q{i}" for i in range(1, 7)],
    "Data": [f"q{i}" for i in range(7, 13)],
    "Culture": [f"q{i}" for i in range(13, 19)],
}


def compute_aggregates(df: pd.DataFrame) -> pd.Series:
    """Return average score per dimension and global average."""
    if df.empty:
        return pd.Series({k: 0 for k in list(DIMS) + ["Global"]})

    for dim, cols in DIMS.items():
        df[dim] = df[cols].mean(axis=1)
    df["Global"] = df[[f"q{i}" for i in range(1, 19)]].mean(axis=1)
    agg = {dim: df[dim].mean() for dim in DIMS.keys()}
    agg["Global"] = df["Global"].mean()
    return pd.Series(agg)


def make_radar(agg: pd.Series):
    fig = go.Figure()
    labels = list(agg.index)
    values = list(agg.values)
    fig.add_trace(
        go.Scatterpolar(r=values, theta=labels, fill="toself", name="Average")
    )
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 5])),
        showlegend=False,
        margin=dict(l=40, r=40, t=40, b=40),
    )
    return fig


def export_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def export_pdf(df: pd.DataFrame, agg: pd.Series) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    c.drawString(40, 760, "AI Maturity Report")
    c.drawString(40, 740, f"Generated: {datetime.utcnow().isoformat()} UTC")

    y = 710
    for dim, val in agg.items():
        c.drawString(40, y, f"{dim}: {val:.2f}")
        y -= 20

    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

# --------------------------------------------------------------------
# Streamlit UI helpers
# --------------------------------------------------------------------

st.set_page_config(page_title="AI Maturity Questionnaire", page_icon="🤖")

# Tailwind-like simple styling
st.markdown(
    """
    <style>
    html, body {font-family: 'Inter', sans-serif;}
    .stButton>button {background-color:#4f46e5;color:white;border-radius:0.25rem;border:none;padding:0.5rem 1rem;}
    .stButton>button:hover {background-color:#4338ca;}
    </style>
    """,
    unsafe_allow_html=True,
)

if "page" not in st.session_state:
    st.session_state.page = 1

# --------------------------------------------------------------------
# UI Pages
# --------------------------------------------------------------------

def page_intro():
    st.title("AI Maturity Assessment")
    st.write(
        "Fill in the questionnaire to get an overview of your organisation's AI maturity."
    )
    st.session_state.name = st.text_input("Name", value=st.session_state.get("name", ""))
    st.session_state.role = st.text_input("Role", value=st.session_state.get("role", ""))
    if st.button("Next"):
        if not st.session_state.name or not st.session_state.role:
            st.error("Name and Role are required.")
        else:
            st.session_state.page = 2
            st.experimental_rerun()


def page_questions():
    st.header("Questionnaire")
    q_text = [f"Question {i}" for i in range(1, 19)]
    for idx, text in enumerate(q_text, start=1):
        st.session_state[f"q{idx}"] = st.slider(
            text, 1, 5, value=3, key=f"q{idx}_slider", help="1=Low, 5=High"
        )
    if st.button("Submit"):
        answers = [st.session_state[f"q{i}"] for i in range(1, 19)]
        row_id = insert_response(st.session_state.name, st.session_state.role, answers)
        st.session_state["row_id"] = row_id
        st.session_state.page = 3
        st.experimental_rerun()


def page_thankyou():
    st.success("Thank you for your responses!")
    if st.button("Delete my data"):
        delete_response(st.session_state.get("row_id"))
        st.success("Your data has been removed.")
    if st.button("New response"):
        st.session_state.page = 1
        st.experimental_rerun()
    st.write("---")
    admin_access()


def admin_access():
    pwd = st.text_input("Admin password", type="password")
    if pwd == ADMIN_PWD:
        st.session_state.page = "admin"
        st.experimental_rerun()


def admin_page():
    st.title("Admin Dashboard")
    df = fetch_data()
    agg = compute_aggregates(df)
    st.subheader("Average Scores")
    st.write(agg)
    st.plotly_chart(make_radar(agg), use_container_width=True)
    st.download_button(
        "Download CSV", export_csv(df), file_name="responses.csv", mime="text/csv"
    )
    st.download_button(
        "Download PDF", export_pdf(df, agg), file_name="report.pdf", mime="application/pdf"
    )
    if st.button("Back"):
        st.session_state.page = 1
        st.experimental_rerun()

# --------------------------------------------------------------------
# Page router
# --------------------------------------------------------------------

page = st.session_state.page
if page == 1:
    page_intro()
elif page == 2:
    page_questions()
elif page == 3:
    page_thankyou()
elif page == "admin":
    admin_page()
