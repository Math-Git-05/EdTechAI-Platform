USE EdTech;
GO

SET XACT_ABORT ON;
GO

/* ============================================================
   1) Extensiones recomendadas para la tabla users existente
   ============================================================ */
IF COL_LENGTH('dbo.users', 'is_active') IS NULL
BEGIN
    ALTER TABLE dbo.users
    ADD is_active BIT NOT NULL CONSTRAINT DF_users_is_active DEFAULT (1);
END
GO

IF COL_LENGTH('dbo.users', 'approved_at') IS NULL
BEGIN
    ALTER TABLE dbo.users
    ADD approved_at DATETIME NULL;
END
GO

IF COL_LENGTH('dbo.users', 'approved_by') IS NULL
BEGIN
    ALTER TABLE dbo.users
    ADD approved_by INT NULL;
END
GO

IF COL_LENGTH('dbo.users', 'updated_at') IS NULL
BEGIN
    ALTER TABLE dbo.users
    ADD updated_at DATETIME NOT NULL CONSTRAINT DF_users_updated_at DEFAULT (GETDATE());
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = 'FK_users_approved_by'
)
BEGIN
    ALTER TABLE dbo.users
    ADD CONSTRAINT FK_users_approved_by
    FOREIGN KEY (approved_by) REFERENCES dbo.users(user_id);
END
GO

/* ============================================================
   2) Solicitudes de creacion de cuenta (pendiente/aprobada/rechazada)
   ============================================================ */
IF OBJECT_ID('dbo.account_requests', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.account_requests (
        request_id INT IDENTITY(1,1) PRIMARY KEY,
        first_name NVARCHAR(100) NOT NULL,
        last_name NVARCHAR(100) NOT NULL,
        email NVARCHAR(150) NOT NULL,
        password_hash NVARCHAR(255) NOT NULL,
        requested_role NVARCHAR(20) NOT NULL
            CHECK (requested_role IN ('estudiante', 'profesor')),
        status NVARCHAR(20) NOT NULL
            CONSTRAINT DF_account_requests_status DEFAULT ('pendiente')
            CHECK (status IN ('pendiente', 'aprobada', 'rechazada')),
        requested_at DATETIME NOT NULL CONSTRAINT DF_account_requests_requested_at DEFAULT (GETDATE()),
        reviewed_at DATETIME NULL,
        reviewed_by INT NULL,
        review_note NVARCHAR(500) NULL,
        CONSTRAINT FK_account_requests_reviewed_by
            FOREIGN KEY (reviewed_by) REFERENCES dbo.users(user_id)
    );
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'UX_account_requests_email_pending'
      AND object_id = OBJECT_ID('dbo.account_requests')
)
BEGIN
    CREATE UNIQUE INDEX UX_account_requests_email_pending
        ON dbo.account_requests(email)
        WHERE status = 'pendiente';
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_account_requests_status_requested_at'
      AND object_id = OBJECT_ID('dbo.account_requests')
)
BEGIN
    CREATE INDEX IX_account_requests_status_requested_at
        ON dbo.account_requests(status, requested_at DESC);
END
GO

/* ============================================================
   3) Auditoria de cambio de roles de usuarios
   ============================================================ */
IF OBJECT_ID('dbo.user_role_audit', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.user_role_audit (
        audit_id INT IDENTITY(1,1) PRIMARY KEY,
        user_id INT NOT NULL,
        old_role NVARCHAR(20) NOT NULL
            CHECK (old_role IN ('estudiante', 'profesor', 'admin')),
        new_role NVARCHAR(20) NOT NULL
            CHECK (new_role IN ('estudiante', 'profesor', 'admin')),
        changed_by INT NOT NULL,
        change_reason NVARCHAR(500) NULL,
        changed_at DATETIME NOT NULL CONSTRAINT DF_user_role_audit_changed_at DEFAULT (GETDATE()),
        CONSTRAINT FK_user_role_audit_user
            FOREIGN KEY (user_id) REFERENCES dbo.users(user_id),
        CONSTRAINT FK_user_role_audit_changed_by
            FOREIGN KEY (changed_by) REFERENCES dbo.users(user_id)
    );
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_user_role_audit_user_changed_at'
      AND object_id = OBJECT_ID('dbo.user_role_audit')
)
BEGIN
    CREATE INDEX IX_user_role_audit_user_changed_at
        ON dbo.user_role_audit(user_id, changed_at DESC);
END
GO

/* ============================================================
   4) Procedimiento: aprobar solicitud de cuenta
   ============================================================ */
CREATE OR ALTER PROCEDURE dbo.sp_account_request_approve
    @request_id INT,
    @admin_user_id INT,
    @review_note NVARCHAR(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @first_name NVARCHAR(100),
        @last_name NVARCHAR(100),
        @email NVARCHAR(150),
        @password_hash NVARCHAR(255),
        @requested_role NVARCHAR(20),
        @status NVARCHAR(20),
        @admin_role NVARCHAR(20);

    BEGIN TRANSACTION;

    SELECT @admin_role = role
    FROM dbo.users
    WHERE user_id = @admin_user_id;

    IF @admin_role <> 'admin'
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51001, 'Solo un usuario admin puede aprobar solicitudes.', 1;
    END

    SELECT
        @first_name = first_name,
        @last_name = last_name,
        @email = email,
        @password_hash = password_hash,
        @requested_role = requested_role,
        @status = status
    FROM dbo.account_requests WITH (UPDLOCK, HOLDLOCK)
    WHERE request_id = @request_id;

    IF @status IS NULL
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51002, 'Solicitud no encontrada.', 1;
    END

    IF @status <> 'pendiente'
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51003, 'La solicitud ya fue procesada.', 1;
    END

    IF EXISTS (SELECT 1 FROM dbo.users WHERE email = @email)
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51004, 'El correo ya existe en users.', 1;
    END

    INSERT INTO dbo.users (
        first_name,
        last_name,
        email,
        password_hash,
        role,
        created_at,
        last_login,
        is_active,
        approved_at,
        approved_by,
        updated_at
    )
    VALUES (
        @first_name,
        @last_name,
        @email,
        @password_hash,
        @requested_role,
        GETDATE(),
        NULL,
        1,
        GETDATE(),
        @admin_user_id,
        GETDATE()
    );

    UPDATE dbo.account_requests
    SET
        status = 'aprobada',
        reviewed_at = GETDATE(),
        reviewed_by = @admin_user_id,
        review_note = @review_note
    WHERE request_id = @request_id;

    COMMIT TRANSACTION;
END
GO

/* ============================================================
   5) Procedimiento: rechazar solicitud de cuenta
   ============================================================ */
CREATE OR ALTER PROCEDURE dbo.sp_account_request_reject
    @request_id INT,
    @admin_user_id INT,
    @review_note NVARCHAR(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @status NVARCHAR(20),
        @admin_role NVARCHAR(20);

    BEGIN TRANSACTION;

    SELECT @admin_role = role
    FROM dbo.users
    WHERE user_id = @admin_user_id;

    IF @admin_role <> 'admin'
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51005, 'Solo un usuario admin puede rechazar solicitudes.', 1;
    END

    SELECT @status = status
    FROM dbo.account_requests WITH (UPDLOCK, HOLDLOCK)
    WHERE request_id = @request_id;

    IF @status IS NULL
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51006, 'Solicitud no encontrada.', 1;
    END

    IF @status <> 'pendiente'
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51007, 'La solicitud ya fue procesada.', 1;
    END

    UPDATE dbo.account_requests
    SET
        status = 'rechazada',
        reviewed_at = GETDATE(),
        reviewed_by = @admin_user_id,
        review_note = @review_note
    WHERE request_id = @request_id;

    COMMIT TRANSACTION;
END
GO

/* ============================================================
   6) Procedimiento: cambio de rol por admin + auditoria
   ============================================================ */
CREATE OR ALTER PROCEDURE dbo.sp_user_change_role
    @target_user_id INT,
    @new_role NVARCHAR(20),
    @admin_user_id INT,
    @reason NVARCHAR(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @old_role NVARCHAR(20),
        @admin_role NVARCHAR(20);

    IF @new_role NOT IN ('estudiante', 'profesor', 'admin')
    BEGIN
        THROW 51008, 'Rol invalido.', 1;
    END

    BEGIN TRANSACTION;

    SELECT @admin_role = role
    FROM dbo.users
    WHERE user_id = @admin_user_id;

    IF @admin_role <> 'admin'
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51009, 'Solo un usuario admin puede cambiar roles.', 1;
    END

    SELECT @old_role = role
    FROM dbo.users WITH (UPDLOCK, HOLDLOCK)
    WHERE user_id = @target_user_id;

    IF @old_role IS NULL
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51010, 'Usuario objetivo no encontrado.', 1;
    END

    IF @old_role = @new_role
    BEGIN
        ROLLBACK TRANSACTION;
        THROW 51011, 'El rol nuevo es igual al rol actual.', 1;
    END

    UPDATE dbo.users
    SET
        role = @new_role,
        updated_at = GETDATE()
    WHERE user_id = @target_user_id;

    INSERT INTO dbo.user_role_audit (
        user_id,
        old_role,
        new_role,
        changed_by,
        change_reason
    )
    VALUES (
        @target_user_id,
        @old_role,
        @new_role,
        @admin_user_id,
        @reason
    );

    COMMIT TRANSACTION;
END
GO
