from flask import Flask, render_template, request, jsonify
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import logging
import os
from urllib.parse import urlparse, parse_qs


print("Current working directory:", os.getcwd())
print(f"Running on port: {os.environ.get('HTTP_PLATFORM_PORT')}")
print(os.getenv('VejmanKassenSQL'))

# Enable SQLAlchemy query logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
app = Flask(__name__)

# Database connection using SQLAlchemy
def get_connection():
    connection_string = os.getenv('VejmanKassenSQL')
    engine = create_engine(connection_string)
    return engine

# Route to display rows where "Faktureret" is 0

@app.route('/')
def index():
    engine = get_connection()
    query = "SELECT * FROM [dbo].[VejmanFakturering] WHERE (Faktureret = 0 OR Faktureret IS NULL) AND (SendTilFakturering = 0 or SendTilFakturering IS NULL) AND ([FakturerIkke] != 1 OR [FakturerIkke] IS NULL)"
    df = pd.read_sql(query, engine)
    # Format dates and decimals
    df['Startdato'] = pd.to_datetime(df['Startdato']).dt.strftime('%d-%m-%Y')
    df['Slutdato'] = pd.to_datetime(df['Slutdato']).dt.strftime('%d-%m-%Y')
    df['Enhedspris'] = df['Enhedspris'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Enhedspris' in df else ''
    df['Meter'] = df['Meter'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Meter' in df else ''
    return render_template('index.html', page_title="Ikke Faktureret", active_page='ikke-faktureret', data=df.to_dict(orient='records'))

# Route to display rows where "SendTilFakturering" is 1 and "Faktureret" is 0
@app.route('/til-fakturering')
def til_fakturering():
    engine = get_connection()
    query = "SELECT * FROM [dbo].[VejmanFakturering] WHERE SendTilFakturering = 1 AND (Faktureret = 0 OR Faktureret IS NULL) AND ([FakturerIkke] != 1 OR [FakturerIkke] IS NULL)"
    df = pd.read_sql(query, engine)
    
    # Format dates and decimals
    df['Startdato'] = pd.to_datetime(df['Startdato']).dt.strftime('%d-%m-%Y')
    df['Slutdato'] = pd.to_datetime(df['Slutdato']).dt.strftime('%d-%m-%Y')
    df['Enhedspris'] = df['Enhedspris'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Enhedspris' in df else ''
    df['Meter'] = df['Meter'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Meter' in df else ''
    
    return render_template('index.html', page_title="Til Fakturering", active_page='til-fakturering', data=df.to_dict(orient='records'))


# Route to display rows where "Faktureret" is 1
@app.route('/faktureret')
def faktureret():
    engine = get_connection()
    query = "SELECT * FROM [dbo].[VejmanFakturering] WHERE Faktureret = 1 AND ([FakturerIkke] != 1 OR [FakturerIkke] IS NULL)"
    df = pd.read_sql(query, engine)
    
    # Format dates and decimals
    df['Startdato'] = pd.to_datetime(df['Startdato']).dt.strftime('%d-%m-%Y')
    df['Slutdato'] = pd.to_datetime(df['Slutdato']).dt.strftime('%d-%m-%Y')
    df['Enhedspris'] = df['Enhedspris'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Enhedspris' in df else ''
    df['Meter'] = df['Meter'].map(lambda x: f"{x:.3f}".replace('.', ',')) if 'Meter' in df else ''
    
    return render_template('index.html', page_title="Faktureret", active_page="faktureret", data=df.to_dict(orient='records'))

@app.route('/indstillinger')
def indstillinger():
    try:
        engine = get_connection()
        query = "SELECT * FROM [dbo].[VejmanFakturaTekster]"
        df = pd.read_sql(query, engine)

        # Format dates
        df['FraStartdato'] = pd.to_datetime(df['FraStartdato'], errors='coerce').dt.strftime('%d-%m-%Y')
        df['FraSlutdato'] = pd.to_datetime(df['FraSlutdato'], errors='coerce').dt.strftime('%d-%m-%Y')

        return render_template(
            'indstillinger.html',
            page_title="Indstillinger",
            active_page="indstillinger",
            data=df.to_dict(orient='records')
        )
    except Exception as e:
        return f"Fejl: {str(e)}", 500

#Add row indstillinger
@app.route('/api/indstillinger', methods=['POST'])
def add_indstilling():
    data = request.json
    errors = []

    # Debugging: Print incoming data
    print("Received data:", data)

    # Validation logic
    if not data.get('Fakturalinje'):
        errors.append("Fakturalinje is required.")

    try:
        fra_startdato = datetime.strptime(data['FraStartdato'], '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        errors.append("FraStartdato must be in the format dd-mm-yyyy, e.g., 31-12-2024.")

    try:
        fra_slutdato = datetime.strptime(data['FraSlutdato'], '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        errors.append("FraSlutdato must be in the format dd-mm-yyyy, e.g., 31-12-2024.")

    try:
        materiale_nr_opus = int(data['MaterialeNrOpus']) if data['MaterialeNrOpus'] else None
    except ValueError:
        errors.append("MaterialeNrOpus must be a valid integer.")

    try:
        materiel_id_vejman = int(data['MaterielIDVejman']) if data['MaterielIDVejman'] else None
    except ValueError:
        errors.append("MaterielIDVejman must be a valid integer.")

    # Return errors if validation fails
    if errors:
        return jsonify(success=False, errors=errors)

    # Prepare the SQL query
    insert_query = text("""
        INSERT INTO [dbo].[VejmanFakturaTekster] 
        (Fakturalinje, Fordringstype, PSPElement, MaterialeNrOpus, KundRefId, Toptekst, Forklaring, MaterielIDVejman, FraStartdato, FraSlutdato)
        VALUES (:Fakturalinje, :Fordringstype, :PSPElement, :MaterialeNrOpus, :KundRefId, :Toptekst, :Forklaring, :MaterielIDVejman, :FraStartdato, :FraSlutdato)
    """)

    # Database connection
    engine = get_connection()
    try:
        with engine.begin() as connection:
            result = connection.execute(insert_query, {
                'Fakturalinje': data['Fakturalinje'],
                'Fordringstype': data.get('Fordringstype', None),
                'PSPElement': data.get('PSPElement', None),
                'MaterialeNrOpus': materiale_nr_opus,
                'KundRefId': data.get('KundRefId', None),
                'Toptekst': data.get('Toptekst', None),
                'Forklaring': data.get('Forklaring', None),
                'MaterielIDVejman': materiel_id_vejman,
                'FraStartdato': fra_startdato,
                'FraSlutdato': fra_slutdato
            })

        # Debugging: Check if rows were affected
        print("Rows affected:", result.rowcount)

        return jsonify(success=True, message=f"Indstillinger for fakturalinje {data['Fakturalinje']} er blevet oprettet")

    except Exception as e:
        # Debugging: Log the error
        print(f"Error occurred: {e}")
        return jsonify(success=False, error=str(e)), 500

#Update row indstillinger
@app.route('/api/indstillinger', methods=['PUT'])
def update_indstilling():
    try:
        data = request.json
        print("Received data:", data)
        fakturalinje = data.get('Fakturalinje')
        # Optional: Validate or transform date fields
        fra_startdato = data.get('FraStartdato')
        fra_slutdato = data.get('FraSlutdato')

        if fra_startdato:
            fra_startdato = datetime.strptime(fra_startdato, '%d-%m-%Y').strftime('%Y-%m-%d')
        if fra_slutdato:
            fra_slutdato = datetime.strptime(fra_slutdato, '%d-%m-%Y').strftime('%Y-%m-%d')

        engine = get_connection()
        with engine.begin() as conn:
            query = text("""
                UPDATE [dbo].[VejmanFakturaTekster]
                SET Fordringstype = :Fordringstype,
                    PSPElement = :PSPElement,
                    MaterialeNrOpus = :MaterialeNrOpus,
                    KundRefId = :KundRefId,
                    Toptekst = :Toptekst,
                    Forklaring = :Forklaring,
                    MaterielIDVejman = :MaterielIDVejman,
                    FraStartdato = :FraStartdato,
                    FraSlutdato = :FraSlutdato
                WHERE Fakturalinje = :Fakturalinje
            """)
            conn.execute(query, {
                'Fakturalinje': fakturalinje,
                'Fordringstype': data['Fordringstype'],
                'PSPElement': data['PSPElement'],
                'MaterialeNrOpus': int(data['MaterialeNrOpus']),
                'KundRefId': data['KundRefId'],
                'Toptekst': data['Toptekst'],
                'Forklaring': data['Forklaring'],
                'MaterielIDVejman': int(data['MaterielIDVejman']),
                'FraStartdato': fra_startdato,
                'FraSlutdato': fra_slutdato,
            })
        return jsonify({"message": f"Indstillinger for fakturalinje {fakturalinje} er blevet opdateret"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

#Delete row indstillinger
@app.route('/api/indstillinger', methods=['DELETE'])
def delete_indstilling():
    try:
        data = request.json
        print(data)
        fakturalinje = data.get('Fakturalinje')
        engine = get_connection()
        with engine.begin() as conn:
            query = text("DELETE FROM [dbo].[VejmanFakturaTekster] WHERE Fakturalinje = :Fakturalinje")
            conn.execute(query, {'Fakturalinje': fakturalinje})
        return jsonify({"message": f"Indstillinger for fakturalinje {fakturalinje} er blevet slettet"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# Route to update a row in the table
@app.route('/update', methods=['POST'])
def update():
    data = request.json
    errors = []

    # Debugging: Print incoming data
    print("Received data:", data)

    # Validation logic
    if not data.get('CvrNr') or not re.match(r"^\d{8}$", data['CvrNr']):
        errors.append("CVR skal være præcist 8 cifre.")

    try:
        enhedspris = float(data['Enhedspris'].replace(',', '.'))
    except ValueError:
        errors.append("Enhedspris skal være et gyldigt tal.")

    try:
        meter = float(data['Meter'].replace(',', '.'))
    except ValueError:
        errors.append("Meter skal være et gyldigt tal.")

    try:
        startdato = datetime.strptime(data['Startdato'], '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        errors.append("Startdato skal skrives som dag-måned-år i tal, f.eks. 31-12-2024.")

    try:
        slutdato = datetime.strptime(data['Slutdato'], '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        errors.append("Slutdato skal skrives som dag-måned-år i tal, f.eks. 31-12-2024.")

    # Return errors if validation fails
    if errors:
        return jsonify(success=False, errors=errors)

    # Prepare the SQL query
    update_query = text("""
        UPDATE [dbo].[VejmanFakturering]
        SET Ansøger = :Ansøger, FørsteSted = :FørsteSted, CvrNr = :CvrNr, TilladelsesType = :TilladelsesType, 
            Enhedspris = :Enhedspris, Meter = :Meter, Startdato = :Startdato, 
            Slutdato = :Slutdato, SendTilFakturering = :SendTilFakturering
        WHERE ID = :ID
    """)

    engine = get_connection()
    try:
        # Start a transaction and execute the query
        with engine.begin() as connection:
            result = connection.execute(update_query, {
                'Ansøger': data['Ansøger'],
                'FørsteSted': data['FørsteSted'],
                'CvrNr': data['CvrNr'],
                'TilladelsesType': data['TilladelsesType'],
                'Enhedspris': enhedspris,
                'Meter': meter,
                'Startdato': startdato,
                'Slutdato': slutdato,
                'SendTilFakturering': data['SendTilFakturering'],
                'ID': data['ID']
            })
        print("Rows affected:", result.rowcount)  # Debugging: Check if rows were affected
        return jsonify(success=True)
    except Exception as e:
        print("SQL fejl:", e)  # Log the error
        return jsonify(success=False, errors=[f"Database fejl: {str(e)}"])


import re
from urllib.parse import urlparse, parse_qs

@app.route('/add', methods=['GET', 'POST'])
def add_row():
    if request.method == 'POST':
        data = request.form
        errors = []
        # Extract and validate fields
        vejman_link = data.get('VejmanLink', '').strip()
        ansøger = data.get('Ansøger', '').strip()
        første_sted = data.get('FørsteSted', '').strip()
        tilladelsesnr = data.get('Tilladelsesnr', '').strip()
        cvrnr = data.get('CvrNr', '').strip()
        tilladelsestype = data.get('TilladelsesType', '').strip()
        enhedspris = data.get('Enhedspris', '').strip()
        meter = data.get('Meter', '').strip()
        startdato = data.get('Startdato', '').strip()
        slutdato = data.get('Slutdato', '').strip()

        # Regex patterns
        vejman_pattern = r"https:\/\/vejman\.vd\.dk\/permissions\/update\.jsp\?caseid=\d+"
        tilladelsesnr_pattern = r"^\d{2}-\d{1,}$"
        cvr_pattern = r"^\d{8}$"

        # Validation checks
        if not re.match(vejman_pattern, vejman_link):
            errors.append("Link til tilladelse skal være direkte til vejman, f.eks. https://vejman.vd.dk/permissions/update.jsp?caseid=12345678.")

        if not re.match(tilladelsesnr_pattern, tilladelsesnr):
            errors.append("Tilladelsesnr skal være på formateret som 24-01234.")

        if not re.match(cvr_pattern, cvrnr):
            errors.append("CVR skal være præcis 8 cifre.")

        try:
            enhedspris = float(enhedspris.replace(',', '.'))
        except ValueError:
            errors.append("Enhedspris skal være et gyldigt tal.")

        try:
            meter = float(meter.replace(',', '.'))
        except ValueError:
            errors.append("Meter skal være et gyldigt tal.")

        try:
            startdato = datetime.strptime(startdato, '%d-%m-%Y')
        except ValueError:
            errors.append("Startdato skal skrives som dag-måned-år i tal, f.eks. 31-12-2024.")

        try:
            slutdato = datetime.strptime(slutdato, '%d-%m-%Y')
        except ValueError:
            errors.append("Slutdato skal skrives som dag-måned-år i tal, f.eks. 31-12-2024.")

        # Extract VejmanID from the provided link
        try:
            query_params = parse_qs(urlparse(vejman_link).query)
            vejmanid = query_params.get('caseid', [None])[0]  # Extract caseid
            if not vejmanid:
                errors.append("Link til tilladelse mangler 'caseid' parameter.")
        except Exception as e:
            errors.append(f"Ugyldigt link til tilladelse: {e}")

        # If errors, return JSON response
        if errors:
            return jsonify(success=False, errors=errors)

        # Insert into the database
        engine = get_connection()
        insert_query = text("""
            INSERT INTO [dbo].[VejmanFakturering] (
                Ansøger, FørsteSted, Tilladelsesnr, CvrNr,
                TilladelsesType, Enhedspris, Meter, Startdato, Slutdato, VejmanID
            )
            VALUES (
                :Ansøger, :FørsteSted, :Tilladelsesnr, :CvrNr,
                :TilladelsesType, :Enhedspris, :Meter, :Startdato, :Slutdato, :VejmanID
            )
        """)

        try:
            with engine.begin() as connection:
                connection.execute(insert_query, {
                    'Ansøger': ansøger,
                    'FørsteSted': første_sted,
                    'Tilladelsesnr': tilladelsesnr,
                    'CvrNr': cvrnr,
                    'TilladelsesType': tilladelsestype,
                    'Enhedspris': enhedspris,
                    'Meter': meter,
                    'Startdato': startdato,
                    'Slutdato': slutdato,
                    'VejmanID': vejmanid
                })
            success_message = f"Faktura for tilladelse {tilladelsesnr} er blevet oprettet, og kan nu opdateres og sendes til fakturering."
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=True, message=success_message)
            else:
                return render_template('add.html', page_title="Add Row", active_page="add", success=success_message)
        except Exception as e:
            error_message = f"Fejl ved oprettelse af faktura: {e}"
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(success=False, errors=[error_message])
            else:
                return render_template('add.html', page_title="Add Row", active_page="add", errors=[error_message])

    # Render the form for GET requests
    return render_template('add.html', page_title="Add Row", active_page="add")

@app.route('/delete', methods=['DELETE'])
def delete():
    data = request.json
    row_id = data.get('ID')

    if not row_id:
        return jsonify(success=False, error="Ingen ID oplyst.")

    update_query = text("UPDATE [dbo].[VejmanFakturering] SET FakturerIkke = 1 WHERE ID = :ID")

    engine = get_connection()
    try:
        with engine.begin() as connection:
            result = connection.execute(update_query, {'ID': row_id})
            if result.rowcount == 0:
                return jsonify(success=False, error="Fakturalinjen blev ikke fundet.")
        return jsonify(success=True)
    except Exception as e:
        print("SQL fejl:", e)
        return jsonify(success=False, error=f"Database fejl: {str(e)}")



if __name__ == '__main__':
    app.run(debug=True)
