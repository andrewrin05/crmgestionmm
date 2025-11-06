# CRM Gestión MM

Sistema de gestión de pólizas de seguros construido con Flask y PostgreSQL.

## Configuración de Seguridad

Este proyecto implementa las siguientes medidas de seguridad:

### Archivos Sensibles Protegidos

Los siguientes archivos **NO** están versionados en Git por razones de seguridad:

- **`.env`**: Contiene variables de entorno sensibles (credenciales, URLs de conexión)
- **`*.db`**: Archivos de base de datos que pueden contener información personal
- **`uploads/`**: Directorio con documentos subidos por usuarios
- **`__pycache__/`**: Archivos de caché de Python

### Configuración Inicial

1. **Copia el archivo de ejemplo de variables de entorno:**
   ```bash
   cp .env.example .env
   ```

2. **Edita `.env` con tus credenciales reales:**
   ```bash
   DATABASE_URL="postgresql://usuario:contraseña@host:puerto/nombre_db"
   AZURE_STORAGE_CONNECTION_STRING="tu_cadena_de_conexión"
   FLASK_SECRET_KEY="tu_clave_secreta_fuerte"
   ```

3. **Instala las dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Ejecuta la aplicación:**
   ```bash
   python app.py
   ```

## Variables de Entorno

| Variable | Descripción | Requerida |
|----------|-------------|-----------|
| `DATABASE_URL` | URL de conexión a PostgreSQL | Sí |
| `AZURE_STORAGE_CONNECTION_STRING` | Cadena de conexión a Azure Storage | No |
| `AZURE_STORAGE_CONTAINER_NAME` | Nombre del contenedor de Azure | No |
| `FLASK_SECRET_KEY` | Clave secreta para sesiones | No (se genera automáticamente) |
| `RENDER_DISK_PATH` | Ruta del disco de Render para uploads | No (solo en producción) |

## Mejores Prácticas de Seguridad

### ✅ Implementado

- Archivo `.gitignore` completo que protege archivos sensibles
- Variables de entorno usando `.env` (no versionado)
- Archivo `.env.example` como plantilla para desarrolladores
- Archivos de base de datos excluidos del control de versiones

### ⚠️ Recomendaciones Adicionales

Para mejorar aún más la seguridad de la aplicación:

1. **Mover credenciales hardcodeadas a variables de entorno:**
   - `USUARIO_ADMIN` y `PASSWORD_ADMIN` deben estar en `.env`
   - Usar hash para las contraseñas (ej: bcrypt)

2. **Implementar autenticación más robusta:**
   - Usar Flask-Login o Flask-Security
   - Implementar tokens CSRF

3. **Base de datos:**
   - Usar migraciones (Flask-Migrate)
   - Backups regulares automatizados

## Estructura del Proyecto

```
crmgestionmm/
├── app.py                 # Aplicación Flask principal
├── requirements.txt       # Dependencias de Python
├── .env                   # Variables de entorno (NO versionado)
├── .env.example          # Plantilla de variables de entorno
├── .gitignore            # Archivos ignorados por Git
├── templates/            # Plantillas HTML
│   ├── dashboard.html
│   └── login.html
├── static/              # Archivos estáticos
│   └── logo.png
└── uploads/             # Documentos subidos (NO versionado)
```

## Despliegue en Producción

Este proyecto está configurado para desplegarse en Render:

1. Configurar las variables de entorno en el panel de Render
2. Conectar el disco persistente de Render para `uploads/`
3. Asegurar que `DATABASE_URL` apunte a la base de datos PostgreSQL de Render

## Contribuir

Al contribuir a este proyecto:

1. **NUNCA** commits archivos `.env` o bases de datos
2. Actualiza `.env.example` si añades nuevas variables de entorno
3. Documenta cualquier cambio de seguridad en este README

## Soporte

Para problemas o preguntas, abre un issue en el repositorio.
