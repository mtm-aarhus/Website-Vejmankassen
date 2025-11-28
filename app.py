from flask import Flask, render_template, request, jsonify, Response, stream_with_context, redirect, session
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
import os
import csv
import io
from decimal import Decimal
import requests
import jwt
import time
from functools import wraps
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError


# --- Flask app ---
app = Flask(__name__)
csrf = CSRFProtect(app)
# Exempt API POST endpoints
app.secret_key = os.getenv("FLASK_SECRET_KEY")  
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12)
)
app.config["PREFERRED_URL_SCHEME"] = "https"

last_button_press = None
conn_str = os.getenv('VejmanKassenSQLTEST')
#conn_str = os.getenv('VejmanKassenSQL')


engine = create_engine(conn_str, pool_pre_ping=True, pool_size=10, max_overflow=20)

def get_connection():
    return engine

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):

        # Development bypass (only active on local dev)
        if app.debug or app.env == "development":
            if request.host.startswith("127.0.0.1") or request.host.startswith("localhost"):
                session["user"] = {
                    "email": "test@aarhus.dk",
                    "name": "Local Developer",
                    "groups": ["Vejmankassen-Sagsbehandler"]
                }
                return f(*args, **kwargs)

        # Production
        if "user" not in session:
            next_url = request.url
            login_url = f"https://pyorchestrator.aarhuskommune.dk/api/auth/login?next={next_url}"
            return redirect(login_url)

        return f(*args, **kwargs)
    return wrapper


def user_has_role(*needed):
    user = session.get("user")
    if not user:
        return False
    groups = user.get("groups", [])
    return any(g in groups for g in needed)

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not user_has_role(*roles):
                return jsonify(success=False, error="Access denied"), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def log_action(conn, *, row_id, action_type, old, new, user_email):
    sql = text("""
        INSERT INTO VejmanFaktureringLog (
            RowID, ActionType,
            OldMeter, NewMeter,
            OldStartdato, NewStartdato,
            OldSlutdato, NewSlutdato,
            OldStatus, NewStatus,
            PerformedBy
        )
        VALUES (
            :RowID, :ActionType,
            :OldMeter, :NewMeter,
            :OldStartdato, :NewStartdato,
            :OldSlutdato, :NewSlutdato,
            :OldStatus, :NewStatus,
            :PerformedBy
        )
    """)

    conn.execute(sql, {
        "RowID": row_id,
        "ActionType": action_type,

        "OldMeter": old.get("Meter"),
        "NewMeter": new.get("Meter"),
        "OldStartdato": old.get("Startdato"),
        "NewStartdato": new.get("Startdato"),
        "OldSlutdato": old.get("Slutdato"),
        "NewSlutdato": new.get("Slutdato"),

        "OldStatus": old.get("FakturaStatus"),
        "NewStatus": new.get("FakturaStatus"),

        "PerformedBy": user_email
    })


# --- Helpers ---
def fmt_date(d):
    try:
        return datetime.strptime(str(d), '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        try:
            # Try parsing common variants
            return datetime.fromisoformat(str(d)).strftime('%d-%m-%Y')
        except Exception:
            return ''

def fmt_date_iso(d):
    """Helper to ISO (yyyy-mm-dd) for <input type='date'>."""
    try:
        return datetime.strptime(str(d), '%Y-%m-%d').strftime('%Y-%m-%d')
    except Exception:
        try:
            return datetime.fromisoformat(str(d)).strftime('%Y-%m-%d')
        except Exception:
            return ''

def fmt_num(x):
    """
    Show up to 2 decimals, but trim trailing zeros (Danish comma).
    Examples:
      2.8     -> "2,8"
      2.800   -> "2,8"
      0.0     -> "0"
      12.340  -> "12,34"
      3.14159 -> "3,14"
    """
    try:
        s = f"{float(x):.2f}".rstrip('0').rstrip('.')
        return s.replace('.', ',')
    except Exception:
        return ''

def parse_number_to_float(s):
    """Parse '1,23' or '1.23' to float or raise."""
    return float(str(s).replace(',', '.'))

# Allowed columns for sorting from the UI -> map to DB columns
SORTABLE_COLUMNS = {
    'Ansøger': 'Ansøger',
    'Adresse': 'FørsteSted',
    'Tilladelsesnr': 'Tilladelsesnr',
    'CvrNr': 'CvrNr',
    'TilladelsesType': 'TilladelsesType',
    'Enhedspris': 'Enhedspris',
    'Meter': 'Meter',
    'Startdato': 'Startdato',
    'Slutdato': 'Slutdato',
    'AntalDage': 'AntalDage',
    'TotalPris': 'TotalPris',
    'FakturaDato': 'FakturaDato',
    'Ordrenummer': 'Ordrenummer'
}

# ------------------------- Pages -------------------------

@app.route('/')
@login_required
def ikkefaktureret_page():
    return render_template('ikkefaktureret.html', page_title='Ikke Faktureret')

@app.route('/til-fakturering')
@login_required
def til_fakturering_page():
    return render_template('tilfakturering.html', page_title='Til fakturering')

@app.route('/faktureret')
@login_required
def faktureret_page():
    return render_template('faktureret.html', page_title='Faktureret')

@app.route('/statistik')
@login_required
def statistik_page():
    return render_template('statistik.html', page_title='Statistik')

@app.route('/konflikter')
@login_required
def konflikter_page():
    return render_template('konflikter.html', page_title='Konflikter')

# ------------------------- API: Lists -------------------------

@app.route('/api/ikkefaktureret')
def ikkefaktureret_data():
    """Server-side endpoint for the 'Ikke faktureret' view.
    New logic: rows with FakturaStatus = 'Ny'.
    """
    engine = get_connection()

    # Pagination
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0

    # Search & sorting
    search = (request.args.get('search') or '').strip()
    sort_ui = (request.args.get('sort') or 'Ansøger').strip()
    order = (request.args.get('order') or 'asc').upper()
    order = 'DESC' if order.lower() == 'desc' else 'ASC'
    sort_col = SORTABLE_COLUMNS.get(sort_ui, 'Ansøger')

    base_where = "FakturaStatus = 'Ny'"

    params = {}
    search_clause = ''
    if search:
        search_clause = """
            AND (
                Ansøger LIKE :q OR
                FørsteSted LIKE :q OR
                Tilladelsesnr LIKE :q OR
                CONVERT(varchar(20), CvrNr) LIKE :q OR
                TilladelsesType LIKE :q
            )
        """
        params['q'] = f"%{search}%"

    count_sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
    """)

    data_sql = text(f"""
        SELECT
            ID,
            VejmanID,
            Ansøger,
            FørsteSted,
            Tilladelsesnr,
            CvrNr,
            TilladelsesType,
            Enhedspris,
            Meter,
            Startdato,
            Slutdato,
            AntalDage,
            TotalPris,
            FakturaStatus
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
        ORDER BY {sort_col} {order}
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    with engine.begin() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(data_sql, {**params, 'offset': offset, 'limit': limit}).mappings().all()

    out = []
    for r in rows:
        out.append({
            'ID': r['ID'],
            'VejmanID': r['VejmanID'],
            'Ansøger': r['Ansøger'] or '',
            'Adresse': r['FørsteSted'] or '',
            'Tilladelsesnr': r['Tilladelsesnr'] or '',
            'CvrNr': r['CvrNr'] or '',
            'TilladelsesType': r['TilladelsesType'] or '',
            'Enhedspris': fmt_num(r['Enhedspris']),
            'Meter': fmt_num(r['Meter']),
            'Startdato': fmt_date(r['Startdato']),
            'Slutdato': fmt_date(r['Slutdato']),
            'AntalDage': r['AntalDage'] if r['AntalDage'] is not None else 0,
            'TotalPris': fmt_num(r['TotalPris']),
            'FakturaStatus': r['FakturaStatus'] or '',
        })

    return jsonify({'total': total, 'rows': out})

@app.route('/api/tilfakturering')
def tilfakturering_data():
    """Server-side for 'Til fakturering' (FakturaStatus='Afsendt')."""
    engine = get_connection()

    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0

    search = (request.args.get('search') or '').strip()
    sort_ui = (request.args.get('sort') or 'Ansøger').strip()
    order = (request.args.get('order') or 'asc').upper()
    order = 'DESC' if order.lower() == 'desc' else 'ASC'
    sort_col = SORTABLE_COLUMNS.get(sort_ui, 'Ansøger')

    base_where = "FakturaStatus = 'Afsendt'"

    params = {}
    search_clause = ''
    if search:
        search_clause = """
            AND (
                Ansøger LIKE :q OR
                FørsteSted LIKE :q OR
                Tilladelsesnr LIKE :q OR
                CONVERT(varchar(20), CvrNr) LIKE :q OR
                TilladelsesType LIKE :q
            )
        """
        params['q'] = f"%{search}%"

    count_sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
    """)

    data_sql = text(f"""
        SELECT
            ID,
            VejmanID,
            Ansøger,
            FørsteSted,
            Tilladelsesnr,
            CvrNr,
            TilladelsesType,
            Enhedspris,
            Meter,
            Startdato,
            Slutdato,
            AntalDage,
            TotalPris,
            FakturaStatus
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
        ORDER BY {sort_col} {order}
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    with engine.begin() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(data_sql, {**params, 'offset': offset, 'limit': limit}).mappings().all()

    out = []
    for r in rows:
        out.append({
            'ID': r['ID'],
            'VejmanID': r['VejmanID'],
            'Ansøger': r['Ansøger'] or '',
            'Adresse': r['FørsteSted'] or '',
            'Tilladelsesnr': r['Tilladelsesnr'] or '',
            'CvrNr': r['CvrNr'] or '',
            'TilladelsesType': r['TilladelsesType'] or '',
            'Enhedspris': fmt_num(r['Enhedspris']),
            'Meter': fmt_num(r['Meter']),
            'Startdato': fmt_date(r['Startdato']),
            'Slutdato': fmt_date(r['Slutdato']),
            'AntalDage': r['AntalDage'] if r['AntalDage'] is not None else 0,
            'TotalPris': fmt_num(r['TotalPris']),
            'FakturaStatus': r['FakturaStatus'] or '',
        })

    return jsonify({'total': total, 'rows': out})

@app.route('/api/faktureret')
def faktureret_data():
    """Server-side for 'Faktureret' (FakturaStatus='Faktureret')."""
    engine = get_connection()

    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0

    search = (request.args.get('search') or '').strip()
    sort_ui = (request.args.get('sort') or 'Ansøger').strip()
    order = (request.args.get('order') or 'asc').upper()
    order = 'DESC' if order.lower() == 'desc' else 'ASC'
    sort_col = SORTABLE_COLUMNS.get(sort_ui, 'Ansøger')

    base_where = "FakturaStatus = 'Faktureret'"

    params = {}
    search_clause = ''
    if search:
        search_clause = """
            AND (
                Ansøger LIKE :q OR
                FørsteSted LIKE :q OR
                Tilladelsesnr LIKE :q OR
                CONVERT(varchar(20), CvrNr) LIKE :q OR
                TilladelsesType LIKE :q
                OR CONVERT(varchar(20), Ordrenummer) LIKE :q
            )
        """
        params['q'] = f"%{search}%"

    count_sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
    """)

    data_sql = text(f"""
        SELECT
            ID,
            VejmanID,
            Ansøger,
            FørsteSted,
            Tilladelsesnr,
            CvrNr,
            TilladelsesType,
            Startdato,
            Slutdato,
            TotalPris,
            FakturaDato,
            Ordrenummer
        FROM [dbo].[VejmanFakturering]
        WHERE {base_where} {search_clause}
        ORDER BY {sort_col} {order}
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    with engine.begin() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(data_sql, {**params, 'offset': offset, 'limit': limit}).mappings().all()

    out = []
    for r in rows:
        out.append({
            'ID': r['ID'],
            'VejmanID': r['VejmanID'],
            'Ansøger': r['Ansøger'] or '',
            'Adresse': r['FørsteSted'] or '',
            'Tilladelsesnr': r['Tilladelsesnr'] or '',
            'CvrNr': r['CvrNr'] or '',
            'TilladelsesType': r['TilladelsesType'] or '',
            'Startdato': fmt_date(r['Startdato']),
            'Slutdato': fmt_date(r['Slutdato']),
            'TotalPris': fmt_num(r['TotalPris']),
            'FakturaDato': fmt_date(r['FakturaDato']),
            'Ordrenummer': r['Ordrenummer'],
        })

    return jsonify({'total': total, 'rows': out})

# ------------------------- Row: Read & Update -------------------------

@app.route('/api/fakturering/<int:row_id>')
def get_row(row_id):
    engine = get_connection()
    sql = text("""
        SELECT TOP 1
            ID,
            VejmanID,
            FørsteSted,
            Tilladelsesnr,
            Ansøger,
            CvrNr,
            TilladelsesType,
            Enhedspris,
            Meter,
            Startdato,
            Slutdato,
            AntalDage,
            TotalPris,
            FakturaStatus,
            FakturaNr,
            VejmanFakturaID,
            ATT,
            FakturaDato,
            Ordrenummer
        FROM [dbo].[VejmanFakturering]
        WHERE ID = :id
    """)
    with engine.begin() as conn:
        row = conn.execute(sql, {'id': row_id}).mappings().first()

    if not row:
        return jsonify(success=False, error='Række ikke fundet'), 404

    status = (row['FakturaStatus'] or '').strip()

    return jsonify(success=True, data={
        'ID': row['ID'],
        'VejmanID': row['VejmanID'],
        'Adresse': row['FørsteSted'] or '',
        'Tilladelsesnr': row['Tilladelsesnr'] or '',
        'Ansøger': row['Ansøger'] or '',
        'CvrNr': str(row['CvrNr'] or ''),
        'TilladelsesType': row['TilladelsesType'] or '',
        'Enhedspris': fmt_num(row['Enhedspris']),
        'Meter': fmt_num(row['Meter']),
        'Startdato': fmt_date_iso(row['Startdato']),
        'Slutdato': fmt_date_iso(row['Slutdato']),
        'AntalDage': row['AntalDage'],
        'TotalPris': fmt_num(row['TotalPris']),
        'FakturaStatus': status,
        'FakturaNr': row['FakturaNr'] or '',
        'VejmanFakturaID': row['VejmanFakturaID'],
        'ATT': row['ATT'] or '',
        'FakturaDato': fmt_date_iso(row['FakturaDato']),
        'Ordrenummer': row['Ordrenummer'] or ''
    })

@csrf.exempt
@app.route('/update', methods=['POST'])
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler")
def update_row():
    """
    Only allow editing of Meter, Startdato, Slutdato. Status is updated only if the
    payload explicitly asks to send for billing or mark as do-not-invoice.
    """
    data = request.get_json(force=True) or {}
    errors = []

    row_id = data.get('ID')
    meter_raw = data.get('Meter', '')
    start_raw = data.get('Startdato', '')
    slut_raw = data.get('Slutdato', '')
    send_flag = data.get('SendTilFakturering', None)  # optional trigger to set status 'Afsendt'
    no_invoice_flag = data.get('FakturerIkke', None)  # optional trigger to set status 'FakturerIkke'

    # Validate id
    if not row_id:
        errors.append("ID mangler.")

    # Parse number (comma or dot)
    meter = None
    try:
        meter = parse_number_to_float(meter_raw)
    except Exception:
        errors.append("Meter skal være et gyldigt tal.")

    # Parse dates (yyyy-mm-dd or dd-mm-yyyy)
    def parse_date_any(s, field_name):
        s = (s or '').strip()
        if not s:
            return None
        # ISO (from <input type='date'>)
        try:
            return datetime.strptime(s, '%Y-%m-%d').strftime('%Y-%m-%d')
        except Exception:
            pass
        # dd-mm-yyyy (manual)
        try:
            return datetime.strptime(s, '%d-%m-%Y').strftime('%Y-%m-%d')
        except Exception:
            errors.append(f"{field_name} skal være en gyldig dato (yyyy-mm-dd eller dd-mm-yyyy).")
            return None

    startdato = parse_date_any(start_raw, 'Startdato')
    slutdato = parse_date_any(slut_raw, 'Slutdato')

    # interpret optional flags
    set_status_to = None
    if send_flag is not None and str(send_flag).lower() in ('1', 'true', 'ja', 'yes'):
        set_status_to = 'Afsendt'
    if no_invoice_flag is not None and str(no_invoice_flag).lower() in ('1', 'true', 'ja', 'yes'):
        set_status_to = 'FakturerIkke'

    if errors:
        return jsonify(success=False, errors=errors), 400

    # Build dynamic SET for only the fields we allow
    set_parts = ["Meter = :Meter", "Startdato = :Startdato", "Slutdato = :Slutdato"]
    params = {
        'Meter': meter,
        'Startdato': startdato,
        'Slutdato': slutdato,
        'ID': row_id
    }
    if set_status_to:
        set_parts.append("FakturaStatus = :FakturaStatus")
        params['FakturaStatus'] = set_status_to

    sql_update = text(f"""
        UPDATE [dbo].[VejmanFakturering]
        SET {', '.join(set_parts)}
        WHERE ID = :ID
    """)

    sql_select = text("""
        SELECT
            ID, VejmanID, Ansøger, FørsteSted, Tilladelsesnr, CvrNr, TilladelsesType,
            Enhedspris, Meter, Startdato, Slutdato, AntalDage, TotalPris, FakturaStatus
        FROM [dbo].[VejmanFakturering]
        WHERE ID = :ID
    """)

    engine = get_connection()

    sql_before = text("""
        SELECT Meter, Startdato, Slutdato, FakturaStatus
        FROM [dbo].[VejmanFakturering]
        WHERE ID = :ID
    """)

    with engine.begin() as conn:
        old_row = conn.execute(sql_before, {"ID": row_id}).mappings().first()
        res = conn.execute(sql_update, params)
        if res.rowcount == 0:
            return jsonify(success=False, errors=['Fakturalinjen blev ikke fundet.']), 404

        # Fetch the row again
        r = conn.execute(sql_select, {'ID': row_id}).mappings().first()
        new_row = conn.execute(sql_select, {"ID": row_id}).mappings().first()

        action = "edit"
        if set_status_to == "Afsendt":
            action = "send_for_billing"
        elif set_status_to == "FakturerIkke":
            action = "mark_do_not_invoice"

        log_action(
            conn,
            row_id=row_id,
            action_type=action,
            old=old_row,
            new=new_row,
            user_email=session["user"]["email"]
        )


    updated_row = {
        'ID': r['ID'],
        'VejmanID': r['VejmanID'],
        'Ansøger': r['Ansøger'] or '',
        'Adresse': r['FørsteSted'] or '',
        'Tilladelsesnr': r['Tilladelsesnr'] or '',
        'CvrNr': r['CvrNr'] or '',
        'TilladelsesType': r['TilladelsesType'] or '',
        'Enhedspris': fmt_num(r['Enhedspris']),
        'Meter': fmt_num(r['Meter']),
        'Startdato': fmt_date(r['Startdato']),
        'Slutdato': fmt_date(r['Slutdato']),
        'AntalDage': r['AntalDage'] if r['AntalDage'] is not None else 0,
        'TotalPris': fmt_num(r['TotalPris']),
        'FakturaStatus': r['FakturaStatus'] or '',
    }

    return jsonify(success=True, data=updated_row)

# Fortryd from 'Til fakturering' list -> set back to 'Ny'
@csrf.exempt
@app.route('/api/tilfakturering/fortryd/<int:row_id>', methods=['POST'])
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler")
def fortryd_fakturering(row_id):
    engine = get_connection()
    sql = text("""
        UPDATE [dbo].[VejmanFakturering]
        SET FakturaStatus = 'Ny'
        WHERE ID = :id
    """)
    with engine.begin() as conn:
        old = conn.execute(text("""
            SELECT Meter, Startdato, Slutdato, FakturaStatus
            FROM VejmanFakturering
            WHERE ID = :id
        """), {"id": row_id}).mappings().first()

        res = conn.execute(sql, {'id': row_id})


        new = { **old, "FakturaStatus": "Ny" }

        log_action(conn,
            row_id=row_id,
            action_type="undo_send",
            old=old,
            new=new,
            user_email=session["user"]["email"]
        )

        if res.rowcount == 0:
            return jsonify(success=False, error='Række ikke fundet'), 404
    return jsonify(success=True, data={'ID': row_id})

# 'Faktureret' -> reinvoice button: move to 'FakturerIkke'
@csrf.exempt
@app.route('/api/faktureret/fortryd/<int:row_id>', methods=['POST'])
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler")
def faktureret_fortryd(row_id):
    engine = get_connection()
    sql = text("""
        UPDATE [dbo].[VejmanFakturering]
        SET FakturaStatus = 'FakturerIkke'
        WHERE ID = :id
    """)
    with engine.begin() as conn:
        old = conn.execute(text("""
            SELECT Meter, Startdato, Slutdato, FakturaStatus
            FROM VejmanFakturering
            WHERE ID = :id
        """), {"id": row_id}).mappings().first()

        res = conn.execute(sql, {'id': row_id})
        new = { **old, "FakturaStatus": "FakturerIkke" }

        log_action(conn,
            row_id=row_id,
            action_type="undo_faktureret",
            old=old,
            new=new,
            user_email=session["user"]["email"]
        )
        if res.rowcount == 0:
            return jsonify(success=False, error='Række ikke fundet'), 404
    return jsonify(success=True, data={'ID': row_id})

# ------------------------- Konflikter ------------------------

@app.route('/api/issues')
@login_required
def api_issues():
    engine = get_connection()

    # Pagination
    try:
        limit = int(request.args.get('limit', 10))
    except:
        limit = 10
    try:
        offset = int(request.args.get('offset', 0))
    except:
        offset = 0

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or '').strip()
    mine = (request.args.get('mine') or '').lower() == "true"

    # Session user
    user = session.get("user")
    user_email = user["email"] if user else None

    # Build dynamic filters
    where_clauses = []
    params = {}

    if search:
        where_clauses.append("""
            (
                v.Tilladelsesnr LIKE :q OR
                i.IssueType LIKE :q OR
                i.Fakturalinje LIKE :q OR
                i.IssueDescription LIKE :q OR
                i.SuggestedFix LIKE :q OR
                i.Status LIKE :q OR
                LEFT(i.CaseworkerEmail, CHARINDEX('@', i.CaseworkerEmail + '@') - 1) LIKE :q
            )
        """)
        params["q"] = f"%{search}%"

    # Status filter
    if status:
        where_clauses.append("Status = :status")
        params["status"] = status

    # My issues only
    if mine and user_email:
        where_clauses.append("CaseworkerEmail = :email")
        params["email"] = user_email

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Count total rows
    count_sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM dbo.InvoiceIssues i
        LEFT JOIN dbo.VejmanFakturering v
            ON i.InvoiceID = v.VejmanFakturaID
        {where_sql}
    """)

    # Data query
    data_sql = text(f"""
        SELECT
            i.*,
            v.VejmanID,
            v.Tilladelsesnr AS CaseNumber,
            LEFT(i.CaseworkerEmail, CHARINDEX('@', i.CaseworkerEmail + '@') - 1) AS ShortEmail
        FROM dbo.InvoiceIssues i
        LEFT JOIN dbo.VejmanFakturering v
            ON i.InvoiceID = v.VejmanFakturaID
        {where_sql}
        ORDER BY i.UpdatedAt DESC
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    params["offset"] = offset
    params["limit"] = limit

    with engine.begin() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(data_sql, params).mappings().all()

    # Convert RowMapping → dict → JSON-safe
    out_rows = [dict(r) for r in rows]

    return jsonify({
        "total": total,
        "rows": out_rows
    })


@csrf.exempt
@app.post('/api/issues/resolve/<int:issue_id>')
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler")
def api_issue_resolve(issue_id):
    user = session.get("user")
    if not user:
        return jsonify(success=False, error="Unauthorized"), 401

    email = user["email"]
    engine = get_connection()

    sql = text("""
        UPDATE dbo.InvoiceIssues
        SET
            Status = 'UserAccepted',
            ResolvedBy = :email,
            ResolvedAt = GETDATE(),
            UpdatedAt = GETDATE()
        WHERE IssueID = :id AND Status = 'Open'
    """)

    with engine.begin() as conn:
        res = conn.execute(sql, {"email": email, "id": issue_id})

    if res.rowcount == 0:
        return jsonify(success=False, error="Issue not found or already resolved")

    return jsonify(success=True)

@csrf.exempt
@app.post('/api/issues/unresolve/<int:issue_id>')
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler")
def api_issue_unresolve(issue_id):
    engine = get_connection()

    sql = text("""
        UPDATE dbo.InvoiceIssues
        SET
            Status = 'Open',
            ResolvedBy = NULL,
            ResolvedAt = NULL,
            UpdatedAt = GETDATE()
        WHERE IssueID = :id
    """)

    with engine.begin() as conn:
        res = conn.execute(sql, {"id": issue_id})

    if res.rowcount == 0:
        return jsonify(success=False, error="Issue not found")

    return jsonify(success=True)

@app.route('/api/nav_counts')
@login_required
def api_nav_counts():
    engine = get_connection()

    sql = text("""
        SELECT
          (SELECT COUNT(*) FROM VejmanFakturering WHERE FakturaStatus='Ny') AS new_rows,
          (SELECT COUNT(*) FROM InvoiceIssues WHERE Status='Open') AS open_issues
    """)

    with engine.begin() as conn:
        row = conn.execute(sql).mappings().first()

    return jsonify(dict(row) if row else {})

# ------------------------- Statistik -------------------------

def _status_where_fragments(selected_statuses):
    """Map legacy bucket names to FakturaStatus single column.
    Buckets (checkbox values in UI):
      - faktureret          -> FakturaStatus = 'Faktureret'
      - ikke_faktureret     -> FakturaStatus = 'Ny'
      - sendt_til_fakturering -> FakturaStatus = 'Afsendt'
      - under_fakturering   -> FakturaStatus = 'TilFakturering' 
      - fakturer_ikke       -> FakturaStatus = 'FakturerIkke'
    """
    if not selected_statuses:
        return "", {}

    clauses = []
    for s in selected_statuses:
        s = s.strip().lower()
        if s == 'faktureret':
            clauses.append("FakturaStatus = 'Faktureret'")
        elif s == 'ikke_faktureret':
            clauses.append("FakturaStatus = 'Ny'")
        elif s == 'sendt_til_fakturering':
            clauses.append("FakturaStatus = 'Afsendt'")
        elif s == 'under_fakturering':
            clauses.append("FakturaStatus = 'TilFakturering'")
        elif s == 'fakturer_ikke':
            clauses.append("FakturaStatus = 'FakturerIkke'")
    if not clauses:
        return "", {}
    return "(" + " OR ".join(clauses) + ")", {}

def _date_filters(args):
    """Read date range filters from query args and return SQL + params.
    Args supported: start_from, start_to, slut_from, slut_to (all ISO yyyy-mm-dd)
    """
    where = []
    params = {}

    def add_date(field_db, key_from, key_to):
        v_from = (args.get(key_from) or '').strip()
        v_to = (args.get(key_to) or '').strip()
        if v_from:
            where.append(f"{field_db} >= :{key_from}")
            params[key_from] = v_from
        if v_to:
            where.append(f"{field_db} <= :{key_to}")
            params[key_to] = v_to

    add_date("Startdato", "start_from", "start_to")
    add_date("Slutdato",  "slut_from",  "slut_to")
    return where, params

def _type_filter(args):
    """Multiple choice TilladelsesType filter. Query arg: types = comma-separated list."""
    types_raw = (args.get('types') or '').strip()
    if not types_raw:
        return "", {}

    values = [v.strip() for v in types_raw.split(',') if v.strip()]
    if not values:
        return "", {}

    placeholders = []
    params = {}
    for idx, val in enumerate(values):
        key = f"t{idx}"
        placeholders.append(f":{key}")
        params[key] = val
    return f"COALESCE(TilladelsesType, '') IN ({', '.join(placeholders)})", params

@app.route('/api/statistik')
def statistik_data():
    """Table data for Statistik (filters by status/types/dates, server-side pagination)."""
    engine = get_connection()

    # Pagination
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0

    # Search & sort
    search = (request.args.get('search') or '').strip()
    sort_ui = (request.args.get('sort') or 'Ansøger').strip()
    order = (request.args.get('order') or 'asc').upper()
    order = 'DESC' if order.lower() == 'desc' else 'ASC'
    sort_col = SORTABLE_COLUMNS.get(sort_ui, 'Ansøger')

    # Filters
    statuses_raw = (request.args.get('statuses') or '').strip()
    selected_statuses = [s for s in statuses_raw.split(',') if s.strip()] if statuses_raw else []

    where_clauses = []
    params = {}

    status_sql, status_params = _status_where_fragments(selected_statuses)
    if status_sql:
        where_clauses.append(status_sql)
        params.update(status_params)

    type_sql, type_params = _type_filter(request.args)
    if type_sql:
        where_clauses.append(type_sql)
        params.update(type_params)

    date_sqls, date_params = _date_filters(request.args)
    if date_sqls:
        where_clauses.extend(date_sqls)
        params.update(date_params)

    if search:
        where_clauses.append("""
            (
                Ansøger LIKE :q OR
                FørsteSted LIKE :q OR
                Tilladelsesnr LIKE :q OR
                CONVERT(varchar(20), CvrNr) LIKE :q OR
                TilladelsesType LIKE :q
            )
        """)
        params['q'] = f"%{search}%"

    where_all = " AND ".join(where_clauses) if where_clauses else "1=1"

    count_sql = text(f"""
        SELECT COUNT(*) AS cnt
        FROM [dbo].[VejmanFakturering]
        WHERE {where_all}
    """)

    data_sql = text(f"""
        SELECT
            ID,
            VejmanID,
            Ansøger,
            FørsteSted,
            Tilladelsesnr,
            CvrNr,
            TilladelsesType,
            Enhedspris,
            Meter,
            Startdato,
            Slutdato,
            AntalDage,
            TotalPris,
            FakturaStatus
        FROM [dbo].[VejmanFakturering]
        WHERE {where_all}
        ORDER BY {sort_col} {order}
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """)

    with engine.begin() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(data_sql, {**params, 'offset': offset, 'limit': limit}).mappings().all()

    out = []
    for r in rows:
        out.append({
            'ID': r['ID'],
            'VejmanID': r['VejmanID'],
            'Ansøger': r['Ansøger'] or '',
            'Adresse': r['FørsteSted'] or '',
            'Tilladelsesnr': r['Tilladelsesnr'] or '',
            'CvrNr': r['CvrNr'] or '',
            'TilladelsesType': r['TilladelsesType'] or '',
            'Enhedspris': fmt_num(r['Enhedspris']),
            'Meter': fmt_num(r['Meter']),
            'Startdato': fmt_date(r['Startdato']),
            'Slutdato': fmt_date(r['Slutdato']),
            'AntalDage': r['AntalDage'] if r['AntalDage'] is not None else 0,
            'TotalPris': fmt_num(r['TotalPris']),
            'FakturaStatus': r['FakturaStatus'] or '',
        })

    return jsonify({'total': total, 'rows': out})

@app.route('/api/statistik/metrics')
def statistik_metrics():
    """Aggregated metrics for Statistik dashboard (same filters as /api/statistik)."""
    engine = get_connection()

    # Same filters
    statuses_raw = (request.args.get('statuses') or '').strip()
    selected_statuses = [s for s in statuses_raw.split(',') if s.strip()] if statuses_raw else []
    where_clauses = []
    params = {}

    status_sql, status_params = _status_where_fragments(selected_statuses)
    if status_sql:
        where_clauses.append(status_sql)
        params.update(status_params)

    type_sql, type_params = _type_filter(request.args)
    if type_sql:
        where_clauses.append(type_sql)
        params.update(type_params)

    date_sqls, date_params = _date_filters(request.args)
    if date_sqls:
        where_clauses.extend(date_sqls)
        params.update(date_params)

    search = (request.args.get('search') or '').strip()
    if search:
        where_clauses.append("""
            (
                Ansøger LIKE :q OR
                FørsteSted LIKE :q OR
                Tilladelsesnr LIKE :q OR
                CONVERT(varchar(20), CvrNr) LIKE :q OR
                TilladelsesType LIKE :q
            )
        """)
        params['q'] = f"%{search}%"

    where_all = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Overall totals
    sql_totals = text(f"""
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(TotalPris), 0) AS total_pris,
          COALESCE(SUM(NULLIF(Meter, 0)), 0) AS sum_meter,
          COALESCE(SUM(NULLIF(AntalDage, 0)), 0) AS sum_dage
        FROM [dbo].[VejmanFakturering]
        WHERE {where_all}
    """)

    # Status breakdowns via single-column CASE
    sql_status = text(f"""
        SELECT
          SUM(CASE WHEN FakturaStatus = 'Faktureret' THEN 1 ELSE 0 END) AS cnt_faktureret,
          COALESCE(SUM(CASE WHEN FakturaStatus = 'Faktureret' THEN TotalPris ELSE 0 END), 0) AS sum_faktureret,

          SUM(CASE WHEN FakturaStatus = 'Ny' THEN 1 ELSE 0 END) AS cnt_ikke,
          COALESCE(SUM(CASE WHEN FakturaStatus = 'Ny' THEN TotalPris ELSE 0 END), 0) AS sum_ikke,

          SUM(CASE WHEN FakturaStatus = 'Afsendt' THEN 1 ELSE 0 END) AS cnt_sendt,
          COALESCE(SUM(CASE WHEN FakturaStatus = 'Afsendt' THEN TotalPris ELSE 0 END), 0) AS sum_sendt,

          SUM(CASE WHEN FakturaStatus = 'UnderFakturering' THEN 1 ELSE 0 END) AS cnt_under,
          COALESCE(SUM(CASE WHEN FakturaStatus = 'UnderFakturering' THEN TotalPris ELSE 0 END), 0) AS sum_under,

          SUM(CASE WHEN FakturaStatus = 'FakturerIkke' THEN 1 ELSE 0 END) AS cnt_nej,
          COALESCE(SUM(CASE WHEN FakturaStatus = 'FakturerIkke' THEN TotalPris ELSE 0 END), 0) AS sum_nej
        FROM [dbo].[VejmanFakturering]
        WHERE {where_all}
    """)

    # Per-type sums
    sql_types = text(f"""
        SELECT
          COALESCE(TilladelsesType, '') AS TilladelsesType,
          COUNT(*) AS row_count,
          COALESCE(SUM(TotalPris), 0) AS total_pris
        FROM [dbo].[VejmanFakturering]
        WHERE {where_all}
        GROUP BY COALESCE(TilladelsesType, '')
        ORDER BY total_pris DESC, row_count DESC
    """)

    with engine.begin() as conn:
        t = conn.execute(sql_totals, params).mappings().first()
        s = conn.execute(sql_status, params).mappings().first()
        type_rows = conn.execute(sql_types, params).mappings().all()

    payload = {
        "totals": {
            "row_count": int(t['row_count'] or 0),
            "total_pris": float(t['total_pris'] or 0.0),
            "sum_meter": float(t['sum_meter'] or 0.0),
            "sum_dage": float(t['sum_dage'] or 0.0),
        },
        "status": {
            "faktureret":         {"count": int(s['cnt_faktureret'] or 0), "sum": float(s['sum_faktureret'] or 0.0)},
            "ikke_faktureret":    {"count": int(s['cnt_ikke'] or 0),       "sum": float(s['sum_ikke'] or 0.0)},
            "sendt_til_fakturering":{"count": int(s['cnt_sendt'] or 0),    "sum": float(s['sum_sendt'] or 0.0)},
            "under_fakturering":  {"count": int(s['cnt_under'] or 0),      "sum": float(s['sum_under'] or 0.0)},
            "fakturer_ikke":      {"count": int(s['cnt_nej'] or 0),        "sum": float(s['sum_nej'] or 0.0)},
        },
        "types": [
            {"TilladelsesType": r['TilladelsesType'] or '', "count": int(r['row_count'] or 0), "sum": float(r['total_pris'] or 0.0)}
            for r in type_rows
        ]
    }
    return jsonify(payload)

@app.route('/api/statistik/types')
def statistik_types():
    """Return all distinct TilladelsesType values (unfiltered)."""
    engine = get_connection()
    sql = text("""
        SELECT DISTINCT COALESCE(TilladelsesType, '') AS t
        FROM [dbo].[VejmanFakturering]
        ORDER BY t
    """)
    with engine.begin() as conn:
        rows = conn.execute(sql).all()
    return jsonify([r[0] for r in rows])

# ------------------------- CSV Export -------------------------

@app.route('/api/statistik/export-csv')
def statistik_export_csv():
    """Stream hele [dbo].[VejmanFakturering] som CSV (dansk format med ';')."""
    engine = get_connection()
    sql = text("SELECT * FROM [dbo].[VejmanFakturering] ORDER BY ID")  # includes FakturaStatus

    def generate():
        yield '\ufeff'  # UTF-8 BOM

        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(sql)

            out = io.StringIO()
            writer = csv.writer(out, delimiter=';', quoting=csv.QUOTE_MINIMAL)

            headers = list(result.keys())
            writer.writerow(headers)
            yield out.getvalue()
            out.seek(0); out.truncate(0)

            for row in result.mappings():
                values = []
                for col in headers:
                    val = row.get(col)
                    if val is None:
                        values.append('')
                    elif isinstance(val, (float, Decimal)):
                        values.append(str(val).replace('.', ','))  # dk decimal
                    else:
                        values.append(val)
                writer.writerow(values)
                yield out.getvalue()
                out.seek(0); out.truncate(0)

    fname = f"VejmanFakturering_{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(
        stream_with_context(generate()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )

# ------------------------- Trigger & Sync -------------------------

@csrf.exempt
@app.route('/reset_trigger', methods=['POST'])
@login_required
@role_required("Vejmankassen-Admin", "Vejmankassen-Sagsbehandler", "Vejmankassen-BI")
def reset_trigger():
    global last_button_press
    now = datetime.now()

    # Prevent multiple presses within 5 minutes
    if last_button_press and now - last_button_press < timedelta(minutes=5):
        remaining_time = 5 - (now - last_button_press).total_seconds() / 60
        return jsonify(success=False, message=f"Synkronisering allerede igangsat, vent {round(remaining_time, 1)} minutter"), 403

    try:
        api_key = os.getenv("PyOrchestratorAPIKey")
        if not api_key:
            return jsonify(success=False, message="API-nøgle mangler i miljøvariabler"), 500

        url = "https://pyorchestrator.aarhuskommune.dk/api/trigger"
        payload = {
            "trigger_name": "VejmanKassenWebsiteTrigger",
            "process_status": "IDLE"
        }
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key
        }

        response = requests.post(url, json=payload, headers=headers, timeout=15)

        if response.status_code == 200:
            last_button_press = now
            return jsonify(success=True, message="Synkronisering igangsat!")
        else:
            return jsonify(success=False, message=f"Fejl fra PyOrchestrator: {response.text}"), response.status_code

    except requests.exceptions.RequestException as e:
        return jsonify(success=False, message=f"Netværksfejl: {str(e)}"), 500
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

@csrf.exempt
@app.route('/get_last_button_press', methods=['GET'])
def get_last_button_press():
    global last_button_press
    return jsonify(timestamp=last_button_press.strftime('%Y-%m-%d %H:%M:%S') if last_button_press else None)

def get_last_sync_time():
    """Fetch latest sync timestamp from VejmanKassenSyncHistory."""
    try:
        engine = get_connection()
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT TOP 1 SyncedAt
                FROM VejmanKassenSyncHistory
                ORDER BY SyncedAt DESC
            """)).fetchone()

        if row and row.SyncedAt:
            return row.SyncedAt.strftime('%d-%m-%Y %H:%M:%S')

        return "Ukendt tidspunkt"

    except Exception as e:
        print("Error fetching sync time:", e)
        return "Fejl ved hentning"


@app.route('/api/sync/last', methods=['GET'])
def api_sync_last():
    return jsonify({"display": get_last_sync_time()})

@app.context_processor
def inject_last_sync():
    return {'last_sync': get_last_sync_time()}

@app.context_processor
def inject_user_roles():
    user = session.get("user", {})
    groups = user.get("groups", [])

    return {
        "user": user,
        "user_is_admin": "Vejmankassen-Admin" in groups,
        "user_is_sags": "Vejmankassen-Sagsbehandler" in groups,
        "user_is_bi": "Vejmankassen-BI" in groups
    }
JWT_SHARED_SECRET = os.getenv("PYORCHESTRATOR_JWT_SECRET")

@app.route('/login/token')
def login_token():
    token = request.args.get("jwt")
    next_url = request.args.get("next", "/")

    if not token:
        return "Missing token", 400

    try:
        decoded = jwt.decode(token, JWT_SHARED_SECRET, algorithms=["HS256"], leeway = 600)
    except Exception as e:
        return f"Invalid token", 400

    session.permanent = True
    session["user"] = {
        "email": decoded.get("email"),
        "name": decoded.get("name"),
        "groups": decoded.get("groups", []),
        "iat": decoded.get("iat"),
    }

    return redirect(next_url)


@app.route('/logout')
def logout():
    """Clear the user session."""
    session.clear()
    return redirect('/')

@app.route("/whoami")
def whoami():
    user = session.get("user")
    if not user:
        return "Not logged in", 401
    return jsonify(user)

if __name__ == '__main__':
    # Run dev server
    app.run(host='0.0.0.0', port=5000, debug=True)
