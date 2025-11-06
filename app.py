from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, send_from_directory, Response
import os
import psycopg2 # ¡NUEVO! Reemplaza a sqlite3
from psycopg2.extras import RealDictCursor # Para obtener resultados como diccionarios
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from werkzeug.utils import secure_filename 
from azure.storage.blob import BlobServiceClient, ContentSettings # ¡NUEVO! Para subir archivos
from dotenv import load_dotenv # ¡NUEVO! Para cargar contraseñas

# --- Cargar Variables de Entorno ---
load_dotenv() # Carga las variables del archivo .env

# --- Configuración de la Aplicación ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'una_clave_secreta_local_muy_fuerte') 

# --- Configuración de Azure Blob Storage (Documentos) ---
AZURE_CONNECTION_STRING = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
AZURE_CONTAINER_NAME = os.environ.get('AZURE_STORAGE_CONTAINER_NAME')

# Inicializar el cliente de Blob Storage
blob_service_client = None
if AZURE_CONNECTION_STRING:
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
    except ValueError:
        print("Error: La cadena de conexión de Azure Storage no es válida.")
else:
    print("Advertencia: No se ha configurado la cadena de conexión de Azure Storage.")

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

# --- Configuración de Autenticación ---
USUARIO_ADMIN = "72296378H"
PASSWORD_ADMIN = "Marlonmontesmontes18" 
NOMBRE_USUARIO = "MARLON MONTES" 

# --- Función Helper: "Upsert" de Cliente ---
def find_or_create_cliente(cursor, nombre, apellido, dni):
    cliente_id = None
    if dni: 
        # MODIFICADO: Sintaxis de %s para PostgreSQL
        cursor.execute("SELECT id FROM clientes WHERE dni = %s", (dni,))
        existing_client = cursor.fetchone()
        if existing_client:
            cliente_id = existing_client['id']
            cursor.execute("""
                UPDATE clientes SET nombre = %s, apellido = %s
                WHERE id = %s
            """, (nombre, apellido, cliente_id))
    
    if cliente_id is None:
        cursor.execute("""
            INSERT INTO clientes (nombre, apellido, dni) 
            VALUES (%s, %s, %s) RETURNING id
        """, (nombre, apellido, dni))
        cliente_id = cursor.fetchone()['id']
        
    return cliente_id

# --- Configuración de la Base de Datos (MODIFICADA PARA POSTGRESQL) ---

def get_db_connection():
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        # Si estamos en local (debug=True), usamos SQLite como respaldo
        if app.debug:
            print("ADVERTENCIA: Usando base de datos SQLite local (polizas.db)")
            conn = sqlite3.connect('polizas.db')
            conn.row_factory = sqlite3.Row
            return conn
        raise ValueError("No se ha configurado la variable de entorno DATABASE_URL")
    
    # Conexión a PostgreSQL
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    # Esta función ahora solo es para crear el schema, la ejecutaremos manualmente en Azure
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # Tabla 1: Clientes (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100) NOT NULL,
                apellido VARCHAR(100),
                dni VARCHAR(20) UNIQUE
            );
        """)
        
        # Tabla 2: Pólizas Generales (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polizas (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
                numero_poliza VARCHAR(100) NOT NULL,
                fecha_inicio DATE, 
                cuotas_totales INTEGER,
                cuotas_pagadas INTEGER DEFAULT 0,
                tipo_poliza VARCHAR(100),
                estado VARCHAR(50),
                frecuencia_pago VARCHAR(50)
            );
        """)
        
        # Tabla 3: Pólizas Mutua (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polizas_mutua (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
                numero_poliza VARCHAR(100) NOT NULL,
                fecha_inicio DATE, 
                tipo_pago VARCHAR(50)
            );
        """)
        
        # Tabla 4: Recibos (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recibos (
                id SERIAL PRIMARY KEY,
                poliza_id INTEGER NOT NULL,
                cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE, 
                poliza_tabla VARCHAR(50) NOT NULL,
                numero_poliza VARCHAR(100),
                descripcion VARCHAR(255),
                fecha_vencimiento DATE,
                estado VARCHAR(50) DEFAULT 'Pendiente'
            );
        """)

        # Tabla 5: Documentos (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documentos (
                id SERIAL PRIMARY KEY,
                poliza_id INTEGER NOT NULL,
                poliza_tabla VARCHAR(50) NOT NULL, 
                nombre_visible VARCHAR(255),     
                path_archivo VARCHAR(255) NOT NULL, /* Ahora es el nombre del blob */
                fecha_subida DATE NOT NULL
            );
        """)
        
        # Tabla 6: Tareas (Sintaxis PostgreSQL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tareas (
                id SERIAL PRIMARY KEY,
                cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
                descripcion TEXT NOT NULL,
                fecha_limite DATE,
                estado VARCHAR(50) DEFAULT 'Pendiente'
            );
        """)
    
    conn.commit()
    conn.close()

# (Ya no llamamos a init_db() al iniciar la app)

# --- Decorador de Seguridad ---
def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__ 
    return wrapper

# --- Rutas de Autenticación ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == USUARIO_ADMIN and password == PASSWORD_ADMIN:
            session['logged_in'] = True
            session['user_name'] = NOMBRE_USUARIO
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Credenciales incorrectas.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('user_name', None) 
    return redirect(url_for('login'))

# --- Ruta Principal (Dashboard) ---
@app.route('/')
@login_required 
def dashboard():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor: # Usar RealDictCursor
        
        # 1. Cargar Clientes
        cursor.execute('SELECT * FROM clientes ORDER BY nombre')
        clientes = cursor.fetchall()

        # 2. Cargar Pólizas Generales
        cursor.execute("""
            SELECT p.*, c.nombre, c.apellido, c.dni 
            FROM polizas p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            ORDER BY c.nombre
        """)
        polizas = cursor.fetchall()
        cursor.execute("SELECT COUNT(id) FROM polizas WHERE estado = 'En Vigor'")
        total_vigentes = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(id) FROM polizas WHERE estado = 'Anulada'")
        total_anuladas = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(id) FROM polizas")
        total_polizas = cursor.fetchone()['count']
        
        # 3. Cargar Pólizas Mutua
        cursor.execute("""
            SELECT p.*, c.nombre, c.apellido, c.dni,
                   to_char(p.fecha_inicio, 'DD/MM/YYYY') AS fecha_inicio_formateada
            FROM polizas_mutua p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            ORDER BY c.nombre
        """)
        polizas_mutua = cursor.fetchall()
        cursor.execute("SELECT COUNT(id) FROM polizas_mutua")
        total_mutuas = cursor.fetchone()['count']

        # 4. Cargar Recibos Pendientes
        cursor.execute("""
            SELECT r.*, c.nombre, c.apellido,
                   to_char(r.fecha_vencimiento, 'DD/MM/YYYY') AS fecha_vencimiento_formateada
            FROM recibos r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            WHERE r.estado = 'Pendiente' 
              AND r.fecha_vencimiento <= (CURRENT_DATE + INTERVAL '3 days')
            ORDER BY r.fecha_vencimiento ASC
        """)
        recibos_pendientes = cursor.fetchall()
        recibos_pendientes_count = len(recibos_pendientes)

        # 5. Cargar Tareas
        cursor.execute("""
            SELECT t.*, c.nombre, c.apellido,
                   to_char(t.fecha_limite, 'DD/MM/YYYY') AS fecha_limite_formateada
            FROM tareas t
            LEFT JOIN clientes c ON t.cliente_id = c.id
            WHERE t.estado = 'Pendiente'
            ORDER BY t.fecha_limite ASC
        """)
        tareas_pendientes = cursor.fetchall()
        
        cursor.execute("""
            SELECT t.*, c.nombre, c.apellido,
                   to_char(t.fecha_limite, 'DD/MM/YYYY') AS fecha_limite_formateada
            FROM tareas t
            LEFT JOIN clientes c ON t.cliente_id = c.id
            WHERE t.estado = 'Completada'
            ORDER BY t.fecha_limite DESC
            LIMIT 5
        """)
        tareas_completadas = cursor.fetchall()

    conn.close()
    
    columnas_general = ['ID', 'N° Póliza', 'Nombre', 'Apellido', 'DNI', 'Tipo Póliza', 'Estado', 'Acciones']
    columnas_mutua = ['ID', 'N° Póliza', 'Nombre', 'Apellido', 'DNI', 'Fecha Inicio', 'Tipo Pago', 'Acciones']
    columnas_clientes = ['ID', 'Nombre', 'Apellido', 'DNI', 'Acciones']
    
    user_name = session.get('user_name', 'Usuario') 
    
    return render_template('dashboard.html', 
                           clientes=clientes, 
                           polizas=polizas, 
                           columnas=columnas_general,
                           total_vigentes=total_vigentes,
                           total_anuladas=total_anuladas,
                           total_polizas=total_polizas,
                           polizas_mutua=polizas_mutua, 
                           columnas_mutua=columnas_mutua, 
                           total_mutuas=total_mutuas, 
                           recibos_pendientes=recibos_pendientes, 
                           recibos_pendientes_count=recibos_pendientes_count, 
                           columnas_clientes=columnas_clientes,
                           tareas_pendientes=tareas_pendientes, 
                           tareas_completadas=tareas_completadas,
                           user_name=user_name)

# --- RUTAS DE CLIENTES ---

@app.route('/details_cliente/<int:cliente_id>')
@login_required
def get_cliente_details(cliente_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT id, nombre, apellido, dni FROM clientes WHERE id = %s', (cliente_id,))
        cliente = cursor.fetchone()
    conn.close()
    if cliente is None: abort(404)
    return jsonify(cliente)

@app.route('/details_cliente_completo/<int:cliente_id>')
@login_required
def get_cliente_completo(cliente_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT * FROM clientes WHERE id = %s', (cliente_id,))
        cliente = cursor.fetchone()
        
        if cliente is None:
            conn.close()
            abort(404)
        
        cursor.execute("""
            SELECT *, to_char(fecha_inicio, 'DD/MM/YYYY') AS fecha_inicio_formateada
            FROM polizas WHERE cliente_id = %s
        """, (cliente_id,))
        polizas_generales = cursor.fetchall()
        
        cursor.execute("""
            SELECT *, to_char(fecha_inicio, 'DD/MM/YYYY') AS fecha_inicio_formateada
            FROM polizas_mutua WHERE cliente_id = %s
        """, (cliente_id,))
        polizas_mutua = cursor.fetchall()
    
    conn.close()

    return jsonify({
        'cliente': cliente,
        'polizas_generales': polizas_generales,
        'polizas_mutua': polizas_mutua
    })

@app.route('/delete_cliente/<int:cliente_id>', methods=['POST'])
@login_required
def delete_cliente(cliente_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('DELETE FROM clientes WHERE id = %s', (cliente_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/edit_cliente/<int:cliente_id>', methods=['POST'])
@login_required
def edit_cliente(cliente_id):
    nombre = request.form.get('edit_cliente_nombre')
    apellido = request.form.get('edit_cliente_apellido')
    dni = request.form.get('edit_cliente_dni')
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE clientes 
                SET nombre = %s, apellido = %s, dni = %s
                WHERE id = %s
            """, (nombre, apellido, dni, cliente_id))
        conn.commit()
    except Exception as e:
        print(f"Error al editar cliente: {e}")
    finally:
        conn.close()
    
    return redirect(url_for('dashboard'))


# --- Rutas Pólizas Generales ---

@app.route('/add_poliza_general', methods=['POST'])
@login_required
def add_poliza_general():
    nombre = request.form.get('nombre')
    apellido = request.form.get('apellido')
    dni = request.form.get('dni')
    numero_poliza = request.form.get('numero_poliza')
    tipo_poliza = request.form.get('tipo_poliza')
    estado = request.form.get('estado')
    fecha_inicio = request.form.get('fecha_inicio') if request.form.get('fecha_inicio') else None
    frecuencia_pago = request.form.get('frecuencia_pago') 
    
    cuotas_totales = 0
    delta = None
    if frecuencia_pago == 'Mensual':
        cuotas_totales = 12
        delta = relativedelta(months=1)
    elif frecuencia_pago == 'Trimestral':
        cuotas_totales = 4
        delta = relativedelta(months=3)
    elif frecuencia_pago == 'Semestral':
        cuotas_totales = 2
        delta = relativedelta(months=6)
    elif frecuencia_pago == 'Anual':
        cuotas_totales = 1
        delta = relativedelta(years=1)
    
    cuotas_pagadas_inicial = 1 if cuotas_totales > 0 else 0
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cliente_id = find_or_create_cliente(cursor, nombre, apellido, dni)

        cursor.execute("""
            INSERT INTO polizas 
            (cliente_id, numero_poliza, fecha_inicio, cuotas_totales, cuotas_pagadas, tipo_poliza, estado, frecuencia_pago) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (cliente_id, numero_poliza, fecha_inicio, cuotas_totales, cuotas_pagadas_inicial, tipo_poliza, estado, frecuencia_pago))
        
        poliza_id = cursor.fetchone()['id']
        
        if fecha_inicio and delta:
            try:
                fecha_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
                nombre_cliente_completo = f"{nombre} {apellido}"
                
                for i in range(cuotas_totales):
                    fecha_vencimiento = fecha_dt + (delta * i)
                    descripcion = f"Cuota {i+1}/{cuotas_totales} ({frecuencia_pago})"
                    estado_recibo = 'Pagado' if i == 0 else 'Pendiente'
                    
                    cursor.execute("""
                        INSERT INTO recibos (poliza_id, cliente_id, poliza_tabla, numero_poliza, descripcion, fecha_vencimiento, estado)
                        VALUES (%s, %s, 'polizas', %s, %s, %s, %s)
                    """, (poliza_id, cliente_id, numero_poliza, descripcion, fecha_vencimiento.strftime('%Y-%m-%d'), estado_recibo))
            except Exception as e:
                print(f"Error generando recibos generales: {e}")
            
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/delete/<int:poliza_id>', methods=['POST'])
@login_required
def delete_poliza(poliza_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('DELETE FROM polizas WHERE id = %s', (poliza_id,))
        cursor.execute('DELETE FROM recibos WHERE poliza_id = %s AND poliza_tabla = "polizas"', (poliza_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/details/<int:poliza_id>')
@login_required
def get_poliza_details(poliza_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT p.*, c.nombre, c.apellido, c.dni,
                   to_char(p.fecha_inicio, 'DD/MM/YYYY') AS fecha_inicio_formateada 
            FROM polizas p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            WHERE p.id = %s
        """, (poliza_id,))
        poliza = cursor.fetchone()
    conn.close()
    if poliza is None: abort(404)
    
    if not poliza['fecha_inicio_formateada'] and poliza['fecha_inicio']:
        poliza['fecha_inicio_formateada'] = poliza['fecha_inicio'].strftime('%d/%m/%Y')
        
    cuotas_t = poliza.get('cuotas_totales') or 0
    cuotas_p = poliza.get('cuotas_pagadas') or 0
    try:
        poliza['cuotas_restantes'] = int(cuotas_t) - int(cuotas_p)
    except:
         poliza['cuotas_restantes'] = 0
    return jsonify(poliza)

@app.route('/edit_poliza_general/<int:poliza_id>', methods=['POST'])
@login_required
def edit_poliza_general(poliza_id):
    numero_poliza = request.form.get('edit_poliza_numero')
    tipo_poliza = request.form.get('edit_poliza_tipo')
    estado = request.form.get('edit_poliza_estado')
    fecha_inicio = request.form.get('edit_poliza_fecha_inicio')
    frecuencia_pago = request.form.get('edit_poliza_frecuencia')
    
    cuotas_totales = 0
    if frecuencia_pago == 'Mensual': cuotas_totales = 12
    elif frecuencia_pago == 'Trimestral': cuotas_totales = 4
    elif frecuencia_pago == 'Semestral': cuotas_totales = 2
    elif frecuencia_pago == 'Anual': cuotas_totales = 1
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE polizas 
            SET numero_poliza = %s, tipo_poliza = %s, estado = %s, fecha_inicio = %s, cuotas_totales = %s, frecuencia_pago = %s
            WHERE id = %s
        """, (numero_poliza, tipo_poliza, estado, fecha_inicio, cuotas_totales, frecuencia_pago, poliza_id))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


# --- RUTAS PÓLIZAS MUTUA ---

@app.route('/add_mutua', methods=['POST'])
@login_required
def add_mutua_poliza():
    nombre = request.form.get('nombre')
    apellido = request.form.get('apellido')
    dni = request.form.get('dni')
    numero_poliza = request.form.get('numero_poliza')
    fecha_inicio = request.form.get('fecha_inicio') if request.form.get('fecha_inicio') else None
    tipo_pago = request.form.get('tipo_pago') 
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cliente_id = find_or_create_cliente(cursor, nombre, apellido, dni)

        cursor.execute("""
            INSERT INTO polizas_mutua
            (cliente_id, numero_poliza, fecha_inicio, tipo_pago) 
            VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (cliente_id, numero_poliza, fecha_inicio, tipo_pago))
        
        poliza_id = cursor.fetchone()['id']
        nombre_cliente_completo = f"{nombre} {apellido}"

        if fecha_inicio and tipo_pago:
            try:
                fecha_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
                
                if tipo_pago == 'Pactada':
                    fechas_desc_estado = [
                        (fecha_dt, "Cuota Pactada 1/3", "Pagado"),
                        (fecha_dt + timedelta(days=45), "Cuota Pactada 2/3", "Pendiente"),
                        (fecha_dt + timedelta(days=90), "Cuota Pactada 3/3", "Pendiente")
                    ]
                    for fecha, desc, estado in fechas_desc_estado:
                        cursor.execute("""
                            INSERT INTO recibos (poliza_id, cliente_id, poliza_tabla, numero_poliza, descripcion, fecha_vencimiento, estado)
                            VALUES (%s, %s, 'polizas_mutua', %s, %s, %s, %s)
                        """, (poliza_id, cliente_id, numero_poliza, desc, fecha.strftime('%Y-%m-%d'), estado))
                
                elif tipo_pago == 'Anual':
                    cursor.execute("""
                        INSERT INTO recibos (poliza_id, cliente_id, poliza_tabla, numero_poliza, descripcion, fecha_vencimiento, estado)
                        VALUES (%s, %s, 'polizas_mutua', %s, %s, %s, 'Pagado')
                    """, (poliza_id, cliente_id, numero_poliza, "Pago Anual (Año 1)", fecha_dt.strftime('%Y-%m-%d')))
                    fecha_vencimiento_proxima = fecha_dt + relativedelta(years=1)
                    cursor.execute("""
                        INSERT INTO recibos (poliza_id, cliente_id, poliza_tabla, numero_poliza, descripcion, fecha_vencimiento, estado)
                        VALUES (%s, %s, 'polizas_mutua', %s, %s, %s, 'Pendiente')
                    """, (poliza_id, cliente_id, numero_poliza, "Pago Anual (Año 2 - Renovación)", fecha_vencimiento_proxima.strftime('%Y-%m-%d')))

            except Exception as e:
                print(f"Error generando recibos de mutua: {e}")

    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/delete_mutua/<int:poliza_id>', methods=['POST'])
@login_required
def delete_mutua_poliza(poliza_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute('DELETE FROM polizas_mutua WHERE id = %s', (poliza_id,))
        cursor.execute('DELETE FROM recibos WHERE poliza_id = %s AND poliza_tabla = "polizas_mutua"', (poliza_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/details_mutua/<int:poliza_id>')
@login_required
def get_mutua_details(poliza_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT p.*, c.nombre, c.apellido, c.dni
            FROM polizas_mutua p
            LEFT JOIN clientes c ON p.cliente_id = c.id
            WHERE p.id = %s
        """, (poliza_id,))
        poliza = cursor.fetchone()
        
        if poliza is None:
            conn.close()
            abort(404)

        details = poliza
        
        details['cuota_1_fecha'] = "N/A"
        details['cuota_1_status'] = "N/A"
        details['cuota_2_fecha'] = "N/A"
        details['cuota_2_status'] = "N/A"
        details['cuota_3_fecha'] = "N/A"
        details['cuota_3_status'] = "N/A"
        details['fecha_pago_anual'] = "N/A"
        
        original_fecha_inicio = details['fecha_inicio'] 

        if original_fecha_inicio:
            try:
                fecha_inicio_dt = original_fecha_inicio
                details['fecha_inicio'] = fecha_inicio_dt.strftime('%d/%m/%Y')
                
                if details['tipo_pago'] == 'Pactada':
                    cursor.execute("""
                        SELECT descripcion, estado, to_char(fecha_vencimiento, 'DD/MM/YYYY') AS fecha_vencimiento_formateada
                        FROM recibos
                        WHERE poliza_id = %s AND poliza_tabla = 'polizas_mutua'
                        ORDER BY fecha_vencimiento ASC
                    """, (poliza_id,))
                    recibos = cursor.fetchall()
                    
                    for r in recibos:
                        if "1/3" in r['descripcion']:
                            details['cuota_1_fecha'] = r['fecha_vencimiento_formateada']
                            details['cuota_1_status'] = r['estado']
                        elif "2/3" in r['descripcion']:
                            details['cuota_2_fecha'] = r['fecha_vencimiento_formateada']
                            details['cuota_2_status'] = r['estado']
                        elif "3/3" in r['descripcion']:
                            details['cuota_3_fecha'] = r['fecha_vencimiento_formateada']
                            details['cuota_3_status'] = r['estado']
                
                elif details['tipo_pago'] == 'Anual':
                    cursor.execute("""
                        SELECT to_char(fecha_vencimiento, 'DD/MM/YYYY') AS fecha_vencimiento_formateada 
                        FROM recibos 
                        WHERE poliza_id = %s AND poliza_tabla = 'polizas_mutua' AND estado = 'Pendiente'
                    """, (poliza_id,))
                    recibo_anual = cursor.fetchone()
                    
                    if recibo_anual:
                        details['fecha_pago_anual'] = recibo_anual['fecha_vencimiento_formateada']
                    else:
                        details['fecha_pago_anual'] = "Todos los recibos pagados"

            except Exception as e:
                print(f"Error al formatear la fecha de la mutua: {e}")
                details['fecha_inicio'] = original_fecha_inicio.strftime('%Y-%m-%d')
                
    conn.close()
    return jsonify(details)

@app.route('/edit_mutua_poliza/<int:poliza_id>', methods=['POST'])
@login_required
def edit_mutua_poliza(poliza_id):
    numero_poliza = request.form.get('edit_mutua_numero')
    fecha_inicio = request.form.get('edit_mutua_fecha_inicio')
    tipo_pago = request.form.get('edit_mutua_tipo_pago')
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE polizas_mutua 
            SET numero_poliza = %s, fecha_inicio = %s, tipo_pago = %s
            WHERE id = %s
        """, (numero_poliza, fecha_inicio, tipo_pago, poliza_id))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


# --- RUTA PARA PAGAR RECIBOS ---

@app.route('/pagar_recibo/<int:recibo_id>', methods=['POST'])
@login_required
def pagar_recibo(recibo_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        
        cursor.execute('SELECT * FROM recibos WHERE id = %s', (recibo_id,))
        recibo = cursor.fetchone()
        if not recibo:
            conn.close()
            return redirect(url_for('dashboard'))

        cursor.execute("UPDATE recibos SET estado = 'Pagado' WHERE id = %s", (recibo_id,))
        
        if recibo['poliza_tabla'] == 'polizas':
            cursor.execute("UPDATE polizas SET cuotas_pagadas = cuotas_pagadas + 1 WHERE id = %s", (recibo['poliza_id'],))
        
        elif recibo['poliza_tabla'] == 'polizas_mutua':
            cursor.execute('SELECT * FROM polizas_mutua WHERE id = %s', (recibo['poliza_id'],))
            poliza_mutua = cursor.fetchone()
            
            if poliza_mutua and poliza_mutua['tipo_pago'] == 'Anual':
                try:
                    fecha_vencimiento_actual = recibo['fecha_vencimiento']
                    fecha_vencimiento_proxima = fecha_vencimiento_actual + relativedelta(years=1)
                    
                    cursor.execute('SELECT * FROM clientes WHERE id = %s', (recibo['cliente_id'],))
                    cliente = cursor.fetchone()
                    nombre_cliente = f"{cliente['nombre']} {cliente['apellido']}"

                    cursor.execute("""
                        INSERT INTO recibos (poliza_id, cliente_id, poliza_tabla, numero_poliza, descripcion, fecha_vencimiento, estado)
                        VALUES (%s, %s, 'polizas_mutua', %s, %s, %s, 'Pendiente')
                    """, (recibo['poliza_id'], recibo['cliente_id'], recibo['numero_poliza'], f"Pago Anual (Renovación)", fecha_vencimiento_proxima.strftime('%Y-%m-%d')))
                
                except Exception as e:
                    print(f"Error generando recibo anual futuro: {e}")

    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


# --- RUTAS DE GESTIÓN DE DOCUMENTOS (MODIFICADAS PARA AZURE BLOB) ---

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload_documento/<string:poliza_tabla>/<int:poliza_id>', methods=['POST'])
@login_required
def upload_documento(poliza_tabla, poliza_id):
    if 'file' not in request.files:
        return redirect(url_for('dashboard'))
    
    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('dashboard'))

    if file and allowed_file(file.filename) and blob_service_client:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename_base = secure_filename(file.filename)
        filename_seguro = f"{timestamp}_{poliza_tabla}_{poliza_id}_{filename_base}"
        
        content_type = file.content_type
        
        try:
            blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=filename_seguro)
            content_settings = ContentSettings(content_type=content_type)
            blob_client.upload_blob(file.stream, content_settings=content_settings)
            
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO documentos (poliza_id, poliza_tabla, nombre_visible, path_archivo, fecha_subida)
                    VALUES (%s, %s, %s, %s, %s)
                """, (poliza_id, poliza_tabla, file.filename, filename_seguro, datetime.now().strftime('%Y-%m-%d')))
            conn.commit()
            conn.close()
        
        except Exception as e:
            print(f"Error al subir a Azure Blob: {e}")

    return redirect(url_for('dashboard'))

@app.route('/get_documentos/<string:poliza_tabla>/<int:poliza_id>')
@login_required
def get_documentos(poliza_tabla, poliza_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, nombre_visible, to_char(fecha_subida, 'DD/MM/YYYY') AS fecha_subida_formateada 
            FROM documentos 
            WHERE poliza_id = %s AND poliza_tabla = %s
        """, (poliza_id, poliza_tabla))
        documentos = cursor.fetchall()
    conn.close()
    
    return jsonify(documentos)

@app.route('/download_documento/<int:doc_id>')
@login_required
def download_documento(doc_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT * FROM documentos WHERE id = %s', (doc_id,))
        documento = cursor.fetchone()
    conn.close()
    
    if documento and blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=documento['path_archivo'])
            stream = blob_client.download_blob().readall()
            
            return Response(
                stream,
                mimetype=blob_client.get_blob_properties().content_settings.content_type,
                headers={"Content-Disposition": f"attachment;filename=\"{documento['nombre_visible']}\""}
            )
        except Exception as e:
            print(f"Error al descargar de Azure Blob: {e}")
            abort(404)
    
    abort(404)

@app.route('/delete_documento/<int:doc_id>', methods=['POST'])
@login_required
def delete_documento(doc_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('SELECT * FROM documentos WHERE id = %s', (doc_id,))
        documento = cursor.fetchone()
        
        if documento and blob_service_client:
            try:
                blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=documento['path_archivo'])
                blob_client.delete_blob()
            except Exception as e:
                print(f"Error borrando blob: {e}")
            
            cursor.execute('DELETE FROM documentos WHERE id = %s', (doc_id,))
            conn.commit()
        
    conn.close()
    return jsonify({'success': True})


# --- RUTAS DE TAREAS ---

@app.route('/add_tarea', methods=['POST'])
@login_required
def add_tarea():
    descripcion = request.form.get('descripcion')
    fecha_limite = request.form.get('fecha_limite')
    cliente_id = request.form.get('cliente_id') 

    if not descripcion or not fecha_limite:
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO tareas (cliente_id, descripcion, fecha_limite, estado) 
            VALUES (%s, %s, %s, 'Pendiente')
        """, (cliente_id if cliente_id else None, descripcion, fecha_limite))
    conn.commit()
    conn.close()
    
    return redirect(url_for('dashboard')) 

@app.route('/complete_tarea/<int:tarea_id>', methods=['POST'])
@login_required
def complete_tarea(tarea_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE tareas SET estado = 'Completada' WHERE id = %s", (tarea_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/delete_tarea/<int:tarea_id>', methods=['POST'])
@login_required
def delete_tarea(tarea_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM tareas WHERE id = %s", (tarea_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))


if __name__ == '__main__':
    # Esto es solo para pruebas locales
    # Para crear la base de datos localmente, ejecuta:
    # 1. Abre la terminal de python: python
    # 2. from app import init_db
    # 3. init_db()
    # 4. exit()
    # 5. python app.py
    
    # Asegurarse de que la DB local exista
    if not os.path.exists('polizas.db'):
        print("Creando base de datos SQLite local...")
        init_db()
        
    app.run(debug=True, host='0.0.0.0')