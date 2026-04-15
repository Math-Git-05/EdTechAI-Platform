from app import db


class StudentProfile(db.Model):
    __tablename__ = "student_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)

    # Identificador academico principal (RNE / matricula)
    student_id = db.Column(db.String(24), nullable=False, unique=True, index=True)
    segundo_apellido = db.Column(db.String(100), nullable=False)
    genero = db.Column(db.String(30), nullable=False, default="prefiero_no_decir")
    fecha_nacimiento = db.Column(db.Date, nullable=False)
    edad = db.Column(db.Integer, nullable=False)
    grado_nivel = db.Column(db.String(30), nullable=False)
    school_id = db.Column(db.String(30), nullable=False, default="10-03-00092")
    enrollment_year = db.Column(db.Integer, nullable=True)
    academic_status = db.Column(db.String(30), nullable=False, default="activo")
    interest_technology = db.Column(db.Float, nullable=True, default=0)
    interest_design = db.Column(db.Float, nullable=True, default=0)
    interest_business = db.Column(db.Float, nullable=True, default=0)
    interest_health = db.Column(db.Float, nullable=True, default=0)

    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    def __repr__(self):
        return f"<StudentProfile user={self.user_id} student_id={self.student_id}>"
