USE EdTech;
GO

SET XACT_ABORT ON;
GO

/* ===========================================================
   1) Compatibilidad con esquema legacy users (user_id, first_name...)
   =========================================================== */
IF OBJECT_ID('dbo.users', 'U') IS NOT NULL
AND COL_LENGTH('dbo.users', 'id') IS NULL
AND COL_LENGTH('dbo.users', 'user_id') IS NOT NULL
BEGIN
    IF OBJECT_ID('dbo.users_legacy', 'U') IS NULL
    BEGIN
        EXEC sp_rename 'dbo.users', 'users_legacy';
    END
END
GO

/* ===========================================================
   2) Tabla users esperada por la aplicacion Flask actual
   =========================================================== */
IF OBJECT_ID('dbo.users', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.users (
        id INT IDENTITY(1,1) PRIMARY KEY,
        nombre NVARCHAR(100) NOT NULL,
        apellido NVARCHAR(100) NOT NULL,
        email NVARCHAR(120) NOT NULL UNIQUE,
        [password] NVARCHAR(200) NOT NULL,
        [role] NVARCHAR(20) NOT NULL CONSTRAINT DF_users_role DEFAULT ('estudiante')
            CHECK ([role] IN ('estudiante', 'profesor', 'admin')),
        fecha_creacion DATETIME2 NOT NULL CONSTRAINT DF_users_fecha_creacion DEFAULT (GETDATE()),
        email_verificado BIT NOT NULL CONSTRAINT DF_users_email_verificado DEFAULT (0),
        email_verificado_at DATETIME2 NULL,
        ultimo_login DATETIME2 NULL,
        activo BIT NOT NULL CONSTRAINT DF_users_activo DEFAULT (1),
        reset_requested_at DATETIME2 NULL,
        profesor_id INT NULL,
        seccion NVARCHAR(50) NULL
    );
END
GO

IF COL_LENGTH('dbo.users', 'email_verificado') IS NULL
    ALTER TABLE dbo.users ADD email_verificado BIT NOT NULL CONSTRAINT DF_users_email_verificado2 DEFAULT (0);
GO
IF COL_LENGTH('dbo.users', 'email_verificado_at') IS NULL
    ALTER TABLE dbo.users ADD email_verificado_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.users', 'ultimo_login') IS NULL
    ALTER TABLE dbo.users ADD ultimo_login DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.users', 'activo') IS NULL
    ALTER TABLE dbo.users ADD activo BIT NOT NULL CONSTRAINT DF_users_activo2 DEFAULT (1);
GO
IF COL_LENGTH('dbo.users', 'reset_requested_at') IS NULL
    ALTER TABLE dbo.users ADD reset_requested_at DATETIME2 NULL;
GO
IF COL_LENGTH('dbo.users', 'profesor_id') IS NULL
    ALTER TABLE dbo.users ADD profesor_id INT NULL;
GO
IF COL_LENGTH('dbo.users', 'seccion') IS NULL
    ALTER TABLE dbo.users ADD seccion NVARCHAR(50) NULL;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = 'FK_users_profesor'
)
BEGIN
    ALTER TABLE dbo.users
    ADD CONSTRAINT FK_users_profesor
    FOREIGN KEY (profesor_id) REFERENCES dbo.users(id);
END
GO

/* ===========================================================
   3) Migrar datos desde users_legacy (si existiera)
   =========================================================== */
IF OBJECT_ID('dbo.users_legacy', 'U') IS NOT NULL
BEGIN
    INSERT INTO dbo.users (
        nombre, apellido, email, [password], [role], fecha_creacion,
        email_verificado, email_verificado_at, ultimo_login, activo
    )
    SELECT
        ISNULL(NULLIF(LTRIM(RTRIM(first_name)), ''), 'SinNombre') AS nombre,
        ISNULL(NULLIF(LTRIM(RTRIM(last_name)), ''), 'SinApellido') AS apellido,
        LTRIM(RTRIM(email)) AS email,
        password_hash AS [password],
        CASE WHEN [role] IN ('estudiante', 'profesor', 'admin') THEN [role] ELSE 'estudiante' END AS [role],
        ISNULL(created_at, GETDATE()) AS fecha_creacion,
        1 AS email_verificado,
        ISNULL(created_at, GETDATE()) AS email_verificado_at,
        last_login AS ultimo_login,
        1 AS activo
    FROM dbo.users_legacy legacy
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.users u WHERE u.email = LTRIM(RTRIM(legacy.email))
    );
END
GO

/* ===========================================================
   4) Tabla de evaluaciones basica (registro local del envio)
   =========================================================== */
IF OBJECT_ID('dbo.evaluaciones', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.evaluaciones (
        id INT IDENTITY(1,1) PRIMARY KEY,
        estudiante_id INT NOT NULL,
        estado NVARCHAR(30) NOT NULL CONSTRAINT DF_evaluaciones_estado DEFAULT ('completada'),
        origen NVARCHAR(40) NOT NULL CONSTRAINT DF_evaluaciones_origen DEFAULT ('tally'),
        referencia_externa NVARCHAR(120) NULL,
        datos_json NVARCHAR(MAX) NULL,
        fecha_creacion DATETIME2 NOT NULL CONSTRAINT DF_evaluaciones_fecha_creacion DEFAULT (GETDATE()),
        fecha_actualizacion DATETIME2 NOT NULL CONSTRAINT DF_evaluaciones_fecha_actualizacion DEFAULT (GETDATE()),
        CONSTRAINT FK_evaluaciones_estudiante FOREIGN KEY (estudiante_id) REFERENCES dbo.users(id)
    );
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_evaluaciones_estudiante_id'
      AND object_id = OBJECT_ID('dbo.evaluaciones')
)
BEGIN
    CREATE INDEX IX_evaluaciones_estudiante_id
    ON dbo.evaluaciones(estudiante_id, fecha_creacion DESC);
END
GO

PRINT 'Schema EdTech listo para la app Flask.';
GO
