# Deploy en Render + Supabase

## 1. Variables en Render
Configura estas variables en tu servicio web:

- `FLASK_CONFIG=production`
- `SECRET_KEY=<tu-secret>`
- `SECURITY_PASSWORD_SALT=<tu-salt>`
- `DATABASE_URL=<connection string de Supabase>`
- `APP_BASE_URL=<url publica de Render>`
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`
- `TALLY_FORM_ID`, `TALLY_API_KEY`, `TALLY_WEBHOOK_SECRET`, `TALLY_SUBMISSIONS_SHEET_URL`
- `ENABLE_STUDENT_DATA_SHADOW_SYNC=false`
- `ENABLE_FORM_RESPONSE_STORAGE=false`
- `DROP_DEPRECATED_TABLES=false` (activar en `true` solo cuando quieras eliminar tablas legacy)

## 2. Cadena de conexion Supabase
Usa la URL de Postgres de Supabase en `DATABASE_URL`.

Ejemplo:

`postgresql+psycopg2://USER:PASSWORD@HOST:5432/postgres?sslmode=require`

Si Render te entrega `postgres://...`, la app ya la transforma automaticamente a `postgresql://...`.

## 3. Build/Start
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn run:app --workers 2 --threads 4 --timeout 120`

## 4. Migracion de datos (local -> Supabase)
Flujo recomendado:

1. Respaldar base local completa.
2. Exportar datos de tablas usadas (`users`, `student_profiles`, `teacher_profiles`, `evaluaciones`, `calificaciones`, `academic_scores`, `student_interests`, etc.).
3. Levantar app contra Supabase en un entorno de prueba para que cree/ajuste esquema automaticamente.
4. Importar datos.
5. Validar login, panel admin, resultados y eliminacion de usuarios.

## 5. Activacion final
Cuando valides datos y funcionamiento:

1. Cambia `DROP_DEPRECATED_TABLES=true` para limpiar tablas obsoletas.
2. Reinicia el servicio.
3. Vuelve a `DROP_DEPRECATED_TABLES=false` para evitar drops accidentales en futuros reinicios.
