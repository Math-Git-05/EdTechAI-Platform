USE EdTech;
GO

IF COL_LENGTH('dbo.users', 'profesor_id') IS NULL
BEGIN
    ALTER TABLE dbo.users ADD profesor_id INT NULL;
END
GO

IF COL_LENGTH('dbo.users', 'seccion') IS NULL
BEGIN
    ALTER TABLE dbo.users ADD seccion NVARCHAR(50) NULL;
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = 'FK_users_profesor'
)
AND COL_LENGTH('dbo.users', 'id') IS NOT NULL
BEGIN
    ALTER TABLE dbo.users
    ADD CONSTRAINT FK_users_profesor
    FOREIGN KEY (profesor_id) REFERENCES dbo.users(id);
END
GO

PRINT 'Columnas profesor_id y seccion listas.';
GO
