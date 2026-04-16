import os

from flask import Flask, flash, redirect, request, session, url_for
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from sqlalchemy import inspect, text

from .config import config

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def _migrate_legacy_sqlserver_users_table() -> None:
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    has_legacy_shape = {"user_id", "first_name", "last_name", "password_hash"}.issubset(existing_columns)
    has_new_shape = "id" in existing_columns
    if has_new_shape or not has_legacy_shape:
        return

    db.session.execute(
        text(
            """
            IF OBJECT_ID('dbo.users_legacy', 'U') IS NULL
            BEGIN
                EXEC sp_rename 'dbo.users', 'users_legacy';
            END
            """
        )
    )

    db.session.execute(
        text(
            """
            IF OBJECT_ID('dbo.users', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.users (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    nombre NVARCHAR(100) NOT NULL,
                    apellido NVARCHAR(100) NOT NULL,
                    email NVARCHAR(120) NOT NULL UNIQUE,
                    [password] NVARCHAR(200) NOT NULL,
                    role NVARCHAR(20) NOT NULL CONSTRAINT DF_users_role DEFAULT ('estudiante'),
                    fecha_creacion DATETIME2 NOT NULL CONSTRAINT DF_users_fecha_creacion DEFAULT (GETDATE()),
                    email_verificado BIT NOT NULL CONSTRAINT DF_users_email_verificado DEFAULT (0),
                    email_verificado_at DATETIME2 NULL,
                    ultimo_login DATETIME2 NULL,
                    activo BIT NOT NULL CONSTRAINT DF_users_activo DEFAULT (1),
                    reset_requested_at DATETIME2 NULL,
                    profesor_id INT NULL,
                    seccion NVARCHAR(50) NULL,
                    CONSTRAINT FK_users_profesor FOREIGN KEY (profesor_id) REFERENCES dbo.users(id)
                );
            END
            """
        )
    )

    db.session.execute(
        text(
            """
            IF OBJECT_ID('dbo.users_legacy', 'U') IS NOT NULL
            BEGIN
                INSERT INTO dbo.users (
                    nombre, apellido, email, [password], role, fecha_creacion,
                    email_verificado, email_verificado_at, ultimo_login, activo
                )
                SELECT
                    ISNULL(NULLIF(LTRIM(RTRIM(first_name)), ''), 'SinNombre'),
                    ISNULL(NULLIF(LTRIM(RTRIM(last_name)), ''), 'SinApellido'),
                    LTRIM(RTRIM(email)),
                    password_hash,
                    CASE WHEN role IN ('estudiante', 'profesor', 'admin') THEN role ELSE 'estudiante' END,
                    ISNULL(created_at, GETDATE()),
                    1,
                    ISNULL(created_at, GETDATE()),
                    last_login,
                    1
                FROM dbo.users_legacy legacy
                WHERE NOT EXISTS (
                    SELECT 1 FROM dbo.users u WHERE u.email = LTRIM(RTRIM(legacy.email))
                );
            END
            """
        )
    )

    db.session.commit()


def _ensure_user_columns() -> None:
    inspector = inspect(db.engine)
    dialect_name = db.engine.dialect.name

    if dialect_name == "mssql":
        _migrate_legacy_sqlserver_users_table()
        inspector = inspect(db.engine)

    if "users" not in inspector.get_table_names():
        return

    bool_type = "BIT" if dialect_name == "mssql" else "BOOLEAN"
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    text_type = "NVARCHAR(50)" if dialect_name == "mssql" else "VARCHAR(50)"
    add_column_prefix = "ALTER TABLE users ADD" if dialect_name == "mssql" else "ALTER TABLE users ADD COLUMN"

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    statements = []

    if "email_verificado" not in existing_columns:
        statements.append(f"{add_column_prefix} email_verificado {bool_type} NOT NULL DEFAULT 0")
    if "email_verificado_at" not in existing_columns:
        statements.append(f"{add_column_prefix} email_verificado_at {datetime_type}")
    if "ultimo_login" not in existing_columns:
        statements.append(f"{add_column_prefix} ultimo_login {datetime_type}")
    if "activo" not in existing_columns:
        statements.append(f"{add_column_prefix} activo {bool_type} NOT NULL DEFAULT 1")
    if "reset_requested_at" not in existing_columns:
        statements.append(f"{add_column_prefix} reset_requested_at {datetime_type}")
    if "profesor_id" not in existing_columns:
        statements.append(f"{add_column_prefix} profesor_id {int_type}")
    if "seccion" not in existing_columns:
        statements.append(f"{add_column_prefix} seccion {text_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    # Asegura FK de profesor_id en SQL Server si no existe.
    if dialect_name == "mssql":
        db.session.execute(
            text(
                """
                IF COL_LENGTH('dbo.users', 'profesor_id') IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM sys.foreign_keys
                    WHERE name = 'FK_users_profesor'
                )
                BEGIN
                    ALTER TABLE dbo.users
                    ADD CONSTRAINT FK_users_profesor
                    FOREIGN KEY (profesor_id) REFERENCES dbo.users(id);
                END
                """
            )
        )

    if statements:
        db.session.commit()
    elif dialect_name == "mssql":
        db.session.commit()


def _ensure_evaluacion_columns() -> None:
    inspector = inspect(db.engine)
    if "evaluaciones" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    float_type = "FLOAT" if dialect_name == "mssql" else "FLOAT"
    bool_type = "BIT" if dialect_name == "mssql" else "BOOLEAN"
    text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    short_text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    id_text_type = "NVARCHAR(24)" if dialect_name == "mssql" else "VARCHAR(24)"
    add_column_prefix = (
        "ALTER TABLE evaluaciones ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE evaluaciones ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("evaluaciones")}
    statements = []

    if "comentario_profesor" not in existing_columns:
        statements.append(f"{add_column_prefix} comentario_profesor {text_type}")
    if "comentario_profesor_at" not in existing_columns:
        statements.append(f"{add_column_prefix} comentario_profesor_at {datetime_type}")
    if "profesor_comentario_id" not in existing_columns:
        statements.append(f"{add_column_prefix} profesor_comentario_id {int_type}")
    if "form_id" not in existing_columns:
        statements.append(f"{add_column_prefix} form_id {short_text_type}")
    if "respondent_id" not in existing_columns:
        statements.append(f"{add_column_prefix} respondent_id {short_text_type}")
    if "matricula_estudiante" not in existing_columns:
        statements.append(f"{add_column_prefix} matricula_estudiante {id_text_type}")
    if "submitted_at" not in existing_columns:
        statements.append(f"{add_column_prefix} submitted_at {datetime_type}")
    if "logical_reasoning_score" not in existing_columns:
        statements.append(f"{add_column_prefix} logical_reasoning_score {float_type}")
    if "problem_resolution_score" not in existing_columns:
        statements.append(f"{add_column_prefix} problem_resolution_score {float_type}")
    if "detail_attention_score" not in existing_columns:
        statements.append(f"{add_column_prefix} detail_attention_score {float_type}")
    if "creativity_score" not in existing_columns:
        statements.append(f"{add_column_prefix} creativity_score {float_type}")
    if "tech_ability_score" not in existing_columns:
        statements.append(f"{add_column_prefix} tech_ability_score {float_type}")
    if "average_score" not in existing_columns:
        statements.append(f"{add_column_prefix} average_score {float_type}")
    if "results_released" not in existing_columns:
        if dialect_name == "mssql":
            statements.append(
                "ALTER TABLE evaluaciones "
                "ADD results_released BIT NOT NULL "
                "CONSTRAINT DF_evaluaciones_results_released DEFAULT 0"
            )
        else:
            statements.append(
                f"{add_column_prefix} results_released {bool_type} NOT NULL DEFAULT FALSE"
            )

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()

    if dialect_name == "mssql":
        db.session.execute(
            text(
                """
                IF COL_LENGTH('dbo.evaluaciones', 'matricula_estudiante') IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE name = 'IX_evaluaciones_matricula_estudiante'
                      AND object_id = OBJECT_ID('dbo.evaluaciones')
                )
                BEGIN
                    CREATE INDEX IX_evaluaciones_matricula_estudiante
                    ON dbo.evaluaciones(matricula_estudiante);
                END
                """
            )
        )
        db.session.commit()


def _ensure_root_admin_account(app: Flask) -> None:
    root_email = (app.config.get("ROOT_ADMIN_EMAIL") or "").strip().lower()
    if not root_email:
        return

    from datetime import datetime
    import secrets

    from werkzeug.security import generate_password_hash

    from .models.user import User

    root_first_name = (app.config.get("ROOT_ADMIN_FIRST_NAME") or "Admin").strip() or "Admin"
    root_last_name = (app.config.get("ROOT_ADMIN_LAST_NAME") or "Root").strip() or "Root"
    configured_password = (app.config.get("ROOT_ADMIN_PASSWORD") or "").strip()

    user = User.query.filter(db.func.lower(User.email) == root_email).first()
    if user:
        changed = False
        if user.role != "admin":
            user.role = "admin"
            changed = True
        if not user.activo:
            user.activo = True
            changed = True
        if not user.email_verificado:
            user.email_verificado = True
            user.email_verificado_at = user.email_verificado_at or datetime.utcnow()
            changed = True
        if not user.password:
            generated_password = configured_password or secrets.token_urlsafe(18)
            user.password = generate_password_hash(generated_password)
            changed = True
            if not configured_password:
                app.logger.warning(
                    "ROOT_ADMIN_PASSWORD no configurado; se genero una clave aleatoria para %s. "
                    "Usa 'Olvide mi contrasena' para acceso inicial.",
                    root_email,
                )
        if changed:
            db.session.add(user)
            db.session.commit()
            app.logger.info("Cuenta root admin sincronizada: %s", root_email)
        return

    generated_password = configured_password or secrets.token_urlsafe(18)
    user = User(
        nombre=root_first_name,
        apellido=root_last_name,
        email=root_email,
        password=generate_password_hash(generated_password),
        role="admin",
        email_verificado=True,
        email_verificado_at=datetime.utcnow(),
        activo=True,
    )
    db.session.add(user)
    db.session.commit()
    if configured_password:
        app.logger.info("Cuenta root admin creada: %s", root_email)
    else:
        app.logger.warning(
            "Cuenta root admin creada para %s con clave aleatoria. "
            "Usa 'Olvide mi contrasena' para establecer una clave propia.",
            root_email,
        )


def _ensure_student_profile_columns() -> None:
    inspector = inspect(db.engine)
    if "student_profiles" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    existing_columns = {column["name"] for column in inspector.get_columns("student_profiles")}
    
    # If id column is missing, we need to recreate the table with the proper schema
    if "id" not in existing_columns:
        # Drop and recreate the table with correct schema
        try:
            db.session.execute(text("DROP TABLE student_profiles"))
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Let the model be recreated by db.create_all()
        return

    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    date_type = "DATE"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    id_type = "NVARCHAR(24)" if dialect_name == "mssql" else "VARCHAR(24)"
    school_type = "NVARCHAR(30)" if dialect_name == "mssql" else "VARCHAR(30)"
    add_column_prefix = (
        "ALTER TABLE student_profiles ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE student_profiles ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("student_profiles")}
    statements = []

    if "student_id" not in existing_columns:
        statements.append(f"{add_column_prefix} student_id {id_type}")
    if "user_id" not in existing_columns:
        statements.append(f"{add_column_prefix} user_id {int_type}")
    if "segundo_apellido" not in existing_columns:
        statements.append(f"{add_column_prefix} segundo_apellido {text_type}")
    if "genero" not in existing_columns:
        statements.append(f"{add_column_prefix} genero {school_type}")
    if "fecha_nacimiento" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_nacimiento {date_type}")
    if "edad" not in existing_columns:
        statements.append(f"{add_column_prefix} edad {int_type}")
    if "grado_nivel" not in existing_columns:
        statements.append(f"{add_column_prefix} grado_nivel {school_type}")
    if "school_id" not in existing_columns:
        statements.append(f"{add_column_prefix} school_id {school_type}")
    if "enrollment_year" not in existing_columns:
        statements.append(f"{add_column_prefix} enrollment_year {int_type}")
    if "academic_status" not in existing_columns:
        statements.append(f"{add_column_prefix} academic_status {school_type}")
    if "interest_technology" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_technology FLOAT")
    if "interest_design" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_design FLOAT")
    if "interest_business" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_business FLOAT")
    if "interest_health" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_health FLOAT")
    if "fecha_creacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_creacion {datetime_type}")
    if "fecha_actualizacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_actualizacion {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_teacher_profile_columns() -> None:
    inspector = inspect(db.engine)
    if "teacher_profiles" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    existing_columns = {column["name"] for column in inspector.get_columns("teacher_profiles")}
    
    # If id column is missing, we need to recreate the table with the proper schema
    if "id" not in existing_columns:
        # Drop and recreate the table with correct schema
        try:
            db.session.execute(text("DROP TABLE teacher_profiles"))
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Let the model be recreated by db.create_all()
        return

    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    short_text_type = "NVARCHAR(30)" if dialect_name == "mssql" else "VARCHAR(30)"
    add_column_prefix = (
        "ALTER TABLE teacher_profiles ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE teacher_profiles ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("teacher_profiles")}
    statements = []

    if "employee_id" not in existing_columns:
        statements.append(f"{add_column_prefix} employee_id {short_text_type}")
    if "user_id" not in existing_columns:
        statements.append(f"{add_column_prefix} user_id {int_type}")
    if "especialidad" not in existing_columns:
        statements.append(f"{add_column_prefix} especialidad {text_type}")
    if "departamento" not in existing_columns:
        statements.append(f"{add_column_prefix} departamento {text_type}")
    if "telefono" not in existing_columns:
        statements.append(f"{add_column_prefix} telefono {short_text_type}")
    if "school_id" not in existing_columns:
        statements.append(f"{add_column_prefix} school_id {short_text_type}")
    if "labor_status" not in existing_columns:
        statements.append(f"{add_column_prefix} labor_status {short_text_type}")
    if "fecha_creacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_creacion {datetime_type}")
    if "fecha_actualizacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_actualizacion {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_calificaciones_columns() -> None:
    inspector = inspect(db.engine)
    if "calificaciones" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    float_type = "FLOAT" if dialect_name == "mssql" else "FLOAT"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    short_text_type = "NVARCHAR(40)" if dialect_name == "mssql" else "VARCHAR(40)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE calificaciones ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE calificaciones ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("calificaciones")}
    statements = []

    if "estudiante_id" not in existing_columns:
        statements.append(f"{add_column_prefix} estudiante_id {int_type}")
    if "asignatura" not in existing_columns:
        statements.append(f"{add_column_prefix} asignatura {text_type}")
    if "valor" not in existing_columns:
        statements.append(f"{add_column_prefix} valor {float_type}")
    if "periodo" not in existing_columns:
        statements.append(f"{add_column_prefix} periodo {short_text_type}")
    if "anio" not in existing_columns:
        statements.append(f"{add_column_prefix} anio {int_type}")
    if "observaciones" not in existing_columns:
        statements.append(f"{add_column_prefix} observaciones {long_text_type}")
    if "fecha_creacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_creacion {datetime_type}")
    if "fecha_actualizacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_actualizacion {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_student_interests_columns() -> None:
    inspector = inspect(db.engine)
    if "student_interests" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    id_text_type = "NVARCHAR(24)" if dialect_name == "mssql" else "VARCHAR(24)"
    float_type = "FLOAT" if dialect_name == "mssql" else "FLOAT"
    add_column_prefix = (
        "ALTER TABLE student_interests ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE student_interests ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("student_interests")}
    statements = []
    if "student_id" not in existing_columns:
        statements.append(f"{add_column_prefix} student_id {id_text_type}")
    if "interest_technology" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_technology {float_type}")
    if "interest_design" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_design {float_type}")
    if "interest_business" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_business {float_type}")
    if "interest_health" not in existing_columns:
        statements.append(f"{add_column_prefix} interest_health {float_type}")

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _ensure_academic_scores_columns() -> None:
    inspector = inspect(db.engine)
    if "academic_scores" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    float_type = "FLOAT" if dialect_name == "mssql" else "FLOAT"
    id_text_type = "NVARCHAR(24)" if dialect_name == "mssql" else "VARCHAR(24)"
    add_column_prefix = (
        "ALTER TABLE academic_scores ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE academic_scores ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("academic_scores")}

    id_added = False
    if "id" not in existing_columns:
        if dialect_name == "mssql":
            id_statements = [
                "ALTER TABLE academic_scores ADD id INT IDENTITY(1,1) NOT NULL",
                "ALTER TABLE dbo.academic_scores ADD id INT IDENTITY(1,1) NOT NULL",
            ]
        else:
            id_statements = [f"{add_column_prefix} id {int_type}"]
        for stmt in id_statements:
            try:
                db.session.execute(text(stmt))
                db.session.commit()
                id_added = True
                break
            except Exception:
                db.session.rollback()

    if id_added:
        inspector = inspect(db.engine)
        existing_columns = {column["name"] for column in inspector.get_columns("academic_scores")}

    statements = []
    if "estudiante_id" not in existing_columns:
        statements.append(f"{add_column_prefix} estudiante_id {int_type}")
    if "student_id" not in existing_columns:
        statements.append(f"{add_column_prefix} student_id {id_text_type}")
    if "anio" not in existing_columns:
        statements.append(f"{add_column_prefix} anio {int_type}")
    if "math_average" not in existing_columns:
        statements.append(f"{add_column_prefix} math_average {float_type}")
    if "language_average" not in existing_columns:
        statements.append(f"{add_column_prefix} language_average {float_type}")
    if "science_average" not in existing_columns:
        statements.append(f"{add_column_prefix} science_average {float_type}")
    if "overall_average" not in existing_columns:
        statements.append(f"{add_column_prefix} overall_average {float_type}")
    if "period_count" not in existing_columns:
        statements.append(f"{add_column_prefix} period_count {int_type}")
    if "fecha_creacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_creacion {datetime_type}")
    if "fecha_actualizacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_actualizacion {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _ensure_profile_edit_request_columns() -> None:
    inspector = inspect(db.engine)
    if "profile_edit_requests" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    short_text_type = "NVARCHAR(20)" if dialect_name == "mssql" else "VARCHAR(20)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE profile_edit_requests ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE profile_edit_requests ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("profile_edit_requests")}
    statements = []

    if "user_id" not in existing_columns:
        statements.append(f"{add_column_prefix} user_id {int_type}")
    if "status" not in existing_columns:
        statements.append(f"{add_column_prefix} status {short_text_type}")
    if "request_payload_json" not in existing_columns:
        statements.append(f"{add_column_prefix} request_payload_json {long_text_type}")
    if "admin_note" not in existing_columns:
        statements.append(f"{add_column_prefix} admin_note {long_text_type}")
    if "reviewed_by" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_by {int_type}")
    if "requested_at" not in existing_columns:
        statements.append(f"{add_column_prefix} requested_at {datetime_type}")
    if "reviewed_at" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_at {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_comentario_edit_request_columns() -> None:
    inspector = inspect(db.engine)
    if "comentario_edit_requests" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    short_text_type = "NVARCHAR(20)" if dialect_name == "mssql" else "VARCHAR(20)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE comentario_edit_requests ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE comentario_edit_requests ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("comentario_edit_requests")}
    statements = []

    if "profesor_id" not in existing_columns:
        statements.append(f"{add_column_prefix} profesor_id {int_type}")
    if "estudiante_id" not in existing_columns:
        statements.append(f"{add_column_prefix} estudiante_id {int_type}")
    if "status" not in existing_columns:
        statements.append(f"{add_column_prefix} status {short_text_type}")
    if "teacher_note" not in existing_columns:
        statements.append(f"{add_column_prefix} teacher_note {long_text_type}")
    if "admin_note" not in existing_columns:
        statements.append(f"{add_column_prefix} admin_note {long_text_type}")
    if "requested_at" not in existing_columns:
        statements.append(f"{add_column_prefix} requested_at {datetime_type}")
    if "reviewed_at" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_at {datetime_type}")
    if "reviewed_by" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_by {int_type}")
    if "used_at" not in existing_columns:
        statements.append(f"{add_column_prefix} used_at {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_grade_change_request_columns() -> None:
    inspector = inspect(db.engine)
    if "grade_change_requests" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    float_type = "FLOAT"
    short_text_type = "NVARCHAR(20)" if dialect_name == "mssql" else "VARCHAR(20)"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE grade_change_requests ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE grade_change_requests ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("grade_change_requests")}
    statements = []

    if "profesor_id" not in existing_columns:
        statements.append(f"{add_column_prefix} profesor_id {int_type}")
    if "estudiante_id" not in existing_columns:
        statements.append(f"{add_column_prefix} estudiante_id {int_type}")
    if "calificacion_id" not in existing_columns:
        statements.append(f"{add_column_prefix} calificacion_id {int_type}")
    if "asignatura" not in existing_columns:
        statements.append(f"{add_column_prefix} asignatura {text_type}")
    if "periodo" not in existing_columns:
        statements.append(f"{add_column_prefix} periodo {short_text_type}")
    if "anio" not in existing_columns:
        statements.append(f"{add_column_prefix} anio {int_type}")
    if "valor" not in existing_columns:
        statements.append(f"{add_column_prefix} valor {float_type}")
    if "observaciones" not in existing_columns:
        statements.append(f"{add_column_prefix} observaciones {long_text_type}")
    if "status" not in existing_columns:
        statements.append(f"{add_column_prefix} status {short_text_type}")
    if "admin_note" not in existing_columns:
        statements.append(f"{add_column_prefix} admin_note {long_text_type}")
    if "requested_at" not in existing_columns:
        statements.append(f"{add_column_prefix} requested_at {datetime_type}")
    if "reviewed_at" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_at {datetime_type}")
    if "reviewed_by" not in existing_columns:
        statements.append(f"{add_column_prefix} reviewed_by {int_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_audit_log_columns() -> None:
    inspector = inspect(db.engine)
    if "audit_logs" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    text_type = "NVARCHAR(80)" if dialect_name == "mssql" else "VARCHAR(80)"
    id_text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE audit_logs ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE audit_logs ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("audit_logs")}
    statements = []

    if "actor_user_id" not in existing_columns:
        statements.append(f"{add_column_prefix} actor_user_id {int_type}")
    if "action" not in existing_columns:
        statements.append(f"{add_column_prefix} action {text_type}")
    if "target_type" not in existing_columns:
        statements.append(f"{add_column_prefix} target_type {text_type}")
    if "target_id" not in existing_columns:
        statements.append(f"{add_column_prefix} target_id {id_text_type}")
    if "metadata_json" not in existing_columns:
        statements.append(f"{add_column_prefix} metadata_json {long_text_type}")
    if "created_at" not in existing_columns:
        statements.append(f"{add_column_prefix} created_at {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_form_response_columns() -> None:
    inspector = inspect(db.engine)
    if "form_responses" not in inspector.get_table_names():
        return

    dialect_name = db.engine.dialect.name
    datetime_type = "DATETIME2" if dialect_name == "mssql" else "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    short_text_type = "NVARCHAR(40)" if dialect_name == "mssql" else "VARCHAR(40)"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    id_text_type = "NVARCHAR(24)" if dialect_name == "mssql" else "VARCHAR(24)"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE form_responses ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE form_responses ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("form_responses")}

    id_added = False
    if "id" not in existing_columns:
        if dialect_name == "mssql":
            id_statements = [
                "ALTER TABLE form_responses ADD id INT IDENTITY(1,1) NOT NULL",
                "ALTER TABLE dbo.form_responses ADD id INT IDENTITY(1,1) NOT NULL",
            ]
        else:
            id_statements = [f"{add_column_prefix} id {int_type}"]

        for stmt in id_statements:
            try:
                db.session.execute(text(stmt))
                db.session.commit()
                id_added = True
                break
            except Exception:
                db.session.rollback()

    if id_added:
        inspector = inspect(db.engine)
        existing_columns = {column["name"] for column in inspector.get_columns("form_responses")}

    statements = []

    if "estudiante_id" not in existing_columns:
        statements.append(f"{add_column_prefix} estudiante_id {int_type}")
    if "evaluacion_id" not in existing_columns:
        statements.append(f"{add_column_prefix} evaluacion_id {int_type}")
    if "submission_id" not in existing_columns:
        statements.append(f"{add_column_prefix} submission_id {text_type}")
    if "respondent_id" not in existing_columns:
        statements.append(f"{add_column_prefix} respondent_id {text_type}")
    if "form_id" not in existing_columns:
        statements.append(f"{add_column_prefix} form_id {short_text_type}")
    if "matricula_estudiante" not in existing_columns:
        statements.append(f"{add_column_prefix} matricula_estudiante {id_text_type}")
    if "submitted_at" not in existing_columns:
        statements.append(f"{add_column_prefix} submitted_at {datetime_type}")
    if "payload_json" not in existing_columns:
        statements.append(f"{add_column_prefix} payload_json {long_text_type}")
    if "source" not in existing_columns:
        statements.append(f"{add_column_prefix} source {short_text_type}")
    if "fecha_creacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_creacion {datetime_type}")
    if "fecha_actualizacion" not in existing_columns:
        statements.append(f"{add_column_prefix} fecha_actualizacion {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))

    if statements:
        db.session.commit()


def _ensure_ai_predictions_columns() -> None:
    inspector = inspect(db.engine)
    dialect_name = db.engine.dialect.name
    table_names = set(inspector.get_table_names())

    if "ai_predictions" not in table_names:
        if dialect_name == "mssql":
            db.session.execute(
                text(
                    """
                    CREATE TABLE ai_predictions (
                        prediction_id INT IDENTITY(1,1) PRIMARY KEY,
                        student_id INT NOT NULL,
                        response_id INT NULL,
                        recommended_career NVARCHAR(120) NULL,
                        prob_informatica FLOAT NULL,
                        prob_enfermeria FLOAT NULL,
                        prob_administracion FLOAT NULL,
                        prob_comercio FLOAT NULL,
                        model_version NVARCHAR(40) NULL,
                        predicted_at DATETIME2 NOT NULL DEFAULT (GETDATE())
                    )
                    """
                )
            )
        elif dialect_name == "sqlite":
            db.session.execute(
                text(
                    """
                    CREATE TABLE ai_predictions (
                        prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL,
                        response_id INTEGER,
                        recommended_career VARCHAR(120),
                        prob_informatica FLOAT,
                        prob_enfermeria FLOAT,
                        prob_administracion FLOAT,
                        prob_comercio FLOAT,
                        model_version VARCHAR(40),
                        predicted_at DATETIME
                    )
                    """
                )
            )
        else:
            db.session.execute(
                text(
                    """
                    CREATE TABLE ai_predictions (
                        prediction_id SERIAL PRIMARY KEY,
                        student_id INTEGER NOT NULL,
                        response_id INTEGER,
                        recommended_career VARCHAR(120),
                        prob_informatica FLOAT,
                        prob_enfermeria FLOAT,
                        prob_administracion FLOAT,
                        prob_comercio FLOAT,
                        model_version VARCHAR(40),
                        predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        db.session.commit()
        return

    if dialect_name == "mssql":
        datetime_type = "DATETIME2"
    elif dialect_name == "postgresql":
        datetime_type = "TIMESTAMP"
    else:
        datetime_type = "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    text_type = "NVARCHAR(120)" if dialect_name == "mssql" else "VARCHAR(120)"
    short_text_type = "NVARCHAR(40)" if dialect_name == "mssql" else "VARCHAR(40)"
    add_column_prefix = (
        "ALTER TABLE ai_predictions ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE ai_predictions ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("ai_predictions")}
    statements = []
    if "prediction_id" not in existing_columns:
        if dialect_name == "mssql":
            for stmt in (
                "ALTER TABLE ai_predictions ADD prediction_id INT IDENTITY(1,1) NOT NULL",
                "ALTER TABLE dbo.ai_predictions ADD prediction_id INT IDENTITY(1,1) NOT NULL",
            ):
                try:
                    db.session.execute(text(stmt))
                    db.session.commit()
                    break
                except Exception:
                    db.session.rollback()
        else:
            statements.append(f"{add_column_prefix} prediction_id {int_type}")
    if "student_id" not in existing_columns:
        statements.append(f"{add_column_prefix} student_id {int_type}")
    if "response_id" not in existing_columns:
        statements.append(f"{add_column_prefix} response_id {int_type}")
    if "recommended_career" not in existing_columns:
        statements.append(f"{add_column_prefix} recommended_career {text_type}")
    if "prob_informatica" not in existing_columns:
        statements.append(f"{add_column_prefix} prob_informatica FLOAT")
    if "prob_enfermeria" not in existing_columns:
        statements.append(f"{add_column_prefix} prob_enfermeria FLOAT")
    if "prob_administracion" not in existing_columns:
        statements.append(f"{add_column_prefix} prob_administracion FLOAT")
    if "prob_comercio" not in existing_columns:
        statements.append(f"{add_column_prefix} prob_comercio FLOAT")
    if "model_version" not in existing_columns:
        statements.append(f"{add_column_prefix} model_version {short_text_type}")
    if "predicted_at" not in existing_columns:
        statements.append(f"{add_column_prefix} predicted_at {datetime_type}")

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _ensure_teacher_observations_columns() -> None:
    inspector = inspect(db.engine)
    dialect_name = db.engine.dialect.name
    table_names = set(inspector.get_table_names())

    if "teacher_observations" not in table_names:
        if dialect_name == "mssql":
            db.session.execute(
                text(
                    """
                    CREATE TABLE teacher_observations (
                        student_id INT NOT NULL PRIMARY KEY,
                        teacher_id INT NULL,
                        teacher_comment NVARCHAR(MAX) NULL,
                        observation NVARCHAR(MAX) NULL,
                        observed_at DATETIME2 NOT NULL DEFAULT (GETDATE()),
                        teamwork_score FLOAT NULL,
                        discipline_score FLOAT NULL,
                        motivation_score FLOAT NULL
                    )
                    """
                )
            )
        elif dialect_name == "sqlite":
            db.session.execute(
                text(
                    """
                    CREATE TABLE teacher_observations (
                        student_id INTEGER PRIMARY KEY,
                        teacher_id INTEGER,
                        teacher_comment TEXT,
                        observation TEXT,
                        observed_at DATETIME,
                        teamwork_score FLOAT,
                        discipline_score FLOAT,
                        motivation_score FLOAT
                    )
                    """
                )
            )
        else:
            db.session.execute(
                text(
                    """
                    CREATE TABLE teacher_observations (
                        student_id INTEGER PRIMARY KEY,
                        teacher_id INTEGER,
                        teacher_comment TEXT,
                        observation TEXT,
                        observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        teamwork_score FLOAT,
                        discipline_score FLOAT,
                        motivation_score FLOAT
                    )
                    """
                )
            )
        db.session.commit()
        return

    if dialect_name == "mssql":
        datetime_type = "DATETIME2"
    elif dialect_name == "postgresql":
        datetime_type = "TIMESTAMP"
    else:
        datetime_type = "DATETIME"
    int_type = "INT" if dialect_name == "mssql" else "INTEGER"
    long_text_type = "NVARCHAR(MAX)" if dialect_name == "mssql" else "TEXT"
    add_column_prefix = (
        "ALTER TABLE teacher_observations ADD"
        if dialect_name == "mssql"
        else "ALTER TABLE teacher_observations ADD COLUMN"
    )

    existing_columns = {column["name"] for column in inspector.get_columns("teacher_observations")}
    statements = []
    if "student_id" not in existing_columns:
        statements.append(f"{add_column_prefix} student_id {int_type}")
    if "teacher_id" not in existing_columns:
        statements.append(f"{add_column_prefix} teacher_id {int_type}")
    if "teacher_comment" not in existing_columns:
        statements.append(f"{add_column_prefix} teacher_comment {long_text_type}")
    if "observation" not in existing_columns:
        statements.append(f"{add_column_prefix} observation {long_text_type}")
    if "observed_at" not in existing_columns:
        statements.append(f"{add_column_prefix} observed_at {datetime_type}")
    if "teamwork_score" not in existing_columns:
        statements.append(f"{add_column_prefix} teamwork_score FLOAT")
    if "discipline_score" not in existing_columns:
        statements.append(f"{add_column_prefix} discipline_score FLOAT")
    if "motivation_score" not in existing_columns:
        statements.append(f"{add_column_prefix} motivation_score FLOAT")

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _drop_legacy_student_interest_fk_if_present() -> None:
    inspector = inspect(db.engine)
    if db.engine.dialect.name != "mssql":
        return
    if "student_interests" not in set(inspector.get_table_names()):
        return

    try:
        db.session.execute(
            text(
                """
                IF OBJECT_ID('dbo.student_interests', 'U') IS NOT NULL
                BEGIN
                    DECLARE @fkName NVARCHAR(128);
                    SELECT TOP 1 @fkName = fk.name
                    FROM sys.foreign_keys fk
                    INNER JOIN sys.tables tParent ON fk.parent_object_id = tParent.object_id
                    INNER JOIN sys.tables tRef ON fk.referenced_object_id = tRef.object_id
                    WHERE tParent.name = 'student_interests'
                      AND (fk.name = 'fk_interests_student' OR tRef.name IN ('students_data', 'student_data'));
                    IF @fkName IS NOT NULL
                    BEGIN
                        EXEC('ALTER TABLE dbo.student_interests DROP CONSTRAINT [' + @fkName + ']');
                    END
                END
                """
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _normalize_academic_scores_legacy_columns() -> None:
    inspector = inspect(db.engine)
    if "academic_scores" not in set(inspector.get_table_names()):
        return

    columns = {column["name"] for column in inspector.get_columns("academic_scores")}
    statements = []
    if {"math_avg", "math_average"}.issubset(columns):
        statements.append("UPDATE academic_scores SET math_avg = COALESCE(math_avg, math_average)")
    if {"language_avg", "language_average"}.issubset(columns):
        statements.append("UPDATE academic_scores SET language_avg = COALESCE(language_avg, language_average)")
    if {"science_avg", "science_average"}.issubset(columns):
        statements.append("UPDATE academic_scores SET science_avg = COALESCE(science_avg, science_average)")

    if (
        db.engine.dialect.name == "mssql"
        and {"student_id", "estudiante_id"}.issubset(columns)
        and "student_profiles" in set(inspector.get_table_names())
    ):
        statements.append(
            """
            UPDATE ac
            SET ac.student_id = COALESCE(ac.student_id, sp.student_id)
            FROM academic_scores ac
            INNER JOIN student_profiles sp ON sp.user_id = ac.estudiante_id
            WHERE ac.student_id IS NULL OR LTRIM(RTRIM(CAST(ac.student_id AS NVARCHAR(24)))) = ''
            """
        )

    for stmt in statements:
        db.session.execute(text(stmt))
    if statements:
        db.session.commit()


def _drop_deprecated_tables_if_requested(app) -> None:
    if not app.config.get("DROP_DEPRECATED_TABLES", False):
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    dialect_name = db.engine.dialect.name

    if "form_responses" in table_names and not app.config.get("ENABLE_FORM_RESPONSE_STORAGE", False):
        try:
            if dialect_name == "mssql":
                db.session.execute(
                    text(
                        """
                        IF OBJECT_ID('dbo.form_responses', 'U') IS NOT NULL
                        BEGIN
                            DROP TABLE dbo.form_responses;
                        END
                        ELSE IF OBJECT_ID('form_responses', 'U') IS NOT NULL
                        BEGIN
                            DROP TABLE form_responses;
                        END
                        """
                    )
                )
            else:
                db.session.execute(text("DROP TABLE form_responses"))
            db.session.commit()
        except Exception:
            db.session.rollback()

    for table_name in ("student_data", "students_data"):
        if table_name not in table_names:
            continue
        try:
            total = db.session.execute(text(f"SELECT COUNT(1) AS total FROM {table_name}")).scalar() or 0
            if int(total) > 0:
                continue
            if dialect_name == "mssql":
                db.session.execute(
                    text(
                        f"""
                        IF OBJECT_ID('dbo.{table_name}', 'U') IS NOT NULL
                        BEGIN
                            DROP TABLE dbo.{table_name};
                        END
                        ELSE IF OBJECT_ID('{table_name}', 'U') IS NOT NULL
                        BEGIN
                            DROP TABLE {table_name};
                        END
                        """
                    )
                )
            else:
                db.session.execute(text(f"DROP TABLE {table_name}"))
            db.session.commit()
        except Exception:
            db.session.rollback()


def _drop_unused_student_data_table_if_empty() -> None:
    inspector = inspect(db.engine)
    if "student_data" not in inspector.get_table_names():
        return

    try:
        total = db.session.execute(text("SELECT COUNT(1) AS total FROM student_data")).scalar() or 0
    except Exception:
        db.session.rollback()
        return

    if int(total) > 0:
        return

    try:
        dialect_name = db.engine.dialect.name
        if dialect_name == "mssql":
            db.session.execute(
                text(
                    """
                    IF OBJECT_ID('dbo.student_data', 'U') IS NOT NULL
                    BEGIN
                        DROP TABLE dbo.student_data;
                    END
                    ELSE IF OBJECT_ID('student_data', 'U') IS NOT NULL
                    BEGIN
                        DROP TABLE student_data;
                    END
                    """
                )
            )
        else:
            db.session.execute(text("DROP TABLE student_data"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _backfill_student_interests_from_profiles() -> None:
    try:
        from .models.student_interest import StudentInterest
        from .models.student_profile import StudentProfile
        from .models.user import User
        from .services.student_data_sync_service import sync_student_data_if_exists
    except Exception:
        return

    try:
        profiles = StudentProfile.query.all()
    except Exception:
        db.session.rollback()
        return

    changed = False
    for profile in profiles:
        student_id = (profile.student_id or "").strip()
        if not student_id:
            continue
        user = User.query.get(profile.user_id)
        if user:
            sync_student_data_if_exists(user, profile)
        row = StudentInterest.query.filter_by(student_id=student_id).first()
        if not row:
            row = StudentInterest(student_id=student_id)
        row.interest_technology = getattr(profile, "interest_technology", 0) or 0
        row.interest_design = getattr(profile, "interest_design", 0) or 0
        row.interest_business = getattr(profile, "interest_business", 0) or 0
        row.interest_health = getattr(profile, "interest_health", 0) or 0
        db.session.add(row)
        changed = True

    if changed:
        db.session.commit()


def _backfill_academic_scores_if_empty() -> None:
    try:
        from .models.academic_score import AcademicScore
        from .models.calificacion import Calificacion
        from .services.academic_score_service import recalculate_all_academic_scores
    except Exception:
        return

    try:
        has_scores = AcademicScore.query.first() is not None
        has_grades = Calificacion.query.first() is not None
    except Exception:
        db.session.rollback()
        return

    if has_scores or not has_grades:
        return

    recalculate_all_academic_scores()
    db.session.commit()


def _backfill_ai_predictions_from_evaluaciones() -> None:
    try:
        from .models.evaluacion import Evaluacion
        from .models.user import User
        from .services.prediction_persistence_service import upsert_ai_prediction_for_evaluacion
        from .services.recommendation_engine_service import build_recommendation_for_student
    except Exception:
        return

    inspector = inspect(db.engine)
    if "ai_predictions" not in set(inspector.get_table_names()):
        return

    try:
        evaluaciones = (
            Evaluacion.query.filter_by(origen="tally")
            .order_by(Evaluacion.fecha_creacion.desc())
            .limit(5000)
            .all()
        )
    except Exception:
        db.session.rollback()
        return

    changed = False
    for evaluacion in evaluaciones:
        student = User.query.get(evaluacion.estudiante_id)
        if not student:
            continue
        try:
            recommendation = build_recommendation_for_student(student=student, evaluacion=evaluacion)
            upsert_ai_prediction_for_evaluacion(
                student_id=student.id,
                evaluacion_id=evaluacion.id,
                recommendation=recommendation,
            )
            changed = True
        except Exception:
            db.session.rollback()
            return

    if changed:
        db.session.commit()


def _backfill_teacher_observations_from_evaluaciones() -> None:
    try:
        from .models.evaluacion import Evaluacion
        from .services.teacher_observation_service import upsert_teacher_observation
    except Exception:
        return

    inspector = inspect(db.engine)
    if "teacher_observations" not in set(inspector.get_table_names()):
        return

    try:
        evaluaciones = (
            Evaluacion.query.filter(Evaluacion.comentario_profesor.isnot(None))
            .order_by(Evaluacion.comentario_profesor_at.desc(), Evaluacion.fecha_creacion.desc())
            .limit(5000)
            .all()
        )
    except Exception:
        db.session.rollback()
        return

    changed = False
    for evaluacion in evaluaciones:
        comentario = (evaluacion.comentario_profesor or "").strip()
        if not comentario:
            continue
        try:
            upsert_teacher_observation(
                student_id=evaluacion.estudiante_id,
                teacher_id=evaluacion.profesor_comentario_id,
                observation_text=comentario,
                observed_at=evaluacion.comentario_profesor_at or evaluacion.fecha_actualizacion,
            )
            changed = True
        except Exception:
            db.session.rollback()
            return

    if changed:
        db.session.commit()


def create_app(env: str = "default"):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config[env])
    app.config["JSON_AS_ASCII"] = False
    try:
        app.json.ensure_ascii = False
    except Exception:
        pass

    db.init_app(app)
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Debes iniciar sesion para acceder."
    login_manager.login_message_category = "warning"

    @app.context_processor
    def inject_csrf_token():
        from .services.settings_service import get_bool_setting, get_datetime_setting, get_setting

        try:
            maintenance_mode_enabled = get_bool_setting("maintenance_mode_enabled", default=False)
            demo_mode_enabled = get_bool_setting("demo_mode_enabled", default=False)
            maintenance_eta_dt = get_datetime_setting("maintenance_eta_text")
            if maintenance_eta_dt:
                maintenance_eta_text = maintenance_eta_dt.strftime("%Y-%m-%d %H:%M")
            else:
                maintenance_eta_text = (get_setting("maintenance_eta_text", "") or "").strip()
        except Exception:
            db.session.rollback()
            maintenance_mode_enabled = False
            demo_mode_enabled = False
            maintenance_eta_text = ""

        context = {
            "csrf_token": generate_csrf,
            "nav_notifications": [],
            "nav_notifications_count": 0,
            "show_notification_toast": False,
            "maintenance_mode_enabled": maintenance_mode_enabled,
            "demo_mode_enabled": demo_mode_enabled,
            "maintenance_eta_text": maintenance_eta_text,
        }
        try:
            is_authenticated = bool(current_user.is_authenticated)
        except Exception:
            db.session.rollback()
            return context

        if not is_authenticated:
            return context

        context["show_notification_toast"] = bool(session.pop("show_notification_toast", False))

        try:
            if current_user.role == "admin":
                from .models.comentario_edit_request import ComentarioEditRequest
                from .models.grade_change_request import GradeChangeRequest
                from .models.profile_edit_request import ProfileEditRequest
                from .models.user import User

                profile_count = ProfileEditRequest.query.filter_by(status="pendiente").count()
                comment_count = ComentarioEditRequest.query.filter_by(status="pendiente").count()
                grade_request_count = GradeChangeRequest.query.filter_by(status="pendiente").count()
                pending_email_verification = (
                    User.query.filter(
                        User.role.in_(["estudiante", "profesor"]),
                        User.email_verificado == False,
                    ).count()
                )
                pending_approval = (
                    User.query.filter(
                        User.role.in_(["estudiante", "profesor"]),
                        User.email_verificado == True,
                        User.activo == False,
                    ).count()
                )
                if profile_count:
                    context["nav_notifications"].append(
                        {"label": f"Solicitudes de perfil: {profile_count}", "url": url_for("admin.solicitudes")}
                    )
                if comment_count:
                    context["nav_notifications"].append(
                        {"label": f"Solicitudes de comentarios: {comment_count}", "url": url_for("admin.solicitudes")}
                    )
                if pending_email_verification:
                    context["nav_notifications"].append(
                        {
                            "label": f"Cuentas por verificar correo: {pending_email_verification}",
                            "url": url_for("admin.solicitudes"),
                        }
                    )
                if pending_approval:
                    context["nav_notifications"].append(
                        {"label": f"Usuarios pendientes de aprobacion: {pending_approval}", "url": url_for("admin.solicitudes")}
                    )
                if grade_request_count:
                    context["nav_notifications"].append(
                        {"label": f"Solicitudes de calificaciones: {grade_request_count}", "url": url_for("admin.solicitudes")}
                    )
                context["nav_notifications_count"] = (
                    profile_count + comment_count + pending_email_verification + pending_approval + grade_request_count
                )
            elif current_user.role == "profesor":
                from .models.comentario_edit_request import ComentarioEditRequest
                from .models.grade_change_request import GradeChangeRequest
                from .models.profile_edit_request import ProfileEditRequest

                profile_pending = ProfileEditRequest.query.filter_by(
                    user_id=current_user.id, status="pendiente"
                ).count()
                comment_pending = ComentarioEditRequest.query.filter_by(
                    profesor_id=current_user.id, status="pendiente"
                ).count()
                comment_approved = ComentarioEditRequest.query.filter_by(
                    profesor_id=current_user.id, status="aprobada"
                ).count()
                grade_pending = GradeChangeRequest.query.filter_by(
                    profesor_id=current_user.id, status="pendiente"
                ).count()
                if profile_pending:
                    context["nav_notifications"].append(
                        {"label": f"Tu solicitud de perfil pendiente: {profile_pending}", "url": url_for("profesor.perfil")}
                    )
                if comment_pending:
                    context["nav_notifications"].append(
                        {"label": f"Solicitudes de edicion pendientes: {comment_pending}", "url": url_for("profesor.index")}
                    )
                if comment_approved:
                    context["nav_notifications"].append(
                        {"label": f"Permisos de edicion aprobados: {comment_approved}", "url": url_for("profesor.index")}
                    )
                if grade_pending:
                    context["nav_notifications"].append(
                        {
                            "label": f"Solicitudes de calificacion pendientes: {grade_pending}",
                            "url": url_for("profesor.index"),
                        }
                    )
                context["nav_notifications_count"] = profile_pending + comment_pending + comment_approved + grade_pending
            elif current_user.role == "estudiante":
                from .models.profile_edit_request import ProfileEditRequest

                profile_pending = ProfileEditRequest.query.filter_by(
                    user_id=current_user.id, status="pendiente"
                ).count()
                if profile_pending:
                    context["nav_notifications"].append(
                        {"label": f"Solicitud de perfil pendiente: {profile_pending}", "url": url_for("estudiante.perfil")}
                    )
                context["nav_notifications_count"] = profile_pending
        except Exception:
            db.session.rollback()
            app.logger.exception("Error cargando notificaciones de navegacion")
            return context
        return context

    @app.before_request
    def apply_runtime_modes():
        from .services.settings_service import get_bool_setting

        endpoint = request.endpoint or ""
        if not endpoint:
            return None

        if endpoint.startswith("static"):
            return None

        try:
            maintenance_mode = get_bool_setting("maintenance_mode_enabled", default=False)
            demo_mode = get_bool_setting("demo_mode_enabled", default=False)
        except Exception:
            db.session.rollback()
            return None

        if maintenance_mode:
            allow_maintenance_endpoints = {
                "auth.login",
                "auth.logout",
                "auth.forgot_password",
                "auth.reset_password",
                "main.maintenance",
            }
            is_admin = bool(current_user.is_authenticated and current_user.role == "admin")
            if not is_admin and endpoint not in allow_maintenance_endpoints:
                if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                    return {"ok": False, "error": "maintenance_mode"}, 503
                return redirect(url_for("main.maintenance"))

        if demo_mode and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            is_admin = bool(current_user.is_authenticated and current_user.role == "admin")
            allow_demo_write_endpoints = {
                "auth.login",
                "auth.logout",
                "auth.register",
                "auth.forgot_password",
                "auth.reset_password",
            }
            if not is_admin and endpoint not in allow_demo_write_endpoints:
                if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                    return {"ok": False, "error": "demo_mode_readonly"}, 403
                flash("Modo demo activo: solo administradores pueden aplicar cambios.", "warning")
                return redirect(request.referrer or url_for("dashboard.index"))
        return None

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        flash("La sesion del formulario expiro. Intenta de nuevo.", "warning")
        return redirect(request.referrer or url_for("auth.login"))

    @app.after_request
    def add_security_headers(response):
        sensitive_endpoints = {
            "auth.login",
            "auth.register",
            "auth.forgot_password",
            "auth.reset_password",
            "auth.logout",
            "dashboard.index",
            "estudiante.index",
            "profesor.index",
            "admin.index",
            "formulario.index",
            "resultado.index",
        }
        session_user_present = bool(session.get("_user_id"))
        if session_user_present or request.endpoint in sensitive_endpoints:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["Vary"] = "Cookie"
        content_type = (response.headers.get("Content-Type") or "").lower()
        if response.mimetype == "text/html" and "charset=" not in content_type:
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    @login_manager.user_loader
    def load_user(user_id):
        from .models.user import User

        try:
            return db.session.get(User, int(user_id))
        except Exception:
            db.session.rollback()
            app.logger.exception("No se pudo cargar user_id=%s desde sesion", user_id)
            return None

    with app.app_context():
        # Carga modelos antes de create_all para registrar metadata completa.
        from .models.academic_score import AcademicScore  # noqa: F401
        from .models.audit_log import AuditLog  # noqa: F401
        from .models.calificacion import Calificacion  # noqa: F401
        from .models.comentario_edit_request import ComentarioEditRequest  # noqa: F401
        from .models.evaluacion import Evaluacion  # noqa: F401
        from .models.form_response import FormResponse  # noqa: F401
        from .models.grade_change_request import GradeChangeRequest  # noqa: F401
        from .models.profile_edit_request import ProfileEditRequest  # noqa: F401
        from .models.system_setting import SystemSetting  # noqa: F401
        from .models.student_interest import StudentInterest  # noqa: F401
        from .models.student_profile import StudentProfile  # noqa: F401
        from .models.teacher_profile import TeacherProfile  # noqa: F401
        from .models.user import User  # noqa: F401

        if db.engine.dialect.name == "mssql":
            _migrate_legacy_sqlserver_users_table()

        startup_steps = [
            ("create_all_initial", db.create_all),
            ("ensure_user_columns", _ensure_user_columns),
            ("ensure_root_admin_account", lambda: _ensure_root_admin_account(app)),
            ("ensure_evaluacion_columns", _ensure_evaluacion_columns),
            ("ensure_student_profile_columns", _ensure_student_profile_columns),
            ("ensure_teacher_profile_columns", _ensure_teacher_profile_columns),
            ("ensure_calificaciones_columns", _ensure_calificaciones_columns),
            ("ensure_student_interests_columns", _ensure_student_interests_columns),
            ("ensure_academic_scores_columns", _ensure_academic_scores_columns),
            ("ensure_profile_edit_request_columns", _ensure_profile_edit_request_columns),
            ("ensure_comentario_edit_request_columns", _ensure_comentario_edit_request_columns),
            ("ensure_grade_change_request_columns", _ensure_grade_change_request_columns),
            ("ensure_form_response_columns", _ensure_form_response_columns),
            ("ensure_ai_predictions_columns", _ensure_ai_predictions_columns),
            ("ensure_teacher_observations_columns", _ensure_teacher_observations_columns),
            ("ensure_audit_log_columns", _ensure_audit_log_columns),
            ("drop_legacy_student_interest_fk_if_present", _drop_legacy_student_interest_fk_if_present),
            ("normalize_academic_scores_legacy_columns", _normalize_academic_scores_legacy_columns),
            ("drop_unused_student_data_table_if_empty", _drop_unused_student_data_table_if_empty),
            ("backfill_student_interests_from_profiles", _backfill_student_interests_from_profiles),
            ("backfill_academic_scores_if_empty", _backfill_academic_scores_if_empty),
            ("backfill_ai_predictions_from_evaluaciones", _backfill_ai_predictions_from_evaluaciones),
            ("backfill_teacher_observations_from_evaluaciones", _backfill_teacher_observations_from_evaluaciones),
            ("create_all_after_migrations", db.create_all),
        ]

        for step_name, step_callable in startup_steps:
            try:
                step_callable()
            except Exception:
                db.session.rollback()
                app.logger.exception("Startup step failed: %s", step_name)

        try:
            _drop_deprecated_tables_if_requested(app)
        except Exception:
            db.session.rollback()
            app.logger.exception("Startup step failed: drop_deprecated_tables_if_requested")

        if app.config.get("SEED_TEST_USERS", False):
            from .seed import seed_test_users

            seed_test_users()

    from .controllers.admin_controller import admin_bp
    from .controllers.auth_controller import auth_bp
    from .controllers.dashboard_controller import dashboard_bp
    from .controllers.estudiante_controller import estudiante_bp
    from .controllers.formulario_controller import formulario_bp
    from .controllers.main_controller import main_bp
    from .controllers.profesor_controller import profesor_bp
    from .controllers.resultado_controller import resultado_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(estudiante_bp, url_prefix="/dashboard/estudiante")
    app.register_blueprint(profesor_bp, url_prefix="/dashboard/profesor")
    app.register_blueprint(admin_bp, url_prefix="/dashboard/admin")
    app.register_blueprint(formulario_bp)
    app.register_blueprint(resultado_bp)

    return app


# Fallback WSGI entrypoint for platforms configured as `gunicorn app:app`.
_wsgi_env = (os.getenv("FLASK_CONFIG", "production") or "production").strip().lower()
if _wsgi_env not in config:
    _wsgi_env = "production"
app = create_app(_wsgi_env)
